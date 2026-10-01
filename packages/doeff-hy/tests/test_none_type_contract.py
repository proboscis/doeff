"""契約 `(: % None)` と型付き束縛 `(<- x None e)` が実行時に通ること。

型の注記の `None` は isinstance に渡せない(`TypeError: isinstance() arg 2 must be a type`)。
契約と `<-` の型付き束縛は `_runtime-type` の 1 点で `None.__class__` へ写す(名前を引かない形 — #1825・#1845)。
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

;; 局所の名 type を持つ関数(agora-redesign #1825 — 展開が素の名 type を呼ぶと隠されて TypeError)
(defk shadowed-union [x]
  {:pre [(: x (| int None))] :post [(: % (| str None))]}
  (setv type "kind")
  (if (is x None) None type))

(defk shadowed-tuple [x]
  {:pre [(: x #(int None))] :post [(: % #(str None))]}
  (setv type "kind")
  (if (is x None) None type))

(defk shadowed-none [x]
  {:pre [(: x int)] :post [(: % None)]}
  (setv type "kind")
  None)
"""


@pytest.fixture(scope="module")
def mod(tmp_path_factory: pytest.TempPathFactory) -> ModuleType:
    import doeff_hy  # noqa: F401 - Hy の import hook を登録する

    root: Path = tmp_path_factory.mktemp("doeff_hy_none_contract")
    (root / "none_contract_probe.hy").write_text(SOURCE, encoding="utf-8")
    sys.path.insert(0, str(root))
    dont_write_before = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        return importlib.import_module("none_contract_probe")
    finally:
        sys.path.remove(str(root))
        sys.dont_write_bytecode = dont_write_before


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


def test_contracts_survive_a_local_name_type(mod: ModuleType) -> None:
    assert _run(mod.shadowed_union(None)) is None
    assert _run(mod.shadowed_union(1)) == "kind"
    assert _run(mod.shadowed_tuple(None)) is None
    assert _run(mod.shadowed_tuple(1)) == "kind"
    assert _run(mod.shadowed_none(1)) is None


def test_none_return_contract_still_rejects_other_values(mod: ModuleType) -> None:
    with pytest.raises(AssertionError, match="expected None, got int"):
        _run(mod.wrong_none(3))


def test_none_contract_expands_without_a_runtime_name_lookup() -> None:
    """契約の None の写しが実行時の名前の解決(hy.I → hy.__getattr__ → slashes2dots)を通らない(agora-redesign #1845)。

    以前の `hy.I.types.NoneType` は確かめのたびに名前を変換し直していた。展開した Python に `hy.I` が残らず、
    写しが `None.__class__` であることを確かめる。
    """
    import ast

    import hy
    from hy.compiler import hy_compile

    import doeff_hy  # noqa: F401 - Hy の import hook を登録する

    python = ast.unparse(hy_compile(hy.read_many(
        "(require doeff-hy.macros [deff])\n"
        "(deff pure-none [x] {:pre [(: x int)] :post [(: % None)]} None)\n"
        "(deff pure-optional [x] {:pre [(: x #(int None))] :post [(: % #(int None))]} x)\n"
    ), ModuleType("none_contract_expansion_probe")))
    assert "hy.I" not in python
    assert "None.__class__" in python
