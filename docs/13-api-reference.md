# API Reference

Complete API reference for doeff's current public surface.

Most code blocks on this page are excerpts: a few lines from inside a `@do` function, or a call
on a program you write yourself (`my_program()`, `worker()`). They do not run on their own. The
blocks that run as written print what their `# ...` comments state — for example
[@do](#do), [Resume](#resumek-value), [Pass](#passeffect-k), [slog_handler](#slog_handler)
and [run](#rundoexpr).

## Core Types

### Program (DoExpr)

The core abstraction for lazy, reusable effectful computations.

`Program` is a virtual type alias for `DoExpr`. It is a union of all program node types
(`Pure`, `Expand`, `Perform`, `WithHandlerType`, `WithObserve`, etc.). There are no
`.map()`, `.flat_map()`, `.pure()`, or `.lift()` methods. Compose programs using the
`@do` decorator and `yield`.

```python excerpt
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

An effect keeps its data in its own class's fields (an `__init__` as above, or `@dataclass`). A class
with no `__init__` keeps no arguments: `EffectBase("fetch", url=...)` (`doeff.Effect`), or a subclass
without an `__init__` called with arguments, raises `TypeError` in the release after 0.5.0; 0.5.0 drops
the arguments without a word.

**See:** [Core Concepts](02-core-concepts.md#control-vs-effect-data)

---

### Result[T]

Success/failure sum type used by `Try(...)`.

```python excerpt
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
from doeff import Get, do, run
from doeff_core_effects.handlers import state

@do
def my_program(x: int):
    value = yield Get("key")
    return value + x

prog = my_program(42)  # returns Expand (a DoExpr node), not executed yet
result = run(state(initial={"key": 1})(prog))  # execute under a handler for Get
print(result)  # 43
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

Raises `MissingEnvKeyError` (a `KeyError`) when `key` is not present in the current environment;
its message names the keys the environment holds and the close names of `key`.

If the environment value is a `Program`, `Ask` evaluates it lazily once per `run()` invocation and
caches the computed value per key (when using `lazy_ask`). Concurrent `Ask` calls for the same lazy key are coordinated so
only one task performs evaluation while others wait cooperatively. `Local(...)` overrides can
invalidate that per-run cache for overridden keys.

```python excerpt
config = yield Ask("database_url")
```

**Signature:** `Ask(key)`

**See:** [Basic Effects](03-basic-effects.md#reader-effects)

---

### Local(env, program)

Run a sub-program with environment overrides.

```python excerpt
result = yield Local({"timeout": 30}, sub_program())
```

**Signature:** `Local(env: Mapping, program: DoExpr)`

**See:** [Basic Effects](03-basic-effects.md#local)

---

## State Effects

### Get(key)

Read state value.

```python excerpt
count = yield Get("counter")
```

**Signature:** `Get(key)`

Returns `None` when `key` is missing (with the default `state()` handler).

---

### Put(key, value)

Write state value.

```python excerpt
yield Put("counter", 42)
```

**Signature:** `Put(key, value)`

**See:** [Basic Effects](03-basic-effects.md#state-effects)

---

## Writer Effects

### Tell(message)

Append a log entry. `Tell(message)` is a convenience function that creates a
`WriterTellEffect(message)`.

```python excerpt
yield Tell("Processing started")
```

**Signature:** `Tell(message) -> WriterTellEffect`

---

### slog(msg, **kwargs)

Emit a structured log entry. Creates a `SlogEffect` with the message and keyword arguments.

```python excerpt
yield slog("Processing", count=42, level="info")
```

**Signature:** `slog(msg, **kwargs) -> SlogEffect`

`Slog` is an alias for `SlogEffect`. `SlogEffect` is a separate type from `WriterTellEffect`:
`writer` does not collect it, and it needs `slog_handler` (display) or `slog_discard_handler`
(silence). See [slog_handler](#slog_handler).

---

### WriterTellEffect(msg)

The effect type created by `Tell`. `writer` collects these, and `Listen` collects them by default.

```python excerpt
yield WriterTellEffect("event")
```

**Signature:** `WriterTellEffect(msg)`

**Fields:**

- **`msg`** - The log message

---

### Listen(program, types=None)

Run a sub-program and collect all effects of the given types emitted during execution.

```python excerpt
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

```python excerpt
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

```python excerpt
value = yield Pure({"status": "ok"})
```

**Signature:** `Pure(value)`

---

### Resume(k, value)

Answer an effect inside a handler: send `value` to the program that performed the
effect and run the rest of it. `yield Resume(k, value)` gives the handler that
program's answer, and the handler's return value is the answer of the program the
handler was installed on. A handler that returns without `Resume` (or `Transfer`)
ends that program with its return value: the rest of the program never runs, and no
error is raised.

```python
from dataclasses import dataclass

from doeff import EffectBase, Resume, do, handler, run


@dataclass(frozen=True)
class Double(EffectBase):
    value: int


@do
def doubles(effect: Double, k):
    return (yield Resume(k, effect.value * 2))


@do
def gives_up(effect: Double, k):
    return "gave up"  # no Resume: the rest of compute() never runs


@do
def compute():
    doubled = yield Double(21)
    return f"answer={doubled}"


print(run(handler(doubles)(compute())))  # answer=42
print(run(handler(gives_up)(compute())))  # gave up
```

**Signature:** `Resume(k, value)`. `Transfer(k, value)` resumes the same way when the
handler is done after answering; the handler does not get the program's answer back.

**See:** [Core Concepts](02-core-concepts.md#returning-without-resume-ends-the-program-under-the-handler)

---

### Pass(effect, k)

Forward an effect to the next outer handler. Used inside handler functions to say
"I don't handle this effect". A handler without an annotation is called for every
effect, so for an effect it does not answer it must `yield Pass(effect, k)`: falling
off the end returns `None`, which ends the handled program with `None`. A handler
whose effect parameter is annotated (`effect: Double`) is not called for other
effects and needs no `Pass` for them.

```python
from dataclasses import dataclass

from doeff import EffectBase, Pass, Resume, do, handler, run
from doeff_core_effects import Ask
from doeff_core_effects.handlers import reader


@dataclass(frozen=True)
class Double(EffectBase):
    value: int


@do
def my_handler(effect, k):
    if isinstance(effect, Double):
        return (yield Resume(k, effect.value * 2))
    yield Pass(effect, k)  # forward everything else (here: Ask)


@do
def without_pass(effect, k):
    if isinstance(effect, Double):
        return (yield Resume(k, effect.value * 2))


@do
def compute():
    base = yield Ask("base")
    return (yield Double(base))


print(run(reader(env={"base": 21})(handler(my_handler)(compute()))))  # 42
print(run(reader(env={"base": 21})(handler(without_pass)(compute()))))  # None
```

**Signature:** `Pass(effect, k)`

**See:** [Core Concepts](02-core-concepts.md#pass-the-effects-a-handler-does-not-answer)

---

### WithObserve(observer, body)

Install a cross-cutting observer and run a body program under it. The observer is
called for each effect performed during execution.

```python excerpt
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

```python excerpt
from doeff_core_effects.handlers import reader

scoped = reader(env={"name": "override"})(worker())
```

**Shape:** `handler(program: DoExpr) -> DoExpr`

Handlers built with `doeff.program.handler()` have this `Program -> Program` shape.
Compose them by nesting calls:

```python excerpt
prog = my_program()
prog = writer(prog)
prog = state()(prog)
prog = reader(env={"key": "value"})(prog)
result = run(scheduled(prog))
```

### with_handlers(handlers, program) and stacked_handlers(*handlers)

`with_handlers([h1, h2], program)` applies a handler list in scope order: `h1` is outermost,
`h2` innermost. An entry that carries the handler marker (made by `defhandler` or
`doeff.handler()`) is called with the program; an entry without it is a raw effect dispatcher
and is called with `(effect, k)`. A `Program -> Program` function passed without the marker —
`lambda program: ...`, or a factory that returns one — is refused with a `TypeError` naming it
when the list is applied, before anything runs. To pass several handlers as one, bundle them with
`stacked_handlers(h1, h2)`: the result is a marked installer with the same order, and an
empty `stacked_handlers()` is the identity.

```python excerpt
from doeff import stacked_handlers, with_handlers

test_handlers = stacked_handlers(reader(env={"key": "value"}), state())
result = run(scheduled(with_handlers([test_handlers, writer], my_program())))
```

---

## Async and Concurrency Effects

### Await(coroutine)

Await a Python coroutine or future. Bridges async into doeff.

```python excerpt
value = yield Await(async_call())
```

**Signature:** `Await(coroutine)`

Requires `await_handler()` and `scheduled()` to be installed.

---

### Spawn(program, priority=PRIORITY_NORMAL)

Spawn a program in the background and return a `Task` handle.

```python excerpt
task = yield Spawn(worker())
result = yield Wait(task)
```

**Signature:** `Spawn(program, priority=PRIORITY_NORMAL)`

**Returns:** `Task`

**See:** [Advanced Effects](09-advanced-effects.md)

---

### Wait(task)

Wait for a `Task` or `Future` handle to complete.

```python excerpt
result = yield Wait(task)
```

**Signature:** `Wait(task, priority=None)`

---

### Gather(*tasks)

Wait for multiple tasks/futures and return results in input order.

```python excerpt
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

```python excerpt
winner = yield Race(task_a, task_b)
```

**Signature:** `Race(*tasks)`

**See:** [Async Effects](04-async-effects.md#race-semantics),
[Advanced Effects](09-advanced-effects.md#race-effect)

---

### Cancel(task)

Request cooperative cancellation for a target `Task`.

```python excerpt
task = yield Spawn(worker())
yield Cancel(task)
```

**Signature:** `Cancel(task)`

`Cancel` is an effect that must be yielded. There is no `task.cancel()` method.

**See:** [Async Effects](04-async-effects.md#cancel-and-taskcancellederror)

---

### TaskCancelledError

Raised by `Wait`, `Gather`, or `Race` when a waited task was cancelled.

```python excerpt
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

```python excerpt
promise = yield CreatePromise()
future = promise.future  # read-side Future handle
```

**Signature:** `CreatePromise()`

**Returns:** `Promise` (with `.future` property for `Wait`)

---

### CompletePromise(promise, value)

Complete a promise with a success value.

```python excerpt
yield CompletePromise(promise, result_value)
```

**Signature:** `CompletePromise(promise, value, *, yield_to_woken=True)`

- `yield_to_woken=True` (default): the completer goes on only after every task the completion woke has run (#493) —
  a waiter that must register a fresh promise before the next completion cannot miss it.
- `yield_to_woken=False`: the completer goes on at once; the woken tasks run when it next waits (like asyncio's
  `Future.set_result` or a Redis `PUBLISH`). Use it only when the waiters cannot miss what the completer does next —
  the in-memory notice broker of doeff-events queues a notice for a subscriber not waiting (agora-redesign #4013).
  Deadlock detection is the same for both.

---

### FailPromise(promise, error)

Complete a promise with an error.

```python excerpt
yield FailPromise(promise, ValueError("something went wrong"))
```

**Signature:** `FailPromise(promise, error)`

---

### CreateExternalPromise()

Create a thread-safe external promise. The returned `ExternalPromise` has `.complete(value)`
and `.fail(error)` methods that can be called from any thread.

```python excerpt
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

```python excerpt
sem = yield CreateSemaphore(3)
```

**Signature:** `CreateSemaphore(permits=1)`

**Returns:** `Semaphore`

---

### AcquireSemaphore(semaphore)

Acquire one permit from a semaphore; blocks cooperatively when no permits are available.

```python excerpt
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

```python excerpt
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

```python excerpt
from doeff_core_effects.cache_effects import CacheGet

value = yield CacheGet("expensive_key")
```

**Signature:** `CacheGet(key) -> CacheGetEffect`

---

### CachePut(key, value, ttl=None, *, lifecycle=None, storage=None, metadata=None, policy=None)

Store a value in cache with optional policy.

```python excerpt
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

```python excerpt
from doeff_core_effects.memo_effects import MemoGet

value = yield MemoGet("computation_result")
```

**Signature:** `MemoGet(key, *, recompute_cost=RecomputeCost.CHEAP) -> MemoGetEffect`

---

### MemoPut(key, value, ...)

Store a memoized value.

```python excerpt
from doeff_core_effects.memo_effects import MemoPut

yield MemoPut("key", value, recompute_cost="expensive")
```

**Signature:** `MemoPut(key, value, ttl=None, *, recompute_cost=None, lifecycle=None, metadata=None, policy=None, source_effect=None) -> MemoPutEffect`

---

## Execution

### run(doexpr)

Execute a DoExpr program to completion and return the raw result value.

```python
from doeff import Ask, Put, Tell, do, run
from doeff_core_effects.handlers import reader, state, writer
from doeff_core_effects.scheduler import scheduled

@do
def my_program():
    name = yield Ask("name")
    yield Put("greeted", True)
    yield Tell(f"Hello, {name}")
    return f"Done greeting {name}"

prog = my_program()
prog = writer(prog)
prog = state()(prog)
prog = reader(env={"name": "Alice"})(prog)
result = run(scheduled(prog))
print(result)  # Done greeting Alice
# result is the raw value, not a wrapper
```

**Signature:**

```python excerpt
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

```python excerpt
from doeff import do, run
from doeff_core_effects.handlers import reader, state, writer, try_handler, slog_handler
from doeff_core_effects.scheduler import scheduled

prog = my_program()
prog = slog_handler(prog)        # innermost
prog = writer(prog)
prog = state(initial={"k": 0})(prog)
prog = reader(env={"key": "val"})(prog)  # outermost user handler
result = run(scheduled(prog))    # scheduler wraps everything
```

### reader(env=None)

Handles `Ask(key)` by looking up `key` in `env`. Raises `MissingEnvKeyError` (a `KeyError`) on miss.

```python
from doeff_core_effects.handlers import reader

prog = reader(env={"db_url": "postgres://..."})(my_program())
```

---

### lazy_ask(env=None, *, strict=False)

Handles `Ask` and `Local` with lazy evaluation and caching. If an env value is a
`Program`, evaluates it lazily once per key with concurrent coordination via semaphores.

```python excerpt
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

### writer

Handles `Tell(message)` / `WriterTellEffect`. Collects messages into a list.
Pre-installed `Program -> Program` handler (not a factory -- use directly).
Requires `state()` to be installed as an outer handler.

Access the log from inside a `@do` program via `yield writer_log()`.

```python excerpt
from doeff_core_effects.handlers import writer, writer_log

prog = writer(my_program())
prog = state()(prog)
run(scheduled(prog))

# To access the log, use writer_log() inside a @do program:
@do
def with_log_access():
    yield my_program()
    log = yield writer_log()
    return log
```

---

### slog_handler

Handles `slog(...)` / `SlogEffect` structured log entries by printing each one as a line on
stderr (`    INFO event count=42`). It only displays: it collects nothing and does not need
`state()`. Pre-installed `Program -> Program` handler (not a factory -- use directly).
`slog_discard_handler` consumes the entries without printing them.

To read the entries as values, capture them with `Listen(program, types=(SlogEffect,))` inside a
`@do` program. `Listen` passes each entry on, so `slog_handler` or `slog_discard_handler` is still
needed outside it. (`slog_log()` was retired.)

```python
from doeff import Listen, SlogEffect, do, run, slog
from doeff_core_effects.handlers import listen_handler, slog_handler

@do
def my_program():
    yield slog("event", count=42)
    return "done"

# Display: prints "    INFO event count=42" on stderr
print(run(slog_handler(my_program())))  # done

# Capture the entries as values
@do
def with_slog_capture():
    value, entries = yield Listen(my_program(), types=(SlogEffect,))
    return [(entry.msg, entry.kwargs) for entry in entries]

print(run(slog_handler(listen_handler(with_slog_capture()))))  # [('event', {'count': 42})]
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
| **Control** | `Pure`, `Resume`, `Transfer`, `Pass`, `WithObserve`, handler installer calls |
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
from doeff import Pure, Resume, Transfer, Pass, WithObserve

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
from doeff import Ask, Put, Tell, do, run
from doeff_core_effects.handlers import reader, state, writer
from doeff_core_effects.scheduler import scheduled

@do
def my_program():
    name = yield Ask("name")
    yield Put("count", 1)
    yield Tell(f"greeted {name}")
    return name

prog = my_program()
prog = writer(prog)
prog = state()(prog)
prog = reader(env={"name": "Alice"})(prog)
result = run(scheduled(prog))
print(result)  # Alice
```

---

## Removed APIs

The following APIs have been removed. The names still importable from `doeff` raise
`RuntimeError` with a migration hint when used; `RunResult`, `Step`, `Annotate`, `Snapshot`,
`CaptureGraph`, `graph_to_html`, `write_graph_html`, `AtomicGet` and `AtomicUpdate` are no longer
importable.

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
