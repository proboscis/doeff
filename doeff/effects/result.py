"""Result/error handling effects."""

from __future__ import annotations

from dataclasses import dataclass
import inspect
from typing import Any

import doeff_vm
from doeff.types import Err as _PyErr
from doeff.types import Ok as _PyOk

from ._program_types import ProgramLike
from ._validators import ensure_program_like
from .base import EffectBase


@dataclass(frozen=True)
class ResultSafeEffect(EffectBase):
    """Runs the sub-program and yields a Result for success/failure."""

    sub_program: ProgramLike

    def __post_init__(self) -> None:
        ensure_program_like(self.sub_program, name="sub_program")


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


_RustOk = getattr(doeff_vm, "Ok", None)
_RustErr = getattr(doeff_vm, "Err", None)


def _ok(value: Any) -> Any:
    if _RustOk is not None:
        return _RustOk(value)
    return _PyOk(value)


def _err(error: Exception) -> Any:
    if _RustErr is not None:
        return _RustErr(error)
    return _PyErr(error)


def _wrap_kernel_as_result(execution_kernel: Any):
    def wrapped_kernel(*args: Any, **kwargs: Any):
        try:
            gen_or_value = execution_kernel(*args, **kwargs)
        except Exception as exc:
            return _err(exc)

        if not inspect.isgenerator(gen_or_value):
            return _ok(gen_or_value)

        gen = gen_or_value
        try:
            current = next(gen)
        except StopIteration as stop_exc:
            return _ok(stop_exc.value)
        except Exception as exc:
            return _err(exc)

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
                    return _ok(stop_exc.value)
                except Exception as exc:
                    return _err(exc)
                continue

            try:
                current = gen.send(sent_value)
            except StopIteration as stop_exc:
                return _ok(stop_exc.value)
            except Exception as exc:
                return _err(exc)

    return wrapped_kernel


def _is_kpc_like(value: Any) -> bool:
    required = (
        "kleisli_source",
        "args",
        "kwargs",
        "function_name",
        "execution_kernel",
        "created_at",
    )
    return all(hasattr(value, name) for name in required)


def _safe_program(sub_program: ProgramLike) -> ProgramLike:
    ensure_program_like(sub_program, name="sub_program")

    from doeff import do

    @do
    def _safe_body():
        return (yield sub_program)

    safe_body = _safe_body()
    if not _is_kpc_like(safe_body):
        return safe_body

    return _clone_kpc_with_kernel(
        safe_body,
        _wrap_kernel_as_result(safe_body.execution_kernel),
    )


def safe(sub_program: ProgramLike):
    return _safe_program(sub_program)


def Safe(sub_program: ProgramLike) -> ProgramLike:
    return _safe_program(sub_program)


__all__ = [
    "ResultSafeEffect",
    "Safe",
    "safe",
]
