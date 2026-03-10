"""
Core types for the doeff effects system.

This module is only supported as ``doeff.types``. Importing this file as the
top-level module ``types`` indicates an invalid import topology and must fail
fast instead of rewriting interpreter-global import state.
"""

from typing import TYPE_CHECKING

if __name__ == "types":
    raise ImportError(
        "doeff/types.py was imported as top-level 'types'. "
        "Remove the package directory from sys.path and import 'doeff.types' instead."
    )

if TYPE_CHECKING:
    from doeff._types_internal import (  # noqa: F401
        DEFAULT_REPR_LIMIT,
        NOTHING,
        REPR_LIMIT_KEY,
        CallFrame,
        CapturedTraceback,
        Effect,
        EffectBase,
        EffectFailure,
        EffectFailureError,
        EffectGenerator,
        EffectObservation,
        EnvKey,
        Err,
        FrozenDict,
        ListenResult,
        Maybe,
        Nothing,
        Ok,
        Program,
        ProgramBase,
        Result,
        RunResult,
        Some,
        TraceError,
        WGraph,
        WNode,
        WStep,
        _intercept_value,
        capture_traceback,
        get_captured_traceback,
        trace_err,
    )

import importlib as _importlib

_internal = _importlib.import_module("doeff._types_internal")
globals().update(_internal.__dict__)
