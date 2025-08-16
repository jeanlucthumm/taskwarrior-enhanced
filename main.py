import json
import subprocess
from collections import defaultdict, deque
from typing import Dict, List, Set, Optional

import click


@click.group()
def cli():
    """Taskwarrior Enhanced - Companion CLI for taskwarrior"""
    pass


@cli.command()
@click.argument("filters", nargs=-1)
def tree(filters):
    """Display pending tasks in a dependency tree format"""

    # Build task command with filters
    task_cmd = ["task", "+PENDING"]
    if filters:
        task_cmd.extend(filters)
    task_cmd.append("export")

    try:
        result = subprocess.run(task_cmd, capture_output=True, text=True, check=True)
        tasks_data = json.loads(result.stdout)
    except subprocess.CalledProcessError:
        click.echo("Error: Failed to run 'task +PENDING export'", err=True)
        return
    except json.JSONDecodeError:
        click.echo("Error: Failed to parse task export output", err=True)
        return

    if not tasks_data:
        click.echo("No pending tasks found.")
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
    def get_sort_key(uuid):
        task = tasks[uuid]
        priority = task.get("priority", "")
        urgency = task.get("urgency", 0)
        # Priority order: H > M > L > None, then by urgency
        priority_order = {"H": 4, "M": 3, "L": 2, "": 1}
        return (priority_order.get(priority, 0), urgency)

    roots.sort(key=get_sort_key)

    # Print the tree
    visited = set()

    def print_tree(task_uuid: str, prefix: str = "", is_last: bool = True):
        if task_uuid in visited:
            return
        visited.add(task_uuid)

        task = tasks[task_uuid]
        task_id = task.get("id", "?")
        description = task["description"]
        priority = task.get("priority", "")

        # Add multiple parents indicator
        task_parents = parents.get(task_uuid, [])
        if len(task_parents) > 1:
            parent_ids = [
                tasks[parent_uuid].get("id", "?") for parent_uuid in task_parents
            ]
            parent_ids_str = ",".join(map(str, parent_ids))
            description = f"[{len(task_parents)}↑:{parent_ids_str}] {description}"

        # Print current task with ID prefix and color based on priority
        connector = "└── " if is_last else "├── "
        task_line = f"{prefix}{connector}{task_id} {description}"

        # Color based on priority and active status
        is_active = "start" in task

        if is_active:
            task_line = click.style(task_line, fg="bright_green", bold=True)
        elif priority == "L":
            task_line = click.style(task_line, fg="bright_black")
        elif priority == "H":
            task_line = click.style(task_line, fg="bright_red", bold=True)

        click.echo(task_line)

        # Print children
        task_children = children.get(task_uuid, [])
        # Sort children by priority first, then urgency
        task_children.sort(key=get_sort_key)

        for i, child_uuid in enumerate(task_children):
            if child_uuid not in visited:
                is_child_last = i == len(task_children) - 1
                child_prefix = prefix + ("    " if is_last else "│   ")
                print_tree(child_uuid, child_prefix, is_child_last)

    # Print all trees starting from roots
    for i, root_uuid in enumerate(roots):
        if root_uuid not in visited:
            print_tree(root_uuid, "", i == len(roots) - 1)


if __name__ == "__main__":
    cli(prog_name="taskwarrior-enhanced")
