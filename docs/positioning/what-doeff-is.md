# What doeff Is

This page defines what doeff is. It is the source that the README, these docs and any article or
post about doeff draw on. After reading it you can say what doeff is in one sentence, tell the
mechanism from its uses, know where it stops, and describe it in words that match. Every example
on this page runs as a test.

[![A narrated video, about three minutes: a program yields effects and a handler receives each with k; research under handlers that resume k, drop it or keep a budget; handlers stacking; state, exceptions, concurrency, observation and I/O as effects of one kind; swapping I/O as one use; one-shot continuations and the other limits; and where doeff sits next to effect, effectful, OCaml 5 and Koka](../assets/videos/what-doeff-is.gif)](../assets/videos/what-doeff-is.mp4)

**doeff is algebraic effects for Python.** A program yields *effects*, and a *handler* receives
each effect together with *k*, the *continuation*: the rest of the program from that `yield` on.
What the handler does with `k` decides what the effect means. State, exceptions, concurrency,
observation, budgets and I/O are all effects with handlers of this kind, in one program, under
one mechanism. Continuations are one-shot: a handler resumes each `k` at most once.

## The mechanism

- **An effect** is a plain value that names an operation: `Spend(3)`, `Get("count")`,
  `Spawn(task)`. It does nothing by itself. You define your own as a class, or use the ones a package ships.
- **A program** is a generator function under `@do`. At each `yield` of an effect it pauses.
- **A handler** is a function of `(effect, k)`. The innermost handler installed around the program
  that takes that effect receives it.
- **The continuation `k`** is the rest of the program, paused at the `yield`. The handler stays
  installed around it, so it also receives the effects the rest of the program yields.

What the handler does with `k`:

| The handler | Written as | The program |
| --- | --- | --- |
| answers and runs the rest | `Resume(k, value)` | goes on with `value`; when it finishes, the handler gets its result and returns that or another value |
| answers and is done | `Transfer(k, value)` | goes on with `value`; the handler does not get control back |
| throws into it | `ResumeThrow(k, error)` | sees `error` raised at the `yield`, where its own `try` can catch it |
| passes the effect on | `Pass(effect, k)` | gets the answer of the next handler out |
| holds it while other tasks run | a scheduler handler, such as the reference `scheduled(...)` | waits; `Spawn`, `Gather` and `Race` are effects of this handler |
| drops it | `return value`, without resuming `k` | stops at the `yield`; `value` is the result of everything under the handler |

`research` yields one `Spend` per topic and knows nothing about what spending means. Two handlers
give `Spend` two meanings:

```python
from dataclasses import dataclass
from doeff import EffectBase, Pass, Resume, Transfer, do, handler, run

@dataclass(frozen=True)
class Spend(EffectBase):            # an effect: "the next step costs this much"
    cost: int

@do
def research(topics):               # a program: pauses at each yield
    notes = []
    for topic in topics:
        yield Spend(3)
        notes.append(f"notes on {topic}")
    return notes

@handler
def free(effect: Spend, k):         # answer, and let the rest of research run
    yield Transfer(k, None)

@handler
def refuse(effect: Spend, k):       # drop k: research stops at its first Spend
    return "refused"

print(run(free(research(["tides", "moons"]))))    # ['notes on tides', 'notes on moons']
print(run(refuse(research(["tides", "moons"]))))  # refused
```

A third meaning: a budget. The handler keeps the budget between effects, resumes `k` while the
budget lasts and drops it when the budget runs out. Add this below the example above:

```python continues
def budget(limit):
    left = limit

    @handler
    def on_spend(effect: Spend, k):
        nonlocal left
        if effect.cost > left:
            return "over budget"            # drop k: research never gets past this Spend
        left -= effect.cost
        return (yield Resume(k, None))      # resume k: research goes on

    return on_spend

print(run(budget(10)(research(["tides", "moons"]))))  # ['notes on tides', 'notes on moons']
print(run(budget(5)(research(["tides", "moons"]))))   # over budget
```

Handlers stack. An effect goes to the innermost handler that takes it, and `Pass` hands it to the
next one out. `trace` takes no annotation, so it receives every effect first:

```python continues
seen: list[object] = []

@handler
def trace(effect, k):
    seen.append(effect)
    return (yield Pass(effect, k))          # on to budget, the next handler out

print(run(budget(5)(trace(research(["tides", "moons"])))))  # over budget
print(seen)  # [Spend(cost=3), Spend(cost=3)]
```

## One mechanism for state, exceptions, concurrency, observation, budgets and I/O

The effects the packages ship are effects like `Spend`, and their reference handlers are handlers like `budget`:

| What | The program yields | The handler that gives it its meaning |
| --- | --- | --- |
| State | `n = yield Get("fetched")` and `yield Put("fetched", n + 1)` | `state()` keeps a dict and resumes `k` with the value |
| Exceptions | `result = yield Try(size(url))` gives `Ok(value)` or `Err(error)` | `try_handler`; any handler can also throw into the program with `ResumeThrow(k, error)` |
| Concurrency | `task = yield Spawn(size(url))`, then `yield Gather(*tasks)` | `scheduled()` holds the continuation of each waiting task and resumes it when it can go on |
| Observation | `value, log = yield Listen(program)` | `listen_handler` collects the `Tell` lines of `program`; a handler such as `trace` sees every effect |
| Budgets and limits | `yield Spend(3)` | a handler of your own, such as `budget` above, that resumes or drops `k` |
| I/O | `page = yield Fetch(url)` | a handler of your own, or a built-in one: files, SQL and processes with `doeff[hy]`, HTTP with `doeff[http]` |

One program can use all of them. `crawl` fetches pages in parallel tasks, counts them in state,
turns an error in one task into a value, and collects the log lines of every task:

```python
from dataclasses import dataclass
from doeff import EffectBase, Resume, do, handler, run
from doeff_core_effects import Get, Listen, Put, Tell, Try
from doeff_core_effects import listen_handler, state, try_handler, writer
from doeff_core_effects import Gather, Spawn, scheduled

@dataclass(frozen=True)
class Fetch(EffectBase):
    url: str

@do
def size(url):
    page = yield Fetch(url)                          # I/O
    if not page:
        raise ValueError(f"empty page: {url}")       # an exception, raised as in plain Python
    yield Put("fetched", (yield Get("fetched")) + 1) # state
    yield Tell(f"fetched {url}")                     # a log line
    return len(page)

@do
def crawl(urls):
    tasks = []
    for url in urls:
        tasks.append((yield Spawn(Try(size(url)))))  # a task each; Try turns an error into Err
    return (yield Gather(*tasks))

@do
def report(urls):
    sizes, log = yield Listen(crawl(urls))           # observation: the Tell lines of crawl
    return sizes, log, (yield Get("fetched"))

PAGES = {"https://a.example": "<p>hello</p>", "https://b.example": ""}

@handler
def local_pages(effect: Fetch, k):                   # answers Fetch from PAGES
    return (yield Resume(k, PAGES[effect.url]))

program = local_pages(report(list(PAGES)))  # Fetch
program = try_handler(program)              # Try
program = listen_handler(program)           # Listen
program = writer(program)                   # Tell
program = state({"fetched": 0})(program)    # Get, Put
sizes, log, fetched = run(scheduled(program))  # Spawn, Gather

print(sizes)    # [Ok(12), Err(ValueError('empty page: https://b.example'))]
print(log)      # [Tell('fetched https://a.example')]
print(fetched)  # 1
```

`size`, `crawl` and `report` name the effects they need and nothing else. Which handler gives each
effect its meaning is decided where the program runs, and changing a handler does not change
them.

## Swapping I/O is one use

`Fetch` above is answered from a dict. A handler that calls an HTTP client answers it the same
way, and `size`, `crawl` and `report` stay as they are:

```python excerpt
import urllib.request

@handler
def live_pages(effect: Fetch, k):                    # answers Fetch over the network
    with urllib.request.urlopen(effect.url) as response:
        page = response.read().decode()
    return (yield Resume(k, page))
```

So a test answers I/O effects with fake handlers and production with real ones, and nothing is
patched: if no handler answers an effect, the run stops with `UnhandledEffect`. This is the use
most people meet first, and the README shows it at length
([Swap a handler](../../README.md#swap-a-handler-fakes-in-tests-real-io-in-production),
[Before and after](../../README.md#before-and-after-fakes-in-a-test)).

It is a use, not what doeff is. `research` does no I/O at all, and its handlers decide whether it
goes on, stops or fails. If swapping one implementation for another is all you need, dependency
injection is the simpler tool
([Isn't this just dependency injection?](../faq.md#isnt-this-just-dependency-injection)).

## Limits

### Continuations are one-shot

A handler resumes each `k` at most once. A second `Resume` of the same `k` raises a
`RuntimeError` (a one-shot violation). So the effects that resume one continuation several times
cannot be written as doeff handlers: backtracking search, nondeterministic choice, probabilistic
enumeration. A handler that tries to run the rest of the program once per option fails:

```python
from dataclasses import dataclass
from doeff import EffectBase, Resume, do, handler, run

@dataclass(frozen=True)
class Choose(EffectBase):              # "one of these": nondeterministic choice
    options: tuple

@do
def order():
    drink = yield Choose(("tea", "coffee"))
    return f"{drink} please"

@handler
@do(non_tail=True)
def every_option(effect: Choose, k):   # would resume k once per option
    answers = []
    for option in effect.options:
        answers.append((yield Resume(k, option)))
    return answers

try:
    run(every_option(order()))
except RuntimeError as error:
    print("one-shot violation" in str(error))  # True
```

What stands in for it is running the whole program again, once per choice. A program is a value,
so it can run any number of times. Add this below the example above:

```python continues
def choosing(index):                   # answers Choose with one fixed option
    @handler
    def on_choose(effect: Choose, k):
        return (yield Resume(k, effect.options[index]))
    return on_choose

print([run(choosing(index)(order())) for index in range(2)])  # ['tea please', 'coffee please']
```

Each run repeats the work before the choice. A handler that records the expensive effects and
replays them makes the repeated part cheap
([Class 3](../22-capability-classes.md#class-3--multi-shot-deliberately-absent)).

### Programs are Python generators

- **Only the body of a `@do` function can perform an effect.** A plain function it calls cannot:
  make that function a `@do` function too, and `yield` it. As with `async`, this spreads to the
  callers.
- **Calling a `@do` function only builds a program.** Without `yield` or `run()` it never runs,
  and nothing warns you.
- **`yield` cannot appear in a comprehension or a generator expression** (Python raises
  `SyntaxError`), so a program that yields in a loop writes the loop out, as `crawl` does.
- **A suspended generator cannot be copied.** `copy.copy` of one raises `TypeError` in CPython.
  The continuation holds the suspended generators of the program, which is one reason it cannot be
  resumed twice.
- **`@do` refuses an `async def` function.** Existing coroutines run through `yield Await(coro)`
  under `await_handler` ([How is it different from asyncio?](../faq.md#how-is-it-different-from-asyncio)).

### Other limits

- **Effects are not in the types.** The type checker does not check that the installed handlers
  answer every effect a program yields; a missing handler is an `UnhandledEffect` when the program
  runs ([Does type checking work?](../faq.md#does-type-checking-work)).
- **An effect costs about 3 to 5 µs of CPU.** Next to a network call that does not show; in a
  tight numeric loop it does, so keep such a loop in plain Python and yield effects around it
  ([How fast is it?](../faq.md#how-fast-is-it)).

## Where doeff sits

| | What a handler receives | Resume more than once | Effects in the types |
| --- | --- | --- | --- |
| [effect](https://github.com/python-effect/effect) 1.1.0 (Python, 2019) | the intent; a performer written without `sync_performer` also gets a box to answer through, now or later | no continuation | no |
| [effectful](https://github.com/BasisResearch/effectful) 0.4.0 (Python, 2026) | the operation's arguments; `fwd()` forwards them to the next handler out | no continuation | no; an operation with no handler returns a term |
| [OCaml 5](https://ocaml.org/manual/5.3/effects.html) | the effect and its continuation | no: [continuations are linear](https://ocaml.org/manual/5.3/effects.html#s:effects-linearity), and a second resume raises `Continuation_already_resumed` | no: [an unhandled effect raises `Effect.Unhandled`](https://ocaml.org/manual/5.3/effects.html#s:effects-unhandled) when it is performed |
| [Koka](https://koka-lang.github.io/koka/doc/book.html#sec-handlers) | the operation and `resume`, a first-class function | yes: [`resume` can be called more than once](https://koka-lang.github.io/koka/doc/book.html#sec-multi-resume), for backtracking and probabilistic programming | yes: the compiler infers the effects of every function |
| doeff 0.7.0 (Python) | the effect and its continuation `k` | no: a second `Resume` raises `RuntimeError` | no: a missing handler is an `UnhandledEffect` when the program runs |

- **effect** describes itself as "a library for helping you write purely functional code by
  isolating the effects (that is, IO or state manipulation) in your code". A performer answers an
  intent; it does not get the rest of the computation.
- **effectful** describes itself as "an algebraic effect system for Python, intended for use in
  the implementation of probabilistic programming languages". Programs are plain functions that
  call `@defop` operations, and a handler is an interpretation of those operations installed with
  `with handler(...)`.
- **OCaml 5** (since 5.0) is the closest: the handler receives the continuation, the
  continuation is linear (resumed with `continue` or `discontinue` exactly once), and effects are
  not checked by the types. Its deep handlers stay installed around the continuation, as doeff's
  do. A doeff handler may also drop `k` and end the computation with a value of its own.
- **Koka** tracks effects in the types and allows resuming one continuation many times. doeff has
  neither.

[How does it compare with other Python effect libraries?](../faq.md#how-does-it-compare-with-other-python-effect-libraries)
has the cost of one effect in each Python library, and how a test answers effects in each.

## How to describe doeff

The subject of the first sentence about doeff is the mechanism: effects, handlers and the
continuation. Uses come after it, named as uses. The words swap, fake, mock and real I/O belong to
the I/O use, not to the description of doeff.

| Do not write | Why | Write instead |
| --- | --- | --- |
| "Algebraic effects for Python: swap real and fake I/O without mocks." | Names one use as the whole. Most of what handlers do (budgets, early exit, errors, the scheduler) has no I/O in it. | "Algebraic effects for Python: your code yields effects, and handlers decide what they mean." |
| "A replacement for mocks", "mock-free testing", "test without mocks" as the headline | Tests are one consumer of handlers, and for swapping alone dependency injection is the simpler tool. | As a use, after the mechanism: "A test answers effects with handlers, so nothing is patched, and an effect nobody answers stops the run with `UnhandledEffect`." |
| "Your code yields its I/O", "yield I/O effects", "push I/O to the edges" | An effect is any operation a program names, not a wrapper around I/O. | "Your code yields effects: operations you define, such as `Spend(3)`, or ones a package ships, such as `Get("count")` and `Spawn(task)`. Fetching a page is one of them." |
| "Handlers are swappable implementations", "dependency injection with generators" | An injected object answers a call; it does not get the rest of the program. | "A handler receives each effect with its continuation, and decides whether the program goes on, with what, when, or not at all." |
| "Do-notation for Python", "a do-notation and effects system" | `@do` is the syntax a program is written in, not what doeff does. | "Algebraic effects for Python. Programs are generator functions under `@do`." |
| "Full algebraic effects, like Koka or OCaml 5" | Overclaims: continuations are one-shot (no backtracking or nondeterminism), and effects are not in the types. | "Algebraic effects with one-shot continuations, as in OCaml 5; no multi-shot handlers, and no effect types." |
| "Zero-cost", "as fast as plain Python" | An effect costs about 3 to 5 µs; a function call about 0.13 µs. | "About 3 to 5 µs per effect: invisible next to I/O, visible in a tight loop." |
