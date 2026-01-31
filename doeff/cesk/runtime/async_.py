from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, TypeVar, cast

from doeff._vendor import FrozenDict
from doeff.cesk.errors import UnhandledEffectError
from doeff.cesk.handler_frame import Handler, WithHandler
from doeff.cesk.handlers.async_effects_handler import async_effects_handler
from doeff.cesk.handlers.core_handler import core_handler
from doeff.cesk.handlers.queue_handler import (
    CURRENT_TASK_KEY,
    PENDING_IO_KEY,
    TASK_QUEUE_KEY,
    TASK_REGISTRY_KEY,
    WAITERS_KEY,
    queue_handler,
)
from doeff.cesk.handlers.scheduler_handler import scheduler_handler
from doeff.cesk.result import Done, Failed, Suspended
from doeff.cesk.runtime.base import BaseRuntime, ExecutionError
from doeff.cesk.runtime_result import RuntimeResult
from doeff.cesk.state import CESKState, ProgramControl
from doeff.cesk.step import step
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
        max_steps = 100000
        pending_async: dict[Any, asyncio.Task[Any]] = {}
        for _ in range(max_steps):
            result = step(state)
            if isinstance(result, Done):
                return (result.value, state)
            if isinstance(result, Failed):
                raise ExecutionError(result.exception, state, result.captured_traceback)
            if isinstance(result, Suspended):
                task_id, value, error = await self._await_pending_io(result, pending_async)
                state = self._resume_task(result, task_id, value, error, state.S)
                continue
            if isinstance(result, CESKState):
                state = result
                continue
            raise RuntimeError(f"Unexpected step result: {type(result)}")
        raise ExecutionError(RuntimeError(f"Exceeded maximum steps ({max_steps})"), state)

    async def _await_pending_io(
        self, result: Suspended, pending_async: dict[Any, asyncio.Task[Any]]
    ) -> tuple[Any, Any, BaseException | None]:
        pending_io = result.pending_io
        if pending_io:
            for tid, info in pending_io.items():
                if tid not in pending_async:
                    pending_async[tid] = asyncio.create_task(info["awaitable"])
            done, _ = await asyncio.wait(
                pending_async.values(), return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                task_id = next(tid for tid, t in pending_async.items() if t is task)
                del pending_async[task_id]
                try:
                    return (task_id, task.result(), None)
                except asyncio.CancelledError:
                    raise
                except Exception as ex:
                    return (task_id, None, ex)
        awaitable = result.effect.awaitable  # type: ignore[attr-defined]
        try:
            value = await cast(Any, awaitable)
            return (None, value, None)
        except asyncio.CancelledError:
            raise
        except Exception as ex:
            return (None, None, ex)

    def _resume_task(
        self,
        result: Suspended,
        task_id: Any,
        value: Any,
        error: BaseException | None,
        current_store: dict[str, Any],
    ) -> CESKState:
        pending_io = result.pending_io
        global_store = result.stored_store or {}
        if pending_io and task_id is not None:
            task_info = pending_io[task_id]
            task_k = task_info["k"]
            task_store = dict(task_info.get("store_snapshot", {}))
            new_pending = {tid: info for tid, info in pending_io.items() if tid != task_id}
            task_store[PENDING_IO_KEY] = new_pending
            task_store[CURRENT_TASK_KEY] = task_id
            task_store[TASK_QUEUE_KEY] = global_store.get(TASK_QUEUE_KEY, [])
            task_store[TASK_REGISTRY_KEY] = global_store.get(TASK_REGISTRY_KEY, {})
            task_store[WAITERS_KEY] = global_store.get(WAITERS_KEY, {})
            from doeff.cesk.state import Error, Value

            if error is not None:
                return CESKState(C=Error(error), E=FrozenDict(), S=task_store, K=task_k)
            return CESKState(C=Value(value), E=FrozenDict(), S=task_store, K=task_k)
        if error is not None:
            return result.resume_error(error)
        return result.resume(value, current_store)


__all__ = [
    "AsyncRuntime",
]
