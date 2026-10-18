# doeff Documentation

doeff brings algebraic effects and their handlers to Python. A function `yield`s an effect, and
the nearest handler for it receives the effect together with `k`, the continuation: the rest of the
function from that `yield` on. The handler decides what the effect means by what it does with `k`:
resume it with an answer, throw an error into it, pass it on, hold it while other tasks run, or
drop it and end the computation with a value of its own. State, exceptions, concurrency,
observation, budgets and I/O are all effects with handlers of this kind, under one mechanism in one
program; answering I/O effects one way in tests and another in production is one use of it.
Continuations are one-shot: a handler resumes each `k` at most once, so backtracking and
nondeterministic choice cannot be written as handlers. [What doeff Is](positioning/what-doeff-is.md)
defines all of this; this page is where the documentation starts.

## Start Here

Read these in order the first time:

1. **Install** - `pip install doeff` on Python 3.11 or later: see [Install](#install) below
2. **A first program** - the [Quick Example](#quick-example) below, then [Getting Started](01-getting-started.md)
3. **What doeff is** - [the mechanism, what it covers and where it stops](positioning/what-doeff-is.md)
4. **Questions** - the [FAQ](faq.md): what algebraic effects are, dependency injection, asyncio, speed, types, debugging, Hy, production use
5. **Why effects?** - [what a handler does with the rest of the program, example by example, and how that compares with dependency injection and plain `async`](20-why-effects-over-di.md)
6. **Examples** - an [LLM agent](../examples/llm_agent.py) and a [GitHub API client](../examples/github_repo/README.md); their tests answer the model and HTTP effects with handlers, without the network
7. **API** - the [API Reference](13-api-reference.md) and the [built-in effects and handlers](../packages/doeff-core-effects/README.md)

The [Table of Contents](#table-of-contents) below lists every guide.

## Install

```bash
pip install doeff          # Python 3.11+: the core (doeff, doeff-vm, doeff-core-effects)
pip install "doeff[hy]"    # + Hy and the handlers written in Hy (files, SQL, processes, cache, …)
pip install "doeff[http]"  # + the HTTP client handlers (Hy and httpx)
pip install "doeff[all]"   # both
```

## Quick Example

`pay` yields an effect of its own, `Withdraw`, and a built-in one, `Tell`. It does not know where
the money comes from or what happens when it runs out: the handler `account` decides, by resuming
`k` or by throwing an error into it at the `yield`:

```python
from dataclasses import dataclass
from doeff import EffectBase, Resume, ResumeThrow, do, handler, run
from doeff_core_effects import Tell
from doeff_core_effects.handlers import state, writer, writer_log

@dataclass(frozen=True)
class Withdraw(EffectBase):          # your own effect: "take this much from the account"
    amount: int

@do
def pay(bills):                      # yields Withdraw (yours) and Tell (built in)
    paid = []
    for name, amount in bills:
        try:
            yield Withdraw(amount)
        except ValueError as error:  # thrown in by the handler, at the yield
            yield Tell(f"skipped {name}: {error}")
            continue
        paid.append(name)
    return paid, (yield writer_log())

def account(balance):                # one meaning of Withdraw: an account with a balance
    @do
    def on_withdraw(effect: Withdraw, k):
        nonlocal balance
        if effect.amount > balance:
            return (yield ResumeThrow(k, ValueError("not enough money")))
        balance -= effect.amount
        return (yield Resume(k, None))

    return handler(on_withdraw)

bills = [("rent", 80), ("phone", 30), ("water", 15)]
print(run(state()(writer(account(100)(pay(bills))))))  # (['rent', 'water'], ['skipped phone: not enough money'])
```

The built-in handlers are written the same way as `account`: `writer` is a `@do` function of
`(effect, k)` that answers `Tell`, and it keeps its log by yielding `Get` and `Put`, which `state`
answers. Another handler could give `Withdraw` another meaning without changing `pay`: one that
records every withdrawal and passes it on, or one that drops `k` and ends the run at the first bill
it refuses.

## Table of Contents

The pages in this table need only the five core packages (`doeff`, `doeff-vm`,
`doeff-core-effects`, `doeff-hy`, `doeff-hy-pytest`). `pip install doeff` installs the first three;
add the extra `hy` (`pip install "doeff[hy]"`) for the pages that use Hy, the handlers written in
Hy or the cache, or `all` (`pip install "doeff[all]"`) for every page. The few examples
that also need `doeff-time` are named below and say so where they appear. Pages that need other
components are listed separately under
[Pages That Need Other Components](#pages-that-need-other-components).

### Getting Started

1. **[Getting Started](01-getting-started.md)** - Installation, first program, basic concepts
2. **[What doeff Is](positioning/what-doeff-is.md)** - Algebraic effects: what a handler does with the continuation, what one mechanism covers, where it stops (one-shot continuations), and how to describe doeff
3. **[FAQ](faq.md)** - Short answers to the first questions: dependency injection, asyncio, speed, types, debugging, Hy, production use
4. **[Why Effects?](20-why-effects-over-di.md)** - What a handler does with the continuation (drop it, pass it on, retry, route, record, hold it for the scheduler), compared with dependency injection and plain `async`, in examples that run as written
5. **[Core Concepts](02-core-concepts.md)** - Program, Effect, execution model, type system

### Effect Types

6. **[Basic Effects](03-basic-effects.md)** - Reader, State, Writer effects
7. **[Async Effects](04-async-effects.md)** - Gather, Spawn, Await for async operations
8. **[Error Handling](05-error-handling.md)** - Result, Try for error handling
9. **[Cache System](07-cache-system.md)** - Cache effects with policies and handlers
10. **[Advanced Effects](09-advanced-effects.md)** - Gather, concurrency patterns (the time effects need `doeff-time`)
11. **[Semaphore Effects](21-semaphore-effects.md)** - Create, acquire, and release permits with FIFO fairness

### Examples

- **[LLM agent](../examples/llm_agent.py)** - The model call as your own effect `Complete(prompt)`: an OpenAI-compatible client answers it in production, canned answers in [its pytest tests](../examples/test_llm_agent.py). It needs only `pip install doeff`, and its tests pytest besides
- **[GitHub API client](../examples/github_repo/README.md)** - Calls the GitHub REST API through the HTTP handler of `doeff[http]`; its tests answer the HTTP effects with a handler of their own, without the network. Run them with `pip install "doeff[http]" pytest`, then `pytest examples/github_repo`

### Integration & Advanced Topics

12. **[Kleisli Arrows](11-kleisli-arrows.md)** - Composition and automatic unwrapping
13. **[Patterns](12-patterns.md)** - Best practices and common patterns (five examples need `doeff-time`)
14. **[API Reference](13-api-reference.md)** - Complete API documentation

### CLI Tools

15. **[CLI Script Execution](15-cli-script-execution.md)** - Execute Python scripts with program execution results
16. **[Effect Boundaries](17-effect-boundaries.md)** - Effect vs escape architecture and runner boundaries
17. **[Effect Combinations](18-effect-combinations.md)** - Composition laws and interaction guarantees across effects

### Specialized Topics

- **[Program Architecture](program-architecture-overview.md)** - Runtime internals overview (partly out of date: the VM internals predate 0.5.0)
- **[Removed IO API](06-io-effects.md)** - Historical note for the removed `IO(...)` effect
- **[Removed run_program API](16-run-program-api.md)** - Historical note for the removed Python `run_program()` entrypoint

### Design Notes

- **[Static Typing](23-static-typing.md)** - Typed effects (`EffectBase[T]`), typed `@do` programs, typed handler answers, and runtime readers of the same types
- **[Rust Scheduler](25-rust-scheduler.md)** - The scheduler implemented in Python (default) and in Rust (opt-in): switching, the condition for making Rust the default, downstream extension points, measurements
- **[Capability Classes](22-capability-classes.md)** - A four-class taxonomy predicting where effects pay off, retry under one-shot continuations, and adoption anti-patterns
- **[CLI Architecture](cli-run-command-architecture.md)** - Run command pipeline design

## Pages That Need Other Components

These pages use components that `pip install doeff` does not install. Each page says at its top
which components it needs and how to install them.

`doeff-indexer` is on PyPI. `doeff-agents` is on PyPI only as an old 0.1.1 build that does not
import with doeff 0.5 or later. The other components are not on PyPI: install them from a checkout of
this repository, passing pip the path of every component the page names. pip takes everything
else from PyPI.

```bash
git clone --depth 1 https://github.com/proboscis/doeff.git
pip install doeff ./doeff/packages/doeff-time   # the paths the page names
```

### CLI auto-discovery and IDE plugins — `doeff-indexer`

- **[CLI Auto-Discovery](14-cli-auto-discovery.md)** - Automatic interpreter and environment discovery for `doeff run`
- **[MARKERS.md](MARKERS.md)** - Marker comments that `doeff-indexer` and the IDE plugins read
- **[IDE Plugins](ide-plugins.md)** - PyCharm and VS Code extensions (they also need the `doeff-indexer` command, built from the checkout)

### Coding-agent sessions — `doeff-agents`

- **[Agent Tutorial](19-agent-tutorial.md)** - Building an automated code review system
- **[MCP Tools](12-mcp-tools.md)** - Exposing `defk` functions as MCP tools to agents
- **[doeff-agents / session host](../packages/doeff-agents/README.md)** - Agent session management, the `doeff-sessionhost` host, and joining an agent control plane

### LLM and image providers — `doeff-llm`, `doeff-gemini`, `doeff-openai`, `doeff-openrouter`, `doeff-seedream`

These packages ship ready-made LLM effects and provider handlers. To call a model with only
`pip install doeff`, define the call as your own effect, as the [LLM agent example](../examples/llm_agent.py) does.

- **[Unified LLM Effects](llm_unified_effects.md)** - Provider-agnostic LLM effects and handler stacking
- **[Gemini Setup](gemini_client_setup.md)** - API key and ADC configuration
- **[Gemini Cost Hook](gemini_cost_hook.md)** - Custom cost calculation
- **[seedream.md](seedream.md)** - SeeDream integration

## What is doeff?

doeff is an **algebraic effects** system for Python. Effects are first-class operations that can be intercepted and handled by composable handlers. The runtime uses **one-shot continuations** — each effect invocation suspends, and its handler resumes it at most once or drops it — backed by a high-performance **Rust VM**.

Key characteristics:

- **Algebraic effects with handlers**: Define effects as data; a handler receives each effect with its continuation and decides what it means: resume it, throw into it, pass it on, hold it while other tasks run, or drop it
- **One mechanism**: State, exceptions, concurrency, observation, budgets and I/O are all effects and handlers, and stack around one program
- **One-shot continuations**: Each continuation resumes at most once (unlike multi-shot systems like Koka or Eff), so backtracking and nondeterministic choice cannot be written as handlers
- **Rust VM runtime**: High-performance effect handling and continuation management
- **Batteries-included handlers**: Reader, State, Writer, Scheduler, Result — ready to use
- **Plain generator functions**: A program is a generator function under `@do`; no new syntax
- **Stack-safe execution**: Trampolining prevents stack overflow
- **Type safety**: Full type annotations with `.pyi` files

## Learning Path

### For Beginners

1. Start with **[Getting Started](01-getting-started.md)** for installation and basics
2. Read **[Core Concepts](02-core-concepts.md)** to understand algebraic effects, handlers, and the execution model
3. Learn **[Basic Effects](03-basic-effects.md)** for Reader, State, Writer
4. Explore **[Error Handling](05-error-handling.md)** for robust programs

### For Intermediate Users

1. **[Async Effects](04-async-effects.md)** for concurrent operations
2. **[Cache System](07-cache-system.md)** for performance optimization
3. **[Kleisli Arrows](11-kleisli-arrows.md)** for elegant composition
4. **[Patterns](12-patterns.md)** for best practices

### For Advanced Users

1. **[Advanced Effects](09-advanced-effects.md)** for Gather, concurrency
2. **[Semaphore Effects](21-semaphore-effects.md)** for concurrency control
3. **[Effect Boundaries](17-effect-boundaries.md)** for architecture design
4. **[API Reference](13-api-reference.md)** for complete API details

## By Use Case

### Building Web Applications

- **[Async Effects](04-async-effects.md)** for HTTP requests
- **[Cache System](07-cache-system.md)** for response caching
- **[Error Handling](05-error-handling.md)** for request validation
- **[Patterns](12-patterns.md#architectural-patterns)** for layering, repositories, and units of work

### Data Processing Pipelines

- **[Basic Effects](03-basic-effects.md)** for state management
- **[Async Effects](04-async-effects.md)** for parallel processing
- **[Error Handling](05-error-handling.md)** for retry logic
- **[Patterns](12-patterns.md#performance-patterns)** for parallel fetching and batching

### CLI Applications

- **[Basic Effects](03-basic-effects.md)** for configuration and local state
- **[Async Effects](04-async-effects.md)** for external work and concurrency
- **[Error Handling](05-error-handling.md)** for validation
- **[Effect Boundaries](17-effect-boundaries.md)** for observability and auditing

### Testing

- **[Patterns](12-patterns.md#testing-patterns)** for testing strategies
- **[Error Handling](05-error-handling.md)** for error simulation

## Effect Quick Reference

| Category | Effects | Source |
|----------|---------|--------|
| **Reader** | `Ask`, `Local` | `doeff_core_effects` |
| **State** | `Get`, `Put` | `doeff_core_effects` |
| **Writer** | `Tell`, `slog`, `Listen` | `doeff_core_effects` |
| **Error** | `Try` | `doeff_core_effects` |
| **Scheduler** | `Spawn`, `Wait`, `Gather`, `Race`, `Cancel` | `doeff_core_effects.scheduler` |
| **Promise** | `CreatePromise`, `CompletePromise`, `FailPromise` | `doeff_core_effects.scheduler` |
| **Semaphore** | `CreateSemaphore`, `AcquireSemaphore`, `ReleaseSemaphore` | `doeff_core_effects.scheduler` |
| **Cache** | `MemoGet`, `CachePut` | `doeff_core_effects.cache_effects` |

## Common Patterns

These are excerpts: they leave out their imports (`Ask`, `Put`, `Tell`, `Try` and `Ok` from
`doeff_core_effects` and `doeff`, `Spawn` and `Gather` from `doeff_core_effects.scheduler`) and
call programs your application defines (`do_work`, `risky_operation`, `process_item`).

### Configuration + State + Logging

```python excerpt
@do
def application():
    config = yield Ask("config")
    yield Put("status", "running")
    yield Tell(f"Started with config: {config}")
    result = yield do_work()
    return result
```

### Concurrency + Error Handling

```python excerpt
@do
def robust_workflow():
    result = yield Try(risky_operation())
    if isinstance(result, Ok):
        return result.value
    else:
        yield Tell(f"Failed: {result.error}")
        return None
```

### Parallel Processing + Aggregation

```python excerpt
@do
def process_batch(items):
    spawned = []
    for item in items:
        task = yield Spawn(process_item(item))
        spawned.append(task)
    results = yield Gather(*spawned)
    return {"processed": len(results), "results": results}
```

## Links

- **[GitHub Repository](https://github.com/proboscis/doeff)**
- **[PyPI Package](https://pypi.org/project/doeff/)**
- **[Issue Tracker](https://github.com/proboscis/doeff/issues)**

## Contributing

- Ask a question, report a bug or propose a feature: [open an issue](https://github.com/proboscis/doeff/issues/new/choose)
- Find a small task to start with: [good first issues](https://github.com/proboscis/doeff/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22)
- Set up the development environment, run the tests and send a pull request: [CONTRIBUTING.md](https://github.com/proboscis/doeff/blob/main/CONTRIBUTING.md)

## License

MIT License - see [LICENSE](https://github.com/proboscis/doeff/blob/main/LICENSE) for details.

## Author

Proboscis (3684241+proboscis@users.noreply.github.com)

This project evolved from earlier internal prototypes.
