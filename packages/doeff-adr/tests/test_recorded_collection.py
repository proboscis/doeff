"""記録から item を作る近道(``indexed_pytest`` — agora-redesign #1551)が、pytest の通常の経路と同じ item を作ることの反例。

どの test も、1 回目(記録が無いので収集で import する = pytest の通常の Module の収集)と 2 回目以降(記録から収集する =
近道)を別の process で走らせ、nodeid の並び・結果の数・検の中で確かめる値が同じことを見る。検の中で fixture の値や
``request.fixturenames`` を確かめるので、fixture の解決が兄弟の item から漏れれば 2 回目が赤になる。

キャッシュと pyc の置き場は tmp の下(test_lazy_collection と同じ)。
"""

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

pytest_plugins = ["pytester"]

PRELUDE = "(require doeff-hy.macros [deftest val])\n(import pytest)\n"

CONFTEST = """
import pytest


@pytest.fixture
def doeff_interpreter():
    def run_program(program, *, env=None):
        from doeff import run

        return run(program)

    return run_program


@pytest.fixture
def answer():
    return 42


@pytest.fixture
def auto_seen():
    return "auto"


@pytest.fixture(autouse=True)
def autoused(auto_seen):
    return auto_seen


@pytest.fixture
def ini_used():
    return "ini"


@pytest.fixture(params=[10, 20], ids=["ten", "twenty"])
def base(request):
    return request.param


"""

REGISTRATION_CONFTEST = """
import pytest
class _Registered:
    @pytest.fixture(name="answer")
    def registered_answer(self):
        return 99


def pytest_collectstart(collector):
    # 収集の前に module 1 つだけへ fixture を追加で登録する(fixture の追加登録の反例)。
    if isinstance(collector, pytest.Module) and collector.path.name == "test_registered.hy":
        collector.session._fixturemanager.parsefactories(holder=_Registered(), node=collector)
"""

FILES = {
    "pkg/__init__.py": "",
    # 同じ conftest の下の 2 file — 片方だけ module の fixture で answer を 41 に上書きする
    "pkg/registered/__init__.py": "",
    "pkg/registered/conftest.py": REGISTRATION_CONFTEST,
    "pkg/test_fixture_a.hy": PRELUDE
    + """
(defn local-answer [] 41)
(val answer ((pytest.fixture :name "answer") local-answer))
(deftest test-local-answer [answer] (assert (= answer 41)))
(deftest test-local-answer-again [answer] (assert (= answer 41)))
(deftest test-local-answer-with-request [answer request] (assert (= answer 41)) (assert (in "autoused" request.fixturenames)))
""",
    "pkg/test_fixture_b.hy": PRELUDE
    + """
(deftest test-conftest-answer [answer] (assert (= answer 42)))
(deftest test-conftest-answer-again [answer] (assert (= answer 42)))
""",
    # 下位の conftest の上書き
    "pkg/sub/__init__.py": "",
    "pkg/sub/conftest.py": "import pytest\n\n\n@pytest.fixture\ndef answer():\n    return 43\n",
    "pkg/sub/test_fixture_c.hy": PRELUDE
    + """
(deftest test-sub-answer [answer] (assert (= answer 43)))
(deftest test-sub-answer-again [answer] (assert (= answer 43)))
""",
    # 収集の前に plugin が module へ追加で登録した fixture
    "pkg/registered/test_registered.hy": PRELUDE
    + """
(deftest test-registered-answer [answer] (assert (= answer 99)))
(deftest test-registered-answer-again [answer] (assert (= answer 99)))
""",
    # autouse・ini の usefixtures・module の usefixtures(module の usefixtures の呼び出しは記録できないので import の経路)
    "pkg/test_used.hy": PRELUDE
    + """
(deftest test-autouse-and-ini [request]
  (assert (in "autoused" request.fixturenames))
  (assert (in "auto_seen" request.fixturenames))
  (assert (in "ini_used" request.fixturenames)))
(deftest test-autouse-and-ini-again [request]
  (assert (in "ini_used" request.fixturenames)))
(deftest test-bare-usefixtures [request] {:marks ["usefixtures"]} (assert (in "ini_used" request.fixturenames)))
(deftest test-bare-usefixtures-again [request] {:marks ["usefixtures"]} (assert (in "ini_used" request.fixturenames)))
""",
    "pkg/test_module_usefixtures.hy": PRELUDE
    + """
(val pytestmark (pytest.mark.usefixtures "auto_seen"))
(deftest test-module-usefixtures [request] (assert (in "auto_seen" request.fixturenames)))
""",
}

PARAM_FILES = {
    "pkg/__init__.py": "",
    # literal の直積・同じ形の parametrize の兄弟(直の parametrize の書き換えが次の item へ漏れない)
    "pkg/test_product.hy": PRELUDE
    + """
(deftest test-product [left right] {:params {"left" [1 2] "right" [3 4]}} (assert (in (+ left right) [4 5 6])))
(deftest test-product-again [left right] {:params {"left" [5] "right" [6]}} (assert (= (+ left right) 11)))
(deftest test-plain [answer] (assert (= answer 42)))
""",
    # 動的な値と明示の id(記録は import の後に実値で補って保存する)
    "pkg/test_recorded_params.hy": PRELUDE
    + """
(val VALUES [(pytest.param 21 :id "half") (pytest.param 42 :id "whole")])
(deftest test-recorded-param-values [value] {:params {"value" VALUES}} (assert (in value [21 42])))
""",
    # 個別の印(skip)を持つ値 — 記録で説明できないので毎回 import する
    "pkg/test_marked_params.hy": PRELUDE
    + """
(val VALUES [(pytest.param 21 :id "half") (pytest.param 42 :id "whole") (pytest.param 0 :id "skip" :marks pytest.mark.skip)])
(deftest test-param-values [value] {:params {"value" VALUES}} (assert (in value [21 42])))
""",
    # 間接の parametrize(params を持つ conftest の fixture)と、直の parametrize との組
    "pkg/test_indirect.hy": PRELUDE
    + """
(deftest test-indirect [base] (assert (in base [10 20])))
(deftest test-indirect-again [base] (assert (in base [10 20])))
(deftest test-indirect-and-direct [base value] {:params {"value" [1 2]}} (assert (in (+ base value) [11 12 21 22])))
""",
}

PLUGIN_FILES = {
    "pkg/__init__.py": "",
    "pkg/test_plain.hy": PRELUDE + "(deftest test-plain [answer] (assert (= answer 42)))\n",
    "pkg/custom/__init__.py": "",
    # 未知の plugin: FunctionDefinition の型を求めて値を足す pytest_generate_tests と、item に情報を付け・module に
    # item を足す pytest_pycollect_makeitem の包み。makeitem に渡った名を file に書く(汎用の収集なら関数でない名も渡る)
    "pkg/custom/conftest.py": """
import os

import pytest
from _pytest.python import FunctionDefinition


def pytest_generate_tests(metafunc):
    assert isinstance(metafunc.definition, FunctionDefinition)
    if "extra" in metafunc.fixturenames:
        metafunc.parametrize("extra", [3, 5], ids=["three", "five"])


class ExtraItem(pytest.Item):
    def runtest(self):
        pass


@pytest.hookimpl(wrapper=True)
def pytest_pycollect_makeitem(collector, name, obj):
    with open(os.environ["MAKEITEM_LOG"], "a") as log:
        log.write(name + "\\n")
    result = yield
    if isinstance(result, list):
        for item in result:
            if isinstance(item, pytest.Function):
                item.user_properties.append(("plugin_seen", True))
        if name == "test_generated":
            result = [*result, ExtraItem.from_parent(collector, name="extra_item")]
    return result


def pytest_runtest_setup(item):
    if isinstance(item, pytest.Function):
        assert ("plugin_seen", True) in item.user_properties
""",
    "pkg/custom/test_plugin.hy": PRELUDE
    + """
(defn local-answer [] 7)
(val answer ((pytest.fixture :name "answer") local-answer))
(deftest test-generated [extra] (assert (in extra [3 5])))
(deftest test-local [answer] (assert (= answer 7)))
""",
}

# 同じ名の deftest が 2 つ — Python の module と同じく、最初の位置に最後の定義が残る(item は 1 本)
DUPLICATE_FILES = {
    "pkg/__init__.py": "",
    "pkg/test_duplicate.hy": PRELUDE
    + """
(deftest test-dup (assert False))
(deftest test-between (assert True))
(deftest test-dup [answer] (assert (= answer 42)))
""",
}


def _write_project(
    pytester: pytest.Pytester,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    files: dict[str, str],
) -> None:
    """file と conftest・ini を置く。pyc と記録は tmp の下の置き場へ書く。"""
    for name, text in files.items():
        path = pytester.path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    pytester.makeconftest(CONFTEST)
    pytester.makeini(
        f"""
        [pytest]
        doeff_adr_hy_files = pkg/test_*.hy
            pkg/sub/test_*.hy
            pkg/custom/test_*.hy
            pkg/registered/test_*.hy
        doeff_adr_wiring = off
        doeff_adr_items_cache = {tmp_path / "items"}
        usefixtures = ini_used
        """
    )
    monkeypatch.setenv("PYTHONPYCACHEPREFIX", str(tmp_path / "pyc"))
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "")
    monkeypatch.setenv("MAKEITEM_LOG", str(tmp_path / "makeitem.log"))


@dataclass(frozen=True)
class Run:
    """1 回の走りの結果: 集めた nodeid の並び・結果の数・報告の全文。"""

    nodeids: list[str]
    outcomes: dict[str, int]
    out: str


_VERBOSE_OUTCOMES = frozenset({"PASSED", "FAILED", "SKIPPED", "ERROR", "XFAIL", "XPASS"})


def _run(pytester: pytest.Pytester, *args: str) -> Run:
    """別の process で走らせ、nodeid と結果(-v の行 ``<nodeid> <結果> [..%]``)と結果の数を読む。"""
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider", "-v", *args, timeout=30)
    rows = [line.split() for line in result.stdout.lines]
    nodeids = [
        f"{row[0]} {row[1]}"
        for row in rows
        if len(row) >= 2 and "::" in row[0] and row[1] in _VERBOSE_OUTCOMES
    ]
    return Run(nodeids, result.parseoutcomes(), result.stdout.str())


def _collect(pytester: pytest.Pytester, *args: str) -> list[str]:
    """別の process で --collect-only -q の nodeid の並び(順を含む)を読む。"""
    result = pytester.runpytest_subprocess(
        "--collect-only", "-q", "-p", "no:cacheprovider", *args, timeout=30
    )
    return [line for line in result.stdout.lines if "::" in line and not line.startswith(" ")]


@dataclass(frozen=True)
class ColdAndWarm:
    """import して収集した走り(cold)と記録から収集した走り(warm)、それぞれの収集の nodeid の並び(順を含む)。"""

    cold: Run
    warm: Run
    cold_ids: list[str]
    warm_ids: list[str]


def _cold_then_warm(pytester: pytest.Pytester) -> ColdAndWarm:
    """1 回目(記録が無いので import して収集)と 2 回目(記録から収集)を走らせ、収集の nodeid の並びも
    記録の無い置き場(import の経路)と記録のある置き場(近道)で取る。"""
    cold = _run(pytester)
    warm = _run(pytester)
    cold_ids = _collect(
        pytester, "-o", "doeff_adr_items_cache=" + str(pytester.path / "no-records")
    )
    warm_ids = _collect(pytester)
    return ColdAndWarm(cold, warm, cold_ids, warm_ids)


def test_fixture_resolution_is_not_shared_across_modules_conftests_or_registrations(
    pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """module の fixture の上書き(片方の file だけ)・下位の conftest の上書き・収集の前の追加登録・autouse・ini と module の
    usefixtures・引数の無い usefixtures の印 — どれも記録からの収集で、import した収集と同じ fixture に解決する。"""
    _write_project(pytester, tmp_path, monkeypatch, FILES)
    runs = _cold_then_warm(pytester)
    cold, warm, cold_ids, warm_ids = runs.cold, runs.warm, runs.cold_ids, runs.warm_ids
    assert cold.outcomes.get("passed") == 14, cold.out
    assert warm.outcomes == cold.outcomes, warm.out
    assert warm.nodeids == cold.nodeids
    assert warm_ids == cold_ids
    assert "記録から収集 4 file・収集で import 2 file" in warm.out
    assert "generic:" not in warm.out
    # 引数の無い usefixtures の警告は item ごと(近道でも兄弟と共有して 1 本に減らさない)
    assert warm.outcomes.get("warnings") == cold.outcomes.get("warnings")


def test_recollection_in_the_same_process_resolves_fixtures_afresh(
    pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同じ process の中で pytest を 3 回走らせる(1 回目 import・2 回目と 3 回目は記録から)。どの回も同じ結果になる —
    前の回の fixture の解決や登録を次の回へ持ち越さない。"""
    _write_project(pytester, tmp_path, monkeypatch, FILES)
    script = pytester.makepyfile(
        rerun="""
        import json
        import sys

        import pytest


        class Recorder:
            def __init__(self):
                self.rows = []

            def pytest_runtest_logreport(self, report):
                if report.when == "call" or report.outcome != "passed":
                    self.rows.append([report.nodeid, report.when, report.outcome])


        runs = []
        for _ in range(3):
            recorder = Recorder()
            code = pytest.main(["-p", "no:cacheprovider", "-q", "pkg"], plugins=[recorder])
            runs.append([int(code), sorted(recorder.rows)])
        print("RUNS=" + json.dumps(runs))
        """
    )
    result = pytester.runpython(script)
    line = next(line for line in result.stdout.lines if line.startswith("RUNS="))
    runs = json.loads(line.removeprefix("RUNS="))
    assert [code for code, _ in runs] == [0, 0, 0], result.stdout.str()
    assert runs[1][1] == runs[0][1]
    assert runs[2][1] == runs[0][1]
    assert len(runs[0][1]) == 14


def test_parametrize_ids_counts_values_and_skips_match_the_imported_collection(
    pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """literal の直積・動的な値と明示の id・間接の parametrize と直の組・個別の印を持つので import する file — nodeid・
    数・値(検の中で確かめる)・skip が、import した収集と同じ。"""
    _write_project(pytester, tmp_path, monkeypatch, PARAM_FILES)
    runs = _cold_then_warm(pytester)
    cold, warm, cold_ids, warm_ids = runs.cold, runs.warm, runs.cold_ids, runs.warm_ids
    assert cold.outcomes == {"passed": 18, "skipped": 1}, cold.out
    assert warm.outcomes == cold.outcomes, warm.out
    assert warm.nodeids == cold.nodeids
    assert warm_ids == cold_ids
    assert "pkg/test_product.hy::test_product[3-1]" in warm_ids
    assert "pkg/test_recorded_params.hy::test_recorded_param_values[half]" in warm_ids
    assert "pkg/test_marked_params.hy::test_param_values[skip] SKIPPED" in warm.nodeids
    assert sum("test_indirect_and_direct[" in nodeid for nodeid in warm_ids) == 4
    assert "記録から収集 3 file・収集で import 1 file" in warm.out
    assert "import: pkg/test_marked_params.hy" in warm.out


def test_unknown_collection_plugins_import_the_real_module_before_collection(
    pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FunctionDefinition の型を求めて値を足す ``pytest_generate_tests`` と、item に情報を付け module に item を足す
    ``pytest_pycollect_makeitem`` の包みがある dir の file は、本物の module を import して pytest の通常の収集に回る — makeitem は
    関数でない名(module の fixture の名)にも呼ばれ、足した item も欠けない。その外の file は近道のまま。"""
    _write_project(pytester, tmp_path, monkeypatch, PLUGIN_FILES)
    runs = _cold_then_warm(pytester)
    cold, warm, cold_ids, warm_ids = runs.cold, runs.warm, runs.cold_ids, runs.warm_ids
    assert cold.outcomes == {"passed": 5}, cold.out
    assert warm.outcomes == cold.outcomes, warm.out
    assert warm.nodeids == cold.nodeids
    assert warm_ids == cold_ids
    assert "pkg/custom/test_plugin.hy::extra_item" in warm_ids
    assert "pkg/custom/test_plugin.hy::test_generated[three]" in warm_ids
    assert "記録から収集 1 file・収集で import 1 file" in warm.out
    assert "未知の収集 hook" in warm.out
    assert "import: pkg/test_plain.hy" not in warm.out
    log = tmp_path / "makeitem.log"
    log.write_text("")
    _collect(pytester)
    assert "answer" in log.read_text().split()


def test_a_name_recorded_twice_becomes_one_item_at_its_first_position_with_its_last_body(
    pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同じ名の deftest が 2 つある file は、Python の module と同じく最初の位置に最後の定義の item が 1 本だけ並ぶ
    (試作の 1 つ目は記録を 2 重に集めて 1 本多かった — agora-redesign #1460 の反例)。"""
    _write_project(pytester, tmp_path, monkeypatch, DUPLICATE_FILES)
    runs = _cold_then_warm(pytester)
    cold, warm, cold_ids, warm_ids = runs.cold, runs.warm, runs.cold_ids, runs.warm_ids
    assert cold.outcomes == {"passed": 2}, cold.out
    assert warm.outcomes == cold.outcomes, warm.out
    assert (
        warm_ids
        == cold_ids
        == ["pkg/test_duplicate.hy::test_dup", "pkg/test_duplicate.hy::test_between"]
    )
    assert "記録から収集 1 file" in warm.out


def test_fixture_alias_keeps_the_attribute_used_by_pytest(
    pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = {
        "pkg/__init__.py": "",
        "pkg/test_alias.hy": PRELUDE
        + """
(defn original [] 73)
(val exposed (pytest.fixture original))
(deftest test-alias [exposed] (assert (= exposed 73)))
(deftest test-alias-again [exposed] (assert (= exposed 73)))
""",
    }
    _write_project(pytester, tmp_path, monkeypatch, files)
    runs = _cold_then_warm(pytester)
    assert runs.cold.outcomes == {"passed": 2}, runs.cold.out
    assert runs.warm.outcomes == runs.cold.outcomes, runs.warm.out
    assert runs.warm_ids == runs.cold_ids
    assert "記録から収集 1 file・収集で import 0 file" in runs.warm.out


def test_item_specific_fixture_registration_does_not_reuse_the_sibling_closure(
    pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = {
        "pkg/__init__.py": "",
        "pkg/test_first.hy": PRELUDE + "(deftest test-value [answer] (assert (= answer 42)))\n",
        "pkg/test_second.hy": PRELUDE + "(deftest test-value [answer] (assert (= answer 99)))\n",
    }
    _write_project(pytester, tmp_path, monkeypatch, files)
    conftest = pytester.path / "conftest.py"
    conftest.write_text(
        conftest.read_text()
        + """
class Registered:
    @pytest.fixture(name="answer")
    def answer_for_second(self):
        return 99

@pytest.hookimpl(wrapper=True)
def pytest_collect_file(file_path, parent):
    collectors = yield
    for collector in collectors:
        if isinstance(collector, pytest.Module) and collector.path.name == "test_second.hy":
            collector.session._fixturemanager.parsefactories(holder=Registered(), node=collector)
    return collectors
"""
    )
    runs = _cold_then_warm(pytester)
    assert runs.cold.outcomes == {"passed": 2}, runs.cold.out
    assert runs.warm.outcomes == runs.cold.outcomes, runs.warm.out
    assert runs.warm_ids == runs.cold_ids
    assert "記録から収集 2 file・収集で import 0 file" in runs.warm.out
