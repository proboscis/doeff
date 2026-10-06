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
from pathlib import Path, PurePath
from typing import TYPE_CHECKING, Any

import hy  # noqa: F401  # .hy import hook — ToolCall / ToolAnswer live in the Hy module doeff_claude_code.lines
from doeff import EffectBase
from doeff_claude_code.lines import ModelWindow as ModelWindow
from doeff_claude_code.lines import ToolAnswer as ToolAnswer
from doeff_claude_code.lines import ToolCall as ToolCall

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
class AgentTurnUsage:
    """Tokens one turn used, and what it cost, as the agent runtime counted them.

    ``cache_write_tokens`` = tokens written to the prompt cache,
    ``cache_read_tokens`` = tokens read from it.  ``cost_usd`` = what this
    turn cost in USD, as the agent runtime (the CLI) itself priced it — for
    Claude Code, the turn's share of the CLI's own ``total_cost_usd``, which
    the CLI reports as a running total for the whole conversation.  A count or
    cost the runtime did not report (or that cannot be attributed to this turn)
    is ``None`` (never an invented 0).
    """

    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_write_tokens: int | None = None
    cache_read_tokens: int | None = None
    cost_usd: float | None = None


# Every turn end also carries what the conversation's current context size is read from
# (agora-redesign #3744): ``last_call_usage`` = the usage of the turn's last API call in the main
# conversation (subagent calls excluded; ``cost_usd`` is None — the runtime prices whole turns, not
# calls; the context size is its input side: input + cache_read + cache_write), ``last_call_model`` =
# that call's model, ``model_windows`` = each model's context window and output limit as the runtime
# stated them in the turn.  A turn that ended early carries what was read so far (``None`` / ``()``
# when nothing was — never an invented 0).


@dataclass(frozen=True, kw_only=True)
class AgentTurnCompleted:
    """The turn finished.  ``resume_from`` continues this agent's context.

    ``usage`` = the tokens the turn used and what it cost (``None`` when the
    runtime reports none of them).
    """

    result_text: str
    input_refs: tuple[str, ...] = ()
    resume_from: str
    usage: AgentTurnUsage | None = None
    last_call_usage: AgentTurnUsage | None = None
    last_call_model: str | None = None
    model_windows: tuple[ModelWindow, ...] = ()


@dataclass(frozen=True, kw_only=True)
class AgentTurnFailed:
    """The agent runtime ended the turn with an error.

    ``usage`` = the tokens the turn used before the error and what they cost
    (``None`` when the runtime reports none of them).
    """

    detail: str
    input_refs: tuple[str, ...] = ()
    resume_from: str
    usage: AgentTurnUsage | None = None
    last_call_usage: AgentTurnUsage | None = None
    last_call_model: str | None = None
    model_windows: tuple[ModelWindow, ...] = ()


@dataclass(frozen=True, kw_only=True)
class AgentTurnInterrupted:
    """The turn was stopped (``Interrupt`` / ``Stop``); the context stays.

    ``cli_kept`` tells whether the same agent CLI process stays with the
    session and runs its next turn (a stop that interrupts only the turn) or
    went down with the stop (a signal-form interrupt, ``Stop``, or the process
    exiting while stopping) — the caller must not keep resources (e.g. a
    borrowed credential) for a CLI that is gone.  It has no default: every
    producer states it.
    ``surviving_refs`` inputs run on as the next turn of the same session;
    ``dropped_refs`` inputs were never read.
    """

    cli_kept: bool
    surviving_refs: tuple[str, ...] = ()
    dropped_refs: tuple[str, ...] = ()
    resume_from: str
    last_call_usage: AgentTurnUsage | None = None
    last_call_model: str | None = None
    model_windows: tuple[ModelWindow, ...] = ()


@dataclass(frozen=True, kw_only=True)
class AgentTurnLost:
    """The runtime went away before the turn's end was read.

    The context is kept: the next turn continues from ``resume_from``.
    """

    detail: str
    resume_from: str
    last_call_usage: AgentTurnUsage | None = None
    last_call_model: str | None = None
    model_windows: tuple[ModelWindow, ...] = ()


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
class AgentThinkingDeltaEvent:
    """A partial piece of the agent's thinking before it writes its text (``text`` may be empty when the
    runtime streams the thinking without its words). An upper layer shows that the agent is thinking
    before the first text arrives (agora-redesign #3789)."""

    seq: int
    at: datetime
    text: str


@dataclass(frozen=True, kw_only=True)
class AgentToolUseEvent:
    """The agent called tools: one ToolCall per call, in block order — ``id`` (the tool_use block id),
    ``name`` (the tool) and ``input`` (the call's command: the block's input JSON object, deep-frozen).
    Each id is the one a later AgentToolResultEvent's answer names (agora-redesign #3744)."""

    seq: int
    at: datetime
    tool_calls: tuple[ToolCall, ...]


@dataclass(frozen=True, kw_only=True)
class AgentToolResultEvent:
    """Tool results went back to the agent: one ToolAnswer per result, in block order — ``id`` (the
    call it answers), ``text`` (the result's text), ``is_error`` and ``non_text_kinds`` (kinds of the
    result's non-text blocks, e.g. images, that ``text`` leaves out) (agora-redesign #3744)."""

    seq: int
    at: datetime
    answers: tuple[ToolAnswer, ...]


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
    | AgentThinkingDeltaEvent
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


@dataclass(frozen=True)
class TurnCredential:
    """An access token for this session's turns (issue #665).

    It is never a field of a caller-visible effect: ``LaunchEffect`` carries
    only ``turn_credential_ref`` (a reference the caller holds), and the launch
    handler redeems that reference with ``RedeemTurnCredentialEffect`` — this
    value is one answer of that effect (issue #979). A handler that can place
    it puts it into the agent process's turn-auth env
    (``CLAUDE_CODE_OAUTH_TOKEN`` for Claude), never through ``session_env``
    (which stays a non-auth overlay). Only the access token travels — a
    refresh token is never carried. The token is kept out of ``repr`` so
    effects, answers and errors never print it.

    ``usable_until`` is the instant (epoch seconds; generic — the answer does
    not say who lent it) from which no turn is handed to a CLI running on this
    credential: the borrower has already taken its safety margin off the
    credential's expiry, so the value is the stop instant itself and the
    process layer adds or subtracts nothing (agora-redesign #3753 (c)). The
    launch handler copies it to the session declaration; at that instant
    (inclusive) the process layer stops the live CLI between turns
    (agora-redesign #3672 D2), and the lender is told through the process
    layer's stop reason, never by this value. ``None`` means the redeemer
    does not know the instant, and then the credential never stops the CLI.
    """

    oauth_token: str = field(repr=False)
    usable_until: float | None

    def __post_init__(self) -> None:
        if not isinstance(self.oauth_token, str) or not self.oauth_token:
            raise ValueError("TurnCredential.oauth_token must be a non-empty string")
        if any(ch in self.oauth_token for ch in "\r\n\x00"):
            raise ValueError("TurnCredential.oauth_token must be a single line")
        if self.usable_until is not None and (
            isinstance(self.usable_until, bool) or not isinstance(self.usable_until, int | float)
        ):
            raise TypeError("TurnCredential.usable_until must be epoch seconds or None")


@dataclass(frozen=True)
class HomeTurnCredential:
    """Answer of ``RedeemTurnCredentialEffect``: the reference is valid, and the
    credential lives in the agent's home — launch on the handler's own home
    credentials (no token is handed over)."""


@dataclass(frozen=True)
class TurnCredentialUnavailable:
    """Answer of ``RedeemTurnCredentialEffect``: the reference cannot be redeemed
    (unknown, already returned, expired ...). ``reason`` is for people and never
    holds a credential value. The launch handler does not start the session."""

    reason: str


@dataclass(frozen=True, kw_only=True)
class RedeemTurnCredentialEffect(AgentEffectBase):
    """Redeem a turn credential reference for the credential itself (issue #979).

    Emitted by a launch handler that can place a turn credential, when
    ``LaunchEffect.turn_credential_ref`` is set, right before it starts the
    agent process. It is answered by the environment the caller installs
    outside the agent runtime handlers (whoever issued the reference) — the
    caller's program never sees the token, and the token never appears in
    ``LaunchEffect``.

    Yields: TurnCredential | HomeTurnCredential | TurnCredentialUnavailable
    """

    credential_ref: str

    def __post_init__(self) -> None:
        if not isinstance(self.credential_ref, str) or not self.credential_ref:
            raise ValueError("RedeemTurnCredentialEffect.credential_ref must be a non-empty string")


@dataclass(frozen=True)
class HandlerMadeContextId:
    """``LaunchEffect.new_context_id``: the handler makes the id of the session's
    new context itself (the caller learns it from a turn end's ``resume_from``)."""


@dataclass(frozen=True)
class NamedContextId:
    """``LaunchEffect.new_context_id``: the caller names the id of the session's
    new context.

    Two launches that name the same id (and the same launch conditions) mean
    the same new context: a runtime started ahead of the input for one
    (``WarmSessionEffect``) serves the first turn of the other.  The spelling
    the runtime accepts is the handler's to check; a handler refuses an id it
    cannot take with ``AgentLaunchError`` and an id already in use with
    ``SessionAlreadyExistsError``.
    """

    context_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.context_id, str) or not self.context_id:
            raise ValueError("NamedContextId.context_id must be a non-empty string")


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

    ``resume_snapshot`` brings the ``resume_from`` context into this runtime
    before it continues: an opaque copy of the context taken earlier with
    ``ExportContextEffect`` (possibly by another runtime whose home is gone).
    A context already present here is not overwritten. It needs
    ``resume_from`` and must not be empty. Handlers that cannot bring a
    context in refuse it with ``AgentCapabilityUnsupportedError``.

    ``new_context_id`` says who makes the id of a new context (a launch
    without ``resume_from``): the handler (``HandlerMadeContextId``, the
    default) or the caller (``NamedContextId``).  A named id cannot be
    combined with ``resume_from``.  Handlers that cannot start a context under
    a given id refuse a named one with ``AgentCapabilityUnsupportedError``.

    Yields: SessionHandle
    """

    session_name: str
    agent_type: AgentType
    # A plain path value: the handler builds the filesystem ``Path`` (and the
    # directory) itself, so a caller never needs filesystem types to ask.
    work_dir: str | PurePath
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
    # A reference to the credential for this session's turns (None = the
    # handler's own home credentials). The launch handler redeems it with
    # ``RedeemTurnCredentialEffect``; the token itself never rides on this
    # effect (issue #979). Handlers that cannot place a turn credential refuse
    # it with ``AgentCapabilityUnsupportedError``.
    turn_credential_ref: str | None = None
    # An opaque copy of the ``resume_from`` context (the answer of
    # ``ExportContextEffect``) to bring in before continuing.
    resume_snapshot: str | None = None
    # Who makes the id of the new context of a launch without ``resume_from``.
    new_context_id: HandlerMadeContextId | NamedContextId = HandlerMadeContextId()

    def __post_init__(self) -> None:
        if self.turn_credential_ref is not None and (
            not isinstance(self.turn_credential_ref, str) or not self.turn_credential_ref
        ):
            raise ValueError("LaunchEffect.turn_credential_ref must be a non-empty string")
        if not isinstance(self.new_context_id, HandlerMadeContextId | NamedContextId):
            raise TypeError(
                "LaunchEffect.new_context_id must be HandlerMadeContextId or NamedContextId"
            )
        if isinstance(self.new_context_id, NamedContextId) and self.resume_from is not None:
            raise ValueError(
                "LaunchEffect.new_context_id names a new context; it cannot be combined with resume_from"
            )
        if self.resume_snapshot is None:
            return
        if self.resume_from is None:
            raise ValueError("LaunchEffect.resume_snapshot needs resume_from")
        if not isinstance(self.resume_snapshot, str) or not self.resume_snapshot.strip():
            raise ValueError("LaunchEffect.resume_snapshot must be a non-empty string")


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


@dataclass(frozen=True, kw_only=True)
class WarmSessionEffect(AgentEffectBase):
    """Start the session's agent runtime before its next input and keep it waiting.

    Pays the runtime's start-up time (process start until it can take input)
    before the input arrives.  The session's next turn (``Send`` /
    ``FollowUp``) uses the waiting runtime when its launch conditions are
    unchanged, and starts a new one otherwise.  A session launched without a
    prompt keeps its new context, so its first turn still starts that context
    fresh (the waiting runtime has not recorded anything before the input).
    Before the first input the runtime may only report its session-start
    hooks; anything else it prints takes it down.  Stop or release the session
    to take the waiting runtime down without a turn.

    Handlers whose ``LaunchEffect`` already starts a runtime that waits for
    input (terminal handlers) refuse it with
    ``AgentCapabilityUnsupportedError`` instead of silently doing nothing; a
    stopped session is refused with ``SessionNotFoundError``.

    Yields: bool (True = the runtime waits for the session's next input;
    False = a turn is running or inputs are queued, so there is nothing to
    warm)
    """

    handle: SessionHandle


@dataclass(frozen=True, kw_only=True)
class ExportContextEffect(AgentEffectBase):
    """Take an opaque copy of an agent runtime context out of this runtime.

    ``context_id`` is the value a turn end carried as ``resume_from``;
    ``work_dir`` is the work dir the context ran in.  The copy is kept by the
    caller (for example outside a disposable home) and brought back with
    ``LaunchEffect(resume_from=context_id, resume_snapshot=copy)``.  Handlers
    that cannot take a copy refuse it with ``AgentCapabilityUnsupportedError``.

    Yields: str | None (the copy; None = this runtime has no such context)
    """

    agent_type: AgentType
    # A plain path value, like ``LaunchEffect.work_dir``.
    work_dir: str | PurePath
    context_id: str


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
    work_dir: str | PurePath,
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
    resume_snapshot: str | None = None,
    new_context_id: HandlerMadeContextId | NamedContextId = HandlerMadeContextId(),
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
        resume_snapshot=resume_snapshot,
        new_context_id=new_context_id,
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


def WarmSession(handle: SessionHandle) -> WarmSessionEffect:  # noqa: N802
    return WarmSessionEffect(handle=handle)


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


class TurnInFlightError(AgentError):
    """A turn was to be started in a context where another turn is already running.

    The runtime refuses a second turn on the same context; the caller decides
    whether to wait for the running turn or give up.
    """

    def __init__(self, *, session_id: str, context_id: str) -> None:
        self.session_id = session_id
        self.context_id = context_id
        super().__init__(
            f"session {session_id}: context {context_id} already has a turn in flight"
        )


def refuse_turn_capabilities(effect: AgentEffectBase, *, handler: str) -> None:
    """Refuse the turn-level fields a turn-less (terminal) handler cannot honour.

    Terminal handlers call this before acting on ``LaunchEffect`` /
    ``FollowUpEffect``, so ``resume_from`` never silently starts a fresh
    context, a ``turn_credential_ref`` is never silently dropped (the
    launch would run on whatever home credentials the handler has), and ``TurnInputMode.INJECT`` never silently becomes a keystroke.
    A named ``new_context_id`` is never silently replaced by an id the caller
    does not know.
    """
    if isinstance(effect, LaunchEffect) and isinstance(effect.new_context_id, NamedContextId):
        raise AgentCapabilityUnsupportedError(
            capability="LaunchEffect.new_context_id", handler=handler
        )
    if isinstance(effect, LaunchEffect) and effect.resume_from is not None:
        raise AgentCapabilityUnsupportedError(
            capability="LaunchEffect.resume_from", handler=handler
        )
    if isinstance(effect, LaunchEffect) and effect.resume_snapshot is not None:
        raise AgentCapabilityUnsupportedError(
            capability="LaunchEffect.resume_snapshot", handler=handler
        )
    if isinstance(effect, LaunchEffect) and effect.turn_credential_ref is not None:
        raise AgentCapabilityUnsupportedError(
            capability="LaunchEffect.turn_credential_ref", handler=handler
        )
    if isinstance(effect, FollowUpEffect) and effect.mode is not TurnInputMode.NEXT_TURN:
        raise AgentCapabilityUnsupportedError(
            capability=f"FollowUpEffect.mode={effect.mode.value}", handler=handler
        )


class TurnCredentialUnavailableError(AgentLaunchError):
    """``turn_credential_ref`` could not be redeemed, so the session was not started.

    ``reason`` is the ``TurnCredentialUnavailable.reason`` of the redeem answer
    (never a credential value). The caller decides what to do (e.g. refuse the
    turn as credential-unavailable).
    """

    def __init__(self, *, credential_ref: str, reason: str) -> None:
        self.credential_ref = credential_ref
        self.reason = reason
        super().__init__(f"turn credential {credential_ref} is unavailable: {reason}")


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
