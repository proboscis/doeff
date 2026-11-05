"""ADR-DOE-ENFORCE-001 R7 / R9: authoring-time (git pre-commit) check and generation behavior of the enforcement ledger.

The live incident = f47f0a4b (new ADR-DOE-HY-004, defadr +1 / deftest +2 / law +1) reached main directly
without the ledger being updated and without going through the land queue; the first detection was delayed until the next morning's daily verify
(doeff-verify-20260821-065005), and two innocent changes (L43/L44) came under suspicion.
The landing window is supposed to look only at dynamics, so the only detection point that can structurally stop a missed entry
is authoring time = git commit time. This pins down that mechanism:

- The single home of the counting, scripts/check_enforcement_ledger.py (stdlib only, no venv needed)
- Working tree comparison (default) and staged snapshot (index) comparison (--staged, the surface the hook uses)
- The tracked hook original scripts/git-hooks/pre-commit (checks only when target paths are staged,
  and makes no judgment during a rebuild (rebase / cherry-pick / sequencer))

With R9 (2026-09-28) the ledger changed from counts to a list of names (a generated artifact):

- --write builds the ledger from the tree and reports dropped items by name
- ADRs are read as Hy forms (spellings inside comments, strings, and #_ are not counted)
- If the commit's own additions and removals are reflected in the ledger, the hook does not block on drift already present in HEAD
- Additions from two concurrent changes combine line by line in a git merge (counts did not combine)
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
    """Build a ledger (same shape as the generated artifact) from {file name: (ADR id, sequence of law names)}."""
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
    """Lay down a minimal set of enforcement assets (one defadr file, one semgrep rule)."""
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
    assert proc.returncode == 0, f"red on a matching tree: {proc.stderr}"


def test_checker_detects_unrecorded_addition(tmp_path: Path) -> None:
    # Missed entry for an addition (the shape of this incident) — a law was added but the ledger stayed as is.
    _write_fixture_tree(tmp_path, laws=3, ledger_laws=2)
    proc = _run_checker(tmp_path, f"--root={tmp_path}")
    assert proc.returncode == 1, "does not detect a missed entry (tree > ledger)"
    assert "ADR-FIXTURE-001 fixture-2" in proc.stderr, f"does not name the added item: {proc.stderr}"


def test_checker_detects_silent_drop(tmp_path: Path) -> None:
    # Silent loss (erosion) — the core of R5. The removed item appears by name.
    _write_fixture_tree(tmp_path, laws=1, ledger_laws=2)
    proc = _run_checker(tmp_path, f"--root={tmp_path}")
    assert proc.returncode == 1, "does not detect erosion (tree < ledger)"
    assert "消えた" in proc.stderr, proc.stderr
    assert "ADR-FIXTURE-001 fixture-1" in proc.stderr, f"does not name the removed item: {proc.stderr}"


def test_checker_does_not_count_comments_strings_or_discarded_forms(tmp_path: Path) -> None:
    # Literal counting (occurrences of `(law `) also counted spellings inside comments and strings (measured 2026-09-28: law 134 / forms 132).
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
    assert proc.returncode == 0, f"counted spellings inside comments, strings, and #_ as laws: {proc.stderr}"


def test_old_count_ledger_is_refused_with_the_repair_command(tmp_path: Path) -> None:
    _write_fixture_tree(tmp_path, laws=2, ledger_laws=2)
    ledger = tmp_path / "docs" / "adr" / "enforcement-ledger.json"
    ledger.write_text(json.dumps({"defadr_files": 1, "semgrep_rules": 1, "adr_laws": 2,
                                  "adr_deftest_enforcements": 0, "adr_defsemgrep_enforcements": 0}),
                      encoding="utf-8")
    proc = _run_checker(tmp_path, f"--root={tmp_path}")
    assert proc.returncode == 1, "passed an old count-based ledger"
    assert "make enforcement-ledger" in proc.stderr, f"does not show the fix (the generation command): {proc.stderr}"


def test_write_generates_the_ledger_and_names_removed_items(tmp_path: Path) -> None:
    _write_fixture_tree(tmp_path, laws=1, ledger_laws=2)
    written = _run_checker(tmp_path, f"--root={tmp_path}", "--write")
    assert written.returncode == 0, written.stderr
    assert "ADR-FIXTURE-001 fixture-1" in written.stderr, f"does not report the dropped item by name: {written.stderr}"
    assert _run_checker(tmp_path, f"--root={tmp_path}").returncode == 0, "the generated ledger does not match the tree"
    data = json.loads((tmp_path / "docs" / "adr" / "enforcement-ledger.json").read_text(encoding="utf-8"))
    assert data["adr_laws"] == ["ADR-FIXTURE-001 fixture-0"]


def test_staged_mode_catches_stage_forgetting(tmp_path: Path) -> None:
    # Working tree matches, index does not (the ledger fix was forgotten to be staged).
    # The working tree comparison lets this through — the reason the hook reads the index with --staged.
    _write_fixture_tree(tmp_path, laws=2, ledger_laws=2)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _write_fixture_tree(tmp_path, laws=3, ledger_laws=3)
    _git(tmp_path, "add", f"docs/adr/{ADR}")  # the ledger is not staged

    worktree = _run_checker(tmp_path, f"--root={tmp_path}")
    assert worktree.returncode == 0, f"the working tree should match: {worktree.stderr}"
    staged = _run_checker(tmp_path, f"--root={tmp_path}", "--staged")
    assert staged.returncode == 1, "does not detect a staged snapshot mismatch (forgot to stage)"


def _drifted_head(tmp_path: Path) -> Path:
    """A repo whose HEAD itself has drifted from the ledger (the shape where the commit that introduced the drift bypassed the hook)."""
    _write_fixture_tree(tmp_path, laws=2, ledger_laws=3)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "drift", "--no-verify")
    return tmp_path


def test_staged_mode_does_not_blame_a_commit_for_drift_already_on_head(tmp_path: Path) -> None:
    # After e15f9516 on 2026-09-26, more than 10 unrelated changes were blocked in this shape and bypassed with SKIP.
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
    assert proc.returncode == 0, f"blocked a commit that recorded its own changes because of drift in HEAD: {proc.stderr}"
    assert "HEAD に既に在った" in proc.stderr, f"does not report the remaining drift: {proc.stderr}"
    assert "ADR-FIXTURE-001 fixture-2" in proc.stderr, f"does not name the remaining drift: {proc.stderr}"


def test_staged_mode_still_blocks_the_commits_own_unrecorded_change_on_drifted_head(tmp_path: Path) -> None:
    repo = _drifted_head(tmp_path)
    adr = repo / "docs" / "adr" / ADR
    adr.write_text(_adr_text(["fixture-0", "fixture-1", "added-here"]), encoding="utf-8")
    _git(repo, "add", "-A")  # the ledger is not moved
    proc = _run_checker(repo, f"--root={repo}", "--staged")
    assert proc.returncode == 1, "passed this commit's own missed entry, hidden among the drift in HEAD"
    # The red report names "this commit's part" and "the part already in HEAD" separately — otherwise
    # the reader takes it as the mainline's drift and bypasses with --no-verify (in this shape b32941c3 on 2026-09-28
    # put one law onto the mainline without listing it in the ledger).
    own, _, inherited = proc.stderr.partition("HEAD に既に在ったずれ")
    assert "この commit 自身の増減" in own, f"does not say the red comes from this commit: {proc.stderr}"
    assert "ADR-FIXTURE-001 added-here" in own, f"does not name this commit's missed entry: {proc.stderr}"
    assert "ADR-FIXTURE-001 fixture-2" not in own, f"mixed HEAD's drift into this commit's part: {proc.stderr}"
    assert "ADR-FIXTURE-001 fixture-2" in inherited, f"does not show HEAD's drift for reference: {proc.stderr}"


def test_staged_mode_on_clean_head_names_only_the_commits_own_change(tmp_path: Path) -> None:
    # When the mainline matches, the red names only this commit's missed entry and does not print the HEAD drift section.
    _write_fixture_tree(tmp_path, laws=2, ledger_laws=2)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "clean", "--no-verify")
    (tmp_path / "docs" / "adr" / ADR).write_text(
        _adr_text(["fixture-0", "fixture-1", "added-here"]), encoding="utf-8"
    )
    _git(tmp_path, "add", "-A")
    proc = _run_checker(tmp_path, f"--root={tmp_path}", "--staged")
    assert proc.returncode == 1, "passed this commit's missed entry"
    assert "ADR-FIXTURE-001 added-here" in proc.stderr, proc.stderr
    assert "HEAD に既に在ったずれ" not in proc.stderr, f"called a matching HEAD drifted: {proc.stderr}"


def test_parallel_additions_merge_into_a_consistent_ledger(tmp_path: Path) -> None:
    # With a count-based ledger, two changes stamped the same "6 -> 7" and the merge silently settled on 7 (the shape of ed98775a on 2026-09-17).
    # With a list of names, additions to separate ADRs combine line by line, and the merge result matches the tree.
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
    assert proc.returncode == 0, f"the merge of two concurrent additions does not match the ledger: {proc.stderr}"
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
    assert proc.returncode == 1, f"the hook passed a mismatching staged snapshot: {proc.stderr}"


def test_hook_passes_consistent_staged_snapshot(tmp_path: Path) -> None:
    repo = _install_fixture_repo_with_hook(tmp_path, laws=2, ledger_laws=2)
    proc = _run_hook(repo)
    assert proc.returncode == 0, f"the hook is red on a matching staged snapshot: {proc.stderr}"


def test_hook_silent_during_rebase_replay(tmp_path: Path) -> None:
    # No judgment during a rebuild (rebase / cherry-pick) — so as not to block the landing window's follow-up or replay
    # (same guard as the prepare-commit-msg hook).
    repo = _install_fixture_repo_with_hook(tmp_path, laws=3, ledger_laws=2)
    (repo / ".git" / "rebase-merge").mkdir()
    proc = _run_hook(repo)
    assert proc.returncode == 0, "the hook blocked a replay during rebase"


def test_hook_skips_commits_not_touching_enforcement_assets(tmp_path: Path) -> None:
    # A commit that touches no enforcement assets passes through (even if the index mismatches, it is not that commit's
    # responsibility — the check is imposed on the commit that touched them).
    repo = _install_fixture_repo_with_hook(tmp_path, laws=2, ledger_laws=2)
    _git(repo, "commit", "-q", "-m", "seed", "--no-verify")
    (repo / "README.md").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    proc = _run_hook(repo)
    assert proc.returncode == 0, f"the hook checked an unrelated commit: {proc.stderr}"


def test_pytest_ledger_test_uses_the_same_counting_home() -> None:
    # Do not create a second definition point of counting — the default pytest R5 check (tests/test_enforcement_ledger.py)
    # consumes the same home as the hook (scripts/check_enforcement_ledger.py).
    text = (ROOT / "tests" / "test_enforcement_ledger.py").read_text(encoding="utf-8")
    assert "check_enforcement_ledger" in text, (
        "the R5 check does not consume the counting home (scripts/check_enforcement_ledger.py) — "
        "from the day the regexes diverge, hook green = verify green no longer holds (ADR-DOE-ENFORCE-001 R7)"
    )


def test_checker_is_stdlib_only() -> None:
    # The hook cannot depend on the venv state (it runs even in a mid-rebase worktree or an unsynced machine)
    # — red if the counting home imports anything outside stdlib.
    if not CHECKER.exists():
        pytest.fail("the counting home scripts/check_enforcement_ledger.py does not exist")
    text = CHECKER.read_text(encoding="utf-8")
    imported = {
        line.split()[1].split(".")[0]
        for line in text.splitlines()
        if line.startswith(("import ", "from "))
    }
    stdlib = {"json", "re", "subprocess", "sys", "pathlib", "argparse", "collections", "dataclasses"}
    assert imported <= stdlib, f"imports outside stdlib: {sorted(imported - stdlib)}"
