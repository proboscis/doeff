# Getting Started with doeff

doeff is an **algebraic effects** system for Python. You write programs that *perform* effects (read config, update state, log messages), and **handlers** interpret those effects. The runtime uses **one-shot continuations** backed by a **Rust VM** for performance.

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
from doeff import do, Program, Put, Get, Tell
from doeff import run, default_handlers

@do
def counter_program():
    yield Put("counter", 0)
    yield Tell("Counter initialized")

    count = yield Get("counter")
    yield Tell(f"Current count: {count}")

    yield Put("counter", count + 1)
    final_count = yield Get("counter")

    yield Tell(f"Final count: {final_count}")
    return final_count

def main():
    result = run(counter_program(), default_handlers())
    print(f"Result: {result.value}")

main()
```

Output:
```
Result: 1
```

## Understanding the Example

Let's break down what's happening:

1. **`@do` decorator**: Converts a generator function into a reusable `KleisliProgram`
2. **`yield` effects**: Each `yield` *performs* an algebraic effect — the program suspends and the handler processes it
3. **Effects** (algebraic effect operations):
   - `Put("counter", value)` - State effect: sets state (handled by the State handler)
   - `Get("counter")` - State effect: retrieves state
   - `Tell(message)` - Writer effect: appends to log (handled by the Writer handler)
4. **`run`**: Executes programs with the provided effect handlers
5. **Result**: Returns a `RuntimeResult` with `.value` for success or `.error` for failure

## Key Concepts

### Programs are Lazy

Programs don't execute until you call `run()` or `async_run()`:

```python
@do
def my_program():
    yield Tell("This won't execute yet")
    return 42

# Just creates a Program, doesn't execute
program = my_program()

# Now it executes
result = run(program, default_handlers())
```

### Programs are Reusable

Unlike Python generators, `@do` functions can be called multiple times:

```python
@do
def get_timestamp():
    import time
    yield Tell("Getting timestamp")
    return yield IO(lambda: time.time())

# Each call creates a fresh Program
prog1 = get_timestamp()
prog2 = get_timestamp()

# Each execution is independent
result1 = run(prog1, default_handlers())
result2 = run(prog2, default_handlers())
```

### Effects are Composable

You can compose effectful programs together — a key advantage of algebraic effects:

```python
@do
def setup():
    yield Put("config", {"debug": True})
    yield Tell("Setup complete")

@do
def work():
    config = yield Get("config")
    yield Tell(f"Working with config: {config}")
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
from doeff import run, default_handlers

# Pass initial environment and store
result = run(
    my_program(),
    default_handlers(),
    env={"database_url": "postgresql://localhost/mydb"},
    store={"user_id": 123}
)
```

## Error Handling

`run()` and `async_run()` always return a `RuntimeResult`:

```python
from doeff import run, default_handlers

result = run(my_program(), default_handlers())

# Check success with methods (use parentheses!)
if result.is_ok():
    print(f"Success: {result.value}")
else:
    print(f"Error: {result.error}")

# Pattern matching (Python 3.10+)
from doeff import Ok, Err

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
    yield Tell("Processing data...")

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
        yield Tell("Count is high")
        yield Put("status", "high")
    else:
        yield Tell("Count is low")
        yield Put("status", "low")

    return count
```

## Troubleshooting

### "AttributeError: 'generator' object has no attribute '...'"

Make sure you're using `@do` decorator:

```python
# Wrong - missing @do
def my_program():
    yield Tell("test")

# Right - has @do
@do
def my_program():
    yield Tell("test")
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
prog = my_program()
result1 = run(prog, default_handlers())
# Don't reuse prog - create a new one
prog2 = my_program()
result2 = run(prog2, default_handlers())
```

## Next Steps

Now that you understand the basics, explore:

- **[Core Concepts](02-core-concepts.md)** - Algebraic effects, handlers, one-shot continuations, and the Rust VM
- **[Basic Effects](03-basic-effects.md)** - Reader, State, Writer effects
- **[Async Effects](04-async-effects.md)** - Parallel execution and futures
- **[Error Handling](05-error-handling.md)** - Result, Try for error handling

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
    Tell, Listen,

    # Async effects
    Await, Gather,

    # Error handling
    Try,

    # IO effects
    IO,

    # Result types
    Ok, Err, Result,

    # Execution functions
    run, async_run,
    default_handlers, default_async_handlers,
)
```

### Basic Program Template

```python
from doeff import do, Tell, run, default_handlers

@do
def my_program():
    # Your effects here
    yield Tell("Hello, doeff!")
    return "result"

def main():
    result = run(my_program(), default_handlers())
    print(result.value)

if __name__ == "__main__":
    main()
```
