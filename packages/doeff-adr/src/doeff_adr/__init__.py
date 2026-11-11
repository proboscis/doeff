"""Executable ADR contracts for doeff projects.

The execution API is loaded on first use. Merely importing the pytest plugin does not load the registry or YAML.
The types of the public API are declared in __init__.pyi; the Hy import hook and the AST fixups are installed first, as before.
"""

from importlib import import_module
from typing import TYPE_CHECKING

import doeff_hy as doeff_hy

if TYPE_CHECKING:
    from doeff_adr._effects import (
        AdrIds as AdrIds,
    )
    from doeff_adr._effects import (
        AssertAdrContract as AssertAdrContract,
    )
    from doeff_adr._effects import (
        AssertAllAdrContracts as AssertAllAdrContracts,
    )
    from doeff_adr._effects import (
        ClearRegistry as ClearRegistry,
    )
    from doeff_adr._effects import (
        EnforcementIds as EnforcementIds,
    )
    from doeff_adr._effects import (
        GetAdr as GetAdr,
    )
    from doeff_adr._effects import (
        GetEnforcement as GetEnforcement,
    )
    from doeff_adr._effects import (
        RegisterAdr as RegisterAdr,
    )
    from doeff_adr._effects import (
        RegisterDeftest as RegisterDeftest,
    )
    from doeff_adr._effects import (
        adr_registry as adr_registry,
    )
    from doeff_adr._registry import (
        AdrSpec as AdrSpec,
    )
    from doeff_adr._registry import AdrStatus as AdrStatus
    from doeff_adr._registry import EnforcementMode as EnforcementMode
    from doeff_adr._registry import (
        EnforcementRef as EnforcementRef,
    )
    from doeff_adr._registry import FixtureSpec as FixtureSpec
    from doeff_adr._registry import (
        SemgrepSpec as SemgrepSpec,
    )
    from doeff_adr._semgrep_effects import (
        AssertSemgrep as AssertSemgrep,
    )
    from doeff_adr._semgrep_effects import (
        RegisterSemgrep as RegisterSemgrep,
    )
    from doeff_adr._semgrep_effects import (
        semgrep_enforcements as semgrep_enforcements,
    )
    from doeff_adr._semgrep_hy import HyScanExpansionError as HyScanExpansionError


#: From a public name to the module that defines it — only that module is loaded on first use (effects and handlers are in the inner modules
#: _effects and _semgrep_effects).
_LAZY_EXPORTS: dict[str, str] = {
    "AdrIds": "doeff_adr._effects",
    "AdrSpec": "doeff_adr._registry",
    "AssertAdrContract": "doeff_adr._effects",
    "AssertAllAdrContracts": "doeff_adr._effects",
    "AssertSemgrep": "doeff_adr._semgrep_effects",
    "ClearRegistry": "doeff_adr._effects",
    "EnforcementIds": "doeff_adr._effects",
    "EnforcementRef": "doeff_adr._registry",
    "GetAdr": "doeff_adr._effects",
    "GetEnforcement": "doeff_adr._effects",
    "RegisterAdr": "doeff_adr._effects",
    "RegisterDeftest": "doeff_adr._effects",
    "RegisterSemgrep": "doeff_adr._semgrep_effects",
    "SemgrepSpec": "doeff_adr._registry",
    "adr_registry": "doeff_adr._effects",
    "semgrep_enforcements": "doeff_adr._semgrep_effects",
    "AdrStatus": "doeff_adr._registry",
    "EnforcementMode": "doeff_adr._registry",
    "FixtureSpec": "doeff_adr._registry",
    "HyScanExpansionError": "doeff_adr._semgrep_hy",
}


def __getattr__(name: str) -> object:
    """Lazily resolve only the declared public names. An import failure is propagated to the caller as is."""
    # Enumerate the lazy public API only when import * asks for names.
    if name == "__all__":
        return ("doeff_hy", *_LAZY_EXPORTS)
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    # The value of a public name is the value of the defining module itself. Once looked up, a name becomes an ordinary attribute of the package.
    value = vars(import_module(module_name))[name]
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_LAZY_EXPORTS})
