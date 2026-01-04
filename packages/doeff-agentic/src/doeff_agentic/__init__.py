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
from .types import (
    # Enums
    AgenticEnvironmentType,
    AgenticSessionStatus,
    AgenticWorkflowStatus,
    # Handles
    AgenticWorkflowHandle,
    AgenticEnvironmentHandle,
    AgenticSessionHandle,
    AgenticMessageHandle,
    AgenticMessage,
    # Events
    AgenticEvent,
    AgenticEndOfEvents,
    # Legacy types (deprecated)
    AgentConfig,
    AgentInfo,
    AgentStatus,
    WatchEventType,
    WatchUpdate,
    WorkflowInfo,
    WorkflowStatus,
)

# Exceptions
from .exceptions import (
    AgenticError,
    AgenticSessionNotFoundError,
    AgenticEnvironmentNotFoundError,
    AgenticWorkflowNotFoundError,
    AgenticSessionNotRunningError,
    AgenticEnvironmentInUseError,
    AgenticUnsupportedOperationError,
    AgenticServerError,
    AgenticTimeoutError,
    AgenticDuplicateNameError,
    AgenticAmbiguousPrefixError,
)

# Effects - new spec-compliant effects
from .effects import (
    # Effect base
    AgenticEffectBase,
    # Workflow effects
    AgenticCreateWorkflow,
    AgenticGetWorkflow,
    # Environment effects
    AgenticCreateEnvironment,
    AgenticGetEnvironment,
    AgenticDeleteEnvironment,
    # Session effects
    AgenticCreateSession,
    AgenticForkSession,
    AgenticGetSession,
    AgenticAbortSession,
    AgenticDeleteSession,
    # Message effects
    AgenticSendMessage,
    AgenticGetMessages,
    # Event effects
    AgenticNextEvent,
    # Parallel effects
    AgenticGather,
    AgenticRace,
    # Status effects
    AgenticGetSessionStatus,
    AgenticSupportsCapability,
    # Legacy effects (deprecated)
    RunAgentEffect,
    SendMessageEffect,
    WaitForStatusEffect,
    CaptureOutputEffect,
    WaitForUserInputEffect,
    StopAgentEffect,
    # Legacy constructors (deprecated)
    RunAgent,
    SendMessage,
    WaitForStatus,
    CaptureOutput,
    WaitForUserInput,
    StopAgent,
    # Legacy error aliases
    WorkflowNotFoundError,
    AgentNotRunningError,
    UserInputTimeoutError,
    AmbiguousPrefixError,
)

# State management
from .state import (
    StateManager,
    generate_workflow_id,
    get_default_state_dir,
)

# Event logging
from .event_log import (
    EventLogWriter,
    EventLogReader,
    WorkflowState,
    LogEvent,
    WorkflowEventType,
    SessionEventType,
    MessageEventType,
    EnvironmentEventType,
    get_default_event_log_dir,
)

# API
from .api import AgenticAPI

# OpenCode Handler (new - primary)
from .opencode_handler import (
    OpenCodeHandler,
    opencode_handler,
)

# Tmux Handler (legacy fallback)
from .tmux_handler import (
    TmuxHandler,
    tmux_handler,
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
    # Types - new
    "AgenticEnvironmentType",
    "AgenticSessionStatus",
    "AgenticWorkflowStatus",
    "AgenticWorkflowHandle",
    "AgenticEnvironmentHandle",
    "AgenticSessionHandle",
    "AgenticMessageHandle",
    "AgenticMessage",
    "AgenticEvent",
    "AgenticEndOfEvents",
    # Types - legacy
    "AgentConfig",
    "AgentInfo",
    "AgentStatus",
    "WatchEventType",
    "WatchUpdate",
    "WorkflowInfo",
    "WorkflowStatus",
    # Exceptions
    "AgenticError",
    "AgenticSessionNotFoundError",
    "AgenticEnvironmentNotFoundError",
    "AgenticWorkflowNotFoundError",
    "AgenticSessionNotRunningError",
    "AgenticEnvironmentInUseError",
    "AgenticUnsupportedOperationError",
    "AgenticServerError",
    "AgenticTimeoutError",
    "AgenticDuplicateNameError",
    "AgenticAmbiguousPrefixError",
    # Effects - new
    "AgenticEffectBase",
    "AgenticCreateWorkflow",
    "AgenticGetWorkflow",
    "AgenticCreateEnvironment",
    "AgenticGetEnvironment",
    "AgenticDeleteEnvironment",
    "AgenticCreateSession",
    "AgenticForkSession",
    "AgenticGetSession",
    "AgenticAbortSession",
    "AgenticDeleteSession",
    "AgenticSendMessage",
    "AgenticGetMessages",
    "AgenticNextEvent",
    "AgenticGather",
    "AgenticRace",
    "AgenticGetSessionStatus",
    "AgenticSupportsCapability",
    # Effects - legacy
    "RunAgentEffect",
    "SendMessageEffect",
    "WaitForStatusEffect",
    "CaptureOutputEffect",
    "WaitForUserInputEffect",
    "StopAgentEffect",
    "RunAgent",
    "SendMessage",
    "WaitForStatus",
    "CaptureOutput",
    "WaitForUserInput",
    "StopAgent",
    # Errors - legacy aliases
    "WorkflowNotFoundError",
    "AgentNotRunningError",
    "UserInputTimeoutError",
    "AmbiguousPrefixError",
    # State
    "StateManager",
    "generate_workflow_id",
    "get_default_state_dir",
    # Event logging
    "EventLogWriter",
    "EventLogReader",
    "WorkflowState",
    "LogEvent",
    "WorkflowEventType",
    "SessionEventType",
    "MessageEventType",
    "EnvironmentEventType",
    "get_default_event_log_dir",
    # API
    "AgenticAPI",
    # OpenCode Handler (new - primary)
    "OpenCodeHandler",
    "opencode_handler",
    # Tmux Handler (legacy fallback)
    "TmuxHandler",
    "tmux_handler",
    # Handler (deprecated - requires doeff-agents)
    "AgenticHandler",
    "agent_handler",
    "agentic_effectful_handlers",
]
