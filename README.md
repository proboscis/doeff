# doeff

**Write your Python logic once. Choose later how it talks to the world.**

[![PyPI](https://img.shields.io/pypi/v/doeff)](https://pypi.org/project/doeff/)
[![Python](https://img.shields.io/pypi/pyversions/doeff)](https://pypi.org/project/doeff/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/proboscis/doeff/blob/main/LICENSE)

doeff brings algebraic effects to Python. Your functions `yield` requests such as
"fetch this URL" or "summarize this text" instead of doing the I/O themselves.
Handlers decide what each request means: real I/O in production, fakes in tests,
recorded responses when you debug. Swap a handler, not your code.

```bash
pip install doeff   # Python 3.11+
```

> **Changing in the next release (0.6.0, not on PyPI yet):** `pip install doeff` will install
> only the core. Add `"doeff[hy]"` for Hy and the handlers written in Hy (files, SQL,
> subprocesses, randomness, caching, metrics), `"doeff[http]"` for the HTTP client handlers, or
> `"doeff[all]"` for everything `pip install doeff` installs in 0.5.0. Installing from this
> repository already works this way.

## A 30-second example

```python
from dataclasses import dataclass
from doeff import EffectBase, Pass, Resume, do, handler, run

@dataclass(frozen=True)
class Fetch(EffectBase):       # a request your code can make
    url: str

@dataclass(frozen=True)
class Summarize(EffectBase):
    text: str

@do
def digest(url):               # your logic: no I/O inside
    page = yield Fetch(url)
    return (yield Summarize(page))

@do
def fake_world(effect, k):     # a handler: answers the requests it knows
    if isinstance(effect, Fetch):
        return (yield Resume(k, "<h1>Hello</h1>"))
    if isinstance(effect, Summarize):
        return (yield Resume(k, f"{len(effect.text)} chars"))
    yield Pass(effect, k)      # anything else goes to the next handler

print(run(handler(fake_world)(digest("https://example.com"))))  # 14 chars
```

To go live, replace `fake_world` with handlers that call your HTTP client and
your model. `digest` does not change, and the fake stays as your test.

## Why doeff?

| You want to… | Plain Python | doeff |
| --- | --- | --- |
| Test code that does I/O | `mock.patch("app.requests.get")` for every import path | Install a fake handler. Nothing is patched |
| Add logging, retries or a cost cap | One wrapper per client | One handler that sees every effect |
| Run work in parallel | Rewrite the path with `async` or threads | `Spawn`, `Gather` and `Race` in the same program |

- **Batteries included.** Production *and* test handlers ship for files, HTTP (record / replay),
  SQL (in-memory SQLite for tests), subprocesses, randomness, caching and metrics.
- **Plain Python.** Programs are generator functions; no new syntax.
  Optional [Hy](https://hylang.org/) macros live in [doeff-hy](https://github.com/proboscis/doeff/blob/main/packages/doeff-hy/README.md).
- **Rust VM.** Effects are dispatched by a VM written in Rust, with one-shot continuations.

## One handler, every effect

A handler can watch every request without touching `digest`. Add this below the example above:

```python continues
seen = []

@do
def trace(effect, k):          # sees every request first, then passes it on
    seen.append(effect)
    return (yield Pass(effect, k))

print(run(handler(fake_world)(handler(trace)(digest("https://example.com")))))  # 14 chars
print(seen)  # [Fetch(url='https://example.com'), Summarize(text='<h1>Hello</h1>')]
```

## Next steps

- **Tutorial**: [Getting started](https://github.com/proboscis/doeff/blob/main/docs/01-getting-started.md)
- **Why effects?**: [Effects vs. dependency injection](https://github.com/proboscis/doeff/blob/main/docs/20-why-effects-over-di.md)
- **Built-in effects and handlers**: [doeff-core-effects](https://github.com/proboscis/doeff/blob/main/packages/doeff-core-effects/README.md)
- **Writing programs in Hy**: [doeff-hy](https://github.com/proboscis/doeff/blob/main/packages/doeff-hy/README.md)
- **Command line**: [`doeff run`](https://github.com/proboscis/doeff/blob/main/docs/14-cli-auto-discovery.md)

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
(require doeff-hy.handle [defhandler])
(require doeff-hy.macros [do! <-])
(import doeff [Ask run])

(defhandler ask-env
  (Ask [key]
    (resume (get {"greeting" "hello"} key))))

(setv greet
  (do!
    (<- greeting (Ask "greeting"))
    greeting))

(print (run (ask-env greet)))  ; hello
```

`pip install doeff` installs Hy too (from 0.6.0, Hy comes with the extra `hy`:
`pip install "doeff[hy]"`). Save the example as `greet.hy` and run it with `hy greet.hy`. Macros such as `do!`, `<-` and `defhandler` are brought in with `require`, never
with `import`.

### Using a Hy module from Python

Python reads a `.hy` file only after `import hy` has run, so put `import hy` before the import of a
Hy module:

```hy file=greet.hy
(require doeff-hy.macros [do! <-])
(import doeff [Ask])

(setv greet
  (do!
    (<- greeting (Ask "greeting"))
    greeting))
```

```python
import hy  # first: registers Hy's import hook, so Python can find greet.hy
from doeff import run
from doeff_core_effects.handlers import reader
from greet import greet

print(run(reader(env={"greeting": "hello"})(greet)))  # hello
```

### Common Hy mistakes and the line that fixes each

| You see | Why | Fix |
| --- | --- | --- |
| `NameError: name 'hyx_doXexclamation_markX' is not defined` (or `name 'defk' is not defined`) | A macro was used without a `require`, so Hy compiled it as a call to an undefined function. doeff prints a `HyMacroUseWarning` with the line to add just before the traceback. | Add `(require doeff-hy.macros [do! <-])` |
| `ImportError: cannot import name 'defhandler' from 'doeff_hy.handle'` | A macro was brought in with `import`. | Write `(require doeff-hy.handle [defhandler])` instead of `(import doeff-hy.handle [defhandler])` |
| `ModuleNotFoundError: No module named 'greet'` with a `greet.hy` next to the script | Python does not read `.hy` files until `import hy` has run. | Add `import hy` above `from greet import greet` |

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

| Handler | Installer | Effects handled |
| --- | --- | --- |
| Reader | `reader(env={...})` | `Ask` |
| Lazy Ask | `lazy_ask(env={...})` | `Ask`, `Local` (with caching) |
| State | `state(initial={...})` | `Get`, `Put` |
| Writer | `writer` | `Tell` / `WriterTellEffect` |
| Try | `try_handler` | `Try` |
| Slog | `slog_handler` | `Slog` |
| Local | `local_handler` | `Local` |
| Listen | `listen_handler` | `Listen` |
| Await | `await_handler()` | `Await` |
| Scheduler | `scheduled(prog)` (from `doeff_core_effects.scheduler`) | `Spawn`, `Wait`, `Gather`, `Race`, etc. |

`writer` keeps its log in state: install `state()` outside it, and read the log with
`yield writer_log()` (from `doeff_core_effects.handlers`).

## Scheduler and Concurrency

```python
from doeff import do, run
from doeff_core_effects import Tell
from doeff_core_effects.handlers import state, writer
from doeff_core_effects.scheduler import scheduled, Spawn, Gather

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

# writer keeps its log in state, so install state() outside writer.
result = run(scheduled(state()(writer(main()))))
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

## Related packages

The add-on packages (LLM clients, agents, workflows and more) live in
[`packages/`](https://github.com/proboscis/doeff/tree/main/packages).

`doeff-agents` 0.1.1 on PyPI was built for doeff 0.4 and does not work with doeff 0.5 — importing
its handlers fails with `ImportError: cannot import name 'WithHandler' from 'doeff'`. To keep using
that release, pin the doeff 0.4 line: `pip install doeff-agents "doeff<0.5"`. The `doeff-agents` in
this repository ([`packages/doeff-agents`](https://github.com/proboscis/doeff/blob/main/packages/doeff-agents/README.md),
with its CLI) is not published to PyPI.

## Repository map

The paths for users come first. The rest are for people who work on doeff itself.

| Path | For | What is inside |
| --- | --- | --- |
| [`doeff/`][map-doeff], [`packages/`][map-packages] | users | the library and its add-on packages |
| [`docs/index.md`][map-docs-index] | users | start here: guides and API notes |
| [`examples/`][map-examples] | users | runnable examples |
| [`ide-plugins/`][map-ide-plugins] | users | editor plugins for PyCharm, VS Code and Neovim |
| [`docs/adr/`][map-adr] | contributors | design decisions written as executable tests |
| [`tests/`][map-tests], [`scripts/`][map-scripts], [`tools/`][map-tools], [`benchmarks/`][map-benchmarks] | contributors | the test suite, development tools and benchmarks |
| [`docs/design/`][map-design], [`docs/design-checks/`][map-design-checks] | maintainers | development records; not user documentation |
| [`docs/history/`][map-history] | maintainers | older planning notes |
| [`publications/`][map-publications], [`images/`][map-images] | maintainers | drafts of articles about doeff and their images |

[`docs/README.md`][map-docs-readme] tells the user guides in `docs/` apart from the development
records.

[map-doeff]: https://github.com/proboscis/doeff/tree/main/doeff
[map-packages]: https://github.com/proboscis/doeff/tree/main/packages
[map-docs-index]: https://github.com/proboscis/doeff/blob/main/docs/index.md
[map-examples]: https://github.com/proboscis/doeff/tree/main/examples
[map-ide-plugins]: https://github.com/proboscis/doeff/tree/main/ide-plugins
[map-adr]: https://github.com/proboscis/doeff/tree/main/docs/adr
[map-tests]: https://github.com/proboscis/doeff/tree/main/tests
[map-scripts]: https://github.com/proboscis/doeff/tree/main/scripts
[map-tools]: https://github.com/proboscis/doeff/tree/main/tools
[map-benchmarks]: https://github.com/proboscis/doeff/tree/main/benchmarks
[map-design]: https://github.com/proboscis/doeff/tree/main/docs/design
[map-design-checks]: https://github.com/proboscis/doeff/tree/main/docs/design-checks
[map-history]: https://github.com/proboscis/doeff/tree/main/docs/history
[map-publications]: https://github.com/proboscis/doeff/tree/main/publications
[map-images]: https://github.com/proboscis/doeff/tree/main/images
[map-docs-readme]: https://github.com/proboscis/doeff/blob/main/docs/README.md

## Contributing

Setting up the development environment, running the tests and running the benchmarks are
described in [CONTRIBUTING.md](https://github.com/proboscis/doeff/blob/main/CONTRIBUTING.md).

## License

MIT License. See [LICENSE](https://github.com/proboscis/doeff/blob/main/LICENSE).
