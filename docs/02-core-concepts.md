# Core Concepts

This chapter defines the doeff execution model:

- `Program[T]` is `DoExpr[T]`
- `DoExpr` is control IR evaluated by the Rust VM
- effect values are user-space data and dispatch through `Perform(effect)`
- `@do` generators are lazy ASTs interpreted by the runtime

## Program Model

`Program[T]` is the user-facing program type for effectful computations.
In the type model, `Program[T]` is `DoExpr[T]`.

```python
class DoExpr(Generic[T]):
    def map(self, f): ...
    def flat_map(self, f): ...
    @staticmethod
    def pure(value): ...

class DoCtrl(DoExpr[T]):
    ...

Program = DoExpr
```

## Control vs Effect Data

The execution boundary is explicit:

- `DoExpr[T]`: control expression evaluated by VM
- `DoCtrl[T]`: concrete control nodes (`Pure`, `Call`, `Map`, `FlatMap`, `Perform`, ...)
- `EffectValue[T]`: operation payload (`Ask`, `Get`, `Put`, `Tell`, ...)

Effect values are lifted into control IR with `Perform(effect)`.

```python
from doeff import Ask, Perform

expr = Perform(Ask("key"))
```

At source level, programs can still yield effect values directly:

```python
value = yield Ask("key")
```

Lowering represents this as:

```python
value = yield Perform(Ask("key"))
```

## Generator-as-AST

`@do` programs are interpreted as lazy AST streams:

1. `next(gen)` yields a control expression.
2. VM evaluates the expression.
3. VM sends the result with `gen.send(value)`.
4. Generator yields the next expression, until completion.

Free-monad interpretation:

```
yield expr  ≡  Bind(expr, λresult. rest_of_program)
```

## DoCtrl Instruction Set

Core control nodes include:

- `Pure(value)` literal node
- `Call(f, args, kwargs, metadata)` invocation node
- `Eval(expr, handlers)` scoped evaluation node
- `Map(source, f)` composition node
- `FlatMap(source, f)` bind node
- `Perform(effect)` effect dispatch node
- `WithHandler(handler, body)` handler scoping node
- continuation/control nodes such as `Resume`, `Transfer`, `Delegate`, `ResumeContinuation`

These nodes are VM syntax, so composition remains in IR.

## `@do` and Macro Expansion

`@do` returns a `KleisliProgram`. Calling it emits a `Call` node.

`KleisliProgram.__call__()`:

1. Computes argument unwrap policy from annotations.
2. Lifts unwrapable effect arguments to `Perform(arg)`.
3. Wraps pass-through values as `Pure(arg)`.
4. Returns `Call(Pure(func), args, kwargs, metadata)`.

Example:

```python
from doeff import Ask, do

@do
def fetch_user(user_id: int):
    db = yield Ask("db")
    return db[user_id]

program = fetch_user(Ask("active_user_id"))
```

`program` above is a `DoExpr` (`Call`) that is evaluated by `run`/`async_run`.

## Rust VM Execution Pipeline

Runtime flow:

1. Driver reads yielded object from current generator frame.
2. `classify_yielded` performs binary classification:
   - `DoCtrlBase` -> VM control evaluation path
   - `EffectBase` -> handler dispatch path (normalized through `Perform`)
3. VM step loop advances until `Done`, `Failed`, `Continue`, or async escape state.

Key properties:

- binary yield classification (`DoCtrlBase | EffectBase`)
- low-overhead tag-based dispatch in hot paths
- effect payload fields stay opaque to VM core and are interpreted by handlers

## `run` and `async_run`

Both entrypoints accept:

- `DoExpr[T]`
- raw effect values, normalized at the boundary to `Perform(effect)`

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

`PythonAsyncSyntaxEscape` is handled in the async execution path.

## Handler Contract

Handlers interpret effects with this shape:

- input: `(effect, k)`
- output: `DoExpr`

If a host handler returns an effect value, runtime normalizes it through `Perform(effect)`
before continuing.

## Intercept Transform Contract

`Intercept(program, *transforms)` installs scoped transforms for yielded effects.
Each transform is called with the current effect and may return:

- `None`: pass through to the next transform (or to normal effect handling if none match)
- `Effect`: replace the original effect with that effect
- `Program`: replace the original effect by running that program

Transforms are evaluated in declaration order, and the first non-`None` result wins.
This contract defines interception as effect-to-effect/program rewriting at the
`Perform(effect)` boundary, not as a separate control-node family.

## Composition

IR-level composition primitives:

- `Program.pure(x)` -> `Pure(x)`
- `expr.map(f)` -> `Map(expr, f)`
- `expr.flat_map(f)` -> `FlatMap(expr, f)`

Effect payloads compose after lifting:

```python
from doeff import Ask, Perform

program = Perform(Ask("key")).map(str.upper)
```

## Core Example

```python
from doeff import Ask, Get, Put, Tell, default_handlers, do, run

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

## Summary

- `Program[T]` is `DoExpr[T]`
- effect dispatch is explicit via `Perform(effect)`
- VM evaluates control IR and handlers interpret effect payloads
- `@do` calls emit `Call` nodes that are evaluated by the runtime
- `run` and `async_run` share core stepping semantics with different async integration paths
