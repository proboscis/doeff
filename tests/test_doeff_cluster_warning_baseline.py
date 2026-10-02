"""packages/doeff-cluster の warning の基点の比べ(scripts/doeff_cluster_warning_baseline.py・agora-redesign #2683)の検。

失敗ケース: 新しい warning が 1 つ増えると赤・直したのに基点を下げないと赤・lower は下げるだけで上げない。
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

ROOT: Path = Path(__file__).resolve().parents[1]
SCRIPT: Path = ROOT / "scripts" / "doeff_cluster_warning_baseline.py"


def _module() -> ModuleType:
    """script を module として読み、純粋な比べの関数を直に試すため。"""
    spec = importlib.util.spec_from_file_location("doeff_cluster_warning_baseline", SCRIPT)
    assert spec is not None and spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


BASELINE = _module()
BASE: dict[str, dict[str, int]] = {"DOEFF172": {"src/a.hy": 2, "src/b.hy": 1}}


def test_a_new_warning_is_red() -> None:
    comparison = BASELINE.compare(BASE, {"DOEFF172": {"src/a.hy": 3, "src/b.hy": 1}}, None)
    assert [(f.path, f.baseline, f.current) for f in comparison.grown] == [("src/a.hy", 2, 3)]
    assert comparison.stale == ()


def test_a_new_rule_in_a_new_file_is_red() -> None:
    current = {"DOEFF172": {"src/a.hy": 2, "src/b.hy": 1}, "DOEFF113": {"src/c.py": 1}}
    comparison = BASELINE.compare(BASE, current, None)
    assert [(f.rule, f.path) for f in comparison.grown] == [("DOEFF113", "src/c.py")]


def test_a_fix_without_lowering_the_baseline_is_red() -> None:
    comparison = BASELINE.compare(BASE, {"DOEFF172": {"src/a.hy": 2}}, None)
    assert comparison.grown == ()
    assert [(f.path, f.baseline, f.current) for f in comparison.stale] == [("src/b.hy", 1, 0)]


def test_only_the_measured_files_are_compared() -> None:
    # commit の hook は変えた file だけを測る — 測っていない b.hy が 0 に見えても下げ忘れにしない。
    comparison = BASELINE.compare(BASE, {"DOEFF172": {"src/a.hy": 2}}, frozenset({"src/a.hy", "architecture.hy"}))
    assert comparison.grown == () and comparison.stale == ()


def test_lower_only_lowers() -> None:
    current = {"DOEFF172": {"src/a.hy": 5, "src/b.hy": 0}, "DOEFF113": {"src/c.py": 4}}
    assert BASELINE.lowered(BASE, current) == {"DOEFF172": {"src/a.hy": 2}}


def test_warning_counts_reads_only_warnings() -> None:
    report = [
        {"rule": "DOEFF172", "severity": "warning", "violations": [{"file": "src/a.hy"}, {"file": "src/a.hy"}]},
        {"rule": "DOEFF014", "severity": "info", "violations": [{"file": "src/a.hy"}]},
        {"rule": "DOEFF104", "severity": "error", "violations": [{"file": "src/b.hy"}]},
    ]
    assert BASELINE.warning_counts(report, Path("/nowhere/packages/doeff-cluster")) == {"DOEFF172": {"src/a.hy": 2}}


def test_the_check_entry_is_red_on_a_new_warning(tmp_path: Path) -> None:
    # 端から端: 偽の doeff-linter が基点より 1 つ多い warning を出すと、check の入口が rc 1 で増えた組を名指す。
    repo: Path = tmp_path / "repo"
    package: Path = repo / "packages" / "doeff-cluster"
    package.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (package / "lint-warning-baseline.json").write_text(json.dumps(BASE), encoding="utf-8")
    report = [{"rule": "DOEFF172", "severity": "warning",
               "violations": [{"file": "src/a.hy"}, {"file": "src/a.hy"}, {"file": "src/a.hy"}, {"file": "src/b.hy"}]}]
    tools: Path = tmp_path / "bin"
    tools.mkdir()
    linter: Path = tools / "doeff-linter"
    linter.write_text(f"#!/bin/sh\ncat <<'EOF'\n{json.dumps(report)}\nEOF\n", encoding="utf-8")
    linter.chmod(0o755)
    environment = {"PATH": f"{tools}:/usr/bin:/bin"}
    result = subprocess.run([sys.executable, str(SCRIPT), "--root", str(repo), "check"], cwd=repo, env=environment,
                            capture_output=True, text=True, check=False)
    assert result.returncode == 1, result.stderr
    assert "DOEFF172 src/a.hy: 基点 2 → 今 3" in result.stderr
    # 基点どおりなら緑。
    report_ok = [{"rule": "DOEFF172", "severity": "warning",
                  "violations": [{"file": "src/a.hy"}, {"file": "src/a.hy"}, {"file": "src/b.hy"}]}]
    linter.write_text(f"#!/bin/sh\ncat <<'EOF'\n{json.dumps(report_ok)}\nEOF\n", encoding="utf-8")
    passed = subprocess.run([sys.executable, str(SCRIPT), "--root", str(repo), "check"], cwd=repo, env=environment,
                            capture_output=True, text=True, check=False)
    assert passed.returncode == 0, passed.stderr


def _fake_repo(root: Path, report: list[dict[str, object]], baseline: dict[str, dict[str, int]]) -> dict[str, str]:
    """置き場を任意の dir(root)にした検体の木(偽の linter つき)を作るため — 基点の鍵が置き場に依らないことを、別々の場所の木で試す。
    答えは、偽の linter を先に引く子の process の環境。"""
    package: Path = root / "packages" / "doeff-cluster"
    package.mkdir(parents=True)
    (package / "lint-warning-baseline.json").write_text(json.dumps(baseline), encoding="utf-8")
    tools: Path = root / "bin"
    tools.mkdir()
    linter: Path = tools / "doeff-linter"
    linter.write_text(f"#!/bin/sh\ncat <<'EOF'\n{json.dumps(report)}\nEOF\n", encoding="utf-8")
    linter.chmod(0o755)
    return {"PATH": f"{tools}:/usr/bin:/bin"}


def _absolute_report(root: Path, files: list[str]) -> list[dict[str, object]]:
    """本物の linter と同じく、その木の絶対 path で warning を報せる報告を作るため。"""
    package: Path = root / "packages" / "doeff-cluster"
    return [{"rule": "DOEFF172", "severity": "warning", "violations": [{"file": str(package / f)} for f in files]}]


def test_absolute_paths_from_another_tree_are_keyed_from_the_package(tmp_path: Path) -> None:
    # 反例(ddc164310・cc1-w38 の実測): 基点は package の dir からの鍵。別の場所の木で linter が絶対 path を報せても、
    # 基点どおりなら check は緑・path を渡す check も比べる(1 つ増やせば赤)・lower は基点を空にしない。
    for place in ("tree-a", "elsewhere/tree-b"):
        root: Path = tmp_path / place
        environment = _fake_repo(root, _absolute_report(root, ["src/a.hy", "src/a.hy", "src/b.hy"]), BASE)
        ok = subprocess.run([sys.executable, str(SCRIPT), "--root", str(root), "check"], env=environment,
                            capture_output=True, text=True, check=False)
        assert ok.returncode == 0, ok.stderr
        lower = subprocess.run([sys.executable, str(SCRIPT), "--root", str(root), "lower"], env=environment,
                               capture_output=True, text=True, check=False)
        assert lower.returncode == 0, lower.stderr
        kept = json.loads((root / "packages" / "doeff-cluster" / "lint-warning-baseline.json").read_text(encoding="utf-8"))
        assert kept == BASE, kept
    grown_root: Path = tmp_path / "tree-c"
    environment = _fake_repo(grown_root, _absolute_report(grown_root, ["src/a.hy"] * 3 + ["src/b.hy"]), BASE)
    staged = subprocess.run([sys.executable, str(SCRIPT), "--root", str(grown_root), "check", "packages/doeff-cluster/src/a.hy"],
                            env=environment, capture_output=True, text=True, check=False)
    assert staged.returncode == 1, staged.stderr
    assert "DOEFF172 src/a.hy: 基点 2 → 今 3" in staged.stderr
