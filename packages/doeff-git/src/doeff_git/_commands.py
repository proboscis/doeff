"""The git and gh commands the production handlers run, one function per effect.

Inside the package: users install ``git_local_handler`` and ``github_handler`` (or
``production_handlers``), which call these.
"""


import re
import subprocess
from pathlib import Path
from typing import Any

from doeff_git._effects import CreatePR, GitCommit, GitDiff, GitPull, GitPush, MergePR
from doeff_git._exceptions import GitCommandError
from doeff_git._types import MergeStrategy, PRHandle


def _run_command(
    args: list[str],
    *,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run git/gh command and raise GitCommandError on failure."""
    try:
        return subprocess.run(
            args,
            cwd=str(cwd) if cwd else None,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as error:
        raise GitCommandError.from_subprocess_error(error, cwd=str(cwd) if cwd else None) from error


def _current_branch(work_dir: Path) -> str:
    result = _run_command(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=work_dir)
    return result.stdout.strip()


def _extract_pr_url(raw_output: str) -> str:
    lines = [line.strip() for line in raw_output.splitlines() if line.strip()]
    if not lines:
        raise ValueError("gh pr create returned no output")
    return lines[-1]


def _extract_pr_number(pr_url: str) -> int:
    match = re.search(r"/(\d+)$", pr_url)
    if not match:
        raise ValueError(f"Could not parse PR number from URL: {pr_url}")
    return int(match.group(1))


def _normalize_strategy(strategy: MergeStrategy | str | Any | None) -> MergeStrategy:
    if strategy is None:
        return MergeStrategy.MERGE

    if isinstance(strategy, MergeStrategy):
        return strategy

    raw_value = strategy.value if hasattr(strategy, "value") else strategy
    if isinstance(raw_value, str):
        normalized = raw_value.strip().lower()
        for candidate in MergeStrategy:
            if candidate.value == normalized:
                return candidate

    raise ValueError(f"Unsupported merge strategy: {strategy!r}")


def _merge_selector(pr: PRHandle) -> str:
    if pr.url:
        return pr.url
    return str(pr.number)


def commit(effect: GitCommit) -> str:
    if effect.all:
        _run_command(["git", "add", "-A"], cwd=effect.work_dir)

    _run_command(["git", "commit", "-m", effect.message], cwd=effect.work_dir)
    result = _run_command(["git", "rev-parse", "HEAD"], cwd=effect.work_dir)
    return result.stdout.strip()


def diff(effect: GitDiff) -> str:
    args = ["git", "diff", *(["--staged"] if effect.staged else [])]
    result = _run_command(args, cwd=effect.work_dir)
    return result.stdout


def push(effect: GitPush) -> None:
    branch = effect.branch or _current_branch(effect.work_dir)
    args = [
        "git",
        "push",
        *(["--force"] if effect.force else []),
        *(["-u"] if effect.set_upstream else []),
        effect.remote,
        branch,
    ]
    _run_command(args, cwd=effect.work_dir)


def pull(effect: GitPull) -> None:
    args = [
        "git",
        "pull",
        *(["--rebase"] if effect.rebase else []),
        effect.remote,
        *([effect.branch] if effect.branch else []),
    ]
    _run_command(args, cwd=effect.work_dir)


def create_pr(effect: CreatePR) -> PRHandle:
    branch = effect.head or _current_branch(effect.work_dir)
    args = [
        "gh",
        "pr",
        "create",
        "--title",
        effect.title,
        "--base",
        effect.target,
        "--head",
        branch,
        "--body",
        effect.body or "",
        *(["--draft"] if effect.draft else []),
        *(part for label in effect.labels or [] for part in ("--label", label)),
    ]

    result = _run_command(args, cwd=effect.work_dir)
    pr_url = _extract_pr_url(result.stdout)
    pr_number = _extract_pr_number(pr_url)

    return PRHandle(
        url=pr_url,
        number=pr_number,
        title=effect.title,
        branch=branch,
        target=effect.target,
        status="open",
        work_dir=effect.work_dir,
    )


_STRATEGY_FLAGS: dict[MergeStrategy, str] = {
    MergeStrategy.MERGE: "--merge",
    MergeStrategy.REBASE: "--rebase",
    MergeStrategy.SQUASH: "--squash",
}


def merge_pr(effect: MergePR) -> None:
    strategy = _normalize_strategy(effect.strategy)
    args = [
        "gh",
        "pr",
        "merge",
        _merge_selector(effect.pr),
        _STRATEGY_FLAGS[strategy],
        *(["--delete-branch"] if effect.delete_branch else []),
    ]
    _run_command(args, cwd=effect.pr.work_dir)
