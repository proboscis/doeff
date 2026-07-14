"""Tmux session operations with typed errors and pane tracking."""

import os
import re
import shlex
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from .session_backend import SessionBackend, SessionConfig, SessionInfo
from .shell import assert_no_forbidden_agent_env


class TmuxError(Exception):
    """Base exception for tmux operations."""


class TmuxNotAvailableError(TmuxError):
    """Raised when tmux is not installed."""


class SessionNotFoundError(TmuxError):
    """Raised when a tmux session doesn't exist."""


class SessionAlreadyExistsError(TmuxError):
    """Raised when trying to create a session that already exists."""


# ANSI/terminal control sequence pattern for stripping.
#
# tmux pipe-pane records raw full-screen application output. Claude Code emits
# CSI cursor movement, OSC title updates, and a few single ESC controls; result
# extraction must not treat those bytes as JSON payload text.
ANSI_PATTERN = re.compile(
    r"(?:"
    r"\x1b\][^\x07]*(?:\x07|\x1b\\)"  # OSC ... BEL/ST
    r"|\x1b\[[0-?]*[ -/]*[@-~]"  # CSI
    r"|\x1b[@-Z\\-_]"  # single-character ESC controls
    r")"
)


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text."""
    return ANSI_PATTERN.sub("", text)


class TmuxSessionBackend(SessionBackend):
    """Session backend implementation backed by tmux."""

    def __init__(self, executable: str | os.PathLike[str] | None = None) -> None:
        self.executable = str(executable or "tmux")
        self._transcript_dir = Path(tempfile.gettempdir()) / "doeff-agents-tmux-transcripts"
        self._transcript_paths: dict[str, Path] = {}

    def _args(self, *args: str) -> list[str]:
        return [self.executable, *args]

    def _ensure_tmux_available(self) -> None:
        if not self.is_available():
            raise TmuxNotAvailableError(f"tmux is not available: {self.executable}")

    def is_available(self) -> bool:
        result = subprocess.run(self._args("-V"), check=False, capture_output=True)
        return result.returncode == 0

    def is_inside_session(self) -> bool:
        return os.environ.get("TMUX") is not None

    def has_session(self, name: str) -> bool:
        self._ensure_tmux_available()
        result = subprocess.run(
            self._args("has-session", "-t", name),
            check=False,
            capture_output=True,
        )
        return result.returncode == 0

    def new_session(self, cfg: SessionConfig) -> SessionInfo:
        self._ensure_tmux_available()
        if self.has_session(cfg.session_name):
            raise SessionAlreadyExistsError(f"Session '{cfg.session_name}' already exists")
        assert_no_forbidden_agent_env(cfg.env, context="tmux session environment")

        args = self._args("new-session", "-d", "-s", cfg.session_name, "-P", "-F", "#D")
        if cfg.work_dir:
            args.extend(["-c", str(cfg.work_dir)])
        if cfg.window_name:
            args.extend(["-n", cfg.window_name])
        # Propagate env vars into the pane shell. Setting them on the
        # tmux client subprocess doesn't reach the pane (the daemon spawns
        # the shell), so we must use `-e KEY=VAL`.
        if cfg.env:
            for key, value in cfg.env.items():
                args.extend(["-e", f"{key}={value}"])

        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
        pane_id = result.stdout.strip()
        self._start_transcript_pipe(pane_id, cfg.session_name)

        return SessionInfo(
            session_name=cfg.session_name,
            pane_id=pane_id,
            created_at=datetime.now(timezone.utc),
        )

    def _start_transcript_pipe(self, pane_id: str, session_name: str) -> None:
        self._transcript_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._transcript_dir.chmod(0o700)
        safe_session = re.sub(r"[^A-Za-z0-9_.-]+", "_", session_name)
        safe_pane = re.sub(r"[^A-Za-z0-9_.-]+", "_", pane_id)
        path = self._transcript_dir / f"{safe_session}-{os.getpid()}-{safe_pane}.log"
        path.touch(mode=0o600, exist_ok=True)
        path.chmod(0o600)
        subprocess.run(
            self._args(
                "pipe-pane",
                "-t",
                pane_id,
                "-o",
                f"cat >> {shlex.quote(str(path))}",
            ),
            check=True,
        )
        self._transcript_paths[pane_id] = path

    def send_keys(
        self,
        target: str,
        keys: str,
        *,
        literal: bool = True,
        enter: bool = True,
    ) -> None:
        self._ensure_tmux_available()
        if literal and keys:
            self._paste_literal(target, keys)
        elif keys:
            args = self._args("send-keys", "-t", target)
            if literal:
                args.extend(["-l", keys])
            else:
                args.append(keys)
            subprocess.run(args, check=True)

        if enter:
            if literal and keys:
                # Claude/Codex redraw the input box after large pasted prompts.
                # Pressing Enter immediately after the paste can be swallowed by
                # that redraw, leaving "[Pasted text ...]" in the prompt.
                time.sleep(1.0)
            subprocess.run(self._args("send-keys", "-t", target, "Enter"), check=True)
            if literal and keys:
                self._confirm_literal_prompt_submitted(target, keys)

    def _paste_literal(self, target: str, text: str) -> None:
        buffer_name = f"doeff-agents-{os.getpid()}-{re.sub(r'[^A-Za-z0-9_]+', '_', target)}"
        # Buffer content streams through load-buffer's STDIN, never argv:
        # tmux's client-server protocol caps one command at ~16KB (imsg
        # framing), so argv-passed set-buffer dies with "command too long"
        # on large prompts (doeff-agentd oracle 33ab4bae).
        subprocess.run(
            self._args("load-buffer", "-b", buffer_name, "-"),
            input=text,
            encoding="utf-8",
            check=True,
        )
        try:
            # -p = bracketed paste. Without it the pasted newlines reach the
            # agent TUI as bare Enter presses and per-line submits are only
            # avoided by the TUI's timing-dependent burst heuristics — on a
            # cold start a multi-paragraph prompt splits into fragments the
            # agent acknowledges without executing (issue
            # agentd-codex-coldstart-paste-race, reproduced live even after
            # the readiness gate passed).
            subprocess.run(
                self._args("paste-buffer", "-p", "-b", buffer_name, "-t", target),
                check=True,
            )
        finally:
            subprocess.run(
                self._args("delete-buffer", "-b", buffer_name),
                check=False,
                capture_output=True,
            )

    def _confirm_literal_prompt_submitted(self, target: str, text: str) -> None:
        # Startup banners (e.g. usage-limit promos) can keep the agent input
        # box unresponsive well past the first Enter, so retry with escalating
        # waits. If the pasted prompt verifiably never submits, raise instead
        # of returning: a silent give-up here leaves the agent session idle
        # forever while the caller polls for a result that can never arrive.
        time.sleep(1.2)
        for wait in (1.0, 2.0, 4.0, 8.0, 15.0):
            output = self.capture_pane(target, 20)
            if not _output_has_unsubmitted_paste_input(output, text):
                return
            subprocess.run(self._args("send-keys", "-t", target, "Enter"), check=True)
            time.sleep(wait)
        output = self.capture_pane(target, 20)
        if _output_has_unsubmitted_paste_input(output, text):
            raise RuntimeError(
                f"pasted prompt was never submitted in tmux pane {target}: the "
                "agent input box still shows the pasted text after repeated "
                "Enter retries; refusing to leave a silently idle agent session"
            )

    def capture_pane(
        self,
        target: str,
        lines: int = 100,
        *,
        strip_ansi_codes: bool = True,
    ) -> str:
        self._ensure_tmux_available()
        result = subprocess.run(
            self._args("capture-pane", "-t", target, "-p", "-J", "-S", f"-{lines}"),
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
        output = result.stdout
        if strip_ansi_codes:
            output = strip_ansi(output)
        return output

    def capture_transcript(
        self,
        target: str,
        lines: int = 100,
        *,
        strip_ansi_codes: bool = True,
    ) -> str:
        path = self._transcript_paths.get(target)
        if path is None or not path.exists():
            return ""
        output = _tail_text_lines(path, lines)
        if strip_ansi_codes:
            output = strip_ansi(output)
        return output

    def kill_session(self, session: str) -> None:
        self._ensure_tmux_available()
        subprocess.run(self._args("kill-session", "-t", session), check=True)
        for pane_id, path in list(self._transcript_paths.items()):
            if session in path.name:
                self._transcript_paths.pop(pane_id, None)

    def attach_session(self, session: str) -> None:
        self._ensure_tmux_available()
        if self.is_inside_session():
            subprocess.run(self._args("switch-client", "-t", session), check=True)
        else:
            subprocess.run(self._args("attach-session", "-t", session), check=True)

    def list_sessions(self) -> list[str]:
        self._ensure_tmux_available()
        result = subprocess.run(
            self._args("list-sessions", "-F", "#S"),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if result.returncode != 0:
            if "no server running" in result.stderr:
                return []
            raise TmuxError(f"Failed to list sessions: {result.stderr}")
        return [session for session in result.stdout.strip().split("\n") if session]


class StableTmuxSessionBackend(TmuxSessionBackend):
    """Tmux backend that caches the first successful availability check."""

    def __init__(self, executable: str | os.PathLike[str] | None = None) -> None:
        super().__init__(executable=executable)
        self._availability_verified = False

    def _ensure_tmux_available(self) -> None:
        if self._availability_verified:
            return
        super()._ensure_tmux_available()
        self._availability_verified = True


def get_default_backend() -> TmuxSessionBackend:
    """Return a default tmux backend instance using `tmux` from PATH."""
    return TmuxSessionBackend()


def _output_has_unsubmitted_paste_input(output: str, sent_text: str | None = None) -> bool:
    last_prompt_line = ""
    prompt_index = -1
    lines = output.splitlines()[-20:]
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        # "❯"/"›" are the classic TUI input glyphs; "input:" is the
        # --ax-screen-reader rendering. A glyph-only scan misses the
        # ax-mode input box, so a stuck "[Pasted text ...]" there was
        # reported as submitted and Enter was never retried
        # (2026-07-09 live incident: both broker agents idled for hours).
        if stripped.startswith(("❯", "›", "input:")):  # noqa: RUF001
            last_prompt_line = stripped
            prompt_index = index
    if "[Pasted text" in last_prompt_line:
        return True
    if not sent_text or prompt_index < 0:
        return False
    prompt_region = _normalize_prompt_text("\n".join(lines[prompt_index:]))
    return any(
        fragment in prompt_region
        for fragment in _literal_prompt_fragments(sent_text)
    )


def _normalize_prompt_text(text: str) -> str:
    return " ".join(text.replace("\u00a0", " ").split())


def _literal_prompt_fragments(text: str) -> list[str]:
    normalized = _normalize_prompt_text(text)
    words = normalized.split()
    fragments: list[str] = []
    for start in range(max(len(words) - 3, 0)):
        fragment = " ".join(words[start : start + 4])
        if len(fragment) >= 24:
            fragments.append(fragment)
    if len(normalized) >= 24:
        fragments.append(normalized[:80])
        fragments.append(normalized[-80:])
    return fragments


def _tail_text_lines(path: Path, lines: int) -> str:
    if lines <= 0:
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    selected = text.splitlines()[-lines:]
    if not selected:
        return ""
    suffix = "\n" if text.endswith("\n") else ""
    return "\n".join(selected) + suffix


def is_tmux_available() -> bool:
    return get_default_backend().is_available()


def is_inside_tmux() -> bool:
    return get_default_backend().is_inside_session()


def has_session(name: str) -> bool:
    return get_default_backend().has_session(name)


def new_session(cfg: SessionConfig) -> SessionInfo:
    return get_default_backend().new_session(cfg)


def send_keys(target: str, keys: str, *, literal: bool = True, enter: bool = True) -> None:
    get_default_backend().send_keys(target, keys, literal=literal, enter=enter)


def capture_pane(target: str, lines: int = 100, *, strip_ansi_codes: bool = True) -> str:
    return get_default_backend().capture_pane(target, lines, strip_ansi_codes=strip_ansi_codes)


def kill_session(session: str) -> None:
    get_default_backend().kill_session(session)


def attach_session(session: str) -> None:
    get_default_backend().attach_session(session)


def list_sessions() -> list[str]:
    return get_default_backend().list_sessions()


__all__ = [
    "SessionAlreadyExistsError",
    "SessionConfig",
    "SessionInfo",
    "SessionNotFoundError",
    "StableTmuxSessionBackend",
    "TmuxError",
    "TmuxNotAvailableError",
    "TmuxSessionBackend",
    "attach_session",
    "capture_pane",
    "get_default_backend",
    "has_session",
    "is_inside_tmux",
    "is_tmux_available",
    "kill_session",
    "list_sessions",
    "new_session",
    "send_keys",
    "strip_ansi",
]
