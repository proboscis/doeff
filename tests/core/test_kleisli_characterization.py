from __future__ import annotations

import pytest

from doeff import (
    Ask,
    Delegate,
    Effect,
    EffectBase,
    Get,
    Listen,
    Pass,
    Put,
    Resume,
    Tell,
    WithHandler,
    WithIntercept,
    default_handlers,
    do,
    run,
)


class Ping(EffectBase):
    def __init__(self, payload: str) -> None:
        super().__init__()
        self.payload = payload


class Pong(EffectBase):
    def __init__(self, payload: str) -> None:
        super().__init__()
        self.payload = payload


def _prog(gen_factory):
    @do
    def _wrapped():
        return (yield from gen_factory())

    return _wrapped()


class TestPlainGeneratorHandler:
    def test_resume_returns_value_to_body(self) -> None:
        def handler(effect, k):
            if isinstance(effect, Ping):
                return (yield Resume(k, f"pong:{effect.payload}"))
            yield Delegate()

        def body():
            value = yield Ping("hello")
            return value

        def main():
            return (yield WithHandler(handler, _prog(body)))

        result = run(_prog(main), handlers=default_handlers())
        assert result.value == "pong:hello"

    def test_delegate_passes_to_outer(self) -> None:
        def inner_handler(_effect, _k):
            delegated = yield Delegate()
            return delegated

        def outer_handler(effect, k):
            if isinstance(effect, Ping):
                return (yield Resume(k, "from-outer"))
            yield Pass()

        def body():
            value = yield Ping("x")
            return value

        def main():
            inner = WithHandler(inner_handler, _prog(body))
            return (yield WithHandler(outer_handler, inner))

        result = run(_prog(main), handlers=default_handlers())
        assert result.value == "from-outer"

    def test_handler_can_yield_effects(self) -> None:
        def handler(effect, k):
            if isinstance(effect, Ping):
                state_val = yield Get("key")
                return (yield Resume(k, f"got:{state_val}"))
            yield Pass()

        def body():
            value = yield Ping("ignored")
            return value

        def main():
            return (yield WithHandler(handler, _prog(body)))

        result = run(_prog(main), handlers=default_handlers(), store={"key": "magic"})
        assert result.value == "got:magic"

    def test_multiple_effects_in_body(self) -> None:
        def handler(effect, k):
            if isinstance(effect, Ping):
                return (yield Resume(k, f"ping:{effect.payload}"))
            if isinstance(effect, Pong):
                return (yield Resume(k, f"pong:{effect.payload}"))
            yield Pass()

        def body():
            first = yield Ping("a")
            second = yield Pong("b")
            return f"{first}|{second}"

        def main():
            return (yield WithHandler(handler, _prog(body)))

        result = run(_prog(main), handlers=default_handlers())
        assert result.value == "ping:a|pong:b"


class TestRustBuiltinHandlers:
    def test_state_get_put(self) -> None:
        @do
        def program():
            yield Put("counter", 1)
            value = yield Get("counter")
            return value

        result = run(program(), handlers=default_handlers(), store={})
        assert result.value == 1

    def test_reader_ask(self) -> None:
        @do
        def program():
            value = yield Ask("name")
            return f"hello:{value}"

        result = run(program(), handlers=default_handlers(), env={"name": "world"})
        assert result.value == "hello:world"

    def test_writer_tell(self) -> None:
        @do
        def sub_program():
            yield Tell("entry")
            return "done"

        @do
        def program():
            listened = yield Listen(sub_program())
            return listened

        result = run(program(), handlers=default_handlers())
        assert result.value.value == "done"
        assert "entry" in result.value.log

    def test_state_with_custom_handler(self) -> None:
        def handler(effect, k):
            if isinstance(effect, Ping):
                before = yield Get("counter")
                yield Put("counter", before + 1)
                return (yield Resume(k, f"counter:{before}"))
            yield Pass()

        @do
        def body():
            from_handler = yield Ping("x")
            current = yield Get("counter")
            return f"{from_handler}|{current}"

        result = run(
            WithHandler(handler, body()),
            handlers=default_handlers(),
            store={"counter": 10},
        )
        assert result.value == "counter:10|11"


class TestDoHandlerPreKleisli:
    @pytest.mark.pre_kleisli_behavior
    def test_do_handler_returns_value(self) -> None:
        @do
        def handler(effect: Effect, _k):
            if isinstance(effect, Ping):
                return (yield Resume(_k, f"handled:{effect.payload}"))
            yield Delegate()

        def body():
            value = yield Ping("hello")
            return value

        def main():
            return (yield WithHandler(handler, _prog(body)))

        result = run(_prog(main), handlers=default_handlers())
        assert result.value == "handled:hello"

    @pytest.mark.pre_kleisli_behavior
    def test_do_handler_with_effects_returns_value(self) -> None:
        @do
        def handler(effect: Effect, _k):
            if isinstance(effect, Ping):
                state_val = yield Get("key")
                return (yield Resume(_k, f"handled:{state_val}"))
            yield Pass()

        def body():
            value = yield Ping("hello")
            return value

        def main():
            return (yield WithHandler(handler, _prog(body)))

        result = run(_prog(main), handlers=default_handlers(), store={"key": "magic"})
        assert result.value == "handled:magic"


class TestHandlerIdentity:
    def test_handler_does_not_handle_own_effects(self) -> None:
        def inner_handler(effect, k):
            if isinstance(effect, Ping):
                delegated = yield Ping("from-inner-body")
                return (yield Resume(k, f"inner:{delegated}"))
            yield Pass()

        def outer_handler(effect, k):
            if isinstance(effect, Ping):
                return (yield Resume(k, f"outer:{effect.payload}"))
            yield Pass()

        def body():
            value = yield Ping("from-user")
            return value

        result = run(
            WithHandler(outer_handler, WithHandler(inner_handler, _prog(body))),
            handlers=default_handlers(),
        )
        assert result.value == "inner:outer:from-inner-body"


class TestNestedWithHandler:
    def test_inner_handler_takes_priority(self) -> None:
        def inner_handler(effect, k):
            if isinstance(effect, Ping):
                return (yield Resume(k, "inner"))
            yield Pass()

        def outer_handler(effect, k):
            if isinstance(effect, Ping):
                return (yield Resume(k, "outer"))
            yield Pass()

        def body():
            return (yield Ping("x"))

        result = run(
            WithHandler(outer_handler, WithHandler(inner_handler, _prog(body))),
            handlers=default_handlers(),
        )
        assert result.value == "inner"

    def test_delegation_chain(self) -> None:
        def inner_handler(effect, _k):
            if isinstance(effect, Ping):
                yield Pass()
                return "unreachable"
            yield Pass()

        def middle_handler(effect, k):
            if isinstance(effect, Ping):
                delegated = yield Delegate()
                return (yield Resume(k, f"middle:{delegated}"))
            yield Pass()

        def outer_handler(effect, k):
            if isinstance(effect, Ping):
                return (yield Resume(k, "outer"))
            yield Pass()

        def body():
            return (yield Ping("x"))

        result = run(
            WithHandler(
                outer_handler,
                WithHandler(middle_handler, WithHandler(inner_handler, _prog(body))),
            ),
            handlers=default_handlers(),
        )
        assert result.value == "middle:outer"


@pytest.mark.phase3_baseline
def test_with_handler_plain_generator() -> None:
    def handler(effect, k):
        if isinstance(effect, Ping):
            return (yield Resume(k, f"plain:{effect.payload}"))
        yield Pass()

    @do
    def body():
        return (yield Ping("x"))

    result = run(WithHandler(handler, body()), handlers=default_handlers())
    assert result.value == "plain:x"


@pytest.mark.phase3_baseline
def test_with_handler_do_decorated() -> None:
    @do
    def handler(effect: Effect, k):
        if isinstance(effect, Ping):
            return (yield Resume(k, f"do:{effect.payload}"))
        yield Delegate()

    @do
    def body():
        return (yield Ping("y"))

    result = run(WithHandler(handler, body()), handlers=default_handlers())
    assert result.value == "do:y"


@pytest.mark.phase3_baseline
def test_with_intercept_plain_callable() -> None:
    def interceptor(expr):
        if isinstance(expr, Ping):
            return Ping("mutated")
        return expr

    def handler(effect, k):
        if isinstance(effect, Ping):
            return (yield Resume(k, effect.payload))
        yield Pass()

    @do
    def body():
        return (yield Ping("original"))

    result = run(
        WithHandler(
            handler,
            WithIntercept(interceptor, body(), (Ping,), "include"),
        ),
        handlers=default_handlers(),
    )
    assert result.value == "mutated"


@pytest.mark.phase3_baseline
def test_with_intercept_do_decorated() -> None:
    @do
    def interceptor(expr):
        if isinstance(expr, Ping):
            seen = yield Get("seen")
            yield Put("seen", seen + 1)
        return expr

    def handler(effect, k):
        if isinstance(effect, Ping):
            return (yield Resume(k, effect.payload))
        yield Pass()

    @do
    def body():
        value = yield Ping("base")
        seen = yield Get("seen")
        return f"{value}:{seen}"

    result = run(
        WithHandler(
            handler,
            WithIntercept(interceptor, body()),
        ),
        handlers=default_handlers(),
        store={"seen": 0},
    )
    assert result.value == "base:1"


@pytest.mark.phase3_baseline
def test_with_handler_return_clause() -> None:
    def handler(_effect, _k):
        yield Pass()

    def return_clause(value):
        return f"ret:{value}"

    @do
    def body():
        return "done"

    result = run(
        WithHandler(handler, body(), return_clause),
        handlers=default_handlers(),
    )
    assert result.value == "ret:done"


@pytest.mark.phase3_baseline
def test_with_intercept_effectful() -> None:
    def interceptor(expr):
        @do
        def effectful():
            if isinstance(expr, Ping):
                count = yield Get("count")
                yield Put("count", count + 1)
            return expr

        return effectful()

    def handler(effect, k):
        if isinstance(effect, Ping):
            return (yield Resume(k, "ok"))
        yield Pass()

    @do
    def body():
        _ = yield Ping("ignored")
        return (yield Get("count"))

    result = run(
        WithHandler(
            handler,
            WithIntercept(interceptor, body(), (Ping,), "include"),
        ),
        handlers=default_handlers(),
        store={"count": 0},
    )
    assert result.value == 1
