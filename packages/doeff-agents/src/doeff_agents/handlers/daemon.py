"""Agent effect handler backed by the doeff-agentd supervisor daemon."""

import os
import shlex
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

from doeff_agents.adapters.base import AgentType, LaunchParams
from doeff_agents.adapters.codex import trust_workspace_in_codex_home
from doeff_agents.agentd_client import RPC_ERR_NO_SUCH_SESSION, AgentdClientError
from doeff_agents.claude_home import prepare_claude_home
from doeff_agents.effects import (
    AgentError,
    AgentLaunchError,
    AgentNotAvailableError,
    AgentSessionLifecycle,
    AgentSessionSnapshot,
    AttachAgentSessionEffect,
    AwaitOutcome,
    AwaitResultEffect,
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
    SessionHandle,
    SessionNotFoundError,
    StopEffect,
    StopSessionEffect,
)
from doeff_agents.runtime import ClaudeRuntimePolicy
from doeff_agents.shell import assert_no_forbidden_agent_env, wrap_with_shell_exports

from .production import AgentHandler, get_adapter


class AgentdSessionClient(Protocol):
    """Subset of AgentdClient used by DaemonAgentHandler."""

    def launch_session(
        self,
        *,
        session_id: str,
        session_name: str,
        agent_type: str,
        work_dir: Path,
        command: str | None = None,
        prompt: str | None = None,
        model: str | None = None,
        effort: str | None = None,
        lifecycle: AgentSessionLifecycle,
        session_env: Mapping[str, str] | None = None,
        expected_result: Mapping[str, object] | None = None,
    ) -> AgentSessionSnapshot: ...

    def await_result(
        self,
        session_id: str,
        *,
        timeout_seconds: float | None = None,
    ) -> AwaitOutcome: ...

    def get_session(self, session_id: str) -> AgentSessionSnapshot | None: ...

    def list_sessions(
        self,
        query=None,
    ) -> tuple[AgentSessionSnapshot, ...]: ...

    def capture_session(self, session_id: str, *, lines: int = 100) -> str: ...

    def send_session(
        self,
        session_id: str,
        message: str,
        *,
        enter: bool = True,
        literal: bool = True,
    ) -> None: ...

    def cancel_session(self, session_id: str) -> AgentSessionSnapshot: ...

    def cleanup_session(self, session_id: str) -> AgentSessionSnapshot: ...


class DaemonAgentHandler(AgentHandler):
    """Agent handler that delegates lifecycle ownership to doeff-agentd."""

    def __init__(
        self,
        *,
        client: AgentdSessionClient,
        claude_runtime_policy: ClaudeRuntimePolicy | None = None,
    ) -> None:
        self._client = client
        self._claude_runtime_policy = claude_runtime_policy or ClaudeRuntimePolicy()

    def handle_launch(
        self,
        effect: LaunchEffect,
        mcp_servers: dict[str, str] | None = None,
    ) -> SessionHandle:
        """Build the launch command and register it with doeff-agentd."""
        if effect.mcp_tools:
            raise AgentLaunchError(
                "doeff-agentd does not manage MCP lifecycle; prepare MCP with defmcp"
            )
        return self._launch(
            session_name=effect.session_name,
            agent_type=effect.agent_type,
            work_dir=effect.work_dir,
            prompt=effect.prompt,
            model=effect.model,
            effort=effect.effort,
            bare=effect.bare,
            lifecycle=effect.lifecycle,
            session_env=effect.session_env,
        )

    def handle_launch_task(self, effect: LaunchTaskEffect) -> SessionHandle:
        """LaunchTaskEffect is deprecated."""
        raise AgentLaunchError("LaunchTaskEffect is deprecated; use LaunchEffect directly")

    def handle_claude_launch(self, effect: ClaudeLaunchEffect) -> SessionHandle:
        """Launch a Claude session through doeff-agentd."""
        if effect.mcp_tools:
            raise AgentLaunchError(
                "doeff-agentd does not manage MCP lifecycle; prepare MCP with defmcp"
            )
        return self._launch(
            session_name=effect.session_name,
            agent_type=AgentType.CLAUDE,
            work_dir=effect.work_dir,
            prompt=effect.prompt,
            model=effect.model,
            effort=effect.effort,
            bare=effect.bare,
            lifecycle=effect.lifecycle,
            session_env=effect.session_env,
        )

    def handle_monitor(self, effect: MonitorEffect) -> Observation:
        """Return the daemon's latest recorded session state."""
        snapshot = self._require_snapshot(effect.handle.session_id)
        return Observation(
            status=snapshot.status,
            output_changed=False,
            output_snippet=snapshot.output_snippet,
        )

    def handle_capture(self, effect: CaptureEffect) -> str:
        """Capture current session output through the daemon."""
        return self._client.capture_session(effect.handle.session_id, lines=effect.lines)

    def handle_send(self, effect: SendEffect) -> None:
        """Send input to a running session through the daemon."""
        self._client.send_session(
            effect.handle.session_id,
            effect.message,
            enter=effect.enter,
            literal=effect.literal,
        )

    def handle_stop(self, effect: StopEffect) -> None:
        """Cancel a session through the daemon."""
        self._client.cancel_session(effect.handle.session_id)

    def handle_get_session(
        self,
        effect: GetAgentSessionEffect,
    ) -> AgentSessionSnapshot | None:
        """Read a session snapshot without causing backend observation."""
        return self._client.get_session(effect.session_id)

    def handle_list_sessions(
        self,
        effect: ListAgentSessionsEffect,
    ) -> tuple[AgentSessionSnapshot, ...]:
        """List daemon-owned session snapshots."""
        return self._client.list_sessions(effect.query)

    def handle_observe_session(
        self,
        effect: ObserveAgentSessionEffect,
    ) -> AgentSessionSnapshot:
        """Read the daemon's latest snapshot without mutating session state."""
        return self._require_snapshot(effect.session_id)

    def handle_attach_session(self, effect: AttachAgentSessionEffect) -> None:
        """Attach is intentionally not part of the first daemon RPC slice."""
        raise AgentError("AttachAgentSession is not implemented by doeff-agentd client")

    def handle_cancel_session(
        self,
        effect: CancelAgentSessionEffect,
    ) -> AgentSessionSnapshot:
        """Cancel a running session through the daemon."""
        return self._client.cancel_session(effect.session_id)

    def handle_cleanup_session(
        self,
        effect: CleanupAgentSessionEffect,
    ) -> AgentSessionSnapshot:
        """Clean up daemon-owned session resources."""
        return self._client.cleanup_session(effect.session_id)

    def _launch(
        self,
        *,
        session_name: str,
        agent_type: AgentType,
        work_dir: Path,
        prompt: str | None,
        model: str | None,
        effort: str | None,
        bare: bool,
        lifecycle: AgentSessionLifecycle,
        session_env: Mapping[str, str] | None,
    ) -> SessionHandle:
        adapter = get_adapter(agent_type)
        if not adapter.is_available():
            raise AgentNotAvailableError(f"{agent_type.value} CLI is not available")

        assert_no_forbidden_agent_env(
            session_env,
            context="AgentdAgentHandler session_env",
        )
        tmux_env: dict[str, str] = dict(session_env or {})
        command_env: dict[str, str] = dict(session_env or {})
        self._prepare_agent_environment(agent_type, work_dir, tmux_env, command_env)

        command: str | None = None
        if agent_type not in (AgentType.CLAUDE, AgentType.CODEX):
            argv = adapter.launch_command(
                LaunchParams(
                    work_dir=work_dir,
                    prompt=None,
                    model=model,
                    effort=effort,
                    bare=bare,
                )
            )
            command = wrap_with_shell_exports(shlex.join(argv), command_env)
        snapshot = self._client.launch_session(
            session_id=session_name,
            session_name=session_name,
            agent_type=agent_type.value,
            work_dir=work_dir,
            command=command,
            prompt=prompt,
            model=model,
            effort=effort,
            lifecycle=lifecycle,
            session_env=tmux_env,
        )
        return snapshot.to_handle()

    def handle_launch_session(
        self,
        effect: LaunchSessionEffect,
        mcp_servers: dict[str, str] | None = None,
    ) -> L2SessionHandle:
        """Idempotently launch or re-adopt an agentd session."""
        if effect.spec.mcp_tools:
            raise AgentLaunchError(
                "doeff-agentd does not manage MCP lifecycle; prepare MCP with defmcp"
            )
        session_id = effect.spec.session_id
        existing = self._client.get_session(session_id)
        if existing is not None:
            return L2SessionHandle(session_id=session_id)

        adapter = get_adapter(effect.spec.agent_type)
        if not adapter.is_available():
            raise AgentNotAvailableError(f"{effect.spec.agent_type.value} CLI is not available")

        assert_no_forbidden_agent_env(
            effect.spec.session_env,
            context="AgentSpec.session_env",
        )
        tmux_env: dict[str, str] = dict(effect.spec.session_env or {})
        command_env: dict[str, str] = dict(effect.spec.session_env or {})
        self._prepare_agent_environment(
            effect.spec.agent_type, effect.spec.work_dir, tmux_env, command_env
        )

        # agentd owns the concrete result-file contract.  The Python layer
        # supplies only the schema and retry budget.
        self._client.launch_session(
            session_id=session_id,
            session_name=session_id,
            agent_type=effect.spec.agent_type.value,
            work_dir=effect.spec.work_dir,
            prompt=effect.spec.prompt,
            model=effect.spec.model,
            effort=effect.spec.effort,
            lifecycle=effect.spec.lifecycle,
            session_env=tmux_env,
            expected_result={
                "payload_schema": effect.spec.result_schema,
                "max_retries": effect.spec.max_retries,
            },
        )
        return L2SessionHandle(session_id=session_id)

    def handle_await_result(self, effect: AwaitResultEffect) -> AwaitOutcome:
        """Await agentd's single await_result handoff."""
        try:
            return self._client.await_result(
                effect.handle.session_id,
                timeout_seconds=effect.timeout_seconds,
            )
        except AgentdClientError as exc:
            if exc.error_code == RPC_ERR_NO_SUCH_SESSION:
                raise SessionNotFoundError(effect.handle.session_id) from exc
            raise

    def handle_follow_up(self, effect: FollowUpEffect) -> L2SessionHandle:
        """Continue a daemon-owned session."""
        self._client.send_session(effect.handle.session_id, effect.message)
        return effect.handle

    def handle_stop_session(self, effect: StopSessionEffect) -> None:
        """Stop a daemon-owned L2 session."""
        self._client.cancel_session(effect.handle.session_id)

    def handle_release_session(self, effect: ReleaseSessionEffect) -> None:
        """Release is a no-op for daemon-owned durable sessions."""
        return

    def _prepare_agent_environment(
        self,
        agent_type: AgentType,
        work_dir: Path,
        tmux_env: dict[str, str],
        command_env: dict[str, str],
    ) -> None:
        if agent_type == AgentType.CLAUDE:
            assert_no_forbidden_agent_env(
                self._claude_runtime_policy.bootstrap_exports,
                context="ClaudeRuntimePolicy.bootstrap_exports",
            )
            agent_home = self._claude_runtime_policy.agent_home or work_dir / ".agent-home"
            trusted_workspaces = self._claude_runtime_policy.trusted_workspaces or (work_dir,)
            self._prepare_claude_home(agent_home, trusted_workspaces)
            tmux_env.setdefault("DISABLE_AUTO_UPDATE", "true")
            tmux_env.setdefault("DISABLE_UPDATE_PROMPT", "true")
            claude_env = {
                "HOME": str(agent_home),
                "CLAUDE_HOME": str(agent_home / ".claude"),
                **self._claude_runtime_policy.bootstrap_exports,
            }
            tmux_env.update(claude_env)
            command_env.update(claude_env)
        elif agent_type == AgentType.CODEX:
            codex_home = command_env.get(
                "CODEX_HOME",
                os.environ.get("CODEX_HOME", str(Path.home() / ".codex")),
            )
            trust_workspace_in_codex_home(codex_home, work_dir)

    def _require_snapshot(self, session_id: str) -> AgentSessionSnapshot:
        snapshot = self._client.get_session(session_id)
        if snapshot is None:
            raise SessionNotFoundError(f"Session {session_id} is not registered")
        return snapshot

    def _prepare_claude_home(
        self,
        agent_home: Path,
        trusted_workspaces: tuple[Path, ...],
    ) -> None:
        prepare_claude_home(agent_home, trusted_workspaces)


__all__ = [
    "AgentdSessionClient",
    "DaemonAgentHandler",
]
