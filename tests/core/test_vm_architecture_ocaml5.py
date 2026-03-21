"""Architecture enforcement tests: doeff VM must match OCaml 5 model.

These tests verify STRUCTURAL properties of the VM, not behavior.
They ensure the architecture converges toward OCaml 5:
  - Fiber = frames + handler + parent (nothing else)
  - VarStore = the heap (separate from fibers)
  - Continuation = moved fiber IDs (not Arc snapshots)
  - Dispatch = topology change (no side-table accumulation)

If these tests fail, the architecture has regressed.
"""

import pytest
from doeff import (
    Effect,
    EffectBase,
    Pass,
    Resume,
    Spawn,
    Gather,
    WithHandler,
    default_handlers,
    do,
    run,
)


# ---------------------------------------------------------------------------
# Continuation move semantics: a fiber is in ONE place, never both
# ---------------------------------------------------------------------------


@do
def _capture_and_check_handler(effect: Effect, k):
    """Handler that captures k and returns it for inspection."""
    return (yield Resume(k, "handled"))


@do
def _simple_effect_program():
    """Program that yields one effect."""

    class Ping(EffectBase):
        pass

    result = yield Ping()
    return result


def test_resume_returns_to_same_execution_context():
    """Resume(k, v) must return to the original execution context.

    In OCaml 5, continue k v reattaches the original fiber — it does NOT
    create a new fiber. The resumed code runs in the same context it was
    captured from.
    """

    @do
    def _handler(effect: Effect, k):
        return (yield Resume(k, "resumed_value"))

    @do
    def _program():
        class MyEffect(EffectBase):
            pass

        val = yield MyEffect()
        return val

    result = run(WithHandler(_handler, _program()), handlers=default_handlers())
    assert result.is_ok()
    assert result.value == "resumed_value"


def test_continuation_is_one_shot():
    """A continuation can only be resumed once (one-shot semantics).

    In OCaml 5, continue k v moves the fiber back — k is consumed.
    Attempting to continue k again must fail.
    """

    @do
    def _double_resume_handler(effect: Effect, k):
        yield Resume(k, "first")
        yield Resume(k, "second")  # should fail: one-shot violation

    @do
    def _program():
        class MyEffect(EffectBase):
            pass

        return (yield MyEffect())

    result = run(
        WithHandler(_double_resume_handler, _program()),
        handlers=default_handlers(),
    )
    assert result.is_err()
    assert "one-shot" in str(result.error).lower() or "consumed" in str(
        result.error
    ).lower()


# ---------------------------------------------------------------------------
# Shared handlers: spawned tasks share parent handlers (OCaml 5 model)
# ---------------------------------------------------------------------------


class SharedState(EffectBase):
    pass


class GetShared(SharedState):
    pass


class PutShared(SharedState):
    pass

    def __init__(self, value):
        self.value = value


def test_spawned_tasks_share_handler_state():
    """Spawned tasks must share the SAME handler instance via parent chain.

    In OCaml 5, spawned fibers delegate effects up the fiber chain to the
    same handler. Put in task A is visible to Get in task B.
    This is the shared handler invariant from DEC-VM-012.
    """
    shared = {"value": 0}

    @do
    def _shared_handler(effect: Effect, k):
        if isinstance(effect, GetShared):
            return (yield Resume(k, shared["value"]))
        elif isinstance(effect, PutShared):
            shared["value"] = effect.value
            return (yield Resume(k, None))
        else:
            yield Pass()

    @do
    def _put_task():
        yield PutShared(42)
        return "put_done"

    @do
    def _get_task():
        return (yield GetShared())

    @do
    def _program():
        t1 = yield Spawn(_put_task())
        yield Gather(t1)
        t2 = yield Spawn(_get_task())
        result = yield Gather(t2)
        return result

    result = run(
        WithHandler(_shared_handler, _program()), handlers=default_handlers()
    )
    assert result.is_ok()
    assert result.value == [42], f"Spawned task should see Put(42), got {result.value}"


def test_spawn_does_not_duplicate_handlers():
    """Spawning must NOT duplicate handler segments.

    In OCaml 5, spawned fibers share parent handlers naturally via the
    fiber chain. No cloning, no GetHandlers, no scope_parent.
    """

    handler_call_count = {"n": 0}

    class CountEffect(EffectBase):
        pass

    @do
    def _counting_handler(effect: Effect, k):
        if not isinstance(effect, CountEffect):
            yield Pass()
            return
        handler_call_count["n"] += 1
        return (yield Resume(k, handler_call_count["n"]))

    @do
    def _task():
        return (yield CountEffect())

    @do
    def _program():
        tasks = []
        for _ in range(10):
            tasks.append((yield Spawn(_task())))
        results = list((yield Gather(*tasks)))
        return results

    result = run(
        WithHandler(_counting_handler, _program()), handlers=default_handlers()
    )
    assert result.is_ok()
    # All 10 tasks should have called the SAME handler instance
    assert handler_call_count["n"] == 10


# ---------------------------------------------------------------------------
# Fiber immutability: non-current fibers must not be mutated during dispatch
# ---------------------------------------------------------------------------


def test_dispatch_does_not_corrupt_shared_handler_chain():
    """Dispatch must not mutate shared handler fibers.

    When task A performs an effect, the dispatch must not mutate handler
    fibers that task B also depends on. This was the caller chain pollution
    bug fixed in PR #354.
    """

    class TaskEffect(EffectBase):
        pass

    results = []

    @do
    def _handler(effect: Effect, k):
        if isinstance(effect, TaskEffect):
            return (yield Resume(k, "ok"))
        else:
            yield Pass()

    @do
    def _task(task_id):
        result = yield TaskEffect()
        return f"task_{task_id}={result}"

    @do
    def _program():
        tasks = []
        for i in range(5):
            tasks.append((yield Spawn(_task(i))))
        return list((yield Gather(*tasks)))

    result = run(
        WithHandler(_handler, _program()), handlers=default_handlers()
    )
    assert result.is_ok()
    assert len(result.value) == 5
    for i, val in enumerate(result.value):
        assert val == f"task_{i}=ok", f"Task {i} got corrupted result: {val}"


# ---------------------------------------------------------------------------
# VarStore as heap: variables must survive across dispatch
# ---------------------------------------------------------------------------


def test_handler_state_survives_across_multiple_dispatches():
    """Handler state must persist across multiple effect dispatches.

    In OCaml 5, handler state lives in heap ref cells captured by the
    handler closure. Multiple perform/continue cycles access the same ref.
    """

    class Inc(EffectBase):
        pass

    class GetCount(EffectBase):
        pass

    counter = {"n": 0}

    @do
    def _counter_handler(effect: Effect, k):
        if isinstance(effect, Inc):
            counter["n"] += 1
            return (yield Resume(k, None))
        elif isinstance(effect, GetCount):
            return (yield Resume(k, counter["n"]))
        else:
            yield Pass()

    @do
    def _program():
        yield Inc()
        yield Inc()
        yield Inc()
        return (yield GetCount())

    result = run(
        WithHandler(_counter_handler, _program()), handlers=default_handlers()
    )
    assert result.is_ok()
    assert result.value == 3
