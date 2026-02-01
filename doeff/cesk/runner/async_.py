"""Asynchronous runner with user-provided handlers.

AsyncRunner handles PythonAsyncSyntaxEscape via await, returning async T.
Unlike AsyncRuntime (which has hardcoded handlers), AsyncRunner accepts
user-provided handler lists for explicit composition.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, TypeVar, cast

from doeff._vendor import Err, FrozenDict, Ok
from doeff.cesk.errors import UnhandledEffectError
from doeff.cesk.handler_frame import Handler, WithHandler
from doeff.cesk.result import Done, Failed, PythonAsyncSyntaxEscape
from doeff.cesk.runtime_result import (
    EffectStackTrace,
    KStackTrace,
    PythonStackTrace,
    RuntimeResult,
    RuntimeResultImpl,
    build_stacks_from_captured_traceback,
)
from doeff.cesk.state import CESKState, ProgramControl
from doeff.cesk.step import step
from doeff.program import Program

if TYPE_CHECKING:
    from doeff.cesk.types import Environment, Store

T = TypeVar("T")


class AsyncRunner:
    """Asynchronous runner that handles PythonAsyncSyntaxEscape via await.

    Unlike Runtime classes (which have hardcoded handlers), Runner accepts
    user-provided handler lists for explicit composition.

    The only difference between SyncRunner and AsyncRunner is how they handle
    PythonAsyncSyntaxEscape:
    - SyncRunner: runs awaitable in thread pool with isolated event loop
    - AsyncRunner: awaits in user's event loop

    Example:
        runner = AsyncRunner()
        result = await runner.run(
            program,
            handlers=[core_handler, scheduler_handler, python_async_handler],
        )
    """

    async def run(
        self,
        program: Program[T],
        handlers: list[Handler],
        env: dict[str, Any] | None = None,
        store: dict[str, Any] | None = None,
    ) -> RuntimeResult[T]:
        """Run a program with the given handlers.

        Args:
            program: The program to run.
            handlers: List of handlers, from outermost to innermost.
            env: Optional initial environment.
            store: Optional initial store.

        Returns:
            RuntimeResult containing the final value or error.
        """
        frozen_env: Environment = FrozenDict(env) if env else FrozenDict()
        final_store: Store = dict(store) if store else {}

        wrapped = _wrap_with_handlers(program, handlers)

        state = CESKState(
            C=ProgramControl(wrapped),
            E=frozen_env,
            S=final_store,
            K=[],
        )

        try:
            value, final_state = await self._run_until_done(state)
            return self._build_success_result(value, final_state, final_state.S)
        except asyncio.CancelledError:
            raise
        except _ExecutionError as err:
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
        handlers: list[Handler],
        env: dict[str, Any] | None = None,
        store: dict[str, Any] | None = None,
    ) -> T:
        """Run a program and return the value directly, raising on error."""
        result = await self.run(program, handlers, env, store)
        return result.value

    async def _run_until_done(self, state: CESKState) -> tuple[Any, CESKState]:
        """Step until Done or Failed, handling PythonAsyncSyntaxEscape via await."""
        pending_tasks: dict[Any, asyncio.Task[Any]] = {}

        while True:
            result = step(state)

            if isinstance(result, Done):
                return (result.value, state)

            if isinstance(result, Failed):
                raise _ExecutionError(
                    exception=result.exception,
                    final_state=state,
                    captured_traceback=result.captured_traceback,
                )

            if isinstance(result, PythonAsyncSyntaxEscape):
                current_store = result.store if result.store is not None else state.S

                if result.awaitables:
                    # Multi-task case: await first completion
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
                                state = result.resume_error((task_id, ex))
                            break
                    else:
                        raise RuntimeError("asyncio.wait returned but no task completed")
                elif result.awaitable is not None:
                    # Single awaitable case
                    try:
                        value = await result.awaitable
                        state = result.resume(value, current_store)
                    except asyncio.CancelledError:
                        raise
                    except Exception as ex:
                        state = result.resume_error(ex)
                else:
                    raise RuntimeError("PythonAsyncSyntaxEscape with neither awaitable nor awaitables")
                continue

            if isinstance(result, CESKState):
                state = result
                continue

            raise RuntimeError(f"Unexpected step result: {type(result)}")

    def _build_success_result(
        self,
        value: T,
        state: CESKState,
        final_store: dict[str, Any] | None = None,
    ) -> RuntimeResultImpl[T]:
        store = final_store if final_store is not None else state.S

        return RuntimeResultImpl(
            _result=Ok(value),
            _raw_store=dict(store),
            _env={},
            _k_stack=KStackTrace(frames=()),
            _effect_stack=EffectStackTrace(),
            _python_stack=PythonStackTrace(frames=()),
        )

    def _build_error_result(
        self,
        exc: BaseException,
        state: CESKState,
        final_store: dict[str, Any] | None = None,
        captured_traceback: Any = None,
    ) -> RuntimeResultImpl[Any]:
        store = final_store if final_store is not None else state.S

        if captured_traceback is None:
            captured_traceback = getattr(exc, "__cesk_traceback__", None)
        python_stack, effect_stack = build_stacks_from_captured_traceback(captured_traceback)

        return RuntimeResultImpl(
            _result=Err(exc),  # type: ignore[arg-type]
            _raw_store=dict(store),
            _env={},
            _k_stack=KStackTrace(frames=()),
            _effect_stack=effect_stack,
            _python_stack=python_stack,
            _captured_traceback=captured_traceback,
        )


class _ExecutionError(Exception):
    """Internal exception for carrying execution errors with state."""

    def __init__(
        self,
        exception: BaseException,
        final_state: CESKState,
        captured_traceback: Any = None,
    ):
        self.exception = exception
        self.final_state = final_state
        self.captured_traceback = captured_traceback
        super().__init__(str(exception))


def _wrap_with_handlers(program: Program[T], handlers: list[Handler]) -> Program[T]:
    """Wrap a program with the handler stack.

    Handlers are applied from outermost to innermost (first handler in list
    becomes outermost).
    """
    result: Program[T] = program
    for handler in reversed(handlers):
        result = WithHandler(
            handler=cast(Handler, handler),
            program=result,
        )
    return result


__all__ = [
    "AsyncRunner",
]
