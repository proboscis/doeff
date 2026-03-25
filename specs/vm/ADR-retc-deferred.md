# ADR: retc (return continuation) deferred

## Context

OCaml 5's `match_with` has three callbacks:
- `effc` — body performed an effect (we have this)
- `exnc` — body raised an exception (we propagate via Mode::Raise)
- `retc` — body returned normally (we DON'T have this)

In OCaml 5, `retc` is always called when the body returns, even if no effects were performed. It allows the handler to transform the body's return value.

## Decision

Defer retc. Not implementing it now.

## Rationale

1. **Handler already captures return value via Resume.** When the handler does `result = yield Resume(k, v)`, the body's return value comes back as `result`. The handler can transform it there. This covers the effect-performing case.

2. **The no-effect case (body returns without performing) is rare for handlers.** If the body doesn't perform, the handler was never needed. Pass-through is the right default.

3. **State capture doesn't need retc.** OCaml 5 uses `retc = fun v -> (v, !state)` for state handlers. We use `AllocVar`/`ReadVar`/`WriteVar` instead — state is in the VM's var_store, not in the handler.

4. **Resource cleanup has Python alternatives.** `try/finally` and context managers handle cleanup without retc.

## If we add it later

retc would be an optional second argument to `WithHandler`:

```python
WithHandler(handler, body, retc=lambda v: v)
```

Or a field on the handler boundary fiber. When the body fiber completes normally and reaches the boundary, if retc is set, the boundary calls retc(value) instead of passing through.

Small, localized change. No architectural impact.
