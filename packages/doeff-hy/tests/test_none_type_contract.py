"""契約 `(: % None)` と型付き束縛 `(<- x None e)` が実行時に通ること。

型の注記の `None` は isinstance に渡せない(`TypeError: isinstance() arg 2 must be a type`)。
契約と `<-` の型付き束縛は `_runtime-type` の 1 点で `(type None)` へ写す。
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType

import pytest

SOURCE = """\
(require doeff-hy.macros [defk deff <- defhandler])
(import dataclasses [dataclass])
(import doeff [EffectBase])

(defclass [(dataclass :frozen True)] Write [EffectBase]
  #^ str key)

(defk returns-none [x]
  {:pre [(: x int)] :post [(: % None)]}
  None)

(defk returns-optional [x]
  {:pre [(: x (| int None))] :post [(: % #(int None))]}
  x)

(defk binds-none []
  {:pre [] :post [(: % None)]}
  (<- got None (Write "none"))
  got)

(defhandler answers-none
  (Write [key] (resume None)))

(deff pure-none [x]
  {:pre [(: x int)] :post [(: % None)]}
  None)

(defk wrong-none [x]
  {:pre [(: x int)] :post [(: % None)]}
  x)
"""


@pytest.fixture(scope="module")
def mod(tmp_path_factory: pytest.TempPathFactory) -> ModuleType:
    import doeff_hy  # noqa: F401 - Hy の import hook を登録する

    root: Path = tmp_path_factory.mktemp("doeff_hy_none_contract")
    (root / "none_contract_probe.hy").write_text(SOURCE, encoding="utf-8")
    sys.path.insert(0, str(root))
    sys.dont_write_bytecode = True
    try:
        return importlib.import_module("none_contract_probe")
    finally:
        sys.path.remove(str(root))
        sys.dont_write_bytecode = False


def _run(program: object) -> object:
    from doeff import run

    return run(program)


def test_none_return_contract_accepts_none(mod: ModuleType) -> None:
    assert _run(mod.returns_none(1)) is None
    assert mod.pure_none(1) is None


def test_none_inside_a_type_tuple_and_union(mod: ModuleType) -> None:
    assert _run(mod.returns_optional(None)) is None
    assert _run(mod.returns_optional(4)) == 4


def test_typed_bind_accepts_none(mod: ModuleType) -> None:
    assert _run(mod.answers_none(mod.binds_none())) is None


def test_none_return_contract_still_rejects_other_values(mod: ModuleType) -> None:
    with pytest.raises(AssertionError, match="expected None, got int"):
        _run(mod.wrong_none(3))
