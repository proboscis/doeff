# 24. `@effectful` and `perform` — writing effects in plain Python syntax

An `@effectful` function writes each effect as an ordinary function call, `perform(effect)`.
pyright checks both the answer type and the effects the function is allowed to perform; at run time, the
import step rewrites `perform(e)` into `(yield e)`, producing the same Program as today's `@do`.

Copy the two files below (`clock.py` and `main.py`) into the same dir and `python main.py` runs them, with no
errors from pyright (`strict`) or mypy (`--strict`). The effects and the `@effectful` functions go in `clock.py`:

```python file=clock.py
from dataclasses import dataclass

from doeff import EffectBase, Effects, effectful


@dataclass(frozen=True)
class ReadClock(EffectBase[int]):          # the answer type is int (docs/23-static-typing.md)
    pass


@dataclass(frozen=True)
class ReadShared(EffectBase[list[str]]):   # the answer is the list of keys starting with prefix
    prefix: str


@dataclass(frozen=True)
class WriteShared(EffectBase[bool]):       # the answer is whether the write succeeded
    key: str


@effectful
def elapsed(perform: Effects[ReadClock], since: int) -> int:
    now = perform(ReadClock())             # now: int
    return now - since


@effectful
def place_turns(perform: Effects[ReadShared | WriteShared | ReadClock]) -> int:
    rows = perform(ReadShared("turn/"))    # rows: list[str] (the answer type of ReadShared)
    spent = perform(elapsed(400))          # another Program also runs through perform (spent: int)
    placed = 0
    for row in rows:
        if perform(WriteShared(f"{row}/{spent}")):   # the answer is bool
            placed += 1
    return placed
```

- `perform` is received as the function's **first parameter** (after `self` for a method), and `Effects[...]`
  lists the effects this function may perform. The return annotation stays an ordinary `-> int`.
- The type seen by callers is `Callable[P, Expand[T, E]]` (the `perform` parameter disappears). It is the same
  type as `@do`, so it mixes freely with `@do` Programs.

`main.py` registers `clock` for rewriting before importing it (see "Import-time rewriting" below), then runs it
with a handler that answers the three effects. Handlers are still written as generators with `typed_resume`:

```python file=main.py
from collections.abc import Generator
from typing import Any

from doeff import K, handler, install_import_hook, run, typed_resume

install_import_hook("clock")               # register the rewrite before importing clock

from clock import ReadClock, ReadShared, WriteShared, elapsed, place_turns

program = elapsed(400)    # Expand[int, ReadClock] — a Program not yet run (same type as @do)


@handler
def world(effect: ReadClock | ReadShared | WriteShared, k: K) -> Generator[Any, Any, Any]:
    if isinstance(effect, ReadClock):
        return (yield typed_resume(effect, k, 1_000))
    if isinstance(effect, ReadShared):
        return (yield typed_resume(effect, k, [f"{effect.prefix}1", f"{effect.prefix}2"]))
    return (yield typed_resume(effect, k, True))


print(run(world(program)))        # 600
print(run(world(place_turns())))  # 2
```

## What pyright catches

The 8 deliberate mistakes in `tests/test_static_typing.py` are rewritten in the `perform` form, and
`tests/test_effectful.py::test_pyright_types_perform_and_catches_each_mistake` pins that all 8 are reported
as errors.

| Mistake | Error |
|---|---|
| Wrong answer type `now: str = perform(ReadClock())` | `reportAssignmentType` |
| Undeclared effect `perform(Delete("row"))` (inside `Effects[ReadClock]`) | `reportArgumentType` |
| The called Program's effect is undeclared `perform(elapsed(1))` (inside `Effects[WriteShared]`) | `reportArgumentType` |
| Wrong Program result type `label: str = perform(elapsed(1))` | `reportAssignmentType` |
| Wrong handler answer type `typed_resume(effect, k, "now")` | `reportArgumentType` (handlers are still written as generators) |
| The `Spawn`ed child's effect is undeclared `perform(Spawn(elapsed(1)))` | `reportArgumentType` |
| Wrong `Wait` result type `text: str = perform(Wait(task))` | `reportAssignmentType` |
| Wrong argument type `elapsed("1")` | `reportArgumentType` |
| Bonus: calling `perform` outside `@effectful` | `reportUndefinedVariable` (`perform` is not defined anywhere) |

How it works: `Effects[E].__call__` is `(self: Effects[X], effect: _Performable[X, T]) -> T`.
`_Performable[X, T]` is the shape "performs effects X, answers T": an effect performs itself and answers the T of
`EffectBase[T]`, `Expand[T, E]` performs E and answers T, and `Spawn(p)` performs itself plus p's effects and
answers `Task[T]` (it reads the type declarations of docs/23 as they are). `Effects` is contravariant in E, so if
X does not fit inside the declared E, the argument type does not match and it is an error.

## Where it can be written, and what the rewrite rejects

`perform` can be written only directly in the body of an `@effectful` function (that is the only place where the
generator can be suspended). The import-time rewrite rejects the following with a `SyntaxError` (pointing at file
and line). `doeff-effectful-check PATH ...` runs the same check without importing (it can go in CI or pre-commit).

- A function that is not `@effectful` takes an `Effects[...]` parameter
- `perform` inside a lambda, a comprehension (list / set / dict), a generator expression, a nested plain function,
  or a class body
  (make the nested function `@effectful` and it can use `perform` inside and be called with `perform(inner(...))`)
- Passing, assigning, or returning `perform` as a value
- Writing `yield` / `yield from` / `await` directly in an `@effectful` body (one way to write effects)
- `perform(...)` given other than exactly one value, a function without a `perform` parameter, `perform` without an
  `Effects[...]` annotation, `async def`

pyright alone does not flag `perform` inside a lambda or comprehension (at the type level it is an ordinary function
call). The rewrite and `doeff-effectful-check` cover that.

## Import-time rewriting

- Registration: call `doeff.install_import_hook("mypkg")` before importing that package.
  The basic form is writing `install_import_hook(__name__)` in the package's `__init__.py`, which covers every
  module below it (a single module name also works, as in `main.py` above). Even inside a registered package, a
  module whose source does not contain the text `effectful` is compiled normally without rewriting.
- Using `@effectful` in an unregistered module makes `@effectful` raise a `TypeError` on the spot, whose message
  says which package to register (when the module lacks the rewrite marker `__doeff_effectful__`, or its version
  differs).
- What the rewrite does: `perform(e)` → `(yield e)`, and the `perform` parameter is removed. The result is the same
  kind of generator function `@do` takes, and it becomes a Program through the same `program_factory` as `@do`
  (doeff/do.py).
- Tracebacks: rewritten nodes keep the position (line and column) of the original call, so tracebacks point at the
  original source line (`test_traceback_of_a_handler_error_points_at_the_perform_line`).
- Bytecode cache: `__pycache__/<name>.<cache_tag>-doeff-effectful-<version>.pyc`. The version is a hash of the
  contents of the rewrite module (`doeff/_effectful_rewrite.py`), so when the rewrite changes the cache name changes
  and old caches are not read (the same idea as pytest's assertion rewriting putting a version in the name).
  Validity of the contents is judged by the source's mtime and size, as with the standard import.

## Runtime semantics and cost

- `tests/test_effectful.py` checks handlers (`Resume` / `Transfer`), nested Programs, methods,
  `Spawn` / `Wait` / `Gather`, `finally` running on `Cancel`, and sending a not-yet-run Program to another process
  with cloudpickle.
- The bytecode of a rewritten function matches the same function written with `@do` + `yield` (same `co_code`).
  Cost of one effect (2026-09-23, a `Transfer` handler, best of 200,000 runs):
  `yield` 0.90 µs, `perform` 0.91 µs, `yield from` 1.00 µs. `perform` equals `yield` and is about 10% cheaper than
  `yield from`.

## The Hy side

doeff-hy's expansion for type checking (`doeff-hy-check`, packages/doeff-hy/docs/static-check.md) emits
`(<- x T e)` as `_hy_gensym_bound_N: 'T' = _doeff_perform(e)` then `x = _hy_gensym_bound_N` (the declared type is on a
temporary, so same-named binds of other types in other clauses / branches of one function do not collide). This is the same shape as Python's `x = perform(e)`, and gives the same
answer type without using `yield` or `yield from`. Hy has no way yet to write an effect set, so `_doeff_perform`
does not match against a declaration and passes values of unknown type as Any. The runtime Hy expansion can stay
`(yield e)` — that is the same shape as the result of the `@effectful` rewrite.

## Relation to the `yield from` form

The `x = yield from Eff(...)` form of docs/23 rests on two things: the runtime `__iter__` of effects and Programs
(Rust `src/typing_support.rs`) and the declared `__iter__` in the type stubs (`doeff_vm/__init__.pyi` and others).
The `perform` form **does not use the runtime `__iter__`** (the rewrite produces `yield e`), but
**`_Performable` reads the declared `__iter__` as is**.

- The runtime `__iter__` can be removed once the `yield from` style is retired.
- To remove the declared `__iter__`, first move "the effects performed and the answer type" to another declaration
  (for example a type-only dedicated attribute), repoint `_Performable` at it, then remove it.
- Whether to drop the `yield from` style (its impact on users' code) is decided separately.

## Decisions (reversible, 2026-09-23)

| Decision | Reason | Rollback |
|---|---|---|
| The effects a function may perform are declared by the `perform: Effects[E]` parameter, not the return annotation | Writing `-> Program[int, E]` makes the body's `return n` a pyright error (`int` is not `Program[int, E]`), and a global `perform(e: EffectBase[T]) -> T` cannot catch undeclared effects (measured: 2 errors on correct code, 0 for undeclared effects). As a parameter, it catches all 8 kinds | Change the overloads of `Effects` and `effectful` |
| Registration is explicit by package name (`install_import_hook`) | Deciding by reading every module's source adds cost to unrelated imports. A forgotten registration makes `@effectful` raise a named error, so it does not break silently | Change `EffectfulFinder.covers` |
| The cache version is a hash of the rewrite module | If a hand-maintained version number is not bumped, caches of the old rewrite get read. A hash always changes whenever the rewrite changes | Make `REWRITE_VERSION` a constant |
| In the type declaration (`__iter__`) of `Spawn`, it performs itself as `Spawn[Any, Any]` rather than `Spawn[_T, _E]` | With `perform(Spawn(p))` where p performs nothing (`E = Never`), strict pyright filled in `Spawn[Unknown, Unknown]` from the declared union and reported a "partially Unknown" error (the `yield from` form does not). The declared union has always been written as `Spawn[Any, Any]`, so what gets caught does not change. A type-only overload fix was not adopted because, combined with the mainline change to type-parameter defaults, it made `yield from Spawn(...)` "no matching overload" | Change the `Spawn[Any, Any]` in the `__iter__` annotation in scheduler.py back to `Spawn[_T, _E]` |
| Hy's type-checking expansion of `(<- x e)` became `_doeff_perform(e)` (no `yield` emitted) | Aligns with the Python form and emits e only once. Confirmed that the errors in agora-controllers' `controllers/worker` did not change (57, the same set) | Change the static branch of `_bind-yield` in macros.hy back to `_doeff_bound(e, (yield e))` |

## Not yet verified / out of scope

- Writing handlers with `@effectful` (handlers are still written as generators with `typed_resume`).
- Writing `@effectful` directly in a pytest test file (`test_*.py`): pytest's assertion rewriting and the import hook
  overlap, so put `@effectful` functions in a module outside the test file.
- `program_signature` does not read the E of an `@effectful` function (the annotation disappears along with the
  `perform` parameter).
- Measurements on Python 3.10–3.13 (this measurement used the free-threaded build of 3.14).
