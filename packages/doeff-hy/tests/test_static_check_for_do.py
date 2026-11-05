"""Failure cases for the types of defk in modules that use for/do (parent #2318).

The for/do expansion names `_doeff_do`, `_doeff_traverse_Traverse` and `_doeff_traverse_Skip`, and the author imports those names with
`(import doeff [do :as _doeff-do])` or the like (docstring of for/do). In the expansion for type checking, doeff-hy-check places static_types' `do` at the head of the
module as `_doeff_do`, so two declarations of the same name sit side by side. Previously the two types differed
(core's `do` had no overload for functions whose body has no yield); pyright read the defk projection from the later author import
(`<-` became `_doeff_perform` and the function had no yield), so every defk definition in the same module turned red and the results of calls to defk
became Unknown (47 cases in agora's book_rows.hy). In addition, the per-item function parameter (x in `(<- x (From ...))`) became
an unannotated def parameter, and strict reported "parameter type is unknown". doeff_traverse has no py.typed, and the Collection
`valid_values` of the Traverse result was also Unknown. After the fix:

- With strict, 0 errors for the subject defk that uses for/do and the defk that calls it.
- Result types do not escape: adding a number to the item x (str), or a string to the for/do result value (int), gives a type-mismatch error.
- The runtime expansion is unchanged (it takes the item parameter as is and names no type-checking helper), and the run result is the same.
"""

import ast
import contextlib
import importlib
import io
import json
import shutil
from pathlib import Path

import hy
import pytest

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright is not available")

PROBE = """\
(require doeff-hy.macros [defk val <- for/do])
(import doeff [do :as _doeff-do])
(import doeff_traverse [Traverse :as _doeff_traverse_Traverse])
(import doeff_traverse [Skip :as _doeff_traverse_Skip])

(defk width-of [text]
  {:pre [(: text str)] :post [(: % int)]}
  (len text))

(defk widths [texts]
  {:pre [(: texts (get tuple #(str ...)))] :post [(: % (get tuple #(int ...)))]}
  (<- got
      (for/do
        (<- text (From texts :label "width"))
        (When (> (len text) 0))
        (<- width (width-of text))
        width))
  (val errors got.errors)
  (when errors
    (raise (get errors 0)))
  (tuple got.valid-values))

(defk total-width [texts]
  {:pre [(: texts (get tuple #(str ...)))] :post [(: % int)]}
  (<- each (widths texts))
  (<- one (width-of "a"))
  (+ (sum each) one))
"""


def _check(root: Path, text: str) -> list[dict[str, object]]:
    from doeff_hy._static_check import main

    (root / "probe.hy").write_text(text, encoding="utf-8")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        main(["--root", str(root), "--json", "--no-cache", "--strict", str(root / "probe.hy")])
    printed = out.getvalue()
    found: list[dict[str, object]] = json.loads(printed) if printed.strip() else []
    return [d for d in found if d["severity"] == "error"]


def _line_of(text: str, fragment: str) -> int:
    return next(index for index, line in enumerate(text.splitlines(), 1) if fragment in line)


@needs_pyright
def test_for_do_module_has_no_strict_error(tmp_path: Path) -> None:
    assert _check(tmp_path, PROBE) == []


@needs_pyright
def test_item_used_as_the_wrong_type_is_red(tmp_path: Path) -> None:
    # The item text reads as str, the element type of items (tuple[str, ...]) -- adding a number gives a type-mismatch error
    # (checks that the item parameter has not escaped to object / Any).
    text = PROBE.replace("(When (> (len text) 0))", "(When (> (+ text 1) 0))")
    errors = _check(tmp_path, text)
    assert ("reportOperatorIssue", _line_of(text, "(+ text 1)")) in [
        (d["rule"], d["line"]) for d in errors
    ], errors


@needs_pyright
def test_for_do_answer_used_as_the_wrong_type_is_red(tmp_path: Path) -> None:
    # The valid value of the for/do result reads as the body result (int from width-of) -- adding a string gives an error.
    text = PROBE.replace("(tuple got.valid-values))", '(tuple (+ (get got.valid-values 0) "x")))')
    errors = _check(tmp_path, text)
    assert ("reportOperatorIssue", _line_of(text, '"x"')) in [(d["rule"], d["line"]) for d in errors], errors


@needs_pyright
def test_defk_answer_in_a_for_do_module_is_read(tmp_path: Path) -> None:
    # Even in a for/do module, the result of calling defk (a tuple[int, ...] of widths) is readable -- adding a string gives an error
    # (previously the result was Unknown, and adding did not give an error).
    text = PROBE.replace("(+ (sum each) one)", '(+ each "x")')
    errors = _check(tmp_path, text)
    assert ("reportOperatorIssue", _line_of(text, '"x"')) in [(d["rule"], d["line"]) for d in errors], errors


# for/do with two Froms stacked -- the result is a Collection that flattens the body values of each tuple, and the valid value
# reads as the body result (int). Previously the outer item function returned the inner Traverse as a value, and the valid value was a Traverse even in the types.
NESTED_PROBE = """\
(require doeff-hy.macros [defk <- for/do])
(import doeff [do :as _doeff-do])
(import doeff_traverse [Traverse :as _doeff_traverse_Traverse])

(defk sums [xs ys]
  {:pre [(: xs (get tuple #(int ...))) (: ys (get tuple #(int ...)))] :post [(: % (get tuple #(int ...)))]}
  (<- got
      (for/do
        (<- x (From xs :label "outer"))
        (<- y (From ys :label "inner"))
        (+ x y)))
  (tuple got.valid-values))
"""


@needs_pyright
def test_two_froms_module_has_no_strict_error(tmp_path: Path) -> None:
    assert _check(tmp_path, NESTED_PROBE) == []


@needs_pyright
def test_two_froms_answer_used_as_the_wrong_type_is_red(tmp_path: Path) -> None:
    text = NESTED_PROBE.replace("(tuple got.valid-values))", '(tuple (+ (get got.valid-values 0) "x")))')
    errors = _check(tmp_path, text)
    assert ("reportOperatorIssue", _line_of(text, '"x"')) in [(d["rule"], d["line"]) for d in errors], errors


def test_two_froms_run_to_one_value_per_pair(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from doeff_traverse import sequential

    from doeff import run, with_handlers

    importlib.import_module("doeff_hy")
    (tmp_path / "for_do_nested_probe_ki7e6a.hy").write_text(NESTED_PROBE, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    probe = importlib.import_module("for_do_nested_probe_ki7e6a")
    assert run(with_handlers([sequential], probe.sums((1, 2), (10, 20)))) == (11, 21, 12, 22)


def _expand(source: str) -> str:
    # importing doeff_hy sets up the Hy importer and macros (an import for its side effects)
    importlib.import_module("doeff_hy")
    return ast.unparse(hy.compiler.hy_compile(hy.read_many(source), "__main__"))


def test_only_the_static_expansion_reads_the_item_through_the_helper() -> None:
    from doeff_hy._static_view import static_view

    runtime = _expand(PROBE)
    with static_view():
        static = _expand(PROBE)
    assert "_doeff_traverse_item" in static, static
    # The runtime expansion takes the item parameter as is (names no type-checking helper)
    assert "_doeff_traverse_item" not in runtime, runtime
    assert "(text):" in runtime, runtime


def test_for_do_runs_as_before(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from doeff_traverse import fail_handler, sequential

    from doeff import run, with_handlers

    importlib.import_module("doeff_hy")
    (tmp_path / "for_do_probe_2321.hy").write_text(PROBE, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    probe = importlib.import_module("for_do_probe_2321")
    widths = probe.widths
    total_width = probe.total_width
    assert run(with_handlers([sequential, fail_handler], widths(("ab", "", "cde")))) == (2, 3)
    assert run(with_handlers([sequential, fail_handler], total_width(("ab", "", "cde")))) == 6
