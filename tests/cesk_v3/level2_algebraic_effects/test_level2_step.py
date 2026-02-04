from __future__ import annotations

import pytest
from typing import Any, Generator

from doeff.cesk_v3.level1_cesk import (
    CESKState,
    ProgramControl,
    Value,
    Error,
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
    ControlPrimitive,
    WithHandler,
)
from doeff.cesk_v3.level2_algebraic_effects.step import level2_step


def make_handler(name: str = "test") -> Any:
    def handler(effect: Any) -> Generator[Any, Any, Any]:
        yield effect
        return None
    return handler


class TestLevel2StepDelegatesToLevel1:

    def test_program_control_delegates_to_level1(self):
        def simple_gen() -> Generator[Any, Any, int]:
            return 42
            yield

        state = CESKState(C=ProgramControl(simple_gen), E={}, S={}, K=[])
        result = level2_step(state)

        assert isinstance(result, CESKState)
        assert isinstance(result.C, Value)
        assert result.C.value == 42

    def test_value_with_return_frame_delegates_to_level1(self):
        def gen() -> Generator[Any, Any, int]:
            x = yield "effect"
            return x + 10

        g = gen()
        next(g)

        state = CESKState(C=Value(5), E={}, S={}, K=[ReturnFrame(g)])
        result = level2_step(state)

        assert isinstance(result, CESKState)
        assert isinstance(result.C, Value)
        assert result.C.value == 15
        assert result.K == []

    def test_error_with_return_frame_delegates_to_level1(self):
        def gen() -> Generator[Any, Any, int]:
            try:
                yield "effect"
            except ValueError as e:
                return int(str(e))
            return 0

        g = gen()
        next(g)

        state = CESKState(C=Error(ValueError("99")), E={}, S={}, K=[ReturnFrame(g)])
        result = level2_step(state)

        assert isinstance(result, CESKState)
        assert isinstance(result.C, Value)
        assert result.C.value == 99

    def test_done_passes_through(self):
        def simple_gen() -> Generator[Any, Any, str]:
            return "final"
            yield

        state = CESKState(C=ProgramControl(simple_gen), E={}, S={}, K=[])
        result = level2_step(state)

        assert isinstance(result, CESKState)
        assert isinstance(result.C, Value)
        result2 = level2_step(result)
        assert isinstance(result2, Done)
        assert result2.value == "final"

    def test_failed_passes_through(self):
        def failing_gen() -> Generator[Any, Any, None]:
            raise RuntimeError("boom")
            yield

        state = CESKState(C=ProgramControl(failing_gen), E={}, S={}, K=[])
        result = level2_step(state)

        assert isinstance(result, CESKState)
        assert isinstance(result.C, Error)
        result2 = level2_step(result)
        assert isinstance(result2, Failed)
        assert str(result2.error) == "boom"


class TestWithHandlerFrameInterception:

    def test_value_at_whf_pops_handler(self):
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

    def test_value_at_whf_continues_with_rest_of_k(self):
        def outer_gen() -> Generator[Any, Any, int]:
            x = yield "wait"
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

    def test_error_at_whf_pops_handler(self):
        ae = AlgebraicEffectsState().push_handler(make_handler())
        err = ValueError("test error")
        state = CESKState(
            C=Error(err),
            E={},
            S=set_ae_state({}, ae),
            K=[WithHandlerFrame()],
        )

        result = level2_step(state)

        assert isinstance(result, CESKState)
        assert isinstance(result.C, Error)
        assert result.C.error is err
        assert result.K == []
        new_ae = get_ae_state(result.S)
        assert len(new_ae.handler_stack) == 0

    def test_nested_whf_pop_only_innermost_handler(self):
        ae = (
            AlgebraicEffectsState()
            .push_handler(make_handler("outer"))
            .push_handler(make_handler("inner"))
        )
        state = CESKState(
            C=Value("done"),
            E={},
            S=set_ae_state({}, ae),
            K=[WithHandlerFrame(), WithHandlerFrame()],
        )

        result = level2_step(state)

        assert isinstance(result, CESKState)
        new_ae = get_ae_state(result.S)
        assert len(new_ae.handler_stack) == 1
        assert len(result.K) == 1
        assert isinstance(result.K[0], WithHandlerFrame)


class TestEffectYieldPassthrough:

    def test_non_control_primitive_passes_through(self):
        class UserEffect:
            pass

        def gen() -> Generator[Any, Any, None]:
            yield UserEffect()
            return None

        state = CESKState(C=ProgramControl(gen), E={}, S={}, K=[])
        result = level2_step(state)

        assert isinstance(result, CESKState)
        assert isinstance(result.C, EffectYield)
        assert isinstance(result.C.yielded, UserEffect)
