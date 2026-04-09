"""Test: WithObserve must see ALL effects, including those yielded from handler bodies.

Bug: When a handler body yields a slog/Tell effect, WithObserve (placed outside
the handler chain) does not see it. The slog is consumed by slog_handler/writer
before reaching the observer.

Expected: WithObserve observes every effect that passes through the VM,
regardless of whether it's yielded from the main program or from a handler body.
"""
from doeff import do, run, WithHandler, WithObserve, Resume, Pass
from doeff_core_effects import Ask, Try, slog, Tell
from doeff_core_effects.handlers import (
    reader, state, writer, try_handler, slog_handler,
)
from doeff_core_effects.memo_effects import MemoExists, MemoGet, MemoPut, MemoExistsEffect, MemoGetEffect, MemoPutEffect
from doeff_core_effects.memo_handlers import (
    memo_handler, make_memo_rewriter, in_memory_memo_handler,
)
from doeff_vm import EffectBase, Callable as VMCallable


class CustomQuery(EffectBase):
    def __init__(self, prompt):
        self.prompt = prompt


@do
def custom_handler(effect, k):
    """Handler that emits slog from its body."""
    if isinstance(effect, CustomQuery):
        yield slog(msg=f"handling query: {effect.prompt}")
        yield Tell(f"tell from handler: {effect.prompt}")
        return (yield Resume(k, f"result({effect.prompt})"))
    yield Pass(effect, k)


def test_observe_sees_program_effects():
    """Observer sees effects from the main program."""
    observed = []

    def observer(effect):
        observed.append(type(effect).__name__)

    @do
    def prog():
        yield slog(msg="from program")
        yield Tell("tell from program")
        return 42

    wrapped = prog()
    for h in reversed([state(), writer(), slog_handler()]):
        wrapped = WithHandler(h, wrapped)
    wrapped = WithObserve(VMCallable(observer), wrapped)

    result = run(wrapped)
    assert result == 42
    assert "WriterTellEffect" in observed or "Slog" in observed, f"observed: {observed}"


def test_observe_sees_handler_body_effects():
    """Observer sees slog/Tell yielded from a handler body.

    This is the critical test: effects from handler bodies must be
    visible to WithObserve, not just effects from the main program.
    """
    observed = []

    def observer(effect):
        if hasattr(effect, 'msg'):
            observed.append(f"{type(effect).__name__}:{effect.msg}")
        else:
            observed.append(type(effect).__name__)

    @do
    def prog():
        result = yield CustomQuery("hello")
        return result

    wrapped = prog()
    for h in reversed([state(), writer(), try_handler, slog_handler(), custom_handler]):
        wrapped = WithHandler(h, wrapped)
    wrapped = WithObserve(VMCallable(observer), wrapped)

    result = run(wrapped)
    assert result == "result(hello)"

    # Observer should have seen the slog and Tell from handler body
    slog_msgs = [o for o in observed if "handling query" in o]
    tell_msgs = [o for o in observed if "tell from handler" in o]
    assert slog_msgs, f"Observer missed handler body slog. Observed: {observed}"
    assert tell_msgs, f"Observer missed handler body Tell. Observed: {observed}"


def test_observe_sees_cache_handler_effects():
    """Observer sees MemoExists/MemoGet/MemoPut from cache_handler."""
    observed = []

    def observer(effect):
        observed.append(type(effect).__name__)

    @do
    def prog():
        yield MemoPut("key1", "value1")
        exists = yield MemoExists("key1")
        assert exists
        value = yield MemoGet("key1")
        assert value == "value1"
        return "done"

    wrapped = prog()
    for h in reversed([state(), writer(), slog_handler(), in_memory_memo_handler()]):
        wrapped = WithHandler(h, wrapped)
    wrapped = WithObserve(VMCallable(observer), wrapped)

    result = run(wrapped)
    assert result == "done"
    assert "MemoPutEffect" in observed, f"observed: {observed}"
    assert "MemoExistsEffect" in observed, f"observed: {observed}"
    assert "MemoGetEffect" in observed, f"observed: {observed}"


def test_observe_sees_memo_rewriter_effects():
    """Observer sees effects from make_memo_rewriter (slog + cache effects)."""
    observed_msgs = []

    def observer(effect):
        if hasattr(effect, 'msg') and effect.msg:
            observed_msgs.append(effect.msg)

    @do
    def custom_handler_for_memo(effect, k):
        if isinstance(effect, CustomQuery):
            return (yield Resume(k, f"result({effect.prompt})"))
        yield Pass(effect, k)

    memo = make_memo_rewriter(CustomQuery)

    @do
    def prog():
        r1 = yield CustomQuery("hello")  # miss
        r2 = yield CustomQuery("hello")  # hit
        return (r1, r2)

    wrapped = prog()
    for h in reversed([
        state(), writer(), slog_handler(),
        in_memory_memo_handler(),
        custom_handler_for_memo,
        memo,
    ]):
        wrapped = WithHandler(h, wrapped)
    wrapped = WithObserve(VMCallable(observer), wrapped)

    result = run(wrapped)
    assert result == ("result(hello)", "result(hello)")

    # Observer should see [memo] checking/MISS/STORED/HIT messages
    memo_msgs = [m for m in observed_msgs if "[memo]" in m]
    assert any("MISS" in m for m in memo_msgs), f"No MISS log. msgs: {memo_msgs}"
    assert any("HIT" in m for m in memo_msgs), f"No HIT log. msgs: {memo_msgs}"
