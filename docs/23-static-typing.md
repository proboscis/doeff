# 23. Static Typing of Effects and Programs

doeff ships type information (`py.typed` in `doeff`, `doeff_vm`, `doeff_core_effects`).
With pyright (or any checker that reads the annotations) an effectful program is
checked for three things: the value each effect answers with, the effects a
program may yield, and the value a handler answers with.

## Effects declare their answer type

```python
from dataclasses import dataclass
from doeff import EffectBase

@dataclass(frozen=True)
class ReadClock(EffectBase[int]):        # the handler answers with an int
    pass

class WriteEffect(EffectBase[T]):        # a generic parent is fine
    pass

@dataclass(frozen=True)
class WriteShared(WriteEffect[bool]):
    key: str
```

`EffectBase[T]` is valid at runtime (`types.GenericAlias`). An effect that
subclasses plain `EffectBase` has an unknown answer type.

## `yield from` gives the answer its type

(The same types without `yield from`: `@effectful` + `perform(e)`, rewritten at import —
docs/24-effectful-perform.md.)

```python
@do
def elapsed(since: int) -> Generator[ReadClock, Any, int]:
    now = yield from ReadClock()          # now: int
    return now - since
```

`yield from eff` evaluates exactly like `yield eff` (the effect's `__iter__`
yields the effect once and returns what the VM sends back); only the static
type differs — `yield eff` gives `Any`. Programs work the same way:
`spent = yield from elapsed(400)` runs the sub-program and gives `spent: int`,
as does `yield from Pure(x)`. A program under a handler, `h(p)` (a handler
installed with `@handler`), is typed `Program[Any]`: the installer's signature
does not carry `p`'s result type, so `yield from h(p)` gives `Any`.

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
| `Wait(task_or_future)` / `Race(*...)` | `T` |
| `Gather(*tasks)` | `list[T]` |
| `CreatePromise[T]()` | `Promise[T]` (`.future: Future[T]`) |
| `CompletePromise(promise, value)` | `None`, `value` checked against `T` |
| `Cancel`, `AcquireSemaphore`, `ReleaseSemaphore`, `FailPromise` | `None` |
| `CreateSemaphore(n)` | `Semaphore` |

## Handler answers

```python
@do
def clock(effect: ReadClock, k: K):
    return (yield typed_resume(effect, k, now_ms()))   # value must be an int
```

`typed_resume(effect, k, value)` / `typed_transfer(effect, k, value)` are
`Resume(k, value)` / `Transfer(k, value)` whose value is checked against the
effect's `T`. The `@do` tail-resume analysis recognises `typed_resume` like
`Resume`. The effect annotation (`effect: ReadClock`) is also the handler's
runtime filter — see "Typed handlers" in [Core Concepts](02-core-concepts.md).

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

## Mapping from a hand-written typing layer

| hand-written layer | doeff |
|---|---|
| `class Eff[T](EffectBase)` with `__iter__` | `EffectBase[T]` |
| `Prog[E, T]` | `Expand[T, E]` / `Program[T, E]` (result first) |
| `@program` | `@do` |
| `call(p)` | `yield from p` |
| `effect.reply(value)` | `typed_resume(effect, k, value)` |
| isinstance dispatch table | the effect annotation of the handler |
