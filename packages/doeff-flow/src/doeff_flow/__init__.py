"""
doeff-flow: Live Effect Trace Observability

This package provides tools for observing doeff workflow execution in real-time.

Features:
    - Write live effect trace to JSONL files during workflow execution
    - CLI commands to watch traces in real-time
    - Support for multiple concurrent workflows
    - XDG-compliant default trace directory (~/.local/state/doeff-flow)

Quick Start:
    # The program yields the trace effects; production_handlers writes each one to
    # {trace_dir}/{workflow_id}/trace.jsonl (XDG default directory when trace_dir is None).
    from doeff import do, run
    from doeff_flow import TracePush, TraceSnapshot, production_handlers

    @do
    def my_workflow():
        yield TracePush(name="extract")
        yield TraceSnapshot(label="extracted")
        return "done"

    result = run(production_handlers("wf-001")(my_workflow()))

CLI Usage:
    # Watch all workflows
    $ doeff-flow watch

    # Watch single workflow
    $ doeff-flow watch wf-001

    # List active workflows
    $ doeff-flow ps

    # Show history
    $ doeff-flow history wf-001
"""

from importlib import import_module
from typing import TYPE_CHECKING

from doeff_flow._trace import LiveTrace, TraceFrame

__all__ = [
    "LiveTrace",
    "MockTraceRecorder",
    "ProductionTraceRecorder",
    "ReadTraces",
    "TraceAnnotate",
    "TraceCapture",
    "TraceEffectBase",
    "TraceFrame",
    "TracePush",
    "TraceSnapshot",
    "mock_handlers",
    "production_handlers",
    "trace_files_handler",
]


# Names of the submodules behind `_` (ADR-DOE-API-001 R5), read on first use: the root does not import
# them while it loads. `X as X` shows each name to type checkers. A root that already has `__all__`
# lists the names there too; the tool does not add `__all__` to a root without one (DOEFF021).
if TYPE_CHECKING:
    from doeff_flow._effects.trace import ReadTraces as ReadTraces
    from doeff_flow._effects.trace import TraceAnnotate as TraceAnnotate
    from doeff_flow._effects.trace import TraceCapture as TraceCapture
    from doeff_flow._effects.trace import TraceEffectBase as TraceEffectBase
    from doeff_flow._effects.trace import TracePush as TracePush
    from doeff_flow._effects.trace import TraceSnapshot as TraceSnapshot
    from doeff_flow._handlers.files import trace_files_handler as trace_files_handler
    from doeff_flow._handlers.production import ProductionTraceRecorder as ProductionTraceRecorder
    from doeff_flow._handlers.production import production_handlers as production_handlers
    from doeff_flow._handlers.testing import MockTraceRecorder as MockTraceRecorder
    from doeff_flow._handlers.testing import mock_handlers as mock_handlers

_LAZY_EXPORTS: dict[str, str] = {
    "ReadTraces": "doeff_flow._effects.trace",
    "TraceAnnotate": "doeff_flow._effects.trace",
    "TraceCapture": "doeff_flow._effects.trace",
    "TraceEffectBase": "doeff_flow._effects.trace",
    "TracePush": "doeff_flow._effects.trace",
    "TraceSnapshot": "doeff_flow._effects.trace",
    "MockTraceRecorder": "doeff_flow._handlers.testing",
    "ProductionTraceRecorder": "doeff_flow._handlers.production",
    "mock_handlers": "doeff_flow._handlers.testing",
    "production_handlers": "doeff_flow._handlers.production",
    "trace_files_handler": "doeff_flow._handlers.files",
}


def __getattr__(name: str) -> object:
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_LAZY_EXPORTS})
