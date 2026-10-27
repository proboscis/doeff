# doeff-core-effects

Reference implementations of effects and handlers for [doeff](https://github.com/proboscis/doeff).
None of them is built into doeff: a handler of your own can take the place of any of them.

**This README is for** Python developers who write doeff programs. After reading it
you can install the package, run a program with the reference handlers, choose the handler that gives
an effect its meaning (a test handler in place of a production one is one such choice),
and find the effect family you need.

## What it does

doeff brings algebraic effects to Python. A program *performs effects*: small data
objects such as `Get("count")` or `ReadText("notes.txt")`. A *handler* installed around
the program receives each effect together with `k`, the rest of the program from that
`yield` on, and decides what the effect means: it resumes `k` with an answer, throws an
error into it, holds it while other tasks run, or drops it
([What doeff Is](https://github.com/proboscis/doeff/blob/main/docs/positioning/what-doeff-is.md)).
This package provides:

- the everyday effects: reader (`Ask`), state (`Get` / `Put`), writer (`Tell`),
  error handling (`Try`), absence and failure (`Absent` / `Raise`);
- the scheduler for concurrency (`Spawn`, `Gather`, `Race`, promises, semaphores);
- effects for I/O — HTTP, WebSocket, SQL, files, OS processes, random bytes, caching,
  metrics — each with a **production handler** (real I/O) and a **test handler**
  (in-memory or scripted), so the same program runs against both.

`pip install doeff` installs this package. The everyday effects and handlers can be
imported from the top-level `doeff_core_effects` module; a few of them (`Ask`, `Get`,
`Put`, `Tell`, `Try`, `Spawn`, `Gather`) are also re-exported from `doeff`.

## Installation

```bash
pip install doeff                              # installs doeff-core-effects: the modules written in Python
pip install "doeff[hy]"                        # + Hy (doeff-hy): the modules written in Hy
pip install "doeff[http]"                      # + Hy and httpx: the HTTP client handler
pip install "doeff-core-effects[http-server]"  # + Hy and aiohttp: HTTP server and WebSocket client handlers
pip install "doeff-core-effects[postgres]"     # + Hy and psycopg: PostgreSQL handler
```

Requires Python 3.11 or newer. Many modules are written in Hy; the Extra column of the
table under [Effect families](#effect-families) names the extra each family needs.
Without it, importing such a module fails with a `ModuleNotFoundError` that ends with
the install line (`pip install "doeff[hy]"`). The SQLite and ClickHouse handlers need
nothing beyond the extra `hy`: they use the standard library.

## Minimal example

Each handler installer wraps a program and returns a new program. Call `run` on the
outermost one:

```python
from doeff import do, run
from doeff_core_effects import Ask, Get, Put, ReadWriterLog, Tell
from doeff_core_effects.handlers import reader, state, writer
from doeff_core_effects.scheduler import scheduled


@do
def greet():
    name = yield Ask("name")
    count = yield Get("count")
    yield Put("count", count + 1)
    yield Tell(f"greeted {name}")
    return f"hello, {name} (#{count + 1})"


@do
def main():
    first = yield greet()
    second = yield greet()
    log = yield ReadWriterLog()
    return first, second, log


prog = writer(main())                       # Tell     (keeps its log in state)
prog = state(initial={"count": 0})(prog)    # Get, Put
prog = reader(env={"name": "doeff"})(prog)  # Ask
print(run(scheduled(prog)))  # ('hello, doeff (#1)', 'hello, doeff (#2)', ['greeted doeff', 'greeted doeff'])
```

How the handlers wrap the program in this example (an effect travels outward until a
handler accepts it):

```text
scheduled ( reader ( state ( writer ( main() ) ) ) )
   outermost ◄─────────────────────────── innermost
```

## Swapping a production handler for a test handler

The program below reads a file. With `os_file_handler` it reads the real disk; with
`memory_file_handler` it reads an in-memory file system. The program does not change.

The file handlers are written in Hy, so this example needs the extra `hy`.

```python
import tempfile
from pathlib import Path

from doeff import do, run
from doeff_core_effects.file_effects import FileFailed, MemoryFile, MemoryFiles, ReadText
from doeff_core_effects.handlers import state
from doeff_core_effects.memory_file import memory_file_handler
from doeff_core_effects.os_file import os_file_handler
from doeff_core_effects.scheduler import scheduled


@do
def word_count(path):
    text = yield ReadText(path)
    if isinstance(text, FileFailed):  # failures are answers, not exceptions
        return 0
    return len(text.split())


# Production: the real file system.
with tempfile.TemporaryDirectory() as tmp:
    path = str(Path(tmp) / "notes.txt")
    Path(path).write_text("effects are just data")
    print(run(scheduled(os_file_handler(word_count(path)))))  # 4

# Tests: an in-memory file system. It keeps its files in state, so install state() outside it.
files = MemoryFiles(files=(MemoryFile(path="/notes.txt", content=b"hello effects"),))
print(run(scheduled(state()(memory_file_handler(files)(word_count("/notes.txt"))))))  # 2
```

## Concurrency

`scheduled(...)` runs the scheduler. `Spawn` starts a task and `Gather` waits for
several:

```python
from doeff import do, run
from doeff_core_effects import Tell
from doeff_core_effects.handlers import state, writer
from doeff_core_effects.scheduler import Gather, Spawn, scheduled


@do
def work(label):
    yield Tell(f"working on {label}")
    return label.upper()


@do
def main():
    tasks = []
    for label in ["a", "b", "c"]:
        tasks.append((yield Spawn(work(label))))
    return (yield Gather(*tasks))


print(run(scheduled(state()(writer(main())))))  # ['A', 'B', 'C']
```

## Absence and failure

A program writes only its success path. `Absent(why)` says "there is no value" and
`Raise(reason)` says "this failed". The caller decides how to fold them into values:

| Fold | `Absent` becomes | `Raise` becomes | Success becomes |
| --- | --- | --- | --- |
| `maybe(program)` | `Nothing` | passes through | `Some(value)` |
| `result(program)` | passes through | `Err(reason)` | `Ok(value)` |

```python
from doeff import do, run
from doeff_core_effects import Absent
from doeff_core_effects.outcomes import maybe, result
from doeff_core_effects.scheduler import scheduled

USERS = {"ada": 36}


@do
def age_of(name):
    if name not in USERS:
        yield Absent(f"no user {name!r}")
    return USERS[name]


print(run(scheduled(maybe(age_of("ada")))))   # Some(36)
print(run(scheduled(maybe(age_of("bob")))))   # Nothing
print(run(scheduled(result(age_of("ada")))))  # Ok(36)
```

Nest them to get both: `result(maybe(program))` gives `Ok(Some(v))`, `Ok(Nothing)` or
`Err(e)`. `on_raise` and `absent_as` handle specific cases.

## Writing your own effect and handler

An effect is a frozen dataclass that inherits `EffectBase`. A handler receives the
effect and the continuation `k`, and either resumes `k` with an answer or passes the
effect outward. `@handler` above its def makes it an installer that you apply to a
program:

```python
from dataclasses import dataclass

from doeff import EffectBase, Pass, Resume, do, handler, run
from doeff_core_effects.scheduler import scheduled


@dataclass(frozen=True)
class Now(EffectBase):
    """Ask for the current time."""


@handler
def fixed_clock(effect, k):
    if isinstance(effect, Now):
        return (yield Resume(k, "2026-01-01T00:00:00Z"))
    yield Pass(effect, k)


@do
def stamp(message):
    now = yield Now()
    return f"[{now}] {message}"


print(run(scheduled(fixed_clock(stamp("started")))))  # [2026-01-01T00:00:00Z] started
```

## Effect families

Effects are imported from the module in the second column; handlers from the modules
named in the handler columns (all under `doeff_core_effects`).

| Family | Effects (module: examples) | Production handler | Test handler | Extra |
| --- | --- | --- | --- | --- |
| Reader, state, writer | `doeff_core_effects`: `Ask`, `Local`, `Get`, `Put`, `Tell`, `Listen`, `slog` | `handlers`: `reader(env)`, `lazy_ask(env)`, `env_var_ask`, `state(initial)`, `writer`, `local_handler`, `listen_handler`, `slog_handler` | same | — |
| Errors | `doeff_core_effects`: `Try` | `handlers.try_handler` | same | — |
| Absence and failure | `doeff_core_effects`: `Absent`, `Raise` | `outcomes`: `maybe`, `result`, `on_raise`, `absent_as` | same | — |
| asyncio bridge | `doeff_core_effects`: `Await` | `handlers.await_handler()` | — | — |
| Concurrency | `scheduler`: `Spawn`, `Wait`, `Gather`, `Race`, `Cancel`, `CreatePromise`, `CreateExternalPromise`, `CreateSemaphore` | `scheduler.scheduled(program)` | same | — |
| Channels | `channel_effects`: `CreateChannel`, `PutChannel`, `TakeChannel` | `scheduler_channel.scheduler_channel_handler` | same | `hy` |
| HTTP client | `http_effects`: `HttpRequest`, `HttpResponse`, `HttpError` | `http_handlers.http_production_handler` | `http_handlers.http_fixture_handler(path, mode="record" or "replay")` | `http` |
| HTTP server | `http_server_effects`: `HttpListen`, `HttpNextRequest`, `HttpRespond` | `aiohttp_http_server.aiohttp_http_server` | `scripted_http_server.scripted_http_server(script)` | `http-server` |
| WebSocket client | `ws_client_effects`: `WsConnect`, `WsSend`, `WsReceive`, `WsDisconnect` | `aiohttp_ws_client.aiohttp_ws_client` | `scripted_ws_client.scripted_ws_client(script)` | `http-server` |
| SQL | `sql_effects`: `SqlQuery`, `SqlInsertRows`, `SqlTransaction`, `SqlEnsureTables` | `postgres_sql.postgres_sql_handler`, `clickhouse_http_sql.clickhouse_http_sql_handler`, `sqlite_file_sql.sqlite_file_sql_handler` | `sqlite_sql.sqlite_sql_handler(databases)` (in-memory SQLite) | `hy`; `postgres` for PostgreSQL |
| Files | `file_effects`: `ReadText`, `WriteText`, `ListDirectory`, `StatPath` | `os_file.os_file_handler`, `rooted_file.rooted_file_handler(root)` | `memory_file.memory_file_handler(files)` | `hy` |
| OS processes | `process_effects`: `RunProcess`, `StartProcess`, `PollProcess`, `StopProcess` | `os_process.subprocess_handler` | `scripted_process.scripted_process_handler(script)` | `hy` |
| Compute offload | `compute_effects`: `Compute` | `thread_pool_compute.thread_pool_compute_handler(pool)` | `inline_compute.inline_compute_handler` | `hy` |
| Random bytes | `random_effects`: `RandomBytes` | `os_random.os_random_handler` | `seeded_random.seeded_random_handler(seed)` | `hy` |
| Cache and memo | `cache_effects`: `cache_get`, `cache_put`; `memo_effects`: `MemoGet`, `MemoPut`; decorator `cache.cache` | `cache_handlers.sqlite_cache_handler(db_path)`, `memo_handlers.sqlite_memo_handler` | `cache_handlers.in_memory_cache_handler()`, `memo_handlers.in_memory_memo_handler()` | `hy` |
| Metrics | `meter_effects`: `CountMetric`, `ObserveSeconds`, `SetGauge`, `ReadMeter` | `process_meter.process_meter_handler`; `meter_prometheus.render_prometheus` | `memory_meter.memory_meter_handler` | `hy` |
| Latest value | `latest_effects`: `PublishLatest`, `ReadLatest`, `AwaitLatest` | `process_latest.process_latest_handler(name)` | `memory_latest.memory_latest_handler` | `hy` |
| Stop signals | `stop_signal_effects`: `StopRequested`, `AwaitStop` | `stop_signal_handlers.os_signal_stop_handler` | `stop_signal_handlers.scripted_stop_handler` | `hy` |

Notes on installing handlers:

- `scheduled(...)` goes outermost.
- `writer` keeps its log in state, so install `state()` outside it.
- Handlers that keep data between effects (the in-memory files, the in-memory SQLite
  databases, and others written with doeff-hy's `session` values) keep it in state,
  so install `state()` outside them as well.

The package also contains lower-level modules (OS-specific process-exit watchers,
heap freezing, scheduler step tallies, pre-forked "warm" processes) that the handlers
above use. `doeff_lifeline` is a small standard-library-only helper that stops child
processes when their parent process exits; `os_process` starts it for you.

Many modules are written in [Hy](https://hylang.org/) with
[doeff-hy](https://github.com/proboscis/doeff/tree/main/packages/doeff-hy). You import
them from Python like any other module once the extra `hy` is installed; `.pyi`
stubs ship with the package.

## How it fits with the other doeff packages

doeff is published as five core packages. `pip install doeff` installs the first three; the extra
`hy` (`pip install "doeff[hy]"`) adds doeff-hy and doeff-hy-pytest.

| Package | Role |
| --- | --- |
| [doeff](https://github.com/proboscis/doeff) | The Python API: `@do`, handler installers, `run`, the `doeff` CLI |
| [doeff-vm](https://github.com/proboscis/doeff/tree/main/packages/doeff-vm) | The Rust VM that executes programs |
| **doeff-core-effects** | This package: reference implementations of effects and their handlers |
| [doeff-hy](https://github.com/proboscis/doeff/tree/main/packages/doeff-hy) | Macros for writing doeff code in Hy, a Lisp that compiles to Python |
| [doeff-hy-pytest](https://github.com/proboscis/doeff/tree/main/packages/doeff-hy-pytest) | pytest plugin for tests written with doeff-hy |

Arrows point from a package to a package it depends on (the dotted one only with the extra `hy`):

```text
doeff ──► doeff-vm
  │
  └─────► doeff-core-effects ┄┄► doeff-hy ──► doeff-hy-pytest
```

doeff-hy also depends on doeff and doeff-core-effects, so the five packages are
released together with matching versions.

## License

MIT. See [LICENSE](https://github.com/proboscis/doeff/blob/main/LICENSE).

### Licenses of dependencies

Two dependencies are under the **GNU Lesser General Public License v3 (LGPL-3.0)**;
the others are under permissive licenses (MIT, BSD-3-Clause, Apache-2.0, PSF-2.0,
MPL-2.0).

| Dependency | License | When it is installed | Used for |
| --- | --- | --- | --- |
| [frozendict](https://pypi.org/project/frozendict/) | LGPL-3.0 | always | the cache keys of the `@cache` decorator (`doeff_core_effects.cache`) |
| [psycopg](https://pypi.org/project/psycopg/) | LGPL-3.0 | only with the `postgres` extra | the PostgreSQL handler |

doeff imports both as unmodified, separately installed libraries and does not copy or
bundle their code. If your project has rules about LGPL components (for example, when
you ship everything inside one frozen executable), check them against these two
packages.
