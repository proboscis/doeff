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
    |  | Create   | | Create   | | RunAgent | | Commit   |                |
    |  | Merge    | | List     | | Send     | | Push     |                |
    |  | Delete   | | Resolve  | | Capture  | | CreatePR |                |
    |  +----------+ +----------+ +----------+ +----------+                |
    +---------------------------------------------------------------------+
    |  Dependencies                                                        |
    |  - doeff-agentic (session management, agent adapters)               |
    |  - doeff-flow (trace observability)                                 |
    |  - doeff (core effects, @do, run_sync)                              |
    +---------------------------------------------------------------------+

Quick Start:
    from doeff import do
    from doeff_conductor import CreateWorktree, RunAgent, CreatePR

    @do
    def basic_pr(issue):
        env = yield CreateWorktree(issue=issue)
        yield RunAgent(env=env, prompt=issue.body)
        pr = yield CreatePR(env=env, title=issue.title)
        return pr

CLI Usage:
    $ conductor run basic_pr --issue ISSUE-001.md
    $ conductor ps
    $ conductor watch <workflow-id>
    $ conductor issue create "Add feature"
"""

# Types
from .types import (
    # Enums
    IssueStatus,
    WorkflowStatus,
    MergeStrategy,
    # Issue types
    Issue,
    # Environment types
    WorktreeEnv,
    # Agent types
    AgentRef,
    # Git types
    PRHandle,
    # Workflow types
    WorkflowHandle,
)

# Effects
from .effects import (
    # Base
    ConductorEffectBase,
    # Worktree
    CreateWorktree,
    MergeBranches,
    DeleteWorktree,
    # Issue
    CreateIssue,
    ListIssues,
    GetIssue,
    ResolveIssue,
    # Agent
    RunAgent,
    SpawnAgent,
    SendMessage,
    WaitForStatus,
    CaptureOutput,
    # Git
    Commit,
    Push,
    CreatePR,
    MergePR,
)

# Handlers
from .handlers import (
    WorktreeHandler,
    IssueHandler,
    AgentHandler,
    GitHandler,
)

# API
from .api import ConductorAPI

# Templates
from .templates import (
    basic_pr,
    enforced_pr,
    reviewed_pr,
    multi_agent,
    is_template,
    get_template,
    get_available_templates,
    get_template_source,
)

__all__ = [
    # Types - Enums
    "IssueStatus",
    "WorkflowStatus",
    "MergeStrategy",
    # Types - Data classes
    "Issue",
    "WorktreeEnv",
    "AgentRef",
    "PRHandle",
    "WorkflowHandle",
    # Effects - Base
    "ConductorEffectBase",
    # Effects - Worktree
    "CreateWorktree",
    "MergeBranches",
    "DeleteWorktree",
    # Effects - Issue
    "CreateIssue",
    "ListIssues",
    "GetIssue",
    "ResolveIssue",
    # Effects - Agent
    "RunAgent",
    "SpawnAgent",
    "SendMessage",
    "WaitForStatus",
    "CaptureOutput",
    # Effects - Git
    "Commit",
    "Push",
    "CreatePR",
    "MergePR",
    # Handlers
    "WorktreeHandler",
    "IssueHandler",
    "AgentHandler",
    "GitHandler",
    # API
    "ConductorAPI",
    # Templates
    "basic_pr",
    "enforced_pr",
    "reviewed_pr",
    "multi_agent",
    "is_template",
    "get_template",
    "get_available_templates",
    "get_template_source",
]
