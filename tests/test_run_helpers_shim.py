"""Guards for the legacy-runner shim in :mod:`tests._run_helpers`.

Seven ``packages/*/tests`` trees route their pre-rebuild call sites through
``run_with_defaults``, so a swallow-everything ``except`` or a swallow-every-
kwarg ``**kwargs`` there is a failure mode for all of them at once.
"""

from __future__ import annotations

import pytest
from _pytest.outcomes import Failed, Skipped

from doeff import Pure, do
from tests._run_helpers import run_with_defaults


@do
def _raises(exc: BaseException):
    yield Pure(None)
    raise exc


@do
def _ok(value: int):
    return (yield Pure(value))


def test_ordinary_exception_becomes_err() -> None:
    result = run_with_defaults(_raises(ValueError("boom")))
    assert result.is_err()
    assert isinstance(result.error, ValueError)


def test_pytest_skip_is_not_swallowed_into_err() -> None:
    """``pytest.skip()`` inside a program must skip the test, not return Err."""
    with pytest.raises(Skipped):
        run_with_defaults(_raises(Skipped(msg="needs a key")))


def test_pytest_fail_is_not_swallowed_into_err() -> None:
    with pytest.raises(Failed):
        run_with_defaults(_raises(Failed(msg="explicit failure")))


def test_keyboard_interrupt_is_not_swallowed_into_err() -> None:
    """A long run must stay interruptible with Ctrl-C."""
    with pytest.raises(KeyboardInterrupt):
        run_with_defaults(_raises(KeyboardInterrupt()))


def test_known_legacy_kwargs_are_absorbed() -> None:
    result = run_with_defaults(_ok(7), trace=True, print_doeff_trace=False)
    assert result.is_ok()
    assert result.value == 7


def test_misspelled_kwarg_raises_instead_of_running_without_it() -> None:
    """``enV=`` must not silently run the program with no environment."""
    with pytest.raises(TypeError, match="enV"):
        run_with_defaults(_ok(7), enV={"k": "v"})
