# doeff

**Algebraic effects for Python: your code yields effects, and handlers decide what they mean.**

[![PyPI](https://img.shields.io/pypi/v/doeff)](https://pypi.org/project/doeff/)
[![Python](https://img.shields.io/pypi/pyversions/doeff)](https://pypi.org/project/doeff/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/proboscis/doeff/blob/main/LICENSE)

doeff brings algebraic effects and their handlers to Python. A function `yield`s an *effect*, a
plain value that names an operation such as `Spend(3)`, `Get("count")` or `Spawn(task)`, and pauses
there. The nearest handler for that effect receives it together with `k`, the *continuation*: the
rest of the function from that `yield` on. What the handler does with `k` decides what the effect
means: it resumes `k` with an answer, throws an error into it, passes the effect on, holds it while
other tasks run, or drops it.

```bash
pip install doeff   # Python 3.11+
```

**Documentation:** [start here](https://github.com/proboscis/doeff/blob/main/docs/index.md), with
install, a first program, the FAQ, why effects, examples and the API, in that order.

## A 30-second example

`research` spends part of a budget on each topic. It yields `Spend` and leaves the budget to a
handler, which either resumes `k` or drops it:

```python
from dataclasses import dataclass
from doeff import EffectBase, Resume, do, handler, run

@dataclass(frozen=True)
class Spend(EffectBase):        # an effect: "the next step costs this much"
    cost: int

@do
def research(topics):           # yields effects; knows nothing about budgets
    notes = []
    for topic in topics:
        yield Spend(3)
        notes.append(f"notes on {topic}")
    return notes

def budget(limit):              # one meaning of Spend: stop when the budget runs out
    left = limit

    @handler
    def on_spend(effect: Spend, k):     # k: the rest of research, paused at the yield
        nonlocal left
        if effect.cost > left:
            return "over budget"         # drop k: research stops at this Spend
        left -= effect.cost
        return (yield Resume(k, None))   # resume k: research goes on

    return on_spend

print(run(budget(10)(research(["tides", "moons"]))))                      # ['notes on tides', 'notes on moons']
print(run(budget(10)(research(["tides", "moons", "comets", "stars"]))))  # over budget
```

The fourth `Spend` finds 1 left, so the handler returns without resuming `k`: the fourth topic is
never researched, and `"over budget"` is the result. `research` has no check of its own. Another
handler gives `Spend` another meaning without changing `research`: one that only counts the cost,
or one that throws an error into `research` with `ResumeThrow(k, error)`.

## What a handler does with `k`

`budget` above resumes `k` or drops it. These are all the things a handler can do with `k`:

| The handler | Written as | The function |
| --- | --- | --- |
| answers and runs the rest | `Resume(k, value)` | goes on with `value`; when it finishes, the handler gets its result and returns that or another value |
| answers and is done | `Transfer(k, value)` | goes on with `value`; the handler does not get control back |
| throws into it | `ResumeThrow(k, error)` | sees `error` raised at the `yield`, where its own `try` can catch it |
| passes the effect on | `Pass(effect, k)` | gets the answer of the next handler out |
| holds it while other tasks run | a scheduler handler, such as the reference `scheduled(...)` | waits; `Spawn`, `Gather`, `Race`, promises and semaphores are effects of this handler |
| drops it | `return value`, without resuming `k` | stops at the `yield`; `value` is the result of everything under the handler |

State, early exit, retries, concurrency and I/O are all handlers built from these, and a function
stays the same when you change the handlers around it.

**Continuations are one-shot**, as in OCaml 5: a handler resumes each `k` at most once. Effects that
resume one continuation several times, such as backtracking search or nondeterministic choice,
cannot be written as doeff handlers.
[What doeff Is](https://github.com/proboscis/doeff/blob/main/docs/positioning/what-doeff-is.md)
defines the mechanism, what it covers and where it stops.

In the recording, one program runs under four handlers. The first answers its effects with
made-up values, the second traces every effect, the third stops the program midway by dropping
`k`, and the fourth fetches the page over real HTTP. Swapping real I/O for fakes is one of these
uses:

![A terminal session: pip install doeff, then the same digest program runs under four handlers: one answers with made-up values, one traces every effect, one stops it midway by dropping k, and one fetches the page over real HTTP](https://raw.githubusercontent.com/proboscis/doeff/main/images/readme-demo.gif)

Run `python -m doeff demo` after `pip install doeff` to watch that program run under the same four
handlers, each giving its effects another meaning. The extras of `doeff` add Hy and more reference
handlers:

```bash
pip install doeff          # Python 3.11+: the core (doeff, doeff-vm, doeff-core-effects)
pip install "doeff[hy]"    # + Hy and the handlers written in Hy (files, SQL, processes, cache, …)
pip install "doeff[http]"  # + the HTTP client handlers (Hy and httpx)
pip install "doeff[all]"   # both: what `pip install doeff` installed in 0.5.0
```

## Why doeff?

| You want to… | Plain Python | doeff |
| --- | --- | --- |
| Decide outside a function whether its next step runs: go on, fail there, wait or stop | A callee can only return or raise; a generator needs a driver loop written for it | A handler gets the rest of the function as `k`, and resumes it, throws into it, holds it or drops it |
| Run work in parallel | Rewrite the path with `async` or threads | `Spawn`, `Gather` and `Race` in the same program; the scheduler that runs them is a handler |
| Add logging, retries or a cost cap | One wrapper per client | One handler that sees every effect |
| Test code that does I/O | `mock.patch("app.requests.get")` for every import path | Install a fake handler. Nothing is patched |

- **Reference handlers.** No handler is built into doeff. The packages ship reference
  implementations, production *and* test, for files, HTTP (record / replay), SQL (in-memory
  SQLite for tests), subprocesses, randomness, caching and metrics; any of them can be replaced
  by a handler of your own.
- **Plain Python.** Programs are generator functions; no new syntax.
  Optional [Hy](https://hylang.org/) macros live in [doeff-hy](https://github.com/proboscis/doeff/blob/main/packages/doeff-hy/README.md).
- **Rust VM.** Effects are dispatched by a VM written in Rust, with one-shot continuations.

Why not dependency injection or plain `async`? [Why effects?](https://github.com/proboscis/doeff/blob/main/docs/20-why-effects-over-di.md)
compares effects with both, example by example, and says when DI or plain Python is the simpler choice.

## Swap a handler: fakes in tests, real I/O in production

Effects that reach the outside world are effects like any other, so a handler decides whether
they reach it. `digest` yields two effects and does nothing else:

```python
from dataclasses import dataclass
from doeff import EffectBase, Pass, Resume, do, handler, run

@dataclass(frozen=True)
class Fetch(EffectBase):       # an effect: "get the page at url"
    url: str

@dataclass(frozen=True)
class Summarize(EffectBase):   # an effect: "summarize this text"
    text: str

@do
def digest(url):               # the program: it only yields effects
    page = yield Fetch(url)
    return (yield Summarize(page))

@handler
def canned(effect, k):         # a handler: resumes k with made-up values
    if isinstance(effect, Fetch):
        return (yield Resume(k, "<h1>Hello</h1>"))
    if isinstance(effect, Summarize):
        return (yield Resume(k, f"{len(effect.text)} chars"))
    yield Pass(effect, k)      # anything else goes to the next handler

print(run(canned(digest("https://example.com"))))  # 14 chars
```

To go live, replace `canned` with handlers that call your HTTP client and
your model. `digest` does not change, and `canned` stays as the fake in your test.
[`examples/llm_agent.py`](https://github.com/proboscis/doeff/blob/main/examples/llm_agent.py) does
this for an LLM agent, with pytest tests that need no API key.
[`examples/github_repo`](https://github.com/proboscis/doeff/tree/main/examples/github_repo) does it
for a REST API: it calls GitHub through the HTTP handler of `doeff[http]`, and its tests answer
with a fake handler, without the network.

## One handler, every effect

A handler can watch every effect without touching `digest`. Add this below the example above:

```python continues
seen: list[object] = []

@handler
def trace(effect, k):          # sees every effect first, then passes it on
    seen.append(effect)
    return (yield Pass(effect, k))

print(run(canned(trace(digest("https://example.com")))))  # 14 chars
print(seen)  # [Fetch(url='https://example.com'), Summarize(text='<h1>Hello</h1>')]
```

## Before and after: fakes in a test

`welcome` loads a user from the database and sends a welcome mail. Its test must reach neither.

**Before: plain Python with `mock.patch`.** The test patches each dependency by the name `app`
looks it up under. Forget one patch and the test still passes, after sending a real mail:

```python excerpt
# app.py
from myapp.db import load_user
from myapp.mail import send_mail

def welcome(user_id):
    user = load_user(user_id)
    body = f"Welcome, {user.name}!"
    send_mail(user.email, body)
    return body

# test_app.py
from unittest import mock
import app
from myapp.db import User

def test_welcome():
    with mock.patch("app.load_user", return_value=User("Alice", "alice@example.com")):
        assert app.welcome(1) == "Welcome, Alice!"
    # Passes. app.send_mail was never patched, so the test sent a real mail.
```

**After: doeff.** `welcome` yields its requests. The test answers them with one fake handler
per dependency, and nothing is patched:

```python
from dataclasses import dataclass
from doeff import EffectBase, Resume, do, handler, run

@dataclass(frozen=True)
class User:
    name: str
    email: str

@dataclass(frozen=True)
class LoadUser(EffectBase):
    user_id: int

@dataclass(frozen=True)
class SendMail(EffectBase):
    to: str
    body: str

@do
def welcome(user_id):
    user = yield LoadUser(user_id)
    body = f"Welcome, {user.name}!"
    yield SendMail(user.email, body)
    return body

sent: list[str] = []

@handler
def fake_db(effect: LoadUser, k):      # the annotation picks the effects it answers
    return (yield Resume(k, User("Alice", "alice@example.com")))

@handler
def fake_mail(effect: SendMail, k):    # keeps the mail instead of sending it
    sent.append(effect.to)
    return (yield Resume(k, None))

print(run(fake_db(fake_mail(welcome(1)))))  # Welcome, Alice!
print(sent)  # ['alice@example.com']
```

Forget `fake_mail` and nothing is sent: no handler answers `SendMail`, so the run stops and
names it. Add this below the example above:

```python continues
try:
    run(fake_db(welcome(1)))  # fake_mail left out
except Exception as error:
    print(type(error).__name__)  # UnhandledEffect
```

In production, `welcome` stays as it is and handlers that call your database and mail server
take the place of the fakes. The doeff version is longer: each request is a class, and
`welcome` is a generator. What the extra lines buy is a test that cannot reach production by
accident.

The same test, written in other styles, with one fake left out:

| Style | The logic is | The test swaps in fakes by | Forget one fake and |
| --- | --- | --- | --- |
| Plain Python, `mock.patch` | plain functions | patching each name where the code looks it up | the real function runs |
| Plain Python, dependencies as arguments | functions that take their dependencies | passing fake functions | a `TypeError` if the test builds its own set; the real function runs if the test starts from production's set |
| [effect](https://github.com/python-effect/effect) 1.1.0 | generators that yield `Effect(...)` | listing the expected effects and their answers in order | an `AssertionError` (`Performer not found`) |
| [effectful](https://github.com/BasisResearch/effectful) 0.4.0 | plain functions that call `@defop` operations | a dict of handlers | no error: the call returns a symbolic term instead of a value |
| doeff | generators that yield effects | installing one handler per effect | an `UnhandledEffect` error |

A doeff handler without an effect annotation sees every effect: without `: LoadUser`, `fake_db`
would answer `SendMail` too, and the test without `fake_mail` would pass.

## Performance

An effect costs about 4 to 5 µs. A network request or a model call takes milliseconds, so in I/O
code the cost does not show. In a tight numeric loop it does: keep such a loop in plain Python and
yield effects around it.

One effect round trip: perform an effect and get the handler's answer back (µs of CPU per effect,
2,000 effects per sample):

| Implementation | Median | p90 | × a function call |
| --- | ---: | ---: | ---: |
| Plain Python: a function call | 0.13 | 0.20 | 1 |
| Plain Python: a generator and a driver loop | 0.38 | 0.61 | 3 |
| doeff: `@handler` and `Resume` | 4.3 | 6.0 | 34 |
| doeff: built-in `reader` (`Ask`) | 4.9 | 6.7 | 38 |
| [effect](https://github.com/python-effect/effect) 1.1.0: `@do` and a sync performer | 12.7 | 14.6 | 99 |
| [effectful](https://github.com/BasisResearch/effectful) 0.4.0: `@defop` and `handler` | 57.9 | 62.3 | 452 |

Many tasks: spawn 1,000 tasks and gather them, and 1,000 tasks that all wait on one promise and
wake at once (µs of CPU per task):

| Implementation | Spawn and gather: median | p90 | Wait on one promise: median | p90 |
| --- | ---: | ---: | ---: | ---: |
| Plain Python: round-robin generator tasks | 1.3 | 1.9 | – | – |
| Plain Python: `asyncio` | 11.4 | 13.7 | 15.5 | 18.6 |
| effect 1.1.0: `parallel()` (runs the effects one by one) | 13.9 | 16.1 | – | – |
| doeff: `scheduled()` (the Rust scheduler, the default) | 16.6 | 20.7 | 25.5 | 44.7 |
| doeff: `scheduled(..., implementation="python")` | 151 | 167 | 205 | 223 |

A dash marks a case the implementation has no promise for. effectful has no scheduler, so it is
not in this table. `pip install doeff` installs 0.8.0, whose `scheduled()` runs the Rust scheduler
by default (the default since 0.7.0; it ran the Python one up to 0.6.0). Select the Python one
(the last row) with `DOEFF_SCHEDULER=python` or `scheduled(program, implementation="python")`.

How the numbers were measured:

- **Machine**: an Intel Core i9-7980XE (18 cores, 2.6 GHz) under Linux, with CPython 3.14.7, on
  2026-10-17 at commit
  [`d40d0ab`](https://github.com/proboscis/doeff/commit/d40d0ab9576cf5671d95caf1f85062d8630d6d6a).
- **Method**:
  [`benchmarks/compare_libraries.py`](https://github.com/proboscis/doeff/blob/main/benchmarks/compare_libraries.py)
  takes 50 samples of each case and reports the median and the 90th percentile of the process CPU
  time. Each sample runs every case once, in a new random order. Each case checks its result, so no
  implementation can win by skipping work. The script has two more scenarios: nested calls, and
  handlers that an effect passes on its way.
- **Load**: the machine was shared with other work. The random order spreads the load over every
  case alike, so compare the rows with each other more than with your own numbers.
- **Free-threaded build**: two runs on CPython 3.14.3t gave numbers of the same size: doeff
  medians of 3.2 to 5.8 µs per effect, and with `scheduled()` 17.5 to 19.5 µs per spawned task and
  29 to 32 µs per waiting task.

To run the benchmark in a checkout after `make sync` (there `uv run` uses the free-threaded CPython
3.14.3t):

```bash
uv run --with effect==1.1.0 --with effectful==0.4.0 python benchmarks/compare_libraries.py --runs 50
```

## Next steps

- **Documentation**: [start here](https://github.com/proboscis/doeff/blob/main/docs/index.md): every guide, in reading order
- **Tutorial**: [Getting started](https://github.com/proboscis/doeff/blob/main/docs/01-getting-started.md)
- **What doeff is**: [the mechanism, what it covers and where it stops](https://github.com/proboscis/doeff/blob/main/docs/positioning/what-doeff-is.md)
- **Testing an LLM agent**: [`examples/llm_agent.py`](https://github.com/proboscis/doeff/blob/main/examples/llm_agent.py)
  makes the model call an effect: an OpenAI-compatible client answers it in production, canned answers
  in [its tests](https://github.com/proboscis/doeff/blob/main/examples/test_llm_agent.py). It needs only
  `pip install doeff`, and its tests pytest besides.
- **Testing an HTTP API client**: [`examples/github_repo`](https://github.com/proboscis/doeff/tree/main/examples/github_repo)
  calls the GitHub API through the HTTP handler of `doeff[http]`; its tests answer the HTTP effects
  with a handler of their own.
  Run them with `pip install "doeff[http]" pytest`, then `pytest examples/github_repo` (no network).
- **Why effects?**: [What a handler does with the rest of the program](https://github.com/proboscis/doeff/blob/main/docs/20-why-effects-over-di.md), compared with dependency injection and plain `async`
- **Questions**: [FAQ](https://github.com/proboscis/doeff/blob/main/docs/faq.md): dependency injection, asyncio, speed, types, Hy, production use
- **Reference effects and handlers**: [doeff-core-effects](https://github.com/proboscis/doeff/blob/main/packages/doeff-core-effects/README.md)
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
from doeff_core_effects import reader

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

Hy comes with the extra `hy`: `pip install "doeff[hy]"`. Save the example as `greet.hy` and run it with `hy greet.hy`. Macros such as `do!`, `<-` and `defhandler` are brought in with `require`, never
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
from doeff_core_effects import reader
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

Effects of the reference implementation `doeff_core_effects`:

- **Reader**: `Ask(key)`, `Local(env, program)`
- **State**: `Get(key)`, `Put(key, value)`
- **Writer**: `Tell(message)`, `slog(msg, **kwargs)`
- **Error**: `Try(program)` — returns `Ok(value)` or `Err(error)`
- **Observe**: `Listen(program, types=...)` — collect effects during execution

Scheduler effects (from `doeff_core_effects._scheduler`):

- **Concurrency**: `Spawn(program)`, `Wait(task)`, `Gather(*tasks)`, `Race(*tasks)`
- **Promises**: `CreatePromise()`, `CompletePromise(p, v)`, `FailPromise(p, e)`
- **External**: `CreateExternalPromise()` — bridge external threads
- **Semaphores**: `CreateSemaphore(n)`, `AcquireSemaphore(s)`, `ReleaseSemaphore(s)`

## Handlers

Reference handlers (from `doeff_core_effects._handlers`), written like any other handler:

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
| Await | `await_handler` | `Await` |
| Scheduler | `scheduled(prog)` (from `doeff_core_effects._scheduler`) | `Spawn`, `Wait`, `Gather`, `Race`, etc. |

`writer` keeps its log in state: install `state()` outside it, and read the log by yielding the
effect `ReadWriterLog()`, which `writer` answers: `log = yield ReadWriterLog()`.

## Scheduler and Concurrency

```python
from doeff import do, run
from doeff_core_effects import Tell
from doeff_core_effects import state, writer
from doeff_core_effects import scheduled, Spawn, Gather

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

## Type checking

The examples above leave out type annotations and use plain `yield`, so pyright and mypy see
`Any` where their values flow. Here is the `digest` example with types:

```python
from collections.abc import Generator
from dataclasses import dataclass
from typing import Any

from doeff import EffectBase, K, Resume, do, handler, run, typed_resume

@dataclass(frozen=True)
class Fetch(EffectBase[str]):  # [str]: the type a handler answers with
    url: str

@dataclass(frozen=True)
class Summarize(EffectBase[str]):
    text: str

@do
def digest(url: str) -> Generator[Fetch | Summarize, Any, str]:
    page = yield from Fetch(url)  # page: str (plain yield gives Any)
    return (yield from Summarize(page))

@handler
def canned(effect: Fetch | Summarize, k: K) -> Generator[Resume, Any, Any]:
    if isinstance(effect, Fetch):
        return (yield typed_resume(effect, k, "<h1>Hello</h1>"))  # must be a str
    return (yield typed_resume(effect, k, f"{len(effect.text)} chars"))

summary = run(canned(digest("https://example.com")))  # summary: str
print(summary)  # 14 chars
```

- `yield from Fetch(url)` runs exactly like `yield Fetch(url)`, but gives the answer the type
  `EffectBase[str]` declares: `page` is a `str`, where `yield` gives `Any`.
- `Generator[Fetch | Summarize, Any, str]` lists the effects `digest` may yield and its result.
  Yielding another effect in `digest` is an error, and `run` of the program is a `str`.
- `typed_resume` checks the handler's answer against the effect: `42` for a `Fetch` is an error.
- mypy needs the annotations. It does not infer the types of a function without them: to mypy, an
  unannotated `@do` function takes and returns `Any`, and its body is not checked. pyright infers
  part of the types without annotations, and reads the same annotations.

[Static typing](https://github.com/proboscis/doeff/blob/main/docs/23-static-typing.md) covers the
scheduler's effects, handlers and what stays `Any`.

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

This README covers `doeff` and its extras. The add-on packages (LLM clients, agents, workflows and
more) live in [`packages/`](https://github.com/proboscis/doeff/tree/main/packages); `pip install doeff`
does not install them, and the guides that use one say so and
[how to install it](https://github.com/proboscis/doeff/blob/main/docs/index.md#pages-that-need-other-components).

`doeff-agents` on PyPI (0.1.1 and 0.1.2) was built for doeff 0.4 and does not work with doeff 0.5 or
later (`ImportError: cannot import name 'WithHandler' from 'doeff'`); 0.1.2 declares `doeff<0.5`, so
pip installs it with the doeff 0.4 line. The `doeff-agents` in
[`packages/doeff-agents`](https://github.com/proboscis/doeff/blob/main/packages/doeff-agents/README.md)
is not published to PyPI.

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
[map-publications]: https://github.com/proboscis/doeff/tree/main/publications
[map-images]: https://github.com/proboscis/doeff/tree/main/images
[map-docs-readme]: https://github.com/proboscis/doeff/blob/main/docs/README.md

## Contributing

Questions, bug reports and feature ideas are welcome as
[issues](https://github.com/proboscis/doeff/issues/new/choose). To send a change, pick a
[good first issue](https://github.com/proboscis/doeff/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22);
setting up the development environment, running the tests and opening a pull request are
described in [CONTRIBUTING.md](https://github.com/proboscis/doeff/blob/main/CONTRIBUTING.md).
Everyone taking part follows the
[Code of Conduct](https://github.com/proboscis/doeff/blob/main/CODE_OF_CONDUCT.md); report a
security problem privately as described in
[SECURITY.md](https://github.com/proboscis/doeff/blob/main/SECURITY.md).

## License

MIT License. See [LICENSE](https://github.com/proboscis/doeff/blob/main/LICENSE).
