"""Failure cases for the types of the arguments of the deftest expansion.

Previously the arguments of the function the deftest expansion builds (the fixtures doeff_interpreter, tmp_path, monkeypatch …) had no types, and strict
doeff-hy-check gave about 5 reds per deftest that the author could not fix (reportUnknownParameterType,
reportMissingParameterType, an Unknown return value, an Unknown fixture value). The import for recording pytest items was also
a red because `doeff_hy._pytest_items` had no stub (reportMissingTypeStubs).

- Under strict, 0 reds on the lines of deftests that use doeff_interpreter and built-in fixtures (tmp_path, monkeypatch)
  and of deftests with :env.
- The author's fixtures: an annotation `#^ T name` is carried over as is, and a name without an annotation is object (not Unknown).
- Only the expansion for type checking changes shape; the runtime expansion (argument names, returning the answer) is as before.
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

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright is missing")

PROBE = """\
(require doeff-hy.macros [defk deftest <-])
(import pytest)

(defk add-one [n]
  {:pre [(: n int)] :post [(: % int)]}
  (+ n 1))

(deftest test-add-one-in-tmp [tmp-path monkeypatch capsys]
  (<- got (add-one 1))
  (val target (/ tmp-path "out.txt"))
  (val written (.write-text target (str got)))
  (val _ (.setenv monkeypatch "PROBE" "1"))
  (val captured (.readouterr capsys))
  (assert (= written 1))
  (assert (= captured.out ""))
  (assert (= (.read-text target) "2")))

(deftest test-add-one-with-env
  {:env {"probe.key" "value"} :marks ["slow"]}
  (<- got (add-one 2))
  (assert (= got 3)))

(deftest test-user-fixtures [#^ str label anything]
  (<- got (add-one (len label)))
  (assert (is-not anything None))
  (assert (= (pytest.approx 1.0) 1.0))
  (assert (> got 0)))
"""

#: The line of the first deftest in PROBE (1-based) — reds on lines after this come from the deftest expansion.
FIRST_DEFTEST_LINE = 8


def _check(root: Path, *extra: str) -> list[dict[str, object]]:
    from doeff_hy._static_check import main

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        main(["--root", str(root), "--json", "--no-cache", *extra, str(root / "probe.hy")])
    text = out.getvalue()
    found: list[dict[str, object]] = json.loads(text) if text.strip() else []
    return [d for d in found if d["severity"] == "error"]


@pytest.fixture
def probe(tmp_path: Path) -> Path:
    (tmp_path / "probe.hy").write_text(PROBE, encoding="utf-8")
    return tmp_path


@needs_pyright
def test_strict_has_no_error_from_deftest_expansions(probe: Path) -> None:
    errors = _check(probe, "--strict")
    from_deftests = [
        (d["rule"], d["line"]) for d in errors if int(str(d["line"])) >= FIRST_DEFTEST_LINE
    ]
    assert from_deftests == [], errors
    # The import for recording drops out of the expansion as bookkeeping (no missing-stub red), and pytest in the :marks decorator does not overlap the author's
    # `(import pytest)` (no reportDuplicateImport)
    assert not [d for d in errors if "pytest_items" in str(d["message"])], errors
    assert not [d for d in errors if d["rule"] == "reportDuplicateImport"], errors
    # The helper imports at the head of the module are not red either (0 over the whole sample)
    assert errors == [], errors


DEFK_ONLY = """\
(require doeff-hy.macros [defk])

(defk note [path]
  {:pre [(: path str)] :post [(: % None)]}
  (print path)
  None)
"""


@needs_pyright
def test_strict_has_no_error_in_a_defk_module_with_a_statement(tmp_path: Path) -> None:
    # An expression in statement position is wrapped by `_guard_statement_value`. Previously the argument type of macros.pyi (a union of DoExpr without type arguments) was
    # partially unknown, and each module with statements got one red at the module-head import (one the author cannot fix).
    (tmp_path / "probe.hy").write_text(DEFK_ONLY, encoding="utf-8")
    assert _check(tmp_path, "--strict") == []


@needs_pyright
def test_user_fixture_annotation_is_checked(probe: Path) -> None:
    # The author's annotation `#^ str label` is carried over, so using label as an int is red (the type has not escaped).
    text = PROBE.replace("(len label)", "(+ label 1)")
    (probe / "probe.hy").write_text(text, encoding="utf-8")
    errors = _check(probe, "--strict")
    assert any(
        d["rule"] == "reportOperatorIssue"
        and int(str(d["line"])) == text.splitlines().index("  (<- got (add-one (+ label 1)))") + 1
        for d in errors
    ), errors


def _expand(source: str) -> str:
    # The import of doeff_hy prepares Hy's importer and macros (an import for side effects)
    importlib.import_module("doeff_hy")
    return ast.unparse(hy.compiler.hy_compile(hy.read_many(source), "__main__"))


def test_static_view_annotates_only_the_static_expansion() -> None:
    from doeff_hy._static_view import static_view

    runtime = _expand(PROBE)
    with static_view():
        static = _expand(PROBE)
    # Expansion for type checking: arguments get the fixture type, the author's annotation, or object; the return is None
    assert (
        "def test_add_one_in_tmp(doeff_interpreter: _doeff_DeftestInterpreter, tmp_path: _doeff_TmpPath, "
        "monkeypatch: _doeff_MonkeyPatch, capsys: _doeff_CaptureStr) -> None:"
    ) in static, static
    assert (
        "def test_user_fixtures(doeff_interpreter: _doeff_DeftestInterpreter, label: str, anything: object) -> None:"
        in static
    )
    # Runtime expansion: returns the interpreter's answer with no annotation (as before)
    assert (
        "def test_add_one_in_tmp(doeff_interpreter, tmp_path, monkeypatch, capsys):" in runtime
    ), runtime
    assert "def test_user_fixtures(doeff_interpreter, label, anything):" in runtime
    assert "return doeff_interpreter(" in runtime
    assert "_doeff_DeftestInterpreter" not in runtime
    # The :marks decorator: pytest at runtime, the module-head alias _doeff_pytest in the expansion for type checking
    assert "@pytest.mark.slow" in runtime
    assert "@_doeff_pytest.mark.slow" in static


def test_every_fixture_type_is_declared_in_the_stub() -> None:
    # The type names in the table (static_view.DEFTEST_FIXTURE_TYPES) are declared in static_types.pyi (without that the annotation is Unknown)
    from doeff_hy import _static_view as static_view

    stub = Path(static_view.__file__).with_name("static_types.pyi")
    body = ast.parse(stub.read_text(encoding="utf-8")).body
    declared = {
        node.target.id
        for node in body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    } | {node.name for node in body if isinstance(node, ast.ClassDef)}
    missing = [
        e.type_name for e in static_view.DEFTEST_FIXTURE_TYPES if e.type_name not in declared
    ]
    assert missing == []
