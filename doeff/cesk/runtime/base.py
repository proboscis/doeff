"""Base runtime class with shared RuntimeResult building logic."""

from __future__ import annotations

from abc import ABC
from typing import TYPE_CHECKING, Any, TypeVar

from doeff._vendor import FrozenDict, Ok, Err
from doeff.cesk.state import CESKState, TaskState, Ready
from doeff.cesk.result import Done, Failed, Suspended
from doeff.cesk.step import step
from doeff.cesk.handlers import Handler, default_handlers
from doeff.cesk.frames import ContinueValue, ContinueError, ContinueProgram, FrameResult
from doeff.cesk.errors import UnhandledEffectError
from doeff.cesk.runtime_result import (
    RuntimeResult,
    RuntimeResultImpl,
    KStackTrace,
    EffectStackTrace,
    PythonStackTrace,
    build_k_stack_trace,
    build_stacks_from_captured_traceback,
)

if TYPE_CHECKING:
    from doeff.program import Program
    from doeff.cesk.types import Environment, Store

T = TypeVar("T")


class BaseRuntime(ABC):
    """Abstract base class for CESK runtimes.

    Provides shared helper methods for building RuntimeResult and stepping
    through execution. Concrete runtimes (SyncRuntime, AsyncRuntime, etc.)
    implement their own run() methods with appropriate sync/async signatures.
    """

    def __init__(self, handlers: dict[type, Handler] | None = None):
        self._handlers = handlers if handlers is not None else default_handlers()

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

    def _dispatch_effect(
        self,
        effect: Any,
        task_state: TaskState,
        store: dict[str, Any],
    ) -> FrameResult:
        handler = self._handlers.get(type(effect))

        if handler is None:
            return ContinueError(
                error=UnhandledEffectError(f"No handler for {type(effect).__name__}"),
                env=task_state.env,
                store=store,
                k=task_state.kontinuation,
            )

        try:
            frame_result = handler(effect, task_state, store)
        except Exception as ex:
            return ContinueError(error=ex, env=task_state.env, store=store, k=task_state.kontinuation)

        if isinstance(frame_result, (ContinueValue, ContinueError, ContinueProgram)):
            return frame_result

        return ContinueError(
            error=RuntimeError(f"Handler returned unexpected type: {type(frame_result)}"),
            env=task_state.env,
            store=store,
            k=task_state.kontinuation,
        )

    def _build_success_result(
        self,
        value: T,
        state: CESKState,
    ) -> RuntimeResultImpl[T]:
        """Build RuntimeResult for successful execution."""
        # Extract state (excluding internal keys)
        final_state = {
            k: v for k, v in state.store.items()
            if not k.startswith("__")
        }

        # Extract log
        final_log = list(state.store.get("__log__", []))

        # Extract graph if captured
        final_graph = state.store.get("__graph__")

        # Get main task for env and k_stack
        main_task = state.tasks.get(state.main_task)
        k_stack = KStackTrace(frames=())
        final_env: dict[Any, Any] = {}
        if main_task:
            k_stack = build_k_stack_trace(main_task.kontinuation)
            final_env = dict(main_task.env)

        return RuntimeResultImpl(
            _result=Ok(value),
            _state=final_state,
            _log=final_log,
            _env=final_env,
            _k_stack=k_stack,
            _effect_stack=EffectStackTrace(),  # Success path: no error tree needed
            _python_stack=PythonStackTrace(frames=()),  # Success path: no error stack needed
            _graph=final_graph,
        )

    def _build_error_result(
        self,
        exc: Exception,
        state: CESKState,
    ) -> RuntimeResultImpl[Any]:
        """Build RuntimeResult for failed execution."""
        # Extract state (excluding internal keys)
        final_state = {
            k: v for k, v in state.store.items()
            if not k.startswith("__")
        }

        # Extract log
        final_log = list(state.store.get("__log__", []))

        # Extract graph if captured (DON'T lose it on error!)
        final_graph = state.store.get("__graph__")

        # Get main task for env and k_stack
        main_task = state.tasks.get(state.main_task)
        k_stack = KStackTrace(frames=())
        final_env: dict[Any, Any] = {}
        if main_task:
            k_stack = build_k_stack_trace(main_task.kontinuation)
            final_env = dict(main_task.env)

        # Get captured traceback if available and convert to stack traces
        captured_tb = getattr(exc, "__cesk_traceback__", None)
        python_stack, effect_stack = build_stacks_from_captured_traceback(captured_tb)

        return RuntimeResultImpl(
            _result=Err(exc),
            _state=final_state,
            _log=final_log,
            _env=final_env,
            _k_stack=k_stack,
            _effect_stack=effect_stack,
            _python_stack=python_stack,
            _graph=final_graph,
            _captured_traceback=captured_tb,
        )

    def _step_until_done(self, state: CESKState) -> tuple[Any, CESKState]:
        """Step execution until completion, returning (value, final_state)."""
        from doeff.cesk.state import ProgramControl

        while True:
            result = step(state, self._handlers)

            if isinstance(result, Done):
                return (result.value, state)

            if isinstance(result, Failed):
                exc = result.exception
                if result.captured_traceback is not None:
                    exc.__cesk_traceback__ = result.captured_traceback  # type: ignore[attr-defined]
                raise exc

            if isinstance(result, CESKState):
                state = result
                continue

            if isinstance(result, Suspended):
                main_task = state.tasks[state.main_task]
                dispatch_result = self._dispatch_effect(
                    result.effect, main_task, state.store
                )

                if isinstance(dispatch_result, ContinueError):
                    state = result.resume_error(dispatch_result.error)
                elif isinstance(dispatch_result, ContinueProgram):
                    state = CESKState(
                        C=ProgramControl(dispatch_result.program),
                        E=dispatch_result.env,
                        S=dispatch_result.store,
                        K=dispatch_result.k,
                    )
                elif isinstance(dispatch_result, ContinueValue):
                    state = result.resume(dispatch_result.value, dispatch_result.store)
                else:
                    raise RuntimeError(f"Unexpected dispatch result: {type(dispatch_result)}")
                continue

            raise RuntimeError(f"Unexpected step result: {type(result)}")


__all__ = [
    "BaseRuntime",
]
