"""
OpenCode handler for doeff-agentic.

This module provides the handler that uses OpenCode's HTTP API for agent session management.

Usage:
    from doeff import run
    from doeff_agentic.opencode_handler import opencode_handler
    from doeff_core_effects.handlers import slog_handler
    from doeff_core_effects.scheduler import scheduled

    handlers = opencode_handler()
    program = slog_handler(handlers(my_workflow()))
    result = run(scheduled(program))
"""


import contextlib
import hashlib
import json
import shutil
import subprocess
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from doeff import Await, Effect, Pass, Resume, do, handler, slog
from doeff.program import ProgramHandler
from doeff_agentic.effects import (
    AgenticAbortSession,
    AgenticCreateEnvironment,
    AgenticCreateSession,
    AgenticCreateWorkflow,
    AgenticDeleteEnvironment,
    AgenticDeleteSession,
    AgenticForkSession,
    AgenticGetEnvironment,
    AgenticGetMessages,
    AgenticGetSession,
    AgenticGetSessionStatus,
    AgenticGetWorkflow,
    AgenticNextEvent,
    AgenticSendMessage,
    AgenticSupportsCapability,
)
from doeff_agentic.event_log import EventLogWriter, WorkflowIndex
from doeff_agentic.exceptions import (
    AgenticDuplicateNameError,
    AgenticEnvironmentInUseError,
    AgenticEnvironmentNotFoundError,
    AgenticServerError,
    AgenticSessionNotFoundError,
    AgenticTimeoutError,
)
from doeff_agentic.handlers._effect_groups import MessageEffect, SessionEffect, WorkflowEffect
from doeff_agentic.types import (
    AgenticEndOfEvents,
    AgenticEnvironmentHandle,
    AgenticEnvironmentType,
    AgenticEvent,
    AgenticMessage,
    AgenticMessageHandle,
    AgenticSessionHandle,
    AgenticSessionStatus,
    AgenticWorkflowHandle,
    AgenticWorkflowStatus,
)

# =============================================================================
# HTTP Client
# =============================================================================


class AsyncHttpClient:
    """Asynchronous HTTP client using httpx.

    All methods return Program[..., T] that yield Await effects for async HTTP calls.
    This allows handlers to simply `yield client.get(...)` without dealing
    with Await directly.
    """

    def __init__(self, base_url: str, timeout: float = 30.0) -> None:
        import httpx

        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout)
        self.base_url = base_url

    @do
    def get(self, path: str, **kwargs: Any):
        """GET request returning Program[..., dict[str, Any]]."""
        resp = yield Await(self._client.get(path, **kwargs))
        resp.raise_for_status()
        return resp.json()

    @do
    def post(self, path: str, **kwargs: Any):
        """POST request returning Program[..., dict[str, Any] | None]."""
        resp = yield Await(self._client.post(path, **kwargs))
        resp.raise_for_status()
        if resp.status_code == 204:
            return None
        return resp.json()

    @do
    def delete(self, path: str, **kwargs: Any):
        """DELETE request returning Program[..., bool]."""
        resp = yield Await(self._client.delete(path, **kwargs))
        resp.raise_for_status()
        return resp.json() if resp.content else True

    @do
    def patch(self, path: str, **kwargs: Any):
        """PATCH request returning Program[..., dict[str, Any]]."""
        resp = yield Await(self._client.patch(path, **kwargs))
        resp.raise_for_status()
        return resp.json()

    @do
    def close(self):
        """Close client, returning Program[..., None]."""
        yield Await(self._client.aclose())


# =============================================================================
# State Management
# =============================================================================


@dataclass
class _OpenCodeWorkflow:
    """In-memory state for a workflow."""

    id: str
    name: str | None
    status: AgenticWorkflowStatus
    created_at: datetime
    metadata: dict[str, Any] | None = None
    environments: dict[str, AgenticEnvironmentHandle] = field(default_factory=dict)
    sessions: dict[str, AgenticSessionHandle] = field(default_factory=dict)  # name -> handle
    session_by_id: dict[str, str] = field(default_factory=dict)  # session_id -> name


@dataclass
class _OpenCodeWorld:
    """The state one ``opencode_handler()`` handler keeps in its closure."""

    server_url: str | None
    hostname: str
    port: int | None
    startup_timeout: float
    working_dir: Path
    event_log: EventLogWriter
    workflow_index: WorkflowIndex
    client: AsyncHttpClient | None = None
    server_process: subprocess.Popen | None = None
    workflow: _OpenCodeWorkflow | None = None
    # session_id -> the open SSE stream: the sync httpx client, the response and its line iterator
    sse_connections: dict[str, Any] = field(default_factory=dict)


def generate_workflow_id(name: str | None = None) -> str:
    """Generate a 7-char hex workflow ID."""
    data = f"{name or 'workflow'}-{time.time()}"
    return hashlib.sha256(data.encode()).hexdigest()[:7]


def generate_environment_id(name: str | None = None) -> str:
    """Generate environment ID."""
    data = f"env-{name or 'default'}-{time.time()}"
    return hashlib.sha256(data.encode()).hexdigest()[:8]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_datetime_or_now(value: Any) -> datetime:
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    return _utc_now()


_SUPPORTED_CAPABILITIES = frozenset({"fork", "events", "worktree"})

_STATUS_BY_OPENCODE_NAME = {
    "pending": AgenticSessionStatus.PENDING,
    "booting": AgenticSessionStatus.BOOTING,
    "running": AgenticSessionStatus.RUNNING,
    "blocked": AgenticSessionStatus.BLOCKED,
    "done": AgenticSessionStatus.DONE,
    "error": AgenticSessionStatus.ERROR,
    "aborted": AgenticSessionStatus.ABORTED,
}


# =============================================================================
# Server lifecycle
# =============================================================================


def _initialize(world: _OpenCodeWorld) -> AsyncHttpClient:
    """Connect to the OpenCode server (starting one if no URL was given) the first time it is needed."""
    if world.client is not None:
        return world.client

    if world.server_url:
        _check_health_sync(world.server_url)
        world.client = AsyncHttpClient(world.server_url)
    else:
        world.client = _start_server(world)
    return world.client


def _check_health_sync(server_url: str) -> None:
    """Check if server is healthy (sync, for startup only)."""
    import httpx

    try:
        resp = httpx.get(f"{server_url}/global/health", timeout=5.0)
        result = resp.json()
        if not result.get("healthy"):
            raise AgenticServerError("Server reports unhealthy")
    except Exception as e:
        raise AgenticServerError(f"Health check failed: {e}") from None


def _start_server(world: _OpenCodeWorld) -> AsyncHttpClient:
    """Auto-start OpenCode server and return a client for it."""
    opencode_bin = shutil.which("opencode")
    if not opencode_bin:
        raise AgenticServerError("opencode binary not found in PATH")

    cmd = [opencode_bin, "serve", "--hostname", world.hostname]
    if world.port:
        cmd.extend(["--port", str(world.port)])

    world.server_process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=world.working_dir,
    )

    # Wait for server to be ready
    url = f"http://{world.hostname}:{world.port or 4096}"
    deadline = time.time() + world.startup_timeout
    while time.time() < deadline:
        try:
            import httpx

            resp = httpx.get(f"{url}/global/health", timeout=1.0)
            if resp.status_code == 200 and resp.json().get("healthy"):
                world.server_url = url
                return AsyncHttpClient(url)
        except Exception:
            pass
        time.sleep(0.1)

    world.server_process.terminate()
    raise AgenticServerError(f"Server failed to start within {world.startup_timeout}s")


# =============================================================================
# Helpers
# =============================================================================


def _ensure_workflow(world: _OpenCodeWorld) -> _OpenCodeWorkflow:
    """The current workflow, made (with the server connection) on the first effect that needs one."""
    if world.workflow is None:
        _initialize(world)
        workflow_id = generate_workflow_id(None)
        world.workflow = _OpenCodeWorkflow(
            id=workflow_id,
            name=None,
            status=AgenticWorkflowStatus.RUNNING,
            created_at=datetime.now(timezone.utc),
            metadata=None,
        )
        world.event_log.log_workflow_created(workflow_id, None, None)
        world.workflow_index.add(workflow_id, None)
    return world.workflow


def _client(world: _OpenCodeWorld) -> AsyncHttpClient:
    _ensure_workflow(world)
    assert world.client is not None
    return world.client


def _create_worktree(world: _OpenCodeWorld, env_id: str, base_commit: str | None) -> str:
    """Create a git worktree."""
    worktree_dir = Path(f"/tmp/doeff/worktrees/{env_id}")
    worktree_dir.parent.mkdir(parents=True, exist_ok=True)

    cmd = ["git", "worktree", "add", str(worktree_dir)]
    if base_commit:
        cmd.append(base_commit)
    else:
        cmd.extend(["--detach", "HEAD"])

    subprocess.run(cmd, cwd=world.working_dir, check=True, capture_output=True)
    return str(worktree_dir)


def _delete_worktree(world: _OpenCodeWorld, path: str) -> None:
    """Remove a git worktree."""
    try:
        subprocess.run(
            ["git", "worktree", "remove", "--force", path],
            cwd=world.working_dir,
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError:
        # Force remove directory if worktree removal fails
        shutil.rmtree(path, ignore_errors=True)


def _copy_directory(source: str, env_id: str) -> str:
    """Copy directory for COPY environment type."""
    dest = Path(f"/tmp/doeff/copies/{env_id}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, dest)
    return str(dest)


def _source_environment(
    workflow: _OpenCodeWorkflow, effect: AgenticCreateEnvironment, env_kind: str
) -> AgenticEnvironmentHandle:
    if not effect.source_environment_id:
        raise ValueError(f"{env_kind} type requires source_environment_id")
    source = workflow.environments.get(effect.source_environment_id)
    if not source:
        raise AgenticEnvironmentNotFoundError(effect.source_environment_id)
    return source


def _update_session_status(
    world: _OpenCodeWorld, session_id: str, status: AgenticSessionStatus
) -> None:
    """Record a session's new status locally, and log it when it changed."""
    if world.workflow is None:
        return

    name = world.workflow.session_by_id.get(session_id)
    if not name:
        return

    session = world.workflow.sessions[name]
    world.workflow.sessions[name] = replace(session, status=status)
    if session.status != status:
        world.event_log.log_session_status(world.workflow.id, name, status)


@do
def _refresh_session_status(world: _OpenCodeWorld, client: AsyncHttpClient, session_id: str):
    """Refresh session status from server. Returns Program[..., None]."""
    try:
        result = yield client.get("/session/status")
        status = _STATUS_BY_OPENCODE_NAME.get(
            result.get(session_id, "pending"), AgenticSessionStatus.PENDING
        )
        _update_session_status(world, session_id, status)
    except Exception:
        pass


def _sse_connection(world: _OpenCodeWorld, client: AsyncHttpClient, session_id: str) -> Any:
    """The session's open SSE stream of the server's events, opened on its first AgenticNextEvent."""
    if session_id not in world.sse_connections:
        import httpx

        sse_client = httpx.Client(timeout=None)
        # NOTE: We intentionally don't use a context manager here because
        # we need to keep the streaming connection open across multiple
        # AgenticNextEvent effects. The connection is closed in _close_sse.
        response = sse_client.stream("GET", f"{client.base_url}/event").__enter__()
        world.sse_connections[session_id] = {
            "client": sse_client,
            "response": response,
            "iter": response.iter_lines(),
        }
    return world.sse_connections[session_id]


def _close_sse(world: _OpenCodeWorld, session_id: str) -> None:
    """Close SSE connection for session."""
    conn = world.sse_connections.pop(session_id, None)
    if conn is not None:
        with contextlib.suppress(Exception):
            conn["response"].close()
            conn["client"].close()


def _message_from_api(session_id: str, msg: Any) -> AgenticMessage:
    """One OpenCode API message (``{"info": ..., "parts": [...]}``) as an AgenticMessage."""
    info: dict[str, Any] = msg.get("info", {}) if isinstance(msg, dict) else {}
    parts: list[dict[str, Any]] = msg.get("parts", []) if isinstance(msg, dict) else []
    text_parts = [
        part.get("text", "")
        for part in parts
        if isinstance(part, dict) and part.get("type") == "text"
    ]
    return AgenticMessage(
        id=info.get("id", "") if isinstance(info, dict) else "",
        session_id=session_id,
        role=info.get("role", "user") if isinstance(info, dict) else "user",
        content="\n".join(text_parts),
        created_at=_parse_datetime_or_now(
            info.get("createdAt") if isinstance(info, dict) else None
        ),
        parts=parts,
    )


# =============================================================================
# Answers (one per effect — each returns the Program whose value resumes k)
# =============================================================================


@do
def _create_workflow(world: _OpenCodeWorld, effect: AgenticCreateWorkflow):
    _initialize(world)

    workflow_id = generate_workflow_id(effect.name)
    world.workflow = _OpenCodeWorkflow(
        id=workflow_id,
        name=effect.name,
        status=AgenticWorkflowStatus.RUNNING,
        created_at=_utc_now(),
        metadata=effect.metadata,
    )

    world.event_log.log_workflow_created(workflow_id, effect.name, effect.metadata)
    world.workflow_index.add(workflow_id, effect.name)

    return AgenticWorkflowHandle(
        id=workflow_id,
        name=effect.name,
        status=AgenticWorkflowStatus.RUNNING,
        created_at=world.workflow.created_at,
        metadata=effect.metadata,
    )


@do
def _get_workflow(world: _OpenCodeWorld, effect: AgenticGetWorkflow):
    workflow = _ensure_workflow(world)
    return AgenticWorkflowHandle(
        id=workflow.id,
        name=workflow.name,
        status=workflow.status,
        created_at=workflow.created_at,
        metadata=workflow.metadata,
    )


@do
def _create_environment(world: _OpenCodeWorld, effect: AgenticCreateEnvironment):
    workflow = _ensure_workflow(world)
    env_id = generate_environment_id(effect.name)

    match effect.env_type:
        case AgenticEnvironmentType.SHARED:
            working_dir = effect.working_dir or str(world.working_dir)
        case AgenticEnvironmentType.WORKTREE:
            working_dir = _create_worktree(world, env_id, effect.base_commit)
        case AgenticEnvironmentType.INHERITED:
            working_dir = _source_environment(workflow, effect, "inherited").working_dir
        case AgenticEnvironmentType.COPY:
            source = _source_environment(workflow, effect, "copy")
            working_dir = _copy_directory(source.working_dir, env_id)
        case _:
            raise ValueError(f"Unknown environment type: {effect.env_type}")

    result = AgenticEnvironmentHandle(
        id=env_id,
        env_type=effect.env_type,
        name=effect.name,
        working_dir=working_dir,
        created_at=_utc_now(),
        base_commit=effect.base_commit,
        source_environment_id=effect.source_environment_id,
    )
    workflow.environments[env_id] = result
    world.event_log.log_environment_created(workflow.id, result)
    return result


@do
def _get_environment(world: _OpenCodeWorld, effect: AgenticGetEnvironment):
    result = _ensure_workflow(world).environments.get(effect.environment_id)
    if not result:
        raise AgenticEnvironmentNotFoundError(effect.environment_id)
    return result


@do
def _delete_environment(world: _OpenCodeWorld, effect: AgenticDeleteEnvironment):
    workflow = _ensure_workflow(world)
    handle = workflow.environments.get(effect.environment_id)
    if not handle:
        raise AgenticEnvironmentNotFoundError(effect.environment_id)

    using_sessions = [
        s.name for s in workflow.sessions.values() if s.environment_id == effect.environment_id
    ]
    if using_sessions and not effect.force:
        raise AgenticEnvironmentInUseError(effect.environment_id, using_sessions)

    if handle.env_type == AgenticEnvironmentType.WORKTREE:
        _delete_worktree(world, handle.working_dir)
    elif handle.env_type == AgenticEnvironmentType.COPY:
        shutil.rmtree(handle.working_dir, ignore_errors=True)

    world.event_log.log_environment_deleted(workflow.id, effect.environment_id, effect.force)
    del workflow.environments[effect.environment_id]
    return True


@do
def _create_session(world: _OpenCodeWorld, effect: AgenticCreateSession):
    client = _client(world)
    workflow = _ensure_workflow(world)

    if effect.name in workflow.sessions:
        raise AgenticDuplicateNameError(effect.name, workflow.id)

    if effect.environment_id:
        if effect.environment_id not in workflow.environments:
            raise AgenticEnvironmentNotFoundError(effect.environment_id)
        env_id = effect.environment_id
    else:
        # Create implicit shared environment
        env_id = generate_environment_id("shared")
        workflow.environments[env_id] = AgenticEnvironmentHandle(
            id=env_id,
            env_type=AgenticEnvironmentType.SHARED,
            name="shared",
            working_dir=str(world.working_dir),
            created_at=_utc_now(),
        )

    yield slog(status="connecting", session=effect.name)

    body: dict[str, Any] = {"title": effect.title} if effect.title else {}
    api_result = yield client.post("/session", json=body)
    assert api_result is not None
    session_id = api_result["id"]

    yield slog(status="created", session=effect.name, id=session_id[:12])

    result = AgenticSessionHandle(
        id=session_id,
        name=effect.name,
        workflow_id=workflow.id,
        environment_id=env_id,
        status=AgenticSessionStatus.PENDING,
        created_at=_utc_now(),
        title=effect.title or effect.name,
        agent=effect.agent,
        model=effect.model,
    )
    workflow.sessions[effect.name] = result
    workflow.session_by_id[session_id] = effect.name

    world.event_log.log_session_created(workflow.id, result)
    world.event_log.log_session_bound_to_environment(workflow.id, env_id, effect.name)
    return result


@do
def _fork_session(world: _OpenCodeWorld, effect: AgenticForkSession):
    client = _client(world)
    workflow = _ensure_workflow(world)

    if effect.name in workflow.sessions:
        raise AgenticDuplicateNameError(effect.name, workflow.id)

    source_name = workflow.session_by_id.get(effect.session_id)
    if not source_name:
        raise AgenticSessionNotFoundError(effect.session_id)
    source = workflow.sessions[source_name]

    body: dict[str, Any] = {"messageID": effect.message_id} if effect.message_id else {}
    api_result = yield client.post(f"/session/{effect.session_id}/fork", json=body)
    assert api_result is not None
    new_session_id = api_result["id"]

    result = AgenticSessionHandle(
        id=new_session_id,
        name=effect.name,
        workflow_id=workflow.id,
        environment_id=source.environment_id,
        status=AgenticSessionStatus.PENDING,
        created_at=_utc_now(),
        title=effect.name,
        agent=source.agent,
        model=source.model,
    )
    workflow.sessions[effect.name] = result
    workflow.session_by_id[new_session_id] = effect.name
    return result


@do
def _get_session(world: _OpenCodeWorld, effect: AgenticGetSession):
    workflow = _ensure_workflow(world)

    if effect.name:
        result = workflow.sessions.get(effect.name)
        if not result:
            raise AgenticSessionNotFoundError(effect.name, by_name=True)
        return result

    if effect.session_id:
        name = workflow.session_by_id.get(effect.session_id)
        if not name:
            raise AgenticSessionNotFoundError(effect.session_id)
        return workflow.sessions[name]

    raise ValueError("Either session_id or name must be provided")


@do
def _abort_session(world: _OpenCodeWorld, effect: AgenticAbortSession):
    client = _client(world)
    workflow = _ensure_workflow(world)

    yield client.post(f"/session/{effect.session_id}/abort")

    name = workflow.session_by_id.get(effect.session_id)
    if name and name in workflow.sessions:
        workflow.sessions[name] = replace(
            workflow.sessions[name], status=AgenticSessionStatus.ABORTED
        )


@do
def _delete_session(world: _OpenCodeWorld, effect: AgenticDeleteSession):
    client = _client(world)
    workflow = _ensure_workflow(world)

    api_result = yield client.delete(f"/session/{effect.session_id}")

    name = workflow.session_by_id.pop(effect.session_id, None)
    if name:
        workflow.sessions.pop(name, None)
    return bool(api_result)


@do
def _send_message(world: _OpenCodeWorld, effect: AgenticSendMessage):
    client = _client(world)
    workflow = _ensure_workflow(world)
    session_name = workflow.session_by_id.get(effect.session_id)
    shown_name = session_name or effect.session_id[:8]

    body: dict[str, Any] = {
        "parts": [{"type": "text", "text": effect.content}],
        **({"agent": effect.agent} if effect.agent else {}),
        **({"model": effect.model} if effect.model else {}),
    }

    if session_name:
        world.event_log.log_message_sent(workflow.id, session_name, effect.content, effect.wait)

    if not effect.wait:
        yield slog(status="sending-async", session=shown_name)
        yield client.post(f"/session/{effect.session_id}/prompt_async", json=body)
        return AgenticMessageHandle(
            id=f"msg-{time.time_ns()}",
            session_id=effect.session_id,
            role="user",
            created_at=_utc_now(),
        )

    yield slog(status="sending", session=shown_name)

    api_result = yield client.post(f"/session/{effect.session_id}/message", json=body)
    assert api_result is not None
    info = api_result.get("info", {})

    _update_session_status(world, effect.session_id, AgenticSessionStatus.RUNNING)

    yield slog(status="response", session=shown_name)

    if session_name:
        world.event_log.log_message_complete(workflow.id, session_name)

    return AgenticMessageHandle(
        id=info.get("id", f"msg-{time.time_ns()}"),
        session_id=effect.session_id,
        role=info.get("role", "user"),
        created_at=_parse_datetime_or_now(
            info.get("createdAt") if isinstance(info, dict) else None
        ),
    )


@do
def _get_messages(world: _OpenCodeWorld, effect: AgenticGetMessages):
    client = _client(world)
    params: dict[str, Any] = {"limit": effect.limit} if effect.limit else {}

    api_result = yield client.get(f"/session/{effect.session_id}/message", params=params)

    # api_result is a list of message dicts
    result_list: list[Any] = api_result if isinstance(api_result, list) else []
    return [_message_from_api(effect.session_id, msg) for msg in result_list]


@do
def _next_event(world: _OpenCodeWorld, effect: AgenticNextEvent):
    """Answer AgenticNextEvent from the server's SSE stream, filtered to the session."""
    conn = _sse_connection(world, _client(world), effect.session_id)
    start = time.time()
    for line in conn["iter"]:
        if effect.timeout and (time.time() - start) > effect.timeout:
            raise AgenticTimeoutError("next_event", effect.timeout)

        line_str = line if isinstance(line, str) else line.decode("utf-8")
        if not line_str.startswith("data:"):
            continue

        data = json.loads(line_str[5:].strip())
        event_type: str = data.get("type", "unknown")

        # Filter to this session's events
        props = data.get("properties", {})
        if isinstance(props, dict) and props.get("sessionID") != effect.session_id:
            continue

        if event_type == "session.updated" and isinstance(props, dict):
            status = props.get("status")
            if status in ("done", "error", "aborted"):
                _close_sse(world, effect.session_id)
                return AgenticEndOfEvents(
                    reason=f"session_{status}",
                    final_status=_STATUS_BY_OPENCODE_NAME[status],
                )

        return AgenticEvent(
            event_type=event_type,
            session_id=effect.session_id,
            data=props if isinstance(props, dict) else {},
            timestamp=_utc_now(),
        )

    # The stream ended
    _close_sse(world, effect.session_id)
    return AgenticEndOfEvents(reason="connection_closed", final_status=None)


@do
def _get_session_status(world: _OpenCodeWorld, effect: AgenticGetSessionStatus):
    client = _client(world)
    workflow = _ensure_workflow(world)

    yield _refresh_session_status(world, client, effect.session_id)

    name = workflow.session_by_id.get(effect.session_id)
    if not name:
        raise AgenticSessionNotFoundError(effect.session_id)
    return workflow.sessions[name].status


@do
def _supports_capability(world: _OpenCodeWorld, effect: AgenticSupportsCapability):
    return effect.capability in _SUPPORTED_CAPABILITIES


def _answer_workflow_effect(world: _OpenCodeWorld, effect: WorkflowEffect) -> Any:
    """The Program answering a workflow or environment effect."""
    match effect:
        case AgenticCreateWorkflow():
            return _create_workflow(world, effect)
        case AgenticGetWorkflow():
            return _get_workflow(world, effect)
        case AgenticCreateEnvironment():
            return _create_environment(world, effect)
        case AgenticGetEnvironment():
            return _get_environment(world, effect)
        case AgenticDeleteEnvironment():
            return _delete_environment(world, effect)


def _answer_session_effect(world: _OpenCodeWorld, effect: SessionEffect) -> Any:
    """The Program answering a session lifecycle effect."""
    match effect:
        case AgenticCreateSession():
            return _create_session(world, effect)
        case AgenticForkSession():
            return _fork_session(world, effect)
        case AgenticGetSession():
            return _get_session(world, effect)
        case AgenticAbortSession():
            return _abort_session(world, effect)
        case AgenticDeleteSession():
            return _delete_session(world, effect)


def _answer_message_effect(world: _OpenCodeWorld, effect: MessageEffect) -> Any:
    """The Program answering a message, event or status effect."""
    match effect:
        case AgenticSendMessage():
            return _send_message(world, effect)
        case AgenticGetMessages():
            return _get_messages(world, effect)
        case AgenticNextEvent():
            return _next_event(world, effect)
        case AgenticGetSessionStatus():
            return _get_session_status(world, effect)
        case AgenticSupportsCapability():
            return _supports_capability(world, effect)


# =============================================================================
# Handler Factory
# =============================================================================


def opencode_handler(
    server_url: str | None = None,
    hostname: str = "127.0.0.1",
    port: int | None = None,
    startup_timeout: float = 30.0,
    working_dir: str | None = None,
) -> ProgramHandler:
    """Return a doeff handler that answers the agentic effects through OpenCode's HTTP API.

    The server connection, workflow, environments, sessions and SSE streams live in
    the handler's closure. The server is reached (or started) on the first effect
    that needs it, not when the handler is built. HTTP calls are yielded as Await,
    so the caller's stack needs an await handler; effects it does not own go to
    the handlers outside it.

    Args:
        server_url: URL of existing OpenCode server (skip auto-start if provided)
        hostname: Hostname for auto-started server
        port: Port for auto-started server (auto-assign if None)
        startup_timeout: Timeout for server startup
        working_dir: Default working directory

    Usage:
        from doeff import run
        from doeff_agentic import opencode_handler
        from doeff_core_effects.handlers import slog_handler
        from doeff_core_effects.scheduler import scheduled

        program = slog_handler(opencode_handler()(my_workflow()))
        result = run(scheduled(program))
    """
    world = _OpenCodeWorld(
        server_url=server_url,
        hostname=hostname,
        port=port,
        startup_timeout=startup_timeout,
        working_dir=Path(working_dir) if working_dir else Path.cwd(),
        event_log=EventLogWriter(),
        workflow_index=WorkflowIndex(),
    )

    @handler
    def answer_opencode_agentic(effect: Effect, k: Any):
        """Answer an agentic effect through the OpenCode server; pass every other effect outward."""
        match effect:
            case (
                AgenticCreateWorkflow()
                | AgenticGetWorkflow()
                | AgenticCreateEnvironment()
                | AgenticGetEnvironment()
                | AgenticDeleteEnvironment()
            ):
                answer = _answer_workflow_effect(world, effect)
            case (
                AgenticCreateSession()
                | AgenticForkSession()
                | AgenticGetSession()
                | AgenticAbortSession()
                | AgenticDeleteSession()
            ):
                answer = _answer_session_effect(world, effect)
            case (
                AgenticSendMessage()
                | AgenticGetMessages()
                | AgenticNextEvent()
                | AgenticGetSessionStatus()
                | AgenticSupportsCapability()
            ):
                answer = _answer_message_effect(world, effect)
            case _:
                return (yield Pass(effect, k))
        value = yield answer
        return (yield Resume(k, value))

    return answer_opencode_agentic


__all__ = [
    "opencode_handler",
]
