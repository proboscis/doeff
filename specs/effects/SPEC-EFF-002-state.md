# SPEC-EFF-002: State Effects Semantics

## Status: Accepted

## Summary

This specification defines the semantics for State effects: `Get` and `Put`, resolved by the
`state()` handler. State effects provide mutable key-value storage within a program execution.
`Modify` was removed (see [Removed: Modify](#removed-modify)).

## Effect Definitions

### Get(key) -> T | None

Retrieves the value associated with `key` from the store.

**Behavior (`state()` handler):**
- Returns the stored value if the key exists
- Returns `None` if the key does not exist — a missing key is not an error

**Rationale for returning None:**
1. **Lazy initialization of handler state** - `writer` and the Hy `defhandler` session val / var
   read their slot with `Get` on first use and put an initial value when the slot is unset. They
   tell "unset" from "stored" by storing `Some(value)`, so the first read of a missing key must
   succeed without wrapping every first read in `Try`.
2. **Shipped behavior** - doeff 0.5.0 returns `None`; the docs (03, 09 and 13) describe it, and
   `tests/test_core_effects.py::TestState::test_get_missing_returns_none` guards it.
3. **Ask stays strict** - `Ask` reads configuration that must be provided, so a missing env key
   raises (SPEC-EFF-001). State is written by the program itself, so reading before the first
   `Put` is an ordinary step, not a bug.

**Handling missing keys:**
Start from a default, or `Put` an initial value before the first read:

```python
@do
def counter():
    current = yield Get("counter")
    if current is None:
        current = 0
    yield Put("counter", current + 1)
    return current + 1
```

**None as stored data:** `Get` cannot tell "missing" from "stored `None`". When `None` is a valid
stored value, `Put` an initial value before the first read, or store `Some(value)` as `writer`
does.

### Put(key, value) -> None

Stores `value` under `key` in the store.

**Behavior:**
- Creates the key if it does not exist, overwrites any existing value
- Returns `None`
- Effect is immediately visible to subsequent `Get` calls

### Removed: Modify

`Modify(key, func)` was removed. `doeff.Modify` remains only as a removed name: using it raises
`RuntimeError("Modify was removed: use Get + Put")`. Read with `Get`, transform, then write with
`Put`.

## Composition Rules

### Put + Get: Immediate Visibility

State changes are immediately visible within the same execution context.

```python
@do
def put_then_get():
    yield Put("x", 42)
    value = yield Get("x")
    return value  # Returns 42
```

**Verified by:** `tests/test_core_effects.py::TestState::test_get_put`

### Try + Put: Changes Persist on Caught Error

State modifications made before an error occurs persist even when `Try` catches the error.
There is NO automatic rollback.

```python
@do
def risky_operation():
    yield Put("modified", True)
    raise ValueError("error")

@do
def try_keeps_state():
    yield Put("modified", False)
    result = yield Try(risky_operation())
    # result is Err(ValueError)
    value = yield Get("modified")
    return value  # Returns True - change persisted

run(try_handler(state()(try_keeps_state())))
```

**Rationale:**
- Implementing transactional rollback would require significant complexity
- Programs needing transaction semantics can implement explicit compensation
- This behavior is consistent with standard imperative programming expectations

**Verified by:** `tests/effects/test_effect_combinations.py::TestSafeNonRollbackLaw::test_safe_does_not_rollback_state`

### Spawn + Put: One Shared Store, Results as the Channel

Tasks spawned under `scheduled(...)` read and write the one store of the enclosing `state()`
handler. Capturing a continuation or spawning a task never snapshots or forks the store
(decision D9 and law S6 in `docs/crystallization/`). Tasks interleave only where they perform a
scheduler or external effect, so the effects between two such points in one task are atomic
(law CS1).

Tasks must not communicate through `Get` / `Put` (decision D16): the shared store makes it work,
but it is outside the law guarantees. Pass values between tasks through `Wait` / `Gather`
results, `Promise`, or `Semaphore`, and let the parent write the combined result:

```python
@do
def score(item):
    return len(item)

@do
def total_score():
    first = yield Spawn(score("alpha"))
    second = yield Spawn(score("beta"))
    scores = yield Gather(first, second)
    yield Put("total", sum(scores))  # the parent writes the combined result
    return (yield Get("total"))  # Returns 9

run(scheduled(state()(total_score())))
```

**Verified by:** `tests/laws/test_generator_laws.py::TestConcurrencyLaws::test_cs1_no_yield_point_no_interleave`
and `tests/laws/test_generator_laws.py::TestConcurrencyLaws::test_cs1_yield_point_admits_interleave`
(a spawned task reads the parent's `Put` from the same store, at the yield points CS1 names)

## Design Decisions

### D1: Get Returns None for Missing Keys

**Decision:** With the `state()` handler, `Get` returns `None` for a missing key.

**Considered alternatives:**
1. Raise an error for a missing key (as `Ask` does) — the earlier draft of this decision
2. Return `Option[T]`
3. Require a default value parameter

**Rationale:**
- **Lazy initialization**: `writer` and Hy `defhandler` session val / var depend on reading an
  unset slot without an error
- **Shipped behavior**: doeff 0.5.0 returns `None`; changing it would break every program that
  starts from `current or 0` or checks `is None`
- **None ambiguity** is handled by initializing first or storing `Some(value)`

**History:** The earlier draft chose an error for a missing key and cited a test that never
existed in the tree, while the handler returned `None`. It was aligned with the shipped behavior
in 2026-10 (card ki-4fc77b6d6aa9).

### D2: No Transaction Rollback on Error

**Decision:** State changes persist through `Try` error boundaries.

**Considered alternatives:**
1. Automatic rollback on Try-caught errors
2. Explicit `Transaction` effect wrapper
3. Copy-on-write semantics for Try blocks

**Rationale:**
- Complexity of implementing true transaction semantics
- Most use cases don't require rollback
- Explicit compensation patterns are clearer when needed
- Consistent with imperative programming mental model

### D3: One Store for Spawned Tasks, No Communication Through It

**Decision:** All tasks spawned under one `state()` handler share the same store, with no
snapshot on capture and no fork on `Spawn` (D9). Tasks communicate only through `Wait` /
`Gather` results, `Promise`, and `Semaphore`; cross-task `Get` / `Put` is outside the law
guarantees (D16).

**Considered alternatives:**
1. Snapshot isolation (each task gets a copy) — rejected in D9
2. Fork the store on `Spawn` — rejected in D16: it changes the implementation and breaks code
   that already depends on the shared store
3. Only state an interference-free condition — rejected in D16: it cannot be checked in review

**Rationale:**
- Continuations are one-shot, so resuming once over a shared store is correct (D9)
- With communication through results only, interleavings cannot change what a task observes,
  so the concurrency laws (CC1, AW2) apply (D16)
- Review rule: a spawned program that talks to its parent or siblings through `Get` / `Put`
  breaks the norm

## Test Coverage

| Rule | Test |
|------|------|
| Put + Get | `tests/test_core_effects.py::TestState::test_get_put` |
| Get missing key returns None | `tests/test_core_effects.py::TestState::test_get_missing_returns_none` |
| Try + Put | `tests/effects/test_effect_combinations.py::TestSafeNonRollbackLaw::test_safe_does_not_rollback_state` |
| Spawn + Put (one store, CS1) | `tests/laws/test_generator_laws.py::TestConcurrencyLaws::test_cs1_no_yield_point_no_interleave` |

`tests/test_docs_state_get_missing_key.py` checks that every test named here exists.

## References

- Effects: `packages/doeff-core-effects/doeff_core_effects/effects.py` (`Get`, `Put`)
- Handler: `packages/doeff-core-effects/doeff_core_effects/handlers.py` (`state`)
- Decisions D9 (shared state, no snapshot) and D16 (tasks communicate through results):
  `docs/crystallization/decision-records.md`
- Laws S6 (state × continuation sharing) and CS1 (cooperative atomicity):
  `docs/crystallization/algebra-draft.md` §3
- Related issue: gh#466 (drop the snapshot-isolation known issue)
