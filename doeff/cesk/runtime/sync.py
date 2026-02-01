"""Synchronous runtime with pure handler-based cooperative scheduling.

This runtime implements cooperative scheduling through handlers:
- scheduler_state_handler: Manages task queue using store primitives (outermost)
- task_scheduler_handler: Handles Spawn/Wait/Gather/Race/Promise effects
- sync_await_handler: Handles async effects via background thread
- core_handler: Handles basic effects (Get, Put, Ask, etc.)

All task scheduling is done by handlers using ResumeK - no task tracking
in the runtime. The runtime just steps until Done/Failed.

Per SPEC-CESK-EFFECT-BOUNDARIES.md: SyncRuntime NEVER sees PythonAsyncSyntaxEscape.
The sync_await_handler handles Await/Delay/WaitUntil directly by running them
in a background asyncio thread.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypeVar, cast

from doeff._vendor import FrozenDict
from doeff.cesk.errors import UnhandledEffectError
from doeff.cesk.handler_frame import Handler, WithHandler
from doeff.cesk.handlers.core_handler import core_handler
from doeff.cesk.handlers.scheduler_state_handler import scheduler_state_handler
from doeff.cesk.handlers.sync_await_handler import sync_await_handler
from doeff.cesk.handlers.task_scheduler_handler import task_scheduler_handler
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
    """Wrap a program with the handler stack for cooperative scheduling.

    Handler stack (outermost to innermost):
    - scheduler_state_handler: Manages task queue state in store
    - task_scheduler_handler: Handles Spawn/Wait/Gather/Race effects
    - sync_await_handler: Handles async effects via background thread (NO escape)
    - core_handler: Handles basic effects (Get, Put, Ask, etc.)
    """
    return WithHandler(
        handler=cast(Handler, scheduler_state_handler),
        program=WithHandler(
            handler=cast(Handler, task_scheduler_handler),
            program=WithHandler(
                handler=cast(Handler, sync_await_handler),
                program=WithHandler(
                    handler=cast(Handler, core_handler),
                    program=program,
                ),
            ),
        ),
    )


class SyncRuntime(BaseRuntime):
    """Synchronous runtime with hardcoded handler-based cooperative scheduling.

    This runtime has hardcoded handlers:
    - scheduler_state_handler: Task queue management
    - task_scheduler_handler: Spawn/Wait/Gather/Race
    - sync_await_handler: Async effects via background thread
    - core_handler: Get/Put/Ask/etc.

    Per spec: SyncRuntime NEVER sees PythonAsyncSyntaxEscape. Async effects
    are handled directly by sync_await_handler using a background thread.
    """

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
        """Step until Done or Failed. Handlers manage all task scheduling.

        SyncRuntime only expects Done, Failed, or CESKState from step().
        """
        while True:
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


__all__ = [
    "SyncRuntime",
]
