"""Tests for the doeff-agentd client boundary."""

from __future__ import annotations

import json
import shutil
import socket
import sys
import tempfile
import threading
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from doeff_agents import (
    AgentdClient,
    AgentdClientError,
    AgentdProtocolError,
    AgentdUnavailableError,
    AgentSessionLifecycle,
    AgentType,
    DaemonAgentHandler,
    LaunchEffect,
    LazyAgentdClient,
    ObserveAgentSessionEffect,
    SessionStatus,
    agentd_client,
    default_agentd_paths,
    ensure_agentd,
)


@pytest.fixture
def short_runtime_dir() -> Iterator[Path]:
    """Return a short runtime dir so AF_UNIX socket paths fit on macOS."""
    runtime_dir = Path(tempfile.mkdtemp(prefix="agentd-runtime-", dir="/tmp"))
    try:
        yield runtime_dir
    finally:
        shutil.rmtree(runtime_dir, ignore_errors=True)


class OneShotAgentdServer:
    """Tiny Unix-socket JSON-line server for client tests."""

    def __init__(
        self,
        socket_path: Path,
        handler: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    ) -> None:
        self.socket_path = socket_path
        self.handler = handler
        self.requests: list[Mapping[str, Any]] = []
        self._thread: threading.Thread | None = None

    def __enter__(self) -> OneShotAgentdServer:
        if self.socket_path.exists():
            self.socket_path.unlink()
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(self.socket_path))
        server.listen(1)

        def serve() -> None:
            with server:
                conn, _addr = server.accept()
                with conn:
                    line = conn.makefile("r", encoding="utf-8").readline()
                    request = json.loads(line)
                    self.requests.append(request)
                    response = self.handler(request)
                    conn.sendall(json.dumps(response).encode("utf-8") + b"\n")

        self._thread = threading.Thread(target=serve, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc_info: object) -> None:
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        if self.socket_path.exists():
            self.socket_path.unlink()


def test_agentd_client_get_session_round_trip() -> None:
    def handle(request: Mapping[str, Any]) -> Mapping[str, Any]:
        return {
            "id": request["id"],
            "ok": True,
            "result": _snapshot_payload(),
        }

    with (
        tempfile.TemporaryDirectory(prefix="agentd-", dir="/tmp") as temp_dir,
        OneShotAgentdServer(Path(temp_dir) / "agentd.sock", handle) as server,
    ):
        client = AgentdClient(server.socket_path, timeout=2.0)
        snapshot = client.get_session("s1")

    assert snapshot is not None
    assert snapshot.session_id == "s1"
    assert snapshot.status == SessionStatus.RUNNING
    assert server.requests[0]["method"] == "session.get"
    assert server.requests[0]["params"] == {"session_id": "s1"}


def test_agentd_client_launch_sends_interactive_lifecycle(tmp_path: Path) -> None:
    def handle(request: Mapping[str, Any]) -> Mapping[str, Any]:
        return {
            "id": request["id"],
            "ok": True,
            "result": _snapshot_payload(lifecycle="interactive"),
        }

    with (
        tempfile.TemporaryDirectory(prefix="agentd-", dir="/tmp") as temp_dir,
        OneShotAgentdServer(Path(temp_dir) / "agentd.sock", handle) as server,
    ):
        client = AgentdClient(server.socket_path, timeout=2.0)
        snapshot = client.launch_session(
            session_id="s1",
            session_name="s1",
            agent_type="codex",
            work_dir=tmp_path,
            command="codex",
            lifecycle=AgentSessionLifecycle.INTERACTIVE,
        )

    assert snapshot.lifecycle == AgentSessionLifecycle.INTERACTIVE
    assert server.requests[0]["method"] == "session.launch"
    assert server.requests[0]["params"]["lifecycle"] == "interactive"


def test_agentd_client_raises_daemon_error() -> None:
    def handle(request: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"id": request["id"], "ok": False, "error": "boom"}

    with (
        tempfile.TemporaryDirectory(prefix="agentd-", dir="/tmp") as temp_dir,
        OneShotAgentdServer(Path(temp_dir) / "agentd.sock", handle) as server,
    ):
        client = AgentdClient(server.socket_path, timeout=2.0)
        with pytest.raises(AgentdClientError, match="boom"):
            client.status()

    assert server.requests[0]["method"] == "daemon.status"


def test_agentd_client_success_response_requires_result_key() -> None:
    def handle(request: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"id": request["id"], "ok": True}

    with (
        tempfile.TemporaryDirectory(prefix="agentd-", dir="/tmp") as temp_dir,
        OneShotAgentdServer(Path(temp_dir) / "agentd.sock", handle) as server,
    ):
        client = AgentdClient(server.socket_path, timeout=2.0)
        with pytest.raises(AgentdProtocolError, match=r"daemon.status.*missing result"):
            client.status()

    assert server.requests[0]["method"] == "daemon.status"


def test_default_agentd_paths_use_xdg(monkeypatch, tmp_path: Path) -> None:
    state_home = tmp_path / "state"
    runtime_dir = tmp_path / "runtime"
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime_dir))

    paths = default_agentd_paths()

    assert paths.db_path == state_home / "doeff" / "agentd.sqlite"
    assert paths.socket_path == runtime_dir / "doeff" / "agentd.sock"
    assert paths.log_path == state_home / "doeff" / "agentd.log"


def test_ensure_agentd_uses_reachable_canonical_socket(
    monkeypatch,
    tmp_path: Path,
    short_runtime_dir: Path,
) -> None:
    def handle(request: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"id": request["id"], "ok": True, "result": {"state": "running"}}

    state_home = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(short_runtime_dir))
    paths = default_agentd_paths()
    paths.socket_path.parent.mkdir(parents=True)

    with OneShotAgentdServer(paths.socket_path, handle) as server:
        client = ensure_agentd(client_timeout=2.0)

    assert client.socket_path == paths.socket_path
    assert server.requests[0]["method"] == "daemon.status"


def test_ensure_agentd_fails_loudly_when_canonical_socket_unreachable(
    monkeypatch,
    tmp_path: Path,
    short_runtime_dir: Path,
) -> None:
    state_home = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(short_runtime_dir))
    paths = default_agentd_paths()

    with pytest.raises(AgentdUnavailableError) as error:
        ensure_agentd(daemon_bin="/usr/local/bin/doeff-agentd", max_running=7)

    message = str(error.value)
    assert str(paths.socket_path) in message
    assert "doeff-agentd --db" in message
    assert f"--socket {paths.socket_path}" in message
    assert "--max-running 7 serve" in message


def test_lazy_agentd_client_resolves_daemon_on_first_operation(monkeypatch, tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []

    class FakeResolvedClient:
        def status(self):
            return {"state": "running"}

    def fake_ensure_agentd(**kwargs):
        calls.append(kwargs)
        return FakeResolvedClient()

    monkeypatch.setattr(agentd_client, "ensure_agentd", fake_ensure_agentd)
    client = LazyAgentdClient(
        db_path=tmp_path / "agentd.sqlite",
        socket_path=tmp_path / "agentd.sock",
        daemon_bin="/usr/local/bin/doeff-agentd",
        max_running=4,
    )

    assert calls == []
    assert client.status() == {"state": "running"}
    assert client.status() == {"state": "running"}
    assert len(calls) == 1
    assert calls[0]["max_running"] == 4


def test_daemon_handler_observe_is_read_only() -> None:
    fake_client = FakeAgentdClient()
    handler = DaemonAgentHandler(client=fake_client)

    snapshot = handler.handle_observe_session(ObserveAgentSessionEffect(session_id="s1"))

    assert snapshot.session_id == "s1"
    assert fake_client.calls == [("get_session", "s1")]


def test_daemon_handler_launch_delegates_lifecycle_to_client(monkeypatch, tmp_path: Path) -> None:
    adapter = FakeAdapter()
    monkeypatch.setattr("doeff_agents.handlers.daemon.get_adapter", lambda _agent_type: adapter)
    fake_client = FakeAgentdClient()
    handler = DaemonAgentHandler(client=fake_client)

    handle = handler.handle_launch(
        LaunchEffect(
            session_name="s2",
            agent_type=AgentType.CUSTOM,
            work_dir=tmp_path,
            prompt="review this",
            model="test-model",
            lifecycle=AgentSessionLifecycle.INTERACTIVE,
        )
    )

    assert handle.session_id == "s2"
    assert adapter.params is not None
    assert adapter.params.prompt == "review this"
    assert fake_client.launches[0]["session_id"] == "s2"
    assert fake_client.launches[0]["agent_type"] == "custom"
    assert fake_client.launches[0]["lifecycle"] == AgentSessionLifecycle.INTERACTIVE
    assert "custom-agent" in fake_client.launches[0]["command"]


class FakeAgentdClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.launches: list[dict[str, Any]] = []

    def get_session(self, session_id: str):
        self.calls.append(("get_session", session_id))
        return _snapshot_payload_obj()

    def launch_session(self, **payload: Any):
        self.launches.append(payload)
        return _snapshot_payload_obj(
            session_id=payload["session_id"],
            agent_type=payload["agent_type"],
            work_dir=str(payload["work_dir"]),
        )


class FakeAdapter:
    def __init__(self) -> None:
        self.params = None

    def is_available(self) -> bool:
        return True

    def launch_command(self, params):
        self.params = params
        return ["custom-agent", "--model", params.model or "default", params.prompt or ""]


def _snapshot_payload(
    *,
    session_id: str = "s1",
    agent_type: str = "codex",
    work_dir: str = "/tmp/work",
    lifecycle: str = "run_to_completion",
) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "session_name": session_id,
        "pane_id": "%1",
        "agent_type": agent_type,
        "work_dir": work_dir,
        "lifecycle": lifecycle,
        "status": "running",
        "backend_kind": "tmux",
        "backend_ref": {"session_name": session_id, "pane_id": "%1"},
        "started_at": "2026-05-25T00:00:00+00:00",
        "last_observed_at": "2026-05-25T00:00:01+00:00",
        "finished_at": None,
        "cleaned_at": None,
        "output_snippet": "running",
    }


def _snapshot_payload_obj(
    *,
    session_id: str = "s1",
    agent_type: str = AgentType.CODEX.value,
    work_dir: str = "/tmp/work",
):
    from doeff_agents import AgentSessionSnapshot

    return AgentSessionSnapshot.from_dict(
        _snapshot_payload(session_id=session_id, agent_type=agent_type, work_dir=work_dir)
    )


def _spy_client(captured: dict) -> AgentdClient:
    class _Spy(AgentdClient):
        def request(self, method, params=None, *, read_timeout=None):
            captured["method"] = method
            captured["read_timeout"] = read_timeout
            if method == "session.launch":
                return _snapshot_payload()
            return {
                "session": _snapshot_payload(),
                "result": {"payload": None},
                "validation_error": None,
            }

    return _Spy("/tmp/unused.sock")


class _StaticAwaitResultClient(AgentdClient):
    def __init__(self, result: Mapping[str, Any]) -> None:
        super().__init__("/tmp/unused.sock")
        self.result = result

    def request(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
        *,
        read_timeout: float | None = None,
    ) -> Any:
        assert method == "session.await_result"
        return self.result


def test_await_result_rejects_missing_session() -> None:
    client = _StaticAwaitResultClient(
        {"result": {"payload": {"ok": True}}, "validation_error": None}
    )

    with pytest.raises(AgentdProtocolError, match=r"session.await_result.*missing session"):
        client.await_result("s1", timeout_seconds=1.0)


def test_await_result_rejects_non_object_result_payload() -> None:
    client = _StaticAwaitResultClient(
        {"session": _snapshot_payload(), "result": "not an object", "validation_error": None}
    )

    with pytest.raises(AgentdProtocolError, match=r"session.await_result result.*non-object"):
        client.await_result("s1", timeout_seconds=1.0)


def test_launch_read_timeout_covers_daemon_launch_budget() -> None:
    """session.launch blocks daemon-side up to 60s; the client socket must wait longer."""
    captured: dict = {}
    _spy_client(captured).launch_session(
        session_id="s1",
        session_name="s1",
        agent_type="codex",
        work_dir=Path("/tmp"),
    )
    assert captured["method"] == "session.launch"
    assert captured["read_timeout"] is not None
    assert captured["read_timeout"] > 60.0


def test_await_result_read_timeout_covers_caller_budget() -> None:
    captured: dict = {}
    _spy_client(captured).await_result("s1", timeout_seconds=120.0)
    assert captured["method"] == "session.await_result"
    assert captured["read_timeout"] > 120.0


def test_await_result_read_timeout_covers_default_budget() -> None:
    captured: dict = {}
    _spy_client(captured).await_result("s1")
    assert captured["read_timeout"] > 600.0
