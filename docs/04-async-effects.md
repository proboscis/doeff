# Async Effects

This chapter covers effects for asynchronous operations: awaiting coroutines, running tasks in parallel, spawning background tasks, and time-based effects.

## Table of Contents

- [Await Effect](#await-effect)
- [Gather Effect](#gather-effect)
- [Spawn and Task Effects](#spawn-and-task-effects)
- [Time Effects](#time-effects)
- [Async Integration Patterns](#async-integration-patterns)
- [Best Practices](#best-practices)

## Await Effect

`Await(awaitable)` integrates async/await with doeff programs.

### Basic Usage

```python
import asyncio
from doeff import do, Await, Log
from doeff.cesk.runtime import AsyncRuntime

async def fetch_data():
    await asyncio.sleep(0.1)
    return {"user_id": 123, "name": "Alice"}

@do
def process_user():
    yield Log("Fetching user data...")
    data = yield Await(fetch_data())
    yield Log(f"Received: {data}")
    return data["name"]

async def main():
    runtime = AsyncRuntime()
    result = await runtime.run(process_user())
    print(result.value)  # "Alice"

asyncio.run(main())
```

### Awaiting Multiple Operations Sequentially

```python
async def fetch_user(user_id):
    await asyncio.sleep(0.1)
    return {"id": user_id, "name": f"User{user_id}"}

async def fetch_posts(user_id):
    await asyncio.sleep(0.1)
    return [{"id": 1, "title": "Post 1"}, {"id": 2, "title": "Post 2"}]

@do
def load_user_profile(user_id):
    # Sequential async operations
    user = yield Await(fetch_user(user_id))
    yield Log(f"Loaded user: {user['name']}")
    
    posts = yield Await(fetch_posts(user_id))
    yield Log(f"Loaded {len(posts)} posts")
    
    return {"user": user, "posts": posts}
```

### With HTTP Requests

```python
import httpx

@do
def fetch_api_data():
    async with httpx.AsyncClient() as client:
        # Await the request
        response = yield Await(
            client.get("https://api.example.com/data")
        )
        
        if response.status_code == 200:
            yield Log("API request successful")
            return response.json()
        else:
            raise Exception(f"HTTP {response.status_code}")
```

## Gather Effect

`Gather(*programs)` runs multiple **Programs** in parallel and collects their results.

**Important:** `Gather` takes `Program` objects, not awaitables. For raw awaitables, use `Await`.

### Basic Parallel Execution

```python
from doeff import do, Gather, Log, Delay

@do
def task1():
    yield Delay(0.1)
    return "result1"

@do
def task2():
    yield Delay(0.1)
    return "result2"

@do
def task3():
    yield Delay(0.1)
    return "result3"

@do
def run_parallel_tasks():
    yield Log("Starting parallel tasks...")
    
    # All tasks run concurrently in AsyncRuntime
    results = yield Gather(task1(), task2(), task3())
    
    yield Log(f"All tasks complete: {results}")
    return results  # ["result1", "result2", "result3"]
```

### Gather vs Parallel

| Feature | `Gather(*programs)` | `Parallel(*awaitables)` |
|---------|---------------------|------------------------|
| Input | `Program` objects | Python awaitables |
| Effects | Full effect support | No effect support |
| Use case | Parallel Programs | Parallel coroutines |

**When to use Gather:**
- Running multiple doeff Programs in parallel
- When inner tasks need state, logging, or other effects
- Primary parallel execution mechanism

**When to use Parallel:**
- Running raw Python async coroutines
- Integration with external async libraries
- When you don't need effects in the parallel tasks

### Parallel API Requests with Gather

```python
@do
def fetch_from_api(endpoint):
    yield Log(f"Fetching {endpoint}...")
    response = yield Await(httpx_client.get(endpoint))
    yield Log(f"Got response from {endpoint}")
    return response.json()

@do
def fetch_multiple_apis():
    # Run multiple Program-based fetchers in parallel
    results = yield Gather(
        fetch_from_api("https://api1.example.com/data"),
        fetch_from_api("https://api2.example.com/data"),
        fetch_from_api("https://api3.example.com/data")
    )
    
    yield Log(f"Fetched {len(results)} responses")
    return results
```

### Fan-Out Pattern

```python
@do
def process_item(item_id):
    yield Log(f"Processing item {item_id}")
    yield Delay(0.05)
    return f"processed-{item_id}"

@do
def process_batch(item_ids):
    yield Log(f"Processing {len(item_ids)} items in parallel")
    
    # Create Program for each item
    tasks = [process_item(item_id) for item_id in item_ids]
    
    # Process all in parallel
    results = yield Gather(*tasks)
    
    yield Log("All items processed")
    return results

# Usage
async def main():
    runtime = AsyncRuntime()
    result = await runtime.run(process_batch([1, 2, 3, 4, 5]))
    print(result.value)  # ["processed-1", ..., "processed-5"]
```

## Spawn and Task Effects

`Spawn` creates background tasks with **snapshot semantics** for fire-and-forget or deferred execution patterns.

See [SPEC-EFF-005](../specs/effects/SPEC-EFF-005-concurrency.md) for the full specification.

### Basic Spawn Usage

```python
from doeff import do, Spawn, Log, Delay

@do
def background_work():
    yield Log("Background work starting...")
    yield Delay(1.0)
    yield Log("Background work complete")
    return "background_result"

@do
def main_program():
    yield Log("Main: Starting")
    
    # Spawn background task
    task = yield Spawn(background_work())
    yield Log("Main: Spawned background task")
    
    # Do other work while background runs
    yield Delay(0.5)
    yield Log("Main: Did some work")
    
    # Wait for background task result
    result = yield task.join()
    yield Log(f"Main: Background returned: {result}")
    
    return result
```

### Snapshot Semantics

Spawned tasks receive a **snapshot** of the environment and store at spawn time. Changes to state in the spawned task do not affect the parent:

```python
@do
def spawned_task():
    # This modifies the spawned task's isolated store
    yield Put("counter", 999)
    return yield Get("counter")  # Returns 999

@do
def parent_task():
    yield Put("counter", 0)
    
    task = yield Spawn(spawned_task())
    
    # Parent's store is unchanged
    parent_counter = yield Get("counter")  # Still 0
    
    # Spawned task sees its own store
    spawned_result = yield task.join()  # Returns 999
    
    # Parent's store STILL unchanged
    final_counter = yield Get("counter")  # Still 0
    
    return {"parent": final_counter, "spawned": spawned_result}
```

### Task Operations

```python
@do
def task_operations_example():
    # Spawn a task
    task = yield Spawn(long_running_work())
    
    # Check if done (non-blocking)
    is_done = yield task.is_done()
    yield Log(f"Is done: {is_done}")  # False initially
    
    # Cancel the task
    cancelled = yield task.cancel()
    yield Log(f"Cancelled: {cancelled}")  # True if successfully cancelled
    
    # Join waits for completion (blocks)
    # After cancel, this raises TaskCancelledError
    try:
        result = yield task.join()
    except TaskCancelledError:
        yield Log("Task was cancelled")
```

### Spawn for Fire-and-Forget

```python
@do
def send_notification():
    yield Log("Sending notification...")
    yield Await(email_service.send("Hello!"))
    yield Log("Notification sent")
    return "sent"

@do
def main_workflow():
    # Fire and forget - we don't wait for the result
    yield Spawn(send_notification())
    
    # Continue immediately
    yield Log("Workflow continues...")
    return "done"
```

### Spawn with Error Handling

```python
@do
def failing_task():
    yield Delay(0.1)
    raise ValueError("Task failed!")

@do
def handle_spawn_error():
    task = yield Spawn(failing_task())
    
    # Wrap join in Safe to handle errors
    result = yield Safe(task.join())
    
    if result.is_ok():
        return result.value
    else:
        yield Log(f"Task failed: {result.error}")
        return "fallback_value"
```

### When to Use Spawn vs Gather

| Use Case | Use Spawn | Use Gather |
|----------|-----------|------------|
| Wait for all results | No | Yes |
| Fire-and-forget | Yes | No |
| Need cancellation | Yes | No |
| Check completion | Yes | No |
| State isolation needed | Yes | No |

## Time Effects

Time effects control timing in your programs.

### Delay - Sleep for Duration

```python
from doeff import do, Delay, Log

@do
def with_delay():
    yield Log("Starting...")
    yield Delay(1.0)  # Sleep for 1 second
    yield Log("After 1 second")
    yield Delay(0.5)
    yield Log("After another 0.5 seconds")
    return "done"
```

**Runtime behavior:**
- `AsyncRuntime`: Uses `asyncio.sleep` (non-blocking)
- `SyncRuntime`: Uses `time.sleep` (blocking)
- `SimulationRuntime`: Advances simulated time instantly

### GetTime - Get Current Time

```python
from doeff import do, GetTime, Delay, Log

@do
def measure_duration():
    start = yield GetTime()
    yield Log(f"Start time: {start}")
    
    yield Delay(1.0)
    
    end = yield GetTime()
    duration = (end - start).total_seconds()
    yield Log(f"Duration: {duration}s")
    
    return duration
```

### WaitUntil - Wait Until Specific Time

```python
from datetime import datetime, timedelta
from doeff import do, WaitUntil, GetTime, Log

@do
def wait_until_example():
    now = yield GetTime()
    target = now + timedelta(seconds=5)
    
    yield Log(f"Current time: {now}")
    yield Log(f"Waiting until: {target}")
    
    yield WaitUntil(target)
    
    yield Log("Target time reached!")
    return "done"
```

### Time Effects with SimulationRuntime

`SimulationRuntime` advances time instantly, making time-based tests fast:

```python
from doeff.cesk.runtime import SimulationRuntime

@do
def slow_in_real_time():
    yield Delay(3600)  # 1 hour delay
    return "done"

# This completes instantly with SimulationRuntime
def test_time_based_program():
    runtime = SimulationRuntime()
    result = runtime.run(slow_in_real_time())
    assert result.is_ok()
    assert result.value == "done"
```

## Async Integration Patterns

### Async Context Managers

```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def database_connection():
    db = await connect_db()
    try:
        yield db
    finally:
        await db.close()

@do
def with_database():
    # Use async context manager
    async with database_connection() as db:
        result = yield Await(db.execute("SELECT * FROM users"))
        yield Log(f"Query returned {len(result)} rows")
        return result
```

### Timeouts

```python
@do
def with_timeout():
    async def slow_operation():
        await asyncio.sleep(10)
        return "done"
    
    try:
        # Set timeout using asyncio
        result = yield Await(
            asyncio.wait_for(slow_operation(), timeout=1.0)
        )
        yield Log(f"Completed: {result}")
        return result
    except asyncio.TimeoutError:
        yield Log("Operation timed out")
        raise Exception("Timeout exceeded")
```

### Rate Limiting

```python
@do
def rate_limited_requests(urls):
    """Process URLs with rate limiting"""
    results = []
    
    for i, url in enumerate(urls):
        if i > 0:
            # Wait between requests
            yield Delay(0.5)
        
        yield Log(f"Fetching {url}...")
        response = yield Await(fetch_url(url))
        results.append(response)
    
    yield Log(f"Completed {len(results)} requests")
    return results
```

### Combining with Other Effects

```python
@do
def async_with_state_and_config():
    # Get config from environment
    api_url = yield Ask("api_url")
    max_concurrent = yield Ask("max_concurrent")
    
    # Track progress in state
    yield Put("completed", 0)
    yield Put("total", 10)
    
    # Run parallel async operations with Gather
    tasks = [fetch_item(api_url, i) for i in range(max_concurrent)]
    results = yield Gather(*tasks)
    
    # Update state
    yield Modify("completed", lambda x: x + len(results))
    
    # Log progress
    completed = yield Get("completed")
    total = yield Get("total")
    yield Log(f"Progress: {completed}/{total}")
    
    return results

@do
def fetch_item(base_url, item_id):
    yield Delay(0.1)
    return f"{base_url}/items/{item_id}"
```

### Error Handling with Async

```python
@do
def safe_async_operation():
    async def risky_async():
        await asyncio.sleep(0.1)
        if random.random() < 0.5:
            raise Exception("Random failure")
        return "success"
    
    # Safe wrapping for async errors
    safe_result = yield Safe(Await(risky_async()))
    
    if safe_result.is_ok():
        result = safe_result.value
    else:
        result = f"Failed: {safe_result.error}"
    
    yield Log(f"Result: {result}")
    return result
```

## Best Practices

### When to Use Await

**DO:**
- Await I/O-bound operations (network, disk, database)
- Integrate with async libraries
- Call async functions from other libraries

```python
@do
def good_await_usage():
    # I/O-bound: good use of async
    data = yield Await(httpx_client.get(url))
    result = yield Await(db.execute(query))
    return result
```

**DON'T:**
- Await CPU-bound operations (use threading/multiprocessing instead)
- Await trivial operations

### When to Use Gather

**DO:**
- Independent operations that all need results
- Multiple Programs with effect support
- Batch processing where all results matter

```python
@do
def good_gather_usage():
    # Independent Programs - perfect for Gather
    results = yield Gather(
        fetch_users(),
        fetch_posts(),
        fetch_comments()
    )
    return {"users": results[0], "posts": results[1], "comments": results[2]}
```

**DON'T:**
- Fire-and-forget patterns (use Spawn)
- Single operations
- Dependent operations that must be sequential

### When to Use Spawn

**DO:**
- Fire-and-forget operations
- When you need cancellation
- When you need state isolation
- Background work with deferred result retrieval

```python
@do
def good_spawn_usage():
    # Fire-and-forget notification
    yield Spawn(send_notification())
    
    # Background work with later join
    task = yield Spawn(expensive_computation())
    yield do_other_work()
    result = yield task.join()
    
    return result
```

**DON'T:**
- When you need all results immediately (use Gather)
- Simple sequential operations

### Performance Considerations

**Sequential (slower):**
```python
@do
def sequential():
    r1 = yield task1()  # 100ms
    r2 = yield task2()  # 100ms
    r3 = yield task3()  # 100ms
    return [r1, r2, r3]
# Total: ~300ms
```

**Parallel with Gather (faster):**
```python
@do
def parallel():
    results = yield Gather(
        task1(),  # 100ms
        task2(),  # 100ms
        task3()   # 100ms
    )
    return results
# Total: ~100ms (all run concurrently)
```

### Testing Async Programs

```python
import asyncio
import pytest
from doeff import do, Await, Log
from doeff.cesk.runtime import AsyncRuntime

@pytest.mark.asyncio
async def test_async_program():
    @do
    def my_program():
        result = yield Await(asyncio.sleep(0, result="test"))
        yield Log(f"Result: {result}")
        return result
    
    runtime = AsyncRuntime()
    result = await runtime.run(my_program())
    
    assert result.is_ok()
    assert result.value == "test"
```

## Summary

| Effect | Purpose | Runtime Support |
|--------|---------|-----------------|
| `Await(coro)` | Wait for async operation | AsyncRuntime only |
| `Gather(*progs)` | Run Programs in parallel | All (parallel in Async, sequential in Sync/Sim) |
| `Spawn(prog)` | Background task with snapshot | AsyncRuntime only |
| `task.join()` | Wait for spawned task | AsyncRuntime only |
| `task.cancel()` | Request cancellation | AsyncRuntime only |
| `task.is_done()` | Check completion | AsyncRuntime only |
| `Delay(seconds)` | Sleep for duration | All |
| `GetTime()` | Get current time | All |
| `WaitUntil(time)` | Wait until specific time | All |

**Key Points:**
- `Await` integrates Python's async/await with doeff
- `Gather` runs Programs in parallel (with full effect support)
- `Spawn` creates isolated background tasks
- Time effects behave differently per runtime
- Use `Safe` for error handling in async operations

## Next Steps

- **[Error Handling](05-error-handling.md)** - Safe effect and RuntimeResult
- **[Effects Matrix](21-effects-matrix.md)** - Complete effect support reference
- **[Patterns](12-patterns.md)** - Common async patterns and best practices
