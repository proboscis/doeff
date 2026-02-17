# Basic Effects

This chapter covers the fundamental **batteries-included algebraic effects** that form the building blocks of most programs: Reader, State, and Writer. Each effect type has a corresponding built-in handler that interprets the effect operations.

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
from doeff import do, Ask, Tell, run, default_handlers

@do
def connect_to_database():
    db_url = yield Ask("database_url")
    timeout = yield Ask("timeout")
    yield Tell(f"Connecting to {db_url} with timeout {timeout}")
    return f"Connected to {db_url}"

# Run with environment
def main():
    result = run(
        connect_to_database(),
        default_handlers(),
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

### Ask with Missing Keys

If a key is not in the environment, `Ask` raises `MissingEnvKeyError`:

```python
@do
def requires_config():
    # Raises MissingEnvKeyError if "timeout" not in env
    timeout = yield Ask("timeout")
    return timeout

# Provide required keys via env parameter
result = run(requires_config(), default_handlers(), env={"timeout": 30})
```

### Lazy Program Evaluation in Environment

When an environment value is a `Program`, it is evaluated lazily on first access. The result is cached for subsequent `Ask` calls with the same key:

```python
@do
def expensive_computation():
    yield Tell("Computing config...")
    yield Delay(1.0)  # Simulate expensive work
    return {"setting": "computed_value"}

@do
def use_config():
    # If env["config"] is a Program, it runs lazily here
    config = yield Ask("config")
    return config

# Pass a Program as the env value - evaluated lazily on first Ask
result = run(
    use_config(),
    default_handlers(),
    env={"config": expensive_computation()}  # Program, not value
)
```

This is useful for:
- Expensive computations that should only run when needed
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
    yield Tell(f"Default URL: {url1}")

    # Override for sub-program
    result = yield Local(
        {"api_url": "https://staging.example.com"},
        fetch_data()
    )

    # Back to normal
    url2 = yield Ask("api_url")
    yield Tell(f"Back to: {url2}")
    return result

@do
def fetch_data():
    url = yield Ask("api_url")
    yield Tell(f"Fetching from {url}")
    return f"data from {url}"
```

**Use cases:**
- Testing with mock configuration
- Different settings for sub-operations
- Scoped feature flags
- Temporary overrides

### Reader Pattern Example

```python
from doeff import run, default_handlers

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
    result = run(
        application(),
        default_handlers(),
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
    yield Tell(f"Current count: {count}")
    return count
```

**Returns:** The value if key exists.

**Raises:** `KeyError` if key doesn't exist.

This strict lookup behavior is intentional. `None` is valid state data, so
returning `None` for missing keys would make "missing" and "stored None"
indistinguishable.

Use `Safe` when a key may be missing:

```python
@do
def read_counter_or_default():
    result = yield Safe(Get("counter"))
    if result.is_ok():
        return result.value
    return 0
```

### Put - Write State

`Put(key, value)` sets a state value:

```python
@do
def initialize_state():
    yield Put("counter", 0)
    yield Put("status", "ready")
    yield Put("items", [])
    yield Tell("State initialized")
```

**Behavior:**
- Creates key if it doesn't exist
- Overwrites existing value
- Returns `None`

### Safe + Put - No Automatic Rollback

`Safe` catches exceptions and returns them as `Err`, but it does **not** roll back state writes.

```python
@do
def risky_update():
    yield Put("status", "dirty")
    raise ValueError("boom")

@do
def safe_wrapper():
    yield Put("status", "clean")
    result = yield Safe(risky_update())
    final_status = yield Get("status")
    return (result.is_err(), final_status)  # (True, "dirty")
```

### Modify - Transform State Atomically

`Modify(key, func)` applies a function to current state and returns the new value:

```python
@do
def increment_counter():
    # Get, transform, and set in one operation
    new_value = yield Modify("counter", lambda x: x + 1)
    yield Tell(f"Counter now: {new_value}")
    return new_value
```

**Returns:** The new (transformed) value after applying the function.

If the key doesn't exist, `Modify` passes `None` to `func` (unlike `Get`, which raises `KeyError`):

```python
@do
def increment_with_init():
    return (yield Modify("counter", lambda x: (x or 0) + 1))
```

If `func` raises, the store is unchanged (atomic update behavior):

```python
@do
def atomic_modify_demo():
    yield Put("value", 10)

    def failing_transform(x):
        raise ValueError("transform failed")

    result = yield Safe(Modify("value", failing_transform))
    current = yield Get("value")
    return (result.is_err(), current)  # (True, 10)
```

### Gather + State - Shared Store Semantics

`Gather` branches share the same store. State updates in one branch are visible
to sibling branches and to the parent.

- With `run(...)`, scheduling is deterministic.
- With `async_run(...)`, branch interleaving is concurrent and race conditions are possible.

```python
@do
def increment():
    current = yield Get("counter")
    yield Put("counter", current + 1)
    return current

@do
def gather_with_state():
    yield Put("counter", 0)
    results = yield Gather(increment(), increment(), increment())
    final = yield Get("counter")
    return (results, final)  # sync run: ([0, 1, 2], 3)
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
        yield Tell("Started processing")

    # Do work
    yield process_work()

    # Transition: processing -> complete
    yield Put("state", "complete")
    yield Tell("Processing complete")
```

## Writer Effects

Writer effects accumulate output (logs, messages, events) throughout program execution.

### Tell - Append Writer Output

`Tell(message)` appends a message to the log:

```python
from doeff import run, default_handlers

@do
def with_logging():
    yield Tell("Starting operation")
    yield Tell("Processing data")

    count = yield Get("count")
    yield Tell(f"Count: {count}")

    yield Tell("Operation complete")
    return "done"

def main():
    result = run(with_logging(), default_handlers(), store={"count": 0})
    # Logs are in result.raw_store.get("__log__", [])

main()
```

### StructuredLog - Structured Logging

`StructuredLog(**kwargs)` logs structured data:

```python
from doeff import run, default_handlers

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
    result = run(structured_logging(), default_handlers())
    # Structured logs in result.raw_store.get("__log__", [])

main()
```

### Listen - Capture Sub-Program Logs

`Listen(sub_program)` runs a sub-program and captures its log output. Per [SPEC-EFF-003](../specs/effects/SPEC-EFF-003-writer.md), logs from the inner program are **propagated to the outer scope** in addition to being captured:

```python
from doeff import run, default_handlers

@do
def inner_operation():
    yield Tell("Inner step 1")
    yield Tell("Inner step 2")
    return 42

@do
def outer_operation():
    yield Tell("Before inner")

    # Capture inner logs (they're also propagated to outer)
    listen_result = yield Listen(inner_operation())

    yield Tell("After inner")
    yield Tell(f"Inner returned: {listen_result.value}")
    yield Tell(f"Inner logs: {listen_result.log}")

    return listen_result.value

def main():
    result = run(outer_operation(), default_handlers())
    # All logs (outer AND inner) are in result.raw_store.get("__log__", [])

main()
```

**ListenResult structure:**
```python
@dataclass
class ListenResult(Generic[T]):
    value: T           # Return value of sub-program
    log: list[Any]     # Entries captured from sub-program
```

### Writer Pattern Examples

**Audit Trail:**
```python
@do
def process_transaction(transaction_id):
    yield Tell(f"[AUDIT] Starting transaction {transaction_id}")

    yield Put("balance", 1000)
    yield Tell(f"[AUDIT] Initial balance: 1000")

    yield Modify("balance", lambda x: x - 100)
    new_balance = yield Get("balance")
    yield Tell(f"[AUDIT] Debited 100, new balance: {new_balance}")

    yield Tell(f"[AUDIT] Transaction {transaction_id} complete")
    return new_balance
```

**Debug Trace:**
```python
@do
def debug_computation():
    yield Tell("[DEBUG] Computation start")

    x = yield Get("x")
    yield Tell(f"[DEBUG] x = {x}")

    y = x * 2
    yield Tell(f"[DEBUG] y = x * 2 = {y}")

    yield Put("result", y)
    yield Tell(f"[DEBUG] Stored result = {y}")

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
    yield Tell(f"Config: max_retries = {max_retries}")

    # Initialize state
    yield Put("attempt", 0)
    yield Put("status", "pending")

    # Process with retry logic
    for i in range(max_retries):
        attempt = yield Get("attempt")
        yield Modify("attempt", lambda x: x + 1)
        yield Tell(f"Attempt {attempt + 1}/{max_retries}")

        # Simulate work
        success = yield try_operation()

        if success:
            yield Put("status", "success")
            yield Tell("Operation succeeded")
            return "success"
        else:
            yield Tell(f"Attempt {attempt + 1} failed")

    yield Put("status", "failed")
    yield Tell("All attempts failed")
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
    yield Tell(f"Using feature mode: {mode}")
    return f"processed with {mode}"
```

### Shared State with Listen

```python
@do
def shared_operation():
    # Main state
    yield Put("main_counter", 0)

    # Run sub-operation and capture its writer output
    listen_result = yield Listen(shared_sub_operation())

    # State changes in the sub-program are visible here
    main_count = yield Get("main_counter")
    yield Tell(f"Main counter after sub-operation: {main_count}")

    return listen_result.value

@do
def shared_sub_operation():
    yield Modify("main_counter", lambda x: (x or 0) + 10)
    yield Tell("Modified counter in sub-operation")
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
    return (yield Modify("counter", lambda x: (x or 0) + 1))
```

**DON'T:**
- Overuse state - prefer passing values when possible
- Use state for configuration (use Reader instead)

### Writer Effects

**DO:**
- Use consistent log formats
- Record important state transitions
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
- Emit excessively in tight loops
- Emit sensitive information (passwords, tokens)
- Use `Tell` for control flow

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
| `Ask(key)` | Read environment | Config, settings (Programs in env are lazily evaluated) |
| `Local(env, prog)` | Scoped environment | Testing, overrides |
| `Get(key)` | Read state | Counters, flags |
| `Put(key, val)` | Write state | Initialize, update |
| `Modify(key, f)` | Transform state | Increment, append |
| `Tell(msg)` | Append to log | Debugging, audit |
| `StructuredLog(**kw)` | Structured logging | Machine-readable logs |
| `Listen(prog)` | Capture sub-logs | Nested operations |

## Next Steps

- **[Async Effects](04-async-effects.md)** - Gather, Spawn, Time effects for async operations
- **[Error Handling](05-error-handling.md)** - Safe effect for robust programs
- **[Patterns](12-patterns.md)** - Common patterns combining multiple effects
