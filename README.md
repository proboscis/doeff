# doeff - Algebraic Effects for Python

doeff is an algebraic effects system for Python with one-shot continuations.
Programs are written with generator-based do-notation (`@do`), and executed by a Rust VM stepping engine that handles effect dispatch, continuation resumption, and trace collection.

## Documentation

- [Docs index](docs/index.md)
- [Getting started](docs/01-getting-started.md)
- [Core concepts](docs/02-core-concepts.md)
- [API reference](docs/13-api-reference.md)
- [Runtime architecture overview](docs/program-architecture-overview.md)

## Installation

```bash
pip install doeff
```

Optional bridges and provider packages live under `packages/` (for example `doeff-pinjected`, `doeff-openai`, and `doeff-gemini`).

## Quick Start

```python
from doeff import Get, Program, Put, Tell, default_handlers, do, run

@do
def counter_program() -> Program[int]:
    yield Put("counter", 0)
    yield Tell("Starting computation")
    count = yield Get("counter")
    yield Put("counter", count + 1)
    return count + 1

result = run(counter_program(), handlers=default_handlers())
print(result.value)  # 1
```

`run()` returns a `RunResult`. Use `result.value` for success, or `result.is_err()` / `result.error` for failures.

## Runtime Entrypoints

| Entrypoint | Signature | Notes |
|---|---|---|
| `run` | `run(program, handlers=(), env=None, store=None, trace=False)` | Synchronous stepping |
| `async_run` | `async_run(program, handlers=(), env=None, store=None, trace=False)` | Async stepping for `await`-heavy flows |

Notes:
- Handlers are not auto-installed. Pass `handlers=default_handlers()` for built-in Reader/State/Writer/Result/Scheduler/Await support.
- `trace=True` enables VM trace capture in `RunResult.trace`.

## Handler Presets

```python
from doeff import default_handlers

handlers = default_handlers()
```

`default_handlers()` is the public preset used for both `run()` and `async_run()`.
There is no separate public `default_async_handlers()` in the current API.

## Handler Architecture (`WithHandler`)

`WithHandler` wraps a sub-program with a typed effect handler. Handlers can be stacked to compose behavior.

```python
from doeff import Ask, AskEffect, Delegate, Resume, WithHandler, default_handlers, do, run

@do
def read_region():
    return (yield Ask("region"))

def override_region(effect, k):
    if isinstance(effect, AskEffect) and effect.key == "region":
        return (yield Resume(k, "us-west-2"))
    return (yield Delegate())

def outer_passthrough(effect, k):
    return (yield Delegate())

wrapped = WithHandler(
    outer_passthrough,
    WithHandler(override_region, read_region()),
)

result = run(wrapped, handlers=default_handlers(), env={"region": "local"})
print(result.value)  # us-west-2
```

## Scheduler and Concurrency

Use scheduler effects for cooperative concurrency:
- `Spawn(program)` to start a background task
- `Wait(task_or_future)` to await one task/future
- `Gather(*tasks_or_programs)` to wait for all
- `Race(*waitables)` to return the first completion

```python
from doeff import Gather, Race, Spawn, Wait, default_handlers, do, run

@do
def child(value: int):
    return value * 10

@do
def scheduler_demo():
    t1 = yield Spawn(child(1))
    t2 = yield Spawn(child(2))

    gathered = yield Gather(t1, t2)
    first = yield Race(t1, t2)
    waited = yield Wait(t1)
    return tuple(gathered), first.value, waited

result = run(scheduler_demo(), handlers=default_handlers())
print(result.value)  # ((10, 20), 10, 10)
```

## Bridging External Async Code (`ExternalPromise`)

Use `CreateExternalPromise()` when non-doeff code (threads, callbacks, external loops) needs to complete a value back into doeff.

```python
import threading
import time

from doeff import CreateExternalPromise, Wait, default_handlers, do, run

@do
def external_bridge_demo():
    promise = yield CreateExternalPromise()

    def worker() -> None:
        time.sleep(0.01)
        promise.complete("done-from-thread")

    threading.Thread(target=worker, daemon=True).start()
    return (yield Wait(promise.future))

result = run(external_bridge_demo(), handlers=default_handlers())
print(result.value)  # done-from-thread
```

If you already have Python coroutines, `Await` works with `async_run`:
`yield Await(coro)` inside your `@do` program and execute with `await async_run(..., handlers=default_handlers())`.

## Effect Surface (Public)

| Family | Effects |
|---|---|
| Reader | `Ask`, `Local` |
| State | `Get`, `Put`, `Modify`, `AtomicGet`, `AtomicUpdate` |
| Writer | `Log`, `Tell`, `Listen`, `StructuredLog` |
| Result | `Safe` |
| Async and scheduling | `Await`, `Spawn`, `Wait`, `Gather`, `Race`, `Future`, `Promise`, `Task` |
| External bridging | `CreateExternalPromise`, `ExternalPromise` |
| Cache | `CacheGet`, `CachePut`, `CacheExists`, `CacheDelete` |
| Graph and trace | `Step`, `Annotate`, `Snapshot`, `CaptureGraph`, `ProgramTrace` |

See [API reference](docs/13-api-reference.md) for the complete list and signatures.

## CLI Auto-Discovery

The `doeff run` CLI can auto-discover a default interpreter and environment values via markers.
Use this when you want `--program`-only invocation in larger projects.

- Full guide: [docs/14-cli-auto-discovery.md](docs/14-cli-auto-discovery.md)

## Pinjected Integration

Pinjected support is provided by `doeff-pinjected`.
Use it when you want to adapt a doeff `Program` into pinjected's dependency-resolution flow.

- Guide: [docs/10-pinjected-integration.md](docs/10-pinjected-integration.md)

## Development

Install dev dependencies:

```bash
uv sync --group dev
```

Run lint suite (Ruff + Pyright + Semgrep + doeff-linter):

```bash
make lint
```

Run tests:

```bash
uv run pytest
```

## License

MIT. See [LICENSE](LICENSE).
