# Kleisli Arrows

`@do` produces a normal callable. Calling that callable performs
pure call-time macro expansion and emits an `Expand` DoExpr directly.

[![A narrated video, about two and a half minutes: calling a @do function building a Call node that run evaluates, the call built with no handler asked, arguments passed as given, programs composed by yielding programs, and @handler refusing a handler that breaks its contract](assets/videos/kleisli-arrows.gif)](assets/videos/kleisli-arrows.mp4)

## Table of Contents

- [What `@do` Returns](#what-do-returns)
- [Call-Time Macro Expansion](#call-time-macro-expansion)
- [Why Expand Is a Macro](#why-expand-is-a-macro)
- [KPC Non-Effect Invariant](#kpc-non-effect-invariant)
- [Arguments Are Passed As Given](#arguments-are-passed-as-given)
- [`@do` Handler Authoring Contract](#do-handler-authoring-contract)
- [Composition via Nested `@do`](#composition-via-nested-do)
- [Migration Note](#migration-note)
- [Best Practices](#best-practices)

## What `@do` Returns

```python
from doeff import Expand, do, run

@do
def add_one(x: int):
    return x + 1

call_expr = add_one(41)
# call_expr is an Expand DoExpr (program-shaped DoExpr).
print(isinstance(call_expr, Expand))  # True
print(run(call_expr))  # 42
```

Conceptually:

- `@do` transforms a function into a normal callable
- Calling it returns an `Expand` object
- The VM evaluates that `Expand` directly

## Call-Time Macro Expansion

When you call a `@do`-decorated function, expansion happens synchronously at Python call
time:

1. Keep the positional and keyword arguments exactly as given.
2. Return one `Call` node (a subclass of `Expand`) that holds the function and those
   arguments (`call_expr.args`, `call_expr.kwargs`).

No handlers are consulted during this expansion, and no argument is run or converted.
The transformation is pure. The VM calls the function with the same arguments when it
evaluates the `Call`.

## Why Expand Is a Macro

Calling a `@do`-decorated function is intentionally a macro step to preserve phase separation:
Python call-time expansion builds `DoExpr`, and VM runtime evaluation executes `DoExpr`.
Treating call-construction as an effect introduces a recursion flaw where call dispatch
must invoke effect handling before the call tree is fully constructed.

The macro model avoids that cycle by constructing `Expand(...)` immediately, then letting
standard DoExpr evaluation proceed. Historical removed component matrix details are kept
in `docs/revision-log.md` so this chapter stays focused on the current model.

## KPC Non-Effect Invariant

> KPC is not an effect type.
>
> - It does not extend `PyEffectBase`.
> - It is never dispatched through `Perform(KPC(...))`.
> - There is no KPC handler in the runtime handler stack.

Calling a `@do`-decorated function returns an `Expand` DoExpr directly, and the VM evaluates that
DoExpr normally.

## Arguments Are Passed As Given

Calling a `@do`-decorated function does not run, resolve, or convert its arguments. The
body receives the same objects the caller passed:

| Argument value | What the body receives |
| --- | --- |
| Plain value (`5`, `"text"`, a dict) | The value |
| Effect (`Ask("x")`) | The effect object itself, not its result |
| Program (`Pure(10)`, the result of calling a `@do` function) | The program object itself, not its result |

Parameter annotations do not change this. `x: int` does not make doeff resolve an effect
passed as `x`, and a `Program[T]` or `Effect` annotation is not needed to keep an object
as data. Only static type checkers read the annotations.

To use the result of an effect or a program, `yield` it:

- in the caller, then pass the value (the usual style: the function takes plain values), or
- in the body, when the function takes the effect or program as data on purpose.

### Example

```python
from doeff import Ask, Pure, do, run
from doeff_core_effects.handlers import reader


@do
def echo(x: int):
    return x


@do
def use_values(x, raw_program):
    x_value = yield x
    y = yield raw_program
    return f"{x_value}:{y}"


call_expr = use_values(Ask("x"), Pure(10))
print(call_expr.args)  # (Ask('x'), Pure(10))

with_env = reader(env={"x": 5})
print(run(with_env(echo(Ask("x")))))  # Ask('x')
print(run(with_env(call_expr)))  # 5:10
```

- `call_expr.args` keeps `Ask("x")` and `Pure(10)` as they were passed.
- `echo` returns the `Ask` object itself, although its parameter is annotated `int`.
- `use_values` yields both arguments in its body, so it gets `5` from the reader handler
  and `10` from `Pure(10)`.

### Resolving in the Caller

```python
from doeff import Ask, do, run
from doeff_core_effects.handlers import reader


@do
def add_one(x: int):
    return x + 1


@do
def caller():
    x = yield Ask("x")
    return (yield add_one(x))


print(run(reader(env={"x": 5})(caller())))  # 6
```

`caller` yields `Ask("x")` first and passes the plain value `5`, so `add_one` receives
the `int` its annotation names.

## `@do` Handler Authoring Contract

When a `@do`-decorated function is used as a handler, it must follow the handler
signature contract:

- It must expose an inspectable signature.
- It must accept at least two parameters: `(effect, k)`.
- The first parameter must be annotated as an Effect-family type (`Effect`,
  `Effect[T]`, `EffectBase`, or an `EffectBase` subclass).

If these rules are violated, handler installation fails with `TypeError` at the
Python API boundary.

## Composition via Nested `@do`

Since `@do` functions return `Expand` objects with no `.map()`, `.flat_map()`, `.fmap()`,
`.partial()`, or `>>` operators, composition is done via nested `@do` functions and `yield`:

### Sequential Composition

```python
from doeff import do, run

@do
def fetch_user(user_id: int):
    return {"id": user_id, "name": f"user-{user_id}"}

@do
def fetch_posts(user: dict):
    return [f"post-for-{user['name']}"]

@do
def fetch_user_posts(user_id: int):
    user = yield fetch_user(user_id)
    posts = yield fetch_posts(user)
    return posts

result = run(fetch_user_posts(7))
print(result)  # ['post-for-user-7']
```

### Mapping Over Results

```python
from doeff import do, run

@do
def get_user():
    return {"id": 1, "name": "Alice"}

@do
def get_name():
    user = yield get_user()
    return user["name"]

print(run(get_name()))  # Alice
```

### Partial Application via Closure

```python
from doeff import do, run

@do
def greet(prefix: str, name: str):
    return f"{prefix}, {name}"

@do
def say_hello(name: str):
    return (yield greet("Hello", name))

print(run(say_hello("Bob")))  # Hello, Bob
```

### Varargs at Composition Boundaries

`*args` and `**kwargs` follow the same rule as named parameters
([Arguments Are Passed As Given](#arguments-are-passed-as-given)): every extra positional
or keyword argument reaches the body as given, whatever the annotation. Yield each effect
or program that the body needs the result of.

## Migration Note

Migration guidance for the removed KPC handler is archived in `docs/revision-log.md`.
This chapter documents only the current call-time macro architecture.

## Best Practices

- Yield effects and programs in the caller, and pass plain values to `@do` functions.
- When a function takes an effect or program as data, annotate the parameter with the
  type callers pass (`Effect` for an effect, `Program[...]` for a program) and `yield` it
  in the body where the result is needed.
- Treat calling a `@do`-decorated function as macro expansion that constructs a DoExpr
  tree for VM evaluation.
- Keep reasoning at the DoExpr level: each call produces an `Expand` node.
- Use nested `@do` functions and `yield` for composition instead of method chaining.

## Summary

| Topic | Macro Model |
| --- | --- |
| KPC identity | Call-time macro |
| `__call__` result | `Expand` DoExpr |
| Resolution path | Pure expansion + VM eval |
| Handler dependency | `Expand` executes as regular DoExpr |
| Argument policy | Passed as given (annotations do not resolve arguments) |
| Composition | Nested `@do` + `yield` |

## Next Steps

- **[Patterns](12-patterns.md)** for larger composition patterns
- **[Core Concepts](02-core-concepts.md)** for DoExpr/Effect architecture
- **[API Reference](13-api-reference.md)** for complete runtime API details
