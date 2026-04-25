"""Production effect handler backed by tmux."""


import json
import os
import re
import shlex
import shutil
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from doeff_agents import tmux
from doeff_agents.adapters.base import AgentAdapter, AgentType, InjectionMethod, LaunchParams
from doeff_agents.adapters.claude import ClaudeAdapter
from doeff_agents.adapters.codex import CodexAdapter
from doeff_agents.adapters.gemini import GeminiAdapter
from doeff_agents.effects import (
    AgentLaunchError,
    AgentNotAvailableError,
    AgentReadyTimeoutError,
    CaptureEffect,
    ClaudeLaunchEffect,
    LaunchEffect,
    LaunchTaskEffect,
    MonitorEffect,
    Observation,
    SendEffect,
    SessionAlreadyExistsError,
    SessionHandle,
    SessionNotFoundError,
    SleepEffect,
    StopEffect,
)
from doeff_agents.mcp_server import McpToolServer, RunToolFn
from doeff_agents.monitor import (
    MonitorState,
    SessionStatus,
    detect_pr_url,
    detect_status,
    hash_content,
    is_waiting_for_input,
)
from doeff_agents.runtime import ClaudeRuntimePolicy
from doeff_agents.session import _dismiss_onboarding_dialogs
from doeff_agents.session_backend import SessionBackend


class AgentHandler(ABC):
    """Abstract handler for agent effects."""

    @abstractmethod
    def handle_launch(self, effect: LaunchEffect) -> SessionHandle:
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
    def handle_sleep(self, effect: SleepEffect) -> None:
        """Handle Sleep effect."""


@dataclass
class SessionState:
    """Mutable state for a session (internal to handler)."""

    handle: SessionHandle
    adapter: AgentAdapter
    monitor_state: MonitorState = field(default_factory=MonitorState)
    status: SessionStatus = SessionStatus.BOOTING
    pr_url: str | None = None


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


class TmuxAgentHandler(AgentHandler):
    """Handler that executes effects using real tmux sessions."""

    def __init__(
        self,
        *,
        backend: SessionBackend | None = None,
        claude_runtime_policy: ClaudeRuntimePolicy | None = None,
    ) -> None:
        self._sessions: dict[str, SessionState] = {}
        self._backend = backend or tmux.get_default_backend()
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

        # Start MCP server if tools are provided
        if effect.mcp_tools and run_tool is not None:
            self._start_mcp_server(effect, run_tool)

        tmux_config = tmux.SessionConfig(
            session_name=effect.session_name,
            work_dir=effect.work_dir,
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
        command = shlex.join(argv)

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
            session_name=effect.session_name,
            pane_id=session_info.pane_id,
            agent_type=effect.agent_type,
            work_dir=effect.work_dir,
        )

        self._sessions[effect.session_name] = SessionState(handle=handle, adapter=adapter)
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
                timeout=effect.ready_timeout_sec,
                backend=self._backend,
            )

        handle = SessionHandle(
            session_name=effect.session_name,
            pane_id=session_info.pane_id,
            agent_type=AgentType.CLAUDE,
            work_dir=effect.work_dir,
        )
        self._sessions[effect.session_name] = SessionState(handle=handle, adapter=adapter)
        return handle

    def handle_monitor(self, effect: MonitorEffect) -> Observation:
        """Check session status and return observation."""
        handle = effect.handle
        state = self._sessions.get(handle.session_name)

        if state is None:
            if not self._backend.has_session(handle.session_name):
                return Observation(status=SessionStatus.EXITED)
            state = SessionState(handle=handle, adapter=get_adapter(handle.agent_type))
            self._sessions[handle.session_name] = state

        if not self._backend.has_session(handle.session_name):
            state.status = SessionStatus.EXITED
            return Observation(status=SessionStatus.EXITED)

        output = self._backend.capture_pane(handle.pane_id)

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

        pr_url = None
        if not state.pr_url:
            detected_url = detect_pr_url(output)
            if detected_url:
                state.pr_url = detected_url
                pr_url = detected_url

        new_status = detect_status(output, state.monitor_state, output_changed, has_prompt)
        if new_status:
            state.status = new_status

        return Observation(
            status=state.status,
            output_changed=output_changed,
            pr_url=pr_url,
            output_snippet=output[-500:] if output else None,
        )

    def handle_capture(self, effect: CaptureEffect) -> str:
        """Capture pane output."""
        handle = effect.handle
        if not self._backend.has_session(handle.session_name):
            raise SessionNotFoundError(f"Session {handle.session_name} does not exist")
        return self._backend.capture_pane(handle.pane_id, effect.lines)

    def handle_send(self, effect: SendEffect) -> None:
        """Send message to session."""
        handle = effect.handle
        if not self._backend.has_session(handle.session_name):
            raise SessionNotFoundError(f"Session {handle.session_name} does not exist")
        self._backend.send_keys(
            handle.pane_id,
            effect.message,
            literal=effect.literal,
            enter=effect.enter,
        )

    def handle_stop(self, effect: StopEffect) -> None:
        """Stop session and its MCP server (if any)."""
        handle = effect.handle
        self._stop_mcp_server(handle.session_name)
        if self._backend.has_session(handle.session_name):
            self._backend.kill_session(handle.session_name)
        state = self._sessions.get(handle.session_name)
        if state:
            state.status = SessionStatus.STOPPED

    def handle_sleep(self, effect: SleepEffect) -> None:
        """Sleep for duration."""
        time.sleep(effect.seconds)

    # -- MCP server lifecycle -------------------------------------------------

    def _start_mcp_server(
        self,
        effect: LaunchEffect,
        run_tool: RunToolFn,
    ) -> None:
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
        source_home = Path.home()
        claude_dir = agent_home / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)

        # Claude Code 2.1+ reads `${CLAUDE_HOME}/.claude.json` (CLAUDE_HOME is
        # set to `${agent_home}/.claude` below). Older versions read
        # `${HOME}/.claude.json`. Write trust state to both so the
        # workspace-trust dialog is skipped regardless of which version of
        # Claude Code is installed (the dialog is interactive and would
        # block the tmux launch indefinitely otherwise).
        candidate_json_paths: list[Path] = [
            agent_home / ".claude.json",                # legacy
            agent_home / ".claude" / ".claude.json",    # 2.1+ — read by CLI
        ]
        source_claude_json = source_home / ".claude.json"

        # Reuse the user's authentication blob in agent_home (only if the
        # caller explicitly relocated to a non-default home).
        for claude_json in candidate_json_paths:
            if (
                agent_home != source_home
                and not claude_json.exists()
                and source_claude_json.exists()
            ):
                claude_json.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_claude_json, claude_json)

        for claude_json in candidate_json_paths:
            data = (
                json.loads(claude_json.read_text()) if claude_json.exists() else {}
            )
            projects = data.setdefault("projects", {})
            for workspace in trusted_workspaces:
                entry = projects.setdefault(str(workspace), {})
                # Claude Code keeps each project entry's existing fields
                # (allowedTools, mcpServers, etc.) intact; we only need to
                # ensure the trust + onboarding flags are set so no dialog
                # appears on launch. Always overwrite — a stale entry from
                # a previous launch where trust was declined would still
                # show the dialog otherwise.
                entry.setdefault("allowedTools", [])
                entry["hasTrustDialogAccepted"] = True
                entry["hasCompletedProjectOnboarding"] = True
                entry.setdefault("projectOnboardingSeenCount", 0)
            claude_json.parent.mkdir(parents=True, exist_ok=True)
            claude_json.write_text(json.dumps(data))

        config_path = claude_dir / "config.json"
        source_config = source_home / ".claude" / "config.json"
        if (
            agent_home != source_home
            and not config_path.exists()
            and source_config.exists()
        ):
            shutil.copy2(source_config, config_path)
        if not config_path.exists():
            config_path.write_text(json.dumps({"hasCompletedOnboarding": True}))
        else:
            config_data = json.loads(config_path.read_text())
            config_data["hasCompletedOnboarding"] = True
            config_path.write_text(json.dumps(config_data))

        settings_path = claude_dir / "settings.json"
        source_settings = source_home / ".claude" / "settings.json"
        if (
            agent_home != source_home
            and not settings_path.exists()
            and source_settings.exists()
        ):
            shutil.copy2(source_settings, settings_path)
        if not settings_path.exists():
            settings_path.write_text("{}")

        credentials_path = claude_dir / ".credentials.json"
        source_credentials = source_home / ".claude" / ".credentials.json"
        if (
            agent_home != source_home
            and not credentials_path.exists()
            and source_credentials.exists()
        ):
            shutil.copy2(source_credentials, credentials_path)

    def _wrap_with_shell_exports(self, command: str, env: dict[str, str]) -> str:
        exports = " ".join(
            f"export {key}={shlex.quote(value)};" for key, value in env.items()
        )
        return f"{exports} {command}"


__all__ = [
    "AgentHandler",
    "SessionState",
    "TmuxAgentHandler",
    "get_adapter",
    "register_adapter",
]
