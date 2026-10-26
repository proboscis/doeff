"""What passes only between doeff-agents' own handlers (internal to doeff-agents).

``ClaudeLaunchEffect`` is the Claude-specific launch a handler emits for another
handler, and ``refuse_turn_capabilities`` is the refusal every terminal handler
applies to the turn-level fields it cannot honour. Neither is part of the public
effects a doeff-agents user yields (``doeff_agents.effects``).
"""

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from doeff_agents.effects.agent import (
    AgentCapabilityUnsupportedError,
    AgentEffectBase,
    AgentSessionLifecycle,
    FollowUpEffect,
    LaunchEffect,
    NamedContextId,
    TurnInputMode,
)

if TYPE_CHECKING:
    from doeff.mcp import McpToolDef


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


def refuse_turn_capabilities(effect: AgentEffectBase, *, handler: str) -> None:
    """Refuse the turn-level fields a turn-less (terminal) handler cannot honour.

    Terminal handlers call this before acting on ``LaunchEffect`` /
    ``FollowUpEffect``, so ``resume_from`` never silently starts a fresh
    context, a ``turn_credential_ref`` is never silently dropped (the
    launch would run on whatever home credentials the handler has), and ``TurnInputMode.INJECT`` never silently becomes a keystroke.
    A named ``new_context_id`` is never silently replaced by an id the caller
    does not know. A stated ``autocompact`` never silently falls back to the
    runtime's own default.
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
    if isinstance(effect, LaunchEffect) and effect.autocompact is not None:
        raise AgentCapabilityUnsupportedError(
            capability="LaunchEffect.autocompact", handler=handler
        )
    if isinstance(effect, FollowUpEffect) and effect.mode is not TurnInputMode.NEXT_TURN:
        raise AgentCapabilityUnsupportedError(
            capability=f"FollowUpEffect.mode={effect.mode.value}", handler=handler
        )
    # Images never silently fall off the prompt (a terminal handler has no way
    # to hand them to the runtime).
    if isinstance(effect, LaunchEffect) and effect.attachments:
        raise AgentCapabilityUnsupportedError(
            capability="LaunchEffect.attachments", handler=handler
        )
    if isinstance(effect, FollowUpEffect) and effect.attachments:
        raise AgentCapabilityUnsupportedError(
            capability="FollowUpEffect.attachments", handler=handler
        )
