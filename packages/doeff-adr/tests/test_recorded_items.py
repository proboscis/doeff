"""記録から収集する file の item の生成(agora-redesign #1551)— fixture の閉包の共有・parametrize・他の plugin との両立の反例。

pytest を別の process で走らせる(pytester の subprocess)。1 回目(cold)は記録が無いので収集で import し、2 回目(warm)は
記録から収集する。両方の nodeid・順序・結果が同じで、warm の収集は test module を import しないことを確かめる。
"""

from dataclasses import dataclass
from pathlib import Path

import pytest

pytest_plugins = ["pytester"]

PRELUDE = """
(require doeff-hy.macros [deftest val])
(import os pytest)
(with [log (open (get os.environ "IMPORT_LOG") "a")] (.write log (+ __name__ "\\n")))
"""

INI = """
[pytest]
doeff_adr_hy_files = pkg/tests/test_*.hy
    pkg/tests/sub/test_*.hy
doeff_adr_wiring = off
doeff_adr_items_cache = {cache}
markers =
    seen: seen by a wrapper
"""

ROOT_CONFTEST = """
import pytest


@pytest.fixture
def doeff_interpreter():
    def run_program(program, *, env=None):
        from doeff import run

        return run(program)

    return run_program
"""


@dataclass(frozen=True)
class Collected:
    """--collect-only の結果: 集めた nodeid(順序つき)と、報告の全文。"""

    nodeids: list[str]
    out: str


def _write(pytester: pytest.Pytester, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = pytester.path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


@pytest.fixture
def project(pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> pytest.Pytester:
    """pkg/tests の下に Hy の test file を置く project。pyc と記録は tmp の下へ書く。"""
    _write(pytester, {"pkg/__init__.py": "", "pkg/tests/__init__.py": "", "pkg/tests/sub/__init__.py": ""})
    pytester.makeconftest(ROOT_CONFTEST)
    pytester.makeini(INI.format(cache=tmp_path / "items"))
    monkeypatch.setenv("PYTHONPYCACHEPREFIX", str(tmp_path / "pyc"))
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "")
    monkeypatch.setenv("IMPORT_LOG", str(tmp_path / "imports.log"))
    return pytester


def _imports(tmp_path: Path) -> list[str]:
    """これまでに import された test module の名。読んだら空にする。"""
    log = tmp_path / "imports.log"
    names = log.read_text().split() if log.exists() else []
    log.write_text("")
    return names


def _collect(project: pytest.Pytester, *args: str) -> Collected:
    result = project.runpytest_subprocess("--collect-only", "-q", "-p", "no:cacheprovider", *args)
    nodeids = [line for line in result.stdout.lines if "::" in line and not line.startswith(" ")]
    return Collected(nodeids, result.stdout.str())


def _run(project: pytest.Pytester, *args: str) -> pytest.RunResult:
    return project.runpytest_subprocess("-p", "no:cacheprovider", "-rs", *args)


# ---------------------------------------------------------------------------
# 完了条件 2: fixture — 同じ conftest の下の 2 file で片方だけ module の fixture が同名を上書きする
# ---------------------------------------------------------------------------

FIXTURE_FILES = {
    "pkg/tests/conftest.py": """
import pytest

CALLS = []


@pytest.fixture
def answer():
    return "conftest"


@pytest.fixture(autouse=True)
def count_calls(request):
    with open(request.config.rootpath / "autouse.log", "a") as log:
        log.write(request.node.nodeid + "\\n")
""",
    # 片方だけ module の fixture が conftest の answer を上書きする
    "pkg/tests/test_overrides.hy": PRELUDE
    + """
(defn make-answer [] "module")
(val answer ((pytest.fixture :name "answer") make-answer))
(deftest test-module-answer [answer] (assert (= answer "module")))
(deftest test-module-answer-again [answer] (assert (= answer "module")))
""",
    "pkg/tests/test_inherits.hy": PRELUDE
    + """
(deftest test-conftest-answer [answer] (assert (= answer "conftest")))
(deftest test-no-fixture (assert True))
(deftest test-no-fixture-either (assert True))
""",
    # 下位の conftest が上書きする
    "pkg/tests/sub/conftest.py": """
import pytest


@pytest.fixture
def answer():
    return "sub"


@pytest.fixture
def added_later():
    return "added"
""",
    "pkg/tests/sub/test_lower.hy": PRELUDE
    + """
(deftest test-sub-answer [answer] (assert (= answer "sub")))
(deftest test-added-fixture [added-later] (assert (= added-later "added")))
""",
    # module の pytestmark の usefixtures は値が要るので記録できない — 今までどおり import で集める
    "pkg/tests/test_usefixtures.hy": PRELUDE
    + """
(val pytestmark (pytest.mark.usefixtures "answer"))
(deftest test-marked (assert True))
""",
}


def test_fixture_overrides_resolve_the_same_from_records(project: pytest.Pytester, tmp_path: Path) -> None:
    """module の fixture の上書き・下位 conftest の上書き・追加の fixture・autouse は、記録からの収集でも import した時と同じに解ける。"""
    _write(project, FIXTURE_FILES)
    cold = _run(project)
    cold.assert_outcomes(passed=8)
    autouse = project.path / "autouse.log"
    cold_autouse = sorted(autouse.read_text().split())
    autouse.write_text("")
    cold_ids = _collect(project).nodeids
    _imports(tmp_path)
    warm = _collect(project)
    assert warm.nodeids == cold_ids
    assert "記録から収集 3 file・収集で import 1 file" in warm.out
    assert "import: pkg/tests/test_usefixtures.hy" in warm.out
    assert _imports(tmp_path) == ["pkg.tests.test_usefixtures"]
    run = _run(project)
    run.assert_outcomes(passed=8)
    assert sorted(autouse.read_text().split()) == cold_autouse


EXTRA_GENERATE_PLUGIN = """
from _pytest.python import FunctionDefinition


def pytest_generate_tests(metafunc):
    assert isinstance(metafunc.definition, FunctionDefinition), type(metafunc.definition)
"""


def test_recollection_in_one_process_shares_nothing_between_sessions(project: pytest.Pytester) -> None:
    """同じ process で 2 度収集しても、fixture の閉包の共有と item の生成の決め方は session ごとに新しい: 1 度目は pytest 本体の
    実装だけなので parametrize の無い関数を直に作り、2 度目は他の pytest_generate_tests の実装を足したので pytest の手順を通す。
    どちらも item は同じで、fixture は解け直す。"""
    _write(project, FIXTURE_FILES)
    (project.path / "extra_generate.py").write_text(EXTRA_GENERATE_PLUGIN)
    project.syspathinsert()
    _run(project).assert_outcomes(passed=8)
    first = project.runpytest_inprocess("-p", "no:cacheprovider", "-p", "no:asyncio", "pkg/tests")
    first.assert_outcomes(passed=8)
    first.stdout.fnmatch_lines(["*直に 7 関数・pytest の手順で 0 関数*"])
    second = project.runpytest_inprocess(
        "-p", "no:cacheprovider", "-p", "no:asyncio", "-p", "extra_generate", "pkg/tests"
    )
    second.assert_outcomes(passed=8)
    second.stdout.fnmatch_lines(["*直に 0 関数・pytest の手順で 7 関数*extra_generate*"])


# ---------------------------------------------------------------------------
# 完了条件 3: parametrize — literal の直積・動的値と明示 id・間接 parametrize・個別の印
# ---------------------------------------------------------------------------

PARAMETRIZE_FILES = {
    "pkg/tests/conftest.py": """
import pytest


@pytest.fixture
def doubled(request):
    return request.param * 2


@pytest.fixture(params=["p", "q"])
def flavor(request):
    return request.param
""",
    "pkg/tests/inputs.py": """
CASES = [("one", 1), ("two", 2)]
""",
    "pkg/tests/test_product.hy": PRELUDE
    + """
(deftest test-product [x y] {:params {"x" [1 2] "y" ["a" "b"]}} (assert (in #(x y) [#(1 "a") #(1 "b") #(2 "a") #(2 "b")])))
(deftest test-fixture-params [flavor] (assert (in flavor ["p" "q"])))
(deftest test-plain (assert True))
""",
    "pkg/tests/test_dynamic_ids.hy": PRELUDE
    + """
(import pkg.tests.inputs [CASES])
(deftest test-dynamic [case] {:params {"case" (lfor c CASES (pytest.param c :id (get c 0)))}}
  (assert (= (len case) 2)))
""",
    # 間接 parametrize と個別の印は記録できない形 — 今までどおり収集で import する
    "pkg/tests/test_unrecordable.hy": PRELUDE
    + """
(defn [(pytest.mark.parametrize "doubled" [1 2] :indirect True)] test-indirect [doubled]
  (assert (in doubled [2 4])))
(deftest test-marked-param [v] {:params {"v" [1 (pytest.param 2 :marks pytest.mark.skip)]}} (assert (= v 1)))
""",
}


def test_parametrize_ids_and_counts_match_the_imported_collection(project: pytest.Pytester, tmp_path: Path) -> None:
    """literal の直積・fixture の params・動的値と明示 id は記録から同じ id と本数で集まり、間接 parametrize と
    個別の印を持つ file は今までどおり import で集める。"""
    _write(project, PARAMETRIZE_FILES)
    cold = _run(project)
    cold.assert_outcomes(passed=12, skipped=1)
    cold_ids = _collect(project).nodeids
    assert "pkg/tests/test_product.hy::test_product[a-1]" in cold_ids
    assert "pkg/tests/test_product.hy::test_fixture_params[p]" in cold_ids
    assert "pkg/tests/test_dynamic_ids.hy::test_dynamic[one]" in cold_ids
    assert "pkg/tests/test_unrecordable.hy::test_indirect[1]" in cold_ids
    assert "pkg/tests/test_unrecordable.hy::test_marked_param[2]" in cold_ids
    _imports(tmp_path)
    warm = _collect(project)
    assert warm.nodeids == cold_ids
    assert "記録から収集 2 file・収集で import 1 file" in warm.out
    assert _imports(tmp_path) == ["pkg.tests.test_unrecordable"]
    run = _run(project)
    run.assert_outcomes(passed=12, skipped=1)


# ---------------------------------------------------------------------------
# 完了条件 4: plugin — FunctionDefinition を求める pytest_generate_tests・item に情報を付ける makeitem の wrapper・item を足す impl
# ---------------------------------------------------------------------------

PLUGIN_CONFTEST = """
import pytest
from _pytest.python import FunctionDefinition


def pytest_generate_tests(metafunc):
    assert isinstance(metafunc.definition, FunctionDefinition), type(metafunc.definition)
    if "extra" in metafunc.fixturenames:
        metafunc.parametrize("extra", [1, 2])


@pytest.hookimpl(specname="pytest_pycollect_makeitem", hookwrapper=True)
def pytest_mark_every_item_seen(collector, name, obj):
    outcome = yield
    result = outcome.get_result()
    nodes = result if isinstance(result, list) else [] if result is None else [result]
    for node in nodes:
        node.add_marker(pytest.mark.seen)


@pytest.hookimpl(specname="pytest_pycollect_makeitem")
def pytest_add_an_item_next_to_test_host(collector, name, obj):
    if name == "test_host":
        return [
            *collector._genfunctions(name, obj),
            pytest.Function.from_parent(collector, name="test_added_by_plugin", callobj=lambda: None),
        ]
    return None
"""

PLUGIN_FILES = {
    "pkg/tests/test_plugin.hy": PRELUDE
    + """
(deftest test-extra [extra] (assert (in extra [1 2])))
(deftest test-host (assert True))
(deftest test-plain (assert True))
""",
}


def test_plugins_that_need_function_definitions_keep_the_standard_path(project: pytest.Pytester, tmp_path: Path) -> None:
    """FunctionDefinition を求めて値を足す pytest_generate_tests・item に印を付ける makeitem の wrapper・item を足す makeitem の
    実装は、記録からの収集でも全部呼ばれる(hook を飛ばさない)。他の実装があるので item の生成は pytest の手順を通す。"""
    _write(project, PLUGIN_FILES)
    (project.path / "pkg/tests/conftest.py").write_text(PLUGIN_CONFTEST)
    cold = _run(project, "-m", "seen")
    cold.assert_outcomes(passed=5)
    cold_ids = _collect(project).nodeids
    assert cold_ids == [
        "pkg/tests/test_plugin.hy::test_extra[1]",
        "pkg/tests/test_plugin.hy::test_extra[2]",
        "pkg/tests/test_plugin.hy::test_host",
        "pkg/tests/test_plugin.hy::test_added_by_plugin",
        "pkg/tests/test_plugin.hy::test_plain",
    ]
    _imports(tmp_path)
    warm = _collect(project)
    assert warm.nodeids == cold_ids
    assert "記録から収集 1 file" in warm.out
    assert "直に 0 関数・pytest の手順で 3 関数" in warm.out
    assert "pytest_generate_tests の他の実装: " in warm.out
    assert "conftest" in warm.out
    assert _imports(tmp_path) == []
    _run(project, "-m", "seen").assert_outcomes(passed=5)


def test_without_foreign_generate_tests_plain_functions_are_made_directly(project: pytest.Pytester, tmp_path: Path) -> None:
    """pytest_generate_tests の実装が pytest 本体だけなら、parametrize の無い関数は FunctionDefinition と Metafunc を作らずに
    Function を 1 つ直に作る。parametrize のある関数と、params つきの fixture を使う関数は pytest の手順を通す。"""
    _write(project, PARAMETRIZE_FILES)
    _run(project, "-p", "no:asyncio").assert_outcomes(passed=12, skipped=1)
    cold_ids = _collect(project, "-p", "no:asyncio").nodeids
    _imports(tmp_path)
    warm = _collect(project, "-p", "no:asyncio")
    assert warm.nodeids == cold_ids
    assert "直に 1 関数・pytest の手順で 3 関数" in warm.out
    assert "他の実装" not in warm.out
    _run(project, "-p", "no:asyncio").assert_outcomes(passed=12, skipped=1)


# ---------------------------------------------------------------------------
# 完了条件 5 の一部: 同名の記録の上書き(Python の module と同じく最後の値・最初の位置)
# ---------------------------------------------------------------------------


def test_a_name_recorded_twice_keeps_the_last_value_at_the_first_position(project: pytest.Pytester, tmp_path: Path) -> None:
    _write(
        project,
        {
            "pkg/tests/test_twice.hy": PRELUDE
            + """
(deftest test-a (assert True))
(deftest test-dup (assert False))
(deftest test-b (assert True))
(deftest test-dup (assert True))
""",
        },
    )
    cold = _run(project)
    cold.assert_outcomes(passed=3)
    cold_ids = _collect(project).nodeids
    assert cold_ids == [
        "pkg/tests/test_twice.hy::test_a",
        "pkg/tests/test_twice.hy::test_dup",
        "pkg/tests/test_twice.hy::test_b",
    ]
    _imports(tmp_path)
    warm = _collect(project)
    assert warm.nodeids == cold_ids
    assert _imports(tmp_path) == []
    _run(project).assert_outcomes(passed=3)
