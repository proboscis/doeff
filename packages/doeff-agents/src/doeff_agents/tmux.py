"""Tmux session operations with typed errors and pane tracking.

段 7 lane 7c(agora-redesign・決定 1.3): argv の組み立て・出力の読み・
再送するかの判断はこの module に残り、子 process / file / 時計に触る 1 行は
`doeff_agents.io_effects` の要求になった。実行する家は本番
`doeff_agents.io_handlers` と検 `doeff_agents.io_fake` の 2 つで、どちらを
当てるかは composition root(backend を組む側が渡す ``io_root``)が選ぶ。

module 下端の ``*_program`` は effect で書かれた呼び手がそのまま bind できる
形で、``TmuxSessionBackend`` はそれを同期の ``SessionBackend`` 契約へ落とす
composition root である。
"""

import os
import posixpath
import re
import shlex
from datetime import datetime, timezone
from pathlib import Path

import hy  # noqa: F401  # .hy import hook — the I/O effect vocabulary is a Hy module
from doeff import do

from doeff_agents.io_effects import (
    ProcessOutcome,
    env_value,
    make_dirs,
    process_id,
    read_text,
    run_process,
    sleep,
    temp_root,
    touch_file,
)

from doeff_agents.io_root import (
    IoGenerator,
    IoRoot,
    as_bool,
    as_int,
    as_optional_str,
    as_process_outcome,
    as_str,
    as_str_tuple,
)

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

TRANSCRIPT_DIR_NAME = "doeff-agents-tmux-transcripts"
TRANSCRIPT_DIR_MODE = 0o700
TRANSCRIPT_FILE_MODE = 0o600
# Claude/Codex redraw the input box after large pasted prompts. Pressing Enter
# immediately after the paste can be swallowed by that redraw, leaving
# "[Pasted text ...]" in the prompt.
PASTE_SETTLE_SECONDS = 1.0
CONFIRM_INITIAL_SECONDS = 1.2
CONFIRM_RETRY_WAITS = (1.0, 2.0, 4.0, 8.0, 15.0)
CONFIRM_CAPTURE_LINES = 20


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text."""
    return ANSI_PATTERN.sub("", text)


# -- Pure judgment: argv, names, output reading -----------------------------


def tmux_argv(executable: str, *args: str) -> tuple[str, ...]:
    """The argv for one tmux command."""
    return (executable, *args)


def new_session_argv(executable: str, cfg: SessionConfig) -> tuple[str, ...]:
    """The argv that creates a detached session and prints its pane id."""
    args = [executable, "new-session", "-d", "-s", cfg.session_name, "-P", "-F", "#D"]
    if cfg.work_dir:
        args.extend(["-c", str(cfg.work_dir)])
    if cfg.window_name:
        args.extend(["-n", cfg.window_name])
    # Propagate env vars into the pane shell. Setting them on the tmux client
    # subprocess doesn't reach the pane (the daemon spawns the shell), so we
    # must use `-e KEY=VAL`.
    if cfg.env:
        for key, value in cfg.env.items():
            args.extend(["-e", f"{key}={value}"])
    return tuple(args)


def safe_name_part(value: str) -> str:
    """A file-name-safe rendering of a session or pane name."""
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


def transcript_file_name(session_name: str, pid: int, pane_id: str) -> str:
    """The transcript log file name for one pane of one process."""
    return f"{safe_name_part(session_name)}-{pid}-{safe_name_part(pane_id)}.log"


def paste_buffer_name(pid: int, target: str) -> str:
    """The tmux buffer name used to stream one literal paste."""
    return f"doeff-agents-{pid}-{re.sub(r'[^A-Za-z0-9_]+', '_', target)}"


def parse_session_list(outcome: ProcessOutcome) -> list[str]:
    """Pure judgment: session names from `tmux list-sessions`, or [] with no server."""
    if outcome.exit_code != 0:
        if "no server running" in outcome.stderr:
            return []
        raise TmuxError(f"Failed to list sessions: {outcome.stderr}")
    return [session for session in outcome.stdout.strip().split("\n") if session]


def tail_text_lines(text: str, lines: int) -> str:
    """Pure judgment: the last ``lines`` lines of ``text`` keeping its final newline."""
    if lines <= 0:
        return ""
    selected = text.splitlines()[-lines:]
    if not selected:
        return ""
    suffix = "\n" if text.endswith("\n") else ""
    return "\n".join(selected) + suffix


def require_success(outcome: ProcessOutcome, what: str) -> ProcessOutcome:
    """Pure judgment: a non-zero tmux command is a typed failure, never silence."""
    if outcome.exit_code != 0:
        raise TmuxError(f"tmux {what} failed ({outcome.exit_code}): {outcome.stderr.strip()}")
    return outcome


# -- Programs: judgment plus I/O requests ------------------------------------


@do
def tmux_available_program(executable: str) -> IoGenerator[bool]:
    """Program answering whether this tmux executable answers `-V`."""
    outcome = as_process_outcome((yield run_process(tmux_argv(executable, "-V"))))
    return outcome.exit_code == 0


@do
def inside_session_program() -> IoGenerator[bool]:
    """Program answering whether the caller already runs inside tmux."""
    value = as_optional_str((yield env_value("TMUX")))
    return value is not None


@do
def has_session_program(executable: str, name: str) -> IoGenerator[bool]:
    """Program answering whether a session with this name exists."""
    outcome = as_process_outcome(
        (yield run_process(tmux_argv(executable, "has-session", "-t", name)))
    )
    return outcome.exit_code == 0


@do
def transcript_dir_program() -> IoGenerator[str]:
    """Program answering where pane transcripts are recorded."""
    root = as_str((yield temp_root()))
    return posixpath.join(root, TRANSCRIPT_DIR_NAME)


@do
def start_transcript_pipe_program(executable: str, pane_id: str, session_name: str) -> IoGenerator[str]:
    """Program wiring `pipe-pane` into a private transcript log; returns its path."""
    directory = as_str((yield transcript_dir_program()))
    yield make_dirs(directory, TRANSCRIPT_DIR_MODE)
    pid = as_int((yield process_id()))
    path = posixpath.join(directory, transcript_file_name(session_name, pid, pane_id))
    yield touch_file(path, TRANSCRIPT_FILE_MODE)
    outcome = as_process_outcome(
        (
            yield run_process(
                tmux_argv(
                    executable, "pipe-pane", "-t", pane_id, "-o", f"cat >> {shlex.quote(path)}"
                )
            )
        )
    )
    require_success(outcome, "pipe-pane")
    return path


@do
def new_session_program(
    executable: str, cfg: SessionConfig, created_at: datetime
) -> IoGenerator[tuple[SessionInfo, str]]:
    """Program creating a detached session; returns (SessionInfo, transcript path).

    ``created_at`` は composition root(``TmuxSessionBackend``)が読んだ壁時計を受け取るだけ
    — program は時計に触らない(module 上端の規律「時計に触る 1 行は composition root か
    io_effects の要求」・rule ``doeff-no-datetime-now-in-do``)。同じ cfg と同じ時刻なら
    同じ ``SessionInfo`` が出る。
    """
    assert_no_forbidden_agent_env(cfg.env, context="tmux session environment")
    outcome = as_process_outcome((yield run_process(new_session_argv(executable, cfg))))
    require_success(outcome, "new-session")
    pane_id = outcome.stdout.strip()
    transcript_path = as_str(
        (yield start_transcript_pipe_program(executable, pane_id, cfg.session_name))
    )
    return (
        SessionInfo(
            session_name=cfg.session_name,
            pane_id=pane_id,
            created_at=created_at,
        ),
        transcript_path,
    )


@do
def capture_pane_program(
    executable: str, target: str, lines: int = 100, *, strip_ansi_codes: bool = True
) -> IoGenerator[str]:
    """Program reading the visible pane text."""
    outcome = as_process_outcome(
        (
            yield run_process(
                tmux_argv(executable, "capture-pane", "-t", target, "-p", "-J", "-S", f"-{lines}")
            )
        )
    )
    require_success(outcome, "capture-pane")
    output = outcome.stdout
    return strip_ansi(output) if strip_ansi_codes else output


@do
def paste_literal_program(executable: str, target: str, text: str) -> IoGenerator[None]:
    """Program pasting literal text through a tmux buffer (never through argv).

    Buffer content streams through load-buffer's STDIN, never argv: tmux's
    client-server protocol caps one command at ~16KB (imsg framing), so
    argv-passed set-buffer dies with "command too long" on large prompts
    (doeff-agentd oracle 33ab4bae).
    """
    pid = as_int((yield process_id()))
    buffer_name = paste_buffer_name(pid, target)
    outcome = as_process_outcome(
        (
            yield run_process(
                tmux_argv(executable, "load-buffer", "-b", buffer_name, "-"), stdin=text
            )
        )
    )
    require_success(outcome, "load-buffer")
    try:
        # -p = bracketed paste. Without it the pasted newlines reach the agent
        # TUI as bare Enter presses and per-line submits are only avoided by
        # the TUI's timing-dependent burst heuristics — on a cold start a
        # multi-paragraph prompt splits into fragments the agent acknowledges
        # without executing (issue agentd-codex-coldstart-paste-race,
        # reproduced live even after the readiness gate passed).
        pasted = as_process_outcome(
            (
                yield run_process(
                    tmux_argv(executable, "paste-buffer", "-p", "-b", buffer_name, "-t", target)
                )
            )
        )
        require_success(pasted, "paste-buffer")
    finally:
        yield run_process(tmux_argv(executable, "delete-buffer", "-b", buffer_name))
    return None


@do
def confirm_literal_prompt_submitted_program(executable: str, target: str, text: str) -> IoGenerator[None]:
    """Program retrying Enter until the pasted prompt verifiably left the composer.

    Startup banners (e.g. usage-limit promos) can keep the agent input box
    unresponsive well past the first Enter, so retry with escalating waits. If
    the pasted prompt verifiably never submits, raise instead of returning: a
    silent give-up here leaves the agent session idle forever while the caller
    polls for a result that can never arrive.
    """
    yield sleep(CONFIRM_INITIAL_SECONDS)
    for wait in CONFIRM_RETRY_WAITS:
        output = as_str((yield capture_pane_program(executable, target, CONFIRM_CAPTURE_LINES)))
        if not output_has_unsubmitted_paste_input(output, text):
            return None
        enter = as_process_outcome(
            (yield run_process(tmux_argv(executable, "send-keys", "-t", target, "Enter")))
        )
        require_success(enter, "send-keys Enter")
        yield sleep(wait)
    output = as_str((yield capture_pane_program(executable, target, CONFIRM_CAPTURE_LINES)))
    if output_has_unsubmitted_paste_input(output, text):
        raise RuntimeError(
            f"pasted prompt was never submitted in tmux pane {target}: the "
            "agent input box still shows the pasted text after repeated "
            "Enter retries; refusing to leave a silently idle agent session"
        )
    return None


@do
def send_keys_program(
    executable: str, target: str, keys: str, *, literal: bool = True, enter: bool = True
) -> IoGenerator[None]:
    """Program delivering keys (or a literal paste) into a pane."""
    if literal and keys:
        yield paste_literal_program(executable, target, keys)
    elif keys:
        args = [executable, "send-keys", "-t", target]
        if literal:
            args.extend(["-l", keys])
        else:
            args.append(keys)
        outcome = as_process_outcome((yield run_process(tuple(args))))
        require_success(outcome, "send-keys")

    if enter:
        if literal and keys:
            yield sleep(PASTE_SETTLE_SECONDS)
        outcome = as_process_outcome(
            (yield run_process(tmux_argv(executable, "send-keys", "-t", target, "Enter")))
        )
        require_success(outcome, "send-keys Enter")
        if literal and keys:
            yield confirm_literal_prompt_submitted_program(executable, target, keys)
    return None


@do
def capture_transcript_program(path: str | None, lines: int, *, strip_ansi_codes: bool = True) -> IoGenerator[str]:
    """Program reading the tail of a pane transcript log."""
    if path is None:
        return ""
    text = as_optional_str((yield read_text(path)))
    if text is None:
        return ""
    output = tail_text_lines(text, lines)
    return strip_ansi(output) if strip_ansi_codes else output


@do
def kill_session_program(executable: str, session: str) -> IoGenerator[None]:
    """Program killing a session."""
    outcome = as_process_outcome(
        (yield run_process(tmux_argv(executable, "kill-session", "-t", session)))
    )
    require_success(outcome, "kill-session")
    return None


@do
def attach_session_program(executable: str, session: str) -> IoGenerator[None]:
    """Program attaching to a session, switching the client when already inside."""
    inside = as_bool((yield inside_session_program()))
    verb = "switch-client" if inside else "attach-session"
    outcome = as_process_outcome((yield run_process(tmux_argv(executable, verb, "-t", session))))
    require_success(outcome, verb)
    return None


@do
def list_sessions_program(executable: str) -> IoGenerator[list[str]]:
    """Program listing session names."""
    outcome = as_process_outcome(
        (yield run_process(tmux_argv(executable, "list-sessions", "-F", "#S")))
    )
    return parse_session_list(outcome)


# -- Composition root: the synchronous SessionBackend contract ---------------


class TmuxSessionBackend(SessionBackend):
    """Session backend implementation backed by tmux.

    The backend owns no raw I/O: every operation is a program from this module
    run through ``io_root``, which the constructing site chooses (production
    handler by default, the in-memory fake in tests).
    """

    def __init__(
        self,
        executable: str | os.PathLike[str] | None = None,
        *,
        io_root: IoRoot | None = None,
    ) -> None:
        self.executable = str(executable or "tmux")
        self._io: IoRoot = io_root if io_root is not None else _default_io_root()
        self._transcript_paths: dict[str, str] = {}

    def _ensure_tmux_available(self) -> None:
        if not self.is_available():
            raise TmuxNotAvailableError(f"tmux is not available: {self.executable}")

    def is_available(self) -> bool:
        return as_bool(self._io(tmux_available_program(self.executable)))

    def is_inside_session(self) -> bool:
        return as_bool(self._io(inside_session_program()))

    def has_session(self, name: str) -> bool:
        self._ensure_tmux_available()
        return as_bool(self._io(has_session_program(self.executable, name)))

    def new_session(self, cfg: SessionConfig) -> SessionInfo:
        self._ensure_tmux_available()
        if self.has_session(cfg.session_name):
            raise SessionAlreadyExistsError(f"Session '{cfg.session_name}' already exists")
        # 壁時計を読むのは composition root のこの 1 点 — program は受け取るだけ。
        created = self._io(new_session_program(self.executable, cfg, datetime.now(timezone.utc)))
        if not isinstance(created, tuple) or len(created) != 2:
            raise TmuxError(f"new-session の答えの形が違う: {created!r}")
        info, transcript_path = created
        if not isinstance(info, SessionInfo):
            raise TmuxError(f"new-session が SessionInfo を返していない: {info!r}")
        self._transcript_paths[info.pane_id] = as_str(transcript_path)
        return info

    def send_keys(
        self,
        target: str,
        keys: str,
        *,
        literal: bool = True,
        enter: bool = True,
    ) -> None:
        self._ensure_tmux_available()
        self._io(
            send_keys_program(self.executable, target, keys, literal=literal, enter=enter)
        )

    def capture_pane(
        self,
        target: str,
        lines: int = 100,
        *,
        strip_ansi_codes: bool = True,
    ) -> str:
        self._ensure_tmux_available()
        return as_str(
            self._io(
                capture_pane_program(
                    self.executable, target, lines, strip_ansi_codes=strip_ansi_codes
                )
            )
        )

    def capture_transcript(
        self,
        target: str,
        lines: int = 100,
        *,
        strip_ansi_codes: bool = True,
    ) -> str:
        return as_str(
            self._io(
                capture_transcript_program(
                    self._transcript_paths.get(target), lines, strip_ansi_codes=strip_ansi_codes
                )
            )
        )

    def kill_session(self, session: str) -> None:
        self._ensure_tmux_available()
        self._io(kill_session_program(self.executable, session))
        for pane_id, path in list(self._transcript_paths.items()):
            if session in posixpath.basename(path):
                self._transcript_paths.pop(pane_id, None)

    def attach_session(self, session: str) -> None:
        self._ensure_tmux_available()
        self._io(attach_session_program(self.executable, session))

    def list_sessions(self) -> list[str]:
        self._ensure_tmux_available()
        listed = self._io(list_sessions_program(self.executable))
        if not isinstance(listed, list):
            raise TmuxError(f"list-sessions の答えの形が違う: {listed!r}")
        return list(as_str_tuple(tuple(listed)))


class StableTmuxSessionBackend(TmuxSessionBackend):
    """Tmux backend that caches the first successful availability check."""

    def __init__(
        self,
        executable: str | os.PathLike[str] | None = None,
        *,
        io_root: IoRoot | None = None,
    ) -> None:
        super().__init__(executable=executable, io_root=io_root)
        self._availability_verified = False

    def _ensure_tmux_available(self) -> None:
        if self._availability_verified:
            return
        super()._ensure_tmux_available()
        self._availability_verified = True


def _default_io_root() -> IoRoot:
    from .io_handlers import run_driver_io

    return run_driver_io


def get_default_backend() -> TmuxSessionBackend:
    """Return a default tmux backend instance using `tmux` from PATH."""
    return TmuxSessionBackend()


def output_has_unsubmitted_paste_input(output: str, sent_text: str | None = None) -> bool:
    """Pure judgment: is a pasted prompt still sitting in the agent's composer?"""
    prompt_index = -1
    lines = output.splitlines()[-20:]
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        # "❯"/"›" are the classic TUI input glyphs; "input:" is the
        # --ax-screen-reader rendering. A glyph-only scan misses the
        # ax-mode input box, so a stuck "[Pasted text ...]" there was
        # reported as submitted and Enter was never retried
        # (2026-07-09 live incident: both broker agents idled for hours).
        if stripped.startswith(("❯", "›", "input:")):
            prompt_index = index
    if prompt_index >= 0:
        # issue #568 (ADR-DOE-AGENTS-010 R1): attachment chips ([Image #N],
        # collapsed paste chips) render on the lines BELOW the prompt glyph,
        # so the whole composer region (last prompt line onward) must be
        # scanned — a prompt-line-only scan reported the 2026-07-28 live
        # wedge (empty prompt + "  [Image #150]") as submitted. History above
        # the last prompt line stays out of scope.
        composer_region = "\n".join(lines[prompt_index:])
        if any(
            marker in composer_region
            for marker in (
                "[Pasted text",
                "[Pasted Content",
                "[Image #",
                "Press up to edit queued messages",
            )
        ):
            return True
    if not sent_text or prompt_index < 0:
        return False
    prompt_region = _normalize_prompt_text("\n".join(lines[prompt_index:]))
    return any(
        fragment in prompt_region
        for fragment in _literal_prompt_fragments(sent_text)
    )


#: 旧名(内部名)を読んでいる検・呼び手のための同名束縛。判断は 1 点。
_output_has_unsubmitted_paste_input = output_has_unsubmitted_paste_input


def _normalize_prompt_text(text: str) -> str:
    return " ".join(text.replace(" ", " ").split())


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
    "attach_session_program",
    "capture_pane",
    "capture_pane_program",
    "capture_transcript_program",
    "get_default_backend",
    "has_session",
    "has_session_program",
    "inside_session_program",
    "is_inside_tmux",
    "is_tmux_available",
    "kill_session",
    "kill_session_program",
    "list_sessions",
    "list_sessions_program",
    "new_session",
    "new_session_program",
    "output_has_unsubmitted_paste_input",
    "parse_session_list",
    "send_keys",
    "send_keys_program",
    "strip_ansi",
    "tail_text_lines",
    "tmux_available_program",
]
