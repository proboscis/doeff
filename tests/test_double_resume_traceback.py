"""Test: double Resume on the same continuation must produce a useful traceback.

Currently "Resume: continuation already consumed" gives NO information about
where the first consumption happened. This makes debugging impossible.

Goal: the error should include both:
  - Where the continuation was first consumed (first Resume)
  - Where the second Resume was attempted (crash site)
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from doeff import (
    EffectBase,
    EffectGenerator,
    Pass,
    Resume,
    WithHandler,
    do,
    run,
)
from doeff_core_effects.handlers import writer, try_handler, state
from doeff_core_effects.scheduler import scheduled


@dataclass(frozen=True)
class Ping(EffectBase):
    value: int


@do
def double_resume_handler(effect, k):
    """Handler that incorrectly resumes k twice."""
    if not isinstance(effect, Ping):
        yield Pass(effect, k)
        return
    # First Resume — correct
    yield Resume(k, effect.value * 10)
    # Second Resume — BUG: k already consumed
    yield Resume(k, effect.value * 20)


@do
def program_single_ping() -> EffectGenerator[int]:
    return (yield Ping(value=5))


def _run(program):
    wrapped = WithHandler(writer(), WithHandler(try_handler,
             WithHandler(state(), WithHandler(double_resume_handler, program))))
    return run(scheduled(wrapped))


class TestDoubleResume:

    def test_double_resume_raises(self):
        """Double Resume must raise, not silently succeed."""
        with pytest.raises(RuntimeError, match="continuation already consumed"):
            _run(program_single_ping())

    def test_error_message_includes_current_fiber(self):
        """Error must include the current fiber ID for diagnostics.

        The one-shot violation is now detected in VM core's continue_k,
        which has access to self.current_segment. The error includes
        the fiber ID so the doeff traceback can identify the handler.
        """
        try:
            _run(program_single_ping())
            pytest.fail("Expected RuntimeError")
        except RuntimeError as e:
            msg = str(e)
            assert "continuation already consumed" in msg
            assert "current fiber=" in msg
