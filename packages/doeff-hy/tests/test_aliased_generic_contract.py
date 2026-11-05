"""The T of a contract `(: x T)` and of a typed binding `(<- x T e)` must be checked by the outer type, without failing with TypeError at runtime, even when
T is a name whose value is a parameterized generic type (a type alias).

Contracts and bindings map the written type with `doeff_hy._type_forms.runtime_type_form` into a form that can be passed to isinstance, but only a literal generic type can be mapped;
a name has an unknown value at expansion time, so it is passed to isinstance as is. If the name's value is `Callable[..., object]` or `FrozenMap[object] | tuple[object, ...]`,
every value produced `TypeError: isinstance() argument 2 cannot be a parameterized generic` (twice in production on 2026-10-11 —
agora-controllers' turn-host stopped for 9 minutes on the alias LiveLimitAnswer = (get Callable #(... object)), and the intake returned 500 for all
mail on the alias FrozenJson. Users avoided it by expanding to the literal, and for FrozenJson doeff-hy made the runtime name a union of outer types on its side — 6ab735f37).
What is guarded here is that the name is treated the same whatever its value: only the outer type is checked at runtime (the decision of #1790), and a value of a different outer type is still refused
with a named AssertionError. A contract that puts a non-type value in the name is refused with a TypeError that says what is not a type.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType

import pytest

SOURCE = """\
(require doeff-hy.macros [defk <-])
(import collections.abc [Callable])
(import doeff_hy._frozen [FrozenMap])

;; Names whose value is a parameterized generic type (the alias form a user module writes).
(setv Answer (get Callable #(... object)))
(setv Rows (| (get FrozenMap object) (get tuple #(object ...)) None))
(setv NotAType 3)

(defk called [answer]
  {:pre [(: answer Answer)] :post [(: % Answer)]}
  answer)

(defk rows-of [rows]
  {:pre [(: rows Rows)] :post [(: % bool)]}
  True)

;; To isolate only the binding check, insert one step that lets both strings and functions through with a literal type.
(defk echo [value]
  {:pre [(: value (| str (get Callable #(... object))))] :post [(: % (| str (get Callable #(... object))))]}
  value)

(defk bound-answer [value]
  {:pre [(: value (| str (get Callable #(... object))))] :post [(: % (get Callable #(... object)))]}
  (<- got Answer (echo value))
  got)

(defk misdeclared [value]
  {:pre [(: value NotAType)] :post [(: % bool)]}
  True)
"""


@pytest.fixture(scope="module")
def mod(tmp_path_factory: pytest.TempPathFactory) -> ModuleType:
    import doeff_hy  # noqa: F401 - registers the Hy import hook

    root: Path = tmp_path_factory.mktemp("doeff_hy_aliased_generic_contract")
    (root / "aliased_generic_contract_probe.hy").write_text(SOURCE, encoding="utf-8")
    sys.path.insert(0, str(root))
    dont_write_before = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        return importlib.import_module("aliased_generic_contract_probe")
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


def test_a_contract_naming_an_aliased_callable_accepts_a_function(mod: ModuleType) -> None:
    assert _run(mod.called(len)) is len


def test_a_contract_naming_an_aliased_union_of_generics_accepts_each_outer_type(mod: ModuleType) -> None:
    from doeff_hy._frozen import freeze_json_text

    for value in (freeze_json_text('{"a": 1}'), (1, 2), None):
        assert _run(mod.rows_of(value)) is True, value


def test_a_typed_bind_naming_an_aliased_callable_passes(mod: ModuleType) -> None:
    assert _run(mod.bound_answer(len)) is len


def test_an_aliased_contract_still_refuses_a_value_of_another_outer_type(mod: ModuleType) -> None:
    # A value of a different outer type is refused by the contract (not TypeError, but a named AssertionError).
    with pytest.raises(AssertionError, match="expected Answer"):
        _run(mod.called(3))
    with pytest.raises(AssertionError, match="expected Rows"):
        _run(mod.rows_of([1]))
    with pytest.raises(AssertionError, match="expected Answer"):
        _run(mod.bound_answer("x"))


def test_a_contract_naming_a_value_that_is_not_a_type_names_it(mod: ModuleType) -> None:
    with pytest.raises(TypeError, match="not a type"):
        _run(mod.misdeclared(1))
