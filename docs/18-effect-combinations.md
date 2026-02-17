# 18. Effect Combinations

This chapter documents effect-composition behavior from `SPEC-EFF-100` and current runtime
contracts.

## Table of Contents

- [Effect Combination Matrix](#effect-combination-matrix)
- [Matrix Key](#matrix-key)
- [Composition Laws](#composition-laws)
- [Sync vs Async Runtime Differences](#sync-vs-async-runtime-differences)
- [Design Decision Notes](#design-decision-notes)
- [References](#references)

## Effect Combination Matrix

The matrix below follows `SPEC-EFF-100` outer/inner structure.

| Outer / Inner | Ask | Get | Put | Log | Safe | Local | Listen | Intercept | Gather |
|---------------|-----|-----|-----|-----|------|-------|--------|-----------|--------|
| **Local**     | Scoped | Propagates | Propagates | Propagates | Propagates | Scoped | Propagates | Propagates | Propagates |
| **Listen**    | - | - | - | Captured* | - | - | Nested | - | All captured* |
| **Safe**      | - | - | Persists | Persists | Wrapped | Restores | - | - | First error |
| **Intercept** | Transform | Transform | Transform | Transform | Transform | Transform | Transform | Transform | Transform |
| **Gather**    | Inherit | Shared** | Shared** | Merged | Isolated | Inherit | Shared | Propagates | Nested |

`*` Listen captures logs only on success paths. On error, the error propagates and logs are not
wrapped in `ListenResult`.[^D5]

`**` Gather store sharing differs by runtime (`Law 8a` and `Law 8b`).[^D4]

## Matrix Key

- `Scoped`: Effect operates within a bounded scope and restores after completion.
- `Propagates`: Effect passes through unchanged.
- `Captured`: Output is captured by the outer effect on success path.
- `Persists`: Side effects persist even when errors are caught.
- `Wrapped`: Result is wrapped in outer effect type.
- `Restores`: Environment restores to pre-scope value.
- `Transform`: Effect can be transformed by `Intercept` (including nested programs).[^D1]
- `Isolated`: Error handling remains per-branch while parent receives failing outcome.
- `Inherit`: Child inherits parent environment snapshot at invocation time.
- `Shared`: All branches use the same underlying resource.
- `Merged`: Outputs from branches are combined.
- `Nested`: Inner effect nests within outer effect.

## Composition Laws

### Law 1: Local Restoration Law

Environment MUST restore after `Local` scope, regardless of success or error.

```python
@do
def test_law_1():
    outer_val = yield Ask("key")
    try:
        yield Local({"key": "inner"}, failing_program())
    except Exception:
        pass
    restored_val = yield Ask("key")
    assert outer_val == restored_val
```

### Law 2: Local Non-State-Scoping Law

`Local` does NOT scope state (`Get`/`Put`). State changes propagate.

```python
@do
def test_law_2():
    yield Put("counter", 0)
    yield Local({}, do_increment())
    assert (yield Get("counter")) == 1
```

### Law 3: Listen Capture Law

`Listen` captures logs only on successful completion. On error, the error propagates without
`ListenResult` wrapping.[^D5]

```python
@do
def test_law_3_error():
    result = yield Safe(Listen(failing_with_logs()))
    assert result.is_err()
```

### Law 4: Safe Non-Rollback Law

`Safe` does NOT rollback state on error. State changes persist.[^D3]

```python
@do
def test_law_4():
    yield Put("counter", 0)
    _ = yield Safe(do_modify_then_fail())
    assert (yield Get("counter")) > 0
```

### Law 5: Safe Environment Restoration Law

`Safe` restores environment context to pre-`Safe` value.

```python
@do
def test_law_5():
    _ = yield Safe(Local({"x": 1}, failing_program()))
    # Environment is restored after Safe exits
```

### Law 6: Intercept Transformation Law

`Intercept` transforms effects in the intercepted program, including nested program values in
payloads (for example `Gather` children and `Local`/`Listen` sub-programs).[^D1]

```python
@do
def test_law_6():
    seen = [0]

    def transform(effect):
        if isinstance(effect, AskEffect):
            seen[0] += 1
        return effect

    @do
    def child():
        return (yield Ask("key"))

    _ = yield Gather(child(), child()).intercept(transform)
    assert seen[0] == 2
```

### Law 7: Gather Environment Inheritance Law

`Gather` children inherit parent environment as a snapshot at gather invocation time.

```python
@do
def test_law_7():
    _ = yield Local({"env_key": "value"}, Gather(child_that_reads_env_key()))
```

### Law 8: Gather Store Sharing Law (Runtime-Dependent)

Gather shares store across children in both runtimes, but execution model differs.[^D4]

#### Law 8a: SyncRuntime (Sequential Gather)

Children execute sequentially and share store deterministically.

```python
@do
def test_law_8a_sync():
    yield Put("counter", 0)
    results = yield Gather(increment(), increment(), increment())
    assert results == [0, 1, 2]
    assert (yield Get("counter")) == 3
```

#### Law 8b: AsyncRuntime (Parallel Gather)

Children execute in parallel with shared store. Concurrent writes may race; ordering of
intermediate observations is non-deterministic.

```python
@do
async def test_law_8b_async():
    yield Put("counter", 0)
    results = yield Gather(increment(), increment(), increment())
    assert (yield Get("counter")) == 3
    # `results` order is by input child position, but values may reflect interleaving.
```

### Law 9: Gather Error Propagation Law

When any `Gather` child fails, error propagates to parent. Sibling behavior depends on runtime.

- `SyncRuntime`: Sequential execution stops at first error.
- `AsyncRuntime`: All children are already spawned; siblings may continue and side effects may still occur.

```python
@do
def test_law_9():
    result = yield Safe(Gather(
        succeeds_with_side_effect(),
        fails_immediately(),
        succeeds_with_side_effect(),
    ))
    assert result.is_err()
```

## Sync vs Async Runtime Differences

`SPEC-EFF-100` defines shared semantics plus explicit runtime-dependent behavior.

| Concern | `run(...)` (`SyncRuntime`) | `async_run(...)` (`AsyncRuntime`) |
| --- | --- | --- |
| Gather execution model | Sequential child execution. | Parallel child execution. |
| Gather store semantics | Shared store, deterministic state observation by child order. | Shared store, race-prone interleavings for read/modify/write patterns. |
| Gather error propagation | Stop at first failing child; later children are not executed. | First failure propagates; other children may continue running. |
| Gather side effects on sibling failure | Effects only from children that already ran before failure. | Effects from siblings may still happen after failure is observed. |

Guideline: treat `Gather` as shared-store in both runtimes; require explicit coordination for
race-sensitive async state updates.

## Design Decision Notes

[^D1]: **D1 Intercept Scope**: Intercept applies structural rewriting to nested programs in effect payloads. It does not reach independently spawned tasks that do not share the parent continuation stack.

[^D2]: **D2 Listen + Gather Ordering**: No ordering guarantees for logs from parallel gather branches.

[^D3]: **D3 Safe Rollback**: `Safe` provides error capture and environment restoration, not transactional state rollback.

[^D4]: **D4 Gather Store Semantics**: Gather store is shared in both runtimes: sequential and deterministic in sync, parallel and race-prone in async.

[^D5]: **D5 Listen Error Behavior**: `Listen` does not capture logs on error paths; error propagates without `ListenResult` wrapping.

## References

- `specs/effects/SPEC-EFF-100-combinations.md`
- `tests/effects/test_effect_combinations.py`
- `tests/core/test_sa008_runtime_contracts.py`
- `tests/core/test_runtime_regressions_manual.py`
- `doeff/effects/spawn.py`
