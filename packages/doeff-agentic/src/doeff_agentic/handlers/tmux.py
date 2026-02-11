"""
Legacy tmux handler for doeff-agentic.

This module provides a handler that uses tmux for agent session management.
It has limited feature support compared to the OpenCode handler.

Limitations:
- Only SHARED environment type (no worktree, inherited, copy)
- No session forking (AgenticForkSession raises AgenticUnsupportedOperationError)
- Polling-based status detection (no SSE events)
- Use core Gather/Race effects for parallel execution

Usage:
    from doeff import default_handlers, run
    from doeff_agentic.tmux_handler import tmux_handler
    from doeff_agentic.runtime import with_handler_map

    handlers = tmux_handler()
    program = with_handler_map(my_workflow(), handlers)
    result = run(program, handlers=default_handlers())
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from doeff import Resume

from ..effects import (
    AgenticAbortSession,
    AgenticCreateEnvironment,
    AgenticCreateSession,
    AgenticCreateWorkflow,
    AgenticDeleteEnvironment,
    AgenticDeleteSession,
    AgenticForkSession,
    AgenticGetEnvironment,
    AgenticGetMessages,
    AgenticGetSession,
    AgenticGetSessionStatus,
    AgenticGetWorkflow,
    AgenticNextEvent,
    AgenticSendMessage,
    AgenticSupportsCapability,
)
from ..event_log import EventLogWriter, WorkflowIndex
from ..exceptions import (
    AgenticDuplicateNameError,
    AgenticEnvironmentInUseError,
    AgenticEnvironmentNotFoundError,
    AgenticServerError,
    AgenticSessionNotFoundError,
    AgenticSessionNotRunningError,
    AgenticTimeoutError,
    AgenticUnsupportedOperationError,
)
from ..types import (
    AgenticEndOfEvents,
    AgenticEnvironmentHandle,
    AgenticEnvironmentType,
    AgenticEvent,
    AgenticMessage,
    AgenticMessageHandle,
    AgenticSessionHandle,
    AgenticSessionStatus,
    AgenticWorkflowHandle,
    AgenticWorkflowStatus,
)

# =============================================================================
# Tmux utilities (inline to avoid doeff-agents dependency)
# =============================================================================

ANSI_PATTERN = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text."""
    return ANSI_PATTERN.sub("", text)


def is_tmux_available() -> bool:
    """Check if tmux is installed."""
    result = subprocess.run(["tmux", "-V"], check=False, capture_output=True)
    return result.returncode == 0


def has_session(name: str) -> bool:
    """Check if a tmux session exists."""
    result = subprocess.run(
        ["tmux", "has-session", "-t", name], check=False, capture_output=True
    )
    return result.returncode == 0


def new_session(session_name: str, work_dir: Path | None = None) -> str:
    """Create a new detached tmux session. Returns pane_id."""
    args = ["tmux", "new-session", "-d", "-s", session_name, "-P", "-F", "#{pane_id}"]
    if work_dir:
        args.extend(["-c", str(work_dir)])

    result = subprocess.run(args, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def send_keys(
    target: str, keys: str, *, literal: bool = True, enter: bool = True
) -> None:
    """Send keys to a tmux pane."""
    args = ["tmux", "send-keys", "-t", target]
    if literal:
        args.extend(["-l", keys])
    else:
        args.append(keys)
    subprocess.run(args, check=True)

    if enter:
        subprocess.run(["tmux", "send-keys", "-t", target, "Enter"], check=True)


def capture_pane(target: str, lines: int = 100) -> str:
    """Capture the content of a tmux pane."""
    result = subprocess.run(
        ["tmux", "capture-pane", "-t", target, "-p", "-S", f"-{lines}"],
        capture_output=True,
        text=True,
        check=True,
    )
    return strip_ansi(result.stdout)


def kill_session(session: str) -> None:
    """Kill a tmux session."""
    subprocess.run(["tmux", "kill-session", "-t", session], check=False)


def attach_session(session: str) -> None:
    """Attach to a tmux session."""
    if os.environ.get("TMUX"):
        subprocess.run(["tmux", "switch-client", "-t", session], check=True)
    else:
        subprocess.run(["tmux", "attach-session", "-t", session], check=True)


# =============================================================================
# Status Detection
# =============================================================================


def detect_status(output: str) -> AgenticSessionStatus:
    """Detect session status from pane output."""
    lines = output.strip().split("\n")
    last_lines = "\n".join(lines[-10:]) if lines else ""

    # Check for completion patterns
    done_patterns = [
        r"Agent completed",
        r"Task completed",
        r"Session complete",
        r"\[done\]",
        r"Done\.",
        r"Goodbye!",
    ]
    for pattern in done_patterns:
        if re.search(pattern, last_lines, re.IGNORECASE):
            return AgenticSessionStatus.DONE

    # Check for error patterns
    error_patterns = [
        r"Error:",
        r"Exception:",
        r"Failed:",
        r"\[error\]",
        r"fatal:",
    ]
    for pattern in error_patterns:
        if re.search(pattern, last_lines, re.IGNORECASE):
            return AgenticSessionStatus.ERROR

    # Check for blocked/waiting patterns
    blocked_patterns = [
        r"Waiting for",
        r"Press Enter",
        r"\[y/n\]",
        r"\?$",
        r"> $",
        r">>> $",
    ]
    for pattern in blocked_patterns:
        if re.search(pattern, last_lines):
            return AgenticSessionStatus.BLOCKED

    # Default to running
    return AgenticSessionStatus.RUNNING


# =============================================================================
# State Management
# =============================================================================


@dataclass
class TmuxSessionState:
    """State for a tmux-backed session."""

    handle: AgenticSessionHandle
    pane_id: str
    last_output_hash: str = ""
    message_history: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class TmuxWorkflowState:
    """In-memory state for a workflow."""

    id: str
    name: str | None
    status: AgenticWorkflowStatus
    created_at: datetime
    metadata: dict[str, Any] | None = None
    environments: dict[str, AgenticEnvironmentHandle] = field(default_factory=dict)
    sessions: dict[str, TmuxSessionState] = field(default_factory=dict)  # name -> state
    session_by_id: dict[str, str] = field(default_factory=dict)  # session_id -> name


def generate_workflow_id(name: str | None = None) -> str:
    """Generate a 7-char hex workflow ID."""
    data = f"{name or 'workflow'}-{time.time()}"
    return hashlib.sha256(data.encode()).hexdigest()[:7]


def generate_session_id() -> str:
    """Generate a unique session ID."""
    return f"tmux_{hashlib.sha256(str(time.time_ns()).encode()).hexdigest()[:12]}"


def generate_environment_id(name: str | None = None) -> str:
    """Generate environment ID."""
    data = f"env-{name or 'default'}-{time.time()}"
    return hashlib.sha256(data.encode()).hexdigest()[:8]


# =============================================================================
# Tmux Handler
# =============================================================================


class TmuxHandler:
    """Legacy handler using tmux for session management.

    This handler has limited capability compared to OpenCodeHandler:
    - Only SHARED environment type
    - No fork support
    - Polling-based status detection
    """

    SUPPORTED_CAPABILITIES = frozenset({"worktree"})  # worktree via git commands

    def __init__(self, working_dir: str | None = None) -> None:
        """Initialize the tmux handler.

        Args:
            working_dir: Default working directory
        """
        self._working_dir = Path(working_dir) if working_dir else Path.cwd()
        self._workflow: TmuxWorkflowState | None = None

        # Event logging
        self._event_log = EventLogWriter()
        self._workflow_index = WorkflowIndex()

        # Verify tmux is available
        if not is_tmux_available():
            raise AgenticServerError("tmux is not installed or not in PATH")

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    def initialize(self) -> None:
        """Initialize handler."""
        # No-op for tmux

    def close(self) -> None:
        """Clean up resources."""
        # Kill all sessions
        if self._workflow:
            for state in self._workflow.sessions.values():
                try:
                    kill_session(state.handle.name)
                except Exception:
                    pass

    # -------------------------------------------------------------------------
    # Workflow Effects
    # -------------------------------------------------------------------------

    def handle_create_workflow(
        self, effect: AgenticCreateWorkflow
    ) -> AgenticWorkflowHandle:
        """Handle AgenticCreateWorkflow effect."""
        workflow_id = generate_workflow_id(effect.name)
        self._workflow = TmuxWorkflowState(
            id=workflow_id,
            name=effect.name,
            status=AgenticWorkflowStatus.RUNNING,
            created_at=datetime.now(timezone.utc),
            metadata=effect.metadata,
        )

        # Log workflow creation
        self._event_log.log_workflow_created(
            workflow_id, effect.name, effect.metadata
        )
        self._workflow_index.add(workflow_id, effect.name)

        return AgenticWorkflowHandle(
            id=workflow_id,
            name=effect.name,
            status=AgenticWorkflowStatus.RUNNING,
            created_at=self._workflow.created_at,
            metadata=effect.metadata,
        )

    def handle_get_workflow(self, effect: AgenticGetWorkflow) -> AgenticWorkflowHandle:
        """Handle AgenticGetWorkflow effect."""
        self._ensure_workflow()
        assert self._workflow is not None

        return AgenticWorkflowHandle(
            id=self._workflow.id,
            name=self._workflow.name,
            status=self._workflow.status,
            created_at=self._workflow.created_at,
            metadata=self._workflow.metadata,
        )

    # -------------------------------------------------------------------------
    # Environment Effects
    # -------------------------------------------------------------------------

    def handle_create_environment(
        self, effect: AgenticCreateEnvironment
    ) -> AgenticEnvironmentHandle:
        """Handle AgenticCreateEnvironment effect."""
        self._ensure_workflow()
        assert self._workflow is not None

        # Only SHARED and WORKTREE are supported
        if effect.env_type not in (
            AgenticEnvironmentType.SHARED,
            AgenticEnvironmentType.WORKTREE,
        ):
            raise AgenticUnsupportedOperationError(
                f"create_environment({effect.env_type.value})",
                "tmux",
                "Only SHARED and WORKTREE environment types are supported",
            )

        env_id = generate_environment_id(effect.name)

        if effect.env_type == AgenticEnvironmentType.SHARED:
            working_dir = effect.working_dir or str(self._working_dir)
        else:  # WORKTREE
            working_dir = self._create_worktree(env_id, effect.base_commit)

        handle = AgenticEnvironmentHandle(
            id=env_id,
            env_type=effect.env_type,
            name=effect.name,
            working_dir=working_dir,
            created_at=datetime.now(timezone.utc),
            base_commit=effect.base_commit,
            source_environment_id=effect.source_environment_id,
        )

        self._workflow.environments[env_id] = handle

        # Log environment creation
        self._event_log.log_environment_created(self._workflow.id, handle)

        return handle

    def handle_get_environment(
        self, effect: AgenticGetEnvironment
    ) -> AgenticEnvironmentHandle:
        """Handle AgenticGetEnvironment effect."""
        self._ensure_workflow()
        assert self._workflow is not None

        handle = self._workflow.environments.get(effect.environment_id)
        if not handle:
            raise AgenticEnvironmentNotFoundError(effect.environment_id)
        return handle

    def handle_delete_environment(self, effect: AgenticDeleteEnvironment) -> bool:
        """Handle AgenticDeleteEnvironment effect."""
        self._ensure_workflow()
        assert self._workflow is not None

        handle = self._workflow.environments.get(effect.environment_id)
        if not handle:
            raise AgenticEnvironmentNotFoundError(effect.environment_id)

        # Check for sessions using this environment
        using_sessions = [
            s.handle.name
            for s in self._workflow.sessions.values()
            if s.handle.environment_id == effect.environment_id
        ]

        if using_sessions and not effect.force:
            raise AgenticEnvironmentInUseError(effect.environment_id, using_sessions)

        # Clean up worktree if applicable
        if handle.env_type == AgenticEnvironmentType.WORKTREE:
            self._delete_worktree(handle.working_dir)

        del self._workflow.environments[effect.environment_id]
        return True

    # -------------------------------------------------------------------------
    # Session Effects
    # -------------------------------------------------------------------------

    def handle_create_session(
        self, effect: AgenticCreateSession
    ) -> AgenticSessionHandle:
        """Handle AgenticCreateSession effect."""
        self._ensure_workflow()
        assert self._workflow is not None

        # Check for duplicate name
        if effect.name in self._workflow.sessions:
            raise AgenticDuplicateNameError(effect.name, self._workflow.id)

        # Create or get environment
        if effect.environment_id:
            env = self._workflow.environments.get(effect.environment_id)
            if not env:
                raise AgenticEnvironmentNotFoundError(effect.environment_id)
            env_id = effect.environment_id
            work_dir = Path(env.working_dir)
        else:
            # Create implicit shared environment
            env_id = generate_environment_id("shared")
            env = AgenticEnvironmentHandle(
                id=env_id,
                env_type=AgenticEnvironmentType.SHARED,
                name="shared",
                working_dir=str(self._working_dir),
                created_at=datetime.now(timezone.utc),
            )
            self._workflow.environments[env_id] = env
            work_dir = self._working_dir

        # Create tmux session
        session_name = f"doeff-{self._workflow.id}-{effect.name}"
        session_id = generate_session_id()

        try:
            pane_id = new_session(session_name, work_dir)
        except subprocess.CalledProcessError as e:
            raise AgenticServerError(f"Failed to create tmux session: {e}")

        handle = AgenticSessionHandle(
            id=session_id,
            name=effect.name,
            workflow_id=self._workflow.id,
            environment_id=env_id,
            status=AgenticSessionStatus.PENDING,
            created_at=datetime.now(timezone.utc),
            title=effect.title or effect.name,
            agent=effect.agent,
            model=effect.model,
        )

        state = TmuxSessionState(handle=handle, pane_id=pane_id)
        self._workflow.sessions[effect.name] = state
        self._workflow.session_by_id[session_id] = effect.name

        # Log session creation
        self._event_log.log_session_created(self._workflow.id, handle)
        self._event_log.log_session_bound_to_environment(
            self._workflow.id, env_id, effect.name
        )

        return handle

    def handle_fork_session(self, effect: AgenticForkSession) -> AgenticSessionHandle:
        """Handle AgenticForkSession effect - NOT SUPPORTED."""
        raise AgenticUnsupportedOperationError(
            "fork_session",
            "tmux",
            "Session forking is not supported with tmux backend",
        )

    def handle_get_session(self, effect: AgenticGetSession) -> AgenticSessionHandle:
        """Handle AgenticGetSession effect."""
        self._ensure_workflow()
        assert self._workflow is not None

        if effect.name:
            state = self._workflow.sessions.get(effect.name)
            if not state:
                raise AgenticSessionNotFoundError(effect.name, by_name=True)
            return state.handle

        if effect.session_id:
            name = self._workflow.session_by_id.get(effect.session_id)
            if not name:
                raise AgenticSessionNotFoundError(effect.session_id)
            return self._workflow.sessions[name].handle

        raise ValueError("Either session_id or name must be provided")

    def handle_abort_session(self, effect: AgenticAbortSession) -> None:
        """Handle AgenticAbortSession effect."""
        self._ensure_workflow()
        assert self._workflow is not None

        name = self._workflow.session_by_id.get(effect.session_id)
        if not name:
            raise AgenticSessionNotFoundError(effect.session_id)

        state = self._workflow.sessions[name]
        session_name = f"doeff-{self._workflow.id}-{name}"

        # Send Ctrl+C to abort
        try:
            send_keys(state.pane_id, "C-c", literal=False, enter=False)
        except Exception:
            pass

        # Kill the tmux session
        kill_session(session_name)

        # Update status
        state.handle = AgenticSessionHandle(
            id=state.handle.id,
            name=state.handle.name,
            workflow_id=state.handle.workflow_id,
            environment_id=state.handle.environment_id,
            status=AgenticSessionStatus.ABORTED,
            created_at=state.handle.created_at,
            title=state.handle.title,
            agent=state.handle.agent,
            model=state.handle.model,
        )

    def handle_delete_session(self, effect: AgenticDeleteSession) -> bool:
        """Handle AgenticDeleteSession effect."""
        self._ensure_workflow()
        assert self._workflow is not None

        name = self._workflow.session_by_id.get(effect.session_id)
        if not name:
            raise AgenticSessionNotFoundError(effect.session_id)

        session_name = f"doeff-{self._workflow.id}-{name}"
        kill_session(session_name)

        self._workflow.session_by_id.pop(effect.session_id, None)
        self._workflow.sessions.pop(name, None)

        return True

    # -------------------------------------------------------------------------
    # Message Effects
    # -------------------------------------------------------------------------

    def handle_send_message(self, effect: AgenticSendMessage) -> AgenticMessageHandle:
        """Handle AgenticSendMessage effect."""
        self._ensure_workflow()
        assert self._workflow is not None

        name = self._workflow.session_by_id.get(effect.session_id)
        if not name:
            raise AgenticSessionNotFoundError(effect.session_id)

        state = self._workflow.sessions[name]

        # Check if session is running
        if not has_session(f"doeff-{self._workflow.id}-{name}"):
            raise AgenticSessionNotRunningError(
                effect.session_id, state.handle.status.value
            )

        # Log message sent
        self._event_log.log_message_sent(
            self._workflow.id, name, effect.content, effect.wait
        )

        # Send message to tmux
        send_keys(state.pane_id, effect.content, literal=True, enter=True)

        # Record message
        msg_id = f"msg_{time.time_ns()}"
        state.message_history.append(
            {
                "id": msg_id,
                "role": "user",
                "content": effect.content,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )

        # Update status to running
        old_status = state.handle.status
        state.handle = AgenticSessionHandle(
            id=state.handle.id,
            name=state.handle.name,
            workflow_id=state.handle.workflow_id,
            environment_id=state.handle.environment_id,
            status=AgenticSessionStatus.RUNNING,
            created_at=state.handle.created_at,
            title=state.handle.title,
            agent=state.handle.agent,
            model=state.handle.model,
        )

        # Log status change if different
        if old_status != AgenticSessionStatus.RUNNING:
            self._event_log.log_session_status(
                self._workflow.id, name, AgenticSessionStatus.RUNNING
            )

        if effect.wait:
            # Poll until completion or blocked
            self._wait_for_completion(state)
            # Log message complete
            self._event_log.log_message_complete(self._workflow.id, name)

        return AgenticMessageHandle(
            id=msg_id,
            session_id=effect.session_id,
            role="user",
            created_at=datetime.now(timezone.utc),
        )

    def handle_get_messages(self, effect: AgenticGetMessages) -> list[AgenticMessage]:
        """Handle AgenticGetMessages effect."""
        self._ensure_workflow()
        assert self._workflow is not None

        name = self._workflow.session_by_id.get(effect.session_id)
        if not name:
            raise AgenticSessionNotFoundError(effect.session_id)

        state = self._workflow.sessions[name]

        # Get output from tmux
        try:
            output = capture_pane(state.pane_id, 500)
        except Exception:
            output = ""

        # Create a pseudo-message from output
        messages = []
        for msg in state.message_history:
            messages.append(
                AgenticMessage(
                    id=msg["id"],
                    session_id=effect.session_id,
                    role=msg["role"],
                    content=msg["content"],
                    created_at=datetime.fromisoformat(msg["created_at"]),
                )
            )

        # Add current output as assistant message
        if output:
            messages.append(
                AgenticMessage(
                    id=f"output_{time.time_ns()}",
                    session_id=effect.session_id,
                    role="assistant",
                    content=output,
                    created_at=datetime.now(timezone.utc),
                )
            )

        if effect.limit:
            messages = messages[-effect.limit :]

        return messages

    # -------------------------------------------------------------------------
    # Event Effects
    # -------------------------------------------------------------------------

    def handle_next_event(
        self, effect: AgenticNextEvent
    ) -> AgenticEvent | AgenticEndOfEvents:
        """Handle AgenticNextEvent effect - uses polling."""
        self._ensure_workflow()
        assert self._workflow is not None

        name = self._workflow.session_by_id.get(effect.session_id)
        if not name:
            raise AgenticSessionNotFoundError(effect.session_id)

        state = self._workflow.sessions[name]
        session_name = f"doeff-{self._workflow.id}-{name}"

        start = time.time()
        while True:
            if effect.timeout and (time.time() - start) > effect.timeout:
                raise AgenticTimeoutError("next_event", effect.timeout)

            # Check if session still exists
            if not has_session(session_name):
                return AgenticEndOfEvents(
                    reason="session_done",
                    final_status=AgenticSessionStatus.DONE,
                )

            # Capture output and detect status
            try:
                output = capture_pane(state.pane_id, 50)
            except Exception:
                return AgenticEndOfEvents(
                    reason="session_error",
                    final_status=AgenticSessionStatus.ERROR,
                )

            new_status = detect_status(output)

            # Check for status change
            if new_status != state.handle.status:
                old_status = state.handle.status
                state.handle = AgenticSessionHandle(
                    id=state.handle.id,
                    name=state.handle.name,
                    workflow_id=state.handle.workflow_id,
                    environment_id=state.handle.environment_id,
                    status=new_status,
                    created_at=state.handle.created_at,
                    title=state.handle.title,
                    agent=state.handle.agent,
                    model=state.handle.model,
                )

                if new_status.is_terminal():
                    return AgenticEndOfEvents(
                        reason=f"session_{new_status.value}",
                        final_status=new_status,
                    )

                return AgenticEvent(
                    event_type="session.status",
                    session_id=effect.session_id,
                    data={"old": old_status.value, "new": new_status.value},
                    timestamp=datetime.now(timezone.utc),
                )

            # Check for output change
            output_hash = hashlib.sha256(output.encode()).hexdigest()[:16]
            if output_hash != state.last_output_hash:
                state.last_output_hash = output_hash
                return AgenticEvent(
                    event_type="poll.output",
                    session_id=effect.session_id,
                    data={"snippet": output[-500:]},
                    timestamp=datetime.now(timezone.utc),
                )

            # Poll interval
            time.sleep(1.0)

    # -------------------------------------------------------------------------
    # Status Effects
    # -------------------------------------------------------------------------

    def handle_get_session_status(
        self, effect: AgenticGetSessionStatus
    ) -> AgenticSessionStatus:
        """Handle AgenticGetSessionStatus effect."""
        self._ensure_workflow()
        assert self._workflow is not None

        name = self._workflow.session_by_id.get(effect.session_id)
        if not name:
            raise AgenticSessionNotFoundError(effect.session_id)

        state = self._workflow.sessions[name]
        self._refresh_session_status(state)

        return state.handle.status

    def handle_supports_capability(self, effect: AgenticSupportsCapability) -> bool:
        """Handle AgenticSupportsCapability effect."""
        return effect.capability in self.SUPPORTED_CAPABILITIES

    # -------------------------------------------------------------------------
    # Helper Methods
    # -------------------------------------------------------------------------

    def _ensure_workflow(self) -> None:
        """Ensure workflow is created."""
        if self._workflow is None:
            self.handle_create_workflow(AgenticCreateWorkflow())

    def _create_worktree(self, env_id: str, base_commit: str | None) -> str:
        """Create a git worktree."""
        worktree_dir = Path(f"/tmp/doeff/worktrees/{env_id}")
        worktree_dir.parent.mkdir(parents=True, exist_ok=True)

        cmd = ["git", "worktree", "add", str(worktree_dir)]
        if base_commit:
            cmd.append(base_commit)
        else:
            cmd.extend(["--detach", "HEAD"])

        subprocess.run(cmd, cwd=self._working_dir, check=True, capture_output=True)
        return str(worktree_dir)

    def _delete_worktree(self, path: str) -> None:
        """Remove a git worktree."""
        try:
            subprocess.run(
                ["git", "worktree", "remove", "--force", path],
                cwd=self._working_dir,
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError:
            shutil.rmtree(path, ignore_errors=True)

    def _refresh_session_status(self, state: TmuxSessionState) -> None:
        """Refresh session status from tmux."""
        assert self._workflow is not None

        session_name = f"doeff-{self._workflow.id}-{state.handle.name}"
        old_status = state.handle.status

        if not has_session(session_name):
            new_status = AgenticSessionStatus.DONE
            state.handle = AgenticSessionHandle(
                id=state.handle.id,
                name=state.handle.name,
                workflow_id=state.handle.workflow_id,
                environment_id=state.handle.environment_id,
                status=new_status,
                created_at=state.handle.created_at,
                title=state.handle.title,
                agent=state.handle.agent,
                model=state.handle.model,
            )
            # Log status change
            if old_status != new_status:
                self._event_log.log_session_status(
                    self._workflow.id, state.handle.name, new_status
                )
            return

        try:
            output = capture_pane(state.pane_id, 50)
            new_status = detect_status(output)
            state.handle = AgenticSessionHandle(
                id=state.handle.id,
                name=state.handle.name,
                workflow_id=state.handle.workflow_id,
                environment_id=state.handle.environment_id,
                status=new_status,
                created_at=state.handle.created_at,
                title=state.handle.title,
                agent=state.handle.agent,
                model=state.handle.model,
            )
            # Log status change
            if old_status != new_status:
                self._event_log.log_session_status(
                    self._workflow.id, state.handle.name, new_status
                )
        except Exception:
            pass

    def _wait_for_completion(
        self, state: TmuxSessionState, timeout: float = 300.0
    ) -> None:
        """Wait for session to complete or become blocked."""
        assert self._workflow is not None

        session_name = f"doeff-{self._workflow.id}-{state.handle.name}"
        start = time.time()

        while time.time() - start < timeout:
            if not has_session(session_name):
                return

            self._refresh_session_status(state)

            if state.handle.status.is_terminal() or state.handle.status == AgenticSessionStatus.BLOCKED:
                return

            time.sleep(1.0)


# =============================================================================
# Handler Factory
# =============================================================================

def _as_protocol_handler(
    handler_fn: Callable[[Any], Any],
) -> Callable[[Any, Any], Any]:
    """Adapt an effect -> value handler into (effect, k) protocol."""

    def _wrapped(effect: Any, k):
        return (yield Resume(k, handler_fn(effect)))

    return _wrapped


def tmux_handler(working_dir: str | None = None) -> dict[type, Any]:
    """Create CESK-compatible handlers for agentic effects using tmux.

    Args:
        working_dir: Default working directory

    Returns:
        Typed handler map for use with WithHandler composition.

    Usage:
        from doeff import default_handlers, run
        from doeff_agentic import tmux_handler
        from doeff_agentic.runtime import with_handler_map

        handlers = tmux_handler()
        program = with_handler_map(my_workflow(), handlers)
        result = run(program, handlers=default_handlers())
    """
    handler = TmuxHandler(working_dir=working_dir)

    return {
        # Workflow
        AgenticCreateWorkflow: _as_protocol_handler(handler.handle_create_workflow),
        AgenticGetWorkflow: _as_protocol_handler(handler.handle_get_workflow),
        # Environment
        AgenticCreateEnvironment: _as_protocol_handler(handler.handle_create_environment),
        AgenticGetEnvironment: _as_protocol_handler(handler.handle_get_environment),
        AgenticDeleteEnvironment: _as_protocol_handler(handler.handle_delete_environment),
        # Session
        AgenticCreateSession: _as_protocol_handler(handler.handle_create_session),
        AgenticForkSession: _as_protocol_handler(handler.handle_fork_session),
        AgenticGetSession: _as_protocol_handler(handler.handle_get_session),
        AgenticAbortSession: _as_protocol_handler(handler.handle_abort_session),
        AgenticDeleteSession: _as_protocol_handler(handler.handle_delete_session),
        # Message
        AgenticSendMessage: _as_protocol_handler(handler.handle_send_message),
        AgenticGetMessages: _as_protocol_handler(handler.handle_get_messages),
        # Event
        AgenticNextEvent: _as_protocol_handler(handler.handle_next_event),
        # Status
        AgenticGetSessionStatus: _as_protocol_handler(handler.handle_get_session_status),
        AgenticSupportsCapability: _as_protocol_handler(handler.handle_supports_capability),
    }


__all__ = [
    "TmuxHandler",
    "tmux_handler",
]
