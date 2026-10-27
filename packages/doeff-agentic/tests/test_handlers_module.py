"""Tests for the doeff-agentic handlers: doeff handlers built by factories, and compatibility exports."""


from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from doeff_agentic.effects import (
    AgenticCreateSession,
    AgenticGetMessages,
    AgenticGetSessionStatus,
    AgenticSendMessage,
    RunAgentEffect,
    WaitForStatusEffect,
)
from doeff_agentic.handlers import mock_handlers, production_handlers
from doeff_agentic.handlers.production import agentic_effectful_handlers
from doeff_agentic.types import AgentConfig, AgentStatus
from doeff_agents import AgentType, SessionHandle, SessionStatus
from doeff_agents.effects import (
    CaptureEffect,
    LaunchEffect,
    MonitorEffect,
    Observation,
    SendEffect,
    StopEffect,
)

from doeff import Effect, Pass, Resume, do, handler, run


def test_handlers_init_exports_required_factory_functions() -> None:
    """`doeff_agentic.handlers` exports both required factories."""
    assert callable(production_handlers)
    assert callable(mock_handlers)


def test_production_handlers_factory_returns_protocol_handler() -> None:
    """Production factory returns a protocol handler without side effects at construction time."""
    handler = production_handlers()
    assert callable(handler)


def test_mock_handlers_execute_simple_workflow() -> None:
    """Mock handlers can run a full effectful workflow in tests."""

    @do
    def workflow():
        session = yield AgenticCreateSession(name="reviewer")
        msg = yield AgenticSendMessage(
            session_id=session.id,
            content="Review this patch",
            wait=True,
        )
        messages = yield AgenticGetMessages(session_id=session.id)
        status = yield AgenticGetSessionStatus(session_id=session.id)
        return msg.role, messages[-1].role, status.value

    program = mock_handlers()(workflow())
    result = run(program)

    assert result == ("user", "assistant", "done")


def test_legacy_opencode_and_tmux_modules_still_import() -> None:
    """Backward-compatible top-level module imports still resolve."""
    from doeff_agentic.handlers.opencode import opencode_handler as new_opencode_handler
    from doeff_agentic.handlers.tmux import tmux_handler as new_tmux_handler
    from doeff_agentic.opencode_handler import opencode_handler as old_opencode_handler
    from doeff_agentic.tmux_handler import tmux_handler as old_tmux_handler

    assert old_opencode_handler is new_opencode_handler
    assert old_tmux_handler is new_tmux_handler


def test_legacy_handler_module_reexport_when_optional_dependency_available() -> None:
    """Legacy `doeff_agentic.handler` remains importable when doeff-agents is present."""
    pytest.importorskip("doeff_agents")

    from doeff_agentic.handler import agentic_effectful_handlers as old_export
    from doeff_agentic.handlers.production import (
        agentic_effectful_handlers as new_export,
    )

    assert old_export is new_export


def _fake_agent_runtime(
    *,
    statuses: tuple[SessionStatus, ...] = (),
    output: str = "done",
    launched: list[LaunchEffect] | None = None,
):
    """A doeff handler standing in for doeff-agents' runtime: it answers Launch / Monitor /
    Capture / Send / Stop, reporting ``statuses`` in turn (then DONE) to each Monitor."""
    remaining = iter(statuses)

    @handler
    def fake_runtime(effect: Effect, k: Any):
        match effect:
            case LaunchEffect():
                if launched is not None:
                    launched[:] = [*launched, effect]
                return (yield Resume(k, SessionHandle(session_id=effect.session_name)))
            case MonitorEffect():
                status = next(remaining, SessionStatus.DONE)
                observation = Observation(status=status, output_changed=False, output_snippet="")
                return (yield Resume(k, observation))
            case CaptureEffect():
                return (yield Resume(k, output))
            case SendEffect() | StopEffect():
                return (yield Resume(k, None))
            case _:
                return (yield Pass(effect, k))

    return fake_runtime


def test_legacy_agentic_handler_accepts_injected_agent_runtime(tmp_path: Path) -> None:
    """RunAgent lowers onto Launch / Monitor / Capture, which the injected runtime handler answers."""
    launched: list[LaunchEffect] = []

    @do
    def workflow():
        return (
            yield RunAgentEffect(
                config=AgentConfig(
                    agent_type="codex",
                    prompt="Review this patch",
                    work_dir=str(tmp_path),
                )
            )
        )

    result = run(
        agentic_effectful_handlers(
            workflow_id="wf-test",
            workflow_name="wf-test",
            state_dir=tmp_path / "state",
            agent_runtime=_fake_agent_runtime(output="done", launched=launched),
        )(workflow()),
    )

    assert result == "done"
    assert [(effect.agent_type, Path(effect.work_dir)) for effect in launched] == [
        (AgentType.CODEX, tmp_path)
    ]


def test_legacy_agentic_handler_polls_with_time_delay(tmp_path: Path) -> None:
    """While the agent is still running, RunAgent waits with doeff_time Delay."""
    from doeff_core_effects.scheduler import scheduled
    from doeff_time import sync_time_handler

    slept: list[float] = []

    @do
    def workflow():
        return (
            yield RunAgentEffect(
                config=AgentConfig(agent_type="codex", prompt="p", work_dir=str(tmp_path)),
                poll_interval=0.25,
            )
        )

    program = agentic_effectful_handlers(
        workflow_id="wf-poll",
        workflow_name="wf-poll",
        state_dir=tmp_path / "state",
        agent_runtime=_fake_agent_runtime(
            statuses=(SessionStatus.RUNNING, SessionStatus.RUNNING), output="finished"
        ),
    )(workflow())
    result = run(scheduled(sync_time_handler(sleep=slept.append)(program)))

    assert result == "finished"
    # Status updates also yield Monitor, so the exact count of polls is not pinned.
    assert slept
    assert all(seconds == 0.25 for seconds in slept)


def test_legacy_agentic_handler_writes_observed_agent_status(tmp_path: Path) -> None:
    """The state files report each agent with the status the runtime's Monitor gave."""
    from doeff_agentic.state import StateManager

    @do
    def workflow():
        yield RunAgentEffect(
            config=AgentConfig(agent_type="codex", prompt="p", work_dir=str(tmp_path)),
            session_name="reviewer",
        )
        yield WaitForStatusEffect(session_name="reviewer", target_status=AgentStatus.DONE)

    run(
        agentic_effectful_handlers(
            workflow_id="wf-state",
            workflow_name="wf-state",
            state_dir=tmp_path / "state",
            agent_runtime=_fake_agent_runtime(),
        )(workflow())
    )

    workflow_info = StateManager(tmp_path / "state").read_workflow("wf-state")
    assert workflow_info is not None
    assert [(agent.name, agent.status) for agent in workflow_info.agents] == [
        ("reviewer", AgentStatus.DONE)
    ]


def test_legacy_agentic_handler_refuses_resume_and_profile(tmp_path: Path) -> None:
    """Launch has no resume / profile any more; asking for them is an error, not a silent drop."""
    from doeff_agentic.exceptions import AgenticUnsupportedOperationError

    for config in (
        AgentConfig(agent_type="codex", prompt="p", work_dir=str(tmp_path), resume=True),
        AgentConfig(agent_type="codex", prompt="p", work_dir=str(tmp_path), profile="work"),
    ):

        @do
        def workflow(config=config):
            return (yield RunAgentEffect(config=config))

        with pytest.raises(AgenticUnsupportedOperationError):
            run(
                agentic_effectful_handlers(
                    workflow_id="wf-refuse",
                    workflow_name="wf-refuse",
                    state_dir=tmp_path / "state",
                    agent_runtime=_fake_agent_runtime(),
                )(workflow())
            )


def test_legacy_agentic_handler_builds_with_default_runtime(tmp_path: Path) -> None:
    """Without an injected runtime the handler builds (the session backend is asked for later)."""
    installer = agentic_effectful_handlers(workflow_id="wf-default", state_dir=tmp_path / "state")
    assert getattr(installer, "_doeff_is_handler_fn", False) is True


def test_mock_handlers_pass_through_unrelated_effects() -> None:
    """Effects the agentic mock does not own reach the handlers outside it."""
    from doeff_core_effects import reader

    from doeff import Ask

    @do
    def workflow():
        session = yield AgenticCreateSession(name="reviewer")
        base = yield Ask("base")
        return session.name, base

    result = run(reader(env={"base": 7})(mock_handlers()(workflow())))
    assert result == ("reviewer", 7)


def test_mock_handlers_keep_their_world_in_the_handler() -> None:
    """One mock handler keeps its sessions across programs; a new handler starts empty."""
    from doeff_agentic.exceptions import AgenticDuplicateNameError

    @do
    def create_reviewer():
        return (yield AgenticCreateSession(name="reviewer")).name

    shared = mock_handlers()
    assert run(shared(create_reviewer())) == "reviewer"
    with pytest.raises(AgenticDuplicateNameError):
        run(shared(create_reviewer()))
    assert run(mock_handlers()(create_reviewer())) == "reviewer"


def test_opencode_handler_answers_without_reaching_the_server() -> None:
    """Capability questions are answered from the handler alone; other effects go outward."""
    from doeff_agentic.effects import AgenticSupportsCapability
    from doeff_agentic.handlers import opencode_handler
    from doeff_core_effects import reader

    from doeff import Ask

    @do
    def workflow():
        fork = yield AgenticSupportsCapability(capability="fork")
        attach = yield AgenticSupportsCapability(capability="attach")
        base = yield Ask("base")
        return fork, attach, base

    handler_without_server = opencode_handler(server_url="http://127.0.0.1:9")
    assert run(reader(env={"base": 7})(handler_without_server(workflow()))) == (True, False, 7)


class _FakeSessionBackend:
    """A doeff-agents session backend standing in for tmux (the terminal is the outside world)."""

    def __init__(self) -> None:
        self.live: frozenset[str] = frozenset()
        self.keys: tuple[tuple[str, str], ...] = ()

    def is_available(self) -> bool:
        return True

    def new_session(self, config):
        self.live = self.live | {config.session_name}
        return SimpleNamespace(pane_id=f"%{len(self.live)}")

    def has_session(self, name: str) -> bool:
        return name in self.live

    def send_keys(self, target: str, keys: str, *, literal: bool = True, enter: bool = True) -> None:
        self.keys = (*self.keys, (target, keys))

    def capture_pane(self, target: str, lines: int = 100) -> str:
        return "working on it"

    def kill_session(self, name: str) -> None:
        self.live = self.live - {name}


def test_tmux_handler_drives_the_injected_session_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The tmux handler keeps its sessions in the handler and reaches tmux only through the backend."""
    from doeff_agentic.effects import AgenticAbortSession
    from doeff_agentic.handlers import tmux_handler

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    backend = _FakeSessionBackend()

    @do
    def workflow():
        session = yield AgenticCreateSession(name="reviewer")
        yield AgenticSendMessage(session_id=session.id, content="Review this patch", wait=False)
        messages = yield AgenticGetMessages(session_id=session.id)
        status = yield AgenticGetSessionStatus(session_id=session.id)
        yield AgenticAbortSession(session_id=session.id)
        return [(m.role, m.content) for m in messages], status.value

    messages, status = run(tmux_handler(str(tmp_path), backend=backend)(workflow()))

    assert messages == [("user", "Review this patch"), ("assistant", "working on it")]
    assert status == "running"
    assert backend.keys == (("%1", "Review this patch"), ("%1", "C-c"))
    assert backend.live == frozenset()


def test_public_modules_export_no_handler_class() -> None:
    """The handlers are doeff handlers (functions); no public module exports a class named *Handler."""
    import importlib
    import inspect

    modules = (
        "doeff_agentic",
        "doeff_agentic.handler",
        "doeff_agentic.handlers",
        "doeff_agentic.handlers.opencode",
        "doeff_agentic.handlers.production",
        "doeff_agentic.handlers.testing",
        "doeff_agentic.handlers.tmux",
        "doeff_agentic.opencode_handler",
        "doeff_agentic.tmux_handler",
    )
    handler_classes = [
        f"{module_name}.{name}"
        for module_name in modules
        for name in importlib.import_module(module_name).__all__
        if name.endswith("Handler")
        and inspect.isclass(getattr(importlib.import_module(module_name), name))
    ]
    assert handler_classes == []
