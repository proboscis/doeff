"""Executable ADR contracts for doeff projects.

実行 API は最初の利用時に読む。pytest plugin の import だけでは registry と YAML を読まない。
公開 API の型は __init__.pyi で宣言し、Hy の import hook と AST の補正は従来どおり先に入れる。
"""

from typing import TYPE_CHECKING

import doeff_hy as doeff_hy

if TYPE_CHECKING:
    from doeff_adr.registry import (
        AdrSpec as AdrSpec,
    )
    from doeff_adr.registry import (
        EnforcementRef as EnforcementRef,
    )
    from doeff_adr.registry import (
        SemgrepSpec as SemgrepSpec,
    )
    from doeff_adr.registry import (
        adr_ids as adr_ids,
    )
    from doeff_adr.registry import (
        assert_adr_contract as assert_adr_contract,
    )
    from doeff_adr.registry import (
        assert_all_adr_contracts as assert_all_adr_contracts,
    )
    from doeff_adr.registry import (
        assert_semgrep_enforcement as assert_semgrep_enforcement,
    )
    from doeff_adr.registry import (
        clear_registry as clear_registry,
    )
    from doeff_adr.registry import (
        enforcement_ids as enforcement_ids,
    )
    from doeff_adr.registry import (
        get_adr as get_adr,
    )
    from doeff_adr.registry import (
        get_enforcement as get_enforcement,
    )
    from doeff_adr.registry import (
        register_adr as register_adr,
    )
    from doeff_adr.registry import (
        register_deftest_enforcement as register_deftest_enforcement,
    )
    from doeff_adr.registry import (
        register_semgrep_enforcement as register_semgrep_enforcement,
    )


_PUBLIC_API_NAMES = (
    "AdrSpec",
    "EnforcementRef",
    "SemgrepSpec",
    "adr_ids",
    "assert_adr_contract",
    "assert_all_adr_contracts",
    "assert_semgrep_enforcement",
    "clear_registry",
    "doeff_hy",
    "enforcement_ids",
    "get_adr",
    "get_enforcement",
    "register_adr",
    "register_deftest_enforcement",
    "register_semgrep_enforcement",
)


def __getattr__(name: str) -> object:
    """宣言した公開名だけを遅延解決する。import 失敗は呼び手へそのまま伝える。"""
    # import * が名前を要求した時だけ遅延公開 API を列挙する。
    if name == "__all__":
        return _PUBLIC_API_NAMES
    if name not in _PUBLIC_API_NAMES:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from doeff_adr import registry

    exports: dict[str, object] = {
        key: vars(registry)[key] for key in _PUBLIC_API_NAMES if key != "doeff_hy"
    }
    globals().update(exports)
    return exports[name]


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_PUBLIC_API_NAMES))
