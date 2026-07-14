"""Prompt paste must be gated on the agent TUI's input composer being visible.

Regression suite for issue agentd-codex-coldstart-paste-race (2026-07-14):
codex/claude adapters shipped ``ready_pattern = None``, so every TMUX launch
path pasted the prompt immediately after the launch command. On a cold start
the terminal split the multi-paragraph prompt into per-line submits, the agent
acknowledged each fragment without doing any work, and the session exited 0 —
a false Succeeded. The fix requires:

1. codex/claude adapters declare a real ``ready_pattern`` that matches the
   idle composer and rejects every known non-ready screen (login screen,
   trust dialog, update dialog, MCP-boot window, pre-launch shell frames).
2. every launch path hard-fails with ``AgentReadyTimeoutError`` (and delivers
   NO prompt) when the composer never appears.

Screen fixtures under ``data/ready_screens/`` are verbatim tmux captures of
codex 0.144.4 and Claude Code 2.1.209 (``--ax-screen-reader``), with user
identity strings and long workspace paths sanitized.
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from doeff_agents.adapters.base import AgentType, LaunchConfig
from doeff_agents.adapters.claude import ClaudeAdapter
from doeff_agents.adapters.codex import CodexAdapter
from doeff_agents.effects.agent import (
    AgentReadyTimeoutError as EffectsAgentReadyTimeoutError,
)
from doeff_agents.effects.agent import (
    ClaudeLaunchEffect,
    LaunchEffect,
)
from doeff_agents.runtime import ClaudeRuntimePolicy, CodexRuntimePolicy
from doeff_agents.session import (
    AgentReadyTimeoutError,
    launch_session,
)
from doeff_agents.session_backend import SessionBackend
from doeff_agents.tmux import SessionConfig, SessionInfo

READY_SCREENS = Path(__file__).parent / "data" / "ready_screens"

CODEX_READY = (READY_SCREENS / "codex_ready.txt").read_text(encoding="utf-8")
CODEX_MCP_BOOT = (READY_SCREENS / "codex_mcp_boot.txt").read_text(encoding="utf-8")
CODEX_LOGIN = (READY_SCREENS / "codex_login.txt").read_text(encoding="utf-8")
CODEX_TRUST_DIALOG = (READY_SCREENS / "codex_trust_dialog.txt").read_text(encoding="utf-8")
CODEX_UPDATE_DIALOG = (READY_SCREENS / "codex_update_dialog.txt").read_text(encoding="utf-8")
CLAUDE_READY = (READY_SCREENS / "claude_screen_reader_ready.txt").read_text(encoding="utf-8")
CLAUDE_TRUST_DIALOG = (READY_SCREENS / "claude_screen_reader_trust_dialog.txt").read_text(
    encoding="utf-8"
)

# Frames a pane shows BEFORE the agent TUI takes over: the shell prompt and
# the echoed launch command. A ready gate matching any of these would paste
# into a half-started agent — the exact race this suite guards against.
PRE_LAUNCH_SHELL_FRAMES = [
    "$ ",
    "$ codex --yolo",
    "➜  workdir codex --yolo",
    "\u276f claude --ax-screen-reader --dangerously-skip-permissions",  # starship-style shell prompt
    "bash-5.2$ claude --ax-screen-reader",
]


# =============================================================================
# ready_pattern facts (screen-fixture level)
# =============================================================================


def test_codex_adapter_declares_ready_pattern() -> None:
    assert CodexAdapter().ready_pattern is not None


def test_claude_adapter_declares_ready_pattern() -> None:
    assert ClaudeAdapter().ready_pattern is not None


def _matches(pattern: str | None, screen: str) -> bool:
    assert pattern is not None
    return re.search(pattern, screen) is not None


def test_codex_ready_pattern_matches_idle_composer() -> None:
    assert _matches(CodexAdapter().ready_pattern, CODEX_READY)


def test_codex_ready_pattern_rejects_login_screen() -> None:
    # No auth in CODEX_HOME → sign-in menu. Pasting there would start an
    # interactive login flow; the launch must time out loudly instead.
    assert not _matches(CodexAdapter().ready_pattern, CODEX_LOGIN)


def test_codex_ready_pattern_rejects_trust_dialog() -> None:
    # The trust dialog draws the same U+203A marker as the composer, in front
    # of a numbered option.
    assert not _matches(CodexAdapter().ready_pattern, CODEX_TRUST_DIALOG)


def test_codex_ready_pattern_rejects_update_dialog() -> None:
    # Enter on the update dialog's default option starts a global npm
    # upgrade — mistaking it for the composer is destructive.
    assert not _matches(CodexAdapter().ready_pattern, CODEX_UPDATE_DIALOG)


def test_codex_ready_pattern_rejects_mcp_boot_window() -> None:
    # The composer is already drawn while MCP servers are still starting,
    # but the input loop is not wired yet: Enter gets eaten and the prompt
    # sits unsubmitted (doeff-agentd oracle, main.rs wait_for_repl_idle).
    assert not _matches(CodexAdapter().ready_pattern, CODEX_MCP_BOOT)


@pytest.mark.parametrize("frame", PRE_LAUNCH_SHELL_FRAMES)
def test_codex_ready_pattern_rejects_pre_launch_shell_frames(frame: str) -> None:
    assert not _matches(CodexAdapter().ready_pattern, frame)


def test_claude_ready_pattern_matches_screen_reader_ready_screen() -> None:
    assert _matches(ClaudeAdapter().ready_pattern, CLAUDE_READY)


def test_claude_ready_pattern_rejects_screen_reader_trust_dialog() -> None:
    assert not _matches(ClaudeAdapter().ready_pattern, CLAUDE_TRUST_DIALOG)


def test_claude_ready_pattern_rejects_codex_screens() -> None:
    assert not _matches(ClaudeAdapter().ready_pattern, CODEX_LOGIN)


@pytest.mark.parametrize("frame", PRE_LAUNCH_SHELL_FRAMES)
def test_claude_ready_pattern_rejects_pre_launch_shell_frames(frame: str) -> None:
    assert not _matches(ClaudeAdapter().ready_pattern, frame)


# =============================================================================
# Scripted backend: pane frames advance on every capture, sends record the
# frame index at which they happened.
# =============================================================================


class ScriptedBackend(SessionBackend):
    """Fake backend whose pane replays a scripted frame sequence.

    ``capture_pane`` returns the current frame and advances the script by
    one frame per call; the final frame repeats forever. ``send_keys``
    records the frame index visible at send time so tests can assert a
    prompt was pasted only once the ready frame was on screen.
    """

    def __init__(self, frames: list[str]) -> None:
        self.frames = frames
        self.frame_index = 0
        self.sessions: set[str] = set()
        self.sent: list[tuple[str, str, int]] = []
        self.killed: list[str] = []

    def is_available(self) -> bool:
        return True

    def is_inside_session(self) -> bool:
        return False

    def has_session(self, name: str) -> bool:
        return name in self.sessions

    def new_session(self, cfg: SessionConfig) -> SessionInfo:
        self.sessions.add(cfg.session_name)
        return SessionInfo(
            session_name=cfg.session_name,
            pane_id=f"%{cfg.session_name}",
            created_at=datetime.now(timezone.utc),
        )

    def send_keys(
        self,
        target: str,
        keys: str,
        *,
        literal: bool = True,
        enter: bool = True,
    ) -> None:
        self.sent.append((target, keys, self.frame_index))

    def capture_pane(
        self,
        target: str,
        lines: int = 100,
        *,
        strip_ansi_codes: bool = True,
    ) -> str:
        frame = self.frames[min(self.frame_index, len(self.frames) - 1)]
        if self.frame_index < len(self.frames) - 1:
            self.frame_index += 1
        return frame

    def capture_transcript(
        self,
        target: str,
        lines: int = 100,
        *,
        strip_ansi_codes: bool = True,
    ) -> str:
        return self.capture_pane(target, lines)

    def kill_session(self, session: str) -> None:
        self.killed.append(session)
        self.sessions.discard(session)

    def attach_session(self, session: str) -> None:
        raise NotImplementedError

    def list_sessions(self) -> list[str]:
        return sorted(self.sessions)

    def sent_texts(self) -> list[str]:
        return [keys for _target, keys, _frame in self.sent]


class ReadyFakeCodexAdapter(CodexAdapter):
    def is_available(self) -> bool:
        return True


class ReadyFakeClaudeAdapter(ClaudeAdapter):
    def is_available(self) -> bool:
        return True

    def pre_launch(self) -> None:
        return None


PROMPT = "line one\nline two\n\nline four (multi-paragraph 規範文)"


# =============================================================================
# session.py launch_session (imperative API)
# =============================================================================


def test_launch_session_codex_hard_fails_when_composer_never_appears(monkeypatch) -> None:
    backend = ScriptedBackend(frames=["$ ", "$ codex --yolo"])
    monkeypatch.setattr(
        "doeff_agents.session.get_adapter", lambda _agent_type: ReadyFakeCodexAdapter()
    )

    config = LaunchConfig(
        agent_type=AgentType.CODEX,
        work_dir=Path("/tmp/ws"),
        prompt=PROMPT,
    )

    with pytest.raises(AgentReadyTimeoutError):
        launch_session("codex-cold", config, ready_timeout=0.5, backend=backend)

    assert PROMPT not in backend.sent_texts()
    assert "codex-cold" in backend.killed


def test_launch_session_codex_pastes_prompt_only_after_ready_frame(monkeypatch) -> None:
    frames = ["$ ", "$ codex --yolo", CODEX_MCP_BOOT, CODEX_READY]
    backend = ScriptedBackend(frames=frames)
    monkeypatch.setattr(
        "doeff_agents.session.get_adapter", lambda _agent_type: ReadyFakeCodexAdapter()
    )

    config = LaunchConfig(
        agent_type=AgentType.CODEX,
        work_dir=Path("/tmp/ws"),
        prompt=PROMPT,
    )

    session = launch_session("codex-warm", config, ready_timeout=5.0, backend=backend)

    assert session.session_name == "codex-warm"
    prompt_sends = [(t, k, f) for t, k, f in backend.sent if k == PROMPT]
    assert len(prompt_sends) == 1
    _target, _keys, frame_at_send = prompt_sends[0]
    ready_frame = frames.index(CODEX_READY)
    assert frame_at_send >= ready_frame, (
        "prompt was pasted before the codex composer appeared "
        f"(frame {frame_at_send} < ready frame {ready_frame})"
    )


def test_launch_session_claude_hard_fails_when_composer_never_appears(monkeypatch) -> None:
    backend = ScriptedBackend(frames=["$ ", "$ claude --ax-screen-reader"])
    monkeypatch.setattr(
        "doeff_agents.session.get_adapter", lambda _agent_type: ReadyFakeClaudeAdapter()
    )

    config = LaunchConfig(
        agent_type=AgentType.CLAUDE,
        work_dir=Path("/tmp/ws"),
        prompt=PROMPT,
    )

    with pytest.raises(AgentReadyTimeoutError):
        launch_session(
            "claude-cold",
            config,
            ready_timeout=0.5,
            dismiss_trust_dialog=False,
            backend=backend,
        )

    assert PROMPT not in backend.sent_texts()
    assert "claude-cold" in backend.killed


def test_launch_session_claude_pastes_prompt_only_after_ready_frame(monkeypatch) -> None:
    frames = ["$ ", "$ claude --ax-screen-reader", CLAUDE_READY]
    backend = ScriptedBackend(frames=frames)
    monkeypatch.setattr(
        "doeff_agents.session.get_adapter", lambda _agent_type: ReadyFakeClaudeAdapter()
    )

    config = LaunchConfig(
        agent_type=AgentType.CLAUDE,
        work_dir=Path("/tmp/ws"),
        prompt=PROMPT,
    )

    launch_session(
        "claude-warm",
        config,
        ready_timeout=5.0,
        dismiss_trust_dialog=False,
        backend=backend,
    )

    prompt_sends = [(t, k, f) for t, k, f in backend.sent if k == PROMPT]
    assert len(prompt_sends) == 1
    assert prompt_sends[0][2] >= frames.index(CLAUDE_READY)


# =============================================================================
# handlers/production.py TmuxAgentHandler (effects API)
# =============================================================================


def _production_handler(backend: ScriptedBackend, tmp_path: Path):
    from doeff_agents.handlers.production import TmuxAgentHandler

    return TmuxAgentHandler(
        backend=backend,
        codex_runtime_policy=CodexRuntimePolicy(codex_home=tmp_path / "codex-home"),
        claude_runtime_policy=ClaudeRuntimePolicy(agent_home=tmp_path / "agent-home"),
    )


def test_handle_launch_codex_hard_fails_when_composer_never_appears(
    monkeypatch, tmp_path: Path
) -> None:
    backend = ScriptedBackend(frames=["$ ", "$ codex --yolo"])
    monkeypatch.setattr(
        "doeff_agents.handlers.production.get_adapter",
        lambda _agent_type: ReadyFakeCodexAdapter(),
    )
    handler = _production_handler(backend, tmp_path)

    launch = LaunchEffect(
        session_name="codex-cold",
        agent_type=AgentType.CODEX,
        work_dir=tmp_path / "ws",
        prompt=PROMPT,
        ready_timeout=0.5,
    )

    with pytest.raises(EffectsAgentReadyTimeoutError):
        handler.handle_launch(launch)

    assert PROMPT not in backend.sent_texts()
    assert "codex-cold" in backend.killed


def test_handle_launch_codex_pastes_prompt_only_after_ready_frame(
    monkeypatch, tmp_path: Path
) -> None:
    frames = ["$ ", "$ codex --yolo", CODEX_MCP_BOOT, CODEX_READY]
    backend = ScriptedBackend(frames=frames)
    monkeypatch.setattr(
        "doeff_agents.handlers.production.get_adapter",
        lambda _agent_type: ReadyFakeCodexAdapter(),
    )
    handler = _production_handler(backend, tmp_path)

    launch = LaunchEffect(
        session_name="codex-warm",
        agent_type=AgentType.CODEX,
        work_dir=tmp_path / "ws",
        prompt=PROMPT,
        ready_timeout=5.0,
    )

    handle = handler.handle_launch(launch)

    assert handle.session_id == "codex-warm"
    prompt_sends = [(t, k, f) for t, k, f in backend.sent if k == PROMPT]
    assert len(prompt_sends) == 1
    assert prompt_sends[0][2] >= frames.index(CODEX_READY)


def test_handle_claude_launch_hard_fails_when_composer_never_appears(
    monkeypatch, tmp_path: Path
) -> None:
    # handle_claude_launch previously had NO ready gate at all — the prompt
    # was pasted right after onboarding dismissal returned.
    backend = ScriptedBackend(frames=["$ ", "$ claude --ax-screen-reader"])
    monkeypatch.setattr(
        "doeff_agents.handlers.production.get_adapter",
        lambda _agent_type: ReadyFakeClaudeAdapter(),
    )
    handler = _production_handler(backend, tmp_path)

    launch = ClaudeLaunchEffect(
        session_name="claude-cold",
        work_dir=tmp_path / "ws",
        prompt=PROMPT,
        ready_timeout=0.5,
    )

    with pytest.raises(EffectsAgentReadyTimeoutError):
        handler.handle_claude_launch(launch)

    assert PROMPT not in backend.sent_texts()
    assert "claude-cold" in backend.killed


def test_handle_claude_launch_pastes_prompt_only_after_ready_frame(
    monkeypatch, tmp_path: Path
) -> None:
    frames = ["$ ", "$ claude --ax-screen-reader", CLAUDE_READY]
    backend = ScriptedBackend(frames=frames)
    monkeypatch.setattr(
        "doeff_agents.handlers.production.get_adapter",
        lambda _agent_type: ReadyFakeClaudeAdapter(),
    )
    handler = _production_handler(backend, tmp_path)

    launch = ClaudeLaunchEffect(
        session_name="claude-warm",
        work_dir=tmp_path / "ws",
        prompt=PROMPT,
        ready_timeout=5.0,
    )

    handler.handle_claude_launch(launch)

    prompt_sends = [(t, k, f) for t, k, f in backend.sent if k == PROMPT]
    assert len(prompt_sends) == 1
    assert prompt_sends[0][2] >= frames.index(CLAUDE_READY)


# =============================================================================
# handlers/codex.hy + handlers/claude.hy (defhandler paths)
# =============================================================================


class ScriptedHyBackend(ScriptedBackend):
    """ScriptedBackend variant matching the Hy handler test harness."""


def _run_with_codex_handler(program, backend):
    from doeff_agents.handlers.codex import codex_handler
    from doeff_core_effects.handlers import state
    from doeff_core_effects.scheduler import scheduled

    from doeff import run

    handler = codex_handler(backend=backend)
    return run(scheduled(state()(handler(program))))


def _run_with_claude_handler(program, backend):
    from doeff_agents.handlers.claude import claude_handler
    from doeff_core_effects.handlers import state
    from doeff_core_effects.scheduler import scheduled

    from doeff import run

    handler = claude_handler(backend=backend)
    return run(scheduled(state()(handler(program))))


def test_codex_hy_handler_hard_fails_when_composer_never_appears(tmp_path: Path) -> None:
    from doeff import Perform, do

    backend = ScriptedHyBackend(frames=["$ ", "$ codex --yolo"])

    @do
    def program():
        return (
            yield Perform(
                LaunchEffect(
                    session_name="codex-hy-cold",
                    agent_type=AgentType.CODEX,
                    work_dir=tmp_path,
                    prompt=PROMPT,
                    ready_timeout=0.5,
                )
            )
        )

    with pytest.raises(AgentReadyTimeoutError):
        _run_with_codex_handler(program(), backend)

    assert PROMPT not in backend.sent_texts()


def test_codex_hy_handler_pastes_prompt_only_after_ready_frame(tmp_path: Path) -> None:
    from doeff import Perform, do

    frames = ["$ ", CODEX_MCP_BOOT, CODEX_READY]
    backend = ScriptedHyBackend(frames=frames)

    @do
    def program():
        return (
            yield Perform(
                LaunchEffect(
                    session_name="codex-hy-warm",
                    agent_type=AgentType.CODEX,
                    work_dir=tmp_path,
                    prompt=PROMPT,
                    ready_timeout=5.0,
                )
            )
        )

    _run_with_codex_handler(program(), backend)

    prompt_sends = [(t, k, f) for t, k, f in backend.sent if k == PROMPT]
    assert len(prompt_sends) == 1
    assert prompt_sends[0][2] >= frames.index(CODEX_READY)


def test_claude_hy_handler_hard_fails_when_composer_never_appears(tmp_path: Path) -> None:
    from doeff import Perform, do

    backend = ScriptedHyBackend(frames=["$ ", "$ claude --ax-screen-reader"])

    @do
    def program():
        return (
            yield Perform(
                LaunchEffect(
                    session_name="claude-hy-cold",
                    agent_type=AgentType.CLAUDE,
                    work_dir=tmp_path,
                    prompt=PROMPT,
                    ready_timeout=0.5,
                )
            )
        )

    with pytest.raises(AgentReadyTimeoutError):
        _run_with_claude_handler(program(), backend)

    assert PROMPT not in backend.sent_texts()


def test_claude_hy_handler_pastes_prompt_only_after_ready_frame(tmp_path: Path) -> None:
    from doeff import Perform, do

    frames = ["$ ", CLAUDE_READY]
    backend = ScriptedHyBackend(frames=frames)

    @do
    def program():
        return (
            yield Perform(
                LaunchEffect(
                    session_name="claude-hy-warm",
                    agent_type=AgentType.CLAUDE,
                    work_dir=tmp_path,
                    prompt=PROMPT,
                    ready_timeout=5.0,
                )
            )
        )

    _run_with_claude_handler(program(), backend)

    prompt_sends = [(t, k, f) for t, k, f in backend.sent if k == PROMPT]
    assert len(prompt_sends) == 1
    assert prompt_sends[0][2] >= frames.index(CLAUDE_READY)


# =============================================================================
# Paste transport: multi-line prompts must be delivered as ONE bracketed
# paste. Without bracketed paste the newlines act as Enter presses and the
# receiving TUI falls back to timing-dependent burst heuristics — the very
# splitting observed in the incident (and reproduced against real codex
# even after the ready gate passed).
# =============================================================================

MULTILINE_PROMPT = "line one\nline two\n\nline four"


def test_tmux_paste_streams_buffer_via_stdin_and_bracketed_paste(monkeypatch) -> None:
    import subprocess

    from doeff_agents.tmux import TmuxSessionBackend

    calls: list[tuple[list[str], object]] = []

    def fake_run(args, **kwargs):
        calls.append((list(args), kwargs.get("input")))
        if args[1] == "-V":
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        if args[1] == "capture-pane":
            # Composer is empty after submit: confirm loop exits immediately.
            return subprocess.CompletedProcess(args, 0, stdout="❯ \n", stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr("doeff_agents.tmux.subprocess.run", fake_run)
    monkeypatch.setattr("doeff_agents.tmux.time.sleep", lambda _seconds: None)

    backend = TmuxSessionBackend()
    backend.send_keys("%42", MULTILINE_PROMPT, literal=True, enter=True)

    command_names = [args[1] for args, _input in calls]
    # argv-passed set-buffer dies at ~16KB (tmux imsg framing) — the content
    # must stream through load-buffer's stdin (doeff-agentd oracle 33ab4bae).
    assert "set-buffer" not in command_names
    load_calls = [(args, i) for args, i in calls if args[1] == "load-buffer"]
    assert len(load_calls) == 1
    load_args, load_input = load_calls[0]
    assert load_args[-1] == "-"
    assert load_input == MULTILINE_PROMPT

    paste_calls = [args for args, _input in calls if args[1] == "paste-buffer"]
    assert len(paste_calls) == 1
    assert "-p" in paste_calls[0], (
        "paste-buffer must use bracketed paste (-p); raw newlines submit "
        "per-line and split the prompt into fragments"
    )


def test_sessionhost_paste_uses_bracketed_paste(monkeypatch) -> None:
    import subprocess as _subprocess

    from doeff_agents.sessionhost import substrate

    calls: list[list[str]] = []

    def fake_run_tmux(_tmux_bin, args):
        calls.append(list(args))
        return _subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    def fake_subprocess_run(args, **kwargs):
        calls.append(list(args[1:]))
        return _subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(substrate, "run_tmux", fake_run_tmux)
    monkeypatch.setattr(substrate.subprocess, "run", fake_subprocess_run)

    substrate.tmux_paste_literal_io("tmux", "%7", MULTILINE_PROMPT)

    paste_calls = [args for args in calls if args and args[0] == "paste-buffer"]
    assert len(paste_calls) == 1
    assert "-p" in paste_calls[0]
