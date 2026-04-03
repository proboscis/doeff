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

## SQL analogy

The `for/do` comprehension reads like SQL:

```
for/do                              SQL
------                              ---
(<- item (From items))              FROM items
(When predicate)                    WHERE predicate
(<- result (process item))          SELECT process(item)
```

Multiple `From` binds nest (like SQL `CROSS JOIN`). `When` guards can appear at any nesting level. All binds are kleisli (effectful).

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

## Examples

See `examples/` directory:

- **ex01** — Fail effect: 3 strategies on same program
- **ex02** — Multi-stage pipeline with history tracking
- **ex03** — Zip: independent computations joined with failure union
- **ex04** — traverse macro: full pipeline with CPS Iterate + fold
- **ex05** — Parallel timing: 10x speedup confirmed (sequential 1.0s -> parallel 0.1s)
- **ex06** — mediagen pipeline rewrite
- **ex07** — proboscis-ema pipeline rewrite with per-item failure tracking
- **ex08** — tqdm progress bar integration
- **ex09** — for/do comprehension: From + When + SortBy + Take

## ADR

See [ADR-TRAVERSE-001](../../specs/features/ADR-TRAVERSE-001-applicative-traverse-via-free-monad-on-algebraic-effects.md) for the full design rationale.
