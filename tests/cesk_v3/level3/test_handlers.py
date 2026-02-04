"""Phase 6: Integration tests for run() and effect handlers.

Tests the full stack: run() loop with handlers for state-like and reader-like patterns.
Uses handler closures for state management (AskStore/AskEnv primitives not yet implemented).

Supports nested program yields (KleisliProgramCall) via ADR-12 monadic bind handling.
"""

from dataclasses import dataclass
from typing import Any, Callable

import pytest

from doeff.cesk_v3 import EffectBase, Forward, Resume, WithHandler, run
from doeff.cesk_v3.errors import UnhandledEffectError
from doeff.do import do


@dataclass(frozen=True)
class Get(EffectBase):
    key: str


@dataclass(frozen=True)
class Put(EffectBase):
    key: str
    value: Any


@dataclass(frozen=True)
class Modify(EffectBase):
    key: str
    fn: Callable[[Any], Any]


@dataclass(frozen=True)
class Ask(EffectBase):
    key: str


@dataclass(frozen=True)
class Log(EffectBase):
    message: str


@dataclass(frozen=True)
class GetLogs(EffectBase):
    pass


class TestFullStackExecution:

    def test_pure_program_returns_value(self) -> None:
        @do
        def program():
            return 42
            yield  # type: ignore

        result = run(program())
        assert result == 42

    def test_pure_program_with_computation(self) -> None:
        @do
        def program():
            x = 10
            y = 20
            return x + y
            yield  # type: ignore

        result = run(program())
        assert result == 30

    def test_nested_pure_programs(self) -> None:
        @do
        def inner(x: int):
            return x * 2
            yield  # type: ignore

        @do
        def outer():
            a = yield inner(10)
            b = yield inner(20)
            return a + b

        result = run(outer())
        assert result == 60

    def test_deeply_nested_programs(self) -> None:
        @do
        def level3(x: int):
            return x + 1
            yield  # type: ignore

        @do
        def level2(x: int):
            return (yield level3(x)) * 2

        @do
        def level1():
            return (yield level2(5))

        result = run(level1())
        assert result == 12

    def test_unhandled_effect_raises(self) -> None:
        @do
        def program():
            return (yield Get("key"))

        with pytest.raises(UnhandledEffectError):
            run(program())

    def test_handled_effect_returns_result(self) -> None:
        @do
        def handler(effect):
            if isinstance(effect, Get):
                return (yield Resume(42))
            return (yield Forward(effect))

        @do
        def program():
            return (yield Get("key"))

        result = run(WithHandler(handler, program()))
        assert result == 42


class TestStateHandler:

    def test_get_returns_initial_value(self) -> None:
        state = {"counter": 0}

        @do
        def state_handler(effect):
            if isinstance(effect, Get):
                return (yield Resume(state.get(effect.key)))
            return (yield Forward(effect))

        @do
        def program():
            return (yield Get("counter"))

        result = run(WithHandler(state_handler, program()))
        assert result == 0

    def test_put_updates_state(self) -> None:
        state: dict[str, Any] = {}

        @do
        def state_handler(effect):
            if isinstance(effect, Get):
                return (yield Resume(state.get(effect.key)))
            if isinstance(effect, Put):
                state[effect.key] = effect.value
                return (yield Resume(None))
            return (yield Forward(effect))

        @do
        def program():
            yield Put("x", 42)
            return (yield Get("x"))

        result = run(WithHandler(state_handler, program()))
        assert result == 42
        assert state == {"x": 42}

    def test_modify_transforms_value(self) -> None:
        state = {"counter": 10}

        @do
        def state_handler(effect):
            if isinstance(effect, Get):
                return (yield Resume(state.get(effect.key)))
            if isinstance(effect, Put):
                state[effect.key] = effect.value
                return (yield Resume(None))
            if isinstance(effect, Modify):
                old = state.get(effect.key, 0)
                state[effect.key] = effect.fn(old)
                return (yield Resume(state[effect.key]))
            return (yield Forward(effect))

        @do
        def program():
            return (yield Modify("counter", lambda x: x + 5))

        result = run(WithHandler(state_handler, program()))
        assert result == 15
        assert state["counter"] == 15

    def test_multiple_state_operations(self) -> None:
        state: dict[str, Any] = {"a": 1, "b": 2}

        @do
        def state_handler(effect):
            if isinstance(effect, Get):
                return (yield Resume(state.get(effect.key, 0)))
            if isinstance(effect, Put):
                state[effect.key] = effect.value
                return (yield Resume(None))
            return (yield Forward(effect))

        @do
        def program():
            a = yield Get("a")
            b = yield Get("b")
            yield Put("c", a + b)
            return (yield Get("c"))

        result = run(WithHandler(state_handler, program()))
        assert result == 3
        assert state == {"a": 1, "b": 2, "c": 3}

    def test_increment_pattern(self) -> None:
        state: dict[str, int] = {"counter": 0}

        @do
        def state_handler(effect):
            if isinstance(effect, Get):
                return (yield Resume(state.get(effect.key, 0)))
            if isinstance(effect, Put):
                state[effect.key] = effect.value
                return (yield Resume(None))
            return (yield Forward(effect))

        @do
        def program():
            c1 = yield Get("counter")
            yield Put("counter", c1 + 1)
            c2 = yield Get("counter")
            yield Put("counter", c2 + 1)
            c3 = yield Get("counter")
            yield Put("counter", c3 + 1)
            return (yield Get("counter"))

        result = run(WithHandler(state_handler, program()))
        assert result == 3


class TestReaderHandler:

    def test_ask_returns_env_value(self) -> None:
        env = {"db_url": "postgres://localhost/test"}

        @do
        def reader_handler(effect):
            if isinstance(effect, Ask):
                return (yield Resume(env.get(effect.key)))
            return (yield Forward(effect))

        @do
        def program():
            return (yield Ask("db_url"))

        result = run(WithHandler(reader_handler, program()))
        assert result == "postgres://localhost/test"

    def test_ask_missing_key_returns_none(self) -> None:
        env: dict[str, Any] = {}

        @do
        def reader_handler(effect):
            if isinstance(effect, Ask):
                return (yield Resume(env.get(effect.key)))
            return (yield Forward(effect))

        @do
        def program():
            return (yield Ask("missing"))

        result = run(WithHandler(reader_handler, program()))
        assert result is None

    def test_multiple_asks(self) -> None:
        env = {"host": "localhost", "port": 5432}

        @do
        def reader_handler(effect):
            if isinstance(effect, Ask):
                return (yield Resume(env.get(effect.key)))
            return (yield Forward(effect))

        @do
        def program():
            host = yield Ask("host")
            port = yield Ask("port")
            return f"{host}:{port}"

        result = run(WithHandler(reader_handler, program()))
        assert result == "localhost:5432"


class TestWriterHandler:

    def test_log_accumulates_messages(self) -> None:
        logs: list[str] = []

        @do
        def writer_handler(effect):
            if isinstance(effect, Log):
                logs.append(effect.message)
                return (yield Resume(None))
            if isinstance(effect, GetLogs):
                return (yield Resume(list(logs)))
            return (yield Forward(effect))

        @do
        def program():
            yield Log("Starting")
            yield Log("Processing")
            yield Log("Done")
            return (yield GetLogs())

        result = run(WithHandler(writer_handler, program()))
        assert result == ["Starting", "Processing", "Done"]

    def test_log_with_computation(self) -> None:
        logs: list[str] = []

        @do
        def writer_handler(effect):
            if isinstance(effect, Log):
                logs.append(effect.message)
                return (yield Resume(None))
            return (yield Forward(effect))

        @do
        def program():
            yield Log("step 1")
            x = 10 + 20
            yield Log("step 2")
            y = x * 2
            yield Log("step 3")
            return y

        result = run(WithHandler(writer_handler, program()))
        assert result == 60
        assert logs == ["step 1", "step 2", "step 3"]


class TestMultipleEffectsOneHandler:

    def test_state_and_logging_in_one_handler(self) -> None:
        state: dict[str, Any] = {"counter": 0}
        logs: list[str] = []

        @do
        def combined_handler(effect):
            if isinstance(effect, Get):
                return (yield Resume(state.get(effect.key, 0)))
            if isinstance(effect, Put):
                state[effect.key] = effect.value
                return (yield Resume(None))
            if isinstance(effect, Log):
                logs.append(effect.message)
                return (yield Resume(None))
            return (yield Forward(effect))

        @do
        def program():
            yield Log("Starting")
            c = yield Get("counter")
            yield Log(f"Counter was {c}")
            yield Put("counter", c + 10)
            yield Log("Incremented by 10")
            return (yield Get("counter"))

        result = run(WithHandler(combined_handler, program()))
        assert result == 10
        assert logs == ["Starting", "Counter was 0", "Incremented by 10"]

    def test_state_reader_logging_in_one_handler(self) -> None:
        state: dict[str, Any] = {}
        env = {"multiplier": 5}
        logs: list[str] = []

        @do
        def uber_handler(effect):
            if isinstance(effect, Get):
                return (yield Resume(state.get(effect.key, 0)))
            if isinstance(effect, Put):
                state[effect.key] = effect.value
                return (yield Resume(None))
            if isinstance(effect, Ask):
                return (yield Resume(env.get(effect.key)))
            if isinstance(effect, Log):
                logs.append(effect.message)
                return (yield Resume(None))
            return (yield Forward(effect))

        @do
        def program():
            mult = yield Ask("multiplier")
            yield Log(f"Multiplier: {mult}")
            yield Put("result", 10 * mult)
            final = yield Get("result")
            yield Log(f"Final: {final}")
            return final

        result = run(WithHandler(uber_handler, program()))
        assert result == 50
        assert state == {"result": 50}
        assert logs == ["Multiplier: 5", "Final: 50"]


class TestErrorHandlingIntegration:

    def test_exception_in_pure_program_propagates(self) -> None:
        @do
        def program():
            raise ValueError("test error")
            yield  # type: ignore

        with pytest.raises(ValueError, match="test error"):
            run(program())

    def test_exception_before_effect_propagates(self) -> None:
        @do
        def handler(effect):
            return (yield Resume("handled"))

        @do
        def program():
            raise ValueError("early error")
            return (yield Get("key"))  # type: ignore

        with pytest.raises(ValueError, match="early error"):
            run(WithHandler(handler, program()))

    def test_forward_chain_ends_in_unhandled(self) -> None:
        @do
        def handler(effect):
            return (yield Forward(effect))

        @do
        def program():
            return (yield Get("key"))

        with pytest.raises(UnhandledEffectError):
            run(WithHandler(handler, program()))
