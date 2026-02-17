# 17. Effect Boundaries

This chapter explains where doeff handles work internally and where execution intentionally leaves
doeff for Python async integration.

## Table of Contents

- [Boundary Model](#boundary-model)
- [Effects That Stay Inside doeff](#effects-that-stay-inside-doeff)
- [Escaped Effects](#escaped-effects)
- [Why `PythonAsyncSyntaxEscape` Exists](#why-pythonasyncsyntaxescape-exists)
- [Scheduler Suspension vs VM Escape](#scheduler-suspension-vs-vm-escape)
- [Free-Monad View of `step`](#free-monad-view-of-step)
- [Runner Pairing (`run` vs `async_run`)](#runner-pairing-run-vs-async_run)
- [Custom Handler Rules](#custom-handler-rules)

## Boundary Model

At runtime, doeff has two execution zones:

- **Inside doeff**: effects are interpreted by the handler stack.
- **Outside doeff**: escaped operations are interpreted by the runner.

The boundary matters because scheduler effects and Python async effects have different execution
responsibilities.

## Effects That Stay Inside doeff

Most effects are fully handled by doeff handlers and never leave the VM stepping loop:

- `Ask`, `Local`
- `Get`, `Put`, `Modify`
- `Tell`, `Listen`
- `Safe`
- scheduler effects such as `Spawn`, `Wait`, `Gather`, `Race`

Example:

```python
from doeff import Ask, Gather, Spawn, Wait, do

@do
def pipeline():
    worker = yield Ask("worker")
    t1 = yield Spawn(worker("a"))
    t2 = yield Spawn(worker("b"))
    values = yield Gather(t1, t2)
    first = yield Wait(t1)
    return values, first
```

All of those effects are resolved by handlers inside doeff.

## Escaped Effects

Escaped effects are represented by `PythonAsyncSyntaxEscape`, a VM step outcome consumed by the
async runner path. This is not a user-facing effect type; it is a runtime handshake for external
async operations.

Common source:

- `Await(awaitable)` handled by `async_await_handler`

## Why `PythonAsyncSyntaxEscape` Exists

`PythonAsyncSyntaxEscape` exists for one reason: Python `await` is syntax-level and must run under
an event loop. A synchronous stepping loop cannot abstract that away.

Key points:

- `await` cannot be evaluated inside a normal sync function without handing control to an event
  loop.
- `async_run(...)` is the opt-in API that allows this handoff.
- The escape type is not a general-purpose "leave doeff" mechanism; it is specifically for Python
  async integration.

## Scheduler Suspension vs VM Escape

These are different mechanisms:

- **Scheduler suspension (internal)**:
  - `Spawn`/`Wait`/`Gather`/`Race` are coordinated by scheduler handlers.
  - Tasks can be queued/suspended/resumed internally.
  - VM continues through `Continue` states.
- **VM escape (external)**:
  - Async handlers emit `PythonAsyncSyntaxEscape`.
  - The runner interprets the escaped action in async context.
  - Control returns to VM after the external async step finishes.

## Free-Monad View of `step`

Conceptually, stepping can be described as:

```text
step : state -> Free[ExternalOp, StepOutcome]

StepOutcome = Done | Failed | Continue
Free[F, A] = Pure(A) | Bind(F, continuation)
```

Interpretation:

- `Pure(Done | Failed | Continue)` means no external interpretation is needed for that step.
- `Bind(op, cont)` means the runner must interpret an external operation and then resume.

In doeff, `PythonAsyncSyntaxEscape` is the concrete external-op handshake for Python async.

## Runner Pairing (`run` vs `async_run`)

Use runner + handler presets intentionally:

| Entry point | Preset | Await behavior |
| --- | --- | --- |
| `run(...)` | `default_handlers()` | `sync_await_handler` bridges awaitables via background loop thread |
| `await async_run(...)` | `default_async_handlers()` | `async_await_handler` emits `PythonAsyncSyntaxEscape` for async driver path |

Example:

```python
import asyncio
from doeff import Await, async_run, default_async_handlers, default_handlers, do, run

@do
def compute():
    value = yield Await(asyncio.sleep(0.01, result=21))
    return value * 2

sync_result = run(compute(), handlers=default_handlers())
assert sync_result.value == 42

async def main():
    async_result = await async_run(compute(), handlers=default_async_handlers())
    assert async_result.value == 42

asyncio.run(main())
```

## Custom Handler Rules

When adding handlers, keep the boundary strict:

1. Handle domain effects inside handlers whenever possible.
2. Use `PythonAsyncSyntaxEscape` only for operations that must execute in Python async context.
3. Keep scheduler state transitions internal to scheduler handlers.
4. Pair sync and async runners with their matching handler presets.

