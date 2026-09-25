"""Agent session effects for doeff.

Fine-grained effects for agent session management.

Key design:
- LaunchEffect: flat fields (no LaunchConfig wrapper), user-facing
- ClaudeLaunchEffect: internal, emitted by claude_resolver_handler
- Monitor/Capture/Send/Stop: session lifecycle
- Get/List/Observe/Cleanup/Cancel/Attach: session state management by id
- SessionHandle: immutable value-type identifier
"""

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from doeff import EffectBase

if TYPE_CHECKING:
    from doeff.mcp import McpToolDef

from doeff_agents.adapters.base import AgentSessionLifecycle, AgentType
from doeff_agents.monitor import SessionStatus

# =============================================================================
# SessionHandle - Immutable session identifier
# =============================================================================


@dataclass(frozen=True)
class SessionHandle:
    """Immutable handle to an agent session.

    Opaque value type.  It identifies a session without exposing substrate
    nouns such as tmux pane ids; all mutable/backend state is handler-private
    and keyed by ``session_id``.
    """

    session_id: str

    def __repr__(self) -> str:
        return f"SessionHandle({self.session_id!r})"


L2SessionHandle = SessionHandle


JSONSchema = dict[str, Any]


class AwaitStatus(Enum):
    """Workflow-facing L2 await statuses."""

    EXITED = "exited"
    AWAITING_INPUT = "awaiting-input"
    TIMED_OUT = "timed-out"


class AgentValidationErrorKind(Enum):
    """Why an ``agent`` effect could not return a valid structured result."""

    ABSENT = "absent"
    INVALID = "invalid"
    AWAITING_INPUT = "awaiting-input"
    TIMED_OUT = "timed-out"
    NO_SUCH_SESSION = "no-such-session"


@dataclass(frozen=True)
class AgentValidationFailure:
    """Typed validation failure used for retry and final failure reporting."""

    kind: AgentValidationErrorKind
    message: str


@dataclass(frozen=True)
class AwaitOutcome:
    """Result of awaiting an L2 session result channel.

    ``continuable`` declares whether the session can still accept a
    corrective follow-up after a failure outcome.  Supervised (agentd)
    sessions resolve their await only on TERMINAL states — the supervisor
    has already spent the contract retries and reaped the pane, so a
    failure is final and ``continuable`` is False.  Local/scenario
    sessions stay alive across an invalid result, so the default holds.

    ``turn_end`` is the typed end of the turn the await observed, for
    handlers that see turns (the headless handler); terminal handlers
    leave it ``None``.
    """

    status: AwaitStatus
    result: Any | None = None
    validation_error: str | None = None
    exit_code: int | None = None
    continuable: bool = True
    turn_end: "AgentTurnEnd | None" = None


# =============================================================================
# Turn events — backend-neutral events of an agent runtime's turns
# (agora-redesign #604).  Handlers translate their CLI's own lines into these;
# callers never see stream-json lines, JSON-RPC messages, pids or argv.
# =============================================================================


class TurnInputMode(Enum):
    """How a follow-up input reaches the session.

    ``NEXT_TURN``: run it as the next turn (after the running turn ends).
    ``INJECT``: add it to the running turn (the agent reads it at its next
    boundary); refused with ``NoTurnInFlightError`` when no turn runs.
    """

    NEXT_TURN = "next-turn"
    INJECT = "inject"


class InputFateState(Enum):
    """What became of one input (named by its ``input_ref``)."""

    QUEUED = "queued"
    STARTED = "started"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    DISCARDED = "discarded"
    REFUSED = "refused"


@dataclass(frozen=True, kw_only=True)
class AgentTurnCompleted:
    """The turn finished.  ``resume_from`` continues this agent's context."""

    result_text: str
    input_refs: tuple[str, ...] = ()
    resume_from: str


@dataclass(frozen=True, kw_only=True)
class AgentTurnFailed:
    """The agent runtime ended the turn with an error."""

    detail: str
    input_refs: tuple[str, ...] = ()
    resume_from: str


@dataclass(frozen=True, kw_only=True)
class AgentTurnInterrupted:
    """The turn was stopped (``Interrupt`` / ``Stop``); the context stays.

    ``surviving_refs`` inputs run on as the next turn of the same session;
    ``dropped_refs`` inputs were never read.
    """

    surviving_refs: tuple[str, ...] = ()
    dropped_refs: tuple[str, ...] = ()
    resume_from: str


@dataclass(frozen=True, kw_only=True)
class AgentTurnLost:
    """The runtime went away before the turn's end was read.

    The context is kept: the next turn continues from ``resume_from``.
    """

    detail: str
    resume_from: str


AgentTurnEnd = AgentTurnCompleted | AgentTurnFailed | AgentTurnInterrupted | AgentTurnLost


@dataclass(frozen=True, kw_only=True)
class AgentTextEvent:
    """A finished piece of the agent's text."""

    seq: int
    at: datetime
    text: str


@dataclass(frozen=True, kw_only=True)
class AgentTextDeltaEvent:
    """A partial piece of text while the agent is still writing it."""

    seq: int
    at: datetime
    text: str


@dataclass(frozen=True, kw_only=True)
class AgentToolUseEvent:
    """The agent called tools (by name)."""

    seq: int
    at: datetime
    tool_names: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class AgentToolResultEvent:
    """Tool results went back to the agent."""

    seq: int
    at: datetime
    tool_use_ids: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class AgentInputFateEvent:
    """An input changed state (``input_ref`` names the input)."""

    seq: int
    at: datetime
    input_ref: str
    state: InputFateState


@dataclass(frozen=True, kw_only=True)
class AgentTurnEndEvent:
    """A turn of the session ended (exactly one per turn)."""

    seq: int
    at: datetime
    end: AgentTurnEnd


AgentEvent = (
    AgentTextEvent
    | AgentTextDeltaEvent
    | AgentToolUseEvent
    | AgentToolResultEvent
    | AgentInputFateEvent
    | AgentTurnEndEvent
)


@dataclass(frozen=True, kw_only=True)
class AgentEventPage:
    """Events after ``after_seq`` in seq order, without gaps or repeats.

    ``next_seq`` is the ``after_seq`` for the next read.  ``end`` is the end of
    the last turn when the session has no turn running and no input waiting;
    ``None`` while work is in flight.
    """

    events: tuple[AgentEvent, ...]
    next_seq: int
    end: AgentTurnEnd | None = None


@dataclass(frozen=True, kw_only=True)
class AgentSpec:
    """L2 launch specification.

    The deterministic ``session_id`` is derived from ``(run_id, node_id,
    attempt)`` and is used for idempotent re-adoption.
    """

    run_id: str
    node_id: str
    attempt: int
    agent_type: AgentType
    work_dir: Path
    prompt: str
    result_schema: JSONSchema
    model: str | None = None
    effort: str | None = None
    mcp_tools: tuple["McpToolDef", ...] = ()
    mcp_server_name: str = "doeff"
    bare: bool = False
    lifecycle: AgentSessionLifecycle = AgentSessionLifecycle.RUN_TO_COMPLETION
    session_env: dict[str, str] | None = None
    max_retries: int = 2

    @property
    def session_id(self) -> str:
        return deterministic_session_id(
            run_id=self.run_id,
            node_id=self.node_id,
            attempt=self.attempt,
        )


@dataclass(frozen=True, kw_only=True)
class AgentTask(AgentSpec):
    """L3-ish task shape consumed by the schema-driven ``agent`` effect.

    ``deadline_seconds`` is the node-spec wall-clock deadline (L-K4-3,
    k8s ``activeDeadlineSeconds`` semantics): declared by the workflow,
    observed by the attempt loop, and surfaced on exceed as
    ``AgentDeadlineExceededError`` for the orchestrator to park as a
    gate. It is NOT a transport timeout — per-await budgets are the
    keep-alive heartbeat (``DEFAULT_AWAIT_BUDGET_SECONDS``).
    """

    deadline_seconds: float | None = None


def deterministic_session_id(*, run_id: str, node_id: str, attempt: int) -> str:
    """Derive the stable session id required for replay-safe launches."""
    raw = f"{run_id}-{node_id}-{attempt}"
    return "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in raw)


# =============================================================================
# Observation - Immutable snapshot of session state
# =============================================================================


@dataclass(frozen=True)
class Observation:
    """Immutable snapshot of session state from monitoring."""

    status: SessionStatus
    output_changed: bool = False
    output_snippet: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in (
            SessionStatus.DONE,
            SessionStatus.FAILED,
            SessionStatus.EXITED,
            SessionStatus.STOPPED,
        )


@dataclass(frozen=True, kw_only=True)
class TurnRef:
    """The caller's reference to the last turn run on a session.

    Opaque to the library: ``turn_id`` is the caller's turn identifier and
    ``attempt`` its attempt number (agora-redesign #608).
    """

    turn_id: str
    attempt: int


@dataclass(frozen=True, kw_only=True)
class TranscriptRef:
    """Where the CLI's transcript for a session lives (node and path, opaque)."""

    node: str
    path: str


@dataclass(frozen=True, kw_only=True)
class AgentSessionSnapshot:
    """Persistent, backend-neutral snapshot of an agent session.

    ``caller_ref`` is the caller's identifier for whoever owns the session
    (agora puts its agent id here); the library never interprets it.
    ``node`` is the machine the session lives on, ``last_turn`` the caller's
    last turn on it, and ``transcript_ref`` where the CLI transcript is kept.
    """

    session_id: str
    session_name: str
    agent_type: AgentType
    work_dir: Path
    status: SessionStatus
    lifecycle: AgentSessionLifecycle = AgentSessionLifecycle.RUN_TO_COMPLETION
    backend_kind: str = "terminal"
    backend_ref: dict[str, str] = field(default_factory=dict)
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_observed_at: datetime | None = None
    finished_at: datetime | None = None
    cleaned_at: datetime | None = None
    output_snippet: str | None = None
    caller_ref: str | None = None
    node: str | None = None
    last_turn: TurnRef | None = None
    transcript_ref: TranscriptRef | None = None

    @classmethod
    def from_handle(
        cls,
        handle: SessionHandle,
        *,
        status: SessionStatus,
        backend_kind: str = "terminal",
        backend_ref: dict[str, str] | None = None,
        last_observed_at: datetime | None = None,
        finished_at: datetime | None = None,
        cleaned_at: datetime | None = None,
        output_snippet: str | None = None,
        lifecycle: AgentSessionLifecycle | None = None,
    ) -> "AgentSessionSnapshot":
        """Create a snapshot from the public handle."""
        return cls(
            session_id=handle.session_id,
            session_name=handle.session_id,
            agent_type=AgentType(
                str((backend_ref or {}).get("agent_type", AgentType.CUSTOM.value))
            ),
            work_dir=Path(str((backend_ref or {}).get("work_dir", "."))),
            lifecycle=lifecycle or AgentSessionLifecycle.RUN_TO_COMPLETION,
            status=status,
            backend_kind=backend_kind,
            backend_ref=backend_ref
            or {
                "session_name": handle.session_id,
            },
            started_at=datetime.now(timezone.utc),
            last_observed_at=last_observed_at,
            finished_at=finished_at,
            cleaned_at=cleaned_at,
            output_snippet=output_snippet,
        )

    def to_handle(self) -> SessionHandle:
        """Recreate the public handle from a persisted snapshot."""
        return SessionHandle(
            session_id=self.session_id,
        )

    def with_update(self, **changes: Any) -> "AgentSessionSnapshot":
        """Return a copy with selected fields updated."""
        return replace(self, **changes)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible values."""
        return {
            "session_id": self.session_id,
            "session_name": self.session_name,
            "agent_type": self.agent_type.value,
            "work_dir": str(self.work_dir),
            "lifecycle": self.lifecycle.value,
            "status": self.status.value,
            "backend_kind": self.backend_kind,
            "backend_ref": dict(self.backend_ref),
            "started_at": self.started_at.isoformat(),
            "last_observed_at": (
                self.last_observed_at.isoformat() if self.last_observed_at is not None else None
            ),
            "finished_at": self.finished_at.isoformat() if self.finished_at is not None else None,
            "cleaned_at": self.cleaned_at.isoformat() if self.cleaned_at is not None else None,
            "output_snippet": self.output_snippet,
            "caller_ref": self.caller_ref,
            "node": self.node,
            "last_turn": (
                {"turn_id": self.last_turn.turn_id, "attempt": self.last_turn.attempt}
                if self.last_turn is not None
                else None
            ),
            "transcript_ref": (
                {"node": self.transcript_ref.node, "path": self.transcript_ref.path}
                if self.transcript_ref is not None
                else None
            ),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentSessionSnapshot":
        """Deserialize from JSON-compatible values."""
        return cls(
            session_id=str(data["session_id"]),
            session_name=str(data["session_name"]),
            agent_type=AgentType(str(data["agent_type"])),
            work_dir=Path(str(data["work_dir"])),
            lifecycle=AgentSessionLifecycle(
                str(data.get("lifecycle", AgentSessionLifecycle.RUN_TO_COMPLETION.value))
            ),
            status=SessionStatus(str(data["status"])),
            backend_kind=str(data.get("backend_kind", "terminal")),
            backend_ref=dict(data.get("backend_ref", {})),
            started_at=_parse_datetime(str(data["started_at"])),
            last_observed_at=_parse_optional_datetime(data.get("last_observed_at")),
            finished_at=_parse_optional_datetime(data.get("finished_at")),
            cleaned_at=_parse_optional_datetime(data.get("cleaned_at")),
            output_snippet=data.get("output_snippet"),
            caller_ref=_parse_optional_str(data.get("caller_ref")),
            node=_parse_optional_str(data.get("node")),
            last_turn=_parse_turn_ref(data.get("last_turn")),
            transcript_ref=_parse_transcript_ref(data.get("transcript_ref")),
        )


@dataclass(frozen=True, kw_only=True)
class AgentSessionQuery:
    """Read-only filter for persistent agent session snapshots.

    Every field left ``None`` matches anything; the set fields are ANDed.
    ``matches`` is the single definition of the filter — every repository
    and store handler answers ``ListAgentSessions`` by it.
    """

    status: SessionStatus | None = None
    agent_type: AgentType | None = None
    backend_kind: str | None = None
    lifecycle: AgentSessionLifecycle | None = None
    caller_ref: str | None = None
    node: str | None = None

    def matches(self, snapshot: "AgentSessionSnapshot") -> bool:
        """Whether ``snapshot`` satisfies every field this query sets."""
        return all(
            expected is None or getattr(snapshot, name) == expected
            for name, expected in (
                ("status", self.status),
                ("agent_type", self.agent_type),
                ("backend_kind", self.backend_kind),
                ("lifecycle", self.lifecycle),
                ("caller_ref", self.caller_ref),
                ("node", self.node),
            )
        )


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _parse_optional_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(str(value))


def _parse_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _parse_turn_ref(value: Any) -> TurnRef | None:
    if value is None:
        return None
    return TurnRef(turn_id=str(value["turn_id"]), attempt=int(value["attempt"]))


def _parse_transcript_ref(value: Any) -> TranscriptRef | None:
    if value is None:
        return None
    return TranscriptRef(node=str(value["node"]), path=str(value["path"]))


# =============================================================================
# Effect Base
# =============================================================================


@dataclass(frozen=True, kw_only=True)
class AgentEffectBase(EffectBase):
    """Base class for agent effects."""


# =============================================================================
# Launch Effects
# =============================================================================


@dataclass(frozen=True, kw_only=True)
class LaunchEffect(AgentEffectBase):
    """Launch a new agent session.

    User-facing effect — flat fields, no config wrapper.
    The claude_resolver_handler converts this to ClaudeLaunchEffect
    when agent_type is CLAUDE.

    ``resume_from`` continues an earlier context of the agent runtime (the
    value an earlier turn end carried as ``resume_from``; opaque to callers).
    Handlers that cannot continue a context refuse it with
    ``AgentCapabilityUnsupportedError`` rather than starting fresh.

    Yields: SessionHandle
    """

    session_name: str
    agent_type: AgentType
    work_dir: Path
    prompt: str | None = None
    model: str | None = None
    mcp_tools: tuple["McpToolDef", ...] = ()
    mcp_server_name: str = "doeff"
    effort: str | None = None
    bare: bool = False
    lifecycle: AgentSessionLifecycle = AgentSessionLifecycle.RUN_TO_COMPLETION
    # Cold-start budget: matches the doeff-agentd oracle's 120s REPL-idle wait.
    ready_timeout: float = 120.0
    session_env: dict[str, str] | None = None
    resume_from: str | None = None


@dataclass(frozen=True, kw_only=True)
class ClaudeLaunchEffect(AgentEffectBase):
    """Claude-specific launch — internal effect (handler-to-handler).

    Emitted by claude_resolver_handler, handled by claude_handler.
    Trust setup, MCP server, tmux, onboarding are handler-internal.

    Yields: SessionHandle
    """

    session_name: str
    work_dir: Path
    prompt: str | None = None
    model: str | None = None
    mcp_tools: tuple["McpToolDef", ...] = ()
    mcp_server_name: str = "doeff"
    effort: str | None = None
    bare: bool = False
    lifecycle: AgentSessionLifecycle = AgentSessionLifecycle.RUN_TO_COMPLETION
    # Cold-start budget: matches the doeff-agentd oracle's 120s REPL-idle wait.
    ready_timeout: float = 120.0
    session_env: dict[str, str] | None = None


# =============================================================================
# L2 Session Algebra Effects
# =============================================================================


@dataclass(frozen=True, kw_only=True)
class LaunchSessionEffect(AgentEffectBase):
    """L2 Launch: idempotently create or re-adopt a deterministic session.

    Yields: L2SessionHandle
    """

    spec: AgentSpec


@dataclass(frozen=True, kw_only=True)
class AwaitResultEffect(AgentEffectBase):
    """L2 AwaitResult: await a schema result channel in one step.

    Yields: AwaitOutcome
    """

    handle: L2SessionHandle
    timeout_seconds: float | None = None


@dataclass(frozen=True, kw_only=True)
class FollowUpEffect(AgentEffectBase):
    """L2 FollowUp: continue an existing session or adapter-private retry.

    ``mode`` chooses next turn or injection into the running turn;
    ``input_ref`` names the input in ``AgentInputFateEvent`` (the handler
    names it when ``None``).  Handlers without turns refuse ``INJECT`` with
    ``AgentCapabilityUnsupportedError`` (and do not report fates for
    ``input_ref``).  A ``NEXT_TURN`` input that waits behind a running turn
    starts at the next effect that touches the session after that turn ends
    (handlers act only when an effect arrives).

    Yields: L2SessionHandle
    """

    handle: L2SessionHandle
    message: str
    mode: TurnInputMode = TurnInputMode.NEXT_TURN
    input_ref: str | None = None


@dataclass(frozen=True, kw_only=True)
class StopSessionEffect(AgentEffectBase):
    """L2 Stop: idempotent abort.

    Yields: None
    """

    handle: L2SessionHandle
    reason: str | None = None


@dataclass(frozen=True, kw_only=True)
class ReleaseSessionEffect(AgentEffectBase):
    """L2 Release: reclaim handler resources after a session is no longer used.

    Yields: None
    """

    handle: L2SessionHandle


@dataclass(frozen=True, kw_only=True)
class AgentEffect(AgentEffectBase):
    """Schema-validated worker invocation.

    Yields: the validated structured result object.
    """

    task: AgentTask


# =============================================================================
# Session Lifecycle Effects
# =============================================================================


@dataclass(frozen=True, kw_only=True)
class MonitorEffect(AgentEffectBase):
    """Check and update session status (single poll).

    Yields: Observation
    """

    handle: SessionHandle


@dataclass(frozen=True, kw_only=True)
class CaptureEffect(AgentEffectBase):
    """Capture current pane output.

    Yields: str
    """

    handle: SessionHandle
    lines: int = 100


@dataclass(frozen=True, kw_only=True)
class SendEffect(AgentEffectBase):
    """Send a message or keys to the session.

    Yields: None
    """

    handle: SessionHandle
    message: str
    enter: bool = True
    literal: bool = True


@dataclass(frozen=True, kw_only=True)
class StopEffect(AgentEffectBase):
    """Stop (kill) an agent session.

    Yields: None
    """

    handle: SessionHandle


@dataclass(frozen=True, kw_only=True)
class InterruptEffect(AgentEffectBase):
    """Stop the running turn only; the session and its context stay.

    The turn's end arrives as ``AgentTurnInterrupted`` through ``Events`` /
    ``AwaitResult``.  Handlers without turns refuse it with
    ``AgentCapabilityUnsupportedError``.

    Yields: bool (True = a turn was running and was asked to stop)
    """

    handle: SessionHandle


@dataclass(frozen=True, kw_only=True)
class EventsEffect(AgentEffectBase):
    """Read the session's turn events after ``after_seq``.

    Waits up to ``wait_seconds`` for a new event or for the session to go
    idle; reading also advances the session (a waiting input starts once the
    running turn's end is read).  Handlers without turns refuse it with
    ``AgentCapabilityUnsupportedError``.

    Yields: AgentEventPage
    """

    handle: SessionHandle
    after_seq: int = -1
    wait_seconds: float = 0.0


# =============================================================================
# Session State Effects
# =============================================================================


@dataclass(frozen=True, kw_only=True)
class GetAgentSessionEffect(AgentEffectBase):
    """Read a persisted session snapshot by public session id.

    Yields: AgentSessionSnapshot | None
    """

    session_id: str


@dataclass(frozen=True, kw_only=True)
class ListAgentSessionsEffect(AgentEffectBase):
    """List persisted session snapshots.

    Yields: tuple[AgentSessionSnapshot, ...]
    """

    query: AgentSessionQuery = field(default_factory=AgentSessionQuery)


@dataclass(frozen=True, kw_only=True)
class PutAgentSessionEffect(AgentEffectBase):
    """Persist a session snapshot, replacing the row with the same session id.

    The write half of the session store (agora-redesign #608). The handler is
    chosen by the deployment: memory for tests, a SQL table (PostgreSQL in the
    cluster, SQLite locally) for real runs.

    Yields: AgentSessionSnapshot (the stored snapshot)
    """

    snapshot: AgentSessionSnapshot


@dataclass(frozen=True, kw_only=True)
class ObserveAgentSessionEffect(AgentEffectBase):
    """Observe a session by id and persist the resulting snapshot.

    Yields: AgentSessionSnapshot
    """

    session_id: str
    lines: int = 100


@dataclass(frozen=True, kw_only=True)
class AttachAgentSessionEffect(AgentEffectBase):
    """Attach to a session by id using the active backend.

    Yields: None
    """

    session_id: str


@dataclass(frozen=True, kw_only=True)
class CancelAgentSessionEffect(AgentEffectBase):
    """Cancel a running session by id and persist the resulting status.

    Yields: AgentSessionSnapshot
    """

    session_id: str


@dataclass(frozen=True, kw_only=True)
class CleanupAgentSessionEffect(AgentEffectBase):
    """Clean up backend resources for a session by id.

    Yields: AgentSessionSnapshot
    """

    session_id: str


# =============================================================================
# Deprecated — kept temporarily for backward compatibility during migration
# =============================================================================

# These remain only for compatibility with callers that have not migrated to
# LaunchEffect / LaunchSession yet.


@dataclass(frozen=True, kw_only=True)
class _DeprecatedLaunchTaskEffect(AgentEffectBase):
    """DEPRECATED: Use LaunchEffect directly. Will be removed."""

    session_name: str
    # Stub — just enough for old code to import without crashing


LaunchTaskEffect = _DeprecatedLaunchTaskEffect  # backward compat alias


# =============================================================================
# Effect Constructors
# =============================================================================


def Launch(  # noqa: N802
    session_name: str,
    *,
    agent_type: AgentType,
    work_dir: Path,
    prompt: str | None = None,
    model: str | None = None,
    mcp_tools: tuple["McpToolDef", ...] = (),
    mcp_server_name: str = "doeff",
    effort: str | None = None,
    bare: bool = False,
    lifecycle: AgentSessionLifecycle = AgentSessionLifecycle.RUN_TO_COMPLETION,
    ready_timeout: float = 120.0,
    session_env: dict[str, str] | None = None,
    resume_from: str | None = None,
) -> LaunchEffect:
    """Create a Launch effect with flat fields."""
    return LaunchEffect(
        session_name=session_name,
        agent_type=agent_type,
        work_dir=work_dir,
        prompt=prompt,
        model=model,
        mcp_tools=mcp_tools,
        mcp_server_name=mcp_server_name,
        effort=effort,
        bare=bare,
        lifecycle=lifecycle,
        ready_timeout=ready_timeout,
        session_env=session_env,
        resume_from=resume_from,
    )


def LaunchSession(spec: AgentSpec) -> LaunchSessionEffect:  # noqa: N802
    return LaunchSessionEffect(spec=spec)


def AwaitResult(  # noqa: N802
    handle: L2SessionHandle,
    *,
    timeout_seconds: float | None = None,
) -> AwaitResultEffect:
    return AwaitResultEffect(handle=handle, timeout_seconds=timeout_seconds)


def FollowUp(  # noqa: N802
    handle: L2SessionHandle,
    message: str,
    *,
    mode: TurnInputMode = TurnInputMode.NEXT_TURN,
    input_ref: str | None = None,
) -> FollowUpEffect:
    return FollowUpEffect(handle=handle, message=message, mode=mode, input_ref=input_ref)


def StopSession(  # noqa: N802
    handle: L2SessionHandle,
    *,
    reason: str | None = None,
) -> StopSessionEffect:
    return StopSessionEffect(handle=handle, reason=reason)


def ReleaseSession(handle: L2SessionHandle) -> ReleaseSessionEffect:  # noqa: N802
    return ReleaseSessionEffect(handle=handle)


def agent(task: AgentTask) -> AgentEffect:
    """Create a schema-validated ``agent`` effect."""
    return AgentEffect(task=task)


def Monitor(handle: SessionHandle) -> MonitorEffect:  # noqa: N802
    return MonitorEffect(handle=handle)


def Capture(handle: SessionHandle, *, lines: int = 100) -> CaptureEffect:  # noqa: N802
    return CaptureEffect(handle=handle, lines=lines)


def Send(  # noqa: N802
    handle: SessionHandle,
    message: str,
    *,
    enter: bool = True,
    literal: bool = True,
) -> SendEffect:
    return SendEffect(handle=handle, message=message, enter=enter, literal=literal)


def Stop(handle: SessionHandle) -> StopEffect:  # noqa: N802
    return StopEffect(handle=handle)


def Interrupt(handle: SessionHandle) -> InterruptEffect:  # noqa: N802
    return InterruptEffect(handle=handle)


def Events(  # noqa: N802
    handle: SessionHandle,
    *,
    after_seq: int = -1,
    wait_seconds: float = 0.0,
) -> EventsEffect:
    return EventsEffect(handle=handle, after_seq=after_seq, wait_seconds=wait_seconds)


def GetAgentSession(session_id: str) -> GetAgentSessionEffect:  # noqa: N802
    return GetAgentSessionEffect(session_id=session_id)


def ListAgentSessions(  # noqa: N802
    *,
    status: SessionStatus | None = None,
    agent_type: AgentType | None = None,
    backend_kind: str | None = None,
    lifecycle: AgentSessionLifecycle | None = None,
    caller_ref: str | None = None,
    node: str | None = None,
) -> ListAgentSessionsEffect:
    return ListAgentSessionsEffect(
        query=AgentSessionQuery(
            status=status,
            agent_type=agent_type,
            backend_kind=backend_kind,
            lifecycle=lifecycle,
            caller_ref=caller_ref,
            node=node,
        )
    )


def PutAgentSession(snapshot: AgentSessionSnapshot) -> PutAgentSessionEffect:  # noqa: N802
    return PutAgentSessionEffect(snapshot=snapshot)


def ObserveAgentSession(  # noqa: N802
    session_id: str,
    *,
    lines: int = 100,
) -> ObserveAgentSessionEffect:
    return ObserveAgentSessionEffect(session_id=session_id, lines=lines)


def AttachAgentSession(session_id: str) -> AttachAgentSessionEffect:  # noqa: N802
    return AttachAgentSessionEffect(session_id=session_id)


def CancelAgentSession(session_id: str) -> CancelAgentSessionEffect:  # noqa: N802
    return CancelAgentSessionEffect(session_id=session_id)


def CleanupAgentSession(session_id: str) -> CleanupAgentSessionEffect:  # noqa: N802
    return CleanupAgentSessionEffect(session_id=session_id)


# =============================================================================
# Errors
# =============================================================================


class AgentError(Exception):
    """Base class for agent-related errors."""


class AgentLaunchError(AgentError):
    """Error during agent launch."""


class AgentNotAvailableError(AgentLaunchError):
    """Agent CLI is not available."""


class AgentReadyTimeoutError(AgentLaunchError):
    """Agent did not become ready within timeout."""


class SessionNotFoundError(AgentError):
    """Session does not exist."""


class SessionAlreadyExistsError(AgentError):
    """Session already exists."""


class AgentCapabilityUnsupportedError(AgentError):
    """The installed handler cannot do what the effect asks.

    Raised instead of silently doing something else (for example a terminal
    handler asked to continue a context, or the headless handler asked for
    pane output).
    """

    def __init__(self, *, capability: str, handler: str) -> None:
        self.capability = capability
        self.handler = handler
        super().__init__(f"{handler} does not support {capability}")


class NoTurnInFlightError(AgentError):
    """An input was to be injected into the running turn, but none runs."""

    def __init__(self, *, session_id: str) -> None:
        self.session_id = session_id
        super().__init__(f"session {session_id} has no turn in flight")


def refuse_turn_capabilities(effect: AgentEffectBase, *, handler: str) -> None:
    """Refuse the turn-level fields a turn-less (terminal) handler cannot honour.

    Terminal handlers call this before acting on ``LaunchEffect`` /
    ``FollowUpEffect``, so ``resume_from`` never silently starts a fresh
    context and ``TurnInputMode.INJECT`` never silently becomes a keystroke.
    """
    if isinstance(effect, LaunchEffect) and effect.resume_from is not None:
        raise AgentCapabilityUnsupportedError(
            capability="LaunchEffect.resume_from", handler=handler
        )
    if isinstance(effect, FollowUpEffect) and effect.mode is not TurnInputMode.NEXT_TURN:
        raise AgentCapabilityUnsupportedError(
            capability=f"FollowUpEffect.mode={effect.mode.value}", handler=handler
        )


class ResumeTargetNotFoundError(AgentLaunchError):
    """``resume_from`` names a context the runtime cannot find here.

    The caller decides: bring the context over, or start a fresh one.
    """

    def __init__(self, *, resume_from: str) -> None:
        self.resume_from = resume_from
        super().__init__(f"no context to resume from: {resume_from}")


class AgentAttemptExhaustedError(AgentError):
    """Raised when ``agent`` exhausts its schema-retry budget."""

    def __init__(
        self,
        *,
        session_id: str,
        attempts: int,
        last_error: AgentValidationFailure,
    ) -> None:
        self.session_id = session_id
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(
            f"agent session {session_id} exhausted {attempts} attempts: "
            f"{last_error.kind.value}: {last_error.message}"
        )


class AgentDeadlineExceededError(AgentError):
    """Raised when ``agent`` exceeds its node-spec wall-clock deadline (L-K4-3).

    Distinct from ``AgentAttemptExhaustedError`` (the unit/attempt budget
    axis): the session may still be alive and healthily working — the
    declared wall-clock window simply ran out. The orchestrator parks
    this as a gate; the ONLY extension path is a gate answer.
    """

    def __init__(
        self,
        *,
        session_id: str,
        deadline_seconds: float,
        elapsed_seconds: float,
    ) -> None:
        self.session_id = session_id
        self.deadline_seconds = deadline_seconds
        self.elapsed_seconds = elapsed_seconds
        super().__init__(
            f"agent session {session_id} exceeded its wall-clock deadline: "
            f"{elapsed_seconds:.1f}s elapsed against a {deadline_seconds:.1f}s deadline"
        )


__all__ = [
    "AgentAttemptExhaustedError",
    "AgentCapabilityUnsupportedError",
    "AgentDeadlineExceededError",
    "AgentEffect",
    "AgentError",
    "AgentEvent",
    "AgentEventPage",
    "AgentInputFateEvent",
    "AgentLaunchError",
    "AgentNotAvailableError",
    "AgentReadyTimeoutError",
    "AgentSessionQuery",
    "AgentSessionSnapshot",
    "AgentSpec",
    "AgentTask",
    "AgentTextDeltaEvent",
    "AgentTextEvent",
    "AgentToolResultEvent",
    "AgentToolUseEvent",
    "AgentTurnCompleted",
    "AgentTurnEnd",
    "AgentTurnEndEvent",
    "AgentTurnFailed",
    "AgentTurnInterrupted",
    "AgentTurnLost",
    "AgentValidationErrorKind",
    "AgentValidationFailure",
    "AttachAgentSession",
    "AttachAgentSessionEffect",
    "AwaitOutcome",
    "AwaitResult",
    "AwaitResultEffect",
    "AwaitStatus",
    "CancelAgentSession",
    "CancelAgentSessionEffect",
    "Capture",
    "CaptureEffect",
    "ClaudeLaunchEffect",
    "CleanupAgentSession",
    "CleanupAgentSessionEffect",
    "Events",
    "EventsEffect",
    "FollowUp",
    "FollowUpEffect",
    "GetAgentSession",
    "GetAgentSessionEffect",
    "InputFateState",
    "Interrupt",
    "InterruptEffect",
    "JSONSchema",
    "L2SessionHandle",
    "Launch",
    "LaunchEffect",
    "LaunchSession",
    "LaunchSessionEffect",
    "ListAgentSessions",
    "ListAgentSessionsEffect",
    "Monitor",
    "MonitorEffect",
    "NoTurnInFlightError",
    "Observation",
    "ObserveAgentSession",
    "ObserveAgentSessionEffect",
    "PutAgentSession",
    "PutAgentSessionEffect",
    "ReleaseSession",
    "ReleaseSessionEffect",
    "ResumeTargetNotFoundError",
    "Send",
    "SendEffect",
    "SessionAlreadyExistsError",
    "SessionHandle",
    "SessionNotFoundError",
    "Stop",
    "StopEffect",
    "StopSession",
    "StopSessionEffect",
    "TranscriptRef",
    "TurnInputMode",
    "TurnRef",
    "agent",
    "deterministic_session_id",
    "refuse_turn_capabilities",
]
