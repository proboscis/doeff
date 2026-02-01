"""Base runtime class with shared RuntimeResult building logic."""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeVar

from doeff._vendor import Err, FrozenDict, Ok
from doeff.cesk.handlers import Handler
from doeff.cesk.result import Done, Failed
from doeff.cesk.runtime_result import (
    EffectStackTrace,
    KStackTrace,
    PythonStackTrace,
    RuntimeResultImpl,
    build_k_stack_trace,
    build_stacks_from_captured_traceback,
)
from doeff.cesk.state import CESKState

if TYPE_CHECKING:
    from doeff.cesk.types import Environment, Store
    from doeff.program import Program

T = TypeVar("T")


@dataclass
class ExecutionError(Exception):
    """Exception that carries both the error and the final state at failure."""
    exception: BaseException
    final_state: CESKState
    captured_traceback: Any = None

    def __str__(self) -> str:
        exc_type = type(self.exception).__name__
        return f"{exc_type}: {self.exception}"


class BaseRuntime(ABC):
    """Abstract base class for CESK runtimes.

    Provides shared helper methods for building RuntimeResult and stepping
    through execution. Concrete runtimes (SyncRuntime, AsyncRuntime, etc.)
    implement their own run() methods with appropriate sync/async signatures.
    """

    def __init__(self, handlers: dict[type, Handler] | None = None):
        self._handlers = handlers if handlers is not None else {}

    def _create_initial_state(
        self,
        program: Program,
        env: dict[str, Any] | None = None,
        store: dict[str, Any] | None = None,
    ) -> CESKState:
        frozen_env: Environment
        if env is None:
            frozen_env = FrozenDict()
        else:
            frozen_env = FrozenDict(env)

        final_store: Store = store if store is not None else {}
        return CESKState.initial(program, frozen_env, final_store)

    def _build_success_result(
        self,
        value: T,
        state: CESKState,
        final_store: dict[str, Any] | None = None,
    ) -> RuntimeResultImpl[T]:
        store = final_store if final_store is not None else state.store

        main_task = state.tasks.get(state.main_task)
        k_stack = KStackTrace(frames=())
        final_env: dict[Any, Any] = {}
        if main_task:
            k_stack = build_k_stack_trace(main_task.kontinuation)
            final_env = dict(main_task.env)

        return RuntimeResultImpl(
            _result=Ok(value),
            _raw_store=dict(store),
            _env=final_env,
            _k_stack=k_stack,
            _effect_stack=EffectStackTrace(),
            _python_stack=PythonStackTrace(frames=()),
        )

    def _build_error_result(
        self,
        exc: BaseException,
        state: CESKState,
        final_store: dict[str, Any] | None = None,
        captured_traceback: Any = None,
    ) -> RuntimeResultImpl[Any]:
        store = final_store if final_store is not None else state.store

        main_task = state.tasks.get(state.main_task)
        k_stack = KStackTrace(frames=())
        final_env: dict[Any, Any] = {}
        if main_task:
            k_stack = build_k_stack_trace(main_task.kontinuation)
            final_env = dict(main_task.env)

        if captured_traceback is None:
            captured_traceback = getattr(exc, "__cesk_traceback__", None)
        python_stack, effect_stack = build_stacks_from_captured_traceback(captured_traceback)

        return RuntimeResultImpl(
            _result=Err(exc),  # type: ignore[arg-type]
            _raw_store=dict(store),
            _env=final_env,
            _k_stack=k_stack,
            _effect_stack=effect_stack,
            _python_stack=python_stack,
            _captured_traceback=captured_traceback,
        )

    def _step_until_done(self, state: CESKState) -> tuple[Any, CESKState, dict[str, Any]]:
        from doeff.cesk.step import step

        while True:
            result = step(state)

            if isinstance(result, Done):
                return (result.value, state, result.store)

            if isinstance(result, Failed):
                raise ExecutionError(
                    exception=result.exception,
                    final_state=state,
                    captured_traceback=result.captured_traceback,
                )

            if isinstance(result, CESKState):
                state = result
                continue

            raise RuntimeError(f"Unexpected step result: {type(result)}")


__all__ = [
    "BaseRuntime",
    "ExecutionError",
]
