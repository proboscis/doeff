"""Hy の test file を import せずに収集し、item の setup で初めて import する(agora-redesign #1223・親 #1211)。

別の process で pytest を走らせる(pytester の subprocess)。1 回目は記録が無いので収集で import し(その時に Hy の
importer が pyc と記録を書く)、2 回目は記録から収集する。2 回の nodeid・選別・結果が同じで、2 回目の収集は test module を
import しないことを確かめる。pyc の置き場は tmp の下(PYTHONPYCACHEPREFIX)— checkout の中に pyc を書かない。

各 test module は import された時に、名を ``IMPORT_LOG`` の file に 1 行足す(import の回数を外から数えるため)。
"""

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

pytest_plugins = ["pytester"]

PRELUDE = """
(require doeff-hy.macros [deftest val])
(import os pytest)
(with [log (open (get os.environ "IMPORT_LOG") "a")] (.write log (+ __name__ "\\n")))
"""

FILES = {
    "pkg/__init__.py": "",
    "pkg/tests/__init__.py": "",
    "pkg/tests/test_alpha.hy": PRELUDE
    + """
(val pytestmark pytest.mark.real-world)
(deftest test-plain (assert True))
(deftest test-slow {:marks ["slow"]} (assert True))
(deftest test-params [x y] {:params {"x" ["a" 1 2.5 True None] "y" [[1 2] {"k" 1}]}}
  (assert (in y [[1 2] {"k" 1}])))
(deftest test-skipped {:skip-if (= 1 1) :skip-reason "always"} (assert False))
(deftest test-kept {:skip-if (= 1 2) :skip-reason "never"} (assert True))
""",
    # 親の package の中の別の module を名前で import する(agora-redesign #1212 の反例)
    "pkg/tests/test_beta.hy": PRELUDE
    + """
(import pkg.tests.test-alpha :as alpha)
(deftest test-uses-alpha (assert (callable alpha.test-plain)))
""",
    "pkg/tests/test_dynamic.hy": PRELUDE
    + """
(val VALUES [1 2 3])
(deftest test-dynamic [v] {:params {"v" VALUES}} (assert (in v VALUES)))
""",
    # module の直下の skipif の pytestmark — 記録から収集し、条件は setup で実物の印から評価する
    "pkg/tests/test_env_gated.hy": PRELUDE
    + """
(val pytestmark (pytest.mark.skipif (not (os.getenv "I1211_NEVER_SET")) :reason "env が無い"))
(deftest test-gated (assert False))
""",
    # module の最上位で module ごと飛ばす(import すれば item は 0 本)
    "pkg/tests/test_skipped_module.hy": PRELUDE
    + """
(pytest.skip "module ごと飛ばす" :allow-module-level True)
(deftest test-never (assert False))
""",
}


@pytest.fixture
def project(pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> pytest.Pytester:
    """3 つの test file を持つ project。pyc と記録は tmp の下の置き場へ書く。"""
    for name, text in FILES.items():
        path = pytester.path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    pytester.makeconftest(
        """
        import pytest


        @pytest.fixture
        def doeff_interpreter():
            def run_program(program, *, env=None):
                from doeff import run

                return run(program)

            return run_program
        """
    )
    pytester.makeini(
        """
        [pytest]
        doeff_adr_hy_files = pkg/tests/test_*.hy
        doeff_adr_wiring = off
        markers =
            slow: slow
            real_world: real world
        """
    )
    monkeypatch.setenv("PYTHONPYCACHEPREFIX", str(tmp_path / "pyc"))
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "")
    monkeypatch.setenv("IMPORT_LOG", str(tmp_path / "imports.log"))
    return pytester


def _imports(tmp_path: Path) -> list[str]:
    """これまでに import された test module の名(import の回数だけ並ぶ)。読んだら空にする。"""
    log = tmp_path / "imports.log"
    names = log.read_text().split() if log.exists() else []
    log.write_text("")
    return names


@dataclass(frozen=True)
class Collected:
    """--collect-only の結果: 集めた nodeid と、報告の全文。"""

    nodeids: list[str]
    out: str


def _collect(project: pytest.Pytester, *args: str) -> Collected:
    """別の process で --collect-only を走らせる。"""
    result = project.runpytest_subprocess("--collect-only", "-q", "-p", "no:cacheprovider", *args)
    nodeids = [line for line in result.stdout.lines if "::" in line and not line.startswith(" ")]
    return Collected(nodeids, result.stdout.str())


def test_warm_collection_matches_cold_and_imports_nothing(project: pytest.Pytester, tmp_path: Path) -> None:
    """2 回目(記録あり)の収集は 1 回目と同じ nodeid を返し、動的な file の他は test module を import しない。"""
    cold = _collect(project).nodeids
    assert sorted(_imports(tmp_path)) == sorted(
        [
            "pkg.tests.test_alpha",
            "pkg.tests.test_beta",
            "pkg.tests.test_dynamic",
            "pkg.tests.test_env_gated",
            "pkg.tests.test_skipped_module",
        ]
    )
    warm = _collect(project)
    out = warm.out
    assert warm.nodeids == cold
    assert "記録から収集 3 file・収集で import 2 file" in out
    assert "import: pkg/tests/test_dynamic.hy — 動的: test_dynamic" in out
    assert "import: pkg/tests/test_skipped_module.hy — 最上位で呼ぶ: pytest.skip" in out
    assert _imports(tmp_path) == ["pkg.tests.test_dynamic", "pkg.tests.test_skipped_module"]


def test_selection_by_k_and_m_is_the_same_and_imports_only_the_chosen_file(
    project: pytest.Pytester, tmp_path: Path
) -> None:
    """``-k`` / ``-m`` の選別は記録からの収集でも同じに効き、走らせた item の file だけが import される。"""
    _collect(project)
    _imports(tmp_path)
    result = project.runpytest_subprocess("-p", "no:cacheprovider", "-k", "test_slow")
    result.assert_outcomes(passed=1, deselected=18, skipped=1)
    assert _imports(tmp_path) == [
        "pkg.tests.test_dynamic",
        "pkg.tests.test_skipped_module",
        "pkg.tests.test_alpha",
    ]
    result = project.runpytest_subprocess("-p", "no:cacheprovider", "-m", "slow")
    result.assert_outcomes(passed=1, deselected=18, skipped=1)
    result = project.runpytest_subprocess("-p", "no:cacheprovider", "-m", "not real_world")
    result.assert_outcomes(passed=4, deselected=14, skipped=2)


def test_params_and_skip_if_run_as_when_imported(project: pytest.Pytester) -> None:
    """params の値は実物の値で走り(中身を問わない値も)、skip-if は実物の式で評価される。"""
    cold = project.runpytest_subprocess("-p", "no:cacheprovider")
    cold.assert_outcomes(passed=17, skipped=3)
    warm = project.runpytest_subprocess("-p", "no:cacheprovider", "-rs")
    warm.assert_outcomes(passed=17, skipped=3)
    warm.stdout.fnmatch_lines(["*always*", "*env が無い*"])


def test_a_record_that_disagrees_fails_the_item_and_is_forgotten(project: pytest.Pytester, tmp_path: Path) -> None:
    """記録を手で書き換えて実物と食い違わせると、その item は赤になり、記録が消え、次の収集は import し直す。"""
    _collect(project)
    [hydeps] = [p for p in (tmp_path / "pyc").rglob("test_alpha*.hydeps")]
    data = json.loads(hydeps.read_text())
    for record in data["records"]["doeff.pytest-items"]:
        if record.get("function") == "test_slow":
            record["decorators"] = [{"mark": "fast"}]
    hydeps.write_text(json.dumps(data))
    _imports(tmp_path)
    result = project.runpytest_subprocess("-p", "no:cacheprovider", "-k", "test_slow or test_fast")
    result.assert_outcomes(errors=1, deselected=18, skipped=1)
    result.stdout.fnmatch_lines(["*記録と実物が食い違った*"])
    out = _collect(project).out
    assert "import: pkg/tests/test_alpha.hy — 記録なし" in out
