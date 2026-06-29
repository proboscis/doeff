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
import warnings
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


def _warn_corrupt_persistent_state(path: Path, reason: object) -> None:
    """Warn about one corrupt persisted entry while allowing list verbs to continue."""
    from doeff_conductor.exceptions import ConductorStateWarning

    warnings.warn(
        f"Corrupt persistent state skipped: {path}: {reason}",
        ConductorStateWarning,
        stacklevel=2,
    )


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
        supervision: str = "autonomous",
    ) -> "WorkflowHandle":
        """Run a workflow template or file.

        Args:
            template_or_file: Template name or path to workflow file
            issue: Issue to pass to workflow
            params: Additional parameters
            run_id: Optional caller-supplied workflow id for resume/replay runs
            supervision: Run-scoped overseer supervision policy

        Returns:
            WorkflowHandle for the started workflow
        """
        from doeff_conductor.workflow_loader import (
            load_workflow_spec,
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
            # Load the Hy workflow module from its run snapshot (ADR 0001 D2).
            template_name = None
            workflow_func = None
            snapshot_path = prepare_workflow_source_for_run(
                template_or_file,
                state_dir=self.state_dir,
                run_id=workflow_id,
            )
            workflow_spec = load_workflow_spec(str(snapshot_path))
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

            from doeff import run

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
                from doeff_conductor.overseer import (
                    answered_gate_options,
                    answered_gate_stakes,
                    answered_retry_agent_counts,
                )
                from doeff_conductor.workflow_runtime import workflow_spec_to_program

                program = workflow_spec_to_program(
                    workflow_spec,
                    run_id=workflow_id,
                    params=kwargs,
                    issue=issue,
                    supervision=supervision,
                    answered_gate_options=answered_gate_options(self.state_dir, workflow_id),
                    answered_retry_agent_counts=answered_retry_agent_counts(
                        self.state_dir,
                        workflow_id,
                    ),
                    answered_gate_stakes=answered_gate_stakes(self.state_dir, workflow_id),
                )

            # Run with conductor handlers
            import doeff_conductor.handlers as handlers_module

            conductor_handler = handlers_module.production_handlers(
                journal_state_dir=self.state_dir,
                journal_run_id=workflow_id,
            )

            result = run(scheduled(conductor_handler(program)))
            result_value = result.value if type(result).__name__ == "RunResult" else result
            open_gates = ()
            from doeff_conductor.workflow_runtime import ParkedValue, WorkflowRuntimeResult

            if isinstance(result_value, WorkflowRuntimeResult):
                open_gates = result_value.open_gates
                result_value = result_value.value
            result_payload = None if isinstance(result_value, ParkedValue) else result_value

            superseding_terminal = self._terminal_handle_superseding_run(
                workflow_id,
                run_started_at=now,
            )
            if superseding_terminal is not None:
                return superseding_terminal

            if open_gates:
                from doeff_conductor.overseer import record_open_gates

                record_open_gates(
                    self.state_dir,
                    workflow_id=workflow_id,
                    workflow_name=handle.name,
                    open_gates=open_gates,
                    supervision=supervision,
                )
                handle = WorkflowHandle(
                    id=workflow_id,
                    name=handle.name,
                    status=WorkflowStatus.BLOCKED,
                    template=template_name,
                    issue_id=issue.id if issue else None,
                    created_at=now,
                    updated_at=datetime.now(timezone.utc),
                    result_payload=result_payload,
                )
                self._save_workflow(handle)
                return handle

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
                result_payload=result_payload,
            )
            self._save_workflow(handle)

        except Exception as e:
            superseding_terminal = self._terminal_handle_superseding_run(
                workflow_id,
                run_started_at=now,
            )
            if superseding_terminal is not None:
                return superseding_terminal
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
        supervision: str | None = None,
    ) -> "WorkflowHandle":
        """Resume a run from its snapshotted workflow source."""

        from doeff_conductor.workflow_loader import workflow_snapshot_path

        snapshot_path: Path = workflow_snapshot_path(self.state_dir, workflow_id)
        if not snapshot_path.exists():
            raise ValueError(f"workflow snapshot not found for run: {workflow_id}")
        active_supervision = supervision
        if active_supervision is None:
            from doeff_conductor.overseer import load_run_state

            try:
                active_supervision = load_run_state(self.state_dir, workflow_id).supervision
            except FileNotFoundError:
                active_supervision = "autonomous"
        return self.run_workflow(
            str(snapshot_path),
            params=params,
            run_id=workflow_id,
            supervision=active_supervision,
        )

    def answer_gate(
        self,
        workflow_id: str,
        gate_id: str,
        option: str,
        *,
        params: dict[str, Any] | None = None,
        note: str = "",
    ) -> "WorkflowHandle":
        """Record an overseer gate answer and apply its closure-preserving verb."""

        from doeff_conductor.overseer import record_gate_answer
        from doeff_conductor.types import WorkflowStatus
        from doeff_conductor.workflow_loader import workflow_snapshot_path

        run_state = record_gate_answer(
            self.state_dir,
            workflow_id=workflow_id,
            gate_id=gate_id,
            option=option,
            note=note,
        )
        if option == "abort":
            handle = self.get_workflow(workflow_id)
            if handle is None:
                raise ValueError(f"Workflow not found: {workflow_id}")
            aborted = WorkflowHandle(
                id=handle.id,
                name=handle.name,
                status=WorkflowStatus.ABORTED,
                template=handle.template,
                issue_id=handle.issue_id,
                created_at=handle.created_at,
                updated_at=datetime.now(timezone.utc),
                workspaces=handle.workspaces,
                agents=handle.agents,
                pr_url=handle.pr_url,
                error=f"aborted at gate {gate_id}",
                result_payload=handle.result_payload,
            )
            self._save_workflow(aborted)
            return aborted
        if option in {"proceed", "extend", "retry-agent"}:
            # These options all close the K5 decision by resuming the
            # snapshotted workflow.  Runtime interprets their journaled
            # answers by gate type: "extend" renews a deadline window,
            # while "retry-agent" increments that agent node's session
            # attempt and injects the previous schema error into the prompt.
            return self.resume_workflow(
                workflow_id,
                params=params,
                supervision=run_state.supervision,
            )
        if option == "redirect":
            handle = self.get_workflow(workflow_id)
            if handle is None:
                raise ValueError(f"Workflow not found: {workflow_id}")
            snapshot_path: Path = workflow_snapshot_path(self.state_dir, workflow_id)
            blocked = WorkflowHandle(
                id=handle.id,
                name=handle.name,
                status=WorkflowStatus.BLOCKED,
                template=handle.template,
                issue_id=handle.issue_id,
                created_at=handle.created_at,
                updated_at=datetime.now(timezone.utc),
                workspaces=handle.workspaces,
                agents=handle.agents,
                pr_url=handle.pr_url,
                error=(
                    f"redirect recorded for gate {gate_id}; "
                    f"edit snapshot at {snapshot_path} then run: "
                    f"conductor resume {workflow_id}"
                ),
                result_payload=handle.result_payload,
            )
            self._save_workflow(blocked)
            return blocked
        raise ValueError(f"unsupported gate answer option: {option}")

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
            except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
                _warn_corrupt_persistent_state(meta_file, error)
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
                f"Ambiguous workflow ID '{workflow_id}': matches {[d.name for d in matches]}"
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
        agentd_client: Any | None = None,
    ) -> list[str]:
        """Stop a workflow or specific agent.

        Returns list of stopped agent names.
        """
        from .types import WorkflowStatus

        handle = self.get_workflow(workflow_id)
        if handle is None:
            raise ValueError(f"Workflow not found: {workflow_id}")

        stopped: list[str] = []
        session_ids = self._session_ids_for_stop(workflow_id, agent=agent)
        if session_ids:
            stopped.extend(self._stop_agentd_sessions(session_ids, agentd_client=agentd_client))

        if agent:
            if agent not in stopped:
                stopped.append(agent)
        else:
            stopped.extend(name for name in handle.agents if name not in stopped)

        from doeff_conductor.overseer import clear_open_gates

        clear_open_gates(
            self.state_dir,
            workflow_id=workflow_id,
            reason="workflow stopped",
        )

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

    def _session_ids_for_stop(self, workflow_id: str, *, agent: str | None) -> list[str]:
        """Return live agentd session ids recorded by the progress journal."""
        from doeff_conductor.journal import (
            PROGRESS_STATUS_RUNNING,
            ProgressJournal,
        )

        latest_by_node = ProgressJournal.for_run(
            workflow_id,
            state_dir=self.state_dir,
        ).latest_by_node()
        session_ids: list[str] = []
        seen: set[str] = set()
        for entry in latest_by_node.values():
            if entry.status != PROGRESS_STATUS_RUNNING or not entry.session_id:
                continue
            if agent is not None and agent not in {
                entry.session_id,
                entry.node_id,
                entry.session_node_key,
            }:
                continue
            if entry.session_id in seen:
                continue
            seen.add(entry.session_id)
            session_ids.append(entry.session_id)
        return session_ids

    def _stop_agentd_sessions(
        self,
        session_ids: list[str],
        *,
        agentd_client: Any | None,
    ) -> list[str]:
        """Cancel and clean up agentd sessions, preserving explicit failures."""
        if not session_ids:
            return []
        client = agentd_client
        if client is None:
            from doeff_agents import LazyAgentdClient

            client = LazyAgentdClient()

        stopped: list[str] = []
        errors: list[str] = []
        for session_id in session_ids:
            try:
                snapshot = client.get_session(session_id)
                if snapshot is None:
                    continue
                client.cancel_session(session_id)
                client.cleanup_session(session_id)
                stopped.append(session_id)
            except Exception as error:  # pragma: no cover - message asserted by caller
                errors.append(f"{session_id}: {error}")
        if errors:
            raise ValueError("failed to stop agentd session(s): " + "; ".join(errors))
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
                    if result.returncode != 0:
                        reason = (
                            result.stderr.strip()
                            or result.stdout.strip()
                            or f"git branch --show-current exited {result.returncode}"
                        )
                        _warn_corrupt_persistent_state(workspace_dir, reason)
                        continue
                    ref = result.stdout.strip()
                    if not ref:
                        raise ValueError("git branch --show-current returned an empty ref")
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
                except (OSError, TypeError, ValueError) as error:
                    _warn_corrupt_persistent_state(workspace_dir, error)
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

    def _terminal_handle_superseding_run(
        self,
        workflow_id: str,
        *,
        run_started_at: datetime,
    ) -> "WorkflowHandle | None":
        """Return a terminal handle written after this run incarnation began."""
        existing = self.get_workflow(workflow_id)
        if existing is None or not existing.status.is_terminal():
            return None
        if existing.created_at <= run_started_at and existing.updated_at > run_started_at:
            return existing
        return None

    def _save_workflow(self, handle: "WorkflowHandle") -> None:
        """Save workflow state to disk."""
        workflow_dir = self.workflows_dir / handle.id
        workflow_dir.mkdir(parents=True, exist_ok=True)

        meta_file = workflow_dir / "meta.json"
        if meta_file.exists():
            try:
                existing = WorkflowHandle.from_dict(json.loads(meta_file.read_text()))
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                existing = None
            if (
                existing is not None
                and existing.status.is_terminal()
                and existing.created_at <= handle.created_at
                and existing.updated_at > handle.created_at
            ):
                return
        meta_file.write_text(json.dumps(handle.to_dict(), indent=2))


# Import WorkflowHandle for type hints
from .types import WorkflowHandle  # noqa: E402

__all__ = ["ConductorAPI"]
