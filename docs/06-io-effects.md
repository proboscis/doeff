# IO Effects

IO effects allow you to perform side effects in a controlled way within the doeff system.

## Table of Contents

- [IO Effect](#io-effect)
- [Print Effect](#print-effect)
- [Best Practices](#best-practices)

## IO Effect

`IO(action)` executes a callable and returns its result.

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
    yield Log(f"Read {len(content)} bytes")
    return content

@do
def write_file(filename, content):
    yield IO(lambda: open(filename, 'w').write(content))
    yield Log(f"Wrote {len(content)} bytes to {filename}")
```

### Current Time

```python
import time

@do
def get_timestamp():
    timestamp = yield IO(lambda: time.time())
    yield Log(f"Timestamp: {timestamp}")
    return timestamp
```

### Random Numbers

```python
import random

@do
def roll_dice():
    value = yield IO(lambda: random.randint(1, 6))
    yield Log(f"Rolled: {value}")
    return value
```

### Environment Variables

```python
import os

@do
def get_env_var(key):
    value = yield IO(lambda: os.environ.get(key))
    if value is None:
        yield Fail(KeyError(f"Environment variable {key} not found"))
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
        yield Log(f"Command failed: {result.stderr}")
        yield Fail(Exception(f"Command failed with code {result.returncode}"))
    
    return result.stdout
```

## Print Effect

`Print(message)` prints to stdout/stderr.

### Basic Printing

```python
@do
def with_print():
    yield Print("Hello, world!")
    yield Print("Processing data...")
    result = yield process_data()
    yield Print(f"Result: {result}")
    return result
```

### Debugging

```python
@do
def debug_program():
    x = yield Get("x")
    yield Print(f"DEBUG: x = {x}")
    
    y = x * 2
    yield Print(f"DEBUG: y = {y}")
    
    yield Put("result", y)
    yield Print("DEBUG: Result stored")
    
    return y
```

### Progress Output

```python
@do
def process_items(items):
    results = []
    total = len(items)
    
    for i, item in enumerate(items):
        yield Print(f"Processing {i+1}/{total}...")
        result = yield process_item(item)
        results.append(result)
    
    yield Print("All items processed!")
    return results
```

## Best Practices

### When to Use IO

**DO:**
- File I/O
- System calls
- Current time/date
- Random number generation
- External library calls with side effects

```python
@do
def good_io_usage():
    # File operations
    data = yield IO(lambda: json.load(open('config.json')))
    
    # Current time
    timestamp = yield IO(lambda: datetime.now())
    
    # Random values
    rand_val = yield IO(lambda: random.random())
    
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

Mock IO effects in tests:

```python
# In tests
class MockInterpreter(ProgramInterpreter):
    def __init__(self):
        super().__init__()
        self.io_calls = []
    
    async def _handle_effect(self, effect, context):
        if isinstance(effect, IOPerformEffect):
            self.io_calls.append(effect)
            return "mocked_result"
        return await super()._handle_effect(effect, context)
```

### Print vs Log

**Use Print for:**
- User-facing output
- CLI progress messages
- Interactive prompts
- Actual terminal output

**Use Log for:**
- Debugging information
- Audit trails
- Internal program state
- Structured logs

```python
@do
def good_separation():
    # Log for internal tracking
    yield Log("Starting operation...")
    
    # Print for user visibility
    yield Print("Processing your request...")
    
    result = yield do_work()
    
    # Log the details
    yield Log(f"Operation completed with result: {result}")
    
    # Print the summary
    yield Print("Done!")
    
    return result
```

## Summary

| Effect | Purpose | Example |
|--------|---------|---------|
| `IO(action)` | Execute side-effectful callable | File I/O, system calls, time |
| `Print(msg)` | Print to output stream | User messages, progress |

**Key Points:**
- IO isolates side effects for testability
- Use IO for non-async side effects
- Print for user-facing output, Log for internal tracking
- Keep IO actions small and focused

## Next Steps

- **[Cache System](07-cache-system.md)** - Caching with policies
- **[Patterns](12-patterns.md)** - Common IO patterns
- **[Testing](12-patterns.md#testing-patterns)** - Testing programs with IO