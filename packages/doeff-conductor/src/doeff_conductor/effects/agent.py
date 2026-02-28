"""
Agent effects for doeff-conductor.

Effects for managing agent sessions:
- RunAgent: Run agent to completion
- SpawnAgent: Start agent without waiting
- SendMessage: Send message to running agent
- WaitForStatus: Wait for agent status
- CaptureOutput: Get agent output
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .base import ConductorEffectBase

if TYPE_CHECKING:
    from doeff_agentic import AgenticSessionStatus

    from ..types import AgentRef, WorktreeEnv


@dataclass(frozen=True, kw_only=True)
class RunAgent(ConductorEffectBase):
    """Run an agent to completion.

    Spawns an agent in the specified environment, sends the prompt,
    and waits for it to finish.

    Yields: str (agent output)

    Example:
        @do
        def implement_feature(issue):
            env = yield CreateWorktree(issue=issue)
            output = yield RunAgent(env=env, prompt=issue.body)
            return output
    """

    env: "WorktreeEnv"  # Environment to run agent in
    prompt: str  # Initial prompt for the agent
    agent_type: str = "claude"  # Agent type (claude, codex, gemini)
    name: str | None = None  # Session name
    profile: str | None = None  # Agent profile/persona
    timeout: float | None = None  # Timeout in seconds


@dataclass(frozen=True, kw_only=True)
class SpawnAgent(ConductorEffectBase):
    """Start an agent without waiting for completion.

    Returns immediately with an AgentRef for later interaction.

    Yields: AgentRef

    Example:
        @do
        def spawn_reviewers():
            ref1 = yield SpawnAgent(env=env, prompt="Review code quality")
            ref2 = yield SpawnAgent(env=env, prompt="Review security")
            # Both agents run in parallel
            yield WaitForStatus(ref1, AgenticSessionStatus.DONE)
            yield WaitForStatus(ref2, AgenticSessionStatus.DONE)
    """

    env: "WorktreeEnv"  # Environment to run agent in
    prompt: str  # Initial prompt for the agent
    agent_type: str = "claude"  # Agent type
    name: str | None = None  # Session name
    profile: str | None = None  # Agent profile/persona


@dataclass(frozen=True, kw_only=True)
class SendMessage(ConductorEffectBase):
    """Send a message to a running agent.

    Sends additional input to an agent that was spawned with SpawnAgent.

    Yields: None

    Example:
        @do
        def guide_agent():
            ref = yield SpawnAgent(env=env, prompt="Start task")
            yield WaitForStatus(ref, AgenticSessionStatus.BLOCKED)
            yield SendMessage(ref, "Continue with step 2")
    """

    agent_ref: "AgentRef"  # Agent to send message to
    message: str  # Message content
    wait: bool = False  # Wait for response to complete


@dataclass(frozen=True, kw_only=True)
class WaitForStatus(ConductorEffectBase):
    """Wait for an agent to reach a specific status.

    Blocks until the agent reaches one of the target statuses.

    Yields: AgenticSessionStatus (final status)

    Example:
        @do
        def wait_for_completion():
            ref = yield SpawnAgent(...)
            status = yield WaitForStatus(
                ref,
                target=(AgenticSessionStatus.DONE, AgenticSessionStatus.ERROR),
                timeout=300,
            )
            return status
    """

    agent_ref: "AgentRef"  # Agent to wait for
    target: "AgenticSessionStatus | tuple[AgenticSessionStatus, ...]"  # Target status(es)
    timeout: float | None = None  # Timeout in seconds
    poll_interval: float = 1.0  # Poll interval


@dataclass(frozen=True, kw_only=True)
class CaptureOutput(ConductorEffectBase):
    """Capture output from an agent session.

    Gets the current or final output from an agent.

    Yields: str (output text)

    Example:
        @do
        def get_review():
            ref = yield SpawnAgent(env=env, prompt="Review the code")
            yield WaitForStatus(ref, AgenticSessionStatus.DONE)
            review = yield CaptureOutput(ref)
            return review
    """

    agent_ref: "AgentRef"  # Agent to capture from
    lines: int = 500  # Number of lines to capture


__all__ = [
    "CaptureOutput",
    "RunAgent",
    "SendMessage",
    "SpawnAgent",
    "WaitForStatus",
]
