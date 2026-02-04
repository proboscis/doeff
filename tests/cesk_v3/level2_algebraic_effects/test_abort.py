from __future__ import annotations

import pytest
import warnings
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
    Abort,
)
from doeff.cesk_v3.level2_algebraic_effects.step import translate_control_primitive


def make_handler() -> Any:
    def handler(effect: Any) -> Generator[Any, Any, Any]:
        yield effect
        return None
    return handler


class TestAbortBasicBehavior:

    def test_abort_sets_value_control(self):
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
            result = yield Abort("aborted_value")
            return result

        handler_g = handler_gen()
        next(handler_g)

        state = CESKState(
            C=EffectYield(Abort("aborted_value")),
            E={},
            S=set_ae_state({}, ae),
            K=[ReturnFrame(handler_g), WithHandlerFrame()],
        )

        result = translate_control_primitive(Abort("aborted_value"), state)

        assert isinstance(result, CESKState)
        assert isinstance(result.C, Value)
        assert result.C.value == "aborted_value"

    def test_abort_does_not_concatenate_captured_k(self):
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
            yield Abort(999)
            return None

        handler_g = handler_gen()
        next(handler_g)

        handler_k = [ReturnFrame(handler_g), WithHandlerFrame()]

        state = CESKState(
            C=EffectYield(Abort(999)),
            E={},
            S=set_ae_state({}, ae),
            K=handler_k,
        )

        result = translate_control_primitive(Abort(999), state)

        assert isinstance(result, CESKState)
        assert len(result.K) == 2
        assert isinstance(result.K[0], ReturnFrame)
        assert result.K[0].generator is handler_g
        assert isinstance(result.K[1], WithHandlerFrame)

    def test_abort_clears_captured_k(self):
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
            yield Abort(0)
            return None

        handler_g = handler_gen()
        next(handler_g)

        state = CESKState(
            C=EffectYield(Abort(0)),
            E={},
            S=set_ae_state({}, ae),
            K=[ReturnFrame(handler_g), WithHandlerFrame()],
        )

        result = translate_control_primitive(Abort(0), state)

        new_ae = get_ae_state(result.S)
        captured, k_id = new_ae.get_captured_at(0)
        assert captured is None
        assert k_id is None


class TestAbortGeneratorCleanup:

    def test_abort_closes_generators_in_captured_k(self):
        closed = []

        def user_gen() -> Generator[Any, Any, int]:
            try:
                x = yield "effect"
                return x
            finally:
                closed.append("user_gen")

        user_g = user_gen()
        next(user_g)
        captured_k = (ReturnFrame(user_g),)

        ae = (
            AlgebraicEffectsState()
            .push_handler(make_handler())
            .capture_continuation_at(0, captured_k, 0)
        )

        def handler_gen() -> Generator[Any, Any, Any]:
            yield Abort("done")
            return None

        handler_g = handler_gen()
        next(handler_g)

        state = CESKState(
            C=EffectYield(Abort("done")),
            E={},
            S=set_ae_state({}, ae),
            K=[ReturnFrame(handler_g), WithHandlerFrame()],
        )

        translate_control_primitive(Abort("done"), state)

        assert "user_gen" in closed


class TestAbortWarning:

    def test_abort_emits_warning_for_abandoned_continuation(self):
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
            yield Abort("abort")
            return None

        handler_g = handler_gen()
        next(handler_g)

        state = CESKState(
            C=EffectYield(Abort("abort")),
            E={},
            S=set_ae_state({}, ae),
            K=[ReturnFrame(handler_g), WithHandlerFrame()],
        )

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            translate_control_primitive(Abort("abort"), state)
            assert len(w) >= 1
            assert "abandoned" in str(w[0].message).lower() or "abort" in str(w[0].message).lower()
