# Async Effects

This chapter covers effects for asynchronous operations: awaiting coroutines and running tasks in parallel.

## Table of Contents

- [Await Effect](#await-effect)
- [Parallel Effect](#parallel-effect)
- [Async Integration Patterns](#async-integration-patterns)
- [Best Practices](#best-practices)

## Await Effect

`Await(awaitable)` integrates async/await with doeff programs.

### Basic Usage

```python
import asyncio
from doeff import do, Await, Log

async def fetch_data():
    await asyncio.sleep(0.1)
    return {"user_id": 123, "name": "Alice"}

@do
def process_user():
    yield Log("Fetching user data...")
    data = yield Await(fetch_data())
    yield Log(f"Received: {data}")
    return data["name"]
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
            yield Fail(Exception(f"HTTP {response.status_code}"))
```

### With Database Operations

```python
from databases import Database

@do
def query_database():
    database = Database("postgresql://localhost/mydb")
    
    # Connect
    yield Await(database.connect())
    yield Log("Connected to database")
    
    try:
        # Query
        rows = yield Await(
            database.fetch_all("SELECT * FROM users WHERE active = true")
        )
        yield Log(f"Found {len(rows)} active users")
        return [dict(row) for row in rows]
    finally:
        # Cleanup
        yield Await(database.disconnect())
```

## Parallel Effect

`Parallel(*awaitables)` runs multiple async operations concurrently.

### Basic Parallel Execution

```python
async def task1():
    await asyncio.sleep(0.1)
    return "result1"

async def task2():
    await asyncio.sleep(0.1)
    return "result2"

async def task3():
    await asyncio.sleep(0.1)
    return "result3"

@do
def run_parallel_tasks():
    yield Log("Starting parallel tasks...")
    
    # All tasks run concurrently
    results = yield Parallel(
        task1(),
        task2(),
        task3()
    )
    
    yield Log(f"All tasks complete: {results}")
    return results  # ["result1", "result2", "result3"]
```

### Parallel API Requests

```python
import httpx

@do
def fetch_multiple_apis():
    async with httpx.AsyncClient() as client:
        # Fetch from multiple endpoints simultaneously
        responses = yield Parallel(
            client.get("https://api1.example.com/data"),
            client.get("https://api2.example.com/data"),
            client.get("https://api3.example.com/data")
        )
        
        # Process all responses
        data = [resp.json() for resp in responses if resp.status_code == 200]
        yield Log(f"Fetched {len(data)} successful responses")
        return data
```

### Fan-Out Pattern

```python
async def process_item(item_id):
    await asyncio.sleep(0.05)
    return f"processed-{item_id}"

@do
def process_batch(item_ids):
    yield Log(f"Processing {len(item_ids)} items in parallel")
    
    # Create async task for each item
    tasks = [process_item(item_id) for item_id in item_ids]
    
    # Process all in parallel
    results = yield Parallel(*tasks)
    
    yield Log("All items processed")
    return results

# Usage
runtime = create_runtime()
result = await runtime.run(process_batch([1, 2, 3, 4, 5]))
# ["processed-1", "processed-2", "processed-3", "processed-4", "processed-5"]
```

### Mixing Parallel and Sequential

```python
async def fetch_config():
    await asyncio.sleep(0.1)
    return {"timeout": 30, "retries": 3}

async def fetch_user(user_id):
    await asyncio.sleep(0.1)
    return {"id": user_id, "name": f"User{user_id}"}

async def fetch_posts(user_id):
    await asyncio.sleep(0.1)
    return [f"post-{i}" for i in range(3)]

async def fetch_comments(user_id):
    await asyncio.sleep(0.1)
    return [f"comment-{i}" for i in range(5)]

@do
def load_dashboard(user_id):
    # First, fetch config (sequential)
    config = yield Await(fetch_config())
    yield Log(f"Config loaded: {config}")
    
    # Then, fetch user and their data in parallel
    user, posts, comments = yield Parallel(
        fetch_user(user_id),
        fetch_posts(user_id),
        fetch_comments(user_id)
    )
    
    return {
        "config": config,
        "user": user,
        "posts": posts,
        "comments": comments
    }
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

### Async Generators

```python
async def fetch_paginated_data():
    """Async generator that yields pages of data"""
    page = 1
    while page <= 3:
        await asyncio.sleep(0.1)
        yield {"page": page, "data": [f"item-{i}" for i in range(10)]}
        page += 1

@do
def consume_async_generator():
    all_data = []
    
    # Consume async generator
    async for page_data in fetch_paginated_data():
        data = yield Await(asyncio.sleep(0, result=page_data))
        yield Log(f"Received page {data['page']}")
        all_data.extend(data["data"])
    
    yield Log(f"Total items: {len(all_data)}")
    return all_data
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
        yield Fail(Exception("Timeout exceeded"))
```

### Rate Limiting

```python
import asyncio
from datetime import datetime

@do
def rate_limited_requests(urls):
    """Process URLs with rate limiting"""
    results = []
    
    for i, url in enumerate(urls):
        if i > 0:
            # Wait between requests
            yield Await(asyncio.sleep(0.5))
        
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
    
    # Run parallel async operations
    tasks = [fetch_item(api_url, i) for i in range(max_concurrent)]
    results = yield Parallel(*tasks)
    
    # Update state
    yield Modify("completed", lambda x: x + len(results))
    
    # Log progress
    completed = yield Get("completed")
    total = yield Get("total")
    yield Log(f"Progress: {completed}/{total}")
    
    return results

async def fetch_item(base_url, item_id):
    await asyncio.sleep(0.1)
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
    
    # Catch async errors
    result = yield Catch(
        Await(risky_async()),
        lambda e: f"Failed: {e}"
    )
    
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

```python
@do
def bad_await_usage():
    # CPU-bound: wrong tool
    result = yield Await(compute_heavy_calculation())  # Should use threads
    
    # Trivial: unnecessary overhead
    x = yield Await(asyncio.sleep(0, result=42))  # Just use Program.pure(42)
```

### When to Use Parallel

**DO:**
- Independent I/O operations
- Multiple API calls
- Batch processing
- Fan-out patterns

```python
@do
def good_parallel_usage():
    # Independent API calls - perfect for parallel
    users, posts, comments = yield Parallel(
        fetch_users(),
        fetch_posts(),
        fetch_comments()
    )
    return {"users": users, "posts": posts, "comments": comments}
```

**DON'T:**
- Dependent operations (use sequential Await instead)
- Operations that share mutable state
- Too many concurrent operations (consider batching)

```python
@do
def bad_parallel_usage():
    # Dependent operations - must be sequential
    user = yield Await(fetch_user(user_id))  # Must happen first
    posts = yield Await(fetch_user_posts(user["id"]))  # Depends on user
    
    # Wrong: trying to parallel dependent operations
    user, posts = yield Parallel(
        fetch_user(user_id),
        fetch_user_posts(user_id)  # Might need user data first
    )
```

### Performance Considerations

**Sequential (slower):**
```python
@do
def sequential():
    r1 = yield Await(task1())  # 100ms
    r2 = yield Await(task2())  # 100ms
    r3 = yield Await(task3())  # 100ms
    return [r1, r2, r3]
# Total: ~300ms
```

**Parallel (faster):**
```python
@do
def parallel():
    results = yield Parallel(
        task1(),  # 100ms
        task2(),  # 100ms
        task3()   # 100ms
    )
    return results
# Total: ~100ms (all run concurrently)
```

### Error Handling

**Parallel with error recovery:**
```python
@do
def parallel_with_fallback():
    # Some tasks might fail
    tasks = [fetch_item(i) for i in range(10)]
    
    # Wrap each in Safe to get Result types
    safe_tasks = [Safe(Await(task)) for task in tasks]
    
    results = yield Parallel(*safe_tasks)
    
    # Filter successful results
    successes = [r.value for r in results if r.is_ok]
    failures = [r.error for r in results if r.is_err]
    
    yield Log(f"Successes: {len(successes)}, Failures: {len(failures)}")
    return successes
```

### Testing Async Programs

```python
import asyncio
import pytest
from doeff import do, Await, Log, create_runtime

@pytest.mark.asyncio
async def test_async_program():
    @do
    def my_program():
        result = yield Await(asyncio.sleep(0, result="test"))
        yield Log(f"Result: {result}")
        return result
    
    runtime = create_runtime()
    result = await runtime.run(my_program())
    
    assert result.is_ok
    assert result.value == "test"
```

## Summary

| Effect | Purpose | Example |
|--------|---------|---------|
| `Await(coro)` | Wait for async operation | HTTP requests, DB queries |
| `Parallel(*coros)` | Run multiple async ops concurrently | Batch API calls, fan-out |

**Key Points:**
- `Await` integrates Python's async/await with doeff
- `Parallel` runs independent operations concurrently
- Combine with other effects (State, Reader, Writer) freely
- Use `Safe`/`Catch` for async error handling
- Sequential for dependent operations, Parallel for independent ones

## Next Steps

- **[Error Handling](05-error-handling.md)** - Fail, Catch, Retry, Safe for robust programs
- **[Patterns](12-patterns.md)** - Common async patterns and best practices
- **[Advanced Effects](09-advanced-effects.md)** - Gather for parallel Programs