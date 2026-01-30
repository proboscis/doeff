"""Base runtime class with shared RuntimeResult building logic."""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeVar

from doeff._vendor import Err, FrozenDict, Ok
from doeff.cesk.errors import UnhandledEffectError
from doeff.cesk.frames import ContinueError, ContinueProgram, ContinueValue, FrameResult, SuspendOn
from doeff.cesk.handlers import Handler, default_handlers
from doeff.cesk.result import Done, Failed, Suspended
from doeff.cesk.runtime.context import HandlerContext
from doeff.cesk.runtime_result import (
    EffectStackTrace,
    KStackTrace,
    PythonStackTrace,
    RuntimeResultImpl,
    build_k_stack_trace,
    build_stacks_from_captured_traceback,
)
from doeff.cesk.state import CESKState, TaskState
from doeff.cesk.step import step

if TYPE_CHECKING:
    from doeff.cesk.types import Environment, Store
    from doeff.program import Program

T = TypeVar("T")


@dataclass
class ExecutionError(Exception):
    """Exception that carries both the error and the final state at failure.
    
    This allows runtimes to build accurate RuntimeResult with the state
    at the point of failure, not the initial state.
    """
    exception: BaseException
    final_state: CESKState
    captured_traceback: Any = None  # CapturedTraceback | None

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
        ctx: HandlerContext | None = None,
    ) -> FrameResult:
        handler = self._handlers.get(type(effect))

        if handler is None:
            return ContinueError(
                error=UnhandledEffectError(f"No handler for {type(effect).__name__}"),
                env=task_state.env,
                store=store,
                k=task_state.kontinuation,
            )

        if ctx is None:
            ctx = HandlerContext(task_state=task_state, store=store, suspend=None)

        try:
            frame_result = handler(effect, ctx)
        except Exception as ex:
            return ContinueError(error=ex, env=task_state.env, store=store, k=task_state.kontinuation)

        if isinstance(frame_result, (ContinueValue, ContinueError, ContinueProgram, SuspendOn)):
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
        final_store: dict[str, Any] | None = None,
    ) -> RuntimeResultImpl[T]:
        """Build RuntimeResult for successful execution.
        
        Args:
            value: The final computed value
            state: The final CESKState
            final_store: Optional override for the store (e.g., from Done.store)
            
        Note:
            Stack traces (effect_stack, python_stack) are only populated on error.
            On success, these are empty/None. This is intentional - trace capture
            is error-driven to avoid performance overhead on the happy path.
            The k_stack is populated from the final continuation state.
        """
        # Use final_store if provided (from Done.store), else state.store
        store = final_store if final_store is not None else state.store

        # Extract state (excluding internal keys)
        final_state = {
            k: v for k, v in store.items()
            if not k.startswith("__")
        }

        # Extract log
        final_log = list(store.get("__log__", []))

        # Extract graph if captured
        final_graph = store.get("__graph__")

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
            _effect_stack=EffectStackTrace(),  # Success path: traces not captured
            _python_stack=PythonStackTrace(frames=()),  # Success path: traces not captured
            _graph=final_graph,
        )

    def _build_error_result(
        self,
        exc: BaseException,
        state: CESKState,
        final_store: dict[str, Any] | None = None,
        captured_traceback: Any = None,
    ) -> RuntimeResultImpl[Any]:
        """Build RuntimeResult for failed execution.
        
        Args:
            exc: The exception that caused the failure
            state: The CESKState at the point of failure
            final_store: Optional override for the store (e.g., from Failed.store)
            captured_traceback: Optional pre-captured traceback
        """
        # Use final_store if provided (from Failed.store), else state.store
        store = final_store if final_store is not None else state.store

        # Extract state (excluding internal keys)
        final_state = {
            k: v for k, v in store.items()
            if not k.startswith("__")
        }

        # Extract log
        final_log = list(store.get("__log__", []))

        # Extract graph if captured (DON'T lose it on error!)
        final_graph = store.get("__graph__")

        # Get main task for env and k_stack
        main_task = state.tasks.get(state.main_task)
        k_stack = KStackTrace(frames=())
        final_env: dict[Any, Any] = {}
        if main_task:
            k_stack = build_k_stack_trace(main_task.kontinuation)
            final_env = dict(main_task.env)

        # Get captured traceback - prefer explicit param, then check exception attr
        if captured_traceback is None:
            captured_traceback = getattr(exc, "__cesk_traceback__", None)
        python_stack, effect_stack = build_stacks_from_captured_traceback(captured_traceback)

        return RuntimeResultImpl(
            _result=Err(exc),  # type: ignore[arg-type]  # BaseException to Exception
            _state=final_state,
            _log=final_log,
            _env=final_env,
            _k_stack=k_stack,
            _effect_stack=effect_stack,
            _python_stack=python_stack,
            _graph=final_graph,
            _captured_traceback=captured_traceback,
        )

    def _step_until_done(self, state: CESKState) -> tuple[Any, CESKState, dict[str, Any]]:
        """Step execution until completion.
        
        Returns:
            Tuple of (value, final_state, final_store) on success
            
        Raises:
            ExecutionError: On failure, containing the exception and final state
        """
        from doeff.cesk.state import ProgramControl

        while True:
            result = step(state, self._handlers)

            if isinstance(result, Done):
                return (result.value, state, result.store)

            if isinstance(result, Failed):
                exc = result.exception
                captured_tb = result.captured_traceback
                if captured_tb is not None:
                    exc.__cesk_traceback__ = captured_tb  # type: ignore[attr-defined]
                raise ExecutionError(
                    exception=exc,
                    final_state=state,
                    captured_traceback=captured_tb,
                )

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
    "ExecutionError",
]
