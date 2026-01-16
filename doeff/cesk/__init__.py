"""
CESK Machine package for the doeff effect interpreter.

This package implements a CESK machine (Control, Environment, Store, Kontinuation)
as described in Felleisen & Friedman (1986) and Van Horn & Might (2010).

The unified multi-task architecture (ISSUE-CORE-456) provides:
- CESKState: All tasks in one immutable object
- Handler → Action → step() → Event → Runtime pattern
- Frame protocol for extensible control flow
- Actions and Events for clean separation between computation and scheduling

For full documentation, see the original ISSUE-CORE-422.md specification.
"""

from doeff.cesk.types import Environment, FutureId, SpawnId, Store, TaskId
from doeff.cesk.frames import (
    # Frame result types
    Continue,
    FrameProtocol,
    FrameResult,
    PopAndContinue,
    Propagate,
    # Concrete frame types
    Frame,
    GatherFrame,
    InterceptFrame,
    JoinFrame,
    Kontinuation,
    ListenFrame,
    LocalFrame,
    MultiGatherFrame,
    RaceFrame,
    ReturnFrame,
    SafeFrame,
)
from doeff.cesk.state import (
    CESKState,
    Condition,
    Control,
    EffectControl,
    Error,
    FutureState,
    GatherCondition,
    ProgramControl,
    RaceCondition,
    TaskState,
    TaskStatus,
    TimeCondition,
    Value,
    WaitingOn,
)
from doeff.cesk.actions import (
    # Sync actions
    Action,
    AwaitExternal,
    CancelTasks,
    CreateTask,
    CreateTasks,
    IOAction,
    ModifyStore,
    PerformIO,
    RaceOnFutures,
    Resume,
    ResumeError,
    ResumeWithStore,
    RunProgram,
    SyncAction,
    TaskAction,
    WaitAction,
    WaitForDuration,
    WaitOnFuture,
    WaitOnFutures,
    WaitUntilTime,
)
from doeff.cesk.events import (
    # Events
    AwaitRequested,
    BlockingEvent,
    CreationEvent,
    Event,
    FutureEvent,
    FutureRejected,
    FutureResolved,
    IOEvent,
    IORequested,
    SchedulingEvent,
    TaskBlocked,
    TaskCancelled,
    TaskCreated,
    TaskDone,
    TaskFailed,
    TaskRacing,
    TaskReady,
    TasksCreated,
    TaskStateEvent,
    TaskWaitingForDuration,
    TaskWaitingOnFuture,
    TaskWaitingOnFutures,
    TaskWaitingUntilTime,
    TaskYielded,
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
from doeff.cesk.step import (
    StepOutput,
    step,
    resume_task,
    resume_task_error,
    resume_task_with_store,
)
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
    # Types (basic)
    "Environment",
    "Store",
    # Types (identifiers)
    "TaskId",
    "FutureId",
    "SpawnId",
    # Control
    "Control",
    "Value",
    "Error",
    "EffectControl",
    "ProgramControl",
    # Task status and conditions
    "TaskStatus",
    "Condition",
    "WaitingOn",
    "GatherCondition",
    "RaceCondition",
    "TimeCondition",
    # Task and future state
    "TaskState",
    "FutureState",
    # Frame results
    "FrameResult",
    "Continue",
    "PopAndContinue",
    "Propagate",
    # Frame protocol
    "FrameProtocol",
    # Frames
    "Frame",
    "ReturnFrame",
    "LocalFrame",
    "InterceptFrame",
    "ListenFrame",
    "GatherFrame",
    "SafeFrame",
    "JoinFrame",
    "MultiGatherFrame",
    "RaceFrame",
    "Kontinuation",
    # State
    "CESKState",
    # Actions (sync)
    "Action",
    "SyncAction",
    "Resume",
    "ResumeError",
    "RunProgram",
    "ModifyStore",
    "ResumeWithStore",
    # Actions (task)
    "TaskAction",
    "CreateTask",
    "CreateTasks",
    "CancelTasks",
    # Actions (wait)
    "WaitAction",
    "WaitOnFuture",
    "WaitOnFutures",
    "RaceOnFutures",
    "WaitUntilTime",
    "WaitForDuration",
    # Actions (I/O)
    "IOAction",
    "PerformIO",
    "AwaitExternal",
    # Events (task state)
    "Event",
    "TaskStateEvent",
    "TaskDone",
    "TaskFailed",
    "TaskCancelled",
    # Events (blocking)
    "BlockingEvent",
    "TaskBlocked",
    "TaskWaitingOnFuture",
    "TaskWaitingOnFutures",
    "TaskRacing",
    "TaskWaitingUntilTime",
    "TaskWaitingForDuration",
    # Events (creation)
    "CreationEvent",
    "TaskCreated",
    "TasksCreated",
    # Events (future)
    "FutureEvent",
    "FutureResolved",
    "FutureRejected",
    # Events (I/O)
    "IOEvent",
    "IORequested",
    "AwaitRequested",
    # Events (scheduling)
    "SchedulingEvent",
    "TaskReady",
    "TaskYielded",
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
    # Step function
    "StepOutput",
    "step",
    "resume_task",
    "resume_task_error",
    "resume_task_with_store",
    # Run functions (deprecated - use doeff.runtimes instead)
    "_run_internal",
    "run",
    "run_sync",
]
