# Getting Started with doeff

## Installation

Install doeff using pip:

```bash
pip install doeff
```

The CLI ships with a Rust-powered indexer used for auto-discovery.
When you install `doeff`, the indexer is built automatically.
Make sure a Rust toolchain is available first:

```bash
curl https://sh.rustup.rs -sSf | sh
```

For pinjected integration support:

```bash
pip install doeff-pinjected
```

Or if you're using uv:

```bash
uv add doeff
```

## Requirements

- Python 3.10 or higher
- asyncio support (built into Python)

## Your First Program

Let's write a simple program that uses state management and logging:

```python
import asyncio
from doeff import do, Program, Put, Get, Log
from doeff.runtimes import AsyncioRuntime

@do
def counter_program():
    yield Put("counter", 0)
    yield Log("Counter initialized")
    
    count = yield Get("counter")
    yield Log(f"Current count: {count}")
    
    yield Put("counter", count + 1)
    final_count = yield Get("counter")
    
    yield Log(f"Final count: {final_count}")
    return final_count

async def main():
    runtime = AsyncioRuntime()
    result = await runtime.run(counter_program())
    print(f"Result: {result}")

asyncio.run(main())
```

Output:
```
Result: 1
```

## Understanding the Example

Let's break down what's happening:

1. **`@do` decorator**: Converts a generator function into a reusable `KleisliProgram`
2. **`yield` effects**: Each `yield` suspends execution and requests an effect
3. **Effects**:
   - `Put("counter", value)` - Sets state
   - `Get("counter")` - Retrieves state
   - `Log(message)` - Writes to log
4. **`AsyncioRuntime`**: Executes programs with real async I/O support
5. **Result**: The `run()` method returns the final value directly; use `run_safe()` for Result wrapping

## Key Concepts

### Programs are Lazy

Programs don't execute until you call `runtime.run()`:

```python
@do
def my_program():
    yield Log("This won't execute yet")
    return 42

# Just creates a Program, doesn't execute
program = my_program()

# Now it executes
runtime = AsyncioRuntime()
result = await runtime.run(program)
```

### Programs are Reusable

Unlike Python generators, `@do` functions can be called multiple times:

```python
@do
def get_timestamp():
    import time
    yield Log("Getting timestamp")
    return yield IO(lambda: time.time())

# Each call creates a fresh Program
prog1 = get_timestamp()
prog2 = get_timestamp()

# Each execution is independent
runtime = AsyncioRuntime()
result1 = await runtime.run(prog1)
result2 = await runtime.run(prog2)
```

### Effects are Composable

You can compose programs together:

```python
@do
def setup():
    yield Put("config", {"debug": True})
    yield Log("Setup complete")

@do
def work():
    config = yield Get("config")
    yield Log(f"Working with config: {config}")
    return "done"

@do
def full_program():
    yield setup()  # Run setup first
    result = yield work()  # Then do work
    return result
```

## Running with Initial State

You can provide initial environment and store:

```python
runtime = AsyncioRuntime()

# Pass initial environment and store to runtime.run()
result = await runtime.run(
    my_program(),
    env={"database_url": "postgresql://localhost/mydb"},
    store={"user_id": 123}
)
```

## Error Handling

The `run()` method returns the value directly and raises `EffectError` on failure.
Use `run_safe()` for a `RuntimeResult` that wraps success/failure:

```python
from doeff.runtimes import AsyncioRuntime, EffectError

runtime = AsyncioRuntime()

# Option 1: Direct execution (raises on error)
try:
    result = await runtime.run(my_program())
    print(f"Success: {result}")
except EffectError as e:
    print(f"Error: {e}")

# Option 2: Safe execution (returns RuntimeResult)
result = await runtime.run_safe(my_program())
if result.is_ok:
    print(f"Success: {result.value}")
else:
    print(f"Error: {result.error}")

# Option 3: Pattern matching (Python 3.10+)
match result.result:
    case Ok(value):
        print(f"Success: {value}")
    case Err(error):
        print(f"Error: {error}")
```

## Common Patterns

### Combining Multiple Effects

```python
@do
def complex_workflow():
    # Reader effect - get configuration
    db_url = yield Ask("database_url")
    
    # State effect - manage local state
    yield Put("connection", f"Connected to {db_url}")
    
    # Writer effect - log progress
    yield Log("Processing data...")
    
    # Async effect - await async operations
    data = yield Await(fetch_data_async())
    
    # Return final result
    return len(data)
```

### Conditional Logic

```python
@do
def conditional_program():
    count = yield Get("count")
    
    if count > 10:
        yield Log("Count is high")
        yield Put("status", "high")
    else:
        yield Log("Count is low")
        yield Put("status", "low")
    
    return count
```

## Troubleshooting

### "AttributeError: 'generator' object has no attribute '...'"

Make sure you're using `@do` decorator:

```python
# Wrong - missing @do
def my_program():
    yield Log("test")

# Right - has @do
@do
def my_program():
    yield Log("test")
```

### "TypeError: Unknown yield type"

Only yield `Effect` or `Program` instances:

```python
# Wrong - yielding a plain value
@do
def wrong():
    value = yield 42  # Error!

# Right - wrap in Program.pure()
@do
def right():
    value = yield Program.pure(42)  # OK
    # Or just return it
    return 42
```

### "RuntimeError: cannot reuse already awaited coroutine"

Don't reuse Program objects after running them with sub-effects:

```python
# If you need to run a program multiple times, call the function again
runtime = AsyncioRuntime()
prog = my_program()
result1 = await runtime.run(prog)
# Don't reuse prog - create a new one
prog2 = my_program()
result2 = await runtime.run(prog2)
```

## Next Steps

Now that you understand the basics, explore:

- **[Core Concepts](02-core-concepts.md)** - Deep dive into Program, Effect, and execution model
- **[Basic Effects](03-basic-effects.md)** - Reader, State, Writer effects
- **[Async Effects](04-async-effects.md)** - Parallel execution and futures
- **[Error Handling](05-error-handling.md)** - Result, Safe for error handling

## Quick Reference

### Common Imports

```python
from doeff import (
    do,                    # Decorator for creating programs
    Program,               # Program type
    
    # State effects
    Get, Put, Modify,
    
    # Reader effects
    Ask, Local,
    
    # Writer effects
    Log, Tell, Listen,
    
    # Async effects
    Await, Parallel,
    
    # Error handling
    Safe,
    
    # IO effects
    IO,
    
    # Result types
    Ok, Err, Result,
)

# Runtime types
from doeff.runtimes import (
    AsyncioRuntime,        # For async I/O (HTTP, DB, files)
    SyncRuntime,           # For pure synchronous execution
    SimulationRuntime,     # For testing with simulated time
)
```

### Basic Program Template

```python
from doeff import do, Log
from doeff.runtimes import AsyncioRuntime
import asyncio

@do
def my_program():
    # Your effects here
    yield Log("Hello, doeff!")
    return "result"

async def main():
    runtime = AsyncioRuntime()
    result = await runtime.run(my_program())
    print(result)

if __name__ == "__main__":
    asyncio.run(main())
```
