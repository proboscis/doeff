from __future__ import annotations

from doeff import EffectBase, Pass, Pure, Resume, WithHandler, default_handlers, do, run


class _Ping(EffectBase):
    def __init__(self, value: int) -> None:
        super().__init__()
        self.value = value


class _ClauseEffect(EffectBase):
    def __init__(self, value: str) -> None:
        super().__init__()
        self.value = value


def _passthrough_handler(_effect, _k):
    yield Pass()


def test_withhandler_without_return_clause_is_identity() -> None:
    @do
    def body():
        return "ok"

    result = run(WithHandler(_passthrough_handler, body()), handlers=default_handlers())
    assert result.value == "ok"


def test_withhandler_return_clause_transforms_body_result() -> None:
    @do
    def body():
        return 7

    result = run(
        WithHandler(
            _passthrough_handler,
            body(),
            return_clause=lambda x: Pure(f"wrapped:{x}"),
        ),
        handlers=default_handlers(),
    )
    assert result.value == "wrapped:7"


def test_withhandler_return_clause_can_return_effectful_doexpr() -> None:
    @do
    def body():
        return "base"

    def clause_handler(effect, k):
        if isinstance(effect, _ClauseEffect):
            return (yield Resume(k, f"{effect.value}-ok"))
        yield Pass()

    @do
    def clause_body(value):
        suffix = yield _ClauseEffect("suffix")
        return f"{value}:{suffix}"

    def return_clause(value):
        return WithHandler(clause_handler, clause_body(value))

    result = run(
        WithHandler(_passthrough_handler, body(), return_clause=return_clause),
        handlers=default_handlers(),
    )
    assert result.value == "base:suffix-ok"


def test_withhandler_return_clause_exception_propagates() -> None:
    @do
    def body():
        return 1

    def boom(_value):
        raise RuntimeError("return clause exploded")

    result = run(
        WithHandler(_passthrough_handler, body(), return_clause=boom),
        handlers=default_handlers(),
    )

    assert result.is_err()
    assert isinstance(result.error, RuntimeError)
    assert "return clause exploded" in str(result.error)


def test_withhandler_resume_and_return_clause_interaction() -> None:
    def ping_handler(effect, k):
        if isinstance(effect, _Ping):
            resumed = yield Resume(k, effect.value + 1)
            return resumed * 2
        yield Pass()

    @do
    def body():
        value = yield _Ping(10)
        return value + 3

    result = run(
        WithHandler(
            ping_handler,
            body(),
            return_clause=lambda x: Pure(x + 5),
        ),
        handlers=default_handlers(),
    )

    assert result.value == 38
