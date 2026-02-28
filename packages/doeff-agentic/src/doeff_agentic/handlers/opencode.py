"""
OpenCode handler for doeff-agentic.

This module provides the handler that uses OpenCode's HTTP API for agent session management.

Usage:
    import asyncio
    from doeff import async_run, default_handlers
    from doeff_agentic.opencode_handler import opencode_handler
    from doeff import WithHandler

    async def main():
        handlers = opencode_handler()
        program = WithHandler(handlers, my_workflow())
        result = await async_run(program, handlers=default_handlers())

    asyncio.run(main())
"""

from __future__ import annotations

import hashlib
import inspect
import json
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from doeff import Await, Effect, Pass, Resume, do, slog
from doeff.do import make_doeff_generator

from ..effects import (
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
from ..event_log import EventLogWriter, WorkflowIndex
from ..exceptions import (
    AgenticDuplicateNameError,
    AgenticEnvironmentInUseError,
    AgenticEnvironmentNotFoundError,
    AgenticServerError,
    AgenticSessionNotFoundError,
    AgenticTimeoutError,
)
from ..types import (
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
        return None


# =============================================================================
# State Management
# =============================================================================


@dataclass
class WorkflowState:
    """In-memory state for a workflow."""

    id: str
    name: str | None
    status: AgenticWorkflowStatus
    created_at: datetime
    metadata: dict[str, Any] | None = None
    environments: dict[str, AgenticEnvironmentHandle] = field(default_factory=dict)
    sessions: dict[str, AgenticSessionHandle] = field(default_factory=dict)  # name -> handle
    session_by_id: dict[str, str] = field(default_factory=dict)  # session_id -> name


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


# =============================================================================
# OpenCode Handler
# =============================================================================


class OpenCodeHandler:
    """Handler using OpenCode's HTTP API with async I/O via Await effect.

    Architecture:
    - Handlers return @do programs directly when async I/O is required
    - The @do program yields Await(coroutine) for async HTTP calls
    - doeff's async runtime handles Await execution

    This handler:
    1. Auto-starts OpenCode server if not running
    2. Manages workflow/environment/session state
    3. Translates agentic effects to OpenCode API calls via Await
    4. Handles SSE event streaming
    """

    SUPPORTED_CAPABILITIES = frozenset({"fork", "events", "worktree"})

    def __init__(
        self,
        server_url: str | None = None,
        hostname: str = "127.0.0.1",
        port: int | None = None,
        startup_timeout: float = 30.0,
        working_dir: str | None = None,
    ) -> None:
        """Initialize the OpenCode handler.

        Args:
            server_url: URL of existing OpenCode server (skip auto-start if provided)
            hostname: Hostname for auto-started server
            port: Port for auto-started server (auto-assign if None)
            startup_timeout: Timeout for server startup
            working_dir: Default working directory
        """
        self._server_url = server_url
        self._hostname = hostname
        self._port = port
        self._startup_timeout = startup_timeout
        self._working_dir = Path(working_dir) if working_dir else Path.cwd()

        self._client: AsyncHttpClient | None = None
        self._server_process: subprocess.Popen | None = None
        self._workflow: WorkflowState | None = None
        self._sse_connections: dict[str, Any] = {}  # session_id -> SSE iterator

        # Event logging
        self._event_log = EventLogWriter()
        self._workflow_index = WorkflowIndex()

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    def initialize(self) -> None:
        """Initialize handler and start server if needed (sync for startup)."""
        if self._client is not None:
            return

        if self._server_url:
            # Connect to existing server
            self._client = AsyncHttpClient(self._server_url)
            self._check_health_sync()
        else:
            # Auto-start server
            self._start_server()

    def close(self) -> None:
        """Clean up resources."""
        # Close the underlying httpx client directly (not via the @do method)
        if self._client:
            import asyncio

            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._client._client.aclose())
            except RuntimeError:
                # No running loop, create one for cleanup
                asyncio.run(self._client._client.aclose())
            self._client = None

        if self._server_process:
            self._server_process.terminate()
            try:
                self._server_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._server_process.kill()
            self._server_process = None

    def _check_health_sync(self) -> None:
        """Check if server is healthy (sync, for startup only)."""
        import httpx

        try:
            resp = httpx.get(f"{self._server_url}/global/health", timeout=5.0)
            result = resp.json()
            if not result.get("healthy"):
                raise AgenticServerError("Server reports unhealthy")
        except Exception as e:
            raise AgenticServerError(f"Health check failed: {e}") from None

    def _start_server(self) -> None:
        """Auto-start OpenCode server."""
        # Find opencode binary
        opencode_bin = shutil.which("opencode")
        if not opencode_bin:
            raise AgenticServerError("opencode binary not found in PATH")

        # Build command
        cmd = [opencode_bin, "serve", "--hostname", self._hostname]
        if self._port:
            cmd.extend(["--port", str(self._port)])

        # Start process
        self._server_process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self._working_dir,
        )

        # Wait for server to be ready
        port = self._port or 4096
        url = f"http://{self._hostname}:{port}"

        deadline = time.time() + self._startup_timeout
        while time.time() < deadline:
            try:
                import httpx

                resp = httpx.get(f"{url}/global/health", timeout=1.0)
                if resp.status_code == 200 and resp.json().get("healthy"):
                    self._server_url = url
                    self._client = AsyncHttpClient(url)
                    return
            except Exception:
                pass
            time.sleep(0.1)

        # Timeout
        if self._server_process:
            self._server_process.terminate()
        raise AgenticServerError(f"Server failed to start within {self._startup_timeout}s")

    # -------------------------------------------------------------------------
    # Workflow Effects
    # -------------------------------------------------------------------------

    def handle_create_workflow(self, effect: AgenticCreateWorkflow):
        """Handle AgenticCreateWorkflow effect."""
        self.initialize()

        workflow_id = generate_workflow_id(effect.name)
        self._workflow = WorkflowState(
            id=workflow_id,
            name=effect.name,
            status=AgenticWorkflowStatus.RUNNING,
            created_at=datetime.now(timezone.utc),
            metadata=effect.metadata,
        )

        # Log workflow creation
        self._event_log.log_workflow_created(workflow_id, effect.name, effect.metadata)
        self._workflow_index.add(workflow_id, effect.name)

        result = AgenticWorkflowHandle(
            id=workflow_id,
            name=effect.name,
            status=AgenticWorkflowStatus.RUNNING,
            created_at=self._workflow.created_at,
            metadata=effect.metadata,
        )
        return result

    def handle_get_workflow(self, effect: AgenticGetWorkflow):
        """Handle AgenticGetWorkflow effect."""
        self._ensure_workflow()
        assert self._workflow is not None

        result = AgenticWorkflowHandle(
            id=self._workflow.id,
            name=self._workflow.name,
            status=self._workflow.status,
            created_at=self._workflow.created_at,
            metadata=self._workflow.metadata,
        )
        return result

    # -------------------------------------------------------------------------
    # Environment Effects
    # -------------------------------------------------------------------------

    def handle_create_environment(self, effect: AgenticCreateEnvironment):
        """Handle AgenticCreateEnvironment effect."""
        self._ensure_workflow()
        assert self._workflow is not None

        env_id = generate_environment_id(effect.name)
        working_dir: str

        if effect.env_type == AgenticEnvironmentType.SHARED:
            working_dir = effect.working_dir or str(self._working_dir)

        elif effect.env_type == AgenticEnvironmentType.WORKTREE:
            working_dir = self._create_worktree(env_id, effect.base_commit)

        elif effect.env_type == AgenticEnvironmentType.INHERITED:
            if not effect.source_environment_id:
                raise ValueError("inherited type requires source_environment_id")
            source = self._workflow.environments.get(effect.source_environment_id)
            if not source:
                raise AgenticEnvironmentNotFoundError(effect.source_environment_id)
            working_dir = source.working_dir

        elif effect.env_type == AgenticEnvironmentType.COPY:
            if not effect.source_environment_id:
                raise ValueError("copy type requires source_environment_id")
            source = self._workflow.environments.get(effect.source_environment_id)
            if not source:
                raise AgenticEnvironmentNotFoundError(effect.source_environment_id)
            working_dir = self._copy_directory(source.working_dir, env_id)

        else:
            raise ValueError(f"Unknown environment type: {effect.env_type}")

        result = AgenticEnvironmentHandle(
            id=env_id,
            env_type=effect.env_type,
            name=effect.name,
            working_dir=working_dir,
            created_at=datetime.now(timezone.utc),
            base_commit=effect.base_commit,
            source_environment_id=effect.source_environment_id,
        )

        self._workflow.environments[env_id] = result

        # Log environment creation
        self._event_log.log_environment_created(self._workflow.id, result)

        return result

    def handle_get_environment(self, effect: AgenticGetEnvironment):
        """Handle AgenticGetEnvironment effect."""
        self._ensure_workflow()
        assert self._workflow is not None

        result = self._workflow.environments.get(effect.environment_id)
        if not result:
            raise AgenticEnvironmentNotFoundError(effect.environment_id)
        return result

    def handle_delete_environment(self, effect: AgenticDeleteEnvironment):
        """Handle AgenticDeleteEnvironment effect."""
        self._ensure_workflow()
        assert self._workflow is not None

        handle = self._workflow.environments.get(effect.environment_id)
        if not handle:
            raise AgenticEnvironmentNotFoundError(effect.environment_id)

        # Check for sessions using this environment
        using_sessions = [
            s.name
            for s in self._workflow.sessions.values()
            if s.environment_id == effect.environment_id
        ]

        if using_sessions and not effect.force:
            raise AgenticEnvironmentInUseError(effect.environment_id, using_sessions)

        # Clean up worktree if applicable
        if handle.env_type == AgenticEnvironmentType.WORKTREE:
            self._delete_worktree(handle.working_dir)
        elif handle.env_type == AgenticEnvironmentType.COPY:
            shutil.rmtree(handle.working_dir, ignore_errors=True)

        # Log environment deletion
        self._event_log.log_environment_deleted(
            self._workflow.id, effect.environment_id, effect.force
        )

        del self._workflow.environments[effect.environment_id]
        return True

    # -------------------------------------------------------------------------
    # Session Effects
    # -------------------------------------------------------------------------

    def handle_create_session(self, effect: AgenticCreateSession):
        """Handle AgenticCreateSession effect."""
        self._ensure_workflow()
        assert self._workflow is not None
        assert self._client is not None

        # Check for duplicate name
        if effect.name in self._workflow.sessions:
            raise AgenticDuplicateNameError(effect.name, self._workflow.id)

        # Create or get environment
        if effect.environment_id:
            env = self._workflow.environments.get(effect.environment_id)
            if not env:
                raise AgenticEnvironmentNotFoundError(effect.environment_id)
            env_id = effect.environment_id
        else:
            # Create implicit shared environment
            env_id = generate_environment_id("shared")
            env = AgenticEnvironmentHandle(
                id=env_id,
                env_type=AgenticEnvironmentType.SHARED,
                name="shared",
                working_dir=str(self._working_dir),
                created_at=datetime.now(timezone.utc),
            )
            self._workflow.environments[env_id] = env

        # Capture references for closure
        client = self._client
        workflow = self._workflow
        event_log = self._event_log

        @do
        def _create_session():
            yield slog(status="connecting", session=effect.name)

            body: dict[str, Any] = {}
            if effect.title:
                body["title"] = effect.title

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

            event_log.log_session_created(workflow.id, result)
            event_log.log_session_bound_to_environment(workflow.id, env_id, effect.name)

            return result

        return _create_session()

    def handle_fork_session(self, effect: AgenticForkSession):
        """Handle AgenticForkSession effect."""
        self._ensure_workflow()
        assert self._workflow is not None
        assert self._client is not None

        # Check for duplicate name
        if effect.name in self._workflow.sessions:
            raise AgenticDuplicateNameError(effect.name, self._workflow.id)

        # Find source session
        source_name = self._workflow.session_by_id.get(effect.session_id)
        if not source_name:
            raise AgenticSessionNotFoundError(effect.session_id)
        source = self._workflow.sessions[source_name]

        # Capture references for closure
        client = self._client
        workflow = self._workflow

        @do
        def _fork_session():
            # Fork via OpenCode API
            body: dict[str, Any] = {}
            if effect.message_id:
                body["messageID"] = effect.message_id

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

        return _fork_session()

    def handle_get_session(self, effect: AgenticGetSession):
        """Handle AgenticGetSession effect."""
        self._ensure_workflow()
        assert self._workflow is not None

        if effect.name:
            result = self._workflow.sessions.get(effect.name)
            if not result:
                raise AgenticSessionNotFoundError(effect.name, by_name=True)
            return result

        if effect.session_id:
            name = self._workflow.session_by_id.get(effect.session_id)
            if not name:
                raise AgenticSessionNotFoundError(effect.session_id)
            result = self._workflow.sessions[name]
            return result

        raise ValueError("Either session_id or name must be provided")

    def handle_abort_session(self, effect: AgenticAbortSession):
        """Handle AgenticAbortSession effect."""
        self._ensure_workflow()
        assert self._client is not None
        assert self._workflow is not None

        client = self._client
        workflow = self._workflow

        @do
        def _abort_session():
            yield client.post(f"/session/{effect.session_id}/abort")

            # Update local state
            name = workflow.session_by_id.get(effect.session_id)
            if name and name in workflow.sessions:
                session = workflow.sessions[name]
                workflow.sessions[name] = AgenticSessionHandle(
                    id=session.id,
                    name=session.name,
                    workflow_id=session.workflow_id,
                    environment_id=session.environment_id,
                    status=AgenticSessionStatus.ABORTED,
                    created_at=session.created_at,
                    title=session.title,
                    agent=session.agent,
                    model=session.model,
                )

            return None

        return _abort_session()

    def handle_delete_session(self, effect: AgenticDeleteSession):
        """Handle AgenticDeleteSession effect."""
        self._ensure_workflow()
        assert self._workflow is not None
        assert self._client is not None

        client = self._client
        workflow = self._workflow

        @do
        def _delete_session():
            api_result = yield client.delete(f"/session/{effect.session_id}")

            # Update local state
            name = workflow.session_by_id.pop(effect.session_id, None)
            if name:
                workflow.sessions.pop(name, None)

            return bool(api_result)

        return _delete_session()

    # -------------------------------------------------------------------------
    # Message Effects
    # -------------------------------------------------------------------------

    def handle_send_message(self, effect: AgenticSendMessage):
        """Handle AgenticSendMessage effect."""
        self._ensure_workflow()
        assert self._workflow is not None
        assert self._client is not None

        client = self._client
        workflow = self._workflow
        event_log = self._event_log
        update_status = self._update_session_status

        # Get session name for logging
        session_name = workflow.session_by_id.get(effect.session_id)

        # Build request body
        body: dict[str, Any] = {
            "parts": [{"type": "text", "text": effect.content}],
        }

        if effect.agent:
            body["agent"] = effect.agent
        if effect.model:
            body["model"] = effect.model

        # Log message sent
        if session_name:
            event_log.log_message_sent(workflow.id, session_name, effect.content, effect.wait)

        @do
        def _send_message():
            if effect.wait:
                yield slog(status="sending", session=session_name or effect.session_id[:8])

                api_result = yield client.post(f"/session/{effect.session_id}/message", json=body)
                assert api_result is not None
                info = api_result.get("info", {})

                update_status(effect.session_id, AgenticSessionStatus.RUNNING)

                yield slog(status="response", session=session_name or effect.session_id[:8])

                if session_name:
                    event_log.log_message_complete(workflow.id, session_name)

                created_at = _parse_datetime_or_now(
                    info.get("createdAt") if isinstance(info, dict) else None
                )

                return AgenticMessageHandle(
                    id=info.get("id", f"msg-{time.time_ns()}"),
                    session_id=effect.session_id,
                    role=info.get("role", "user"),
                    created_at=created_at,
                )
            else:
                yield slog(status="sending-async", session=session_name or effect.session_id[:8])

                yield client.post(f"/session/{effect.session_id}/prompt_async", json=body)

                return AgenticMessageHandle(
                    id=f"msg-{time.time_ns()}",
                    session_id=effect.session_id,
                    role="user",
                    created_at=_utc_now(),
                )

        return _send_message()

    def handle_get_messages(self, effect: AgenticGetMessages):
        """Handle AgenticGetMessages effect."""
        self._ensure_workflow()
        assert self._client is not None

        client = self._client

        @do
        def _get_messages():
            params: dict[str, Any] = {}
            if effect.limit:
                params["limit"] = effect.limit

            api_result = yield client.get(f"/session/{effect.session_id}/message", params=params)

            messages = []
            # api_result is a list of message dicts
            result_list: list[dict[str, Any]] = api_result if isinstance(api_result, list) else []
            for msg in result_list:
                info: dict[str, Any] = msg.get("info", {}) if isinstance(msg, dict) else {}
                parts: list[dict[str, Any]] = msg.get("parts", []) if isinstance(msg, dict) else []

                # Extract text content
                content_parts = []
                for part in parts:
                    if isinstance(part, dict) and part.get("type") == "text":
                        content_parts.append(part.get("text", ""))

                created_at = _parse_datetime_or_now(
                    info.get("createdAt") if isinstance(info, dict) else None
                )

                messages.append(
                    AgenticMessage(
                        id=info.get("id", "") if isinstance(info, dict) else "",
                        session_id=effect.session_id,
                        role=info.get("role", "user") if isinstance(info, dict) else "user",
                        content="\n".join(content_parts),
                        created_at=created_at,
                        parts=parts,
                    )
                )

            return messages

        return _get_messages()

    # -------------------------------------------------------------------------
    # Event Effects
    # -------------------------------------------------------------------------

    def handle_next_event(self, effect: AgenticNextEvent):
        """Handle AgenticNextEvent effect."""
        self._ensure_workflow()
        assert self._client is not None

        # Get or create SSE connection
        if effect.session_id not in self._sse_connections:
            import httpx

            url = f"{self._client.base_url}/event"
            client = httpx.Client(timeout=None)
            # NOTE: We intentionally don't use a context manager here because
            # we need to keep the streaming connection open across multiple
            # calls to handle_next_event. The connection is closed in _close_sse.
            response = client.stream("GET", url).__enter__()
            self._sse_connections[effect.session_id] = {
                "client": client,
                "response": response,
                "iter": response.iter_lines(),
            }

        conn = self._sse_connections[effect.session_id]

        try:
            start = time.time()
            for line in conn["iter"]:
                # Check timeout
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

                # Map to our event types
                if event_type == "session.updated":
                    if isinstance(props, dict):
                        status = props.get("status")
                        if status in ("done", "error", "aborted"):
                            self._close_sse(effect.session_id)
                            final_status = self._map_status(status)
                            result: AgenticEvent | AgenticEndOfEvents = AgenticEndOfEvents(
                                reason=f"session_{status}",
                                final_status=final_status,
                            )
                            return result

                result = AgenticEvent(
                    event_type=event_type,
                    session_id=effect.session_id,
                    data=props if isinstance(props, dict) else {},
                    timestamp=datetime.now(timezone.utc),
                )
                return result

        except StopIteration:
            self._close_sse(effect.session_id)
            result = AgenticEndOfEvents(
                reason="connection_closed",
                final_status=None,
            )
            return result

        # Should not reach here
        result = AgenticEndOfEvents(reason="connection_closed", final_status=None)
        return result

    def _close_sse(self, session_id: str) -> None:
        """Close SSE connection for session."""
        if session_id in self._sse_connections:
            conn = self._sse_connections.pop(session_id)
            try:
                conn["response"].close()
                conn["client"].close()
            except Exception:
                pass

    # -------------------------------------------------------------------------
    # Status Effects
    # -------------------------------------------------------------------------

    def handle_get_session_status(self, effect: AgenticGetSessionStatus):
        """Handle AgenticGetSessionStatus effect."""
        self._ensure_workflow()
        assert self._workflow is not None

        workflow = self._workflow
        refresh_status = self._refresh_session_status

        @do
        def _get_status():
            yield refresh_status(effect.session_id)

            name = workflow.session_by_id.get(effect.session_id)
            if not name:
                raise AgenticSessionNotFoundError(effect.session_id)

            return workflow.sessions[name].status

        return _get_status()

    def handle_supports_capability(self, effect: AgenticSupportsCapability):
        """Handle AgenticSupportsCapability effect."""
        result = effect.capability in self.SUPPORTED_CAPABILITIES
        return result

    # -------------------------------------------------------------------------
    # Helper Methods
    # -------------------------------------------------------------------------

    def _ensure_workflow(self) -> None:
        """Ensure workflow is created (internal helper)."""
        if self._workflow is None:
            self.initialize()
            workflow_id = generate_workflow_id(None)
            self._workflow = WorkflowState(
                id=workflow_id,
                name=None,
                status=AgenticWorkflowStatus.RUNNING,
                created_at=datetime.now(timezone.utc),
                metadata=None,
            )
            self._event_log.log_workflow_created(workflow_id, None, None)
            self._workflow_index.add(workflow_id, None)

    def _create_worktree(self, env_id: str, base_commit: str | None) -> str:
        """Create a git worktree."""
        worktree_dir = Path(f"/tmp/doeff/worktrees/{env_id}")
        worktree_dir.parent.mkdir(parents=True, exist_ok=True)

        cmd = ["git", "worktree", "add", str(worktree_dir)]
        if base_commit:
            cmd.append(base_commit)
        else:
            cmd.extend(["--detach", "HEAD"])

        subprocess.run(cmd, cwd=self._working_dir, check=True, capture_output=True)
        return str(worktree_dir)

    def _delete_worktree(self, path: str) -> None:
        """Remove a git worktree."""
        try:
            subprocess.run(
                ["git", "worktree", "remove", "--force", path],
                cwd=self._working_dir,
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError:
            # Force remove directory if worktree removal fails
            shutil.rmtree(path, ignore_errors=True)

    def _copy_directory(self, source: str, env_id: str) -> str:
        """Copy directory for COPY environment type."""
        dest = Path(f"/tmp/doeff/copies/{env_id}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, dest)
        return str(dest)

    def _update_session_status(self, session_id: str, status: AgenticSessionStatus) -> None:
        """Update local session status."""
        if self._workflow is None:
            return

        name = self._workflow.session_by_id.get(session_id)
        if not name:
            return

        session = self._workflow.sessions[name]
        old_status = session.status

        self._workflow.sessions[name] = AgenticSessionHandle(
            id=session.id,
            name=session.name,
            workflow_id=session.workflow_id,
            environment_id=session.environment_id,
            status=status,
            created_at=session.created_at,
            title=session.title,
            agent=session.agent,
            model=session.model,
        )

        # Log status change if different
        if old_status != status:
            self._event_log.log_session_status(self._workflow.id, name, status)

    @do
    def _refresh_session_status(self, session_id: str):
        """Refresh session status from server. Returns Program[..., None]."""
        assert self._client is not None

        try:
            result = yield self._client.get("/session/status")
            status_str = result.get(session_id, "pending")
            status = self._map_status(status_str)
            self._update_session_status(session_id, status)
        except Exception:
            pass
        return None

    def _map_status(self, status: str) -> AgenticSessionStatus:
        """Map OpenCode status to AgenticSessionStatus."""
        mapping = {
            "pending": AgenticSessionStatus.PENDING,
            "booting": AgenticSessionStatus.BOOTING,
            "running": AgenticSessionStatus.RUNNING,
            "blocked": AgenticSessionStatus.BLOCKED,
            "done": AgenticSessionStatus.DONE,
            "error": AgenticSessionStatus.ERROR,
            "aborted": AgenticSessionStatus.ABORTED,
        }
        return mapping.get(status, AgenticSessionStatus.PENDING)


# =============================================================================
# Handler Factory
# =============================================================================

def _is_lazy_program_value(value: object) -> bool:
    return bool(getattr(value, "__doeff_do_expr_base__", False) or getattr(
        value, "__doeff_effect_base__", False
    ))


def _as_protocol_handler(
    handler_fn: Callable[[Any], Any],
) -> Callable[[Any, Any], Any]:
    """Adapt an effect -> value/do-program handler into (effect, k) protocol."""

    @do
    def _wrapped(effect: Effect, k: Any):
        result = handler_fn(effect)

        if inspect.isgenerator(result):
            resolved = yield make_doeff_generator(result)
            return (yield Resume(k, resolved))

        if _is_lazy_program_value(result):
            resolved = yield result
            return (yield Resume(k, resolved))

        return (yield Resume(k, result))

    return _wrapped


def opencode_handler(
    server_url: str | None = None,
    hostname: str = "127.0.0.1",
    port: int | None = None,
    startup_timeout: float = 30.0,
    working_dir: str | None = None,
) -> Callable[[Any, Any], Any]:
    """Create VM-compatible handlers for agentic effects using OpenCode.

    Args:
        server_url: URL of existing OpenCode server (skip auto-start if provided)
        hostname: Hostname for auto-started server
        port: Port for auto-started server (auto-assign if None)
        startup_timeout: Timeout for server startup
        working_dir: Default working directory

    Returns:
        Protocol handler for use with WithHandler composition.

    Usage:
        import asyncio
        from doeff import async_run, default_handlers
        from doeff_agentic import opencode_handler
        from doeff import WithHandler

        async def main():
            handlers = opencode_handler()
            program = WithHandler(handlers, my_workflow())
            result = await async_run(program, handlers=default_handlers())

        asyncio.run(main())
    """
    handler = OpenCodeHandler(
        server_url=server_url,
        hostname=hostname,
        port=port,
        startup_timeout=startup_timeout,
        working_dir=working_dir,
    )

    effect_handlers: tuple[tuple[type[Any], Callable[[Any, Any], Any]], ...] = (
        (AgenticCreateWorkflow, _as_protocol_handler(handler.handle_create_workflow)),
        (AgenticGetWorkflow, _as_protocol_handler(handler.handle_get_workflow)),
        (AgenticCreateEnvironment, _as_protocol_handler(handler.handle_create_environment)),
        (AgenticGetEnvironment, _as_protocol_handler(handler.handle_get_environment)),
        (AgenticDeleteEnvironment, _as_protocol_handler(handler.handle_delete_environment)),
        (AgenticCreateSession, _as_protocol_handler(handler.handle_create_session)),
        (AgenticForkSession, _as_protocol_handler(handler.handle_fork_session)),
        (AgenticGetSession, _as_protocol_handler(handler.handle_get_session)),
        (AgenticAbortSession, _as_protocol_handler(handler.handle_abort_session)),
        (AgenticDeleteSession, _as_protocol_handler(handler.handle_delete_session)),
        (AgenticSendMessage, _as_protocol_handler(handler.handle_send_message)),
        (AgenticGetMessages, _as_protocol_handler(handler.handle_get_messages)),
        (AgenticNextEvent, _as_protocol_handler(handler.handle_next_event)),
        (AgenticGetSessionStatus, _as_protocol_handler(handler.handle_get_session_status)),
        (AgenticSupportsCapability, _as_protocol_handler(handler.handle_supports_capability)),
    )

    @do
    def protocol_handler(effect: Effect, k: Any):
        for effect_type, effect_handler in effect_handlers:
            if isinstance(effect, effect_type):
                return (yield effect_handler(effect, k))
        yield Pass()

    return protocol_handler


__all__ = [
    "OpenCodeHandler",
    "opencode_handler",
]
