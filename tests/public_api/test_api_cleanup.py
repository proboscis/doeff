"""Public API cleanup regression tests for ISSUE-CORE-499."""

from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    "name",
    [
        "run_program",
        "ProgramRunResult",
    ],
)
def test_removed_api_not_importable(name: str) -> None:
    """Removed symbols must not be in doeff.__all__ or importable."""
    import doeff

    assert name not in doeff.__all__
    assert not hasattr(doeff, name)


def test_try_importable() -> None:
    """Try must be importable from doeff."""
    from doeff import Try, try_

    assert callable(Try)
    assert callable(try_)


def test_safe_not_in_all() -> None:
    """Safe must not be in doeff.__all__."""
    import doeff

    assert "Safe" not in doeff.__all__
    assert "safe" not in doeff.__all__


def test_try_catches_error() -> None:
    """Try wraps sub-program errors into Result."""
    from doeff import Try, default_handlers, do, run

    @do
    def failing():
        raise ValueError("boom")
        yield  # pragma: no cover

    @do
    def program():
        result = yield Try(failing())
        return result

    result = run(program(), handlers=default_handlers())
    assert result.is_ok()
    assert result.value.is_err()
    assert isinstance(result.value.error, ValueError)


def test_internal_helpers_not_in_all() -> None:
    """Internal helpers must not appear in __all__."""
    import doeff

    internals = [
        "run_program",
        "ProgramRunResult",
        "DoCtrl",
        "DoExpr",
        "GeneratorProgram",
        "nonblocking_await_handler",
        "with_nonblocking_await",
    ]
    for name in internals:
        assert name not in doeff.__all__, f"{name} should not be in __all__"


@pytest.mark.parametrize(
    "name",
    [
        "do",
        "run",
        "async_run",
        "Program",
        "KleisliProgram",
        "Ask",
        "Put",
        "Get",
        "Spawn",
        "Gather",
        "Wait",
        "Try",
        "Perform",
        "Resume",
        "WithHandler",
        "default_handlers",
        "RunResult",
        "Result",
        "Ok",
        "Err",
    ],
)
def test_core_api_importable(name: str) -> None:
    """All core public API symbols must be importable from doeff."""
    import doeff

    assert hasattr(doeff, name), f"{name} not found in doeff"
    assert name in doeff.__all__, f"{name} not in doeff.__all__"
