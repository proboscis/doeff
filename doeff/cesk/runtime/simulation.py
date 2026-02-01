"""Simulation runtime with controllable time for testing time-based effects."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, TypeVar, cast

from doeff._vendor import FrozenDict
from doeff.cesk.errors import UnhandledEffectError
from doeff.cesk.handler_frame import Handler, HandlerContext, WithHandler
from doeff.cesk.state import CESKState
from doeff.cesk.handlers.core_handler import core_handler
from doeff.cesk.handlers.scheduler_state_handler import scheduler_state_handler
from doeff.cesk.handlers.task_scheduler_handler import task_scheduler_handler
from doeff.cesk.result import Done, Failed
from doeff.cesk.runtime.base import BaseRuntime, ExecutionError
from doeff.cesk.runtime_result import RuntimeResult
from doeff.cesk.step import step
from doeff.cesk.state import CESKState, ProgramControl
from doeff.effects.time import DelayEffect, GetTimeEffect, WaitUntilEffect

if TYPE_CHECKING:
    from doeff.program import Program

T = TypeVar("T")


def _make_simulation_time_handler(runtime: "SimulationRuntime") -> Handler:
    def simulation_time_handler(effect: Any, ctx: HandlerContext) -> Any:
        if isinstance(effect, DelayEffect):
            runtime._current_time = runtime._current_time + timedelta(seconds=effect.seconds)
            new_store = {**ctx.store, "__current_time__": runtime._current_time}
            return CESKState.with_value(None, ctx.env, new_store, ctx.k)

        if isinstance(effect, WaitUntilEffect):
            runtime._current_time = max(effect.target_time, runtime._current_time)
            new_store = {**ctx.store, "__current_time__": runtime._current_time}
            return CESKState.with_value(None, ctx.env, new_store, ctx.k)

        if isinstance(effect, GetTimeEffect):
            return CESKState.with_value(runtime._current_time, ctx.env, ctx.store, ctx.k)

        raise UnhandledEffectError(f"simulation_time_handler: unhandled effect {type(effect).__name__}")

    return simulation_time_handler


def _wrap_with_simulation_handlers(program: "Program[T]", runtime: "SimulationRuntime") -> "Program[T]":
    simulation_handler = _make_simulation_time_handler(runtime)
    return WithHandler(
        handler=cast(Handler, scheduler_state_handler),
        program=WithHandler(
            handler=cast(Handler, task_scheduler_handler),
            program=WithHandler(
                handler=cast(Handler, simulation_handler),
                program=WithHandler(
                    handler=cast(Handler, core_handler),
                    program=program,
                ),
            ),
        ),
    )


class SimulationRuntime(BaseRuntime):
    """Runtime with controllable time for testing time-based effects."""

    def __init__(
        self,
        handlers: dict[type, Any] | None = None,
        start_time: datetime | None = None,
    ):
        super().__init__(handlers or {})
        self._current_time = start_time if start_time is not None else datetime.now()

    @property
    def current_time(self) -> datetime:
        return self._current_time

    def advance_time(self, delta: timedelta) -> None:
        self._current_time = self._current_time + delta

    def set_time(self, time: datetime) -> None:
        self._current_time = time

    def run(
        self,
        program: Program[T],
        env: dict[str, Any] | None = None,
        store: dict[str, Any] | None = None,
    ) -> RuntimeResult[T]:
        frozen_env = FrozenDict(env) if env else FrozenDict()
        final_store: dict[str, Any] = dict(store) if store else {}
        final_store["__current_time__"] = self._current_time
        
        wrapped_program = _wrap_with_simulation_handlers(program, self)
        
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
    "SimulationRuntime",
]
