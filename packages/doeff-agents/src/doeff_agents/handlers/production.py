"""Production effect handler backed by tmux."""


import json
import os
import re
import shlex
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from doeff_agents import tmux
from doeff_agents.adapters.base import (
    AgentAdapter,
    AgentSessionLifecycle,
    AgentType,
    InjectionMethod,
    LaunchParams,
)
from doeff_agents.adapters.claude import ClaudeAdapter
from doeff_agents.adapters.codex import CodexAdapter, trust_workspace_in_codex_home
from doeff_agents.adapters.gemini import GeminiAdapter
from doeff_agents.claude_home import prepare_claude_home
from doeff_agents.effects import (
    AgentAttemptExhaustedError,
    AgentEffect,
    AgentLaunchError,
    AgentNotAvailableError,
    AgentReadyTimeoutError,
    AgentSessionSnapshot,
    AgentSpec,
    AgentTask,
    AgentValidationErrorKind,
    AgentValidationFailure,
    AttachAgentSessionEffect,
    AwaitOutcome,
    AwaitResultEffect,
    AwaitStatus,
    CancelAgentSessionEffect,
    CaptureEffect,
    ClaudeLaunchEffect,
    CleanupAgentSessionEffect,
    FollowUpEffect,
    GetAgentSessionEffect,
    L2SessionHandle,
    LaunchEffect,
    LaunchSessionEffect,
    LaunchTaskEffect,
    ListAgentSessionsEffect,
    MonitorEffect,
    Observation,
    ObserveAgentSessionEffect,
    ReleaseSessionEffect,
    SendEffect,
    SessionAlreadyExistsError,
    SessionHandle,
    SessionNotFoundError,
    StopEffect,
    StopSessionEffect,
)
from doeff_agents.mcp_server import McpToolServer, RunToolFn
from doeff_agents.monitor import (
    MonitorState,
    SessionStatus,
    detect_status,
    hash_content,
    is_waiting_for_input,
)
from doeff_agents.result_validation import validate_result_payload
from doeff_agents.runtime import ClaudeRuntimePolicy
from doeff_agents.session import _dismiss_onboarding_dialogs
from doeff_agents.session_backend import SessionBackend
from doeff_agents.session_store import AgentSessionRepository, InMemoryAgentSessionRepository
from doeff_agents.shell import wrap_with_shell_exports


class AgentHandler(ABC):
    """Abstract handler for agent effects."""

    def handle_agent(self, effect: AgentEffect) -> object:
        """Handle the schema-validated ``agent`` effect."""
        return _run_agent_task(self, effect.task)

    def handle_launch_session(self, effect: LaunchSessionEffect) -> L2SessionHandle:
        """Handle L2 Launch."""
        raise NotImplementedError

    def handle_await_result(self, effect: AwaitResultEffect) -> AwaitOutcome:
        """Handle L2 AwaitResult."""
        raise NotImplementedError

    def handle_follow_up(self, effect: FollowUpEffect) -> L2SessionHandle:
        """Handle L2 FollowUp."""
        raise NotImplementedError

    def handle_stop_session(self, effect: StopSessionEffect) -> None:
        """Handle L2 Stop."""
        raise NotImplementedError

    def handle_release_session(self, effect: ReleaseSessionEffect) -> None:
        """Handle L2 Release."""
        raise NotImplementedError

    @abstractmethod
    def handle_launch(
        self,
        effect: LaunchEffect,
        run_tool: RunToolFn | None = None,
    ) -> SessionHandle:
        """Handle Launch effect."""

    @abstractmethod
    def handle_launch_task(self, effect: LaunchTaskEffect) -> SessionHandle:
        """Handle generic task launch effect."""

    @abstractmethod
    def handle_claude_launch(self, effect: ClaudeLaunchEffect) -> SessionHandle:
        """Handle Claude-specific launch effect."""

    @abstractmethod
    def handle_monitor(self, effect: MonitorEffect) -> Observation:
        """Handle Monitor effect."""

    @abstractmethod
    def handle_capture(self, effect: CaptureEffect) -> str:
        """Handle Capture effect."""

    @abstractmethod
    def handle_send(self, effect: SendEffect) -> None:
        """Handle Send effect."""

    @abstractmethod
    def handle_stop(self, effect: StopEffect) -> None:
        """Handle Stop effect."""

    @abstractmethod
    def handle_get_session(
        self,
        effect: GetAgentSessionEffect,
    ) -> AgentSessionSnapshot | None:
        """Handle GetAgentSession effect."""

    @abstractmethod
    def handle_list_sessions(
        self,
        effect: ListAgentSessionsEffect,
    ) -> tuple[AgentSessionSnapshot, ...]:
        """Handle ListAgentSessions effect."""

    @abstractmethod
    def handle_observe_session(
        self,
        effect: ObserveAgentSessionEffect,
    ) -> AgentSessionSnapshot:
        """Handle ObserveAgentSession effect."""

    @abstractmethod
    def handle_attach_session(self, effect: AttachAgentSessionEffect) -> None:
        """Handle AttachAgentSession effect."""

    @abstractmethod
    def handle_cancel_session(self, effect: CancelAgentSessionEffect) -> AgentSessionSnapshot:
        """Handle CancelAgentSession effect."""

    @abstractmethod
    def handle_cleanup_session(
        self,
        effect: CleanupAgentSessionEffect,
    ) -> AgentSessionSnapshot:
        """Handle CleanupAgentSession effect."""

@dataclass
class SessionState:
    """Mutable state for a session (internal to handler)."""

    handle: SessionHandle
    adapter: AgentAdapter
    pane_id: str
    agent_type: AgentType
    work_dir: Path
    lifecycle: AgentSessionLifecycle
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    result_schema: dict[str, object] | None = None
    monitor_state: MonitorState = field(default_factory=MonitorState)
    status: SessionStatus = SessionStatus.BOOTING


_adapters: dict[AgentType, type[AgentAdapter]] = {
    AgentType.CLAUDE: ClaudeAdapter,  # type: ignore[dict-item]
    AgentType.CODEX: CodexAdapter,  # type: ignore[dict-item]
    AgentType.GEMINI: GeminiAdapter,  # type: ignore[dict-item]
}


def register_adapter(agent_type: AgentType, adapter_class: type[AgentAdapter]) -> None:
    """Register a custom adapter."""
    _adapters[agent_type] = adapter_class


def get_adapter(agent_type: AgentType) -> AgentAdapter:
    """Get the adapter for an agent type."""
    adapter_class = _adapters.get(agent_type)
    if adapter_class is None:
        raise ValueError(f"No adapter registered for: {agent_type}")
    return adapter_class()


def _run_agent_task(handler: AgentHandler, task: AgentTask) -> object:
    """Run Launch → AwaitResult → validate/retry → Release for ``agent``."""
    handle = handler.handle_launch_session(LaunchSessionEffect(spec=task))
    attempts = 0
    last_error: AgentValidationFailure | None = None
    try:
        while attempts <= task.max_retries:
            outcome = handler.handle_await_result(
                AwaitResultEffect(handle=handle, timeout_seconds=task.timeout_seconds)
            )
            last_error = _validation_failure_from_outcome(outcome, task.result_schema)
            if last_error is None:
                return outcome.result

            if attempts >= task.max_retries:
                raise AgentAttemptExhaustedError(
                    session_id=handle.session_id,
                    attempts=attempts + 1,
                    last_error=last_error,
                )

            handle = handler.handle_follow_up(
                FollowUpEffect(handle=handle, message=_retry_message(last_error))
            )
            attempts += 1
    finally:
        handler.handle_release_session(ReleaseSessionEffect(handle=handle))

    raise AssertionError("unreachable agent retry state")


def _validation_failure_from_outcome(
    outcome: AwaitOutcome,
    schema: dict[str, object],
) -> AgentValidationFailure | None:
    if outcome.status == AwaitStatus.TIMED_OUT:
        return AgentValidationFailure(
            kind=AgentValidationErrorKind.TIMED_OUT,
            message=outcome.validation_error or "timed out awaiting result artifact",
        )
    if outcome.status == AwaitStatus.AWAITING_INPUT:
        return AgentValidationFailure(
            kind=AgentValidationErrorKind.AWAITING_INPUT,
            message=outcome.validation_error or "agent is awaiting input",
        )
    if outcome.result is None:
        if outcome.validation_error:
            return AgentValidationFailure(
                kind=AgentValidationErrorKind.INVALID,
                message=outcome.validation_error,
            )
        return AgentValidationFailure(
            kind=AgentValidationErrorKind.ABSENT,
            message="result artifact is absent",
        )

    if outcome.validation_error:
        return AgentValidationFailure(
            kind=AgentValidationErrorKind.INVALID,
            message=outcome.validation_error,
        )

    validation_error = validate_result_payload(outcome.result, schema)
    if validation_error is not None:
        return AgentValidationFailure(
            kind=AgentValidationErrorKind.INVALID,
            message=validation_error,
        )
    return None


def _retry_message(error: AgentValidationFailure) -> str:
    if error.kind == AgentValidationErrorKind.ABSENT:
        return "No result artifact was produced. Return the required result artifact as JSON."
    if error.kind == AgentValidationErrorKind.INVALID:
        return (
            f"The result artifact was invalid: {error.message}. "
            "Return a corrected result artifact that satisfies the schema."
        )
    return f"Cannot continue automatically: {error.message}"


def _launch_effect_from_spec(spec: AgentSpec) -> LaunchEffect:
    return LaunchEffect(
        session_name=spec.session_id,
        agent_type=spec.agent_type,
        work_dir=spec.work_dir,
        prompt=spec.prompt,
        model=spec.model,
        effort=spec.effort,
        bare=spec.bare,
        lifecycle=spec.lifecycle,
        session_env=spec.session_env,
    )


class TmuxAgentHandler(AgentHandler):
    """Handler that executes effects using real tmux sessions."""

    def __init__(
        self,
        *,
        backend: SessionBackend,
        session_repository: AgentSessionRepository | None = None,
        claude_runtime_policy: ClaudeRuntimePolicy | None = None,
    ) -> None:
        self._sessions: dict[str, SessionState] = {}
        self._backend = backend
        self._session_repository = (
            session_repository or InMemoryAgentSessionRepository()
        )
        self._claude_runtime_policy = claude_runtime_policy or ClaudeRuntimePolicy()
        self._mcp_servers: dict[str, McpToolServer] = {}

    def handle_launch(
        self,
        effect: LaunchEffect,
        run_tool: RunToolFn | None = None,
    ) -> SessionHandle:
        """Launch a new agent session in tmux.

        If ``effect.mcp_tools`` is non-empty and ``run_tool`` is provided,
        an SSE MCP server is started and ``.mcp.json`` is written to the work_dir
        before launching the agent.
        """
        adapter = get_adapter(effect.agent_type)

        if not adapter.is_available():
            raise AgentNotAvailableError(f"{effect.agent_type.value} CLI is not available")

        if self._backend.has_session(effect.session_name):
            raise SessionAlreadyExistsError(f"Session {effect.session_name} already exists")

        # Isolate Claude Code state per launch so the agent's `.claude.json`
        # is not shared with any concurrently-running Claude Code instance
        # on the host (e.g. the user's editor session). Without this, two
        # Claude processes race on `~/.claude/.claude.json` writes and the
        # workspace-trust entry we plant gets clobbered, causing the agent
        # to hang at "Yes, I trust this folder" forever.
        agent_env_exports: dict[str, str] = dict(effect.session_env or {})
        if effect.agent_type == AgentType.CLAUDE:
            agent_home = self._claude_runtime_policy.agent_home
            if agent_home is None:
                # Default to a per-launch isolated home under the workdir so
                # state cannot leak between concurrent agent runs or with
                # the user's interactive Claude Code session.
                agent_home = effect.work_dir / ".agent-home"
            trusted_workspaces = self._claude_runtime_policy.trusted_workspaces or (
                effect.work_dir,
            )
            self._prepare_claude_home(agent_home, trusted_workspaces)
            agent_env_exports.update({
                "HOME": str(agent_home),
                "CLAUDE_HOME": str(agent_home / ".claude"),
                **(
                    {"CLAUDE_CODE_OAUTH_TOKEN": os.environ["CLAUDE_CODE_OAUTH_TOKEN"]}
                    if "CLAUDE_CODE_OAUTH_TOKEN" in os.environ
                    else {}
                ),
                **self._claude_runtime_policy.bootstrap_exports,
            })
        elif effect.agent_type == AgentType.CODEX:
            codex_home = agent_env_exports.get(
                "CODEX_HOME",
                os.environ.get("CODEX_HOME", str(Path.home() / ".codex")),
            )
            trust_workspace_in_codex_home(codex_home, effect.work_dir)

        mcp_servers: dict[str, str] = {}

        # Start MCP server if tools are provided.
        #
        # .mcp.json is still written for clients such as Claude Code, but
        # Codex CLI does not auto-load that file from the workdir. For Codex
        # the adapter also receives the active server URL and injects it as a
        # `-c mcp_servers.<name>.url=...` override in the launch command.
        if effect.mcp_tools and run_tool is not None:
            server = self._start_mcp_server(effect, run_tool)
            mcp_servers[effect.mcp_server_name] = server.url

        # Disable oh-my-zsh's auto-update prompt. Without isolated HOME the
        # user's `.zshrc` would suppress this, but with `HOME=<work_dir>/
        # .agent-home` the agent's shell starts without that config and omz
        # blocks on its `[Y/n]` prompt — eating the first character of the
        # launch command we send next.
        session_env: dict[str, str] = dict(effect.session_env or {})
        if effect.agent_type == AgentType.CLAUDE:
            session_env["DISABLE_AUTO_UPDATE"] = "true"
            session_env["DISABLE_UPDATE_PROMPT"] = "true"

        tmux_config = tmux.SessionConfig(
            session_name=effect.session_name,
            work_dir=effect.work_dir,
            env=session_env or None,
        )
        session_info = self._backend.new_session(tmux_config)

        argv = adapter.launch_command(
            LaunchParams(
                work_dir=effect.work_dir,
                prompt=effect.prompt,
                model=effect.model,
                effort=effect.effort,
                bare=effect.bare,
                mcp_servers=mcp_servers or None,
            )
        )
        command = shlex.join(argv)
        command = self._wrap_with_shell_exports(command, agent_env_exports)

        if adapter.injection_method == InjectionMethod.ARG:
            self._backend.send_keys(session_info.pane_id, command, literal=False)
        else:
            self._backend.send_keys(session_info.pane_id, command, literal=False)
            if adapter.ready_pattern and not self._wait_for_ready(
                session_info.pane_id, adapter.ready_pattern, effect.ready_timeout
            ):
                self._stop_mcp_server(effect.session_name)
                self._backend.kill_session(effect.session_name)
                raise AgentReadyTimeoutError(
                    f"Agent did not become ready within {effect.ready_timeout}s"
                )
            if effect.prompt:
                self._backend.send_keys(session_info.pane_id, effect.prompt)

        # Dismiss onboarding dialogs (trust, theme, auth) if adapter supports them
        onboarding_patterns = getattr(adapter, "onboarding_patterns", None)
        if onboarding_patterns:
            _dismiss_onboarding_dialogs(
                session_info.pane_id,
                onboarding_patterns,
                timeout=effect.ready_timeout,
                backend=self._backend,
            )

        handle = SessionHandle(
            session_id=effect.session_name,
        )

        self._sessions[effect.session_name] = SessionState(
            handle=handle,
            adapter=adapter,
            pane_id=session_info.pane_id,
            agent_type=effect.agent_type,
            work_dir=effect.work_dir,
            lifecycle=effect.lifecycle,
        )
        self._record_snapshot("session_started", handle, SessionStatus.BOOTING)
        return handle

    def handle_launch_task(self, effect: LaunchTaskEffect) -> SessionHandle:
        """Lower a generic task launch using runtime policy."""
        raise AgentLaunchError("LaunchTaskEffect is deprecated; use LaunchEffect directly")

    def handle_claude_launch(self, effect: ClaudeLaunchEffect) -> SessionHandle:
        """Launch a Claude-specific task with dedicated home/bootstrap handling."""
        adapter = get_adapter(AgentType.CLAUDE)
        if not adapter.is_available():
            raise AgentNotAvailableError("claude CLI is not available")

        if self._backend.has_session(effect.session_name):
            raise SessionAlreadyExistsError(f"Session {effect.session_name} already exists")

        agent_home = self._claude_runtime_policy.agent_home or Path.home()
        trusted_workspaces = self._claude_runtime_policy.trusted_workspaces or (effect.work_dir,)
        self._prepare_claude_home(agent_home, trusted_workspaces)

        tmux_config = tmux.SessionConfig(
            session_name=effect.session_name,
            work_dir=effect.work_dir,
            env=effect.session_env,
        )
        session_info = self._backend.new_session(tmux_config)

        argv = adapter.launch_command(
            LaunchParams(
                work_dir=effect.work_dir,
                prompt=effect.prompt,
                model=effect.model,
                effort=effect.effort,
                bare=effect.bare,
            )
        )
        command = self._wrap_with_shell_exports(
            shlex.join(argv),
            {
                **(effect.session_env or {}),
                "HOME": str(agent_home),
                "CLAUDE_HOME": str(agent_home / ".claude"),
                **(
                    {"CLAUDE_CODE_OAUTH_TOKEN": os.environ["CLAUDE_CODE_OAUTH_TOKEN"]}
                    if "CLAUDE_CODE_OAUTH_TOKEN" in os.environ
                    else {}
                ),
                **self._claude_runtime_policy.bootstrap_exports,
            },
        )
        self._backend.send_keys(session_info.pane_id, command, literal=False)

        onboarding_patterns = getattr(adapter, "onboarding_patterns", None)
        if onboarding_patterns:
            _dismiss_onboarding_dialogs(
                session_info.pane_id,
                onboarding_patterns,
                timeout=effect.ready_timeout,
                backend=self._backend,
            )

        handle = SessionHandle(
            session_id=effect.session_name,
        )
        self._sessions[effect.session_name] = SessionState(
            handle=handle,
            adapter=adapter,
            pane_id=session_info.pane_id,
            agent_type=AgentType.CLAUDE,
            work_dir=effect.work_dir,
            lifecycle=effect.lifecycle,
        )
        self._record_snapshot("session_started", handle, SessionStatus.BOOTING)
        return handle

    def handle_monitor(self, effect: MonitorEffect) -> Observation:
        """Check session status and return observation."""
        handle = effect.handle
        state = self._state_for_handle(handle)

        if state is None:
            if not self._backend.has_session(handle.session_id):
                self._record_snapshot("session_exited", handle, SessionStatus.EXITED)
                return Observation(status=SessionStatus.EXITED)
            raise SessionNotFoundError(f"Session {handle.session_id} is not registered")

        if not self._backend.has_session(handle.session_id):
            state.status = SessionStatus.EXITED
            self._record_snapshot("session_exited", handle, SessionStatus.EXITED)
            return Observation(status=SessionStatus.EXITED)

        output = self._backend.capture_pane(state.pane_id)

        skip_lines = 5
        if hasattr(state.adapter, "status_bar_lines"):
            skip_lines = state.adapter.status_bar_lines

        content_hash = hash_content(output, skip_lines)
        output_changed = content_hash != state.monitor_state.output_hash
        has_prompt = is_waiting_for_input(output)

        if output_changed:
            state.monitor_state.output_hash = content_hash
            state.monitor_state.last_output = output
            state.monitor_state.last_output_at = datetime.now(timezone.utc)

        new_status = detect_status(output, state.monitor_state, output_changed, has_prompt)
        if new_status:
            state.status = new_status

        observation = Observation(
            status=state.status,
            output_changed=output_changed,
            output_snippet=output[-500:] if output else None,
        )
        self._record_snapshot(
            "session_observed",
            handle,
            state.status,
            output_snippet=observation.output_snippet,
        )
        return observation

    def handle_capture(self, effect: CaptureEffect) -> str:
        """Capture pane output."""
        handle = effect.handle
        state = self._state_for_handle(handle)
        if state is None:
            raise SessionNotFoundError(f"Session {handle.session_id} is not registered")
        if not self._backend.has_session(handle.session_id):
            raise SessionNotFoundError(f"Session {handle.session_id} does not exist")
        output = self._backend.capture_pane(state.pane_id, effect.lines)
        self._record_snapshot(
            "session_captured",
            handle,
            state.status,
            output_snippet=output[-500:] if output else None,
        )
        return output

    def handle_send(self, effect: SendEffect) -> None:
        """Send message to session."""
        handle = effect.handle
        state = self._state_for_handle(handle)
        if state is None:
            raise SessionNotFoundError(f"Session {handle.session_id} is not registered")
        if not self._backend.has_session(handle.session_id):
            raise SessionNotFoundError(f"Session {handle.session_id} does not exist")
        self._backend.send_keys(
            state.pane_id,
            effect.message,
            literal=effect.literal,
            enter=effect.enter,
        )

    def handle_stop(self, effect: StopEffect) -> None:
        """Stop session and its MCP server (if any)."""
        handle = effect.handle
        self._stop_mcp_server(handle.session_id)
        if self._backend.has_session(handle.session_id):
            self._backend.kill_session(handle.session_id)
        state = self._sessions.get(handle.session_id)
        if state:
            state.status = SessionStatus.STOPPED
        self._record_snapshot("session_stopped", handle, SessionStatus.STOPPED)

    def handle_get_session(
        self,
        effect: GetAgentSessionEffect,
    ) -> AgentSessionSnapshot | None:
        """Return persisted session state without touching the backend."""
        return self._session_repository.get_session(effect.session_id)

    def handle_list_sessions(
        self,
        effect: ListAgentSessionsEffect,
    ) -> tuple[AgentSessionSnapshot, ...]:
        """Return persisted sessions matching the query."""
        return self._session_repository.list_sessions(effect.query)

    def handle_observe_session(
        self,
        effect: ObserveAgentSessionEffect,
    ) -> AgentSessionSnapshot:
        """Observe a session by persisted id and return updated state."""
        snapshot = self._require_snapshot(effect.session_id)
        handle = snapshot.to_handle()
        observation = self.handle_monitor(MonitorEffect(handle=handle))
        updated = self._session_repository.get_session(effect.session_id)
        if updated is None:
            return self._snapshot_from_observation(handle, observation)
        return updated

    def handle_attach_session(self, effect: AttachAgentSessionEffect) -> None:
        """Attach to a session by persisted id."""
        snapshot = self._require_snapshot(effect.session_id)
        if not self._backend.has_session(snapshot.session_name):
            raise SessionNotFoundError(f"Session {effect.session_id} does not exist")
        self._backend.attach_session(snapshot.session_name)

    def handle_cancel_session(
        self,
        effect: CancelAgentSessionEffect,
    ) -> AgentSessionSnapshot:
        """Cancel a session by persisted id."""
        snapshot = self._require_snapshot(effect.session_id)
        handle = snapshot.to_handle()
        self.handle_stop(StopEffect(handle=handle))
        updated = self._session_repository.get_session(effect.session_id)
        if updated is None:
            return self._record_snapshot("session_cancelled", handle, SessionStatus.STOPPED)
        return updated

    def handle_cleanup_session(
        self,
        effect: CleanupAgentSessionEffect,
    ) -> AgentSessionSnapshot:
        """Clean up a session by persisted id."""
        snapshot = self._require_snapshot(effect.session_id)
        handle = snapshot.to_handle()
        self._stop_mcp_server(handle.session_id)
        if self._backend.has_session(handle.session_id):
            self._backend.kill_session(handle.session_id)
        now = datetime.now(timezone.utc)
        cleaned = snapshot.with_update(
            status=SessionStatus.STOPPED,
            cleaned_at=now,
            last_observed_at=now,
        )
        self._sessions.pop(handle.session_id, None)
        return self._session_repository.record_snapshot(
            "session_cleaned",
            cleaned,
        )

    def handle_launch_session(self, effect: LaunchSessionEffect) -> L2SessionHandle:
        """Idempotently launch or re-adopt an L2 session."""
        session_id = effect.spec.session_id
        if session_id in self._sessions or self._session_repository.get_session(session_id):
            return L2SessionHandle(session_id=session_id)
        self.handle_launch(_launch_effect_from_spec(effect.spec))
        state = self._sessions[session_id]
        state.result_schema = effect.spec.result_schema
        self._record_snapshot("session_l2_launched", state.handle, state.status)
        return L2SessionHandle(session_id=session_id)

    def handle_await_result(self, effect: AwaitResultEffect) -> AwaitOutcome:
        """Await a result file or an awaiting-input/timeout state."""
        # Same default contract as agentd_client.DEFAULT_AWAIT_BUDGET_SECONDS:
        # one real agent turn routinely outlives 600s, and a long await is
        # free in the failure case (terminal states resolve it early).
        timeout_seconds = effect.timeout_seconds if effect.timeout_seconds is not None else 3600.0
        deadline = time.monotonic() + timeout_seconds
        while True:
            state = self._state_for_handle(effect.handle)
            if state is None:
                raise SessionNotFoundError(f"Session {effect.handle.session_id} is not registered")

            if not self._backend.has_session(effect.handle.session_id):
                return self._await_outcome_from_result_file(state)

            observation = self.handle_monitor(MonitorEffect(handle=effect.handle))
            if observation.status in (SessionStatus.BLOCKED, SessionStatus.BLOCKED_API):
                return AwaitOutcome(
                    status=AwaitStatus.AWAITING_INPUT,
                    validation_error=observation.output_snippet or "agent is awaiting input",
                )
            if observation.is_terminal:
                return self._await_outcome_from_result_file(state)
            if time.monotonic() >= deadline:
                return AwaitOutcome(status=AwaitStatus.TIMED_OUT)
            time.sleep(0.2)

    def handle_follow_up(self, effect: FollowUpEffect) -> L2SessionHandle:
        """Send validation feedback into the live session."""
        self.handle_send(
            SendEffect(
                handle=effect.handle,
                message=effect.message,
                enter=True,
                literal=True,
            )
        )
        return effect.handle

    def handle_stop_session(self, effect: StopSessionEffect) -> None:
        """Stop an L2 session."""
        self.handle_stop(StopEffect(handle=effect.handle))

    def handle_release_session(self, effect: ReleaseSessionEffect) -> None:
        """Release handler-private state for an L2 session."""
        self._sessions.pop(effect.handle.session_id, None)

    def _require_snapshot(self, session_id: str) -> AgentSessionSnapshot:
        snapshot = self._session_repository.get_session(session_id)
        if snapshot is None:
            raise SessionNotFoundError(f"Session {session_id} is not registered")
        return snapshot

    def _await_outcome_from_result_file(self, state: SessionState) -> AwaitOutcome:
        result_path = state.work_dir / ".agentd-result.json"
        if not result_path.exists():
            return AwaitOutcome(status=AwaitStatus.EXITED)
        try:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return AwaitOutcome(status=AwaitStatus.EXITED, validation_error=str(exc))
        snapshot = self._session_repository.get_session(state.handle.session_id)
        schema = None
        if snapshot is not None:
            schema = snapshot.backend_ref.get("result_schema_json")
        if schema:
            validation_error = validate_result_payload(payload, json.loads(schema))
            if validation_error is not None:
                return AwaitOutcome(
                    status=AwaitStatus.EXITED,
                    result=payload,
                    validation_error=validation_error,
                )
        return AwaitOutcome(status=AwaitStatus.EXITED, result=payload)

    def _snapshot_from_observation(
        self,
        handle: SessionHandle,
        observation: Observation,
    ) -> AgentSessionSnapshot:
        now = datetime.now(timezone.utc)
        return AgentSessionSnapshot.from_handle(
            handle,
            status=observation.status,
            last_observed_at=now,
            finished_at=now if observation.is_terminal else None,
            output_snippet=observation.output_snippet,
        )

    def _record_snapshot(
        self,
        event_type: str,
        handle: SessionHandle,
        status: SessionStatus,
        *,
        output_snippet: str | None = None,
    ) -> AgentSessionSnapshot:
        now = datetime.now(timezone.utc)
        previous = self._session_repository.get_session(handle.session_id)
        state = self._state_for_handle(handle)
        backend_ref = dict(previous.backend_ref) if previous is not None else {}
        if state is not None:
            backend_ref.update(
                {
                    "session_name": handle.session_id,
                    "pane_id": state.pane_id,
                    "agent_type": state.agent_type.value,
                    "work_dir": str(state.work_dir),
                }
            )
            if state.result_schema is not None:
                backend_ref["result_schema_json"] = json.dumps(
                    state.result_schema,
                    sort_keys=True,
                )
        snapshot = AgentSessionSnapshot.from_handle(
            handle,
            status=status,
            backend_ref=backend_ref,
            lifecycle=state.lifecycle if state is not None else None,
            last_observed_at=now,
            finished_at=(
                previous.finished_at
                if previous is not None and previous.finished_at is not None
                else now
                if status
                in (
                    SessionStatus.DONE,
                    SessionStatus.FAILED,
                    SessionStatus.EXITED,
                    SessionStatus.STOPPED,
                )
                else None
            ),
            cleaned_at=previous.cleaned_at if previous is not None else None,
            output_snippet=(
                output_snippet
                if output_snippet is not None
                else previous.output_snippet
                if previous is not None
                else None
            ),
        )
        return self._session_repository.record_snapshot(event_type, snapshot)

    def _state_for_handle(self, handle: SessionHandle) -> SessionState | None:
        state = self._sessions.get(handle.session_id)
        if state is not None:
            return state
        snapshot = self._session_repository.get_session(handle.session_id)
        if snapshot is None:
            return None
        pane_id = snapshot.backend_ref.get("pane_id")
        if pane_id is None:
            return None
        state = SessionState(
            handle=handle,
            adapter=get_adapter(snapshot.agent_type),
            pane_id=pane_id,
            agent_type=snapshot.agent_type,
            work_dir=snapshot.work_dir,
            lifecycle=snapshot.lifecycle,
            started_at=snapshot.started_at,
            status=snapshot.status,
            result_schema=(
                json.loads(snapshot.backend_ref["result_schema_json"])
                if "result_schema_json" in snapshot.backend_ref
                else None
            ),
        )
        self._sessions[handle.session_id] = state
        return state

    # -- MCP server lifecycle -------------------------------------------------

    def _start_mcp_server(
        self,
        effect: LaunchEffect,
        run_tool: RunToolFn,
    ) -> McpToolServer:
        """Start an MCP SSE server and write .mcp.json to work_dir."""
        server = McpToolServer(
            tools=effect.mcp_tools,
            run_tool=run_tool,
        )
        server.start()
        self._mcp_servers[effect.session_name] = server

        mcp_json_path = effect.work_dir / ".mcp.json"
        mcp_config = {
            "mcpServers": {
                effect.mcp_server_name: {
                    "type": "sse",
                    "url": server.url,
                },
            },
        }
        mcp_json_path.write_text(json.dumps(mcp_config, indent=2))
        return server

    def _stop_mcp_server(self, session_name: str) -> None:
        """Stop the MCP server for a session (if any)."""
        server = self._mcp_servers.pop(session_name, None)
        if server is not None:
            server.shutdown()

    # -- Helpers -------------------------------------------------------------

    def _wait_for_ready(self, target: str, pattern: str, timeout: float) -> bool:
        """Wait for agent to be ready for input."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            output = self._backend.capture_pane(target, 50)
            if re.search(pattern, output):
                return True
            time.sleep(0.2)
        return False

    def _materialize_task_workspace(self, task) -> None:
        task.work_dir.mkdir(parents=True, exist_ok=True)
        for wf in task.workspace_files:
            dst = task.work_dir / wf.relative_path
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(wf.content)
            if wf.executable:
                dst.chmod(dst.stat().st_mode | 0o111)

    def _prepare_claude_home(
        self,
        agent_home: Path,
        trusted_workspaces: tuple[Path, ...],
    ) -> None:
        prepare_claude_home(agent_home, trusted_workspaces)

    def _wrap_with_shell_exports(self, command: str, env: dict[str, str]) -> str:
        return wrap_with_shell_exports(command, env)


__all__ = [
    "AgentHandler",
    "SessionState",
    "TmuxAgentHandler",
    "get_adapter",
    "register_adapter",
]
