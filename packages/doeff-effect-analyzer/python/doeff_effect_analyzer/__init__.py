"""doeff-effect-analyzer: the effects a Program performs, what a handler answers and env coverage,
read from the sources without running anything (Hy is macro-expanded first).

A Program asks with the effects ``AnalyzeProgram`` / ``AnalyzeHandler`` / ``AnalyzeEnv`` /
``CheckCoverage``, and the handler ``effect_analysis()`` answers them; the answers are
``ProgramEffects`` / ``HandlerEffects`` / ``Coverage`` (ADR-DOE-API-001 R24).  The CLI command
``doeff-effects`` runs the same effects.  The Rust core (``seda``) is the extension
``doeff_effect_analyzer._native``.
"""

from importlib import import_module
from typing import TYPE_CHECKING

# Names of the submodules behind `_` (ADR-DOE-API-001 R5), read on first use: the root does not import
# them while it loads. `X as X` shows each name to type checkers; `__all__` is not used (DOEFF021).
if TYPE_CHECKING:
    from doeff_effect_analyzer._effects import AnalyzeEnv as AnalyzeEnv
    from doeff_effect_analyzer._effects import AnalyzeHandler as AnalyzeHandler
    from doeff_effect_analyzer._effects import AnalyzeProgram as AnalyzeProgram
    from doeff_effect_analyzer._effects import CheckCoverage as CheckCoverage
    from doeff_effect_analyzer._effects import FoldCarried as FoldCarried
    from doeff_effect_analyzer._effects import effect_analysis as effect_analysis
    from doeff_effect_analyzer._handler_effects import Coverage as Coverage
    from doeff_effect_analyzer._handler_effects import Gap as Gap
    from doeff_effect_analyzer._program_effects import Basis as Basis
    from doeff_effect_analyzer._program_effects import HandlerEffects as HandlerEffects
    from doeff_effect_analyzer._program_effects import ProgramEffects as ProgramEffects
    from doeff_effect_analyzer._program_effects import SuppliedDeclaration as SuppliedDeclaration

_LAZY_EXPORTS: dict[str, str] = {
    "AnalyzeEnv": "doeff_effect_analyzer._effects",
    "AnalyzeHandler": "doeff_effect_analyzer._effects",
    "AnalyzeProgram": "doeff_effect_analyzer._effects",
    "Basis": "doeff_effect_analyzer._program_effects",
    "CheckCoverage": "doeff_effect_analyzer._effects",
    "Coverage": "doeff_effect_analyzer._handler_effects",
    "FoldCarried": "doeff_effect_analyzer._effects",
    "Gap": "doeff_effect_analyzer._handler_effects",
    "HandlerEffects": "doeff_effect_analyzer._program_effects",
    "ProgramEffects": "doeff_effect_analyzer._program_effects",
    "SuppliedDeclaration": "doeff_effect_analyzer._program_effects",
    "effect_analysis": "doeff_effect_analyzer._effects",
}


def __getattr__(name: str) -> object:
    """A public name, imported from the inside module that defines it on first use."""
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """The module's names with the public names not imported yet."""
    return sorted({*globals(), *_LAZY_EXPORTS})
