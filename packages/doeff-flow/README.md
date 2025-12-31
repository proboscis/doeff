# doeff-flow

Live effect trace observability for doeff workflows.

## Overview

When running durable workflows with doeff, you need visibility into the execution state:
- Which `@do` function is currently executing
- The call chain that led to the current point (effect trace / K stack)
- The current effect being processed

`doeff-flow` provides tools to observe this information **live** from external tools (CLI, TUI, monitoring).

## Installation

```bash
pip install doeff-flow
```

## Quick Start

### Running Workflows with Trace

```python
from doeff import do
from doeff.effects import Pure
from doeff_flow import run_workflow
from pathlib import Path

@do
def my_workflow():
    a = yield Pure(10)
    b = yield Pure(20)
    return a + b

# Run with live trace
result = run_workflow(
    my_workflow(),
    workflow_id="wf-001",
    trace_dir=Path(".doeff-flow"),
)
print(result.value)  # 30
```

### Composable with Existing run_sync

```python
from doeff.cesk import run_sync
from doeff_flow import trace_observer
from pathlib import Path

with trace_observer("wf-001", Path(".doeff-flow")) as on_step:
    result = run_sync(my_workflow(), on_step=on_step)
```

## CLI Commands

### Watch a Workflow

Watch live effect trace in real-time:

```bash
# Watch single workflow
doeff-flow watch wf-001

# Watch with custom trace directory
doeff-flow watch wf-001 --trace-dir ./my-traces

# Exit when workflow completes
doeff-flow watch wf-001 --exit-on-complete
```

The watch display shows:

```
┌─ wf-001 [running] step 42 ─────────────────────────┐
│                                                     │
│  main_workflow         workflow.py:100              │
│    └─ process_step     workflow.py:45               │
│         └─ call_api    api.py:23                    │
│              ↳ CacheGet(key='api_result')           │
│                                                     │
│  Updated: 12:00:00.123                              │
└─────────────────────────────────────────────────────┘
```

### List Active Workflows

```bash
doeff-flow ps
```

Output:
```
wf-001    running     step 42
wf-002    completed   step 100
wf-003    failed      step 15
```

### View Execution History

```bash
# Show last 10 steps
doeff-flow history wf-001

# Show last 20 steps
doeff-flow history wf-001 --last 20
```

Output:
```
step    1  running     Pure(10)
step    2  running     CacheGet(key='api_result')
step    3  running     Pure(20)
...
step   42  completed   -
```

## Trace File Format

Traces are stored as JSONL files (one JSON object per line) in:

```
.doeff-flow/
├── wf-001/
│   └── trace.jsonl
├── wf-002/
│   └── trace.jsonl
└── ...
```

Each line contains a `LiveTrace` object:

```json
{
  "workflow_id": "wf-001",
  "step": 42,
  "status": "running",
  "current_effect": "CacheGet(key='api_result')",
  "trace": [
    {"function": "main_workflow", "file": "workflow.py", "line": 100, "code": null},
    {"function": "process_step", "file": "workflow.py", "line": 45, "code": null}
  ],
  "started_at": "2025-12-31T12:00:00.000000",
  "updated_at": "2025-12-31T12:00:00.050000",
  "error": null
}
```

**Benefits of JSONL**:
- Append-only (no atomic rename needed)
- Full execution history preserved
- `tail -f` friendly for live watching
- Easy replay and debugging

## API Reference

### `run_workflow()`

```python
def run_workflow(
    program: Program[T],
    workflow_id: str,
    trace_dir: Path | str = Path(".doeff-flow"),
    *,
    env: Environment | dict | None = None,
    store: Store | None = None,
    storage: DurableStorage | None = None,
) -> CESKResult[T]:
```

Convenience wrapper that combines `run_sync` with `trace_observer`.

### `trace_observer()`

```python
@contextmanager
def trace_observer(
    workflow_id: str,
    trace_dir: Path,
) -> Generator[Callable[[ExecutionSnapshot], None], None, None]:
```

Context manager that creates an `on_step` callback for live trace.

### Data Types

- `TraceFrame` - A frame in the effect trace (corresponds to a `@do` function call)
- `LiveTrace` - Current execution state of a workflow

## Related

- [CESK Execution Observability](../doeff/cesk_observability.py) - Core observability API
- [Durable Execution](../doeff/storage.py) - Storage layer for durable workflows
