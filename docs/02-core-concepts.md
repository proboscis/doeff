# Core Concepts

doeff implements algebraic effects: a program yields effects, and a handler receives each effect
together with the continuation `k` and decides what the effect means by what it does with `k`.
This chapter defines that model first, then the layer that runs it:

- a handler receives `(effect, k)`, stays installed around `k`, and resumes, passes, throws into,
  holds or drops it
- `Program[T]` is `DoExpr[T]`
- `DoExpr` is control IR evaluated by the Rust VM
- effect values are user-space data and dispatch through `Perform(effect)`
- `@do` generators are lazy ASTs interpreted by the runtime

[![A narrated video, under three minutes: an effect, a handler and k; an effect travelling out to the handler whose annotation matches it; a handler staying installed around k; resuming, throwing into, passing, holding and dropping k; the order of nested handlers; and how run evaluates a program yield by yield](assets/videos/core-concepts.gif)](assets/videos/core-concepts.mp4)

## Effects, Handlers and Continuations

- **An effect** is a plain value that names an operation: `Get("count")`, `Spawn(task)`, or an
  instance of an effect class of your own under `EffectBase`. It does nothing by itself.
- **A program** pauses at each `yield` of an effect.
- **A handler** is a callable of `(effect, k)`. The innermost handler installed around the program
  whose annotation matches the effect receives it.
- **The continuation `k`** is the rest of the program from that `yield` on. The handler stays
  installed around it, so it also receives the effects the rest of the program yields.

`after` below is installed once and receives both `Step` effects of `job`: the second one comes
from inside `k`, which runs under the handler. `yield Resume(k, None)` runs the rest of `job` and
gives the handler its result, which the handler returns changed:

```python
from dataclasses import dataclass
from doeff import EffectBase, Resume, do, handler, run

@dataclass(frozen=True)
class Step(EffectBase):
    name: str

@do
def job():
    yield Step("load")
    yield Step("train")
    return "model"

@handler
@do(non_tail=True)                       # the handler goes on after the resumed k returns
def after(effect: Step, k):              # receives both Steps: it stays installed around k
    result = yield Resume(k, None)       # the rest of job runs here, under this handler
    return f"{result} after {effect.name}"

print(run(after(job())))  # model after train after load
```

What a handler can do with `k` ([Handler Contract](#handler-contract) has the rules):

- **resume it** with `Resume(k, value)`, and get the result of the rest back; or with
  `Transfer(k, value)` when it is done after answering
- **throw into it** with `ResumeThrow(k, error)`: the program sees `error` raised at the `yield`
- **pass the effect on** with `Pass(effect, k)` to the next handler out, or perform an effect of
  its own with `yield effect` while it answers
- **hold it** while other tasks run: the scheduler (`scheduled()`) is a handler that keeps the
  continuation of each waiting task, and `Spawn`, `Gather`, `Race` and `Wait` are its effects
- **drop it** by returning a value without resuming: the rest of the program never runs

`k` is one-shot: a handler resumes it at most once, and a second `Resume` of the same `k` raises a
`RuntimeError`. Effects that need several resumptions of one continuation, such as backtracking or
nondeterministic choice, cannot be written as handlers
([Continuations are one-shot](positioning/what-doeff-is.md#continuations-are-one-shot)).

The effects of the reference implementation `doeff-core-effects` work the same way. `state()` answers `Get` and `Put` from a dict and resumes
`k`; `writer` answers `Tell` by yielding `Get` and `Put` to `state()`; `try_handler` answers
`Try(program)` with `Ok(value)` or `Err(error)`. State, exceptions, concurrency, observation and I/O are effects and handlers of this one
kind, and a program uses any of them together.

## Program Model

`Program[T]` is the user-facing program type for effectful computations.
In the type model, `Program[T]` is `DoExpr[T]`. It is a virtual type alias
with no methods — use the standalone constructors `Pure(x)`, `Perform(e)`, etc.

```python excerpt
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
from doeff import Perform, run
from doeff_core_effects import Ask
from doeff_core_effects import reader

expr = Perform(Ask("key"))
print(type(expr).__name__)  # Perform
print(run(reader(env={"key": "value"})(expr)))  # value
```

At source level, programs can still yield effect values directly:

```python excerpt
value = yield Ask("key")
```

Lowering represents this as:

```python excerpt
value = yield Perform(Ask("key"))
```

## Generator-as-AST

`@do` programs are interpreted as lazy AST streams:

1. `next(gen)` yields a control expression.
2. VM evaluates the expression.
3. VM sends the result with `gen.send(value)`.
4. Generator yields the next expression, until completion.

Each `yield` splits the generator into the expression and the rest of the program:

```text
yield expr  ≡  Bind(expr, λresult. rest_of_program)
```

The `λ` is the continuation. For a control node, the VM evaluates the node and calls it with the
value. For an effect, the VM hands it to the handler as `k`, and the handler decides whether, when
and with what it is called.

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
from doeff import Expand, do, run
from doeff_core_effects import Ask
from doeff_core_effects import reader

@do
def fetch_user(user_id: int):
    db = yield Ask("db")
    return db[user_id]

program = fetch_user(42)
print(isinstance(program, Expand))  # True
print(run(reader(env={"db": {42: "Ada"}})(program)))  # Ada
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

Cost of one effect (release build, Apple M-series, CPython 3.14t, 2026-09-23;
a plain Python function call is 0.03 µs):

| shape | µs per effect |
|---|---|
| `@do` handler answering with `Transfer` | 0.95 |
| `@do` handler answering with `Resume` | 1.16 |
| each typed handler skipped by its annotation | ~0.14 |
| each handler that runs and yields `Pass` | ~0.9 |

So: prefer `Transfer` when the handler is done after answering, and let the
effect annotation skip handlers instead of an `isinstance` + `Pass` prologue.

## `run`

`run(doexpr)` is the single entrypoint. It accepts one argument and returns
the raw result value directly.

- `DoExpr[T]` — any program node
- raw effect values are normalized at the boundary to `Perform(effect)`

For async/concurrent programs, wrap with `scheduled()`:

```python
from doeff import Spawn, Wait, do, run
from doeff_core_effects import scheduled

@do
def child():
    return 21

@do
def prog():
    task = yield Spawn(child())
    return (yield Wait(task)) * 2

result = run(scheduled(prog()))
print(result)  # 42
```

Conceptual runner:

```python excerpt
def run(doexpr):
    vm = PyVM()
    return vm.run(doexpr)
```

There is no `async_run` — use `run(scheduled(prog))` instead.

## Handler Contract

Handlers interpret effects with this shape:

- input: `(effect, k)`
- output: `DoExpr`

Handlers use `yield Resume(k, value)` to resume the continuation with a value
(or `yield Transfer(k, value)` when nothing is left to do after it),
`yield ResumeThrow(k, error)` to raise `error` at the `yield` in the program,
`yield Pass(effect, k)` to forward the effect to an outer handler, or
`yield effect` to re-perform an effect in the handler body. A handler that
returns without resuming, transferring or passing `k` ends the program it
handles (see below).

A handler body that returns `Resume(...)`, `Pass(...)` or an effect without
yielding it raises a `TypeError` when it runs: yield it, and return what it gives back.

A handler is a `def (effect, k)` marked with `@handler`, which makes it a `@do`
function (the handler body). `handler(f)` also takes a `@do` function, or a
`functools.partial` or bound method of one. A lambda, a `functools.partial` or
bound method of a plain function, and a callable instance are not handler bodies:
they are refused with a `TypeError` where the handler is made (ADR-DOE-CORE-003).

### Returning without `Resume` ends the program under the handler

A handler's return value is the answer of the program it was installed on.
`yield Resume(k, value)` first runs the rest of that program and gives its answer
back to the handler, so `return (yield Resume(k, value))` passes the program's own
answer through. A handler that returns without `Resume` (or `Transfer`) drops the
rest of the program instead: the code after the `yield` that performed the effect
never runs, and the program answers the handler's return value.

```python
from dataclasses import dataclass

from doeff import EffectBase, Resume, do, handler, run


@dataclass(frozen=True)
class Fetch(EffectBase):
    url: str


@handler
def answers(effect: Fetch, k):
    return (yield Resume(k, f"got {effect.url}"))


@handler
def stops(effect: Fetch, k):
    return f"stopped at {effect.url}"  # no Resume: the rest of page() never runs


@do
def page():
    body = yield Fetch("https://example.com")
    return f"body={body}"


@do
def site(fetch_handler):
    inner = yield fetch_handler(page())
    return f"site got {inner}"


print(run(answers(page())))  # body=got https://example.com
print(run(stops(page())))  # stopped at https://example.com
print(run(site(stops)))  # site got stopped at https://example.com
```

Only the program the handler was installed on ends: in the last line `page()`
stops and `site()` goes on with the handler's value. A handler installed around
the whole program ends the whole run, and `run` returns the handler's value.

This is how a handler stops a program on purpose (a fallback value, a deadline),
so no error or warning is raised: the VM cannot tell a deliberate stop from a
forgotten `Resume`. In Hy, `defhandler` makes the stop explicit — a clause ends
its scope only with `(finish value)`, and a clause that reaches neither `resume`
nor `finish` is refused (ADR-DOE-CORE-EFFECTS-003 R15).

### Typed handlers: the effect annotation selects the effects

The type annotation of the effect parameter is the handler's filter. The VM
skips the handler for any effect that is not an instance of the annotated
type — exactly as if the handler had begun with
`if not isinstance(effect, T): yield Pass(effect, k)` — without calling it.

```python excerpt
@handler
def clock(effect: ReadClock | Sleep, k):      # sees ReadClock and Sleep only
    ...

@handler
def audit(effect: EffectBase, k):             # sees every effect (no filter)
    ...
```

Rules (single definition: `doeff_vm/_effect_types.py`):

- the effect parameter is the first parameter that is not already bound
  (`functools.partial` arguments and `self` are skipped; `@do` wrappers are followed)
- no annotation, `Any`, `object`, `EffectBase` / `Effect` → every effect
- a class → `isinstance` against it, so a parent class covers all subclasses
- `A | B` → either; `Annotated[T, ...]` → `T`; `Parent[X]` → `Parent`
- string annotations are evaluated in the handler's module; an annotation that
  cannot be evaluated at runtime (a `TYPE_CHECKING`-only import) warns once and
  falls back to every effect
- a type no effect can be an instance of — `str`, `int`, `dict`, `list[...]`,
  `None`, `Exception` or a class built on one of them — is refused with a
  `TypeError` where the handler is made (`@handler` on the `def`, or `handler(fn)`;
  a raw `WithHandler` when it is installed), because the handler would never be called.
  pyright and mypy refuse `@handler` on it before the program runs (see
  "Handler answers" in [Static Typing](23-static-typing.md)):

```python
from dataclasses import dataclass
from doeff import EffectBase, Resume, do, handler

@dataclass(frozen=True)
class Fetch(EffectBase):
    url: str

try:
    @handler
    def fetch_handler(effect: str, k):
        return (yield Resume(k, f"got {effect}"))
except TypeError as error:
    # the message starts with "handler fetch_handler (defined at <file>:<line>): "
    print(str(error).partition("): ")[2])  # the annotation of effect parameter 'effect' names str, which no effect can be an instance of, so the handler would never be called. Annotate it with the effect class it handles (a subclass of EffectBase, e.g. effect: MyEffect), or with EffectBase to receive every effect.
```

A handler whose annotation matches can still `yield Pass(effect, k)` after
looking at field values; the annotation only states which effects it wants to see.

When an effect falls off the stack and no handler in scope is annotated for it,
the `UnhandledEffect` message names the handlers whose annotation skipped it,
before the `hint:` line. An effect class of your own ends with how to install
a handler for it:

```python
from dataclasses import dataclass
from doeff import EffectBase, Resume, UnhandledEffect, do, handler, run

@dataclass(frozen=True)
class Fetch(EffectBase):
    url: str

@dataclass(frozen=True)
class Store(EffectBase):
    key: str

@handler
def store_handler(effect: Store, k):
    return (yield Resume(k, None))

@do
def prog():
    return (yield Fetch("https://example.com"))

try:
    run(store_handler(prog()))
except UnhandledEffect as error:
    found, in_scope, skipped, hint = str(error).splitlines()
    print(found)  # no handler found for effect: Fetch (Fetch(url='https://example.com'))
    print(in_scope.strip())  # handlers in scope (innermost→outermost): store_handler
    print(skipped.strip())  # skipped by annotation: store_handler (effect: Store) — a handler only sees the effects its effect parameter is annotated with
    print(hint.strip())  # hint: install a handler for Fetch: run(fn(prog)), where fn is a def (effect: Fetch, k) marked with @handler that returns (yield Resume(k, value))
```

A class of the same name from another module (a script imported both as
`__main__` and by its module name) is spelled with its module there, so the two
`Fetch` classes can be told apart.

### Pass the effects a handler does not answer

A handler without an annotation is called for every effect performed under it,
including the ones it does not know. Falling off the end of the handler for such
an effect returns `None`, and by the rule above the program ends with `None`.
Forward the effect with `yield Pass(effect, k)`, or annotate the effect parameter
so the VM does not call the handler for it:

```python
from dataclasses import dataclass

from doeff import EffectBase, Pass, Resume, do, handler, run
from doeff_core_effects import Ask
from doeff_core_effects import reader


@dataclass(frozen=True)
class Fetch(EffectBase):
    url: str


@handler
def swallows(effect, k):
    if isinstance(effect, Fetch):
        return (yield Resume(k, f"got {effect.url}"))
    # Ask("name") falls off the end here: greeting() ends with None


@handler
def passes(effect, k):
    if isinstance(effect, Fetch):
        return (yield Resume(k, f"got {effect.url}"))
    yield Pass(effect, k)


@handler
def typed(effect: Fetch, k):
    return (yield Resume(k, f"got {effect.url}"))


@do
def greeting():
    name = yield Ask("name")
    body = yield Fetch("https://example.com")
    return f"{name}: {body}"


def under(fetch_handler):
    return reader(env={"name": "ada"})(fetch_handler(greeting()))


print(run(under(swallows)))  # None
print(run(under(passes)))  # ada: got https://example.com
print(run(under(typed)))  # ada: got https://example.com
```

The annotation is the cheaper fix: a skipped handler costs less than one that
runs and yields `Pass` (see the cost table above).

### Catching a family of effects by their parent type

Because the filter is `isinstance`, a guard written against a parent type also
catches effects added later — there is no list to keep in sync. Give the
family a common base derived from `EffectBase`:

```python excerpt
class WriteEffect(EffectBase):
    """Marker base: every effect that writes shared state."""

@dataclass(frozen=True)
class WriteRow(WriteEffect):
    key: str
    value: dict

@handler
def read_only_guard(effect: WriteEffect, k):
    raise PermissionError(f"read-only job emitted {type(effect).__name__}")
    yield

# A DeleteRow(WriteEffect) added next year is refused without
# touching the guard.
```

The remaining obligation is at the single place where an effect is defined:
a new write effect must derive from `WriteEffect`. An effect can belong to
several families (`class DeleteRow(WriteEffect, AuditEffect)`).

A base that does not derive from `EffectBase` filters the same way at run
time, but the type checker then refuses `@handler` on `read_only_guard`: it cannot
tell such a class from `str`, which no effect can be (see "Handler answers"
in [Static Typing](23-static-typing.md)).

## WithObserve Contract

`WithObserve(observer, body)` installs scoped observation for yielded values.
The observer is a plain callable that takes one argument: it receives matched yields
and can inspect or log them.

```python
from doeff import WithObserve, do, run
from doeff_core_effects import Tell
from doeff_core_effects import state, writer

def my_observer(effect):
    print(f"observed: {effect}")  # observed: Tell('hello')

@do
def body():
    yield Tell("hello")
    return "done"

prog = WithObserve(my_observer, body())
print(run(state()(writer(prog))))  # done
```

## Composition

Use standalone constructors for building program nodes:

- `Pure(x)` — lift a plain value into a program node
- `Perform(effect)` — lift an effect into a program node

Effect payloads compose after lifting:

```python
from doeff import Pure, Perform, run
from doeff_core_effects import Ask
from doeff_core_effects import reader

print(run(Pure(1)))  # 1

program = Perform(Ask("key"))
print(run(reader(env={"key": "value"})(program)))  # value
```

Handlers compose by nesting: each one is applied to the program the next one returns:

```python
from doeff import do, run
from doeff_core_effects import Get, Put, Tell
from doeff_core_effects import state, writer

@do
def body():
    count = yield Get("count")
    yield Put("count", count + 1)
    yield Tell("counted")
    return (yield Get("count"))

prog = state(initial={"count": 0})(writer(body()))
print(run(prog))  # 1
```

## Core Example

```python
from doeff import do, run
from doeff_core_effects import Ask, Get, Put, Tell
from doeff_core_effects import reader, state, writer

@do
def update_counter():
    counter_key = yield Ask("counter_key")
    current = yield Get(counter_key)
    yield Put(counter_key, current + 1)
    yield Tell(f"counter updated: {current} -> {current + 1}")
    return current + 1

prog = state(initial={"count": 0})(writer(reader(env={"counter_key": "count"})(update_counter())))
result = run(prog)
print(result)  # 1
```

Here handlers are composed individually: `state()(writer(reader()(prog)))`.
`writer` keeps its log in state, so `state()` must be installed outside it. With the order
reversed (`writer(state()(prog))`), the first `Tell` fails with `UnhandledEffect`.
Each handler is a `Program -> Program` installer. `writer` and `slog_handler` are
pre-installed handlers (used directly), while `reader(env=...)` and `state(initial=...)`
are factories that return an installer.

## Summary

- a handler receives each effect with its continuation `k`, stays installed around `k`, and
  decides what the effect means: resume, transfer, throw into, pass, hold or drop `k`
- continuations are one-shot: each `k` resumes at most once
- state, exceptions, concurrency, observation and I/O are all effects and handlers of this kind
- `Program[T]` is `DoExpr[T]` — a virtual type alias with no methods
- effect dispatch is explicit via `Perform(effect)`
- VM evaluates control IR and handlers interpret effect payloads
- `@do` calls emit `Expand` nodes that are evaluated by the runtime
- `run(doexpr)` takes a single argument and returns the raw value
- for async/concurrent execution, use `run(scheduled(prog))`
