"""収集 session の状態と、pytest 内部の所有境界を確認する (#1551)。"""

import json
import subprocess
from pathlib import Path

import pytest
import yaml
from doeff_adr.source_dependencies import DependencyChecks, ProviderFound, ProviderState

pytest_plugins = ["pytester"]


def test_macro_provider_lookup_is_memoized_only_for_one_collection() -> None:
    seen: list[str] = []

    def find(name: str) -> ProviderState:
        seen.append(name)
        return ProviderFound(str(len(seen)))

    first = DependencyChecks()
    assert first.macro_provider("macros", find) == ProviderFound("1")
    assert first.macro_provider("macros", find) == ProviderFound("1")
    assert seen == ["macros"]
    second = DependencyChecks()
    assert second.macro_provider("macros", find) == ProviderFound("2")
    assert seen == ["macros", "macros"]


def test_session_paths_resolve_the_root_and_prepare_patterns_once(
    pytester: pytest.Pytester,
) -> None:
    pytester.makeini("[pytest]\ndoeff_adr_wiring = off\ndoeff_adr_hy_files = test_*.hy\n")
    pytester.makefile(
        ".hy", test_once="(require doeff-hy.macros [deftest])\n(deftest test-once (assert True))"
    )
    pytester.makeconftest("""
from doeff_adr.pytest_plugin import session_paths

def pytest_configure(config):
    from pathlib import Path
    count = []
    original = Path.resolve
    def observed(path, *args, **kwargs):
        if path == config.rootpath:
            count.append(1)
        return original(path, *args, **kwargs)
    Path.resolve = observed
    try:
        first = session_paths(config)
        second = session_paths(config)
        assert first is second
        assert first.matcher is second.matcher
        assert count == [1]
    finally:
        Path.resolve = original
""")
    result = pytester.runpytest_subprocess("--collect-only", "-q", timeout=30)
    assert result.ret == 0, result.stdout.str()


@pytest.mark.semgrep
def test_collection_internals_have_one_owner_with_repo_fixtures(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[3]
    data = yaml.safe_load((root / ".semgrep.yaml").read_text())
    rule = next(
        rule
        for rule in data["rules"]
        if rule["id"] == "doeff-adr-pytest-collection-internals-single-owner"
    )
    config = tmp_path / "collection.yaml"
    config.write_text(yaml.safe_dump({"rules": [rule]}))
    cases = root / "tests/semgrep/fixtures/doeff_adr"
    result = subprocess.run(
        ["semgrep", "--test", "--config", str(config), str(cases / "collection_internals.txt")],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    # 所有 module は例外ではなく、唯一の検査対象外の所有者として宣言されている。
    assert rule["paths"]["exclude"] == ["**/doeff_adr/indexed_pytest.py"]
    found = subprocess.run(
        [
            "semgrep",
            "--config",
            str(config),
            "--json",
            "--metrics=off",
            "--disable-version-check",
            str(root / "packages/doeff-adr/src/doeff_adr"),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert found.returncode == 0, found.stdout + found.stderr
    assert json.loads(found.stdout)["results"] == []


def test_empty_hy_files_still_do_not_count_as_collected_for_wiring(
    pytester: pytest.Pytester,
) -> None:
    pytester.makeini("[pytest]\ndoeff_adr_wiring = strict\ndoeff_adr_items_cache = items\n")
    pytester.makefile(".hy", defadr_empty="(setv placeholder 42)\n")
    for _ in range(2):
        result = pytester.runpytest_subprocess("--collect-only", "-q", timeout=30)
        assert result.ret != 0
        assert "defadr_empty.hy" in result.stderr.str()
        assert "were not collected" in result.stderr.str()
