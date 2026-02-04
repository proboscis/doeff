from __future__ import annotations

from typing import TYPE_CHECKING, Any, Generator

from doeff.cesk_v3.level1_cesk.frames import ReturnFrame
from doeff.cesk_v3.level1_cesk.state import (
    CESKState,
    Done,
    EffectYield,
    Error,
    Failed,
    ProgramControl,
    Value,
)

if TYPE_CHECKING:
    from doeff.program import Program


def to_generator(program: Program[Any]) -> Generator[Any, Any, Any]:
    if hasattr(program, "to_generator"):
        return program.to_generator()  # type: ignore
    if hasattr(program, "__iter__"):
        return iter(program)  # type: ignore
    raise TypeError(f"Cannot convert {type(program).__name__} to generator")


def cesk_step(state: CESKState) -> CESKState | Done | Failed:
    C, E, S, K = state.C, state.E, state.S, state.K

    if isinstance(C, ProgramControl):
        gen = to_generator(C.program)
        try:
            yielded = next(gen)
            return CESKState(
                C=EffectYield(yielded), E=E, S=S, K=[ReturnFrame(gen)] + K
            )
        except StopIteration as e:
            return CESKState(C=Value(e.value), E=E, S=S, K=K)
        except Exception as e:
            return CESKState(C=Error(e), E=E, S=S, K=K)

    if isinstance(C, Value) and K:
        frame = K[0]
        assert isinstance(frame, ReturnFrame), (
            f"Level 1 only handles ReturnFrame, got {type(frame).__name__}"
        )
        try:
            yielded = frame.generator.send(C.value)
            return CESKState(C=EffectYield(yielded), E=E, S=S, K=K)
        except StopIteration as e:
            return CESKState(C=Value(e.value), E=E, S=S, K=K[1:])
        except Exception as e:
            return CESKState(C=Error(e), E=E, S=S, K=K[1:])

    if isinstance(C, Error) and K:
        frame = K[0]
        assert isinstance(frame, ReturnFrame), (
            f"Level 1 only handles ReturnFrame, got {type(frame).__name__}"
        )
        try:
            yielded = frame.generator.throw(type(C.error), C.error)
            return CESKState(C=EffectYield(yielded), E=E, S=S, K=K)
        except StopIteration as e:
            return CESKState(C=Value(e.value), E=E, S=S, K=K[1:])
        except Exception as e:
            return CESKState(C=Error(e), E=E, S=S, K=K[1:])

    if isinstance(C, Value) and not K:
        return Done(C.value)

    if isinstance(C, Error) and not K:
        return Failed(C.error)

    return state
