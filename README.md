# doeff - Algebraic Effects for Python

doeff is an algebraic effects runtime for Python with one-shot continuations and a Rust VM.
Programs are written with generator-based `@do` notation and executed through explicit handler stacks.

## Installation

```bash
pip install doeff
```

## Quick Start

```python
from doeff import do, run, WithHandler
from doeff_core_effects import Ask, Get, Put, Tell
from doeff_core_effects.handlers import reader, state, writer
from doeff_core_effects.scheduler import scheduled

@do
def counter_program():
    yield Put("counter", 0)
    yield Tell("Starting computation")
    count = yield Get("counter")
    yield Put("counter", count + 1)
    return count + 1

# Compose handlers explicitly with WithHandler
prog = counter_program()
prog = WithHandler(writer(), prog)
prog = WithHandler(state(), prog)
prog = WithHandler(reader(env={"greeting": "hello"}), prog)
result = run(scheduled(prog))
print(result)  # 1
```

## Runtime API

| Entrypoint | Signature | Use case |
| --- | --- | --- |
| `run` | `run(doexpr)` | Execute a DoExpr program to completion |

`run()` takes a single DoExpr (program node) and returns the result value directly.
Handlers are composed explicitly using `WithHandler(handler, body)`.
Use `scheduled(prog)` to wrap with the scheduler for concurrency effects.

## Handler Composition

Handlers are installed with `WithHandler(handler, body)`. Stack multiple handlers by nesting:

```python
from doeff import do, run, WithHandler, Resume, Pass
from doeff_core_effects import Ask

@do
def my_handler(effect, k):
    if isinstance(effect, Ask):
        return (yield Resume(k, "hello"))
    yield Pass(effect, k)

@do
def prog():
    return (yield Ask("greeting"))

result = run(WithHandler(my_handler, prog()))
print(result)  # hello
```

Handlers are `@do` functions that receive `(effect, k)`. They can:
- `yield Resume(k, value)` — resume the continuation with a value
- `yield Pass(effect, k)` — forward the effect to outer handlers
- `yield Transfer(k, value)` — tail-resume (handler done after this)

## Effect Surface

Core effects (from `doeff_core_effects`):

- **Reader**: `Ask(key)`, `Local(env, program)`
- **State**: `Get(key)`, `Put(key, value)`
- **Writer**: `Tell(message)`, `slog(msg, **kwargs)`
- **Error**: `Try(program)` — returns `Ok(value)` or `Err(error)`
- **Observe**: `Listen(program, types=...)` — collect effects during execution

Scheduler effects (from `doeff_core_effects.scheduler`):

- **Concurrency**: `Spawn(program)`, `Wait(task)`, `Gather(*tasks)`, `Race(*tasks)`
- **Promises**: `CreatePromise()`, `CompletePromise(p, v)`, `FailPromise(p, e)`
- **External**: `CreateExternalPromise()` — bridge external threads
- **Semaphores**: `CreateSemaphore(n)`, `AcquireSemaphore(s)`, `ReleaseSemaphore(s)`

## Handlers

Built-in handlers (from `doeff_core_effects.handlers`):

| Handler | Factory | Effects handled |
| --- | --- | --- |
| Reader | `reader(env={...})` | `Ask` |
| Lazy Ask | `lazy_ask(env={...})` | `Ask`, `Local` (with caching) |
| State | `state(initial={...})` | `Get`, `Put` |
| Writer | `writer()` | `Tell` / `WriterTellEffect` |
| Try | `try_handler` | `Try` |
| Slog | `slog_handler()` | `Slog` |
| Local | `local_handler` | `Local` |
| Listen | `listen_handler` | `Listen` |
| Await | `await_handler()` | `Await` |
| Scheduler | `scheduled(prog)` | `Spawn`, `Wait`, `Gather`, `Race`, etc. |

## Scheduler and Concurrency

```python
from doeff import do, run, WithHandler
from doeff_core_effects import Tell
from doeff_core_effects.handlers import writer
from doeff_core_effects.scheduler import scheduled, Spawn, Wait, Gather

@do
def worker(label):
    yield Tell(f"working: {label}")
    return label

@do
def main():
    t1 = yield Spawn(worker("a"))
    t2 = yield Spawn(worker("b"))
    results = yield Gather(t1, t2)
    return results

result = run(scheduled(WithHandler(writer(), main())))
print(result)  # ['a', 'b']
```

## CLI

```bash
# Run a program with auto-discovered interpreter
doeff run --program myapp.module.program

# With explicit interpreter
doeff run --program myapp.program --interpreter myapp.interpreter

# With environment
doeff run --program myapp.program --env myapp.default_env

# Inline code
doeff run -c 'return 42'

# Apply transform (T -> Program[U])
doeff run --program myapp.program --apply myapp.transforms.wrap

# JSON output
doeff run --program myapp.program --format json
```

The CLI supports automatic interpreter/environment discovery via `doeff-indexer`.
See `docs/14-cli-auto-discovery.md` for marker syntax and hierarchy rules.

## Development

```bash
uv sync --reinstall  # rebuild Rust VM
uv run pytest
```

## License

MIT License. See `LICENSE`.
