"""
State file management for doeff-agentic.

This module manages the state files that serve as the contract between
Python (workflow execution) and Rust (fast CLI for plugins).

State directory structure:
    ~/.local/state/doeff-agentic/
    ├── index.json                    # {id → name} for prefix lookup
    └── workflows/
        ├── a3f8b2c/
        │   ├── meta.json             # workflow metadata
        │   ├── trace.jsonl           # effect trace (from doeff-flow)
        │   └── agents/
        │       ├── review-agent.json # agent state
        │       └── fix-agent.json
        └── b7e1d4f/
            └── ...
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .types import AgentInfo, AgentStatus, WorkflowInfo, WorkflowStatus


def _atomic_write(path: Path, content: str) -> None:
    """Write content to file atomically.

    Writes to a temporary file first, then renames to target path.
    This prevents corruption from crashes or concurrent access.

    Args:
        path: Target file path
        content: Content to write
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        os.write(fd, content.encode())
        os.close(fd)
        os.rename(temp_path, path)
    except Exception:
        os.close(fd)
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def get_default_state_dir() -> Path:
    """Get the default state directory following XDG Base Directory Specification.

    Returns:
        Path to ~/.local/state/doeff-agentic/
    """
    xdg_state = os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))
    return Path(xdg_state) / "doeff-agentic"


def generate_workflow_id(name: str, timestamp: datetime | None = None) -> str:
    """Generate a 7-character hex workflow ID.

    Like docker container IDs and git commit hashes, generates a short
    hex identifier that can be used with prefix matching.

    Args:
        name: Workflow name
        timestamp: Timestamp for ID generation (defaults to now)

    Returns:
        7-character hex string (e.g., "a3f8b2c")
    """
    if timestamp is None:
        timestamp = datetime.now(timezone.utc)
    data = f"{name}:{timestamp.isoformat()}"
    return hashlib.sha256(data.encode()).hexdigest()[:7]


@dataclass
class StateManager:
    """Manages workflow and agent state files.

    This class handles reading and writing state files that serve as the
    contract between Python (workflow execution) and Rust CLI (fast queries).

    Attributes:
        state_dir: Root directory for state files
    """

    state_dir: Path

    def __init__(self, state_dir: Path | str | None = None):
        """Initialize the state manager.

        Args:
            state_dir: Directory for state files (defaults to XDG state dir)
        """
        if state_dir is None:
            self.state_dir = get_default_state_dir()
        else:
            self.state_dir = Path(state_dir)

    def _ensure_dirs(self, workflow_id: str) -> Path:
        """Ensure workflow directory structure exists.

        Args:
            workflow_id: Workflow identifier

        Returns:
            Path to the workflow directory
        """
        workflow_dir = self.state_dir / "workflows" / workflow_id
        agents_dir = workflow_dir / "agents"
        agents_dir.mkdir(parents=True, exist_ok=True)
        return workflow_dir

    def _get_index_path(self) -> Path:
        """Get path to the index file."""
        return self.state_dir / "index.json"

    def _load_index(self) -> dict[str, str]:
        """Load the workflow index.

        Returns:
            Dictionary mapping workflow ID to name
        """
        index_path = self._get_index_path()
        if index_path.exists():
            return json.loads(index_path.read_text())
        return {}

    def _save_index(self, index: dict[str, str]) -> None:
        """Save the workflow index atomically.

        Args:
            index: Dictionary mapping workflow ID to name
        """
        index_path = self._get_index_path()
        _atomic_write(index_path, json.dumps(index, indent=2))

    def write_workflow_meta(self, workflow: WorkflowInfo) -> None:
        """Write workflow metadata to state file atomically.

        Args:
            workflow: Workflow information to write
        """
        workflow_dir = self._ensure_dirs(workflow.id)
        meta_path = workflow_dir / "meta.json"

        # Write meta.json without nested agents (they're in separate files)
        meta = {
            "id": workflow.id,
            "name": workflow.name,
            "status": workflow.status.value,
            "started_at": workflow.started_at.isoformat(),
            "updated_at": workflow.updated_at.isoformat(),
            "current_agent": workflow.current_agent,
            "last_slog": workflow.last_slog,
            "error": workflow.error,
        }
        _atomic_write(meta_path, json.dumps(meta, indent=2))

        # Update index
        index = self._load_index()
        index[workflow.id] = workflow.name
        self._save_index(index)

    def write_agent_state(self, workflow_id: str, agent: AgentInfo) -> None:
        """Write agent state to file atomically.

        Args:
            workflow_id: Workflow identifier
            agent: Agent information to write
        """
        workflow_dir = self._ensure_dirs(workflow_id)
        agent_path = workflow_dir / "agents" / f"{agent.name}.json"
        _atomic_write(agent_path, json.dumps(agent.to_dict(), indent=2))

    def read_workflow(self, workflow_id: str) -> WorkflowInfo | None:
        """Read workflow information from state files.

        Args:
            workflow_id: Full or prefix workflow ID

        Returns:
            WorkflowInfo if found, None otherwise
        """
        # Resolve prefix to full ID
        full_id = self.resolve_prefix(workflow_id)
        if full_id is None:
            return None

        workflow_dir = self.state_dir / "workflows" / full_id
        meta_path = workflow_dir / "meta.json"

        if not meta_path.exists():
            return None

        meta = json.loads(meta_path.read_text())

        # Load all agents
        agents_dir = workflow_dir / "agents"
        agents: list[AgentInfo] = []
        if agents_dir.exists():
            for agent_file in agents_dir.glob("*.json"):
                agent_data = json.loads(agent_file.read_text())
                agents.append(AgentInfo.from_dict(agent_data))

        return WorkflowInfo(
            id=meta["id"],
            name=meta["name"],
            status=WorkflowStatus(meta["status"]),
            started_at=datetime.fromisoformat(meta["started_at"]),
            updated_at=datetime.fromisoformat(meta["updated_at"]),
            current_agent=meta.get("current_agent"),
            agents=tuple(agents),
            last_slog=meta.get("last_slog"),
            error=meta.get("error"),
        )

    def read_agent(self, workflow_id: str, agent_name: str) -> AgentInfo | None:
        """Read agent information from state file.

        Args:
            workflow_id: Full or prefix workflow ID
            agent_name: Agent name

        Returns:
            AgentInfo if found, None otherwise
        """
        full_id = self.resolve_prefix(workflow_id)
        if full_id is None:
            return None

        agent_path = self.state_dir / "workflows" / full_id / "agents" / f"{agent_name}.json"
        if not agent_path.exists():
            return None

        return AgentInfo.from_dict(json.loads(agent_path.read_text()))

    def list_workflows(
        self,
        status: list[WorkflowStatus] | None = None,
        agent_status: list[AgentStatus] | None = None,
    ) -> list[WorkflowInfo]:
        """List all workflows with optional filtering.

        Args:
            status: Filter by workflow status
            agent_status: Filter by agent status

        Returns:
            List of WorkflowInfo matching the filters
        """
        workflows_dir = self.state_dir / "workflows"
        if not workflows_dir.exists():
            return []

        result: list[WorkflowInfo] = []
        for workflow_dir in workflows_dir.iterdir():
            if not workflow_dir.is_dir():
                continue

            workflow = self.read_workflow(workflow_dir.name)
            if workflow is None:
                continue

            # Apply status filter
            if status and workflow.status not in status:
                continue

            # Apply agent_status filter
            if agent_status:
                has_matching_agent = any(
                    a.status in agent_status for a in workflow.agents
                )
                if not has_matching_agent:
                    continue

            result.append(workflow)

        # Sort by updated_at descending
        result.sort(key=lambda w: w.updated_at, reverse=True)
        return result

    def resolve_prefix(self, prefix: str) -> str | None:
        """Resolve a workflow ID prefix to the full ID.

        Args:
            prefix: Full or prefix workflow ID (1-7 chars)

        Returns:
            Full workflow ID if unique match found, None otherwise

        Raises:
            ValueError: If prefix matches multiple workflows (ambiguous)
        """
        index = self._load_index()

        # Exact match
        if prefix in index:
            return prefix

        # Prefix match
        matches = [wf_id for wf_id in index if wf_id.startswith(prefix)]

        if len(matches) == 0:
            return None
        if len(matches) == 1:
            return matches[0]
        # Ambiguous prefix
        match_details = ", ".join(f"{wf_id} ({index[wf_id]})" for wf_id in matches)
        raise ValueError(
            f"Ambiguous prefix '{prefix}' matches multiple workflows: {match_details}"
        )

    def delete_workflow(self, workflow_id: str) -> bool:
        """Delete a workflow and all its state files.

        Args:
            workflow_id: Full or prefix workflow ID

        Returns:
            True if deleted, False if not found
        """
        full_id = self.resolve_prefix(workflow_id)
        if full_id is None:
            return False

        import shutil

        workflow_dir = self.state_dir / "workflows" / full_id
        if workflow_dir.exists():
            shutil.rmtree(workflow_dir)

        # Remove from index
        index = self._load_index()
        if full_id in index:
            del index[full_id]
            self._save_index(index)

        return True

    def watch_workflow(
        self,
        workflow_id: str,
        poll_interval: float = 1.0,
    ) -> Iterator[WorkflowInfo]:
        """Watch a workflow for changes.

        Polls the state files and yields updates when changes are detected.

        Args:
            workflow_id: Full or prefix workflow ID
            poll_interval: How often to poll for changes (seconds)

        Yields:
            WorkflowInfo on each change
        """
        import time

        full_id = self.resolve_prefix(workflow_id)
        if full_id is None:
            return

        last_updated: datetime | None = None

        while True:
            workflow = self.read_workflow(full_id)
            if workflow is None:
                return

            # Yield if changed
            if last_updated is None or workflow.updated_at > last_updated:
                last_updated = workflow.updated_at
                yield workflow

            # Check for terminal status
            if workflow.status in (
                WorkflowStatus.COMPLETED,
                WorkflowStatus.FAILED,
                WorkflowStatus.STOPPED,
            ):
                return

            time.sleep(poll_interval)

    def append_trace(self, workflow_id: str, trace_entry: dict[str, Any]) -> None:
        """Append a trace entry to the workflow's trace file.

        Args:
            workflow_id: Workflow identifier
            trace_entry: Trace entry to append
        """
        workflow_dir = self._ensure_dirs(workflow_id)
        trace_path = workflow_dir / "trace.jsonl"

        with trace_path.open("a") as f:
            f.write(json.dumps(trace_entry) + "\n")

    def read_trace(self, workflow_id: str) -> list[dict[str, Any]]:
        """Read the workflow's trace file.

        Args:
            workflow_id: Full or prefix workflow ID

        Returns:
            List of trace entries
        """
        full_id = self.resolve_prefix(workflow_id)
        if full_id is None:
            return []

        trace_path = self.state_dir / "workflows" / full_id / "trace.jsonl"
        if not trace_path.exists():
            return []

        entries: list[dict[str, Any]] = []
        with trace_path.open() as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        return entries
