"""doeff-effect-analyzer.

- ``analyze_program`` and the names beside it (read from this root) / :mod:`.handler_effects` —
  the effects a Program performs, what each handler handles, and env coverage; Hy is
  macro-expanded first.  Pure Python; no extension needed.
- ``analyze`` / ``analyze_symbol`` — the Rust core (``_native`` extension, the
  ``seda`` binary's engine), loaded on first use.
"""

from importlib import import_module
from typing import TYPE_CHECKING

# Names read on first use: the root does not import the Rust core (or a submodule behind `_`)
# while it loads.
if TYPE_CHECKING:
    from doeff_effect_analyzer._program_effects import UNBOUND as UNBOUND
    from doeff_effect_analyzer._program_effects import AllTest as AllTest
    from doeff_effect_analyzer._program_effects import AnyTest as AnyTest
    from doeff_effect_analyzer._program_effects import Basis as Basis
    from doeff_effect_analyzer._program_effects import Binding as Binding
    from doeff_effect_analyzer._program_effects import CarriedProgram as CarriedProgram
    from doeff_effect_analyzer._program_effects import ClassTest as ClassTest
    from doeff_effect_analyzer._program_effects import Clause as Clause
    from doeff_effect_analyzer._program_effects import Comprehension as Comprehension
    from doeff_effect_analyzer._program_effects import EffectUse as EffectUse
    from doeff_effect_analyzer._program_effects import Escape as Escape
    from doeff_effect_analyzer._program_effects import ExpansionObserver as ExpansionObserver
    from doeff_effect_analyzer._program_effects import FunctionNode as FunctionNode
    from doeff_effect_analyzer._program_effects import HandledProgram as HandledProgram
    from doeff_effect_analyzer._program_effects import HandlerEffects as HandlerEffects
    from doeff_effect_analyzer._program_effects import Imported as Imported
    from doeff_effect_analyzer._program_effects import Location as Location
    from doeff_effect_analyzer._program_effects import NotTest as NotTest
    from doeff_effect_analyzer._program_effects import PassTest as PassTest
    from doeff_effect_analyzer._program_effects import ProgramEffects as ProgramEffects
    from doeff_effect_analyzer._program_effects import ReceivedEffect as ReceivedEffect
    from doeff_effect_analyzer._program_effects import ReceivedField as ReceivedField
    from doeff_effect_analyzer._program_effects import Residual as Residual
    from doeff_effect_analyzer._program_effects import SourcesRead as SourcesRead
    from doeff_effect_analyzer._program_effects import UnknownTest as UnknownTest
    from doeff_effect_analyzer._program_effects import Unresolved as Unresolved
    from doeff_effect_analyzer._program_effects import analyze_program as analyze_program
    from doeff_effect_analyzer._program_effects import bindings_for as bindings_for
    from doeff_effect_analyzer._program_effects import module_ast as module_ast
    from doeff_effect_analyzer._program_effects import observe_expansions as observe_expansions
    from doeff_effect_analyzer._program_effects import pass_through as pass_through
    from doeff_effect_analyzer._program_effects import qualified_name as qualified_name
    from doeff_effect_analyzer._program_effects import reading_sources as reading_sources
    from doeff_effect_analyzer._program_effects import resolve_target as resolve_target
    from doeff_effect_analyzer._program_effects import runs_where_performed as runs_where_performed

_LAZY_EXPORTS: dict[str, str] = {
    "analyze": "doeff_effect_analyzer._native",
    "analyze_symbol": "doeff_effect_analyzer._native",
    "AllTest": "doeff_effect_analyzer._program_effects",
    "AnyTest": "doeff_effect_analyzer._program_effects",
    "Basis": "doeff_effect_analyzer._program_effects",
    "Binding": "doeff_effect_analyzer._program_effects",
    "CarriedProgram": "doeff_effect_analyzer._program_effects",
    "ClassTest": "doeff_effect_analyzer._program_effects",
    "Clause": "doeff_effect_analyzer._program_effects",
    "Comprehension": "doeff_effect_analyzer._program_effects",
    "EffectUse": "doeff_effect_analyzer._program_effects",
    "Escape": "doeff_effect_analyzer._program_effects",
    "ExpansionObserver": "doeff_effect_analyzer._program_effects",
    "FunctionNode": "doeff_effect_analyzer._program_effects",
    "HandledProgram": "doeff_effect_analyzer._program_effects",
    "HandlerEffects": "doeff_effect_analyzer._program_effects",
    "Imported": "doeff_effect_analyzer._program_effects",
    "Location": "doeff_effect_analyzer._program_effects",
    "NotTest": "doeff_effect_analyzer._program_effects",
    "PassTest": "doeff_effect_analyzer._program_effects",
    "ProgramEffects": "doeff_effect_analyzer._program_effects",
    "ReceivedEffect": "doeff_effect_analyzer._program_effects",
    "ReceivedField": "doeff_effect_analyzer._program_effects",
    "Residual": "doeff_effect_analyzer._program_effects",
    "SourcesRead": "doeff_effect_analyzer._program_effects",
    "UNBOUND": "doeff_effect_analyzer._program_effects",
    "UnknownTest": "doeff_effect_analyzer._program_effects",
    "Unresolved": "doeff_effect_analyzer._program_effects",
    "analyze_program": "doeff_effect_analyzer._program_effects",
    "bindings_for": "doeff_effect_analyzer._program_effects",
    "module_ast": "doeff_effect_analyzer._program_effects",
    "observe_expansions": "doeff_effect_analyzer._program_effects",
    "pass_through": "doeff_effect_analyzer._program_effects",
    "qualified_name": "doeff_effect_analyzer._program_effects",
    "reading_sources": "doeff_effect_analyzer._program_effects",
    "resolve_target": "doeff_effect_analyzer._program_effects",
    "runs_where_performed": "doeff_effect_analyzer._program_effects",
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
