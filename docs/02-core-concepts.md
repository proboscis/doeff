# Core Concepts

This chapter defines the doeff execution model:

- `Program[T]` is `DoExpr[T]`
- `DoExpr` is control IR evaluated by the Rust VM
- effect values are user-space data and dispatch through `Perform(effect)`
- `@do` generators are lazy ASTs interpreted by the runtime

## Program Model

`Program[T]` is the user-facing program type for effectful computations.
In the type model, `Program[T]` is `DoExpr[T]`. It is a virtual type alias
with no methods — use the standalone constructors `Pure(x)`, `Perform(e)`, etc.

```python
# DoExpr is a virtual base type (isinstance check only).
# No .map(), .flat_map(), or .pure() methods exist on it.
class DoExpr(metaclass=_DoExprMeta):
    """isinstance(x, DoExpr) returns True for any program node."""

# DoCtrl subtypes are the concrete IR nodes.
# Pure, Perform, Expand, WithHandlerType, WithObserve, ...

Program = DoExpr
```

## Control vs Effect Data

The execution boundary is explicit:

- `DoExpr[T]`: control expression evaluated by VM
- `DoCtrl` subtypes: concrete control nodes (`Pure`, `Expand`, `Perform`, `WithHandlerType`, `WithObserve`, ...)
- `EffectValue[T]`: operation payload (`Ask`, `Get`, `Put`, `Tell`, ...)

Effect values are lifted into control IR with `Perform(effect)`.

```python
from doeff import Perform
from doeff_core_effects import Ask

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
- `Expand(Apply(...))` invocation node — `@do` functions return `Expand` when called
- `Perform(effect)` effect dispatch node
- `WithHandlerType(handler, body)` handler scoping node
- `WithObserve(observer, body)` observation/interception node
- `Apply(target, args)` function application node
- `Pass(effect, k)` re-perform to outer handler
- continuation/control nodes such as `Resume`, `Transfer`, `ResumeThrow`, `TransferThrow`

These nodes are VM syntax, so composition remains in IR.

## `@do` and Macro Expansion

`@do` returns a `Callable[..., Expand]`. Calling it emits an `Expand` node.

The `@do` wrapper:

1. Wraps the generator function in a thunk.
2. Returns `Expand(Apply(Pure(Callable(thunk)), []))`.

Example:

```python
from doeff import do
from doeff_core_effects import Ask

@do
def fetch_user(user_id: int):
    db = yield Ask("db")
    return db[user_id]

program = fetch_user(42)
```

`program` above is a `DoExpr` (`Expand`) that is evaluated by `run`.

### Non-Generator `@do` Functions

`@do` also supports non-generator functions that use plain `return` and never
`yield`. In that case, call-time expansion still produces a valid `Expand` node,
and runtime evaluation returns the function result normally.

### Metadata Preservation

The decorator preserves normal function metadata so tooling still works:
`__doc__`, `__name__`, `__qualname__`, `__module__`, `__annotations__`, and
the inspectable signature. This metadata preservation is part of the public
behavior contract for `@do`.

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

## `run`

`run(doexpr)` is the single entrypoint. It accepts one argument and returns
the raw result value directly.

- `DoExpr[T]` — any program node
- raw effect values are normalized at the boundary to `Perform(effect)`

For async/concurrent programs, wrap with `scheduled()`:

```python
from doeff import run
from doeff_core_effects.scheduler import scheduled

result = run(scheduled(prog))
```

Conceptual runner:

```python
def run(doexpr):
    vm = PyVM()
    return vm.run(doexpr)
```

There is no `async_run` — use `run(scheduled(prog))` instead.

## Handler Contract

Handlers interpret effects with this shape:

- input: `(effect, k)`
- output: `DoExpr`

Handlers use `yield Resume(k, value)` to resume the continuation with a value,
`yield Pass(effect, k)` to forward the effect to an outer handler, or
`yield effect` to re-perform an effect in the handler body.

If a host handler returns an effect value, runtime normalizes it through `Perform(effect)`
before continuing.

## WithObserve Contract

`WithObserve(observer, body)` installs scoped observation for yielded values.
The observer receives matched yields and can inspect or log them.

```python
from doeff import WithObserve

def my_observer(effect, k):
    print(f"observed: {effect}")

prog = WithObserve(my_observer, body)
```

## Composition

Use standalone constructors for building program nodes:

- `Pure(x)` — lift a plain value into a program node
- `Perform(effect)` — lift an effect into a program node

Effect payloads compose after lifting:

```python
from doeff import Pure, Perform
from doeff_core_effects import Ask

program = Perform(Ask("key"))
```

Handler composition uses the `handler(body)` pattern:

```python
from doeff_core_effects.handlers import reader, state, writer

prog = writer()(state(initial={"count": 0})(body()))
```

## Core Example

```python
from doeff import do, run
from doeff_core_effects import Ask, Get, Put, Tell
from doeff_core_effects.handlers import reader, state, writer

@do
def update_counter():
    counter_key = yield Ask("counter_key")
    current = yield Get(counter_key)
    yield Put(counter_key, current + 1)
    yield Tell(f"counter updated: {current} -> {current + 1}")
    return current + 1

w = writer()
prog = w(state(initial={"count": 0})(reader(env={"counter_key": "count"})(update_counter())))
result = run(prog)
```

Here handlers are composed individually: `writer()(state()(reader()(prog)))`.
Each handler factory returns a `Program -> Program` installer.

## Summary

- `Program[T]` is `DoExpr[T]` — a virtual type alias with no methods
- effect dispatch is explicit via `Perform(effect)`
- VM evaluates control IR and handlers interpret effect payloads
- `@do` calls emit `Expand` nodes that are evaluated by the runtime
- `run(doexpr)` takes a single argument and returns the raw value
- for async/concurrent execution, use `run(scheduled(prog))`
