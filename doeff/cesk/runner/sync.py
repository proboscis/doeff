"""Synchronous runner with user-provided handlers.

SyncRunner steps the CESK machine until Done/Failed.

Per SPEC-CESK-EFFECT-BOUNDARIES.md:
- SyncRunner should NEVER see PythonAsyncSyntaxEscape
- PythonAsyncSyntaxEscape is ONLY for AsyncRunner
- For SyncRunner, handlers must handle Await effects directly (e.g., via thread pool)
- Do NOT share handlers between SyncRunner and AsyncRunner
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypeVar, cast

from doeff._vendor import Err, FrozenDict, Ok
from doeff.cesk.errors import UnhandledEffectError
from doeff.cesk.handler_frame import Handler, WithHandler
from doeff.cesk.result import Done, Failed
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


class SyncRunner:
    """Synchronous runner that steps until Done/Failed.

    SyncRunner expects handlers to handle ALL effects directly, including Await.
    PythonAsyncSyntaxEscape is ONLY for AsyncRunner - SyncRunner should never see it.

    For async effects in SyncRunner, use handlers that execute awaitables
    synchronously (e.g., in a thread pool).

    Handler order: [h0, h1, h2] means h2 sees effects first (innermost),
    h0 sees last (outermost).

    Example:
        runner = SyncRunner()
        result = runner.run(
            program,
            handlers=[
                sync_await_handler,  # handles Await by running in thread
                core_handler,
            ],
        )
    """

    def run(
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
            value, final_state = self._run_until_done(state)
            return self._build_success_result(value, final_state, final_state.S)
        except _ExecutionError as err:
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
        handlers: list[Handler],
        env: dict[str, Any] | None = None,
        store: dict[str, Any] | None = None,
    ) -> T:
        """Run a program and return the value directly, raising on error."""
        result = self.run(program, handlers, env, store)
        return result.value

    def _run_until_done(self, state: CESKState) -> tuple[Any, CESKState]:
        """Step until Done or Failed.

        SyncRunner only expects Done, Failed, or CESKState from step().
        PythonAsyncSyntaxEscape should NEVER reach here - handlers must handle
        Await effects directly for SyncRunner.
        """
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

            if isinstance(result, CESKState):
                state = result
                continue

            # PythonAsyncSyntaxEscape or any other result type is an error
            raise RuntimeError(
                f"Unexpected step result: {type(result).__name__}. "
                f"SyncRunner only handles Done, Failed, and CESKState. "
                f"For async effects, use handlers that handle Await directly."
            )

    def _build_success_result(
        self,
        value: T,
        state: CESKState,
        final_store: dict[str, Any] | None = None,
    ) -> RuntimeResultImpl[T]:
        store = final_store if final_store is not None else state.S

        final_state = {
            k: v for k, v in store.items()
            if not k.startswith("__")
        }
        final_log = list(store.get("__log__", []))
        final_graph = store.get("__graph__")

        return RuntimeResultImpl(
            _result=Ok(value),
            _state=final_state,
            _log=final_log,
            _env={},
            _k_stack=KStackTrace(frames=()),
            _effect_stack=EffectStackTrace(),
            _python_stack=PythonStackTrace(frames=()),
            _graph=final_graph,
        )

    def _build_error_result(
        self,
        exc: BaseException,
        state: CESKState,
        final_store: dict[str, Any] | None = None,
        captured_traceback: Any = None,
    ) -> RuntimeResultImpl[Any]:
        store = final_store if final_store is not None else state.S

        final_state = {
            k: v for k, v in store.items()
            if not k.startswith("__")
        }
        final_log = list(store.get("__log__", []))
        final_graph = store.get("__graph__")

        if captured_traceback is None:
            captured_traceback = getattr(exc, "__cesk_traceback__", None)
        python_stack, effect_stack = build_stacks_from_captured_traceback(captured_traceback)

        return RuntimeResultImpl(
            _result=Err(exc),  # type: ignore[arg-type]
            _state=final_state,
            _log=final_log,
            _env={},
            _k_stack=KStackTrace(frames=()),
            _effect_stack=effect_stack,
            _python_stack=python_stack,
            _graph=final_graph,
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

    Handlers are applied so that first in list is outermost (sees effects last).
    [h0, h1, h2] -> h2 sees effects first, h0 sees last.
    """
    result: Program[T] = program
    for handler in reversed(handlers):
        result = WithHandler(
            handler=cast(Handler, handler),
            program=result,
        )
    return result


__all__ = [
    "SyncRunner",
]
