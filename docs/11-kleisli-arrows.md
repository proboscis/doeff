# Kleisli Arrows

`@do` produces a normal callable. Calling that callable performs
pure call-time macro expansion and emits an `Expand` DoExpr directly.

## Table of Contents

- [What `@do` Returns](#what-do-returns)
- [Call-Time Macro Expansion](#call-time-macro-expansion)
- [Why Expand Is a Macro](#why-expand-is-a-macro)
- [KPC Non-Effect Invariant](#kpc-non-effect-invariant)
- [Annotation-Aware Argument Classification](#annotation-aware-argument-classification)
- [`@do` Handler Authoring Contract](#do-handler-authoring-contract)
- [Composition via Nested `@do`](#composition-via-nested-do)
- [Migration Note](#migration-note)
- [Best Practices](#best-practices)

## What `@do` Returns

```python
from doeff import do

@do
def add_one(x: int):
    return x + 1

call_expr = add_one(41)
# call_expr is an Expand DoExpr (program-shaped DoExpr).
```

Conceptually:

- `@do` transforms a function into a normal callable
- Calling it returns an `Expand` object
- The VM evaluates that `Expand` directly

## Call-Time Macro Expansion

When you call a `@do`-decorated function, expansion happens synchronously at Python call
time:

1. Load cached auto-unwrap strategy (computed once at decoration time).
2. Classify each argument using annotation-aware `should_unwrap`.
3. Convert each argument to a DoExpr node.
4. Return an `Expand(...)` DoExpr.

No handlers are consulted during this expansion. The transformation is pure.

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

## Annotation-Aware Argument Classification

`should_unwrap` is derived from parameter annotations.

### `should_unwrap=True` (auto-resolve at runtime)

- Plain annotations like `int`, `str`, `dict`, user classes
- Unannotated parameters

### `should_unwrap=False` (pass object through as data)

- `Program`, `Program[T]`
- `DoCtrl`, `DoCtrl[T]`
- `Effect`, `Effect[T]`, `EffectBase` subclasses
- `DoExpr`, `DoExpr[T]`
- Wrapped forms like `Optional[Program[T]]`, `Program[T] | None`,
  `Annotated[Program[T], ...]`

### Value-Type to DoExpr Expansion

| Argument value | `should_unwrap` | Expansion |
| --- | --- | --- |
| `EffectBase` instance | `True` | `Perform(arg)` |
| `DoCtrlBase` instance | `True` | `arg` (already DoCtrl) |
| Plain value | `True` | `Pure(arg)` |
| `EffectBase` instance | `False` | `Pure(arg)` |
| `DoCtrlBase` instance | `False` | `Pure(arg)` |
| Plain value | `False` | `Pure(arg)` |

### Example

```python
from doeff import Ask, Pure, do
from doeff import Program

@do
def use_values(x: int, raw_program: Program):
    y = yield raw_program
    return f"{x}:{y}"

call_expr = use_values(Ask("x"), Pure(10))
# x: int -> should_unwrap=True -> Ask("x") becomes Perform(Ask("x"))
# raw_program: Program -> should_unwrap=False -> Pure(10) becomes Pure(...)
```

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
```

### Mapping Over Results

```python
@do
def get_user():
    return {"id": 1, "name": "Alice"}

@do
def get_name():
    user = yield get_user()
    return user["name"]
```

### Partial Application via Closure

```python
@do
def greet(prefix: str, name: str):
    return f"{prefix}, {name}"

@do
def say_hello(name: str):
    return (yield greet("Hello", name))
```

### Varargs Auto-Unwrap at Composition Boundaries

For `*args` and `**kwargs`, unwrap policy is computed once from the varargs
parameter annotation and then applied to each value crossing that call boundary:

- `*args`: one `var_positional` policy for all extra positional arguments
- `**kwargs`: one `var_keyword` policy for all unmatched keyword arguments

Boundary rules to keep in mind:

- Unannotated varargs default to `should_unwrap=True`, so effect/program values in
  varargs are resolved at that call boundary.
- Annotating varargs as `Program[...]` or `Effect[...]` sets `should_unwrap=False`,
  so those values are passed through as data.

## Migration Note

Migration guidance for the removed KPC handler is archived in `docs/revision-log.md`.
This chapter documents only the current call-time macro architecture.

## Best Practices

- Prefer explicit annotations for parameters that should not auto-unwrap.
- Use `Program[...]`/`Effect[...]` annotations when you need raw objects in the
  function body.
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
| Argument policy | Annotation-aware `should_unwrap` |
| Composition | Nested `@do` + `yield` |

## Next Steps

- **[Patterns](12-patterns.md)** for larger composition patterns
- **[Core Concepts](02-core-concepts.md)** for DoExpr/Effect architecture
- **[API Reference](13-api-reference.md)** for complete runtime API details
