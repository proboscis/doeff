from __future__ import annotations

import pytest

from doeff import (
    Ask,
    Effect,
    EffectBase,
    Get,
    Listen,
    Pass,
    Put,
    Resume,
    Tell,
    do,
)
from doeff import handler as _install_raw_handler
from tests._run_helpers import run_with_defaults


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
        @do
        def handler(effect: Effect, k):
            if isinstance(effect, Ping):
                return (yield Resume(k, f"pong:{effect.payload}"))
            yield effect

        def body():
            value = yield Ping("hello")
            return value

        def main():
            return (yield _install_raw_handler(handler)(_prog(body)))

        result = run_with_defaults(_prog(main))
        assert result.value == "pong:hello"

    def test_handler_can_yield_effects(self) -> None:
        @do
        def handler(effect: Effect, k):
            if isinstance(effect, Ping):
                state_val = yield Get("key")
                return (yield Resume(k, f"got:{state_val}"))
            yield Pass(effect, k)

        def body():
            value = yield Ping("ignored")
            return value

        def main():
            return (yield _install_raw_handler(handler)(_prog(body)))

        result = run_with_defaults(_prog(main), store={"key": "magic"})
        assert result.value == "got:magic"

    def test_multiple_effects_in_body(self) -> None:
        @do
        def handler(effect: Effect, k):
            if isinstance(effect, Ping):
                return (yield Resume(k, f"ping:{effect.payload}"))
            if isinstance(effect, Pong):
                return (yield Resume(k, f"pong:{effect.payload}"))
            yield Pass(effect, k)

        def body():
            first = yield Ping("a")
            second = yield Pong("b")
            return f"{first}|{second}"

        def main():
            return (yield _install_raw_handler(handler)(_prog(body)))

        result = run_with_defaults(_prog(main))
        assert result.value == "ping:a|pong:b"


class TestRustBuiltinHandlers:
    def test_state_get_put(self) -> None:
        @do
        def program():
            yield Put("counter", 1)
            value = yield Get("counter")
            return value

        result = run_with_defaults(program(), store={})
        assert result.value == 1

    def test_reader_ask(self) -> None:
        @do
        def program():
            value = yield Ask("name")
            return f"hello:{value}"

        result = run_with_defaults(program(), env={"name": "world"})
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

        result = run_with_defaults(program())
        inner_value, collected = result.value
        assert inner_value == "done"
        assert any(getattr(e, "msg", None) == "entry" for e in collected)

    def test_state_with_custom_handler(self) -> None:
        @do
        def handler(effect: Effect, k):
            if isinstance(effect, Ping):
                before = yield Get("counter")
                yield Put("counter", before + 1)
                return (yield Resume(k, f"counter:{before}"))
            yield Pass(effect, k)

        @do
        def body():
            from_handler = yield Ping("x")
            current = yield Get("counter")
            return f"{from_handler}|{current}"

        result = run_with_defaults(
            _install_raw_handler(handler)(body()),
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
            yield effect

        def body():
            value = yield Ping("hello")
            return value

        def main():
            return (yield _install_raw_handler(handler)(_prog(body)))

        result = run_with_defaults(_prog(main))
        assert result.value == "handled:hello"

    @pytest.mark.pre_kleisli_behavior
    def test_do_handler_with_effects_returns_value(self) -> None:
        @do
        def handler(effect: Effect, _k):
            if isinstance(effect, Ping):
                state_val = yield Get("key")
                return (yield Resume(_k, f"handled:{state_val}"))
            yield Pass(effect, k)  # noqa: F821 - legacy removed API reference is intentionally preserved

        def body():
            value = yield Ping("hello")
            return value

        def main():
            return (yield _install_raw_handler(handler)(_prog(body)))

        result = run_with_defaults(_prog(main), store={"key": "magic"})
        assert result.value == "handled:magic"


class TestHandlerIdentity:
    def test_handler_does_not_handle_own_effects(self) -> None:
        @do
        def inner_handler(effect: Effect, k):
            if isinstance(effect, Ping):
                delegated = yield Ping("from-inner-body")
                return (yield Resume(k, f"inner:{delegated}"))
            yield Pass(effect, k)

        @do
        def outer_handler(effect: Effect, k):
            if isinstance(effect, Ping):
                return (yield Resume(k, f"outer:{effect.payload}"))
            yield Pass(effect, k)

        def body():
            value = yield Ping("from-user")
            return value

        result = run_with_defaults(
            _install_raw_handler(outer_handler)(_install_raw_handler(inner_handler)(_prog(body))),
        )
        assert result.value == "inner:outer:from-inner-body"


class TestNestedWithHandler:
    def test_inner_handler_takes_priority(self) -> None:
        @do
        def inner_handler(effect: Effect, k):
            if isinstance(effect, Ping):
                return (yield Resume(k, "inner"))
            yield Pass(effect, k)

        @do
        def outer_handler(effect: Effect, k):
            if isinstance(effect, Ping):
                return (yield Resume(k, "outer"))
            yield Pass(effect, k)

        def body():
            return (yield Ping("x"))

        result = run_with_defaults(
            _install_raw_handler(outer_handler)(_install_raw_handler(inner_handler)(_prog(body))),
        )
        assert result.value == "inner"


@pytest.mark.phase3_baseline
def test_with_handler_plain_generator() -> None:
    @do
    def handler(effect: Effect, k):
        if isinstance(effect, Ping):
            return (yield Resume(k, f"plain:{effect.payload}"))
        yield Pass(effect, k)

    @do
    def body():
        return (yield Ping("x"))

    result = run_with_defaults(_install_raw_handler(handler)(body()))
    assert result.value == "plain:x"


@pytest.mark.phase3_baseline
def test_with_handler_do_decorated() -> None:
    @do
    def handler(effect: Effect, k):
        if isinstance(effect, Ping):
            return (yield Resume(k, f"do:{effect.payload}"))
        yield effect

    @do
    def body():
        return (yield Ping("y"))

    result = run_with_defaults(_install_raw_handler(handler)(body()))
    assert result.value == "do:y"




@pytest.mark.phase3_baseline
def test_with_handler_post_composition() -> None:
    @do
    def handler(_effect: Effect, _k):
        yield Pass(effect, k)  # noqa: F821 - legacy removed API reference is intentionally preserved

    @do
    def body():
        return "done"

    @do
    def combined():
        value = yield _install_raw_handler(handler)(body())
        return f"ret:{value}"

    result = run_with_defaults(combined())
    assert result.value == "ret:done"
