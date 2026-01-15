# Advanced Effects

Advanced effects for parallel Program execution, memoization, and atomic operations.

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

### Gather vs Parallel

| Effect | Purpose | Input Type |
|--------|---------|------------|
| `Parallel(*awaitables)` | Parallel async operations | `Awaitable` |
| `Gather(*programs)` | Parallel Programs | `Program` |

```python
@do
def comparison():
    # Parallel: for async functions
    async_results = yield Parallel(
        async_func1(),
        async_func2()
    )
    
    # Gather: for Programs
    program_results = yield Gather(
        program1(),
        program2()
    )
```

## Atomic Effects

Thread-safe state operations.

### AtomicGet

```python
@do
def atomic_read():
    # Thread-safe state read
    count = yield AtomicGet("counter")
    yield Log(f"Counter: {count}")
    return count
```

### AtomicUpdate

```python
@do
def atomic_increment():
    # Thread-safe atomic update
    new_value = yield AtomicUpdate("counter", lambda x: x + 1)
    yield Log(f"Incremented to: {new_value}")
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
    yield Log(f"Request #{count}")
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
        yield Log("Successfully claimed resource")
        return True
    else:
        yield Log("Resource already claimed")
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
interp = yield ask("__interpreter__")
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
| `AtomicGet(key)` | Thread-safe read | Concurrent reads |
| `AtomicUpdate(key, f)` | Thread-safe update | Concurrent modifications |

## Next Steps

- **[Async Effects](04-async-effects.md)** - Parallel with Await/Parallel
- **[Cache System](07-cache-system.md)** - Persistent caching
- **[Patterns](12-patterns.md)** - Advanced patterns and best practices
