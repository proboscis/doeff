# 18. Effect Combinations

This chapter documents how effects compose in doeff 0.5.0. It follows the outer/inner structure
of `SPEC-EFF-100`; where the spec still uses older names (`Intercept`, `SyncRuntime` and
`AsyncRuntime`), this chapter follows the 0.5.0 runtime.

[![A narrated video, under three minutes: the matrix of outer and inner effects; Try and Listen nested both ways around a failing program; Local and Try restoring the environment while the store keeps every write; WithObserve seeing the effects of spawned children; Gather children sharing one store under a scheduler that switches only where a task suspends; and a failing child whose siblings are not cancelled](assets/videos/effect-combinations.gif)](assets/videos/effect-combinations.mp4)

## Table of Contents

- [Effect Combination Matrix](#effect-combination-matrix)
- [Matrix Key](#matrix-key)
- [Composition Laws](#composition-laws)
- [Gather and the Scheduler](#gather-and-the-scheduler)
- [Design Decision Notes](#design-decision-notes)
- [References](#references)

## Effect Combination Matrix

The matrix below follows `SPEC-EFF-100` outer/inner structure.

| Outer / Inner | Ask | Get | Put | Tell | Try | Local | Listen | WithObserve | Gather |
|---------------|-----|-----|-----|------|------|-------|--------|-------------|--------|
| **Local**     | Scoped | Propagates | Propagates | Propagates | Propagates | Scoped | Propagates | Propagates | Propagates |
| **Listen**    | - | - | - | Captured* | - | - | Nested | - | All captured* |
| **Try**      | - | - | Persists | Persists | Wrapped | Restores | - | - | First error |
| **WithObserve** | Observed | Observed | Observed | Observed | Observed | Observed | Observed | Observed | Observed |
| **Gather**    | Inherit | Shared** | Shared** | Merged | Isolated | Inherit | Shared | Propagates | Nested |

`*` Listen captures logs only on success paths. On error, the error propagates and logs are not
wrapped in `ListenResult`.[^D5] The logs that `Listen` captures from parallel `Gather` branches
come in no guaranteed order.[^D2]

`**` Children share one store; a read-modify-write is safe only when the child does not suspend
between the read and the write (`Law 8a` and `Law 8b`).[^D4]

## Matrix Key

- `Scoped`: Effect operates within a bounded scope and restores after completion.
- `Propagates`: Effect passes through unchanged.
- `Captured`: Output is captured by the outer effect on success path.
- `Persists`: Side effects persist even when errors are caught.
- `Wrapped`: Result is wrapped in outer effect type.
- `Restores`: Environment restores to pre-scope value.
- `Observed`: `WithObserve` sees the effect, including effects of spawned children and nested
  programs. It only observes: the observer's return value is ignored.[^D1]
- `Isolated`: Error handling remains per-branch while parent receives failing outcome.
- `Inherit`: Child inherits the parent environment in effect at its `Spawn`.
- `Shared`: All branches use the same underlying resource.
- `Merged`: Outputs from branches are combined.
- `Nested`: Inner effect nests within outer effect.

## Composition Laws

The examples of Laws 1–5 show only the core of each law: they use helper programs
(`failing_program()`, `do_increment()`, ...) that this page does not define. The examples of
Laws 6–9 are complete programs; each one prints the value written in its last comment.

### Law 1: Local Restoration Law

Environment MUST restore after `Local` scope, regardless of success or error.

```python excerpt
@do
def test_law_1():
    outer_val = yield Ask("key")
    _ = yield Try(Local({"key": "inner"}, failing_program()))
    restored_val = yield Ask("key")
    assert outer_val == restored_val
```

### Law 2: Local Non-State-Scoping Law

`Local` does NOT scope state (`Get`/`Put`). State changes propagate.

```python excerpt
@do
def test_law_2():
    yield Put("counter", 0)
    yield Local({}, do_increment())
    assert (yield Get("counter")) == 1
```

### Law 3: Listen Capture Law

`Listen` captures logs only on successful completion. On error, the error propagates without
`ListenResult` wrapping.[^D5]

```python excerpt
@do
def test_law_3_error():
    result = yield Try(Listen(failing_with_logs()))
    assert result.is_err()
```

### Law 4: Try Non-Rollback Law

`Try` does NOT rollback state on error. State changes persist.[^D3]

```python excerpt
@do
def test_law_4():
    yield Put("counter", 0)
    _ = yield Try(do_modify_then_fail())
    assert (yield Get("counter")) > 0
```

### Law 5: Try Environment Restoration Law

`Try` restores environment context to pre-`Try` value.

```python excerpt
@do
def test_law_5():
    _ = yield Try(Local({"x": 1}, failing_program()))
    # Environment is restored after Try exits
```

### Law 6: WithObserve Observation Law

`WithObserve` observes every effect performed in its body, including effects of tasks spawned
inside the body (the children passed to `Gather`) and of `Local`/`Listen` sub-programs.[^D1]
The observer is called for observation only: its return value is ignored, so it cannot replace
an effect. To change how an effect is handled, install a handler instead.

```python
from doeff import Ask, AskEffect, Gather, Spawn, WithObserve, do, run
from doeff_core_effects import scheduled
from doeff_core_effects.handlers import reader


@do
def test_law_6():
    seen = []

    def observer(effect):
        if isinstance(effect, AskEffect):
            seen.append(effect.key)

    @do
    def child():
        return (yield Ask("key"))

    @do
    def body():
        t1 = yield Spawn(child())
        t2 = yield Spawn(child())
        return (yield Gather(t1, t2))

    values = yield WithObserve(observer, body())
    assert seen == ["key", "key"]  # both children's Ask effects were observed
    return values


print(run(scheduled(reader({"key": "value"})(test_law_6()))))  # ['value', 'value']
```

### Law 7: Gather Environment Inheritance Law

A spawned child runs with the environment in effect at its `Spawn`, not at `Gather`: children
spawned inside `Local` see its overrides. A `Local` inside one child does not change what its
siblings see.

```python
from doeff import Ask, Gather, Local, Spawn, do, run
from doeff_core_effects import scheduled
from doeff_core_effects.handlers import local_handler, reader


@do
def child_that_reads_env_key():
    return (yield Ask("env_key"))


@do
def child_that_overrides_env_key():
    return (yield Local({"env_key": "child"}, child_that_reads_env_key()))


@do
def spawn_and_gather():
    t1 = yield Spawn(child_that_overrides_env_key())
    t2 = yield Spawn(child_that_reads_env_key())
    return (yield Gather(t1, t2))


@do
def test_law_7():
    results = yield Local({"env_key": "value"}, spawn_and_gather())
    assert results == ["child", "value"]  # the sibling does not see the child's Local
    task = yield Local({"env_key": "at-spawn"}, Spawn(child_that_reads_env_key()))
    assert (yield Gather(task)) == ["at-spawn"]  # the environment at Spawn, not at Gather
    return results


print(run(scheduled(reader({"env_key": "outer"})(local_handler(test_law_7())))))  # ['child', 'value']
```

### Law 8: Gather Store Sharing Law

Spawned children share the parent's store: a `Put` in one child is visible to the parent and to
its siblings. The scheduler runs one task at a time and switches tasks only where a task
suspends (`Wait`, `Gather`, `Race`, `Await`, acquiring a semaphore, ...). Whether a
read-modify-write is safe therefore depends on whether the child suspends between the read and
the write.[^D4]

#### Law 8a: Children That Do Not Suspend

A child that does not suspend runs from start to end before another task runs. Children run in
spawn order, and each one sees the previous child's write.

```python
from doeff import Gather, Get, Put, Spawn, do, run
from doeff_core_effects import scheduled
from doeff_core_effects.handlers import state


@do
def increment():
    n = yield Get("counter")
    yield Put("counter", n + 1)
    return n


@do
def test_law_8a():
    yield Put("counter", 0)
    tasks = []
    for _ in range(3):
        tasks.append((yield Spawn(increment())))
    results = yield Gather(*tasks)
    counter = yield Get("counter")
    assert results == [0, 1, 2]
    assert counter == 3
    return results, counter


print(run(scheduled(state()(test_law_8a()))))  # ([0, 1, 2], 3)
```

#### Law 8b: Children That Suspend Between Read and Write

When a child suspends between its `Get` and its `Put` (here on doeff-time's `Delay`, run on
`sim_time_handler()`'s virtual clock), the other children run in the gap and read the same old
value. All three children below read `0` and write `1`, so two
of the three updates are lost.
Programs using `Gather` must not rely on a read-modify-write across a suspension. Keep the read
and the write together without a suspension in between, guard them with a semaphore, or give
each child its own key.

```python
from doeff import Gather, Get, Put, Spawn, do, run
from doeff_core_effects import scheduled
from doeff_core_effects.handlers import state
from doeff_time import Delay, sim_time_handler


@do
def increment_across_delay():
    n = yield Get("counter")
    yield Delay(0.05)  # suspends: the other children run here
    yield Put("counter", n + 1)
    return n


@do
def test_law_8b():
    yield Put("counter", 0)
    tasks = []
    for _ in range(3):
        tasks.append((yield Spawn(increment_across_delay())))
    results = yield Gather(*tasks)
    counter = yield Get("counter")
    assert results == [0, 0, 0]
    assert counter == 1  # two of the three updates are lost
    return results, counter


print(run(scheduled(sim_time_handler()(state()(test_law_8b())))))  # ([0, 0, 0], 1)
```

### Law 9: Gather Error Propagation Law

When a child fails, `Gather` raises that child's error in the parent, and a `Try` around the
`Gather` returns `Err`. `Gather` does not cancel the other children or roll back their writes:
children that were already spawned keep running, so their side effects may still happen after
one child has failed. Cancel them explicitly with `Cancel(task)` if they must stop.

```python
from doeff import Gather, Get, Put, Spawn, Try, do, run
from doeff_core_effects import scheduled
from doeff_core_effects.handlers import state, try_handler


@do
def succeeds_with_side_effect():
    n = yield Get("side_effects")
    yield Put("side_effects", n + 1)
    return "ok"


@do
def fails_immediately():
    raise ValueError("boom")


@do
def test_law_9():
    yield Put("side_effects", 0)
    t1 = yield Spawn(succeeds_with_side_effect())
    t2 = yield Spawn(fails_immediately())
    t3 = yield Spawn(succeeds_with_side_effect())
    result = yield Try(Gather(t1, t2, t3))
    side_effects = yield Get("side_effects")
    assert result.is_err()
    assert side_effects == 2  # both siblings ran; their writes are not rolled back
    return result, side_effects


print(run(scheduled(state()(try_handler(test_law_9())))))  # (Err(ValueError('boom')), 2)
```

## Gather and the Scheduler

`run(program)` is the single entry point. `Spawn`, `Wait`, `Gather`, and `Race` are handled by
`scheduled()`, so a program that uses `Gather` runs as `run(scheduled(...))`; without it, the
first `Spawn` fails with `UnhandledEffect`. There is no separate sync or async runtime.
`scheduled()` has a Rust implementation (the default) and a Python one (chosen by its
`implementation=` argument or by `DOEFF_SCHEDULER`), and both give the results shown in Laws 6–9.

| Concern | Children that do not suspend | Children that suspend (`Delay`, `Await`, `Wait`, ...) |
| --- | --- | --- |
| Execution | One child at a time, in spawn order. | Other tasks run while a child is suspended. |
| Store | Shared; each child sees the previous child's writes. | Shared; a read-modify-write across a suspension can lose updates. |
| Error | `Gather` raises a failing child's error; children already spawned still run. | Same; siblings may keep running after the parent has seen the error. |

Guideline: treat `Gather` as shared-store, and require explicit coordination for state that
children update across a suspension.

## Design Decision Notes

[^D1]: **D1 WithObserve Scope**: WithObserve observes effects; it does not rewrite them, and the
observer's return value is ignored. A spawned task inherits the `WithObserve` scopes between its
`Spawn` and `scheduled()`, so an observer around the spawning code sees the children's effects.
An observer placed outside `scheduled()` sees them too, together with the scheduler's internal
effects such as `TaskCompleted`.

[^D2]: **D2 Listen + Gather Ordering**: No ordering guarantees for logs from parallel gather branches.

[^D3]: **D3 Try Rollback**: `Try` provides error capture and environment restoration, not transactional state rollback.

[^D4]: **D4 Gather Store Semantics**: Spawned children share the parent's store. Their updates
are deterministic when a child does not suspend between reading and writing, and race-prone when
it does. For isolation-sensitive flows, use explicit state partitioning or coordination (for
example a semaphore).

[^D5]: **D5 Listen Error Behavior**: `Listen` does not capture logs on error paths; error propagates without `ListenResult` wrapping.

## References

- `docs/history/specs/effects/SPEC-EFF-100-combinations.md`
- `tests/effects/test_effect_combinations.py`
- `tests/core/test_sa008_runtime_contracts.py`
- `tests/core/test_runtime_regressions_manual.py`
- `packages/doeff-core-effects/doeff_core_effects/scheduler.py`
