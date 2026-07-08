# API Reference

Complete API reference for doeff's current public surface.

## Core Types

### Program (DoExpr)

The core abstraction for lazy, reusable effectful computations.

`Program` is a virtual type alias for `DoExpr`. It is a union of all program node types
(`Pure`, `Expand`, `Perform`, `WithHandlerType`, `WithObserve`, etc.). There are no
`.map()`, `.flat_map()`, `.pure()`, or `.lift()` methods. Compose programs using the
`@do` decorator and `yield`.

```python
from doeff import Program, DoExpr

isinstance(my_node, Program)  # True for any program node
isinstance(my_node, DoExpr)   # equivalent
```

**See:** [Core Concepts](02-core-concepts.md#program-model)

---

### EffectBase

Effects are user-space operation payloads. They are data, not execution control nodes.
All effects inherit from `EffectBase` (imported from `doeff_vm`).

```python
from doeff_vm import EffectBase

class MyEffect(EffectBase):
    def __init__(self, payload):
        super().__init__()
        self.payload = payload
```

At runtime, effect dispatch happens through `Perform(effect)`.

**See:** [Core Concepts](02-core-concepts.md#control-vs-effect-data)

---

### Result[T]

Success/failure sum type used by `Try(...)`.

```python
Result[T] = Ok[T] | Err
```

**See:** [Error Handling](05-error-handling.md#ok-and-err)

---

### Ok(value)

Success constructor for `Result[T]`.

```python
ok = Ok({"user_id": 42})
```

**Signature:** `Ok(value: T)`

**Fields:**

- **`value`** - Success payload

**Pattern matching:**

```python
match result:
    case Ok(value=v):
        print("success", v)
```

---

### Err(error)

Failure constructor for `Result[T]`.

```python
err = Err(ValueError("invalid input"))
```

**Signature:** `Err(error: Exception)`

**Fields:**

- **`error`** - The captured exception

**Pattern matching:**

```python
match result:
    case Err(error=e):
        print("failed", e)
```

---

### Maybe

Optional value type.

```python
from doeff import Some, Nothing

maybe_val = Some(42)
empty = Nothing

match maybe_val:
    case Some(value=v):
        print("has value", v)
    case Nothing:
        print("empty")
```

`Maybe = Some | Nothing`

---

## Decorators

### @do

Converts a generator function into a callable that produces `Expand` objects (program nodes).

```python
from doeff import do

@do
def my_program(x: int):
    value = yield Get("key")
    return value + x

prog = my_program(42)  # returns Expand (a DoExpr node), not executed yet
result = run(prog)     # execute
```

**Returns:** `Callable[..., Expand]`

Accepts an optional `non_tail` keyword argument to suppress warnings about non-tail
`Resume`/`ResumeThrow` usage in handler functions:

```python
@do(non_tail=True)
def my_handler(effect, k):
    value = yield Resume(k, 42)
    # ... use value (non-tail position) ...
    return value
```

**See:** [Core Concepts](02-core-concepts.md#generator-as-ast)

---

## Reader Effects

### Ask(key)

Request an environment value.

Raises `KeyError` when `key` is not present in the current environment.

If the environment value is a `Program`, `Ask` evaluates it lazily once per `run()` invocation and
caches the computed value per key (when using `lazy_ask`). Concurrent `Ask` calls for the same lazy key are coordinated so
only one task performs evaluation while others wait cooperatively. `Local(...)` overrides can
invalidate that per-run cache for overridden keys.

```python
config = yield Ask("database_url")
```

**Signature:** `Ask(key)`

**See:** [Basic Effects](03-basic-effects.md#reader-effects)

---

### Local(env, program)

Run a sub-program with environment overrides.

```python
result = yield Local({"timeout": 30}, sub_program())
```

**Signature:** `Local(env: Mapping, program: DoExpr)`

**See:** [Basic Effects](03-basic-effects.md#local)

---

## State Effects

### Get(key)

Read state value.

```python
count = yield Get("counter")
```

**Signature:** `Get(key)`

Returns `None` when `key` is missing (with the default `state()` handler).

---

### Put(key, value)

Write state value.

```python
yield Put("counter", 42)
```

**Signature:** `Put(key, value)`

**See:** [Basic Effects](03-basic-effects.md#state-effects)

---

## Writer Effects

### Tell(message)

Append a log entry. `Tell(message)` is a convenience function that creates a
`WriterTellEffect(message)`.

```python
yield Tell("Processing started")
```

**Signature:** `Tell(message) -> WriterTellEffect`

---

### slog(msg, **kwargs)

Append a structured log entry. Creates a `WriterTellEffect` with keyword arguments.

```python
yield slog("Processing", count=42, level="info")
```

**Signature:** `slog(msg, **kwargs) -> WriterTellEffect`

`Slog` is an alias for `WriterTellEffect`.

---

### WriterTellEffect(msg, **kwargs)

The underlying effect type for both `Tell` and `slog`. `Listen` collects these.

```python
yield WriterTellEffect("event", severity="warn")
```

**Signature:** `WriterTellEffect(msg, **kwargs)`

**Fields:**

- **`msg`** - The log message
- **`kwargs`** - Additional structured key-value pairs

---

### Listen(program, types=None)

Run a sub-program and collect all effects of the given types emitted during execution.

```python
value, collected = yield Listen(sub_program())
```

**Signature:** `Listen(program, types=None)`

**Returns:** `(value, list)` tuple -- the program result and a list of collected effects.

When `types` is `None`, defaults to collecting `WriterTellEffect` instances.

**See:** [Basic Effects](03-basic-effects.md#writer-effects)

---

## Error Handling Effects

### Try(program)

Run a sub-program and capture errors as `Result`.

```python
result = yield Try(risky_operation())
match result:
    case Ok(value=v):
        return v
    case Err(error=e):
        return "fallback"
```

**Signature:** `Try(program: DoExpr)`

**Returns:** `Ok(value)` or `Err(error)`

**See:** [Error Handling](05-error-handling.md#try-effect)

---

## Control Effects

### Pure(value)

Wrap a plain value as a DoExpr program node.

```python
value = yield Pure({"status": "ok"})
```

**Signature:** `Pure(value)`

---

### Pass(effect, k)

Forward an unhandled effect to the next outer handler. Used inside handler functions
to indicate "I don't handle this effect".

```python
@do
def my_handler(effect, k):
    if isinstance(effect, MyEffect):
        result = yield Resume(k, effect.payload * 2)
        return result
    yield Pass(effect, k)  # forward everything else
```

**Signature:** `Pass(effect, k)`

---

### WithObserve(observer, body)

Install a cross-cutting observer and run a body program under it. The observer is
called for each effect performed during execution.

```python
def my_observer(effect):
    print(f"observed: {effect}")

result = yield WithObserve(my_observer, sub_program())
```

**Signature:** `WithObserve(observer: callable, body: DoExpr)`

Accepts a plain Python callable as observer -- automatically wraps it with `doeff_vm.Callable`
so the Rust VM can invoke it.

---

### Handler Installer Call

Run a scoped sub-program under a custom handler by calling a `Program -> Program`
handler installer.

```python
from doeff_core_effects.handlers import reader

scoped = reader(env={"name": "override"})(worker())
```

**Shape:** `handler(program: DoExpr) -> DoExpr`

Handlers built with `doeff.program.handler()` have this `Program -> Program` shape.
Compose them by nesting calls:

```python
prog = my_program()
prog = writer()(prog)
prog = state()(prog)
prog = reader(env={"key": "value"})(prog)
result = run(scheduled(prog))
```

---

## Async and Concurrency Effects

### Await(coroutine)

Await a Python coroutine or future. Bridges async into doeff.

```python
value = yield Await(async_call())
```

**Signature:** `Await(coroutine)`

Requires `await_handler()` and `scheduled()` to be installed.

---

### Spawn(program, priority=PRIORITY_NORMAL)

Spawn a program in the background and return a `Task` handle.

```python
task = yield Spawn(worker())
result = yield Wait(task)
```

**Signature:** `Spawn(program, priority=PRIORITY_NORMAL)`

**Returns:** `Task`

**See:** [Advanced Effects](09-advanced-effects.md)

---

### Wait(task)

Wait for a `Task` or `Future` handle to complete.

```python
result = yield Wait(task)
```

**Signature:** `Wait(task, priority=None)`

---

### Gather(*tasks)

Wait for multiple tasks/futures and return results in input order.

```python
task_1 = yield Spawn(fetch_user(1))
task_2 = yield Spawn(fetch_user(2))
task_3 = yield Spawn(fetch_user(3))
results = yield Gather(task_1, task_2, task_3)
```

**Signature:** `Gather(*tasks)`

**See:** [Advanced Effects](09-advanced-effects.md#gather-effects)

---

### Race(*tasks)

Wait for the first task/future to complete.

```python
winner = yield Race(task_a, task_b)
```

**Signature:** `Race(*tasks)`

**See:** [Async Effects](04-async-effects.md#race-semantics),
[Advanced Effects](09-advanced-effects.md#race-effect)

---

### Cancel(task)

Request cooperative cancellation for a target `Task`.

```python
task = yield Spawn(worker())
yield Cancel(task)
```

**Signature:** `Cancel(task)`

`Cancel` is an effect that must be yielded. There is no `task.cancel()` method.

**See:** [Async Effects](04-async-effects.md#cancel-and-taskcancellederror)

---

### TaskCancelledError

Raised by `Wait`, `Gather`, or `Race` when a waited task was cancelled.

```python
joined = yield Try(Wait(task))
match joined:
    case Err(error=e) if isinstance(e, TaskCancelledError):
        ...  # handle cancellation
```

**Import:** `from doeff import TaskCancelledError`

---

## Promise Effects

### CreatePromise()

Create a scheduler-internal promise and return a `Promise` handle.

```python
promise = yield CreatePromise()
future = promise.future  # read-side Future handle
```

**Signature:** `CreatePromise()`

**Returns:** `Promise` (with `.future` property for `Wait`)

---

### CompletePromise(promise, value)

Complete a promise with a success value.

```python
yield CompletePromise(promise, result_value)
```

**Signature:** `CompletePromise(promise, value)`

---

### FailPromise(promise, error)

Complete a promise with an error.

```python
yield FailPromise(promise, ValueError("something went wrong"))
```

**Signature:** `FailPromise(promise, error)`

---

### CreateExternalPromise()

Create a thread-safe external promise. The returned `ExternalPromise` has `.complete(value)`
and `.fail(error)` methods that can be called from any thread.

```python
ep = yield CreateExternalPromise()
# From another thread:
ep.complete(result)
# or ep.fail(error)
value = yield Wait(ep.future)
```

**Signature:** `CreateExternalPromise()`

**Returns:** `ExternalPromise` (with `.future` property and thread-safe `.complete()`/`.fail()`)

---

## Semaphore Effects

### CreateSemaphore(permits)

Create an opaque semaphore handle with `permits` initial permits.

```python
sem = yield CreateSemaphore(3)
```

**Signature:** `CreateSemaphore(permits=1)`

**Returns:** `Semaphore`

---

### AcquireSemaphore(semaphore)

Acquire one permit from a semaphore; blocks cooperatively when no permits are available.

```python
yield AcquireSemaphore(sem)
try:
    ...
finally:
    yield ReleaseSemaphore(sem)
```

**Signature:** `AcquireSemaphore(semaphore)`

Blocked acquirers are resumed in FIFO order.

**See:** [Semaphore Effects](21-semaphore-effects.md#acquiresemaphoresem)

---

### ReleaseSemaphore(semaphore)

Release one permit back to a semaphore.

```python
yield ReleaseSemaphore(sem)
```

**Signature:** `ReleaseSemaphore(semaphore)`

When waiters exist, release uses direct handoff to the oldest waiter (FIFO).

Always guard critical sections with `try/finally` so `ReleaseSemaphore` still runs
if the task fails or is cancelled.

**See:** [Semaphore Effects](21-semaphore-effects.md#releasesemaphoresem)

---

## Cache Effects

Located in `doeff_core_effects.cache_effects`.

### CacheGet(key)

Retrieve a cached value.

```python
from doeff_core_effects.cache_effects import CacheGet

value = yield CacheGet("expensive_key")
```

**Signature:** `CacheGet(key) -> CacheGetEffect`

---

### CachePut(key, value, ttl=None, *, lifecycle=None, storage=None, metadata=None, policy=None)

Store a value in cache with optional policy.

```python
from doeff_core_effects.cache_effects import CachePut
from doeff_core_effects.cache_policy import CacheLifecycle

yield CachePut(
    "key",
    value,
    ttl=300,
    lifecycle=CacheLifecycle.PERSISTENT,
)
```

**Signature:** `CachePut(key, value, ttl=None, *, lifecycle=None, storage=None, metadata=None, policy=None) -> CachePutEffect`

---

### CachePolicy

```python
from doeff_core_effects.cache_policy import CachePolicy, CacheLifecycle, CacheStorage
```

**CacheLifecycle** enum: `TRANSIENT`, `SESSION`, `PERSISTENT`

**CacheStorage** enum: `MEMORY`, `DISK`

---

## Memo Effects

Located in `doeff_core_effects.memo_effects`. Memo effects are the cost-aware memoization
system that replaced the old `CacheGet` name in the `doeff` top-level namespace.

### MemoGet(key)

Retrieve a memoized value.

```python
from doeff_core_effects.memo_effects import MemoGet

value = yield MemoGet("computation_result")
```

**Signature:** `MemoGet(key, *, recompute_cost=RecomputeCost.CHEAP) -> MemoGetEffect`

---

### MemoPut(key, value, ...)

Store a memoized value.

```python
from doeff_core_effects.memo_effects import MemoPut

yield MemoPut("key", value, recompute_cost="expensive")
```

**Signature:** `MemoPut(key, value, ttl=None, *, recompute_cost=None, lifecycle=None, metadata=None, policy=None, source_effect=None) -> MemoPutEffect`

---

## Execution

### run(doexpr)

Execute a DoExpr program to completion and return the raw result value.

```python
from doeff import do, run
from doeff_core_effects.handlers import reader, state, writer
from doeff_core_effects.scheduler import scheduled

@do
def my_program():
    name = yield Ask("name")
    yield Put("greeted", True)
    yield Tell(f"Hello, {name}")
    return f"Done greeting {name}"

prog = my_program()
prog = writer()(prog)
prog = state()(prog)
prog = reader(env={"name": "Alice"})(prog)
result = run(scheduled(prog))
# result is "Done greeting Alice" (raw value, not a wrapper)
```

**Signature:**

```python
def run(doexpr) -> T
```

`run()` takes a single argument -- the program to execute. There are no `handlers`, `env`,
or `store` keyword arguments. Compose handlers by wrapping the program before passing it
to `run()`. Use `scheduled()` as the outermost wrapper when using scheduler effects
(`Spawn`, `Wait`, `Gather`, `Race`, semaphores, promises).

On error, exceptions propagate with enriched doeff traceback information printed to stderr.

---

### scheduled(program)

Wrap a program with the cooperative scheduler. Returns a new DoExpr that, when passed to
`run()`, enables all scheduler effects (Spawn, Wait, Gather, Race, Cancel, promises,
semaphores).

```python
from doeff_core_effects.scheduler import scheduled

result = run(scheduled(prog))
```

**Signature:** `scheduled(program) -> DoExpr`

This is the replacement for the deleted `async_run()`. All async/concurrent effects
require `scheduled()` to be installed.

---

## Handlers

Handlers are `Program -> Program` functions. Compose them by calling them in sequence:

```python
from doeff import do, run
from doeff_core_effects.handlers import reader, state, writer, try_handler, slog_handler
from doeff_core_effects.scheduler import scheduled

prog = my_program()
prog = slog_handler()(prog)      # innermost
prog = writer()(prog)
prog = state(initial={"k": 0})(prog)
prog = reader(env={"key": "val"})(prog)  # outermost user handler
result = run(scheduled(prog))    # scheduler wraps everything
```

### reader(env=None)

Handles `Ask(key)` by looking up `key` in `env`. Raises `KeyError` on miss.

```python
from doeff_core_effects.handlers import reader

prog = reader(env={"db_url": "postgres://..."})(my_program())
```

---

### lazy_ask(env=None, *, strict=False)

Handles `Ask` and `Local` with lazy evaluation and caching. If an env value is a
`Program`, evaluates it lazily once per key with concurrent coordination via semaphores.

```python
from doeff_core_effects.handlers import lazy_ask

prog = lazy_ask(env={"config": load_config_program()})(my_program())
```

- `strict=False` (default): unresolved keys are forwarded to outer handlers via `Pass`.
- `strict=True`: unresolved keys raise `KeyError`.

Requires `scheduled()` to be installed (for semaphore support).

---

### state(initial=None)

Handles `Get(key)` and `Put(key, value)` with a mutable dict.

```python
from doeff_core_effects.handlers import state

prog = state(initial={"counter": 0})(my_program())
```

---

### writer()

Handles `Tell(message)` / `WriterTellEffect`. Collects messages into a list.

The returned handler has a `.log` attribute for inspecting collected entries after execution.

```python
from doeff_core_effects.handlers import writer

w = writer()
prog = w(my_program())
run(scheduled(prog))
print(w.log)  # ["message1", "message2", ...]
```

---

### slog_handler()

Handles `Slog` / `WriterTellEffect` structured log entries. Collects entries as dicts
with `msg` and all kwargs.

The returned handler has a `.log` attribute.

```python
from doeff_core_effects.handlers import slog_handler

sh = slog_handler()
prog = sh(my_program())
run(scheduled(prog))
print(sh.log)  # [{"msg": "event", "count": 42}, ...]
```

---

### try_handler

Handles `Try(program)` -- wraps inner program execution and catches errors as
`Ok(value)` or `Err(error)`. Pre-installed (not a factory -- use directly).

```python
from doeff_core_effects.handlers import try_handler

prog = try_handler(my_program())
```

---

### local_handler

Handles `Local(env, program)` -- scoped environment overrides. Pre-installed
(not a factory -- use directly).

```python
from doeff_core_effects.handlers import local_handler

prog = local_handler(my_program())
```

---

### listen_handler

Handles `Listen(program, types=...)` -- collects effects during sub-program execution.
Pre-installed (not a factory -- use directly).

```python
from doeff_core_effects.handlers import listen_handler

prog = listen_handler(my_program())
```

---

### await_handler()

Handles `Await(coroutine)` by bridging async into the scheduler via `ExternalPromise`.
Requires `scheduled()` to be installed.

```python
from doeff_core_effects.handlers import await_handler

prog = await_handler()(my_program())
```

---

### env_var_ask(*, prefix="DOEFF_")

Handles `Ask(key)` by looking up `os.environ[prefix + key]`. Supports `{module.path}`
syntax for importing symbols. Unresolved keys are forwarded to outer handlers.

```python
from doeff_core_effects.handlers import env_var_ask

prog = env_var_ask()(my_program())
```

---

## Quick Reference

### Effect Categories

| Category | Effects |
|----------|---------|
| **Reader** | `Ask`, `Local` |
| **State** | `Get`, `Put` |
| **Writer** | `Tell`, `slog`, `WriterTellEffect`, `Listen` |
| **Async/Concurrency** | `Await`, `Spawn`, `Wait`, `Gather`, `Race`, `Cancel` |
| **Semaphore** | `CreateSemaphore`, `AcquireSemaphore`, `ReleaseSemaphore` |
| **Promise** | `CreatePromise`, `CompletePromise`, `FailPromise`, `CreateExternalPromise` |
| **Control** | `Pure`, `Pass`, `WithObserve`, handler installer calls |
| **Error** | `Try`, `Ok`, `Err` |
| **Cache** | `CacheGet`, `CachePut` (in `doeff_core_effects.cache_effects`) |
| **Memo** | `MemoGet`, `MemoPut` (in `doeff_core_effects.memo_effects`) |

### Common Imports

```python
# Core
from doeff import Program, DoExpr, do

# Execution
from doeff import run
from doeff_core_effects.scheduler import scheduled

# Reader / State / Writer
from doeff import Ask, Local, Get, Put, Tell, Listen, slog, WriterTellEffect

# Async / Concurrency
from doeff import Await, Spawn, Wait, Gather, Race, Cancel, Task
from doeff import TaskCancelledError

# Semaphore
from doeff import AcquireSemaphore, CreateSemaphore, ReleaseSemaphore, Semaphore

# Promise
from doeff import CreatePromise, CompletePromise, FailPromise, CreateExternalPromise

# Control
from doeff import Pure, Pass, WithObserve

# Error handling
from doeff import Try, Ok, Err

# Maybe
from doeff import Some, Nothing

# Handlers
from doeff_core_effects.handlers import (
    reader, lazy_ask, state, writer, try_handler,
    slog_handler, local_handler, listen_handler, await_handler,
)

# Cache effects (separate package)
from doeff_core_effects.cache_effects import CacheGet, CachePut
from doeff_core_effects.cache_policy import CacheLifecycle, CachePolicy, CacheStorage

# Memo effects (separate package)
from doeff_core_effects.memo_effects import MemoGet, MemoPut
```

### Handler Composition Pattern

```python
from doeff import do, run
from doeff_core_effects.handlers import reader, state, writer
from doeff_core_effects.scheduler import scheduled

@do
def my_program():
    name = yield Ask("name")
    yield Put("count", 1)
    yield Tell(f"greeted {name}")
    return name

prog = my_program()
prog = writer()(prog)
prog = state()(prog)
prog = reader(env={"name": "Alice"})(prog)
result = run(scheduled(prog))
```

---

## Removed APIs

The following APIs have been removed. Using them raises `RuntimeError` with a migration hint.

| Removed | Replacement |
|---------|-------------|
| `default_handlers` | Compose handlers by calling `handler(program)` |
| `async_run` | Use `run()` with `scheduled()` |
| `default_async_handlers` | Compose handlers by calling `handler(program)` |
| `RunResult` | `run()` returns raw values directly |
| `Modify` | Use `Get` + `Put` instead |
| `WithIntercept` | Use `WithObserve` instead |
| `KleisliProgram` | Use `@do` instead |
| `Delegate` | Use `yield effect` to re-perform in handler body |
| `cache` (decorator) | Cache module removed |
| `CacheGet` (top-level) | Renamed to `MemoGet` in `doeff_core_effects.memo_effects` |
| `graph_snapshot` | Concept removed |
| `Step`, `Annotate`, `Snapshot`, `CaptureGraph` | Graph tracking removed |
| `graph_to_html`, `write_graph_html` | Graph visualization removed |
| `AtomicGet`, `AtomicUpdate` | Concept removed |
| `AllocVar`, `ReadVar` | Use var_store directly |
| `presets` | Presets module removed |

---

## Next Steps

- **[Getting Started](01-getting-started.md)** - Begin using doeff
- **[Core Concepts](02-core-concepts.md)** - Understand fundamentals
- **[Patterns](12-patterns.md)** - Learn best practices
