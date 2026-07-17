"""Session registration must precede the ready gate.

Regression suite for issue agentd-session-registration-after-ready-gate
(2026-07-14): every launch path created the physical tmux session, then sat
in the readiness wait (up to 120s), and only recorded the session AFTER the
gate. During that window the session physically existed while no externally
observable record did — an external monitor assuming a sub-minute handshake
(mediagen engine's 60s orphan reconciler) classified the launch as an
orphan. The contract under test:

1. Registration is bookkeeping independent of TUI readiness: the BOOTING
   row is persisted to the session repository immediately after
   ``new_session``, BEFORE the ready wait starts.
2. The ready gate guards ONLY prompt delivery.
3. On ready timeout the row transitions to a terminal FAILED state — a
   lifecycle transition instead of the old "kill + never record" leak
   avoidance — so no BOOTING row is ever left behind and the
   "everything that exists is observable" principle holds on both the
   success and the failure path.

Observation style mirrors test_ready_gate.py: the REAL handler runs against
a scripted backend. The repository is probed from inside the transport
(``capture_pane``) — i.e. at the exact moments the launch path polls the
pane during its gate loops — not by mocking call order.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from doeff_agents.adapters.base import AgentType  # noqa: E402
from doeff_agents.effects.agent import (  # noqa: E402
    AgentReadyTimeoutError as EffectsAgentReadyTimeoutError,
)
from doeff_agents.effects.agent import (  # noqa: E402
    ClaudeLaunchEffect,
    LaunchEffect,
)
from doeff_agents.monitor import SessionStatus  # noqa: E402
from doeff_agents.runtime import ClaudeRuntimePolicy, CodexRuntimePolicy  # noqa: E402
from doeff_agents.session_store import InMemoryAgentSessionRepository  # noqa: E402
from test_ready_gate import (  # noqa: E402
    CLAUDE_READY,
    CODEX_MCP_BOOT,
    CODEX_READY,
    PROMPT,
    ReadyFakeClaudeAdapter,
    ReadyFakeCodexAdapter,
    ScriptedBackend,
)


class RegistrationProbeBackend(ScriptedBackend):
    """ScriptedBackend that snapshots the repository on every pane capture.

    ``capture_pane`` is called only by the launch path's gate loops
    (onboarding dismissal / readiness polling), all of which run after
    ``new_session`` and before prompt delivery — so the recorded statuses
    are exactly "what an external observer sees while the launch is inside
    its ready wait".
    """

    def __init__(
        self,
        frames: list[str],
        repository: InMemoryAgentSessionRepository,
        watched_session: str,
    ) -> None:
        super().__init__(frames)
        self._repository = repository
        self._watched_session = watched_session
        self.observed_statuses: list[str | None] = []

    def capture_pane(
        self,
        target: str,
        lines: int = 100,
        *,
        strip_ansi_codes: bool = True,
    ) -> str:
        snapshot = self._repository.get_session(self._watched_session)
        self.observed_statuses.append(
            None if snapshot is None else snapshot.status.value
        )
        return super().capture_pane(target, lines, strip_ansi_codes=strip_ansi_codes)


def _handler(backend: ScriptedBackend, repository, tmp_path: Path):
    from doeff_agents.handlers.production import TmuxAgentHandler

    return TmuxAgentHandler(
        backend=backend,
        session_repository=repository,
        codex_runtime_policy=CodexRuntimePolicy(codex_home=tmp_path / "codex-home"),
        claude_runtime_policy=ClaudeRuntimePolicy(agent_home=tmp_path / "agent-home"),
    )


def _booting_rows(repository: InMemoryAgentSessionRepository):
    return [
        snapshot
        for snapshot in repository.list_sessions()
        if snapshot.status is SessionStatus.BOOTING
    ]


class KillFailingBackend(RegistrationProbeBackend):
    """Backend whose kill_session always fails.

    Models a tmux server that died (or a pane torn down out of band)
    between the launch and its cleanup — the terminal-first contract
    (adopted from PR #542's review) requires the FAILED row to be
    persisted BEFORE any cleanup attempt, so a failing cleanup can
    neither leave a BOOTING row behind nor mask the launch error.
    """

    def kill_session(self, session: str) -> None:
        raise RuntimeError(f"tmux kill-session failed for {session}")


# =============================================================================
# handle_launch (LaunchEffect path)
# =============================================================================


def test_handle_launch_registers_booting_row_before_ready_wait(
    monkeypatch, tmp_path: Path
) -> None:
    frames = ["$ ", "$ codex --yolo", CODEX_MCP_BOOT, CODEX_READY]
    repository = InMemoryAgentSessionRepository()
    backend = RegistrationProbeBackend(frames, repository, "codex-reg")
    monkeypatch.setattr(
        "doeff_agents.handlers.production.get_adapter",
        lambda _agent_type: ReadyFakeCodexAdapter(),
    )
    handler = _handler(backend, repository, tmp_path)

    handler.handle_launch(
        LaunchEffect(
            session_name="codex-reg",
            agent_type=AgentType.CODEX,
            work_dir=tmp_path / "ws",
            prompt=PROMPT,
            ready_timeout=5.0,
        )
    )

    assert backend.observed_statuses, "the launch path never polled the pane"
    assert backend.observed_statuses[0] == "booting", (
        "the BOOTING row must be externally observable from the FIRST pane "
        "poll of the launch gate — registration is bookkeeping and must not "
        f"wait for TUI readiness (observed: {backend.observed_statuses[0]!r})"
    )
    assert all(status == "booting" for status in backend.observed_statuses)

    row = repository.get_session("codex-reg")
    assert row is not None
    assert row.status is SessionStatus.BOOTING


def test_handle_launch_ready_timeout_transitions_row_to_failed(
    monkeypatch, tmp_path: Path
) -> None:
    repository = InMemoryAgentSessionRepository()
    backend = RegistrationProbeBackend(
        ["$ ", "$ codex --yolo"], repository, "codex-cold-reg"
    )
    monkeypatch.setattr(
        "doeff_agents.handlers.production.get_adapter",
        lambda _agent_type: ReadyFakeCodexAdapter(),
    )
    handler = _handler(backend, repository, tmp_path)

    with pytest.raises(EffectsAgentReadyTimeoutError):
        handler.handle_launch(
            LaunchEffect(
                session_name="codex-cold-reg",
                agent_type=AgentType.CODEX,
                work_dir=tmp_path / "ws",
                prompt=PROMPT,
                ready_timeout=0.5,
            )
        )

    # The row was observable (BOOTING) while the gate was still polling.
    assert backend.observed_statuses
    assert backend.observed_statuses[0] == "booting"

    # Ready timeout is a lifecycle transition, not a leak: the row survives
    # in a terminal FAILED state with the failure evidence attached.
    row = repository.get_session("codex-cold-reg")
    assert row is not None, "ready timeout must not erase the session record"
    assert row.status is SessionStatus.FAILED
    assert row.finished_at is not None
    assert not _booting_rows(repository), "no BOOTING row may be left behind"

    # Existing #531 guarantees are preserved: prompt undelivered, session killed.
    assert PROMPT not in backend.sent_texts()
    assert "codex-cold-reg" in backend.killed
    # Cleanup succeeded, recorded as cleaned_at (terminal-first contract).
    assert row.cleaned_at is not None


# =============================================================================
# handle_claude_launch (ClaudeLaunchEffect path)
# =============================================================================


def test_handle_claude_launch_registers_booting_row_before_ready_wait(
    monkeypatch, tmp_path: Path
) -> None:
    frames = ["$ ", "$ claude --ax-screen-reader", CLAUDE_READY]
    repository = InMemoryAgentSessionRepository()
    backend = RegistrationProbeBackend(frames, repository, "claude-reg")
    monkeypatch.setattr(
        "doeff_agents.handlers.production.get_adapter",
        lambda _agent_type: ReadyFakeClaudeAdapter(),
    )
    handler = _handler(backend, repository, tmp_path)

    handler.handle_claude_launch(
        ClaudeLaunchEffect(
            session_name="claude-reg",
            work_dir=tmp_path / "ws",
            prompt=PROMPT,
            ready_timeout=5.0,
        )
    )

    assert backend.observed_statuses, "the launch path never polled the pane"
    assert backend.observed_statuses[0] == "booting"
    assert all(status == "booting" for status in backend.observed_statuses)

    row = repository.get_session("claude-reg")
    assert row is not None
    assert row.status is SessionStatus.BOOTING


def test_handle_claude_launch_ready_timeout_transitions_row_to_failed(
    monkeypatch, tmp_path: Path
) -> None:
    repository = InMemoryAgentSessionRepository()
    backend = RegistrationProbeBackend(
        ["$ ", "$ claude --ax-screen-reader"], repository, "claude-cold-reg"
    )
    monkeypatch.setattr(
        "doeff_agents.handlers.production.get_adapter",
        lambda _agent_type: ReadyFakeClaudeAdapter(),
    )
    handler = _handler(backend, repository, tmp_path)

    with pytest.raises(EffectsAgentReadyTimeoutError):
        handler.handle_claude_launch(
            ClaudeLaunchEffect(
                session_name="claude-cold-reg",
                work_dir=tmp_path / "ws",
                prompt=PROMPT,
                ready_timeout=0.5,
            )
        )

    assert backend.observed_statuses
    assert backend.observed_statuses[0] == "booting"

    row = repository.get_session("claude-cold-reg")
    assert row is not None, "ready timeout must not erase the session record"
    assert row.status is SessionStatus.FAILED
    assert row.finished_at is not None
    assert not _booting_rows(repository), "no BOOTING row may be left behind"

    assert PROMPT not in backend.sent_texts()
    assert "claude-cold-reg" in backend.killed
    assert row.cleaned_at is not None


# =============================================================================
# terminal-first: cleanup failure must not skip terminalization
# (order adopted from PR #542's review)
# =============================================================================


def test_handle_launch_terminalizes_before_failed_tmux_cleanup(
    monkeypatch, tmp_path: Path
) -> None:
    repository = InMemoryAgentSessionRepository()
    backend = KillFailingBackend(
        ["$ ", "$ codex --yolo"], repository, "codex-kill-broken"
    )
    monkeypatch.setattr(
        "doeff_agents.handlers.production.get_adapter",
        lambda _agent_type: ReadyFakeCodexAdapter(),
    )
    handler = _handler(backend, repository, tmp_path)

    # The typed ready-timeout error must survive the failing cleanup — the
    # kill error is logged, never allowed to mask the launch failure.
    with pytest.raises(EffectsAgentReadyTimeoutError):
        handler.handle_launch(
            LaunchEffect(
                session_name="codex-kill-broken",
                agent_type=AgentType.CODEX,
                work_dir=tmp_path / "ws",
                prompt=PROMPT,
                ready_timeout=0.5,
            )
        )

    # Terminal-first: the FAILED row was persisted BEFORE the cleanup
    # attempt, so the failing kill cannot leave a BOOTING row behind.
    row = repository.get_session("codex-kill-broken")
    assert row is not None, "failed cleanup must not erase the session record"
    assert row.status is SessionStatus.FAILED
    assert row.finished_at is not None
    assert not _booting_rows(repository), "no BOOTING row may be left behind"
    # Cleanup failed: expressed as the absence of cleaned_at, with the row
    # already terminal.
    assert row.cleaned_at is None
    assert PROMPT not in backend.sent_texts()
