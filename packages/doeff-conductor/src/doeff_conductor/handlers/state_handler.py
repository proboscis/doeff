"""
State handler for doeff-conductor run effects.

``conductor_state_handler`` answers the run effects (``doeff_conductor.effects.runs``)
and the issue effects from the conductor's state directory and issue vault. The
``conductor`` CLI commands run their programs under it.
"""

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from doeff import Effect, K, Pass, Resume, handler
from doeff.program import ProgramHandler
from doeff_conductor.effects.issue import CreateIssue, GetIssue, ListIssues, ResolveIssue
from doeff_conductor.effects.runs import (
    AnswerGate,
    CleanupWorkspaces,
    ConductorStateDir,
    DescribeEnvironment,
    GetTemplateSource,
    GetWorkflow,
    ListGates,
    ListOpenGates,
    ListTemplates,
    ListWorkflows,
    ListWorkspaces,
    OpenMonitor,
    PlanWorkflow,
    ReadProgress,
    RenderMonitor,
    ResumeWorkflow,
    RunWorkflow,
    StopWorkflow,
    ValidateWorkflow,
)

if TYPE_CHECKING:
    from doeff_conductor._api import ConductorAPI
    from doeff_conductor.handlers.issue_handler import IssueHandler

__all__ = ["conductor_state_handler"]


class _Stores:
    """The run state store and issue vault one handler answers from, opened on first use.

    Opening on first use keeps a command that reads neither (``template list``) from
    creating the state directory or the issue vault.
    """

    def __init__(self, state_dir: str | Path | None, issues_dir: Path | None) -> None:
        self._state_dir = state_dir
        self._issues_dir = issues_dir
        self._api: ConductorAPI | None = None
        self._issues: IssueHandler | None = None

    def api(self) -> "ConductorAPI":
        """The run state store, for the effects that read or change workflows."""
        if self._api is None:
            from doeff_conductor._api import ConductorAPI

            self._api = ConductorAPI(self._state_dir)
        return self._api

    def state_dir(self) -> Path:
        """The state directory, for the effects that read overseer and progress files."""
        return self.api().state_dir

    def issues(self) -> "IssueHandler":
        """The issue vault, for the issue effects."""
        if self._issues is None:
            from doeff_conductor.handlers.issue_handler import IssueHandler

            self._issues = IssueHandler(self._issues_dir)
        return self._issues


class _NotMine:
    """Marks an effect this handler does not answer, so it passes outward."""


_NOT_MINE = _NotMine()


def _params(params: Mapping[str, Any] | None) -> dict[str, Any]:
    """Copy JSON workflow parameters into the dict the run state store takes."""
    return dict(params) if params else {}


def _plan(stores: _Stores, effect: PlanWorkflow) -> object:
    """Plan a workflow file, snapshotting its source first when a run id is given."""
    from doeff_conductor.verbs import plan_workflow
    from doeff_conductor.workflow_loader import load_workflow_spec, snapshot_workflow_source

    workflow_source = effect.workflow_file
    if effect.run_id is not None:
        workflow_source = str(
            snapshot_workflow_source(
                effect.workflow_file,
                state_dir=stores.state_dir(),
                run_id=effect.run_id,
            )
        )
    return plan_workflow(load_workflow_spec(workflow_source), supervision=effect.supervision)


def _validate(stores: _Stores, effect: ValidateWorkflow) -> object:
    """Validate a workflow file, persisting overseer state when a run id is given."""
    from doeff_conductor.verbs import validate_workflow
    from doeff_conductor.workflow_loader import load_workflow_spec

    return validate_workflow(
        load_workflow_spec(effect.workflow_file),
        scenarios=effect.scenarios or None,
        supervision=effect.supervision,
        state_dir=str(stores.state_dir()) if effect.run_id else None,
        run_id=effect.run_id,
    )


def _render_monitor(stores: _Stores, effect: RenderMonitor) -> object:
    """Render one frame of the read-only monitor from the state directory."""
    from doeff_conductor.monitor import render_dashboard

    return render_dashboard(
        stores.state_dir(),
        effect.workflow_id,
        only_running=effect.only_running,
        view=effect.view,
    )


def _open_monitor(stores: _Stores, effect: OpenMonitor) -> None:
    """Run the interactive read-only monitor on the state directory until the user quits."""
    from doeff_conductor.monitor_app import MonitorApp

    MonitorApp(
        str(stores.state_dir()),
        workflow_id=effect.workflow_id,
        only_running=effect.only_running,
        interval=effect.interval,
    ).run()


def _answer_run(stores: _Stores, effect: Effect) -> object:  # noqa: PLR0911, PLR0912 - one case per run effect
    """Answer a workflow, gate or workspace effect from the run state store."""
    from doeff_conductor.overseer import list_gates_with_status, list_open_gates, progress_since

    match effect:
        case ListWorkflows():
            return stores.api().list_workflows(status=list(effect.status) or None)
        case GetWorkflow():
            return stores.api().get_workflow(effect.workflow_id)
        case RunWorkflow():
            return stores.api().run_workflow(
                effect.template_or_file,
                issue=effect.issue,
                params=_params(effect.params),
                run_id=effect.run_id,
                supervision=effect.supervision,
            )
        case ResumeWorkflow():
            return stores.api().resume_workflow(
                effect.workflow_id,
                params=_params(effect.params),
                supervision=effect.supervision,
            )
        case StopWorkflow():
            return stores.api().stop_workflow(effect.workflow_id, agent=effect.agent)
        case AnswerGate():
            return stores.api().answer_gate(
                effect.workflow_id,
                effect.gate_id,
                effect.option,
                params=_params(effect.params),
                note=effect.note,
            )
        case ListOpenGates():
            return list_open_gates(stores.state_dir(), effect.workflow_id)
        case ListGates():
            return list_gates_with_status(stores.state_dir(), effect.workflow_id)
        case ReadProgress():
            return progress_since(stores.state_dir(), effect.workflow_id, effect.since)
        case ListWorkspaces():
            return stores.api().list_workspaces(workflow_id=effect.workflow_id)
        case CleanupWorkspaces():
            return stores.api().cleanup_workspaces(
                dry_run=effect.dry_run,
                older_than_days=effect.older_than_days,
            )
        case ConductorStateDir():
            return stores.state_dir()
        case _:
            return _NOT_MINE


def _answer_tooling(stores: _Stores, effect: Effect) -> object:  # noqa: PLR0911 - one case per effect
    """Answer a planning, monitor, environment, template or issue effect."""
    from doeff_conductor.templates import get_available_templates, get_template_source
    from doeff_conductor.verbs import describe_environment

    match effect:
        case PlanWorkflow():
            return _plan(stores, effect)
        case ValidateWorkflow():
            return _validate(stores, effect)
        case RenderMonitor():
            return _render_monitor(stores, effect)
        case OpenMonitor():
            return _open_monitor(stores, effect)
        case DescribeEnvironment():
            return describe_environment()
        case ListTemplates():
            return get_available_templates()
        case GetTemplateSource():
            return get_template_source(effect.name)
        case CreateIssue():
            return stores.issues().handle_create_issue(effect)
        case ListIssues():
            return stores.issues().handle_list_issues(effect)
        case GetIssue():
            return stores.issues().handle_get_issue(effect)
        case ResolveIssue():
            return stores.issues().handle_resolve_issue(effect)
        case _:
            return _NOT_MINE


def conductor_state_handler(
    state_dir: str | Path | None = None,
    *,
    issues_dir: Path | None = None,
) -> ProgramHandler:
    """Build the protocol handler that answers run effects and issue effects.

    Args:
        state_dir: Conductor state directory. Uses the XDG default if not provided.
        issues_dir: Issue vault directory. Auto-detected if not provided.

    Returns:
        Handler-protocol callable; other effects pass to the outer handlers.

    Example:
        @do
        def running():
            return (yield ListWorkflows(status=(WorkflowStatus.RUNNING,)))

        workflows = run(conductor_state_handler(state_dir)(running()))
    """
    stores = _Stores(state_dir, issues_dir)

    @handler
    def answer_from_stores(effect: Effect, k: K):
        """Answer one run or issue effect from the stores; pass any other effect outward."""
        answer = _answer_run(stores, effect)
        if answer is _NOT_MINE:
            answer = _answer_tooling(stores, effect)
        if answer is _NOT_MINE:
            yield Pass(effect, k)
            return None
        return (yield Resume(k, answer))

    return answer_from_stores
