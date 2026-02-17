# Advanced Effects

Advanced effects for parallel Program execution, background tasks, and atomic operations.

## Gather Effects

Execute multiple Programs in parallel and collect results.

### Gather - Parallel Programs

```python
from doeff import Gather, do

@do
def parallel_programs():
    prog1 = fetch_user(1)
    prog2 = fetch_user(2)
    prog3 = fetch_user(3)
    
    # Run all Programs in parallel
    users = yield Gather(prog1, prog2, prog3)
    # users = [user1, user2, user3]
    
    return users
```

### User-Side Dict Pattern

If you need to run a dict of Programs in parallel, use `Gather` with dict reconstruction:

```python
@do
def parallel_dict():
    programs = {
        "user": fetch_user(123),
        "posts": fetch_posts(123),
        "comments": fetch_comments(123)
    }
    keys = list(programs.keys())
    values = yield Gather(*programs.values())
    results = dict(zip(keys, values))
    
    # results = {"user": ..., "posts": [...], "comments": [...]}
    return results
```

### Using Gather with Async Operations

To run async operations in parallel, wrap them with `Await`:

```python
@do
def parallel_async():
    # Create Programs that wrap async functions
    @do
    def fetch_data(url):
        return (yield Await(http_get(url)))

    # Run multiple Programs in parallel
    results = yield Gather(
        fetch_data("https://api1.example.com"),
        fetch_data("https://api2.example.com")
    )
    return results
```

## Atomic Effects

Thread-safe state operations.

### AtomicGet

```python
@do
def atomic_read():
    # Thread-safe state read
    count = yield AtomicGet("counter")
    yield Tell(f"Counter: {count}")
    return count
```

### AtomicUpdate

```python
@do
def atomic_increment():
    # Thread-safe atomic update
    new_value = yield AtomicUpdate("counter", lambda x: x + 1)
    yield Tell(f"Incremented to: {new_value}")
    return new_value
```

### Atomic vs Regular State

```python
# Race condition with regular state
@do
def unsafe_increment():
    count = yield Get("counter")  # Read
    # Another thread could modify here!
    yield Put("counter", count + 1)  # Write

# Safe with atomic update
@do
def safe_increment():
    new_count = yield AtomicUpdate("counter", lambda x: x + 1)
    return new_count
```

### Use Cases for Atomic Effects

**Counters:**
```python
@do
def increment_request_counter():
    count = yield AtomicUpdate("requests", lambda x: x + 1)
    yield Tell(f"Request #{count}")
```

**Flags:**
```python
@do
def claim_resource():
    claimed = yield AtomicUpdate(
        "resource_claimed",
        lambda x: True if not x else x
    )
    if not claimed:
        yield Tell("Successfully claimed resource")
        return True
    else:
        yield Tell("Resource already claimed")
        return False
```

**Accumulators:**
```python
@do
def accumulate_result(value):
    total = yield AtomicUpdate(
        "total",
        lambda current: current + value
    )
    return total
```

## Spawn Effect

Execute Programs in the background and retrieve results later.

### Basic Spawn

```python
from doeff import Spawn, do

@do
def background_work():
    # Spawn a background task
    task = yield Spawn(expensive_computation())
    
    # Do other work while task runs
    yield Tell("Doing other work...")
    other_result = yield quick_operation()
    
    # Wait for background task to complete
    background_result = yield Wait(task)
    
    return (other_result, background_result)
```

### Multiple Background Tasks

```python
@do
def parallel_background_work():
    # Spawn multiple tasks
    task1 = yield Spawn(computation_1())
    task2 = yield Spawn(computation_2())
    task3 = yield Spawn(computation_3())
    
    # Wait for all to complete
    result1 = yield Wait(task1)
    result2 = yield Wait(task2)
    result3 = yield Wait(task3)
    
    return [result1, result2, result3]
```

### Spawn vs Gather

| Effect | Execution | Use Case |
|--------|-----------|----------|
| `Gather(*progs)` | Parallel, blocking | Wait for all immediately |
| `Spawn(prog)` | Background, non-blocking | Do other work while waiting |

```python
@do
def comparison():
    # Gather: blocks until all complete
    results = yield Gather(prog1(), prog2(), prog3())
    
    # Spawn: non-blocking, can do work in between
    task = yield Spawn(slow_prog())
    yield do_other_work()  # Runs while task executes
    result = yield Wait(task)  # Now wait for it
```

## Combining Advanced Effects

### Gather + Atomic

```python
@do
def parallel_counter():
    # Multiple parallel operations updating a counter
    yield Put("count", 0)
    
    @do
    def increment_task(n):
        for _ in range(n):
            yield AtomicUpdate("count", lambda x: x + 1)
        return "done"
    
    # Run in parallel - atomic updates prevent race conditions
    yield Gather(
        increment_task(100),
        increment_task(100),
        increment_task(100)
    )
    
    final = yield Get("count")
    # final = 300 (not less due to race conditions)
    return final
```

## Best Practices

### Accessing the interpreter

If you need the active interpreter instance (for example, to pass it into an
external framework callback), ask for the special key `__interpreter__`:

```python
interp = yield Ask("__interpreter__")
# interp is the ProgramInterpreter currently running this Program.
```

This does not require adding `__interpreter__` to your environment; the
interpreter responds to this key directly.

### When to Use Gather

**DO:**
- Multiple independent Programs
- Fan-out computation patterns
- Parallel data fetching

**DON'T:**
- Dependent computations (use sequential yields)
- Single Program (just yield it directly)

### When to Use Atomic

**DO:**
- Concurrent state updates
- Counters and flags
- Thread-safe operations

**DON'T:**
- Single-threaded programs (overhead unnecessary)
- Complex transactions (use proper DB transactions)

## Summary

| Effect | Purpose | Use Case |
|--------|---------|----------|
| `Gather(*progs)` | Parallel Programs | Fan-out computation |
| `Spawn(prog)` | Background execution | Non-blocking tasks |
| `AtomicGet(key)` | Thread-safe read | Concurrent reads |
| `AtomicUpdate(key, f)` | Thread-safe update | Concurrent modifications |

## Next Steps

- **[Async Effects](04-async-effects.md)** - Async operations with Await and Gather
- **[Cache System](07-cache-system.md)** - Persistent caching
- **[Patterns](12-patterns.md)** - Advanced patterns and best practices