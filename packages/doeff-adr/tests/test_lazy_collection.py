"""Hy の test file を import せずに収集し、item の setup で初めて import する(agora-redesign #1223・親 #1211)。

別の process で pytest を走らせる(pytester の subprocess)。1 回目は記録が無いので収集で import し(その時に Hy の
plugin が記録をキャッシュに書く — agora-redesign #1291)、2 回目はキャッシュから収集する。2 回の nodeid・選別・結果が同じで、
2 回目の収集は test module を import しないことを確かめる。キャッシュと pyc の置き場は tmp の下(ini の doeff_adr_items_cache・
PYTHONPYCACHEPREFIX)— checkout の中にも利用者のキャッシュにも書かない。

各 test module は import された時に、名を ``IMPORT_LOG`` の file に 1 行足す(import の回数を外から数えるため)。
"""

import hashlib
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
    # 自前の macro の提供元(pkg.helpers)を使う — 提供元が変われば古い記録を読まない(#1291 の受入 4)
    "pkg/helpers.hy": "(defmacro answer [] 42)\n",
    "pkg/tests/test_gamma.hy": PRELUDE
    + """
(require pkg.helpers [answer])
(deftest test-answer (assert (= (answer) 42)))
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
        f"""
        [pytest]
        doeff_adr_hy_files = pkg/tests/test_*.hy
        doeff_adr_wiring = off
        doeff_adr_items_cache = {tmp_path / "items"}
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
            "pkg.tests.test_gamma",
            "pkg.tests.test_skipped_module",
        ]
    )
    warm = _collect(project)
    out = warm.out
    assert warm.nodeids == cold
    assert "記録から収集 4 file・収集で import 2 file" in out
    assert "保存しない: 動的: test_dynamic" in out
    assert "import: pkg/tests/test_skipped_module.hy — 記録なし" in out
    assert _imports(tmp_path) == ["pkg.tests.test_dynamic", "pkg.tests.test_skipped_module"]


def test_selection_by_k_and_m_is_the_same_and_imports_only_the_chosen_file(
    project: pytest.Pytester, tmp_path: Path
) -> None:
    """``-k`` / ``-m`` の選別は記録からの収集でも同じに効き、走らせた item の file だけが import される。"""
    _collect(project)
    _imports(tmp_path)
    result = project.runpytest_subprocess("-p", "no:cacheprovider", "-k", "test_slow")
    result.assert_outcomes(passed=1, deselected=19, skipped=1)
    assert _imports(tmp_path) == [
        "pkg.tests.test_dynamic",
        "pkg.tests.test_skipped_module",
        "pkg.tests.test_alpha",
    ]
    result = project.runpytest_subprocess("-p", "no:cacheprovider", "-m", "slow")
    result.assert_outcomes(passed=1, deselected=19, skipped=1)
    result = project.runpytest_subprocess("-p", "no:cacheprovider", "-m", "not real_world")
    result.assert_outcomes(passed=5, deselected=14, skipped=2)


def test_params_and_skip_if_run_as_when_imported(project: pytest.Pytester) -> None:
    """params の値は実物の値で走り(中身を問わない値も)、skip-if は実物の式で評価される。"""
    cold = project.runpytest_subprocess("-p", "no:cacheprovider")
    cold.assert_outcomes(passed=18, skipped=3)
    warm = project.runpytest_subprocess("-p", "no:cacheprovider", "-rs")
    warm.assert_outcomes(passed=18, skipped=3)
    warm.stdout.fnmatch_lines(["*always*", "*env が無い*"])


def test_a_named_hy_file_imported_by_another_named_file_is_read_by_hy(project: pytest.Pytester) -> None:
    """命令の行で名指した .hy を、先に並んだ別の名指しの file が import しても、Hy の loader で読まれる。

    pytest の assert の書き換えは、命令の行で名指した file を Python として読み直す(``ast.parse``)。.hy が
    そこへ渡ると SyntaxError になる(agora-redesign #1211 の後の報告 — test_emulated_delivery_unwritten が
    test_emulated_delivery_to_turn を import する組)。記録が冷えていても温まっていても同じに通る。
    """
    named = ("pkg/tests/test_beta.hy", "pkg/tests/test_alpha.hy")
    cold = project.runpytest_subprocess("-p", "no:cacheprovider", *named)
    cold.assert_outcomes(passed=14, skipped=1)
    warm = project.runpytest_subprocess("-p", "no:cacheprovider", *named)
    warm.assert_outcomes(passed=14, skipped=1)


def test_a_record_that_disagrees_fails_the_item_and_is_forgotten(project: pytest.Pytester, tmp_path: Path) -> None:
    """記録を手で書き換えて実物と食い違わせると、その item は赤になり、記録が消え、次の収集は import し直す。"""
    _collect(project)
    entry = _cache_entry(project, tmp_path, "pkg/tests/test_alpha.hy")
    data = json.loads(entry.read_text())
    texts = [json.loads(text) for text in data["records"]]
    for record in texts:
        if record.get("function") == "test_slow":
            record["decorators"] = [{"mark": "fast"}]
    data["records"] = [json.dumps(record) for record in texts]
    entry.write_text(json.dumps(data))
    _imports(tmp_path)
    result = project.runpytest_subprocess("-p", "no:cacheprovider", "-k", "test_slow or test_fast")
    result.assert_outcomes(errors=1, deselected=19, skipped=1)
    result.stdout.fnmatch_lines(["*記録と実物が食い違った*"])
    out = _collect(project).out
    assert "import: pkg/tests/test_alpha.hy — 記録なし" in out


def _cache_entry(project: pytest.Pytester, tmp_path: Path, relative: str) -> Path:
    """test file の今の内容の hash から、キャッシュの file の path を引く。"""
    digest = hashlib.sha256((project.path / relative).read_bytes()).hexdigest()
    return tmp_path / "items" / f"{digest}.json"


def test_one_changed_character_reads_no_stale_record(project: pytest.Pytester, tmp_path: Path) -> None:
    """source を 1 文字変えた file は古い記録を読まず、import し直して記録を書き直す(#1291 の受入 4)。"""
    _collect(project)
    old_entry = _cache_entry(project, tmp_path, "pkg/tests/test_alpha.hy")
    assert old_entry.exists()
    path = project.path / "pkg/tests/test_alpha.hy"
    path.write_text(path.read_text().replace("test-plain (assert True)", "test-plain (assert (not False))"))
    _imports(tmp_path)
    out = _collect(project).out
    assert "import: pkg/tests/test_alpha.hy — 記録なし" in out
    assert "pkg.tests.test_alpha" in _imports(tmp_path)
    assert _cache_entry(project, tmp_path, "pkg/tests/test_alpha.hy").exists()
    assert "記録から収集 4 file" in _collect(project).out


def test_a_changed_macro_provider_reads_no_stale_record(project: pytest.Pytester, tmp_path: Path) -> None:
    """macro の提供元を変えると、それを使う file は古い記録を読まない(使わない file は記録のまま・#1291 の受入 4)。"""
    _collect(project)
    helpers = project.path / "pkg/helpers.hy"
    helpers.write_text("(defmacro answer [] (+ 40 2))\n")
    _imports(tmp_path)
    out = _collect(project).out
    assert "import: pkg/tests/test_gamma.hy — macro の提供元 pkg.helpers が変わった" in out
    assert "import: pkg/tests/test_alpha.hy" not in out
    assert "pkg.tests.test_gamma" in _imports(tmp_path)


def test_a_fixture_bound_by_assignment_is_collected_from_records(project: pytest.Pytester, tmp_path: Path) -> None:
    """代入で作った module の fixture((val 名 ((pytest.fixture …) 関数)))を持つ file も記録から集める — 仮の module に
    同じ名・scope・引数の仮の fixture を置き、呼ばれた時は本物の module の fixture を呼ぶ。その実行の中で本物の module の
    import は 1 回だけ(agora-redesign #1227 の案 B・議論の席の条件 1)。L713 の前は記録だけで集めて fixture not found に
    なっていた。"""
    (project.path / "pkg/tests/test_fixture_by_val.hy").write_text(
        PRELUDE
        + """
(defn make-answer [tmp-path-factory] (if (is-not tmp-path-factory None) 42 0))
(val answer ((pytest.fixture :scope "module" :name "answer") make-answer))
(defn make-counter []
  (yield [1])
  None)
(val counter ((pytest.fixture :name "counter") make-counter))
(deftest test-uses-the-fixture [answer counter] (assert (= answer 42)) (assert (= counter [1])))
(deftest test-uses-it-again [answer] (assert (= answer 42)))
"""
    )
    first = project.runpytest_subprocess("-p", "no:cacheprovider", "-k", "uses_the_fixture or uses_it_again")
    first.assert_outcomes(passed=2, deselected=20, skipped=1)
    _imports(tmp_path)
    out = _collect(project).out
    assert "test_fixture_by_val.hy" not in "\n".join(line for line in out.splitlines() if "import:" in line)
    assert _imports(tmp_path).count("pkg.tests.test_fixture_by_val") == 0
    second = project.runpytest_subprocess("-p", "no:cacheprovider", "-k", "uses_the_fixture or uses_it_again")
    second.assert_outcomes(passed=2, deselected=20, skipped=1)
    assert _imports(tmp_path).count("pkg.tests.test_fixture_by_val") == 1


@pytest.mark.parametrize(
    "fixture_form",
    [
        '((pytest.fixture :params [1 2] :name "answer") make-answer)',
        '((pytest.fixture :autouse True :name "answer") make-answer)',
    ],
)
def test_fixtures_that_shape_collection_keep_their_file_imported(
    project: pytest.Pytester, tmp_path: Path, fixture_form: str
) -> None:
    """params / autouse つきの fixture は収集の結果を変えるので、その file は今までどおり収集で import する(条件 1)。"""
    (project.path / "pkg/tests/test_shaping_fixture.hy").write_text(
        PRELUDE
        + f"""
(defn make-answer [] 42)
(val answer {fixture_form})
(deftest test-shaping (assert True))
"""
    )
    _collect(project)
    out = _collect(project).out
    assert "import: pkg/tests/test_shaping_fixture.hy — 記録なし" in out
    assert "fixture の answer が params / autouse を持つ" in out
