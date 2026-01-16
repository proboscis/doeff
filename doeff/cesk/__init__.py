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
from doeff.cesk.dispatcher import (
    HandlerRegistryError,
    InterpreterInvariantError,
    ScheduledEffectDispatcher,
    UnhandledEffectError,
)
from doeff.cesk.run import (
    _run_internal,
    run,
    run_sync,
)
from doeff.scheduled_handlers import default_scheduled_handlers

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
    # Dispatcher
    "ScheduledEffectDispatcher",
    "default_scheduled_handlers",
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
    # Run functions (deprecated - use doeff.runtimes instead)
    "_run_internal",
    "run",
    "run_sync",
]
