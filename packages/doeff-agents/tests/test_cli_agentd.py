"""CLI tests for the agentd-backed monitoring commands."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from doeff_agents import AgentdUnavailableError, AgentSessionSnapshot, AgentType, SessionStatus
from doeff_agents import cli as cli_module
from doeff_agents.agentd_client import AgentdSessionList, AgentdSessionParseWarning
from doeff_agents.cli import cli


class FakeAgentdClient:
    def __init__(
        self,
        snapshots: list[AgentSessionSnapshot],
        *,
        warnings: tuple[AgentdSessionParseWarning, ...] = (),
    ) -> None:
        self.snapshots = snapshots
        self.warnings = warnings
        self.calls: list[tuple[str, Any]] = []
        self.sent_messages: list[tuple[str, str]] = []
        self.captures: list[tuple[str, int]] = []
        self.socket_path = Path("/tmp/fake-agentd.sock")
        self.status_payload: dict[str, Any] = {
            "state": "running",
            "db_path": "/tmp/fake-agentd.sqlite",
        }

    def status(self) -> dict[str, Any]:
        self.calls.append(("status", None))
        return dict(self.status_payload)

    def get_session(self, session_id: str) -> AgentSessionSnapshot | None:
        self.calls.append(("get_session", session_id))
        for snapshot in self.snapshots:
            if snapshot.session_id == session_id:
                return snapshot
        return None

    def list_sessions(self, query: Any = None) -> tuple[AgentSessionSnapshot, ...]:
        self.calls.append(("list_sessions", query))
        return tuple(self.snapshots)

    def list_sessions_with_warnings(self, query: Any = None) -> AgentdSessionList:
        self.calls.append(("list_sessions_with_warnings", query))
        return AgentdSessionList(snapshots=tuple(self.snapshots), warnings=self.warnings)

    def capture_session(self, session_id: str, *, lines: int = 100) -> str:
        self.captures.append((session_id, lines))
        return f"captured from {session_id}"

    def send_session(
        self,
        session_id: str,
        message: str,
        *,
        enter: bool = True,
        literal: bool = True,
    ) -> None:
        self.sent_messages.append((session_id, message))


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_ps_lists_agentd_sessions_not_tmux(
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
) -> None:
    client = FakeAgentdClient([_snapshot(session_id="agentd-s1", session_name="agentd-tmux")])
    monkeypatch.setattr(cli_module, "ensure_agentd", lambda: client)

    result = runner.invoke(cli, ["ps"])

    assert result.exit_code == 0
    assert "agentd-s1" in result.output
    assert "agentd-tmux" in result.output
    assert "running" in result.output
    assert client.calls == [("list_sessions_with_warnings", None)]


def test_ps_warns_about_unparseable_agentd_rows(
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
) -> None:
    warning = AgentdSessionParseWarning(
        session_name="raw-session",
        field="agent_type",
        raw_value="future-agent",
    )
    client = FakeAgentdClient(
        [_snapshot(session_id="agentd-s1", session_name="agentd-tmux")],
        warnings=(warning,),
    )
    monkeypatch.setattr(cli_module, "ensure_agentd", lambda: client)

    result = runner.invoke(cli, ["ps"])

    assert result.exit_code == 0
    assert "agentd-s1" in result.output
    warning_output = result.stderr or result.output
    assert "raw-session" in warning_output
    assert "agent_type" in warning_output
    assert "future-agent" in warning_output


def test_output_captures_via_agentd(
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
) -> None:
    client = FakeAgentdClient([_snapshot(session_id="agentd-s1")])
    monkeypatch.setattr(cli_module, "ensure_agentd", lambda: client)

    result = runner.invoke(cli, ["output", "agentd-s1", "--lines", "12"])

    assert result.exit_code == 0
    assert "captured from agentd-s1" in result.output
    assert client.captures == [("agentd-s1", 12)]


def test_send_uses_agentd_rpc(
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
) -> None:
    client = FakeAgentdClient([_snapshot(session_id="agentd-s1")])
    monkeypatch.setattr(cli_module, "ensure_agentd", lambda: client)

    result = runner.invoke(cli, ["send", "agentd-s1", "hello"])

    assert result.exit_code == 0
    assert client.sent_messages == [("agentd-s1", "hello")]


def test_watch_polls_agentd_snapshot_until_terminal(
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
) -> None:
    running = _snapshot(session_id="agentd-s1", status=SessionStatus.RUNNING)
    exited = _snapshot(session_id="agentd-s1", status=SessionStatus.EXITED)
    client = FakeAgentdClient([running])
    polls = iter([running, exited])

    def get_session(session_id: str) -> AgentSessionSnapshot | None:
        client.calls.append(("get_session", session_id))
        return next(polls)

    client.get_session = get_session  # type: ignore[method-assign]
    monkeypatch.setattr(cli_module, "ensure_agentd", lambda: client)
    monkeypatch.setattr(cli_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        cli_module,
        "monitor_session",
        lambda *_args, **_kwargs: pytest.fail("watch must poll agentd snapshots"),
    )

    result = runner.invoke(cli, ["watch", "agentd-s1"])

    assert result.exit_code == 0
    assert "Watching session: agentd-s1" in result.output
    assert "exited" in result.output


def test_attach_resolves_session_in_agentd(
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
) -> None:
    client = FakeAgentdClient([_snapshot(session_id="agentd-s1", session_name="agentd-tmux")])
    attached: list[str] = []
    monkeypatch.setattr(cli_module, "ensure_agentd", lambda: client)
    monkeypatch.setattr(cli_module, "tmux_attach", attached.append)
    monkeypatch.setattr(cli_module, "has_session", lambda *_args: False)

    result = runner.invoke(cli, ["attach", "agentd-s1"])

    assert result.exit_code == 0
    assert attached == ["agentd-tmux"]


@pytest.mark.parametrize(
    "command",
    [
        ["ps"],
        ["watch", "agentd-s1"],
        ["output", "agentd-s1"],
        ["send", "agentd-s1", "hello"],
        ["attach", "agentd-s1"],
    ],
)
def test_monitoring_commands_fail_loudly_when_agentd_unreachable(
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
    tmp_path: Path,
    command: list[str],
) -> None:
    def unavailable() -> None:
        raise AgentdUnavailableError(
            "not reachable",
            socket_path=tmp_path / "agentd.sock",
            start_command=("doeff-agentd", "serve"),
        )

    monkeypatch.setattr(cli_module, "ensure_agentd", unavailable)
    monkeypatch.setattr(cli_module, "has_session", lambda *_args: pytest.fail("no tmux fallback"))

    result = runner.invoke(cli, command)

    assert result.exit_code == 1
    assert "agentd が起動していません" in result.output
    # retirement (DOE-004 R7): the hint points at the ensure verb / the
    # canonical Hy host, never the retired Rust binary
    assert "doeff-sessionhost" in result.output


def test_agentd_ensure_json_outputs_readiness_contract(
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
    tmp_path: Path,
) -> None:
    client = FakeAgentdClient([])
    client.socket_path = tmp_path / "agentd.sock"
    client.status_payload = {
        "state": "running",
        "db_path": str(tmp_path / "agentd.sqlite"),
    }
    monkeypatch.setattr(cli_module, "ensure_agentd", lambda: client)

    result = runner.invoke(cli, ["agentd", "ensure", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload == {
        "socket_path": str(client.socket_path),
        "db_path": str(tmp_path / "agentd.sqlite"),
        "status": client.status_payload,
    }
    assert client.calls == [("status", None)]


def test_agentd_ensure_json_fails_loudly_when_agentd_unreachable(
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
    tmp_path: Path,
) -> None:
    def unavailable() -> None:
        raise AgentdUnavailableError(
            "not reachable",
            socket_path=tmp_path / "agentd.sock",
            start_command=("doeff-agentd", "serve"),
        )

    monkeypatch.setattr(cli_module, "ensure_agentd", unavailable)

    result = runner.invoke(cli, ["agentd", "ensure", "--json"])

    assert result.exit_code == 1
    assert "agentd が起動していません" in result.output
    # retirement (DOE-004 R7): the hint points at the ensure verb / the
    # canonical Hy host, never the retired Rust binary
    assert "doeff-sessionhost" in result.output


def _snapshot(
    *,
    session_id: str,
    session_name: str | None = None,
    status: SessionStatus = SessionStatus.RUNNING,
) -> AgentSessionSnapshot:
    return AgentSessionSnapshot.from_dict(
        {
            "session_id": session_id,
            "session_name": session_name or session_id,
            "pane_id": "%1",
            "agent_type": AgentType.CODEX.value,
            "work_dir": "/tmp/work",
            "lifecycle": "run_to_completion",
            "status": status.value,
            "backend_kind": "tmux",
            "backend_ref": {"session_name": session_name or session_id, "pane_id": "%1"},
            "started_at": "2026-05-25T00:00:00+00:00",
            "last_observed_at": "2026-05-25T00:00:01+00:00",
            "finished_at": None,
            "cleaned_at": None,
            "output_snippet": "running",
        }
    )
