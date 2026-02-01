from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, TypeVar, cast

from doeff._vendor import FrozenDict
from doeff.cesk.errors import UnhandledEffectError
from doeff.cesk.handler_frame import Handler, WithHandler
from doeff.cesk.handlers.core_handler import core_handler
from doeff.cesk.handlers.python_async_handler import python_async_handler
from doeff.cesk.handlers.scheduler_state_handler import scheduler_state_handler
from doeff.cesk.handlers.task_scheduler_handler import task_scheduler_handler
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
        handler=cast(Handler, scheduler_state_handler),
        program=WithHandler(
            handler=cast(Handler, task_scheduler_handler),
            program=WithHandler(
                handler=cast(Handler, python_async_handler),
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
        pending_tasks: dict[Any, asyncio.Task[Any]] = {}

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

            if isinstance(result, Suspended):
                current_store = result.store if result.store is not None else state.S

                if result.awaitables:
                    for task_id, awaitable in result.awaitables.items():
                        if task_id not in pending_tasks:
                            pending_tasks[task_id] = asyncio.create_task(awaitable)

                    done, _ = await asyncio.wait(
                        pending_tasks.values(),
                        return_when=asyncio.FIRST_COMPLETED,
                    )

                    for task_id, atask in list(pending_tasks.items()):
                        if atask in done:
                            del pending_tasks[task_id]
                            try:
                                value = atask.result()
                                state = result.resume((task_id, value), current_store)
                            except asyncio.CancelledError:
                                raise
                            except Exception as ex:
                                state = result.resume_error(ex)
                            break
                    else:
                        raise RuntimeError("asyncio.wait returned but no task completed")
                elif result.awaitable is not None:
                    try:
                        value = await result.awaitable
                        state = result.resume(value, current_store)
                    except asyncio.CancelledError:
                        raise
                    except Exception as ex:
                        state = result.resume_error(ex)
                else:
                    raise RuntimeError("Suspended with neither awaitable nor awaitables")
                continue

            if isinstance(result, CESKState):
                state = result
                continue

            raise RuntimeError(f"Unexpected step result: {type(result)}")


__all__ = [
    "AsyncRuntime",
]
