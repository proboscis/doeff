"""
Run effects for doeff-conductor.

Effects for reading and changing the conductor's runs, answered by
``conductor_state_handler`` from the state directory it is given:

- Workflows: ListWorkflows, GetWorkflow, RunWorkflow, ResumeWorkflow, StopWorkflow
- Gates and progress: ListOpenGates, ListGates, AnswerGate, ReadProgress
- Planning: PlanWorkflow, ValidateWorkflow
- Workspaces: ListWorkspaces, CleanupWorkspaces
- Monitor: RenderMonitor, OpenMonitor
- Environment and templates: DescribeEnvironment, ListTemplates, GetTemplate, GetTemplateSource
- ConductorStateDir: the state directory the handler reads (for messages)

The ``conductor`` CLI commands are programs of these effects.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from doeff_conductor._effects.base import ConductorEffectBase

if TYPE_CHECKING:
    from doeff_conductor._types import Issue, WorkflowStatus


@dataclass(frozen=True, kw_only=True)
class ListWorkflows(ConductorEffectBase):
    """List the workflows in the state directory, newest update first.

    Yields: list[WorkflowHandle]

    Example:
        @do
        def running():
            return (yield ListWorkflows(status=(WorkflowStatus.RUNNING,)))
    """

    status: "tuple[WorkflowStatus, ...]" = ()  # Keep only these statuses; empty keeps all


@dataclass(frozen=True, kw_only=True)
class GetWorkflow(ConductorEffectBase):
    """Get a workflow by ID or unique ID prefix.

    Yields: WorkflowHandle | None (None when no workflow matches)
    Raises: ValueError when the prefix matches more than one workflow
    """

    workflow_id: str


@dataclass(frozen=True, kw_only=True)
class RunWorkflow(ConductorEffectBase):
    """Run a workflow template or Hy workflow file to its end or its first open gate.

    Yields: WorkflowHandle (status done, blocked or error)
    """

    template_or_file: str  # Template name or path to a workflow file
    issue: "Issue | None" = None  # Issue passed to the workflow
    # Workflow keyword parameters: the JSON object of ``--params``, passed through as is
    params: Mapping[str, Any] | None = None
    run_id: str | None = None  # Stable workflow id for resume/replay runs
    supervision: str = "autonomous"  # Run-scoped overseer supervision policy


@dataclass(frozen=True, kw_only=True)
class ResumeWorkflow(ConductorEffectBase):
    """Resume a run from its snapshotted workflow source.

    Yields: WorkflowHandle
    Raises: ValueError when the run has no snapshot
    """

    workflow_id: str
    # Workflow keyword parameters: the JSON object of ``--params``, passed through as is
    params: Mapping[str, Any] | None = None
    supervision: str | None = None  # None keeps the supervision the run recorded


@dataclass(frozen=True, kw_only=True)
class StopWorkflow(ConductorEffectBase):
    """Stop a workflow (or one agent of it) and mark it aborted.

    Yields: list[str] (the stopped agent or session names)
    Raises: ValueError when the workflow is not found
    """

    workflow_id: str
    agent: str | None = None  # Stop only this agent


@dataclass(frozen=True, kw_only=True)
class AnswerGate(ConductorEffectBase):
    """Answer an open overseer gate and apply its closure-preserving option.

    Yields: WorkflowHandle (after proceed/extend/retry-agent resume, redirect or abort)
    """

    workflow_id: str
    gate_id: str
    option: str  # proceed, extend, retry-agent, redirect or abort
    # Resume keyword parameters: the JSON object of ``--params``, passed through as is
    params: Mapping[str, Any] | None = None
    note: str = ""  # Adjudication note for the journal


@dataclass(frozen=True, kw_only=True)
class ListOpenGates(ConductorEffectBase):
    """List the open gates of one workflow, or of every workflow.

    Yields: list[dict[str, Any]]
    """

    workflow_id: str | None = None


@dataclass(frozen=True, kw_only=True)
class ListGates(ConductorEffectBase):
    """List the open and answered gates of one workflow, each with its status.

    Yields: list[dict[str, Any]]
    """

    workflow_id: str


@dataclass(frozen=True, kw_only=True)
class ReadProgress(ConductorEffectBase):
    """Read the progress events of a workflow after a sequence number.

    Yields: list[dict[str, Any]]
    """

    workflow_id: str
    since: int


@dataclass(frozen=True, kw_only=True)
class PlanWorkflow(ConductorEffectBase):
    """Produce the overseer binding plan for a workflow file.

    With ``run_id`` the workflow source is first snapshotted under that run.

    Yields: BindingPlan
    """

    workflow_file: str
    supervision: str = "autonomous"
    run_id: str | None = None


@dataclass(frozen=True, kw_only=True)
class ValidateWorkflow(ConductorEffectBase):
    """Validate a workflow file's control flow under scenario-driven stubs.

    With ``run_id`` the overseer state is persisted under that run.

    Yields: ValidationSuiteReport
    """

    workflow_file: str
    scenarios: tuple[str, ...] = ()  # Empty runs the built-in suite
    supervision: str = "autonomous"
    run_id: str | None = None


@dataclass(frozen=True, kw_only=True)
class ListWorkspaces(ConductorEffectBase):
    """List materialized workspaces.

    Yields: list[Workspace]
    """

    workflow_id: str | None = None


@dataclass(frozen=True, kw_only=True)
class CleanupWorkspaces(ConductorEffectBase):
    """Remove orphaned workspace materializations.

    Yields: list[Path] (the removed paths, or the paths a dry run would remove)
    """

    dry_run: bool = False
    older_than_days: int | None = None  # Only workspaces at least this many days old


@dataclass(frozen=True, kw_only=True)
class RenderMonitor(ConductorEffectBase):
    """Render one static frame of the read-only run monitor (ADR 0002).

    Yields: a Rich renderable
    """

    workflow_id: str | None = None
    only_running: bool = True
    view: str | None = None  # overview, compact or tree


@dataclass(frozen=True, kw_only=True)
class OpenMonitor(ConductorEffectBase):
    """Open the interactive read-only run monitor until the user quits it (ADR 0002).

    Yields: None
    """

    workflow_id: str | None = None
    only_running: bool = True
    interval: float = 1.0  # Refresh interval in seconds


@dataclass(frozen=True, kw_only=True)
class DescribeEnvironment(ConductorEffectBase):
    """Describe profiles, role conventions, router policy and capabilities.

    Yields: dict[str, Any]
    """


@dataclass(frozen=True, kw_only=True)
class ListTemplates(ConductorEffectBase):
    """List the workflow templates by name with their descriptions.

    Yields: dict[str, str]
    """


@dataclass(frozen=True, kw_only=True)
class GetTemplate(ConductorEffectBase):
    """Get a workflow template's function, to call with the template's arguments.

    Yields: Callable[..., Program] (the template's ``@do`` function)
    Raises: KeyError when no template has the name
    """

    name: str


@dataclass(frozen=True, kw_only=True)
class GetTemplateSource(ConductorEffectBase):
    """Get a workflow template's source code.

    Yields: str
    Raises: KeyError when no template has the name
    """

    name: str


@dataclass(frozen=True, kw_only=True)
class ConductorStateDir(ConductorEffectBase):
    """The state directory the handler reads, for messages that name it.

    Yields: Path
    """
