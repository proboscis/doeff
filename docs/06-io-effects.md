# IO Effects

IO effects allow you to perform side effects in a controlled way within the doeff system.

## Table of Contents

- [IO Effect](#io-effect)
- [Common IO Patterns](#common-io-patterns)
- [Best Practices](#best-practices)

## IO Effect

`IO(action)` executes a callable and returns its result. This is the primary way to perform synchronous side effects in doeff programs.

### Basic Usage

```python
from doeff import do, IO

@do
def with_io():
    # Execute side-effectful code
    result = yield IO(lambda: compute_something())
    return result

def compute_something():
    # Regular Python code with side effects
    print("Computing...")
    return 42
```

### File Operations

```python
@do
def read_file(filename):
    content = yield IO(lambda: open(filename).read())
    yield Tell(f"Read {len(content)} bytes")
    return content

@do
def write_file(filename, content):
    yield IO(lambda: open(filename, 'w').write(content))
    yield Tell(f"Wrote {len(content)} bytes to {filename}")
```

### Current Time

```python
import time

@do
def get_timestamp():
    timestamp = yield IO(lambda: time.time())
    yield Tell(f"Timestamp: {timestamp}")
    return timestamp
```

### Random Numbers

```python
import random

@do
def roll_dice():
    value = yield IO(lambda: random.randint(1, 6))
    yield Tell(f"Rolled: {value}")
    return value
```

### Environment Variables

```python
import os

@do
def get_env_var(key):
    value = yield IO(lambda: os.environ.get(key))
    if value is None:
        raise KeyError(f"Environment variable {key} not found")
    return value
```

### System Commands

```python
import subprocess

@do
def run_command(cmd):
    result = yield IO(lambda: subprocess.run(
        cmd,
        shell=True,
        capture_output=True,
        text=True
    ))

    if result.returncode != 0:
        yield Tell(f"Command failed: {result.stderr}")
        raise Exception(f"Command failed with code {result.returncode}")

    return result.stdout
```

## Common IO Patterns

### Printing to Console

Use native Python `print()` wrapped in IO for console output:

```python
@do
def with_console_output():
    yield IO(lambda: print("Hello, world!"))
    yield IO(lambda: print("Processing data..."))
    result = yield process_data()
    yield IO(lambda: print(f"Result: {result}"))
    return result
```

For internal logging, prefer the `Tell` effect which captures logs in the execution context:

```python
@do
def with_logging():
    yield Tell("Starting operation...")  # Captured in logs
    result = yield process_data()
    yield Tell(f"Completed with: {result}")  # Captured in logs
    return result
```

### Progress Output

```python
@do
def process_items(items):
    results = []
    total = len(items)

    for i, item in enumerate(items):
        yield IO(lambda i=i: print(f"Processing {i+1}/{total}..."))
        result = yield process_item(item)
        results.append(result)

    yield IO(lambda: print("All items processed!"))
    return results
```

### Debugging

```python
@do
def debug_program():
    x = yield Get("x")
    yield IO(lambda: print(f"DEBUG: x = {x}"))

    y = x * 2
    yield IO(lambda: print(f"DEBUG: y = {y}"))

    yield Put("result", y)
    yield IO(lambda: print("DEBUG: Result stored"))

    return y
```

## Best Practices

### When to Use IO

**DO:**
- File I/O
- System calls
- Current time/date
- Random number generation
- External library calls with side effects
- Console output

```python
@do
def good_io_usage():
    # File operations
    data = yield IO(lambda: json.load(open('config.json')))

    # Current time
    timestamp = yield IO(lambda: datetime.now())

    # Random values
    rand_val = yield IO(lambda: random.random())

    # Console output
    yield IO(lambda: print(f"Loaded config at {timestamp}"))

    return {"data": data, "time": timestamp, "random": rand_val}
```

**DON'T:**
- Pure computations (use direct Python instead)
- Async operations (use Await instead)
- State access (use Get/Put instead)

```python
@do
def bad_io_usage():
    # Bad: pure computation doesn't need IO
    result = yield IO(lambda: 2 + 2)  # Just use: result = 4

    # Bad: async operation should use Await
    data = yield IO(lambda: asyncio.run(fetch_data()))  # Use Await instead

    # Bad: state access should use Get
    value = yield IO(lambda: some_global_state)  # Use Get instead
```

### IO Isolation

Wrap side effects in IO to track them:

```python
@do
def trackable_side_effects():
    # All side effects are explicit
    yield IO(lambda: database.connect())
    yield IO(lambda: database.execute(query))
    result = yield IO(lambda: database.fetch_all())
    yield IO(lambda: database.close())
    return result
```

### Testing IO Effects

Mock IO effects in tests by providing custom handlers:

```python
from doeff import Program, default_handlers, run

# Create a mock IO handler
def mock_io_handler(effect, k, env, store):
    if isinstance(effect, IO):
        return Program.pure("mocked_result")
    return None  # Not handled

# Prepend custom handler to preset
handlers = [mock_io_handler, *default_handlers()]
result = run(my_program(), handlers)
```

### Console Output vs Writer Log

**Use `IO(print)` for:**
- User-facing output
- CLI progress messages
- Interactive prompts
- Actual terminal output

**Use `Tell` for:**
- Debugging information
- Audit trails
- Internal program state
- Structured logs captured in execution context

```python
@do
def good_separation():
    # Tell for internal tracking (captured in context)
    yield Tell("Starting operation...")

    # print via IO for user visibility (goes to stdout)
    yield IO(lambda: print("Processing your request..."))

    result = yield do_work()

    # Tell the details (captured in context)
    yield Tell(f"Operation completed with result: {result}")

    # print the summary (goes to stdout)
    yield IO(lambda: print("Done!"))

    return result
```

## Summary

| Effect | Purpose | Example |
|--------|---------|---------|
| `IO(action)` | Execute side-effectful callable | File I/O, system calls, time, console output |
| `Tell(msg)` | Internal logging (captured) | Debugging, audit trails |

**Key Points:**
- IO isolates side effects for testability
- Use IO for non-async side effects including console output
- Use `IO(lambda: print(...))` for user-facing output
- Use `Tell` for internal tracking captured in execution context
- Keep IO actions small and focused

## Next Steps

- **[Cache System](07-cache-system.md)** - Caching with policies
- **[Patterns](12-patterns.md)** - Common IO patterns
- **[Testing](12-patterns.md#testing-patterns)** - Testing programs with IO
