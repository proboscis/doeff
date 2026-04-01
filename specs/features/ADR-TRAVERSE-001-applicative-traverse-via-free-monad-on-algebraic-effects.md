# ADR-TRAVERSE-001: Applicative Traverse via Free Monad on Algebraic Effects

**Status:** Accepted  
**Date:** 2026-04-01  
**Context:** doeff-traverse package design

## Fundamental Insight

doeff-traverse solves a fundamental limitation of algebraic effects with single-shot continuations: **multi-shot computation patterns**.

A single-shot continuation can only be resumed once. But many useful patterns require applying the same computation to multiple inputs:

- **List traversal** — apply f to each of N items (need N "copies" of the continuation)
- **Retry** — re-run the same body on failure (need fresh copy each attempt)
- **Backtracking** — try alternative branches (need to re-enter from a choice point)
- **Nondeterminism** — explore all possibilities (need unbounded copies)

All of these are "multi-shot" patterns that a single-shot VM cannot express directly.

**The solution: CPS macro expansion constructs Free Monad nodes where the continuation is a function (thunk), not a captured VM continuation.** Calling the thunk creates a fresh generator each time, bypassing single-shot. The handler (Free Monad interpreter) can call it as many times as needed.

List traversal is the most common instance of this pattern. doeff-traverse is the first application, but the same CPS + thunk technique generalizes to retry, backtracking, and any computation that requires "re-entering" a body multiple times.

## Practical Problem

Batch LLM pipelines require three concerns that are currently hard-coded at call sites:

1. **Error recovery** — retry, normalize, fail-fast (e.g., `_llm_query_with_retry` copy-pasted 6+ times in mediagen)
2. **Parallelism** — sequential vs parallel execution (manual Spawn+Gather boilerplate)
3. **Compute backend** — async, sync, batch API, mock (hard-coded Await calls)

Existing frameworks (Promise.all/allSettled, asyncio.gather, ZIO.foreach/partition) hard-code the strategy at the call site because the return type differs between strategies (e.g., `list[T]` vs `list[Result[T]]`).

## Decision

### Free Monad on top of Algebraic Effects

doeff's VM provides algebraic effects with single-shot continuations. Single-shot means a continuation can only be resumed once — you cannot apply the same continuation to each item in a list.

The `traverse` macro solves this by **CPS-converting** the body into a function (thunk), creating a **Free Monad node** (`Traverse(f, items)`) that the handler interprets:

```hy
;; User writes:
(traverse
  (<- x (Iterate items))
  (<- y (some-effect x))
  [x y])

;; Macro expands to Free Monad node:
(Traverse (fn [x] (do (<- y (some-effect x)) [x y])) items)
```

The function `f` is not a continuation — it's a **continuation factory**. Each call to `f(item)` creates a fresh generator, bypassing the single-shot constraint. This is the key insight: **macros construct Free Monads that overcome the single-shot limitation of the underlying algebraic effects system**.

### Handler = Interpreter

The `Traverse` effect is interpreted by a handler that decides:

- **Execution order**: sequential (for loop) or parallel (Spawn+Gather with semaphore)
- **Failure strategy**: fail-fast (exception propagates) or run-all (per-item Try, collect failures)

These two concerns live in the same handler because they share the same loop. This is not a design flaw — no existing framework separates them either. The difference is that in doeff, the handler is **external to the logic**, not hard-coded at the call site.

### Opaque Results

The return type of `Traverse` is handler-dependent. To allow strategy swapping without changing user code, traverse results are opaque:

- User code accesses results only via `traverse` (Iterate), `fold`, `Zip`, `Inspect`
- Handler decides the concrete type internally
- At the `run` boundary, results resolve to concrete values

This is why opaque collections exist: they hide the `list[T]` vs `list[Result[T]]` distinction so strategies are interchangeable.

### Computation Backend as Effect

The computation inside each item is declared as an effect (e.g., `LLMStructuredQuery`, `FetchPrice`), not as a direct implementation. A separate handler provides the backend:

```hy
;; Logic declares WHAT, not HOW
(defk process [x]
  (<- result (LLMStructuredQuery ...))
  result)

;; Handler decides HOW
(run (with-stack [async-llm-backend (parallel 10)] program))
(run (with-stack [mock-backend (sequential)] program))
```

### Program as Data

Programs are constants. `(pipeline items)` returns a computation graph (Expand node), not a result. Strategy is applied externally. `run` is the only execution point:

```hy
(setv program (pipeline items))
(run (with-stack strategy-a program))
(run (with-stack strategy-b program))
```

## Architecture

```
┌─────────────────────────────────────────────────┐
│  User code (Hy)                                 │
│  traverse + Iterate + fold + Fail + effects     │
└────────────────────┬────────────────────────────┘
                     │ macro expansion (CPS)
┌────────────────────▼────────────────────────────┐
│  Free Monad layer                               │
│  Traverse(f, items) — f is continuation factory  │
│  Reduce(f, init, collection) — fold             │
│  Zip(a, b) — join                               │
└────────────────────┬────────────────────────────┘
                     │ handler interprets
┌────────────────────▼────────────────────────────┐
│  Algebraic Effects (doeff VM)                   │
│  single-shot continuations, handler stacking    │
│  Spawn/Gather, Await, Fail                      │
└─────────────────────────────────────────────────┘
```

## User Vocabulary (Minimal)

| Syntax | Purpose |
|--------|---------|
| `(traverse (<- x (Iterate items)) body)` | Batch processing |
| `(fold collection :init v body)` | Aggregation (`acc` and `it` are implicit) |
| `(Zip a b)` | Join two traverse results |
| `(Inspect collection)` | Post-hoc analysis with per-item history |
| `(Fail cause)` | Failure notification at yield site |
| `(try-call f args)` | Wrap plain function for Fail |
| `(fnk [params] body)` | Anonymous kleisli arrow |

## Consequences

### Positive

- Error handling, parallelism, and backend are fully separated from logic
- Same program runs with any combination of handlers
- `traverse` replaces 4 existing macros (do-list, do-list-try, do-try-list, do-dict-try)
- Spawn+Gather boilerplate eliminated
- Confirmed 10x speedup by swapping `(sequential)` → `(parallel 10)`
- Per-item failure tracking with traceback, automatic across stages

### Negative

- Collection type adds indirection (may be replaced by pure Free Monad in future)
- Handler ordering matters and can be confusing
- `_doeff_traverse_Traverse` / `_doeff_traverse_Reduce` imports required for macro expansion
- Existing code (mediagen, proboscis-ema) not yet migrated

### Future

- **doeff-free**: extract common Free Monad machinery if retryable region shows same pattern
- **Retryable region**: body factory + retry loop, same CPS pattern as traverse
- **Batch API optimization**: handler that collects all LLM calls and submits as one batch
- **Static analysis**: Free Monad structure can be inspected before execution (count calls, estimate cost)

## References

- Algebraic effects: OCaml 5 effect handlers, Koka, Eff
- Free Monad: Haskell `free` package, Scala Cats Free
- Applicative traverse: Haskell `Traversable` typeclass
- CPS conversion: continuation-passing style as macro transformation
- Condition/restart: Common Lisp condition system (closest to Fail + handler)
