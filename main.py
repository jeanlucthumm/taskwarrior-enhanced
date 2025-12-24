import json
import os
import shlex
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import click


def is_overdue_or_due_today(task: Dict) -> Optional[str]:
    """Check if task is overdue or due today. Returns 'overdue', 'due_today', or None."""
    if "due" not in task:
        return None

    try:
        # Parse due date from taskwarrior ISO format
        due_date = datetime.fromisoformat(task["due"].replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)

        # Get start of today in UTC
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = now.replace(hour=23, minute=59, second=59, microsecond=999999)

        if due_date < now:
            return "overdue"
        elif today_start <= due_date <= today_end:
            return "due_today"
        else:
            return None
    except (ValueError, AttributeError):
        return None


def _detect_context_via_task_cli() -> Optional[str]:
    """Attempt to read the active context using the task CLI."""
    try:
        result = subprocess.run(
            ["task", "_get", "rc.context"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None

    context = result.stdout.strip()
    return context or None


def _expand_include_path(include_path: str, base_path: Path) -> Path:
    """Resolve include path relative to base file, expanding user and env vars."""
    expanded = os.path.expandvars(include_path.strip().strip('"\''))
    candidate = Path(expanded).expanduser()
    if candidate.is_absolute():
        return candidate
    return (base_path.parent / candidate).resolve()


def _parse_taskrc_for_contexts(
    path: Path, visited: Set[Path]
) -> Tuple[Optional[str], Dict[str, str]]:
    """Recursively parse Taskwarrior rc files for the active context and definitions.

    Supports both `context.<name>=...` and `context.<name>.read/.write=...` forms.
    When both read/write exist, the returned filter is `.read` since the tree
    command performs a read-only listing.
    """
    try:
        resolved_path = path.resolve()
    except FileNotFoundError:
        return None, {}

    if resolved_path in visited or not resolved_path.exists():
        return None, {}

    visited.add(resolved_path)

    try:
        contents = resolved_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None, {}

    active_context: Optional[str] = None
    # Track possibly separate read/write filters per context name
    read_filters: Dict[str, str] = {}
    write_filters: Dict[str, str] = {}
    generic_filters: Dict[str, str] = {}

    for raw_line in contents:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if line.lower().startswith("include"):
            _, _, include_part = line.partition(" ")
            if include_part:
                include_path = _expand_include_path(include_part, resolved_path)
                nested_active, nested_definitions = _parse_taskrc_for_contexts(
                    include_path, visited
                )
                if nested_active is not None:
                    active_context = nested_active
                for _name, _filter in nested_definitions.items():
                    # Do not overwrite current file's explicit read/write entries
                    if _name not in read_filters and _name not in write_filters and _name not in generic_filters:
                        generic_filters[_name] = _filter
            continue

        if line.startswith("context."):
            key, _, value = line.partition("=")
            if not value:
                continue
            rhs = value.split("#", 1)[0].strip()
            # key looks like: context.NAME or context.NAME.read/write
            key_body = key[len("context.") :].strip()
            if not key_body:
                continue
            parts = key_body.split(".")
            if len(parts) == 1:
                context_name = parts[0]
                generic_filters[context_name] = rhs
            elif len(parts) == 2:
                context_name, mode = parts
                mode = mode.lower()
                if mode == "read":
                    read_filters[context_name] = rhs
                elif mode == "write":
                    write_filters[context_name] = rhs
                else:
                    # Unknown suffix, treat it as generic
                    generic_filters[context_name] = rhs
            else:
                # Unexpected extra dots; use the first as name and last as mode
                context_name = parts[0]
                mode = parts[-1].lower()
                if mode == "read":
                    read_filters[context_name] = rhs
                elif mode == "write":
                    write_filters[context_name] = rhs
                else:
                    generic_filters[context_name] = rhs
            continue

        if line.startswith("context") and not line.startswith("context."):
            _, _, value = line.partition("=")
            context_value = value.split("#", 1)[0].strip()
            if context_value:
                active_context = context_value

    # Merge into a single mapping preferring read > generic > write
    merged: Dict[str, str] = {}
    for name in set().union(read_filters.keys(), generic_filters.keys(), write_filters.keys()):
        if name in read_filters:
            merged[name] = read_filters[name]
        elif name in generic_filters:
            merged[name] = generic_filters[name]
        elif name in write_filters:
            merged[name] = write_filters[name]

    return active_context, merged


def _taskrc_path() -> Optional[Path]:
    """Return the primary taskrc file location if it exists."""
    candidates: List[Path] = []
    taskrc_env = os.environ.get("TASKRC")
    if taskrc_env:
        candidates.append(Path(taskrc_env).expanduser())

    home = Path.home()
    candidates.extend(
        [
            home / ".taskrc",
            home / ".config" / "task" / "taskrc",
        ]
    )

    for candidate in candidates:
        expanded = candidate.expanduser()
        if expanded.exists():
            try:
                return expanded.resolve()
            except FileNotFoundError:
                continue
    return None


def detect_active_context() -> Tuple[Optional[str], Optional[str]]:
    """Detect the active Taskwarrior context and its filter definition."""
    context = _detect_context_via_task_cli()
    context_filters: Dict[str, str] = {}
    config_context: Optional[str] = None

    taskrc = _taskrc_path()
    if taskrc:
        config_context, context_filters = _parse_taskrc_for_contexts(taskrc, set())

    active_context = context or config_context
    filter_definition = None
    if active_context:
        filter_definition = context_filters.get(active_context)

    return active_context, filter_definition


@click.group()
def cli() -> None:
    """Taskwarrior Enhanced - Companion CLI for taskwarrior"""
    pass


@cli.command()
@click.argument("filters", nargs=-1)
def tree(filters: Tuple[str, ...]) -> None:
    """Display pending tasks in a dependency tree format"""

    # Build task command with filters
    task_cmd: List[str] = ["task"]
    context_name, context_filter = detect_active_context()
    if context_name:
        # Minimal, user-friendly log
        click.echo(f"Context: {context_name}")
        if context_filter:
            context_args = shlex.split(context_filter)
            task_cmd.extend(context_args)
        else:
            task_cmd.append(f"rc.context={context_name}")

    # Fetch both pending and waiting tasks
    pending_cmd = task_cmd + ["+PENDING"]
    waiting_cmd = task_cmd + ["+WAITING"]
    if filters:
        pending_cmd.extend(filters)
        waiting_cmd.extend(filters)
    pending_cmd.append("export")
    waiting_cmd.append("export")

    try:
        pending_result = subprocess.run(
            pending_cmd, capture_output=True, text=True, check=True
        )
        pending_tasks = json.loads(pending_result.stdout)

        waiting_result = subprocess.run(
            waiting_cmd, capture_output=True, text=True, check=True
        )
        waiting_tasks = json.loads(waiting_result.stdout)
    except subprocess.CalledProcessError as e:
        click.echo("Error: Failed to run task export", err=True)
        click.echo(f"Return code: {e.returncode}", err=True)
        click.echo(f"stderr: {e.stderr}", err=True)
        click.echo(f"stdout: {e.stdout}", err=True)
        return
    except FileNotFoundError:
        click.echo(
            "Error: 'task' command not found. Is taskwarrior installed?", err=True
        )
        return
    except json.JSONDecodeError:
        click.echo("Error: Failed to parse task export output", err=True)
        return

    # Track waiting task UUIDs for styling
    waiting_uuids: Set[str] = {task["uuid"] for task in waiting_tasks}

    # Merge tasks (pending first, then add waiting tasks not already present)
    seen_uuids: Set[str] = set()
    tasks_data: List[Dict] = []
    for task in pending_tasks:
        if task["uuid"] not in seen_uuids:
            seen_uuids.add(task["uuid"])
            tasks_data.append(task)
    for task in waiting_tasks:
        if task["uuid"] not in seen_uuids:
            seen_uuids.add(task["uuid"])
            tasks_data.append(task)

    if not tasks_data:
        click.echo("No pending or waiting tasks found.")
        return

    # Build task lookup and dependency maps
    tasks = {task["uuid"]: task for task in tasks_data}
    children = defaultdict(list)  # parent_uuid -> [child_uuids]
    parents = defaultdict(list)  # child_uuid -> [parent_uuids]

    # Build dependency relationships
    # For display purposes: task with 'depends' is the parent, dependencies are children
    # This shows what needs to be done before the main task can be completed
    for task in tasks_data:
        if "depends" in task:
            for dependency_uuid in task["depends"]:
                if dependency_uuid in tasks:  # Only include pending dependencies
                    # task['uuid'] is the parent, dependency_uuid is the child
                    children[task["uuid"]].append(dependency_uuid)
                    parents[dependency_uuid].append(task["uuid"])

    # Find root tasks (tasks that are not children of any other task)
    # These are tasks that other tasks depend on, but don't depend on anything themselves
    roots = []
    all_children = set()
    for child_list in children.values():
        all_children.update(child_list)

    for task in tasks_data:
        task_uuid = task["uuid"]
        if task_uuid not in all_children:
            roots.append(task_uuid)

    # Sort roots by priority first, then urgency (both descending) for consistent output
    def get_sort_key(uuid: str) -> Tuple[int, float]:
        task = tasks[uuid]
        priority = task.get("priority", "")
        urgency_value = task.get("urgency", 0)
        try:
            urgency = float(urgency_value)
        except (TypeError, ValueError):
            urgency = 0.0
        # Priority order: H > M > L > None, then by urgency
        priority_order = {"H": 4, "M": 3, "L": 2, "": 1}
        return (priority_order.get(priority, 0), urgency)

    roots.sort(key=get_sort_key)

    # Print the tree
    visited = set()

    def print_tree(
        task_uuid: str,
        prefix: str = "",
        is_last: bool = True,
        current_parent: str | None = None,
    ) -> None:
        if task_uuid in visited:
            return
        visited.add(task_uuid)

        task = tasks[task_uuid]
        task_id = task.get("id", "?")
        description = task["description"]
        priority = task.get("priority", "")

        # Add multiple parents indicator (styled grey), excluding current parent
        task_parents = parents.get(task_uuid, [])
        other_parents = [p for p in task_parents if p != current_parent]
        multi_parent_prefix = ""
        if other_parents:
            parent_ids = [
                tasks[parent_uuid].get("id", "?") for parent_uuid in other_parents
            ]
            parent_ids_str = ",".join(map(str, parent_ids))
            multi_parent_prefix = click.style(f" [{parent_ids_str}]", fg="bright_black")

        # Print current task with ID prefix and color based on priority
        connector = "└── " if is_last else "├── "
        task_content = f"{task_id} {description}"

        # Color based on priority, active status, due dates, and waiting status
        is_active = "start" in task
        is_waiting = task_uuid in waiting_uuids
        due_status = is_overdue_or_due_today(task)

        if is_active:
            task_content = click.style(task_content, fg="bright_green", bold=True)
        elif due_status in ("overdue", "due_today"):
            task_content = click.style(task_content, fg="blue")
        elif is_waiting or priority == "L":
            task_content = click.style(task_content, fg="bright_black")
        elif priority == "H":
            task_content = click.style(task_content, fg="bright_red", bold=True)

        click.echo(f"{prefix}{connector}{task_content}{multi_parent_prefix}")

        # Print children
        task_children = children.get(task_uuid, [])
        # Sort children by priority first, then urgency
        task_children.sort(key=get_sort_key)

        for i, child_uuid in enumerate(task_children):
            if child_uuid not in visited:
                is_child_last = i == len(task_children) - 1
                child_prefix = prefix + ("    " if is_last else "│   ")
                print_tree(child_uuid, child_prefix, is_child_last, task_uuid)

    # Print all trees starting from roots
    for i, root_uuid in enumerate(roots):
        if root_uuid not in visited:
            print_tree(root_uuid, "", i == len(roots) - 1)


@cli.command()
@click.argument("task_id")
def chain(task_id: str) -> None:
    """Display ancestor tree from a task upward (tasks blocked by it)"""

    # Build task command with context
    task_cmd: List[str] = ["task"]
    context_name, context_filter = detect_active_context()
    if context_name:
        if context_filter:
            context_args = shlex.split(context_filter)
            task_cmd.extend(context_args)
        else:
            task_cmd.append(f"rc.context={context_name}")

    # Fetch pending tasks only (no waiting)
    cmd = task_cmd + ["+PENDING", "export"]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        tasks_data = json.loads(result.stdout)
    except subprocess.CalledProcessError as e:
        click.echo("Error: Failed to run task export", err=True)
        click.echo(f"stderr: {e.stderr}", err=True)
        return
    except FileNotFoundError:
        click.echo(
            "Error: 'task' command not found. Is taskwarrior installed?", err=True
        )
        return
    except json.JSONDecodeError:
        click.echo("Error: Failed to parse task export output", err=True)
        return

    if not tasks_data:
        click.echo("No pending tasks found.")
        return

    # Build task lookup
    tasks = {task["uuid"]: task for task in tasks_data}

    # Find the root task by ID or UUID
    root_uuid = None
    for task in tasks_data:
        if str(task.get("id")) == task_id or task.get("uuid") == task_id:
            root_uuid = task["uuid"]
            break

    if not root_uuid:
        click.echo(f"Error: Task '{task_id}' not found in pending tasks.", err=True)
        return

    # Build parent relationships
    # parents[uuid] = list of tasks that depend on uuid (tasks that uuid blocks)
    parents: Dict[str, List[str]] = defaultdict(list)
    for task in tasks_data:
        if "depends" in task:
            for dependency_uuid in task["depends"]:
                if dependency_uuid in tasks:
                    parents[dependency_uuid].append(task["uuid"])

    # Sort helper
    def get_sort_key(uuid: str) -> Tuple[int, float]:
        task = tasks[uuid]
        priority = task.get("priority", "")
        urgency_value = task.get("urgency", 0)
        try:
            urgency = float(urgency_value)
        except (TypeError, ValueError):
            urgency = 0.0
        priority_order = {"H": 4, "M": 3, "L": 2, "": 1}
        return (priority_order.get(priority, 0), urgency)

    # Print the root task first (cyan)
    root_task = tasks[root_uuid]
    root_id = root_task.get("id", "?")
    root_desc = root_task["description"]
    root_content = click.style(f"{root_id} {root_desc}", fg="cyan", bold=True)
    click.echo()
    click.echo(root_content)

    def print_ancestors(
        task_uuid: str,
        prefix: str = "",
        is_last: bool = True,
        path: Set[str] | None = None,
        is_branching: bool = False,
    ) -> None:
        if path is None:
            path = set()

        if task_uuid in path:
            return  # Cycle detected, stop

        # Create new path set for this branch
        current_path = path | {task_uuid}

        task = tasks[task_uuid]
        task_id = task.get("id", "?")
        description = task["description"]
        priority = task.get("priority", "")

        # Use tree connectors only when branching, no connector for linear chains
        if is_branching:
            connector = "└── " if is_last else "├── "
        else:
            connector = ""
        task_content = f"{task_id} {description}"

        # Style based on priority and active status
        is_active = "start" in task
        due_status = is_overdue_or_due_today(task)

        if is_active:
            task_content = click.style(task_content, fg="bright_green", bold=True)
        elif due_status in ("overdue", "due_today"):
            task_content = click.style(task_content, fg="blue")
        elif priority == "L":
            task_content = click.style(task_content, fg="bright_black")
        elif priority == "H":
            task_content = click.style(task_content, fg="bright_red", bold=True)

        click.echo(f"{prefix}{connector}{task_content}")

        # Print parents (tasks blocked by this task)
        task_parents = parents.get(task_uuid, [])
        task_parents_sorted = sorted(task_parents, key=get_sort_key)
        num_parents = len(task_parents_sorted)
        parents_branching = num_parents > 1

        # Calculate prefix for parents based on whether WE used a connector
        if is_branching:
            child_prefix = prefix + ("    " if is_last else "│   ")
        else:
            child_prefix = prefix

        for i, parent_uuid in enumerate(task_parents_sorted):
            is_parent_last = i == num_parents - 1
            print_ancestors(
                parent_uuid, child_prefix, is_parent_last, current_path, parents_branching
            )

    # Print ancestors starting from root's parents
    root_parents = parents.get(root_uuid, [])
    root_parents_sorted = sorted(root_parents, key=get_sort_key)
    root_branching = len(root_parents_sorted) > 1

    root_path: Set[str] = {root_uuid}
    for i, parent_uuid in enumerate(root_parents_sorted):
        is_last = i == len(root_parents_sorted) - 1
        print_ancestors(parent_uuid, "", is_last, root_path, root_branching)


if __name__ == "__main__":
    cli(prog_name="taskwarrior-enhanced")
