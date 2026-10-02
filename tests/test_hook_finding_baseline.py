"""commit の hook の doeff-linter と semgrep(Python)の所見を基点と比べる道具(scripts/hook_finding_baseline.py・agora-redesign #2848)の検。

失敗ケース(#2848 の決め): 前からの所見の在る file に新しい所見を 1 つ足すと止まる・足さなければ通る・前からの所見を直したのに
基点が古いと赤。基点の鍵は行番号に依らない。道具の版が基点と違う時は、赤でも緑でもなく「測れない」と名指して通す。
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

ROOT: Path = Path(__file__).resolve().parents[1]
SCRIPT: Path = ROOT / "scripts" / "hook_finding_baseline.py"


def _module() -> ModuleType:
    """script を module として読み、純粋な比べの関数を直に試すため。"""
    spec = importlib.util.spec_from_file_location("hook_finding_baseline", SCRIPT)
    assert spec is not None and spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


TOOL = _module()
BASE: dict[str, dict[str, int]] = {"DOEFF016": {"pkg/a.py": 2, "pkg/b.py": 1}}


def test_a_new_finding_in_a_file_with_old_findings_is_red() -> None:
    comparison = TOOL.compare(BASE, {"DOEFF016": {"pkg/a.py": 3, "pkg/b.py": 1}}, None)
    assert [(f.path, f.baseline, f.current) for f in comparison.grown] == [("pkg/a.py", 2, 3)]
    assert comparison.stale == ()


def test_the_same_findings_on_moved_lines_pass() -> None:
    # 鍵は file × 規則の数 — 行がずれただけ(数が同じ)なら比べに出ない。
    comparison = TOOL.compare(BASE, {"DOEFF016": {"pkg/a.py": 2, "pkg/b.py": 1}}, None)
    assert comparison.grown == () and comparison.stale == ()


def test_a_fix_without_lowering_the_baseline_is_red() -> None:
    comparison = TOOL.compare(BASE, {"DOEFF016": {"pkg/a.py": 2}}, None)
    assert comparison.grown == ()
    assert [(f.path, f.baseline, f.current) for f in comparison.stale] == [("pkg/b.py", 1, 0)]


def test_only_the_measured_files_are_compared() -> None:
    # commit の hook は commit に入る file だけを測る — 測っていない b.py が 0 に見えても下げ忘れにしない。
    comparison = TOOL.compare(BASE, {"DOEFF016": {"pkg/a.py": 2}}, frozenset({"pkg/a.py"}))
    assert comparison.grown == () and comparison.stale == ()


def test_lower_only_lowers() -> None:
    current = {"DOEFF016": {"pkg/a.py": 5, "pkg/b.py": 0}, "DOEFF021": {"pkg/c.py": 4}}
    assert TOOL.lowered(BASE, current) == {"DOEFF016": {"pkg/a.py": 2}}


def test_only_linter_errors_are_counted_and_paths_are_from_the_root(tmp_path: Path) -> None:
    report = [
        {"rule": "DOEFF016", "severity": "error",
         "violations": [{"file": str(tmp_path / "pkg" / "a.py")}, {"file": "pkg/a.py"}, {"file": "pkg/b.py"}]},
        {"rule": "DOEFF009", "severity": "warning", "violations": [{"file": "pkg/a.py"}]},
    ]
    assert TOOL.linter_error_counts(report, tmp_path) == {"DOEFF016": {"pkg/a.py": 2, "pkg/b.py": 1}}


def test_semgrep_results_are_counted_by_the_rule_name(tmp_path: Path) -> None:
    report = {"results": [{"check_id": "tmp.doeff-no-sleep-in-tests", "path": "tests/t.py"},
                          {"check_id": "doeff-no-sleep-in-tests", "path": "tests/t.py"}], "errors": []}
    assert TOOL.semgrep_counts(report, tmp_path) == {"doeff-no-sleep-in-tests": {"tests/t.py": 2}}


# --- 入口から(一時の git repo と、file の中の BAD の数を error として名乗る偽の linter)-----------------------------------

FAKE_LINTER = """\
import json, sys
from pathlib import Path
if sys.argv[1:] == ["--version"]:
    print(Path(__file__).with_name("version").read_text().strip())
    raise SystemExit(0)
paths = [a for a in sys.argv[1:] if a.endswith(".py")]
violations = [{"file": p} for p in paths for line in Path(p).read_text().splitlines() if "BAD" in line]
print(json.dumps([{"rule": "DOEFF999", "severity": "error", "violations": violations}]))
raise SystemExit(1 if violations else 0)
"""


@dataclass(frozen=True)
class Rig:
    """一時の git repo の根と、偽の linter を PATH の先頭に置いた子の環境(呼び手の環境を写さない)。"""

    repo: Path
    environment: dict[str, str]


def _rig(tmp_path: Path) -> Rig:
    """一時の git repo と、偽の linter を PATH の先頭に置いた子の環境を作るため。"""
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "a.py").write_text("x = 1  # BAD\ny = 2\n")
    (repo / "pkg" / "b.py").write_text("z = 3\n")
    fake = tmp_path / "bin"
    fake.mkdir()
    linter = fake / "doeff-linter"
    linter.write_text(f"#!{sys.executable}\n{FAKE_LINTER}")
    linter.chmod(0o755)
    (fake / "version").write_text("doeff-linter 0.0.0 (fake one)\n")
    environment = {"PATH": f"{fake}{os.pathsep}{os.defpath}", "GIT_CONFIG_NOSYSTEM": "1", "HOME": str(tmp_path)}
    subprocess.run(["git", "init", "-q"], cwd=repo, env=environment, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, env=environment, check=True)
    return Rig(repo=repo, environment=environment)


def _run(rig: Rig, *argv: str) -> subprocess.CompletedProcess[str]:
    """script を一時の repo の根で走らせるため。"""
    return subprocess.run([sys.executable, str(SCRIPT), "--root", str(rig.repo), *argv],
                          env=rig.environment, capture_output=True, text=True, check=False)


def test_the_hook_stops_only_a_new_finding_and_a_stale_baseline(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    assert _run(rig, "init", "doeff-linter").returncode == 0
    baseline = json.loads((rig.repo / "scripts" / "hook_finding_baseline" / "doeff-linter.json").read_text())
    assert baseline["counts"] == {"DOEFF999": {"pkg/a.py": 1}}

    # 前からの所見だけ(足さない)→ 通る
    assert _run(rig, "check", "doeff-linter", "pkg/a.py").returncode == 0
    # 前からの所見の在る file に新しい所見を 1 つ足す → 止まる
    (rig.repo / "pkg" / "a.py").write_text("x = 1  # BAD\ny = 2  # BAD\n")
    added = _run(rig, "check", "doeff-linter", "pkg/a.py")
    assert added.returncode == 1 and "DOEFF999 pkg/a.py: 基点 1 → 今 2" in added.stderr
    # 前からの所見を直したのに基点が古い → 赤。lower の後は通る。
    (rig.repo / "pkg" / "a.py").write_text("x = 1\ny = 2\n")
    fixed = _run(rig, "check", "doeff-linter", "pkg/a.py")
    assert fixed.returncode == 1 and "下がっていない" in fixed.stderr
    assert _run(rig, "lower", "doeff-linter").returncode == 0
    assert _run(rig, "check", "doeff-linter", "pkg/a.py").returncode == 0


def test_a_different_tool_version_is_named_as_unmeasured_and_passes(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    assert _run(rig, "init", "doeff-linter").returncode == 0
    (tmp_path / "bin" / "version").write_text("doeff-linter 0.0.1 (fake two)\n")
    (rig.repo / "pkg" / "a.py").write_text("x = 1  # BAD\ny = 2  # BAD\n")
    other = _run(rig, "check", "doeff-linter", "pkg/a.py")
    assert other.returncode == 0
    assert "測れない" in other.stderr and "0.0.1" in other.stderr and "0.0.0" in other.stderr


def test_init_never_overwrites_a_baseline(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    assert _run(rig, "init", "doeff-linter").returncode == 0
    again = _run(rig, "init", "doeff-linter")
    assert again.returncode == 2 and "書き直さない" in again.stderr
