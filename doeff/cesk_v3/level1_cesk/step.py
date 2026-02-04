from __future__ import annotations

from typing import Any, Generator, cast
from collections.abc import Generator as GeneratorABC

from doeff.cesk_v3.level1_cesk.state import (
    CESKState,
    ProgramControl,
    Value,
    Error,
    EffectYield,
    Done,
    Failed,
)
from doeff.cesk_v3.level1_cesk.frames import ReturnFrame


def _to_generator(program: Any) -> Generator[Any, Any, Any]:
    if isinstance(program, GeneratorABC):
        return cast(Generator[Any, Any, Any], program)
    if callable(program):
        result = program()
        if isinstance(result, GeneratorABC):
            return cast(Generator[Any, Any, Any], result)
        raise TypeError(f"Callable did not return a generator, got {type(result).__name__}")
    raise TypeError(f"Cannot convert {type(program).__name__} to generator")


def cesk_step(state: CESKState) -> CESKState | Done | Failed:
    C, E, S, K = state.C, state.E, state.S, state.K

    if isinstance(C, ProgramControl):
        gen = _to_generator(C.program)
        try:
            yielded = next(gen)
            return CESKState(
                C=EffectYield(yielded),
                E=E,
                S=S,
                K=[ReturnFrame(gen)] + K,
            )
        except StopIteration as e:
            return CESKState(C=Value(e.value), E=E, S=S, K=K)
        except Exception as e:
            return CESKState(C=Error(e), E=E, S=S, K=K)

    if isinstance(C, Value) and K:
        frame = K[0]
        rest_k = K[1:]
        assert isinstance(frame, ReturnFrame), (
            f"Level 1 only handles ReturnFrame, got {type(frame).__name__}"
        )
        try:
            yielded = frame.generator.send(C.value)
            return CESKState(C=EffectYield(yielded), E=E, S=S, K=K)
        except StopIteration as e:
            return CESKState(C=Value(e.value), E=E, S=S, K=rest_k)
        except Exception as e:
            return CESKState(C=Error(e), E=E, S=S, K=rest_k)

    if isinstance(C, Error) and K:
        frame = K[0]
        rest_k = K[1:]
        assert isinstance(frame, ReturnFrame), (
            f"Level 1 only handles ReturnFrame, got {type(frame).__name__}"
        )
        try:
            yielded = frame.generator.throw(C.error)
            return CESKState(C=EffectYield(yielded), E=E, S=S, K=K)
        except StopIteration as e:
            return CESKState(C=Value(e.value), E=E, S=S, K=rest_k)
        except Exception as e:
            return CESKState(C=Error(e), E=E, S=S, K=rest_k)

    if isinstance(C, Value) and not K:
        return Done(C.value)

    if isinstance(C, Error) and not K:
        return Failed(C.error)

    return state
