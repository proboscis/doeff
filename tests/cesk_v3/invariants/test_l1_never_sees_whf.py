from __future__ import annotations

import pytest
from typing import Any, Generator

from doeff.cesk_v3.level1_cesk import (
    CESKState,
    ProgramControl,
    Value,
    Error,
    ReturnFrame,
    WithHandlerFrame,
)
from doeff.cesk_v3.level1_cesk.step import cesk_step


class TestLevel1RejectsWithHandlerFrame:

    def test_cesk_step_asserts_on_value_with_whf(self):
        state = CESKState(
            C=Value(42),
            E={},
            S={},
            K=[WithHandlerFrame()],
        )

        with pytest.raises(AssertionError) as exc_info:
            cesk_step(state)

        assert "Level 1 only handles ReturnFrame" in str(exc_info.value)
        assert "WithHandlerFrame" in str(exc_info.value)

    def test_cesk_step_asserts_on_error_with_whf(self):
        state = CESKState(
            C=Error(ValueError("test")),
            E={},
            S={},
            K=[WithHandlerFrame()],
        )

        with pytest.raises(AssertionError) as exc_info:
            cesk_step(state)

        assert "Level 1 only handles ReturnFrame" in str(exc_info.value)

    def test_cesk_step_asserts_on_whf_after_return_frame(self):
        def gen() -> Generator[Any, Any, int]:
            x = yield "effect"
            return x

        g = gen()
        next(g)

        state = CESKState(
            C=Value(10),
            E={},
            S={},
            K=[ReturnFrame(g), WithHandlerFrame()],
        )

        result = cesk_step(state)
        assert isinstance(result, CESKState)
        assert isinstance(result.C, Value)
        assert result.C.value == 10
        assert len(result.K) == 1
        assert isinstance(result.K[0], WithHandlerFrame)


class TestLevel2InterceptsBeforeLevel1:

    def test_level2_handles_whf_before_delegating(self):
        from doeff.cesk_v3.level2_algebraic_effects import (
            get_ae_state,
            set_ae_state,
            AlgebraicEffectsState,
        )
        from doeff.cesk_v3.level2_algebraic_effects.step import level2_step

        def make_handler(name: str = "test") -> Any:
            def handler(effect: Any) -> Generator[Any, Any, Any]:
                yield effect
                return None

            return handler

        ae = AlgebraicEffectsState().push_handler(make_handler())
        state = CESKState(
            C=Value("result"),
            E={},
            S=set_ae_state({}, ae),
            K=[WithHandlerFrame()],
        )

        result = level2_step(state)

        assert isinstance(result, CESKState)
        assert isinstance(result.C, Value)
        assert result.C.value == "result"
        assert result.K == []
        new_ae = get_ae_state(result.S)
        assert len(new_ae.handler_stack) == 0

    def test_mixed_k_whf_then_rf_handled_correctly(self):
        from doeff.cesk_v3.level2_algebraic_effects import (
            get_ae_state,
            set_ae_state,
            AlgebraicEffectsState,
        )
        from doeff.cesk_v3.level2_algebraic_effects.step import level2_step

        def make_handler() -> Any:
            def handler(effect: Any) -> Generator[Any, Any, Any]:
                yield effect
                return None

            return handler

        def outer_gen() -> Generator[Any, Any, int]:
            x = yield "wait_for_value"
            return x * 2

        outer_g = outer_gen()
        next(outer_g)

        ae = AlgebraicEffectsState().push_handler(make_handler())
        state = CESKState(
            C=Value(21),
            E={},
            S=set_ae_state({}, ae),
            K=[WithHandlerFrame(), ReturnFrame(outer_g)],
        )

        result = level2_step(state)
        assert isinstance(result, CESKState)
        assert isinstance(result.C, Value)
        assert result.C.value == 21
        assert len(result.K) == 1
        assert isinstance(result.K[0], ReturnFrame)

        new_ae = get_ae_state(result.S)
        assert len(new_ae.handler_stack) == 0

        result2 = level2_step(result)
        assert isinstance(result2, CESKState)
        assert isinstance(result2.C, Value)
        assert result2.C.value == 42
        assert result2.K == []
