"""JSON-line client for the doeff-agentd Unix socket API."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from doeff_agents.effects import (
    AgentSessionLifecycle,
    AgentSessionQuery,
    AgentSessionSnapshot,
    AwaitOutcome,
    AwaitStatus,
)

RPC_ERR_AWAIT_TIMEOUT = -32000
RPC_ERR_NO_SUCH_SESSION = -32001


class AgentdClientError(RuntimeError):
    """Base error raised by the doeff-agentd client."""

    def __init__(self, message: str, *, error_code: int | None = None) -> None:
        self.error_code = error_code
        super().__init__(message)


class AgentdProtocolError(AgentdClientError):
    """Raised when doeff-agentd returns an invalid response."""


@dataclass(frozen=True)
class AgentdPaths:
    """Default filesystem locations for doeff-agentd state and control socket."""

    db_path: Path
    socket_path: Path
    log_path: Path


class AgentdClient:
    """Synchronous client for the long-lived agent supervisor daemon."""

    def __init__(self, socket_path: str | Path, *, timeout: float | None = 10.0) -> None:
        self.socket_path = Path(socket_path)
        self.timeout = timeout
        self._request_id = 0
        self._request_lock = threading.Lock()

    def status(self) -> Mapping[str, Any]:
        result = self.request("daemon.status")
        if not isinstance(result, Mapping):
            raise AgentdProtocolError("daemon.status returned a non-object result")
        return result

    def launch_session(
        self,
        *,
        session_id: str,
        session_name: str,
        agent_type: str,
        work_dir: Path,
        command: str | None = None,
        prompt: str | None = None,
        model: str | None = None,
        effort: str | None = None,
        lifecycle: AgentSessionLifecycle | str = AgentSessionLifecycle.RUN_TO_COMPLETION,
        session_env: Mapping[str, str] | None = None,
        expected_result: Mapping[str, Any] | None = None,
    ) -> AgentSessionSnapshot:
        params: dict[str, Any] = {
            "session_id": session_id,
            "session_name": session_name,
            "agent_type": agent_type,
            "work_dir": str(work_dir),
            "lifecycle": _lifecycle_value(lifecycle),
            "session_env": dict(session_env or {}),
        }
        if command is not None:
            params["command"] = command
        if prompt is not None:
            params["prompt"] = prompt
        if model is not None:
            params["model"] = model
        if effort is not None:
            params["effort"] = effort
        if expected_result is not None:
            params["expected_result"] = dict(expected_result)
        result = self.request("session.launch", params)
        return _snapshot_from_result(result)

    def await_result(
        self,
        session_id: str,
        *,
        timeout_seconds: float | None = None,
    ) -> AwaitOutcome:
        params: dict[str, Any] = {"session_id": session_id}
        if timeout_seconds is not None:
            params["timeout_seconds"] = timeout_seconds
        try:
            result = self.request("session.await_result", params)
        except AgentdClientError as exc:
            if exc.error_code == RPC_ERR_AWAIT_TIMEOUT:
                return AwaitOutcome(status=AwaitStatus.TIMED_OUT, validation_error=str(exc))
            raise
        if not isinstance(result, Mapping):
            raise AgentdProtocolError("session.await_result returned a non-object result")
        return _await_outcome_from_result(result)

    def get_session(self, session_id: str) -> AgentSessionSnapshot | None:
        result = self.request("session.get", {"session_id": session_id})
        if result is None:
            return None
        return _snapshot_from_result(result)

    def list_sessions(
        self,
        query: AgentSessionQuery | None = None,
    ) -> tuple[AgentSessionSnapshot, ...]:
        result = self.request("session.list", _query_to_params(query))
        if not isinstance(result, list):
            raise AgentdProtocolError("session.list returned a non-list result")
        return tuple(_snapshot_from_result(item) for item in result)

    def capture_session(self, session_id: str, *, lines: int = 100) -> str:
        result = self.request("session.capture", {"session_id": session_id, "lines": lines})
        if not isinstance(result, Mapping):
            raise AgentdProtocolError("session.capture returned a non-object result")
        text = result.get("text")
        if not isinstance(text, str):
            raise AgentdProtocolError("session.capture result is missing text")
        return text

    def send_session(
        self,
        session_id: str,
        message: str,
        *,
        enter: bool = True,
        literal: bool = True,
    ) -> None:
        self.request(
            "session.send",
            {
                "session_id": session_id,
                "message": message,
                "enter": enter,
                "literal": literal,
            },
        )

    def cancel_session(self, session_id: str) -> AgentSessionSnapshot:
        result = self.request("session.cancel", {"session_id": session_id})
        return _snapshot_from_result(result)

    def cleanup_session(self, session_id: str) -> AgentSessionSnapshot:
        result = self.request("session.cleanup", {"session_id": session_id})
        return _snapshot_from_result(result)

    def request(self, method: str, params: Mapping[str, Any] | None = None) -> Any:
        request = {
            "id": self._next_request_id(),
            "method": method,
            "params": dict(params or {}),
        }
        encoded = json.dumps(request, separators=(",", ":")).encode("utf-8") + b"\n"

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            if self.timeout is not None:
                sock.settimeout(self.timeout)
            sock.connect(str(self.socket_path))
            sock.sendall(encoded)
            with sock.makefile("r", encoding="utf-8") as reader:
                line = reader.readline()

        if not line:
            raise AgentdProtocolError("doeff-agentd closed the connection without a response")
        response = json.loads(line)
        if not isinstance(response, Mapping):
            raise AgentdProtocolError("doeff-agentd returned a non-object response")
        if response.get("id") != request["id"]:
            raise AgentdProtocolError("doeff-agentd response id did not match request id")
        if not response.get("ok"):
            error = response.get("error")
            if not isinstance(error, str) or not error:
                error = "doeff-agentd request failed"
            error_code = response.get("error_code")
            if error_code is not None and not isinstance(error_code, int):
                raise AgentdProtocolError("doeff-agentd error_code was not an integer")
            raise AgentdClientError(error, error_code=error_code)
        return response.get("result")

    def _next_request_id(self) -> int:
        with self._request_lock:
            self._request_id += 1
            return self._request_id


class LazyAgentdClient:
    """Client proxy that starts doeff-agentd only when an agent effect needs it."""

    def __init__(
        self,
        *,
        db_path: str | Path | None = None,
        socket_path: str | Path | None = None,
        daemon_bin: str | Path | None = None,
        timeout: float = 5.0,
        client_timeout: float = 1.0,
        max_running: int = 10,
    ) -> None:
        self.db_path = db_path
        self.socket_path = socket_path
        self.daemon_bin = daemon_bin
        self.timeout = timeout
        self.client_timeout = client_timeout
        self.max_running = max_running
        self._client: AgentdClient | None = None
        self._lock = threading.Lock()

    def _resolve(self) -> AgentdClient:
        with self._lock:
            if self._client is None:
                self._client = ensure_agentd(
                    db_path=self.db_path,
                    socket_path=self.socket_path,
                    daemon_bin=self.daemon_bin,
                    timeout=self.timeout,
                    client_timeout=self.client_timeout,
                    max_running=self.max_running,
                )
            return self._client

    def status(self) -> Mapping[str, Any]:
        return self._resolve().status()

    def launch_session(
        self,
        *,
        session_id: str,
        session_name: str,
        agent_type: str,
        work_dir: Path,
        command: str | None = None,
        prompt: str | None = None,
        model: str | None = None,
        effort: str | None = None,
        lifecycle: AgentSessionLifecycle | str = AgentSessionLifecycle.RUN_TO_COMPLETION,
        session_env: Mapping[str, str] | None = None,
        expected_result: Mapping[str, Any] | None = None,
    ) -> AgentSessionSnapshot:
        return self._resolve().launch_session(
            session_id=session_id,
            session_name=session_name,
            agent_type=agent_type,
            work_dir=work_dir,
            command=command,
            prompt=prompt,
            model=model,
            effort=effort,
            lifecycle=lifecycle,
            session_env=session_env,
            expected_result=expected_result,
        )

    def await_result(
        self,
        session_id: str,
        *,
        timeout_seconds: float | None = None,
    ) -> AwaitOutcome:
        return self._resolve().await_result(session_id, timeout_seconds=timeout_seconds)

    def get_session(self, session_id: str) -> AgentSessionSnapshot | None:
        return self._resolve().get_session(session_id)

    def list_sessions(
        self,
        query: AgentSessionQuery | None = None,
    ) -> tuple[AgentSessionSnapshot, ...]:
        return self._resolve().list_sessions(query)

    def capture_session(self, session_id: str, *, lines: int = 100) -> str:
        return self._resolve().capture_session(session_id, lines=lines)

    def send_session(
        self,
        session_id: str,
        message: str,
        *,
        enter: bool = True,
        literal: bool = True,
    ) -> None:
        self._resolve().send_session(session_id, message, enter=enter, literal=literal)

    def cancel_session(self, session_id: str) -> AgentSessionSnapshot:
        return self._resolve().cancel_session(session_id)

    def cleanup_session(self, session_id: str) -> AgentSessionSnapshot:
        return self._resolve().cleanup_session(session_id)


def default_agentd_paths() -> AgentdPaths:
    """Return XDG-style default paths for doeff-agentd."""
    state_home = (
        Path(os.environ["XDG_STATE_HOME"])
        if "XDG_STATE_HOME" in os.environ
        else Path.home() / ".local" / "state"
    )
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir:
        socket_path = Path(runtime_dir) / "doeff" / "agentd.sock"
    else:
        user = os.environ.get("USER") or os.environ.get("LOGNAME") or "unknown"
        socket_path = Path("/tmp") / f"doeff-agentd-{user}.sock"
    state_dir = state_home / "doeff"
    return AgentdPaths(
        db_path=state_dir / "agentd.sqlite",
        socket_path=socket_path,
        log_path=state_dir / "agentd.log",
    )


def ensure_agentd(
    *,
    db_path: str | Path | None = None,
    socket_path: str | Path | None = None,
    daemon_bin: str | Path | None = None,
    timeout: float = 5.0,
    client_timeout: float = 1.0,
    max_running: int = 10,
) -> AgentdClient:
    """Return a client, starting doeff-agentd if its socket is not already live."""
    paths = default_agentd_paths()
    active_db_path = Path(db_path) if db_path is not None else paths.db_path
    active_socket_path = Path(socket_path) if socket_path is not None else paths.socket_path
    active_log_path = active_db_path.parent / "agentd.log"
    client = AgentdClient(active_socket_path, timeout=client_timeout)
    if _agentd_is_ready(client):
        return client

    active_db_path.parent.mkdir(parents=True, exist_ok=True)
    active_socket_path.parent.mkdir(parents=True, exist_ok=True)
    active_log_path.parent.mkdir(parents=True, exist_ok=True)
    command = _agentd_command(
        daemon_bin=daemon_bin,
        db_path=active_db_path,
        socket_path=active_socket_path,
        max_running=max_running,
    )
    with active_log_path.open("ab") as log_file:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    _wait_for_agentd(client, process=process, timeout=timeout)
    return client


def _agentd_is_ready(client: AgentdClient) -> bool:
    try:
        client.status()
    except OSError:
        return False
    except AgentdClientError:
        return False
    return True


def _wait_for_agentd(
    client: AgentdClient,
    *,
    process: subprocess.Popen[Any],
    timeout: float,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _agentd_is_ready(client):
            return
        returncode = process.poll()
        if returncode is not None:
            raise AgentdClientError(f"doeff-agentd exited during startup: {returncode}")
        time.sleep(0.05)
    raise AgentdClientError("timed out waiting for doeff-agentd startup")


def _agentd_command(
    *,
    daemon_bin: str | Path | None,
    db_path: Path,
    socket_path: Path,
    max_running: int,
) -> list[str]:
    if daemon_bin is not None:
        executable = str(daemon_bin)
        prefix = [executable]
    elif env_bin := os.environ.get("DOEFF_AGENTD_BIN"):
        prefix = [env_bin]
    elif path_bin := shutil.which("doeff-agentd"):
        prefix = [path_bin]
    else:
        cargo_manifest = _repo_agentd_manifest()
        if cargo_manifest is None:
            raise AgentdClientError("doeff-agentd binary was not found")
        prefix = [
            "cargo",
            "run",
            "--quiet",
            "--manifest-path",
            str(cargo_manifest),
            "--",
        ]
    return [
        *prefix,
        "--db",
        str(db_path),
        "--socket",
        str(socket_path),
        "--max-running",
        str(max_running),
        "serve",
    ]


def _repo_agentd_manifest() -> Path | None:
    source_path = Path(__file__).resolve()
    for parent in source_path.parents:
        candidate = parent / "packages" / "doeff-agentd" / "Cargo.toml"
        if candidate.exists():
            return candidate
    return None


def _query_to_params(query: AgentSessionQuery | None) -> dict[str, Any]:
    if query is None:
        return {}
    params: dict[str, Any] = {}
    if query.status is not None:
        params["status"] = [query.status.value]
    if query.agent_type is not None:
        params["agent_type"] = query.agent_type.value
    if query.backend_kind is not None:
        params["backend_kind"] = query.backend_kind
    if query.lifecycle is not None:
        params["lifecycle"] = query.lifecycle.value
    return params


def _lifecycle_value(lifecycle: AgentSessionLifecycle | str) -> str:
    if isinstance(lifecycle, AgentSessionLifecycle):
        return lifecycle.value
    return str(lifecycle)


def _snapshot_from_result(result: Any) -> AgentSessionSnapshot:
    if not isinstance(result, Mapping):
        raise AgentdProtocolError("doeff-agentd returned a non-object session snapshot")
    return AgentSessionSnapshot.from_dict(dict(result))


def _await_outcome_from_result(result: Mapping[str, Any]) -> AwaitOutcome:
    session = result.get("session")
    status = "exited"
    if isinstance(session, Mapping):
        status = str(session.get("status", "exited"))
    validation_error = result.get("validation_error")
    if validation_error is not None and not isinstance(validation_error, str):
        raise AgentdProtocolError("session.await_result validation_error was not a string")

    if status in ("blocked", "blocked_api"):
        return AwaitOutcome(
            status=AwaitStatus.AWAITING_INPUT,
            validation_error=validation_error or status,
        )

    payload = None
    response_result = result.get("result")
    if isinstance(response_result, Mapping):
        payload = response_result.get("payload")

    return AwaitOutcome(
        status=AwaitStatus.EXITED,
        result=payload,
        validation_error=validation_error,
    )


__all__ = [
    "RPC_ERR_AWAIT_TIMEOUT",
    "RPC_ERR_NO_SUCH_SESSION",
    "AgentdClient",
    "AgentdClientError",
    "AgentdPaths",
    "AgentdProtocolError",
    "LazyAgentdClient",
    "default_agentd_paths",
    "ensure_agentd",
]
