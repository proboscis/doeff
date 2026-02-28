# doeff - Algebraic Effects for Python

doeff is an algebraic effects runtime for Python with one-shot continuations and a Rust stepping engine.
Programs are written with generator-based `@do` notation and interpreted through explicit handler stacks.

## Documentation

- [Documentation index](docs/index.md)
- [Getting started](docs/01-getting-started.md)
- [API reference](docs/13-api-reference.md)
- [Program architecture overview](docs/program-architecture-overview.md)
- [CLI auto-discovery](docs/14-cli-auto-discovery.md)

## Installation

`doeff` is published on PyPI (`0.2.1`).

```bash
pip install doeff
```

Optional packages:
`pip install doeff-pinjected` and `pip install doeff-secret doeff-google-secret-manager`

## Quick Start

```python
from doeff import Program, Get, Put, Tell, default_handlers, do, run

@do
def counter_program() -> Program[int]:
    yield Put("counter", 0)
    yield Tell("Starting computation")
    count = yield Get("counter")
    yield Put("counter", count + 1)
    return count + 1

result = run(counter_program(), handlers=default_handlers())
print(result.value)      # 1
print(result.log)        # ['Starting computation']
print(result.raw_store)  # {'counter': 1}
```

## Runtime API

Only these public entrypoints are supported:
| Entrypoint | Signature | Use case |
| --- | --- | --- |
| `run` | `run(program, handlers=(), env=None, store=None, trace=False)` | Synchronous execution |
| `async_run` | `async_run(program, handlers=(), env=None, store=None, trace=False)` | Async execution |

Notes:

- `run()` and `async_run()` return a `RunResult` object (`.value`, `.error`, `.log`, `.raw_store`, `.trace`).
- Default `handlers` is an empty tuple (`()`), so effectful programs should pass handler presets or custom handlers.

## Default Handler Presets

Use the sync preset with `run()` and the async preset with `async_run()`.

- `default_handlers()` includes `sync_await_handler`
- `default_async_handlers()` includes `async_await_handler`

```python
from doeff import default_async_handlers, default_handlers

sync_handlers = default_handlers()
async_handlers = default_async_handlers()

assert sync_handlers != async_handlers
print(sync_handlers[-1].__name__)   # sync_await_handler
print(async_handlers[-1].__name__)  # async_await_handler
```

## Effect Surface (Public)

All effect names mentioned here are current public exports.
Core categories include:

- Reader/State/Writer: `Ask`, `Local`, `Get`, `Put`, `Modify`, `Tell`, `StructuredLog`, `slog`, `Listen`
- Result/cache: `Safe`, `CacheGet`, `CachePut`, `CacheExists`, `CacheDelete`
- Scheduler: `Await`, `Spawn`, `Wait`, `Gather`, `Race`, `Future`, `Task`
- External bridging: `CreateExternalPromise`, `ExternalPromise`
- Tracing/graph: `ProgramTrace`, `Step`, `Annotate`, `Snapshot`, `CaptureGraph`

Writer convenience helpers:
- `StructuredLog(**entries)` and `slog(**entries)` are shorthand for `Tell({**entries})`.

## Handler Architecture (`WithHandler`)

`WithHandler` lets you attach typed handlers to a sub-program and stack multiple handlers.
The innermost `WithHandler` layer sees the effect first.

```python
from doeff import Ask, AskEffect, Delegate, Effect, Resume, WithHandler, default_handlers, do, run

@do
def base_handler(effect: Effect, k: object):
    if isinstance(effect, AskEffect):
        return (yield Resume(k, "base"))
    yield Delegate()


@do
def override_handler(effect: Effect, k: object):
    if isinstance(effect, AskEffect):
        return (yield Resume(k, "override"))
    yield Delegate()

@do
def read_mode():
    return (yield Ask("mode"))

stacked = WithHandler(base_handler, WithHandler(override_handler, read_mode()))
result = run(stacked, handlers=default_handlers())
print(result.value)  # override
```

## Scheduler and Concurrency

`Spawn`, `Wait`, `Gather`, and `Race` provide cooperative concurrency.
Use `async_run(..., handlers=default_async_handlers())` for event-loop-aware behavior.

```python
import asyncio

from doeff import Await, Gather, Race, Spawn, Wait, async_run, default_async_handlers, do

@do
def worker(label: str, delay: float):
    return (yield Await(asyncio.sleep(delay, result=label)))

@do
def scheduler_program():
    t1 = yield Spawn(worker("fast", 0.01))
    t2 = yield Spawn(worker("slow", 0.02))
    first = yield Race(t1, t2)
    values = yield Gather(t1, t2)
    waited = yield Wait(t2)
    return (first.value, values, waited)

async def main():
    result = await async_run(scheduler_program(), handlers=default_async_handlers())
    print(result.value)

asyncio.run(main())
```

## External Promise Bridge

`CreateExternalPromise` and `ExternalPromise` let external threads or async callbacks complete work and wake suspended doeff tasks.

```python
import threading
import time

from doeff import CreateExternalPromise, Wait, default_handlers, do, run

@do
def wait_for_external_value():
    promise = yield CreateExternalPromise()

    def worker():
        time.sleep(0.01)
        promise.complete("from-thread")

    threading.Thread(target=worker, daemon=True).start()
    return (yield Wait(promise.future))

result = run(wait_for_external_value(), handlers=default_handlers())
print(result.value)  # from-thread
```

## Rust VM Stepping Engine

The Rust VM is the stepping engine for `Program` execution.
It drives effect dispatch, continuation resume/delegate flow, and async escape points (`PythonAsyncSyntaxEscape`) while Python handlers define semantics.
Use `trace=True` on `run()`/`async_run()` to capture effect-level trace data in `RunResult.trace`.

## CLI Auto-Discovery

The CLI supports automatic interpreter/environment discovery for `doeff run`.
Keep README usage minimal and refer to the full guide for markers, hierarchy rules, and troubleshooting:
`docs/14-cli-auto-discovery.md`.
For RunResult report output (`--report` / `--report-verbose`), see `docs/program-architecture-overview.md`.
## Pinjected Integration

`doeff-pinjected` is an optional bridge package for pinjected integration.
For setup and examples, see `packages/doeff-pinjected/README.md` and `docs/10-pinjected-integration.md`.

## Development

`make lint` runs Ruff, Pyright, Semgrep, and doeff-linter.

```bash
uv sync --group dev
uv run pytest
uv run pyright
make lint
make format
```
## License

MIT License. See `LICENSE`.
