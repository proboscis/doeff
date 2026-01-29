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
    $ doeff-agentic attach <id>:<session>        # Attach to session
    $ doeff-agentic logs <id>                    # View logs
    $ doeff-agentic stop <id>                    # Stop workflow
"""

# Types - new spec-compliant types
# API
from .api import AgenticAPI

# Effects - new spec-compliant effects
from .effects import (
    AgenticAbortSession,
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
    AgenticForkSession,
    AgenticGetEnvironment,
    AgenticGetMessages,
    AgenticGetSession,
    # Status effects
    AgenticGetSessionStatus,
    AgenticGetWorkflow,
    # Event effects
    AgenticNextEvent,
    # Message effects
    AgenticSendMessage,
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

# Event logging (new - JSONL event logs)
from .event_log import (
    EventLogReader,
    EventLogWriter,
    WorkflowIndex,
)

# Exceptions
from .exceptions import (
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

# OpenCode Handler (new - primary)
from .opencode_handler import (
    OpenCodeHandler,
    opencode_handler,
)

# State management
from .state import (
    StateManager,
    generate_workflow_id,
    get_default_state_dir,
)

# Tmux Handler (legacy fallback)
from .tmux_handler import (
    TmuxHandler,
    tmux_handler,
)
from .types import (
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

# Legacy Handler (deprecated - requires doeff-agents)
try:
    from .handler import (
        AgenticHandler,
        agent_handler,
        agentic_effectful_handlers,
    )
except ImportError:
    # doeff-agents not installed, handlers not available
    AgenticHandler = None  # type: ignore
    agent_handler = None  # type: ignore
    agentic_effectful_handlers = None  # type: ignore

__all__ = [
    # Types - legacy
    "AgentConfig",
    "AgentInfo",
    "AgentNotRunningError",
    "AgentStatus",
    # API
    "AgenticAPI",
    "AgenticAbortSession",
    "AgenticAmbiguousPrefixError",
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
    "AgenticForkSession",
    "AgenticGetEnvironment",
    "AgenticGetMessages",
    "AgenticGetSession",
    "AgenticGetSessionStatus",
    "AgenticGetWorkflow",
    # Handler (deprecated - requires doeff-agents)
    "AgenticHandler",
    "AgenticMessage",
    "AgenticMessageHandle",
    "AgenticNextEvent",
    "AgenticSendMessage",
    "AgenticServerError",
    "AgenticSessionHandle",
    "AgenticSessionNotFoundError",
    "AgenticSessionNotRunningError",
    "AgenticSessionStatus",
    "AgenticSupportsCapability",
    "AgenticTimeoutError",
    "AgenticUnsupportedOperationError",
    "AgenticWorkflowHandle",
    "AgenticWorkflowNotFoundError",
    "AgenticWorkflowStatus",
    "AmbiguousPrefixError",
    "CaptureOutput",
    "CaptureOutputEffect",
    "EventLogReader",
    # Event logging
    "EventLogWriter",
    # OpenCode Handler (new - primary)
    "OpenCodeHandler",
    "RunAgent",
    # Effects - legacy
    "RunAgentEffect",
    "SendMessage",
    "SendMessageEffect",
    # State
    "StateManager",
    "StopAgent",
    "StopAgentEffect",
    # Tmux Handler (legacy fallback)
    "TmuxHandler",
    "UserInputTimeoutError",
    "WaitForStatus",
    "WaitForStatusEffect",
    "WaitForUserInput",
    "WaitForUserInputEffect",
    "WatchEventType",
    "WatchUpdate",
    "WorkflowIndex",
    "WorkflowInfo",
    # Errors - legacy aliases
    "WorkflowNotFoundError",
    "WorkflowStatus",
    "agent_handler",
    "agentic_effectful_handlers",
    "generate_workflow_id",
    "get_default_state_dir",
    "opencode_handler",
    "tmux_handler",
]
