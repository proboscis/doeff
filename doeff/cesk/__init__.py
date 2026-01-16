"""
CESK Machine package for the doeff effect interpreter.

This package implements a CESK machine (Control, Environment, Store, Kontinuation)
as described in Felleisen & Friedman (1986) and Van Horn & Might (2010).

For full documentation, see the original ISSUE-CORE-422.md specification.
"""

from doeff.cesk.types import Environment, Store
from doeff.cesk.frames import (
    Frame,
    GatherFrame,
    InterceptFrame,
    Kontinuation,
    ListenFrame,
    LocalFrame,
    ReturnFrame,
    SafeFrame,
)
from doeff.cesk.state import (
    CESKState,
    Control,
    EffectControl,
    Error,
    ProgramControl,
    Value,
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
from doeff.cesk.step import step
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

from doeff.cesk.types import (
    FutureId,
    IdGenerator,
    SimulatedTime,
    SpawnId,
    TaskErr,
    TaskId,
    TaskOk,
    TaskResult,
)
from doeff.cesk.unified_state import (
    Condition,
    TaskState,
    TaskStatus,
    UnifiedCESKState,
    WaitingForAll,
    WaitingForAny,
    WaitingForFuture,
    WaitingForIO,
    WaitingForTime,
)
from doeff.cesk.frames import (
    FrameResult,
    JoinFrame,
    RaceFrame,
)
from doeff.cesk.actions import (
    Action,
    AppendLog,
    AwaitExternal,
    BlockForFuture,
    BlockForTasks,
    CancelTasks,
    CreateTask,
    CreateTasks,
    Delay as DelayAction,
    ModifyStore,
    PerformIO,
    Resume,
    ResumeError,
    RunProgram,
    WaitUntil as WaitUntilAction,
)
from doeff.cesk.events import (
    AllTasksComplete,
    EffectSuspended,
    Event,
    ExternalAwait,
    IORequested,
    NeedsTimeAdvance,
    Stepped,
    TaskBlocked,
    TaskCompleted,
    TaskFailed,
    TasksCreated,
    TimeWait,
)
from doeff.cesk.unified_step import Handler, HandlerContext, unified_step
from doeff.cesk.handlers import HandlerRegistry, default_handlers
from doeff.cesk.runtime import (
    Runtime,
    SimulationRuntimeError,
    SyncRuntimeError,
    UnifiedSimulationRuntime,
    UnifiedSyncRuntime,
)

__all__ = [
    # Types (new unified API)
    "TaskId",
    "FutureId",
    "SpawnId",
    "TaskOk",
    "TaskErr",
    "TaskResult",
    "SimulatedTime",
    "IdGenerator",
    "Environment",
    "Store",
    # Control
    "Control",
    "Value",
    "Error",
    "EffectControl",
    "ProgramControl",
    # Frames
    "Frame",
    "FrameResult",
    "ReturnFrame",
    "LocalFrame",
    "InterceptFrame",
    "ListenFrame",
    "GatherFrame",
    "SafeFrame",
    "RaceFrame",
    "JoinFrame",
    "Kontinuation",
    # State (new unified API)
    "CESKState",
    "TaskState",
    "TaskStatus",
    "Condition",
    "WaitingForFuture",
    "WaitingForTime",
    "WaitingForIO",
    "WaitingForAny",
    "WaitingForAll",
    # Actions (new unified API)
    "Action",
    "Resume",
    "ResumeError",
    "CreateTask",
    "CreateTasks",
    "PerformIO",
    "AwaitExternal",
    "DelayAction",
    "WaitUntilAction",
    "CancelTasks",
    "RunProgram",
    "BlockForFuture",
    "BlockForTasks",
    "ModifyStore",
    "AppendLog",
    # Events (new unified API)
    "Event",
    "TaskCompleted",
    "TaskFailed",
    "TaskBlocked",
    "EffectSuspended",
    "IORequested",
    "ExternalAwait",
    "TimeWait",
    "TasksCreated",
    "AllTasksComplete",
    "NeedsTimeAdvance",
    "Stepped",
    # Handlers (new unified API)
    "Handler",
    "HandlerContext",
    "HandlerRegistry",
    "default_handlers",
    # Runtime (new unified API)
    "Runtime",
    "UnifiedSyncRuntime",
    "UnifiedSimulationRuntime",
    "SyncRuntimeError",
    "SimulationRuntimeError",
    # Step results (legacy)
    "StepResult",
    "Done",
    "Failed",
    "Suspended",
    "Terminal",
    # Public result type (legacy)
    "CESKResult",
    # Classification (legacy)
    "is_control_flow_effect",
    "is_pure_effect",
    "is_effectful",
    "has_intercept_frame",
    "find_intercept_frame_index",
    # Errors (legacy)
    "UnhandledEffectError",
    "InterpreterInvariantError",
    "HandlerRegistryError",
    # Dispatcher (legacy)
    "ScheduledEffectDispatcher",
    "default_scheduled_handlers",
    # Transform (legacy)
    "apply_transforms",
    "apply_intercept_chain",
    # State merging (legacy)
    "merge_store",
    "_merge_thread_state",
    # Thread pool (legacy)
    "shutdown_shared_executor",
    # Generator conversion (legacy)
    "to_generator",
    # Step function
    "step",
    # Run functions (deprecated - use doeff.runtimes instead)
    "_run_internal",
    "run",
    "run_sync",
]
