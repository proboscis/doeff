from __future__ import annotations

from dataclasses import dataclass

import pytest

from doeff import (
    Apply,
    Finally,
    Get,
    Modify,
    Pass,
    Pure,
    Put,
    Resume,
    Transfer,
    Try,
    WithHandler,
    default_handlers,
    do,
    run,
)
from doeff.types import EffectBase


def _meta_for(fn: object) -> dict[str, object]:
    code = getattr(fn, "__code__", None)
    assert code is not None
    return {
        "function_name": code.co_name,
        "source_file": code.co_filename,
        "source_line": code.co_firstlineno,
    }


def test_finally_runs_on_normal_return() -> None:
    @do
    def program():
        yield Put("cleaned", False)
        yield Finally(Put("cleaned", True))
        return "ok"

    @do
    def wrapper():
        value = yield program()
        cleaned = yield Get("cleaned")
        return value, cleaned

    result = run(wrapper(), handlers=default_handlers(), store={})
    assert result.value == ("ok", True)


def test_finally_runs_on_exception() -> None:
    @do
    def failing():
        yield Put("cleaned", False)
        yield Finally(Put("cleaned", True))
        raise ValueError("boom")

    @do
    def wrapper():
        outcome = yield Try(failing())
        cleaned = yield Get("cleaned")
        return outcome, cleaned

    result = run(wrapper(), handlers=default_handlers(), store={})
    outcome, cleaned = result.value
    assert outcome.is_err()
    assert isinstance(outcome.error, ValueError)
    assert cleaned is True


def test_multiple_finally_cleanups_are_lifo() -> None:
    @do
    def program():
        yield Put("trace", [])
        yield Finally(Modify("trace", lambda xs: [*xs, "outer"]))
        yield Finally(Modify("trace", lambda xs: [*xs, "inner"]))
        yield Modify("trace", lambda xs: [*xs, "body"])
        return "done"

    result = run(program(), handlers=default_handlers(), store={})
    assert result.value == "done"
    assert result.raw_store["trace"] == ["body", "inner", "outer"]


def test_finally_cleanup_can_perform_effects() -> None:
    @do
    def program():
        yield Put("counter", 1)
        yield Finally(Modify("counter", lambda n: (n or 0) + 10))
        yield Modify("counter", lambda n: (n or 0) + 1)
        return "done"

    result = run(program(), handlers=default_handlers(), store={})
    assert result.value == "done"
    assert result.raw_store["counter"] == 12


def test_finally_travels_with_resumed_continuation() -> None:
    @dataclass(frozen=True, kw_only=True)
    class Ping(EffectBase):
        label: str

    def ping_handler(effect: object, k: object):
        if isinstance(effect, Ping):
            return (yield Resume(k, f"handled:{effect.label}"))
        yield Pass()

    @do
    def program():
        yield Put("cleaned", False)
        yield Finally(Put("cleaned", True))
        value = yield Ping(label="x")
        return value

    @do
    def wrapper():
        value = yield WithHandler(ping_handler, program())
        cleaned = yield Get("cleaned")
        return value, cleaned

    result = run(wrapper(), handlers=default_handlers(), store={})
    assert result.value == ("handled:x", True)


def test_finally_travels_with_transferred_continuation() -> None:
    @dataclass(frozen=True, kw_only=True)
    class Ping(EffectBase):
        label: str

    def ping_handler(effect: object, k: object):
        if isinstance(effect, Ping):
            yield Transfer(k, f"handled:{effect.label}")
        yield Pass()

    @do
    def program():
        yield Put("cleaned", False)
        yield Finally(Put("cleaned", True))
        value = yield Ping(label="x")
        return value

    @do
    def wrapper():
        value = yield WithHandler(ping_handler, program())
        cleaned = yield Get("cleaned")
        return value, cleaned

    result = run(wrapper(), handlers=default_handlers(), store={})
    assert result.value == ("handled:x", True)


def test_abandoned_continuation_with_finally_logs_warning(
    capfd: pytest.CaptureFixture[str],
) -> None:
    @dataclass(frozen=True, kw_only=True)
    class Ping(EffectBase):
        label: str

    def abandon_handler(effect: object, k: object):
        if isinstance(effect, Ping):
            return "abandoned"
        yield Pass()

    @do
    def program():
        yield Finally(Put("cleaned", True))
        _ = yield Ping(label="x")
        return "unreachable"

    result = run(
        WithHandler(abandon_handler, program()),
        handlers=default_handlers(),
        store={},
    )
    assert result.value == "abandoned"

    captured = capfd.readouterr()
    assert "warning: continuation" in captured.err
    assert "Finally cleanup" in captured.err


def test_cleanup_exception_does_not_swallow_original_exception() -> None:
    def cleanup_raises() -> None:
        raise RuntimeError("cleanup exploded")

    @do
    def failing():
        yield Finally(Apply(Pure(cleanup_raises), [], {}, _meta_for(cleanup_raises)))
        raise ValueError("original boom")

    @do
    def wrapper():
        return (yield Try(failing()))

    result = run(wrapper(), handlers=default_handlers(), store={})
    assert result.value.is_err()
    assert isinstance(result.value.error, ValueError)
    assert "original boom" in str(result.value.error)
    assert isinstance(result.value.error.__context__, RuntimeError)
    assert "cleanup exploded" in str(result.value.error.__context__)
