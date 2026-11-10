"""Absence of the retired Rust crate in-tree + existence of the rollback tag (issue #575 M3).

Enforcement of the ADR-DOE-AGENTS-004 retired-impl-lives-only-in-git-history law:
the only rollback coordinate is the git tag agentd-rust-final (the commit just before deletion), and
no frozen in-tree copy exists (the counterexample, "delete the crate without creating a rollback tag,
making rollback availability a verbal promise", is mechanized).

The crate path is not written as a literal in the source (consistent with the backflow-prevention semgrep rule
doeff-agentd-retired-crate-reference-forbidden) -- it is built at run time.

TDD red (2026-07-30): the crate still exists and the tag is not applied, so
red until the M3 implementation (deletion) and tag push.
"""

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CRATE_REL = Path("packages") / "doeff-agentd"
ROLLBACK_TAG = "agentd-rust-final"


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_retired_rust_crate_is_absent_from_tree() -> None:
    crate_dir = REPO_ROOT / CRATE_REL
    assert not crate_dir.exists(), (
        f"the retired Rust crate remains in-tree: {crate_dir} -- "
        f"rollback is only via git tag {ROLLBACK_TAG} (ADR-DOE-AGENTS-004 R7)"
    )


def test_rollback_tag_preserves_the_retired_crate() -> None:
    # The subject of this law is the git history itself -- in an inspection tree that does not carry history (remote_check copies only the
    # git ls-files listing; no .git) it cannot be observed, so use an explicit skip rather than fail
    # (do not turn an unobservable property into a false red). In a checkout with git metadata,
    # a missing tag = fail as before (measured 2026-08-17 node-h, route-3da438e018).
    inside_repo = _git("rev-parse", "--git-dir")
    if inside_repo.returncode != 0:
        pytest.skip(
            "inspection tree without git metadata -- the rollback tag is a property of git history and "
            "cannot be observed from this tree (the law is enforced on the git checkout side)"
        )
    rev = _git("rev-parse", "--verify", f"refs/tags/{ROLLBACK_TAG}")
    assert rev.returncode == 0, (
        f"rollback tag {ROLLBACK_TAG} not found (run `git fetch --tags`) -- "
        "deleting the crate without a tag violates retired-impl-lives-only-in-git-history"
    )
    ls = _git("ls-tree", "--name-only", ROLLBACK_TAG, "packages/")
    assert ls.returncode == 0, ls.stderr
    assert str(CRATE_REL) in ls.stdout.splitlines(), (
        f"the tree of tag {ROLLBACK_TAG} has no retired crate -- the rollback coordinate is broken"
    )
