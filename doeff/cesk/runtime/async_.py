from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, TypeVar, cast

from doeff._vendor import FrozenDict
from doeff.cesk.errors import UnhandledEffectError
from doeff.cesk.frames import ReturnFrame
from doeff.cesk.handler_frame import Handler, WithHandler
from doeff.cesk.handlers.async_effects_handler import async_effects_handler
from doeff.cesk.handlers.core_handler import core_handler
from doeff.cesk.handlers.queue_handler import queue_handler
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

SCHEDULER_KEY_PREFIX = "__scheduler_"


def _merge_scheduler_state(
    task_store: dict[str, Any],
    current_store: dict[str, Any],
    task_id: Any,
) -> dict[str, Any]:
    merged = dict(task_store)
    for key, value in current_store.items():
        if isinstance(key, str) and key.startswith(SCHEDULER_KEY_PREFIX):
            merged[key] = value
    merged[f"{SCHEDULER_KEY_PREFIX}current_task__"] = task_id
    return merged


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
        
        for step_num in range(max_steps):
            result = step(state)
            
            if isinstance(result, Done):
                import os
                debug = os.environ.get("DOEFF_DEBUG")
                if debug:
                    print(f"[runtime] Done! value={result.value}")
                return (result.value, state)
            
            if isinstance(result, Failed):
                raise ExecutionError(
                    exception=result.exception,
                    final_state=state,
                    captured_traceback=result.captured_traceback,
                )
            
            if isinstance(result, Suspended):
                from doeff.effects.future import AllTasksSuspendedEffect
                import os
                debug = os.environ.get("DOEFF_DEBUG")
                if debug:
                    print(f"[runtime] Suspended, effect type: {type(result.effect).__name__}")
                
                if isinstance(result.effect, AllTasksSuspendedEffect):
                    pending_io = result.effect.pending_io
                    effect_store = result.effect.store
                    if not pending_io:
                        raise ExecutionError(
                            exception=RuntimeError("Suspended with no pending I/O"),
                            final_state=state,
                        )
                    
                    for task_id, info in pending_io.items():
                        if task_id not in pending_async:
                            coro = info["awaitable"]
                            pending_async[task_id] = asyncio.create_task(coro)
                    
                    tasks_only = list(pending_async.values())
                    import os
                    debug = os.environ.get("DOEFF_DEBUG")
                    if debug:
                        print(f"[runtime] asyncio.wait with {len(tasks_only)} tasks, pending_async={list(pending_async.keys())}")
                    done, _ = await asyncio.wait(tasks_only, return_when=asyncio.FIRST_COMPLETED)
                    if debug:
                        print(f"[runtime] asyncio.wait done, {len(done)} completed")
                    
                    for task_id, atask in list(pending_async.items()):
                        if atask in done:
                            del pending_async[task_id]
                            task_info = pending_io.get(task_id)
                            if task_info is None:
                                continue
                            
                            new_pending = dict(pending_io)
                            del new_pending[task_id]
                            new_store = dict(effect_store)
                            new_store[f"{SCHEDULER_KEY_PREFIX}pending_io__"] = new_pending
                            
                            task_k = task_info["k"]
                            task_store_snapshot = task_info.get("store_snapshot", {})
                            import os
                            debug = os.environ.get("DOEFF_DEBUG")
                            if debug:
                                print(f"[runtime] Resuming task {task_id} with k_len={len(task_k)}, k_types={[type(f).__name__ for f in task_k[:10]]}")
                            
                            merged_store = _merge_scheduler_state(task_store_snapshot, new_store, task_id)
                            
                            try:
                                value = atask.result()
                                from doeff.cesk.state import Value
                                state = CESKState(
                                    C=Value(value),
                                    E=FrozenDict(),
                                    S=merged_store,
                                    K=task_k,
                                )
                            except asyncio.CancelledError:
                                raise
                            except Exception as ex:
                                from doeff.cesk.state import Error
                                state = CESKState(
                                    C=Error(ex),
                                    E=FrozenDict(),
                                    S=merged_store,
                                    K=task_k,
                                )
                            break
                    continue
                else:
                    awaitable = result.effect.awaitable  # type: ignore[attr-defined]
                    try:
                        value = await cast(Any, awaitable)
                        state = result.resume(value, state.S)
                    except asyncio.CancelledError:
                        raise
                    except Exception as ex:
                        state = result.resume_error(ex)
                    continue
            
            if isinstance(result, CESKState):
                state = result
                continue
            
            raise RuntimeError(f"Unexpected step result: {type(result)}")
        
        raise ExecutionError(
            exception=RuntimeError(f"Exceeded maximum steps ({max_steps})"),
            final_state=state,
        )


__all__ = [
    "AsyncRuntime",
]
