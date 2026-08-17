"""defsemgrep runner の機体非依存性 — 検査の意味は tree だけで決まる。

実測 2026-08-17(zeus 全量検査・route-3da438e018): 遠隔検査 tree は git の
metadata(.git)を運ばない(remote_check は git ls-files の名簿だけを写す)。
その tree 上で defsemgrep が 2 つの機体依存で全滅した:

1. repo root の判定が .git の実在に依存 — .git が無い tree で RuntimeError。
   検査 tree の意味は tree 自身(config file の実在)が決めるべきで、
   git metadata の有無で変わってはならない。
2. semgrep 本体が起動時に crash した時(exit 1・stdout 空)、runner が
   「発火なし」と黙読した — 壊れた scanner と『rule が発火しない』が
   区別されず、根因(interpreter 不整合)が『installed rule が hit fixture に
   発火しない』という偽の診断で報告された。scan が走らなかったことは
   大声で落ちなければならない。
"""

import json
import re
import stat
import textwrap
from pathlib import Path

import pytest
from doeff_adr.registry import (
    _resolve_config_path,
    _run_semgrep,
    assert_semgrep_enforcement,
    clear_registry,
    register_semgrep_enforcement,
)


@pytest.fixture
def registry():
    clear_registry()
    yield
    clear_registry()


def _make_tree_without_git(tmp_path: Path) -> Path:
    """検査 tree の模型 — config はあるが git metadata は無い。"""
    root = tmp_path / "tree"
    (root / "packages" / "sample").mkdir(parents=True)
    (root / ".semgrep.yaml").write_text(
        textwrap.dedent(
            """\
            rules:
              - id: no-future-annotations-tree-only-probe
                languages:
                  - python
                severity: ERROR
                message: probe rule for tree-only config resolution
                pattern: from __future__ import annotations
            """
        ),
        encoding="utf-8",
    )
    return root


def test_resolve_config_path_does_not_require_git_metadata(tmp_path, monkeypatch):
    root = _make_tree_without_git(tmp_path)
    assert not (root / ".git").exists()
    monkeypatch.chdir(root / "packages" / "sample")

    resolved = _resolve_config_path(".semgrep.yaml")

    assert resolved == root / ".semgrep.yaml"


def test_resolve_config_path_fails_when_no_tree_carries_the_config(tmp_path, monkeypatch):
    lonely = tmp_path / "no-config-anywhere"
    lonely.mkdir()
    monkeypatch.chdir(lonely)

    with pytest.raises(RuntimeError, match=re.escape("doeff-adr-missing-config-probe.yaml")):
        _resolve_config_path("doeff-adr-missing-config-probe.yaml")


def test_installed_rule_enforcement_runs_on_tree_without_git(tmp_path, monkeypatch, registry):
    """installed-rule 形の全経路(config 解決 → fixture 走査)が .git なしで成立する。"""
    root = _make_tree_without_git(tmp_path)
    monkeypatch.chdir(root)
    register_semgrep_enforcement(
        "tree_only_installed_probe",
        rule_id="no-future-annotations-tree-only-probe",
        hit_fixtures=[
            {
                "relative-path": "packages/sample/bad.py",
                "source": "from __future__ import annotations\n",
            }
        ],
        clean_fixtures=[
            {
                "relative-path": "packages/sample/clean.py",
                "source": "import json\n",
            }
        ],
    )

    assert_semgrep_enforcement("tree_only_installed_probe")


def _fake_semgrep(tmp_path: Path, script_body: str) -> Path:
    path = tmp_path / "fake-semgrep"
    path.write_text("#!/bin/sh\n" + script_body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_run_semgrep_crash_with_exit_1_is_loud_not_empty(tmp_path):
    """scanner が exit 1 で crash(stdout 非 JSON)→ 空の結果ではなく AssertionError。

    exit 1 は semgrep の『findings あり』とも重なるため、JSON の実在だけが
    『scan が走った』の根拠になる — 走らなかった scan を緑側の材料にしない。
    """
    fake = _fake_semgrep(
        tmp_path,
        'echo "Traceback (most recent call last): interpreter mismatch" >&2\nexit 1\n',
    )
    config = tmp_path / "rule.json"
    config.write_text(json.dumps({"rules": []}), encoding="utf-8")
    target = tmp_path / "target.txt"
    target.write_text("anything\n", encoding="utf-8")

    with pytest.raises(AssertionError, match="interpreter mismatch"):
        _run_semgrep(str(fake), config, [target], project_root=tmp_path)


def test_run_semgrep_empty_stdout_with_exit_0_is_loud(tmp_path):
    fake = _fake_semgrep(tmp_path, "exit 0\n")
    config = tmp_path / "rule.json"
    config.write_text(json.dumps({"rules": []}), encoding="utf-8")
    target = tmp_path / "target.txt"
    target.write_text("anything\n", encoding="utf-8")

    with pytest.raises(AssertionError, match="JSON"):
        _run_semgrep(str(fake), config, [target], project_root=tmp_path)


def test_crashing_scanner_is_not_reported_as_rule_not_firing(tmp_path, monkeypatch, registry):
    """zeus 実測の再演: crash する semgrep が『bad fixture に不一致』と報告されない。"""
    fake = _fake_semgrep(
        tmp_path,
        'echo "Traceback (most recent call last): scanner is broken" >&2\nexit 1\n',
    )
    register_semgrep_enforcement(
        "crash_is_not_a_clean_scan",
        pattern="forbidden-token",
        bad=["forbidden-token"],
        good=["allowed-token"],
    )
    import doeff_adr.registry as registry_module

    monkeypatch.setattr(registry_module.shutil, "which", lambda _name: str(fake))

    with pytest.raises(AssertionError) as excinfo:
        assert_semgrep_enforcement("crash_is_not_a_clean_scan")

    assert "did not match any bad fixture" not in str(excinfo.value)
    assert "scanner is broken" in str(excinfo.value)
