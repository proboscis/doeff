# doeff-traverse

Collection comprehension as algebraic effects — multi-shot computation via CPS macro expansion.

## What this solves

doeff's VM has single-shot continuations: a continuation can only be resumed once. But many useful patterns need to apply the same computation to multiple inputs — list traversal, retry, backtracking. These are all "multi-shot" patterns.

doeff-traverse solves this with a CPS macro that converts the computation body into a **thunk (function)**, not a captured continuation. The handler calls the thunk as many times as needed, creating a fresh generator each time. This is a Free Monad node interpreted by the handler.

List traversal is the most common instance of this pattern.

## What it looks like

```hy
(require doeff-hy.macros [defk <- for/do fold])

;; Pipeline logic: no error handling, no parallelism, no backend choice
(defk pipeline [items]
  ;; FROM + WHERE + SELECT — like SQL, but monadic
  (<- features
    (for/do
      (<- item (From items :label "extract"))
      (<- ok (validate item))
      (When ok)
      (<- result (try_call extract-feature item))
      result))

  ;; Fold: aggregate valid items
  (<- #(total count)
    (fold features :init #(0 0)
      #((+ (get acc 0) it) (+ (get acc 1) 1))))

  ;; SortBy + Take: top-N pattern
  (<- sorted (SortBy score features :reverse True))
  (<- top-10 (Take 10 sorted))
  top-10)

;; Program is data — not yet executed
(setv program (pipeline items))

;; Strategy is handler — swap freely
(run (with-stack [backend fail_handler (sequential)] program))
(run (with-stack [backend fail_handler (parallel 10)] program))
```

Same program, different strategies. Handler decides:
- **Execution order**: `(sequential)` or `(parallel N)`
- **Failure strategy**: fail-fast or per-item isolation
- **Compute backend**: async, sync, mock, batch API

A `Traverse` handler runs each item program under itself: with nested `From` binds an item program performs `Traverse` again to the same handler. A handler must not hold a resource shared across `Traverse`s (one semaphore for the whole handler) while an item runs, or nested `From` deadlocks. `(parallel N)` bounds each `Traverse` separately, so k nested `From` binds can run up to N^k leaf items at once — bound a leaf resource (an API call) in the handler of that effect. `tests/test_for_do_runtime.py::test_nested_froms_answer_under_every_shipped_handler` runs every shipped handler on nested `From`.

## SQL analogy

The `for/do` comprehension reads like SQL:

```
for/do                              SQL
------                              ---
(<- item (From items))              FROM items
(When predicate)                    WHERE predicate
(<- result (process item))          SELECT process(item)
```

Multiple `From` binds nest (like SQL `CROSS JOIN`): the answer has one item per combination, e.g. `(for/do (<- a (From [1 2])) (<- b (From [10 20])) (+ a b))` gives `11 21 12 22`. `When` guards can appear at any nesting level. All binds are kleisli (effectful).

### Pure vs effectful predicates

```hy
;; Pure predicate — inline expression
(for/do
  (<- item (From items))
  (When (> item 0))
  (* item 10))

;; Effectful predicate — bang (!) inlines the bind
(for/do
  (<- item (From items))
  (When (! (validate item)))     ;; validate is a defk, returns DoExpr
  (<- result (process item))
  result)

;; Equivalent explicit form (no bang)
(for/do
  (<- item (From items))
  (<- ok (validate item))
  (When ok)
  (<- result (process item))
  result)
```

## Why existing frameworks hard-code strategy

`Promise.all` vs `Promise.allSettled`, `asyncio.gather(return_exceptions=True/False)`, `ZIO.foreach` vs `ZIO.partition` — all require choosing the strategy at the call site because the return type differs (`list[T]` vs `list[Result[T]]`).

doeff-traverse makes results opaque. User code accesses them only via `for/do`, `fold`, `Zip`, `Inspect`, `SortBy`, `Take`. The handler resolves to a concrete type internally. This is why strategies are interchangeable without changing logic.

## User vocabulary

### Comprehension (inside `for/do` — CPS macro)

| Syntax | Purpose | SQL analogy |
|--------|---------|-------------|
| `(<- x (From items :label "name"))` | Generator bind | `FROM` |
| `(When pred)` | Guard (skip if falsy) | `WHERE` |
| `(<- y (effect x))` | Effect bind (kleisli) | subquery |
| `body-expr` | Return value | `SELECT` |

### Collection effects (normal effect binds)

| Syntax | Purpose |
|--------|---------|
| `(fold collection :init v body)` | Fold aggregation (`acc` = accumulator, `it` = item) |
| `(Zip a b)` | Join two collections by index (failure union) |
| `(Inspect collection)` | Post-hoc analysis: values + per-item history |
| `(SortBy key collection)` | Sort valid items by key function |
| `(Take n collection)` | Take first n valid items |
| `(Fail cause)` | Failure notification at yield site |
| `(try-call f args)` | Wrap plain function as Fail yield site |

### Legacy aliases

`traverse` and `Iterate` are aliases for `for/do` and `From`. Prefer `for/do` + `From` in new code.

## Architecture

```
User code (Hy macros)
  for/do + From + When + fold + Fail
        |
        | CPS macro expansion
        v
Free Monad layer
  Traverse(f, items)  <- f is thunk, not continuation
  Skip                <- mzero (When guard)
  Reduce(f, init, col)
  Zip(a, b)
  SortBy(key, col)
  Take(n, col)
        |
        | handler interprets
        v
Algebraic Effects (doeff VM)
  single-shot continuations
  Spawn/Gather, Await, Fail
```

## Item states

Items in a Collection can be:

| State | Cause | Downstream behavior |
|-------|-------|-------------------|
| `ok` | Thunk returned a value | Processed normally |
| `failed` | Unhandled exception in thunk | Carried forward, skipped |
| `skipped` | `When` guard was falsy | Carried forward, skipped |

Both `failed` and `skipped` items are excluded from downstream `for/do` and `fold`. `Inspect` reveals the full history including the cause.

## Counting item outcomes (progress)

`sequential`, `parallel` and `parallel_fail_fast` report each item's outcome with the effect `ItemOutcome(traverse, label, total, outcome, item)` as soon as it is known — `outcome` is `ok`, `failed`, `skipped`, or `carried` (an item an earlier stage failed or skipped, kept without running `f`), and `item` is the `ItemResult` the Traverse answers for it. A handler that counts them reaches `total` for every Traverse, failed items included. `progress_bars(make_bar)` draws one bar per Traverse and closes it at `total`:

```hy
(import tqdm [tqdm])
(import doeff_traverse.handlers [progress-bars parallel])

(run (with-stack [backend fail_handler (progress-bars tqdm) (parallel 10)] program))
```

- **Where**: the Traverse handler performs `ItemOutcome` under the handlers between the Traverse and itself (the ones it puts back around each item program), then under itself. A counting handler therefore goes *inside* the Traverse handler, like a body handler; installed outside it sees nothing.
- **Consumers pass it on**: a consumer looks at the report and passes it on (`Pass(effect, k)`, or `(reperform effect)` in a `defhandler` clause); only the Traverse handler answers it. So any number of consumers (`progress_bars`, a second bar, a failure log) compose in any order. A consumer that answers the report or throws into it fails the Traverse with `ItemOutcomeNotPassedError` — how the Traverse runs (stopping at a failure included) stays the Traverse handler's decision.
- **When**: only while one of those handlers names `ItemOutcome` in the annotation of its effect parameter (a `defhandler` clause `(ItemOutcome [...] ...)` does). Reporting adds half to all of an item's own cost, so a Traverse nobody counts does not pay for it. A handler that admits every effect does not count as naming it — nor does `Listen(prog, types=(ItemOutcome,))`.
- **Limits**: a Traverse with no item to run (no items, or only carried ones) reports nothing, so no bar opens for it. `sequential` reads an iterable without a length to its end at the first item when it reports (to know `total`). `parallel_fail_fast` reports the items finished before the first failure and the failed one; the items it cancels report nothing, so that Traverse's bar stays open.

## Examples

See `examples/` directory:

- **ex01** — Fail effect: 3 strategies on same program
- **ex02** — Multi-stage pipeline with history tracking
- **ex03** — Zip: independent computations joined with failure union
- **ex04** — traverse macro: full pipeline with CPS Iterate + fold
- **ex05** — Parallel timing: 10x speedup confirmed (sequential 1.0s -> parallel 0.1s)
- **ex06** — mediagen pipeline rewrite
- **ex07** — proboscis-ema pipeline rewrite with per-item failure tracking
- **ex08** — tqdm progress bar integration (bars close with a failing item too)
- **ex09** — for/do comprehension: From + When + SortBy + Take

## ADR

See [ADR-TRAVERSE-001](../../specs/features/ADR-TRAVERSE-001-applicative-traverse-via-free-monad-on-algebraic-effects.md) for the full design rationale.
