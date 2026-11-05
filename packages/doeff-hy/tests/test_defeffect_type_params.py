"""Failure cases for the type parameter :tp of defeffect.

For an effect whose answer is determined by the answer of the Program it runs (SqlTransaction in doeff-core-effects -- answer = the program's answer T | a failure value),
the field `program` and :answer read the type parameter T. The type-check expansion did not make the class generic in T, so writing `Program[T, object]` in a field
made the defining module's check report red ("T has no meaning in this context"), and not writing it left callers with no way to determine the answer T.
:tp has the same form as :tp in defk and deff, and is the entry point for writing the class's type parameters while keeping the contract at a single point:

- In the type-check expansion this is PEP 695 `class E[T](...)`. The .pyi also carries the type parameter, and the base class's answer reads T.
- The runtime expansion emits no type parameters (the class's __type_params__ is empty) -- T is read through the module's TypeVar.
- A :tp that is not a sequence of names, or that has duplicate names, is rejected.
- Failure case: without :tp, the defining module's check turns red (T has no meaning). Callers can turn a mix-up of the answer type red.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import hy
import pytest

from doeff_hy._static_check import Projection, project
from doeff_hy._static_stub import stub_of, strict_errors

# The type-parameter syntax written by the type-check expansion and the stub (PEP 695, Hy's :tp) needs Python 3.12 -- Hy on 3.11 rejects :tp.
needs_type_parameter_syntax = pytest.mark.skipif(sys.version_info < (3, 12), reason="type parameter syntax requires Python 3.12")

PRELUDE = """
(require doeff-hy.macros [defeffect val])
(import doeff [Program])
(import typing [TypeVar])
(val T (TypeVar "T"))
"""

GENERIC = """
(defeffect Wrapped
  "Wrap and run a program (answer = the program's answer | None)."
  {:tp [T]
   :fields [(: database str) (: program (get Program #(T object)))]
   :pre [(: database str) (: program Program)]
   :answer (| T None)
   :tags {:context "probe" :role "foundation"}})
"""

# The form without :tp (how it was written before #2939): the field's T is not a type parameter of the class, so the defining module's check turns red.
UNDECLARED = GENERIC.replace("{:tp [T]\n   :fields", "{:fields")

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright is not available")


def evaluate(source: str) -> dict[str, object]:
    """To evaluate PRELUDE and source in a single namespace and return the namespace."""
    namespace: dict[str, object] = {"__name__": "effect_type_params_probe"}
    hy.eval(hy.read_many(PRELUDE + source), namespace)
    return namespace


def refused(source: str) -> str:
    """To confirm that evaluating source is rejected and return the error message."""
    with pytest.raises(Exception) as caught:
        evaluate(source)
    return str(caught.value)


def _module(tmp_path: Path, body: str) -> Path:
    """To place the checked module as a package under the tmp root (root = tmp_path)."""
    source = tmp_path / "probe_pkg" / "probe_mod.hy"
    source.parent.mkdir(parents=True, exist_ok=True)
    (source.parent / "__init__.py").write_text("", encoding="utf-8")
    source.write_text(PRELUDE + body, encoding="utf-8")
    return source


def test_the_effect_works_at_runtime_and_the_class_has_no_type_parameter_syntax() -> None:
    wrapped = evaluate(GENERIC)["Wrapped"]
    assert isinstance(wrapped, type)
    # The class's __type_params__ exists from Python 3.12 (3.11 has no type parameter syntax at all).
    if sys.version_info >= (3, 12):
        assert wrapped.__type_params__ == ()
    assert wrapped.__doeff_defeffect__ is True


def test_the_runtime_checks_are_unchanged_by_the_type_parameter() -> None:
    wrapped = evaluate(GENERIC)["Wrapped"]
    assert callable(wrapped)
    with pytest.raises(AssertionError):
        wrapped(database="db", program="not a program")


@needs_type_parameter_syntax
def test_the_type_check_view_makes_the_class_generic(tmp_path: Path) -> None:
    projection = project(tmp_path, [tmp_path], _module(tmp_path, GENERIC))
    assert isinstance(projection, Projection)
    lines = projection.text.splitlines()
    assert "class Wrapped[T](_doeff_effect_base):" in lines
    assert "    program: Program[T, object]" in lines


@needs_type_parameter_syntax
def test_the_stub_carries_the_type_parameter_and_the_answer_reads_it(tmp_path: Path) -> None:
    made = stub_of(tmp_path, [tmp_path], _module(tmp_path, GENERIC))
    lines = made.text.splitlines()
    assert "class Wrapped[T](_doeff_effect_base[T | None]):" in lines
    assert "    program: Program[T, object]" in lines
    assert made.incomplete == ()


def test_without_the_type_parameter_the_class_is_not_generic(tmp_path: Path) -> None:
    # Failure case: without :tp, the expansion does not make the class generic (the defining module's check reads the field's T as a meaningless type parameter).
    projection = project(tmp_path, [tmp_path], _module(tmp_path, UNDECLARED))
    assert isinstance(projection, Projection)
    assert "class Wrapped(_doeff_effect_base):" in projection.text.splitlines()


@needs_pyright
def test_the_defining_module_has_no_red_with_the_type_parameter_and_one_without(tmp_path: Path) -> None:
    (tmp_path / "probe_pkg").mkdir()
    (tmp_path / "probe_pkg" / "__init__.py").write_text("", encoding="utf-8")
    declared = strict_errors(tmp_path, PRELUDE + GENERIC)
    undeclared = strict_errors(tmp_path, PRELUDE + UNDECLARED)
    assert not [e for e in declared if "no meaning" in e], declared
    assert [e for e in undeclared if "no meaning" in e], undeclared


USER = """\
(require doeff-hy.macros [defk <-])
(import doeff [Pure])
(import probe_pkg.probe_mod [Wrapped])

(defk answer-of []
  {:pre [] :post [(: % ANSWER)]}
  "To receive the program's answer as the caller's type."
  (<- answer ANSWER (Wrapped :database "db" :program (Pure 1)))
  answer)
"""


@needs_pyright
@needs_type_parameter_syntax
def test_the_program_answer_type_reaches_the_caller(tmp_path: Path) -> None:
    source = _module(tmp_path, GENERIC)
    # The type callers read is the .pyi next to the .hy (the type-check expansion's base stays plain -- only the .pyi carries the answer).
    source.with_suffix(".pyi").write_text(stub_of(tmp_path, [tmp_path], source).text, encoding="utf-8")
    # Caller: the program's answer int becomes T, and the effect's answer is `int | None`. Receiving it as the same type is green; receiving it as str is red.
    assert strict_errors(tmp_path, USER.replace("ANSWER", "(| int None)")) == ()
    assert [e for e in strict_errors(tmp_path, USER.replace("ANSWER", "str")) if "reportAssignmentType" in e]


@pytest.mark.parametrize("value", ["T", "[]", '["T"]', "[T T]"])
def test_type_parameters_must_be_distinct_names(value: str) -> None:
    broken = GENERIC.replace("{:tp [T]", f"{{:tp {value}")
    assert ":tp" in refused(broken)
