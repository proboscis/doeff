"""
doeff-conductor: Multi-agent workflow orchestration.

This package provides a unified orchestration layer for multi-agent workflows:

- Issue-driven agent workflows
- Git worktree management
- Multi-agent DAG execution
- Full CLI for monitoring and control

Architecture:
    +---------------------------------------------------------------------+
    |                         doeff-conductor                              |
    +---------------------------------------------------------------------+
    |  CLI Layer                                                           |
    |  - run, ps, show, watch, attach, stop, logs                         |
    |  - issue create/list/show/resolve                                   |
    |  - env list/cleanup                                                 |
    |  - template list/show/run                                           |
    +---------------------------------------------------------------------+
    |  Effects                                                             |
    |  +----------+ +----------+ +----------+ +----------+                |
    |  | Worktree | |  Issue   | |  Agent   | |   Git    |                |
    |  | Create   | | Create   | | Agent   | | Commit   |                |
    |  | Merge    | | List     | | Task    | | Push     |                |
    |  | Delete   | | Resolve  | | Artifact| | CreatePR |                |
    |  +----------+ +----------+ +----------+ +----------+                |
    +---------------------------------------------------------------------+
    |  Dependencies                                                        |
    |  - doeff-agentic (session management, agent adapters)               |
    |  - doeff-flow (trace observability)                                 |
    |  - doeff (core effects, @do, run_sync)                              |
    +---------------------------------------------------------------------+

Quick Start:
    from doeff import do
    from doeff_conductor import Agent, AgentTask, CreateWorktree, CreatePR

    @do
    def basic_pr(issue):
        env = yield CreateWorktree(issue=issue)
        yield Agent(AgentTask(
            run_id=issue.id,
            node_id="implement",
            attempt=0,
            env=env,
            prompt=issue.body,
            result_schema={"type": "object"},
            verification_class="test-verifiable",
            agent_type="codex",
        ))
        pr = yield CreatePR(env=env, title=issue.title)
        return pr

CLI Usage:
    $ conductor run basic_pr --issue ISSUE-001.md
    $ conductor ps
    $ conductor watch <workflow-id>
    $ conductor issue create "Add feature"
"""

# Types
# API
from .api import ConductorAPI

# Effects
from .effects import (
    BLOCKER_FINDING,
    CALIBRATION_SAMPLE_BUDGET_KEY,
    DEFAULT_REVIEW_ROUTE_TABLE,
    REVIEW_VERDICT_RESULT_SCHEMA,
    TIER1_REVIEW_BUDGET_KEY,
    TIER2_ESCALATION_BUDGET_KEY,
    Agent,
    AgentAttemptExhaustedError,
    AgentCall,
    AgentEffect,
    AgentTask,
    AgentValidationErrorKind,
    AgentValidationFailure,
    # Git
    Commit,
    # Base
    ConductorEffectBase,
    # Issue
    CreateIssue,
    CreatePR,
    # Worktree
    CreateWorktree,
    DefaultReviewRouter,
    DeleteWorktree,
    DurableReviewBudget,
    GateCall,
    GetIssue,
    ListIssues,
    MergeBranches,
    MergeCall,
    MergePR,
    OpenGate,
    OpenGateReason,
    Push,
    RandomCall,
    RemainingReviewBudget,
    ResolveIssue,
    ReviewerAgentLost,
    ReviewEscalationReason,
    ReviewEscalationTerminal,
    ReviewFinding,
    ReviewItem,
    ReviewRoutingResult,
    ReviewSeverity,
    ReviewStakes,
    ReviewStakesLevel,
    ReviewVerdict,
    ReviewVerdictArtifact,
    ReviewVerdictTerminal,
    TimeCall,
    WorkspaceCall,
    route_review_item,
    run_review_routing_demo,
)

# Exceptions
from .exceptions import (
    AgentError,
    AgentTimeoutError,
    ConductorError,
    GitCommandError,
    IssueAlreadyExistsError,
    IssueNotFoundError,
    PRError,
    WorktreeError,
)

# Handlers
from .handlers import (
    AgentHandler,
    GitHandler,
    IssueHandler,
    MockConductorRuntime,
    WorktreeHandler,
    default_scheduled_handlers,
    make_async_scheduled_handler,
    make_blocking_scheduled_handler,
    make_blocking_scheduled_handler_with_store,
    # Handler utilities
    make_scheduled_handler,
    make_scheduled_handler_with_store,
    mock_handlers,
    production_handlers,
)
from .replay_keying import (
    ResolvedIdentity,
    agent_cache_key,
    longest_valid_prefix,
    node_identity_fingerprint,
    resolved_identity_fingerprint,
)

# Templates
from .templates import (
    basic_pr,
    enforced_pr,
    get_available_templates,
    get_template,
    get_template_source,
    is_template,
    multi_agent,
    reviewed_pr,
)
from .types import (
    # Agent types
    AgentRef,
    # Issue types
    Issue,
    # Enums
    IssueStatus,
    MergeStrategy,
    # Git types
    PRHandle,
    # Workflow types
    WorkflowHandle,
    WorkflowStatus,
    # Environment types
    WorktreeEnv,
)

__all__ = [
    "BLOCKER_FINDING",
    "CALIBRATION_SAMPLE_BUDGET_KEY",
    "DEFAULT_REVIEW_ROUTE_TABLE",
    "REVIEW_VERDICT_RESULT_SCHEMA",
    "TIER1_REVIEW_BUDGET_KEY",
    "TIER2_ESCALATION_BUDGET_KEY",
    "Agent",
    "AgentAttemptExhaustedError",
    "AgentCall",
    "AgentEffect",
    "AgentError",
    "AgentHandler",
    "AgentRef",
    "AgentTask",
    "AgentTimeoutError",
    "AgentValidationErrorKind",
    "AgentValidationFailure",
    # Effects - Git
    "Commit",
    # API
    "ConductorAPI",
    # Effects - Base
    "ConductorEffectBase",
    # Exceptions
    "ConductorError",
    # Effects - Issue
    "CreateIssue",
    "CreatePR",
    # Effects - Worktree
    "CreateWorktree",
    "DefaultReviewRouter",
    "DeleteWorktree",
    "DurableReviewBudget",
    "GateCall",
    "GetIssue",
    "GitCommandError",
    "GitHandler",
    # Types - Data classes
    "Issue",
    "IssueAlreadyExistsError",
    "IssueHandler",
    "IssueNotFoundError",
    # Types - Enums
    "IssueStatus",
    "ListIssues",
    "MergeBranches",
    "MergeCall",
    "MergePR",
    "MergeStrategy",
    "MockConductorRuntime",
    "OpenGate",
    "OpenGateReason",
    "PRError",
    "PRHandle",
    "Push",
    "RandomCall",
    "RemainingReviewBudget",
    "ResolveIssue",
    "ResolvedIdentity",
    "ReviewEscalationReason",
    "ReviewEscalationTerminal",
    "ReviewFinding",
    "ReviewItem",
    "ReviewRoutingResult",
    "ReviewSeverity",
    "ReviewStakes",
    "ReviewStakesLevel",
    "ReviewVerdict",
    "ReviewVerdictArtifact",
    "ReviewVerdictTerminal",
    "ReviewerAgentLost",
    "TimeCall",
    "WorkflowHandle",
    "WorkflowStatus",
    "WorkspaceCall",
    "WorktreeEnv",
    "WorktreeError",
    # Handlers
    "WorktreeHandler",
    "agent_cache_key",
    # Templates
    "basic_pr",
    "default_scheduled_handlers",
    "enforced_pr",
    "get_available_templates",
    "get_template",
    "get_template_source",
    "is_template",
    "longest_valid_prefix",
    "make_async_scheduled_handler",
    "make_blocking_scheduled_handler",
    "make_blocking_scheduled_handler_with_store",
    "make_scheduled_handler",
    "make_scheduled_handler_with_store",
    # Handler utilities
    "mock_handlers",
    "multi_agent",
    "node_identity_fingerprint",
    "production_handlers",
    "resolved_identity_fingerprint",
    "reviewed_pr",
    "route_review_item",
    "run_review_routing_demo",
]
