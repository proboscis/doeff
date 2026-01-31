"""Synchronous runtime with pure handler-based cooperative scheduling.

This runtime implements cooperative scheduling through handlers:
- queue_handler: Manages task queue using store primitives (outermost)
- scheduler_handler: Handles Spawn/Wait/Gather/Race/Promise effects
- core_handler: Handles basic effects (Get, Put, Ask, etc.)

All task scheduling is done by handlers using ResumeK - no task tracking
in the runtime. The runtime just steps until Done/Failed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import uuid4

from doeff._vendor import FrozenDict
from doeff.cesk.errors import UnhandledEffectError
from doeff.cesk.frames import ReturnFrame
from doeff.cesk.handler_frame import Handler, WithHandler
from doeff.cesk.handlers.core_handler import core_handler
from doeff.cesk.handlers.queue_handler import (
    CURRENT_TASK_KEY,
    TASK_QUEUE_KEY,
    TASK_REGISTRY_KEY,
    WAITERS_KEY,
    queue_handler,
)
from doeff.cesk.handlers.scheduler_handler import scheduler_handler
from doeff.cesk.helpers import to_generator
from doeff.cesk.result import Done, Failed
from doeff.cesk.runtime.base import BaseRuntime, ExecutionError
from doeff.cesk.runtime_result import RuntimeResult
from doeff.cesk.step import step
from doeff.cesk.state import CESKState, ProgramControl
from doeff.program import Program

if TYPE_CHECKING:
    pass

T = TypeVar("T")


def _wrap_with_handlers(program: Program[T]) -> Program[T]:
    """Wrap a program with the handler stack for cooperative scheduling."""
    return WithHandler(
        handler=cast(Handler, queue_handler),
        program=WithHandler(
            handler=cast(Handler, scheduler_handler),
            program=WithHandler(
                handler=cast(Handler, core_handler),
                program=program,
            ),
        ),
    )


class SyncRuntime(BaseRuntime):
    """Synchronous runtime with pure handler-based cooperative scheduling."""

    def __init__(self, handlers: dict[type, Any] | None = None):
        super().__init__(handlers or {})
        self._user_handlers = handlers or {}

    def run(
        self,
        program: Program[T],
        env: dict[str, Any] | None = None,
        store: dict[str, Any] | None = None,
    ) -> RuntimeResult[T]:
        frozen_env = FrozenDict(env) if env else FrozenDict()
        final_store: dict[str, Any] = dict(store) if store else {}
        
        main_task_id = uuid4()
        final_store[CURRENT_TASK_KEY] = main_task_id
        final_store[TASK_QUEUE_KEY] = []
        final_store[TASK_REGISTRY_KEY] = {}
        final_store[WAITERS_KEY] = {}
        
        wrapped_program = _wrap_with_handlers(program)
        
        state = CESKState(
            C=ProgramControl(wrapped_program),
            E=frozen_env,
            S=final_store,
            K=[],
        )
        
        try:
            value, final_state = self._run_until_done(state)
            return self._build_success_result(value, final_state, final_state.S)
        except ExecutionError as err:
            if isinstance(err.exception, (KeyboardInterrupt, SystemExit, UnhandledEffectError)):
                raise err.exception from None
            return self._build_error_result(
                err.exception,
                err.final_state,
                captured_traceback=err.captured_traceback,
            )

    def run_and_unwrap(
        self,
        program: Program[T],
        env: dict[str, Any] | None = None,
        store: dict[str, Any] | None = None,
    ) -> T:
        result = self.run(program, env, store)
        return result.value

    def _run_until_done(self, state: CESKState) -> tuple[Any, CESKState]:
        """Step until Done or Failed. Handlers manage all task scheduling."""
        max_steps = 100000
        for _ in range(max_steps):
            result = step(state)
            
            if isinstance(result, Done):
                return (result.value, state)
            
            if isinstance(result, Failed):
                raise ExecutionError(
                    exception=result.exception,
                    final_state=state,
                    captured_traceback=result.captured_traceback,
                )
            
            if isinstance(result, CESKState):
                state = result
                continue
            
            raise RuntimeError(f"Unexpected step result: {type(result)}")
        
        raise ExecutionError(
            exception=RuntimeError(f"Exceeded maximum steps ({max_steps})"),
            final_state=state,
        )


__all__ = [
    "SyncRuntime",
]
