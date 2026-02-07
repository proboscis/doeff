# Graph Tracking

doeff can track program execution as a graph for visualization and debugging.

## Graph Effects

### Step - Add Named Step

```python
@do
def with_steps():
    yield Step("initialize", "Setup phase")
    yield Put("ready", True)

    yield Step("process", "Processing data")
    result = yield process_data()

    yield Step("finalize", "Cleanup")
    yield cleanup()

    return result
```

### Annotate - Add Metadata

```python
@do
def with_annotations():
    yield Annotate({"user_id": 123, "operation": "fetch"})
    data = yield fetch_data()
    yield Annotate({"records": len(data)})
    return data
```

### Snapshot - Capture Current Graph

```python
@do
def with_snapshot():
    yield Step("start")

    # Capture graph at this point
    graph = yield Snapshot()
    yield Log(f"Graph has {len(graph.steps)} steps")

    yield Step("continue")
    return "done"
```

### CaptureGraph - Get Final Graph

```python
from doeff import run, default_handlers

@do
def traced_program():
    yield Step("step1")
    yield Step("step2")
    yield Step("step3")
    return "result"

result = run(traced_program(), default_handlers())
# Graph is tracked during execution in result.raw_store.get("__graph__")
```

## Visualization

### Export to HTML

```python
from doeff import graph_to_html, run, default_handlers

# Run program with graph tracking
result = run(my_program(), default_handlers())

# Get graph from raw_store
graph = result.raw_store.get("__graph__")
if graph:
    # graph_to_html is a @do function, run it to get HTML
    html_result = run(graph_to_html(graph), default_handlers())
    html = html_result.value
```

### Getting the Graph

```python
from doeff import run, default_handlers

result = run(my_program(), default_handlers())

# Access graph from raw_store
graph = result.raw_store.get("__graph__")
```

## Use Cases

### Debugging Complex Workflows

```python
@do
def complex_workflow():
    yield Step("load_config")
    config = yield load_config()
    yield Annotate({"config_keys": list(config.keys())})

    yield Step("validate_inputs")
    valid = yield validate(config)
    yield Annotate({"validation_passed": valid})

    if valid:
        yield Step("process_data")
        result = yield process(config)
        yield Annotate({"result_size": len(result)})
    else:
        yield Step("error_handling")
        result = []

    yield Step("complete")
    return result
```

### Performance Analysis

```python
@do
def timed_operations():
    import time

    yield Step("operation_a")
    start = yield IO(lambda: time.time())
    yield expensive_a()
    duration_a = yield IO(lambda: time.time() - start)
    yield Annotate({"duration_a": duration_a})

    yield Step("operation_b")
    start = yield IO(lambda: time.time())
    yield expensive_b()
    duration_b = yield IO(lambda: time.time() - start)
    yield Annotate({"duration_b": duration_b})
```

## Graph Structure

The execution graph is a `WGraph` with:

```python
@dataclass
class WGraph:
    last: WStep          # Most recent step
    steps: frozenset[WStep]  # All steps

@dataclass
class WStep:
    inputs: tuple[WNode, ...]  # Input nodes
    output: WNode              # Output node
    meta: dict                 # Metadata
```

## Best Practices

### Strategic Step Placement

```python
# Good: steps at logical boundaries
@do
def well_traced():
    yield Step("phase1")
    yield do_phase1()

    yield Step("phase2")
    yield do_phase2()

# Less useful: too granular
@do
def over_traced():
    yield Step("increment")
    x = yield Get("x")
    yield Step("add_one")
    y = x + 1
    yield Step("store")
    yield Put("x", y)
```

### Meaningful Annotations

```python
# Good: useful context
yield Annotate({
    "user_id": user_id,
    "records_processed": count,
    "cache_hit_rate": hits/total
})

# Less useful: redundant info
yield Annotate({"step": "processing"})  # Use Step instead
```

## Next Steps

- **[Patterns](12-patterns.md)** - Graph tracking patterns
- **[Advanced Effects](09-advanced-effects.md)** - Gather for parallel execution
- **[MARKERS.md](MARKERS.md)** - Marker system for Program manipulation