"""動的な値と明示idの収集記録、および古い記録を実行しない反例(#1459)。"""

import hashlib
from pathlib import Path

import pytest

pytest_plugins = ["pytester"]

SOURCE = """
(require doeff-hy.macros [deftest val])
(import os pytest)
(import pkg.inputs [HOST VALUES CASES])
(with [log (open (get os.environ "IMPORT_LOG") "a")] (.write log "imported\\n"))
(deftest test-host {:interpreters [HOST]} (assert True))
(deftest test-values [value] {:params {"value" (lfor v VALUES v)}}
  (assert (in value VALUES)))
(deftest test-cases [case] {:params {"case" (lfor c CASES (pytest.param c :id c.label))}}
  (assert (= case.answer 42)))
"""

INPUTS = """
from types import SimpleNamespace
HOST = "host-a"
VALUES = [1, 2]
CASES = [SimpleNamespace(label="case-a", answer=42)]
"""


@pytest.fixture
def dynamic_project(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch) -> pytest.Pytester:
    """実値を別moduleから読むHy testを独立processで収集・実行する。"""
    package: Path = pytester.path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "inputs.py").write_text(INPUTS)
    (package / "test_dynamic.hy").write_text(SOURCE)
    pytester.makeini(
        """
        [pytest]
        pythonpath = .
        doeff_adr_hy_files = pkg/test_dynamic.hy
        doeff_adr_wiring = off
        doeff_adr_items_cache = items
        """
    )
    pytester.makeconftest(
        """
        import pytest
        from doeff import run

        @pytest.fixture
        def doeff_interpreter_name():
            return "default"

        @pytest.fixture
        def doeff_interpreter(doeff_interpreter_name):
            assert doeff_interpreter_name in {"default", "host-a", "host-b"}
            return run
        """
    )
    monkeypatch.setenv("IMPORT_LOG", str(pytester.path / "imports.log"))
    # 同じ秒・同じ大きさのprovider変更でもPythonのtimestamp pycを使わない。
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    return pytester


def imports(project: pytest.Pytester) -> list[str]:
    log: Path = project.path / "imports.log"
    result: list[str] = log.read_text().splitlines() if log.exists() else []
    log.write_text("")
    return result


def collect(project: pytest.Pytester, *args: str) -> pytest.RunResult:
    return project.runpytest_subprocess("pkg/test_dynamic.hy", "--collect-only", "-q", *args)


def nodeids(result: pytest.RunResult) -> list[str]:
    return [line for line in result.stdout.lines if line.startswith("pkg/test_dynamic.hy::")]


def cache_path(project: pytest.Pytester) -> Path:
    source: Path = project.path / "pkg/test_dynamic.hy"
    digest: str = hashlib.sha256(source.read_bytes()).hexdigest()
    return project.path / "items" / f"{digest}.json"


def test_dynamic_values_interpreters_and_explicit_ids_are_cached(dynamic_project: pytest.Pytester) -> None:
    project: pytest.Pytester = dynamic_project
    cold: pytest.RunResult = collect(project)
    assert len(nodeids(cold)) == 4
    assert imports(project) == ["imported"]
    warm: pytest.RunResult = collect(project)
    assert nodeids(warm) == nodeids(cold)
    assert imports(project) == []
    assert "記録から収集 1 file・収集で import 0 file" in warm.stdout.str()
    collect(project, "-k", "not_selected")
    assert imports(project) == []
    result: pytest.RunResult = project.runpytest_subprocess("pkg/test_dynamic.hy", "-q")
    result.assert_outcomes(passed=4)
    assert imports(project) == ["imported"]


def test_changed_provider_invalidates_interpreters_values_and_ids(dynamic_project: pytest.Pytester) -> None:
    project: pytest.Pytester = dynamic_project
    collect(project)
    assert cache_path(project).exists()
    imports(project)
    provider: Path = project.path / "pkg/inputs.py"
    provider.write_text(INPUTS.replace("host-a", "host-b").replace("[1, 2]", "[3]").replace("case-a", "case-b"))
    changed: pytest.RunResult = collect(project)
    assert imports(project) == ["imported"]
    assert any("[host-b]" in name for name in nodeids(changed))
    assert any("[3]" in name for name in nodeids(changed))
    assert any("[case-b]" in name for name in nodeids(changed))
    assert not any("host-a" in name or "case-a" in name for name in nodeids(changed))
    warm: pytest.RunResult = collect(project)
    assert nodeids(warm) == nodeids(changed)
    assert imports(project) == []


@pytest.mark.parametrize("changed_part", ["value", "id"])
def test_runtime_mismatch_fails_before_body_and_forgets_cache(
    dynamic_project: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, changed_part: str
) -> None:
    project: pytest.Pytester = dynamic_project
    source: Path = project.path / "pkg/test_dynamic.hy"
    form: str = '(int (os.getenv "DYNAMIC_VALUE" "1"))' if changed_part == "value" else '1 :id (os.getenv "DYNAMIC_VALUE" "1")'
    source.write_text(
        '(require doeff-hy.macros [deftest])\n(import os pytest)\n'
        f'(deftest test-changing [value] {{:params {{"value" [(pytest.param {form})]}}}}\n'
        '  (assert False "古い記録で本体を実行した"))\n'
    )
    monkeypatch.setenv("DYNAMIC_VALUE", "1")
    collect(project)
    assert cache_path(project).exists()
    monkeypatch.setenv("DYNAMIC_VALUE", "2")
    result: pytest.RunResult = project.runpytest_subprocess("pkg/test_dynamic.hy", "-q")
    result.assert_outcomes(errors=1)
    assert "記録と実物が食い違った" in result.stdout.str()
    assert not cache_path(project).exists()
