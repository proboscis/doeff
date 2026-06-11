"""Persistent agent session state repositories.

The public doeff effects expose semantic operations such as
``GetAgentSession`` and ``ObserveAgentSession``. Repository methods in this
module are handler internals: they persist facts after a handler has already
performed the corresponding backend operation.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from doeff_agents.effects import AgentSessionQuery, AgentSessionSnapshot


@dataclass(frozen=True, kw_only=True)
class AgentSessionEvent:
    """Internal event recorded by a session handler."""

    event_type: str
    session_id: str
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    snapshot: AgentSessionSnapshot | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "session_id": self.session_id,
            "occurred_at": self.occurred_at.isoformat(),
            "snapshot": self.snapshot.to_dict()
            if self.snapshot is not None
            else None,
            "details": dict(self.details),
        }


class AgentSessionRepository(Protocol):
    """Internal repository used by agent handlers."""

    def record_snapshot(
        self,
        event_type: str,
        snapshot: AgentSessionSnapshot,
        *,
        details: dict[str, Any] | None = None,
    ) -> AgentSessionSnapshot: ...

    def get_session(self, session_id: str) -> AgentSessionSnapshot | None: ...

    def list_sessions(
        self,
        query: AgentSessionQuery | None = None,
    ) -> tuple[AgentSessionSnapshot, ...]: ...


class InMemoryAgentSessionRepository:
    """In-memory repository for tests and short-lived processes."""

    def __init__(self) -> None:
        self.events: list[AgentSessionEvent] = []
        self.snapshots: dict[str, AgentSessionSnapshot] = {}

    def record_snapshot(
        self,
        event_type: str,
        snapshot: AgentSessionSnapshot,
        *,
        details: dict[str, Any] | None = None,
    ) -> AgentSessionSnapshot:
        self.snapshots[snapshot.session_id] = snapshot
        self.events.append(
            AgentSessionEvent(
                event_type=event_type,
                session_id=snapshot.session_id,
                snapshot=snapshot,
                details=details or {},
            )
        )
        return snapshot

    def get_session(self, session_id: str) -> AgentSessionSnapshot | None:
        return self.snapshots.get(session_id)

    def list_sessions(
        self,
        query: AgentSessionQuery | None = None,
    ) -> tuple[AgentSessionSnapshot, ...]:
        return tuple(
            snapshot
            for snapshot in self.snapshots.values()
            if _matches_query(snapshot, query)
        )


class JsonlAgentSessionRepository:
    """Vault/file-backed repository using event JSONL plus snapshot files."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def record_snapshot(
        self,
        event_type: str,
        snapshot: AgentSessionSnapshot,
        *,
        details: dict[str, Any] | None = None,
    ) -> AgentSessionSnapshot:
        event = AgentSessionEvent(
            event_type=event_type,
            session_id=snapshot.session_id,
            snapshot=snapshot,
            details=details or {},
        )
        event_path = self._event_path(snapshot.session_id)
        snapshot_path = self._snapshot_path(snapshot.session_id)
        event_path.parent.mkdir(parents=True, exist_ok=True)
        with event_path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(event.to_dict(), ensure_ascii=False))
            fp.write("\n")
        snapshot_path.write_text(
            json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return snapshot

    def get_session(self, session_id: str) -> AgentSessionSnapshot | None:
        snapshot_path = self._snapshot_path(session_id)
        if not snapshot_path.exists():
            return None
        data = json.loads(snapshot_path.read_text(encoding="utf-8"))
        return AgentSessionSnapshot.from_dict(data)

    def list_sessions(
        self,
        query: AgentSessionQuery | None = None,
    ) -> tuple[AgentSessionSnapshot, ...]:
        snapshots: list[AgentSessionSnapshot] = []
        for snapshot_path in sorted(self.root.glob("*.snapshot.json")):
            data = json.loads(snapshot_path.read_text(encoding="utf-8"))
            snapshot = AgentSessionSnapshot.from_dict(data)
            if _matches_query(snapshot, query):
                snapshots.append(snapshot)
        return tuple(snapshots)

    def _event_path(self, session_id: str) -> Path:
        return self.root / f"{_safe_session_id(session_id)}.jsonl"

    def _snapshot_path(self, session_id: str) -> Path:
        return self.root / f"{_safe_session_id(session_id)}.snapshot.json"


def _matches_query(
    snapshot: AgentSessionSnapshot,
    query: AgentSessionQuery | None,
) -> bool:
    if query is None:
        return True
    if query.status is not None and snapshot.status != query.status:
        return False
    if query.agent_type is not None and snapshot.agent_type != query.agent_type:
        return False
    return query.backend_kind is None or snapshot.backend_kind == query.backend_kind


def _safe_session_id(session_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", session_id)


__all__ = [
    "AgentSessionEvent",
    "AgentSessionRepository",
    "InMemoryAgentSessionRepository",
    "JsonlAgentSessionRepository",
]
