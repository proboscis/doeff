"""Workspace handler for the git medium family.

Materialization is idempotent ensure-style: a workspace's branch and worktree
path derive deterministically from its ``workspace_id``, so re-emitting the
same effect after a process restart re-binds the same state instead of
creating a fresh worktree (the resume-divergence defect observed live on
2026-06-11).
"""

import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from doeff_conductor.git_workspace import (
    GitCommandError,
    append_git_output,
    conflicted_files,
    get_current_commit,
    get_default_branch,
    get_repo_root,
    run_git,
)
from doeff_conductor.types import (
    MergeConflict,
    MergeStatus,
    MergeWorkspacesResult,
    Workspace,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from doeff_conductor.effects.workspace import (
        CreateWorkspace,
        DeleteWorkspace,
        MergeWorkspaces,
    )


class WorkspaceStateError(RuntimeError):
    """Raised when on-disk workspace state contradicts its identity."""


@dataclass(frozen=True)
class _WorkspaceMaterialization:
    workspace: Workspace
    path: Path
    base_commit: str


def _get_workspace_base_dir() -> Path:
    """Get the base directory for site-local workspace materializations."""
    return Path.home() / ".local" / "share" / "doeff-conductor" / "workspaces"


def _branch_for(workspace_id: str) -> str:
    """Derive the deterministic branch name for a workspace identity."""
    return f"conductor/{workspace_id}"


class WorkspaceHandler:
    """Handler for logical workspaces backed by git worktrees."""

    def __init__(
        self,
        repo_path: Path | None = None,
        *,
        repo_paths: "Mapping[str, Path] | None" = None,
        workspace_base: Path | None = None,
    ) -> None:
        default_repo_path: Path = repo_path or get_repo_root()
        resolved_repo_paths: dict[str, Path] = {"default": default_repo_path}
        if repo_paths is not None:
            for repo_name, candidate_path in repo_paths.items():
                resolved_repo_paths[repo_name] = candidate_path

        self.repo_paths = resolved_repo_paths
        self.workspace_base = workspace_base or _get_workspace_base_dir()
        self.workspace_base.mkdir(parents=True, exist_ok=True)
        self.logs_dir = self.workspace_base / "logs"
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        # Workspace identity is scoped per repo: branch and worktree both live
        # inside one repository.
        self._materializations: dict[tuple[str, str], _WorkspaceMaterialization] = {}

    def repo_path(self, repo: str) -> Path:
        """Resolve a workflow repo name to a local repository path."""
        if repo not in self.repo_paths:
            raise ValueError(f"Workspace repo is not configured: {repo}")
        return self.repo_paths[repo]

    def resolve_path(self, workspace: Workspace) -> Path:
        """Resolve a workspace to its handler-private materialization path."""
        materialization = self._materializations.get((workspace.repo, workspace.id))
        if materialization is None:
            raise ValueError(f"Workspace is not materialized on this site: {workspace.id}")
        return materialization.path

    def register_materialization(
        self,
        workspace: Workspace,
        path: Path,
        *,
        base_commit: str | None = None,
    ) -> None:
        """Register an existing path for tests or system-side adapters."""
        self._materializations[(workspace.repo, workspace.id)] = _WorkspaceMaterialization(
            workspace=workspace,
            path=path,
            base_commit=base_commit or get_current_commit(path),
        )

    def _ensure_materialized(
        self,
        *,
        repo: str,
        workspace_id: str,
        base_ref: str,
        issue_id: str | None = None,
        log_path: Path | None = None,
    ) -> Workspace:
        """Idempotently bind ``workspace_id`` to its branch and worktree.

        - branch + worktree both present: re-adopt as-is (uncommitted changes
          preserved);
        - branch present, worktree missing: re-materialize from the branch,
          never from ``base_ref``;
        - neither present: create branch + worktree from ``base_ref`` — this
          happens exactly once per identity lifetime;
        - worktree present without its branch: corrupt state, fail loudly.
        """
        if not workspace_id:
            raise ValueError("workspace effects require a non-empty workspace_id")

        repo_path: Path = self.repo_path(repo)
        ref: str = _branch_for(workspace_id)
        materialized_path: Path = self.workspace_base / repo / workspace_id
        materialized_path.parent.mkdir(parents=True, exist_ok=True)

        branch_exists: bool = (
            run_git(
                ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{ref}"],
                cwd=repo_path,
                check=False,
            ).returncode
            == 0
        )

        if materialized_path.exists():
            if not branch_exists:
                raise WorkspaceStateError(
                    f"workspace {workspace_id}: worktree {materialized_path} exists "
                    f"but branch {ref} does not; refusing to guess"
                )
            current_branch: str = run_git(
                ["git", "branch", "--show-current"],
                cwd=materialized_path,
            ).stdout.strip()
            if current_branch != ref:
                raise WorkspaceStateError(
                    f"workspace {workspace_id}: worktree {materialized_path} is on "
                    f"branch {current_branch!r}, expected {ref!r}"
                )
        elif branch_exists:
            # The worktree was deleted (e.g. host cleanup) but the branch — the
            # workspace's portable identity — survives. Re-materialize from it.
            run_git(["git", "worktree", "prune"], cwd=repo_path, log_path=log_path)
            run_git(
                ["git", "worktree", "add", str(materialized_path), ref],
                cwd=repo_path,
                log_path=log_path,
            )
        else:
            run_git(
                ["git", "worktree", "add", "-b", ref, str(materialized_path), base_ref],
                cwd=repo_path,
                log_path=log_path,
            )

        workspace = Workspace(
            id=workspace_id,
            repo=repo,
            ref=ref,
            base_ref=base_ref,
            issue_id=issue_id,
            created_at=datetime.now(timezone.utc),
        )
        self._materializations[(repo, workspace_id)] = _WorkspaceMaterialization(
            workspace=workspace,
            path=materialized_path,
            base_commit=get_current_commit(materialized_path),
        )
        return workspace

    def handle_create_workspace(self, effect: "CreateWorkspace") -> Workspace:
        """Ensure the logical workspace bound to the effect's identity."""
        repo_path: Path = self.repo_path(effect.repo)
        base_ref: str = effect.from_ref or get_default_branch(repo_path)
        return self._ensure_materialized(
            repo=effect.repo,
            workspace_id=effect.workspace_id,
            base_ref=base_ref,
            issue_id=effect.issue.id if effect.issue is not None else None,
        )

    def handle_merge_workspaces(self, effect: "MergeWorkspaces") -> MergeWorkspacesResult:
        """Merge several workspaces into the identity-bound merge workspace."""
        from doeff_conductor.types import MergeStrategy

        if not effect.workspaces:
            raise ValueError("No workspaces to merge")

        repo_names: set[str] = {workspace.repo for workspace in effect.workspaces}
        if len(repo_names) != 1:
            raise ValueError("Cannot merge workspaces from different repos")

        base_workspace: Workspace = effect.workspaces[0]
        log_path: Path = self.logs_dir / f"merge-{effect.workspace_id}.log"
        workspace: Workspace = self._ensure_materialized(
            repo=base_workspace.repo,
            workspace_id=effect.workspace_id,
            base_ref=base_workspace.ref,
            log_path=log_path,
        )
        materialized_path: Path = self.resolve_path(workspace)

        strategy: MergeStrategy = effect.strategy or MergeStrategy.MERGE
        # Merge ALL sources, including workspaces[0]: the merge branch is
        # created from source[0]'s tip on first run (merging it again is a
        # no-op), but on RESUME the branch is re-adopted frozen at that old
        # tip — skipping source[0] silently dropped its newer commits, so
        # the resumed merged tree diverged from a fresh run's (review
        # finding F2, reproduced with real git).
        for source_workspace in effect.workspaces:
            if strategy is MergeStrategy.MERGE:
                args = ["git", "merge", source_workspace.ref, "--no-edit"]
            elif strategy is MergeStrategy.REBASE:
                args = ["git", "rebase", source_workspace.ref]
            else:
                args = ["git", "merge", "--squash", source_workspace.ref]

            try:
                run_git(args, cwd=materialized_path, log_path=log_path)
                if strategy is MergeStrategy.SQUASH:
                    # Re-running an already-applied squash merge stages
                    # nothing; committing then would fail spuriously.
                    staged_changes = (
                        run_git(
                            ["git", "diff", "--cached", "--quiet"],
                            cwd=materialized_path,
                            check=False,
                        ).returncode
                        != 0
                    )
                    if staged_changes:
                        run_git(
                            ["git", "commit", "-m", f"Merge {source_workspace.ref}"],
                            cwd=materialized_path,
                            log_path=log_path,
                        )
            except GitCommandError as error:
                append_git_output(log_path, error.result)
                conflict = MergeConflict(
                    workspace=source_workspace,
                    files=conflicted_files(materialized_path),
                )
                return MergeWorkspacesResult(
                    status=MergeStatus.CONFLICT,
                    workspace=workspace,
                    conflicts=(conflict,),
                    log_path=str(log_path),
                    message=f"Merge conflict while reconciling {source_workspace.ref}",
                )

        return MergeWorkspacesResult(
            status=MergeStatus.MERGED,
            workspace=workspace,
            log_path=str(log_path),
        )

    def handle_delete_workspace(self, effect: "DeleteWorkspace") -> bool:
        """Remove a workspace materialization from this site."""
        materialization = self._materializations.get(
            (effect.workspace.repo, effect.workspace.id)
        )
        if materialization is None:
            return False

        args: list[str] = ["git", "worktree", "remove"]
        if effect.force:
            args.append("--force")
        args.append(str(materialization.path))

        try:
            run_git(
                args,
                cwd=self.repo_path(effect.workspace.repo),
            )
        except GitCommandError:
            if not effect.force:
                return False
            shutil.rmtree(materialization.path, ignore_errors=True)
            run_git(
                ["git", "worktree", "prune"],
                cwd=self.repo_path(effect.workspace.repo),
                check=False,
            )

        self._materializations.pop((effect.workspace.repo, effect.workspace.id), None)
        return True
