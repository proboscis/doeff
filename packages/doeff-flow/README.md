# doeff-flow

Live effect trace observability for doeff workflows.

## Overview

When running durable or long-running workflows with doeff, you need visibility into execution state:

- **Where am I?** Which `@do` function is currently executing
- **How did I get here?** The call chain that led to the current point
- **What's happening?** The current effect being processed
- **Is it stuck?** Whether execution is progressing

`doeff-flow` provides real-time workflow observation through JSONL trace files and CLI tools.

## Installation

```bash
pip install doeff-flow
```

Or with uv:

```bash
uv add doeff-flow
```

## Quick Start Tutorial

### Step 1: Create a Workflow

```python
# my_workflow.py
from doeff import do
from doeff.effects import Pure
from doeff_flow import run_workflow
from pathlib import Path

@do
def fetch_data():
    """Simulate fetching data."""
    data = yield Pure({"items": [1, 2, 3, 4, 5]})
    return data

@do
def process_item(item: int):
    """Process a single item."""
    result = yield Pure(item * 2)
    return result

@do
def my_workflow():
    """Main workflow."""
    # Step 1: Fetch
    data = yield fetch_data()

    # Step 2: Process each item
    results = []
    for item in data["items"]:
        result = yield process_item(item)
        results.append(result)

    # Step 3: Return summary
    return {"count": len(results), "sum": sum(results)}

if __name__ == "__main__":
    result = run_workflow(
        my_workflow(),
        workflow_id="my-first-workflow",
        trace_dir=Path(".doeff-flow"),
    )
    print(f"Result: {result.value}")
```

### Step 2: Run and Watch

**Terminal 1** - Run the workflow:
```bash
python my_workflow.py
```

**Terminal 2** - Watch live execution:
```bash
doeff-flow watch my-first-workflow --exit-on-complete
```

You'll see the call stack update in real-time:

```
┌─ my-first-workflow [running] step 7 ───────────────┐
│                                                     │
│  my_workflow           my_workflow.py:18            │
│    └─ process_item     my_workflow.py:11            │
│         ↳ Pure(6)                                   │
│                                                     │
│  Updated: 12:00:00.123                              │
└─────────────────────────────────────────────────────┘
```

### Step 3: Inspect After Completion

```bash
# List all workflows
doeff-flow ps

# View execution history
doeff-flow history my-first-workflow --last 20
```

## Examples

The package includes several examples demonstrating different use cases:

| Example | Description |
|---------|-------------|
| `01_live_trace.py` | Basic live tracing with `run_workflow` and `trace_observer` |
| `02_data_pipeline.py` | ETL pipeline with multiple stages |
| `03_error_handling.py` | Error capture and failure tracing |
| `04_concurrent_workflows.py` | Multiple concurrent workers with separate traces |

Run examples from the repository root:

```bash
python examples/flow/01_live_trace.py
python examples/flow/02_data_pipeline.py
```

## API Usage

### Option A: `run_workflow` (Simple)

```python
from doeff_flow import run_workflow

result = run_workflow(
    my_workflow(),
    workflow_id="wf-001",
    trace_dir=".doeff-flow",  # str or Path
)
```

### Option B: `trace_observer` (Composable)

```python
from doeff.cesk import run_sync
from doeff_flow import trace_observer

with trace_observer("wf-001", ".doeff-flow") as on_step:
    result = run_sync(
        my_workflow(),
        env={"config": "value"},  # Custom environment
        on_step=on_step,
    )
```

### With Durable Storage

```python
from doeff.storage import FileStorage

result = run_workflow(
    my_workflow(),
    workflow_id="durable-wf",
    storage=FileStorage(".doeff-cache"),
)
```

## CLI Reference

### `doeff-flow watch`

Watch live effect trace for a workflow.

```bash
doeff-flow watch WORKFLOW_ID [OPTIONS]
```

**Options:**
- `--trace-dir PATH` - Directory containing traces (default: `.doeff-flow`)
- `--exit-on-complete` - Exit when workflow completes or fails
- `--poll-interval FLOAT` - Poll interval in seconds (default: 0.1)

### `doeff-flow ps`

List all workflows with their status.

```bash
doeff-flow ps [OPTIONS]
```

**Options:**
- `--trace-dir PATH` - Directory containing traces

### `doeff-flow history`

Show execution history for a workflow.

```bash
doeff-flow history WORKFLOW_ID [OPTIONS]
```

**Options:**
- `--trace-dir PATH` - Directory containing traces
- `--last N` - Show last N steps (default: 10)

## Trace Format

Traces are stored as JSONL files:

```
.doeff-flow/
├── workflow-001/
│   └── trace.jsonl
├── workflow-002/
│   └── trace.jsonl
└── ...
```

Each line is a JSON snapshot:

```json
{
  "workflow_id": "workflow-001",
  "step": 42,
  "status": "running",
  "current_effect": "Pure(10)",
  "trace": [
    {"function": "my_workflow", "file": "workflow.py", "line": 18, "code": null},
    {"function": "process_item", "file": "workflow.py", "line": 11, "code": null}
  ],
  "started_at": "2025-12-31T12:00:00.000000",
  "updated_at": "2025-12-31T12:00:00.050000",
  "error": null
}
```

## Best Practices

1. **Use descriptive workflow IDs** with timestamps for uniqueness:
   ```python
   from datetime import datetime
   workflow_id = f"pipeline-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
   ```

2. **Add `.doeff-flow` to `.gitignore`**:
   ```
   .doeff-flow/
   ```

3. **Use `--exit-on-complete` in scripts**:
   ```bash
   doeff-flow watch my-wf --exit-on-complete
   ```

4. **Clean up old traces periodically**:
   ```bash
   find .doeff-flow -mtime +7 -delete
   ```

## API Reference

### Functions

| Function | Description |
|----------|-------------|
| `run_workflow(program, workflow_id, ...)` | Run workflow with live trace |
| `trace_observer(workflow_id, trace_dir)` | Context manager for `on_step` callback |
| `validate_workflow_id(workflow_id)` | Validate workflow ID format |

### Data Types

| Type | Description |
|------|-------------|
| `TraceFrame` | A frame in the effect trace (function, file, line, code) |
| `LiveTrace` | Complete execution snapshot (workflow_id, step, status, trace, ...) |

## Documentation

Full documentation: [docs/17-workflow-observability.md](../../docs/17-workflow-observability.md)

## License

MIT
