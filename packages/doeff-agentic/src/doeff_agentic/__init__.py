"""
doeff-agentic: Agent-based workflow orchestration.

This package combines doeff-flow (durable execution) and doeff-agents
(session management) to orchestrate multi-agent workflows.

Key Features:
- Effect-based agent invocation (RunAgent, SendMessage, etc.)
- State file management for CLI/plugin consumers
- Real-time workflow observability
- Human-in-the-loop workflow support

Quick Start:
    from doeff import do
    from doeff_agentic import RunAgent, AgentConfig
    from doeff_flow import run_workflow

    @do
    def my_workflow():
        result = yield RunAgent(
            config=AgentConfig(
                agent_type="claude",
                prompt="Hello, world!",
            ),
        )
        return result

    run_workflow(my_workflow(), workflow_id="hello")

API Usage:
    from doeff_agentic.api import AgenticAPI

    api = AgenticAPI()
    workflows = api.list_workflows()
    api.attach("a3f")

CLI Usage:
    $ doeff-agentic ps                    # List workflows
    $ doeff-agentic watch <id>            # Monitor workflow
    $ doeff-agentic attach <id>           # Attach to agent
    $ doeff-agentic send <id> "message"   # Send message
    $ doeff-agentic stop <id>             # Stop workflow
    $ doeff-agentic tui                   # Interactive TUI
    $ doeff-agentic-tui                   # Alternative TUI entry point
"""

from .types import (
    AgentConfig,
    AgentInfo,
    AgentStatus,
    WatchEventType,
    WatchUpdate,
    WorkflowInfo,
    WorkflowStatus,
)

from .effects import (
    # Effect types
    RunAgentEffect,
    SendMessageEffect,
    WaitForStatusEffect,
    CaptureOutputEffect,
    WaitForUserInputEffect,
    StopAgentEffect,
    # Constructors
    RunAgent,
    SendMessage,
    WaitForStatus,
    CaptureOutput,
    WaitForUserInput,
    StopAgent,
    # Errors
    AgenticError,
    WorkflowNotFoundError,
    AgentNotRunningError,
    UserInputTimeoutError,
    AmbiguousPrefixError,
)

from .handler import (
    AgenticHandler,
    agent_handler,
    agentic_effectful_handlers,
)

from .state import (
    StateManager,
    generate_workflow_id,
    get_default_state_dir,
)

from .api import AgenticAPI

__all__ = [
    # Types
    "AgentConfig",
    "AgentInfo",
    "AgentStatus",
    "WatchEventType",
    "WatchUpdate",
    "WorkflowInfo",
    "WorkflowStatus",
    # Effects
    "RunAgentEffect",
    "SendMessageEffect",
    "WaitForStatusEffect",
    "CaptureOutputEffect",
    "WaitForUserInputEffect",
    "StopAgentEffect",
    # Effect Constructors
    "RunAgent",
    "SendMessage",
    "WaitForStatus",
    "CaptureOutput",
    "WaitForUserInput",
    "StopAgent",
    # Errors
    "AgenticError",
    "WorkflowNotFoundError",
    "AgentNotRunningError",
    "UserInputTimeoutError",
    "AmbiguousPrefixError",
    # Handler
    "AgenticHandler",
    "agent_handler",
    "agentic_effectful_handlers",
    # State
    "StateManager",
    "generate_workflow_id",
    "get_default_state_dir",
    # API
    "AgenticAPI",
]
