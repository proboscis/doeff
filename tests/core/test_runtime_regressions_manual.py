from __future__ import annotations

import doeff_vm
import pytest

from doeff import (
    Ask,
    Err,
    Gather,
    Get,
    Local,
    MissingEnvKeyError,
    Ok,
    Safe,
    Spawn,
    default_handlers,
    do,
    run,
)
from doeff.effects import TaskCompleted


def _rust_ok_err_classes() -> tuple[type, type]:
    rust_ok = doeff_vm.Ok
    rust_err = doeff_vm.Err
    assert rust_ok is not None
    assert rust_err is not None
    return rust_ok, rust_err


def test_rust_ok_err_pyclass_constructors() -> None:
    rust_ok, rust_err = _rust_ok_err_classes()
    ok = rust_ok(1)
    err = rust_err(ValueError("x"))

    assert ok.is_ok() is True
    assert ok.is_err() is False
    assert bool(ok) is True
    assert ok.value == 1

    assert err.is_ok() is False
    assert err.is_err() is True
    assert bool(err) is False
    assert isinstance(err.error, ValueError)
    assert str(err.error) == "x"
    assert err.captured_traceback is None


def test_try_except_catches_yielded_program_error() -> None:
    @do
    def boom():
        raise ValueError("x")

    @do
    def program():
        try:
            _ = yield boom()
            return ("unexpected",)
        except Exception as exc:
            return ("caught", type(exc).__name__, str(exc))

    result = run(program(), handlers=default_handlers())
    assert result.is_ok()
    assert result.value == ("caught", "ValueError", "x")


def test_safe_wraps_error_as_result_value() -> None:
    @do
    def boom():
        raise ValueError("x")

    @do
    def program():
        return (yield Safe(boom()))

    result = run(program(), handlers=default_handlers())
    assert result.is_ok()

    value = result.value
    assert type(value).__name__ == "Err"
    assert value.is_err() is True
    assert isinstance(value.error, ValueError)
    assert str(value.error) == "x"


def test_safe_gather_and_run_result_share_rust_ok_err_surface() -> None:
    rust_ok, rust_err = _rust_ok_err_classes()

    @do
    def succeeds():
        if False:
            yield
        return 10

    @do
    def fails():
        raise ValueError("boom")

    @do
    def program():
        t1 = yield Spawn(Safe(succeeds()))
        t2 = yield Spawn(Safe(fails()))
        return (yield Gather(t1, t2))

    result = run(program(), handlers=default_handlers())
    assert result.is_ok()

    outer = result.result
    assert isinstance(outer, Ok)
    assert isinstance(outer, rust_ok)

    values = result.value
    assert len(values) == 2

    first, second = values
    assert isinstance(first, Ok)
    assert isinstance(first, rust_ok)
    assert first.value == 10

    assert isinstance(second, Err)
    assert isinstance(second, rust_err)
    assert isinstance(second.error, ValueError)
    assert str(second.error) == "boom"


def test_lazy_ask_evaluates_program_env_once_per_run() -> None:
    calls = {"service": 0}

    @do
    def service_program():
        calls["service"] += 1
        if False:
            yield
        return 42

    @do
    def program():
        first = yield Ask("service")
        second = yield Ask("service")
        return (first, second)

    result = run(program(), handlers=default_handlers(), env={"service": service_program()})
    assert result.is_ok()
    assert result.value == (42, 42)
    assert calls["service"] == 1


def test_lazy_ask_local_override_is_enabled_and_cached() -> None:
    calls = {"outer": 0, "inner": 0}

    @do
    def outer_service():
        calls["outer"] += 1
        if False:
            yield
        return "outer"

    @do
    def inner_service():
        calls["inner"] += 1
        if False:
            yield
        return "inner"

    @do
    def local_scope():
        first = yield Ask("service")
        second = yield Ask("service")
        return (first, second)

    @do
    def program():
        outer_before = yield Ask("service")
        local_values = yield Local({"service": inner_service()}, local_scope())
        outer_after = yield Ask("service")
        return (outer_before, local_values, outer_after)

    result = run(program(), handlers=default_handlers(), env={"service": outer_service()})
    assert result.is_ok()
    assert result.value == ("outer", ("inner", "inner"), "outer")
    assert calls["outer"] == 1
    assert calls["inner"] == 1


def test_lazy_ask_program_error_propagates() -> None:
    @do
    def failing_service():
        raise ValueError("lazy boom")

    @do
    def program():
        return (yield Ask("service"))

    result = run(program(), handlers=default_handlers(), env={"service": failing_service()})
    assert result.is_err()
    assert isinstance(result.error, ValueError)
    assert str(result.error) == "lazy boom"


def test_lazy_ask_safe_captures_program_error() -> None:
    @do
    def failing_service():
        raise ValueError("lazy boom")

    @do
    def program():
        return (yield Safe(Ask("service")))

    result = run(program(), handlers=default_handlers(), env={"service": failing_service()})
    assert result.is_ok()
    safe_result = result.value
    assert safe_result.is_err()
    assert isinstance(safe_result.error, ValueError)
    assert str(safe_result.error) == "lazy boom"


def test_ask_missing_key_raises_missing_env_key_error() -> None:
    @do
    def program():
        return (yield Ask("missing"))

    result = run(program(), handlers=default_handlers(), env={})
    assert result.is_err()
    assert isinstance(result.error, MissingEnvKeyError)
    assert isinstance(result.error, KeyError)


def test_ask_missing_key_error_includes_helpful_hint() -> None:
    @do
    def program():
        return (yield Ask("service"))

    result = run(program(), handlers=default_handlers(), env={})
    assert result.is_err()
    assert isinstance(result.error, MissingEnvKeyError)

    message = str(result.error)
    assert "Environment key not found: 'service'" in message
    assert "Provide this key via `env={'service': value}`" in message
    assert "Local({'service': value}, ...)" in message


def test_get_missing_key_raises_key_error() -> None:
    @do
    def program():
        return (yield Get("missing"))

    result = run(program(), handlers=default_handlers())
    assert result.is_err()
    assert isinstance(result.error, KeyError)


def test_scheduler_task_completed_uses_single_result_payload() -> None:
    rust_ok, _ = _rust_ok_err_classes()
    task_id = 9999

    payload = TaskCompleted(task_id=task_id, result=rust_ok(123))
    assert payload.result.is_ok() is True
    assert not hasattr(payload, "error")

    @do
    def rejects_non_result_payload():
        _ = yield TaskCompleted(task_id=task_id, result=123)
        return "unexpected"

    bad_result = run(rejects_non_result_payload(), handlers=default_handlers())
    assert bad_result.is_err()
    assert isinstance(bad_result.error, TypeError)
    assert "TaskCompleted.result must be Ok(...) or Err(...)" in str(bad_result.error)

    with pytest.raises(TypeError):
        _ = TaskCompleted(task_id=task_id, error=ValueError("x"))
