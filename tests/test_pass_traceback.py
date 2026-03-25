"""Test: 'Pass: no outer handler found' must include the effect type and call site.

Currently the error message is just:
  RuntimeError: internal error: Pass: no outer handler found

It should include:
  - Which effect was not handled (type name + repr)
  - Where in user code the effect was performed (file, line, function)
"""
from doeff import do, run, WithHandler, Resume, Pass
from doeff_vm import EffectBase
import pytest


class Handled(EffectBase):
    """An effect that WILL be handled."""
    pass


class Unhandled(EffectBase):
    """An effect that will NOT be handled."""
    def __repr__(self):
        return "Unhandled()"


@do
def handler(effect, k):
    if isinstance(effect, Handled):
        result = yield Resume(k, 42)
        return result
    yield Pass(effect, k)


def test_pass_error_includes_effect_type():
    """The error message must name the unhandled effect type."""

    @do
    def prog():
        yield Unhandled()
        return "unreachable"

    with pytest.raises(RuntimeError, match="Unhandled"):
        run(WithHandler(handler, prog()))


def test_pass_error_after_successful_handle():
    """Handler handles first effect, then second effect is unhandled."""

    @do
    def prog():
        x = yield Handled()      # handled → 42
        yield Unhandled()         # not handled → should error with effect info
        return x

    with pytest.raises(RuntimeError, match="Unhandled"):
        run(WithHandler(handler, prog()))


def test_no_handler_at_all():
    """Effect performed with no handlers installed at all."""

    @do
    def prog():
        yield Unhandled()
        return "unreachable"

    with pytest.raises(RuntimeError, match="Unhandled"):
        run(prog())
