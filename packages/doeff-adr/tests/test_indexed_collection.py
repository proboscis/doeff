"""記録からの収集の最適化と、通常の pytest に戻す互換性の反例(#1551)。"""

import os
import subprocess
import sys
from pathlib import Path

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
namespace = {}
exec('from doeff_adr import *', namespace)
assert {'AdrSpec', 'register_adr', 'doeff_hy'} <= namespace.keys()
from doeff_adr import AdrSpec, register_adr, adr_ids, clear_registry
from doeff_adr.registry import AdrSpec as ActualSpec
assert AdrSpec is ActualSpec
clear_registry()
register_adr('lazy', title='lazy', status='accepted')
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
    """
    )
    (nested / "test_d.hy").write_text(
        PRELUDE
        + """
        (val pytestmark (pytest.mark.usefixtures "used"))
        (deftest test-used (assert True))
    """
    )
    cold = collect(project)
    warm = collect(project)
    assert cold.ret == warm.ret == 0
    assert nodeids(warm) == nodeids(cold)
    assert "記録から収集 3 file・収集で import 1 file" in warm.stdout.str()
    assert "module の引数つきの印" in warm.stdout.str()
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


def test_fixture_registration_between_files_invalidates_closure(
    indexed_project: pytest.Pytester,
) -> None:
    project = indexed_project
    for filename in ("test_one", "test_two"):
        project.makefile(
            ".hy",
            **{
                filename: PRELUDE
                + """
            (deftest test-value [answer] (assert (= answer 42)))
        """
            },
        )
    collect(project)
    conftest = project.path / "conftest.py"
    conftest.write_text(
        conftest.read_text()
        + """
from types import ModuleType

@pytest.fixture(autouse=True)
def added():
    return 'late'

def report_hook(fixture):
    def pytest_collectreport(report):
        if report.nodeid == 'test_one.hy':
            manager = report.result[0].session._fixturemanager
            holder = ModuleType("late")
            holder.added = fixture
            manager.parsefactories(holder, nodeid="")
    return pytest_collectreport

pytest_collectreport = report_hook(added)
del added

def pytest_collection_finish(session):
    first, second = session.items
    assert 'added' not in first.fixturenames
    assert 'added' in second.fixturenames
"""
    )
    warm = collect(project)
    assert warm.ret == 0, warm.stdout.str()


def test_recollection_in_same_process_owns_new_fixture_cache(
    indexed_project: pytest.Pytester,
) -> None:
    project = indexed_project
    project.makefile(
        ".hy",
        test_repeat=PRELUDE
        + """
        (deftest test-value [answer] (assert (= answer 42)))
        (deftest test-value-again [answer] (assert (= answer 42)))
    """,
    )
    collect(project)
    for _ in range(2):
        result = project.runpytest_inprocess("-q", "-p", "no:cacheprovider")
        result.assert_outcomes(passed=2)
        assert "記録から収集 1 file・収集で import 0 file" in result.stdout.str()


def test_path_patterns_keep_symlink_external_globs_and_collected_files(
    indexed_project: pytest.Pytester,
    tmp_path: Path,
) -> None:
    project = indexed_project
    nested = project.path / "nested"
    nested.mkdir()
    source = nested / "policy.hy"
    source.write_text(PRELUDE + "(deftest test-path (assert True))\n")
    (project.path / "defadr_alias.hy").symlink_to(source)
    ini = project.path / "tox.ini"
    ini.write_text(ini.read_text().replace("test_*.hy", "nested/*.hy"))
    conftest = project.path / "conftest.py"
    conftest.write_text(
        conftest.read_text()
        + """
def pytest_collection_finish(session):
    if session.testsfailed:
        return
    from doeff_adr.pytest_plugin import session_wiring, WiringVerified
    verdict = session_wiring(session)
    assert isinstance(verdict, WiringVerified)
    assert len(verdict.executable_adrs) == 1
"""
    )
    cold, warm = collect(project), collect(project)
    assert cold.ret == warm.ret == 0
    assert cold.stdout.str().count("::test_path") == 2
    assert warm.stdout.str().count("::test_path") == 2
    external = tmp_path / "defadr_external.hy"
    external.write_text(PRELUDE + "(deftest test-external (assert True))\n")
    assert not external.is_relative_to(project.path)
    outside = project.runpytest_subprocess(
        "-c",
        str(ini),
        "--rootdir",
        str(project.path),
        str(external),
        "--collect-only",
        "-q",
        timeout=30,
    )
    assert outside.ret != 0
    assert "outside pytest root" in outside.stdout.str()


@pytest.mark.semgrep
def test_pytest_patch_guard_rejects_mutation_and_accepts_local_cache(tmp_path: Path) -> None:
    import yaml

    root = Path(__file__).resolve().parents[3]
    definitions = yaml.safe_load((root / ".semgrep.yaml").read_text())
    rule = next(
        rule
        for rule in definitions["rules"]
        if rule["id"] == "doeff-adr-no-process-wide-pytest-patching"
    )
    config = tmp_path / "guard.yaml"
    config.write_text(yaml.safe_dump({"rules": [rule]}))
    fixture = tmp_path / "doeff_adr" / "indexed_pytest.py"
    fixture.parent.mkdir()
    fixture.write_text("""
from _pytest.fixtures import FixtureManager
from _pytest.python import Function, FunctionDefinition, Module
from _pytest import python
# ruleid: doeff-adr-no-process-wide-pytest-patching
FixtureManager.getfixtureclosure = replacement
# ruleid: doeff-adr-no-process-wide-pytest-patching
Function.from_parent = replacement
# ruleid: doeff-adr-no-process-wide-pytest-patching
FunctionDefinition.from_parent = replacement
# ruleid: doeff-adr-no-process-wide-pytest-patching
Module.collect = replacement
# ruleid: doeff-adr-no-process-wide-pytest-patching
python.pytest_generate_tests = replacement
# ok: doeff-adr-no-process-wide-pytest-patching
info = manager.getfixtureinfo(item, function, None)
# ok: doeff-adr-no-process-wide-pytest-patching
item = Function.from_parent(collector, name=name, fixtureinfo=info)
""")
    result = subprocess.run(
        ["semgrep", "--test", "--config", str(config), str(fixture)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "All tests passed" in result.stdout + result.stderr


def test_unknown_report_wrapper_preserves_module_additions(
    indexed_project: pytest.Pytester,
) -> None:
    project = indexed_project
    project.makefile(
        ".hy",
        test_report=PRELUDE
        + """
        (defn extra [] (assert True))
        (val EXTRA extra)
        (deftest test-original (assert True))
    """,
    )
    collect(project)
    conftest = project.path / "conftest.py"
    conftest.write_text(
        conftest.read_text()
        + """
@pytest.hookimpl(wrapper=True)
def pytest_make_collect_report(collector):
    if isinstance(collector, pytest.Module) and collector.path.suffix == '.hy':
        collector.obj.test_added = collector.obj.EXTRA
    return (yield)
"""
    )
    result = project.runpytest_subprocess("-q", "-p", "no:cacheprovider", timeout=30)
    result.assert_outcomes(passed=2)
    assert "収集で import 1 file" in result.stdout.str()


def test_lazy_runtime_import_keeps_import_errors() -> None:
    script = """
import importlib.abc
import sys
class BrokenYaml(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'yaml':
            raise ImportError('YAML unavailable')
sys.meta_path.insert(0, BrokenYaml())
import doeff_adr.pytest_plugin
try:
    from doeff_adr import AdrSpec
except ImportError as exc:
    assert str(exc) == 'YAML unavailable'
else:
    raise AssertionError('the original import error must reach the caller')
"""
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=30, check=False
    )
    assert result.returncode == 0, result.stderr
