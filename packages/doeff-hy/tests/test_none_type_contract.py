"""The contract `(: % None)` and the typed binding `(<- x None e)` must pass at runtime.

A type annotation `None` cannot be passed to isinstance (`TypeError: isinstance() arg 2 must be a type`).
Contracts and the typed binding of `<-` map it to `None.__class__` at the single point `doeff_hy._type_forms.runtime_type_form` (a form that does not look up a name — #1825, #1845).
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

;; A function with a local name type (if the expansion called the bare name type it would be shadowed and raise TypeError)
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
    import doeff_hy  # noqa: F401 - registers the Hy import hook

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
    """Run the Program of the loaded Hy module (a module attribute is object as far as types go, so narrow it to Program)."""
    from doeff import Program, run

    match program:
        case Program():
            return run(program)
        case _:
            raise TypeError(f"tried to run a value that is not a Program: {program!r}")


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
    """The contract's None mapping does not go through runtime name resolution (hy.I -> hy.__getattr__ -> slashes2dots).

    The earlier `hy.I.types.NoneType` converted the name again at every check. Check that no `hy.I` remains in the expanded Python and
    that the mapping is `None.__class__`.
    """
    import ast

    import hy
    from hy.compiler import hy_compile

    import doeff_hy  # noqa: F401 - registers the Hy import hook

    compiled = hy_compile(hy.read_many(
        "(require doeff-hy.macros [deff])\n"
        "(deff pure-none [x] {:pre [(: x int)] :post [(: % None)]} None)\n"
        "(deff pure-optional [x] {:pre [(: x #(int None))] :post [(: % #(int None))]} x)\n"
    ), ModuleType("none_contract_expansion_probe"))
    # hy_compile returns only a Module when get_expr is not passed (a union with a tuple as far as types go) — the tuple shape is a form we do not call, so stop on it.
    assert isinstance(compiled, ast.Module), compiled
    python = ast.unparse(compiled)
    assert "hy.I" not in python
    assert "None.__class__" in python
