# 23. Static Typing of Effects and Programs

doeff ships type information (`py.typed` in `doeff`, `doeff_vm`, `doeff_core_effects`).
With pyright (or any checker that reads the annotations) an effectful program is
checked for three things: the value each effect answers with, the effects a
program may yield, and the value a handler answers with.

The examples that start with `import` lines build one file: copy them in
order, and pyright (`strict`) and mypy (`--strict`) report no errors. Fragments
without imports show a few lines out of a larger program.

## Effects declare their answer type

```python
from dataclasses import dataclass
from typing import TypeVar

from doeff import EffectBase, effect_result_type

T = TypeVar("T")


@dataclass(frozen=True)
class ReadClock(EffectBase[int]):        # the handler answers with an int
    pass


class WriteEffect(EffectBase[T]):        # a generic parent is fine
    pass


@dataclass(frozen=True)
class WriteShared(WriteEffect[bool]):
    key: str


print(effect_result_type(WriteShared))   # <class 'bool'>
```

`EffectBase[T]` is valid at runtime (`types.GenericAlias`), so the answer type
can also be read at runtime (`effect_result_type` — see "Runtime readers"
below). An effect that subclasses plain `EffectBase` has an unknown answer type.

## `yield from` gives the answer its type

(The same types without `yield from`: `@effectful` + `perform(e)`, rewritten at import —
docs/24-effectful-perform.md.)

Add this below the example above:

```python continues
from collections.abc import Generator
from typing import Any

from doeff import do


@do
def elapsed(since: int) -> Generator[ReadClock, Any, int]:
    now = yield from ReadClock()          # now: int
    return now - since
```

`yield from eff` evaluates exactly like `yield eff` (the effect's `__iter__`
yields the effect once and returns what the VM sends back); only the static
type differs — `yield eff` gives `Any`. Programs work the same way:
`spent = yield from elapsed(400)` runs the sub-program and gives `spent: int`,
as does `yield from Pure(x)`. A program under a handler keeps its result type:
every installer — `reader(env)`, `state()`, `writer`, `handler(h)`,
`stacked_handlers(...)` — is a `doeff.program.ProgramHandler`, which takes a
`Program[T, E]` and gives a `Program[T, Any]`, so `yield from reader(env)(p)` and
`run(reader(env)(p))` give `p`'s `T`. The effects after installing are `Any`:
Python types cannot take the effects the handler answers out of `E`. Installing
on a value that is not a program — the `@do` function itself (`writer(prog)`)
or `state()(42)` — is an error.

## `@do` keeps parameters, effects and result

`@do` turns `Callable[P, Generator[E, Any, T]]` into `Callable[P, Expand[T, E]]`:

- `P`: calling with wrong arguments is an error
- `T`: the result type seen by `yield from`
- `E`: the effects the body may yield. Yielding an effect outside `E` is an
  error, and `yield from sub()` adds `sub`'s `E` to the caller's, so the
  declaration closes over calls.

`Program[T, E]` is the protocol every program node satisfies; `Program[T]`
leaves the effects open. `EffectGenerator[T]` is `Generator[Any, Any, T]`
(effects unchecked).

To also catch a forgotten `yield from` on an effect whose value is not used
(`SleepSeconds(1.0)` as a bare statement), enable `reportUnusedCallResult` in
the business-code files.

## Scheduler effects

| effect | answer |
|---|---|
| `Spawn(p)` with `p: Program[T, E]` | `Task[T]`; `yield from Spawn(p)` also adds `E` to the caller's effects (the task runs under the caller's handlers) |
| `Wait(task_or_future)` | `T` |
| `Race(*tasks_or_futures)` | `T`, the union of the handles' result types |
| `Gather(*tasks_or_futures)` | `list[T]`, `T` the union of the handles' result types |
| `CreatePromise[T]()` | `Promise[T]` (`.future: Future[T]`) |
| `CompletePromise(promise, value)` | `None`, `value` checked against `T` |
| `Cancel`, `AcquireSemaphore`, `ReleaseSemaphore`, `FailPromise` | `None` |
| `CreateSemaphore(n)` | `Semaphore` |

`Task[T]` and `Future[T]` are covariant in `T`: a `Task[int]` is also a
`Task[object]`. So handles of different result types go into one `Gather` or
`Race`. Add this below the examples above:

```python continues
from doeff import Gather, Race, Spawn


@do
def label(since: int) -> Generator[ReadClock, Any, str]:
    spent = yield from elapsed(since)
    return f"{spent} ms"


@do
def both(since: int) -> Generator[
    Spawn[Any, Any] | Gather[int | str] | Race[int | str] | ReadClock, Any, list[int | str]
]:
    number = yield from Spawn(elapsed(since))   # Task[int]
    text = yield from Spawn(label(since))       # Task[str]
    together = yield from Gather(number, text)  # list[int | str]
    first = yield from Race(number, text)       # int | str
    return [*together, first]
```

The spawned programs' `ReadClock` is in `both`'s effects: the tasks run under
the caller's handlers.

mypy joins the result types instead of taking their union (`int` and `str`
join to `object`), so `Gather` and `Race` declare overloads for 2 to 6 handles
that give the union under mypy too. With more than 6 handles of different
types, mypy answers `list[object]` / `object`; pyright still answers the union.

## Handler answers

A handler for `ReadClock` runs `elapsed`, and under the scheduler `both`. Add
this below the examples above:

```python continues
from doeff import K, handler, run, typed_resume
from doeff_core_effects.scheduler import scheduled


def now_ms() -> int:
    return 1_000


@handler
def clock(effect: ReadClock, k: K) -> Generator[Any, Any, Any]:
    return (yield typed_resume(effect, k, now_ms()))   # value must be an int


print(run(clock(elapsed(400))))               # 600
print(run(scheduled(clock(both(400)))))       # [600, '600 ms', 600]
```

`typed_resume(effect, k, value)` / `typed_transfer(effect, k, value)` are
`Resume(k, value)` / `Transfer(k, value)` whose value is checked against the
effect's `T`. The `@do` tail-resume analysis recognises `typed_resume` like
`Resume`. The effect annotation (`effect: ReadClock`) is also the handler's
runtime filter — see "Typed handlers" in [Core Concepts](02-core-concepts.md).

`handler(raw)` takes a raw handler `(effect, k: K)` whose effect annotation is
an `EffectBase` class, a union of them, `EffectBase` itself, or open (no
annotation, `Any`, `object`). A handler no effect can reach is a type error in
pyright and mypy, as it is a `TypeError` when `@handler` runs:

```python excerpt
@handler                                     # refused here
def wrong(effect: str, k: K):                # no effect is a str
    return (yield Resume(k, None))

# pyright: Argument of type "(effect: str, k: K) -> ..." cannot be assigned to
#          parameter "raw_handler" of type "(_HandledEffect@handler, K) -> object"
# mypy:    Value of type variable "_HandledEffect" of "handler" cannot be "str"
```

A function that does not take `(effect, k)` is refused the same way. A marker
base the effects share (`WriteEffect` in Core Concepts) must derive from
`EffectBase` for the type checker to accept `effect: WriteEffect`: a base that
does not, and a runtime-checkable `Protocol`, still filter at run time, but the
type checker cannot tell them from `str`.

## Runtime readers (for Hy and other tools)

The same annotations are readable at runtime, so tools that cannot run pyright
— the doeff-hy static checker, checks that an environment's handler stack
covers a program's effects — read one source of truth instead of re-declaring
types:

| function | returns |
|---|---|
| `effect_result_type(ReadClock)` | `int` (follows generic parents: `WriteShared` → `bool`) |
| `program_signature(elapsed)` | `ProgramSignature(effects=(ReadClock,), result=int)`; `effects=None` when open |
| `handler_effect_types(clock)` | `(ReadClock,)` — the handler's runtime filter; `None` = every effect |

A Hy form such as `(<- now int (ReadClock))` can be checked by comparing `int`
with `effect_result_type(ReadClock)`, and a `defk` whose annotation declares its
effects can be checked with `program_signature`.

## What is not typed

- `Ask` / `Get` answer `Any` (the environment and state are dynamic).
- Checking that an environment's handlers cover a program's `E` is not done by
  the type checker; `program_signature` + `handler_effect_types` give the data.
- `with_handlers([...], p)` and `stacked_handlers(...)` take any callable: the
  type checker reads a raw handler's effect annotation in `handler(raw)` only
  (all three refuse it at run time).

## Mapping from a hand-written typing layer

| hand-written layer | doeff |
|---|---|
| `class Eff[T](EffectBase)` with `__iter__` | `EffectBase[T]` |
| `Prog[E, T]` | `Expand[T, E]` / `Program[T, E]` (result first) |
| `@program` | `@do` |
| `call(p)` | `yield from p` |
| `effect.reply(value)` | `typed_resume(effect, k, value)` |
| isinstance dispatch table | the effect annotation of the handler |
