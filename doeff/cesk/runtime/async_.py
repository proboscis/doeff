from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING, Any

from doeff.cesk.runtime.base import BaseRuntime
from doeff.cesk.state import CESKState, TaskState, ProgramControl
from doeff.cesk.result import Done, Failed, Suspended
from doeff.cesk.step import step
from doeff.cesk.handlers import Handler, default_handlers
from doeff.cesk.frames import ContinueValue, ContinueError, ContinueProgram, FrameResult
from doeff.cesk.types import Store, TaskId
from doeff.effects.future import FutureAwaitEffect
from doeff.effects.gather import GatherEffect
from doeff.effects.time import DelayEffect, WaitUntilEffect

if TYPE_CHECKING:
    from doeff.program import Program


def _placeholder_handler(effect: Any, task_state: TaskState, store: Store) -> ContinueValue:
    return ContinueValue(
        value=None,
        env=task_state.env,
        store=store,
        k=task_state.kontinuation,
    )


class AsyncRuntime(BaseRuntime):

    def __init__(self, handlers: dict[type, Handler] | None = None):
        base_handlers = default_handlers()
        base_handlers[DelayEffect] = _placeholder_handler
        base_handlers[WaitUntilEffect] = _placeholder_handler
        base_handlers[FutureAwaitEffect] = _placeholder_handler
        base_handlers[GatherEffect] = _placeholder_handler

        if handlers:
            base_handlers.update(handlers)

        super().__init__(base_handlers)
        self._user_handlers = handlers or {}

    async def run(
        self,
        program: Program,
        env: dict[str, Any] | None = None,
        store: dict[str, Any] | None = None,
    ) -> Any:
        state = self._create_initial_state(program, env, store)
        return await self._run_scheduler(state)

    async def _run_scheduler(self, state: CESKState) -> Any:
        pending_async: dict[TaskId, tuple[asyncio.Task[tuple[Any, Store]], Suspended]] = {}
        task_results: dict[TaskId, Any] = {}
        task_errors: dict[TaskId, BaseException] = {}
        gather_waiters: dict[TaskId, tuple[list[TaskId], Suspended]] = {}

        main_task_id = state.main_task

        while True:
            ready_task_ids = [
                tid for tid in state.get_ready_tasks()
                if tid not in pending_async
            ]

            if ready_task_ids:
                task_id = ready_task_ids[0]
                single_state = self._make_single_task_state(state, task_id)
                result = step(single_state, self._handlers)

                if isinstance(result, Done):
                    state = self._update_store(state, result.store)
                    if task_id == main_task_id:
                        await self._cancel_all(pending_async)
                        return result.value
                    task_results[task_id] = result.value
                    state = self._check_gather_complete(
                        state, task_id, gather_waiters, task_results, task_errors
                    )
                    continue

                if isinstance(result, Failed):
                    state = self._update_store(state, result.store)
                    if task_id == main_task_id:
                        await self._cancel_all(pending_async)
                        exc = result.exception
                        if result.captured_traceback is not None:
                            exc.__cesk_traceback__ = result.captured_traceback  # type: ignore[attr-defined]
                        raise exc
                    task_errors[task_id] = result.exception
                    state = self._check_gather_complete(
                        state, task_id, gather_waiters, task_results, task_errors
                    )
                    continue

                if isinstance(result, CESKState):
                    state = self._merge_task(state, task_id, result)
                    continue

                if isinstance(result, Suspended):
                    effect = result.effect
                    effect_type = type(effect)

                    if effect_type in self._user_handlers:
                        task_state = state.tasks[task_id]
                        dispatch_result = self._dispatch_effect(effect, task_state, state.store)
                        state = self._apply_dispatch_result(state, task_id, result, dispatch_result)
                        continue

                    if isinstance(effect, GatherEffect):
                        programs = effect.programs
                        if not programs:
                            new_single = result.resume([], state.store)
                            state = self._merge_task(state, task_id, new_single)
                            continue

                        child_ids: list[TaskId] = []
                        current_env = state.tasks[task_id].env
                        for prog in programs:
                            child_id = TaskId.new()
                            child_task = TaskState.initial(prog, dict(current_env))
                            state = state.add_task(child_id, child_task)
                            child_ids.append(child_id)

                        gather_waiters[task_id] = (child_ids, result)
                        continue

                    if isinstance(effect, FutureAwaitEffect):
                        coro = self._do_await(effect.awaitable, state.store)
                        pending_async[task_id] = (asyncio.create_task(coro), result)
                        continue

                    if isinstance(effect, DelayEffect):
                        coro = self._do_delay(effect.seconds, state.store)
                        pending_async[task_id] = (asyncio.create_task(coro), result)
                        continue

                    if isinstance(effect, WaitUntilEffect):
                        coro = self._do_wait_until(effect.target_time, state.store)
                        pending_async[task_id] = (asyncio.create_task(coro), result)
                        continue

                    task_state = state.tasks[task_id]
                    dispatch_result = self._dispatch_effect(effect, task_state, state.store)
                    state = self._apply_dispatch_result(state, task_id, result, dispatch_result)
                    continue

            if pending_async:
                tasks_only = [t for t, _ in pending_async.values()]
                done, _ = await asyncio.wait(tasks_only, return_when=asyncio.FIRST_COMPLETED)

                for tid in list(pending_async.keys()):
                    atask, suspended = pending_async[tid]
                    if atask in done:
                        del pending_async[tid]
                        try:
                            value, new_store = atask.result()
                            state = self._update_store(state, new_store)
                            new_single = suspended.resume(value, new_store)
                            state = self._merge_task(state, tid, new_single)
                        except Exception as ex:
                            if tid == main_task_id:
                                await self._cancel_all(pending_async)
                                raise
                            task_errors[tid] = ex
                            state = self._check_gather_complete(
                                state, tid, gather_waiters, task_results, task_errors
                            )
                        break
                continue

            if not ready_task_ids and not pending_async:
                if state.is_main_task_done():
                    main_result = state.get_main_result()
                    if main_result is not None:
                        if main_result.is_ok():
                            return main_result.ok()
                        raise main_result.err()  # type: ignore[misc]
                await asyncio.sleep(0)

    async def _do_await(self, awaitable: Any, store: Store) -> tuple[Any, Store]:
        value = await awaitable
        return (value, store)

    async def _do_delay(self, seconds: float, store: Store) -> tuple[Any, Store]:
        await asyncio.sleep(seconds)
        return (None, store)

    async def _do_wait_until(self, target_time: datetime, store: Store) -> tuple[Any, Store]:
        now = datetime.now()
        if target_time > now:
            delay_seconds = (target_time - now).total_seconds()
            await asyncio.sleep(delay_seconds)
        return (None, store)

    def _make_single_task_state(self, state: CESKState, task_id: TaskId) -> CESKState:
        task = state.tasks[task_id]
        return CESKState(
            tasks={task_id: task},
            store=state.store,
            main_task=task_id,
            futures=state.futures,
            spawn_results=state.spawn_results,
        )

    def _merge_task(self, state: CESKState, task_id: TaskId, stepped: CESKState) -> CESKState:
        new_tasks = dict(state.tasks)
        new_tasks[task_id] = stepped.tasks[stepped.main_task]
        return CESKState(
            tasks=new_tasks,
            store=stepped.store,
            main_task=state.main_task,
            futures=state.futures,
            spawn_results=state.spawn_results,
        )

    def _update_store(self, state: CESKState, store: Store) -> CESKState:
        return CESKState(
            tasks=state.tasks,
            store=store,
            main_task=state.main_task,
            futures=state.futures,
            spawn_results=state.spawn_results,
        )

    def _apply_dispatch_result(
        self,
        state: CESKState,
        task_id: TaskId,
        suspended: Suspended,
        dispatch_result: FrameResult,
    ) -> CESKState:
        if isinstance(dispatch_result, ContinueError):
            new_single = suspended.resume_error(dispatch_result.error)
            return self._merge_task(state, task_id, new_single)
        if isinstance(dispatch_result, ContinueProgram):
            return self._merge_task(state, task_id, CESKState(
                C=ProgramControl(dispatch_result.program),
                E=dispatch_result.env,
                S=dispatch_result.store,
                K=dispatch_result.k,
            ))
        if isinstance(dispatch_result, ContinueValue):
            new_single = suspended.resume(dispatch_result.value, dispatch_result.store)
            return self._merge_task(state, task_id, new_single)
        raise RuntimeError(f"Unexpected dispatch result type: {type(dispatch_result)}")

    def _check_gather_complete(
        self,
        state: CESKState,
        completed_id: TaskId,
        gather_waiters: dict[TaskId, tuple[list[TaskId], Suspended]],
        task_results: dict[TaskId, Any],
        task_errors: dict[TaskId, BaseException],
    ) -> CESKState:
        for parent_id, (child_ids, suspended) in list(gather_waiters.items()):
            if completed_id not in child_ids:
                continue

            if completed_id in task_errors:
                del gather_waiters[parent_id]
                new_single = suspended.resume_error(task_errors[completed_id])
                return self._merge_task(state, parent_id, new_single)

            all_done = all(cid in task_results or cid in task_errors for cid in child_ids)
            if all_done:
                del gather_waiters[parent_id]
                results = [task_results[cid] for cid in child_ids]
                new_single = suspended.resume(results, state.store)
                return self._merge_task(state, parent_id, new_single)

        return state

    async def _cancel_all(
        self,
        pending: dict[TaskId, tuple[asyncio.Task[tuple[Any, Store]], Suspended]],
    ) -> None:
        for atask, _ in pending.values():
            atask.cancel()
        if pending:
            tasks = [t for t, _ in pending.values()]
            await asyncio.gather(*tasks, return_exceptions=True)


__all__ = [
    "AsyncRuntime",
]
