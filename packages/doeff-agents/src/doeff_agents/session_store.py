"""Persistent agent session state repositories.

The public doeff effects expose semantic operations such as
``GetAgentSession`` and ``ObserveAgentSession``. Repository methods in this
module are handler internals: they persist facts after a handler has already
performed the corresponding backend operation.

段 7 lane 7c(agora-redesign・決定 1.3): どの path に何を書くかの判断は
この module に残り、file の読み書きは `doeff_agents.io_effects` の要求に
なった。実行する家は ``io_root``(本番 / 検)が選ぶ。
"""

import json
import posixpath
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import hy  # noqa: F401  # .hy import hook — the I/O effect vocabulary is a Hy module
from doeff import do

from doeff_agents.effects import AgentSessionQuery, AgentSessionSnapshot
from doeff_agents.io_effects import append_text, list_dir, make_dirs, read_text, write_text
from doeff_agents.io_root import IoGenerator, IoRoot, as_optional_str, as_str_tuple


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
    """Vault/file-backed repository using event JSONL plus snapshot files.

    The repository holds no raw I/O: each operation is a program run through
    ``io_root``, which the constructing site chooses.
    """

    def __init__(
        self,
        root: Path,
        *,
        io_root: IoRoot | None = None,
    ) -> None:
        self.root = root
        self._io: IoRoot = io_root if io_root is not None else _default_io_root()
        self._io(make_dirs(str(root)))

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
        self._io(
            record_snapshot_program(
                str(self.root), event, snapshot, self._event_path(snapshot.session_id),
                self._snapshot_path(snapshot.session_id),
            )
        )
        return snapshot

    def get_session(self, session_id: str) -> AgentSessionSnapshot | None:
        found = self._io(read_snapshot_program(self._snapshot_path(session_id)))
        if found is not None and not isinstance(found, AgentSessionSnapshot):
            raise TypeError(f"session の断面の形が違う: {found!r}")
        return found

    def list_sessions(
        self,
        query: AgentSessionQuery | None = None,
    ) -> tuple[AgentSessionSnapshot, ...]:
        found = self._io(list_snapshots_program(str(self.root), query))
        if not isinstance(found, tuple) or not all(
            isinstance(item, AgentSessionSnapshot) for item in found
        ):
            raise TypeError(f"session の断面の並びの形が違う: {found!r}")
        return found

    def _event_path(self, session_id: str) -> str:
        return posixpath.join(str(self.root), f"{_safe_session_id(session_id)}.jsonl")

    def _snapshot_path(self, session_id: str) -> str:
        return posixpath.join(str(self.root), f"{_safe_session_id(session_id)}.snapshot.json")


@do
def record_snapshot_program(
    root: str,
    event: "AgentSessionEvent",
    snapshot: AgentSessionSnapshot,
    event_path: str,
    snapshot_path: str,
) -> IoGenerator[None]:
    """Program appending one event line and rewriting the snapshot file."""
    yield make_dirs(root)
    yield append_text(event_path, json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
    yield write_text(
        snapshot_path, json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2)
    )
    return None


@do
def read_snapshot_program(snapshot_path: str) -> IoGenerator[AgentSessionSnapshot | None]:
    """Program reading one snapshot file; absent is None, not an error."""
    raw_text = as_optional_str((yield read_text(snapshot_path)))
    if raw_text is None:
        return None
    return AgentSessionSnapshot.from_dict(json.loads(raw_text))


@do
def list_snapshots_program(root: str, query: AgentSessionQuery | None) -> IoGenerator[tuple[AgentSessionSnapshot, ...]]:
    """Program reading every snapshot under ``root`` that matches ``query``."""
    snapshots: list[AgentSessionSnapshot] = []
    paths = as_str_tuple((yield list_dir(root, "*.snapshot.json")))
    for snapshot_path in paths:
        raw_text = as_optional_str((yield read_text(snapshot_path)))
        if raw_text is None:
            continue
        snapshot = AgentSessionSnapshot.from_dict(json.loads(raw_text))
        if _matches_query(snapshot, query):
            snapshots.append(snapshot)
    return tuple(snapshots)


def _default_io_root() -> IoRoot:
    from doeff_agents.io_handlers import run_driver_io

    return run_driver_io


def _matches_query(
    snapshot: AgentSessionSnapshot,
    query: AgentSessionQuery | None,
) -> bool:
    # The filter has one definition point: AgentSessionQuery.matches (#608).
    return query is None or query.matches(snapshot)


def _safe_session_id(session_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", session_id)


__all__ = [
    "AgentSessionEvent",
    "AgentSessionRepository",
    "InMemoryAgentSessionRepository",
    "JsonlAgentSessionRepository",
    "list_snapshots_program",
    "read_snapshot_program",
    "record_snapshot_program",
]
