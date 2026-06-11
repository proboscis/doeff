"""Tests for persistent agent session state APIs."""

from __future__ import annotations

import sys
import time
from pathlib import Path

from doeff import WithHandler, do, run

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from doeff_agents import (
    AgentType,
    CleanupAgentSession,
    GetAgentSession,
    Launch,
    LaunchConfig,
    ListAgentSessions,
    MockSessionScript,
    ObserveAgentSession,
    SessionStatus,
    configure_mock_session,
    mock_agent_handlers,
    wait_agent_session,
)


def _install_handlers(handlers, program):
    wrapped = program
    for handler in reversed(handlers):
        wrapped = WithHandler(handler, wrapped)
    return wrapped


@do
def _launch_and_query_session(session_name: str, config: LaunchConfig):
    handle = yield Launch(
        session_name,
        agent_type=config.agent_type,
        work_dir=config.work_dir,
        prompt=config.prompt,
    )
    snapshot = yield GetAgentSession(session_name)
    sessions = yield ListAgentSessions()
    return handle, snapshot, sessions


@do
def _observe_and_cleanup_session(session_name: str, config: LaunchConfig):
    yield Launch(
        session_name,
        agent_type=config.agent_type,
        work_dir=config.work_dir,
        prompt=config.prompt,
    )
    first = yield ObserveAgentSession(session_name)
    second = yield ObserveAgentSession(session_name)
    cleaned = yield CleanupAgentSession(session_name)
    return first, second, cleaned


@do
def _launch_and_wait_session(session_name: str, config: LaunchConfig):
    yield Launch(
        session_name,
        agent_type=config.agent_type,
        work_dir=config.work_dir,
        prompt=config.prompt,
    )
    return (yield from wait_agent_session(session_name, poll_interval=5.0))


def _config(tmp_path: Path) -> LaunchConfig:
    return LaunchConfig(
        agent_type=AgentType.CLAUDE,
        work_dir=tmp_path,
        prompt="work on the issue",
    )


def test_launch_records_queryable_session_state(tmp_path: Path) -> None:
    session_name = f"state-query-{time.time_ns()}"

    handle, snapshot, sessions = run(
        _install_handlers(
            mock_agent_handlers(),
            _launch_and_query_session(session_name, _config(tmp_path)),
        )
    )

    assert snapshot is not None
    assert snapshot.session_id == session_name
    assert snapshot.session_name == handle.session_id
    assert not hasattr(handle, "pane_id")
    assert snapshot.agent_type == AgentType.CLAUDE
    assert snapshot.work_dir == tmp_path
    assert snapshot.status == SessionStatus.BOOTING
    assert session_name in {session.session_id for session in sessions}


def test_observe_and_cleanup_are_session_id_based(tmp_path: Path) -> None:
    session_name = f"state-observe-{time.time_ns()}"
    configure_mock_session(
        session_name,
        MockSessionScript(
            observations=[
                (SessionStatus.RUNNING, "working"),
                (SessionStatus.DONE, "done"),
            ]
        ),
    )

    first, second, cleaned = run(
        _install_handlers(
            mock_agent_handlers(),
            _observe_and_cleanup_session(session_name, _config(tmp_path)),
        )
    )

    assert first.status == SessionStatus.RUNNING
    assert first.output_snippet == "working"
    assert second.status == SessionStatus.DONE
    assert second.finished_at is not None
    assert cleaned.status == SessionStatus.STOPPED
    assert cleaned.cleaned_at is not None


def test_wait_agent_session_uses_persisted_session_id(tmp_path: Path) -> None:
    session_name = f"state-wait-{time.time_ns()}"
    configure_mock_session(
        session_name,
        MockSessionScript(
            observations=[
                (SessionStatus.RUNNING, "working"),
                (SessionStatus.DONE, "done"),
            ]
        ),
    )

    snapshot = run(
        _install_handlers(
            mock_agent_handlers(),
            _launch_and_wait_session(session_name, _config(tmp_path)),
        )
    )

    assert snapshot.session_id == session_name
    assert snapshot.status == SessionStatus.STOPPED
    assert snapshot.cleaned_at is not None
