"""ADR-DOE-ENFORCE-001 R7: enforcement 台帳の著述時(git pre-commit)検査の挙動。

実弾 = f47f0a4b(ADR-DOE-HY-004 新設・defadr +1 / deftest +2 / law +1)が台帳
未更新のまま land queue を通らず直接 main へ届き、初検出が翌朝の日次 verify
(doeff-verify-20260821-065005)まで遅れて無実の 2 便(L43/L44)が容疑に挙がった。
着地の窓は力学のみ(2026-08-17 operator 裁定)なので、記帳漏れを構造的に
止められる検出点は著述時 = git commit 時だけ。その機構をここで固定する:

- 勘定の単一の家 scripts/check_enforcement_ledger.py(stdlib 単独・venv 不要)
- working tree 突合(既定)と staged 断面(index)突合(--staged・hook が使う面)
- tracked hook 原本 scripts/git-hooks/pre-commit(対象 path が staged の時だけ
  検査し、作り直し(rebase / cherry-pick / sequencer)中は判定しない)
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts" / "check_enforcement_ledger.py"
HOOK = ROOT / "scripts" / "git-hooks" / "pre-commit"


def _write_fixture_tree(root: Path, *, laws: int, ledger_laws: int) -> None:
    """最小の enforcement 資産一式を敷く(defadr 1 file・semgrep rule 1 本)。"""
    adr_dir = root / "docs" / "adr"
    adr_dir.mkdir(parents=True, exist_ok=True)
    body = "\n".join(f'(law fixture-{i} :statement "x")' for i in range(laws))
    (adr_dir / "defadr_fixture_001.hy").write_text(f";; fixture\n{body}\n", encoding="utf-8")
    (root / ".semgrep.yaml").write_text("rules:\n  - id: fixture-rule\n", encoding="utf-8")
    ledger = {
        "_comment": "fixture ledger",
        "defadr_files": 1,
        "semgrep_rules": 1,
        "adr_deftest_enforcements": 0,
        "adr_defsemgrep_enforcements": 0,
        "adr_laws": ledger_laws,
    }
    (adr_dir / "enforcement-ledger.json").write_text(json.dumps(ledger), encoding="utf-8")


def _run_checker(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CHECKER), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)


def test_checker_green_on_consistent_tree(tmp_path):
    _write_fixture_tree(tmp_path, laws=2, ledger_laws=2)
    proc = _run_checker(tmp_path, f"--root={tmp_path}")
    assert proc.returncode == 0, f"一致する tree で赤: {proc.stderr}"


def test_checker_detects_unrecorded_addition(tmp_path):
    # 増加の記帳漏れ(今回の実弾の形)— law が増えたのに台帳が据え置き。
    _write_fixture_tree(tmp_path, laws=3, ledger_laws=2)
    proc = _run_checker(tmp_path, f"--root={tmp_path}")
    assert proc.returncode == 1, "記帳漏れ(実数 > 台帳)を検出しない"
    assert "不一致" in proc.stderr, f"不一致の申告が無い: {proc.stderr}"


def test_checker_detects_silent_drop(tmp_path):
    # 黙った喪失(侵食)— R5 の本丸。
    _write_fixture_tree(tmp_path, laws=1, ledger_laws=2)
    proc = _run_checker(tmp_path, f"--root={tmp_path}")
    assert proc.returncode == 1, "侵食(実数 < 台帳)を検出しない"


def test_staged_mode_catches_stage_forgetting(tmp_path):
    # working tree は一致・index は不一致(台帳の直しを stage し忘れた形)。
    # working tree 突合はこれを素通しする — hook が --staged で index を読む理由。
    _write_fixture_tree(tmp_path, laws=2, ledger_laws=2)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _write_fixture_tree(tmp_path, laws=3, ledger_laws=3)
    _git(tmp_path, "add", "docs/adr/defadr_fixture_001.hy")  # 台帳は stage しない

    worktree = _run_checker(tmp_path, f"--root={tmp_path}")
    assert worktree.returncode == 0, f"working tree は一致のはず: {worktree.stderr}"
    staged = _run_checker(tmp_path, f"--root={tmp_path}", "--staged")
    assert staged.returncode == 1, "staged 断面の不一致(stage し忘れ)を検出しない"


def _install_fixture_repo_with_hook(tmp_path: Path, *, laws: int, ledger_laws: int) -> Path:
    _write_fixture_tree(tmp_path, laws=laws, ledger_laws=ledger_laws)
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy(CHECKER, scripts / "check_enforcement_ledger.py")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    return tmp_path


def _run_hook(repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["sh", str(HOOK)], cwd=repo, capture_output=True, text=True, check=False
    )


def test_hook_blocks_inconsistent_staged_snapshot(tmp_path):
    repo = _install_fixture_repo_with_hook(tmp_path, laws=3, ledger_laws=2)
    proc = _run_hook(repo)
    assert proc.returncode == 1, f"不一致の staged 断面を hook が通した: {proc.stderr}"


def test_hook_passes_consistent_staged_snapshot(tmp_path):
    repo = _install_fixture_repo_with_hook(tmp_path, laws=2, ledger_laws=2)
    proc = _run_hook(repo)
    assert proc.returncode == 0, f"一致する staged 断面で hook が赤: {proc.stderr}"


def test_hook_silent_during_rebase_replay(tmp_path):
    # 作り直し(rebase / cherry-pick)中は判定しない — 着地の窓の追随・replay を
    # 塞がないため(prepare-commit-msg hook と同じ guard)。
    repo = _install_fixture_repo_with_hook(tmp_path, laws=3, ledger_laws=2)
    (repo / ".git" / "rebase-merge").mkdir()
    proc = _run_hook(repo)
    assert proc.returncode == 0, "rebase 中の replay を hook が塞いだ"


def test_hook_skips_commits_not_touching_enforcement_assets(tmp_path):
    # enforcement 資産に触れない commit は素通し(index が不一致でも、その commit の
    # 責任ではない — 検査は触った commit に課す)。
    repo = _install_fixture_repo_with_hook(tmp_path, laws=2, ledger_laws=2)
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com",
         "commit", "-q", "-m", "seed", "--no-verify")
    (repo / "README.md").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    proc = _run_hook(repo)
    assert proc.returncode == 0, f"無関係な commit を hook が検査した: {proc.stderr}"


def test_pytest_ledger_test_uses_the_same_counting_home():
    # 勘定の第 2 定義点を作らない — 既定 pytest の R5 検査(tests/test_enforcement_ledger.py)
    # は hook と同じ家(scripts/check_enforcement_ledger.py)を消費する。
    text = (ROOT / "tests" / "test_enforcement_ledger.py").read_text(encoding="utf-8")
    assert "check_enforcement_ledger" in text, (
        "R5 検査が勘定の家(scripts/check_enforcement_ledger.py)を消費していない — "
        "regex が乖離した日から hook 緑 = verify 緑が成立しなくなる(ADR-DOE-ENFORCE-001 R7)"
    )


def test_checker_is_stdlib_only():
    # hook は venv の状態に依存できない(rebase 途中の worktree・未 sync の機体でも
    # 走る)— 勘定の家が stdlib の外を import したら赤。
    if not CHECKER.exists():
        pytest.fail("勘定の家 scripts/check_enforcement_ledger.py が無い")
    text = CHECKER.read_text(encoding="utf-8")
    imported = {
        line.split()[1].split(".")[0]
        for line in text.splitlines()
        if line.startswith(("import ", "from "))
    }
    stdlib = {"json", "re", "subprocess", "sys", "pathlib", "argparse"}
    assert imported <= stdlib, f"stdlib 外の import: {sorted(imported - stdlib)}"
