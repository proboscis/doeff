from __future__ import annotations

from doeff import Get, Put, Try, do
from tests._run_helpers import run_with_defaults


def _meta_for(fn: object) -> dict[str, object]:
    code = getattr(fn, "__code__", None)
    assert code is not None
    return {
        "function_name": code.co_name,
        "source_file": code.co_filename,
        "source_line": code.co_firstlineno,
    }


def test_try_finally_runs_on_normal_return() -> None:
    @do
    def program():
        yield Put("cleaned", False)
        try:
            return "ok"
        finally:
            yield Put("cleaned", True)

    @do
    def wrapper():
        value = yield program()
        cleaned = yield Get("cleaned")
        return value, cleaned

    result = run_with_defaults(wrapper(), store={})
    assert result.value == ("ok", True)


def test_try_finally_runs_on_exception() -> None:
    @do
    def failing():
        yield Put("cleaned", False)
        try:
            raise ValueError("boom")
        finally:
            yield Put("cleaned", True)

    @do
    def wrapper():
        outcome = yield Try(failing())
        cleaned = yield Get("cleaned")
        return outcome, cleaned

    result = run_with_defaults(wrapper(), store={})
    outcome, cleaned = result.value
    assert outcome.is_err()
    assert isinstance(outcome.error, ValueError)
    assert cleaned is True


def test_nested_try_finally_cleanups_are_lifo() -> None:
    @do
    def program():
        yield Put("trace", [])
        try:
            try:
                xs = yield Get("trace")
                yield Put("trace", [*xs, "body"])
                return "done"
            finally:
                xs = yield Get("trace")
                yield Put("trace", [*xs, "inner"])
        finally:
            xs = yield Get("trace")
            yield Put("trace", [*xs, "outer"])

    result = run_with_defaults(program(), store={})
    assert result.value == "done"


def test_try_finally_cleanup_can_perform_effects() -> None:
    @do
    def program():
        yield Put("counter", 1)
        try:
            n = yield Get("counter")
            yield Put("counter", (n or 0) + 1)
            return (yield Get("counter"))
        finally:
            n = yield Get("counter")
            yield Put("counter", (n or 0) + 10)

    result = run_with_defaults(program(), store={})
    # After body+finally: 1 → 2 (body) → 12 (finally). Body returns current value=2.
    assert result.value == 2


def test_cleanup_exception_does_not_swallow_original_exception() -> None:
    @do
    def _cleanup_raises():
        raise RuntimeError("cleanup exploded")
        yield  # pragma: no cover

    @do
    def failing():
        try:
            raise ValueError("original boom")
        finally:
            yield _cleanup_raises()

    @do
    def wrapper():
        return (yield Try(failing()))

    result = run_with_defaults(wrapper(), store={})
    assert result.value.is_err()
    assert isinstance(result.value.error, RuntimeError)
    assert "cleanup exploded" in str(result.value.error)
