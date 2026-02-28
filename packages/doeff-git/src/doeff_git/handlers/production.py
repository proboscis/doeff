"""Production handlers for doeff-git effects."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from doeff import Effect, Pass, Resume, do
from doeff_git.effects import CreatePR, GitCommit, GitDiff, GitPull, GitPush, MergePR
from doeff_git.exceptions import GitCommandError
from doeff_git.types import MergeStrategy, PRHandle

ProtocolHandler = Callable[[Any, Any], Any]


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

    raw_value = getattr(strategy, "value", strategy)
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


class GitLocalHandler:
    """Handler for local git CLI operations."""

    def handle_commit(self, effect: GitCommit) -> str:
        if effect.all:
            _run_command(["git", "add", "-A"], cwd=effect.work_dir)

        _run_command(["git", "commit", "-m", effect.message], cwd=effect.work_dir)
        result = _run_command(["git", "rev-parse", "HEAD"], cwd=effect.work_dir)
        return result.stdout.strip()

    def handle_diff(self, effect: GitDiff) -> str:
        args = ["git", "diff"]
        if effect.staged:
            args.append("--staged")
        result = _run_command(args, cwd=effect.work_dir)
        return result.stdout

    def handle_push(self, effect: GitPush) -> None:
        branch = effect.branch or _current_branch(effect.work_dir)

        args = ["git", "push"]
        if effect.force:
            args.append("--force")

        if effect.set_upstream:
            args.extend(["-u", effect.remote, branch])
        else:
            args.extend([effect.remote, branch])

        _run_command(args, cwd=effect.work_dir)

    def handle_pull(self, effect: GitPull) -> None:
        args = ["git", "pull"]
        if effect.rebase:
            args.append("--rebase")
        args.append(effect.remote)
        if effect.branch:
            args.append(effect.branch)

        _run_command(args, cwd=effect.work_dir)


class GitHubHandler:
    """Handler for hosting operations backed by GitHub CLI."""

    def handle_create_pr(self, effect: CreatePR) -> PRHandle:
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
        ]

        if effect.draft:
            args.append("--draft")

        for label in effect.labels or []:
            args.extend(["--label", label])

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

    def handle_merge_pr(self, effect: MergePR) -> None:
        strategy = _normalize_strategy(effect.strategy)

        args = ["gh", "pr", "merge", _merge_selector(effect.pr)]
        if strategy is MergeStrategy.MERGE:
            args.append("--merge")
        elif strategy is MergeStrategy.REBASE:
            args.append("--rebase")
        elif strategy is MergeStrategy.SQUASH:
            args.append("--squash")

        if effect.delete_branch:
            args.append("--delete-branch")

        _run_command(args, cwd=effect.pr.work_dir)


def production_handlers(
    *,
    local_handler: GitLocalHandler | None = None,
    github_handler: GitHubHandler | None = None,
) -> ProtocolHandler:
    """Build the production protocol handler for git and GitHub effects."""

    local = local_handler or GitLocalHandler()
    hosting = github_handler or GitHubHandler()

    @do
    def handler(effect: Effect, k: Any):
        if isinstance(effect, GitCommit):
            return (yield Resume(k, local.handle_commit(effect)))
        if isinstance(effect, GitDiff):
            return (yield Resume(k, local.handle_diff(effect)))
        if isinstance(effect, GitPush):
            local.handle_push(effect)
            return (yield Resume(k, None))
        if isinstance(effect, GitPull):
            local.handle_pull(effect)
            return (yield Resume(k, None))
        if isinstance(effect, CreatePR):
            return (yield Resume(k, hosting.handle_create_pr(effect)))
        if isinstance(effect, MergePR):
            hosting.handle_merge_pr(effect)
            return (yield Resume(k, None))
        return (yield Pass())

    return handler


__all__ = [
    "GitHubHandler",
    "GitLocalHandler",
    "ProtocolHandler",
    "production_handlers",
]
