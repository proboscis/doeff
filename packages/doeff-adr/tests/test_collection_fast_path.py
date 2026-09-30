"""記録の高速収集と、pytest の通常収集との互換性。"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest_plugins = ["pytester"]

PRELUDE = "(require doeff-hy.macros [deftest])\n(import pytest)\n"


@pytest.fixture
def project(pytester: pytest.Pytester) -> pytest.Pytester:
    pytester.makeini(
        "[pytest]\ndoeff_adr_hy_files = test_*.hy\n"
        "doeff_adr_wiring = off\ndoeff_adr_items_cache = items\n",
    )
    return pytester


def write_hy(project: pytest.Pytester, name: str, body: str) -> None:
    path = project.path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(PRELUDE + body)


def collect(project: pytest.Pytester) -> list[str]:
    result = project.runpytest_subprocess("--collect-only", "-q")
    assert result.ret == 0
    return [line for line in result.stdout.lines if "::" in line and not line.startswith(" ")]


def test_plugin_import_defers_registry_and_preserves_exports() -> None:
    result = subprocess.run(
        [sys.executable, "-c", """
import sys
import doeff_adr.pytest_plugin
assert 'doeff_adr.registry' not in sys.modules
assert 'yaml' not in sys.modules
import doeff_adr
from doeff_adr import AdrSpec, register_adr
from doeff_adr.registry import AdrSpec as RealAdrSpec, register_adr as real_register
assert AdrSpec is RealAdrSpec
assert register_adr is real_register
try:
    doeff_adr.no_such_export
except AttributeError:
    pass
else:
    raise AssertionError('unknown export must fail')
"""],
        capture_output=True, text=True, timeout=15, check=False,
    )
    assert result.returncode == 0, result.stderr


def test_nonparametrized_records_skip_function_definition(project: pytest.Pytester) -> None:
    write_hy(project, "test_plain.hy", "(deftest test-one (assert True))\n")
    collect(project)
    project.makeconftest("""
        from _pytest.python import FunctionDefinition
        def pytest_configure(config):
            def forbidden(*args, **kwargs):
                raise AssertionError('unnecessary FunctionDefinition')
            FunctionDefinition.from_parent = forbidden
    """)
    assert collect(project) == ["test_plain.hy::test_one"]


def test_fixture_visibility_and_recollection(project: pytest.Pytester) -> None:
    project.makeconftest("""
        import pytest
        @pytest.fixture
        def value(): return 'root'
        @pytest.fixture(autouse=True)
        def automatic(request): request.node.user_properties.append(('auto', True))
        @pytest.fixture
        def requested(request): request.node.user_properties.append(('used', True))
    """)
    write_hy(project, "test_a.hy", """
(defn [pytest.fixture] value [] "module")
(deftest test-value [value] (assert (= value "module")))
""")
    write_hy(project, "test_b.hy", """
(deftest test-value [value] (assert (= value "root")))
(deftest test-another [value] (assert (= value "root")))
""")
    write_hy(project, "nested/test_c.hy", """
(deftest test-value [value] (assert (= value "nested")))
""")
    (project.path / "nested/conftest.py").write_text(
        "import pytest\n@pytest.fixture\ndef value(): return 'nested'\n",
    )
    cold = collect(project)
    assert collect(project) == cold
    project.runpytest_subprocess("-q", "-o", "usefixtures=requested").assert_outcomes(passed=4)
    # 同じ process 内の pytest.main でも前の session の FixtureDef を再利用しない。
    project.runpytest_inprocess("-q", "-o", "usefixtures=requested").assert_outcomes(passed=4)
    project.runpytest_inprocess("-q", "-o", "usefixtures=requested").assert_outcomes(passed=4)


def test_parametrize_values_ids_marks_and_duplicate_names(project: pytest.Pytester) -> None:
    write_hy(project, "test_matrix.hy", """
(deftest test-product [x y] {:params {"x" [1 2] "y" [3 4]}}
  (assert (in x [1 2])) (assert (in y [3 4])))
(deftest test-replaced (assert False))
(deftest test-last (assert True))
(deftest test-replaced (assert True))
(setv values [(pytest.param 7 :id "seven") (pytest.param 8 :id "eight")])
(deftest test-dynamic [v] {:params {"v" values}} (assert (in v [7 8])))
""")
    write_hy(project, "test_imported.hy", """
(defn [pytest.fixture] value [request] (* request.param 2))
(defn [(pytest.mark.parametrize "value" [3] :indirect True)] test-indirect [value]
  (assert (= value 6)))
(setv values [(pytest.param 1 :marks pytest.mark.skip :id "skip") 2])
(deftest test-marked [v] {:params {"v" values}} (assert (= v 2)))
""")
    cold = collect(project)
    assert collect(project) == cold
    assert cold.index("test_matrix.hy::test_replaced") < cold.index("test_matrix.hy::test_last")
    project.runpytest_subprocess("-q").assert_outcomes(passed=10, skipped=1)


def test_unknown_hooks_keep_definition_wrapper_and_added_items(project: pytest.Pytester) -> None:
    write_hy(project, "test_hooks.hy", "(deftest test-generated [value] (assert (= value 9)))\n")
    project.makeconftest("""
        import pytest
        from _pytest.python import FunctionDefinition
        def pytest_generate_tests(metafunc):
            assert isinstance(metafunc.definition, FunctionDefinition)
            if 'value' in metafunc.fixturenames:
                metafunc.parametrize('value', [9], ids=['plugin'])
        @pytest.hookimpl(wrapper=True)
        def pytest_pycollect_makeitem(collector, name, obj):
            result = yield
            for item in result if isinstance(result, list) else []:
                item.user_properties.append(('wrapped', True))
            return result
        def pytest_collectstart(collector):
            if isinstance(collector, pytest.Module):
                def test_added(): pass
                collector.obj.test_added = test_added
        def pytest_collection_modifyitems(items):
            assert all(('wrapped', True) in item.user_properties for item in items)
    """)
    cold = collect(project)
    assert cold == ["test_hooks.hy::test_generated[plugin]", "test_hooks.hy::test_added"]
    assert collect(project) == cold
    project.runpytest_subprocess("-q").assert_outcomes(passed=2)


def test_path_matching_preserves_symlink_external_and_globs(tmp_path: Path) -> None:
    from doeff_adr.pytest_plugin import _matches_file_patterns, _relative_posix

    root = tmp_path / "root"
    root.mkdir()
    source = root / "docs/test_a.hy"
    source.parent.mkdir()
    source.touch()
    link = tmp_path / "link"
    link.symlink_to(root, target_is_directory=True)
    assert _relative_posix(link / "docs/test_a.hy", root) == "docs/test_a.hy"
    assert _relative_posix(tmp_path / "root-other/test_a.hy", root) == str(tmp_path / "root-other/test_a.hy")
    for path in (source, link / "docs/test_a.hy"):
        assert _matches_file_patterns(path, root, ["docs/test_*.hy"])
    assert not _matches_file_patterns(source, root, ["docs/test_b.hy"])
