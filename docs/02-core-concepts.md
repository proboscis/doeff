# Core Concepts

This chapter defines the current doeff execution model:

- `Program[T]` is the user-facing name for `DoExpr[T]`
- `DoExpr` is control IR evaluated by the VM
- effect values are user-space data, dispatched only through `Perform(effect)`
- generators produced by `@do` are lazy ASTs interpreted by the Rust VM

## Mental Model

doeff is an algebraic-effects runtime with one-shot continuations.

1. Programs build control expressions (`DoExpr`).
2. Programs request operations by yielding effect values.
3. Runtime lowering inserts `Perform(effect)` at the dispatch boundary.
4. Handlers interpret effects and resume continuations exactly once.
5. The Rust VM evaluates control nodes and manages continuation state.

## Program[T] Is Not a Dataclass Wrapper

Older docs described `Program[T]` as a dataclass wrapping a generator function.
That is stale.

Current model:

- `DoExpr[T]`: composable control expression type
- `DoCtrl[T]`: VM instruction set (control nodes)
- `Program[T]`: public alias/type name for user code working with `DoExpr`

Conceptually:

```python
class DoExpr(Generic[T]):
    def map(self, f): ...
    def flat_map(self, f): ...
    @staticmethod
    def pure(value): ...

class DoCtrl(DoExpr[T]):
    ...

Program = DoExpr  # user-facing name
```

In the Python package, `Program` is exposed via `ProgramBase`, which is a `DoExpr` subtype.

## DoExpr vs DoCtrl vs EffectValue

The key boundary is explicit:

- `DoExpr[T]`: control IR evaluated by VM
- `DoCtrl[T]`: concrete control nodes (`Pure`, `Call`, `Map`, `FlatMap`, `Perform`, ...)
- `EffectValue[T]`: user-space operation data (`Ask`, `Get`, `Put`, `Tell`, ...)

Effect values are not control nodes. They become executable only when lifted:

```python
from doeff import Perform

expr = Perform(Ask("key"))  # EffectValue -> DoExpr
```

Source-level ergonomics are preserved:

```python
# user code
value = yield Ask("key")

# lowered control form
value = yield Perform(Ask("key"))
```

`Perform(effect)` is the control boundary for handler dispatch.

## Generator-as-AST (Free Monad)

For `@do` programs, the generator is not a thin wrapper. It is lazy program text.

From the VM perspective:

1. `next(gen)` reads the next expression node.
2. VM evaluates that node.
3. VM sends the result back via `gen.send(value)`.
4. Generator yields the next node, until `StopIteration`.

Free-monad interpretation:

```
yield expr  ≡  Bind(expr, λresult. rest_of_program)
```

So each `yield` captures continuation state, and VM drives evaluation.

## DoCtrl Instruction Set

Core control forms include:

- `Pure(value)` literal leaf node
- `Call(f, args, kwargs, metadata)` function/generator invocation
- `Eval(expr, handlers)` scoped evaluation
- `Map(source, f)` functor map
- `FlatMap(source, f)` monadic bind
- `Perform(effect)` explicit effect dispatch
- `WithHandler(handler, body)` handler scoping
- continuation/control nodes such as `Resume`, `Transfer`, `Delegate`, `ResumeContinuation`
- async escape node/result integration via `PythonAsyncSyntaxEscape` in async path

`Map` and `FlatMap` are DoCtrl nodes, not ad-hoc Python wrappers. This is why composition stays in IR.

## `@do` and Call-Time Macro Expansion

`@do` returns a `KleisliProgram`. Calling it does not execute immediately; it emits a `Call` control node.

`KleisliProgram.__call__()`:

1. Computes auto-unwrap strategy from annotations.
2. Lifts unwrapable effect arguments to `Perform(arg)`.
3. Wraps non-unwrapped values as `Pure(arg)`.
4. Returns `Call(Pure(func), args, kwargs, metadata)`.

This is a call-time macro expansion step, not handler dispatch.

Example:

```python
from doeff import Ask, do

@do
def fetch_user(user_id: int):
    db = yield Ask("db")
    return db[user_id]

program = fetch_user(Ask("active_user_id"))
# program is a DoExpr (Call node), not an executed result
```

## Rust VM Architecture

Execution pipeline:

1. Python driver obtains yielded object from generator frame.
2. `classify_yielded` performs binary classification:
   - `DoCtrlBase` -> evaluate as control
   - `EffectBase` -> dispatch through handler stack (or normalize to `Perform`)
3. VM step loop processes control until `Done`, `Failed`, `Continue`, or async escape.

Important properties:

- Binary classifier (`DoCtrlBase | EffectBase`) replaces older multi-branch string matching.
- Tag-based dispatch in VM is designed for low-overhead/GIL-minimized hot paths.
- Effects remain opaque payloads to VM; handler logic owns effect interpretation.

## `run` vs `async_run`

Both entrypoints accept:

- `DoExpr[T]` directly
- raw effect value (`EffectValue[T]`), normalized at boundary to `Perform(effect)`

Conceptual sync runner:

```python
def run(program, handlers):
    state = init(program, handlers)
    while True:
        out = step(state)
        if out is Done:
            return value
        if out is Failed:
            raise error
        if out is Continue:
            state = out.state
```

Conceptual async runner:

```python
async def async_run(program, handlers):
    state = init(program, handlers)
    while True:
        out = step(state)
        if out is Done:
            return value
        if out is Failed:
            raise error
        if out is Continue:
            state = out.state
        if out is PythonAsyncSyntaxEscape:
            resolved = await out.awaitable
            state = out.resume(resolved)
```

Key distinction:

- `run`: no async escape handling; sync-compatible handlers must resolve awaitables internally.
- `async_run`: handles `PythonAsyncSyntaxEscape` by awaiting in the caller's event loop.

## Handlers and the Dispatch Contract

Handlers operate at the effect boundary:

- input: `(effect, k)` where `k` is continuation
- output: `DoExpr`

If a host handler returns raw effect data, runtime normalizes it back through `Perform(effect)` before continuation.

## Composition and Lifting

Composition is IR-native:

- `Program.pure(x)` creates `Pure(x)`
- `expr.map(f)` creates `Map(expr, f)`
- `expr.flat_map(f)` creates `FlatMap(expr, f)`

Effects compose after lifting:

```python
from doeff import Ask, Perform

program = Perform(Ask("key")).map(str.upper)
```

## Core Example (Current Semantics)

```python
from doeff import Ask, Get, Put, Tell, do, run, default_handlers

@do
def update_counter():
    counter_key = yield Ask("counter_key")
    current = yield Get(counter_key)
    yield Put(counter_key, current + 1)
    yield Tell(f"counter updated: {current} -> {current + 1}")
    return current + 1

result = run(
    update_counter(),
    handlers=default_handlers(),
    env={"counter_key": "count"},
    store={"count": 0},
)
```

Use `Tell` for writer output in docs and examples. `Log` is not part of the current core conceptual model.

## Summary

- `Program[T]` is the public `DoExpr[T]` model, not a dataclass wrapper.
- `DoExpr` (control) and `EffectValue` (data) are separate.
- `Perform(effect)` is the explicit dispatch boundary.
- `@do` calls macro-expand to `Call` nodes.
- The Rust VM interprets a lazy free-monad AST via binary yield classification.
- `run` and `async_run` share control evaluation but differ at async escape handling.
