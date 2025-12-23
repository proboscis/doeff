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

### GatherDict - Named Parallel Programs

```python
@do
def parallel_dict():
    results = yield GatherDict({
        "user": fetch_user(123),
        "posts": fetch_posts(123),
        "comments": fetch_comments(123)
    })
    
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

## Memo Effects

In-memory memoization within a single execution.

### MemoGet / MemoPut

```python
@do
def with_memo():
    try:
        # Try to get memoized value
        result = yield MemoGet("expensive_key")
        yield Log("Memo hit!")
    except KeyError:
        # Compute and memoize
        yield Log("Memo miss, computing...")
        result = yield expensive_computation()
        yield MemoPut("expensive_key", result)
    
    return result
```

### Memo vs Cache

| Feature | Memo | Cache |
|---------|------|-------|
| **Lifetime** | Single execution | Across executions |
| **Storage** | Memory only | Memory/Disk/Distributed |
| **Policy** | None | TTL, lifecycle, etc. |
| **Use case** | Within-program dedup | Persistent caching |

### Memo Pattern

```python
@do
def with_internal_memo():
    # First call computes
    result1 = yield memoized_operation("key1")
    
    # Second call with same key uses memo
    result2 = yield memoized_operation("key1")  # Instant
    
    # Different key computes again
    result3 = yield memoized_operation("key2")
    
    return [result1, result2, result3]

@do
def memoized_operation(key):
    result = yield Recover(
        MemoGet(key),
        fallback=compute_and_memo(key)
    )
    return result

@do
def compute_and_memo(key):
    value = yield expensive_work(key)
    yield MemoPut(key, value)
    return value
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

### Gather + Memo

```python
@do
def parallel_with_memo():
    # Fetch multiple users in parallel, memoizing each
    user_ids = [1, 2, 3, 4, 5]
    
    programs = [memoized_fetch_user(uid) for uid in user_ids]
    users = yield Gather(*programs)
    
    # If we fetch same users again, they're memoized
    user_1_again = yield memoized_fetch_user(1)  # From memo
    
    return users

@do
def memoized_fetch_user(user_id):
    user = yield Recover(
        MemoGet(f"user_{user_id}"),
        fallback=fetch_and_memo_user(user_id)
    )
    return user
```

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

### When to Use Memo

**DO:**
- Expensive computations called multiple times in one execution
- Deduplication within a Program
- Temporary caching

**DON'T:**
- Cross-execution caching (use Cache instead)
- Large data (limited by memory)

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
| `GatherDict(dict)` | Named parallel Programs | Structured parallel data |
| `MemoGet(key)` | Get memoized value | Within-execution caching |
| `MemoPut(key, val)` | Store memoized value | Deduplication |
| `AtomicGet(key)` | Thread-safe read | Concurrent reads |
| `AtomicUpdate(key, f)` | Thread-safe update | Concurrent modifications |

## Next Steps

- **[Async Effects](04-async-effects.md)** - Parallel with Await/Parallel
- **[Cache System](07-cache-system.md)** - Persistent caching
- **[Patterns](12-patterns.md)** - Advanced patterns and best practices
