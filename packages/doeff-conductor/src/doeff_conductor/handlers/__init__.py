"""
Handlers for doeff-conductor effects.

Each handler implements the logic for a category of effects:
- WorktreeHandler: Git worktree operations
- IssueHandler: Issue vault operations
- AgentHandler: Agent session management
- GitHandler: Git operations

Handler utilities:
- make_scheduled_handler: Wrap sync handlers for scheduled_handlers API
- make_async_scheduled_handler: Wrap async handlers
- make_blocking_scheduled_handler: Wrap blocking handlers (runs in thread)
"""

from .worktree_handler import WorktreeHandler
from .issue_handler import IssueHandler
from .agent_handler import AgentHandler
from .git_handler import GitHandler
from .utils import (
    make_scheduled_handler,
    make_scheduled_handler_with_store,
    make_async_scheduled_handler,
    make_blocking_scheduled_handler,
    make_blocking_scheduled_handler_with_store,
    default_scheduled_handlers,
)

__all__ = [
    # Handlers
    "WorktreeHandler",
    "IssueHandler",
    "AgentHandler",
    "GitHandler",
    # Utilities
    "make_scheduled_handler",
    "make_scheduled_handler_with_store",
    "make_async_scheduled_handler",
    "make_blocking_scheduled_handler",
    "make_blocking_scheduled_handler_with_store",
    "default_scheduled_handlers",
]
