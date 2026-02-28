from __future__ import annotations

from dataclasses import dataclass

import doeff_vm
import pytest

from doeff import (
    Effect,
    EffectBase,
    Pass,
    Resume,
    WithHandler,
    WithIntercept,
    async_run,
    do,
    run,
)


@dataclass(frozen=True, kw_only=True)
class Ping(EffectBase):
    label: str


@do
def body():
    return (yield Ping(label="x"))


def plain_handler(effect, k):
    if isinstance(effect, Ping):
        return (yield Resume(k, f"plain:{effect.label}"))
    yield Pass()


def plain_interceptor(effect):
    return effect


@do
def do_handler(effect: Effect, k):
    if isinstance(effect, Ping):
        return (yield Resume(k, f"do:{effect.label}"))
    yield Pass()


def test_withhandler_rejects_plain_generator_with_helpful_message() -> None:
    with pytest.raises(TypeError) as exc_info:
        WithHandler(plain_handler, body())

    message = str(exc_info.value)
    assert "WithHandler handler must be" in message
    assert "@do" in message
    assert "plain_handler" in message


def test_run_rejects_plain_generator_handler_with_helpful_message() -> None:
    with pytest.raises(TypeError) as exc_info:
        run(body(), handlers=[plain_handler])

    message = str(exc_info.value)
    assert "run() handler must be" in message
    assert "@do" in message


@pytest.mark.asyncio
async def test_async_run_rejects_plain_generator_handler_with_helpful_message() -> None:
    with pytest.raises(TypeError) as exc_info:
        await async_run(body(), handlers=[plain_handler])

    message = str(exc_info.value)
    assert "async_run() handler must be" in message
    assert "@do" in message


def test_withintercept_rejects_plain_callable_with_helpful_message() -> None:
    with pytest.raises(TypeError) as exc_info:
        WithIntercept(plain_interceptor, body(), (Ping,), "include")

    message = str(exc_info.value)
    assert "WithIntercept interceptor must be" in message
    assert "@do" in message
    assert "plain_interceptor" in message


def test_withhandler_accepts_do_decorated_handler() -> None:
    result = run(WithHandler(do_handler, body()), handlers=[])
    assert result.is_ok()
    assert result.value == "do:x"


def test_withhandler_accepts_rust_handler() -> None:
    ctrl = WithHandler(doeff_vm.state, body())
    assert type(ctrl).__name__ == "WithHandler"


def test_run_accepts_do_decorated_handler() -> None:
    result = run(body(), handlers=[do_handler])
    assert result.is_ok()
    assert result.value == "do:x"


def test_error_message_shows_function_name_and_type() -> None:
    with pytest.raises(TypeError) as exc_info:
        WithHandler(plain_handler, body())

    message = str(exc_info.value)
    assert "plain_handler" in message
    assert "type: function" in message


def test_error_message_suggests_do_decorator_syntax() -> None:
    with pytest.raises(TypeError) as exc_info:
        WithHandler(plain_handler, body())

    message = str(exc_info.value)
    assert "from doeff import do" in message
    assert "@do" in message
