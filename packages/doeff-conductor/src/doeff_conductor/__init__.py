"""
doeff-conductor: Multi-agent workflow orchestration.

This package provides a unified orchestration layer for multi-agent workflows:

- Issue-driven agent workflows
- Git workspace management
- Multi-agent DAG execution
- Full CLI for monitoring and control
"""
from importlib import import_module
from typing import TYPE_CHECKING

from doeff_conductor._effects import (
    BLOCKER_FINDING as BLOCKER_FINDING,
)
from doeff_conductor._effects import (
    CALIBRATION_SAMPLE_BUDGET_KEY as CALIBRATION_SAMPLE_BUDGET_KEY,
)
from doeff_conductor._effects import (
    DEFAULT_REVIEW_ROUTE_TABLE as DEFAULT_REVIEW_ROUTE_TABLE,
)
from doeff_conductor._effects import (
    REVIEW_VERDICT_RESULT_SCHEMA as REVIEW_VERDICT_RESULT_SCHEMA,
)
from doeff_conductor._effects import (
    TIER1_REVIEW_BUDGET_KEY as TIER1_REVIEW_BUDGET_KEY,
)
from doeff_conductor._effects import (
    TIER2_ESCALATION_BUDGET_KEY as TIER2_ESCALATION_BUDGET_KEY,
)
from doeff_conductor._effects import (
    Agent as Agent,
)
from doeff_conductor._effects import (
    AgentAttemptExhaustedError as AgentAttemptExhaustedError,
)
from doeff_conductor._effects import (
    AgentCall as AgentCall,
)
from doeff_conductor._effects import (
    AgentDeadlineExceededError as AgentDeadlineExceededError,
)
from doeff_conductor._effects import (
    AgentEffect as AgentEffect,
)
from doeff_conductor._effects import (
    AgentTask as AgentTask,
)
from doeff_conductor._effects import (
    AgentValidationErrorKind as AgentValidationErrorKind,
)
from doeff_conductor._effects import (
    AgentValidationFailure as AgentValidationFailure,
)
from doeff_conductor._effects import (
    AnswerGate as AnswerGate,
)
from doeff_conductor._effects import (
    CleanupWorkspaces as CleanupWorkspaces,
)
from doeff_conductor._effects import (
    Commit as Commit,
)
from doeff_conductor._effects import (
    ConductorEffectBase as ConductorEffectBase,
)
from doeff_conductor._effects import (
    ConductorStateDir as ConductorStateDir,
)
from doeff_conductor._effects import (
    CreateIssue as CreateIssue,
)
from doeff_conductor._effects import (
    CreatePR as CreatePR,
)
from doeff_conductor._effects import (
    CreateWorkspace as CreateWorkspace,
)
from doeff_conductor._effects import (
    DefaultReviewRouter as DefaultReviewRouter,
)
from doeff_conductor._effects import (
    DeleteWorkspace as DeleteWorkspace,
)
from doeff_conductor._effects import (
    DescribeEnvironment as DescribeEnvironment,
)
from doeff_conductor._effects import (
    DurableReviewBudget as DurableReviewBudget,
)
from doeff_conductor._effects import (
    Exec as Exec,
)
from doeff_conductor._effects import (
    GateCall as GateCall,
)
from doeff_conductor._effects import (
    GetIssue as GetIssue,
)
from doeff_conductor._effects import (
    GetTemplateSource as GetTemplateSource,
)
from doeff_conductor._effects import (
    GetWorkflow as GetWorkflow,
)
from doeff_conductor._effects import (
    ListGates as ListGates,
)
from doeff_conductor._effects import (
    ListIssues as ListIssues,
)
from doeff_conductor._effects import (
    ListOpenGates as ListOpenGates,
)
from doeff_conductor._effects import (
    ListTemplates as ListTemplates,
)
from doeff_conductor._effects import (
    ListWorkflows as ListWorkflows,
)
from doeff_conductor._effects import (
    ListWorkspaces as ListWorkspaces,
)
from doeff_conductor._effects import (
    MergeCall as MergeCall,
)
from doeff_conductor._effects import (
    MergePR as MergePR,
)
from doeff_conductor._effects import (
    MergeWorkspaces as MergeWorkspaces,
)
from doeff_conductor._effects import (
    OpenGate as OpenGate,
)
from doeff_conductor._effects import (
    OpenGateReason as OpenGateReason,
)
from doeff_conductor._effects import (
    OpenMonitor as OpenMonitor,
)
from doeff_conductor._effects import (
    PlanWorkflow as PlanWorkflow,
)
from doeff_conductor._effects import (
    Push as Push,
)
from doeff_conductor._effects import (
    RandomCall as RandomCall,
)
from doeff_conductor._effects import (
    ReadProgress as ReadProgress,
)
from doeff_conductor._effects import (
    RemainingReviewBudget as RemainingReviewBudget,
)
from doeff_conductor._effects import (
    RenderMonitor as RenderMonitor,
)
from doeff_conductor._effects import (
    ResolveIssue as ResolveIssue,
)
from doeff_conductor._effects import (
    ResumeWorkflow as ResumeWorkflow,
)
from doeff_conductor._effects import (
    ReviewerAgentLost as ReviewerAgentLost,
)
from doeff_conductor._effects import (
    ReviewEscalationReason as ReviewEscalationReason,
)
from doeff_conductor._effects import (
    ReviewEscalationTerminal as ReviewEscalationTerminal,
)
from doeff_conductor._effects import (
    ReviewFinding as ReviewFinding,
)
from doeff_conductor._effects import (
    ReviewItem as ReviewItem,
)
from doeff_conductor._effects import (
    ReviewRoutingResult as ReviewRoutingResult,
)
from doeff_conductor._effects import (
    ReviewSeverity as ReviewSeverity,
)
from doeff_conductor._effects import (
    ReviewStakes as ReviewStakes,
)
from doeff_conductor._effects import (
    ReviewStakesLevel as ReviewStakesLevel,
)
from doeff_conductor._effects import (
    ReviewVerdict as ReviewVerdict,
)
from doeff_conductor._effects import (
    ReviewVerdictArtifact as ReviewVerdictArtifact,
)
from doeff_conductor._effects import (
    ReviewVerdictTerminal as ReviewVerdictTerminal,
)
from doeff_conductor._effects import (
    RunWorkflow as RunWorkflow,
)
from doeff_conductor._effects import (
    StopWorkflow as StopWorkflow,
)
from doeff_conductor._effects import (
    TimeCall as TimeCall,
)
from doeff_conductor._effects import (
    ValidateWorkflow as ValidateWorkflow,
)
from doeff_conductor._effects import (
    WorkspaceCall as WorkspaceCall,
)
from doeff_conductor._effects import (
    route_review_item as route_review_item,
)
from doeff_conductor._effects import (
    run_review_routing_demo as run_review_routing_demo,
)
from doeff_conductor._exceptions import (
    AgentError as AgentError,
)
from doeff_conductor._exceptions import (
    AgentTimeoutError as AgentTimeoutError,
)
from doeff_conductor._exceptions import (
    ConductorError as ConductorError,
)
from doeff_conductor._exceptions import (
    ConductorStateWarning as ConductorStateWarning,
)
from doeff_conductor._exceptions import (
    GitCommandError as GitCommandError,
)
from doeff_conductor._exceptions import (
    IssueAlreadyExistsError as IssueAlreadyExistsError,
)
from doeff_conductor._exceptions import (
    IssueFileCorruptError as IssueFileCorruptError,
)
from doeff_conductor._exceptions import (
    IssueNotFoundError as IssueNotFoundError,
)
from doeff_conductor._exceptions import (
    JournalCorruptionError as JournalCorruptionError,
)
from doeff_conductor._exceptions import (
    PRError as PRError,
)
from doeff_conductor._exceptions import (
    WorkspaceError as WorkspaceError,
)
from doeff_conductor._handlers import (
    MockConductorRuntime as MockConductorRuntime,
)
from doeff_conductor._handlers import (
    agent_handler as agent_handler,
)
from doeff_conductor._handlers import (
    conductor_state_handler as conductor_state_handler,
)
from doeff_conductor._handlers import (
    default_scheduled_handlers as default_scheduled_handlers,
)
from doeff_conductor._handlers import (
    exec_handler as exec_handler,
)
from doeff_conductor._handlers import (
    git_handler as git_handler,
)
from doeff_conductor._handlers import (
    issue_handler as issue_handler,
)
from doeff_conductor._handlers import (
    journaled_agent_handler as journaled_agent_handler,
)
from doeff_conductor._handlers import (
    journaled_workflow_effect_handler as journaled_workflow_effect_handler,
)
from doeff_conductor._handlers import (
    journaled_workspace_handler as journaled_workspace_handler,
)
from doeff_conductor._handlers import (
    make_async_scheduled_handler as make_async_scheduled_handler,
)
from doeff_conductor._handlers import (
    make_blocking_scheduled_handler as make_blocking_scheduled_handler,
)
from doeff_conductor._handlers import (
    make_blocking_scheduled_handler_with_store as make_blocking_scheduled_handler_with_store,
)
from doeff_conductor._handlers import (
    make_scheduled_handler_with_store as make_scheduled_handler_with_store,
)
from doeff_conductor._handlers import (
    mock_handlers as mock_handlers,
)
from doeff_conductor._handlers import (
    production_handlers as production_handlers,
)
from doeff_conductor._handlers import (
    workspace_handler as workspace_handler,
)
from doeff_conductor._replay_keying import (
    ResolvedIdentity as ResolvedIdentity,
)
from doeff_conductor._replay_keying import (
    agent_cache_key as agent_cache_key,
)
from doeff_conductor._replay_keying import (
    longest_valid_prefix as longest_valid_prefix,
)
from doeff_conductor._replay_keying import (
    node_identity_fingerprint as node_identity_fingerprint,
)
from doeff_conductor._replay_keying import (
    resolved_identity_fingerprint as resolved_identity_fingerprint,
)
from doeff_conductor._replay_keying import (
    workflow_effect_cache_key as workflow_effect_cache_key,
)
from doeff_conductor._templates import (
    get_available_templates as get_available_templates,
)
from doeff_conductor._templates import (
    get_template as get_template,
)
from doeff_conductor._templates import (
    get_template_source as get_template_source,
)
from doeff_conductor._templates import (
    is_template as is_template,
)
from doeff_conductor._types import (
    AgentRef as AgentRef,
)
from doeff_conductor._types import (
    ExecResult as ExecResult,
)
from doeff_conductor._types import (
    Issue as Issue,
)
from doeff_conductor._types import (
    IssueStatus as IssueStatus,
)
from doeff_conductor._types import (
    MergeConflict as MergeConflict,
)
from doeff_conductor._types import (
    MergeStatus as MergeStatus,
)
from doeff_conductor._types import (
    MergeStrategy as MergeStrategy,
)
from doeff_conductor._types import (
    MergeWorkspacesResult as MergeWorkspacesResult,
)
from doeff_conductor._types import (
    PRHandle as PRHandle,
)
from doeff_conductor._types import (
    WorkflowHandle as WorkflowHandle,
)
from doeff_conductor._types import (
    WorkflowStatus as WorkflowStatus,
)
from doeff_conductor._types import (
    Workspace as Workspace,
)


# Names of the submodules behind `_` (ADR-DOE-API-001 R5), read on first use: the root does not import
# them while it loads. `X as X` shows each name to type checkers; `__all__` is not used (DOEFF021).
if TYPE_CHECKING:
    from doeff_conductor._dsl import AgentSpec as AgentSpec
    from doeff_conductor._dsl import ArtifactSpec as ArtifactSpec
    from doeff_conductor._dsl import BindSpec as BindSpec
    from doeff_conductor._dsl import FieldRef as FieldRef
    from doeff_conductor._dsl import ForbiddenSpec as ForbiddenSpec
    from doeff_conductor._dsl import GateSpec as GateSpec
    from doeff_conductor._dsl import LoopSpec as LoopSpec
    from doeff_conductor._dsl import MergeSpec as MergeSpec
    from doeff_conductor._dsl import OksProjection as OksProjection
    from doeff_conductor._dsl import ParallelForSpec as ParallelForSpec
    from doeff_conductor._dsl import ParallelSpec as ParallelSpec
    from doeff_conductor._dsl import PhaseSpec as PhaseSpec
    from doeff_conductor._dsl import PromptExpr as PromptExpr
    from doeff_conductor._dsl import RandomSpec as RandomSpec
    from doeff_conductor._dsl import RawNodeSpec as RawNodeSpec
    from doeff_conductor._dsl import Ref as Ref
    from doeff_conductor._dsl import TimeSpec as TimeSpec
    from doeff_conductor._dsl import WorkflowExpansionError as WorkflowExpansionError
    from doeff_conductor._dsl import WorkflowSpec as WorkflowSpec
    from doeff_conductor._dsl import WorkspaceSpec as WorkspaceSpec
    from doeff_conductor._dsl import agent as agent
    from doeff_conductor._dsl import agent_bang as agent_bang
    from doeff_conductor._dsl import artifact as artifact
    from doeff_conductor._dsl import ask as ask
    from doeff_conductor._dsl import bind as bind
    from doeff_conductor._dsl import defphase as defphase
    from doeff_conductor._dsl import defworkflow as defworkflow
    from doeff_conductor._dsl import deref as deref
    from doeff_conductor._dsl import field as field
    from doeff_conductor._dsl import gate as gate
    from doeff_conductor._dsl import gate_bang as gate_bang
    from doeff_conductor._dsl import local as local
    from doeff_conductor._dsl import loop as loop
    from doeff_conductor._dsl import merge as merge
    from doeff_conductor._dsl import merge_bang as merge_bang
    from doeff_conductor._dsl import oks as oks
    from doeff_conductor._dsl import parallel as parallel
    from doeff_conductor._dsl import parallel_for as parallel_for
    from doeff_conductor._dsl import pipeline as pipeline
    from doeff_conductor._dsl import prompt as prompt
    from doeff_conductor._dsl import random_bang as random_bang
    from doeff_conductor._dsl import random_value as random_value
    from doeff_conductor._dsl import raw_node as raw_node
    from doeff_conductor._dsl import ref as ref
    from doeff_conductor._dsl import time as time
    from doeff_conductor._dsl import time_bang as time_bang
    from doeff_conductor._dsl import workspace as workspace
    from doeff_conductor._dsl import workspace_bang as workspace_bang
    from doeff_conductor._effects.review import BudgetConsumption as BudgetConsumption
    from doeff_conductor._effects.review import BudgetCounterEntry as BudgetCounterEntry
    from doeff_conductor._effects.review import BudgetCounterKey as BudgetCounterKey
    from doeff_conductor._effects.review import CalibrationEscapeRecord as CalibrationEscapeRecord
    from doeff_conductor._effects.review import CalibrationLaneRate as CalibrationLaneRate
    from doeff_conductor._effects.review import CalibrationLedger as CalibrationLedger
    from doeff_conductor._effects.review import CalibrationPolicy as CalibrationPolicy
    from doeff_conductor._effects.review import ClosureTerminal as ClosureTerminal
    from doeff_conductor._effects.base import E as E
    from doeff_conductor._effects.review import GateOption as GateOption
    from doeff_conductor._effects.review import ReviewBudgetStatus as ReviewBudgetStatus
    from doeff_conductor._effects.review import ReviewRouteRule as ReviewRouteRule
    from doeff_conductor._effects.review import ReviewTier as ReviewTier
    from doeff_conductor._effects.review import Tier1ReviewResult as Tier1ReviewResult
    from doeff_conductor._effects.review import Tier2Callback as Tier2Callback
    from doeff_conductor._effects.review import Tier2ReviewRequest as Tier2ReviewRequest
    from doeff_conductor._handlers.journaled_workspace import (
        PreCoverageRunError as PreCoverageRunError,
    )
    from doeff_conductor._handlers.workspace_handler import (
        WorkspaceStateError as WorkspaceStateError,
    )

_LAZY_EXPORTS: dict[str, str] = {
    "AgentSpec": "doeff_conductor._dsl",
    "ArtifactSpec": "doeff_conductor._dsl",
    "BindSpec": "doeff_conductor._dsl",
    "FieldRef": "doeff_conductor._dsl",
    "ForbiddenSpec": "doeff_conductor._dsl",
    "GateSpec": "doeff_conductor._dsl",
    "LoopSpec": "doeff_conductor._dsl",
    "MergeSpec": "doeff_conductor._dsl",
    "OksProjection": "doeff_conductor._dsl",
    "ParallelForSpec": "doeff_conductor._dsl",
    "ParallelSpec": "doeff_conductor._dsl",
    "PhaseSpec": "doeff_conductor._dsl",
    "PromptExpr": "doeff_conductor._dsl",
    "RandomSpec": "doeff_conductor._dsl",
    "RawNodeSpec": "doeff_conductor._dsl",
    "Ref": "doeff_conductor._dsl",
    "TimeSpec": "doeff_conductor._dsl",
    "WorkflowExpansionError": "doeff_conductor._dsl",
    "WorkflowSpec": "doeff_conductor._dsl",
    "WorkspaceSpec": "doeff_conductor._dsl",
    "agent": "doeff_conductor._dsl",
    "agent_bang": "doeff_conductor._dsl",
    "artifact": "doeff_conductor._dsl",
    "ask": "doeff_conductor._dsl",
    "bind": "doeff_conductor._dsl",
    "defphase": "doeff_conductor._dsl",
    "defworkflow": "doeff_conductor._dsl",
    "deref": "doeff_conductor._dsl",
    "field": "doeff_conductor._dsl",
    "gate": "doeff_conductor._dsl",
    "gate_bang": "doeff_conductor._dsl",
    "local": "doeff_conductor._dsl",
    "loop": "doeff_conductor._dsl",
    "merge": "doeff_conductor._dsl",
    "merge_bang": "doeff_conductor._dsl",
    "oks": "doeff_conductor._dsl",
    "parallel": "doeff_conductor._dsl",
    "parallel_for": "doeff_conductor._dsl",
    "pipeline": "doeff_conductor._dsl",
    "prompt": "doeff_conductor._dsl",
    "random_bang": "doeff_conductor._dsl",
    "random_value": "doeff_conductor._dsl",
    "raw_node": "doeff_conductor._dsl",
    "ref": "doeff_conductor._dsl",
    "time": "doeff_conductor._dsl",
    "time_bang": "doeff_conductor._dsl",
    "workspace": "doeff_conductor._dsl",
    "workspace_bang": "doeff_conductor._dsl",
    "BudgetConsumption": "doeff_conductor._effects.review",
    "BudgetCounterEntry": "doeff_conductor._effects.review",
    "BudgetCounterKey": "doeff_conductor._effects.review",
    "CalibrationEscapeRecord": "doeff_conductor._effects.review",
    "CalibrationLaneRate": "doeff_conductor._effects.review",
    "CalibrationLedger": "doeff_conductor._effects.review",
    "CalibrationPolicy": "doeff_conductor._effects.review",
    "ClosureTerminal": "doeff_conductor._effects.review",
    "E": "doeff_conductor._effects.base",
    "GateOption": "doeff_conductor._effects.review",
    "ReviewBudgetStatus": "doeff_conductor._effects.review",
    "ReviewRouteRule": "doeff_conductor._effects.review",
    "ReviewTier": "doeff_conductor._effects.review",
    "Tier1ReviewResult": "doeff_conductor._effects.review",
    "Tier2Callback": "doeff_conductor._effects.review",
    "Tier2ReviewRequest": "doeff_conductor._effects.review",
    "PreCoverageRunError": "doeff_conductor._handlers.journaled_workspace",
    "WorkspaceStateError": "doeff_conductor._handlers.workspace_handler",
}


def __getattr__(name: str) -> object:
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_LAZY_EXPORTS})
