"""Executable ADR contracts for doeff projects.

実行の API(``registry`` — YAML・subprocess・tempfile を読む)は、最初に名を引いた時に読む(PEP 562 の module の
``__getattr__``)。pytest の plugin の entry point ``doeff_adr.pytest_plugin`` はこの package を先に読むので、plugin の import
だけでは registry と YAML を読まない(agora-redesign #1551)。``doeff_hy`` の import(Hy の import の仕掛け・AST の補正)は今までどおり
package の import で行う。公開する名は下の閉じた一覧だけ — 一覧の外の名は今までどおり AttributeError / ImportError。
"""

from typing import TYPE_CHECKING

import doeff_hy as doeff_hy

if TYPE_CHECKING:
    from doeff_adr.registry import AdrSpec as AdrSpec
    from doeff_adr.registry import EnforcementRef as EnforcementRef
    from doeff_adr.registry import SemgrepSpec as SemgrepSpec
    from doeff_adr.registry import adr_ids as adr_ids
    from doeff_adr.registry import assert_adr_contract as assert_adr_contract
    from doeff_adr.registry import assert_all_adr_contracts as assert_all_adr_contracts
    from doeff_adr.registry import assert_semgrep_enforcement as assert_semgrep_enforcement
    from doeff_adr.registry import clear_registry as clear_registry
    from doeff_adr.registry import enforcement_ids as enforcement_ids
    from doeff_adr.registry import get_adr as get_adr
    from doeff_adr.registry import get_enforcement as get_enforcement
    from doeff_adr.registry import register_adr as register_adr
    from doeff_adr.registry import register_deftest_enforcement as register_deftest_enforcement
    from doeff_adr.registry import register_semgrep_enforcement as register_semgrep_enforcement

# registry から引き直す公開の名(閉じた一覧 — 上の TYPE_CHECKING の import と 1 対 1)。
REGISTRY_EXPORTS: frozenset[str] = frozenset(
    {
        "AdrSpec",
        "EnforcementRef",
        "SemgrepSpec",
        "adr_ids",
        "assert_adr_contract",
        "assert_all_adr_contracts",
        "assert_semgrep_enforcement",
        "clear_registry",
        "enforcement_ids",
        "get_adr",
        "get_enforcement",
        "register_adr",
        "register_deftest_enforcement",
        "register_semgrep_enforcement",
    }
)


def __getattr__(name: str) -> object:
    """公開の名を最初に引いた時に registry を読む。一覧の外の名は AttributeError(``from doeff_adr import x`` は ImportError)。"""
    if name in REGISTRY_EXPORTS:
        from doeff_adr import registry

        return getattr(registry, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted({*globals(), *REGISTRY_EXPORTS})
