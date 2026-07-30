"""退役 Rust crate の in-tree 不在 + rollback tag の存在(issue #575 M3)。

ADR-DOE-AGENTS-004 retired-impl-lives-only-in-git-history law の enforcement:
rollback 座標は git tag agentd-rust-final(削除直前 commit)だけであり、
in-tree 凍結コピーは存在しない(反例:「rollback tag を打たずに crate を
削除し、rollback 可用性を口約束にする」を機械化)。

crate path はソースに literal で書かない(逆流防止 semgrep rule
doeff-agentd-retired-crate-reference-forbidden と整合)— 実行時に構築する。

TDD red(2026-07-30): crate 現存 + tag 未付与のため、M3 実装(削除)と
tag push まで red。
"""

import subprocess
from pathlib import Path

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
        f"退役 Rust crate が in-tree に残っている: {crate_dir} — "
        f"rollback は git tag {ROLLBACK_TAG} のみ(ADR-DOE-AGENTS-004 R7)"
    )


def test_rollback_tag_preserves_the_retired_crate() -> None:
    rev = _git("rev-parse", "--verify", f"refs/tags/{ROLLBACK_TAG}")
    assert rev.returncode == 0, (
        f"rollback tag {ROLLBACK_TAG} が見つからない(`git fetch --tags` を実行)— "
        "tag なしの crate 削除は retired-impl-lives-only-in-git-history 違反"
    )
    ls = _git("ls-tree", "--name-only", ROLLBACK_TAG, "packages/")
    assert ls.returncode == 0, ls.stderr
    assert str(CRATE_REL) in ls.stdout.splitlines(), (
        f"tag {ROLLBACK_TAG} の tree に退役 crate が無い — rollback 座標が壊れている"
    )
