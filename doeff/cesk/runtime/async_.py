from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import TYPE_CHECKING, Any, TypeVar, cast

from doeff._vendor import FrozenDict
from doeff.cesk.errors import UnhandledEffectError
from doeff.cesk.handler_frame import Handler, WithHandler
from doeff.cesk.handlers.async_effects_handler import async_effects_handler
from doeff.cesk.handlers.core_handler import core_handler
from doeff.cesk.handlers.queue_handler import (
    CURRENT_TASK_KEY,
    TASK_QUEUE_KEY,
    TASK_REGISTRY_KEY,
    WAITERS_KEY,
    queue_handler,
)
from doeff.cesk.handlers.scheduler_handler import scheduler_handler
from doeff.cesk.result import Done, Failed, Suspended
from doeff.cesk.runtime.base import BaseRuntime, ExecutionError
from doeff.cesk.runtime_result import RuntimeResult
from doeff.cesk.step import step
from doeff.cesk.state import CESKState, ProgramControl
from doeff.program import Program

if TYPE_CHECKING:
    pass

T = TypeVar("T")


def _wrap_with_handlers(program: Program[T]) -> Program[T]:
    return WithHandler(
        handler=cast(Handler, queue_handler),
        program=WithHandler(
            handler=cast(Handler, scheduler_handler),
            program=WithHandler(
                handler=cast(Handler, async_effects_handler),
                program=WithHandler(
                    handler=cast(Handler, core_handler),
                    program=program,
                ),
            ),
        ),
    )


class AsyncRuntime(BaseRuntime):

    def __init__(self, handlers: dict[type, Any] | None = None):
        super().__init__(handlers or {})
        self._user_handlers = handlers or {}

    async def run(
        self,
        program: Program[T],
        env: dict[str, Any] | None = None,
        store: dict[str, Any] | None = None,
    ) -> RuntimeResult[T]:
        frozen_env = FrozenDict(env) if env else FrozenDict()
        final_store: dict[str, Any] = dict(store) if store else {}
        
        from uuid import uuid4
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
            value, final_state = await self._run_until_done(state)
            return self._build_success_result(value, final_state, final_state.S)
        except asyncio.CancelledError:
            raise
        except ExecutionError as err:
            if isinstance(err.exception, (KeyboardInterrupt, SystemExit, UnhandledEffectError)):
                raise err.exception from None
            return self._build_error_result(
                err.exception,
                err.final_state,
                captured_traceback=err.captured_traceback,
            )
        except Exception as exc:
            return self._build_error_result(exc, state)

    async def run_and_unwrap(
        self,
        program: Program[T],
        env: dict[str, Any] | None = None,
        store: dict[str, Any] | None = None,
    ) -> T:
        result = await self.run(program, env, store)
        return result.value

    async def _run_until_done(self, state: CESKState) -> tuple[Any, CESKState]:
        active_tasks: dict[Any, asyncio.Task[Any]] = {}
        for _ in range(100000):
            result = step(state)
            if isinstance(result, Done):
                return (result.value, state)
            if isinstance(result, Failed):
                raise ExecutionError(result.exception, state, result.captured_traceback)
            if isinstance(result, Suspended):
                state = await self._handle_suspended(result, state, active_tasks)
                continue
            if isinstance(result, CESKState):
                state = result
                continue
            raise RuntimeError(f"Unexpected step result: {type(result)}")
        raise ExecutionError(RuntimeError("Exceeded maximum steps"), state)
    
    async def _handle_suspended(
        self, result: Suspended, state: CESKState, active_tasks: dict[Any, asyncio.Task[Any]]
    ) -> CESKState:
        if result.awaitables:
            completed = await self._await_first(result.awaitables, active_tasks)
            return result.resume(completed, result.stored_store or state.S)
        awaitable = result.effect.awaitable  # type: ignore[attr-defined]
        try:
            value = await cast(Any, awaitable)
            return result.resume(value, state.S)
        except asyncio.CancelledError:
            raise
        except Exception as ex:
            return result.resume_error(ex)
    
    async def _await_first(
        self,
        awaitables: dict[Any, Awaitable[Any]],
        active_tasks: dict[Any, asyncio.Task[Any]],
    ) -> tuple[Any, Any]:
        for tid, awaitable in awaitables.items():
            if tid not in active_tasks:
                active_tasks[tid] = asyncio.ensure_future(awaitable)
        
        tasks_to_wait = [active_tasks[tid] for tid in awaitables if tid in active_tasks]
        done, _ = await asyncio.wait(tasks_to_wait, return_when=asyncio.FIRST_COMPLETED)
        
        for task in done:
            for tid, t in list(active_tasks.items()):
                if t is task:
                    del active_tasks[tid]
                    try:
                        return (tid, task.result())
                    except asyncio.CancelledError:
                        raise
                    except Exception as ex:
                        return (tid, ex)
        raise RuntimeError("No task completed")


__all__ = [
    "AsyncRuntime",
]
