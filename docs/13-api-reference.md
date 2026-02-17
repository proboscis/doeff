# API Reference

Complete API reference for doeff's current public surface.

## Core Types

### Program[T]

The core abstraction for lazy, reusable effectful computations.

```python
class Program[T]:
    def map(self, f: Callable[[T], U]) -> Program[U]
    def flat_map(self, f: Callable[[T], Program[U]]) -> Program[U]
    def and_then_k(self, binder: Callable[[T], Program[U]]) -> Program[U]

    @staticmethod
    def pure(value: T) -> Program[T]

    @staticmethod
    def lift(value: Program[U] | U) -> Program[U]
```

**See:** [Core Concepts](02-core-concepts.md#program-model)

---

### EffectBase / EffectValue[T]

Effects are user-space operation payloads. They are data, not execution control nodes.

```python
@dataclass(frozen=True, kw_only=True)
class EffectBase:
    created_at: EffectCreationContext | None = None
```

At runtime, effect dispatch happens through `Perform(effect)`.

**See:** [Core Concepts](02-core-concepts.md#control-vs-effect-data)

---

### RunResult[T]

Result container returned by `run()` and `async_run()`.

```python
class RunResult[T](Protocol):
    @property
    def result(self) -> Result[T]: ...

    @property
    def raw_store(self) -> dict[str, Any]: ...

    @property
    def value(self) -> T: ...

    @property
    def error(self) -> BaseException: ...

    def is_ok(self) -> bool: ...
    def is_err(self) -> bool: ...
```

**Core fields:**

- **`result`** - `Ok(value)` or `Err(error)`
- **`raw_store`** - Final store snapshot
- **`value`** - Unwraps `Ok` value (raises on `Err`)
- **`error`** - Unwraps `Err` error (raises on `Ok`)

**See:** [Error Handling](05-error-handling.md#runresult-overview)

---

### Result[T]

Success/failure sum type used by `RunResult.result` and `Safe(...)`.

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

**Signature:**
`Err(error: Exception, captured_traceback: Maybe[EffectTraceback] = NOTHING)`

**Fields:**

- **`error`** - The captured exception
- **`captured_traceback`** - Optional effect traceback metadata. Defaults to `NOTHING`,
  and is populated when traceback capture is enabled for that error path.

**Pattern matching:**

```python
match result:
    case Err(error=e, captured_traceback=tb):
        print("failed", e, tb)
```

---

## Decorators

### @do

Converts a generator function into a `KleisliProgram`.

```python
@do
def my_program(x: int) -> Program[int]:
    value = yield Get("key")
    return value + x
```

**Returns:** `KleisliProgram[..., T]`

**See:** [Core Concepts](02-core-concepts.md#generator-as-ast),
[Kleisli Arrows](11-kleisli-arrows.md)

---

### @cache

Caches Program results with policy fields.

```python
@cache(ttl=60, lifecycle=CacheLifecycle.SESSION)
@do
def expensive_computation(x: int):
    return x * 2
```

**Primary parameters:**

- **`ttl`** (`float | None`) - Time-to-live in seconds
- **`lifecycle`** (`CacheLifecycle | str | None`) - Cache lifecycle hint
- **`storage`** (`CacheStorage | str | None`) - Storage hint
- **`metadata`** (`Mapping[str, Any] | None`) - Policy metadata
- **`policy`** (`CachePolicy | Mapping[str, Any] | None`) - Full policy object

**See:** [Cache System](07-cache-system.md), [cache.md](cache.md)

---

## Reader Effects

### Ask(key)

Request an environment value.

Raises `MissingEnvKeyError` (a `KeyError` subtype) when `key` is not present in the current
environment.

If the environment value is a `Program`, `Ask` evaluates it lazily once per `run()` invocation and
caches the computed value per key. Concurrent `Ask` calls for the same lazy key are coordinated so
only one task performs evaluation while others wait cooperatively. `Local(...)` overrides can
invalidate that per-run cache for overridden keys.

```python
config = yield Ask("database_url")
```

**Signature:** `Ask(key: EnvKey)`

**See:** [Basic Effects](03-basic-effects.md#reader-effects)

---

### Local(env_update, sub_program)

Run a sub-program with environment overrides.

```python
result = yield Local({"timeout": 30}, sub_program())
```

**Signature:**
`Local(env_update: Mapping[Any, object], sub_program: ProgramLike)`

**See:** [Basic Effects](03-basic-effects.md#local)

---

## State Effects

### Get(key)

Read state value.

```python
count = yield Get("counter")
```

**Signature:** `Get(key: str)`

Raises `KeyError` when `key` is missing.

---

### Put(key, value)

Write state value.

```python
yield Put("counter", 42)
```

**Signature:** `Put(key: str, value: Any)`

---

### Modify(key, f)

Update state value using a transformation function.

```python
yield Modify("counter", lambda x: x + 1)
```

**Signature:** `Modify(key: str, f: Callable[[Any | None], Any])`

If `key` is missing, `Modify` calls `f(None)` (it does not raise `KeyError`).

`Modify` is atomic: if `f` raises, the store is left unchanged.

**See:** [Basic Effects](03-basic-effects.md#state-effects)

---

## Writer Effects

### Tell(message)

Append a log entry.

```python
yield Tell("Processing started")
```

**Signature:** `Tell(message: object)`

`Log` is a deprecated alias for `Tell`. Use `Tell` instead.

---

### Listen(sub_program)

Run a sub-program and capture emitted writer logs.

```python
result = yield Listen(sub_program())
value, logs = result
```

**Signature:** `Listen(sub_program: ProgramLike)`

**Returns:** `ListenResult(value: T, log: BoundedLog)`

`ListenResult.log` uses bounded retention semantics: when capacity is exceeded, oldest
entries are evicted.

---

### StructuredLog(**entries)

Append a structured log payload.

```python
yield StructuredLog(level="info", message="Processing", count=42)
```

**Signature:** `StructuredLog(**entries: object)`

**See:** [Basic Effects](03-basic-effects.md#writer-effects)

---

## Async and Concurrency Effects

### Await(awaitable)

Await a Python awaitable.

```python
value = yield Await(async_call())
```

**Signature:** `Await(awaitable: Awaitable[Any])`

`Await` bridges Python `asyncio` awaitables into doeff. For doeff-native `Task`/`Future`
handles, use `Wait` instead.

---

### Spawn(program, *, preferred_backend=None, **options)

Spawn a Program in the background and return a `Task` handle.

```python
task = yield Spawn(worker())
result = yield Wait(task)
```

**See:** [Advanced Effects](09-advanced-effects.md)

---

### Wait(future)

Wait for a `Task`/`Future` waitable value.

```python
result = yield Wait(task)
```

**Signature:** `Wait(future: Waitable[T])`

---

### Gather(*items)

Resolve multiple waitables and return results in input order.

```python
task_1 = yield Spawn(fetch_user(1))
task_2 = yield Spawn(fetch_user(2))
task_3 = yield Spawn(fetch_user(3))
results = yield Gather(task_1, task_2, task_3)
```

**Signature:** `Gather(*items: Waitable[Any])`

**See:** [Advanced Effects](09-advanced-effects.md#gather-effects)

---

### Race(*waitables)

Wait for the first waitable to complete.

```python
winner = yield Race(task_a, task_b)
value = winner.value
```

**Signature:** `Race(*futures: Waitable[Any])`

**Returns:** `RaceResult(first, value, rest)`

**See:** [Async Effects](04-async-effects.md#race-semantics),
[Advanced Effects](09-advanced-effects.md#race-effect)

---

### Task.cancel()

Request cooperative cancellation for a target `Task`.

```python
task = yield Spawn(worker())
_ = yield task.cancel()
```

**Signature:** `Task.cancel()`

`Task.cancel()` is effectful: it returns a cancellation effect that must be yielded.

Cancellation behavior depends on the target task state:

- **`Pending`** - Mark cancelled immediately and wake waiters with `TaskCancelledError`.
- **`Running`** - Set cooperative cancel flag; task is cancelled at next scheduler yield point.
- **`Suspended`** - Mark cancelled immediately and wake waiters with `TaskCancelledError`.
- **`Blocked`** - Mark cancelled, remove target wait registration, wake waiters with
  `TaskCancelledError`.
- **`Completed` / `Failed` / `Cancelled`** - No-op.

**See:** [Async Effects](04-async-effects.md#cancel-and-taskcancellederror)

---

### TaskCancelledError

Raised by `Wait`, `Gather`, or `Race` when a waited task was cancelled.

```python
joined = yield Safe(Wait(task))
if joined.is_err() and joined.error.__class__.__name__ == "TaskCancelledError":
    ...
```

**Signature:** `class TaskCancelledError(Exception)`

**Import:** `from doeff.effects import TaskCancelledError`

---

## Semaphore Effects

### CreateSemaphore(permits)

Create a semaphore handle with `permits` initial permits.

`permits` must be `>= 1`; `CreateSemaphore(0)` raises
`ValueError("permits must be >= 1")`.

```python
sem = yield CreateSemaphore(3)
```

**Signature:** `CreateSemaphore(permits: int)`

**Returns:** `Semaphore`

---

### AcquireSemaphore(sem)

Acquire one permit from a semaphore; blocks cooperatively when no permits are available.

```python
yield AcquireSemaphore(sem)
try:
    ...
finally:
    yield ReleaseSemaphore(sem)
```

**Signature:** `AcquireSemaphore(semaphore: Semaphore)`

Blocked acquirers are resumed in FIFO order. When no permit is available, the task transitions to
`BLOCKED` until a release occurs.

If a blocked waiter is cancelled, it is removed from the semaphore queue, raises
`TaskCancelledError`, and consumes no permit.

**See:** [Semaphore Effects](21-semaphore-effects.md#acquiresemaphoresem)

---

### ReleaseSemaphore(sem)

Release one permit back to a semaphore.

```python
yield ReleaseSemaphore(sem)
```

**Signature:** `ReleaseSemaphore(semaphore: Semaphore)`

When waiters exist, release uses direct handoff to the oldest waiter (FIFO): the permit transfers
to that waiter and `available_permits` remains `0`.

Permit leak warning: semaphores do not track ownership. If a task fails or is cancelled after
acquire and before release, that permit is leaked. Always guard critical sections with
`try/finally` so `ReleaseSemaphore` still runs.

Lifecycle: there is no explicit destroy API; semaphore state is released when handles are garbage
collected.

**See:** [Semaphore Effects](21-semaphore-effects.md#releasesemaphoresem)

---

## Control Effects

### Pure(value)

Return an immediate value without mutating state, environment, or writer log.

On the control path, `yield Pure(x)` is equivalent to returning `x` directly.

```python
value = yield Pure({"status": "ok"})
```

**Signature:** `Pure(value: Any)`

---

### Intercept(program, transform)

Run a program under effect interception. Each yielded effect is offered to `transform`.

```python
def transform(effect):
    if isinstance(effect, AskEffect) and effect.key == "timeout":
        return Pure(30)
    return None

result = yield Intercept(worker(), transform)
```

**Signature:**
`Intercept(program: Program[T], transform: Callable[[Effect], Effect | Program | None])`

**Transform contract:**

- Return `None` to pass through unchanged.
- Return an `Effect` to substitute that effect.
- Return a `Program` to execute replacement logic.

For multiple transforms, pass additional transform functions to `Intercept(...)`; first non-`None`
result wins.

Interception propagates to child execution contexts (for example `Gather`, `Safe`, and `Spawn`).

---

## Error Handling Effects

### Safe(sub_program)

Run a sub-program and capture errors as `Result`.

```python
result = yield Safe(risky_operation())
if result.is_ok():
    return result.value
return "fallback"
```

**Signature:** `Safe(sub_program: ProgramLike)`

**See:** [Error Handling](05-error-handling.md#safe-effect)

---

## Cache Effects

### CacheGet(key)

Retrieve a cached value.

```python
value = yield CacheGet("expensive_key")
```

**Signature:** `CacheGet(key: Any)`

---

### CachePut(key, value, ttl=None, *, lifecycle=None, storage=None, metadata=None, policy=None)

Store a value in cache.

```python
yield CachePut(
    "key",
    value,
    ttl=300,
    lifecycle=CacheLifecycle.PERSISTENT,
)
```

**See:** [Cache System](07-cache-system.md), [cache.md](cache.md)

---

## Graph Effects

### Step(value, meta=None)

Add a step node to the execution graph.

```python
yield Step("initialize", {"phase": "setup"})
```

**Signature:** `Step(value: Any, meta: dict[str, Any] | None = None)`

---

### Annotate(meta)

Add metadata to the latest graph step.

```python
yield Annotate({"user_id": 123, "operation": "fetch"})
```

**Signature:** `Annotate(meta: dict[str, Any])`

---

### Snapshot()

Capture current graph state.

```python
graph = yield Snapshot()
```

---

### CaptureGraph(program)

Run a sub-program and capture its graph output.

```python
value, graph = yield CaptureGraph(sub_program())
```

**Signature:** `CaptureGraph(program: ProgramLike)`

**See:** [Graph Tracking](08-graph-tracking.md#capturegraph)

---

## Atomic Effects

### AtomicGet(key, *, default_factory=None)

Thread-safe shared-state read.

```python
count = yield AtomicGet("counter")
```

**Signature:**
`AtomicGet(key: str, *, default_factory: Callable[[], Any] | None = None)`

---

### AtomicUpdate(key, updater, *, default_factory=None)

Thread-safe shared-state update.

```python
new_value = yield AtomicUpdate("counter", lambda x: x + 1)
```

**Signature:**
`AtomicUpdate(key: str, updater: Callable[[Any], Any], *, default_factory: Callable[[], Any] | None = None)`

---

## Pinjected Integration

### program_to_injected(program)

Convert `Program[T]` to `Injected[T]`.

```python
from doeff_pinjected import program_to_injected

injected = program_to_injected(my_program())
result = await resolver.provide(injected)
```

**Signature:** `program_to_injected(prog: Program[T]) -> Injected[T]`

---

### program_to_injected_result(program)

Convert `Program[T]` to `Injected[RunResult[T]]`.

```python
from doeff_pinjected import program_to_injected_result

injected = program_to_injected_result(my_program())
result = await resolver.provide(injected)
```

**Signature:** `program_to_injected_result(prog: Program[T]) -> Injected[RunResult[T]]`

---

### program_to_iproxy(program)

Convert `Program[T]` to `IProxy[T]`.

```python
from doeff_pinjected import program_to_iproxy

iproxy = program_to_iproxy(my_program())
```

**Signature:** `program_to_iproxy(prog: Program[T]) -> IProxy[T]`

---

### program_to_iproxy_result(program)

Convert `Program[T]` to `IProxy[RunResult[T]]`.

```python
from doeff_pinjected import program_to_iproxy_result

iproxy = program_to_iproxy_result(my_program())
```

**Signature:** `program_to_iproxy_result(prog: Program[T]) -> IProxy[RunResult[T]]`

---

## Execution

### run

Synchronously execute a Program/effect with explicit handler stack.

```python
from doeff import default_handlers, run

result = run(
    my_program(),
    handlers=default_handlers(),
    env={"key": "value"},
    store={"state": 0},
)
```

**Signature:**

```python
def run(
    program: DoExpr[T] | EffectValue[T],
    handlers: Sequence[Any] = (),
    env: dict[Any, Any] | None = None,
    store: dict[str, Any] | None = None,
    trace: bool = False,
) -> RunResult[T]
```

`env` and `store` are the execution inputs (Reader and State roots).

---

### async_run

Asynchronously execute a Program/effect with async-aware await handling.

```python
from doeff import async_run, default_async_handlers

result = await async_run(
    my_program(),
    handlers=default_async_handlers(),
    env={"key": "value"},
    store={"state": 0},
)
```

**Signature:**

```python
async def async_run(
    program: DoExpr[T] | EffectValue[T],
    handlers: Sequence[Any] = (),
    env: dict[Any, Any] | None = None,
    store: dict[str, Any] | None = None,
    trace: bool = False,
) -> RunResult[T]
```

---

### run_program(...)

Run a Program from Python using CLI-equivalent discovery defaults.

```python
from doeff import run_program

result = run_program("pkg.features.login_program", quiet=True, report=True)
assert result.value == "ok"
```

**Signature:**

```python
def run_program(
    program: str | Program[Any],
    *,
    interpreter: str | Callable[..., Any] | None = None,
    envs: list[str | Program[dict[str, Any]] | Mapping[str, Any]] | None = None,
    apply: str | KleisliProgram[..., Any] | Callable[[Program[Any]], Program[Any]] | None = None,
    transform: list[str | Callable[[Program[Any]], Program[Any]]] | None = None,
    report: bool = False,
    report_verbose: bool = False,
    quiet: bool = False,
    load_default_env: bool = True,
) -> ProgramRunResult
```

**See:** [Python run_program API](16-run-program-api.md)

---

### ProgramRunResult

Dataclass returned by `run_program()`.

```python
from doeff import ProgramRunResult
```

**Fields:**

- **`value`** - Final Program value
- **`run_result`** - `RunResult[Any] | None` (present when interpreter returns one)
- **`interpreter_path`** - Resolved interpreter path or callable descriptor
- **`env_sources`** - Applied environment sources
- **`applied_kleisli`** - Applied Kleisli descriptor (if any)
- **`applied_transforms`** - Applied transform descriptors

---

## Utilities

### graph_to_html(graph, *, title="doeff Graph Snapshot", mark_success=False)

Generate graph-visualization HTML as a Program.

```python
from doeff import default_handlers, graph_to_html, run

html = run(graph_to_html(graph), handlers=default_handlers()).value
```

**Signature:**
`graph_to_html(graph: WGraph, *, title: str = ..., mark_success: bool = False) -> Program[str]`

---

### write_graph_html(graph, output_path, *, title="doeff Graph Snapshot", mark_success=False)

Write graph HTML to a file as a Program.

```python
from doeff import default_handlers, run, write_graph_html

path = run(write_graph_html(graph, "output.html"), handlers=default_handlers()).value
```

**Signature:**
`write_graph_html(graph: WGraph, output_path: str | Path, *, title: str = ..., mark_success: bool = False) -> Program[Path]`

---

## Quick Reference

### Effect Categories

| Category | Effects |
|----------|---------|
| **Reader** | Ask, Local |
| **State** | Get, Put, Modify |
| **Writer** | Tell, Listen, StructuredLog, slog |
| **Async/Concurrency** | Await, Spawn, Wait, Gather, Race, Task.cancel, TaskCancelledError |
| **Semaphore** | CreateSemaphore, AcquireSemaphore, ReleaseSemaphore |
| **Control** | Pure, Intercept |
| **Error** | Safe, Ok, Err |
| **Cache** | CacheGet, CachePut |
| **Graph** | Step, Annotate, Snapshot, CaptureGraph |
| **Atomic** | AtomicGet, AtomicUpdate |

### Common Imports

```python
# Core
from doeff import Program, EffectBase, do

# Execution
from doeff import run, async_run
from doeff import default_handlers, default_async_handlers

# Reader / State / Writer
from doeff import Ask, Local, Get, Put, Modify, Tell, Listen, StructuredLog, slog

# Async / Concurrency
from doeff import Await, Spawn, Wait, Gather, Race, Task
from doeff.effects import TaskCancelledError  # raised on Wait/Gather/Race for cancelled tasks

# Semaphore
from doeff import AcquireSemaphore, CreateSemaphore, ReleaseSemaphore, Semaphore

# Control
from doeff import Pure, Intercept

# Error handling
from doeff import Safe, Ok, Err

# Cache
from doeff import CacheGet, CachePut, cache

# Graph
from doeff import Step, Annotate, Snapshot, CaptureGraph, graph_to_html, write_graph_html

# Atomic
from doeff import AtomicGet, AtomicUpdate

# Pinjected (separate package)
from doeff_pinjected import (
    program_to_injected,
    program_to_injected_result,
    program_to_iproxy,
    program_to_iproxy_result,
)
```

## Next Steps

- **[Getting Started](01-getting-started.md)** - Begin using doeff
- **[Core Concepts](02-core-concepts.md)** - Understand fundamentals
- **[Patterns](12-patterns.md)** - Learn best practices
