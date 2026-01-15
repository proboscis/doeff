# Patterns and Best Practices

Common patterns, best practices, and anti-patterns for building robust applications with doeff.

## Architectural Patterns

### Layered Architecture

Separate concerns into distinct layers:

```python
# Domain layer - pure business logic
@do
def calculate_order_total(items):
    subtotal = sum(item.price * item.quantity for item in items)
    tax = subtotal * 0.1
    return subtotal + tax

# Service layer - orchestration
@do
def order_service(order_id):
    db = yield Ask("database")
    cache = yield Ask("cache")
    
    # Fetch order
    order = yield db.get_order(order_id)
    
    # Calculate total
    total = yield calculate_order_total(order.items)
    
    # Update and cache
    order.total = total
    yield db.save_order(order)
    yield cache.set(f"order_{order_id}", order, ttl=300)
    
    return order

# API layer - external interface
@do
def get_order_handler(order_id):
    yield Log(f"Request: GET /orders/{order_id}")
    
    result = yield Safe(order_service(order_id))
    order = result.value if result.is_ok() else {"error": str(result.error)}
    
    yield Log(f"Response: {order}")
    return order
```

### Repository Pattern

Centralize data access:

```python
@do
def create_repository(table_name):
    db = yield Ask("database")
    
    @do
    def find_by_id(id):
        query = f"SELECT * FROM {table_name} WHERE id = ?"
        return yield db.query_one(query, id)
    
    @do
    def find_all():
        query = f"SELECT * FROM {table_name}"
        return yield db.query_many(query)
    
    @do
    def save(entity):
        if hasattr(entity, "id") and entity.id:
            yield db.update(table_name, entity)
        else:
            yield db.insert(table_name, entity)
        return entity
    
    @do
    def delete(id):
        query = f"DELETE FROM {table_name} WHERE id = ?"
        yield db.execute(query, id)
    
    return {
        "find_by_id": find_by_id,
        "find_all": find_all,
        "save": save,
        "delete": delete
    }

# Usage
@do
def app():
    users = yield create_repository("users")
    user = yield users["find_by_id"](42)
    user.name = "Updated"
    yield users["save"](user)
```

### Service Locator

Centralized dependency access:

```python
@do
def create_services():
    return {
        "database": yield Ask("database"),
        "cache": yield Ask("cache"),
        "logger": yield Ask("logger"),
        "config": yield Ask("config")
    }

@do
def business_logic():
    services = yield create_services()
    
    data = yield services["database"].query()
    yield services["cache"].set("key", data)
    yield services["logger"].info("Processed data")
```

### Unit of Work

Transactional boundary:

```python
@do
def unit_of_work(operations):
    db = yield Ask("database")
    
    # Start transaction
    yield db.begin_transaction()
    
    try:
        # Execute operations
        result = yield operations()
        
        # Commit
        yield db.commit()
        yield Log("Transaction committed")
        return result
    except Exception as e:
        # Rollback on error
        yield db.rollback()
        yield Log(f"Transaction rolled back: {e}")
        raise e

# Usage
@do
def transfer_money(from_account, to_account, amount):
    @do
    def operations():
        yield debit_account(from_account, amount)
        yield credit_account(to_account, amount)
        return {"status": "transferred"}
    
    return yield unit_of_work(operations)
```

## Effect Composition Patterns

### Error Handling Sandwich

Wrap operations with consistent error handling:

```python
@do
def with_error_handling(operation, operation_name):
    yield Log(f"Starting: {operation_name}")
    yield Step(f"start_{operation_name}")
    
    safe_result = yield Safe(operation())
    if safe_result.is_err():
        yield handle_error(operation_name, safe_result.error)
    result = safe_result.value if safe_result.is_ok() else None
    
    yield Step(f"end_{operation_name}")
    yield Log(f"Completed: {operation_name}")
    
    return result

@do
def handle_error(operation_name, error):
    yield Log(f"Error in {operation_name}: {error}")
    yield Annotate({"error": str(error), "operation": operation_name})

# Usage
@do
def app():
    result = yield with_error_handling(
        risky_operation,
        "data_fetch"
    )
```

### Retry with Backoff

Exponential backoff for retries:

```python
@do
def retry_with_exponential_backoff(operation, max_attempts=5):
    for attempt in range(1, max_attempts + 1):
        safe_result = yield Safe(operation())
        
        if safe_result.is_ok():
            return safe_result.value
        
        if attempt < max_attempts:
            delay = 2 ** attempt  # Exponential backoff
            yield Log(f"Retry {attempt} failed, waiting {delay}s")
            yield Await(asyncio.sleep(delay))
        else:
            raise safe_result.error

# Usage
@do
def fetch_with_retry():
    return yield retry_with_exponential_backoff(
        lambda: fetch_from_unreliable_api()
    )
```

### Resource Management

Ensure cleanup with try/finally:

```python
@do
def with_resource(acquire, release, operation):
    resource = yield acquire()
    
    try:
        result = yield operation(resource)
        return result
    finally:
        yield release(resource)
        yield Log("Resource released")

# Usage
@do
def use_database_connection():
    @do
    def acquire():
        db = yield Ask("database")
        conn = yield db.get_connection()
        yield Log("Connection acquired")
        return conn
    
    @do
    def release(conn):
        yield conn.close()
    
    @do
    def work(conn):
        return yield conn.query("SELECT * FROM users")
    
    return yield with_resource(acquire, release, work)
```

### Circuit Breaker

Prevent cascading failures:

```python
@do
def circuit_breaker(operation, threshold=5, timeout=60):
    failures = yield AtomicGet("circuit_failures")
    last_failure_time = yield Get("circuit_last_failure")
    
    # Check if circuit is open
    now = yield IO(lambda: time.time())
    if failures >= threshold:
        if last_failure_time and (now - last_failure_time) < timeout:
            yield Log("Circuit breaker OPEN")
            raise Exception("Circuit breaker is open")
        else:
            # Reset after timeout
            yield AtomicUpdate("circuit_failures", lambda _: 0)
            yield Put("circuit_last_failure", None)
    
    # Try operation
    safe_result = yield Safe(operation())
    
    if safe_result.is_ok():
        # Success - reset counter
        yield AtomicUpdate("circuit_failures", lambda _: 0)
        return safe_result.value
    else:
        # Failure - update circuit breaker state
        yield AtomicUpdate("circuit_failures", lambda x: x + 1)
        yield Put("circuit_last_failure", now)
        raise safe_result.error
```

## State Management Patterns

### State Machine

Explicit state transitions:

```python
@do
def order_state_machine(order_id):
    state = yield Get(f"order_{order_id}_state")
    
    if state == "pending":
        yield process_payment(order_id)
        yield Put(f"order_{order_id}_state", "paid")
        yield Log(f"Order {order_id}: pending -> paid")
    
    elif state == "paid":
        yield ship_order(order_id)
        yield Put(f"order_{order_id}_state", "shipped")
        yield Log(f"Order {order_id}: paid -> shipped")
    
    elif state == "shipped":
        yield complete_order(order_id)
        yield Put(f"order_{order_id}_state", "completed")
        yield Log(f"Order {order_id}: shipped -> completed")
    
    else:
        raise ValueError(f"Invalid state: {state}")
    
    return yield Get(f"order_{order_id}_state")
```

### Immutable State Updates

Functional state transformations:

```python
@do
def update_user(user_id, updates):
    user = yield Get(f"user_{user_id}")
    
    # Create updated copy
    updated_user = {
        **user,
        **updates,
        "updated_at": yield IO(lambda: time.time())
    }
    
    yield Put(f"user_{user_id}", updated_user)
    return updated_user

# Usage
@do
def change_username(user_id, new_username):
    return yield update_user(user_id, {"username": new_username})
```

### Snapshot and Restore

Save and restore state:

```python
@do
def with_state_snapshot(operation):
    # Snapshot state
    state = yield Get("_state")
    snapshot = state.copy() if state else {}
    
    safe_result = yield Safe(operation())
    
    if safe_result.is_err():
        # Restore state and raise
        yield Put("_state", snapshot)
        yield Log("State restored from snapshot")
        raise safe_result.error
    
    return safe_result.value
```

## Performance Patterns

### Parallel Data Fetching

Fetch multiple resources concurrently:

```python
@do
def fetch_user_dashboard(user_id):
    # Fetch all data in parallel using Gather + dict reconstruction
    programs = {
        "user": fetch_user(user_id),
        "posts": fetch_user_posts(user_id),
        "followers": fetch_followers(user_id),
        "notifications": fetch_notifications(user_id)
    }
    keys = list(programs.keys())
    values = yield Gather(*programs.values())
    results = dict(zip(keys, values))
    
    return {
        "user": results["user"],
        "posts": results["posts"],
        "followers": results["followers"],
        "notifications": results["notifications"],
        "dashboard_loaded_at": yield IO(lambda: time.time())
    }
```

### Batching

Group operations for efficiency:

```python
@do
def batch_processor(items, batch_size=100):
    results = []
    
    for i in range(0, len(items), batch_size):
        batch = items[i:i + batch_size]
        yield Log(f"Processing batch {i//batch_size + 1}")
        
        batch_results = yield Parallel(*[
            process_item(item) for item in batch
        ])
        
        results.extend(batch_results)
    
    return results
```

## Testing Patterns

### Dependency Injection for Tests

```python
# Production code
@do
def user_workflow(user_id):
    db = yield Ask("database")
    email = yield Ask("email_service")
    
    user = yield db.get_user(user_id)
    yield email.send(user.email, "Welcome!")
    return user

# Test code
import pytest
from doeff_pinjected import program_to_injected
from pinjected import design, AsyncResolver

@pytest.mark.asyncio
async def test_user_workflow():
    # Mock dependencies
    class MockDB:
        async def get_user(self, id):
            return {"id": id, "email": "test@example.com"}
    
    class MockEmail:
        def __init__(self):
            self.sent = []
        
        async def send(self, to, subject):
            self.sent.append((to, subject))
    
    # Setup test bindings
    mock_email = MockEmail()
    test_bindings = design(
        database=MockDB(),
        email_service=mock_email
    )
    
    # Run test
    resolver = AsyncResolver(test_bindings)
    injected = program_to_injected(user_workflow(123))
    result = await resolver.provide(injected)
    
    # Assertions
    assert result["id"] == 123
    assert len(mock_email.sent) == 1
    assert mock_email.sent[0][0] == "test@example.com"
```

### Test Effects

Verify effects were executed:

```python
@pytest.mark.asyncio
async def test_effects_executed():
    @do
    def program():
        yield Put("key", "value")
        yield Log("Logged message")
        yield Step("step1")
        return "result"
    
    from doeff.runtimes import AsyncioRuntime
    
    # Run with result
    runtime = AsyncioRuntime()
    result = await runtime.run(program())
    
    # Verify result
    assert result.is_ok
    assert result.value == "result"
```

### Property-Based Testing

```python
from hypothesis import given, strategies as st
from doeff.runtimes import AsyncioRuntime

@given(st.integers(min_value=0, max_value=1000))
@pytest.mark.asyncio
async def test_counter_properties(initial_value):
    @do
    def counter_program():
        yield Put("counter", initial_value)
        yield Modify("counter", lambda x: x + 1)
        result = yield Get("counter")
        return result
    
    runtime = AsyncioRuntime()
    result = await runtime.run(counter_program())
    
    # Property: counter should always increment by 1
    assert result.value == initial_value + 1
```

## Anti-Patterns

### ❌ Blocking Operations in Programs

**BAD:**
```python
@do
def bad_program():
    # DON'T: blocking call
    import time
    time.sleep(5)  # Blocks entire runtime
    return "done"
```

**GOOD:**
```python
@do
def good_program():
    # DO: use Await with async
    import asyncio
    yield Await(asyncio.sleep(5))
    return "done"
```

### ❌ Side Effects Without IO

**BAD:**
```python
@do
def bad_side_effects():
    # DON't: side effects without IO
    with open("file.txt", "w") as f:
        f.write("data")  # Untracked side effect
    return "done"
```

**GOOD:**
```python
@do
def good_side_effects():
    # DO: wrap side effects with IO
    yield IO(lambda: open("file.txt", "w").write("data"))
    return "done"
```

### ❌ Overusing State

**BAD:**
```python
@do
def bad_state_usage():
    # Don't: passing everything through state
    yield Put("arg1", arg1)
    yield Put("arg2", arg2)
    yield Put("arg3", arg3)
    return yield compute()  # Reads from state
```

**GOOD:**
```python
@do
def good_parameter_passing():
    # DO: pass arguments directly
    return yield compute(arg1, arg2, arg3)
```

### ❌ Ignoring Errors

**BAD:**
```python
@do
def bad_error_handling():
    result = yield Safe(risky_operation())
    return result.value  # Might be None unexpectedly, error is silently ignored
```

**GOOD:**
```python
@do
def good_error_handling():
    result = yield Safe(risky_operation())
    
    if result.is_err():
        yield Log(f"Error occurred: {result.error}")
        yield Annotate({"error": str(result.error)})
        # Return default value or re-raise
        raise result.error
    
    return result.value
```

### ❌ Deep Nesting

**BAD:**
```python
@do
def deeply_nested():
    if condition1:
        if condition2:
            if condition3:
                yield operation1()
                if condition4:
                    yield operation2()
    return result
```

**GOOD:**
```python
@do
def flat_structure():
    if not condition1:
        return default_value
    
    if not condition2:
        return default_value
    
    if not condition3:
        return default_value
    
    yield operation1()
    
    if condition4:
        yield operation2()
    
    return result
```

## Summary

### Key Patterns

| Pattern | Use Case |
|---------|----------|
| **Layered Architecture** | Separation of concerns |
| **Repository** | Data access abstraction |
| **Unit of Work** | Transactional boundaries |
| **Circuit Breaker** | Prevent cascading failures |
| **Retry with Backoff** | Handle transient failures |
| **Parallel Fetching** | Performance optimization |

### Best Practices

1. **Use IO for side effects**: Never perform untracked side effects
2. **Keep Programs pure**: Inject dependencies, don't create them
3. **Handle errors explicitly**: Don't silently swallow errors
4. **Prefer composition**: Small, focused Programs over large ones
5. **Test with mocks**: Use Ask for testable dependency injection

## Next Steps

- **[Error Handling](05-error-handling.md)** - Robust error handling patterns
- **[Advanced Effects](09-advanced-effects.md)** - Parallel execution and optimization
- **[Pinjected Integration](10-pinjected-integration.md)** - DI patterns