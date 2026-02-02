# Basic Effects

This chapter covers the fundamental effects that form the building blocks of most programs: Reader, State, and Writer.

## Table of Contents

- [Reader Effects](#reader-effects)
- [State Effects](#state-effects)
- [Writer Effects](#writer-effects)
- [Combining Effects](#combining-effects)
- [Best Practices](#best-practices)

## Reader Effects

Reader effects provide read-only access to an environment/configuration that flows through your program.

### Ask - Read Environment

`Ask(key)` retrieves a value from the environment:

```python
from doeff import do, Ask, Log, sync_run, sync_handlers_preset

@do
def connect_to_database():
    db_url = yield Ask("database_url")
    timeout = yield Ask("timeout")
    yield Log(f"Connecting to {db_url} with timeout {timeout}")
    return f"Connected to {db_url}"

# Run with environment
def main():
    result = sync_run(
        connect_to_database(),
        sync_handlers_preset,
        env={
            "database_url": "postgresql://localhost/mydb",
            "timeout": 30
        }
    )
    print(result.value)  # "Connected to postgresql://localhost/mydb"

main()
```

**Use cases:**
- Configuration values
- API keys and secrets
- Feature flags
- Application settings

### Ask with Default Values

`Ask(key, default=value)` returns a default if the key is missing:

```python
@do
def with_default():
    # Returns 30 if "timeout" is not in environment
    timeout = yield Ask("timeout", default=30)
    return timeout
```

### Ask with Lazy Program Evaluation

When you pass a `Program` as the default value to `Ask`, it is evaluated lazily **only if the key is missing**. The result is cached for subsequent accesses:

```python
@do
def expensive_computation():
    yield Log("Computing expensive default...")
    yield Delay(1.0)  # Simulate expensive work
    return {"setting": "computed_value"}

@do
def with_lazy_default():
    # expensive_computation() only runs if "config" is missing from env
    # If it runs, the result is cached for any subsequent Ask("config")
    config = yield Ask("config", default=expensive_computation())
    return config

# With env={"config": "provided"} -> expensive_computation never runs
# With env={} -> expensive_computation runs once, result is cached
```

This is useful for:
- Expensive default computations that should only run when needed
- Deferred initialization of complex configurations
- Lazy loading of resources

See [SPEC-EFF-001](../specs/effects/SPEC-EFF-001-reader.md) for details.

### Local - Temporary Environment Override

`Local(env_update, sub_program)` runs a sub-program with modified environment:

```python
@do
def with_custom_config():
    # Normal environment
    url1 = yield Ask("api_url")
    yield Log(f"Default URL: {url1}")

    # Override for sub-program
    result = yield Local(
        {"api_url": "https://staging.example.com"},
        fetch_data()
    )

    # Back to normal
    url2 = yield Ask("api_url")
    yield Log(f"Back to: {url2}")
    return result

@do
def fetch_data():
    url = yield Ask("api_url")
    yield Log(f"Fetching from {url}")
    return f"data from {url}"
```

**Use cases:**
- Testing with mock configuration
- Different settings for sub-operations
- Scoped feature flags
- Temporary overrides

### Reader Pattern Example

```python
from doeff import sync_run, sync_handlers_preset

@do
def application():
    # Load config once
    config = yield Ask("config")

    # Pass to all operations
    result1 = yield process_data(config["option1"])
    result2 = yield validate_data(config["option2"])

    return {"result1": result1, "result2": result2}

# Initialize with config
def main():
    result = sync_run(
        application(),
        sync_handlers_preset,
        env={"config": {"option1": "value1", "option2": "value2"}}
    )
    print(result.value)

main()
```

## State Effects

State effects manage mutable state that persists across operations.

### Get - Read State

`Get(key)` retrieves a value from state:

```python
@do
def read_counter():
    count = yield Get("counter")
    yield Log(f"Current count: {count}")
    return count
```

**Returns:**
- The value if key exists
- Raises `KeyError` if key doesn't exist

### Put - Write State

`Put(key, value)` sets a state value:

```python
@do
def initialize_state():
    yield Put("counter", 0)
    yield Put("status", "ready")
    yield Put("items", [])
    yield Log("State initialized")
```

**Behavior:**
- Creates key if it doesn't exist
- Overwrites existing value
- Returns `None`

### Modify - Transform State

`Modify(key, func)` applies a function to current state:

```python
@do
def increment_counter():
    # Get, transform, and set in one operation
    new_value = yield Modify("counter", lambda x: x + 1)
    yield Log(f"Counter now: {new_value}")
    return new_value
```

**Equivalent to:**
```python
@do
def increment_counter_manual():
    current = yield Get("counter")
    new_value = current + 1
    yield Put("counter", new_value)
    return new_value
```

### State Pattern Examples

**Counter Example:**
```python
@do
def counter_operations():
    # Initialize
    yield Put("count", 0)

    # Increment multiple times
    yield Modify("count", lambda x: x + 1)
    yield Modify("count", lambda x: x + 1)
    yield Modify("count", lambda x: x + 1)

    # Read final value
    final = yield Get("count")
    return final  # 3
```

**Accumulator Example:**
```python
@do
def collect_items():
    # Initialize list
    yield Put("items", [])

    # Add items
    yield Modify("items", lambda xs: xs + [1])
    yield Modify("items", lambda xs: xs + [2, 3])

    # Read all
    items = yield Get("items")
    return items  # [1, 2, 3]
```

**State Machine Example:**
```python
@do
def state_machine():
    yield Put("state", "idle")

    # Transition: idle -> processing
    state = yield Get("state")
    if state == "idle":
        yield Put("state", "processing")
        yield Log("Started processing")

    # Do work
    yield process_work()

    # Transition: processing -> complete
    yield Put("state", "complete")
    yield Log("Processing complete")
```

## Writer Effects

Writer effects accumulate output (logs, messages, events) throughout program execution.

### Log / Tell - Append to Log

`Log(message)` appends a message to the log:

```python
from doeff import sync_run, sync_handlers_preset

@do
def with_logging():
    yield Log("Starting operation")
    yield Log("Processing data")

    count = yield Get("count")
    yield Log(f"Count: {count}")

    yield Log("Operation complete")
    return "done"

def main():
    result = sync_run(with_logging(), sync_handlers_preset, store={"count": 0})
    # Logs are in result.raw_store.get("__log__", [])

main()
```

**Note:** `Log` and `Tell` are aliases for the same effect.

### StructuredLog - Structured Logging

`StructuredLog(**kwargs)` logs structured data:

```python
from doeff import sync_run, sync_handlers_preset

@do
def structured_logging():
    yield StructuredLog(
        level="info",
        message="User logged in",
        user_id=12345,
        ip="192.168.1.1"
    )

    yield StructuredLog(
        level="warn",
        message="High memory usage",
        memory_mb=512,
        threshold_mb=400
    )

    return "logged"

def main():
    result = sync_run(structured_logging(), sync_handlers_preset)
    # Structured logs in result.raw_store.get("__log__", [])

main()
```

### Listen - Capture Sub-Program Log

`Listen(sub_program)` runs a sub-program and captures its log output. Per [SPEC-EFF-003](../specs/effects/SPEC-EFF-003-writer.md), logs from the inner program are **propagated to the outer scope** in addition to being captured:

```python
from doeff import sync_run, sync_handlers_preset

@do
def inner_operation():
    yield Log("Inner step 1")
    yield Log("Inner step 2")
    return 42

@do
def outer_operation():
    yield Log("Before inner")

    # Capture inner logs (they're also propagated to outer)
    listen_result = yield Listen(inner_operation())

    yield Log("After inner")
    yield Log(f"Inner returned: {listen_result.value}")
    yield Log(f"Inner logs: {listen_result.log}")

    return listen_result.value

def main():
    result = sync_run(outer_operation(), sync_handlers_preset)
    # All logs (outer AND inner) are in result.raw_store.get("__log__", [])

main()
```

**ListenResult structure:**
```python
@dataclass
class ListenResult(Generic[T]):
    value: T           # Return value of sub-program
    log: list[Any]     # Log entries from sub-program
```

### Writer Pattern Examples

**Audit Trail:**
```python
@do
def process_transaction(transaction_id):
    yield Log(f"[AUDIT] Starting transaction {transaction_id}")

    yield Put("balance", 1000)
    yield Log(f"[AUDIT] Initial balance: 1000")

    yield Modify("balance", lambda x: x - 100)
    new_balance = yield Get("balance")
    yield Log(f"[AUDIT] Debited 100, new balance: {new_balance}")

    yield Log(f"[AUDIT] Transaction {transaction_id} complete")
    return new_balance
```

**Debug Trace:**
```python
@do
def debug_computation():
    yield Log("[DEBUG] Computation start")

    x = yield Get("x")
    yield Log(f"[DEBUG] x = {x}")

    y = x * 2
    yield Log(f"[DEBUG] y = x * 2 = {y}")

    yield Put("result", y)
    yield Log(f"[DEBUG] Stored result = {y}")

    return y
```

## Combining Effects

The real power comes from combining these effects:

### Configuration + State + Logging

```python
@do
def application_workflow():
    # Read config
    max_retries = yield Ask("max_retries")
    yield Log(f"Config: max_retries = {max_retries}")

    # Initialize state
    yield Put("attempt", 0)
    yield Put("status", "pending")

    # Process with retry logic
    for i in range(max_retries):
        attempt = yield Get("attempt")
        yield Modify("attempt", lambda x: x + 1)
        yield Log(f"Attempt {attempt + 1}/{max_retries}")

        # Simulate work
        success = yield try_operation()

        if success:
            yield Put("status", "success")
            yield Log("Operation succeeded")
            return "success"
        else:
            yield Log(f"Attempt {attempt + 1} failed")

    yield Put("status", "failed")
    yield Log("All attempts failed")
    return "failed"
```

### Nested Local + State

```python
@do
def with_feature_flag():
    # Check global flag
    enabled = yield Ask("feature_enabled")

    if enabled:
        # Run with feature-specific config
        result = yield Local(
            {"feature_mode": "advanced"},
            process_with_feature()
        )
    else:
        result = yield process_without_feature()

    return result

@do
def process_with_feature():
    mode = yield Ask("feature_mode")
    yield Put("mode_used", mode)
    yield Log(f"Using feature mode: {mode}")
    return f"processed with {mode}"
```

### Scoped State with Listen

```python
@do
def isolated_operation():
    # Main state
    yield Put("main_counter", 0)

    # Run isolated sub-operation
    listen_result = yield Listen(isolated_sub_operation())

    # Sub-operation's state changes don't affect main state
    main_count = yield Get("main_counter")
    yield Log(f"Main counter unchanged: {main_count}")

    return listen_result.value

@do
def isolated_sub_operation():
    # This operates on the same state
    yield Modify("main_counter", lambda x: x + 10)
    yield Log("Modified counter in sub-operation")
    return "sub-done"
```

## Best Practices

### Reader Effects

**DO:**
- Use for configuration that doesn't change during execution
- Keep environment keys well-documented
- Use typed access functions

```python
@do
def get_database_config():
    url = yield Ask("database_url")
    pool_size = yield Ask("pool_size")
    return {"url": url, "pool_size": pool_size}
```

**DON'T:**
- Use for values that change frequently (use State instead)
- Store mutable objects that could be modified

### State Effects

**DO:**
- Use descriptive key names
- Initialize state before reading
- Use `Modify` for atomic updates

```python
@do
def safe_increment():
    # Initialize if not exists
    try:
        count = yield Get("counter")
    except KeyError:
        yield Put("counter", 0)
        count = 0

    yield Put("counter", count + 1)
```

**DON'T:**
- Overuse state - prefer passing values when possible
- Use state for configuration (use Reader instead)

### Writer Effects

**DO:**
- Use consistent log formats
- Log important state transitions
- Use `StructuredLog` for machine-readable logs

```python
@do
def well_logged_operation():
    yield StructuredLog(
        event="operation_start",
        timestamp=...,
        user_id=...
    )
    # ... work ...
    yield StructuredLog(
        event="operation_complete",
        timestamp=...,
        duration_ms=...
    )
```

**DON'T:**
- Log excessively in tight loops
- Log sensitive information (passwords, tokens)
- Use Log for control flow

### Combining Effects

**DO:**
- Separate concerns (config via Reader, state via State)
- Use `Local` for scoped configuration
- Document state dependencies

**DON'T:**
- Mix environment and state unnecessarily
- Create deeply nested `Local` scopes

## Summary

| Effect | Purpose | Example |
|--------|---------|---------|
| `Ask(key)` | Read environment | Config, settings |
| `Ask(key, default=prog)` | Read with lazy default | Deferred initialization |
| `Local(env, prog)` | Scoped environment | Testing, overrides |
| `Get(key)` | Read state | Counters, flags |
| `Put(key, val)` | Write state | Initialize, update |
| `Modify(key, f)` | Transform state | Increment, append |
| `Log(msg)` | Append to log | Debugging, audit |
| `StructuredLog(**kw)` | Structured logging | Machine-readable logs |
| `Listen(prog)` | Capture sub-logs | Nested operations |

## Next Steps

- **[Async Effects](04-async-effects.md)** - Gather, Spawn, Time effects for async operations
- **[Error Handling](05-error-handling.md)** - Safe effect for robust programs
- **[Patterns](12-patterns.md)** - Common patterns combining multiple effects
