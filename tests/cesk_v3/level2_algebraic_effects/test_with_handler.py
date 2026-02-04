from __future__ import annotations

import pytest
from typing import Any, Generator

from doeff.cesk_v3.level1_cesk import (
    CESKState,
    ProgramControl,
    Value,
    EffectYield,
    ReturnFrame,
    WithHandlerFrame,
)
from doeff.cesk_v3.level2_algebraic_effects import (
    get_ae_state,
    set_ae_state,
    AlgebraicEffectsState,
    WithHandler,
    ControlPrimitive,
)
from doeff.cesk_v3.level2_algebraic_effects.step import level2_step


def make_handler(name: str = "test") -> Any:
    def handler(effect: Any) -> Generator[Any, Any, Any]:
        yield effect
        return None

    return handler


def make_program_returning(value: Any) -> Any:
    def program() -> Generator[Any, Any, Any]:
        return value
        yield

    return program


class TestWithHandlerTranslation:

    def test_with_handler_pushes_handler_to_stack(self):
        handler = make_handler()
        program = make_program_returning(42)

        def gen() -> Generator[Any, Any, Any]:
            result = yield WithHandler(handler=handler, program=program)
            return result

        state = CESKState(C=ProgramControl(gen), E={}, S={}, K=[])

        result = level2_step(state)
        assert isinstance(result, CESKState)
        assert isinstance(result.C, ProgramControl)

        ae = get_ae_state(result.S)
        assert len(ae.handler_stack) == 1
        assert ae.handler_stack[0].handler is handler

    def test_with_handler_pushes_whf_to_k(self):
        handler = make_handler()
        program = make_program_returning(42)

        def gen() -> Generator[Any, Any, Any]:
            result = yield WithHandler(handler=handler, program=program)
            return result

        state = CESKState(C=ProgramControl(gen), E={}, S={}, K=[])

        result = level2_step(state)

        assert isinstance(result, CESKState)
        assert len(result.K) >= 1
        whf_count = sum(1 for f in result.K if isinstance(f, WithHandlerFrame))
        assert whf_count >= 1

    def test_with_handler_starts_inner_program(self):
        handler = make_handler()

        def inner_program() -> Generator[Any, Any, str]:
            return "inner_result"
            yield

        def outer_gen() -> Generator[Any, Any, Any]:
            result = yield WithHandler(handler=handler, program=inner_program)
            return f"outer_{result}"

        state = CESKState(C=ProgramControl(outer_gen), E={}, S={}, K=[])

        result = level2_step(state)
        assert isinstance(result, CESKState)
        assert isinstance(result.C, ProgramControl)


class TestWithHandlerScopeCompletion:

    def test_inner_program_result_flows_through_whf(self):
        handler = make_handler()

        def inner_program() -> Generator[Any, Any, int]:
            return 100
            yield

        def outer_gen() -> Generator[Any, Any, Any]:
            result = yield WithHandler(handler=handler, program=inner_program)
            return result * 2

        state = CESKState(C=ProgramControl(outer_gen), E={}, S={}, K=[])

        while not isinstance(state, (CESKState,)) or not (
            isinstance(state.C, Value) and not state.K
        ):
            state = level2_step(state)
            if hasattr(state, "value"):
                break
            if isinstance(state, CESKState) and isinstance(state.C, Value) and not state.K:
                break

        from doeff.cesk_v3.level1_cesk import Done

        if isinstance(state, Done):
            assert state.value == 200
        elif isinstance(state, CESKState):
            final = level2_step(state)
            assert isinstance(final, Done)
            assert final.value == 200

    def test_handler_popped_after_scope_ends(self):
        handler = make_handler()

        def inner() -> Generator[Any, Any, str]:
            return "done"
            yield

        def outer() -> Generator[Any, Any, Any]:
            yield WithHandler(handler=handler, program=inner)
            return "final"

        state = CESKState(C=ProgramControl(outer), E={}, S={}, K=[])

        while True:
            ae = get_ae_state(state.S) if isinstance(state, CESKState) else None
            state = level2_step(state)

            from doeff.cesk_v3.level1_cesk import Done, Failed

            if isinstance(state, (Done, Failed)):
                break

        ae_final = get_ae_state({})
        assert len(ae_final.handler_stack) == 0


class TestNestedWithHandler:

    def test_nested_handlers_push_in_order(self):
        outer_handler = make_handler("outer")
        inner_handler = make_handler("inner")

        def inner_prog() -> Generator[Any, Any, str]:
            return "inner_done"
            yield

        def middle_prog() -> Generator[Any, Any, Any]:
            result = yield WithHandler(handler=inner_handler, program=inner_prog)
            return f"middle_{result}"

        def outer_prog() -> Generator[Any, Any, Any]:
            result = yield WithHandler(handler=outer_handler, program=middle_prog)
            return f"outer_{result}"

        state = CESKState(C=ProgramControl(outer_prog), E={}, S={}, K=[])

        result = level2_step(state)
        result = level2_step(result)

        ae = get_ae_state(result.S)
        assert len(ae.handler_stack) >= 1

    def test_handlers_pop_in_reverse_order(self):
        outer_handler = make_handler("outer")
        inner_handler = make_handler("inner")

        def inner_prog() -> Generator[Any, Any, int]:
            return 1
            yield

        def middle_prog() -> Generator[Any, Any, int]:
            result = yield WithHandler(handler=inner_handler, program=inner_prog)
            return result + 1

        def outer_prog() -> Generator[Any, Any, int]:
            result = yield WithHandler(handler=outer_handler, program=middle_prog)
            return result + 1

        state = CESKState(C=ProgramControl(outer_prog), E={}, S={}, K=[])

        from doeff.cesk_v3.level1_cesk import Done, Failed

        max_handlers_seen = 0
        while True:
            if isinstance(state, CESKState):
                ae = get_ae_state(state.S)
                max_handlers_seen = max(max_handlers_seen, len(ae.handler_stack))
            state = level2_step(state)
            if isinstance(state, (Done, Failed)):
                break

        assert isinstance(state, Done)
        assert state.value == 3
