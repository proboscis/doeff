"""Test: Try handler must preserve FULL handler chain for inner program.

When try_handler handles Try(program), the inner program must execute
with access to ALL handlers in the chain — both outer AND inner relative
to try_handler's position.

Bug reproduction: if a custom effect handler is installed INNER to try_handler,
effects performed inside Try(program) cannot reach that inner handler.
"""
from doeff import do, run, WithHandler, Resume, Pass
from doeff_core_effects import Ask, Try, slog
from doeff_core_effects.handlers import reader, try_handler, slog_handler, writer
from doeff_vm import EffectBase


class CustomEffect(EffectBase):
    """A custom effect for testing handler chain scope."""
    def __init__(self, data):
        self.data = data

    def __repr__(self):
        return f"CustomEffect({self.data!r})"


@do
def custom_handler(effect, k):
    if isinstance(effect, CustomEffect):
        result = yield Resume(k, f"handled:{effect.data}")
        return result
    yield Pass(effect, k)


def test_ask_inside_try_with_reader_outer():
    """Ask inside Try — reader is OUTER to try_handler."""
    @do
    def inner():
        model = yield Ask("llm_model")
        return f"model={model}"

    @do
    def prog():
        result = yield Try(inner())
        return result

    env = {"llm_model": "gpt-5.4"}
    wrapped = prog()
    for h in reversed([reader(env=env), try_handler]):
        wrapped = WithHandler(h, wrapped)

    result = run(wrapped)
    assert result.value == "model=gpt-5.4"


def test_custom_effect_inside_try_handler_inner():
    """Custom effect handler INNER to try_handler — effect inside Try must reach it.

    This is the critical bug: handler chain order is
        reader (outer) → try_handler → custom_handler (inner)
    Try(inner()) performs CustomEffect, which should reach custom_handler.
    """
    @do
    def inner():
        model = yield Ask("model")
        result = yield CustomEffect(model)
        return result

    @do
    def prog():
        result = yield Try(inner())
        return result

    env = {"model": "gpt-5.4"}
    wrapped = prog()
    # reader (outer) → try_handler → custom_handler (inner)
    for h in reversed([reader(env=env), try_handler, custom_handler]):
        wrapped = WithHandler(h, wrapped)

    result = run(wrapped)
    assert result.value == "handled:gpt-5.4"


def test_custom_effect_inside_try_handler_outer():
    """Custom effect handler OUTER to try_handler — should also work."""
    @do
    def inner():
        result = yield CustomEffect("test")
        return result

    @do
    def prog():
        result = yield Try(inner())
        return result

    wrapped = prog()
    # custom_handler (outer) → try_handler (inner)
    for h in reversed([custom_handler, try_handler]):
        wrapped = WithHandler(h, wrapped)

    result = run(wrapped)
    assert result.value == "handled:test"


def test_multiple_effects_inside_try_mixed_handlers():
    """Multiple effects inside Try, handled by different handlers at different levels."""
    @do
    def inner():
        model = yield Ask("model")        # handled by reader (outer)
        yield slog(msg="testing")         # handled by writer (outer)
        result = yield CustomEffect(model) # handled by custom (inner)
        return result

    @do
    def prog():
        result = yield Try(inner())
        return result

    env = {"model": "gpt-5.4"}
    wrapped = prog()
    # reader (outer) → writer → try_handler → custom_handler (inner)
    for h in reversed([reader(env=env), writer(), try_handler, custom_handler]):
        wrapped = WithHandler(h, wrapped)

    result = run(wrapped)
    assert result.value == "handled:gpt-5.4"


def test_try_error_with_inner_handler():
    """Try still catches errors even with inner handlers."""
    @do
    def inner():
        yield CustomEffect("before")
        raise ValueError("boom")

    @do
    def prog():
        result = yield Try(inner())
        return result

    wrapped = prog()
    for h in reversed([try_handler, custom_handler]):
        wrapped = WithHandler(h, wrapped)

    result = run(wrapped)
    assert result.is_err()
    assert isinstance(result.error, ValueError)
