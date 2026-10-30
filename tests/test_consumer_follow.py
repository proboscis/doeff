"""消費者の追従の後段(tools/consumer_follow.py)が、消費者を導き、針の赤を報告の行にする事(card ki-93ba968bbafe)。

1. 消費者 = 根か 1 段下の pyproject.toml が doeff の配布物に依存する checkout。doeff 自身と、doeff を読まない repo は消費者でない。
2. 針の赤の検は 1 行ずつ報告の行になり、消費者の conftest の fixture を求める検は「測らない検」に数えて赤にしない(conftest は読まない)。
3. 針の検が集まらない・針を持つ消費者が無い回は、測りが消えた事を赤の行にし、どの回も最後に数えの行を出す。
"""

from __future__ import annotations

import importlib.util
import re
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load() -> types.ModuleType:
    """tools/ の道具を読む(tools/ は package ではない)。"""
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
    assert False, "名 get_adr が doeff_adr に無い"


def test_needs_consumer_fixture(consumer_only_fixture):
    pass


@pytest.mark.skip(reason="消費者が飛ばした")
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
            # 消費者の conftest は消費者の環境の依存を読む — 後段が読めば、どの検も走らない
            "docs/adr/conftest.py": 'raise ImportError("消費者の環境にしか無い依存")\n',
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
        r"AssertionError: 名 get_adr が doeff_adr に無い",
        lines[0],
    ), lines[0]
    assert (
        lines[1]
        == f"{tool.COUNT_PREFIX}赤 1 行(消費者 1 個・針のある 1 個・測った検 2 本・測らない検 2 本)"
    )


def test_probe_that_does_not_collect_is_red(tmp_path: Path) -> None:
    _consumer(
        tmp_path, probe="from doeff_adr import name_that_was_removed\n\n\ndef test_x():\n    pass\n"
    )

    lines = tool.report_lines(tmp_path)

    assert len(lines) == 2, lines
    assert lines[0].startswith(f"{tool.REPORT_PREFIX}dotfiles "), lines[0]
    assert "name_that_was_removed" in lines[0], lines[0]
    assert lines[1].startswith(f"{tool.COUNT_PREFIX}赤 1 行"), lines[1]


def test_no_consumer_with_a_probe_is_red(tmp_path: Path) -> None:
    _checkout(
        tmp_path / "repos" / "moneta",
        {"pyproject.toml": '[project]\nname = "moneta"\ndependencies = ["doeff"]\n'},
    )

    lines = tool.report_lines(tmp_path)

    assert lines[0].startswith(
        f"{tool.REPORT_PREFIX}(消費者なし) - docs/adr/test_doeff_adr_api_follows.py — "
    ), lines
    assert (
        lines[1]
        == f"{tool.COUNT_PREFIX}赤 1 行(消費者 1 個・針のある 0 個・測った検 0 本・測らない検 0 本)"
    )
