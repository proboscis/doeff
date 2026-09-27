"""ADR-DOE-ENFORCE-001 R7 / R9: enforcement 台帳の著述時(git pre-commit)検査と生成の挙動。

実弾 = f47f0a4b(ADR-DOE-HY-004 新設・defadr +1 / deftest +2 / law +1)が台帳
未更新のまま land queue を通らず直接 main へ届き、初検出が翌朝の日次 verify
(doeff-verify-20260821-065005)まで遅れて無実の 2 便(L43/L44)が容疑に挙がった。
着地の窓は力学のみ(2026-08-17 operator 裁定)なので、記帳漏れを構造的に
止められる検出点は著述時 = git commit 時だけ。その機構をここで固定する:

- 勘定の単一の家 scripts/check_enforcement_ledger.py(stdlib 単独・venv 不要)
- working tree 突合(既定)と staged 断面(index)突合(--staged・hook が使う面)
- tracked hook 原本 scripts/git-hooks/pre-commit(対象 path が staged の時だけ
  検査し、作り直し(rebase / cherry-pick / sequencer)中は判定しない)

R9(2026-09-28・agora-redesign #802)で台帳は数から名の一覧(生成物)になった:

- --write が木から台帳を作り、外れた項目を名前で申告する
- ADR は Hy の形として読む(註・文字列・#_ の中の綴りを数えない)
- hook は commit 自身の増減が台帳に写っていれば、HEAD に既に在るずれで塞がない
- 並行する 2 便の追加は git の merge で行ごとに合わさる(数は合わさらなかった)
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
ADR = "defadr_fixture_001.hy"


def _adr_text(laws: list[str], adr_id: str = "ADR-FIXTURE-001") -> str:
    body = "\n".join(f'   (law {name} :statement "x")' for name in laws)
    return f";; fixture\n(defadr {adr_id}\n  :laws\n  [\n{body}\n  ])\n"


def _ledger(adrs: dict[str, tuple[str, list[str]]]) -> str:
    """{file 名: (ADR id, law 名の並び)} から台帳(生成物と同じ形)を作る。"""
    laws = sorted(f"{adr_id} {name}" for adr_id, names in adrs.values() for name in names)
    ledger = {
        "_comment": "fixture ledger",
        "defadr_files": sorted(adrs),
        "semgrep_rules": ["fixture-rule"],
        "adr_laws": laws,
        "adr_deftest_enforcements": [],
        "adr_defsemgrep_enforcements": [],
    }
    return json.dumps(ledger, ensure_ascii=False, indent=2) + "\n"


def _write_fixture_tree(root: Path, *, laws: int, ledger_laws: int) -> None:
    """最小の enforcement 資産一式を敷く(defadr 1 file・semgrep rule 1 本)。"""
    adr_dir = root / "docs" / "adr"
    adr_dir.mkdir(parents=True, exist_ok=True)
    (adr_dir / ADR).write_text(_adr_text([f"fixture-{i}" for i in range(laws)]), encoding="utf-8")
    (root / ".semgrep.yaml").write_text("rules:\n  - id: fixture-rule\n", encoding="utf-8")
    (adr_dir / "enforcement-ledger.json").write_text(
        _ledger({ADR: ("ADR-FIXTURE-001", [f"fixture-{i}" for i in range(ledger_laws)])}),
        encoding="utf-8",
    )


def _run_checker(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CHECKER), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        check=True, capture_output=True, text=True,
    ).stdout


def test_checker_green_on_consistent_tree(tmp_path: Path) -> None:
    _write_fixture_tree(tmp_path, laws=2, ledger_laws=2)
    proc = _run_checker(tmp_path, f"--root={tmp_path}")
    assert proc.returncode == 0, f"一致する tree で赤: {proc.stderr}"


def test_checker_detects_unrecorded_addition(tmp_path: Path) -> None:
    # 増加の記帳漏れ(今回の実弾の形)— law が増えたのに台帳が据え置き。
    _write_fixture_tree(tmp_path, laws=3, ledger_laws=2)
    proc = _run_checker(tmp_path, f"--root={tmp_path}")
    assert proc.returncode == 1, "記帳漏れ(木 > 台帳)を検出しない"
    assert "ADR-FIXTURE-001 fixture-2" in proc.stderr, f"足した項目を名指さない: {proc.stderr}"


def test_checker_detects_silent_drop(tmp_path: Path) -> None:
    # 黙った喪失(侵食)— R5 の本丸。消えた項目は名前で出る。
    _write_fixture_tree(tmp_path, laws=1, ledger_laws=2)
    proc = _run_checker(tmp_path, f"--root={tmp_path}")
    assert proc.returncode == 1, "侵食(木 < 台帳)を検出しない"
    assert "消えた" in proc.stderr, proc.stderr
    assert "ADR-FIXTURE-001 fixture-1" in proc.stderr, f"消えた項目を名指さない: {proc.stderr}"


def test_checker_does_not_count_comments_strings_or_discarded_forms(tmp_path: Path) -> None:
    # 字面の数え(`(law ` の出現数)は註・文字列の中の綴りも数えた(2026-09-28 実測 law 134 / 形 132)。
    _write_fixture_tree(tmp_path, laws=1, ledger_laws=1)
    adr = tmp_path / "docs" / "adr" / ADR
    adr.write_text(
        adr.read_text(encoding="utf-8")
        + ';; (law in-a-comment :statement "x")\n'
        + '(setv note "(law in-a-string :statement \\"x\\")")\n'
        + '#_ (law discarded :statement "x")\n'
        + "(setv raw #[[ (law in-a-bracket-string) ]])\n",
        encoding="utf-8",
    )
    proc = _run_checker(tmp_path, f"--root={tmp_path}")
    assert proc.returncode == 0, f"註・文字列・#_ の中の綴りを law に数えた: {proc.stderr}"


def test_old_count_ledger_is_refused_with_the_repair_command(tmp_path: Path) -> None:
    _write_fixture_tree(tmp_path, laws=2, ledger_laws=2)
    ledger = tmp_path / "docs" / "adr" / "enforcement-ledger.json"
    ledger.write_text(json.dumps({"defadr_files": 1, "semgrep_rules": 1, "adr_laws": 2,
                                  "adr_deftest_enforcements": 0, "adr_defsemgrep_enforcements": 0}),
                      encoding="utf-8")
    proc = _run_checker(tmp_path, f"--root={tmp_path}")
    assert proc.returncode == 1, "旧い数の台帳を通した"
    assert "make enforcement-ledger" in proc.stderr, f"直し方(生成の命令)を示さない: {proc.stderr}"


def test_write_generates_the_ledger_and_names_removed_items(tmp_path: Path) -> None:
    _write_fixture_tree(tmp_path, laws=1, ledger_laws=2)
    written = _run_checker(tmp_path, f"--root={tmp_path}", "--write")
    assert written.returncode == 0, written.stderr
    assert "ADR-FIXTURE-001 fixture-1" in written.stderr, f"外した項目を名前で申告しない: {written.stderr}"
    assert _run_checker(tmp_path, f"--root={tmp_path}").returncode == 0, "生成した台帳が木と一致しない"
    data = json.loads((tmp_path / "docs" / "adr" / "enforcement-ledger.json").read_text(encoding="utf-8"))
    assert data["adr_laws"] == ["ADR-FIXTURE-001 fixture-0"]


def test_staged_mode_catches_stage_forgetting(tmp_path: Path) -> None:
    # working tree は一致・index は不一致(台帳の直しを stage し忘れた形)。
    # working tree 突合はこれを素通しする — hook が --staged で index を読む理由。
    _write_fixture_tree(tmp_path, laws=2, ledger_laws=2)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _write_fixture_tree(tmp_path, laws=3, ledger_laws=3)
    _git(tmp_path, "add", f"docs/adr/{ADR}")  # 台帳は stage しない

    worktree = _run_checker(tmp_path, f"--root={tmp_path}")
    assert worktree.returncode == 0, f"working tree は一致のはず: {worktree.stderr}"
    staged = _run_checker(tmp_path, f"--root={tmp_path}", "--staged")
    assert staged.returncode == 1, "staged 断面の不一致(stage し忘れ)を検出しない"


def _drifted_head(tmp_path: Path) -> Path:
    """HEAD 自体が台帳とずれた repo(ずれを持ち込んだ commit が hook を外して入った形)。"""
    _write_fixture_tree(tmp_path, laws=2, ledger_laws=3)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "drift", "--no-verify")
    return tmp_path


def test_staged_mode_does_not_blame_a_commit_for_drift_already_on_head(tmp_path: Path) -> None:
    # 2026-09-26 e15f9516 の後、無関係な 10 便以上がこの形で塞がれ SKIP で外した。
    repo = _drifted_head(tmp_path)
    adr = repo / "docs" / "adr" / ADR
    adr.write_text(_adr_text(["fixture-0", "fixture-1", "added-here"]), encoding="utf-8")
    ledger = repo / "docs" / "adr" / "enforcement-ledger.json"
    ledger.write_text(
        _ledger({ADR: ("ADR-FIXTURE-001", ["fixture-0", "fixture-1", "fixture-2", "added-here"])}),
        encoding="utf-8",
    )
    _git(repo, "add", "-A")
    proc = _run_checker(repo, f"--root={repo}", "--staged")
    assert proc.returncode == 0, f"自分の増減を記した commit を HEAD のずれで塞いだ: {proc.stderr}"
    assert "HEAD に既に在った" in proc.stderr, f"残っているずれを申告しない: {proc.stderr}"
    assert "ADR-FIXTURE-001 fixture-2" in proc.stderr, f"残っているずれを名指さない: {proc.stderr}"


def test_staged_mode_still_blocks_the_commits_own_unrecorded_change_on_drifted_head(tmp_path: Path) -> None:
    repo = _drifted_head(tmp_path)
    adr = repo / "docs" / "adr" / ADR
    adr.write_text(_adr_text(["fixture-0", "fixture-1", "added-here"]), encoding="utf-8")
    _git(repo, "add", "-A")  # 台帳は動かさない
    proc = _run_checker(repo, f"--root={repo}", "--staged")
    assert proc.returncode == 1, "この commit 自身の記帳漏れを HEAD のずれに紛れて通した"


def test_parallel_additions_merge_into_a_consistent_ledger(tmp_path: Path) -> None:
    # 数の台帳では 2 便が同じ「6 → 7」を刻み、merge が黙って 7 に合わせた(2026-09-17 ed98775a の形)。
    # 名の一覧なら別々の ADR への追加は行ごとに合わさり、merge の結果が木と一致する。
    repo = tmp_path
    adr_dir = repo / "docs" / "adr"
    adr_dir.mkdir(parents=True)
    (repo / ".semgrep.yaml").write_text("rules:\n  - id: fixture-rule\n", encoding="utf-8")
    first, second = "defadr_fixture_001.hy", "defadr_fixture_002.hy"
    books = {first: ("ADR-FIXTURE-001", ["a1", "a2", "a3"]),
             second: ("ADR-FIXTURE-002", ["b1", "b2", "b3"])}
    for name, (adr_id, laws) in books.items():
        (adr_dir / name).write_text(_adr_text(laws, adr_id), encoding="utf-8")
    (adr_dir / "enforcement-ledger.json").write_text(_ledger(books), encoding="utf-8")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base", "--no-verify")

    for branch, name, law in (("left", first, "a4"), ("right", second, "b4")):
        _git(repo, "checkout", "-q", "-b", branch, "main")
        adr_id, laws = books[name]
        (adr_dir / name).write_text(_adr_text([*laws, law], adr_id), encoding="utf-8")
        assert _run_checker(repo, f"--root={repo}", "--write").returncode == 0
        _git(repo, "commit", "-q", "-am", branch, "--no-verify")

    _git(repo, "checkout", "-q", "left")
    _git(repo, "merge", "-q", "--no-edit", "right")
    proc = _run_checker(repo, f"--root={repo}")
    assert proc.returncode == 0, f"並行する 2 便の追加の merge が台帳と一致しない: {proc.stderr}"
    data = json.loads((adr_dir / "enforcement-ledger.json").read_text(encoding="utf-8"))
    assert len(data["adr_laws"]) == 8


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


def test_hook_blocks_inconsistent_staged_snapshot(tmp_path: Path) -> None:
    repo = _install_fixture_repo_with_hook(tmp_path, laws=3, ledger_laws=2)
    proc = _run_hook(repo)
    assert proc.returncode == 1, f"不一致の staged 断面を hook が通した: {proc.stderr}"


def test_hook_passes_consistent_staged_snapshot(tmp_path: Path) -> None:
    repo = _install_fixture_repo_with_hook(tmp_path, laws=2, ledger_laws=2)
    proc = _run_hook(repo)
    assert proc.returncode == 0, f"一致する staged 断面で hook が赤: {proc.stderr}"


def test_hook_silent_during_rebase_replay(tmp_path: Path) -> None:
    # 作り直し(rebase / cherry-pick)中は判定しない — 着地の窓の追随・replay を
    # 塞がないため(prepare-commit-msg hook と同じ guard)。
    repo = _install_fixture_repo_with_hook(tmp_path, laws=3, ledger_laws=2)
    (repo / ".git" / "rebase-merge").mkdir()
    proc = _run_hook(repo)
    assert proc.returncode == 0, "rebase 中の replay を hook が塞いだ"


def test_hook_skips_commits_not_touching_enforcement_assets(tmp_path: Path) -> None:
    # enforcement 資産に触れない commit は素通し(index が不一致でも、その commit の
    # 責任ではない — 検査は触った commit に課す)。
    repo = _install_fixture_repo_with_hook(tmp_path, laws=2, ledger_laws=2)
    _git(repo, "commit", "-q", "-m", "seed", "--no-verify")
    (repo / "README.md").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    proc = _run_hook(repo)
    assert proc.returncode == 0, f"無関係な commit を hook が検査した: {proc.stderr}"


def test_pytest_ledger_test_uses_the_same_counting_home() -> None:
    # 勘定の第 2 定義点を作らない — 既定 pytest の R5 検査(tests/test_enforcement_ledger.py)
    # は hook と同じ家(scripts/check_enforcement_ledger.py)を消費する。
    text = (ROOT / "tests" / "test_enforcement_ledger.py").read_text(encoding="utf-8")
    assert "check_enforcement_ledger" in text, (
        "R5 検査が勘定の家(scripts/check_enforcement_ledger.py)を消費していない — "
        "regex が乖離した日から hook 緑 = verify 緑が成立しなくなる(ADR-DOE-ENFORCE-001 R7)"
    )


def test_checker_is_stdlib_only() -> None:
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
    stdlib = {"json", "re", "subprocess", "sys", "pathlib", "argparse", "collections", "dataclasses"}
    assert imported <= stdlib, f"stdlib 外の import: {sorted(imported - stdlib)}"
