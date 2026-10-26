# Patterns and Best Practices

Common patterns, best practices, and anti-patterns for building robust applications with doeff.

[![A narrated video, about three minutes: the effects of a layered program travelling out to the handler stack, with Try keeping a failure as a value; retry with backoff under a virtual and a wall-clock time handler; state held by the state handler, with a state machine and the snapshot pattern; tests as the same program under another handler stack; and two anti-patterns, a blocking sleep and a side effect done in place](assets/videos/patterns.jpg)](assets/videos/patterns.mp4)

> Five examples also need `doeff-time` (Retry with Backoff, Circuit Breaker, Immutable State
> Updates, Parallel Data Fetching, Bad: Blocking Operations in Programs). `pip install doeff`
> does not install it; install it from PyPI with `pip install doeff-time`.

## Architectural Patterns

### Layered Architecture

Each layer yields effects for what it needs from the layer below, and the outermost layer, the
infrastructure, is a set of handlers. `order_service` reads and saves an order with `GetOrder` and
`SaveOrder` and caches it with `CachePut`; it holds no database or cache client. `sql_orders`
answers the order effects from SQL tables, `in_memory_orders` from a dict a test can read, and
`in_memory_cache_handler()` answers `CachePut` (it needs `await_handler()` and `scheduled()`
outside it, see [Cache System](07-cache-system.md)):

```python
import sqlite3
from dataclasses import dataclass, replace
from doeff import EffectBase, Resume, Try, do, handler, run, with_handlers
from doeff_core_effects import Tell
from doeff_core_effects.cache_effects import CachePut
from doeff_core_effects.cache_handlers import in_memory_cache_handler
from doeff_core_effects.handlers import await_handler, state, try_handler, writer, writer_log
from doeff_core_effects.scheduler import scheduled

@dataclass(frozen=True)
class Item:
    price: float
    quantity: int

@dataclass(frozen=True)
class Order:
    id: int
    items: tuple[Item, ...]
    total: float = 0.0

# Effects - what the service layer asks the infrastructure for
@dataclass(frozen=True)
class GetOrder(EffectBase):
    order_id: int

@dataclass(frozen=True)
class SaveOrder(EffectBase):
    order: Order

# Domain layer - pure business logic
def calculate_order_total(items):
    subtotal = sum(item.price * item.quantity for item in items)
    return round(subtotal * 1.1, 2)

# Service layer - orchestration, by effects only
@do
def order_service(order_id):
    order = yield GetOrder(order_id)
    order = replace(order, total=calculate_order_total(order.items))
    yield SaveOrder(order)
    yield CachePut(f"order_{order_id}", order, ttl=300)
    return order

# API layer - external interface
@do
def get_order_handler(order_id):
    yield Tell(f"Request: GET /orders/{order_id}")
    result = yield Try(order_service(order_id))
    response = result.value.total if result.is_ok() else {"error": repr(result.error)}
    yield Tell(f"Response: {response}")
    return response

# Infrastructure layer - handlers give the effects their meaning
def sql_orders(conn):
    """Production: orders and their items live in SQL tables."""
    @handler
    def on_order(effect: GetOrder | SaveOrder, k):
        if isinstance(effect, GetOrder):
            rows = conn.execute(
                "SELECT price, quantity FROM items WHERE order_id = ?", (effect.order_id,)
            ).fetchall()
            if not rows:
                raise KeyError(effect.order_id)
            return (yield Resume(k, Order(effect.order_id, tuple(Item(*row) for row in rows))))
        conn.execute(
            "INSERT OR REPLACE INTO orders (id, total) VALUES (?, ?)",
            (effect.order.id, effect.order.total),
        )
        return (yield Resume(k, None))

    return on_order

def in_memory_orders(orders):
    """Tests: orders live in a dict the test can read."""
    @handler
    def on_order(effect: GetOrder | SaveOrder, k):
        if isinstance(effect, GetOrder):
            return (yield Resume(k, orders[effect.order_id]))
        orders[effect.order.id] = effect.order
        return (yield Resume(k, None))

    return on_order

@do
def with_log(program):
    response = yield program
    return response, (yield writer_log())

def serve(orders_handler, order_id):
    stack = [state(), writer, try_handler, await_handler(), in_memory_cache_handler(), orders_handler]
    return run(scheduled(with_handlers(stack, with_log(get_order_handler(order_id)))))

# Production stack: SQLite
conn = sqlite3.connect(":memory:")
conn.executescript("""
    CREATE TABLE items (order_id INTEGER, price REAL, quantity INTEGER);
    CREATE TABLE orders (id INTEGER PRIMARY KEY, total REAL);
    INSERT INTO items VALUES (1, 10.0, 2), (1, 5.0, 1);
""")
print(serve(sql_orders(conn), 1))  # (27.5, ['Request: GET /orders/1', 'Response: 27.5'])
print(conn.execute("SELECT total FROM orders WHERE id = 1").fetchone())  # (27.5,)

# Test stack: the same program over a dict
orders = {7: Order(7, (Item(100.0, 1),))}
print(serve(in_memory_orders(orders), 7)[0])  # 110.0
print(orders[7].total)  # 110.0
print(serve(in_memory_orders(orders), 8)[0])  # {'error': 'KeyError(8)'}
```

`order_service` and `get_order_handler` are the same in both runs; only the handler for the order
effects differs. A failure in the infrastructure, such as the `KeyError` for a missing order, comes
back into the program at the `yield`, where `Try` turns it into a value.

### Repository Pattern

Centralize data access in effects, and keep the connection in one handler. A program names the
table and the entity; `sqlite_repository` holds the connection and turns each effect into SQL:

```python
import sqlite3
from dataclasses import dataclass
from doeff import EffectBase, Resume, do, handler, run

@dataclass(frozen=True)
class FindById(EffectBase):
    table: str
    id: int

@dataclass(frozen=True)
class FindAll(EffectBase):
    table: str

@dataclass(frozen=True)
class Save(EffectBase):
    table: str
    entity: dict

@dataclass(frozen=True)
class Delete(EffectBase):
    table: str
    id: int

RepositoryEffect = FindById | FindAll | Save | Delete

def sqlite_repository(conn, tables):
    """One handler holds the connection; programs only name the table and the entity."""
    conn.row_factory = sqlite3.Row

    @handler
    def on_repository(effect: RepositoryEffect, k):
        if effect.table not in tables:
            raise ValueError(f"unknown table: {effect.table}")
        table = effect.table
        if isinstance(effect, FindById):
            row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (effect.id,)).fetchone()
            return (yield Resume(k, dict(row) if row else None))
        if isinstance(effect, FindAll):
            rows = conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
            return (yield Resume(k, [dict(row) for row in rows]))
        if isinstance(effect, Save):
            columns = ", ".join(effect.entity)
            marks = ", ".join("?" for _ in effect.entity)
            cursor = conn.execute(
                f"INSERT OR REPLACE INTO {table} ({columns}) VALUES ({marks})",
                tuple(effect.entity.values()),
            )
            return (yield Resume(k, {**effect.entity, "id": cursor.lastrowid}))
        conn.execute(f"DELETE FROM {table} WHERE id = ?", (effect.id,))
        return (yield Resume(k, None))

    return on_repository

# Usage
@do
def rename_user(user_id, name):
    user = yield FindById("users", user_id)
    return (yield Save("users", {**user, "name": name}))

@do
def app():
    alice = yield Save("users", {"name": "Alice"})
    yield Save("users", {"name": "Bob"})
    yield rename_user(alice["id"], "Alicia")
    yield Delete("users", 2)
    return (yield FindAll("users"))

conn = sqlite3.connect(":memory:")
conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
print(run(sqlite_repository(conn, {"users"})(app())))  # [{'id': 1, 'name': 'Alicia'}]
```

A test installs a handler that answers the same four effects from a dict, and `app` does not change.

### Service Locator

There is no locator object to build: the handler stack around a program is the locator. A program
yields what it needs (`Ask` for a value, its own effects for data, `Tell` for a log line), and
whichever handlers are installed around it supply them:

```python
from dataclasses import dataclass
from doeff import Ask, EffectBase, Resume, do, handler, run, with_handlers
from doeff_core_effects import Tell
from doeff_core_effects.handlers import reader, state, writer, writer_log

@dataclass(frozen=True)
class LoadSales(EffectBase):
    day: str

@do
def business_logic(day):
    limit = yield Ask("top_n")                # a value from the environment
    sales = yield LoadSales(day)              # data, from whichever handler answers
    yield Tell(f"Loaded {len(sales)} sales")  # a log line, kept by writer
    return sorted(sales, reverse=True)[:limit]

def fixed_sales(sales):
    """Answers LoadSales with fixed rows, in place of the database."""
    @handler
    def on_load(effect: LoadSales, k):
        return (yield Resume(k, sales))

    return on_load

@do
def with_log(program):
    result = yield program
    return result, (yield writer_log())

services = [reader({"top_n": 2}), state(), writer, fixed_sales([3, 1, 4])]
print(run(with_handlers(services, with_log(business_logic("mon")))))  # ([4, 3], ['Loaded 3 sales'])
```

To use the database, replace `fixed_sales` with a handler that queries it; to keep the log
elsewhere, replace `writer`. `business_logic` lists its needs by the effects it yields, and an
effect that no installed handler answers stops the run with `UnhandledEffect`, which names it.

### Unit of Work

Transactional boundary: `Begin`, `Commit` and `Rollback` are effects, and `Try` decides which of
the last two runs. `sqlite_accounts` answers them, and the balance changes, on one connection:

```python
import sqlite3
from dataclasses import dataclass
from doeff import EffectBase, Resume, Try, do, handler, run, with_handlers
from doeff_core_effects import Tell
from doeff_core_effects.handlers import state, try_handler, writer, writer_log

@dataclass(frozen=True)
class Begin(EffectBase):
    pass

@dataclass(frozen=True)
class Commit(EffectBase):
    pass

@dataclass(frozen=True)
class Rollback(EffectBase):
    pass

@dataclass(frozen=True)
class AddToBalance(EffectBase):
    account: str
    amount: int

@do
def unit_of_work(operations):
    yield Begin()
    result = yield Try(operations)
    if result.is_err():
        yield Rollback()
        yield Tell(f"Transaction rolled back: {result.error!r}")
        raise result.error
    yield Commit()
    yield Tell("Transaction committed")
    return result.value

@do
def transfer_money(from_account, to_account, amount):
    @do
    def operations():
        yield AddToBalance(from_account, -amount)
        yield AddToBalance(to_account, amount)
        return "transferred"

    return (yield unit_of_work(operations()))

def sqlite_accounts(conn):
    """Gives the transaction and balance effects their meaning on one SQLite connection."""
    @handler
    def on_account(effect: Begin | Commit | Rollback | AddToBalance, k):
        if isinstance(effect, AddToBalance):
            cursor = conn.execute(
                "UPDATE accounts SET balance = balance + ? WHERE name = ?",
                (effect.amount, effect.account),
            )
            if cursor.rowcount == 0:
                raise KeyError(effect.account)
        else:
            conn.execute(type(effect).__name__.upper())  # BEGIN, COMMIT or ROLLBACK
        return (yield Resume(k, None))

    return on_account

@do
def transfer_and_log(from_account, to_account, amount):
    result = yield Try(transfer_money(from_account, to_account, amount))
    return result.is_ok(), (yield writer_log())

conn = sqlite3.connect(":memory:", isolation_level=None)  # transactions only by the effects
conn.executescript("""
    CREATE TABLE accounts (name TEXT PRIMARY KEY, balance INTEGER);
    INSERT INTO accounts VALUES ('alice', 100), ('bob', 0);
""")

def transfer(from_account, to_account, amount):
    return run(with_handlers(
        [state(), writer, try_handler, sqlite_accounts(conn)],
        transfer_and_log(from_account, to_account, amount),
    ))

def balances():
    return conn.execute("SELECT name, balance FROM accounts ORDER BY name").fetchall()

print(transfer("alice", "bob", 30))  # (True, ['Transaction committed'])
print(balances())  # [('alice', 70), ('bob', 30)]
print(transfer("alice", "carol", 30))  # (False, ["Transaction rolled back: KeyError('carol')"])
print(balances())  # [('alice', 70), ('bob', 30)]
```

The failed transfer debited `alice` before the credit to `carol` failed; `Rollback` undid the debit.

## Effect Composition Patterns

This section summarizes the composition guarantees from
`docs/history/specs/effects/SPEC-EFF-100-combinations.md`.

### Effect Combination Matrix

| Outer / Inner | Ask | Get | Put | Tell | Try | Local | Listen | WithObserve | Gather |
|---------------|-----|-----|-----|------|------|-------|--------|-----------|--------|
| **Local**     | Scoped | Propagates | Propagates | Propagates | Propagates | Scoped | Propagates | Propagates | Propagates |
| **Listen**    | - | - | - | Captured* | - | - | Nested | - | All captured* |
| **Try**      | - | - | Persists | Persists | Wrapped | Restores | - | - | First error |
| **WithObserve** | Observe | Observe | Observe | Observe | Observe | Observe | Observe | Observe | Observe |
| **Gather**    | Inherit | Shared** | Shared** | Merged | Isolated | Inherit | Shared | Propagates | Nested |

Matrix key:
- `Scoped`: Effect operates within a bounded scope and is restored afterward.
- `Propagates`: Effect passes through unchanged.
- `Captured`: Output is captured by the outer effect (success path only).
- `Persists`: Side effects persist even when errors occur.
- `Wrapped`: Result is wrapped in the outer effect's result type.
- `Restores`: Environment context is restored after inner completion.
- `Observe`: Effect is observed by the observer function.
- `Isolated`: Branch-local error handling boundaries apply.
- `Inherit`: Child inherits parent context snapshot at invocation time.
- `Shared`: Branches share the same underlying store/runtime resource.
- `Merged`: Outputs from child branches are combined.
- `Nested`: Inner effect nests inside the outer effect.

\* `Listen` captures `Tell` output only when the listened program succeeds.  
\** `Gather` store sharing is runtime-dependent (see runtime caveats below).

### Composition Laws

The following laws from SPEC-EFF-100 apply to all compositions:

1. **Local Restoration Law**: Environment MUST restore after `Local` scope, on both success and error.
2. **Local Non-State-Scoping Law**: `Local` does NOT scope `Get`/`Put`; state changes propagate.
3. **Listen Capture Law**: `Listen` captures `Tell` output ONLY on success; on error it propagates the error.
4. **Try Non-Rollback Law**: `Try` does NOT roll back state on error.
5. **Try Environment Restoration Law**: `Try` restores environment context, but not state.
6. **WithObserve Observation Law**: `WithObserve` observes effects in nested programs, including `Gather` children.
7. **Gather Environment Inheritance Law**: `Gather` children inherit parent environment snapshot at invocation.
8. **Gather Store Sharing Law**: Store sharing under `Gather` is runtime-dependent.
9. **Gather Error Propagation Law**: First child error propagates; sibling behavior is runtime-dependent.

### Runtime Behavior Caveats

`SyncRuntime` and `AsyncRuntime` intentionally differ in `Gather`-related behavior:

| Concern | SyncRuntime | AsyncRuntime |
|---------|-------------|--------------|
| Gather store sharing | Sequential execution with deterministic state observation | Parallel execution with shared store and race-condition risk |
| Listen + Gather log ordering | Deterministic (sequential execution order) | Non-deterministic (concurrent interleaving) |
| Gather error propagation | Stops at first error; later children do not run | First error propagates to parent; other children may keep running |

### Error Handling Sandwich

Wrap operations with consistent error handling:

```python excerpt
from doeff import Try, do
from doeff_core_effects import Tell

@do
def with_error_handling(operation, operation_name):
    yield Tell(f"Starting: {operation_name}")
    
    safe_result = yield Try(operation())
    if safe_result.is_err():
        yield handle_error(operation_name, safe_result.error)
    result = safe_result.value if safe_result.is_ok() else None
    
    yield Tell(f"Completed: {operation_name}")
    
    return result

@do
def handle_error(operation_name, error):
    yield Tell(f"Error in {operation_name}: {error}")

# Usage
@do
def app():
    result = yield with_error_handling(
        risky_operation,
        "data_fetch"
    )
```

### Retry with Backoff

Exponential backoff for retries. The wait is doeff-time's `Delay` effect, so the caller's
time handler decides what a second is: `async_time_handler()` waits on the wall clock,
`sim_time_handler()` moves a virtual clock and a test of the retry path never sleeps
(see [Advanced Effects](09-advanced-effects.md)):

```python excerpt
from doeff import Try, do
from doeff_core_effects import Tell
from doeff_time import Delay

@do
def retry_with_exponential_backoff(operation, max_attempts=5):
    for attempt in range(1, max_attempts + 1):
        safe_result = yield Try(operation())
        
        if safe_result.is_ok():
            return safe_result.value
        
        if attempt < max_attempts:
            delay = 2 ** attempt  # Exponential backoff
            yield Tell(f"Retry {attempt} failed, waiting {delay}s")
            yield Delay(delay)
        else:
            raise safe_result.error

# Usage
@do
def fetch_with_retry():
    return (yield retry_with_exponential_backoff(
        lambda: fetch_from_unreliable_api()
    ))
```

### Resource Management

Ensure cleanup with try/finally. `Acquire`, `Release` and `Query` are effects; the `pool` handler
holds the open connections and the program only sees the handles it gives out:

```python
import sqlite3
from dataclasses import dataclass
from doeff import EffectBase, Resume, Try, do, handler, run, with_handlers
from doeff_core_effects import Tell
from doeff_core_effects.handlers import state, try_handler, writer, writer_log

@dataclass(frozen=True)
class Acquire(EffectBase):
    pass

@dataclass(frozen=True)
class Release(EffectBase):
    conn: object

@dataclass(frozen=True)
class Query(EffectBase):
    conn: object
    sql: str

@do
def with_connection(work):
    conn = yield Acquire()
    try:
        return (yield work(conn))
    finally:
        yield Release(conn)
        yield Tell("Connection released")

# Usage
@do
def count_users(conn):
    rows = yield Query(conn, "SELECT count(*) FROM users")
    return rows[0][0]

@do
def broken_query(conn):
    return (yield Query(conn, "SELECT * FROM no_such_table"))

def pool(connect, size):
    """Holds the open connections; programs only see the handles it gives out."""
    free = [connect() for _ in range(size)]

    @handler
    def on_pool(effect: Acquire | Release | Query, k):
        if isinstance(effect, Acquire):
            return (yield Resume(k, free.pop()))
        if isinstance(effect, Release):
            free.append(effect.conn)
            return (yield Resume(k, None))
        return (yield Resume(k, effect.conn.execute(effect.sql).fetchall()))

    return on_pool, free

def connect():
    conn = sqlite3.connect(":memory:")
    conn.executescript("CREATE TABLE users (name TEXT); INSERT INTO users VALUES ('alice'), ('bob');")
    return conn

on_pool, free = pool(connect, size=2)

@do
def both():
    users = yield with_connection(count_users)
    failed = yield Try(with_connection(broken_query))
    return users, failed.is_err(), (yield writer_log())

print(run(with_handlers([state(), writer, try_handler, on_pool], both())))  # (2, True, ['Connection released', 'Connection released'])
print(len(free))  # 2
```

The `finally` block runs when the query fails too, so both connections are back in the pool.

### Circuit Breaker

Prevent cascading failures:

```python excerpt
from doeff import Try, do
from doeff_core_effects import Get, Put, Tell
from doeff_time import GetTime

@do
def circuit_breaker(operation, threshold=5, timeout=60):
    failures = yield Get("circuit_failures")
    last_failure_time = yield Get("circuit_last_failure")

    # Check if circuit is open
    now = yield GetTime()
    if failures >= threshold:
        if last_failure_time and (now - last_failure_time).total_seconds() < timeout:
            yield Tell("Circuit breaker OPEN")
            raise Exception("Circuit breaker is open")
        else:
            # Reset after timeout
            yield Put("circuit_failures", 0)
            yield Put("circuit_last_failure", None)
    
    # Try operation
    safe_result = yield Try(operation())
    
    if safe_result.is_ok():
        # Success - reset counter
        yield Put("circuit_failures", 0)
        return safe_result.value
    else:
        # Failure - update circuit breaker state
        current_failures = yield Get("circuit_failures")
        yield Put("circuit_failures", current_failures + 1)
        yield Put("circuit_last_failure", now)
        raise safe_result.error
```

## State Management Patterns

### State Machine

Explicit state transitions:

```python excerpt
from doeff import do
from doeff_core_effects import Get, Put, Tell

@do
def order_state_machine(order_id):
    state = yield Get(f"order_{order_id}_state")
    
    if state == "pending":
        yield process_payment(order_id)
        yield Put(f"order_{order_id}_state", "paid")
        yield Tell(f"Order {order_id}: pending -> paid")
    
    elif state == "paid":
        yield ship_order(order_id)
        yield Put(f"order_{order_id}_state", "shipped")
        yield Tell(f"Order {order_id}: paid -> shipped")
    
    elif state == "shipped":
        yield complete_order(order_id)
        yield Put(f"order_{order_id}_state", "completed")
        yield Tell(f"Order {order_id}: shipped -> completed")
    
    else:
        raise ValueError(f"Invalid state: {state}")
    
    return (yield Get(f"order_{order_id}_state"))
```

### Immutable State Updates

Functional state transformations:

```python excerpt
from doeff import do
from doeff_core_effects import Get, Put
from doeff_time import GetTime

@do
def update_user(user_id, updates):
    user = yield Get(f"user_{user_id}")

    # Create updated copy
    updated_user = {
        **user,
        **updates,
        "updated_at": (yield GetTime())
    }

    yield Put(f"user_{user_id}", updated_user)
    return updated_user

# Usage
@do
def change_username(user_id, new_username):
    return (yield update_user(user_id, {"username": new_username}))
```

### Snapshot and Restore

Save and restore state:

```python excerpt
from doeff import Try, do
from doeff_core_effects import Get, Put, Tell

@do
def with_state_snapshot(operation):
    # Snapshot state
    state = yield Get("_state")
    snapshot = state.copy() if state else {}
    
    safe_result = yield Try(operation())
    
    if safe_result.is_err():
        # Restore state and raise
        yield Put("_state", snapshot)
        yield Tell("State restored from snapshot")
        raise safe_result.error
    
    return safe_result.value
```

## Performance Patterns

### Parallel Data Fetching

Fetch multiple resources concurrently:

```python excerpt
from doeff import Spawn, Gather, do
from doeff_time import GetTime

@do
def fetch_user_dashboard(user_id):
    # Spawn all fetches, then gather results
    t_user = yield Spawn(fetch_user(user_id))
    t_posts = yield Spawn(fetch_user_posts(user_id))
    t_followers = yield Spawn(fetch_followers(user_id))
    t_notifications = yield Spawn(fetch_notifications(user_id))

    results = yield Gather(t_user, t_posts, t_followers, t_notifications)

    return {
        "user": results[0],
        "posts": results[1],
        "followers": results[2],
        "notifications": results[3],
        "dashboard_loaded_at": (yield GetTime())
    }
```

### Batching

Group operations for efficiency:

```python excerpt
from doeff import Spawn, Gather, do
from doeff_core_effects import Tell

@do
def batch_processor(items, batch_size=100):
    results = []

    for i in range(0, len(items), batch_size):
        batch = items[i:i + batch_size]
        yield Tell(f"Processing batch {i//batch_size + 1}")

        # Spawn tasks and gather results
        tasks = []
        for item in batch:
            tasks.append((yield Spawn(process_item(item))))
        batch_results = yield Gather(*tasks)

        results.extend(batch_results)

    return results
```

## Testing Patterns

### Test Effects

Verify effects were executed:

```python
from doeff import do, run
from doeff_core_effects import Put, Tell
from doeff_core_effects.handlers import state, writer

def test_effects_executed():
    @do
    def program():
        yield Put("key", "value")
        yield Tell("Logged message")
        return "result"

    # Run with handlers composed individually
    result = run(state()(writer(program())))

    # run() returns the raw value directly
    assert result == "result"

test_effects_executed()
print("passed")  # passed
```

### Property-Based Testing

```python excerpt
from hypothesis import given, strategies as st
from doeff import do, run
from doeff_core_effects import Get, Put
from doeff_core_effects.handlers import state

@given(st.integers(min_value=0, max_value=1000))
def test_counter_properties(initial_value):
    @do
    def counter_program():
        yield Put("counter", initial_value)
        current = yield Get("counter")
        yield Put("counter", current + 1)
        result = yield Get("counter")
        return result

    result = run(state()(counter_program()))

    # Property: counter should always increment by 1
    assert result == initial_value + 1
```

## Anti-Patterns

### Bad: Blocking Operations in Programs

**BAD:**
```python excerpt
@do
def bad_program():
    # DON'T: blocking call
    import time
    time.sleep(5)  # Blocks entire runtime
    return "done"
```

`yield Await(asyncio.sleep(5))` does not block, but it is still wrong: it waits on the wall
clock behind the time handler's back, so a test cannot run it on a virtual clock.

**GOOD:**
```python excerpt
from doeff import do
from doeff_time import Delay

@do
def good_program():
    # DO: yield the time effect; the installed time handler does the waiting
    yield Delay(5)
    return "done"
```

The `.semgrep.yaml` rule `doeff-no-sleep-in-programs` rejects `time.sleep` and `asyncio.sleep`
inside a `@do` program.

### Bad: Untracked Side Effects

**BAD:**
```python excerpt
@do
def bad_side_effects():
    # Don't: perform side effects directly in program bodies
    with open("file.txt", "w") as f:
        f.write("data")  # Untracked side effect
    return "done"
```

**GOOD:**
```python excerpt
from dataclasses import dataclass
from doeff_vm import EffectBase

@dataclass(frozen=True)
class WriteFile(EffectBase):
    path: str
    data: str

@do
def good_side_effects():
    # Do: model the side effect explicitly
    yield WriteFile(path="file.txt", data="data")
    return "done"
```

### Bad: Overusing State

**BAD:**
```python excerpt
@do
def bad_state_usage():
    # Don't: passing everything through state
    yield Put("arg1", arg1)
    yield Put("arg2", arg2)
    yield Put("arg3", arg3)
    return (yield compute())  # Reads from state
```

**GOOD:**
```python excerpt
@do
def good_parameter_passing():
    # DO: pass arguments directly
    return (yield compute(arg1, arg2, arg3))
```

### Bad: Ignoring Errors

**BAD:**
```python excerpt
@do
def bad_error_handling():
    result = yield Try(risky_operation())
    # On failure `result` is an Err, which has no `.value`: this line raises
    # AttributeError, and the original error from risky_operation() is lost.
    return result.value
```

**GOOD:**
```python excerpt
from doeff import Try, do
from doeff_core_effects import Tell

@do
def good_error_handling():
    result = yield Try(risky_operation())
    
    if result.is_err():
        yield Tell(f"Error occurred: {result.error}")
        # Return default value or re-raise
        raise result.error
    
    return result.value
```

### Bad: Deep Nesting

**BAD:**
```python excerpt
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
```python excerpt
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

1. **Model side effects explicitly**: Never hide file/network/process work inside plain Python calls
2. **Keep Programs pure**: Inject dependencies, don't create them
3. **Handle errors explicitly**: Don't silently swallow errors
4. **Prefer composition**: Small, focused Programs over large ones
5. **Let handlers give the meaning**: Read dependencies with `Ask` and define your own effects; the handler stack around a Program decides what each effect does, so a test, a replay or a dry run is another stack over the same Program

## Next Steps

- **[Error Handling](05-error-handling.md)** - Robust error handling patterns
- **[Advanced Effects](09-advanced-effects.md)** - Parallel execution and optimization
- **[Basic Effects](03-basic-effects.md)** - Reader-based dependency injection patterns
