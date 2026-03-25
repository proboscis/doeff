"""Minimum reproduction: Try effect is not dispatched by VM.

Try(program) should be caught by try_handler and return Ok/Err.
Currently fails because Try does not satisfy the VM effect protocol.
"""
from doeff import do, run, WithHandler
from doeff_core_effects import Try
from doeff_core_effects.handlers import try_handler
from doeff_vm import Ok, Err


def test_try_catches_error():
    """yield Try(failing_program) should return Err, not raise."""

    @do
    def failing():
        raise ValueError("boom")

    @do
    def prog():
        result = yield Try(failing())
        return result

    result = run(WithHandler(try_handler, prog()))
    assert isinstance(result, Err)
    assert isinstance(result.error, ValueError)


def test_try_returns_ok_on_success():
    """yield Try(succeeding_program) should return Ok(value)."""

    @do
    def succeeding():
        return 42

    @do
    def prog():
        result = yield Try(succeeding())
        return result

    result = run(WithHandler(try_handler, prog()))
    assert isinstance(result, Ok)
    assert result.value == 42
