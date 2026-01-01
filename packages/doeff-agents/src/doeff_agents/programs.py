"""High-level agent Programs composed from fine-grained effects.

This module provides convenience Programs for common agent workflows.
These are NOT Effects - they are composed Programs using @do that
yield fine-grained effects internally.

Key design:
- Programs are lazy computations, not immediate side effects
- Programs compose fine-grained effects (Launch, Monitor, Sleep, etc.)
- Programs can be run with different handlers (real tmux or mock)
- Programs are testable and inspectable
"""

from __future__ import annotations

from collections.abc import Callable, Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from .adapters.base import AgentType, LaunchConfig
from .effects import (
    Capture,
    Launch,
    Monitor,
    Observation,
    Send,
    SessionHandle,
    Sleep,
    Stop,
)
from .monitor import SessionStatus

T = TypeVar("T")

# Type alias for effect generator (Program-like)
EffectGenerator = Generator[Any, Any, T]


# =============================================================================
# Result Types
# =============================================================================


@dataclass(frozen=True)
class AgentResult:
    """Result from running an agent to completion.

    Contains the final observation, captured output, and any PR URL detected.
    """

    handle: SessionHandle
    final_status: SessionStatus
    output: str
    pr_url: str | None = None
    iterations: int = 0

    @property
    def succeeded(self) -> bool:
        """Check if agent completed successfully."""
        return self.final_status == SessionStatus.DONE

    @property
    def failed(self) -> bool:
        """Check if agent failed."""
        return self.final_status == SessionStatus.FAILED


@dataclass(frozen=True)
class MonitorResult:
    """Result from a monitoring iteration."""

    observation: Observation
    should_continue: bool


# =============================================================================
# Low-level Program Helpers
# =============================================================================


def monitor_once(handle: SessionHandle) -> EffectGenerator[Observation]:
    """Monitor session once and return observation.

    This is a simple wrapper that yields the Monitor effect.
    """
    observation: Observation = yield Monitor(handle)
    return observation


def wait_and_monitor(
    handle: SessionHandle,
    poll_interval: float = 1.0,
) -> EffectGenerator[Observation]:
    """Sleep then monitor session.

    Useful for polling loops.
    """
    yield Sleep(poll_interval)
    observation: Observation = yield Monitor(handle)
    return observation


def capture_and_send(
    handle: SessionHandle,
    message: str,
    *,
    capture_lines: int = 100,
) -> EffectGenerator[str]:
    """Capture output, send message, return captured output.

    Common pattern for interactive agents.
    """
    output: str = yield Capture(handle, lines=capture_lines)
    yield Send(handle, message)
    return output


# =============================================================================
# Core Workflow Programs
# =============================================================================


def monitor_until_terminal(
    handle: SessionHandle,
    *,
    poll_interval: float = 1.0,
    max_iterations: int = 0,  # 0 = no limit
    on_observation: Callable[[Observation], None] | None = None,
) -> EffectGenerator[tuple[Observation, int]]:
    """Monitor session until it reaches a terminal state.

    Args:
        handle: Session to monitor
        poll_interval: Seconds between polls
        max_iterations: Maximum iterations (0 = unlimited)
        on_observation: Optional callback for each observation

    Yields: Fine-grained effects (Monitor, Sleep)
    Returns: (final_observation, iteration_count)
    """
    iteration = 0

    while True:
        observation: Observation = yield Monitor(handle)

        if on_observation:
            on_observation(observation)

        if observation.is_terminal:
            return (observation, iteration)

        iteration += 1
        if max_iterations > 0 and iteration >= max_iterations:
            return (observation, iteration)

        yield Sleep(poll_interval)


def run_agent_to_completion(
    session_name: str,
    config: LaunchConfig,
    *,
    poll_interval: float = 1.0,
    timeout_iterations: int = 0,  # 0 = no limit
    capture_lines: int = 100,
    ready_timeout: float = 30.0,
    on_observation: Callable[[Observation], None] | None = None,
) -> EffectGenerator[AgentResult]:
    """Launch agent and run to completion, then stop.

    This is the main high-level workflow Program. It:
    1. Launches the agent session
    2. Monitors until terminal state
    3. Captures final output
    4. Stops the session

    This is a PROGRAM (composed of effects), NOT an Effect itself.
    The difference is crucial for testability and composability.

    Args:
        session_name: Name for tmux session
        config: Agent launch configuration
        poll_interval: Seconds between status checks
        timeout_iterations: Max monitoring iterations (0 = no limit)
        capture_lines: Lines to capture in final output
        ready_timeout: Timeout for agent to be ready
        on_observation: Callback for each observation

    Yields: Fine-grained effects (Launch, Monitor, Sleep, Capture, Stop)
    Returns: AgentResult with status and output
    """
    # Launch
    handle: SessionHandle = yield Launch(
        session_name,
        config,
        ready_timeout=ready_timeout,
    )

    pr_url: str | None = None

    try:
        # Monitor until terminal
        iteration = 0
        final_observation: Observation | None = None

        while True:
            observation: Observation = yield Monitor(handle)

            if on_observation:
                on_observation(observation)

            # Track PR URL
            if observation.pr_url:
                pr_url = observation.pr_url

            if observation.is_terminal:
                final_observation = observation
                break

            iteration += 1
            if timeout_iterations > 0 and iteration >= timeout_iterations:
                final_observation = observation
                break

            yield Sleep(poll_interval)

        # Capture final output
        output: str = yield Capture(handle, lines=capture_lines)

        return AgentResult(
            handle=handle,
            final_status=final_observation.status if final_observation else SessionStatus.EXITED,
            output=output,
            pr_url=pr_url,
            iterations=iteration,
        )

    finally:
        # Always stop
        yield Stop(handle)


def with_session(
    session_name: str,
    config: LaunchConfig,
    use: Callable[[SessionHandle], EffectGenerator[T]],
    *,
    ready_timeout: float = 30.0,
) -> EffectGenerator[T]:
    """Bracket pattern: launch, use, then stop.

    Ensures session is stopped even if `use` raises.
    This is the effect-based equivalent of a context manager.

    Args:
        session_name: Name for tmux session
        config: Agent launch configuration
        use: Function that takes handle and yields effects
        ready_timeout: Timeout for agent to be ready

    Yields: Fine-grained effects
    Returns: Result from use function
    """
    handle: SessionHandle = yield Launch(
        session_name,
        config,
        ready_timeout=ready_timeout,
    )

    try:
        # Run the use function
        use_gen = use(handle)

        # Drive the inner generator
        try:
            current = next(use_gen)
        except StopIteration as stop_exc:
            return stop_exc.value

        while True:
            try:
                sent_value = yield current
            except GeneratorExit:
                use_gen.close()
                raise
            except BaseException as e:
                try:
                    current = use_gen.throw(e)
                except StopIteration as stop_exc:
                    return stop_exc.value
                continue
            try:
                current = use_gen.send(sent_value)
            except StopIteration as stop_exc:
                return stop_exc.value

    finally:
        yield Stop(handle)


# =============================================================================
# Convenience Factories
# =============================================================================


def quick_agent(
    prompt: str,
    *,
    agent_type: AgentType = AgentType.CLAUDE,
    work_dir: Path | None = None,
    session_prefix: str = "quick-agent",
    poll_interval: float = 1.0,
) -> EffectGenerator[AgentResult]:
    """Quick one-shot agent run with minimal configuration.

    Args:
        prompt: The prompt to send to the agent
        agent_type: Which agent to use (default: Claude)
        work_dir: Working directory (default: cwd)
        session_prefix: Prefix for session name
        poll_interval: Seconds between status checks

    Returns: AgentResult
    """
    import time

    work = work_dir or Path.cwd()
    session_name = f"{session_prefix}-{int(time.time())}"

    config = LaunchConfig(
        agent_type=agent_type,
        work_dir=work,
        prompt=prompt,
    )

    result: AgentResult = yield from run_agent_to_completion(
        session_name,
        config,
        poll_interval=poll_interval,
    )
    return result


# =============================================================================
# Interactive Patterns
# =============================================================================


def interactive_session(
    session_name: str,
    config: LaunchConfig,
    messages: list[str],
    *,
    wait_for_blocked: bool = True,
    poll_interval: float = 1.0,
    ready_timeout: float = 30.0,
) -> EffectGenerator[AgentResult]:
    """Run interactive session with multiple messages.

    Sends each message when agent is blocked (waiting for input).

    Args:
        session_name: Name for tmux session
        config: Agent launch configuration
        messages: List of messages to send in order
        wait_for_blocked: Wait for BLOCKED status before each message
        poll_interval: Seconds between polls
        ready_timeout: Timeout for agent to be ready

    Returns: AgentResult
    """
    handle: SessionHandle = yield Launch(
        session_name,
        config,
        ready_timeout=ready_timeout,
    )

    pr_url: str | None = None
    message_index = 0
    iteration = 0

    try:
        while True:
            observation: Observation = yield Monitor(handle)

            if observation.pr_url:
                pr_url = observation.pr_url

            if observation.is_terminal:
                break

            # Send next message when blocked
            if (
                observation.status == SessionStatus.BLOCKED
                and message_index < len(messages)
            ):
                yield Send(handle, messages[message_index])
                message_index += 1

            iteration += 1
            yield Sleep(poll_interval)

        output: str = yield Capture(handle, lines=100)

        return AgentResult(
            handle=handle,
            final_status=observation.status,
            output=output,
            pr_url=pr_url,
            iterations=iteration,
        )

    finally:
        yield Stop(handle)


__all__ = [  # noqa: RUF022 - grouped by category for readability
    # Result types
    "AgentResult",
    "MonitorResult",
    # Low-level helpers
    "monitor_once",
    "wait_and_monitor",
    "capture_and_send",
    # Core workflows
    "monitor_until_terminal",
    "run_agent_to_completion",
    "with_session",
    # Convenience
    "quick_agent",
    "interactive_session",
]
