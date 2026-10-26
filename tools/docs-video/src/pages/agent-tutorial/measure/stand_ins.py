# The stand-ins every run of this page's video uses, so the runs need no tmux, no Claude Code and no login:
#   ScriptedTerminal  a SessionBackend (doeff-agents' terminal seam) whose pane plays a scripted Claude Code: the
#                     shell, the ready screen once the launch command is typed, "esc to interrupt" while it works on
#                     the prompt, then its review and the shell prompt again (the scripted agent exits). With ask,
#                     it stops part way with a question ("Esc to cancel") until something is typed into it.
#   fake_world_root   the io_root (doeff-agents' I/O seam) answering the driver's effects from an in-memory world
#                     where `claude` is on PATH and ~/.claude.json carries a login. recording_root also prints each
#                     effect and its answer.
# doeff_agents._session's default io_root is pointed at fake_world_root, because session_scope and
# async_session_scope (Steps 2 to 5 of docs/19-agent-tutorial.md) take a backend but no io_root.
import json
import time
from datetime import datetime, timezone

import hy  # noqa: F401
import doeff_agents._session as session_module
from doeff_agents.io_fake import FakeIoWorld, with_fake_io
from doeff_agents.session_backend import SessionConfig, SessionInfo

from doeff import EffectBase, Resume, do, handler, run

HOME = "/home/dev"
READY = "\n".join(["[Screen Reader Mode: on via flag]", "Claude Code v2.1.209", "❯ ", "auto mode on (shift+tab to cycle)"])


def world() -> FakeIoWorld:
    return FakeIoWorld(
        which={"claude": "/usr/local/bin/claude"},
        files={f"{HOME}/.claude.json": json.dumps({"oauthAccount": {"emailAddress": "dev@example.com"}})},
        dirs=[HOME],
        home=HOME,
    )


def fake_world_root(program):
    return run(with_fake_io(world(), program))


@handler
@do(non_tail=True)
def recorder(effect: EffectBase, k):
    answer = yield effect  # re-performed: the next handler out (the in-memory world) answers it
    print(f"  effect {type(effect).__name__}{_fields(effect)} -> {answer!r}")
    return (yield Resume(k, answer))


def _fields(effect) -> str:
    names = [name for name in getattr(effect, "__dataclass_fields__", {})]
    return "(" + ", ".join(f"{getattr(effect, name)!r}" for name in names) + ")"


def recording_root(program):
    return run(with_fake_io(world(), recorder(program)))


session_module._default_io_root = lambda: fake_world_root


class ScriptedTerminal:
    """Panes that play a scripted Claude Code. work[file] is how many seconds the scripted agent takes on the prompt
    that names the file; review[file] is the review it prints before it exits; ask[file] = (seconds, question) makes
    it stop after that many seconds of work and wait for an answer."""

    def __init__(self, work: dict[str, float], review: dict[str, list[str]], ask: dict[str, tuple[float, str]] | None = None):
        self.work, self.review, self.ask = work, review, ask or {}
        self.panes: dict[str, dict] = {}
        self.killed: list[str] = []

    def is_available(self) -> bool:
        return True

    def is_inside_session(self) -> bool:
        return False

    def has_session(self, name: str) -> bool:
        return name in self.panes

    def new_session(self, cfg: SessionConfig) -> SessionInfo:
        self.panes[cfg.session_name] = {"sends": [], "prompt_at": None, "file": None, "asked_at": None}
        return SessionInfo(session_name=cfg.session_name, pane_id=cfg.session_name, created_at=datetime.now(timezone.utc))

    def send_keys(self, target: str, keys: str, *, literal: bool = True, enter: bool = True) -> None:
        pane = self.panes[target]
        pane["sends"].append(keys)
        if len(pane["sends"]) == 2:
            pane["prompt_at"] = time.monotonic()
            pane["file"] = next((name for name in self.work if name in keys), None)
        if len(pane["sends"]) == 3 and pane["asked_at"] is not None:
            # The answer: the work clock moves on from where the question stopped it.
            pane["prompt_at"] += time.monotonic() - pane["asked_at"]

    def capture_pane(self, target: str, lines: int = 100, *, strip_ansi_codes: bool = True) -> str:
        pane = self.panes[target]
        if not pane["sends"]:
            return "$ "
        if pane["prompt_at"] is None:
            return READY
        name = pane["file"]
        spent = time.monotonic() - pane["prompt_at"]
        asked = f"❯ Review the code in {name}"
        if name in self.ask and len(pane["sends"]) < 3 and spent >= self.ask[name][0]:
            if pane["asked_at"] is None:
                pane["asked_at"] = pane["prompt_at"] + self.ask[name][0]
            return "\n".join([asked, f"? {self.ask[name][1]}", "  1. Yes   2. No", "Esc to cancel"])
        if spent < self.work[name]:
            return "\n".join([asked, f"✻ Reviewing {name}… ({spent:.0f}s · esc to interrupt)", "auto mode on (shift+tab to cycle)"])
        return "\n".join([asked, f"⏺ Review of {name}", *[f"  {line}" for line in self.review[name]], "$ "])

    def capture_transcript(self, target: str, lines: int = 100, *, strip_ansi_codes: bool = True) -> str:
        return self.capture_pane(target, lines)

    def kill_session(self, session: str) -> None:
        self.killed.append(session)
        self.panes.pop(session, None)

    def attach_session(self, session: str) -> None:
        raise NotImplementedError

    def list_sessions(self) -> list[str]:
        return sorted(self.panes)
