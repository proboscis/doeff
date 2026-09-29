"""Executable ADR contracts for doeff projects.

公開 API(下の ``_REGISTRY_EXPORTS``)の実体は ``doeff_adr.registry`` にあり、最初に使われた時に読む
(agora-redesign #1551)。pytest は plugin の ``doeff_adr.pytest_plugin`` を読むたびにこの package を先に読むが、
収集は registry(と、それが読む YAML)を使わないので、収集のたびに読むと時間だけかかっていた。

型の検査器には ``TYPE_CHECKING`` の下の import がそのまま見える(名も型も registry の物と同じ)。実行の時は
``__getattr__`` が同じ名だけを registry から引く — 無い名は今までどおり ``AttributeError`` / ``ImportError`` になり、
registry の import が壊れていれば、その例外が最初の利用の場所でそのまま上がる。
"""

import importlib
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

# registry から引く公開の名(上の TYPE_CHECKING の import と同じ名 — 片方だけ直さない。tests/test_lazy_public_api.py が
# 2 つの一致と、実行の時の値が registry の物と同じことを確かめる)。
_REGISTRY_EXPORTS = (
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
)


def __getattr__(name: str) -> object:
    """公開の名を初めて引かれた時に registry を読み、その値を package に置く(2 回目からは普通の属性)。"""
    if name not in _REGISTRY_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value: object = vars(importlib.import_module(f"{__name__}.registry"))[name]
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """``dir(doeff_adr)`` に、まだ読んでいない公開の名も並べる。"""
    return sorted({*globals(), *_REGISTRY_EXPORTS})
