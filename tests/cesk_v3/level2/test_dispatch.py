from dataclasses import dataclass

import pytest

from doeff.cesk_v3.errors import UnhandledEffectError
from doeff.cesk_v3.level1_cesk.frames import ReturnFrame
from doeff.cesk_v3.level1_cesk.state import (
    CESKState,
    EffectYield,
    ProgramControl,
    Value,
)
from doeff.cesk_v3.level2_algebraic_effects.dispatch import (
    collect_available_handlers,
    start_dispatch,
)
from doeff.cesk_v3.level2_algebraic_effects.frames import (
    DispatchingFrame,
    EffectBase,
    WithHandlerFrame,
)
from doeff.cesk_v3.level2_algebraic_effects.handlers import (
    handle_create_continuation,
    handle_get_handlers,
    handle_resume_continuation,
)
from doeff.cesk_v3.level2_algebraic_effects.primitives import (
    Continuation,
    CreateContinuation,
    GetHandlers,
    ResumeContinuation,
)
from doeff.cesk_v3.level2_algebraic_effects.step import level2_step
from doeff.program import Program


@dataclass(frozen=True)
class SampleEffect(EffectBase):
    value: int


def make_handler(name: str):
    def handler(effect):
        yield f"{name}_handled"

    return handler


class TestCollectAvailableHandlers:

    def test_empty_k_returns_empty(self) -> None:
        handlers = collect_available_handlers([])
        assert handlers == []

    def test_single_whf(self) -> None:
        h1 = make_handler("h1")
        k = [WithHandlerFrame(handler=h1)]

        handlers = collect_available_handlers(k)

        assert handlers == [h1]

    def test_multiple_whf_in_order(self) -> None:
        h1 = make_handler("h1")
        h2 = make_handler("h2")
        h3 = make_handler("h3")

        k = [
            WithHandlerFrame(handler=h1),
            WithHandlerFrame(handler=h2),
            WithHandlerFrame(handler=h3),
        ]

        handlers = collect_available_handlers(k)

        assert handlers == [h1, h2, h3]

    def test_mixed_frames(self) -> None:
        h1 = make_handler("h1")
        h2 = make_handler("h2")

        def gen():
            yield 1

        k = [
            ReturnFrame(generator=gen()),
            WithHandlerFrame(handler=h1),
            ReturnFrame(generator=gen()),
            WithHandlerFrame(handler=h2),
        ]

        handlers = collect_available_handlers(k)

        assert handlers == [h1, h2]

    def test_stops_at_dispatching_frame_busy_boundary(self) -> None:
        h1 = make_handler("h1")
        h2 = make_handler("h2")
        h3 = make_handler("h3")

        df = DispatchingFrame(
            effect=SampleEffect(0),
            handler_idx=1,
            handlers=(h2, h3),
            handler_started=True,
        )

        k = [
            WithHandlerFrame(handler=h1),
            df,
            WithHandlerFrame(handler=h2),
            WithHandlerFrame(handler=h3),
        ]

        handlers = collect_available_handlers(k)

        assert handlers == [h2, h1]

    def test_busy_boundary_uses_parent_handlers_up_to_idx(self) -> None:
        h1 = make_handler("h1")
        h2 = make_handler("h2")
        h3 = make_handler("h3")

        df = DispatchingFrame(
            effect=SampleEffect(0),
            handler_idx=0,
            handlers=(h1, h2, h3),
            handler_started=True,
        )

        k = [df]

        handlers = collect_available_handlers(k)

        assert handlers == []


class TestStartDispatch:

    def test_creates_dispatching_frame(self) -> None:
        h1 = make_handler("h1")

        state = CESKState(
            C=EffectYield(SampleEffect(42)),
            E={},
            S={},
            K=[WithHandlerFrame(handler=h1)],
        )

        result = start_dispatch(SampleEffect(42), state)

        assert isinstance(result, CESKState)
        assert isinstance(result.C, Value)
        assert len(result.K) == 2
        assert isinstance(result.K[0], DispatchingFrame)

        df = result.K[0]
        assert df.effect == SampleEffect(42)
        assert df.handler_idx == 0
        assert df.handlers == (h1,)
        assert df.handler_started is False

    def test_multiple_handlers_highest_idx(self) -> None:
        h1 = make_handler("h1")
        h2 = make_handler("h2")

        state = CESKState(
            C=EffectYield(SampleEffect(1)),
            E={},
            S={},
            K=[
                WithHandlerFrame(handler=h1),
                WithHandlerFrame(handler=h2),
            ],
        )

        result = start_dispatch(SampleEffect(1), state)

        df = result.K[0]
        assert isinstance(df, DispatchingFrame)
        assert df.handler_idx == 1
        assert df.handlers == (h1, h2)

    def test_raises_unhandled_effect_error(self) -> None:
        state = CESKState(
            C=EffectYield(SampleEffect(1)),
            E={},
            S={},
            K=[],
        )

        with pytest.raises(UnhandledEffectError):
            start_dispatch(SampleEffect(1), state)


class TestLevel2StepWithHandlerFrame:

    def test_whf_pops_on_value(self) -> None:
        h1 = make_handler("h1")

        state = CESKState(
            C=Value(42),
            E={},
            S={},
            K=[WithHandlerFrame(handler=h1)],
        )

        result = level2_step(state)

        assert isinstance(result, CESKState)
        assert isinstance(result.C, Value)
        assert result.C.value == 42
        assert result.K == []


class TestLevel2StepDispatchingFrame:

    def test_df_starts_handler_when_not_started(self) -> None:
        def handler(effect):
            yield f"handling {effect}"

        state = CESKState(
            C=Value(None),
            E={},
            S={},
            K=[
                DispatchingFrame(
                    effect=SampleEffect(42),
                    handler_idx=0,
                    handlers=(handler,),
                    handler_started=False,
                ),
                WithHandlerFrame(handler=handler),
            ],
        )

        result = level2_step(state)

        assert isinstance(result, CESKState)
        assert isinstance(result.C, ProgramControl)

        df = result.K[0]
        assert isinstance(df, DispatchingFrame)
        assert df.handler_started is True

    def test_df_raises_unhandled_when_idx_negative(self) -> None:
        def handler(effect):
            yield effect

        state = CESKState(
            C=Value(None),
            E={},
            S={},
            K=[
                DispatchingFrame(
                    effect=SampleEffect(1),
                    handler_idx=-1,
                    handlers=(),
                    handler_started=False,
                ),
            ],
        )

        with pytest.raises(UnhandledEffectError):
            level2_step(state)


class TestHandleGetHandlers:

    def test_returns_handlers_from_dispatching_frame(self) -> None:
        h1 = make_handler("h1")
        h2 = make_handler("h2")

        df = DispatchingFrame(
            effect=SampleEffect(1),
            handler_idx=1,
            handlers=(h1, h2),
            handler_started=True,
        )

        def gen():
            yield 1

        state = CESKState(
            C=EffectYield(GetHandlers()),
            E={},
            S={},
            K=[
                ReturnFrame(generator=gen()),
                df,
                WithHandlerFrame(handler=h1),
                WithHandlerFrame(handler=h2),
            ],
        )

        result = handle_get_handlers(GetHandlers(), state)

        assert isinstance(result, CESKState)
        assert isinstance(result.C, Value)
        assert result.C.value == (h1, h2)
        assert result.K == state.K

    def test_raises_outside_dispatch_context(self) -> None:
        state = CESKState(
            C=EffectYield(GetHandlers()),
            E={},
            S={},
            K=[],
        )

        with pytest.raises(RuntimeError, match="outside handler context"):
            handle_get_handlers(GetHandlers(), state)


class TestHandleCreateContinuation:

    def test_creates_unstarted_continuation(self) -> None:
        h1 = make_handler("h1")
        prog = Program.pure(42)

        state = CESKState(
            C=EffectYield(CreateContinuation(prog, handlers=(h1,))),
            E={},
            S={},
            K=[],
        )

        result = handle_create_continuation(
            CreateContinuation(prog, handlers=(h1,)), state
        )

        assert isinstance(result, CESKState)
        assert isinstance(result.C, Value)

        cont = result.C.value
        assert isinstance(cont, Continuation)
        assert cont.started is False
        assert cont.program is prog
        assert cont.handlers == (h1,)
        assert cont.frames == ()

    def test_creates_continuation_with_empty_handlers(self) -> None:
        prog = Program.pure("test")

        state = CESKState(
            C=EffectYield(CreateContinuation(prog, handlers=())),
            E={},
            S={},
            K=[],
        )

        result = handle_create_continuation(CreateContinuation(prog, handlers=()), state)

        cont = result.C.value
        assert cont.handlers == ()
        assert cont.started is False


class TestHandleResumeContinuationUnstarted:

    def test_builds_k_from_handlers_and_starts_program(self) -> None:
        h1 = make_handler("h1")
        prog = Program.pure(100)

        unstarted_cont = Continuation(
            cont_id=999,
            frames=(),
            program=prog,
            started=False,
            handlers=(h1,),
        )

        def handler_gen():
            yield "handler_yield"

        gen = handler_gen()
        next(gen)

        df = DispatchingFrame(
            effect=SampleEffect(1),
            handler_idx=0,
            handlers=(h1,),
            handler_started=True,
        )

        state = CESKState(
            C=EffectYield(ResumeContinuation(unstarted_cont, None)),
            E={},
            S={},
            K=[
                ReturnFrame(generator=gen),
                df,
                WithHandlerFrame(handler=h1),
            ],
        )

        result = handle_resume_continuation(
            ResumeContinuation(unstarted_cont, None), state
        )

        assert isinstance(result, CESKState)
        assert isinstance(result.C, ProgramControl)
        assert result.C.program is prog

        assert isinstance(result.K[0], WithHandlerFrame)
        assert result.K[0].handler is h1

    def test_raises_if_unstarted_has_no_program(self) -> None:
        h1 = make_handler("h1")

        bad_cont = Continuation(
            cont_id=888,
            frames=(),
            program=None,
            started=False,
            handlers=(h1,),
        )

        def handler_gen():
            yield "x"

        gen = handler_gen()
        next(gen)

        df = DispatchingFrame(
            effect=SampleEffect(1),
            handler_idx=0,
            handlers=(h1,),
            handler_started=True,
        )

        state = CESKState(
            C=EffectYield(ResumeContinuation(bad_cont, None)),
            E={},
            S={},
            K=[
                ReturnFrame(generator=gen),
                df,
                WithHandlerFrame(handler=h1),
            ],
        )

        with pytest.raises(RuntimeError, match="must have a program"):
            handle_resume_continuation(ResumeContinuation(bad_cont, None), state)
