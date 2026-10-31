"""Executable ADR contracts for doeff projects.

実行 API は最初の利用時に読む。pytest plugin の import だけでは registry と YAML を読まない。
公開 API の型は __init__.pyi で宣言し、Hy の import hook と AST の補正は従来どおり先に入れる。
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
    from doeff_adr._pytest_plugin import DEFAULT_FILE_PATTERNS as DEFAULT_FILE_PATTERNS
    from doeff_adr._pytest_plugin import DoeffAdrHyFile as DoeffAdrHyFile
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


#: 公開の名から、名を定義する module へ — 最初の利用の時にその module だけを読む(effect と handler は内側の module
#: _effects・_semgrep_effects)。
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
    "DEFAULT_FILE_PATTERNS": "doeff_adr._pytest_plugin",
    "DoeffAdrHyFile": "doeff_adr._pytest_plugin",
    "HyScanExpansionError": "doeff_adr._semgrep_hy",
}


def __getattr__(name: str) -> object:
    """宣言した公開名だけを遅延解決する。import 失敗は呼び手へそのまま伝える。"""
    # import * が名前を要求した時だけ遅延公開 API を列挙する。
    if name == "__all__":
        return ("doeff_hy", *_LAZY_EXPORTS)
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    # 公開の名の値は定義の module の値そのもの。1 度引いた名は package の普通の属性になる。
    value = vars(import_module(module_name))[name]
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_LAZY_EXPORTS})
