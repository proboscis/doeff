"""Handler functions for CESK effects.

This module provides:
- HandlerContext: Context passed to handler functions
- HandlerResult: Tuple of actions to execute
- Handler: Protocol for handler functions
- default_handlers(): Returns dict[type[Effect], Handler]

Handlers are pure functions, not decorated classes:

    def handle_ask(effect: AskEffect, ctx: HandlerContext) -> HandlerResult:
        value = ctx.env.get(effect.key)
        return HandlerResult((Resume(value),))

    def default_handlers() -> dict[type[Effect], Handler]:
        return {
            AskEffect: handle_ask,
            StateGetEffect: handle_get,
            ...
        }

No decorators. No global registry. Just functions and dicts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol, TypeAlias

from doeff._types_internal import EffectBase
from doeff.cesk.types import Environment, Store, TaskId
from doeff.cesk.actions import Action
from doeff.cesk.frames import Kontinuation


# ============================================================================
# Handler Context
# ============================================================================


@dataclass(frozen=True)
class HandlerContext:
    """Context passed to handler functions.

    Contains all information a handler needs to decide what action to take.
    Handlers should NOT modify any state directly - they return Actions.
    """

    task_id: TaskId
    env: Environment
    store: Store
    kontinuation: Kontinuation  # For effects that need to inspect the K stack

    def get_env(self, key: Any, default: Any = None) -> Any:
        """Get value from environment."""
        return self.env.get(key, default)

    def get_store(self, key: str, default: Any = None) -> Any:
        """Get value from store."""
        return self.store.get(key, default)


# ============================================================================
# Handler Result
# ============================================================================


@dataclass(frozen=True)
class HandlerResult:
    """Result from a handler function.

    Contains one or more actions to execute. Most handlers return a single
    action, but some (like ModifyStore + Resume) return multiple.
    """

    actions: tuple[Action, ...]

    @classmethod
    def resume(cls, value: Any) -> HandlerResult:
        """Convenience: resume with value."""
        from doeff.cesk.actions import Resume

        return cls((Resume(value),))

    @classmethod
    def resume_with_store(cls, value: Any, store: Store) -> HandlerResult:
        """Convenience: resume with value and updated store."""
        from doeff.cesk.actions import ResumeWithStore

        return cls((ResumeWithStore(value, store),))

    @classmethod
    def error(cls, ex: BaseException) -> HandlerResult:
        """Convenience: raise an error."""
        from doeff.cesk.actions import ResumeError

        return cls((ResumeError(ex),))

    @classmethod
    def run_program(cls, program: Any, env: Environment | None = None) -> HandlerResult:
        """Convenience: run a sub-program."""
        from doeff.cesk.actions import RunProgram

        return cls((RunProgram(program, env),))


# ============================================================================
# Handler Protocol
# ============================================================================


class Handler(Protocol):
    """Protocol for effect handler functions.

    Handlers are pure functions: (effect, ctx) -> HandlerResult
    They should not have side effects - all changes come through Actions.
    """

    def __call__(self, effect: EffectBase, ctx: HandlerContext) -> HandlerResult:
        ...


# Type alias for handler registry
HandlerRegistry: TypeAlias = dict[type[EffectBase], Handler]


# ============================================================================
# Default Handlers
# ============================================================================


def default_handlers() -> HandlerRegistry:
    """Return the default handler registry.

    This assembles handlers from the various handler modules into a
    single dict mapping effect types to handler functions.
    """
    from doeff.cesk.handlers.core import (
        handle_ask,
        handle_get,
        handle_modify,
        handle_pure,
        handle_put,
        handle_tell,
    )
    from doeff.cesk.handlers.control import (
        handle_gather,
        handle_intercept,
        handle_listen,
        handle_local,
        handle_safe,
    )
    from doeff.cesk.handlers.time import (
        handle_delay,
        handle_get_time,
        handle_wait_until,
    )
    from doeff.cesk.handlers.task import (
        handle_spawn,
        handle_task_join,
    )
    from doeff.cesk.handlers.io import (
        handle_await,
        handle_io,
        handle_cache_delete,
        handle_cache_exists,
        handle_cache_get,
        handle_cache_put,
    )

    from doeff.effects import (
        AskEffect,
        CacheDeleteEffect,
        CacheExistsEffect,
        CacheGetEffect,
        CachePutEffect,
        DelayEffect,
        FutureAwaitEffect,
        GatherEffect,
        GetTimeEffect,
        InterceptEffect,
        IOPerformEffect,
        LocalEffect,
        PureEffect,
        ResultSafeEffect,
        SpawnEffect,
        StateGetEffect,
        StateModifyEffect,
        StatePutEffect,
        TaskJoinEffect,
        WaitUntilEffect,
        WriterListenEffect,
        WriterTellEffect,
    )

    return {
        # Core effects
        AskEffect: handle_ask,
        StateGetEffect: handle_get,
        StatePutEffect: handle_put,
        StateModifyEffect: handle_modify,
        WriterTellEffect: handle_tell,
        PureEffect: handle_pure,
        # Control flow effects
        LocalEffect: handle_local,
        InterceptEffect: handle_intercept,
        WriterListenEffect: handle_listen,
        GatherEffect: handle_gather,
        ResultSafeEffect: handle_safe,
        # Time effects
        DelayEffect: handle_delay,
        WaitUntilEffect: handle_wait_until,
        GetTimeEffect: handle_get_time,
        # Task effects
        SpawnEffect: handle_spawn,
        TaskJoinEffect: handle_task_join,
        # I/O effects
        IOPerformEffect: handle_io,
        FutureAwaitEffect: handle_await,
        CacheGetEffect: handle_cache_get,
        CachePutEffect: handle_cache_put,
        CacheDeleteEffect: handle_cache_delete,
        CacheExistsEffect: handle_cache_exists,
    }


__all__ = [
    "HandlerContext",
    "HandlerResult",
    "Handler",
    "HandlerRegistry",
    "default_handlers",
]
