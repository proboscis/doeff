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

The workflow yields doeff-flow's trace effects (`TracePush`, `TraceAnnotate`, `TraceSnapshot`), and
`production_handlers(workflow_id, trace_dir=None)` answers them by writing one trace entry per effect to
`{trace_dir}/{workflow_id}/trace.jsonl` (the XDG default directory when `trace_dir` is None).

```python
# my_workflow.py
from pathlib import Path

from doeff import do, run
from doeff_flow import TraceAnnotate, TracePush, TraceSnapshot, production_handlers

@do
def process_item(item: int):
    """Process a single item."""
    yield TracePush(name="process_item", metadata={"item": item})
    return item * 2

@do
def my_workflow():
    """Main workflow."""
    yield TracePush(name="my_workflow")
    results = []
    for item in [1, 2, 3, 4, 5]:
        results.append((yield process_item(item)))
        yield TraceAnnotate(key="done", value=len(results))
    yield TraceSnapshot(label="processed")
    return {"count": len(results), "sum": sum(results)}

if __name__ == "__main__":
    result = run(production_handlers("my-first-workflow", Path(".doeff-flow"))(my_workflow()))
    print(f"Result: {result}")
```

### Step 2: Run and Watch

**Terminal 1** - Run the workflow:
```bash
python my_workflow.py
```

**Terminal 2** - Watch live execution:
```bash
doeff-flow watch my-first-workflow --trace-dir .doeff-flow
```

You'll see the trace update in real-time:

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
| `01_live_trace.py` | Basic live tracing with the examples' runner and `trace_observer` |
| `02_data_pipeline.py` | ETL pipeline with multiple stages |
| `03_error_handling.py` | Error capture and failure tracing |
| `04_concurrent_workflows.py` | Multiple concurrent workers with separate traces |
| `05_durable_execution.py` | Durable execution with local SQLite cache under `.doeff-flow/` |
| `06_testing_with_mocks.py` | In-memory trace assertions with mock handlers |
| `07_workflow_status.py` | Workflow status with slog for semantic status updates |

The examples run their workflows with `doeff_flow._run.run_workflow`, a runner inside the package (not part
of its public API — ADR-DOE-API-001 R1) that runs the program under the reference handlers and writes one
terminal trace entry (`completed` or `failed`). Run examples from the `packages/doeff-flow` directory:

```bash
cd packages/doeff-flow
uv run python examples/01_live_trace.py
uv run python examples/02_data_pipeline.py
```

## API Usage

### Recording a trace

```python
from doeff import run
from doeff_flow import production_handlers

result = run(production_handlers("wf-001", ".doeff-flow")(my_workflow()))  # trace_dir: str or Path
```

In tests, `mock_handlers()` answers the same effects in memory, and `TraceCapture(format=...)` reads back
what was recorded (`examples/06_testing_with_mocks.py`). `ReadTraces(trace_dir, workflow_id=None)` under
`trace_files_handler` reads the written entries.

### Waiting inside a workflow

A workflow waits by yielding doeff-time's `Delay`, not by calling `time.sleep` (the semgrep rule
`doeff-no-sleep-in-programs` covers `examples/` too). The examples' runner installs
`doeff_time.async_time_handler()` inside `await_handler`, so `Delay`, `GetTime` and the other doeff-time
effects are answered on the wall clock:

```python
from doeff import do
from doeff_time import Delay

@do
def poll_until_ready():
    yield Delay(0.5)  # waits 0.5 s of wall-clock time
```

### With Durable Storage

```python
from doeff import run
from doeff.storage import SQLiteStorage
from doeff_flow import production_handlers

storage = SQLiteStorage(".doeff-cache.db")
result = run(production_handlers("durable-wf")(cache_handler(storage)(my_workflow())))
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
- `--status-only` - Show only slog status updates (simplified display)

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
  "error": null,
  "last_slog": {"status": "reviewing", "msg": "Checking PR"}
}
```

## Workflow Status with slog

For multi-agent workflows with complex, varying states, use `slog` (structured log) to emit
human-readable status messages. These are captured in traces and displayed prominently in the CLI.

### Basic Usage

```python
from doeff import do
from doeff.effects.writer import slog

@do
def my_workflow():
    yield slog(status="starting", msg="Beginning workflow")

    result = yield some_operation()
    yield slog(status="processing", msg=f"Processed {len(result)} items")

    yield slog(status="waiting", msg="Ready for user review")
    return result
```

### Watching with Status Mode

```bash
# Full display with slog status at the top
doeff-flow watch my-workflow

# Simplified display showing only slog status
doeff-flow watch my-workflow --status-only
```

### Example Output

```
[automated-review] Starting PR check
Step 5: RunReviewAgent(...)

[waiting] Ready for user review
```

### Use Cases

The slog pattern is designed for:

- **Multi-agent workflows**: Display which agent/phase is currently active
- **Cyclic workflows**: Track iterations in review → fix → review loops
- **User interaction**: Signal when workflow needs human attention
- **Progress tracking**: Show completion status of long-running operations

See `examples/07_workflow_status.py` for a complete example.

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

### Effects and handlers

| Name | Description |
|------|-------------|
| `TracePush(name, metadata=None)` | Push a trace span |
| `TraceAnnotate(key, value)` | Annotate the current span |
| `TraceSnapshot(label)` | Record a labelled snapshot |
| `TraceCapture(format="json")` | Read back what the active trace handler recorded |
| `ReadTraces(trace_dir, workflow_id=None)` | Read the entries written under a trace directory |
| `production_handlers(workflow_id, trace_dir=None)` | Write each trace effect to `{trace_dir}/{workflow_id}/trace.jsonl` |
| `mock_handlers()` | Record the trace effects in memory (tests) |
| `trace_files_handler` | Answer `ReadTraces` from the trace files |

### Data Types

| Type | Description |
|------|-------------|
| `TraceFrame` | A frame in the effect trace (function, file, line, code) |
| `LiveTrace` | Complete execution snapshot (workflow_id, step, status, trace, ...) |

## Examples

Runnable examples: [examples/](examples/)

## License

MIT
