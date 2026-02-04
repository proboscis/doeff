from __future__ import annotations

import pytest
from typing import Any, Generator

from doeff.cesk_v3.level1_cesk import (
    CESKState,
    ProgramControl,
    Value,
    EffectYield,
    Done,
    Failed,
    ReturnFrame,
    WithHandlerFrame,
)
from doeff.cesk_v3.level2_algebraic_effects import (
    get_ae_state,
    set_ae_state,
    AlgebraicEffectsState,
    Resume,
)
from doeff.cesk_v3.level2_algebraic_effects.step import level2_step, translate_control_primitive


def make_handler() -> Any:
    def handler(effect: Any) -> Generator[Any, Any, Any]:
        yield effect
        return None
    return handler


class TestResumeBasicBehavior:

    def test_resume_sets_value_control(self):
        def user_gen() -> Generator[Any, Any, int]:
            x = yield "effect"
            return x + 10

        user_g = user_gen()
        next(user_g)
        captured_k = (ReturnFrame(user_g),)

        ae = (
            AlgebraicEffectsState()
            .push_handler(make_handler())
            .capture_continuation_at(0, captured_k, 0)
        )

        def handler_gen() -> Generator[Any, Any, Any]:
            result = yield Resume(42)
            return result

        handler_g = handler_gen()
        next(handler_g)

        state = CESKState(
            C=EffectYield(Resume(42)),
            E={},
            S=set_ae_state({}, ae),
            K=[ReturnFrame(handler_g), WithHandlerFrame()],
        )

        result = translate_control_primitive(Resume(42), state)

        assert isinstance(result, CESKState)
        assert isinstance(result.C, Value)
        assert result.C.value == 42

    def test_resume_concatenates_k(self):
        def user_gen() -> Generator[Any, Any, int]:
            x = yield "effect"
            return x + 10

        user_g = user_gen()
        next(user_g)
        captured_k = (ReturnFrame(user_g),)

        ae = (
            AlgebraicEffectsState()
            .push_handler(make_handler())
            .capture_continuation_at(0, captured_k, 0)
        )

        def handler_gen() -> Generator[Any, Any, Any]:
            result = yield Resume(42)
            return result

        handler_g = handler_gen()
        next(handler_g)

        handler_k = [ReturnFrame(handler_g), WithHandlerFrame()]

        state = CESKState(
            C=EffectYield(Resume(42)),
            E={},
            S=set_ae_state({}, ae),
            K=handler_k,
        )

        result = translate_control_primitive(Resume(42), state)

        assert isinstance(result, CESKState)
        assert len(result.K) == 3
        assert isinstance(result.K[0], ReturnFrame)
        assert result.K[0].generator is user_g
        assert isinstance(result.K[1], ReturnFrame)
        assert result.K[1].generator is handler_g
        assert isinstance(result.K[2], WithHandlerFrame)

    def test_resume_clears_captured_k(self):
        def user_gen() -> Generator[Any, Any, int]:
            x = yield "effect"
            return x

        user_g = user_gen()
        next(user_g)
        captured_k = (ReturnFrame(user_g),)

        ae = (
            AlgebraicEffectsState()
            .push_handler(make_handler())
            .capture_continuation_at(0, captured_k, 0)
        )

        def handler_gen() -> Generator[Any, Any, Any]:
            yield Resume(100)
            return None

        handler_g = handler_gen()
        next(handler_g)

        state = CESKState(
            C=EffectYield(Resume(100)),
            E={},
            S=set_ae_state({}, ae),
            K=[ReturnFrame(handler_g), WithHandlerFrame()],
        )

        result = translate_control_primitive(Resume(100), state)

        new_ae = get_ae_state(result.S)
        captured, k_id = new_ae.get_captured_at(0)
        assert captured is None
        assert k_id is None


class TestResumeValueFlow:

    def test_resumed_value_reaches_user_code(self):
        def user_gen() -> Generator[Any, Any, int]:
            x = yield "effect"
            return x * 2

        user_g = user_gen()
        next(user_g)
        captured_k = (ReturnFrame(user_g),)

        ae = (
            AlgebraicEffectsState()
            .push_handler(make_handler())
            .capture_continuation_at(0, captured_k, 0)
        )

        def handler_gen() -> Generator[Any, Any, Any]:
            result = yield Resume(21)
            return result

        handler_g = handler_gen()
        next(handler_g)

        state = CESKState(
            C=EffectYield(Resume(21)),
            E={},
            S=set_ae_state({}, ae),
            K=[ReturnFrame(handler_g), WithHandlerFrame()],
        )

        result = translate_control_primitive(Resume(21), state)
        assert isinstance(result.C, Value)
        assert result.C.value == 21

        result = level2_step(result)
        assert isinstance(result, CESKState)
        assert isinstance(result.C, Value)
        assert result.C.value == 42

    def test_user_result_flows_back_to_handler(self):
        def user_gen() -> Generator[Any, Any, str]:
            x = yield "effect"
            return f"user_{x}"

        user_g = user_gen()
        next(user_g)
        captured_k = (ReturnFrame(user_g),)

        ae = (
            AlgebraicEffectsState()
            .push_handler(make_handler())
            .capture_continuation_at(0, captured_k, 0)
        )

        def handler_gen() -> Generator[Any, Any, str]:
            user_result = yield Resume("input")
            return f"handler_{user_result}"

        handler_g = handler_gen()
        next(handler_g)

        state = CESKState(
            C=EffectYield(Resume("input")),
            E={},
            S=set_ae_state({}, ae),
            K=[ReturnFrame(handler_g), WithHandlerFrame()],
        )

        result = translate_control_primitive(Resume("input"), state)
        result = level2_step(result)
        assert isinstance(result, CESKState)
        assert isinstance(result.C, Value)
        assert result.C.value == "user_input"

        result = level2_step(result)
        assert isinstance(result, CESKState)
        assert isinstance(result.C, Value)
        assert result.C.value == "handler_user_input"


class TestResumeWithEmptyCapturedK:

    def test_resume_with_no_captured_k_raises(self):
        ae = AlgebraicEffectsState().push_handler(make_handler())

        def handler_gen() -> Generator[Any, Any, Any]:
            yield Resume(42)
            return None

        handler_g = handler_gen()
        next(handler_g)

        state = CESKState(
            C=EffectYield(Resume(42)),
            E={},
            S=set_ae_state({}, ae),
            K=[ReturnFrame(handler_g), WithHandlerFrame()],
        )

        with pytest.raises((RuntimeError, ValueError, AssertionError)):
            translate_control_primitive(Resume(42), state)
