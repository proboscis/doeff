"""Test: WithObserve must see ALL effects, including those yielded from handler bodies.

Bug: When a handler body yields a slog/Tell effect, WithObserve (placed outside
the handler chain) does not see it. The slog is consumed by slog_handler/writer
before reaching the observer.

Expected: WithObserve observes every effect that passes through the VM,
regardless of whether it's yielded from the main program or from a handler body.
"""
from doeff_core_effects import Tell, slog
from doeff_core_effects.handlers import (
    slog_handler,
    state,
    try_handler,
    writer,
)
from doeff_vm import Callable as VMCallable
from doeff_vm import EffectBase

from doeff import Pass, Resume, WithObserve, do, run
from doeff import handler as _program_handler


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
    for h in reversed([state(), writer, slog_handler]):
        wrapped = _program_handler(h)(wrapped)
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
        if hasattr(effect, "msg"):
            observed.append(f"{type(effect).__name__}:{effect.msg}")
        else:
            observed.append(type(effect).__name__)

    @do
    def prog():
        result = yield CustomQuery("hello")
        return result

    wrapped = prog()
    for h in reversed([state(), writer, try_handler, slog_handler, custom_handler]):
        wrapped = _program_handler(h)(wrapped)
    wrapped = WithObserve(VMCallable(observer), wrapped)

    result = run(wrapped)
    assert result == "result(hello)"

    # Observer should have seen the slog and Tell from handler body
    slog_msgs = [o for o in observed if "handling query" in o]
    tell_msgs = [o for o in observed if "tell from handler" in o]
    assert slog_msgs, f"Observer missed handler body slog. Observed: {observed}"
    assert tell_msgs, f"Observer missed handler body Tell. Observed: {observed}"
