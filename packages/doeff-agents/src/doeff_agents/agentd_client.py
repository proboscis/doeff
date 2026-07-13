"""JSON-line client for the doeff-agentd Unix socket API."""

import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from doeff_agents.adapters.base import AgentType
from doeff_agents.effects import (
    AgentSessionLifecycle,
    AgentSessionQuery,
    AgentSessionSnapshot,
    AwaitOutcome,
    AwaitStatus,
)
from doeff_agents.monitor import SessionStatus

RPC_ERR_AWAIT_TIMEOUT = -32000
RPC_ERR_NO_SUCH_SESSION = -32001


class AgentdClientError(RuntimeError):
    """Base error raised by the doeff-agentd client."""

    def __init__(self, message: str, *, error_code: int | None = None) -> None:
        self.error_code = error_code
        super().__init__(message)


class AgentdProtocolError(AgentdClientError):
    """Raised when doeff-agentd returns an invalid response."""


class AgentdUnavailableError(AgentdClientError):
    """Raised when no doeff-agentd daemon is reachable at the expected socket."""

    def __init__(
        self,
        message: str,
        *,
        socket_path: Path,
        start_command: tuple[str, ...],
    ) -> None:
        self.socket_path = socket_path
        self.start_command = start_command
        super().__init__(message)


@dataclass(frozen=True)
class AgentdPaths:
    """Default filesystem locations for doeff-agentd state and control socket."""

    db_path: Path
    socket_path: Path
    log_path: Path


@dataclass(frozen=True, kw_only=True)
class AgentdSessionParseWarning:
    """Details for one agentd session row that could not be parsed."""

    session_name: str
    field: str
    raw_value: Any


@dataclass(frozen=True)
class AgentdSessionList:
    """Parsed session.list result plus recoverable row parse failures."""

    snapshots: tuple[AgentSessionSnapshot, ...]
    warnings: tuple[AgentdSessionParseWarning, ...] = ()


# RPC read-timeout contract.  The daemon BLOCKS on these methods by design:
# `session.launch` waits for agent readiness (daemon LAUNCH_TIMEOUT_SECONDS,
# 60s) and `session.await_result` waits up to its caller-supplied budget
# (clamp [1, 3600]).  The client socket timeout must therefore cover the
# daemon-side budget plus a margin — a short default here silently breaks
# the protocol (observed live: 10s client timeout vs 60s launch budget ->
# client disconnect, daemon Broken pipe).
RPC_TIMEOUT_MARGIN_SECONDS: float = 15.0
LAUNCH_RPC_TIMEOUT_SECONDS: float = 120.0 + RPC_TIMEOUT_MARGIN_SECONDS
# PURE TRANSPORT HEARTBEAT (L-K4-3).  This constant bounds ONE
# session.await_result round-trip and carries no node semantics: expiry
# means "renew the keep-alive and re-await", never a node failure and
# never a semantic decision.  Wall-clock node deadlines live in the
# workflow node spec (`agent! :deadline-seconds`, k8s
# activeDeadlineSeconds semantics) and are observed by the L3 runtime,
# which parks a gate on exceed.  History: when this constant still
# carried await semantics, a 600s value burned the launcher's whole
# retry budget on a worker that was healthily working (observed live);
# the 600→3600 bump was the band-aid that motivated giving the
# wall-clock axis a real owner.  3600 = the daemon-side clamp max; a
# long heartbeat is free in the failure case because the daemon monitor
# resolves the await early the moment a session turns terminal.
DEFAULT_AWAIT_BUDGET_SECONDS: float = 3600.0
AGENTD_START_POLL_SECONDS: float = 0.1
# Status budget for a listener that answered connect() but not the 1s
# default status probe.  The host serialises ALL store reads through one
# writer actor; on a multi-GiB store a bulk query can hold the queue for
# seconds, so a slow daemon.status is normal degraded operation, NOT
# death.  Spawning a competitor on that misdiagnosis is the "ensure
# spawn spiral" incident (2026-07-07): the child stole the lease, died
# on the socket bind, and left the lease rotting under a dead pid while
# the live host's heartbeat erred forever.  Liveness authority is the
# socket listener; this budget only bounds how long ensure waits for the
# busy host to answer before failing LOUDLY (never by spawning).
AGENTD_BUSY_STATUS_TIMEOUT_SECONDS: float = 15.0


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

    def kinds(self) -> list[Mapping[str, Any]]:
        """Advertised binding-kind vocabulary (DOE-004 R5, reduced form).

        The host advertises {kind, agent_type, required_field, api_version}
        rows; the control plane's reconciler cross-checks its registered
        agent bindings against this list on a level-triggered cadence
        (registration itself never couples to host liveness).
        """
        result = self.request("kinds.list")
        if not isinstance(result, Mapping) or not isinstance(result.get("kinds"), list):
            raise AgentdProtocolError("kinds.list returned a malformed result")
        rows = result["kinds"]
        for row in rows:
            if not isinstance(row, Mapping) or not isinstance(row.get("kind"), str):
                raise AgentdProtocolError("kinds.list returned a malformed kind row")
        return rows

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
        binding: Mapping[str, Any] | None = None,
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
        if binding is not None:
            # ADR-DOE-AGENTS-004 R7: auth/profile rides the typed binding;
            # session_env stays a non-auth overlay.
            params["binding"] = dict(binding)
        if expected_result is not None:
            params["expected_result"] = dict(expected_result)
        result = self.request(
            "session.launch",
            params,
            read_timeout=LAUNCH_RPC_TIMEOUT_SECONDS,
        )
        return _snapshot_from_result(result)

    def await_result(
        self,
        session_id: str,
        *,
        timeout_seconds: float | None = None,
    ) -> AwaitOutcome:
        # Always send the budget explicitly: the client is the single
        # authority for the await budget.  Relying on the daemon-side
        # default left two constants that could (and did) drift apart.
        await_budget = (
            timeout_seconds if timeout_seconds is not None else DEFAULT_AWAIT_BUDGET_SECONDS
        )
        params: dict[str, Any] = {
            "session_id": session_id,
            "timeout_seconds": await_budget,
        }
        try:
            result = self.request(
                "session.await_result",
                params,
                read_timeout=await_budget + RPC_TIMEOUT_MARGIN_SECONDS,
            )
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
        return self.list_sessions_with_warnings(query).snapshots

    def list_sessions_with_warnings(
        self,
        query: AgentSessionQuery | None = None,
    ) -> AgentdSessionList:
        result = self.request("session.list", _query_to_params(query))
        return _session_list_from_result(result)

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

    def resume_session(
        self,
        session_id: str,
        *,
        prompt: str | None = None,
        model: str | None = None,
        effort: str | None = None,
        mcp_servers: Mapping[str, str] | None = None,
        session_env: Mapping[str, str] | None = None,
    ) -> Mapping[str, Any]:
        """Host a new incarnation of the session's conversation
        (ADR-DOE-AGENTS-006 R4). Returns the raw wire snapshot of the NEW
        session row — callers need the ADR-006 fields (``conversation`` /
        ``generation`` / ``resumed_from_session_id``) that the typed
        AgentSessionSnapshot does not carry. Non-auth launch intent
        (session_env / model / effort / mcp_servers) is restored from the
        source row's persisted overlay; keyword arguments override per key."""
        return self._incarnation_request("session.resume", session_id,
                                         prompt=prompt, model=model,
                                         effort=effort,
                                         mcp_servers=mcp_servers,
                                         session_env=session_env)

    def fork_session(
        self,
        session_id: str,
        *,
        prompt: str | None = None,
        model: str | None = None,
        effort: str | None = None,
        mcp_servers: Mapping[str, str] | None = None,
        session_env: Mapping[str, str] | None = None,
    ) -> Mapping[str, Any]:
        """Fork the session's conversation into a NEW conversation
        (ADR-DOE-AGENTS-006 R4; the CLI mints the new native identity —
        the host discovers it and fills ``conversation`` asynchronously).
        Returns the raw wire snapshot of the new session row."""
        return self._incarnation_request("session.fork", session_id,
                                         prompt=prompt, model=model,
                                         effort=effort,
                                         mcp_servers=mcp_servers,
                                         session_env=session_env)

    def _incarnation_request(
        self,
        method: str,
        session_id: str,
        *,
        prompt: str | None,
        model: str | None,
        effort: str | None,
        mcp_servers: Mapping[str, str] | None,
        session_env: Mapping[str, str] | None,
    ) -> Mapping[str, Any]:
        params: dict[str, Any] = {"session_id": session_id}
        if prompt is not None:
            params["prompt"] = prompt
        if model is not None:
            params["model"] = model
        if effort is not None:
            params["effort"] = effort
        if mcp_servers:
            params["mcp_servers"] = dict(mcp_servers)
        if session_env:
            params["session_env"] = dict(session_env)
        result = self.request(method, params)
        if not isinstance(result, Mapping):
            raise AgentdProtocolError(f"{method} returned a non-object result")
        return result

    def request(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
        *,
        read_timeout: float | None = None,
    ) -> Any:
        request = {
            "id": self._next_request_id(),
            "method": method,
            "params": dict(params or {}),
        }
        encoded = json.dumps(request, separators=(",", ":")).encode("utf-8") + b"\n"

        effective_timeout = read_timeout if read_timeout is not None else self.timeout
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            if effective_timeout is not None:
                sock.settimeout(effective_timeout)
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
        if "result" not in response:
            raise AgentdProtocolError(
                f"{method} response is missing result "
                f"(response shape: {_mapping_shape(response)})"
            )
        return response["result"]

    def _next_request_id(self) -> int:
        with self._request_lock:
            self._request_id += 1
            return self._request_id


class LazyAgentdClient:
    """Client proxy that resolves doeff-agentd only when an agent effect needs it."""

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
        binding: Mapping[str, Any] | None = None,
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
            binding=binding,
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

    def list_sessions_with_warnings(
        self,
        query: AgentSessionQuery | None = None,
    ) -> AgentdSessionList:
        return self._resolve().list_sessions_with_warnings(query)

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
    """Return a client for the canonical daemon, starting it when necessary."""
    paths = default_agentd_paths()
    active_db_path = Path(db_path) if db_path is not None else paths.db_path
    active_socket_path = Path(socket_path) if socket_path is not None else paths.socket_path
    client = AgentdClient(active_socket_path, timeout=client_timeout)
    command = _agentd_command(
        daemon_bin=daemon_bin,
        db_path=active_db_path,
        socket_path=active_socket_path,
        max_running=max_running,
    )
    _prepare_agentd_paths(active_db_path, active_socket_path, paths.log_path)
    status = _agentd_status_if_ready(client)
    if status is not None:
        _validate_agentd_identity(
            status,
            expected_db_path=active_db_path,
            expected_socket_path=active_socket_path,
            command=command,
        )
        return client

    # Spawn predicate: only the ABSENCE of a live listener proves the
    # daemon is dead.  A listener that accepts connect() but misses the
    # short status probe is alive-but-busy (slow != dead); starting a
    # competitor against it corrupts the lease and the store, so that
    # path retries with a long budget and then fails loudly instead.
    if _socket_has_live_listener(active_socket_path):
        status = _agentd_status_from_live_listener(client)
        if status is not None:
            _validate_agentd_identity(
                status,
                expected_db_path=active_db_path,
                expected_socket_path=active_socket_path,
                command=command,
            )
            return client
        raise AgentdUnavailableError(
            "doeff-agentd has a live listener on "
            f"{active_socket_path} but did not answer daemon.status within "
            f"{AGENTD_BUSY_STATUS_TIMEOUT_SECONDS}s; refusing to start a "
            "competing daemon against a live socket. Inspect the running "
            "host process and its log instead.\n"
            f"Log path: {paths.log_path}",
            socket_path=active_socket_path,
            start_command=tuple(command),
        )

    try:
        _start_agentd_process(command, paths.log_path)
    except OSError as error:
        command_text = shlex.join(command)
        raise AgentdUnavailableError(
            "doeff-agentd is not reachable at the expected socket "
            f"{active_socket_path}, and starting it failed: {error}. "
            "Start command:\n"
            f"  {command_text}\n"
            f"Expected socket path: {active_socket_path}\n"
            f"Log path: {paths.log_path}",
            socket_path=active_socket_path,
            start_command=tuple(command),
        ) from error

    if _wait_for_agentd_ready(
        client,
        expected_db_path=active_db_path,
        expected_socket_path=active_socket_path,
        command=command,
        timeout=timeout,
    ):
        return client

    command_text = shlex.join(command)
    raise AgentdUnavailableError(
        "doeff-agentd is not reachable at the expected socket "
        f"{active_socket_path} after starting it. Start command:\n"
        f"  {command_text}\n"
        f"Expected socket path: {active_socket_path}\n"
        f"Log path: {paths.log_path}",
        socket_path=active_socket_path,
        start_command=tuple(command),
    )


def _prepare_agentd_paths(db_path: Path, socket_path: Path, log_path: Path) -> None:
    for directory in (db_path.parent, socket_path.parent, log_path.parent):
        directory.mkdir(parents=True, exist_ok=True)


def _validate_agentd_identity(
    status: Mapping[str, Any],
    *,
    expected_db_path: Path,
    expected_socket_path: Path,
    command: list[str],
) -> None:
    daemon_db = status.get("db_path")
    if isinstance(daemon_db, str) and _normalize_path(daemon_db) != _normalize_path(
        expected_db_path
    ):
        raise AgentdUnavailableError(
            "doeff-agentd is reachable at the expected socket "
            f"{expected_socket_path}, but it is using a different database: "
            f"{daemon_db}. Expected database: {expected_db_path}. "
            "Stop the stale daemon bound to this socket and start the "
            "canonical daemon with:\n"
            f"  {shlex.join(command)}\n"
            f"Expected socket path: {expected_socket_path}",
            socket_path=expected_socket_path,
            start_command=tuple(command),
        )


def _start_agentd_process(command: list[str], log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as log_file:
        subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )


def _wait_for_agentd_ready(
    client: AgentdClient,
    *,
    expected_db_path: Path,
    expected_socket_path: Path,
    command: list[str],
    timeout: float,
) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        status = _agentd_status_if_ready(client)
        if status is not None:
            _validate_agentd_identity(
                status,
                expected_db_path=expected_db_path,
                expected_socket_path=expected_socket_path,
                command=command,
            )
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        _sleep_for_agentd_start(min(AGENTD_START_POLL_SECONDS, remaining))


def _sleep_for_agentd_start(seconds: float) -> None:
    time.sleep(seconds)


def _agentd_status_if_ready(client: AgentdClient) -> Mapping[str, Any] | None:
    try:
        return client.status()
    except OSError:
        return None
    except AgentdClientError:
        return None


def _socket_has_live_listener(socket_path: Path, *, connect_timeout: float = 1.0) -> bool:
    """True iff something is accepting connections on the socket.

    Only a missing path or ECONNREFUSED (stale socket file, no listener)
    proves absence.  A successful connect proves presence, and any other
    OSError (e.g. backlog-full timeout under load) is treated as
    presence too: the fail-safe direction is to never spawn a competing
    daemon on an unproven death.
    """
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    probe.settimeout(connect_timeout)
    try:
        probe.connect(str(socket_path))
        return True
    except (ConnectionRefusedError, FileNotFoundError):
        return False
    except OSError:
        return True
    finally:
        probe.close()


def _agentd_status_from_live_listener(client: AgentdClient) -> Mapping[str, Any] | None:
    try:
        result = client.request(
            "daemon.status",
            read_timeout=AGENTD_BUSY_STATUS_TIMEOUT_SECONDS,
        )
    except OSError:
        return None
    except AgentdClientError:
        return None
    if not isinstance(result, Mapping):
        return None
    return result


def _normalize_path(path: str | Path) -> str:
    return str(Path(path).expanduser().resolve())


def _agentd_command(
    *,
    daemon_bin: str | Path | None,
    db_path: Path,
    socket_path: Path,
    max_running: int,
) -> list[str]:
    prefix = [str(daemon_bin)] if daemon_bin is not None else [_resolve_agentd_binary()]
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


def _resolve_agentd_binary() -> str:
    """Resolve the canonical agentd executable: the Hy session host.

    Retirement (DOE-004, user GO 2026-07-06): the Rust ``doeff-agentd``
    binary is no longer a spawn target — auto-(re)starting it silently
    rolled the executor back and invalidated canary observation (ADR 0045
    R5 in agent-control-plane).  The session host ships WITH this package,
    so the console script installed next to the running interpreter is the
    deterministic default; ``DOEFF_AGENTD_BIN`` stays as the explicit
    override seam (tests, oracle runs).
    """
    if env_bin := os.environ.get("DOEFF_AGENTD_BIN"):
        return env_bin
    sibling = Path(sys.executable).parent / "doeff-sessionhost"
    if sibling.exists() and os.access(sibling, os.X_OK):
        return str(sibling)
    if path_bin := shutil.which("doeff-sessionhost"):
        return path_bin
    return "doeff-sessionhost"


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


def _session_list_from_result(result: Any) -> AgentdSessionList:
    if not isinstance(result, list):
        raise AgentdProtocolError("session.list returned a non-list result")

    snapshots: list[AgentSessionSnapshot] = []
    warnings: list[AgentdSessionParseWarning] = []
    for index, item in enumerate(result):
        warning = _snapshot_preflight_warning(item, index)
        if warning is not None:
            warnings.append(warning)
            continue
        try:
            snapshots.append(_snapshot_from_result(item))
        except (KeyError, TypeError, ValueError) as error:
            warnings.append(_snapshot_parse_error_warning(item, index, error))
    return AgentdSessionList(snapshots=tuple(snapshots), warnings=tuple(warnings))


def _snapshot_preflight_warning(
    item: Any,
    index: int,
) -> AgentdSessionParseWarning | None:
    if not isinstance(item, Mapping):
        return AgentdSessionParseWarning(
            session_name=_fallback_session_name(item, index),
            field="<row>",
            raw_value=item,
        )

    for field, enum_value in (
        ("agent_type", AgentType),
        ("status", SessionStatus),
    ):
        warning = _enum_field_warning(item, index, field, enum_value, required=True)
        if warning is not None:
            return warning

    return _enum_field_warning(
        item,
        index,
        "lifecycle",
        AgentSessionLifecycle,
        required=False,
    )


def _enum_field_warning(
    item: Mapping[str, Any],
    index: int,
    field: str,
    enum_value: Callable[[str], object],
    *,
    required: bool,
) -> AgentdSessionParseWarning | None:
    if field not in item:
        if required:
            return AgentdSessionParseWarning(
                session_name=_fallback_session_name(item, index),
                field=field,
                raw_value=None,
            )
        return None

    raw_value = item[field]
    try:
        enum_value(str(raw_value))
    except ValueError:
        return AgentdSessionParseWarning(
            session_name=_fallback_session_name(item, index),
            field=field,
            raw_value=raw_value,
        )
    return None


def _snapshot_parse_error_warning(
    item: Any,
    index: int,
    error: KeyError | TypeError | ValueError,
) -> AgentdSessionParseWarning:
    if isinstance(item, Mapping) and isinstance(error, KeyError):
        field = str(error).strip("'")
        return AgentdSessionParseWarning(
            session_name=_fallback_session_name(item, index),
            field=field,
            raw_value=item.get(field),
        )
    return AgentdSessionParseWarning(
        session_name=_fallback_session_name(item, index),
        field="<snapshot>",
        raw_value=item,
    )


def _fallback_session_name(item: Any, index: int) -> str:
    if isinstance(item, Mapping):
        session_name = item.get("session_name") or item.get("session_id")
        if session_name is not None:
            return str(session_name)
    return f"<row {index}>"


def _await_outcome_from_result(result: Mapping[str, Any]) -> AwaitOutcome:
    if "session" not in result:
        raise AgentdProtocolError(
            "session.await_result payload is missing session "
            f"(payload shape: {_mapping_shape(result)})"
        )
    session = result["session"]
    if not isinstance(session, Mapping):
        raise AgentdProtocolError(
            "session.await_result session was non-object "
            f"(session type: {type(session).__name__})"
        )
    if "status" not in session:
        raise AgentdProtocolError(
            "session.await_result session is missing status "
            f"(session shape: {_mapping_shape(session)})"
        )
    status_value = session["status"]
    if not isinstance(status_value, str):
        raise AgentdProtocolError(
            "session.await_result session status was not a string "
            f"(status type: {type(status_value).__name__})"
        )
    status = status_value
    validation_error = result.get("validation_error")
    if validation_error is not None and not isinstance(validation_error, str):
        raise AgentdProtocolError("session.await_result validation_error was not a string")

    # The daemon resolves an await only on TERMINAL session states: the
    # supervisor has already spent the result-contract retries and reaped
    # the pane, so no outcome from this mapping can accept a follow-up.
    if status in ("blocked", "blocked_api"):
        return AwaitOutcome(
            status=AwaitStatus.AWAITING_INPUT,
            validation_error=validation_error or status,
            continuable=False,
        )

    if "result" not in result:
        raise AgentdProtocolError(
            "session.await_result payload is missing result "
            f"(payload shape: {_mapping_shape(result)})"
        )
    response_result = result["result"]
    if response_result is None:
        return AwaitOutcome(
            status=AwaitStatus.EXITED,
            result=None,
            validation_error=validation_error,
            continuable=False,
        )
    if not isinstance(response_result, Mapping):
        raise AgentdProtocolError(
            "session.await_result result was non-object "
            f"(result type: {type(response_result).__name__})"
        )
    if "payload" not in response_result:
        raise AgentdProtocolError(
            "session.await_result result is missing payload "
            f"(result shape: {_mapping_shape(response_result)})"
        )
    payload = response_result["payload"]

    return AwaitOutcome(
        status=AwaitStatus.EXITED,
        result=payload,
        validation_error=validation_error,
        continuable=False,
    )


def _mapping_shape(mapping: Mapping[str, Any]) -> str:
    fields = ", ".join(f"{key}: {type(value).__name__}" for key, value in mapping.items())
    return f"{{{fields}}}"


__all__ = [
    "RPC_ERR_AWAIT_TIMEOUT",
    "RPC_ERR_NO_SUCH_SESSION",
    "AgentdClient",
    "AgentdClientError",
    "AgentdPaths",
    "AgentdProtocolError",
    "AgentdSessionList",
    "AgentdSessionParseWarning",
    "AgentdUnavailableError",
    "LazyAgentdClient",
    "default_agentd_paths",
    "ensure_agentd",
]
