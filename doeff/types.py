"""
Core types for the doeff effects system (with stdlib safety guard).

This wrapper avoids shadowing the stdlib ``types`` module when tools run
``runpy._run_module_as_main("doeff")`` and end up importing this file as
``types`` instead of ``doeff.types``. If imported as top-level ``types``, we
delegate to the real stdlib module; otherwise we re-export the full doeff
types from ``_types_internal``.
"""

from __future__ import annotations

import os
import sys

if __name__ != "types":
    from typing import TYPE_CHECKING

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
            ExecutionContext,
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

if os.environ.get("DOEFF_DEBUG_TYPES"):
    print(
        f"[doeff.types] __name__={__name__} sys.path0={sys.path[0]}",
        file=sys.stderr,
    )


def _load_stdlib_types() -> None:
    """Load the stdlib ``types`` module into this namespace."""

    stdlib_types_path = os.path.join(os.path.dirname(os.__file__), "types.py")

    try:
        with open(stdlib_types_path, encoding="utf-8") as handle:
            source = handle.read()
    except OSError:  # pragma: no cover - defensive fallback
        mapping_proxy_type = type(type.__dict__)

        class _DynamicClassAttribute(property):
            pass

        globals().update(
            {
                "MappingProxyType": mapping_proxy_type,
                "DynamicClassAttribute": _DynamicClassAttribute,
                "__all__": ["MappingProxyType", "DynamicClassAttribute"],
            }
        )
        return

    module = type(sys)("types")
    module.__file__ = stdlib_types_path
    exec(compile(source, stdlib_types_path, "exec"), module.__dict__)
    sys.modules["types"] = module
    globals().update(module.__dict__)


if __name__ == "types":
    _load_stdlib_types()
else:
    import importlib as _importlib

    _internal = _importlib.import_module("doeff._types_internal")
    globals().update(_internal.__dict__)
    if os.environ.get("DOEFF_DEBUG_TYPES"):
        print(
            f"[doeff.types] loaded internal EffectBase={'EffectBase' in globals()}",
            file=sys.stderr,
        )
