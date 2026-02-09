"""Result/error handling effects."""

from __future__ import annotations

from dataclasses import dataclass
import inspect
from typing import Any

from doeff.types import Err, Ok

from ._program_types import ProgramLike
from ._validators import ensure_program_like
from .base import Effect, EffectBase, create_effect_with_trace


def _clone_kpc_with_kernel(kpc: Any, execution_kernel: Any) -> Any:
    cls = type(kpc)
    return cls(
        kleisli_source=kpc.kleisli_source,
        args=tuple(kpc.args),
        kwargs=dict(kpc.kwargs),
        function_name=kpc.function_name,
        execution_kernel=execution_kernel,
        created_at=kpc.created_at,
    )


def _wrap_kernel_as_result(execution_kernel: Any):
    def wrapped_kernel(*args: Any, **kwargs: Any):
        try:
            gen_or_value = execution_kernel(*args, **kwargs)
        except Exception as exc:
            return Err(exc)

        if not inspect.isgenerator(gen_or_value):
            return Ok(gen_or_value)

        gen = gen_or_value

        try:
            current = next(gen)
        except StopIteration as stop_exc:
            return Ok(stop_exc.value)
        except Exception as exc:
            return Err(exc)

        while True:
            try:
                sent_value = yield current
            except GeneratorExit:
                gen.close()
                raise
            except BaseException as thrown:
                try:
                    current = gen.throw(thrown)
                except StopIteration as stop_exc:
                    return Ok(stop_exc.value)
                except Exception as exc:
                    return Err(exc)
                continue

            try:
                current = gen.send(sent_value)
            except StopIteration as stop_exc:
                return Ok(stop_exc.value)
            except Exception as exc:
                return Err(exc)

    return wrapped_kernel


@dataclass(frozen=True)
class ResultSafeEffect(EffectBase):
    """Runs the sub-program and yields a Result for success/failure."""

    sub_program: ProgramLike

    def __post_init__(self) -> None:
        ensure_program_like(self.sub_program, name="sub_program")


def safe(sub_program: ProgramLike):
    ensure_program_like(sub_program, name="sub_program")

    from doeff import do

    @do
    def _safe_body():
        return (yield sub_program)

    safe_body = _safe_body()
    return _clone_kpc_with_kernel(safe_body, _wrap_kernel_as_result(safe_body.execution_kernel))


def Safe(sub_program: ProgramLike) -> Effect:
    ensure_program_like(sub_program, name="sub_program")

    from doeff import do

    @do
    def _safe_body():
        return (yield sub_program)

    safe_body = _safe_body()
    return _clone_kpc_with_kernel(safe_body, _wrap_kernel_as_result(safe_body.execution_kernel))


__all__ = [
    "ResultSafeEffect",
    "Safe",
    "safe",
]
