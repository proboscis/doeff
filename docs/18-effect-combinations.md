# 18. Effect Combinations

This chapter documents effect-composition behavior from `SPEC-EFF-100` and current runtime
contracts.

## Table of Contents

- [Combination Matrix](#combination-matrix)
- [Key Laws with Examples](#key-laws-with-examples)
- [Sync vs Async Differences](#sync-vs-async-differences)
- [References](#references)

## Combination Matrix

The table below lists documented effect pairs and their interaction behavior.

| Effect Pair | Interaction Behavior | Practical Guarantee |
| --- | --- | --- |
| `Local + Ask` | `Local` overrides reader values inside its scope only. | Reader env restores after scope exit. |
| `Local + Local` | Nested locals compose by inner override, then unwind. | Each scope restores independently. |
| `Local + Get/Put/Modify` | `Local` does not scope state operations. | State mutations propagate outside `Local`. |
| `Local + Safe` | `Safe(Local(...))` catches errors without leaking env override. | Env is restored on both success and failure. |
| `Listen + Tell` | `Listen` captures `Tell` entries from successful sub-programs. | `ListenResult.log` contains scope-local successful logs. |
| `Listen + Local` | `Tell` inside `Local` is still visible to enclosing `Listen` on success. | Reader scoping does not block log capture. |
| `Listen + Listen` | Inner and outer `Listen` scopes capture independently. | Inner captures its subtree; outer sees full outer subtree. |
| `Listen + Safe` | If `Safe` wraps `Listen`, failures are returned as `Err`; no `ListenResult` on failing path. | Failed listens do not produce captured logs. |
| `Listen + Gather` | With `Gather` over spawned tasks, child logs remain task-local snapshots. | Parent `Listen` does not capture spawned-child logs. |
| `Safe + Get/Put/Modify` | `Safe` catches errors but does not roll back state mutations done before failure. | State changes persist after `Err`. |
| `Safe + Local` | Error boundaries do not break env restoration semantics. | Post-`Safe` env equals pre-`Safe` env. |
| `Safe + Gather` | `Safe(Gather(...))` returns `Err` on child failure. | Parent continues with explicit result handling. |
| `State + Gather` | Gathered branches operate on isolated state snapshots and aggregate return values. | Child state writes do not merge into parent store automatically. |
| `Spawn + Ask` | Child task inherits env snapshot at spawn time. | Children read parent env values unless locally overridden. |
| `Spawn + Get/Put/Modify` | Child task runs on isolated state snapshot. | Branch state mutations are not shared with siblings/parent. |
| `Spawn + Tell` | Child task uses isolated log snapshot. | Branch logs are not automatically merged into parent logs. |
| `Gather + Spawn` | `Gather` joins spawned tasks and returns values in input order. | Aggregation is deterministic by input handle order. |
| `Gather + Local` | Local env overrides apply to tasks spawned in that scope. | Gathered children see inherited local env snapshot. |
| `Intercept + Gather/Local/Listen` | `Intercept` rewrites nested program payloads structurally. | Transforms apply through nested composition boundaries. |

## Key Laws with Examples

### 1. Local Restoration

Environment is restored after `Local` exits, even when the inner computation fails.

```python
from doeff import do
from doeff.effects import Ask, Local, Safe


@do
def failing_inner():
    _ = yield Ask("key")
    raise ValueError("boom")


@do
def program():
    before = yield Ask("key")
    result = yield Safe(Local({"key": "inner"}, failing_inner()))
    after = yield Ask("key")
    return (before, result.is_err(), after)  # ("outer", True, "outer")
```

### 2. Listen Capture on Success Only

`Listen` captures logs only when the listened computation completes successfully.

```python
from doeff import do
from doeff.effects import Listen, Safe, Tell


@do
def failing_with_logs():
    yield Tell("before-fail")
    raise RuntimeError("failed")


@do
def program():
    result = yield Safe(Listen(failing_with_logs()))
    return result.is_err()  # True, no ListenResult produced on failure path
```

### 3. Safe State Persistence

State mutations inside a `Safe` boundary persist, even if the computation fails.

```python
from doeff import do
from doeff.effects import Get, Put, Safe


@do
def mutate_then_fail():
    yield Put("counter", 10)
    raise ValueError("fail after mutation")


@do
def program():
    yield Put("counter", 0)
    _ = yield Safe(mutate_then_fail())
    return (yield Get("counter"))  # 10
```

### 4. Gather Store Sharing (Isolation Semantics)

`Gather` aggregates spawned children, but each child runs with isolated `state/log` snapshots while
sharing inherited env context.

```python
from doeff import do
from doeff.effects import Gather, Get, Put, Spawn


@do
def child_increment():
    current = yield Get("counter")
    yield Put("counter", current + 1)
    return current


@do
def program():
    yield Put("counter", 0)
    t1 = yield Spawn(child_increment())
    t2 = yield Spawn(child_increment())
    t3 = yield Spawn(child_increment())
    values = yield Gather(t1, t2, t3)
    final = yield Get("counter")
    return (values, final)  # ([0, 0, 0], 0)
```

### 5. Env Inheritance for Child Tasks

Children spawned inside `Local` inherit the enclosing env snapshot.

```python
from doeff import do
from doeff.effects import Ask, Gather, Local, Spawn


@do
def child():
    return (yield Ask("tenant"))


@do
def program():
    return (
        yield Local(
            {"tenant": "acme"},
            _spawn_and_gather(),
        )
    )


@do
def _spawn_and_gather():
    t1 = yield Spawn(child())
    t2 = yield Spawn(child())
    return (yield Gather(t1, t2))  # ["acme", "acme"]
```

## Sync vs Async Differences

Core composition laws in this chapter are shared by `run(...)` and `async_run(...)`. Differences are
in execution driver behavior:

| Concern | `run(...)` (sync) | `async_run(...)` (async) |
| --- | --- | --- |
| `Await` handling | Uses sync await bridge handler. | Uses async await handler with async runner path. |
| Call site contract | Direct call returns `RunResult`. | `await` call returns `RunResult`. |
| `Spawn` + `Gather` composition guarantees | Same guarantees: env snapshot inheritance, isolated state/log snapshots, result order follows input handles. | Same guarantees: env snapshot inheritance, isolated state/log snapshots, result order follows input handles. |
| Side-effect timing | Cooperative scheduling; timing can vary with awaited operations. | Cooperative scheduling; timing can vary with awaited operations. |

Guideline: rely on value-level guarantees (env inheritance, state/log isolation, explicit error
handling), not on incidental interleaving timing.

## References

- `specs/effects/SPEC-EFF-100-combinations.md`
- `tests/effects/test_effect_combinations.py`
- `tests/core/test_sa008_runtime_contracts.py`
- `tests/core/test_runtime_regressions_manual.py`
- `doeff/effects/spawn.py`
