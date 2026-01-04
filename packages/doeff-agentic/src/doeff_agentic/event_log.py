"""
JSONL Event Log system for doeff-agentic.

This module implements JSONL event logs for workflows, sessions, and environments
as specified in SPEC-AGENTIC-001.

Directory Structure:
    ~/.local/state/doeff-agentic/workflows/
    ├── a3f8b2c/
    │   ├── workflow.jsonl      # Workflow-level events
    │   ├── sessions/
    │   │   ├── reviewer.jsonl  # Per-session events
    │   │   ├── fixer.jsonl
    │   │   └── tester.jsonl
    │   └── environments/
    │       ├── env-abc.jsonl
    │       └── env-def.jsonl
    └── b7e1d4f/
        └── ...

Event Types:
    Workflow events (workflow.jsonl):
    - workflow.created
    - workflow.status
    - environment.created
    - environment.deleted
    - session.created
    - session.status
    - session.deleted

    Session events (sessions/<name>.jsonl):
    - message.sent
    - message.chunk
    - message.complete
    - tool.call
    - tool.result
    - session.status

    Environment events (environments/<id>.jsonl):
    - environment.created
    - environment.session_bound
    - environment.deleted
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .types import (
    AgenticEnvironmentHandle,
    AgenticEnvironmentType,
    AgenticSessionHandle,
    AgenticSessionStatus,
    AgenticWorkflowHandle,
    AgenticWorkflowStatus,
)


def _atomic_write_line(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(content + "\n")


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_default_state_dir() -> Path:
    xdg_state = os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))
    return Path(xdg_state) / "doeff-agentic"


@dataclass(frozen=True)
class WorkflowEvent:
    ts: str
    event_type: str
    data: dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> dict[str, Any]:
        return {"ts": self.ts, "event_type": self.event_type, **self.data}
    
    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> WorkflowEvent:
        ts = d.pop("ts")
        event_type = d.pop("event_type")
        return cls(ts=ts, event_type=event_type, data=d)


@dataclass(frozen=True)
class SessionEvent:
    ts: str
    event_type: str
    data: dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> dict[str, Any]:
        return {"ts": self.ts, "event_type": self.event_type, **self.data}
    
    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SessionEvent:
        ts = d.pop("ts")
        event_type = d.pop("event_type")
        return cls(ts=ts, event_type=event_type, data=d)


@dataclass(frozen=True)
class EnvironmentEvent:
    ts: str
    event_type: str
    data: dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> dict[str, Any]:
        return {"ts": self.ts, "event_type": self.event_type, **self.data}
    
    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> EnvironmentEvent:
        ts = d.pop("ts")
        event_type = d.pop("event_type")
        return cls(ts=ts, event_type=event_type, data=d)


@dataclass
class EventLogWriter:
    """Writes JSONL event logs for workflows, sessions, and environments.
    
    This class manages the JSONL files that serve as the source of truth
    for workflow state. State can be reconstructed by replaying events.
    
    Example:
        writer = EventLogWriter(workflow_id="a3f8b2c")
        writer.write_workflow_created(name="PR Review")
        writer.write_session_created("reviewer", "sess-123", "env-abc")
        writer.write_message_sent("reviewer", "user", "Review this PR")
    """
    
    workflow_id: str
    state_dir: Path = field(default_factory=get_default_state_dir)
    
    def __post_init__(self) -> None:
        if isinstance(self.state_dir, str):
            self.state_dir = Path(self.state_dir)
    
    @property
    def workflow_dir(self) -> Path:
        return self.state_dir / "workflows" / self.workflow_id
    
    @property
    def workflow_log_path(self) -> Path:
        return self.workflow_dir / "workflow.jsonl"
    
    def session_log_path(self, session_name: str) -> Path:
        return self.workflow_dir / "sessions" / f"{session_name}.jsonl"
    
    def environment_log_path(self, env_id: str) -> Path:
        return self.workflow_dir / "environments" / f"{env_id}.jsonl"
    
    def write_workflow_created(
        self,
        name: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        event = {
            "ts": _iso_now(),
            "event_type": "workflow.created",
            "id": self.workflow_id,
            "name": name,
        }
        if metadata:
            event["metadata"] = metadata
        _atomic_write_line(self.workflow_log_path, json.dumps(event))
    
    def write_workflow_status(self, status: AgenticWorkflowStatus) -> None:
        event = {
            "ts": _iso_now(),
            "event_type": "workflow.status",
            "status": status.value,
        }
        _atomic_write_line(self.workflow_log_path, json.dumps(event))
    
    def write_environment_created(
        self,
        env_id: str,
        env_type: AgenticEnvironmentType,
        name: str | None,
        working_dir: str,
        base_commit: str | None = None,
        source_environment_id: str | None = None,
    ) -> None:
        event = {
            "ts": _iso_now(),
            "event_type": "environment.created",
            "id": env_id,
            "env_type": env_type.value,
            "name": name,
            "working_dir": working_dir,
        }
        if base_commit:
            event["base_commit"] = base_commit
        if source_environment_id:
            event["source_environment_id"] = source_environment_id
        
        _atomic_write_line(self.workflow_log_path, json.dumps(event))
        _atomic_write_line(self.environment_log_path(env_id), json.dumps(event))
    
    def write_environment_deleted(self, env_id: str, force: bool = False) -> None:
        event = {
            "ts": _iso_now(),
            "event_type": "environment.deleted",
            "id": env_id,
            "force": force,
        }
        _atomic_write_line(self.workflow_log_path, json.dumps(event))
        _atomic_write_line(self.environment_log_path(env_id), json.dumps(event))
    
    def write_session_created(
        self,
        name: str,
        session_id: str,
        environment_id: str,
        title: str | None = None,
        agent: str | None = None,
        model: str | None = None,
    ) -> None:
        event = {
            "ts": _iso_now(),
            "event_type": "session.created",
            "id": session_id,
            "name": name,
            "environment_id": environment_id,
        }
        if title:
            event["title"] = title
        if agent:
            event["agent"] = agent
        if model:
            event["model"] = model
        
        _atomic_write_line(self.workflow_log_path, json.dumps(event))
        _atomic_write_line(self.session_log_path(name), json.dumps(event))
        binding_event = {
            "ts": _iso_now(),
            "event_type": "environment.session_bound",
            "session_id": session_id,
            "session_name": name,
        }
        _atomic_write_line(self.environment_log_path(environment_id), json.dumps(binding_event))
    
    def write_session_status(
        self,
        name: str,
        status: AgenticSessionStatus,
    ) -> None:
        event = {
            "ts": _iso_now(),
            "event_type": "session.status",
            "name": name,
            "status": status.value,
        }
        _atomic_write_line(self.workflow_log_path, json.dumps(event))
        _atomic_write_line(self.session_log_path(name), json.dumps(event))
    
    def write_session_deleted(self, name: str, session_id: str) -> None:
        event = {
            "ts": _iso_now(),
            "event_type": "session.deleted",
            "name": name,
            "id": session_id,
        }
        _atomic_write_line(self.workflow_log_path, json.dumps(event))
        _atomic_write_line(self.session_log_path(name), json.dumps(event))
    
    def write_message_sent(
        self,
        session_name: str,
        role: str,
        content_preview: str,
        message_id: str | None = None,
    ) -> None:
        event = {
            "ts": _iso_now(),
            "event_type": "message.sent",
            "role": role,
            "preview": content_preview[:200],
        }
        if message_id:
            event["id"] = message_id
        _atomic_write_line(self.session_log_path(session_name), json.dumps(event))
    
    def write_message_chunk(
        self,
        session_name: str,
        content: str,
    ) -> None:
        event = {
            "ts": _iso_now(),
            "event_type": "message.chunk",
            "content": content,
        }
        _atomic_write_line(self.session_log_path(session_name), json.dumps(event))
    
    def write_message_complete(
        self,
        session_name: str,
        message_id: str | None = None,
        tokens: int | None = None,
    ) -> None:
        event: dict[str, Any] = {
            "ts": _iso_now(),
            "event_type": "message.complete",
        }
        if message_id:
            event["id"] = message_id
        if tokens:
            event["tokens"] = tokens
        _atomic_write_line(self.session_log_path(session_name), json.dumps(event))
    
    def write_tool_call(
        self,
        session_name: str,
        tool: str,
        args: dict[str, Any] | None = None,
    ) -> None:
        event: dict[str, Any] = {
            "ts": _iso_now(),
            "event_type": "tool.call",
            "tool": tool,
        }
        if args:
            event["args"] = args
        _atomic_write_line(self.session_log_path(session_name), json.dumps(event))
    
    def write_tool_result(
        self,
        session_name: str,
        tool: str,
        success: bool,
        result_preview: str | None = None,
    ) -> None:
        event: dict[str, Any] = {
            "ts": _iso_now(),
            "event_type": "tool.result",
            "tool": tool,
            "success": success,
        }
        if result_preview:
            event["result_preview"] = result_preview[:200]
        _atomic_write_line(self.session_log_path(session_name), json.dumps(event))


@dataclass
class EventLogReader:
    """Reads and reconstructs state from JSONL event logs.
    
    Example:
        reader = EventLogReader(workflow_id="a3f8b2c")
        workflow = reader.reconstruct_workflow()
        sessions = reader.list_sessions()
        events = reader.read_session_events("reviewer")
    """
    
    workflow_id: str
    state_dir: Path = field(default_factory=get_default_state_dir)
    
    def __post_init__(self) -> None:
        if isinstance(self.state_dir, str):
            self.state_dir = Path(self.state_dir)
    
    @property
    def workflow_dir(self) -> Path:
        return self.state_dir / "workflows" / self.workflow_id
    
    @property
    def workflow_log_path(self) -> Path:
        return self.workflow_dir / "workflow.jsonl"
    
    def session_log_path(self, session_name: str) -> Path:
        return self.workflow_dir / "sessions" / f"{session_name}.jsonl"
    
    def environment_log_path(self, env_id: str) -> Path:
        return self.workflow_dir / "environments" / f"{env_id}.jsonl"
    
    def exists(self) -> bool:
        return self.workflow_log_path.exists()
    
    def read_workflow_events(self) -> list[WorkflowEvent]:
        if not self.workflow_log_path.exists():
            return []
        
        events = []
        with self.workflow_log_path.open() as f:
            for line in f:
                line = line.strip()
                if line:
                    data = json.loads(line)
                    events.append(WorkflowEvent.from_dict(data.copy()))
        return events
    
    def read_session_events(self, session_name: str) -> list[SessionEvent]:
        path = self.session_log_path(session_name)
        if not path.exists():
            return []
        
        events = []
        with path.open() as f:
            for line in f:
                line = line.strip()
                if line:
                    data = json.loads(line)
                    events.append(SessionEvent.from_dict(data.copy()))
        return events
    
    def read_environment_events(self, env_id: str) -> list[EnvironmentEvent]:
        path = self.environment_log_path(env_id)
        if not path.exists():
            return []
        
        events = []
        with path.open() as f:
            for line in f:
                line = line.strip()
                if line:
                    data = json.loads(line)
                    events.append(EnvironmentEvent.from_dict(data.copy()))
        return events
    
    def list_sessions(self) -> list[str]:
        sessions_dir = self.workflow_dir / "sessions"
        if not sessions_dir.exists():
            return []
        return [p.stem for p in sessions_dir.glob("*.jsonl")]
    
    def list_environments(self) -> list[str]:
        env_dir = self.workflow_dir / "environments"
        if not env_dir.exists():
            return []
        return [p.stem for p in env_dir.glob("*.jsonl")]
    
    def reconstruct_workflow(self) -> AgenticWorkflowHandle | None:
        events = self.read_workflow_events()
        if not events:
            return None
        
        workflow_id = self.workflow_id
        name: str | None = None
        status = AgenticWorkflowStatus.PENDING
        created_at = datetime.now(timezone.utc)
        metadata: dict[str, Any] | None = None
        
        for event in events:
            if event.event_type == "workflow.created":
                name = event.data.get("name")
                metadata = event.data.get("metadata")
                created_at = datetime.fromisoformat(event.ts)
            elif event.event_type == "workflow.status":
                status = AgenticWorkflowStatus(event.data.get("status", "pending"))
        
        return AgenticWorkflowHandle(
            id=workflow_id,
            name=name,
            status=status,
            created_at=created_at,
            metadata=metadata,
        )
    
    def reconstruct_sessions(self) -> dict[str, AgenticSessionHandle]:
        sessions: dict[str, AgenticSessionHandle] = {}
        
        for event in self.read_workflow_events():
            if event.event_type == "session.created":
                name = event.data.get("name", "")
                sessions[name] = AgenticSessionHandle(
                    id=event.data.get("id", ""),
                    name=name,
                    workflow_id=self.workflow_id,
                    environment_id=event.data.get("environment_id", ""),
                    status=AgenticSessionStatus.PENDING,
                    created_at=datetime.fromisoformat(event.ts),
                    title=event.data.get("title"),
                    agent=event.data.get("agent"),
                    model=event.data.get("model"),
                )
            elif event.event_type == "session.status":
                name = event.data.get("name", "")
                if name in sessions:
                    session = sessions[name]
                    sessions[name] = AgenticSessionHandle(
                        id=session.id,
                        name=session.name,
                        workflow_id=session.workflow_id,
                        environment_id=session.environment_id,
                        status=AgenticSessionStatus(event.data.get("status", "pending")),
                        created_at=session.created_at,
                        title=session.title,
                        agent=session.agent,
                        model=session.model,
                    )
            elif event.event_type == "session.deleted":
                name = event.data.get("name", "")
                sessions.pop(name, None)
        
        return sessions
    
    def reconstruct_environments(self) -> dict[str, AgenticEnvironmentHandle]:
        environments: dict[str, AgenticEnvironmentHandle] = {}
        
        for event in self.read_workflow_events():
            if event.event_type == "environment.created":
                env_id = event.data.get("id", "")
                environments[env_id] = AgenticEnvironmentHandle(
                    id=env_id,
                    env_type=AgenticEnvironmentType(event.data.get("env_type", "shared")),
                    name=event.data.get("name"),
                    working_dir=event.data.get("working_dir", ""),
                    created_at=datetime.fromisoformat(event.ts),
                    base_commit=event.data.get("base_commit"),
                    source_environment_id=event.data.get("source_environment_id"),
                )
            elif event.event_type == "environment.deleted":
                env_id = event.data.get("id", "")
                environments.pop(env_id, None)
        
        return environments


@dataclass
class WorkflowIndex:
    """Manages the index of all workflows for prefix lookup.
    
    Index file: ~/.local/state/doeff-agentic/index.json
    Format: {"workflow_id": "workflow_name", ...}
    """
    
    state_dir: Path = field(default_factory=get_default_state_dir)
    
    def __post_init__(self) -> None:
        if isinstance(self.state_dir, str):
            self.state_dir = Path(self.state_dir)
    
    @property
    def index_path(self) -> Path:
        return self.state_dir / "index.json"
    
    def _load(self) -> dict[str, str]:
        if self.index_path.exists():
            return json.loads(self.index_path.read_text())
        return {}
    
    def _save(self, index: dict[str, str]) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(dir=self.state_dir, suffix=".tmp")
        try:
            os.write(fd, json.dumps(index, indent=2).encode())
            os.close(fd)
            os.rename(temp_path, self.index_path)
        except Exception:
            os.close(fd)
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            raise
    
    def add(self, workflow_id: str, name: str | None) -> None:
        index = self._load()
        index[workflow_id] = name or workflow_id
        self._save(index)
    
    def remove(self, workflow_id: str) -> None:
        index = self._load()
        index.pop(workflow_id, None)
        self._save(index)
    
    def resolve_prefix(self, prefix: str) -> str | None:
        index = self._load()
        
        if prefix in index:
            return prefix
        
        if len(prefix) < 3:
            return None
        
        matches = [wf_id for wf_id in index if wf_id.startswith(prefix)]
        
        if len(matches) == 0:
            return None
        elif len(matches) == 1:
            return matches[0]
        else:
            from .exceptions import AgenticAmbiguousPrefixError
            raise AgenticAmbiguousPrefixError(prefix, matches)
    
    def list_all(self) -> dict[str, str]:
        return self._load()
    
    def iter_workflows(self) -> Iterator[tuple[str, str]]:
        yield from self._load().items()


__all__ = [
    "get_default_state_dir",
    "EventLogWriter",
    "EventLogReader",
    "WorkflowEvent",
    "SessionEvent",
    "EnvironmentEvent",
    "WorkflowIndex",
]
