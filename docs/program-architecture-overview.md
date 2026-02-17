# Program Architecture Overview

This document describes the current doeff execution architecture, aligned to:

- `specs/core/SPEC-TYPES-001-program-effect-separation.md` (Rev 12)
- `specs/vm/SPEC-008-rust-vm.md` (Rev 14)
- `specs/core/SPEC-CORE-001-effect-boundaries.md`

## Program Model

- `Program[T]` is the user-facing execution type.
- Control is represented as `DoCtrl[T]` nodes (the VM instruction set).
- Effect payloads are user-space values (`EffectValue[T]`) and are dispatched via `Perform(effect)`.
- At API boundaries, `run` and `async_run` accept either:
  - a control expression (`DoExpr[T]`)
  - a raw effect payload (`EffectValue[T]`), normalized to `Perform(effect)`

Source-level ergonomics stay simple:

```python
value = yield Ask("key")
```

Lowered control form:

```python
value = yield Perform(Ask("key"))
```

## Generator as Lazy AST

`@do` generators are evaluated as a lazy AST stream:

1. `next(gen)` yields a control expression.
2. The VM evaluates that expression.
3. The VM sends the result back with `gen.send(value)`.
4. Repeat until completion.

Free-monad interpretation:

```text
yield expr  ==  Bind(expr, lambda result: rest_of_program)
```

The generator holds continuation state; the VM is the evaluator.

## DoCtrl Instruction Set

Core control nodes include:

- `Pure(value)`
- `Call(f, args, kwargs, metadata)`
- `Eval(expr, handlers)`
- `Map(source, f)`
- `FlatMap(source, f)`
- `Perform(effect)`
- `WithHandler(handler, body)`
- `Resume`, `Transfer`, `Delegate`, `ResumeContinuation`
- `PythonAsyncSyntaxEscape` (async integration escape node)

`Pure`, `Map`, `FlatMap`, and `Call` are DoCtrl nodes evaluated by the VM, not wrapper types.

## KPC Is Call-Time Macro Expansion

`@do` creates a `KleisliProgram`. Calling it performs call-time macro expansion into `Call(...)`:

1. Compute argument unwrap strategy from function annotations.
2. Wrap unwrapable effect arguments as `Perform(arg)`.
3. Wrap plain values as `Pure(arg)`.
4. Emit `Call(Pure(func), args, kwargs, metadata)`.

KPC is not dispatched through handlers; there is no handler-phase KPC resolution path.

## VM Step Semantics

The step loop is modeled as:

```text
step : state -> Free[ExternalOp, step_outcome]

step_outcome = Done(value) | Failed(error) | Continue(state) | Escape(payload, resume)
```

- `Done` and `Failed` terminate execution.
- `Continue` advances the VM state.
- `Escape` is the external bind case used for async syntax integration.

## Yield Classification and Dispatch

Classification is binary:

- `DoCtrlBase` -> evaluate as control IR
- `EffectBase` -> dispatch through handlers (via `Perform` boundary)

Dispatch is tag-based in hot paths:

- `DoCtrlBase`/`EffectBase` instances carry immutable discriminant tags.
- The VM reads tags directly for GIL-free fast classification and DoCtrl dispatch.

Effect payloads are opaque VM objects (`Py<PyAny>` at the Rust boundary). The VM does not use an effect enum or inspect effect-specific fields during classification.

## Handler Protocol

Handlers interpret effect payloads with this contract:

- input: `(effect, k)`
- output: `DoExpr`

The handler receives an opaque effect object and performs any needed downcast itself. If a host handler returns a raw effect value, runtime normalizes it to `Perform(effect)` before continuing.

## Escape Boundary: Async Only

`PythonAsyncSyntaxEscape` is the only VM-level escape for Python async syntax integration.

- Handler-internal suspension:
  - stays inside handler logic (for example, scheduler bookkeeping)
  - VM continues with normal `Continue` transitions
- VM-level escape:
  - leaves doeff boundary for external async runtime integration
  - resumed by async runner after awaiting payload

## Runners

There are two entrypoints:

- `run(program, ...)` for synchronous execution
- `async_run(program, ...)` for async-loop integration

Both drive the same core step semantics. `async_run` handles `PythonAsyncSyntaxEscape`; `run` rejects async escape nodes.

## End-to-End Flow

1. Build a control expression (`DoExpr`) from `@do` code and macro-expanded calls.
2. Step the VM over yielded nodes.
3. Classify each yielded object (`DoCtrlBase` or `EffectBase`) using tag dispatch.
4. Evaluate DoCtrl directly; dispatch effects to handlers.
5. Continue until `Done`, `Failed`, or async escape/resume completion.
