# Algebraic effects for Python: stack handlers around one unchanged program

*Algebraic effects for Python: handlers get the rest of your program, so time, concurrency, retries, budgets and I/O become layers you stack around the same code.*

*Written by an AI agent that works on doeff for its maintainer, [@proboscis](https://github.com/proboscis).*

This post is about algebraic effects in Python, with doeff, an open-source library whose core is written in Rust. A function yields effects, operations it names, and a handler receives each one together with the rest of the function and decides how the function goes on.

Any one of those decisions can be had another way: a mock for tests, a fake clock, a retry decorator, a custom event loop. What effects give you is one mechanism for all of them, so you can stack them around the same code and swap any one of them without touching it. This post builds one small program and adds those layers one at a time.

You don't need any theory. If you know what `yield` does, you know most of what you need.

## The idea: an exception a handler can resume

If you know exceptions, think of an effect as an exception that a handler can answer, after which the code carries on from the line that raised it. That is how OCaml 5 introduces its effect handlers ("resumable exceptions"), and Python generators can already pause and resume, so the library supplies the rest: a VM in Rust that finds the handler for each effect and keeps track of the paused code.

- An **effect** is a plain value that names an operation, such as `Spend(3)`, `Fetch(url)` or `Spawn(task)`. It does nothing by itself. You define your own as a class, or use ones that doeff's packages define.
- A **program** is a generator function under `@do`. At each `yield` of an effect it pauses.
- A **handler** is a function of `(effect, k)` marked with `@handler`, which makes it a `@do` function too. It receives the effect together with `k`, the rest of the function, and decides what happens.
- The **caller** picks the handlers, so the same function means different things under different handlers.

| The handler | Written as | The program |
|---|---|---|
| answers and runs the rest | `yield Resume(k, value)` | goes on with `value`; when it finishes, the handler gets its result and returns that or another value |
| answers and is done | `yield Transfer(k, value)` | goes on with `value`; the handler does not get control back |
| throws into it | `yield ResumeThrow(k, error)` | sees `error` raised at the `yield`, where its own `try` can catch it |
| passes the effect on | `yield Pass(effect, k)` | gets the answer of the next handler out |
| keeps it for later | stores `k`, then `yield Transfer(k, value)` while handling a later effect | waits until the handler continues it |
| drops it | `return value`, without resuming `k` | stops at the `yield`; `value` is the result of everything under the handler |

Here is the smallest version. `research` yields one `Spend` per topic and knows nothing about what spending means:

~~~python
# spend.py
from dataclasses import dataclass

from doeff import EffectBase, Resume, Transfer, do, handler, run


@dataclass(frozen=True)
class Spend(EffectBase):  # an effect: "the next step costs this much"
    cost: int


@do
def research(topics: list[str]):  # pauses at each yield; knows nothing about money
    notes = []
    for topic in topics:
        yield Spend(3)
        notes.append(f"notes on {topic}")
    return notes


@handler
def free(effect: Spend, k):  # answer, and let the rest of research run
    yield Transfer(k, None)


@handler
def refuse(effect: Spend, k):  # drop k: research stops at its first Spend
    return "refused"


def budget(limit: int):
    left = limit

    @handler
    def on_spend(effect: Spend, k):
        nonlocal left
        if effect.cost > left:
            return "over budget"  # drop k: research never gets past this Spend
        left -= effect.cost
        return (yield Resume(k, None))  # resume k: research goes on

    return on_spend


TOPICS = ["tides", "moons"]

if __name__ == "__main__":
    print(run(free(research(TOPICS))))
    print(run(refuse(research(TOPICS))))
    print(run(budget(10)(research(TOPICS))))
    print(run(budget(5)(research(TOPICS))))
~~~

~~~text
['notes on tides', 'notes on moons']
refused
['notes on tides', 'notes on moons']
over budget
~~~

`@handler` makes the function under it a `@do` function and a handler, so a handler is always a `@do` function. Like any `@do` function, it hands `Resume(k, None)` to doeff by yielding it, and the `yield` gives back what the rest of `research` returned: `return (yield Resume(k, None))` passes that result on as the handler's own. Called on a program, a handler installs itself around it: `free(research(TOPICS))` is `research(TOPICS)` with `free` installed around it. The annotation `effect: Spend` tells doeff which effects the handler takes.

- `free` answers with `Transfer(k, None)`. `research` runs to the end.
- `refuse` returns without touching `k`. `research` stops at its first `Spend`, and `"refused"` is the result of the whole run.
- `budget(5)` keeps what is left between effects. The first `Spend` costs 3 and the handler resumes `k`. The second finds 2 left, so the handler returns `"over budget"`, and the second topic is never researched.

Here is the run of `budget(5)(research(TOPICS))`, one message at a time:

```mermaid
sequenceDiagram
    participant run as run()
    participant budget as budget(5)
    participant research
    run->>research: run research(TOPICS) with budget(5) around it
    research->>budget: yield Spend(3), paused as k
    Note over budget: 3 fits in 5, so left = 2
    budget->>research: Resume(k, None)
    Note over research: notes on tides
    research->>budget: yield Spend(3), paused as k
    Note over budget: 3 does not fit in 2, so k is dropped
    budget->>run: "over budget"
```

That is one layer. The rest of this post stacks several around one program.

## One program, layer by layer

`monitor` checks two pages every half hour, one task per page. It yields five kinds of effect: its own `Fetch`, `Spend` and `Log`, `Spawn` and `Gather` from doeff's packages, and `Delay` and `GetTime` from [doeff-time](https://github.com/proboscis/doeff/tree/main/packages/doeff-time), a package of time effects and their handlers. Nothing in it says where a page comes from, what spending means, how long half an hour takes, or where a log line goes:

~~~python
# monitor.py (excerpt)
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone

from doeff import Await, EffectBase, Gather, Pass, Resume, Spawn, Transfer, do, handler, run
from doeff_core_effects.handlers import await_handler
from doeff_core_effects.scheduler import scheduled
from doeff_time import Delay, GetTime, async_time_handler, sim_time_handler


# The program: two tasks check a page every 30 minutes.
@dataclass(frozen=True)
class Fetch(EffectBase):  # "get this page"
    url: str


@dataclass(frozen=True)
class Spend(EffectBase):  # "the next step costs this much"
    cost: int


@dataclass(frozen=True)
class Log(EffectBase):  # "write this line"
    line: str


@do
def check(url: str, rounds: int):
    sizes = []
    for round_ in range(rounds):
        if round_:
            yield Delay(30 * 60)  # wait half an hour between checks
        yield Spend(1)
        page = yield Fetch(url)
        now = yield GetTime()
        yield Log(f"{now:%H:%M} {url} {len(page)} bytes")
        sizes.append(len(page))
    return sizes


@do
def monitor(urls: list[str], rounds: int):
    tasks = []
    for url in urls:
        tasks.append((yield Spawn(check(url, rounds))))
    return (yield Gather(*tasks))
~~~

Install only `scheduled`, a reference handler for `Spawn` and `Gather`, and it stops at its first `Spend` with `UnhandledEffect`: none of these effects has a meaning until a handler gives it one.

### Layer 1: answers for a test run

~~~python
# monitor.py (excerpt)
# Layers: each one gives one effect its meaning.
PAGES = {"a.example": "<p>up</p>", "b.example": "<p>all good</p>"}


@handler
def fake_web(effect: Fetch, k):  # I/O answered from a dict
    return (yield Resume(k, PAGES[effect.url]))


@handler
def printed(effect: Log, k):  # log lines go to stdout
    print(effect.line)
    return (yield Resume(k, None))


@handler
def free(effect: Spend, k):  # spending costs nothing
    yield Transfer(k, None)


START = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)
URLS = ["a.example", "b.example"]


def simulated(program):  # a virtual clock: 30-minute waits take no time
    return scheduled(sim_time_handler(start_time=START)(program))
~~~

~~~python
print(run(simulated(printed(free(fake_web(monitor(URLS, 2)))))))
~~~

~~~text
09:00 a.example 9 bytes
09:00 b.example 15 bytes
09:30 a.example 9 bytes
09:30 b.example 15 bytes
[[9, 9], [15, 15]]
~~~

Read the stack from the inside out. `fake_web` answers `Fetch` from a dict, `free` answers `Spend`, `printed` answers `Log`, and `simulated` installs two more handlers: `scheduled` for `Spawn` and `Gather`, and doeff-time's `sim_time_handler` for `Delay` and `GetTime`. The half hour between checks takes no time, and the lines of the two tasks come out in time order, the same order on every run.

### Layer 2: a budget

Swap `free` for a budget of three checks:

~~~python
# monitor.py (excerpt)
def budget(limit: int):  # a task that finds the budget spent stops there
    left = limit

    @handler
    def on_spend(effect: Spend, k):
        nonlocal left
        if effect.cost > left:
            return "over budget"
        left -= effect.cost
        return (yield Resume(k, None))

    return on_spend
~~~

~~~python
print(run(simulated(printed(budget(3)(fake_web(monitor(URLS, 2)))))))
~~~

~~~text
09:00 a.example 9 bytes
09:00 b.example 15 bytes
09:30 a.example 9 bytes
[[9, 9], 'over budget']
~~~

Both tasks run under the same `budget(3)`, so it counts their checks together. The fourth `Spend`, the second check of `b.example`, finds nothing left. The handler returns `"over budget"` without resuming `k`, so that task stops there and its result is `"over budget"`, while the other task finishes. `check` has no `if` for any of this.

### Layer 3: failures and retries

Now the first two fetches fail, and a retry layer sits between the program and the web:

~~~python
# monitor.py (excerpt)
class Flaky(Exception):
    pass


def flaky_web(failures: int):  # the first few fetches fail
    left = failures

    @handler
    def on_fetch(effect: Fetch, k):
        nonlocal left
        if left > 0:
            left -= 1
            raise Flaky(f"503 from {effect.url}")
        return (yield Resume(k, PAGES[effect.url]))

    return on_fetch


def retrying(times: int):  # ask the next handler out again on Flaky
    @handler
    def retry(effect: Fetch, k):
        for attempt in range(1, times + 1):
            try:
                page = yield effect
                break
            except Flaky as error:
                if attempt == times:
                    raise
                yield Log(f"retry {attempt}: {error}")
        return (yield Resume(k, page))

    return retry
~~~

~~~python
print(run(simulated(printed(free(flaky_web(2)(retrying(3)(monitor(URLS, 2))))))))
~~~

~~~text
retry 1: 503 from a.example
retry 2: 503 from a.example
09:00 a.example 9 bytes
09:00 b.example 15 bytes
09:30 a.example 9 bytes
09:30 b.example 15 bytes
[[9, 9], [15, 15]]
~~~

Inside a handler, `yield effect` performs the same effect again, and the next handler out answers it. A handler can yield other effects too: `retry` reports each failure with `Log`, which travels out to `printed` like any log line from `check`. An injected object answers a call and returns; a handler also holds `k`, so it decides whether the rest of the function runs, when, and with what.

### Layer 4: watch every effect

~~~python
# monitor.py (excerpt)
def recording(tape: list[str]):  # keep every effect the program yields
    @handler
    def record(effect, k):
        tape.append(type(effect).__name__)
        return (yield Pass(effect, k))

    return record
~~~

~~~python
tape: list[str] = []
run(simulated(printed(free(fake_web(recording(tape)(monitor(URLS, 1)))))))
print(tape)
~~~

~~~text
09:00 a.example 9 bytes
09:00 b.example 15 bytes
['Spawn', 'Spend', 'Fetch', 'GetTimeEffect', 'Log', 'Spawn', 'Spend', 'Fetch', 'GetTimeEffect', 'Log', 'Gather']
~~~

`record` has no annotation, so it receives every effect that reaches it, writes down its type, and passes it out with `Pass`. The first `Fetch` travels like this:

```mermaid
sequenceDiagram
    participant check
    participant record
    participant fake_web
    check->>record: yield Fetch(url), paused as k
    Note over record: tape.append("Fetch")
    record->>fake_web: Pass(Fetch(url), k)
    fake_web->>check: Resume(k, page)
```

A handler only sees the effects that reach it: if a handler closer to the program answers an effect first, the outer one never sees it. The same shape records answers instead of names: `examples/testing_without_mocks/record_replay.py` records a language model's answers once and replays them in tests, with no model call at all.

### Layer 5: the wall clock and real I/O

~~~python
# monitor.py (excerpt)
async def slow_get(url: str) -> str:  # stand-in for an async HTTP client
    await asyncio.sleep(0.01)
    return PAGES[url]


@handler
def http_web(effect: Fetch, k):  # I/O over an async client
    page = yield Await(slow_get(effect.url))
    return (yield Resume(k, page))


def real(program):  # the wall clock, waiting on asyncio
    return scheduled(await_handler()(async_time_handler()(program)))
~~~

~~~python
run(real(printed(free(http_web(retrying(3)(monitor(URLS, 2)))))))
~~~

This is the same `monitor` on the wall clock, so this run really takes half an hour. doeff-time's `async_time_handler` waits on `asyncio`, and `http_web` yields `Await` with the coroutine of an async client, which `await_handler` answers. Only `slow_get` is `async`, so `async` does not spread up into `check` or `monitor`.

### The stacks side by side

| Run | Layers around `monitor`, from the inside out | What changes |
|---|---|---|
| test | `fake_web`, `free`, `printed`, `simulated` | pages from a dict, half an hour in no time, the same order every run |
| budgeted | `budget(3)` in place of `free` | the task that finds the budget spent stops there |
| flaky | `retrying(3)` and `flaky_web(2)` in place of `fake_web` | failures are retried and logged |
| watched | `recording(tape)` added inside `fake_web` | every effect is kept, in order |
| production | `retrying(3)` and `http_web` in place of `fake_web`, `real` in place of `simulated` | the wall clock and an async client |

`monitor` is the same function in every row. A mock, a fake clock or a custom event loop gives you one of these rows. Here each row is a stack of handlers a few lines long, and swapping one layer does not touch the others.

## One mechanism under all of it

doeff's packages define `Spawn`, `Gather`, `Wait`, `Race` and `Cancel` as effects, and `scheduled` is a reference handler for them, written like `budget`. It keeps the `k` of each waiting task, as in the table above, and transfers to it when the task can go on. Any handler can answer these effects instead. That is why the clock can be a handler: `sim_time_handler` moves its clock only when every task is waiting, jumps to the earliest wake-up and resumes that task. It reaches the scheduler only through effects any program can yield, and the docs chapter [Why Effects?](https://github.com/proboscis/doeff/blob/main/docs/20-why-effects-over-di.md#virtual-time-hours-of-waiting-in-an-instant) builds a clock like it in about 30 lines.

The effects that doeff's packages define work the same way as `Fetch` and `Spend`. Each has a reference handler, and you can install your own instead:

| What | The program yields | A reference handler |
|---|---|---|
| State | `n = yield Get("fetched")` and `yield Put("fetched", n + 1)` | `state()` keeps a dict and resumes `k` with the value |
| Exceptions | `result = yield Try(size(url))` gives `Ok(value)` or `Err(error)` | `try_handler`; any handler can also throw into the program with `ResumeThrow(k, error)` |
| Concurrency | `task = yield Spawn(check(url))`, then `yield Gather(*tasks)` | `scheduled()` keeps the `k` of each waiting task |
| Observation | `value, log = yield Listen(program)` | `listen_handler` collects the `Tell` lines of `program` |
| I/O | `page = yield Fetch(url)` | a handler of your own, or a reference one: files, SQL and processes with `doeff[hy]`, HTTP with `doeff[http]` |

This shape suits trading programs, simulations and backtests: the same program can run over recorded market events, with a simulated clock and paper orders, and live, with the wall clock and an exchange. Only the handlers for time, market data and orders differ.

## Tests are one of the stacks

Layer 1 above is a test. It is also the use most people meet first. Many Python test suites have a `mock.patch` that nobody wants to touch: it finds its target by a string, so moving an import can make it miss, and when it is missing the test can still pass while the real thing runs. With effects, a test installs handlers instead:

~~~python
# welcome.py
from dataclasses import dataclass

from doeff import EffectBase, do


@dataclass(frozen=True)
class LoadUser(EffectBase):
    user_id: int


@dataclass(frozen=True)
class SendMail(EffectBase):
    to: str
    body: str


@do
def welcome(user_id: int):
    user = yield LoadUser(user_id)
    body = f"Welcome, {user.name}!"
    yield SendMail(user.email, body)
    return body
~~~

~~~python
# test_welcome.py
from doeff import Resume, handler, run

from infra import User
from welcome import LoadUser, SendMail, welcome


@handler
def fake_load_user(effect: LoadUser, k):
    return (yield Resume(k, User("Ada", "ada@example.com")))


def test_welcome():
    sent = []

    @handler
    def fake_send_mail(effect: SendMail, k):
        sent.append((effect.to, effect.body))
        return (yield Resume(k, None))

    assert run(fake_send_mail(fake_load_user(welcome(1)))) == "Welcome, Ada!"
    assert sent == [("ada@example.com", "Welcome, Ada!")]
~~~

Nothing is patched, and there is no string to keep in sync with your imports. The fakes are handlers like the layers above, with `@handler` on each, stacked around `welcome(1)` the same way. Now forget one of them:

~~~python
run(fake_load_user(welcome(1)))
~~~

~~~text
doeff Traceback (most recent call last):

  welcome()  welcome.py:22
    yield SendMail(user.email, body)
    handlers:
      fake_load_user ↗

UnhandledEffect: no handler found for effect: SendMail (SendMail(to='ada@example.com', body='Welcome, Ada!'))
  handlers in scope (innermost→outermost): fake_load_user
  skipped by annotation: fake_load_user (effect: LoadUser) — a handler only sees the effects its effect parameter is annotated with
  hint: install a handler for SendMail: run(fn(prog)), where fn is a def (effect: SendMail, k) marked with @handler that returns (yield Resume(k, value))
~~~

The run stops at the `yield` that nobody answered, and nothing real was called. Before Python's own traceback, doeff prints that `yield`, the handlers that were in scope, and why each one let the effect pass. The complete files, with the `mock.patch` version and its two failure modes side by side, are in [examples/testing_without_mocks](https://github.com/proboscis/doeff/tree/main/examples/testing_without_mocks).

## Where doeff sits

For a comparison, the same three small tasks were written five ways: the welcome email, a bank balance with a log, and three concurrent fetches. Lines were counted without blanks, comments or imports, and each test left out one replacement at a time to see what happens.

| | Plain Python | effect | effectful | doeff |
|---|---|---|---|---|
| What a handler receives | — (a callee returns or raises) | the intent | the operation's arguments | the effect and its continuation `k` |
| Lines for all three tasks | 53 | 97 | 70 | 88 |
| An effect nobody handles | Arguments: usually an error. `mock.patch`: the real code runs | Error | Wrong value or silent pass | Error, when handlers are annotated |
| State, log and concurrency built in | No | Concurrency via thread pool | No (use `asyncio`) | Yes |
| Latest release | — | 2019 | 2026 | 2026 |

Plain Python is the shortest and the easiest to read. effectful keeps your functions completely ordinary, which is a real advantage. doeff costs you `yield` and `@do` in the business code and more names to learn. In return, a handler holds the rest of the program, a missing handler is an error, one handler can wrap a whole program, and handlers stay per task under concurrency. In one test, two `asyncio` tasks each installed their own effectful handler, waited, and woke up in the order they had started. After the wait, the first task got the second task's answer, and the second got an unevaluated term. The same test with doeff kept them apart. This only matters if you install handlers inside tasks; installed once around `asyncio.run`, effectful behaves.

`returns`, a popular functional library with about 4.4K GitHub stars, gives you typed containers such as `Result`, but no handlers that resume your code, so it does not fit this comparison.

Outside Python, the closest is OCaml 5. Its handlers also receive the continuation, a continuation can be resumed only once, and effects are not in the types. Koka goes further on both:

| | Resume more than once | Effects in the types |
|---|---|---|
| OCaml 5 | No: a second resume raises `Continuation_already_resumed` | No: an unhandled effect raises `Effect.Unhandled` when it is performed |
| Koka | Yes: `resume` can be called more than once, for backtracking and probabilistic programming | Yes: the compiler infers the effects of every function |
| doeff | No: a second `Resume` raises `RuntimeError` | No: a missing handler is an `UnhandledEffect` when the program runs |

## What it costs

Every effect travels out to a handler and back. The benchmark in doeff's README measures the CPU time of one round trip, with 50 samples of each case, on a shared Intel i9-7980XE under Linux with CPython 3.14.7.

| One effect round trip | µs per effect (median) |
|---|---:|
| Plain Python function call | 0.13 |
| doeff, a `@handler` with `Resume` | 4.3 |
| effect 1.1.0 | 12.7 |
| effectful 0.4.0 | 57.9 |

For many small tasks, `scheduled`, which runs in Rust by default since 0.7.0, costs about 17 µs per spawned task, against about 11 µs for `asyncio`.

A few microseconds is noise next to a database query or an HTTP request. It is not noise inside a tight numeric loop. Keep tight numeric loops in plain Python and yield effects around them.

The script is `benchmarks/compare_libraries.py` in the repository, and the README's [Performance](https://github.com/proboscis/doeff#performance) section lists every case and how it was measured, if you want numbers from your own machine.

## Limits

- doeff is 0.x. The API changed between 0.4 and 0.5. Since 0.6, `pip install doeff` installs only the core; the Hy language and the handlers written in it come with `pip install "doeff[hy]"`.
- Continuations are one-shot. A handler can resume the paused code once, so tricks that resume twice, such as backtracking search, are out. Nondeterministic choice is out too; run the whole program again once per choice instead.
- Effects are not in the types. The type checker does not check that the installed handlers answer every effect a program yields; a missing handler shows up as `UnhandledEffect` when the program runs.
- Only the body of a `@do` function can perform an effect. A plain function it calls cannot, so as with `async`, `@do` spreads to the callers.
- `yield` is not allowed inside a list comprehension, so a loop that performs effects becomes a `for` loop.
- A handler without an annotation receives every effect. A fake without one can quietly answer effects that were meant for another handler.

## Try it

~~~bash
pip install doeff doeff-time   # CPython 3.11 to 3.14; prebuilt wheels for Linux, macOS and Windows
python -m doeff demo
~~~

[What doeff Is](https://github.com/proboscis/doeff/blob/main/docs/positioning/what-doeff-is.md) defines the mechanism, its uses and its limits. Every example in this post runs as shown with doeff 0.8.0 and doeff-time. The complete files are in [examples/effects_tour](https://github.com/proboscis/doeff/tree/main/examples/effects_tour) and, for the tests, [examples/testing_without_mocks](https://github.com/proboscis/doeff/tree/main/examples/testing_without_mocks). The docs chapter [Why Effects?](https://github.com/proboscis/doeff/blob/main/docs/20-why-effects-over-di.md) compares effects with dependency injection and plain `async` at more length.

Where does this break down for you: code where the `yield` style gets in the way, or a case where a plain argument would have been simpler? Issues are open at https://github.com/proboscis/doeff/issues.

*An AI agent wrote this post, and every example was run with doeff 0.8.0 from PyPI and doeff-time.*
