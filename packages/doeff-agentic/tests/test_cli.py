"""The ``doeff-agentic`` console script answers the six verbs of the Rust CLI it replaces.

ps, show, watch, attach, send and stop take the arguments of packages/doeff-agentic-cli
(src/main.rs) and write its JSON — the IDE plugins call them (VS Code's doeff-runner reads
``ps --json`` as the array of workflows; doeff.nvim calls all six). ``RUST_ARGUMENTS``
copies main.rs's arguments; the tests check the commands' parameters against it and run
the commands' programs under fake handlers: the recorded workflows live in memory, the
tmux calls are recorded, the clock stands still and a Delay moves the workflow on.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any

import click
import pytest
from click.testing import CliRunner, Result
from doeff_agentic import (
    AgenticAnswerInput,
    AgenticAttachAgent,
    AgenticFindWorkflow,
    AgenticInputRequested,
    AgenticListWorkflows,
    AgenticSendToAgent,
    AgenticStopWorkflow,
    AgentInfo,
    AgentStatus,
    WorkflowInfo,
    WorkflowStatus,
)
from doeff_agentic._cli import cli
from doeff_time import DelayEffect, GetTimeEffect

from doeff import Effect, Pass, Resume, handler

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)

#: main.rs's arguments per verb: (the option's names, multiple, flag, default) per option
#: and the positional arguments in order.
RUST_ARGUMENTS: dict[
    str, tuple[tuple[str, ...], frozenset[tuple[tuple[str, ...], bool, bool, Any]]]
] = {
    "ps": (
        (),
        frozenset(
            {
                (("--status", "-s"), True, False, None),
                (("--agent-status", "-a"), True, False, None),
                (("--json",), False, True, False),
            }
        ),
    ),
    "show": (("workflow_id",), frozenset({(("--json",), False, True, False)})),
    "watch": (
        ("workflow_id",),
        frozenset({(("--json",), False, True, False), (("--poll", "-p"), False, False, 1.0)}),
    ),
    "attach": (("workflow_id",), frozenset({(("--agent", "-a"), False, False, None)})),
    "send": (
        ("workflow_id", "message"),
        frozenset({(("--agent", "-a"), False, False, None), (("--json",), False, True, False)}),
    ),
    "stop": (("workflow_id",), frozenset({(("--json",), False, True, False)})),
}

WORKFLOW_KEYS = [
    "id",
    "name",
    "status",
    "started_at",
    "updated_at",
    "current_agent",
    "agents",
    "last_slog",
    "error",
]
AGENT_KEYS = ["name", "status", "session_name", "pane_id", "started_at", "last_output_hash"]


def _agent(
    name: str, status: AgentStatus = AgentStatus.RUNNING, pane: str | None = None
) -> AgentInfo:
    return AgentInfo(
        name=name,
        status=status,
        session_name=f"doeff-wf-{name}",
        pane_id=pane,
        started_at=NOW - timedelta(minutes=10),
    )


def _workflow(
    workflow_id: str,
    *,
    status: WorkflowStatus = WorkflowStatus.RUNNING,
    minutes_ago: int = 1,
    current: str | None = None,
    agents: tuple[AgentInfo, ...] = (),
) -> WorkflowInfo:
    return WorkflowInfo(
        id=workflow_id,
        name=f"flow-{workflow_id}",
        status=status,
        started_at=NOW - timedelta(hours=1),
        updated_at=NOW - timedelta(minutes=minutes_ago),
        current_agent=current,
        agents=agents,
        last_slog={"status": "reviewing", "msg": "step 2"},
    )


@dataclass
class FakeWorld:
    """The recorded workflows and the agents' sessions, as the fake handlers keep them."""

    workflows: dict[str, WorkflowInfo]
    waiting_for_input: frozenset[str] = frozenset()
    reachable: bool = True
    #: What a Delay does to the workflows (the poll sees the next state).
    on_delay: Callable[["FakeWorld"], None] = lambda _world: None
    performed: tuple[Effect, ...] = ()
    delays: tuple[float, ...] = ()

    def find(self, workflow_id: str) -> WorkflowInfo | None:
        if workflow_id in self.workflows:
            return self.workflows[workflow_id]
        matches = [wf for wf_id, wf in self.workflows.items() if wf_id.startswith(workflow_id)]
        if len(matches) > 1:
            raise ValueError(f"Ambiguous prefix '{workflow_id}' matches multiple workflows")
        return matches[0] if matches else None

    def handlers(self) -> tuple[Any, ...]:
        world = self

        @handler
        def fake_recorded(effect: Effect, k: Any):
            answer: object
            match effect:
                case AgenticListWorkflows():
                    workflows = world.workflows.values()
                    answer = tuple(sorted(workflows, key=lambda wf: wf.updated_at, reverse=True))
                case AgenticFindWorkflow():
                    answer = world.find(effect.workflow_id)
                case AgenticInputRequested():
                    answer = effect.workflow_id in world.waiting_for_input
                case AgenticAnswerInput() | AgenticAttachAgent():
                    world.performed = (*world.performed, effect)
                    answer = None
                case AgenticSendToAgent():
                    world.performed = (*world.performed, effect)
                    answer = world.reachable
                case AgenticStopWorkflow():
                    world.performed = (*world.performed, effect)
                    workflow = world.workflows[effect.workflow_id]
                    stopped = replace(workflow, status=WorkflowStatus.STOPPED)
                    world.workflows[effect.workflow_id] = stopped
                    answer = tuple(agent.name for agent in workflow.agents)
                case _:
                    return (yield Pass(effect, k))
            return (yield Resume(k, answer))

        @handler
        def fake_clock(effect: Effect, k: Any):
            if isinstance(effect, GetTimeEffect):
                return (yield Resume(k, NOW))
            if isinstance(effect, DelayEffect):
                world.delays = (*world.delays, effect.seconds)
                world.on_delay(world)
                return (yield Resume(k, None))
            return (yield Pass(effect, k))

        return (fake_clock, fake_recorded)


def _invoke(world: FakeWorld, *args: str) -> Result:
    return CliRunner(mix_stderr=False).invoke(cli, list(args), obj={"handlers": world.handlers()})


@pytest.fixture
def world() -> FakeWorld:
    return FakeWorld(
        workflows={
            "a3f8b2c": _workflow(
                "a3f8b2c",
                minutes_ago=5,
                current="reviewer",
                agents=(_agent("reviewer", pane="%3"), _agent("fixer", AgentStatus.BLOCKED)),
            ),
            "b7e1d4f": _workflow("b7e1d4f", status=WorkflowStatus.COMPLETED, minutes_ago=1),
        }
    )


# =============================================================================
# The arguments
# =============================================================================


@pytest.mark.parametrize("verb", sorted(RUST_ARGUMENTS))
def test_each_verb_takes_the_arguments_of_the_rust_cli(verb: str):
    command = cli.commands[verb]
    positionals = tuple(param.name for param in command.params if isinstance(param, click.Argument))
    options = frozenset(
        (
            tuple(param.opts),
            param.multiple,
            param.is_flag,
            None if param.multiple else param.default,
        )
        for param in command.params
        if isinstance(param, click.Option)
    )

    assert (positionals, options) == RUST_ARGUMENTS[verb]


def test_state_dir_is_the_option_before_the_verb_and_reads_the_rust_cli_variable():
    (state_dir,) = (param for param in cli.params if param.name == "state_dir")

    assert isinstance(state_dir, click.Option)
    assert state_dir.opts == ["--state-dir"]
    assert state_dir.envvar == "DOEFF_AGENTIC_STATE_DIR"


# =============================================================================
# ps
# =============================================================================


def test_ps_json_is_the_array_of_workflows_most_recent_first(world: FakeWorld):
    result = _invoke(world, "ps", "--json")

    assert result.exit_code == 0, result.stderr
    workflows = json.loads(result.stdout)
    assert isinstance(workflows, list)
    assert [workflow["id"] for workflow in workflows] == ["b7e1d4f", "a3f8b2c"]
    assert [list(workflow) for workflow in workflows] == [WORKFLOW_KEYS, WORKFLOW_KEYS]
    assert [list(agent) for agent in workflows[1]["agents"]] == [AGENT_KEYS, AGENT_KEYS]
    assert workflows[1]["agents"][0]["pane_id"] == "%3"
    # serde_json's to_string_pretty: an indent of 2
    assert result.stdout == json.dumps(workflows, indent=2) + "\n"


def test_ps_filters_by_workflow_status_and_by_agent_status(world: FakeWorld):
    by_status = json.loads(_invoke(world, "ps", "-s", "completed", "--json").stdout)
    by_either = json.loads(
        _invoke(world, "ps", "--status", "completed", "--status", "running", "--json").stdout
    )
    by_agent = json.loads(_invoke(world, "ps", "-a", "blocked", "--json").stdout)
    by_nothing = _invoke(world, "ps", "--agent-status", "done", "--json")

    assert [workflow["id"] for workflow in by_status] == ["b7e1d4f"]
    assert [workflow["id"] for workflow in by_either] == ["b7e1d4f", "a3f8b2c"]
    assert [workflow["id"] for workflow in by_agent] == ["a3f8b2c"]
    assert by_nothing.stdout == "[]\n"


def test_ps_text_is_the_table_of_the_rust_cli(world: FakeWorld):
    result = _invoke(world, "ps")

    assert result.exit_code == 0, result.stderr
    lines = result.stdout.splitlines()
    assert [cell.strip() for cell in lines[1].strip("|").split("|")] == [
        "ID",
        "Workflow",
        "Status",
        "Agent",
        "Agent Status",
        "Updated",
    ]
    assert [cell.strip() for cell in lines[5].strip("|").split("|")] == [
        "a3f8b2c",
        "flow-a3f8b2c",
        "running",
        "reviewer",
        "running",
        "5m ago",
    ]
    assert _invoke(FakeWorld(workflows={}), "ps").stdout == "No workflows found\n"


# =============================================================================
# show
# =============================================================================


def test_show_json_is_the_workflow_named_by_a_prefix(world: FakeWorld):
    result = _invoke(world, "show", "a3f", "--json")

    assert result.exit_code == 0, result.stderr
    workflow = json.loads(result.stdout)
    assert list(workflow) == WORKFLOW_KEYS
    assert workflow == world.workflows["a3f8b2c"].to_dict()


def test_show_of_an_unrecorded_or_ambiguous_id_fails_with_the_rust_message(world: FakeWorld):
    missing = _invoke(world, "show", "ffff", "--json")
    world.workflows["a3f0000"] = _workflow("a3f0000")
    ambiguous = _invoke(world, "show", "a3f")

    assert (missing.exit_code, missing.stdout, missing.stderr) == (
        1,
        "",
        "Error: Workflow not found: ffff\n",
    )
    assert ambiguous.exit_code == 1
    assert ambiguous.stderr.startswith("Error: Ambiguous prefix 'a3f'")


def test_show_text_marks_the_current_agent(world: FakeWorld):
    result = _invoke(world, "show", "a3f8b2c")

    assert "ID: a3f8b2c" in result.stdout.splitlines()
    assert "  * reviewer: running (doeff-wf-reviewer)" in result.stdout.splitlines()
    assert "    fixer: blocked (doeff-wf-fixer)" in result.stdout.splitlines()


# =============================================================================
# watch
# =============================================================================


def test_watch_json_streams_each_change_then_the_terminal_event(world: FakeWorld):
    steps = iter(
        (
            # the poll sees no change
            lambda workflow: workflow,
            lambda workflow: replace(workflow, updated_at=NOW, current_agent="fixer"),
            lambda workflow: replace(
                workflow, updated_at=NOW + timedelta(seconds=1), status=WorkflowStatus.COMPLETED
            ),
        )
    )

    def move_on(world: FakeWorld) -> None:
        world.workflows["a3f8b2c"] = next(steps)(world.workflows["a3f8b2c"])

    world.on_delay = move_on
    result = _invoke(world, "watch", "a3f", "--json", "--poll", "0.25")

    assert result.exit_code == 0, result.stderr
    lines = result.stdout.splitlines()
    updates = [json.loads(line) for line in lines[:-1]]
    assert [update["current_agent"] for update in updates] == ["reviewer", "fixer", "fixer"]
    assert [update["status"] for update in updates] == ["running", "running", "completed"]
    assert all(list(update) == WORKFLOW_KEYS for update in updates)
    # serde_json's to_string: one line without spaces
    assert lines[0] == json.dumps(updates[0], separators=(",", ":"))
    assert lines[-1] == '{"event":"terminal","status":"completed"}'
    assert world.delays == (0.25, 0.25, 0.25)


def test_watch_json_ends_with_the_deleted_event_when_the_workflow_goes(world: FakeWorld):
    world.on_delay = lambda world: world.workflows.pop("a3f8b2c")
    result = _invoke(world, "watch", "a3f8b2c", "--json")

    assert result.exit_code == 0, result.stderr
    assert result.stdout.splitlines()[-1] == '{"event":"deleted","workflow_id":"a3f8b2c"}'
    assert world.delays == (1.0,)


def test_watch_of_an_unrecorded_id_fails(world: FakeWorld):
    result = _invoke(world, "watch", "ffff")

    assert (result.exit_code, result.stderr) == (1, "Error: Workflow not found: ffff\n")


# =============================================================================
# attach
# =============================================================================


def test_attach_goes_to_the_named_agent_else_the_current_one(world: FakeWorld):
    named = _invoke(world, "attach", "a3f", "--agent", "fixer")
    current = _invoke(world, "attach", "a3f")

    assert named.stdout == "Attaching to: doeff-wf-fixer\n"
    assert current.stdout == "Attaching to: doeff-wf-reviewer\n"
    assert world.performed == (
        AgenticAttachAgent(workflow_id="a3f8b2c", agent="fixer"),
        AgenticAttachAgent(workflow_id="a3f8b2c", agent="reviewer"),
    )


def test_attach_to_a_workflow_without_agents_fails(world: FakeWorld):
    result = _invoke(world, "attach", "b7e")

    assert (result.exit_code, result.stderr) == (1, "Error: No agent to attach to\n")
    assert world.performed == ()


# =============================================================================
# send
# =============================================================================


def test_send_types_into_the_current_agent_or_the_named_one(world: FakeWorld):
    current = _invoke(world, "send", "a3f", "continue", "--json")
    named = _invoke(world, "send", "a3f", "stop here", "-a", "fixer")

    assert current.stdout == '{"ok":true}\n'
    assert named.stdout == "Message sent\n"
    assert world.performed == (
        AgenticSendToAgent(workflow_id="a3f8b2c", agent="reviewer", message="continue"),
        AgenticSendToAgent(workflow_id="a3f8b2c", agent="fixer", message="stop here"),
    )


def test_send_answers_the_input_the_workflow_waits_for(world: FakeWorld):
    world.waiting_for_input = frozenset({"b7e1d4f"})
    result = _invoke(world, "send", "b7e", "approve", "--json")

    assert result.stdout == '{"ok":true,"type":"user_input_response"}\n'
    assert world.performed == (AgenticAnswerInput(workflow_id="b7e1d4f", text="approve"),)


def test_send_reports_an_unreachable_session(world: FakeWorld):
    world.reachable = False
    as_json = _invoke(world, "send", "a3f", "continue", "--json")
    as_text = _invoke(world, "send", "a3f", "continue")

    assert (as_json.exit_code, as_json.stdout) == (0, '{"ok":false}\n')
    assert (as_text.exit_code, as_text.stderr) == (1, "Error: Failed to send message\n")


def test_send_to_a_workflow_without_a_current_agent_fails(world: FakeWorld):
    result = _invoke(world, "send", "b7e", "continue", "--json")

    assert (result.exit_code, result.stderr) == (1, "Error: No agent to send to\n")


# =============================================================================
# stop
# =============================================================================


def test_stop_json_names_the_stopped_agents(world: FakeWorld):
    result = _invoke(world, "stop", "a3f", "--json")

    assert result.stdout == '{"ok":true,"stopped":["reviewer","fixer"]}\n'
    assert world.performed == (AgenticStopWorkflow(workflow_id="a3f8b2c"),)
    assert world.workflows["a3f8b2c"].status is WorkflowStatus.STOPPED


def test_stop_text_and_an_unrecorded_id(world: FakeWorld):
    without_agents = _invoke(world, "stop", "b7e")
    missing = _invoke(world, "stop", "ffff", "--json")

    assert without_agents.stdout == "No agents to stop\n"
    assert (missing.exit_code, missing.stdout, missing.stderr) == (
        1,
        "",
        "Error: Workflow not found: ffff\n",
    )
