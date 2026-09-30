"""記録からの収集の最適化と、通常の pytest に戻す互換性の反例（#1551）。"""

import os
import subprocess
import sys

import pytest

pytest_plugins = ["pytester"]

PRELUDE = """
(require doeff-hy.macros [deftest val])
(import pytest)
"""


@pytest.fixture
def indexed_project(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch) -> pytest.Pytester:
    pytester.makeini("""
        [pytest]
        doeff_adr_hy_files = test_*.hy
        doeff_adr_items_cache = items
        doeff_adr_wiring = off
    """)
    pytester.makeconftest("""
        import pytest
        from doeff import run
        @pytest.fixture
        def doeff_interpreter():
            return run
        @pytest.fixture
        def answer():
            return 42
    """)
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    return pytester


def collect(project: pytest.Pytester) -> pytest.RunResult:
    return project.runpytest_subprocess(
        "--collect-only", "-q", "-p", "no:cacheprovider", timeout=30
    )


def nodeids(result: pytest.RunResult) -> list[str]:
    return [line for line in result.stdout.lines if line.startswith("test_") and "::" in line]


def test_plugin_import_defers_runtime_api_and_preserves_exports() -> None:
    script = """
import sys
import doeff_adr.pytest_plugin
assert 'doeff_hy' in sys.modules
assert 'doeff_adr.registry' not in sys.modules
assert 'yaml' not in sys.modules
import doeff_adr
from doeff_adr import AdrSpec, register_adr, adr_ids, clear_registry
from doeff_adr.registry import AdrSpec as ActualSpec
assert AdrSpec is ActualSpec
clear_registry()
register_adr('lazy', 'lazy', 'accepted')
assert adr_ids() == ['lazy']
assert doeff_adr.register_adr is register_adr
try:
    doeff_adr.unknown_api
except AttributeError:
    pass
else:
    raise AssertionError('unknown API must fail')
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        env=dict(os.environ),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_duplicate_records_keep_last_value_and_first_position(
    indexed_project: pytest.Pytester,
) -> None:
    project = indexed_project
    project.makefile(
        ".hy",
        test_duplicates=PRELUDE
        + """
        (deftest test-repeat [x] {:params {"x" [1]}} (assert False))
        (deftest test-middle (assert True))
        (deftest test-repeat [x] {:params {"x" [2 3]}} (assert (in x [2 3])))
    """,
    )
    cold = collect(project)
    warm = collect(project)
    assert nodeids(warm) == nodeids(cold)
    assert [item.split("::")[-1] for item in nodeids(warm)] == [
        "test_repeat[2]",
        "test_repeat[3]",
        "test_middle",
    ]
    assert "記録から収集 1 file・収集で import 0 file" in warm.stdout.str()
    project.runpytest_subprocess("-q", "-p", "no:cacheprovider").assert_outcomes(passed=3)


def test_unknown_hooks_import_real_module_and_keep_added_items(
    indexed_project: pytest.Pytester,
) -> None:
    project = indexed_project
    conftest = project.path / "conftest.py"
    conftest.write_text(
        conftest.read_text()
        + """
from _pytest.python import FunctionDefinition

@pytest.hookimpl(wrapper=True)
def pytest_pycollect_makeitem(collector, name, obj):
    result = yield
    if isinstance(result, list):
        for item in result:
            item.user_properties.append(('wrapper', 'present'))
    if name == 'EXTRA':
        return [pytest.Function.from_parent(collector, name='test_added', callobj=obj)]
    return result

def pytest_generate_tests(metafunc):
    assert isinstance(metafunc.definition, FunctionDefinition)
    assert metafunc.module.EXTRA is not None
    if 'value' in metafunc.fixturenames:
        metafunc.parametrize('value', [10, 20], ids=['ten', 'twenty'])

def pytest_collection_finish(session):
    for item in session.items:
        if item.name != 'test_added':
            assert ('wrapper', 'present') in item.user_properties
"""
    )
    project.makefile(
        ".hy",
        test_plugin=PRELUDE
        + """
        (defn extra [] (assert True))
        (val EXTRA extra)
        (deftest test-values [value] (assert (in value [10 20])))
    """,
    )
    cold = collect(project)
    warm = collect(project)
    assert cold.ret == warm.ret == 0
    assert nodeids(cold) == nodeids(warm)
    assert len(nodeids(warm)) == 3
    assert "収集で import 1 file" in warm.stdout.str()
    project.runpytest_subprocess("-q", "-p", "no:cacheprovider").assert_outcomes(passed=3)


def test_fixture_visibility_and_nested_override(indexed_project: pytest.Pytester) -> None:
    project = indexed_project
    project.makefile(
        ".hy",
        test_a=PRELUDE
        + """
        (defn local-answer [] 7)
        (val answer ((pytest.fixture) local-answer))
        (deftest test-local [answer] (assert (= answer 7)))
        (deftest test-local-again [answer] (assert (= answer 7)))
    """,
        test_b=PRELUDE
        + """
        (deftest test-global [answer] (assert (= answer 42)))
        (deftest test-global-again [answer] (assert (= answer 42)))
    """,
    )
    nested = project.path / "nested"
    nested.mkdir()
    (nested / "conftest.py").write_text("""
import pytest
@pytest.fixture
def answer():
    return 99
@pytest.fixture(autouse=True)
def needs_answer(answer):
    assert answer == 99
@pytest.fixture
def used(answer):
    assert answer == 99
""")
    (nested / "test_c.hy").write_text(
        PRELUDE
        + """
        (deftest test-nested [answer] (assert (= answer 99)))
        (deftest test-nested-again [answer] (assert (= answer 99)))
        (defn [(pytest.mark.usefixtures "used")] test-used [] (assert True))
    """
    )
    cold = collect(project)
    warm = collect(project)
    assert cold.ret == warm.ret == 0
    assert nodeids(warm) == nodeids(cold)
    project.runpytest_subprocess("-q", "-p", "no:cacheprovider").assert_outcomes(passed=7)


def test_indirect_and_individual_marks_use_normal_import(indexed_project: pytest.Pytester) -> None:
    project = indexed_project
    conftest = project.path / "conftest.py"
    conftest.write_text(
        conftest.read_text()
        + """
@pytest.fixture
def indirect(request):
    return request.param * 2
"""
    )
    project.makefile(
        ".hy",
        test_params=PRELUDE
        + """
        (defn [(pytest.mark.parametrize "indirect" [3 4] :indirect True)] test-indirect [indirect]
          (assert (in indirect [6 8])))
        (deftest test-marks [x] {:params {"x" [(pytest.param 1 :id "kept")
            (pytest.param 2 :id "skipped" :marks pytest.mark.skip)]}}
          (assert (= x 1)))
    """,
    )
    cold = collect(project)
    warm = collect(project)
    assert cold.ret == warm.ret == 0
    assert nodeids(cold) == nodeids(warm)
    assert "収集で import 1 file" in warm.stdout.str()
    project.runpytest_subprocess("-q", "-p", "no:cacheprovider").assert_outcomes(
        passed=3, skipped=1
    )


def test_plain_items_share_closures_without_definition_nodes(
    indexed_project: pytest.Pytester,
) -> None:
    project = indexed_project
    for file in ("test_one", "test_two"):
        project.makefile(
            ".hy",
            **{
                file: PRELUDE
                + """
            (deftest test-first [answer] (assert (= answer 42)))
            (deftest test-second [answer] (assert (= answer 42)))
        """
            },
        )
    collect(project)
    conftest = project.path / "conftest.py"
    conftest.write_text(
        conftest.read_text()
        + """
import sys
from _pytest.python import FunctionDefinition
counts = {'definitions': 0, 'closures': 0}
def profile(frame, event, arg):
    if event == 'call':
        if frame.f_code.co_name == '__init__' and isinstance(frame.f_locals.get('self'), FunctionDefinition):
            counts['definitions'] += 1
        if frame.f_code.co_name == 'getfixtureclosure':
            counts['closures'] += 1

def pytest_sessionstart(session):
    sys.setprofile(profile)

def pytest_collection_finish(session):
    sys.setprofile(None)
    print('COUNTS', counts)
    assert counts == {'definitions': 0, 'closures': 1}
    assert len({id(item._fixtureinfo.names_closure) for item in session.items}) == 4
    assert len({id(item._fixtureinfo.name2fixturedefs) for item in session.items}) == 4
"""
    )
    warm = collect(project)
    assert warm.ret == 0, warm.stdout.str()
