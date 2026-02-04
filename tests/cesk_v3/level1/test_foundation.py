from dataclasses import FrozenInstanceError

import pytest

from doeff.cesk_v3.level1_cesk.frames import ReturnFrame
from doeff.cesk_v3.level1_cesk.state import (
    CESKState,
    Control,
    Done,
    EffectYield,
    Error,
    Failed,
    ProgramControl,
    Value,
)
from doeff.cesk_v3.level2_algebraic_effects.frames import (
    DispatchingFrame,
    EffectBase,
    WithHandlerFrame,
)


class TestReturnFrame:

    def test_return_frame_holds_generator(self) -> None:
        def gen():
            yield 1

        g = gen()
        frame = ReturnFrame(generator=g)
        assert frame.generator is g

    def test_return_frame_is_frozen(self) -> None:
        def gen():
            yield 1

        frame = ReturnFrame(generator=gen())
        with pytest.raises(FrozenInstanceError):
            frame.generator = None  # type: ignore

    def test_return_frame_equality(self) -> None:
        def gen():
            yield 1

        g = gen()
        frame1 = ReturnFrame(generator=g)
        frame2 = ReturnFrame(generator=g)
        assert frame1 == frame2


class TestWithHandlerFrame:

    def test_whf_holds_handler(self) -> None:
        def handler(e):
            yield e

        frame = WithHandlerFrame(handler=handler)
        assert frame.handler is handler

    def test_whf_is_frozen(self) -> None:
        def handler(e):
            yield e

        frame = WithHandlerFrame(handler=handler)
        with pytest.raises(FrozenInstanceError):
            frame.handler = None  # type: ignore

    def test_whf_identity_comparison(self) -> None:
        def handler1(e):
            yield e

        def handler2(e):
            yield e

        frame1 = WithHandlerFrame(handler=handler1)
        frame2 = WithHandlerFrame(handler=handler1)
        frame3 = WithHandlerFrame(handler=handler2)

        assert frame1 == frame2
        assert frame1 != frame3
        assert frame1.handler is handler1
        assert frame3.handler is handler2


class TestDispatchingFrame:

    def test_df_holds_effect_and_handlers(self) -> None:
        class MyEffect(EffectBase):
            pass

        def handler(e):
            yield e

        effect = MyEffect()
        df = DispatchingFrame(
            effect=effect,
            handler_idx=0,
            handlers=(handler,),
            handler_started=False,
        )

        assert df.effect is effect
        assert df.handler_idx == 0
        assert df.handlers == (handler,)
        assert df.handler_started is False

    def test_df_with_handler_started(self) -> None:
        class MyEffect(EffectBase):
            pass

        def handler(e):
            yield e

        df = DispatchingFrame(
            effect=MyEffect(),
            handler_idx=0,
            handlers=(handler,),
            handler_started=False,
        )

        df_started = df.with_handler_started()

        assert df.handler_started is False
        assert df_started.handler_started is True
        assert df_started.effect is df.effect
        assert df_started.handlers is df.handlers

    def test_df_is_frozen(self) -> None:
        class MyEffect(EffectBase):
            pass

        def handler(e):
            yield e

        df = DispatchingFrame(
            effect=MyEffect(),
            handler_idx=0,
            handlers=(handler,),
        )

        with pytest.raises(FrozenInstanceError):
            df.handler_started = True  # type: ignore

    def test_df_multiple_handlers(self) -> None:
        class MyEffect(EffectBase):
            pass

        def h1(e):
            yield e

        def h2(e):
            yield e

        def h3(e):
            yield e

        df = DispatchingFrame(
            effect=MyEffect(),
            handler_idx=2,
            handlers=(h1, h2, h3),
        )

        assert len(df.handlers) == 3
        assert df.handlers[df.handler_idx] is h3


class TestControlTypes:

    def test_program_control(self) -> None:
        program = object()
        pc = ProgramControl(program=program)
        assert pc.program is program

    def test_value(self) -> None:
        v = Value(value=42)
        assert v.value == 42

    def test_value_none(self) -> None:
        v = Value(value=None)
        assert v.value is None

    def test_error(self) -> None:
        exc = ValueError("test error")
        e = Error(error=exc)
        assert e.error is exc
        assert isinstance(e.error, ValueError)

    def test_effect_yield(self) -> None:
        class MyEffect(EffectBase):
            pass

        effect = MyEffect()
        ey = EffectYield(yielded=effect)
        assert ey.yielded is effect

    def test_done(self) -> None:
        d = Done(value="result")
        assert d.value == "result"

    def test_failed(self) -> None:
        exc = RuntimeError("failed")
        f = Failed(error=exc)
        assert f.error is exc

    def test_control_type_union(self) -> None:
        pc = ProgramControl(program=None)
        v = Value(value=1)
        e = Error(error=Exception())
        ey = EffectYield(yielded=None)

        assert isinstance(pc, (ProgramControl, Value, Error, EffectYield))
        assert isinstance(v, (ProgramControl, Value, Error, EffectYield))
        assert isinstance(e, (ProgramControl, Value, Error, EffectYield))
        assert isinstance(ey, (ProgramControl, Value, Error, EffectYield))


class TestCESKState:

    def test_cesk_state_construction(self) -> None:
        state = CESKState(
            C=Value(42),
            E={"key": "value"},
            S={"store_key": 100},
            K=[],
        )

        assert isinstance(state.C, Value)
        assert state.C.value == 42
        assert state.E == {"key": "value"}
        assert state.S == {"store_key": 100}
        assert state.K == []

    def test_cesk_state_with_frames(self) -> None:
        def gen():
            yield 1

        def handler(e):
            yield e

        rf = ReturnFrame(generator=gen())
        whf = WithHandlerFrame(handler=handler)

        state = CESKState(
            C=ProgramControl(program=None),
            E={},
            S={},
            K=[rf, whf],
        )

        assert len(state.K) == 2
        assert isinstance(state.K[0], ReturnFrame)
        assert isinstance(state.K[1], WithHandlerFrame)

    def test_cesk_state_is_frozen(self) -> None:
        state = CESKState(C=Value(1), E={}, S={}, K=[])
        with pytest.raises(FrozenInstanceError):
            state.C = Value(2)  # type: ignore
