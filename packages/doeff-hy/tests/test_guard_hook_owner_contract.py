"""guard が SourceFileLoader に被せた口の持ち主の module は、bytecode を前もって作る道具が呼ぶ 2 つの口を同じ名と引数の形で出す。

worker の焼く道具(doeff-cluster の _foundation/bytecode_pool.hy)は、準備する root の venv の doeff を module の名で import しない —
root の guard を、口(get_code・source_to_code)の持ち主の module として sys.modules から引き、その bytecode_is_current と
source_to_code_as_import を呼ぶ(ADR-DOE-BUILD-002 R7)。持ち主の module の名は版で変わってよい(loader_hooks → _loader_hooks)が、
この 2 つの名と引数の形を変えると、新しい doeff の root で worker の準備が落ちる(2026-11-01 — 名の置き場を移した doeff の root で、
古い worker の準備が import の誤りで落ちた)。
"""

from __future__ import annotations

import importlib.machinery
import inspect
import sys

from doeff_hy_bytecode_guard._loader_hooks import install

#: 道具が呼ぶ口と、その引数の名の並び。
CONTRACT = {
    "bytecode_is_current": ["path", "pyc", "source"],
    "source_to_code_as_import": ["loader", "data", "path"],
}


def test_the_module_owning_the_loader_hooks_offers_the_bake_tools_entries() -> None:
    install()
    loader = importlib.machinery.SourceFileLoader
    owners = {getattr(loader, hook).__module__ for hook in ("get_code", "source_to_code")}
    assert len(owners) == 1, owners
    owner = sys.modules[owners.pop()]
    for name, parameters in CONTRACT.items():
        entry = getattr(owner, name, None)
        assert callable(entry), (owner.__name__, name)
        assert list(inspect.signature(entry).parameters) == parameters, (name, inspect.signature(entry))
