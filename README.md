# Taskwarrior Enhanced

Companion CLI for [taskwarrior](https://taskwarrior.org/)

## Features

### Dependency Tree View

Display pending tasks in a hierarchical tree format that shows task dependencies:

```bash
python main.py tree
```

Example output:

```
├── 1 Plan weekend camping trip
├── 2 Review quarterly budget
├── 3 Update portfolio website
└── 4 Launch product feature
    ├── 5 Complete code review
    ├── 6 Run integration tests
    └── 7 Update documentation
```

#### Features:

- **Hierarchical view**: Tasks with dependencies show their blocking tasks indented underneath
- **Multiple parent indicator**: Tasks that block multiple other tasks show `[N↑]` prefix
- **Priority coloring**:
  - Low priority tasks (`L`) are grayed out
  - High priority tasks (`H`) are highlighted in bright red and bold
- **Task IDs**: Each task shows its taskwarrior ID number for easy reference

#### Filtering

Add taskwarrior filters to focus on specific tasks:

```bash
# Show only tasks in the 'work' project
python main.py tree proj:work

# Show only high priority tasks in the 'personal' project  
python main.py tree proj:personal priority:H

# Multiple filters work too
python main.py tree proj:dev +next
```

All standard taskwarrior filter syntax is supported.
