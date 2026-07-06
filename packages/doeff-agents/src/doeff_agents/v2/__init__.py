"""Outcome-explicit v2 agent effect API.

The v1 ``doeff_agents.effects.agent`` surface remains import-compatible.  This
subpackage adds names whose outcome contract is visible at the call site:

* ``StartAgentSession`` starts a no-result session and returns a session handle.
* ``InvokeAgent`` runs one structured invocation and returns a validated payload.
"""


from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from doeff_agents.adapters.base import AgentSessionLifecycle, AgentType
from doeff_agents.effects.agent import (
    AgentEffect as InvokeAgentEffect,
    AgentTask as _V1AgentTask,
    AwaitOutcome as AgentInvocationOutcome,
    AwaitResultEffect as AwaitAgentInvocationResultEffect,
    AwaitStatus,
    FollowUpEffect as ContinueAgentInvocationEffect,
    JSONSchema,
    L2SessionHandle,
    LaunchEffect as StartAgentSessionEffect,
    LaunchSessionEffect as StartAgentInvocationEffect,
    ReleaseSessionEffect as ReleaseAgentInvocationEffect,
    SessionHandle as AgentSessionHandle,
    StopSessionEffect as CancelAgentInvocationEffect,
    deterministic_session_id,
)

if TYPE_CHECKING:
    from doeff.mcp import McpToolDef


AgentInvocationHandle = L2SessionHandle


@dataclass(frozen=True, kw_only=True)
class AgentSessionSpec:
    """No-result agent session start spec.

    This spec intentionally has no ``result_schema`` field.  Structured payloads
    belong to ``AgentInvocationSpec``.
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
    ready_timeout: float = 30.0
    session_env: dict[str, str] | None = None


@dataclass(frozen=True, kw_only=True)
class AgentInvocationSpec:
    """Structured result-producing agent invocation spec.

    ``result_schema`` is mandatory.  Missing it is a construction error, so a
    result-returning invocation cannot silently degrade into a raw session.
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
    deadline_seconds: float | None = None

    @property
    def session_id(self) -> str:
        return deterministic_session_id(
            run_id=self.run_id,
            node_id=self.node_id,
            attempt=self.attempt,
        )


def _to_v1_agent_task(spec: AgentInvocationSpec) -> _V1AgentTask:
    return _V1AgentTask(
        run_id=spec.run_id,
        node_id=spec.node_id,
        attempt=spec.attempt,
        agent_type=spec.agent_type,
        work_dir=spec.work_dir,
        prompt=spec.prompt,
        result_schema=spec.result_schema,
        model=spec.model,
        effort=spec.effort,
        mcp_tools=spec.mcp_tools,
        mcp_server_name=spec.mcp_server_name,
        bare=spec.bare,
        lifecycle=spec.lifecycle,
        session_env=spec.session_env,
        max_retries=spec.max_retries,
        deadline_seconds=spec.deadline_seconds,
    )


def StartAgentSession(spec: AgentSessionSpec) -> StartAgentSessionEffect:  # noqa: N802
    """Create a no-result session start effect."""

    return StartAgentSessionEffect(
        session_name=spec.session_name,
        agent_type=spec.agent_type,
        work_dir=spec.work_dir,
        prompt=spec.prompt,
        model=spec.model,
        mcp_tools=spec.mcp_tools,
        mcp_server_name=spec.mcp_server_name,
        effort=spec.effort,
        bare=spec.bare,
        lifecycle=spec.lifecycle,
        ready_timeout=spec.ready_timeout,
        session_env=spec.session_env,
    )


def StartAgentInvocation(  # noqa: N802
    spec: AgentInvocationSpec,
) -> StartAgentInvocationEffect:
    """Create an async structured invocation start effect."""

    return StartAgentInvocationEffect(spec=_to_v1_agent_task(spec))


def AwaitAgentInvocationResult(  # noqa: N802
    handle: AgentInvocationHandle,
    *,
    timeout_seconds: float | None = None,
) -> AwaitAgentInvocationResultEffect:
    """Await a structured invocation result."""

    return AwaitAgentInvocationResultEffect(
        handle=handle,
        timeout_seconds=timeout_seconds,
    )


def ContinueAgentInvocation(  # noqa: N802
    handle: AgentInvocationHandle,
    message: str,
) -> ContinueAgentInvocationEffect:
    return ContinueAgentInvocationEffect(handle=handle, message=message)


def CancelAgentInvocation(  # noqa: N802
    handle: AgentInvocationHandle,
    *,
    reason: str | None = None,
) -> CancelAgentInvocationEffect:
    return CancelAgentInvocationEffect(handle=handle, reason=reason)


def ReleaseAgentInvocation(  # noqa: N802
    handle: AgentInvocationHandle,
) -> ReleaseAgentInvocationEffect:
    return ReleaseAgentInvocationEffect(handle=handle)


def InvokeAgent(spec: AgentInvocationSpec) -> InvokeAgentEffect:  # noqa: N802
    """Create a schema-validated invocation effect."""

    return InvokeAgentEffect(task=_to_v1_agent_task(spec))


__all__ = [
    "AgentInvocationHandle",
    "AgentInvocationOutcome",
    "AgentInvocationSpec",
    "AgentSessionHandle",
    "AgentSessionSpec",
    "AwaitAgentInvocationResult",
    "AwaitAgentInvocationResultEffect",
    "AwaitStatus",
    "CancelAgentInvocation",
    "CancelAgentInvocationEffect",
    "ContinueAgentInvocation",
    "ContinueAgentInvocationEffect",
    "InvokeAgent",
    "InvokeAgentEffect",
    "ReleaseAgentInvocation",
    "ReleaseAgentInvocationEffect",
    "StartAgentInvocation",
    "StartAgentInvocationEffect",
    "StartAgentSession",
    "StartAgentSessionEffect",
]
