from __future__ import annotations

from doeff import Program, do
from doeff.effects import Put
from doeff.rust_vm import default_handlers, run


def test_run_result_traceback_data_is_none_on_success() -> None:
    @do
    def body() -> Program[int]:
        return 7
        yield

    result = run(body(), handlers=default_handlers())
    assert result.is_ok(), result.error
    assert result.traceback_data is None


def test_run_result_exposes_typed_traceback_data_without_exception_dunders() -> None:
    @do
    def body() -> Program[int]:
        yield Put("x", 1)
        raise ValueError("boom")
        yield

    result = run(body(), handlers=default_handlers(), store={"x": 0})
    assert result.is_err()

    traceback_data = result.traceback_data
    assert traceback_data is not None
    assert type(traceback_data).__name__ == "DoeffTracebackData"
    assert isinstance(traceback_data.entries, list)
    assert traceback_data.entries

    error = result.error
    assert isinstance(error, ValueError)
    assert not hasattr(error, "__doeff_traceback_data__")
    assert not hasattr(error, "__doeff_traceback__")
