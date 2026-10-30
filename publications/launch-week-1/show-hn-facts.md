# Show HN: facts for the maintainer to write from

Material for the Show HN title, text and replies, which the maintainer writes in their own words:
Hacker News asks that posts and comments not be written or edited by AI, so nothing here is pasted
as it is. Numbers were re-read on 2026-10-27 (JST) unless a row says otherwise.

---


## What it is

- Tagline for the launch posts (decided 2026-10-20): "Algebraic effects for Python: handlers get the rest of your program, so time, concurrency, retries, budgets and I/O become layers you stack around the same code."
- The picture to lead with: one program, and control added around it one layer at a time, then rearranged. The article's `monitor` runs under five stacks: a test run, a budget, retries, a recorder, and production on the wall clock.
- What doeff Is, the page every description draws on, opens with: "doeff is algebraic effects for Python. A program yields effects, and a handler receives each effect together with k, the continuation: the rest of the program from that yield on. What the handler does with k decides what the effect means. State, exceptions, concurrency, observation, budgets and I/O are all effects with handlers of this kind, in one program, under one mechanism. Continuations are one-shot: a handler resumes each k at most once."
- README tagline today: "Algebraic effects for Python: your code yields effects, and handlers decide what they mean."
- What a handler can do with `k`: answer and run the rest (`Resume`), answer and be done (`Transfer`), throw into it (`ResumeThrow`), pass the effect on (`Pass`), keep it and continue it while handling a later effect, or drop it and end the run (`return value`).
- A handler is a function of `(effect, k)` marked with `@handler`, which makes it a `@do` function; called on a program, it installs itself around it. Every example in the drafts writes handlers this way, with `@handler` alone (`@handler` over `@do` is deprecated).
- There are no built-in or special handlers. doeff's packages ship reference handlers (`scheduled`, `state`, `await_handler` and others), written the same way as your own, and any of them can be replaced.
- Effects are dispatched by a VM written in Rust (a PyO3 extension). Prebuilt wheels exist for Linux, macOS and Windows.
- Continuations are one-shot, like OCaml 5: a second `Resume` of the same continuation raises `RuntimeError`. Effects are not in the types.

## The layers, in the order the article stacks them

1. Answers for a test run: pages from a dict, log lines to stdout, and a virtual clock, so half an hour takes no time and the order is the same on every run.
2. A budget: the handler drops `k` when the budget is spent, so that task stops at its `Spend` and the other task finishes. The program has no `if` for it.
3. Failures and retries: a retry handler performs the effect again (`yield effect`) and logs each failure with an effect of its own.
4. Watching every effect: a handler without an annotation sees every effect and passes it on with `Pass`. The same shape records and replays LLM calls (`examples/testing_without_mocks/record_replay.py`).
5. The wall clock and real I/O: doeff-time's `async_time_handler` waits on `asyncio`, and an async client goes through `Await`, which `await_handler` answers. Only the client is `async`; the program stays a plain generator function.

- Underneath all of them: `Spawn`, `Wait`, `Gather`, `Race` and `Cancel` are effects, and their reference handler `scheduled` is written like any other handler: it keeps each waiting task's `k` and transfers to it when the task can go on. That is why the clock can be a handler.
- Each layer alone has a familiar tool: a mock, a fake clock, a retry decorator, a custom event loop. What effects add is one mechanism for all of them, so they stack and swap without touching the program.
- What it suits: trading programs, simulations and backtests. The same program can run over recorded market events with a simulated clock and paper orders, and live with the wall clock and an exchange, by swapping the handlers for time, market data and orders.
- Where it does not pay off (the FAQ says so): code that only ever runs one way, and CPU-bound hot loops. For swapping one implementation alone, dependency injection is simpler.

## doeff-time

| Item | Value | Source |
|---|---|---|
| What | time effects `Delay` and `GetTime`, and three handlers: `sim_time_handler(start_time=...)` (a virtual clock), `async_time_handler()` (the wall clock on `asyncio`), `sync_time_handler()` (the wall clock, blocking the thread) | packages/doeff-time |
| Version | 0.1.0; depends only on doeff (`doeff>=0.8.0,<0.9`) | packages/doeff-time/pyproject.toml |
| PyPI | 0.1.0, uploaded 2026-10-27 04:03 JST; needs doeff 0.8.x | PyPI, packages/doeff-time/pyproject.toml |
| Virtual clock | moves only when every task is waiting, then jumps to the earliest wake-up | packages/doeff-time, docs/20-why-effects-over-di.md |
| Concurrency | `sim_time_handler` and `async_time_handler` wake concurrent tasks in the same order; `sync_time_handler` blocks the thread during each wait, so in a concurrent example the order differs | runs on doeff 0.7.0, 2026-10-19 (not re-run on 0.8.0) |

## Not to claim

- No handler in doeff's packages runs spawned tasks on threads, processes, Ray or actors today; an earlier one was removed in January 2026 (commit bb4ea41ea). A handler inside `scheduled()` does receive `Spawn`, so where a task runs is decided in a handler, not in the program.
- `Spawn` takes a priority (0, 5, 10 or 20), but in a small test on 0.7.0 (not re-run on 0.8.0) it did not change the order of two tasks. Do not present priorities as a working feature yet.

## Numbers

| Item | Value | Source |
|---|---|---|
| PyPI | `doeff` 0.8.0, uploaded 2026-10-27 04:01 JST (`pip install doeff` installs it) | CHANGELOG, PyPI |
| Version the drafts need | 0.8.0: 0.7.0 refuses a generator `def` under `@handler` alone. Every example of the post, the translation and the r/Python text ran on 0.8.0 from PyPI (Python 3.11, 3.12 and 3.14) | CHANGELOG, runs on 2026-10-27 |
| Python | 3.11–3.14; CI also runs 3.14t (free-threaded) as experimental | pyproject.toml, CI workflows |
| License | MIT | LICENSE |
| Default install | 4 distributions (doeff, doeff-vm, doeff-core-effects, frozendict), about 3.6 MB | a fresh Python 3.12 venv on Linux, 2026-10-27 |
| Packages in the repo | 39; doeff, doeff-vm, doeff-core-effects and doeff-hy are on PyPI at 0.8.0, and doeff-time at 0.1.0 | packages/, PyPI |
| Rust VM | about 15,400 lines (doeff-vm 7,664 + doeff-vm-core 7,783) | packages/doeff-vm (counted for the 2026-10-20 draft, not re-counted) |
| Tests (grep counts) | about 5,262 Python, 3,131 Hy, 1,246 Rust | repo (counted for the 2026-10-20 draft, not re-counted) |
| History | first commit 2025-09-16; 5,978 commits up to the v0.8.0 tag; one author (the name in two spellings) | git log |
| GitHub stars | 2 (launch target 100) | GitHub API, 2026-10-27 |
| Cost per effect | 4.3 µs through a `@handler` handler with `Resume`, the only form a handler takes; a plain function call is 0.13 µs | README Performance |
| Other libraries | effect 1.1.0: 12.7 µs; effectful 0.4.0: 57.9 µs | README Performance |
| Cost per task | asyncio 11.4 µs; doeff `scheduled()` (Rust, the default since 0.7.0) 16.6 µs; the Python scheduler (opt-in) 151 µs | README Performance |

- Benchmarks: CPython 3.14.7, i9-7980XE, shared machine, 50 samples, script `benchmarks/compare_libraries.py`, measured 2026-10-17.

## Links

| What | URL |
|---|---|
| What doeff Is | https://github.com/proboscis/doeff/blob/main/docs/positioning/what-doeff-is.md |
| GitHub | https://github.com/proboscis/doeff |
| PyPI | https://pypi.org/project/doeff/ |
| Docs index | https://github.com/proboscis/doeff/blob/main/docs/index.md |
| FAQ | https://github.com/proboscis/doeff/blob/main/docs/faq.md |
| Why effects over DI | https://github.com/proboscis/doeff/blob/main/docs/20-why-effects-over-di.md |

- CI builds a docs site from `mkdocs.yml`, but it is not hosted yet (GitHub Pages is off). Link the GitHub pages.

## A short example (the r/Python one): two layers around one program

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

- `crawl` never changes: `fake_web` answers `Fetch`, `budget` decides whether the next step runs. The README's `digest` example (a fake handler, then live HTTP) is the I/O use; `python -m doeff demo` runs it.

## Short answers to likely questions

| Question | Short answer | Source |
|---|---|---|
| Can't I do this with a custom event loop, or my own `__await__` and `run`? | For one of these layers, yes. A custom loop gives you scheduling; a mock gives you fakes; a decorator gives you retries. Effects give one mechanism for all of them, so a budget, retries, a recorder and a clock stack around the same program and swap one at a time. | the article, section "One program, layer by layer" |
| Isn't this just DI or mocks? | An injected object answers a call; a handler gets the continuation and decides whether the program goes on, with what, when, or not at all. For swapping one implementation alone, DI is simpler. | What doeff Is, docs/faq.md |
| Is it full algebraic effects, like Koka? | One-shot continuations as in OCaml 5: no backtracking or nondeterministic choice, and effects are not in the types. | What doeff Is |
| How is it different from `effect`, `effectful`, `returns`? | An `effect` performer answers an intent and does not get the rest of the computation. `effectful` handlers get the operation's arguments, and a missing handler returns a symbolic term. `returns` is typed containers without resumable handlers. | What doeff Is, docs/faq.md |
| What is the overhead? | About 4 µs per effect. It does not show next to I/O; keep tight loops in plain Python. | README Performance |
| Does it support async? | `@do` refuses `async def`. Use `yield Await(coro)` under `await_handler`. From an asyncio app, call `run()` through `asyncio.to_thread`. | docs/faq.md, docs/04-async-effects.md |
| Does it type-check? | `EffectBase[str]` with `yield from` gives typed values under pyright strict and `mypy --strict`. Whether every effect has a handler is not checked statically. | docs/faq.md, README |
| Do I need Hy or Rust? | No. The core install has no Hy, and prebuilt wheels cover the main platforms. The file, SQL and HTTP handlers need the `[hy]` or `[http]` extra. | docs/faq.md |
| Is it production ready? | It is pre-1.0 and the API still breaks between minor versions. One maintainer uses it in their own projects. | docs/faq.md, CHANGELOG |
| How much of the code was made with an AI assistant? | 4,211 of the 5,978 commits up to v0.8.0 carry a `Co-Authored-By: Claude` line. Answer this one in your own words. | git log |
