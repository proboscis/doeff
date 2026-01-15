# Pinjected Integration

doeff integrates with [pinjected](https://github.com/proboscis/pinjected), a dependency injection framework for Python, enabling Programs to access dependencies managed by pinjected's resolver.

## Installation

```bash
pip install doeff-pinjected
```

## Why Pinjected Integration?

**Benefits:**
- **Dependency Injection**: Access external dependencies (databases, caches, configs) without manual wiring
- **Testability**: Easily swap dependencies for testing
- **Composition**: Combine doeff Programs with pinjected's DI system
- **Separation of Concerns**: Business logic (Programs) separate from dependency management

## The Dep Effect

The `Dep` effect requests a dependency from pinjected's resolver.

```python
from doeff import do, Dep

@do
def service_program():
    # Request dependencies
    database = yield Dep("database")
    cache = yield Dep("cache")
    
    # Use dependencies
    data = yield fetch_from_db(database)
    yield cache_result(cache, data)
    
    return data
```

### Dep vs Ask

| Effect | Purpose | Resolution |
|--------|---------|------------|
| `Ask(key)` | Environment variable | ExecutionContext.env |
| `Dep(key)` | Dependency injection | pinjected resolver |

**Note**: When using `program_to_injected`, both `Ask` and `Dep` resolve from pinjected, but `Dep` is preferred for clarity.

## Bridge Functions

The `doeff-pinjected` package provides four conversion functions:

### program_to_injected

Converts `Program[T]` to `Injected[T]`, returning only the result value.

```python
from doeff import do, Put, Get, Dep
from doeff_pinjected import program_to_injected
from pinjected import design

@do
def counter_program():
    multiplier = yield Dep("multiplier")
    yield Put("count", 0)
    yield Put("count", 10)
    count = yield Get("count")
    return count * multiplier

# Convert to Injected
injected = program_to_injected(counter_program())

# Create resolver with dependencies
bindings = design(
    multiplier=3
)

# Provide dependencies and run
from pinjected import AsyncResolver
resolver = AsyncResolver(bindings)
result = await resolver.provide(injected)
# result = 30
```

**When to use:**
- You only need the final result value
- You want errors to raise exceptions
- You're composing with other pinjected values

### program_to_injected_result

Converts `Program[T]` to `Injected[RunResult[T]]`, returning the full execution context.

```python
from doeff_pinjected import program_to_injected_result

@do
def stateful_program():
    yield Put("status", "initialized")
    yield Log("Starting computation")
    config = yield Dep("config")
    yield Put("config_loaded", True)
    return config["value"]

# Convert with full result
injected = program_to_injected_result(stateful_program())

# Run and access context
result = await resolver.provide(injected)

print(f"Value: {result.value}")           # Final return value
print(f"State: {result.state}")           # {'status': 'initialized', 'config_loaded': True}
print(f"Logs: {result.log}")              # ['Starting computation']
print(f"Graph: {result.graph.steps}")     # Execution graph
```

**When to use:**
- You need access to state, logs, or graph
- You want to inspect execution context
- You're implementing debugging/monitoring

### program_to_iproxy / program_to_iproxy_result

Convenience functions that return `IProxy[T]` or `IProxy[RunResult[T]]`.

```python
from doeff_pinjected import program_to_iproxy, program_to_iproxy_result

# Returns IProxy[T]
iproxy = program_to_iproxy(my_program())

# Returns IProxy[RunResult[T]]
iproxy_result = program_to_iproxy_result(my_program())

# Use like Injected
result = await resolver.provide(iproxy)
```

## How Dependency Resolution Works

1. **Program Definition**: Programs use `Dep` effects to request dependencies
2. **Conversion**: `program_to_injected` wraps the Program
3. **Interception**: `Dep` effects are intercepted and mapped to `resolver.provide(key)`
4. **Execution**: ProgramInterpreter runs with resolved dependencies
5. **Result**: Returns unwrapped value or full RunResult

```python
# Internal flow
@do
def example():
    db = yield Dep("database")  # 1. Dep effect requested
    return db.query()

injected = program_to_injected(example())  # 2. Wrapped for interception
result = await resolver.provide(injected)  # 3. Dep -> resolver.provide("database")
                                          # 4. Program runs with resolved database
                                          # 5. Returns query result
```

## Patterns

### Service Layer Pattern

```python
from doeff import do, Dep, Log, Safe

@do
def user_service(user_id: int):
    db = yield Dep("database")
    cache = yield Dep("cache")
    logger = yield Dep("logger")
    
    # Try cache first
    cache_result = yield Safe(cache.get(f"user_{user_id}"))
    
    if cache_result.is_ok():
        yield Log(f"Cache hit for user {user_id}")
        return cache_result.value
    
    # Fetch from database
    yield Log(f"Fetching user {user_id} from database")
    user = yield db.query_user(user_id)
    
    # Update cache
    yield cache.set(f"user_{user_id}", user, ttl=300)
    
    return user

# Convert to pinjected
user_service_injected = program_to_injected(user_service(123))
```

### Repository Pattern

```python
@do
def user_repository():
    db = yield Dep("database")
    
    @do
    def get_user(user_id: int):
        yield Log(f"Repository: fetching user {user_id}")
        return yield db.query("SELECT * FROM users WHERE id = ?", user_id)
    
    @do
    def save_user(user):
        yield Log(f"Repository: saving user {user.id}")
        yield db.execute("INSERT OR REPLACE INTO users VALUES (?, ?)", user.id, user.name)
    
    return {"get": get_user, "save": save_user}

# Use in application
@do
def app():
    repo = yield user_repository()
    user = yield repo["get"](42)
    user.name = "Updated"
    yield repo["save"](user)
    return user
```

### Configuration Management

```python
@do
def application():
    config = yield Dep("config")
    
    # Use config throughout
    max_retries = config.get("max_retries", 3)
    timeout = config.get("timeout", 30)
    
    yield Put("retries", max_retries)
    yield Put("timeout", timeout)
    
    result = yield business_logic()
    return result

# Setup bindings
bindings = design(
    config={"max_retries": 5, "timeout": 60, "debug": True}
)
```

### Composed Services

```python
@do
def auth_service():
    db = yield Dep("database")
    
    @do
    def authenticate(username, password):
        user = yield db.find_user(username)
        if user and user.check_password(password):
            return user
        yield Fail(ValueError("Invalid credentials"))
    
    return authenticate

@do
def api_handler(username, password):
    # Compose services
    auth = yield auth_service()
    result = yield Safe(auth(username, password))
    
    if result.is_err():
        return {"error": "Authentication failed"}
    
    user = result.value
    return {"user_id": user.id, "username": user.username}
```

## Testing with Pinjected

### Mock Dependencies

```python
import pytest
from doeff import do, Dep
from doeff_pinjected import program_to_injected
from pinjected import design, AsyncResolver

@do
def data_pipeline():
    db = yield Dep("database")
    api = yield Dep("external_api")
    
    data = yield db.fetch_data()
    processed = yield api.process(data)
    yield db.save_result(processed)
    
    return processed

@pytest.mark.asyncio
async def test_data_pipeline():
    # Mock dependencies
    class MockDB:
        async def fetch_data(self):
            return {"raw": "test_data"}
        
        async def save_result(self, data):
            pass
    
    class MockAPI:
        async def process(self, data):
            return {"processed": data["raw"]}
    
    # Create test bindings
    test_bindings = design(
        database=MockDB(),
        external_api=MockAPI()
    )
    
    # Run with mocks
    resolver = AsyncResolver(test_bindings)
    injected = program_to_injected(data_pipeline())
    result = await resolver.provide(injected)
    
    assert result == {"processed": "test_data"}
```

### Partial Mocking

```python
@pytest.mark.asyncio
async def test_with_partial_mocks():
    # Real cache, mock database
    real_cache = RealCacheImplementation()
    mock_db = MockDatabase()
    
    test_bindings = design(
        cache=real_cache,
        database=mock_db
    )
    
    resolver = AsyncResolver(test_bindings)
    result = await resolver.provide(
        program_to_injected(my_program())
    )
```

### Testing Error Paths

```python
@pytest.mark.asyncio
async def test_error_handling():
    class FailingDB:
        async def query(self):
            raise ConnectionError("Database unavailable")
    
    test_bindings = design(database=FailingDB())
    resolver = AsyncResolver(test_bindings)
    
    @do
    def error_handling_program():
        db = yield Dep("database")
        safe_result = yield Safe(db.query())
        if safe_result.is_ok():
            return safe_result.value
        else:
            return {"error": str(safe_result.error)}
    
    injected = program_to_injected(error_handling_program())
    result = await resolver.provide(injected)
    
    assert "error" in result
    assert "unavailable" in result["error"]
```

## Migration from Ask to Dep

If you have existing Programs using `Ask`, migration is straightforward:

**Before:**
```python
@do
def old_program():
    db = yield Ask("database")
    result = yield db.query()
    return result

# Run with ExecutionContext
context = ExecutionContext(env={"database": my_db})
result = await interpreter.run(old_program(), context)
```

**After:**
```python
@do
def new_program():
    db = yield Dep("database")  # Changed Ask -> Dep
    result = yield db.query()
    return result

# Run with pinjected
bindings = design(database=my_db)
resolver = AsyncResolver(bindings)
injected = program_to_injected(new_program())
result = await resolver.provide(injected)
```

**Gradual Migration:**
```python
# Both Ask and Dep work with program_to_injected
@do
def hybrid_program():
    db = yield Ask("database")      # Still works
    cache = yield Dep("cache")       # Preferred
    return (db, cache)

# Both resolve from pinjected
injected = program_to_injected(hybrid_program())
```

## Best Practices

### Use Dep for External Dependencies

**DO:**
```python
@do
def good_design():
    db = yield Dep("database")        # External dependency
    cache = yield Dep("cache")        # External dependency
    
    value = yield Get("counter")      # Internal state
    yield Log("Processing")           # Internal effect
```

**DON'T:**
```python
@do
def poor_design():
    counter = yield Dep("counter")    # Don't inject state
    log_func = yield Dep("logger")    # Use Log effect instead
```

### Keep Programs Pure

```python
# Good: Program requests dependencies
@do
def pure_program():
    db = yield Dep("database")
    return yield db.query()

# Bad: Program creates dependencies
@do
def impure_program():
    db = Database()  # Don't instantiate directly
    return yield db.query()
```

### Interface Dependencies

```python
from abc import ABC, abstractmethod

class DatabaseInterface(ABC):
    @abstractmethod
    async def query(self, sql: str):
        pass

@do
def interface_based_program():
    db: DatabaseInterface = yield Dep("database")
    # Type hints document expected interface
    return yield db.query("SELECT * FROM users")
```

## Summary

| Function | Returns | Use Case |
|----------|---------|----------|
| `program_to_injected` | `Injected[T]` | Just the result value |
| `program_to_injected_result` | `Injected[RunResult[T]]` | Full execution context |
| `program_to_iproxy` | `IProxy[T]` | Proxy for result value |
| `program_to_iproxy_result` | `IProxy[RunResult[T]]` | Proxy for full context |

**Effect:**
- `Dep(key)` - Request dependency from pinjected resolver

## Next Steps

- **[Patterns](12-patterns.md)** - DI patterns and best practices
- **[Basic Effects](03-basic-effects.md)** - Ask effect for environment variables
- **[Testing Patterns](12-patterns.md#testing)** - Testing strategies