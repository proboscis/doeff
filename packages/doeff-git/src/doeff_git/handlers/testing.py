"""Mock handlers for doeff-git effects."""


import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from doeff import Effect, Pass, Resume, do
from doeff_git.effects import CreatePR, GitCommit, GitDiff, GitPull, GitPush, MergePR
from doeff_git.types import PRHandle

ProtocolHandler = Callable[[Any, Any], Any]


@dataclass
class MockGitRuntime:
    """In-memory deterministic runtime for git effects."""

    branch_by_work_dir: Mapping[str, str] = field(default_factory=dict)
    diff_by_work_dir: Mapping[str, str] = field(default_factory=dict)
    commits: list[tuple[str, str, bool]] = field(default_factory=list)
    pushes: list[tuple[str, str, str, bool, bool]] = field(default_factory=list)
    pulls: list[tuple[str, str, bool, str | None]] = field(default_factory=list)
    prs: dict[int, PRHandle] = field(default_factory=dict)
    merged_pr_numbers: list[int] = field(default_factory=list)
    _pr_counter: int = 0

    def _branch_for(self, work_dir: str, requested_branch: str | None = None) -> str:
        if requested_branch:
            return requested_branch
        return self.branch_by_work_dir.get(work_dir, "mock-branch")

    def handle_commit(self, effect: GitCommit) -> str:
        work_dir = str(effect.work_dir)
        self.commits.append((work_dir, effect.message, effect.all))
        payload = f"{work_dir}:{effect.message}:{len(self.commits)}"
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()

    def handle_diff(self, effect: GitDiff) -> str:
        work_dir = str(effect.work_dir)
        staged_prefix = "[staged] " if effect.staged else ""
        return self.diff_by_work_dir.get(work_dir, f"{staged_prefix}mock diff")

    def handle_push(self, effect: GitPush) -> None:
        work_dir = str(effect.work_dir)
        branch = self._branch_for(work_dir, effect.branch)
        self.pushes.append((work_dir, effect.remote, branch, effect.force, effect.set_upstream))

    def handle_pull(self, effect: GitPull) -> None:
        work_dir = str(effect.work_dir)
        self.pulls.append((work_dir, effect.remote, effect.rebase, effect.branch))

    def handle_create_pr(self, effect: CreatePR) -> PRHandle:
        self._pr_counter += 1
        work_dir = str(effect.work_dir)
        branch = self._branch_for(work_dir, effect.head)
        pr = PRHandle(
            url=f"https://github.com/mock/repo/pull/{self._pr_counter}",
            number=self._pr_counter,
            title=effect.title,
            branch=branch,
            target=effect.target,
            status="open",
            work_dir=effect.work_dir,
        )
        self.prs[pr.number] = pr
        return pr

    def handle_merge_pr(self, effect: MergePR) -> None:
        self.merged_pr_numbers.append(effect.pr.number)
        existing = self.prs.get(effect.pr.number)
        if existing is None:
            return
        self.prs[effect.pr.number] = PRHandle(
            url=existing.url,
            number=existing.number,
            title=existing.title,
            branch=existing.branch,
            target=existing.target,
            status="merged",
            created_at=existing.created_at,
            work_dir=existing.work_dir,
        )


def mock_handlers(
    *,
    runtime: MockGitRuntime | None = None,
) -> ProtocolHandler:
    """Build a protocol handler backed by MockGitRuntime."""

    active_runtime = runtime or MockGitRuntime()

    @do
    def handler(effect: Effect, k: Any):
        if isinstance(effect, GitCommit):
            return (yield Resume(k, active_runtime.handle_commit(effect)))
        if isinstance(effect, GitDiff):
            return (yield Resume(k, active_runtime.handle_diff(effect)))
        if isinstance(effect, GitPush):
            active_runtime.handle_push(effect)
            return (yield Resume(k, None))
        if isinstance(effect, GitPull):
            active_runtime.handle_pull(effect)
            return (yield Resume(k, None))
        if isinstance(effect, CreatePR):
            return (yield Resume(k, active_runtime.handle_create_pr(effect)))
        if isinstance(effect, MergePR):
            active_runtime.handle_merge_pr(effect)
            return (yield Resume(k, None))
        return (yield Pass())

    return handler


__all__ = [
    "MockGitRuntime",
    "ProtocolHandler",
    "mock_handlers",
]
