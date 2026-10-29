"""The agentic effects in three groups, so each handler answers them in clauses of a readable size.

A handler matches an effect to its group first (one ``case`` per group, an or-pattern of the
group's effects), then the group's function matches the single effect. The aliases type that
function's argument, so the type checker sees when a clause is missing.
"""

from doeff_agentic._effects import (
    AgenticAbortSession,
    AgenticCreateEnvironment,
    AgenticCreateSession,
    AgenticCreateWorkflow,
    AgenticDeleteEnvironment,
    AgenticDeleteSession,
    AgenticForkSession,
    AgenticGetEnvironment,
    AgenticGetMessages,
    AgenticGetSession,
    AgenticGetSessionStatus,
    AgenticGetWorkflow,
    AgenticNextEvent,
    AgenticSendMessage,
    AgenticSupportsCapability,
)

#: Workflow and environment effects.
WorkflowEffect = (
    AgenticCreateWorkflow
    | AgenticGetWorkflow
    | AgenticCreateEnvironment
    | AgenticGetEnvironment
    | AgenticDeleteEnvironment
)
#: Session lifecycle effects.
SessionEffect = (
    AgenticCreateSession
    | AgenticForkSession
    | AgenticGetSession
    | AgenticAbortSession
    | AgenticDeleteSession
)
#: Message, event and status effects.
MessageEffect = (
    AgenticSendMessage
    | AgenticGetMessages
    | AgenticNextEvent
    | AgenticGetSessionStatus
    | AgenticSupportsCapability
)
