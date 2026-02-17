# Kleisli Arrows

`@do` produces a `KleisliProgram[P, T]`. Calling that `KleisliProgram` performs
pure call-time macro expansion and emits a `Call` DoCtrl directly.

## Table of Contents

- [What `@do` Returns](#what-do-returns)
- [Call-Time Macro Expansion](#call-time-macro-expansion)
- [Why Call Is a Macro](#why-call-is-a-macro)
- [KPC Non-Effect Invariant](#kpc-non-effect-invariant)
- [Annotation-Aware Argument Classification](#annotation-aware-argument-classification)
- [`@do` Handler Authoring Contract](#do-handler-authoring-contract)
- [Call Metadata](#call-metadata)
- [Composability of `Call` DoCtrl](#composability-of-call-doctrl)
- [Kleisli-Level Composition and Partial Application](#kleisli-level-composition-and-partial-application)
- [Migration Note](#migration-note)
- [Best Practices](#best-practices)

## What `@do` Returns

```python
from doeff import Program, do

@do
def add_one(x: int):
    return x + 1

call_expr = add_one(41)
# call_expr is a Call DoCtrl (Program-shaped DoExpr).
```

Conceptually:

- `@do` transforms a function into `KleisliProgram[P, T]`
- `KleisliProgram.__call__` returns `Call[...]`
- the VM evaluates that `Call` directly

## Call-Time Macro Expansion

When you call a `KleisliProgram`, expansion happens synchronously at Python call
time:

1. Load cached auto-unwrap strategy (computed once at decoration time).
2. Classify each argument using annotation-aware `should_unwrap`.
3. Convert each argument to a DoExpr node.
4. Populate call metadata.
5. Return `Call(Pure(kernel), args, kwargs, metadata)`.

No handlers are consulted during this expansion. The transformation is pure.

### Expansion Pseudocode

```python
def __call__(self, *args, **kwargs):
    strategy = self._auto_unwrap_strategy
    do_expr_args = [classify(arg, strategy.should_unwrap_positional(i)) for i, arg in enumerate(args)]
    do_expr_kwargs = {
        name: classify(value, strategy.should_unwrap_keyword(name))
        for name, value in kwargs.items()
    }

    metadata = {
        "function_name": self.__name__,
        "source_file": self.original_func.__code__.co_filename,
        "source_line": self.original_func.__code__.co_firstlineno,
        "program_call": None,
    }
    return Call(Pure(self.execution_kernel), do_expr_args, do_expr_kwargs, metadata)
```

## Why Call Is a Macro

`KleisliProgram.__call__` is intentionally a macro step to preserve phase separation:
Python call-time expansion builds `DoExpr`, and VM runtime evaluation executes `DoExpr`.
Treating call-construction as an effect introduces a recursion flaw where call dispatch
must invoke effect handling before the call tree is fully constructed.

The macro model avoids that cycle by constructing `Call(...)` immediately, then letting
standard DoExpr evaluation proceed. Historical removed component matrix details are kept
in `docs/revision-log.md` so this chapter stays focused on the current model.

## KPC Non-Effect Invariant

> KPC is not an effect type.
>
> - It does not extend `PyEffectBase`.
> - It is never dispatched through `Perform(KPC(...))`.
> - There is no KPC handler in `default_handlers()` or runtime handler stacks.

Calling a `KleisliProgram` returns a `Call` DoCtrl directly, and the VM evaluates that
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
from doeff import Ask, Program, do

@do
def use_values(x: int, raw_program: Program[int]):
    y = yield raw_program
    return f"{x}:{y}"

call_expr = use_values(Ask("x"), Program.pure(10))
# x: int -> should_unwrap=True -> Ask("x") becomes Perform(Ask("x"))
# raw_program: Program[int] -> should_unwrap=False -> Program.pure(10) becomes Pure(...)
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

## Call Metadata

`KleisliProgram.__call__` attaches metadata at call time. The fields are used for
tracing and stack introspection:

- `function_name`
- `source_file`
- `source_line`
- `program_call` (optional call context object)

Example access:

```python
@do
def compute(x: int):
    return x * 2

call_expr = compute(21)
meta = call_expr.meta
print(meta["function_name"])  # compute
```

## Composability of `Call` DoCtrl

A `Call` is a DoExpr node, so it composes like any other DoExpr.

Semantic shape (SPEC-KPC-001):

```python
result = fetch_user(42)                          # Call DoCtrl
mapped = fetch_user(42).map(lambda u: u.name)   # Map(Call(...), f)
chained = fetch_user(42).flat_map(enrich)       # FlatMap(Call(...), f)
value = yield fetch_user(42)                    # yield sends Call to VM
```

The important invariant is the expression shape:

- `fetch_user(42)` returns `Call(...)`
- mapping/chaining over it yields `Map(Call(...), ...)` or
  `FlatMap(Call(...), ...)`
- `run()` evaluates the resulting DoExpr tree directly

## Kleisli-Level Composition and Partial Application

Kleisli-level combinators still work and remain useful:

### `and_then_k` / `>>`

```python
from doeff import default_handlers, do, run

@do
def fetch_user(user_id: int):
    return {"id": user_id, "name": f"user-{user_id}"}

@do
def fetch_posts(user: dict):
    return [f"post-for-{user['name']}"]

fetch_user_posts = fetch_user >> fetch_posts
result = run(fetch_user_posts(7), default_handlers())
```

### `fmap`

```python
@do
def get_user():
    return {"id": 1, "name": "Alice"}

get_name = get_user.fmap(lambda user: user["name"])
```

### `partial`

```python
@do
def greet(prefix: str, name: str):
    return f"{prefix}, {name}"

say_hello = greet.partial("Hello")
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
- `partial(...)` does not bypass classification. Pre-bound arguments and call-time
  arguments are merged, then classified by the base Kleisli signature.

This is why varargs-heavy composition can feel surprising: every Kleisli call
boundary applies its own annotation-driven unwrap policy.

## Migration Note

Migration guidance for the removed KPC handler is archived in `docs/revision-log.md`.
This chapter documents only the current call-time macro architecture.

## Best Practices

- Prefer explicit annotations for parameters that should not auto-unwrap.
- Use `Program[...]`/`Effect[...]` annotations when you need raw objects in the
  function body.
- Treat `KleisliProgram.__call__` as macro expansion that constructs a DoExpr
  tree for VM evaluation.
- Keep reasoning at the DoExpr level: each call produces a `Call` node.

## Summary

| Topic | Macro Model |
| --- | --- |
| KPC identity | Call-time macro |
| `__call__` result | `Call` DoCtrl |
| Resolution path | Pure expansion + VM eval |
| Handler dependency | `Call` executes as regular DoExpr |
| Argument policy | Annotation-aware `should_unwrap` |
| Metadata | Populated at call time |

## Next Steps

- **[Patterns](12-patterns.md)** for larger composition patterns
- **[Core Concepts](02-core-concepts.md)** for DoExpr/Effect architecture
- **[API Reference](13-api-reference.md)** for complete runtime API details
