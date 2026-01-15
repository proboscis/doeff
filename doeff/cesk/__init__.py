"""
CESK Machine package for the doeff effect interpreter.

This package implements a CESK machine (Control, Environment, Store, Kontinuation)
as described in Felleisen & Friedman (1986) and Van Horn & Might (2010).

For full documentation, see the original ISSUE-CORE-422.md specification.
"""

from doeff.cesk.types import Environment, Store
from doeff.cesk.frames import (
    CatchFrame,
    FinallyFrame,
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

__all__ = [
    # Types
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
    "ReturnFrame",
    "CatchFrame",
    "FinallyFrame",
    "LocalFrame",
    "InterceptFrame",
    "ListenFrame",
    "GatherFrame",
    "SafeFrame",
    "Kontinuation",
    # State
    "CESKState",
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
    "step",
    # Run functions (deprecated - use doeff.runtimes instead)
    "_run_internal",
    "run",
    "run_sync",
]
