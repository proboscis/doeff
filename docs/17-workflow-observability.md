# Workflow Observability with doeff-flow

When running durable or long-running workflows with doeff, you need visibility into execution state. The `doeff-flow` package provides live effect trace observability, letting you watch workflows execute in real-time.

## Installation

```bash
pip install doeff-flow
```

Or with uv:

```bash
uv add doeff-flow
```

## Why Workflow Observability?

When debugging or monitoring workflows, you need to know:

- **Where am I?** Which `@do` function is currently executing
- **How did I get here?** The call chain (effect trace / K stack)
- **What's happening?** The current effect being processed
- **Is it stuck?** Whether execution is progressing

`doeff-flow` writes this information to JSONL files that can be watched in real-time from the CLI or external tools.

## Quick Start

### Option 1: Using `run_workflow` (Recommended)

The simplest way to add observability:

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
    workflow_id="my-wf-001",
    trace_dir=Path(".doeff-flow"),
)
print(result.value)  # 30
```

### Option 2: Using `trace_observer` (Composable)

For more control, use the context manager directly:

```python
from doeff.cesk import run_sync
from doeff_flow import trace_observer

with trace_observer("my-wf-001", ".doeff-flow") as on_step:
    result = run_sync(
        my_workflow(),
        env={"config": "value"},  # Custom environment
        on_step=on_step,
    )
```

## Watching Workflows

### Live Watch

In one terminal, run your workflow:

```bash
python my_workflow.py
```

In another terminal, watch it execute:

```bash
doeff-flow watch my-wf-001
```

You'll see a live display like:

```
┌─ my-wf-001 [running] step 42 ──────────────────────┐
│                                                     │
│  main_workflow         workflow.py:100              │
│    └─ process_step     workflow.py:45               │
│         └─ call_api    api.py:23                    │
│              ↳ CacheGet(key='api_result')           │
│                                                     │
│  Updated: 12:00:00.123                              │
└─────────────────────────────────────────────────────┘
```

### List All Workflows

```bash
doeff-flow ps
```

Output:
```
my-wf-001     running     step 42
my-wf-002     completed   step 100
my-wf-003     failed      step 15
```

### View History

```bash
doeff-flow history my-wf-001 --last 20
```

Output:
```
step    1  running     Pure(10)
step    2  running     CacheGet(key='config')
step    3  running     Pure(20)
...
step   42  completed   -
```

## Tutorial: Building an Observable Data Pipeline

Let's build a real-world example: a data pipeline that fetches, processes, and aggregates data.

### Step 1: Define the Workflow

```python
# pipeline.py
from doeff import do
from doeff.effects import Pure
from doeff_flow import run_workflow
from pathlib import Path
import time

@do
def fetch_data(source: str):
    """Simulate fetching data from a source."""
    time.sleep(0.1)  # Simulate network latency
    data = yield Pure({"source": source, "items": list(range(10))})
    return data

@do
def process_item(item: int):
    """Process a single item."""
    time.sleep(0.05)  # Simulate processing
    result = yield Pure(item * 2)
    return result

@do
def aggregate(results: list[int]):
    """Aggregate all results."""
    total = yield Pure(sum(results))
    return total

@do
def data_pipeline():
    """Main pipeline that orchestrates the workflow."""
    # Step 1: Fetch data
    data = yield fetch_data("api/users")

    # Step 2: Process each item
    processed = []
    for item in data["items"]:
        result = yield process_item(item)
        processed.append(result)

    # Step 3: Aggregate
    total = yield aggregate(processed)

    return {"source": data["source"], "total": total}

if __name__ == "__main__":
    result = run_workflow(
        data_pipeline(),
        workflow_id="pipeline-001",
        trace_dir=Path(".doeff-flow"),
    )
    print(f"Result: {result.value}")
```

### Step 2: Run and Watch

Terminal 1:
```bash
python pipeline.py
```

Terminal 2:
```bash
doeff-flow watch pipeline-001 --exit-on-complete
```

You'll see the call stack grow and shrink as the workflow progresses through `fetch_data` → `process_item` (10 times) → `aggregate`.

### Step 3: Analyze the Trace

After completion, examine the trace:

```bash
# See all steps
doeff-flow history pipeline-001 --last 50

# Or inspect the raw JSONL
cat .doeff-flow/pipeline-001/trace.jsonl | jq .
```

## Advanced Usage

### Running Multiple Concurrent Workflows

```python
import threading
from doeff_flow import run_workflow

def run_pipeline(pipeline_id: str):
    result = run_workflow(
        data_pipeline(),
        workflow_id=f"pipeline-{pipeline_id}",
        trace_dir=Path(".doeff-flow"),
    )
    print(f"Pipeline {pipeline_id}: {result.value}")

# Run 3 pipelines concurrently
threads = [
    threading.Thread(target=run_pipeline, args=(f"{i:03d}",))
    for i in range(3)
]

for t in threads:
    t.start()
for t in threads:
    t.join()
```

Watch all of them:
```bash
doeff-flow ps --trace-dir .doeff-flow
```

### Using with Custom Environments

```python
from doeff.effects import Ask
from doeff_flow import trace_observer
from doeff.cesk import run_sync

@do
def workflow_with_config():
    config = yield Ask("database_url")
    # ... use config
    return f"Connected to {config}"

with trace_observer("config-wf", ".doeff-flow") as on_step:
    result = run_sync(
        workflow_with_config(),
        env={"database_url": "postgres://localhost/mydb"},
        on_step=on_step,
    )
```

### Using with State Effects

```python
from doeff.effects import Get, Put

@do
def stateful_workflow():
    yield Put("counter", 0)

    for i in range(5):
        current = yield Get("counter")
        yield Put("counter", current + 1)

    final = yield Get("counter")
    return final

result = run_workflow(
    stateful_workflow(),
    workflow_id="stateful-001",
)
```

### Using with Durable Storage

```python
from doeff.storage import SQLiteStorage

result = run_workflow(
    my_workflow(),
    workflow_id="durable-001",
    trace_dir=".doeff-flow",
    storage=SQLiteStorage(".doeff-cache.db"),  # Enable caching
)
```

## Trace File Format

Traces are stored as JSONL (JSON Lines) files:

```
.doeff-flow/
├── workflow-001/
│   └── trace.jsonl
├── workflow-002/
│   └── trace.jsonl
└── ...
```

Each line is a JSON object representing a snapshot:

```json
{
  "workflow_id": "workflow-001",
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

**Benefits of JSONL:**
- Append-only (no atomic rename needed)
- Full execution history preserved
- `tail -f` friendly for live watching
- Easy to process with `jq`, Python, etc.

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
) -> CESKResult[T]
```

Run a workflow with live trace observability.

**Parameters:**
- `program`: The doeff program to execute
- `workflow_id`: Unique identifier (must match `[a-zA-Z0-9_-]+`, max 255 chars)
- `trace_dir`: Directory for trace files (default: `.doeff-flow`)
- `env`: Initial environment
- `store`: Initial store
- `storage`: Durable storage backend

### `trace_observer()`

```python
@contextmanager
def trace_observer(
    workflow_id: str,
    trace_dir: Path | str,
) -> Generator[Callable[[ExecutionSnapshot], None], None, None]
```

Context manager that creates an `on_step` callback.

### `validate_workflow_id()`

```python
def validate_workflow_id(workflow_id: str) -> str
```

Validate a workflow ID. Raises `ValueError` if invalid.

### Data Types

- `TraceFrame`: A frame in the effect trace
- `LiveTrace`: Current execution state

## CLI Reference

### `doeff-flow watch`

```bash
doeff-flow watch WORKFLOW_ID [OPTIONS]
```

**Options:**
- `--trace-dir PATH`: Directory containing traces (default: `.doeff-flow`)
- `--exit-on-complete`: Exit when workflow completes or fails
- `--poll-interval FLOAT`: Poll interval in seconds (default: 0.1)

### `doeff-flow ps`

```bash
doeff-flow ps [OPTIONS]
```

**Options:**
- `--trace-dir PATH`: Directory containing traces

### `doeff-flow history`

```bash
doeff-flow history WORKFLOW_ID [OPTIONS]
```

**Options:**
- `--trace-dir PATH`: Directory containing traces
- `--last N`: Show last N steps (default: 10)

## Best Practices

1. **Use descriptive workflow IDs**: Include timestamps or UUIDs for uniqueness
   ```python
   workflow_id = f"pipeline-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
   ```

2. **Clean up old traces**: Trace files grow over time
   ```bash
   find .doeff-flow -mtime +7 -delete  # Delete traces older than 7 days
   ```

3. **Use `--exit-on-complete` in scripts**: Prevents watch from hanging
   ```bash
   doeff-flow watch my-wf --exit-on-complete
   ```

4. **Add `.doeff-flow` to `.gitignore`**: Traces are local debugging artifacts
   ```
   # .gitignore
   .doeff-flow/
   ```

## Troubleshooting

### "No trace found for workflow_id"

The workflow hasn't started yet or the trace directory is wrong. Check:
```bash
ls -la .doeff-flow/
```

### Watch shows nothing

The workflow may be completing too quickly. Try adding `--poll-interval 0.01` for faster polling, or use `--exit-on-complete`.

### Invalid workflow_id error

Workflow IDs must match `[a-zA-Z0-9_-]+` and be at most 255 characters. Avoid special characters, spaces, and path separators.