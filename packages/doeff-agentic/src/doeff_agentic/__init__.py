"""
doeff-agentic: Agent-based workflow orchestration.

This package provides a unified system for orchestrating multi-agent workflows
with proper environment isolation and session management.

Architecture:
    ┌─────────────────────────────────────────────────────────────────┐
    │                    Workflow Layer                                │
    │  - Orchestrates multiple agent sessions                          │
    │  - doeff-flow integration (Checkpoint, Slog)                     │
    │  - Workflow metadata, status, history                            │
    └─────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
    ┌─────────────────────────────────────────────────────────────────┐
    │                    Environment Layer                             │
    │  - Manages working directories / git worktrees                   │
    │  - Handles state inheritance between agents                      │
    │  - Types: worktree, inherited, copy, shared                      │
    └─────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
    ┌─────────────────────────────────────────────────────────────────┐
    │                    Session Layer                                 │
    │  - OpenCode server API (primary)                                 │
    │  - tmux (legacy fallback)                                        │
    └─────────────────────────────────────────────────────────────────┘

Quick Start:
    from doeff import do
    from doeff_agentic import (
        AgenticCreateSession,
        AgenticSendMessage,
        AgenticEnvironmentType,
    )

    @do
    def my_workflow():
        session = yield AgenticCreateSession(
            name="reviewer",
            title="Code Reviewer",
        )
        yield AgenticSendMessage(
            session_id=session.id,
            content="Review the changes in this PR",
            wait=True,
        )
        return "Done"

CLI Usage:
    $ doeff-agentic ps                           # List workflows
    $ doeff-agentic watch <id>                   # Monitor workflow
    $ doeff-agentic attach <id> --agent <name>   # Attach to session
    $ doeff-agentic logs <id>                    # View logs
    $ doeff-agentic stop <id>                    # Stop workflow
"""

# Effects - new spec-compliant effects
from ._effects import (
    AgenticAbortSession,
    # Recorded workflow effects (the monitor's)
    AgenticAnswerInput,
    AgenticAttachAgent,
    AgenticCaptureAgentOutput,
    AgenticCleanupEnvironments,
    # Environment effects
    AgenticCreateEnvironment,
    # Session effects
    AgenticCreateSession,
    # Workflow effects
    AgenticCreateWorkflow,
    AgenticDeleteEnvironment,
    AgenticDeleteSession,
    # Effect base
    AgenticEffectBase,
    AgenticFindWorkflow,
    AgenticForkSession,
    AgenticGetEnvironment,
    AgenticGetMessages,
    AgenticGetSession,
    # Status effects
    AgenticGetSessionStatus,
    AgenticGetWorkflow,
    AgenticInputRequested,
    AgenticListEnvironments,
    AgenticListWorkflows,
    # Event effects
    AgenticNextEvent,
    # Message effects
    AgenticSendMessage,
    AgenticSendToAgent,
    AgenticStopWorkflow,
    AgenticSupportsCapability,
    AgentNotRunningError,
    AmbiguousPrefixError,
    CaptureOutput,
    CaptureOutputEffect,
    # Legacy constructors (deprecated)
    RunAgent,
    # Legacy effects (deprecated)
    RunAgentEffect,
    SendMessage,
    SendMessageEffect,
    StopAgent,
    StopAgentEffect,
    UserInputTimeoutError,
    WaitForStatus,
    WaitForStatusEffect,
    WaitForUserInput,
    WaitForUserInputEffect,
    # Legacy error aliases
    WorkflowNotFoundError,
)

# Exceptions
from ._exceptions import (
    AgenticAmbiguousPrefixError,
    AgenticDuplicateNameError,
    AgenticEnvironmentInUseError,
    AgenticEnvironmentNotFoundError,
    AgenticError,
    AgenticServerError,
    AgenticSessionNotFoundError,
    AgenticSessionNotRunningError,
    AgenticTimeoutError,
    AgenticUnsupportedOperationError,
    AgenticWorkflowNotFoundError,
)
from ._handlers import (
    mock_handlers,
    production_handlers,
    recorded_workflows_handler,
)

# OpenCode Handler (new - primary)
from ._opencode_handler import opencode_handler

# Tmux Handler (legacy fallback)
from ._tmux_handler import tmux_handler
from ._types import (
    # Legacy types (deprecated)
    AgentConfig,
    AgenticEndOfEvents,
    AgenticEnvironmentHandle,
    # Enums
    AgenticEnvironmentType,
    # Events
    AgenticEvent,
    AgenticMessage,
    AgenticMessageHandle,
    AgenticSessionHandle,
    AgenticSessionStatus,
    # Handles
    AgenticWorkflowHandle,
    AgenticWorkflowStatus,
    AgentInfo,
    AgentStatus,
    WatchEventType,
    WatchUpdate,
    WorkflowInfo,
    WorkflowStatus,
)

# Visual Interceptor (for examples/debugging)
from ._visual_interceptor import VisualInterceptorConfig

# Legacy Handler (deprecated - requires doeff-agents)
try:
    from ._handler import agentic_effectful_handlers
except ImportError:
    # doeff-agents not installed, handlers not available
    agentic_effectful_handlers = None  # type: ignore

__all__ = [
    # Types - legacy
    "AgentConfig",
    "AgentInfo",
    "AgentNotRunningError",
    "AgentStatus",
    "AgenticAbortSession",
    "AgenticAmbiguousPrefixError",
    "AgenticAnswerInput",
    "AgenticAttachAgent",
    "AgenticCaptureAgentOutput",
    "AgenticCleanupEnvironments",
    "AgenticCreateEnvironment",
    "AgenticCreateSession",
    "AgenticCreateWorkflow",
    "AgenticDeleteEnvironment",
    "AgenticDeleteSession",
    "AgenticDuplicateNameError",
    # Effects - new
    "AgenticEffectBase",
    "AgenticEndOfEvents",
    "AgenticEnvironmentHandle",
    "AgenticEnvironmentInUseError",
    "AgenticEnvironmentNotFoundError",
    # Types - new
    "AgenticEnvironmentType",
    # Exceptions
    "AgenticError",
    "AgenticEvent",
    "AgenticFindWorkflow",
    "AgenticForkSession",
    "AgenticGetEnvironment",
    "AgenticGetMessages",
    "AgenticGetSession",
    "AgenticGetSessionStatus",
    "AgenticGetWorkflow",
    "AgenticInputRequested",
    "AgenticListEnvironments",
    "AgenticListWorkflows",
    "AgenticMessage",
    "AgenticMessageHandle",
    "AgenticNextEvent",
    "AgenticSendMessage",
    "AgenticSendToAgent",
    "AgenticServerError",
    "AgenticSessionHandle",
    "AgenticSessionNotFoundError",
    "AgenticSessionNotRunningError",
    "AgenticSessionStatus",
    "AgenticStopWorkflow",
    "AgenticSupportsCapability",
    "AgenticTimeoutError",
    "AgenticUnsupportedOperationError",
    "AgenticWorkflowHandle",
    "AgenticWorkflowNotFoundError",
    "AgenticWorkflowStatus",
    "AmbiguousPrefixError",
    "CaptureOutput",
    "CaptureOutputEffect",
    "RunAgent",
    # Effects - legacy
    "RunAgentEffect",
    "SendMessage",
    "SendMessageEffect",
    "StopAgent",
    "StopAgentEffect",
    "UserInputTimeoutError",
    "VisualInterceptorConfig",
    "WaitForStatus",
    "WaitForStatusEffect",
    "WaitForUserInput",
    "WaitForUserInputEffect",
    "WatchEventType",
    "WatchUpdate",
    "WorkflowInfo",
    # Errors - legacy aliases
    "WorkflowNotFoundError",
    "WorkflowStatus",
    "agentic_effectful_handlers",
    "mock_handlers",
    "opencode_handler",
    "production_handlers",
    "recorded_workflows_handler",
    "tmux_handler",
]

