# r/Python: the showcase post for the first week

The text for the monthly showcase thread of r/Python, in the three sections the subreddit asks for:
what the project does, who it is for, and how it compares. The intro article link is filled in once
the post is out.

---

**doeff – algebraic effects for Python: stack time, concurrency, retries, budgets and I/O around one program**

## What My Project Does

doeff brings algebraic effects to Python. A function yields effects: operations you define, such as `Fetch(url)` or `Spend(1)`, or ones doeff's packages define, such as `Spawn` and `Gather`. A handler, a function marked with `@handler` (which makes it a `@do` function), receives each effect together with `k`, the rest of the function, and decides what happens: resume it with a value, throw into it, pass it on, keep it to continue later, or drop it. Because handlers get the rest of your program, time, concurrency, retries, budgets and I/O become layers you stack around the same code.

```python
from dataclasses import dataclass
from doeff import EffectBase, Resume, do, handler, run

@dataclass(frozen=True)
class Fetch(EffectBase):       # an effect: "get this page"
    url: str

@dataclass(frozen=True)
class Spend(EffectBase):       # an effect: "the next step costs this much"
    cost: int

@do
def crawl(urls):               # yields effects; knows nothing about HTTP or budgets
    sizes = []
    for url in urls:
        yield Spend(1)
        page = yield Fetch(url)
        sizes.append(len(page))
    return sizes

@handler
def fake_web(effect: Fetch, k):          # k: the rest of crawl, paused at the yield
    return (yield Resume(k, "<p>ok</p>"))

def budget(limit):             # one layer: stop when the budget runs out
    left = limit

    @handler
    def on_spend(effect: Spend, k):
        nonlocal left
        if effect.cost > left:
            return "over budget"         # drop k: crawl stops at this Spend
        left -= effect.cost
        return (yield Resume(k, None))   # resume k: crawl goes on

    return on_spend

URLS = ["a.example", "b.example", "c.example"]
print(run(budget(5)(fake_web(crawl(URLS)))))   # [9, 9, 9]
print(run(budget(2)(fake_web(crawl(URLS)))))   # over budget
```

`crawl` has no HTTP and no budget check of its own. `fake_web` answers each `Fetch`. `budget(2)` lets two `Spend`s through, then returns without resuming `k`, so the third page is never fetched. Swap `fake_web` for a handler over a real client and `crawl` does not change.

- More layers stack the same way. The intro article runs one program, which checks two pages every half hour, under five stacks: a test run with fake pages and a virtual clock, a budget, retries, a handler that records every effect, and production on the wall clock with an async client.
- Concurrency is effects too: `Spawn`, `Wait`, `Gather` and `Race`. Their reference handler, `scheduled`, is written like `budget` and keeps each waiting task's continuation for later, so the clock can be a handler too: the article writes a virtual clock of about 20 lines that runs half-hour waits in no time, and a wall clock on `asyncio` runs the same program for real.
- Effects are dispatched by a VM written in Rust, with one-shot continuations as in OCaml 5: no backtracking or nondeterministic choice. Prebuilt wheels cover Linux, macOS and Windows.
- Tests are one of the stacks: answer effects with fake handlers and nothing is patched. If a test forgets a fake, the run stops with `UnhandledEffect` instead of calling the real thing.
- `pip install doeff` installs 0.8.0 (Python 3.11–3.14, MIT), and every example in the article runs with it alone. `python -m doeff demo` runs a small program under a fake handler, then over real HTTP.

## Target Audience

- People curious about algebraic effects in the style of OCaml 5, without leaving plain Python generators.
- Python developers who want to control from outside a function how it runs: budgets and early exit, retries, logs, concurrency, a simulated clock, and tests that answer effects instead of patching.
- It is pre-1.0: the API still changes between minor versions. One maintainer uses it in their own projects. Treat it as something to try on a side path first, not as a drop-in for a critical system.

## Comparison

- **A custom event loop, mocks or decorators:** each gives you one of these layers. Effects give one mechanism for all of them, so they stack and swap without touching the program.
- **Dependency injection:** an injected object answers a call; a handler also gets the continuation, so it can resume it later, retry, batch or stop the run. For swapping one implementation alone, DI is simpler.
- **OCaml 5 and Koka:** like OCaml 5, continuations are one-shot and effects are not in the types; Koka has both multi-shot resumes and effect types.
- **effect (1.1.0, 2019):** a performer answers an intent and does not get the rest of the computation. Tests list the expected effects in order; a missing answer is an `AssertionError`.
- **effectful (0.4.0):** keeps your functions completely ordinary; a missing handler gives a symbolic term instead of an error.
- **returns:** typed containers, without resumable handlers. It solves a different problem.
- **Cost:** about 4 µs per effect through a `@handler` handler, against 0.13 µs for a plain function call. That does not show next to I/O; keep tight loops in plain Python.

What doeff Is: https://github.com/proboscis/doeff/blob/main/docs/positioning/what-doeff-is.md · Source: https://github.com/proboscis/doeff · FAQ: https://github.com/proboscis/doeff/blob/main/docs/faq.md · Intro article: [the URL of the post, filled in once it is out]
