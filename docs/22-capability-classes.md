# Capability Classes: Predicting Where Effects Pay Off

[Why Effects Over DI](20-why-effects-over-di.md) argues the case for algebraic effects by example. This document gives the underlying theory: a classification that predicts — *before you write any code* — whether a proposed handler is trivial, cheap, powerful, or impossible in doeff. It was distilled from an evidence audit across every downstream project consuming doeff, including the places where adoption paid off and the places where it was pure ceremony.

## The Classification Axis

Everything a handler can be classified by one question:

> **What does the handler demand of the continuation `k`?**

Four classes emerge. Each class strictly contains the previous one, and each step up buys new power and new cost.

| Class | What happens to `k` | Members | DI equivalent? |
|-------|--------------------|---------|----------------|
| 0 — Dispatch | Resumed once, immediately; operation performed as-is | provider routing, mocks, secret backends | Yes |
| 1 — Data middleware | Resumed once, immediately; effect inspected/transformed/elided/repeated first | memo, replay, budget, retry, tracing, dry-run, rewrite | Mostly (decorators + contextvars) |
| 2 — Scheduling | `k` escapes the handler: parked, reordered, interleaved | Spawn/Wait, virtual time, rate limiting, batching, dedup, hedging | **No** |
| 3 — Multi-shot | `k` resumed more than once | backtracking, nondeterminism, probabilistic branching | No — **and not doeff either** |

## Class 0 — Dispatch

The handler performs the operation and resumes `k` once with the result. Routing by model prefix, swapping a mock for a production client, choosing a secret backend — all class 0.

Be honest about this class: **it is the same runtime power as dependency injection.** No continuation is needed at all; a function call would do. What doeff adds is *composition-site* selection — the same Program runs under different stacks per run, per test, per dynamic scope (`Local`) — instead of one object graph fixed at construction time. That is valuable, but if class 0 is all you need, DI is a legitimate and simpler alternative.

## Class 1 — Data Middleware

Same contract as class 0 — `k` is resumed exactly once, immediately — but the handler does something *around* the single resume:

- **inspect** the effect as data (cache keys, structured logs),
- **accumulate state** across heterogeneous effects (budget/cost tracking),
- **elide** the operation and substitute a value (cache hit, dry-run),
- **transform** the effect before delegating (model rewriting),
- **repeat** the operation before resuming (retry).

The required machinery is: effects-as-data (frozen dataclasses give canonical keys for free), handler state, and *re-dispatch* — performing an effect against the rest of the stack and capturing the result as a value. Re-dispatch is not hypothetical; it is the production-proven core of the memo handler ("on memo miss: re-perform effect → outer handler handles it → store result").

**Consequence: the implementability of any class 1 handler is a corollary, not a research question.** A generic retry handler, a budget cap, a replay handler — if you can state it as "do X around a single resume," it is buildable with existing primitives.

### Retry under one-shot continuations (the correct formulation)

doeff continuations are one-shot: each `k` resumes exactly once. A retry handler must therefore **never** loop over `Resume(k, ...)`. The correct shape is:

```python
@do
def retry_handler(effect: Effect, k):
    last_err = None
    for attempt in range(3):
        safe = yield Try(effect)      # re-YIELD the effect: fresh continuation per attempt
        if safe.is_ok():
            return (yield Resume(k, safe.value))   # k resumed exactly once
        last_err = safe.error
    raise last_err
```

Retrying the *operation* is class 1. Retrying the *continuation* (re-running the rest of the program after a downstream failure) would require multi-shot and is impossible by design — see class 3 for the sanctioned approximation.

### Honest comparison: class 1 vs decorators

Python already has a class 1 mechanism: decorator stacks, with `contextvars` bolted on when dynamic scoping is needed (the ecosystem converged on `contextvars` precisely because wrapping alone can't scope state to an execution). Three deltas remain in doeff's favor:

1. **Matching unit.** A decorator wraps one callable, by name, at definition time. A handler matches an effect *vocabulary* (a type) across the whole dynamic extent — including operations performed by code that didn't exist when the handler was written. "One budget across all LLM calls" needs every call site decorated, or all calls funneled through one function; with effects it is one handler.
2. **Composition scope.** Decorator composition is fixed per process. Handler stacks recompose per run and per subtree.
3. **Observable seam.** What happened inside a decorator is invisible to tests without monkeypatching. Effects are data: a test asserts cache behavior by counting intercepted effects.

If none of these three deltas matters for your system, decorators are the cheaper tool.

## Class 2 — Scheduling

Here the power jumps: **`k` escapes the handler's dynamic extent.** It is stored as a value, parked, resumed later, out of order, interleaved with other continuations. Virtual time, deterministic simulation, structured concurrency (`Spawn`/`Wait`), and cooperative schedulers all live here.

This is the class dependency injection **structurally cannot reach** — DI has no continuations at all; the "program" is the call stack itself and evaporates as it runs. If your system needs class 2, effects (or an equivalent runtime) are not a preference but a requirement. One-shot continuations are sufficient: each `k` is still resumed exactly once, just *later*.

### Non-obvious members of class 2

The classification earns its keep by making non-obvious predictions. These features look like "caching relatives" but are actually schedulers, because they must park a continuation:

- **Batching / coalescing**: effect A's continuation waits until B and C arrive so one batched call can serve all three.
- **In-flight deduplication**: the second identical request parks until the first completes, then both resume with the same value.
- **Hedged requests**: launch two, resume with the winner, cancel the loser.
- **Rate limiting done right**: a token-bucket handler that parks continuations until the bucket refills — *preventing* 429s upstream instead of retrying them (class 1) after the fact.

Treat these with scheduler-grade care: class 2 is also where the runtime's cost concentrates. Parked continuations are live objects; their lifecycle (leaks, cancellation, ordering semantics) is the hardest part of the system. Budget review time accordingly.

### The function-color dividend

A class 2 side benefit: because time and concurrency are effects, a Program does not know whether it runs on a real clock or a simulated one, on asyncio or synchronously. The sync/async color distinction — Python's most viral type split — is externalized into the interpreter, and one sequencing syntax (`yield` / `<-` in Hy) expresses everything from a config read to a spawned task.

Be honest about the trade: doeff does not erase colors, it **unifies them into one new color** — Program vs plain value. Forgetting to bind a Program builds a computation that silently never runs; over-binding a plain value fails at runtime. Unlike `async`/`await`, this color is not enforced by the interpreter's syntax. In Hy, the macro layer closes most of this gap: `defk`/`do!`/`defp`/`deftest` wrap statement-position expressions in a runtime guard that raises immediately (with a fix template) when a bare Program/EffectBase is about to be silently discarded (ADR-DOE-HY-001). Return-position composition remains legal; nested control-form bodies are not yet guarded — treat those as a review point.

## Class 3 — Multi-shot (deliberately absent)

Resuming `k` more than once enables backtracking search, nondeterminism, and probabilistic branching. doeff excludes this class by design (one-shot continuations, unlike Koka or Eff). There is no hidden reserve of power here; the ceiling is explicit.

The sanctioned approximation composes classes you already have: **re-execute the whole Program with a memoized prefix.** Program-as-value means the computation can always be run again; a class 1 memo/replay handler makes the re-run deterministic and fast up to the branch point. What-if analysis and branch exploration are therefore expressible — as re-execution, not as continuation copying.

## When Adoption Pays: The Evidence Rule

Across every audited downstream project, one rule explains the outcomes:

> **Effects pay when (a) you need to manipulate call boundaries at composition/run time, and (b) the per-call cost of those boundaries dwarfs the interception overhead.**

Concretely:

- **Strong fits** (both factors high): running one Program under simulated/paper/live interpreters; record/replay of expensive calls (LLM, market data) with fail-on-miss guarantees; hermetic test suites over I/O-heavy pipelines. These were, empirically, the load-bearing wins.
- **Poor fit — small single-context apps** (factor *a* low): if the code will only ever run one way, the effect vocabulary is ceremony. Projects that adopted doeff as a template, without an interpreter-swapping problem, ended up deleting or bypassing it.
- **Poor fit — CPU-bound hot loops** (factor *b* inverted): interception overhead is per-effect. Historical note: an earlier doeff version captured creation context on every effect and was measured at a multiple-x slowdown in a tight simulation loop; current doeff captures trace context only via opt-in effects (`GetTraceback`/`GetExecutionContext`) and on error paths, with line-text resolution deferred to render time — a regression guard pins this property (ADR-DOE-CORE-002). The residual per-effect cost is dispatch itself (VM core plus `@do` generator re-construction glue), so the guidance stands: move sub-millisecond hot paths below the effect boundary (e.g., into a native extension), keeping effects for orchestration.

## An Adoption Anti-Pattern: Building Power Faster Than Using It

The recurring failure mode observed downstream is not misuse of effects but **unexercised capability**: test machinery wired but never adopted, enforcement rules written but never run in CI, documented handlers with zero call sites. Capability that isn't exercised silently decays and gives false confidence.

Practical guidance:

1. Adopt by *consuming* existing handlers before writing new ones. Most needs are class 0/1 and already covered.
2. If you write a class 1 handler, port at least one real call site to it in the same change — a handler with no consumer is a claim, not a feature.
3. If you write a class 2 handler, wire its invariant checks into CI *first*. Scheduler-grade power without scheduler-grade guards is how production incidents happen.
