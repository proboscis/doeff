"""deftest の展開の引数の型(agora-redesign #2214)の失敗ケース。

前は deftest の展開が作る関数の引数(fixture の doeff_interpreter・tmp_path・monkeypatch …)に型が無く、strict の
doeff-hy-check で deftest 1 本につき書き手に直せない赤が約 5 件出た(reportUnknownParameterType・
reportMissingParameterType・返り値の Unknown・fixture の値の Unknown)。pytest の item の記録の import も
`doeff_hy.pytest_items` の stub 無し(reportMissingTypeStubs)の赤になっていた。

- strict で、doeff_interpreter と組み込みの fixture(tmp_path・monkeypatch)を使う deftest と :env の deftest の
  行に赤が 0 件。
- 書き手の fixture: `#^ T 名` の注記はそのまま写り、注記の無い名は object(Unknown にならない)。
- 型検査のための展開だけが形を変え、実行時の展開(引数の名・答えを返すこと)は今までどおり。
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

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")

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

#: PROBE の中で最初の deftest の行(1 始まり)— これより後の行の赤は deftest の展開に由来する。
FIRST_DEFTEST_LINE = 8


def _check(root: Path, *extra: str) -> list[dict[str, object]]:
    from doeff_hy.static_check import main

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
    # 記録の import は記帳として展開から外れ(stub 無しの赤にならない)、:marks の decorator の pytest は書き手の
    # `(import pytest)` と重ならない(reportDuplicateImport にならない)
    assert not [d for d in errors if "pytest_items" in str(d["message"])], errors
    assert not [d for d in errors if d["rule"] == "reportDuplicateImport"], errors
    # module の頭の補助の import も赤にならない(検体の全体で 0 件)
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
    # 文の位置の式は `_guard_statement_value` で包まれる。前は macros.pyi の引数の型(型引数の無い DoExpr の和)が
    # partially unknown で、文を持つ module ごとに 1 件の赤が module の頭の import に出ていた(書き手に直せない)。
    (tmp_path / "probe.hy").write_text(DEFK_ONLY, encoding="utf-8")
    assert _check(tmp_path, "--strict") == []


@needs_pyright
def test_user_fixture_annotation_is_checked(probe: Path) -> None:
    # 書き手の注記 `#^ str label` が写るので、label を int として使えば赤になる(型が逃げていない)。
    text = PROBE.replace("(len label)", "(+ label 1)")
    (probe / "probe.hy").write_text(text, encoding="utf-8")
    errors = _check(probe, "--strict")
    assert any(
        d["rule"] == "reportOperatorIssue"
        and int(str(d["line"])) == text.splitlines().index("  (<- got (add-one (+ label 1)))") + 1
        for d in errors
    ), errors


def _expand(source: str) -> str:
    # doeff_hy の import が Hy の importer と macro を用意する(副作用のための import)
    importlib.import_module("doeff_hy")
    return ast.unparse(hy.compiler.hy_compile(hy.read_many(source), "__main__"))


def test_static_view_annotates_only_the_static_expansion() -> None:
    from doeff_hy.static_view import static_view

    runtime = _expand(PROBE)
    with static_view():
        static = _expand(PROBE)
    # 型検査のための展開: 引数に fixture の型・書き手の注記・object、返り値は None
    assert (
        "def test_add_one_in_tmp(doeff_interpreter: _doeff_DeftestInterpreter, tmp_path: _doeff_TmpPath, "
        "monkeypatch: _doeff_MonkeyPatch, capsys: _doeff_CaptureStr) -> None:"
    ) in static, static
    assert (
        "def test_user_fixtures(doeff_interpreter: _doeff_DeftestInterpreter, label: str, anything: object) -> None:"
        in static
    )
    # 実行時の展開: 注記なしで interpreter の答えを返す(今までどおり)
    assert (
        "def test_add_one_in_tmp(doeff_interpreter, tmp_path, monkeypatch, capsys):" in runtime
    ), runtime
    assert "def test_user_fixtures(doeff_interpreter, label, anything):" in runtime
    assert "return doeff_interpreter(" in runtime
    assert "_doeff_DeftestInterpreter" not in runtime
    # :marks の decorator: 実行時は pytest、型検査のための展開は module の頭の別名 _doeff_pytest
    assert "@pytest.mark.slow" in runtime
    assert "@_doeff_pytest.mark.slow" in static


def test_every_fixture_type_is_declared_in_the_stub() -> None:
    # 表(static_view.DEFTEST_FIXTURE_TYPES)の型の名は、static_types.pyi に宣言が在る(無ければ注記が Unknown になる)
    from doeff_hy import static_view

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
