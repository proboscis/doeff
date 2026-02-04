from __future__ import annotations

import pytest
from typing import Any, Generator

from doeff.cesk_v3.level1_cesk import (
    CESKState,
    Value,
    EffectYield,
    ReturnFrame,
    WithHandlerFrame,
)
from doeff.cesk_v3.level2_algebraic_effects import (
    get_ae_state,
    set_ae_state,
    AlgebraicEffectsState,
    Resume,
)
from doeff.cesk_v3.level2_algebraic_effects.step import translate_control_primitive


def make_handler() -> Any:
    def handler(effect: Any) -> Generator[Any, Any, Any]:
        yield effect
        return None
    return handler


class TestOneShotTracking:

    def test_resume_marks_continuation_as_consumed(self):
        def user_gen() -> Generator[Any, Any, int]:
            x = yield "effect"
            return x

        user_g = user_gen()
        next(user_g)
        captured_k = (ReturnFrame(user_g),)

        ae = (
            AlgebraicEffectsState()
            .push_handler(make_handler())
            .capture_continuation_at(0, captured_k, 42)
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
        assert new_ae.is_consumed(42)

    def test_consumed_continuation_cannot_be_resumed_again(self):
        def user_gen() -> Generator[Any, Any, int]:
            x = yield "effect"
            return x

        user_g = user_gen()
        next(user_g)
        captured_k = (ReturnFrame(user_g),)

        ae = (
            AlgebraicEffectsState()
            .push_handler(make_handler())
            .capture_continuation_at(0, captured_k, 99)
            .mark_consumed(99)
        )

        def handler_gen() -> Generator[Any, Any, Any]:
            yield Resume(1)
            return None

        handler_g = handler_gen()
        next(handler_g)

        state = CESKState(
            C=EffectYield(Resume(1)),
            E={},
            S=set_ae_state({}, ae),
            K=[ReturnFrame(handler_g), WithHandlerFrame()],
        )

        with pytest.raises(RuntimeError) as exc_info:
            translate_control_primitive(Resume(1), state)

        assert "consumed" in str(exc_info.value).lower() or "one-shot" in str(exc_info.value).lower()


class TestOneShotInvariant:

    def test_each_k_id_can_only_be_resumed_once(self):
        def user_gen1() -> Generator[Any, Any, int]:
            x = yield "effect1"
            return x

        def user_gen2() -> Generator[Any, Any, int]:
            x = yield "effect2"
            return x

        user_g1 = user_gen1()
        next(user_g1)
        user_g2 = user_gen2()
        next(user_g2)

        ae = (
            AlgebraicEffectsState()
            .push_handler(make_handler())
            .push_handler(make_handler())
            .capture_continuation_at(0, (ReturnFrame(user_g1),), 0)
            .capture_continuation_at(1, (ReturnFrame(user_g2),), 1)
        )

        def handler_gen() -> Generator[Any, Any, Any]:
            yield Resume(10)
            return None

        handler_g = handler_gen()
        next(handler_g)

        state = CESKState(
            C=EffectYield(Resume(10)),
            E={},
            S=set_ae_state({}, ae),
            K=[ReturnFrame(handler_g), WithHandlerFrame(), WithHandlerFrame()],
        )

        result = translate_control_primitive(Resume(10), state)
        new_ae = get_ae_state(result.S)

        assert new_ae.is_consumed(1)
        assert not new_ae.is_consumed(0)
