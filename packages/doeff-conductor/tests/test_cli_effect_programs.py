"""The conductor CLI commands are programs of conductor effects (card ki-1ef3718c89b3).

The subpackage shows its users effects and handlers only, so every ``conductor``
subcommand yields run effects (``doeff_conductor._effects.runs``) and issue effects that
``conductor_state_handler`` answers, instead of building ``ConductorAPI`` itself:

1. Each subcommand, run with the state handler swapped for one that records the effects
   and answers them, yields the effects listed for it — and never builds ConductorAPI.
2. The list of subcommands below names every subcommand of the CLI group, so a new
   subcommand without its effects fails here.
3. ``conductor_state_handler`` answers the run effects from a real state directory, and
   ``ConductorAPI`` is no longer a name the package root shows.
"""

import json
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import click
import doeff_conductor
import doeff_conductor._cli as cli_module
import pytest
from click.testing import CliRunner
from doeff_conductor import (
    AnswerGate,
    CleanupWorkspaces,
    ConductorStateDir,
    CreateIssue,
    DescribeEnvironment,
    GetIssue,
    GetTemplateSource,
    GetWorkflow,
    ListGates,
    ListIssues,
    ListOpenGates,
    ListTemplates,
    ListWorkflows,
    ListWorkspaces,
    OpenMonitor,
    PlanWorkflow,
    ReadProgress,
    RenderMonitor,
    ResolveIssue,
    ResumeWorkflow,
    RunWorkflow,
    StopWorkflow,
    ValidateWorkflow,
    conductor_state_handler,
)
from doeff_conductor._api import ConductorAPI
from doeff_conductor import Issue, IssueStatus, WorkflowHandle, WorkflowStatus
from doeff_conductor._verbs import BindingPlan, ValidationSuiteReport

from doeff import Effect, K, Pass, Resume, do, handler, run

_NOW = datetime(2026, 10, 27, tzinfo=timezone.utc)
_WORKFLOW = WorkflowHandle(
    id="wf123456",
    name="demo",
    status=WorkflowStatus.DONE,
    created_at=_NOW,
    updated_at=_NOW,
)
_ISSUE = Issue(id="ISSUE-001", title="Demo", body="body", status=IssueStatus.OPEN)

#: What the recording handler answers for each effect it records.
_ANSWERS: dict[type[Any], object] = {
    ListWorkflows: [_WORKFLOW],
    GetWorkflow: _WORKFLOW,
    RunWorkflow: _WORKFLOW,
    ResumeWorkflow: _WORKFLOW,
    StopWorkflow: ["agent-1"],
    AnswerGate: _WORKFLOW,
    ListOpenGates: [],
    ListGates: [],
    ReadProgress: [],
    PlanWorkflow: BindingPlan(
        workflow_name="demo",
        interpreter="plan",
        rows=(),
        estimated_budget_units=0,
        totals_by_profile={},
        capabilities_satisfied=True,
        missing_capabilities=(),
        supervision="autonomous",
    ),
    ValidateWorkflow: ValidationSuiteReport(
        workflow_name="demo",
        interpreter="validation",
        scenarios=(),
        supervision="autonomous",
    ),
    ListWorkspaces: [],
    CleanupWorkspaces: [],
    RenderMonitor: "monitor frame",
    OpenMonitor: None,
    DescribeEnvironment: {
        "profiles": [],
        "router_default_policy": {},
        "available_capabilities": [],
    },
    ListTemplates: {"basic_pr": "demo template"},
    GetTemplateSource: "(defworkflow demo [])",
    ConductorStateDir: Path("/state"),
    CreateIssue: _ISSUE,
    ListIssues: [_ISSUE],
    GetIssue: _ISSUE,
    ResolveIssue: _ISSUE,
}

#: Every subcommand path, the arguments it is run with, and the effects it must yield.
_CASES: tuple[tuple[str, tuple[str, ...], tuple[type[Any], ...]], ...] = (
    ("plan", ("plan", "flow.hy", "--json"), (PlanWorkflow,)),
    ("validate", ("validate", "flow.hy", "--json"), (ValidateWorkflow,)),
    ("run", ("run", "basic_pr", "--json"), (RunWorkflow,)),
    ("resume", ("resume", "wf123456", "--json"), (ResumeWorkflow,)),
    ("ps", ("ps", "--json"), (ListWorkflows,)),
    ("show", ("show", "wf123456", "--json"), (GetWorkflow, ListOpenGates)),
    ("show --since", ("show", "wf123456", "--since", "0", "--json"), (ReadProgress,)),
    ("wait", ("wait", "wf123456", "--json"), (GetWorkflow, ListOpenGates)),
    ("watch", ("watch", "wf123456", "--json"), (GetWorkflow,)),
    ("monitor", ("monitor",), (OpenMonitor,)),
    ("monitor --once", ("monitor", "--once"), (RenderMonitor,)),
    ("stop", ("stop", "wf123456", "--json"), (StopWorkflow,)),
    ("env describe", ("env", "describe", "--json"), (DescribeEnvironment,)),
    ("gate list", ("gate", "list", "--json"), (ListOpenGates,)),
    ("gate list <id>", ("gate", "list", "wf123456", "--json"), (ListGates,)),
    ("gate answer", ("gate", "answer", "wf123456", "g1", "proceed", "--json"), (AnswerGate,)),
    ("gates", ("gates", "--json"), (ListOpenGates,)),
    ("answer", ("answer", "wf123456", "g1", "abort", "--json"), (AnswerGate,)),
    ("issue create", ("issue", "create", "Demo", "--json"), (CreateIssue,)),
    ("issue list", ("issue", "list", "--json"), (ListIssues,)),
    ("issue show", ("issue", "show", "ISSUE-001", "--json"), (GetIssue,)),
    ("issue resolve", ("issue", "resolve", "ISSUE-001", "--json"), (GetIssue, ResolveIssue)),
    ("workspace list", ("workspace", "list", "--json"), (ListWorkspaces,)),
    ("workspace cleanup", ("workspace", "cleanup", "--dry-run", "--json"), (CleanupWorkspaces,)),
    ("template list", ("template", "list", "--json"), (ListTemplates,)),
    ("template show", ("template", "show", "basic_pr"), (GetTemplateSource,)),
)


class _Recorder:
    """The conductor effects one command yielded, in order, as the recording handler saw them."""

    def __init__(self) -> None:
        self.effects: tuple[type[Any], ...] = ()

    def state_handler(self, state_dir: object = None, *, issues_dir: object = None) -> Any:
        """Stand in for conductor_state_handler: record each conductor effect and answer it."""

        @handler
        def record(effect: Effect, k: K):
            answer_type = type(effect)
            if answer_type not in _ANSWERS:
                yield Pass(effect, k)
                return None
            self.effects = (*self.effects, answer_type)
            return (yield Resume(k, _ANSWERS[answer_type]))

        return record


def _command_paths(group: click.Group, prefix: tuple[str, ...] = ()) -> Iterator[str]:
    """Every leaf subcommand path of a click group, as the words a user types."""
    for name, command in group.commands.items():
        if isinstance(command, click.Group):
            yield from _command_paths(command, (*prefix, name))
        else:
            yield " ".join((*prefix, name))


@pytest.fixture
def no_conductor_api(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make building ConductorAPI fail, so a command that still builds it is caught."""

    def refuse(self: ConductorAPI, state_dir: object = None) -> None:
        raise AssertionError("the CLI built ConductorAPI instead of yielding a run effect")

    monkeypatch.setattr(ConductorAPI, "__init__", refuse)


@pytest.mark.parametrize(("label", "argv", "effects"), _CASES, ids=[case[0] for case in _CASES])
def test_subcommand_yields_its_effects(
    label: str,
    argv: tuple[str, ...],
    effects: tuple[type[Any], ...],
    monkeypatch: pytest.MonkeyPatch,
    no_conductor_api: None,
) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(cli_module, "conductor_state_handler", recorder.state_handler)

    result = CliRunner().invoke(cli_module.cli, list(argv))

    assert result.exception is None or isinstance(result.exception, SystemExit), result.output
    assert result.exit_code == 0, result.output
    assert recorder.effects == effects


def test_every_subcommand_has_its_effects_listed() -> None:
    listed = {label.split(" --")[0].split(" <")[0] for label, _argv, _effects in _CASES}

    assert set(_command_paths(cli_module.cli)) == listed


def test_state_handler_answers_run_effects_from_the_state_dir(tmp_path: Path) -> None:
    workflow_dir = tmp_path / "workflows" / "abc12345"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "meta.json").write_text(
        json.dumps(
            {
                "id": "abc12345",
                "name": "demo",
                "status": "running",
                "created_at": _NOW.isoformat(),
                "updated_at": _NOW.isoformat(),
                "agents": ["agent-1"],
            }
        )
    )

    @do
    def stop_running():
        running = yield ListWorkflows(status=(WorkflowStatus.RUNNING,))
        stopped = yield StopWorkflow(workflow_id=running[0].id)
        after = yield GetWorkflow(workflow_id="abc")
        state_dir = yield ConductorStateDir()
        return [workflow.id for workflow in running], stopped, after.status, state_dir

    ids, stopped, status_after, state_dir = run(
        conductor_state_handler(tmp_path)(stop_running())
    )

    assert ids == ["abc12345"]
    assert stopped == ["agent-1"]
    assert status_after is WorkflowStatus.ABORTED
    assert state_dir == tmp_path


def test_package_root_shows_no_conductor_api() -> None:
    assert "ConductorAPI" not in vars(doeff_conductor)
