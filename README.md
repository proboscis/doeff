# doeff - Algebraic Effects for Python

doeff is an algebraic effects runtime for Python with one-shot continuations and a Rust VM.
Programs are written with generator-based `@do` notation and executed through explicit handler stacks.

## Installation

```bash
pip install doeff
```

## Quick Start

```python
from doeff import do, run
from doeff_core_effects import Get, Put, Tell
from doeff_core_effects.handlers import reader, state, writer
from doeff_core_effects.scheduler import scheduled

@do
def counter_program():
    yield Put("counter", 0)
    yield Tell("Starting computation")
    count = yield Get("counter")
    yield Put("counter", count + 1)
    return count + 1

# Compose handlers explicitly by calling each handler installer.
prog = counter_program()
prog = writer()(prog)
prog = state()(prog)
prog = reader(env={"greeting": "hello"})(prog)
result = run(scheduled(prog))
print(result)  # 1
```

## Runtime API

| Entrypoint | Signature | Use case |
| --- | --- | --- |
| `run` | `run(doexpr)` | Execute a DoExpr program to completion |

`run()` takes a single DoExpr (program node) and returns the result value directly.
Handlers are composed explicitly by calling a Program -> Program handler installer.
Use `scheduled(prog)` to wrap with the scheduler for concurrency effects.

## Handler Composition

Handlers are installed by direct call. Stack multiple handlers by wrapping the program:

```python
from doeff import do, run
from doeff_core_effects import Ask
from doeff_core_effects.handlers import reader

@do
def prog():
    return (yield Ask("greeting"))

result = run(reader(env={"greeting": "hello"})(prog()))
print(result)  # hello
```

Reusable custom handlers should expose the same Program -> Program shape. In Hy, `defhandler`
creates that shape directly:

```hy
(import doeff [Ask])

(defhandler ask-env
  (Ask [key]
    (resume (get {"greeting" "hello"} key))))

(ask-env
  (do!
    (<- greeting (Ask "greeting"))
    greeting))
```

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
from doeff import do, run
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

result = run(scheduled(writer()(main())))
print(result)  # ['a', 'b']
```

## CLI

```bash
# Run a program with auto-discovered interpreter
doeff run --program myapp.module.program

# Inline Python code
doeff run -c 'return 42'

# Inline Hy code (preferred — compose handlers inline)
doeff run --hy '(import myapp [p]) (import doeff-core-effects [lazy-ask]) ((lazy-ask :env myapp.env_dict) p)'

# JSON output
doeff run --program myapp.program --format json
```

> **Note**: Legacy flags `--interpreter`, `--env`, `--set`, `--apply`, and
> `--transform` still work but emit deprecation warnings. Use `--hy` for
> inline handler composition instead.

The CLI supports automatic interpreter/environment discovery via `doeff-indexer`.
See `docs/14-cli-auto-discovery.md` for marker syntax and hierarchy rules.

## Performance Benchmarks

Rust VM dispatch micro-benchmarks live in `packages/doeff-vm-core/benches/` and
use Criterion. Run them from the VM core crate with the VM feature enabled:

```bash
cd packages/doeff-vm-core
cargo bench --features python_bridge
```

If PyO3 selects the wrong Python installation on macOS, pin it to the uv
environment:

```bash
cd packages/doeff-vm-core
PYO3_PYTHON="$(cd ../.. && uv run python -c 'import sys; print(sys.executable)')" \
  cargo bench --features python_bridge
```

Python end-to-end benchmarks live in `benchmarks/benchmark_runner.py`. A normal
run writes JSON to `benchmarks/results/<yyyymmdd>-<hostname>.json`:

```bash
uv run python benchmarks/benchmark_runner.py --runs 20
```

Compare a fresh run against a committed baseline with:

```bash
uv run python benchmarks/benchmark_runner.py \
  --compare benchmarks/results/<baseline>.json
```

CI uses smoke mode only. It runs one iteration with the smallest N values and
checks that the benchmark entrypoints still execute; it does not gate on
performance:

```bash
make bench-smoke
```

## Development

```bash
make sync            # install deps + rebuild Rust VM (maturin develop --release)
uv run pytest        # run full test suite
```

> **Warning**: `uv sync` alone does NOT rebuild the Rust VM extension. Always
> use `make sync` after editing `.rs` files under `packages/doeff-vm/`.

## License

MIT License. See `LICENSE`.
