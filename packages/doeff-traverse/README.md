# doeff-traverse

Free Monad on algebraic effects — multi-shot computation via CPS macro expansion.

## What this solves

doeff's VM has single-shot continuations: a continuation can only be resumed once. But many useful patterns need to apply the same computation to multiple inputs — list traversal, retry, backtracking. These are all "multi-shot" patterns.

doeff-traverse solves this with a CPS macro that converts the computation body into a **thunk (function)**, not a captured continuation. The handler calls the thunk as many times as needed, creating a fresh generator each time. This is a Free Monad node interpreted by the handler.

List traversal is the most common instance of this pattern.

## What it looks like

```hy
(require doeff-hy.macros [defk <- traverse fold])

;; Pipeline logic: no error handling, no parallelism, no backend choice
(defk pipeline [items]
  (<- features
    (traverse
      (<- item (Iterate items :label "extract"))
      (<- result (try_call extract-feature item))
      result))
  (<- mean
    (fold features :init #(0 0)
      #((+ (get acc 0) it) (+ (get acc 1) 1))))
  (<- normalized
    (traverse
      (<- v (Iterate features :label "normalize"))
      (normalize-value v (/ (get mean 0) (get mean 1)))))
  normalized)

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

## Why existing frameworks hard-code strategy

`Promise.all` vs `Promise.allSettled`, `asyncio.gather(return_exceptions=True/False)`, `ZIO.foreach` vs `ZIO.partition` — all require choosing the strategy at the call site because the return type differs (`list[T]` vs `list[Result[T]]`).

doeff-traverse makes results opaque. User code accesses them only via `traverse`, `fold`, `Zip`, `Inspect`. The handler resolves to a concrete type internally. This is why strategies are interchangeable without changing logic.

## User vocabulary

| Syntax | Purpose |
|--------|---------|
| `(traverse (<- x (Iterate items)) body)` | Batch processing (CPS → Traverse effect) |
| `(fold collection :init v body)` | Fold aggregation (`acc` = accumulator, `it` = item) |
| `(Zip a b)` | Join two traverse results |
| `(Inspect collection)` | Post-hoc analysis: values + per-item history with traceback |
| `(Fail cause)` | Failure notification at yield site |
| `(try-call f args)` | Wrap plain function → Fail on exception |
| `(fnk [params] body)` | Anonymous kleisli arrow |

## Architecture

```
User code (Hy macros)
  traverse + Iterate + fold + Fail
        │
        │ CPS macro expansion
        ▼
Free Monad layer
  Traverse(f, items)  ← f is thunk, not continuation
  Reduce(f, init, col)
  Zip(a, b)
        │
        │ handler interprets
        ▼
Algebraic Effects (doeff VM)
  single-shot continuations
  Spawn/Gather, Await, Fail
```

## Examples

See `examples/` directory:

- **ex01** — Fail effect: 3 strategies on same program
- **ex02** — Multi-stage pipeline with history tracking
- **ex03** — Zip: independent computations joined with failure union
- **ex04** — traverse macro: full pipeline with CPS Iterate + fold
- **ex05** — Parallel timing: 10x speedup confirmed (sequential 1.0s → parallel 0.1s)
- **ex06** — mediagen pipeline rewrite
- **ex07** — proboscis-ema pipeline rewrite with per-item failure tracking

## ADR

See [ADR-TRAVERSE-001](../../specs/features/ADR-TRAVERSE-001-applicative-traverse-via-free-monad-on-algebraic-effects.md) for the full design rationale.
