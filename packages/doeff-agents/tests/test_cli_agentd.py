"""CLI tests for the agentd-backed monitoring commands."""

from __future__ import annotations

import json
import sys
from dataclasses import replace
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

    def cancel_session(self, session_id: str) -> AgentSessionSnapshot:
        self.calls.append(("cancel_session", session_id))
        for snapshot in self.snapshots:
            if snapshot.session_id == session_id:
                return replace(snapshot, status=SessionStatus.STOPPED)
        raise AssertionError(f"cancel_session for an unknown session: {session_id}")

    def request(self, method: str, params: dict[str, Any]) -> Any:
        self.calls.append((method, params))
        return {"session_id": "agentd-s1"}


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_ps_lists_agentd_sessions_not_tmux(
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
) -> None:
    client = FakeAgentdClient([_snapshot(session_id="agentd-s1", session_name="agentd-tmux")])
    monkeypatch.setattr(cli_module, "_observing_client", lambda _ensure: client)

    result = runner.invoke(cli, ["ps"])

    assert result.exit_code == 0
    assert "agentd-s1" in result.output
    assert "agentd-tmux" in result.output
    assert "running" in result.output
    assert client.calls == [("list_sessions_with_warnings", None)]


def test_ps_warns_about_unparseable_agentd_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # click 8.1's default CliRunner(mix_stderr=True) merges stderr into
    # result.output and leaves result.stderr unusable (raises ValueError).
    # This test asserts on result.stderr specifically, so it needs its own
    # runner with stderr captured separately (issue #530).
    runner = CliRunner(mix_stderr=False)
    warning = AgentdSessionParseWarning(
        session_name="raw-session",
        field="agent_type",
        raw_value="future-agent",
    )
    client = FakeAgentdClient(
        [_snapshot(session_id="agentd-s1", session_name="agentd-tmux")],
        warnings=(warning,),
    )
    monkeypatch.setattr(cli_module, "_observing_client", lambda _ensure: client)

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
    monkeypatch.setattr(cli_module, "_observing_client", lambda _ensure: client)

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
    monkeypatch.setattr(cli_module, "_observing_client", lambda _ensure: client)
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

    result = runner.invoke(cli, ["attach", "agentd-s1"])

    assert result.exit_code == 0
    assert attached == ["agentd-tmux"]


@pytest.mark.parametrize(
    "command",
    [
        ["send", "agentd-s1", "hello"],
        ["attach", "agentd-s1"],
        ["stop", "agentd-s1"],
    ],
)
def test_commands_that_change_a_session_fail_loudly_when_agentd_unreachable(
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
    tmp_path: Path,
    command: list[str],
) -> None:
    def unavailable() -> None:
        raise AgentdUnavailableError(
            "not reachable",
            socket_path=tmp_path / "agentd.sock",
            start_command=("doeff-sessionhost", "serve"),
        )

    monkeypatch.setattr(cli_module, "ensure_agentd", unavailable)

    result = runner.invoke(cli, command)

    assert result.exit_code == 1
    assert "agentd が起動していません" in result.output
    # retirement (DOE-004 R7): the hint points at the ensure verb / the
    # canonical Hy host, never the retired Rust binary
    assert "doeff-sessionhost" in result.output


@pytest.mark.parametrize(
    "command",
    [
        ["ps"],
        ["watch", "agentd-s1"],
        ["output", "agentd-s1"],
        ["agentd", "by-conversation", "--conversation-id", "c-1"],
        ["agentd", "kinds"],
    ],
)
def test_observation_verbs_never_start_a_host(
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
    tmp_path: Path,
    command: list[str],
) -> None:
    """Reading reports "no observation"; it does not bring a host up.

    On a machine where launchd or systemd owns the host, an observation that
    starts a competitor split-brains the store (ADR-DOE-AGENTS-004 R10 (d)).
    The decision cannot hang off an interactive confirmation either, because
    these commands run unattended — so the default is structural.

    The scene is "no host is running", so the default paths point into
    tmp_path: on a machine whose own host listens on the XDG / ``/tmp``
    default socket, the verbs would otherwise observe that real host.
    """
    monkeypatch.delenv("DOEFF_AGENTD_SOCKET", raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("an observation verb tried to start a host")

    monkeypatch.setattr(cli_module, "ensure_agentd", forbidden)

    result = runner.invoke(cli, command)

    assert result.exit_code == 1
    assert "agentd が起動していません" in result.output
    assert "doeff-sessionhost" in result.output


@pytest.mark.parametrize(
    "command",
    [
        ["ps", "--ensure"],
        ["watch", "agentd-s1", "--ensure"],
        ["output", "agentd-s1", "--ensure"],
        ["agentd", "by-conversation", "--conversation-id", "c-1", "--ensure"],
    ],
)
def test_observation_verbs_start_a_host_only_with_the_explicit_flag(
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
    command: list[str],
) -> None:
    calls: list[str] = []
    client = FakeAgentdClient([_snapshot(session_id="agentd-s1", status=SessionStatus.EXITED)])

    def ensure() -> FakeAgentdClient:
        calls.append("ensure")
        return client

    monkeypatch.setattr(cli_module, "ensure_agentd", ensure)
    monkeypatch.setattr(cli_module.time, "sleep", lambda _seconds: None)

    result = runner.invoke(cli, command)

    assert result.exit_code == 0, result.output
    assert calls == ["ensure"]


@pytest.mark.parametrize("backend", ["tmux", "herdr", "headless"])
def test_stop_ends_the_session_through_the_host_on_every_backend(
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
    backend: str,
) -> None:
    """`stop` asks the host to cancel, whatever substrate carries the session.

    While it killed tmux directly, a herdr or headless session answered
    "Session not found" and never said the backend was the reason (mediagen
    #57).  The kill effect is backend-blind inside the host, so the CLI has no
    reason to branch — and a branch here would be a second place that must
    learn every new substrate.
    """
    # tmux 以外は本番と同じ温かい寿命(multi_turn)で撃つ — その寿命を client が
    # 知らなかった間、行は「読めない行」として skip され CLI は session を
    # 名指せなかった。
    lifecycle = "run_to_completion" if backend == "tmux" else "multi_turn"
    client = FakeAgentdClient(
        [
            _snapshot(
                session_id="agentd-s1",
                session_name="doeff-s1",
                backend_kind=backend,
                lifecycle=lifecycle,
            )
        ]
    )
    monkeypatch.setattr(cli_module, "ensure_agentd", lambda: client)

    result = runner.invoke(cli, ["stop", "s1"])

    assert result.exit_code == 0, result.output
    assert ("cancel_session", "agentd-s1") in client.calls
    assert "Stopped session" in result.output
    assert backend in result.output
    assert "stopped" in result.output


def test_agentd_ensure_json_outputs_readiness_contract(
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
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
        "supervised": False,
    }
    assert client.calls == [("status", None)]


def test_agentd_ensure_json_reports_supervised_socket(
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
    tmp_path: Path,
) -> None:
    # Ops verification seam for the single-supervisor deployment (issue
    # #558): `agentd ensure --json` states whether the canonical socket
    # is declared supervisor-managed, so plist⇔ensure wiring is checkable
    # with one command.
    state_home = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    client = FakeAgentdClient([])
    client.socket_path = tmp_path / "agentd.sock"
    declaration_path = state_home / "doeff" / "agentd.supervisor.json"
    declaration_path.parent.mkdir(parents=True)
    declaration_path.write_text(
        json.dumps(
            {
                "socket_path": str(client.socket_path),
                "supervisor": "launchd",
                "label": "com.example.doeff-sessionhost",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli_module, "ensure_agentd", lambda: client)

    result = runner.invoke(cli, ["agentd", "ensure", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["supervised"] is True
    assert payload["socket_path"] == str(client.socket_path)


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
    backend_kind: str = "tmux",
    lifecycle: str = "run_to_completion",
) -> AgentSessionSnapshot:
    return AgentSessionSnapshot.from_dict(
        {
            "session_id": session_id,
            "session_name": session_name or session_id,
            "pane_id": "%1",
            "agent_type": AgentType.CODEX.value,
            "work_dir": "/tmp/work",
            "lifecycle": lifecycle,
            "status": status.value,
            "backend_kind": backend_kind,
            "backend_ref": {"session_name": session_name or session_id, "pane_id": "%1"},
            "started_at": "2026-05-25T00:00:00+00:00",
            "last_observed_at": "2026-05-25T00:00:01+00:00",
            "finished_at": None,
            "cleaned_at": None,
            "output_snippet": "running",
        }
    )
