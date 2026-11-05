"""The contract `(: % (get tuple #(X ...)))` / `(: % (of dict K V))` and typed bindings must pass at runtime.

Generic types with element types cannot be passed to isinstance (`TypeError: isinstance() argument 2 cannot be a
parameterized generic`). The contract and the typed binding of `<-` map to the outer type (tuple, dict) at the single point `doeff_hy._type_forms.runtime_type_form`,
and at runtime only the outer type is checked (element types are seen by the static type check — #1790).
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

(defclass [(dataclass :frozen True)] Names [EffectBase]
  #^ int n)

(defk names [n]
  {:pre [(: n int)] :post [(: % (get tuple #(str ...)))]}
  (tuple (gfor i (range n) (str i))))

(defk names-of [n]
  {:pre [(: n int)] :post [(: % (of tuple str ...))]}
  (tuple (gfor i (range n) (str i))))

(defk counts [n]
  {:pre [(: n int)] :post [(: % (of dict str int))]}
  (dfor i (range n) (str i) i))

(defk counts-get [n]
  {:pre [(: n int)] :post [(: % (get dict #(str int)))]}
  (dfor i (range n) (str i) i))

(defk maybe-names [n]
  {:pre [(: n (| (get tuple #(int ...)) None))] :post [(: % (| (get tuple #(str ...)) None))]}
  (if (is n None) None (tuple (gfor i n (str i)))))

(defk bound-names [n]
  {:pre [(: n int)] :post [(: % (get tuple #(str ...)))]}
  (<- got (get tuple #(str ...)) (Names n))
  got)

(defhandler answers-names
  (Names [n] (resume (tuple (gfor i (range n) (str i))))))

(deff pure-names [n]
  {:pre [(: n int)] :post [(: % (get tuple #(str ...)))]}
  (tuple (gfor i (range n) (str i))))

(defk wrong-names [n]
  {:pre [(: n int)] :post [(: % (get tuple #(str ...)))]}
  (list (gfor i (range n) (str i))))

(defk wrong-counts [n]
  {:pre [(: n int)] :post [(: % (of dict str int))]}
  (tuple (range n)))

;; A mid-body return also goes through :post and guard. A return inside a loop or branch, and a return of a nested function (not the outer
;; exit — not rewritten).
(defk early-names [n]
  {:pre [(: n int)] :post [(: % (get tuple #(str ...)))]}
  (for [i (range n)]
    (when (= i 1) (return (tuple ["early"]))))
  (tuple (gfor i (range n) (str i))))

(defk early-wrong [n]
  {:pre [(: n int)] :post [(: % (get tuple #(str ...)))]}
  (for [i (range n)]
    (when (= i 1) (return [i])))
  (tuple []))

(defk early-effect [n]
  {:pre [(: n int)] :post [(: % (| Names None))]}
  (when (> n 0) (return (Names n)))
  None)

(deff early-pure-wrong [n]
  {:pre [(: n int)] :post [(: % str)]}
  (if (> n 0) (return n) "zero"))

(deff nested-return-is-not-an-exit [n]
  {:pre [(: n int)] :post [(: % str)]}
  (defn inner [] (return 7))
  (str (+ n (inner))))
"""


@pytest.fixture(scope="module")
def mod(tmp_path_factory: pytest.TempPathFactory) -> ModuleType:
    import doeff_hy  # noqa: F401 - register the Hy import hook

    root: Path = tmp_path_factory.mktemp("doeff_hy_generic_contract")
    (root / "generic_contract_probe.hy").write_text(SOURCE, encoding="utf-8")
    sys.path.insert(0, str(root))
    dont_write_before = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        return importlib.import_module("generic_contract_probe")
    finally:
        sys.path.remove(str(root))
        sys.dont_write_bytecode = dont_write_before


def _run(program: object) -> object:
    """Run the Program of a loaded Hy module (a module attribute is object in the type system, so narrow it to Program)."""
    from doeff import Program, run

    match program:
        case Program():
            return run(program)
        case _:
            raise TypeError(f"tried to run a value that is not a Program: {program!r}")


def test_generic_tuple_return_contract_accepts_a_tuple(mod: ModuleType) -> None:
    assert _run(mod.names(2)) == ("0", "1")
    assert _run(mod.names_of(2)) == ("0", "1")
    assert mod.pure_names(2) == ("0", "1")


def test_generic_dict_return_contract_accepts_a_dict(mod: ModuleType) -> None:
    assert _run(mod.counts(2)) == {"0": 0, "1": 1}
    assert _run(mod.counts_get(1)) == {"0": 0}


def test_generic_inside_a_union_with_none(mod: ModuleType) -> None:
    assert _run(mod.maybe_names(None)) is None
    assert _run(mod.maybe_names((1, 2))) == ("1", "2")


def test_typed_bind_accepts_a_generic_tuple(mod: ModuleType) -> None:
    assert _run(mod.answers_names(mod.bound_names(2))) == ("0", "1")


def test_generic_contract_rejects_the_wrong_outer_type(mod: ModuleType) -> None:
    with pytest.raises(AssertionError):
        _run(mod.wrong_names(2))
    with pytest.raises(AssertionError):
        _run(mod.wrong_counts(2))


def test_an_early_return_passes_the_post_condition(mod: ModuleType) -> None:
    # A mid-body return value also passes if it fits :post, and a nested function's return is not the outer exit.
    assert _run(mod.early_names(3)) == ("early",)
    assert _run(mod.early_names(1)) == ("0",)
    assert mod.nested_return_is_not_an_exit(1) == "8"


def test_an_early_return_is_checked_like_the_last_form(mod: ModuleType) -> None:
    # Counterexample: a mid-body return of a type differing from :post fails, and if a mid-body return in defk returns an effect that is not fired, the guard fails it.
    with pytest.raises(AssertionError):
        _run(mod.early_wrong(3))
    with pytest.raises(AssertionError):
        mod.early_pure_wrong(1)
    assert mod.early_pure_wrong(0) == "zero"
    with pytest.raises(RuntimeError, match="unperformed effect"):
        _run(mod.early_effect(1))
