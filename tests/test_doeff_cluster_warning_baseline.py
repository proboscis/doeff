"""packages/doeff-cluster の warning の基点の比べ(scripts/doeff_cluster_warning_baseline.py・agora-redesign #2683)の検。

失敗ケース: 新しい warning が 1 つ増えると赤・直したのに基点を下げないと赤・lower は下げるだけで上げない。
#2906: commit の hook の道(scripts/lint-doeff-cluster.sh に file を渡す時)は HEAD の組み立ての入力の鍵の binary で測り、探し道の
linter は呼ばない・置き場に無ければ「測れない」と名指して通す・比べの script は渡された --linter を使う。
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
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


def test_the_linter_given_by_the_hook_is_used_instead_of_the_path_one(tmp_path: Path) -> None:
    # #2906: commit の hook は HEAD の組み立ての入力の鍵の binary を --linter で渡す — 探し道の linter(別の版)は呼ばない。
    root: Path = tmp_path / "tree"
    report: list[dict[str, object]] = _absolute_report(root, ["src/a.hy"] * 3 + ["src/b.hy"])
    environment: dict[str, str] = _fake_repo(root, report, BASE)
    (root / "bin" / "doeff-linter").write_text("#!/bin/sh\necho 探し道の linter が呼ばれた >&2\nexit 9\n", encoding="utf-8")
    given: Path = tmp_path / "given-linter"
    given.write_text(f"#!/bin/sh\ncat <<'EOF'\n{json.dumps(report)}\nEOF\n", encoding="utf-8")
    given.chmod(0o755)
    result = subprocess.run([sys.executable, str(SCRIPT), "--root", str(root), "--linter", str(given), "check"],
                            env=environment, capture_output=True, text=True, check=False)
    assert result.returncode == 1, result.stderr
    assert "DOEFF172 src/a.hy: 基点 2 → 今 3" in result.stderr
    assert "探し道の linter" not in result.stderr


# --- scripts/lint-doeff-cluster.sh の commit の hook の道(#2906)— HEAD の鍵の binary を HOME の下の置き場から探す ----------------

FAKE_DEV_LINTER = """\
import json, sys
from pathlib import Path
with open(Path(__file__).with_name("calls"), "a") as out:
    out.write(" ".join(sys.argv[1:]) + "\\n")
if "--output-format" in sys.argv:
    print(json.dumps([{"rule": "DOEFF172", "severity": "warning",
                       "violations": [{"file": "src/a.hy"}, {"file": "src/a.hy"}, {"file": "src/b.hy"}]}]))
"""


def _uv_dir(uv: str, kind: str) -> str:
    """本物の uv の置き場(`uv cache dir` / `uv python dir`)— HOME を替えた子にも同じ置き場を渡すため。"""
    return subprocess.run([uv, kind, "dir"], capture_output=True, text=True, check=True).stdout.strip()


@dataclass(frozen=True)
class ClusterRig:
    """一時の repo・HOME を一時の dir に向けた子の環境・HOME の下の偽の land-arm の開発版の置き場。"""

    repo: Path
    environment: dict[str, str]
    dev: Path


def _cluster_repo(tmp_path: Path) -> ClusterRig:
    """script 3 つ・偽の linter の crate・package の基点を commit した一時の repo と、HOME を一時の dir に向けた子の環境、
    HOME の下の偽の land-arm の開発版の置き場を作るため(探し道の linter は呼ばれたら印を残して落ちる)。"""
    repo: Path = tmp_path / "repo"
    package: Path = repo / "packages" / "doeff-cluster"
    (package / "src").mkdir(parents=True)
    (package / "lint-warning-baseline.json").write_text(json.dumps(BASE), encoding="utf-8")
    (package / "architecture.hy").write_text(";; 宣言\n", encoding="utf-8")
    (package / "src" / "a.hy").write_text(";; a\n", encoding="utf-8")
    (repo / "scripts").mkdir()
    for script in ("lint-doeff-cluster.sh", "doeff_cluster_warning_baseline.py", "doeff_linter_locked.py"):
        (repo / "scripts" / script).write_text((ROOT / "scripts" / script).read_text(encoding="utf-8"), encoding="utf-8")
    crate: Path = repo / "packages" / "doeff-linter"
    (crate / "src").mkdir(parents=True)
    (crate / "Cargo.toml").write_text('[package]\nname = "doeff-linter"\n', encoding="utf-8")
    (crate / "src" / "main.rs").write_text("fn main() {}\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "はじめ"],
                   cwd=repo, check=True)
    # 本物の repo と同じく根に .venv を置く: hook は script を `uv run --no-project python` で起こし、uv は今の dir の .venv を使う。
    # 基点の script は doeff の効果で環境を読む(05a2a3f94)ので、doeff の入った venv が要る — この検を走らせている venv を結ぶ
    # (#1201 — 結ばない rig では script が doeff を import できず、hook の性質でなく rig の欠けで赤だった)。
    venv: Path = Path(sys.prefix)
    assert (venv / "pyvenv.cfg").is_file(), f"検は venv の中で走らせる(rig の .venv に結ぶ): {venv}"
    (repo / ".venv").symlink_to(venv, target_is_directory=True)
    tools: Path = tmp_path / "bin"
    tools.mkdir()
    (tools / "doeff-linter").write_text(f"#!/bin/sh\ntouch {tmp_path / 'path-linter-called'}\nexit 9\n", encoding="utf-8")
    (tools / "doeff-linter").chmod(0o755)
    real_uv: str | None = shutil.which("uv")
    assert real_uv is not None, "uv が要る(script の Python を走らせる)"
    environment: dict[str, str] = {
        "PATH": f"{tools}:{Path(real_uv).parent}:/usr/bin:/bin", "HOME": str(tmp_path / "home"),
        "UV_CACHE_DIR": _uv_dir(real_uv, "cache"), "UV_PYTHON_INSTALL_DIR": _uv_dir(real_uv, "python"),
    }
    return ClusterRig(repo=repo, environment=environment, dev=tmp_path / "home" / ".local" / "share" / "doeff-linter-dev")


def _install_dev(dev: Path, repo: Path) -> None:
    """偽の land-arm の開発版を、repo の HEAD から組んだ物として置くため。"""
    dev.mkdir(parents=True)
    linter: Path = dev / "doeff-linter"
    linter.write_text(f"#!{sys.executable}\n{FAKE_DEV_LINTER}", encoding="utf-8")
    linter.chmod(0o755)
    head: str = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()
    (dev / "installed.json").write_text(json.dumps({"commit": head}), encoding="utf-8")


def test_the_cluster_hook_measures_with_the_binary_of_head_and_never_the_path_one(tmp_path: Path) -> None:
    rig: ClusterRig = _cluster_repo(tmp_path)
    _install_dev(rig.dev, rig.repo)
    result = subprocess.run(["sh", "scripts/lint-doeff-cluster.sh", "packages/doeff-cluster/src/a.hy"], cwd=rig.repo,
                            env=rig.environment, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    calls: list[str] = (rig.dev / "calls").read_text(encoding="utf-8").splitlines()
    assert any("--output-format" in call for call in calls)  # warning の基点の比べ
    assert any(call.endswith("src/a.hy architecture.hy") for call in calls)  # error の判定
    assert not (tmp_path / "path-linter-called").exists()


def test_the_cluster_hook_names_unmeasured_and_passes_without_a_binary(tmp_path: Path) -> None:
    rig: ClusterRig = _cluster_repo(tmp_path)
    result = subprocess.run(["sh", "scripts/lint-doeff-cluster.sh", "packages/doeff-cluster/src/a.hy"], cwd=rig.repo,
                            env=rig.environment, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "測れない" in result.stderr
    assert not (tmp_path / "path-linter-called").exists()
