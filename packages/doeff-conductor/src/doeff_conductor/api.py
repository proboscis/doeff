"""
API for doeff-conductor.

Provides programmatic access to conductor functionality:
- Run workflows
- List/get workflows
- Watch workflow progress
- Manage environments
"""

from __future__ import annotations

import json
import os
import secrets
from collections.abc import Generator
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .types import Issue, WorkflowHandle, WorkflowStatus, WorktreeEnv


def _get_state_dir() -> Path:
    """Get the state directory for conductor."""
    xdg_state = os.environ.get(
        "XDG_STATE_HOME", os.path.expanduser("~/.local/state")
    )
    return Path(xdg_state) / "doeff-conductor"


class ConductorAPI:
    """API for conductor workflow management."""

    def __init__(self, state_dir: str | Path | None = None):
        """Initialize API.

        Args:
            state_dir: State directory. Uses XDG default if not provided.
        """
        self.state_dir = Path(state_dir) if state_dir else _get_state_dir()
        self.workflows_dir = self.state_dir / "workflows"
        self.workflows_dir.mkdir(parents=True, exist_ok=True)

    def run_workflow(
        self,
        template_or_file: str,
        issue: Issue | None = None,
        params: dict[str, Any] | None = None,
    ) -> WorkflowHandle:
        """Run a workflow template or file.

        Args:
            template_or_file: Template name or path to workflow file
            issue: Issue to pass to workflow
            params: Additional parameters

        Returns:
            WorkflowHandle for the started workflow
        """
        from .templates import get_template, is_template
        from .types import WorkflowHandle, WorkflowStatus

        # Generate workflow ID
        workflow_id = secrets.token_hex(4)

        # Determine if template or file
        workflow_name: str
        if is_template(template_or_file):
            template_name = template_or_file
            workflow_func = get_template(template_name)
            workflow_name = template_name
        else:
            # Load from file
            template_name = None
            workflow_path = Path(template_or_file)
            workflow_name = workflow_path.stem
            if not workflow_path.exists():
                raise ValueError(f"Workflow file not found: {template_or_file}")

            # Import workflow function
            import importlib.util

            spec = importlib.util.spec_from_file_location("workflow", workflow_path)
            if spec is None or spec.loader is None:
                raise ValueError(f"Cannot load workflow: {template_or_file}")

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            # Look for workflow function (named 'workflow' or 'main')
            workflow_func = getattr(module, "workflow", None) or getattr(module, "main", None)
            if workflow_func is None:
                raise ValueError(
                    f"No 'workflow' or 'main' function found in {template_or_file}"
                )

        # Create workflow handle
        now = datetime.now(timezone.utc)
        handle = WorkflowHandle(
            id=workflow_id,
            name=workflow_name,
            status=WorkflowStatus.PENDING,
            template=template_name,
            issue_id=issue.id if issue else None,
            created_at=now,
            updated_at=now,
        )

        # Save workflow state
        self._save_workflow(handle)

        # Run workflow (in background or foreground based on configuration)
        # For now, we run synchronously
        try:
            handle = WorkflowHandle(
                id=workflow_id,
                name=handle.name,
                status=WorkflowStatus.RUNNING,
                template=template_name,
                issue_id=issue.id if issue else None,
                created_at=now,
                updated_at=datetime.now(timezone.utc),
            )
            self._save_workflow(handle)

            # Execute the workflow
            from doeff import Effect, Pass, default_handlers, do, run

            # Build kwargs
            kwargs = params or {}
            if issue:
                kwargs["issue"] = issue

            # Get the program
            program = workflow_func(**kwargs)

            # Run with conductor handlers
            from .handlers import (
                AgentHandler,
                GitHandler,
                IssueHandler,
                WorktreeHandler,
                make_scheduled_handler,
            )

            worktree_handler = WorktreeHandler()
            issue_handler = IssueHandler()
            agent_handler = AgentHandler(workflow_id=workflow_id)
            git_handler = GitHandler()

            from .effects import (
                CaptureOutput,
                Commit,
                CreateIssue,
                CreatePR,
                CreateWorktree,
                DeleteWorktree,
                GetIssue,
                ListIssues,
                MergeBranches,
                MergePR,
                Push,
                ResolveIssue,
                RunAgent,
                SendMessage,
                SpawnAgent,
                WaitForStatus,
            )

            handlers = (
                (CreateWorktree, make_scheduled_handler(worktree_handler.handle_create_worktree)),
                (MergeBranches, make_scheduled_handler(worktree_handler.handle_merge_branches)),
                (DeleteWorktree, make_scheduled_handler(worktree_handler.handle_delete_worktree)),
                (CreateIssue, make_scheduled_handler(issue_handler.handle_create_issue)),
                (ListIssues, make_scheduled_handler(issue_handler.handle_list_issues)),
                (GetIssue, make_scheduled_handler(issue_handler.handle_get_issue)),
                (ResolveIssue, make_scheduled_handler(issue_handler.handle_resolve_issue)),
                (RunAgent, make_scheduled_handler(agent_handler.handle_run_agent)),
                (SpawnAgent, make_scheduled_handler(agent_handler.handle_spawn_agent)),
                (SendMessage, make_scheduled_handler(agent_handler.handle_send_message)),
                (WaitForStatus, make_scheduled_handler(agent_handler.handle_wait_for_status)),
                (CaptureOutput, make_scheduled_handler(agent_handler.handle_capture_output)),
                (Commit, make_scheduled_handler(git_handler.handle_commit)),
                (Push, make_scheduled_handler(git_handler.handle_push)),
                (CreatePR, make_scheduled_handler(git_handler.handle_create_pr)),
                (MergePR, make_scheduled_handler(git_handler.handle_merge_pr)),
            )

            @do
            def conductor_handler(effect: Effect, k: Any):
                for effect_type, effect_handler in handlers:
                    if isinstance(effect, effect_type):
                        return (yield effect_handler(effect, k))
                yield Pass()

            result = run(
                program,
                handlers=[conductor_handler, *default_handlers()],
            )
            result_value = result.value if hasattr(result, "value") else result

            # Update workflow status
            handle = WorkflowHandle(
                id=workflow_id,
                name=handle.name,
                status=WorkflowStatus.DONE,
                template=template_name,
                issue_id=issue.id if issue else None,
                created_at=now,
                updated_at=datetime.now(timezone.utc),
                pr_url=getattr(result_value, "url", None),
            )
            self._save_workflow(handle)

        except Exception as e:
            handle = WorkflowHandle(
                id=workflow_id,
                name=handle.name,
                status=WorkflowStatus.ERROR,
                template=template_name,
                issue_id=issue.id if issue else None,
                created_at=now,
                updated_at=datetime.now(timezone.utc),
                error=str(e),
            )
            self._save_workflow(handle)
            raise

        return handle

    def list_workflows(
        self,
        status: list[WorkflowStatus] | None = None,
    ) -> list[WorkflowHandle]:
        """List workflows with optional status filter."""
        from .types import WorkflowHandle

        workflows = []

        for workflow_dir in self.workflows_dir.iterdir():
            if not workflow_dir.is_dir():
                continue

            meta_file = workflow_dir / "meta.json"
            if not meta_file.exists():
                continue

            try:
                data = json.loads(meta_file.read_text())
                handle = WorkflowHandle.from_dict(data)

                if status and handle.status not in status:
                    continue

                workflows.append(handle)
            except Exception:
                continue

        # Sort by updated_at descending
        workflows.sort(key=lambda w: w.updated_at, reverse=True)
        return workflows

    def get_workflow(self, workflow_id: str) -> WorkflowHandle | None:
        """Get workflow by ID or prefix."""
        from .types import WorkflowHandle

        # Try exact match first
        workflow_dir = self.workflows_dir / workflow_id
        if workflow_dir.exists():
            meta_file = workflow_dir / "meta.json"
            if meta_file.exists():
                data = json.loads(meta_file.read_text())
                return WorkflowHandle.from_dict(data)

        # Try prefix match
        matches = []
        for d in self.workflows_dir.iterdir():
            if d.is_dir() and d.name.startswith(workflow_id):
                matches.append(d)

        if len(matches) == 1:
            meta_file = matches[0] / "meta.json"
            if meta_file.exists():
                data = json.loads(meta_file.read_text())
                return WorkflowHandle.from_dict(data)
        elif len(matches) > 1:
            raise ValueError(
                f"Ambiguous workflow ID '{workflow_id}': "
                f"matches {[d.name for d in matches]}"
            )

        return None

    def watch_workflow(
        self,
        workflow_id: str,
        poll_interval: float = 1.0,
    ) -> Generator[dict[str, Any], None, None]:
        """Watch workflow progress.

        Yields status updates as dictionaries.
        """
        import time


        last_status = None

        while True:
            handle = self.get_workflow(workflow_id)
            if handle is None:
                yield {"status": "error", "message": "Workflow not found", "terminal": True}
                break

            if handle.status != last_status:
                yield {
                    "status": handle.status.value,
                    "message": f"Workflow {handle.status.value}",
                    "terminal": handle.status.is_terminal(),
                    "workflow": handle.to_dict(),
                }
                last_status = handle.status

            if handle.status.is_terminal():
                break

            time.sleep(poll_interval)

    def stop_workflow(
        self,
        workflow_id: str,
        agent: str | None = None,
    ) -> list[str]:
        """Stop a workflow or specific agent.

        Returns list of stopped agent names.
        """
        from .types import WorkflowStatus

        handle = self.get_workflow(workflow_id)
        if handle is None:
            raise ValueError(f"Workflow not found: {workflow_id}")

        stopped = []

        # TODO: Implement actual agent stopping via doeff-agentic
        # For now, just update workflow status

        if agent:
            stopped.append(agent)
        else:
            stopped.extend(handle.agents)

        # Update workflow status
        handle = WorkflowHandle(
            id=handle.id,
            name=handle.name,
            status=WorkflowStatus.ABORTED,
            template=handle.template,
            issue_id=handle.issue_id,
            created_at=handle.created_at,
            updated_at=datetime.now(timezone.utc),
        )
        self._save_workflow(handle)

        return stopped

    def list_environments(
        self,
        workflow_id: str | None = None,
    ) -> list[WorktreeEnv]:
        """List worktree environments."""
        from .handlers.worktree_handler import _get_worktree_base_dir
        from .types import WorktreeEnv

        environments = []
        worktree_base = _get_worktree_base_dir()

        if not worktree_base.exists():
            return []

        for env_dir in worktree_base.iterdir():
            if not env_dir.is_dir():
                continue

            # Check if it's a git worktree
            git_dir = env_dir / ".git"
            if not git_dir.exists():
                continue

            # Get branch name
            try:
                import subprocess

                result = subprocess.run(
                    ["git", "branch", "--show-current"],
                    cwd=env_dir,
                    capture_output=True,
                    text=True, check=False,
                )
                branch = result.stdout.strip()

                # Get base commit
                result = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=env_dir,
                    capture_output=True,
                    text=True, check=False,
                )
                commit = result.stdout.strip()

                env = WorktreeEnv(
                    id=env_dir.name,
                    path=env_dir,
                    branch=branch,
                    base_commit=commit,
                    created_at=datetime.fromtimestamp(
                        env_dir.stat().st_ctime, tz=timezone.utc
                    ),
                )
                environments.append(env)
            except Exception:
                continue

        return environments

    def cleanup_environments(
        self,
        dry_run: bool = False,
        older_than_days: int | None = None,
    ) -> list[Path]:
        """Cleanup orphaned worktree environments.

        Returns list of cleaned paths.
        """
        import shutil

        from .handlers.worktree_handler import _get_worktree_base_dir

        cleaned = []
        worktree_base = _get_worktree_base_dir()

        if not worktree_base.exists():
            return []

        now = datetime.now(timezone.utc)

        for env_dir in worktree_base.iterdir():
            if not env_dir.is_dir():
                continue

            # Check age if specified
            if older_than_days is not None:
                created = datetime.fromtimestamp(env_dir.stat().st_ctime, tz=timezone.utc)
                age_days = (now - created).days
                if age_days < older_than_days:
                    continue

            if dry_run:
                cleaned.append(env_dir)
            else:
                try:
                    import subprocess

                    # Remove worktree from git
                    subprocess.run(
                        ["git", "worktree", "remove", "--force", str(env_dir)],
                        capture_output=True, check=False,
                    )
                    # Ensure directory is removed
                    if env_dir.exists():
                        shutil.rmtree(env_dir, ignore_errors=True)
                    cleaned.append(env_dir)
                except Exception:
                    pass

        return cleaned

    def _save_workflow(self, handle: WorkflowHandle) -> None:
        """Save workflow state to disk."""
        workflow_dir = self.workflows_dir / handle.id
        workflow_dir.mkdir(parents=True, exist_ok=True)

        meta_file = workflow_dir / "meta.json"
        meta_file.write_text(json.dumps(handle.to_dict(), indent=2))


# Import WorkflowHandle for type hints
from .types import WorkflowHandle  # noqa: E402

__all__ = ["ConductorAPI"]
