"""
API for doeff-conductor.

Provides programmatic access to conductor functionality:
- Run workflows
- List/get workflows
- Watch workflow progress
- Manage workspaces
"""

import json
import secrets
import sys
from collections.abc import Generator
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .types import Issue, WorkflowHandle, WorkflowStatus, Workspace


def _get_state_dir() -> Path:
    """Get the state directory for conductor."""
    return Path.home() / ".local" / "state" / "doeff-conductor"


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

    def run_workflow(  # noqa: PLR0912, PLR0915
        self,
        template_or_file: str,
        issue: "Issue | None" = None,
        params: dict[str, Any] | None = None,
        run_id: str | None = None,
    ) -> "WorkflowHandle":
        """Run a workflow template or file.

        Args:
            template_or_file: Template name or path to workflow file
            issue: Issue to pass to workflow
            params: Additional parameters
            run_id: Optional caller-supplied workflow id for resume/replay runs

        Returns:
            WorkflowHandle for the started workflow
        """
        from doeff_conductor.dsl import WorkflowSpec
        from doeff_conductor.workflow_loader import (
            check_workflow_source_determinism,
            prepare_workflow_source_for_run,
        )

        from .templates import get_template, is_template
        from .types import PRHandle, WorkflowHandle, WorkflowStatus

        # Generate workflow ID
        workflow_id = run_id or secrets.token_hex(4)

        # Determine if template or file
        workflow_name: str
        workflow_spec = None
        workflow_func: Any | None
        if is_template(template_or_file):
            template_name = template_or_file
            workflow_func = get_template(template_name)
            workflow_name = template_name
        else:
            # Load from file
            template_name = None
            source_path = Path(template_or_file)
            workflow_path = prepare_workflow_source_for_run(
                template_or_file,
                state_dir=self.state_dir,
                run_id=workflow_id,
            )
            workflow_name = source_path.stem
            if not workflow_path.exists():
                raise ValueError(f"Workflow file not found: {template_or_file}")
            check_workflow_source_determinism(str(workflow_path))

            # Import workflow function
            import importlib.util

            spec = importlib.util.spec_from_file_location(
                f"doeff_conductor_run_{workflow_id}",
                workflow_path,
            )
            if spec is None or spec.loader is None:
                raise ValueError(f"Cannot load workflow: {template_or_file}")

            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)

            # Look for workflow function (named 'workflow' or 'main')
            workflow_func = None
            if "workflow" in module.__dict__:
                workflow_candidate: object = module.__dict__["workflow"]
                if isinstance(workflow_candidate, WorkflowSpec):
                    workflow_spec = workflow_candidate
                elif callable(workflow_candidate):
                    workflow_func = workflow_candidate
                else:
                    raise ValueError("workflow must be a WorkflowSpec or callable")
            elif "main" in module.__dict__:
                main_candidate: object = module.__dict__["main"]
                if not callable(main_candidate):
                    raise ValueError("main workflow entrypoint must be callable")
                workflow_func = main_candidate
            elif "WORKFLOW" in module.__dict__:
                workflow_spec = module.__dict__["WORKFLOW"]
            elif "build_workflow" in module.__dict__:
                builder = module.__dict__["build_workflow"]
                if not callable(builder):
                    raise ValueError("build_workflow must be callable")
                workflow_spec = builder()
            if workflow_spec is not None and not isinstance(workflow_spec, WorkflowSpec):
                raise ValueError("loaded workflow object must be doeff_conductor.dsl.WorkflowSpec")
            if workflow_func is None:
                if workflow_spec is None:
                    raise ValueError(
                        f"No 'workflow', 'main', 'WORKFLOW', or 'build_workflow' found in "
                        f"{template_or_file}"
                    )
                workflow_name = workflow_spec.name

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
            from doeff_core_effects.scheduler import scheduled

            from doeff import WithHandler, run

            # Build kwargs
            kwargs = params or {}
            if issue:
                kwargs["issue"] = issue

            # Get the program
            if workflow_spec is None:
                if workflow_func is None:
                    raise ValueError("workflow function was not loaded")
                program = workflow_func(**kwargs)
            else:
                from doeff_conductor.workflow_runtime import workflow_spec_to_program

                program = workflow_spec_to_program(
                    workflow_spec,
                    run_id=workflow_id,
                    params=kwargs,
                    issue=issue,
                )

            # Run with conductor handlers
            import doeff_conductor.handlers as handlers_module

            conductor_handler = handlers_module.production_handlers(
                journal_state_dir=self.state_dir,
                journal_run_id=workflow_id,
            )

            result = run(scheduled(WithHandler(conductor_handler, program)))
            result_value = result.value if type(result).__name__ == "RunResult" else result
            pr_url = None
            if isinstance(result_value, PRHandle):
                pr_url = result_value.url
            elif isinstance(result_value, SimpleNamespace):
                if "url" in vars(result_value):
                    pr_url = vars(result_value)["url"]
            elif hasattr(result_value, "__dict__") and "url" in vars(result_value):
                pr_url = vars(result_value)["url"]

            # Update workflow status
            handle = WorkflowHandle(
                id=workflow_id,
                name=handle.name,
                status=WorkflowStatus.DONE,
                template=template_name,
                issue_id=issue.id if issue else None,
                created_at=now,
                updated_at=datetime.now(timezone.utc),
                pr_url=pr_url,
                result_payload=result_value,
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

    def resume_workflow(
        self,
        workflow_id: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> "WorkflowHandle":
        """Resume a run from its snapshotted workflow source."""

        from doeff_conductor.workflow_loader import workflow_snapshot_path

        snapshot_path: Path = workflow_snapshot_path(self.state_dir, workflow_id)
        if not snapshot_path.exists():
            raise ValueError(f"workflow snapshot not found for run: {workflow_id}")
        return self.run_workflow(
            str(snapshot_path),
            params=params,
            run_id=workflow_id,
        )

    def list_workflows(
        self,
        status: "list[WorkflowStatus] | None" = None,
    ) -> "list[WorkflowHandle]":
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
            except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue

        # Sort by updated_at descending
        workflows.sort(key=lambda w: w.updated_at, reverse=True)
        return workflows

    def get_workflow(self, workflow_id: str) -> "WorkflowHandle | None":
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

    def list_workspaces(
        self,
        workflow_id: str | None = None,
    ) -> "list[Workspace]":
        """List materialized workspaces."""
        from .handlers.workspace_handler import _get_workspace_base_dir
        from .types import Workspace

        workspaces = []
        workspace_base = _get_workspace_base_dir()

        if not workspace_base.exists():
            return []

        for repo_dir in workspace_base.iterdir():
            if not repo_dir.is_dir() or repo_dir.name == "logs":
                continue
            for workspace_dir in repo_dir.iterdir():
                if not workspace_dir.is_dir():
                    continue
                git_dir = workspace_dir / ".git"
                if not git_dir.exists():
                    continue
                try:
                    import subprocess

                    result = subprocess.run(
                        ["git", "branch", "--show-current"],
                        cwd=workspace_dir,
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    ref = result.stdout.strip()
                    workspace = Workspace(
                        id=workspace_dir.name,
                        repo=repo_dir.name,
                        ref=ref,
                        base_ref=ref,
                        created_at=datetime.fromtimestamp(
                            workspace_dir.stat().st_ctime,
                            tz=timezone.utc,
                        ),
                    )
                    workspaces.append(workspace)
                except (OSError, TypeError, ValueError):
                    continue

        return workspaces

    def cleanup_workspaces(
        self,
        dry_run: bool = False,
        older_than_days: int | None = None,
    ) -> list[Path]:
        """Cleanup orphaned workspace materializations.

        Returns list of cleaned paths.
        """
        import shutil

        from .handlers.workspace_handler import _get_workspace_base_dir

        cleaned = []
        workspace_base = _get_workspace_base_dir()

        if not workspace_base.exists():
            return []

        now = datetime.now(timezone.utc)

        for repo_dir in workspace_base.iterdir():
            if not repo_dir.is_dir() or repo_dir.name == "logs":
                continue
            for workspace_dir in repo_dir.iterdir():
                if not workspace_dir.is_dir():
                    continue

                if older_than_days is not None:
                    created = datetime.fromtimestamp(
                        workspace_dir.stat().st_ctime,
                        tz=timezone.utc,
                    )
                    age_days = (now - created).days
                    if age_days < older_than_days:
                        continue

                if dry_run:
                    cleaned.append(workspace_dir)
                else:
                    try:
                        import subprocess

                        subprocess.run(
                            ["git", "worktree", "remove", "--force", str(workspace_dir)],
                            capture_output=True,
                            check=False,
                        )
                        if workspace_dir.exists():
                            shutil.rmtree(workspace_dir, ignore_errors=True)
                        cleaned.append(workspace_dir)
                    except OSError as error:
                        raise RuntimeError(
                            f"Failed to remove workspace materialization: {workspace_dir}"
                        ) from error

        return cleaned

    def _save_workflow(self, handle: "WorkflowHandle") -> None:
        """Save workflow state to disk."""
        workflow_dir = self.workflows_dir / handle.id
        workflow_dir.mkdir(parents=True, exist_ok=True)

        meta_file = workflow_dir / "meta.json"
        meta_file.write_text(json.dumps(handle.to_dict(), indent=2))


# Import WorkflowHandle for type hints
from .types import WorkflowHandle  # noqa: E402

__all__ = ["ConductorAPI"]
