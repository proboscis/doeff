# Why Effects? What a Handler Does with the Rest of the Program

doeff is algebraic effects for Python ([What doeff Is](positioning/what-doeff-is.md)). A program
`yield`s effects, and a handler receives each effect together with `k`, the continuation: the rest
of the program from that `yield` on. What the handler does with `k` decides what the effect means.
It can resume `k` with an answer, pass the effect to the next handler out, perform it again, throw
an error into it, hold it while other tasks run, or drop it and end the computation with a value of
its own.

[![A narrated video, about two and a half minutes: a handler gets k, the rest of the program; a cost cap drops k, a retry performs the effect again and resumes k once, a recorder keeps the answers and replays them, a virtual clock holds the k of sleeping tasks; then how dependency injection, plain async and composing handlers compare](assets/videos/why-effects.jpg)](assets/videos/why-effects.mp4)

This page shows, example by example, what that gives a program: control flow decided outside the
code (budgets, retries, routing), one handler for a concern across every kind of operation, effects
as data that a handler can record, replay or rewrite, and concurrency and time as handlers. Then it
compares effects with the two tools Python already has for parts of this, dependency injection (DI)
and plain `async`, and lists where those are the better tool.

Every example runs with `pip install doeff` (0.6.0 or later, Python 3.11+). Copy a block into a
file and run it with `python`; the comment after each `print` is what it prints. For the theory
behind the comparisons, see [Capability Classes](22-capability-classes.md).

## The Running Example

An app summarizes a topic with a language model, then asks an image model for a picture. The
workflow `yield`s one effect per request, a small class that names the operation, and does not say
who answers it. Save this block as `ai_effects.py`; the later examples on this page import it:

```python file=ai_effects.py
from dataclasses import dataclass

from doeff import EffectBase, Resume, do, handler


@dataclass(frozen=True)
class Chat(EffectBase):       # "ask a language model"
    model: str
    prompt: str


@dataclass(frozen=True)
class Draw(EffectBase):       # "ask an image model"
    model: str
    prompt: str


@do
def illustrated_summary(topic):
    summary = yield Chat("gpt-4o", f"Summarize {topic}")
    picture = yield Draw("imagen-3", summary)
    return f"{summary} | {picture}"


@handler
def canned_chat(effect: Chat, k):     # the annotation picks the effects it answers
    return (yield Resume(k, f"summary by {effect.model}"))


@handler
def canned_images(effect: Draw, k):
    return (yield Resume(k, f"image by {effect.model}"))
```

`Resume(k, value)` answers the effect: `k` is the rest of the workflow, and it continues with
`value` as the result of its `yield`. In an app, the handlers that answer `Chat` and `Draw` call
the model providers; `canned_chat` and `canned_images` answer with fixed text so that the examples
run without a network. Install the handlers around the workflow and run it:

```python
from doeff import run

from ai_effects import canned_chat, canned_images, illustrated_summary

program = canned_chat(canned_images(illustrated_summary("tides")))
print(run(program))  # summary by gpt-4o | image by imagen-3
```

`illustrated_summary` names two operations and nothing else. Every example below is a handler
installed around it, and none of them changes it.

## What a Handler Can Do with the Continuation

### 1. Drop it: a budget across all providers

A cost cap must add up the price of every call, whichever model it goes to. A handler that
returns without resuming `k` ends the workflow there, and its return value becomes the answer, so
the call over the budget never reaches a provider:

```python
from doeff import Pass, handler, run

from ai_effects import Chat, Draw, canned_chat, canned_images, illustrated_summary

PRICE_CENTS = {"gpt-4o": 1, "imagen-3": 4}


def cost_cap(limit_cents):
    spent = 0

    @handler
    def cap(effect: Chat | Draw, k):
        nonlocal spent
        spent += PRICE_CENTS[effect.model]
        if spent > limit_cents:
            return f"stopped before {effect.model}: over {limit_cents} cents"
        return (yield Pass(effect, k))

    return cap


def providers(program):
    return canned_chat(canned_images(program))


print(run(providers(cost_cap(10)(illustrated_summary("tides")))))  # summary by gpt-4o | image by imagen-3
print(run(providers(cost_cap(3)(illustrated_summary("tides")))))  # stopped before imagen-3: over 3 cents
```

The workflow has no check of its own, and it cannot get around the cap: it has no client to call
directly. Whether its next step runs is decided by the handler that holds `k`.

### 2. Pass it on: one handler for a concern, across every kind of call

Log every model call. A concern is one handler: it sees every effect it is annotated for, looks at
it, and passes it on to the next handler out with `Pass`:

```python
from doeff import Pass, handler, run

from ai_effects import Chat, Draw, canned_chat, canned_images, illustrated_summary

log = []


@handler
def log_calls(effect: Chat | Draw, k):  # one handler for both services
    log.append(f"{type(effect).__name__.lower()} {effect.model}")
    return (yield Pass(effect, k))


program = log_calls(illustrated_summary("tides"))
print(run(canned_chat(canned_images(program))))  # summary by gpt-4o | image by imagen-3
print(log)  # ['chat gpt-4o', 'draw imagen-3']
```

N kinds of effects and M concerns make N effects and M handlers. A new kind of effect only has to
be named in the annotations of the handlers that should see it; annotate a handler with
`EffectBase` (or leave the annotation out) and it sees every effect, including the ones added later.

### 3. Perform it again, resume once: retries

A handler can perform the effect again on the handlers outside it (`yield effect`) and catch what
they raise. Each attempt is a new effect, and the workflow's `k` resumes once, with the answer that
succeeded:

```python
from doeff import Resume, handler, run

from ai_effects import Chat, Draw, canned_images, illustrated_summary

attempts = []


@handler
def flaky_chat(effect: Chat, k):        # the provider times out twice, then answers
    attempts.append(effect.model)
    if len(attempts) < 3:
        raise ConnectionError("provider timed out")
    return (yield Resume(k, f"summary by {effect.model}"))


@handler
def retry(effect: Chat | Draw, k):      # one retry policy for every model call
    for attempt in range(3):
        try:
            answer = yield effect       # ask the handlers outside this one
        except ConnectionError:
            if attempt == 2:
                raise
            continue
        return (yield Resume(k, answer))


program = retry(illustrated_summary("tides"))
print(run(flaky_chat(canned_images(program))))  # summary by gpt-4o | image by imagen-3
print(len(attempts))  # 3
```

doeff's continuations are one-shot: `k` resumes at most once. Retrying means performing the
effect again, as above, never resuming `k` twice.

### 4. Answer it from the effect itself: dry runs and rewrites

An effect is a frozen dataclass, so a handler can answer it without any provider, keep it, or
build a different one. A dry run records what the workflow would call:

```python
from doeff import Resume, handler, run

from ai_effects import Chat, Draw, illustrated_summary

planned = []


@handler
def dry_run(effect: Chat | Draw, k):
    planned.append(effect)
    return (yield Resume(k, f"<{effect.model}>"))


print(run(dry_run(illustrated_summary("tides"))))  # <gpt-4o> | <imagen-3>
print(planned)  # [Chat(model='gpt-4o', prompt='Summarize tides'), Draw(model='imagen-3', prompt='<gpt-4o>')]
```

A rewrite sends the effect on with a different model, and the workflow does not change:

```python
from doeff import Resume, handler, run

from ai_effects import Chat, canned_chat, canned_images, illustrated_summary


@handler
def cheaper_model(effect: Chat, k):
    if effect.model == "gpt-4o":
        effect = Chat("gpt-4o-mini", effect.prompt)
    return (yield Resume(k, (yield effect)))


program = cheaper_model(illustrated_summary("tides"))
print(run(canned_chat(canned_images(program))))  # summary by gpt-4o-mini | image by imagen-3
```

### 5. Route it: the handler stack is the router

With several providers behind one kind of effect, each provider's handler answers the models it
serves and passes the rest on. Adding a provider is adding a handler:

```python
from doeff import Pass, Resume, do, handler, run

from ai_effects import Chat


@handler
def openai(effect: Chat, k):
    if not effect.model.startswith("gpt-"):
        return (yield Pass(effect, k))  # not ours: the next handler gets it
    return (yield Resume(k, f"openai answered {effect.model}"))


@handler
def gemini(effect: Chat, k):
    if not effect.model.startswith("gemini-"):
        return (yield Pass(effect, k))
    return (yield Resume(k, f"gemini answered {effect.model}"))


@do
def ask_both():
    first = yield Chat("gpt-4o", "hi")
    second = yield Chat("gemini-2.5-pro", "hi")
    return [first, second]


print(run(gemini(openai(ask_both()))))  # ['openai answered gpt-4o', 'gemini answered gemini-2.5-pro']
```

### 6. Keep the answers: record once, replay

Because effects are hashable data, a handler can record the answers the handlers outside it give,
and another can replay them, failing on any effect it has not seen:

```python
from doeff import Resume, handler, run

from ai_effects import Chat, Draw, canned_chat, canned_images, illustrated_summary


def record(tape):
    @handler
    def recorder(effect: Chat | Draw, k):
        answer = yield effect           # the handlers outside answer…
        tape[effect] = answer           # …and the answer is kept
        return (yield Resume(k, answer))

    return recorder


def replay(tape):
    @handler
    def player(effect: Chat | Draw, k):
        if effect not in tape:
            raise LookupError(f"not recorded: {effect}")
        return (yield Resume(k, tape[effect]))

    return player


tape = {}
live = canned_chat(canned_images(record(tape)(illustrated_summary("tides"))))
print(run(live))  # summary by gpt-4o | image by imagen-3
print(run(replay(tape)(illustrated_summary("tides"))))  # summary by gpt-4o | image by imagen-3
try:
    run(replay(tape)(illustrated_summary("waves")))
except LookupError as error:
    print(error)  # not recorded: Chat(model='gpt-4o', prompt='Summarize waves')
```

Here `canned_chat` and `canned_images` stand in for the providers; in your app, the recording run
uses the handlers that call them. The program never changes, and one recorder covers every kind of
effect it is annotated for.

### 7. Hold it: concurrency and time

`Spawn`, `Gather` and `Race` are effects, and the scheduler that runs them, `scheduled()`, is a
handler: it holds the continuation of each task that waits and resumes it when it can go on. A
handler of your own can build on it through effects any program can yield.

#### Virtual time: hours of waiting in an instant

A program that sleeps for hours is slow to run under a test. Answering `Sleep` at once is not
enough once several tasks sleep at the same time: whoever runs the clock must also decide which
task wakes next, which is the scheduler's job.

In doeff, the clock is a handler, and it works with the stock scheduler. The handler below parks
each sleeping task on a promise: it holds `k` until the promise completes. An idle-priority task,
which runs only while every other task waits, moves the clock to the earliest wake-up and wakes
that task:

```python
import heapq
import time
from dataclasses import dataclass
from itertools import count

from doeff import EffectBase, Gather, Resume, Spawn, do, handler, run
from doeff_core_effects.scheduler import (
    PRIORITY_IDLE,
    CompletePromise,
    CreatePromise,
    Wait,
    scheduled,
)


@dataclass(frozen=True)
class Sleep(EffectBase):
    seconds: float


@dataclass(frozen=True)
class Now(EffectBase):
    pass


def virtual_clock():
    now = 0.0
    sleepers = []                       # (wake-up time, order, promise), earliest first
    order = count()
    advancing = False

    @do
    def advance():                      # idle priority: runs while every other task waits
        nonlocal now, advancing
        while sleepers:
            now, _, promise = heapq.heappop(sleepers)
            yield CompletePromise(promise, None)
        advancing = False

    @handler
    def clock(effect: Sleep | Now, k):
        nonlocal advancing
        if isinstance(effect, Now):
            return (yield Resume(k, now))
        promise = yield CreatePromise()
        heapq.heappush(sleepers, (now + effect.seconds, next(order), promise))
        if not advancing:
            advancing = True
            yield Spawn(advance(), priority=PRIORITY_IDLE, daemon=True)
        yield Wait(promise.future)
        return (yield Resume(k, None))

    return clock


log = []


@do
def reminder(name, every_hours, times):
    for _ in range(times):
        yield Sleep(every_hours * 3600)
        hour = (yield Now()) / 3600
        log.append(f"{hour:g}h {name}")


@do
def day():
    tea = yield Spawn(reminder("tea", 2, 3))
    stretch = yield Spawn(reminder("stretch", 3, 2))
    yield Gather(tea, stretch)


started = time.monotonic()
run(scheduled(virtual_clock()(day())))
print(log)  # ['2h tea', '3h stretch', '4h tea', '6h stretch', '6h tea']
print(time.monotonic() - started < 1)  # True
```

Six hours of two concurrent tasks ran in well under a second, in the same order every time. Under
a handler that answers `Sleep` with `Await(asyncio.sleep(effect.seconds))` (under
`await_handler()`, as in [Effects vs. Plain `async`](#effects-vs-plain-async)) and `Now` with the
wall clock, the same `day` waits for real. The scheduler knows nothing about the clock, and the
clock uses the scheduler only through effects any program can yield (`CreatePromise`, `Spawn`,
`Wait`).

### 8. Answer it per environment: secrets, tests and production

The use most people meet first is the plainest one: the same program, answered by different
handlers where it runs. Where a secret comes from depends on the environment: a vault in
production, environment variables in CI, a dictionary in a test. The program asks for the secret
and names no store. The handlers decide, and a handler that does not have the secret passes the
effect on to the next one:

```python
import os
from dataclasses import dataclass

from doeff import EffectBase, Pass, Resume, do, handler, run


@dataclass(frozen=True)
class GetSecret(EffectBase):
    name: str


@do
def connect():
    password = yield GetSecret("db-password")
    return f"connected with a {len(password)}-character password"


def from_dict(secrets):                 # a test's secrets (or a vault client's)
    @handler
    def lookup(effect: GetSecret, k):
        if effect.name not in secrets:
            return (yield Pass(effect, k))
        return (yield Resume(k, secrets[effect.name]))

    return lookup


@handler
def from_environment(effect: GetSecret, k):  # DB_PASSWORD for "db-password"
    return (yield Resume(k, os.environ[effect.name.upper().replace("-", "_")]))


os.environ["DB_PASSWORD"] = "hunter2"
print(run(from_dict({"db-password": "s3cret"})(connect())))  # connected with a 6-character password
print(run(from_environment(from_dict({})(connect()))))  # connected with a 7-character password
```

The stack of handlers is the configuration. In the same way, a test answers the effects that
reach the outside world with handlers that return fixed answers, and production installs handlers
that call the real services. Nothing is patched, and an effect that no handler answers stops the
run with `UnhandledEffect`. Swapping one implementation for another is the part of this page where
DI comes closest, as the next section shows.

## Compared with Dependency Injection and Plain `async`

Python has two common tools for parts of the above: pass the clients in (dependency injection,
DI), and make the I/O `async`.

### Dependency injection

With DI, the workflow takes its clients as arguments, and each environment passes its own:

```python
from typing import Protocol


class ChatModel(Protocol):
    def chat(self, model: str, prompt: str) -> str: ...


class ImageModel(Protocol):
    def draw(self, model: str, prompt: str) -> str: ...


def illustrated_summary(topic: str, llm: ChatModel, images: ImageModel) -> str:
    summary = llm.chat("gpt-4o", f"Summarize {topic}")
    picture = images.draw("imagen-3", summary)
    return f"{summary} | {picture}"


class CannedChat:
    def chat(self, model: str, prompt: str) -> str:
        return f"summary by {model}"


class CannedImages:
    def draw(self, model: str, prompt: str) -> str:
        return f"image by {model}"


print(illustrated_summary("tides", CannedChat(), CannedImages()))  # summary by gpt-4o | image by imagen-3
```

DI is good at this:

- it is plain Python: methods and arguments, nothing new to learn
- the editor completes `llm.` with the methods of `ChatModel`
- the call site shows every dependency
- a type checker verifies the wiring

For choosing one implementation per environment (section 8), DI and effects have the same power,
and DI needs no new concepts. They part where something must happen around the calls, or to the
rest of the computation:

- **An injected object answers a call and returns.** It does not get the rest of the workflow, so
  it cannot drop it (section 1), hold it while other tasks run (section 7), or change its result
  once it has run. A handler gets `k` and can do all three.
- **A concern around every call is a wrapper per client**, where effects need one handler
  (section 2).
- **Reach.** A handler answers an effect for everything inside it, including code deep in the call
  chain that takes no parameter for it, and you can install a handler around one part of a program
  only.

Logging every call, written with DI, needs one wrapper for the chat client and another for the
image client:

```python
class CannedChat:
    def chat(self, model, prompt):
        return f"summary by {model}"


class CannedImages:
    def draw(self, model, prompt):
        return f"image by {model}"


log = []


class LoggedChat:                       # a wrapper for the chat service…
    def __init__(self, inner):
        self.inner = inner

    def chat(self, model, prompt):
        log.append(f"chat {model}")
        return self.inner.chat(model, prompt)


class LoggedImages:                     # …and the same wrapper again for images
    def __init__(self, inner):
        self.inner = inner

    def draw(self, model, prompt):
        log.append(f"draw {model}")
        return self.inner.draw(model, prompt)


llm, images = LoggedChat(CannedChat()), LoggedImages(CannedImages())
llm.chat("gpt-4o", "Summarize tides")
images.draw("imagen-3", "summary by gpt-4o")
print(log)  # ['chat gpt-4o', 'draw imagen-3']
```

Add retries and a cost cap and there are six wrapper classes; add an embedding service and there
are nine. N kinds of calls and M concerns make N × M wrappers, against N effects and M handlers. A
dry run, a rewrite or a recorder is one more wrapper class per service, and a router is one more
object that picks the client for each model.

### Effects vs. Plain `async`

`async` keeps I/O from blocking, but it spreads: a function that awaits must be `async`, so must
its callers, and so must its tests:

```python
import asyncio


async def fetch(url):                   # the I/O is async…
    await asyncio.sleep(0.01)
    return f"<html>{url}</html>"


async def total_size(urls):             # …so is every caller…
    pages = await asyncio.gather(*(fetch(url) for url in urls))
    return sum(len(page) for page in pages)


print(asyncio.run(total_size(["a.com", "b.com"])))  # 36
```

With effects, the program yields a `Fetch` effect and runs concurrent work with `Spawn` and
`Gather`. Whether a `Fetch` is answered by an `async` client is the handler's business; a handler
that answers at once needs no event loop:

```python
import asyncio
from dataclasses import dataclass

from doeff import Await, EffectBase, Gather, Resume, Spawn, do, handler, run
from doeff_core_effects.handlers import await_handler
from doeff_core_effects.scheduler import scheduled


@dataclass(frozen=True)
class Fetch(EffectBase):
    url: str


@do
def fetch(url):
    return (yield Fetch(url))


@do
def total_size(urls):                   # no async, no await
    tasks = []
    for url in urls:
        tasks.append((yield Spawn(fetch(url))))
    pages = yield Gather(*tasks)
    return sum(len(page) for page in pages)


async def download(url):                # production: an async client
    await asyncio.sleep(0.01)
    return f"<html>{url}</html>"


@handler
def async_http(effect: Fetch, k):
    return (yield Resume(k, (yield Await(download(effect.url)))))


@handler
def instant_http(effect: Fetch, k):     # answers at once, without an event loop
    return (yield Resume(k, f"<html>{effect.url}</html>"))


urls = ["a.com", "b.com"]
print(run(scheduled(await_handler()(async_http(total_size(urls))))))  # 36
print(run(scheduled(instant_http(total_size(urls)))))  # 36
```

The trade: doeff does not remove the "color" of functions; it replaces two (plain and `async`)
with one. A function that performs effects is a generator, and its callers
`yield` it, as `total_size` yields `Spawn(fetch(url))`. What you gain is that sync, async and
concurrent work share that one color, and what an effect does, including whether it waits on an
event loop, is decided by a handler. Code that waits on timers is the strongest case: with
`asyncio`, running it on a virtual clock means replacing the event loop's clock, and in doeff it is
one handler ([section 7](#virtual-time-hours-of-waiting-in-an-instant)).

### Where DI or Plain Python Is Better

Effects are not free. Use plain functions or DI when:

**The code runs one way.** With one provider and nothing around its calls, an effect is ceremony:
a class, a generator, a handler and `run()` for what a function argument does in one line.

```python
from dataclasses import dataclass

from doeff import EffectBase, Resume, do, handler, run


def summarize(topic, chat):             # plain Python: pass the one client in
    return chat("gpt-4o", f"Summarize {topic}")


print(summarize("tides", lambda model, prompt: f"summary by {model}"))  # summary by gpt-4o


@dataclass(frozen=True)                 # doeff: the same call, four more pieces
class Chat(EffectBase):
    model: str
    prompt: str


@do
def summarize_effect(topic):
    return (yield Chat("gpt-4o", f"Summarize {topic}"))


@handler
def answer(effect: Chat, k):
    return (yield Resume(k, f"summary by {effect.model}"))


print(run(answer(summarize_effect("tides"))))  # summary by gpt-4o
```

**You find an API through the editor.** `llm.` lists the methods of a client. With effects, you
need to know the names of the effect classes and import them.

**You want the wiring in one place.** A DI constructor shows every dependency where the object is
built. With effects, what answers an effect is decided by the handlers installed around the
program at run time.

**You want the type checker to prove the wiring.** doeff's effects can be typed, so the checker
knows what each `yield` gives back (see [Static Typing](23-static-typing.md)). No checker proves
that a handler is installed for every effect a program performs: a missing one is an
`UnhandledEffect` error when the program runs, naming the effect.

**The code is a tight loop.** An effect costs a few microseconds (see the
[Performance](../README.md#performance) table in the README). Next to a network call that does not
show; inside a numeric loop it does. Keep the loop in plain Python and yield effects around it.

**You need to resume a continuation more than once.** doeff's continuations are one-shot, so
backtracking search and nondeterministic choice cannot be written as handlers
([Continuations are one-shot](positioning/what-doeff-is.md#continuations-are-one-shot)).

### Decision Guide

| Situation | Use |
| --- | --- |
| One provider, code that runs one way | Plain functions or DI |
| A small script, or a service whose dependencies never change | Plain functions or DI |
| Discovering an API through editor completion matters most | DI |
| A tight numeric loop | Plain Python (yield effects around the loop) |
| Backtracking or nondeterministic choice | Not doeff: its continuations are one-shot |
| Deciding outside the code whether its next step runs: budgets, early exit, retries | Effects |
| Several providers behind one kind of operation, chosen per call | Effects |
| Logging, cost caps, retries or tracing across many kinds of calls | Effects |
| Dry runs, record and replay of expensive calls | Effects |
| Async I/O without making every caller `async` | Effects |
| Concurrent code that must also run on a virtual clock | Effects |

## The Composability Argument

Every handler on this page minds its own concern, and an effect it is not annotated for passes it
by without a line of code. Effects are the rows and handlers the columns:

```text
                       Handlers (one concern each)
                    ┌───────────┬──────────┬───────┬───────────────┬───────────┐
                    │ log_calls │ cost_cap │ retry │ virtual_clock │ scheduled │
    ┌───────────────┼───────────┼──────────┼───────┼───────────────┼───────────┤
    │ Chat          │     ✓     │    ✓     │   ✓   │       ·       │     ·     │
E   │ Draw          │     ✓     │    ✓     │   ✓   │       ·       │     ·     │
f   │ GetSecret     │     ·     │    ·     │   ·   │       ·       │     ·     │
f   │ Sleep, Now    │     ·     │    ·     │   ·   │       ✓       │     ·     │
e   │ Spawn, Gather │     ·     │    ·     │   ·   │       ·       │     ✓     │
c   └───────────────┴───────────┴──────────┴───────┴───────────────┴───────────┘
t
s   ✓ = the handler acts on the effect     · = the effect passes the handler by

    A new effect:  one row (one class; the handlers that should see it name it)
    A new concern: one column (one handler; it applies to every effect it names)
```

With DI, each `✓` is a wrapper class, and a clock for concurrent code has to be a scheduler too.

## Summary

A handler gets the rest of the program, so it decides how the program goes on. On this page that
gave:

1. **control flow decided outside the code**: a budget that drops the continuation, a retry that
   performs the effect again, a router that passes it on
2. **one handler per concern** (logging, cost, retries, recording) across every kind of effect
3. **effects as data**: dry runs, rewrites, record and replay
4. **concurrency and time as handlers**: the scheduler holds the continuations of waiting tasks,
   and a virtual clock built on it runs hours in an instant
5. **one program in every environment**: production, a test, a dry run and a replay differ only in
   their handlers

Continuations are one-shot: a handler resumes each `k` at most once, so backtracking and
nondeterministic choice are out. If you call one API with one client and run the code one way, use
plain Python or DI. When you want to try effects, start with
[Getting Started](01-getting-started.md); for when each kind of handler pays off, read
[Capability Classes](22-capability-classes.md).
