# FAQ

Short answers to the questions people ask first about doeff. Each answer links to the page, the
measurement or the test it rests on.

- [What are algebraic effects?](#what-are-algebraic-effects)
- [What does doeff give me?](#what-does-doeff-give-me)
- [Is doeff a tool for swapping I/O in tests?](#is-doeff-a-tool-for-swapping-io-in-tests)
- [Isn't this just dependency injection?](#isnt-this-just-dependency-injection)
- [How is it different from asyncio?](#how-is-it-different-from-asyncio)
- [Isn't this just generators, or a monad?](#isnt-this-just-generators-or-a-monad)
- [How fast is it?](#how-fast-is-it)
- [Does type checking work?](#does-type-checking-work)
- [How do I debug a doeff program?](#how-do-i-debug-a-doeff-program)
- [Does doeff take over my whole codebase?](#does-doeff-take-over-my-whole-codebase)
- [Do I need Hy?](#do-i-need-hy)
- [Is it ready for production?](#is-it-ready-for-production)
- [How does it compare with other Python effect libraries?](#how-does-it-compare-with-other-python-effect-libraries)
- [Is it the same as effect handlers in OCaml 5 or Koka?](#is-it-the-same-as-effect-handlers-in-ocaml-5-or-koka)

## What are algebraic effects?

A way to separate the operations a program performs from what they mean. The program performs an
operation, an *effect*, and pauses. A handler installed around the program receives the effect
together with the *continuation*: the rest of the program from that point on. The handler decides
what happens next: answer and let the program go on, throw an error at that point, run other work
first, or end the computation with a value of its own.

An exception is the case where the handler never goes back: `raise` pauses the function, and the
`except` outside it drops the rest. A handler of an algebraic effect can also answer at the place
of the `yield`, and the program goes on from there:

```python
from dataclasses import dataclass
from doeff import EffectBase, Resume, do, handler, run

@dataclass(frozen=True)
class Missing(EffectBase):              # "this setting has no value: what now?"
    key: str

@do
def settings(given):
    result = {}
    for key in ("host", "port"):
        if key in given:
            result[key] = given[key]
        else:
            result[key] = yield Missing(key)
    return result

@handler
def use_defaults(effect: Missing, k):   # answer, and go back to the yield
    return (yield Resume(k, {"host": "localhost", "port": 8080}[effect.key]))

@handler
def give_up(effect: Missing, k):        # do not go back: like an exception caught outside
    return f"no {effect.key} given"

print(run(use_defaults(settings({"host": "example.com"}))))  # {'host': 'example.com', 'port': 8080}
print(run(give_up(settings({"host": "example.com"}))))  # no port given
```

You define the effects and write the handlers, and several handlers stack around one program, each
answering the effects it knows. State, exceptions, concurrency (a scheduler is a handler that holds
the continuations of waiting tasks), logging and I/O can all be written this way, so one mechanism
covers what a language usually builds in one feature at a time. The idea comes from Plotkin and
Pretnar's *Handlers of Algebraic Effects* (2009); OCaml 5 and Koka build it into the language.

In doeff, an effect is an instance of a class under `EffectBase`, a program is a generator function
under `@do`, and a handler is a function of `(effect, k)`. Continuations are one-shot: a handler
resumes each `k` at most once ([Is it the same as effect handlers in OCaml 5 or Koka?](#is-it-the-same-as-effect-handlers-in-ocaml-5-or-koka)).
[What doeff Is](positioning/what-doeff-is.md) defines doeff's version, what it covers and where it
stops.

## What does doeff give me?

Algebraic effects. Your functions `yield` effects, and a handler decides what each effect means by
what it does with `k`, the continuation: the rest of the function from that `yield` on. It can
resume `k` with an answer, throw an error into it, pass the effect on, hold it while other tasks
run, or drop it and end the computation with a value of its own
([the table in the README](../README.md)). Four things follow that plain Python does not give you:

- **Control flow you define.** Early exit, budgets and errors raised where the effect was
  performed are handlers, not checks inside the function: a handler that drops `k` stops the
  function at the `yield`, and one that resumes it lets it go on
  ([A 30-second example](../README.md#a-30-second-example)).
- **Concurrency without `async`.** `Spawn`, `Gather` and `Race` are effects too, and the
  scheduler that runs them is a handler that holds the continuations of the waiting tasks
  ([Async Effects](04-async-effects.md)).
- **One handler sees every effect.** Logging, a cost cap, retries, or record and replay is one
  handler around the program, not one wrapper per client
  ([One handler, every effect](../README.md#one-handler-every-effect)).
- **One program, many meanings.** Production, a test, a dry run and a replay run the same function
  under different handlers, and an effect that no handler answers stops the run with
  `UnhandledEffect` ([Is doeff a tool for swapping I/O in tests?](#is-doeff-a-tool-for-swapping-io-in-tests)).

What it costs: each effect is a class, the logic is written as generators, and the doeff version
of a function is longer than the plain one. If your code only ever runs one way, the effects are
ceremony ([When adoption pays](22-capability-classes.md#when-adoption-pays-the-evidence-rule)).

## Is doeff a tool for swapping I/O in tests?

No. Swapping I/O is one use of it, and the one most people meet first: a test answers the effects
that reach the outside world with fake handlers, and production with handlers that do the real
I/O. It works well, because nothing is patched: if no handler answers an effect, the run stops with
`UnhandledEffect`, where a `mock.patch` you forget runs the real function
([Before and after](../README.md#before-and-after-fakes-in-a-test)).

But most handlers in these docs do no I/O at all. A budget resumes or drops the rest of the program
([A 30-second example](../README.md#a-30-second-example)), a retry policy performs the effect again,
the scheduler holds the continuations of waiting tasks, a virtual clock runs hours of sleeping tasks
in an instant, and a tracing handler sees every effect and passes it on
([Why Effects?](20-why-effects-over-di.md)). What they share is the mechanism: the handler gets the
continuation and decides how the program goes on.

If swapping one implementation for another is all you need, dependency injection is the simpler
tool ([Isn't this just dependency injection?](#isnt-this-just-dependency-injection)).

## Isn't this just dependency injection?

No, though the two overlap in one place. An injected object answers a call and returns; a handler
receives the effect together with the rest of the program as `k`. Where all you do is pick one
implementation of an operation, such as answering `LoadUser` from a dict in a test and from the
database in production, the two have the same power, and dependency injection is the simpler tool
([Class 0](22-capability-classes.md#class-0--dispatch)).

doeff differs in three places:

- **Holding the continuation.** A handler may drop `k`, throw into it, or resume it later. That is
  how the scheduler works, and how a handler can stop a program over budget, park callers until a
  rate limit frees up, batch several requests into one call, or answer identical requests in
  flight from one call. An injected object has no continuation to hold
  ([Class 2](22-capability-classes.md#class-2--scheduling)).
- **Reach.** A handler answers an effect type for everything inside it, including code deep in
  the call chain that takes no parameter for it, and you can install a handler around one part of
  a program only. One handler can see every effect, where DI needs one wrapper per client.
- **A missing answer.** A missing handler stops the run with `UnhandledEffect`. A test that starts
  from production's set of dependencies, or forgets a `mock.patch`, runs the real one. The
  [comparison table](../README.md#before-and-after-fakes-in-a-test) shows what each style does.

[Why Effects?](20-why-effects-over-di.md) shows what a handler does with the continuation, example
by example, writes the workflow with DI for comparison, and lists
[where dependency injection or plain Python is the better tool](20-why-effects-over-di.md#where-di-or-plain-python-is-better).

## How is it different from asyncio?

- **No `async` in your functions.** A doeff program is a plain `def` generator under `@do`;
  `@do` refuses an `async def` function.
- **Concurrency is effects.** `Spawn`, `Wait`, `Gather` and `Race` are effects, and `scheduled()`
  is the handler that runs them: it holds the continuation of each task that waits and resumes it
  when it can go on. A handler of your own can build on it, as the
  [virtual clock](20-why-effects-over-di.md#virtual-time-hours-of-waiting-in-an-instant) does
  ([Async Effects](04-async-effects.md)).
- **Existing async code still runs.** `yield Await(coro)` under `await_handler()` waits for a
  coroutine; one background asyncio loop thread runs the coroutines of the whole process
  ([Await Effect](04-async-effects.md#await-effect)).
- **From inside an asyncio application**, `run()` is a blocking call: run it in a worker thread.

```python
import asyncio

from doeff import Await, do, run
from doeff_core_effects.handlers import await_handler
from doeff_core_effects.scheduler import scheduled

async def read_answer() -> int:     # an async function you already have
    await asyncio.sleep(0.01)
    return 42

@do
def fetch_value():                  # a plain def: no async here
    return (yield Await(read_answer()))

async def endpoint():               # inside an asyncio application
    program = scheduled(await_handler()(fetch_value()))
    return await asyncio.to_thread(run, program)

print(asyncio.run(endpoint()))  # 42
```

A task costs more than an asyncio task: about 17 µs of CPU per spawned task with the Rust
scheduler, against 11 µs for asyncio ([Performance](../README.md#performance)). What effects buy
over plain `async`, such as a virtual clock in tests, is in
[Effects vs. Plain `async`](20-why-effects-over-di.md#effects-vs-plain-async).

## Isn't this just generators, or a monad?

The shape is the same: `@do` turns a generator function into a program, the do-notation that
generator-based monad libraries for Python have used for years. What doeff adds over a
hand-written driver loop:

- Handlers stack. An effect goes to the innermost handler that answers it, and `Pass` hands it
  outward, so one program mixes effects of many kinds and you compose their handlers by nesting
  them.
- A program yields other programs, and those run under the same handlers.
- A handler can hold the continuation and resume it later; the scheduler is built this way.
- An error shows a [doeff Traceback](#how-do-i-debug-a-doeff-program) through the program's and
  the handler's frames.

The price of that machinery: a generator with a driver loop costs 0.38 µs per request, doeff 3 to
5 µs ([Performance](../README.md#performance)).

## How fast is it?

An effect costs about 3 to 5 µs of CPU: 23 to 38 times a plain function call. A network request
or a model call takes milliseconds, so in I/O code the cost does not show. In a tight numeric loop
it does: keep the loop in plain Python and yield effects around it.

- The other Python effect libraries measured: effect 1.1.0 takes 12.7 µs per effect, effectful
  0.4.0 57.9 µs.
- Many tasks: the Rust scheduler costs about 17 µs of CPU per spawned task, asyncio 11 µs.
  `pip install doeff` installs 0.8.0, whose `scheduled()` runs the Rust scheduler by default (since
  0.7.0; up to 0.6.0 it ran the Python one, about 150 µs per task). Select the Python one with
  `DOEFF_SCHEDULER=python` or `scheduled(program, implementation="python")`
  ([Rust Scheduler](25-rust-scheduler.md)).

[Performance](../README.md#performance) has every number with its p90, the machine and the method.
To measure on your own machine, run
[`benchmarks/compare_libraries.py`](../benchmarks/compare_libraries.py).

## Does type checking work?

Yes, for the values that flow through effects, once you annotate them. `EffectBase[str]` declares
the type a handler answers with, `yield from Fetch(url)` gives that answer the type `str`, the
generator's annotation lists the effects a program may yield, and `typed_resume` checks a
handler's answer. Plain `yield` gives `Any`. The typed example in the README is checked under
pyright's strict mode and `mypy --strict` by
[`tests/test_docs_typed_examples.py`](../tests/test_docs_typed_examples.py)
([Type checking](../README.md#type-checking)).

Not checked by the type checker: that the handlers you install answer every effect a program
yields. A missing handler is an `UnhandledEffect` when the program runs.
[Static Typing](23-static-typing.md#what-is-not-typed) lists what else stays `Any`.

## How do I debug a doeff program?

Programs and handlers are plain Python functions, so `print`, logging and `breakpoint()` work in
both. When a run fails, `run()` prints a doeff Traceback to stderr: the program's frames and the
handler's frames in the order the effect went through them, with the handlers installed at that
point.

```python
from dataclasses import dataclass
from doeff import EffectBase, do, handler, run

@dataclass(frozen=True)
class Fetch(EffectBase):
    url: str

@do
def digest(url):
    page = yield Fetch(url)
    return len(page)

@handler
def offline(effect: Fetch, k):     # a handler that fails
    raise ConnectionError(f"cannot reach {effect.url}")

try:
    run(offline(digest("https://example.com")))
except ConnectionError as error:
    print(error)  # cannot reach https://example.com
```

Saved as `app.py`, the run prints this to stderr above the message:

```text
doeff Traceback (most recent call last):

  digest()  app.py:10
    page = yield Fetch(url)
  offline()  app.py:15
    raise ConnectionError(f"cannot reach {effect.url}")
    handlers:
      offline ↗

ConnectionError: cannot reach https://example.com
```

- While a handler runs, the program that yielded the effect is suspended, so a debugger's stack
  shows the handler under `run()` but not the program. The doeff Traceback shows both.
- To see every effect a program yields, install a handler that records them and passes them on
  ([One handler, every effect](../README.md#one-handler-every-effect)).
- The Rust scheduler makes its decisions in Rust, where a debugger cannot step. To step through
  the scheduler itself, select the Python one with `DOEFF_SCHEDULER=python`.

## Does doeff take over my whole codebase?

No. A `@do` function calls plain functions as usual, and plain code runs a doeff program with
`run()`, so you can start with one function:

```python
from dataclasses import dataclass
from doeff import EffectBase, Resume, do, handler, run

def slugify(title):                  # plain Python, unchanged
    return title.lower().replace(" ", "-")

@dataclass(frozen=True)
class Save(EffectBase):
    key: str
    text: str

@do
def publish(title, text):            # the one function that uses effects
    key = slugify(title)
    yield Save(key, text)
    return key

saved: dict[str, str] = {}

@handler
def in_memory(effect: Save, k):
    saved[effect.key] = effect.text
    return (yield Resume(k, None))

def publish_now(title, text):        # plain code: run() is the boundary
    return run(in_memory(publish(title, text)))

print(publish_now("Hello World", "First post"))  # hello-world
print(saved)  # {'hello-world': 'First post'}
```

What does spread is upward, as with `async`: a function that yields effects is a `@do` function,
and its callers either `yield` it or `run()` it. Calling a `@do` function builds a program; without
`yield` or `run()` it never runs, and Python does not warn you
([The function-color dividend](22-capability-classes.md#the-function-color-dividend)).

## Do I need Hy?

No. `pip install doeff` installs no Hy, and the README's Python examples run without it.

- The handlers written in Hy (files, SQL, processes, the cache) come with
  `pip install "doeff[hy]"`, and the HTTP client handlers with `pip install "doeff[http]"`.
  Importing one without its extra fails with a message that ends with the line to install it.
- GitHub's language bar shows a large share of Hy because much of the repository (handler
  libraries, add-on packages and their tests) is written in Hy. Programs you write with doeff can
  stay Python.
- If you like Lisp, [doeff-hy](../packages/doeff-hy/README.md) adds macros such as `do!` and
  `defhandler` ([Hy examples in the README](../README.md#handler-composition)).

## Is it ready for production?

Judge from these facts:

- doeff is at 0.8.0, and versions before 1.0 change the API: code written for 0.4 does not run on
  0.5 (`WithHandler` is gone from `doeff`), 0.6.0 changed what `pip install doeff` installs,
  0.7.0 changed the reference scheduler `scheduled` to the Rust one, and 0.8.0 takes only a `def`
  marked with `@handler` as a handler.
  The [changelog](../CHANGELOG.md) lists each change. Pin the version you test with.
- One person maintains it, and uses it in their own projects.
- Each push to `main` and each pull request runs the tests of the core packages on Linux with
  CPython 3.11 to 3.14. The core install and the README examples also run on macOS and Windows
  with CPython 3.11 and 3.14, and the examples in these docs run as tests.
- The Rust VM ships as wheels for Linux (x86_64, aarch64), macOS (Intel, Apple silicon) and
  Windows (x64). On other platforms, pip builds it from source, which needs a Rust toolchain.

## How does it compare with other Python effect libraries?

What a handler can do with the computation that performed the effect:

| Library | The logic is | A handler receives | A handler can | One effect (µs) |
| --- | --- | --- | --- | ---: |
| [effect](https://github.com/python-effect/effect) 1.1.0 (2019) | generators that yield `Effect(...)` | the intent and a box for its answer | answer through the box now or later, or fail it; a box it never answers leaves the run unfinished | 12.7 |
| [effectful](https://github.com/BasisResearch/effectful) 0.4.0 | plain functions that call `@defop` operations | the operation's arguments, not a continuation | return the answer, raise, or forward with `fwd()` | 57.9 |
| doeff 0.7.0 | generators that yield effects | the effect and its continuation `k` | resume `k`, throw into it, pass it on, hold it while other tasks run, or drop it and end the computation with a value of its own | 3.0 to 4.9 |

What a test does:

| Library | A test answers effects by | Forget one answer and |
| --- | --- | --- |
| effect 1.1.0 | listing the expected effects and their answers in order | an `AssertionError` |
| effectful 0.4.0 | a dict of handlers | no error: the call returns a symbolic term |
| doeff 0.7.0 | installing one handler per effect | an `UnhandledEffect` error |

- In the benchmark of many tasks, effect's `parallel()` runs the effects one by one, and effectful
  has no scheduler to measure.
- [returns](https://github.com/dry-python/returns) gives typed containers (`Result`, `Maybe`,
  `IO`). It has no handlers that resume a continuation, so it solves a different problem.

The same test in each style, and how the numbers were measured:
[Before and after](../README.md#before-and-after-fakes-in-a-test) and
[Performance](../README.md#performance).

## Is it the same as effect handlers in OCaml 5 or Koka?

The idea is the same: effects, handlers and continuations. Two differences:

- **One-shot continuations.** A handler resumes each `k` at most once, as in OCaml 5. Koka can
  resume one many times. A second `Resume` of the same `k` raises a `RuntimeError` (one-shot
  violation). Backtracking search, nondeterminism and probabilistic branching are left out by
  design; re-running the program with a memoized prefix stands in for them
  ([Class 3](22-capability-classes.md#class-3--multi-shot-deliberately-absent)).
- **No effect types in the language.** Koka's types record the effects each function may perform,
  so its compiler reports an effect nothing handles. In doeff a missing handler is found when the
  program runs
  ([Does type checking work?](#does-type-checking-work)).
