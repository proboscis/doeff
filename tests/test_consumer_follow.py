"""The consumer follow-up stage (tools/consumer_follow.py) must guide consumers and turn needle reds into report lines.

1. Consumer = a checkout whose root or one-level-down pyproject.toml depends on a doeff distribution. doeff itself and repos that do not read doeff are not consumers.
2. Each red needle check becomes one report line; checks that need a consumer conftest fixture count as "unmeasured checks" and are not red (conftest is not read).
3. A run where no needle checks are collected, or no consumer has a needle, emits a line saying the measurement disappeared as red, and every run emits a count line at the end.
"""

from __future__ import annotations

import importlib.util
import re
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load() -> types.ModuleType:
    """Load the tool from tools/ (tools/ is not a package)."""
    loaded = sys.modules.get("consumer_follow")
    if loaded is not None:
        return loaded
    spec = importlib.util.spec_from_file_location(
        "consumer_follow", ROOT / "tools" / "consumer_follow.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["consumer_follow"] = module
    spec.loader.exec_module(module)
    return module


tool = _load()

CONSUMER_PYPROJECT = '[project]\nname = "agentcli"\ndependencies = ["doeff-adr>=0.1", "hy"]\n'
PROBE_BODY = """import pytest


def test_name_exists():
    pass


def test_name_missing():
    assert False, "name get_adr is missing from doeff_adr"


def test_needs_consumer_fixture(consumer_only_fixture):
    pass


@pytest.mark.skip(reason="skipped by the consumer")
def test_skipped():
    pass
"""


def _checkout(path: Path, files: dict[str, str]) -> Path:
    (path / ".git").mkdir(parents=True)
    for name, text in files.items():
        (path / name).parent.mkdir(parents=True, exist_ok=True)
        (path / name).write_text(text, encoding="utf-8")
    return path


def _consumer(home: Path, probe: str = PROBE_BODY) -> Path:
    return _checkout(
        home / "dotfiles",
        {
            "agentcli/pyproject.toml": CONSUMER_PYPROJECT,
            tool.PROBE.as_posix(): probe,
            # The consumer conftest reads dependencies of the consumer environment -- if the follow-up stage read it, no check would run
            "docs/adr/conftest.py": 'raise ImportError("dependency that exists only in the consumer environment")\n',
        },
    )


def test_consumers_are_derived_from_pyproject(tmp_path: Path) -> None:
    home = tmp_path
    _checkout(home / "dotfiles", {"agentcli/pyproject.toml": CONSUMER_PYPROJECT})
    _checkout(
        home / "repos" / "moneta",
        {
            "pyproject.toml": '[project]\nname = "moneta"\n[dependency-groups]\ndev = ["doeff_hy", {include-group = "x"}]\n'
        },
    )
    _checkout(
        home / "repos" / "doeff",
        {"pyproject.toml": '[project]\nname = "doeff"\ndependencies = ["doeff-vm"]\n'},
    )
    _checkout(
        home / "repos" / "plain",
        {"pyproject.toml": '[project]\nname = "plain"\ndependencies = ["doeffish", "requests"]\n'},
    )
    (home / "repos" / "not-a-checkout").mkdir(parents=True)
    (home / "repos" / "dotfiles-link").symlink_to(home / "dotfiles")

    roots = tool.candidate_roots(home)

    assert [root.name for root in roots] == ["dotfiles", "doeff", "moneta", "plain"]
    assert [root.name for root in tool.consumers(roots)] == ["dotfiles", "moneta"]


def test_probe_reds_become_report_lines_and_consumer_fixtures_are_unmeasured(
    tmp_path: Path,
) -> None:
    _consumer(tmp_path)

    lines = tool.report_lines(tmp_path)

    assert len(lines) == 2, lines
    assert re.fullmatch(
        rf"{tool.REPORT_PREFIX}dotfiles \S+ docs/adr/test_doeff_adr_api_follows\.py::test_name_missing — "
        r"AssertionError: name get_adr is missing from doeff_adr",
        lines[0],
    ), lines[0]
    assert (
        lines[1]
        == f"{tool.COUNT_PREFIX}1 red lines (1 consumers, 1 with a probe, 2 checks measured, 2 unmeasured)"
    )


def test_probe_that_does_not_collect_is_red(tmp_path: Path) -> None:
    _consumer(
        tmp_path, probe="from doeff_adr import name_that_was_removed\n\n\ndef test_x():\n    pass\n"
    )

    lines = tool.report_lines(tmp_path)

    assert len(lines) == 2, lines
    assert lines[0].startswith(f"{tool.REPORT_PREFIX}dotfiles "), lines[0]
    assert "name_that_was_removed" in lines[0], lines[0]
    assert lines[1].startswith(f"{tool.COUNT_PREFIX}1 red lines"), lines[1]


def test_no_consumer_with_a_probe_is_red(tmp_path: Path) -> None:
    _checkout(
        tmp_path / "repos" / "moneta",
        {"pyproject.toml": '[project]\nname = "moneta"\ndependencies = ["doeff"]\n'},
    )

    lines = tool.report_lines(tmp_path)

    assert lines[0].startswith(
        f"{tool.REPORT_PREFIX}(no consumer) - docs/adr/test_doeff_adr_api_follows.py — "
    ), lines
    assert (
        lines[1]
        == f"{tool.COUNT_PREFIX}1 red lines (1 consumers, 0 with a probe, 0 checks measured, 0 unmeasured)"
    )
