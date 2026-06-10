"""Workspace handler for the git medium family."""

import secrets
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


@dataclass(frozen=True)
class _WorkspaceMaterialization:
    workspace: Workspace
    path: Path
    base_commit: str


def _get_workspace_base_dir() -> Path:
    """Get the base directory for site-local workspace materializations."""
    return Path.home() / ".local" / "share" / "doeff-conductor" / "workspaces"


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
        self._materializations: dict[str, _WorkspaceMaterialization] = {}

    def repo_path(self, repo: str) -> Path:
        """Resolve a workflow repo name to a local repository path."""
        if repo not in self.repo_paths:
            raise ValueError(f"Workspace repo is not configured: {repo}")
        return self.repo_paths[repo]

    def resolve_path(self, workspace: Workspace) -> Path:
        """Resolve a workspace to its handler-private materialization path."""
        materialization = self._materializations.get(workspace.id)
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
        self._materializations[workspace.id] = _WorkspaceMaterialization(
            workspace=workspace,
            path=path,
            base_commit=base_commit or get_current_commit(path),
        )

    def handle_create_workspace(self, effect: "CreateWorkspace") -> Workspace:
        """Create a logical workspace from a git ref."""
        repo_path: Path = self.repo_path(effect.repo)
        workspace_id: str = secrets.token_hex(4)
        base_ref: str = effect.from_ref or get_default_branch(repo_path)

        branch_parts: list[str] = ["conductor"]
        if effect.issue is not None:
            branch_parts.append(effect.issue.id.lower().replace("-", "_"))
        if effect.suffix is not None:
            branch_parts.append(effect.suffix)
        branch_parts.append(workspace_id[:7])
        ref: str = effect.name or "-".join(branch_parts)
        materialized_path: Path = self.workspace_base / effect.repo / workspace_id
        materialized_path.parent.mkdir(parents=True, exist_ok=True)
        base_commit: str = get_current_commit(repo_path)

        run_git(
            ["git", "worktree", "add", "-b", ref, str(materialized_path), base_ref],
            cwd=repo_path,
        )

        workspace = Workspace(
            id=workspace_id,
            repo=effect.repo,
            ref=ref,
            base_ref=base_ref,
            issue_id=effect.issue.id if effect.issue is not None else None,
            created_at=datetime.now(timezone.utc),
        )
        self._materializations[workspace_id] = _WorkspaceMaterialization(
            workspace=workspace,
            path=materialized_path,
            base_commit=base_commit,
        )
        return workspace

    def handle_merge_workspaces(self, effect: "MergeWorkspaces") -> MergeWorkspacesResult:
        """Merge several workspaces into a new workspace."""
        from doeff_conductor.types import MergeStrategy

        if not effect.workspaces:
            raise ValueError("No workspaces to merge")

        repo_names: set[str] = {workspace.repo for workspace in effect.workspaces}
        if len(repo_names) != 1:
            raise ValueError("Cannot merge workspaces from different repos")

        base_workspace: Workspace = effect.workspaces[0]
        repo_path: Path = self.repo_path(base_workspace.repo)
        workspace_id: str = secrets.token_hex(4)
        ref: str = effect.name or f"conductor-merged-{workspace_id[:7]}"
        materialized_path: Path = self.workspace_base / base_workspace.repo / f"merged-{workspace_id}"
        materialized_path.parent.mkdir(parents=True, exist_ok=True)
        log_path: Path = self.logs_dir / f"merge-{workspace_id}.log"

        run_git(
            ["git", "worktree", "add", "-b", ref, str(materialized_path), base_workspace.ref],
            cwd=repo_path,
            log_path=log_path,
        )
        workspace = Workspace(
            id=workspace_id,
            repo=base_workspace.repo,
            ref=ref,
            base_ref=base_workspace.ref,
            created_at=datetime.now(timezone.utc),
        )
        self._materializations[workspace_id] = _WorkspaceMaterialization(
            workspace=workspace,
            path=materialized_path,
            base_commit=get_current_commit(materialized_path),
        )

        strategy: MergeStrategy = effect.strategy or MergeStrategy.MERGE
        for source_workspace in effect.workspaces[1:]:
            if strategy is MergeStrategy.MERGE:
                args = ["git", "merge", source_workspace.ref, "--no-edit"]
            elif strategy is MergeStrategy.REBASE:
                args = ["git", "rebase", source_workspace.ref]
            else:
                args = ["git", "merge", "--squash", source_workspace.ref]

            try:
                run_git(args, cwd=materialized_path, log_path=log_path)
                if strategy is MergeStrategy.SQUASH:
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
        materialization = self._materializations.get(effect.workspace.id)
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

        self._materializations.pop(effect.workspace.id, None)
        return True
