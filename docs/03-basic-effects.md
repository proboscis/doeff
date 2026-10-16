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
from doeff import do, run
from doeff_core_effects import Ask, Tell
from doeff_core_effects.handlers import reader, state, writer
from doeff_core_effects.scheduler import scheduled

@do
def connect_to_database():
    db_url = yield Ask("database_url")
    timeout = yield Ask("timeout")
    yield Tell(f"Connecting to {db_url} with timeout {timeout}")
    return f"Connected to {db_url}"

# Run with environment
def main():
    prog = connect_to_database()
    prog = writer(prog)
    prog = state()(prog)  # writer keeps its log in state, so state() goes outside it
    prog = reader(env={
        "database_url": "postgresql://localhost/mydb",
        "timeout": 30
    })(prog)
    result = run(scheduled(prog))
    print(result)  # Connected to postgresql://localhost/mydb

main()
```

The examples below show the program and one way to run it. Each one is a complete script:
it imports what it uses and installs the handlers its effects need.

**Use cases:**
- Configuration values
- API keys and secrets
- Feature flags
- Application settings

### Ask with Missing Keys

If a key is not in the environment, `Ask` raises `KeyError`:

```python
from doeff import do, run
from doeff_core_effects import Ask
from doeff_core_effects.handlers import reader

@do
def requires_config():
    # Raises KeyError if "timeout" not in env
    timeout = yield Ask("timeout")
    return timeout

# Provide required keys via reader handler
prog = requires_config()
prog = reader(env={"timeout": 30})(prog)
print(run(prog))  # 30

try:
    run(reader(env={})(requires_config()))
except KeyError:
    print("KeyError")  # KeyError
```

### Lazy Program Evaluation in Environment

With the `lazy_ask` handler (instead of `reader`), an environment value that is a `Program` is
evaluated lazily on first access. The result is cached for subsequent `Ask` calls with the same key.
`reader` returns such a value as it is, without running it. `lazy_ask` coordinates concurrent
`Ask` calls with a semaphore, so run it under `scheduled()`:

```python
from doeff import do, run
from doeff_core_effects import Ask, Tell
from doeff_core_effects.handlers import lazy_ask, state, writer, writer_log
from doeff_core_effects.scheduler import scheduled

@do
def expensive_computation():
    yield Tell("Computing config...")
    return {"setting": "computed_value"}

@do
def use_config():
    # env["config"] is a Program: it runs here, on the first Ask
    config = yield Ask("config")
    yield Ask("config")  # cached: the Program does not run again
    log = yield writer_log()
    return config, log

# Pass a Program as the env value - evaluated lazily on first Ask
prog = use_config()
prog = lazy_ask(env={"config": expensive_computation()})(prog)
prog = state()(writer(prog))
result = run(scheduled(prog))
print(result)  # ({'setting': 'computed_value'}, ['Computing config...'])
```

This is useful for:
- Expensive computations that should only run when needed
- Deferred initialization of complex configurations
- Lazy loading of resources

**Concurrency note for lazy `Ask`:**
- Concurrent `Ask(key)` calls for the same lazy key are coordinated with a per-key semaphore.
- At most one task evaluates the lazy program; other tasks wait cooperatively and reuse the cached result.
- Waiting on the same key is not treated as a circular dependency.
- Lazy program results are cached per `run()` invocation.

See [SPEC-EFF-001](../specs/effects/SPEC-EFF-001-reader.md) for details.

### Local - Temporary Environment Override

`Local(env_update, sub_program)` runs a sub-program with modified environment. `Local` is
handled by `local_handler`, installed inside `reader` (`lazy_ask` handles `Local` by itself):

```python
from doeff import do, run
from doeff_core_effects import Ask, Local, Tell
from doeff_core_effects.handlers import local_handler, reader, state, writer

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

prog = local_handler(with_custom_config())
prog = reader(env={"api_url": "https://api.example.com"})(prog)
prog = state()(writer(prog))
print(run(prog))  # data from https://staging.example.com
```

**Use cases:**
- Testing with mock configuration
- Different settings for sub-operations
- Scoped feature flags
- Temporary overrides

#### Local Composition Rules

- `Local + Local`: nested scopes are LIFO. Inner overrides win inside the inner scope, then each scope restores independently.
- `Local + Try`: environment restoration still happens if the inner program raises and the error is caught by `Try`.
- `Local + Gather`: gathered child programs inherit the enclosing Local environment snapshot. A child's own `Local` stays isolated to that child scope.
- `Local + Ask` (lazy `Program` values): overriding a key with a different `Program` object invalidates that key's lazy cache entry.
- `Local + State`: State changes (`Get`/`Put`) persist outside the Local scope. Local only restores the environment, not state. This is intentional.

### Reader Pattern Example

```python
from doeff import do, run
from doeff_core_effects import Ask
from doeff_core_effects.handlers import reader
from doeff_core_effects.scheduler import scheduled

@do
def application():
    # Load config once
    config = yield Ask("config")

    # Pass to all operations
    result1 = yield process_data(config["option1"])
    result2 = yield validate_data(config["option2"])

    return {"result1": result1, "result2": result2}

@do
def process_data(option):
    return f"processed {option}"

@do
def validate_data(option):
    return f"validated {option}"

# Initialize with config
def main():
    prog = application()
    prog = reader(env={"config": {"option1": "value1", "option2": "value2"}})(prog)
    result = run(scheduled(prog))
    print(result)  # {'result1': 'processed value1', 'result2': 'validated value2'}

main()
```

## State Effects

State effects manage mutable state that persists across operations.

### Get - Read State

`Get(key)` retrieves a value from state:

```python
from doeff import do, run
from doeff_core_effects import Get, Tell
from doeff_core_effects.handlers import state, writer

@do
def read_counter():
    count = yield Get("counter")
    yield Tell(f"Current count: {count}")
    return count

print(run(state(initial={"counter": 5})(writer(read_counter()))))  # 5
```

**Returns:** The value if key exists, `None` if it doesn't. Check for `None` to fall back to a
default (initialize a key before reading it when `None` is also a stored value):

```python
from doeff import do, run
from doeff_core_effects import Get
from doeff_core_effects.handlers import state

@do
def safe_read():
    value = yield Get("maybe_missing")
    if value is None:
        return "default"
    return value

print(run(state()(safe_read())))  # default
print(run(state(initial={"maybe_missing": "stored"})(safe_read())))  # stored
```

### Put - Write State

`Put(key, value)` sets a state value:

```python
from doeff import do, run
from doeff_core_effects import Get, Put, Tell
from doeff_core_effects.handlers import state, writer

@do
def initialize_state():
    yield Put("counter", 0)
    yield Put("status", "ready")
    yield Put("items", [])
    yield Tell("State initialized")
    return (yield Get("status"))

print(run(state()(writer(initialize_state()))))  # ready
```

**Behavior:**
- Creates key if it doesn't exist
- Overwrites existing value
- Returns `None`

### Get + Put - Transform State

Since `Modify` has been removed, use `Get` followed by `Put` to transform state:

```python
from doeff import do, run
from doeff_core_effects import Get, Put, Tell
from doeff_core_effects.handlers import state, writer

@do
def increment_counter():
    # Get, transform, and set
    current = yield Get("counter")
    new_value = current + 1
    yield Put("counter", new_value)
    yield Tell(f"Counter now: {new_value}")
    return new_value

print(run(state(initial={"counter": 41})(writer(increment_counter()))))  # 42
```

**Returns:** The new (transformed) value after applying the function.

**Missing-key behavior:** `Get` returns `None` if the key doesn't exist. Start from a default
when it does:

```python
from doeff import do, run
from doeff_core_effects import Get, Put
from doeff_core_effects.handlers import state

@do
def safe_increment():
    current = yield Get("counter")
    if current is None:
        current = 0
    new_value = current + 1
    yield Put("counter", new_value)
    return new_value

print(run(state()(safe_increment())))  # 1
```

### State Composition Rules

- `Put + Get` (immediate visibility): State changes are immediately visible within the same execution context - a `Put` followed by `Get` on the same key always returns the updated value.
- `State + Try`: state changes made before an error persist even when `Try` catches that error. There is no automatic rollback.
- `State + Gather` in `SyncRuntime`: gathered branches run sequentially in input order and share one store.
- `State + Gather` in `AsyncRuntime`: branches share one store but execution can interleave, so read-modify-write patterns can race without explicit coordination.

### State Pattern Examples

**Counter Example:**
```python
from doeff import do, run
from doeff_core_effects import Get, Put
from doeff_core_effects.handlers import state

@do
def counter_operations():
    # Initialize
    yield Put("count", 0)

    # Increment multiple times
    for _ in range(3):
        val = yield Get("count")
        yield Put("count", val + 1)

    # Read final value
    final = yield Get("count")
    return final

print(run(state()(counter_operations())))  # 3
```

**Accumulator Example:**
```python
from doeff import do, run
from doeff_core_effects import Get, Put
from doeff_core_effects.handlers import state

@do
def collect_items():
    # Initialize list
    yield Put("items", [])

    # Add items
    items = yield Get("items")
    yield Put("items", items + [1])
    items = yield Get("items")
    yield Put("items", items + [2, 3])

    # Read all
    items = yield Get("items")
    return items

print(run(state()(collect_items())))  # [1, 2, 3]
```

**State Machine Example:**
```python
from doeff import do, run
from doeff_core_effects import Get, Put, Tell
from doeff_core_effects.handlers import state, writer

@do
def process_work():
    yield Tell("Working")

@do
def state_machine():
    yield Put("state", "idle")

    # Transition: idle -> processing
    current_state = yield Get("state")
    if current_state == "idle":
        yield Put("state", "processing")
        yield Tell("Started processing")

    # Do work
    yield process_work()

    # Transition: processing -> complete
    yield Put("state", "complete")
    yield Tell("Processing complete")
    return (yield Get("state"))

print(run(state()(writer(state_machine()))))  # complete
```

## Writer Effects

Writer effects accumulate output (messages, events, structured entries) throughout execution.

### Tell - Append to Writer Log

`Tell(message)` appends any Python object to the shared writer log:

```python
from doeff import do, run
from doeff_core_effects import Get, Tell
from doeff_core_effects.handlers import state, writer, writer_log
from doeff_core_effects.scheduler import scheduled

@do
def with_logging():
    yield Tell("Starting operation")
    yield Tell("Processing data")

    count = yield Get("count")
    yield Tell(f"Count: {count}")

    yield Tell("Operation complete")
    return "done"

@do
def main_program():
    result = yield with_logging()
    # result is "done"
    # Access the writer log via writer_log()
    log = yield writer_log()
    return result, log

def main():
    prog = main_program()
    prog = writer(prog)
    prog = state(initial={"count": 0})(prog)
    result = run(scheduled(prog))
    print(result)  # ('done', ['Starting operation', 'Processing data', 'Count: 0', 'Operation complete'])

main()
```

`Tell` is not string-only. It accepts and stores any Python object unchanged, including dictionaries, numbers, and custom objects.

### slog - Structured Entries

`slog(msg, **kwargs)` logs a structured entry with a message and keyword arguments.
`SlogEffect` is the underlying effect class. It is a separate type from `Tell`'s
`WriterTellEffect`: `writer` does not collect it, and it needs `slog_handler` (prints each entry
on stderr) or `slog_discard_handler` (silence). See
[slog_handler](13-api-reference.md#slog_handler) for capturing the entries as values.

```python excerpt
def slog(msg, **kwargs) -> SlogEffect:
    """Create a structured log entry with msg and keyword data."""
```

```python
from doeff import do, run
from doeff_core_effects import slog
from doeff_core_effects.handlers import slog_handler

@do
def structured_logging():
    yield slog(
        "User logged in",
        level="info",
        user_id=12345,
        ip="192.168.1.1",
    )
    yield slog(
        "High memory usage",
        level="warn",
        memory_mb=512,
        threshold_mb=400,
    )
    return "logged"

# slog_handler prints each entry on stderr:
#     INFO User logged in user_id=12345 ip=192.168.1.1
#     WARN High memory usage memory_mb=512 threshold_mb=400
print(run(slog_handler(structured_logging())))  # logged
```

### Listen - Capture Sub-Program Logs

`Listen(sub_program)` runs a sub-program and returns `(value, collected)` -- a tuple of the sub-program's return value and the list of collected effects.

Mechanism (SPEC-EFF-003):
- Record the current log start index before running the sub-program.
- Push an internal listen frame using that start index.
- Execute the sub-program.
- Capture entries from that start index onward into the returned collected list.
- Keep those captured entries in the shared log store (they are not removed).

`Listen` is handled by `listen_handler`, installed inside `writer`:

```python
from doeff import do, run
from doeff_core_effects import Listen, Tell
from doeff_core_effects.handlers import listen_handler, state, writer

@do
def inner_operation():
    yield Tell("inner step 1")
    yield Tell("inner step 2")
    return 42

@do
def outer_operation():
    yield Tell("before inner")
    value, collected = yield Listen(inner_operation())
    yield Tell("after inner")
    return value, collected

print(run(state()(writer(listen_handler(outer_operation())))))  # (42, [Tell('inner step 1'), Tell('inner step 2')])
```

The `collected` result is a plain list of captured effects.

### Listen Composition Rules

- `Listen + Tell`: entries told in the Listen scope appear in `collected`.
- `Listen + Local`: entries from inside `Local(...)` are captured normally by Listen.
- `Listen + Try`:
  - `Listen(Try(sub_program))` returns `(result, collected)` where `result` is `Ok(value)` or `Err(error)`; entries before the caught error remain in `collected`.
  - `Try(Listen(sub_program))` returns `Err(...)` if `sub_program` fails before Listen completes; no result tuple is produced on that path.
- `Listen + Gather`: gathered programs share the same log store.
- `Listen + Gather` in `SyncRuntime`: ordering is sequential in program order.
- `Listen + Gather` in `AsyncRuntime`: ordering is non-deterministic and may interleave.
- `Listen + Listen` (nested): the inner Listen captures only its own scope; the outer Listen captures the full outer scope, including entries produced by the inner scope.

### Writer Pattern Examples

**Audit Trail:**
```python
from doeff import do, run
from doeff_core_effects import Get, Put, Tell
from doeff_core_effects.handlers import state, writer

@do
def process_transaction(transaction_id):
    yield Tell(f"[AUDIT] Starting transaction {transaction_id}")

    yield Put("balance", 1000)
    yield Tell(f"[AUDIT] Initial balance: 1000")

    balance = yield Get("balance")
    yield Put("balance", balance - 100)
    new_balance = yield Get("balance")
    yield Tell(f"[AUDIT] Debited 100, new balance: {new_balance}")

    yield Tell(f"[AUDIT] Transaction {transaction_id} complete")
    return new_balance

print(run(state()(writer(process_transaction("tx-1")))))  # 900
```

**Debug Trace:**
```python
from doeff import do, run
from doeff_core_effects import Get, Put, Tell
from doeff_core_effects.handlers import state, writer

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

print(run(state(initial={"x": 21})(writer(debug_computation()))))  # 42
```

## Combining Effects

The real power comes from combining these effects:

### Configuration + State + Logging

```python
from doeff import do, run
from doeff_core_effects import Ask, Get, Put, Tell
from doeff_core_effects.handlers import reader, state, writer

@do
def try_operation():
    attempt = yield Get("attempt")
    return attempt >= 2  # succeeds on the second attempt

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
        yield Put("attempt", attempt + 1)
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

prog = state()(writer(application_workflow()))
prog = reader(env={"max_retries": 3})(prog)
print(run(prog))  # success
```

### Nested Local + State

```python
from doeff import do, run
from doeff_core_effects import Ask, Local, Put, Tell
from doeff_core_effects.handlers import local_handler, reader, state, writer

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

@do
def process_without_feature():
    return "processed without feature"

prog = local_handler(with_feature_flag())
prog = reader(env={"feature_enabled": True})(prog)
prog = state()(writer(prog))
print(run(prog))  # processed with advanced
```

### State Propagation with Listen

```python
from doeff import do, run
from doeff_core_effects import Get, Listen, Put, Tell
from doeff_core_effects.handlers import listen_handler, state, writer

@do
def isolated_operation():
    # Main state
    yield Put("main_counter", 0)

    # Run sub-operation under Listen (captures logs, not state)
    value, collected = yield Listen(isolated_sub_operation())

    # State changes inside Listen persist in the shared store
    main_count = yield Get("main_counter")
    yield Tell(f"Main counter after Listen: {main_count}")  # 10

    return value, main_count

@do
def isolated_sub_operation():
    # This mutates the same state store as the caller
    val = yield Get("main_counter")
    yield Put("main_counter", val + 10)
    yield Tell("Modified counter in sub-operation")
    return "sub-done"

print(run(state()(writer(listen_handler(isolated_operation())))))  # ('sub-done', 10)
```

`Listen` captures writer output (`Tell`) from the sub-program. It does not isolate state:
`Get`/`Put` effects inside `Listen(...)` remain visible after `Listen` completes.

## Best Practices

### Reader Effects

**DO:**
- Use for configuration that doesn't change during execution
- Keep environment keys well-documented
- Use typed access functions

```python
from doeff import do, run
from doeff_core_effects import Ask
from doeff_core_effects.handlers import reader

@do
def get_database_config():
    url = yield Ask("database_url")
    pool_size = yield Ask("pool_size")
    return {"url": url, "pool_size": pool_size}

env = {"database_url": "postgresql://localhost/mydb", "pool_size": 5}
print(run(reader(env=env)(get_database_config())))  # {'url': 'postgresql://localhost/mydb', 'pool_size': 5}
```

**DON'T:**
- Use for values that change frequently (use State instead)
- Store mutable objects that could be modified

### State Effects

**DO:**
- Use descriptive key names
- Initialize state before reading
- Use `Get` + `Put` for atomic updates

```python
from doeff import do, run
from doeff_core_effects import Get, Put
from doeff_core_effects.handlers import state

@do
def safe_increment():
    # Initialize if not exists (Get returns None for a missing key)
    count = yield Get("counter")
    if count is None:
        yield Put("counter", 0)
        count = 0

    yield Put("counter", count + 1)
    return (yield Get("counter"))

print(run(state()(safe_increment())))  # 1
```

**DON'T:**
- Overuse state - prefer passing values when possible
- Use state for configuration (use Reader instead)

### Writer Effects

**DO:**
- Use consistent log formats
- Log important state transitions
- Use `slog` for machine-readable logs

```python excerpt
@do
def well_logged_operation():
    yield slog(
        "operation_start",
        timestamp=...,
        user_id=...
    )
    # ... work ...
    yield slog(
        "operation_complete",
        timestamp=...,
        duration_ms=...
    )
```

**DON'T:**
- Log excessively in tight loops
- Log sensitive information (passwords, tokens)
- Use writer output for control flow

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
| `Tell(msg)` | Append to log | Debugging, audit |
| `slog(msg, **kw)` (`SlogEffect`) | Structured logging | Machine-readable logs |
| `Listen(prog)` | Capture sub-logs | Nested operations |

## Next Steps

- **[Async Effects](04-async-effects.md)** - Gather, Spawn, Time effects for async operations
- **[Error Handling](05-error-handling.md)** - Try effect for robust programs
- **[Patterns](12-patterns.md)** - Common patterns combining multiple effects
