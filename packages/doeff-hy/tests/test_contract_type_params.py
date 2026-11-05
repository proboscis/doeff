"""Failure cases for the contract type parameter :tp.

A function whose answer is decided by the argument types (such as a wrapper that returns the body's answer as is) checks the contract types with isinstance at runtime, so it cannot write generics,
and used to make `:post` a description string -- the .pyi of the static_stub tool made the answer Incomplete, and the user's type check lost the answer type
(records-connected in doeff-records, with-cluster-handlers in doeff-cluster). :tp is a way to write generics while staying a single point of the contract:

- In the type-checking expansion it is PEP 695 `def f[T]`. The .pyi also carries the type parameters, and the answer does not become Incomplete.
- In the runtime check the type parameters are erased to object -- an answer of any type passes, and outer types (tuple, Program) are still refused as before. The failure message is the type as written.
- The runtime expansion does not emit PEP 695 syntax (the function's __type_params__ is empty).
- Only deff and defk accept it (defp refuses). A :tp that is not a list of names is refused.
- Failure case: if :tp is removed and it goes back to a description string, the answer goes back to Incomplete.
"""

from __future__ import annotations

import sys
from pathlib import Path

import hy
import pytest
from doeff import Pure, run

from doeff_hy._static_check import Projection, project
from doeff_hy._static_stub import stub_of

# The type parameter syntax that the type-checking expansion and the stub write (PEP 695, Hy's :tp) is Python 3.12+ -- Hy on 3.11 refuses :tp.
needs_type_parameter_syntax = pytest.mark.skipif(sys.version_info < (3, 12), reason="type parameter syntax is Python 3.12+")

PRELUDE = """
(require doeff-hy.macros [defk deff defp <-])
(import doeff [Program])
"""

GENERIC = """
(defk passed-through [body]
  {:tp [T] :pre [(: body (of Program T object))] :post [(: % T)]}
  "Returns the body's answer as is."
  (<- answer body)
  answer)

(deff first-of [items]
  {:tp [T] :pre [(: items (of tuple T ...))] :post [(: % T)]}
  "Returns the first element of the tuple."
  (get items 0))
"""

# The form with :tp removed and the answer put back to a description string (how it was written before #2893).
DESCRIBED = GENERIC.replace(
    "{:tp [T] :pre [(: body (of Program T object))] :post [(: % T)]}",
    '{:pre [(: body Program)] :post [(: % "body answer")]}',
)


def evaluate(source: str) -> dict[str, object]:
    """Evaluate PRELUDE and source in one namespace and return the namespace."""
    namespace: dict[str, object] = {"__name__": "type_params_probe"}
    hy.eval(hy.read_many(PRELUDE + source), namespace)
    return namespace


def refused(source: str) -> str:
    """Check that evaluating source is refused, and return the error message."""
    with pytest.raises(Exception) as caught:
        evaluate(source)
    return str(caught.value)


def _module(tmp_path: Path, body: str) -> Path:
    """Put the check module in a package under the tmp root (root = tmp_path)."""
    source = tmp_path / "probe_pkg" / "probe_mod.hy"
    source.parent.mkdir(parents=True, exist_ok=True)
    (source.parent / "__init__.py").write_text("", encoding="utf-8")
    source.write_text(PRELUDE + body, encoding="utf-8")
    return source


def answer_of(namespace: dict[str, object], form: str) -> object:
    """Return the answer of evaluating one Hy expression on the namespace (the expression that runs the definition as a Program is written on the Hy side)."""
    return hy.eval(hy.read(form), namespace)


def test_any_answer_type_passes_at_runtime() -> None:
    ns = evaluate(GENERIC) | {"run": run, "Pure": Pure}
    assert answer_of(ns, "(run (passed-through (Pure 42)))") == 42
    assert answer_of(ns, '(run (passed-through (Pure "x")))') == "x"
    assert answer_of(ns, '(first-of #("a" 1))') == "a"


def test_the_outer_type_is_still_checked_and_named_as_written() -> None:
    first_of = evaluate(GENERIC)["first_of"]
    assert callable(first_of)
    with pytest.raises(AssertionError) as caught:
        first_of(["a"])
    message = str(caught.value)
    assert "Symbol('T')" in message and "got list" in message


def test_the_runtime_expansion_has_no_type_parameter_syntax() -> None:
    ns = evaluate(GENERIC)
    # The function's __type_params__ is Python 3.12+ (3.11 has no type parameter syntax at all).
    if sys.version_info >= (3, 12):
        assert getattr(ns["first_of"], "__type_params__") == ()


@needs_type_parameter_syntax
def test_the_type_check_view_declares_the_type_parameters(tmp_path: Path) -> None:
    projection = project(tmp_path, [tmp_path], _module(tmp_path, GENERIC))
    assert isinstance(projection, Projection)
    lines = projection.text.splitlines()
    assert "def passed_through[T](body: 'Program[T, object]') -> 'T':" in lines
    assert "def first_of[T](items: 'tuple[T, ...]') -> 'T':" in lines


@needs_type_parameter_syntax
def test_the_stub_carries_the_type_parameters(tmp_path: Path) -> None:
    made = stub_of(tmp_path, [tmp_path], _module(tmp_path, GENERIC))
    lines = made.text.splitlines()
    assert "def passed_through[T](body: Program[T, object]) -> _Program[T, object]:" in lines
    assert "def first_of[T](items: tuple[T, ...]) -> T:" in lines
    assert made.incomplete == ()


@needs_type_parameter_syntax
def test_without_the_type_parameter_the_answer_falls_back_to_incomplete(tmp_path: Path) -> None:
    # Failure case: an answer given as a description string does not carry a type.
    made = stub_of(tmp_path, [tmp_path], _module(tmp_path, DESCRIBED))
    assert made.incomplete == ("passed_through answer",)


def test_defp_refuses_type_parameters() -> None:
    assert ":tp" in refused("(defp answer {:tp [T] :post [(: % int)]} 42)")


@pytest.mark.parametrize("value", ["T", "[]", '["T"]', "[T T]"])
def test_type_parameters_must_be_distinct_names(value: str) -> None:
    assert ":tp" in refused(f"(deff f [x] {{:tp {value} :pre [(: x int)] :post [(: % int)]}} x)")
