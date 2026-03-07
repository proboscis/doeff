from __future__ import annotations

from doeff import Apply, Get, Modify, Pure, Put, Try, default_handlers, do, run


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

    result = run(wrapper(), handlers=default_handlers(), store={})
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

    result = run(wrapper(), handlers=default_handlers(), store={})
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
                yield Modify("trace", lambda xs: [*xs, "body"])
                return "done"
            finally:
                yield Modify("trace", lambda xs: [*xs, "inner"])
        finally:
            yield Modify("trace", lambda xs: [*xs, "outer"])

    result = run(program(), handlers=default_handlers(), store={})
    assert result.value == "done"
    assert result.raw_store["trace"] == ["body", "inner", "outer"]


def test_try_finally_cleanup_can_perform_effects() -> None:
    @do
    def program():
        yield Put("counter", 1)
        try:
            yield Modify("counter", lambda n: (n or 0) + 1)
            return "done"
        finally:
            yield Modify("counter", lambda n: (n or 0) + 10)

    result = run(program(), handlers=default_handlers(), store={})
    assert result.value == "done"
    assert result.raw_store["counter"] == 12


def test_cleanup_exception_does_not_swallow_original_exception() -> None:
    def cleanup_raises() -> None:
        raise RuntimeError("cleanup exploded")

    @do
    def failing():
        try:
            raise ValueError("original boom")
        finally:
            yield Apply(Pure(cleanup_raises), [], {}, _meta_for(cleanup_raises))

    @do
    def wrapper():
        return (yield Try(failing()))

    result = run(wrapper(), handlers=default_handlers(), store={})
    assert result.value.is_err()
    assert isinstance(result.value.error, RuntimeError)
    assert "cleanup exploded" in str(result.value.error)
    assert isinstance(result.value.error.__context__, ValueError)
    assert "original boom" in str(result.value.error.__context__)
