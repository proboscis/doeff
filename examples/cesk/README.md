# CESK Interpreter Examples

This directory contains examples demonstrating the features of the CESK interpreter.

## Examples

### 01_basic_cesk.py
Basic CESK interpreter usage with the `@do` decorator:
- Simple programs returning values
- Composing `@do` functions
- Pure effect for explicit values
- Reader effect (ask/local)
- State effect (get/put)
- Writer effect (tell/listen)

### 02_error_traceback.py
Error traceback capture and formatting:
- Simple exception with traceback
- Nested effect chain tracing
- Mixed effect and Python frames
- Error handling with catch
- Traceback serialization to dict
- Programmatic traceback access

### 03_durable_workflow.py
Durable workflow execution with cache effects:
- Basic cache operations (cacheget/cacheput)
- Idempotent operation pattern
- Persistent workflows with SQLite
- Cache management (delete, clear)
- Swappable storage backends

### 04_execution_observability.py
Execution monitoring and debugging:
- Step-by-step observation callbacks
- K stack depth tracking
- K frame type inspection
- Current effect observation
- Execution status tracking
- Detailed frame inspection

## Running Examples

```bash
# Run individual examples
python examples/cesk/01_basic_cesk.py
python examples/cesk/02_error_traceback.py
python examples/cesk/03_durable_workflow.py
python examples/cesk/04_execution_observability.py

# Or run all from the project root
for f in examples/cesk/*.py; do python "$f"; done
```

## Prerequisites

All examples use only the `doeff` package. No additional dependencies required.
