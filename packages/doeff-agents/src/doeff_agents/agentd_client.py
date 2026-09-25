"""JSON-line client for the doeff-agentd Unix socket API.

段 7 lane 7c(agora-redesign・決定 1.3): protocol の組み立てと解釈・起動の
判断はこの module に残り、socket の 1 往復・子 process・file・環境変数の
読みは `doeff_agents.io_effects` の要求になった。実行する家は本番
`doeff_agents.io_handlers` と検 `doeff_agents.io_fake` の 2 つで、どちらを
当てるかは呼び手(composition root)が ``io_root`` で選ぶ。
"""

import json
import posixpath
import shlex
import sys
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import hy  # noqa: F401  # .hy import hook — the I/O effect vocabulary is a Hy module
from doeff import do

from doeff_agents.adapters.base import AgentType
from doeff_agents.io_root import (
    IoGenerator,
    IoRoot,
    as_bool,
    as_float,
    as_int,
    as_optional_str,
    as_process_outcome,
    as_str,
)
from doeff_agents.io_effects import (
    env_value,
    home_path,
    executable_at,
    make_dirs,
    monotonic_time,
    read_text,
    run_process,
    sleep as io_sleep,
    spawn_detached,
    unix_connect_probe,
    unix_line_request,
    which_executable,
)
from doeff_agents.effects import (
    AgentSessionLifecycle,
    AgentSessionQuery,
    AgentSessionSnapshot,
    AwaitOutcome,
    AwaitStatus,
)
from doeff_agents.monitor import SessionStatus

def _default_io_root() -> IoRoot:
    """既定の composition root: 本番の I/O handler。"""
    from doeff_agents.io_handlers import run_driver_io

    return run_driver_io


RPC_ERR_AWAIT_TIMEOUT = -32000
RPC_ERR_NO_SUCH_SESSION = -32001

# session.resume の expected_result は「明示 null = 契約なし」を「未指定 = carry」
# と区別する(ADR-DOE-AGENTS-006 改訂 R4)。未指定の番兵。
_UNSET: Any = object()


class AgentdClientError(RuntimeError):
    """Base error raised by the doeff-agentd client.

    error_code carries the wire `error_code` verbatim: the frozen oracle
    vocabulary is numeric (-32000..), koine session-surface additions
    (ADR-DOE-AGENTS-007) are typed strings (e.g. "adopt_target_not_found").
    """

    def __init__(self, message: str, *, error_code: int | str | None = None) -> None:
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


class AgentdSupervisorConfigError(AgentdClientError):
    """Raised when the supervisor declaration file exists but is invalid.

    Fail-closed: an unreadable declaration might be declaring supervision,
    so the ensure boundary refuses to act on it (and never falls back to a
    self-spawn) until the operator fixes the file.
    """

    def __init__(self, message: str, *, declaration_path: Path) -> None:
        self.declaration_path = declaration_path
        super().__init__(message)


@dataclass(frozen=True)
class AgentdPaths:
    """Default filesystem locations for the session host's state and socket."""

    db_path: Path
    socket_path: Path
    log_path: Path
    supervisor_path: Path
    #: True when $DOEFF_AGENTD_SOCKET named the socket instead of the XDG
    #: defaults deriving it.  A named socket belongs to whoever declared it, so
    #: callers use it or fail rather than starting a host against it.
    socket_is_named: bool = False


@dataclass(frozen=True, kw_only=True)
class AgentdSupervisorDeclaration:
    """Machine-level declaration that a socket is supervisor-managed.

    Written by the deployment (e.g. a launchd/systemd unit installer) next
    to the daemon state.  ``kick_command`` is configuration-injected argv
    that asks the supervisor to (re)start the daemon — doeff itself stays
    platform-agnostic and never hardcodes launchd, systemd, or a label.
    ``supervisor`` and ``label`` are informational (diagnostics only).
    """

    socket_path: Path
    kick_command: tuple[str, ...] | None
    supervisor: str | None
    label: str | None
    source_path: Path


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
# `session.launch` waits for agent REPL readiness (daemon ready gate,
# DOEFF_AGENTD_REPL_IDLE_MAX_WAIT_SECS, default 120s) and
# `session.await_result` waits up to its caller-supplied budget
# (clamp [1, 3600]).  The client socket timeout must therefore cover the
# daemon-side budget plus a margin — a short default here silently breaks
# the protocol (observed live: 10s client timeout vs 60s launch budget ->
# client disconnect, daemon Broken pipe).
RPC_TIMEOUT_MARGIN_SECONDS: float = 15.0
DAEMON_REPL_IDLE_MAX_WAIT_DEFAULT_SECONDS: float = 120.0


def launch_rpc_timeout_seconds() -> float:
    """session.launch の client read-timeout(daemon ready gate + margin)。

    daemon 側の launch 実時間を支配する予算は REPL ready gate
    (``DOEFF_AGENTD_REPL_IDLE_MAX_WAIT_SECS``、host.hy が use-site で読む)。
    client が固定値のままだと、knob で延長された daemon の ready 待ちを
    client が先に切断して Broken pipe / AgentRequestFailed になる
    (2026-07-27 sessionhost wedge incident)。同じ knob を同じ解釈
    (env_positive_i64: 0 以下・parse 失敗は既定 120s)で毎呼び出し読む。
    """
    return as_float(_default_io_root()(launch_rpc_timeout_program()))


def rpc_timeout_budget(raw: str | None) -> float:
    """Pure judgment: the client read-timeout implied by the daemon's ready-gate knob."""
    budget = DAEMON_REPL_IDLE_MAX_WAIT_DEFAULT_SECONDS
    if raw is not None:
        try:
            parsed: int | None = int(raw.strip())
        except ValueError:
            # daemon 側 env_positive_i64 と同値の解釈: parse 失敗は未設定扱い
            parsed = None
        if parsed is not None and parsed > 0:
            budget = float(parsed)
    return budget + RPC_TIMEOUT_MARGIN_SECONDS


@do
def launch_rpc_timeout_program() -> IoGenerator[float]:
    """Program reading the daemon ready-gate knob and answering the client budget."""
    raw = as_optional_str((yield env_value("DOEFF_AGENTD_REPL_IDLE_MAX_WAIT_SECS")))
    return rpc_timeout_budget(raw)
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
#: How long ending a session waits for the host's answer.  `session.cancel` and
#: `session.cleanup` bring the substrate's process down before they answer,
#: which outlives the 1s status-probe budget the client is built with: against
#: the headless backend the CLI printed "timed out" for a session it had in
#: fact stopped (mediagen #57).  The client stays the single authority for the
#: budget, as it is for the await budget above.
DEFAULT_END_SESSION_BUDGET_SECONDS: float = 60.0
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
#: Environment variable naming the socket to talk to, overriding the XDG
#: defaults.  A host started by `doeff-sessionhost join` listens under its own
#: state directory, which the defaults never name; this is the one place that
#: teaches every caller (the CLI included) where to look.  The spelling is the
#: one already used to tell an agent process where the host is, so no second
#: vocabulary appears.  The default order stays untouched when it is unset.
AGENTD_SOCKET_ENV = "DOEFF_AGENTD_SOCKET"


class AgentdClient:
    """Synchronous client for the long-lived agent supervisor daemon."""

    def __init__(
        self,
        socket_path: str | Path,
        *,
        timeout: float | None = 10.0,
        io_root: IoRoot | None = None,
    ) -> None:
        self.socket_path = Path(socket_path)
        self.timeout = timeout
        self._io: IoRoot = io_root if io_root is not None else _default_io_root()
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
            read_timeout=launch_rpc_timeout_seconds(),
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
        result = self.request(
            "session.cancel",
            {"session_id": session_id},
            read_timeout=DEFAULT_END_SESSION_BUDGET_SECONDS,
        )
        return _snapshot_from_result(result)

    def cleanup_session(self, session_id: str) -> AgentSessionSnapshot:
        result = self.request(
            "session.cleanup",
            {"session_id": session_id},
            read_timeout=DEFAULT_END_SESSION_BUDGET_SECONDS,
        )
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
        binding: Mapping[str, Any] | None = None,
        new_session_id: str | None = None,
        expected_result: Mapping[str, Any] | None = _UNSET,
    ) -> Mapping[str, Any]:
        """Host a new incarnation of the session's conversation
        (ADR-DOE-AGENTS-006 R4). Returns the raw wire snapshot of the NEW
        session row — callers need the ADR-006 fields (``conversation`` /
        ``generation`` / ``resumed_from_session_id``) that the typed
        AgentSessionSnapshot does not carry. Non-auth launch intent
        (session_env / model / effort / mcp_servers) is restored from the
        source row's persisted overlay; keyword arguments override per key.

        Cross-binding extension (ADR-006 revision): ``binding`` overrides the
        auth home reconstructed from the source row (the host transplants the
        transcript by symlink when the home differs); ``new_session_id`` names
        the new incarnation row (server minting ``<base>~g<N>`` when absent);
        ``expected_result`` overrides the unfulfilled-contract carry — passing
        it explicitly (even as None) wins over the carry, omitting it keeps
        the carry."""
        extra: dict[str, Any] = {}
        if binding is not None:
            extra["binding"] = dict(binding)
        if new_session_id is not None:
            extra["new_session_id"] = new_session_id
        if expected_result is not _UNSET:
            extra["expected_result"] = (
                dict(expected_result) if expected_result is not None else None
            )
        return self._incarnation_request("session.resume", session_id,
                                         prompt=prompt, model=model,
                                         effort=effort,
                                         mcp_servers=mcp_servers,
                                         session_env=session_env,
                                         extra=extra)

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
        extra: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        params: dict[str, Any] = {"session_id": session_id}
        if extra:
            params.update(extra)
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
        request_id = self._next_request_id()
        effective_timeout = read_timeout if read_timeout is not None else self.timeout
        line = as_str(
            self._io(
                unix_line_request(
                    str(self.socket_path),
                    request_line(request_id, method, params),
                    timeout=effective_timeout,
                )
            )
        )
        return parse_response_line(line, request_id=request_id, method=method)

    def _next_request_id(self) -> int:
        with self._request_lock:
            self._request_id += 1
            return self._request_id


def request_line(request_id: int, method: str, params: Mapping[str, Any] | None) -> str:
    """Pure judgment: the one JSON line that carries this request."""
    request = {"id": request_id, "method": method, "params": dict(params or {})}
    return json.dumps(request, separators=(",", ":")) + "\n"


def parse_response_line(line: str, *, request_id: int, method: str) -> Any:
    """Pure judgment: the result carried by one response line, or a typed failure."""
    if not line:
        raise AgentdProtocolError("doeff-sessionhost closed the connection without a response")
    response = json.loads(line)
    if not isinstance(response, Mapping):
        raise AgentdProtocolError("doeff-sessionhost returned a non-object response")
    if response.get("id") != request_id:
        raise AgentdProtocolError("doeff-sessionhost response id did not match request id")
    if not response.get("ok"):
        error = response.get("error")
        if not isinstance(error, str) or not error:
            error = "doeff-sessionhost request failed"
        error_code = response.get("error_code")
        if error_code is not None and not isinstance(error_code, (int, str)):
            raise AgentdProtocolError("doeff-sessionhost error_code was not an integer or string")
        raise AgentdClientError(error, error_code=error_code)
    if "result" not in response:
        raise AgentdProtocolError(
            f"{method} response is missing result "
            f"(response shape: {_mapping_shape(response)})"
        )
    return response["result"]


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


def agentd_paths_from_env(
    state_home: str | None,
    runtime_dir: str | None,
    user: str | None,
    home: str,
    socket_override: str | None = None,
) -> AgentdPaths:
    """Pure judgment: the XDG-style default paths implied by this environment.

    ``socket_override`` is ``$DOEFF_AGENTD_SOCKET``: a host started by
    ``doeff-sessionhost join`` listens under its own state directory, so the
    XDG defaults below never name it and the CLI could not observe it. The
    environment variable names the socket instead; the default order
    (``XDG_RUNTIME_DIR`` then ``/tmp``) is unchanged when it is absent.
    """
    state_root = Path(state_home) if state_home else Path(home) / ".local" / "state"
    if socket_override:
        socket_path = Path(socket_override)
    elif runtime_dir:
        socket_path = Path(runtime_dir) / "doeff" / "agentd.sock"
    else:
        socket_path = Path("/tmp") / f"doeff-agentd-{user or 'unknown'}.sock"
    state_dir = state_root / "doeff"
    return AgentdPaths(
        db_path=state_dir / "agentd.sqlite",
        socket_path=socket_path,
        log_path=state_dir / "agentd.log",
        supervisor_path=state_dir / "agentd.supervisor.json",
        socket_is_named=bool(socket_override),
    )


@do
def default_agentd_paths_program() -> IoGenerator[AgentdPaths]:
    """Program reading the environment and answering the default paths."""
    state_home = as_optional_str((yield env_value("XDG_STATE_HOME")))
    runtime_dir = as_optional_str((yield env_value("XDG_RUNTIME_DIR")))
    user = as_optional_str((yield env_value("USER")))
    if not user:
        user = as_optional_str((yield env_value("LOGNAME")))
    home = as_str((yield home_path()))
    socket_override = as_optional_str((yield env_value(AGENTD_SOCKET_ENV)))
    return agentd_paths_from_env(state_home, runtime_dir, user, home, socket_override)


def default_agentd_paths(*, io_root: IoRoot | None = None) -> AgentdPaths:
    """Return XDG-style default paths for doeff-agentd."""
    paths = (io_root or _default_io_root())(default_agentd_paths_program())
    if not isinstance(paths, AgentdPaths):
        raise TypeError(f"agentd の既定の path の形が違う: {paths!r}")
    return paths


_SUPERVISOR_DECLARATION_KEYS = frozenset(
    {"socket_path", "kick_command", "supervisor", "label"}
)


def load_supervisor_declaration(
    declaration_path: Path,
    *,
    io_root: IoRoot | None = None,
) -> AgentdSupervisorDeclaration | None:
    """Load the supervisor declaration, or None when the file is absent.

    The declaration is machine state, not caller state: it lives in the
    daemon state dir so EVERY ensure caller on the machine sees it, not
    just processes that inherited some environment variable.  Any defect
    in an existing file is a typed, loud config error — a typo must never
    silently re-enable the self-spawn path.
    """
    raw_text = as_optional_str((io_root or _default_io_root())(read_text(str(declaration_path))))
    if raw_text is None:
        return None
    try:
        raw = json.loads(raw_text)
    except json.JSONDecodeError as error:
        raise AgentdSupervisorConfigError(
            f"supervisor declaration {declaration_path} is not valid JSON: {error}",
            declaration_path=declaration_path,
        ) from error
    if not isinstance(raw, dict):
        raise AgentdSupervisorConfigError(
            f"supervisor declaration {declaration_path} must be a JSON object, "
            f"got {type(raw).__name__}",
            declaration_path=declaration_path,
        )
    unknown = sorted(set(raw) - _SUPERVISOR_DECLARATION_KEYS)
    if unknown:
        raise AgentdSupervisorConfigError(
            f"supervisor declaration {declaration_path} has unknown keys: "
            f"{', '.join(unknown)} (allowed: "
            f"{', '.join(sorted(_SUPERVISOR_DECLARATION_KEYS))})",
            declaration_path=declaration_path,
        )
    socket_value = raw.get("socket_path")
    if not isinstance(socket_value, str) or not socket_value:
        raise AgentdSupervisorConfigError(
            f"supervisor declaration {declaration_path} requires a non-empty "
            "string socket_path",
            declaration_path=declaration_path,
        )
    kick_value = raw.get("kick_command")
    kick_command: tuple[str, ...] | None
    if kick_value is None:
        kick_command = None
    elif (
        isinstance(kick_value, list)
        and kick_value
        and all(isinstance(item, str) and item for item in kick_value)
    ):
        kick_command = tuple(kick_value)
    else:
        raise AgentdSupervisorConfigError(
            f"supervisor declaration {declaration_path} kick_command must be a "
            "non-empty list of non-empty strings",
            declaration_path=declaration_path,
        )
    supervisor_value = raw.get("supervisor")
    if supervisor_value is not None and not isinstance(supervisor_value, str):
        raise AgentdSupervisorConfigError(
            f"supervisor declaration {declaration_path} supervisor must be a string",
            declaration_path=declaration_path,
        )
    label_value = raw.get("label")
    if label_value is not None and not isinstance(label_value, str):
        raise AgentdSupervisorConfigError(
            f"supervisor declaration {declaration_path} label must be a string",
            declaration_path=declaration_path,
        )
    return AgentdSupervisorDeclaration(
        socket_path=Path(socket_value),
        kick_command=kick_command,
        supervisor=supervisor_value,
        label=label_value,
        source_path=declaration_path,
    )


def agentd_socket_is_supervised(socket_path: str | Path) -> bool:
    """True iff the canonical declaration pins this exact socket."""
    declaration = load_supervisor_declaration(default_agentd_paths().supervisor_path)
    if declaration is None:
        return False
    return _normalize_path(declaration.socket_path) == _normalize_path(socket_path)


def ensure_agentd(
    *,
    db_path: str | Path | None = None,
    socket_path: str | Path | None = None,
    daemon_bin: str | Path | None = None,
    timeout: float = 5.0,
    client_timeout: float = 1.0,
    max_running: int = 10,
    io_root: IoRoot | None = None,
) -> AgentdClient:
    """Return a client for the canonical daemon, starting it when necessary.

    ``io_root`` is the composition-root choice of I/O 家 — the production
    handler by default, the in-memory fake in tests.
    """
    root: IoRoot = io_root if io_root is not None else _default_io_root()
    paths = default_agentd_paths(io_root=root)
    active_db_path = Path(db_path) if db_path is not None else paths.db_path
    active_socket_path = Path(socket_path) if socket_path is not None else paths.socket_path
    client = AgentdClient(active_socket_path, timeout=client_timeout, io_root=root)
    command = _agentd_command(
        daemon_bin=daemon_bin,
        db_path=active_db_path,
        socket_path=active_socket_path,
        max_running=max_running,
        io_root=root,
    )
    root(prepare_agentd_paths_program(active_db_path, active_socket_path, paths.log_path))
    # Loaded unconditionally so a malformed declaration is a loud, typed
    # config error on EVERY ensure call — not a surprise at the next
    # restart window.  An unparseable file might be declaring any socket,
    # so no call on this machine may treat it as absent.
    declaration = load_supervisor_declaration(paths.supervisor_path, io_root=root)
    # A socket named by $DOEFF_AGENTD_SOCKET belongs to whoever declared it --
    # a `join`ed host, launchd, systemd.  Naming it says "talk to that host",
    # so this call uses it or fails; it neither audits the host's database
    # against the canonical one (a joined host keeps its own, by design) nor
    # starts a competitor against someone else's socket.
    named_socket = paths.socket_is_named and socket_path is None
    status = _agentd_status_if_ready(client)
    if status is not None:
        if not named_socket:
            _validate_agentd_identity(
                status,
                expected_db_path=active_db_path,
                expected_socket_path=active_socket_path,
                command=command,
            )
        return client

    if named_socket:
        raise AgentdUnavailableError(
            f"{AGENTD_SOCKET_ENV} names {active_socket_path}, but nothing is "
            "listening there. That socket belongs to whoever declared it (a "
            "host started by `doeff-sessionhost join`, launchd, systemd), so "
            "this command will not start one against it. Start the host there, "
            f"or unset {AGENTD_SOCKET_ENV} to use the canonical one:\n"
            f"  {shlex.join(command)}",
            socket_path=active_socket_path,
            start_command=tuple(command),
        )

    # Spawn predicate: only the ABSENCE of a live listener proves the
    # daemon is dead.  A listener that accepts connect() but misses the
    # short status probe is alive-but-busy (slow != dead); starting a
    # competitor against it corrupts the lease and the store, so that
    # path retries with a long budget and then fails loudly instead.
    if _socket_has_live_listener(active_socket_path, io_root=root):
        return _client_from_live_listener(
            client,
            active_db_path=active_db_path,
            active_socket_path=active_socket_path,
            command=command,
            log_path=paths.log_path,
        )

    # Single-supervisor principle (issue #558, ACP ADR 0024): proven
    # listener absence licenses a self-spawn ONLY for unsupervised
    # sockets.  A declared supervisor (launchd, systemd, ...) owns start
    # and restart; spawning here during its restart window (bootout,
    # crash throttle) binds a rogue unsupervised host and split-brains
    # the store, so ensure delegates via the declared kick command or
    # fails loudly.
    if declaration is not None and _normalize_path(
        declaration.socket_path
    ) == _normalize_path(active_socket_path):
        return _delegate_to_supervisor(
            client,
            declaration,
            active_db_path=active_db_path,
            active_socket_path=active_socket_path,
            command=command,
            log_path=paths.log_path,
            timeout=timeout,
            io_root=root,
        )

    try:
        root(start_agentd_process_program(command, paths.log_path))
    except OSError as error:
        command_text = shlex.join(command)
        raise AgentdUnavailableError(
            "doeff-sessionhost is not reachable at the expected socket "
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
        io_root=root,
    ):
        return client

    command_text = shlex.join(command)
    raise AgentdUnavailableError(
        "doeff-sessionhost is not reachable at the expected socket "
        f"{active_socket_path} after starting it. Start command:\n"
        f"  {command_text}\n"
        f"Expected socket path: {active_socket_path}\n"
        f"Log path: {paths.log_path}",
        socket_path=active_socket_path,
        start_command=tuple(command),
    )


def _client_from_live_listener(
    client: AgentdClient,
    *,
    active_db_path: Path,
    active_socket_path: Path,
    command: list[str],
    log_path: Path,
) -> AgentdClient:
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
        "doeff-sessionhost has a live listener on "
        f"{active_socket_path} but did not answer daemon.status within "
        f"{AGENTD_BUSY_STATUS_TIMEOUT_SECONDS}s; refusing to start a "
        "competing daemon against a live socket. Inspect the running "
        "host process and its log instead.\n"
        f"Log path: {log_path}",
        socket_path=active_socket_path,
        start_command=tuple(command),
    )


AGENTD_SUPERVISOR_KICK_TIMEOUT_SECONDS: float = 10.0


def _supervisor_identity(declaration: AgentdSupervisorDeclaration) -> str:
    parts = [f"supervisor: {declaration.supervisor or 'undeclared'}"]
    if declaration.label is not None:
        parts.append(f"label: {declaration.label}")
    parts.append(f"declaration: {declaration.source_path}")
    return ", ".join(parts)


def _delegate_to_supervisor(
    client: AgentdClient,
    declaration: AgentdSupervisorDeclaration,
    *,
    active_db_path: Path,
    active_socket_path: Path,
    command: list[str],
    log_path: Path,
    timeout: float,
    io_root: IoRoot,
) -> AgentdClient:
    identity = _supervisor_identity(declaration)
    if declaration.kick_command is None:
        raise AgentdUnavailableError(
            f"doeff-sessionhost socket {active_socket_path} is declared "
            f"supervisor-managed ({identity}) and has no live listener; "
            "refusing to self-spawn a host the supervisor does not manage. "
            "Start or restart the daemon through its supervisor (the "
            "declaration provides no kick_command).\n"
            f"Log path: {log_path}",
            socket_path=active_socket_path,
            start_command=(),
        )

    kick = list(declaration.kick_command)
    try:
        outcome = as_process_outcome(
            io_root(run_process(tuple(kick), timeout=AGENTD_SUPERVISOR_KICK_TIMEOUT_SECONDS))
        )
    except OSError as error:
        raise AgentdUnavailableError(
            f"doeff-sessionhost socket {active_socket_path} is supervisor-managed "
            f"({identity}) and its kick command failed to run: {error}.\n"
            f"Kick command: {shlex.join(kick)}\n"
            f"Log path: {log_path}",
            socket_path=active_socket_path,
            start_command=declaration.kick_command,
        ) from error
    if outcome.timed_out or outcome.exit_code != 0:
        detail = outcome.stderr.strip() or outcome.stdout.strip()
        raise AgentdUnavailableError(
            f"doeff-sessionhost socket {active_socket_path} is supervisor-managed "
            f"({identity}) and its kick command exited with "
            f"exit code {outcome.exit_code}: {detail}\n"
            f"Kick command: {shlex.join(kick)}\n"
            f"Log path: {log_path}",
            socket_path=active_socket_path,
            start_command=declaration.kick_command,
        )

    if _wait_for_agentd_ready(
        client,
        expected_db_path=active_db_path,
        expected_socket_path=active_socket_path,
        command=command,
        timeout=timeout,
        io_root=io_root,
    ):
        return client

    raise AgentdUnavailableError(
        f"doeff-sessionhost socket {active_socket_path} is supervisor-managed "
        f"({identity}); the kick command succeeded but the daemon did not "
        f"become ready within {timeout}s. Inspect the supervisor state and "
        "the daemon log.\n"
        f"Kick command: {shlex.join(kick)}\n"
        f"Log path: {log_path}",
        socket_path=active_socket_path,
        start_command=declaration.kick_command,
    )


@do
def prepare_agentd_paths_program(db_path: Path, socket_path: Path, log_path: Path) -> IoGenerator[None]:
    """Program creating the state directories the daemon writes into."""
    for directory in (db_path.parent, socket_path.parent, log_path.parent):
        yield make_dirs(str(directory))
    return None


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
            "doeff-sessionhost is reachable at the expected socket "
            f"{expected_socket_path}, but it is using a different database: "
            f"{daemon_db}. Expected database: {expected_db_path}. "
            "Stop the stale daemon bound to this socket and start the "
            "canonical daemon with:\n"
            f"  {shlex.join(command)}\n"
            f"Expected socket path: {expected_socket_path}",
            socket_path=expected_socket_path,
            start_command=tuple(command),
        )


@do
def start_agentd_process_program(command: list[str], log_path: Path) -> IoGenerator[int]:
    """Program starting the daemon detached, with its output appended to the log."""
    yield make_dirs(str(log_path.parent))
    pid = as_int((yield spawn_detached(tuple(command), str(log_path))))
    return pid


def _wait_for_agentd_ready(
    client: AgentdClient,
    *,
    expected_db_path: Path,
    expected_socket_path: Path,
    command: list[str],
    timeout: float,
    io_root: IoRoot,
) -> bool:
    deadline = as_float(io_root(monotonic_time())) + timeout
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
        remaining = deadline - as_float(io_root(monotonic_time()))
        if remaining <= 0:
            return False
        io_root(io_sleep(min(AGENTD_START_POLL_SECONDS, remaining)))


def _agentd_status_if_ready(client: AgentdClient) -> Mapping[str, Any] | None:
    try:
        return client.status()
    except OSError:
        return None
    except AgentdClientError:
        return None


def listener_present(verdict: str) -> bool:
    """Pure judgment: only a proven refusal licenses "no listener".

    Only a missing path or ECONNREFUSED (stale socket file, no listener)
    proves absence.  A successful connect proves presence, and an
    unreachable probe (e.g. backlog-full timeout under load) is treated as
    presence too: the fail-safe direction is to never spawn a competing
    daemon on an unproven death.
    """
    return verdict != "refused"


def _socket_has_live_listener(
    socket_path: Path,
    *,
    connect_timeout: float = 1.0,
    io_root: IoRoot | None = None,
) -> bool:
    """True iff something is accepting connections on the socket."""
    verdict = as_str(
        (io_root or _default_io_root())(unix_connect_probe(str(socket_path), connect_timeout))
    )
    return listener_present(verdict)


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
    io_root: IoRoot | None = None,
) -> list[str]:
    prefix = (
        [str(daemon_bin)]
        if daemon_bin is not None
        else [as_str((io_root or _default_io_root())(resolve_agentd_binary_program()))]
    )
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


@do
def resolve_agentd_binary_program() -> IoGenerator[str]:
    """Program resolving the canonical agentd executable: the Hy session host.

    Retirement (DOE-004, user GO 2026-07-06): the Rust ``doeff-agentd``
    binary is no longer a spawn target — auto-(re)starting it silently
    rolled the executor back and invalidated canary observation (ADR 0045
    R5 in agent-control-plane).  The session host ships WITH this package,
    so the console script installed next to the running interpreter is the
    deterministic default; ``DOEFF_AGENTD_BIN`` stays as the explicit
    override seam (tests, oracle runs).
    """
    env_bin = as_optional_str((yield env_value("DOEFF_AGENTD_BIN")))
    if env_bin:
        return env_bin
    sibling = posixpath.join(posixpath.dirname(sys.executable), "doeff-sessionhost")
    runnable = as_bool((yield executable_at(sibling)))
    if runnable:
        return sibling
    path_bin = as_optional_str((yield which_executable("doeff-sessionhost")))
    if path_bin:
        return path_bin
    return "doeff-sessionhost"


def _query_to_params(query: AgentSessionQuery | None) -> dict[str, Any]:
    if query is None:
        return {}
    if query.caller_ref is not None or query.node is not None:
        # agentd's session.list has no such filter; dropping it would widen
        # the answer silently (#608 added the fields for the session store).
        raise ValueError("agentd session.list cannot filter by caller_ref or node")
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
        raise AgentdProtocolError("doeff-sessionhost returned a non-object session snapshot")
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
    "AgentdSupervisorConfigError",
    "AgentdSupervisorDeclaration",
    "AgentdUnavailableError",
    "LazyAgentdClient",
    "agentd_socket_is_supervised",
    "default_agentd_paths",
    "ensure_agentd",
    "load_supervisor_declaration",
]
