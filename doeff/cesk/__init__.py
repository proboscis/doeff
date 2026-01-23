"""
CESK Machine package for the doeff effect interpreter.

This package implements a CESK machine (Control, Environment, Store, Kontinuation)
as described in Felleisen & Friedman (1986) and Van Horn & Might (2010).

The unified multi-task architecture supports:
- Multiple concurrent tasks with shared Store
- TaskId, FutureId, SpawnId for coordination
- TaskStatus (Ready, Blocked, Requesting, Done) for task state
- Condition types for blocking conditions
- Request types for runtime operations

For full documentation, see SPEC-CORE-001.
"""

from doeff.cesk.types import (
    Environment,
    Store,
    # New unified types
    TaskId,
    FutureId,
    SpawnId,
    TaskHandle,
    FutureHandle,
    SpawnHandle,
    empty_environment,
    empty_store,
)
from doeff.cesk.frames import (
    Frame,
    GatherFrame,
    InterceptFrame,
    Kontinuation,
    ListenFrame,
    LocalFrame,
    ReturnFrame,
    SafeFrame,
    RaceFrame,
    # Frame result types
    FrameResult,
    ContinueValue,
    ContinueError,
    ContinueProgram,
    ContinueGenerator,
)
from doeff.cesk.state import (
    CESKState,
    TaskState,
    Control,
    EffectControl,
    Error,
    ProgramControl,
    Value,
    # Task status types
    TaskStatus,
    Ready,
    Blocked,
    Requesting,
    Done as TaskDone,
    # Condition types
    Condition,
    TimeCondition,
    FutureCondition,
    TaskCondition,
    SpawnCondition,
    # Request types
    Request,
    CreateTask,
    CreateFuture,
    ResolveFuture,
    PerformIO,
    AwaitExternal,
    CreateSpawn,
)
from doeff.cesk.kontinuation import (
    push_frame,
    pop_frame,
    unwind_value,
    unwind_error,
    find_frame,
    has_frame,
    find_safe_frame_index,
    has_safe_frame,
    get_intercept_transforms,
    continuation_depth,
    split_at_safe,
)
from doeff.cesk.result import (
    CESKResult,
    Done,
    Failed,
    StepResult,
    Suspended,
    Terminal,
)
from doeff.cesk.classification import (
    find_intercept_frame_index,
    has_intercept_frame,
    is_control_flow_effect,
    is_effectful,
    is_pure_effect,
)
from doeff.cesk.helpers import (
    _merge_thread_state,
    apply_intercept_chain,
    apply_transforms,
    merge_store,
    shutdown_shared_executor,
    to_generator,
)
from doeff.cesk.step import step, step_task, step_cesk_task
from doeff.cesk.errors import (
    HandlerRegistryError,
    InterpreterInvariantError,
    UnhandledEffectError,
)
from doeff.cesk.handlers import Handler, default_handlers
from doeff.cesk.runtime import BaseRuntime, SyncRuntime, SimulationRuntime, AsyncRuntime
from doeff.cesk.runtime_result import (
    RuntimeResult,
    RuntimeResultImpl,
    KStackTrace,
    KFrame,
    EffectStackTrace,
    EffectCallNode,
    PythonStackTrace,
    PythonFrame,
    SourceLocation,
)

from typing import TYPE_CHECKING, Any, Callable, TypeVar

if TYPE_CHECKING:
    from doeff.program import Program
    from doeff.storage import DurableStorage
    from doeff.cesk_observability import ExecutionSnapshot, OnStepCallback

_T = TypeVar("_T")


def run_sync(
    program: "Program[_T]",
    env: dict[str, Any] | None = None,
    store: dict[str, Any] | None = None,
    storage: "DurableStorage | None" = None,
    on_step: "OnStepCallback | None" = None,
) -> CESKResult[_T]:
    """Execute a program synchronously with optional observability.

    This function provides a synchronous execution model with support for
    step-by-step observation via the on_step callback. It wraps SyncRuntime
    with additional observability hooks.

    Args:
        program: The program to execute.
        env: Optional initial environment (reader context).
        store: Optional initial store (mutable state).
        storage: Optional durable storage backend for observability.
        on_step: Optional callback invoked at each interpreter step.
            Receives an ExecutionSnapshot with current execution state.

    Returns:
        CESKResult containing the execution result.

    Example:
        from doeff import do
        from doeff.cesk import run_sync
        from doeff.effects import Pure

        @do
        def my_workflow():
            x = yield Pure(10)
            return x * 2

        # Simple execution
        result = run_sync(my_workflow())
        print(result.value)  # 20

        # With observability
        def log_step(snapshot):
            print(f"Step {snapshot.step_count}: {snapshot.status}")

        result = run_sync(my_workflow(), on_step=log_step)
    """
    from doeff._vendor import FrozenDict, Ok, Err
    from doeff.cesk.runtime import SyncRuntime
    from doeff.cesk.runtime.base import ExecutionError
    from doeff.cesk.state import CESKState, ProgramControl
    from doeff.cesk.result import Done, Failed, Suspended
    from doeff.cesk.frames import ContinueValue, ContinueError, ContinueProgram

    runtime = SyncRuntime()

    # Create initial state
    frozen_env = FrozenDict(env) if env else FrozenDict()
    final_store: dict[str, Any] = store if store is not None else {}
    state = CESKState.initial(program, frozen_env, final_store)

    step_count = 0

    # Helper to create and emit snapshot
    def emit_snapshot(status: str) -> None:
        if on_step is None:
            return
        from doeff.cesk_observability import ExecutionSnapshot

        snapshot = ExecutionSnapshot.from_state(
            state, status, step_count, storage  # type: ignore[arg-type]
        )
        on_step(snapshot)

    try:
        while True:
            step_count += 1
            emit_snapshot("running")

            result = step(state, runtime._handlers)

            if isinstance(result, Done):
                state = CESKState(C=Value(result.value), E=state.E, S=result.store, K=[])
                emit_snapshot("completed")
                return CESKResult(Ok(result.value))

            if isinstance(result, Failed):
                exc = result.exception if isinstance(result.exception, Exception) else Exception(str(result.exception))
                state = CESKState(C=Error(result.exception), E=state.E, S=result.store, K=[])
                emit_snapshot("failed")
                return CESKResult(Err(exc), result.captured_traceback)

            if isinstance(result, CESKState):
                state = result
                continue

            if isinstance(result, Suspended):
                main_task = state.tasks[state.main_task]
                dispatch_result = runtime._dispatch_effect(
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

    except ExecutionError as err:
        if isinstance(err.exception, (KeyboardInterrupt, SystemExit)):
            raise err.exception from None
        emit_snapshot("failed")
        exc = err.exception if isinstance(err.exception, Exception) else Exception(str(err.exception))
        return CESKResult(Err(exc), err.captured_traceback)


__all__ = [
    # Types
    "Environment",
    "Store",
    # New unified types
    "TaskId",
    "FutureId",
    "SpawnId",
    "TaskHandle",
    "FutureHandle",
    "SpawnHandle",
    "empty_environment",
    "empty_store",
    # Control
    "Control",
    "Value",
    "Error",
    "EffectControl",
    "ProgramControl",
    # Task status types
    "TaskStatus",
    "Ready",
    "Blocked",
    "Requesting",
    "TaskDone",
    # Condition types
    "Condition",
    "TimeCondition",
    "FutureCondition",
    "TaskCondition",
    "SpawnCondition",
    # Request types
    "Request",
    "CreateTask",
    "CreateFuture",
    "ResolveFuture",
    "PerformIO",
    "AwaitExternal",
    "CreateSpawn",
    # Frames
    "Frame",
    "ReturnFrame",
    "LocalFrame",
    "InterceptFrame",
    "ListenFrame",
    "GatherFrame",
    "SafeFrame",
    "RaceFrame",
    "Kontinuation",
    # Frame results
    "FrameResult",
    "ContinueValue",
    "ContinueError",
    "ContinueProgram",
    "ContinueGenerator",
    # Kontinuation helpers
    "push_frame",
    "pop_frame",
    "unwind_value",
    "unwind_error",
    "find_frame",
    "has_frame",
    "find_safe_frame_index",
    "has_safe_frame",
    "get_intercept_transforms",
    "continuation_depth",
    "split_at_safe",
    # State
    "CESKState",
    "TaskState",
    # Step results
    "StepResult",
    "Done",
    "Failed",
    "Suspended",
    "Terminal",
    # Public result type
    "CESKResult",
    # Classification
    "is_control_flow_effect",
    "is_pure_effect",
    "is_effectful",
    "has_intercept_frame",
    "find_intercept_frame_index",
    # Errors
    "UnhandledEffectError",
    "InterpreterInvariantError",
    "HandlerRegistryError",
    # Transform
    "apply_transforms",
    "apply_intercept_chain",
    # State merging
    "merge_store",
    "_merge_thread_state",
    # Thread pool
    "shutdown_shared_executor",
    # Generator conversion
    "to_generator",
    # Step functions
    "step",
    "step_task",
    "step_cesk_task",
    # Handlers
    "Handler",
    "default_handlers",
    # New runtimes
    "BaseRuntime",
    "SyncRuntime",
    "SimulationRuntime",
    "AsyncRuntime",
    # RuntimeResult (SPEC-CESK-002)
    "RuntimeResult",
    "RuntimeResultImpl",
    "KStackTrace",
    "KFrame",
    "EffectStackTrace",
    "EffectCallNode",
    "PythonStackTrace",
    "PythonFrame",
    "SourceLocation",
    "run_sync",
]
