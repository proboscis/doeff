"""Simulation runtime for the unified CESK architecture with virtual time."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, TypeVar

from doeff._vendor import FrozenDict
from doeff.cesk.actions import Delay, Resume, WaitUntil
from doeff.cesk.events import (
    AllTasksComplete,
    EffectSuspended,
    Stepped,
    TaskBlocked,
    TaskCompleted,
    TaskFailed,
    TimeWait,
)
from doeff.cesk.handlers import HandlerRegistry, default_handlers
from doeff.cesk.unified_state import UnifiedCESKState as CESKState, TaskStatus, WaitingForTime
from doeff.cesk.unified_step import unified_step
from doeff.cesk.types import TaskId

if TYPE_CHECKING:
    from doeff.cesk.types import Environment, Store
    from doeff.program import Program

T = TypeVar("T")


class SimulationRuntimeError(Exception):
    def __init__(self, message: str, cause: BaseException | None = None):
        super().__init__(message)
        self.cause = cause


def _make_simulation_handlers(
    base_handlers: HandlerRegistry,
    get_current_time: Any,
) -> dict[type, Any]:
    from doeff.effects import DelayEffect, GetTimeEffect, WaitUntilEffect
    
    def handle_get_time_sim(effect: GetTimeEffect, ctx: Any) -> tuple[Resume, ...]:
        return (Resume(get_current_time()),)
    
    def handle_delay_sim(effect: DelayEffect, ctx: Any) -> tuple[Delay, ...]:
        return (Delay(timedelta(seconds=effect.seconds)),)
    
    def handle_wait_until_sim(effect: WaitUntilEffect, ctx: Any) -> tuple[WaitUntil, ...]:
        return (WaitUntil(effect.target_time),)
    
    handlers: dict[type, Any] = dict(base_handlers)
    handlers[GetTimeEffect] = handle_get_time_sim
    handlers[DelayEffect] = handle_delay_sim
    handlers[WaitUntilEffect] = handle_wait_until_sim
    return handlers


class UnifiedSimulationRuntime:
    def __init__(
        self,
        handlers: HandlerRegistry | None = None,
        start_time: datetime | None = None,
    ):
        self._base_handlers = handlers or default_handlers()
        self._start_time = start_time or datetime(2025, 1, 1, 0, 0, 0)
        self._current_time = self._start_time
    
    def _get_current_time(self) -> datetime:
        return self._current_time
    
    def run(
        self,
        program: Program[T],
        env: Environment | dict[Any, Any] | None = None,
        store: Store | None = None,
    ) -> T:
        if env is None:
            env_frozen: Environment = FrozenDict()
        elif isinstance(env, FrozenDict):
            env_frozen = env
        else:
            env_frozen = FrozenDict(env)
        
        self._current_time = self._start_time
        state = CESKState.initial(program, env_frozen, store, self._current_time)
        
        handlers = _make_simulation_handlers(
            self._base_handlers,
            self._get_current_time,
        )
        
        max_steps = 100_000
        
        for _ in range(max_steps):
            event = unified_step(state, handlers)
            
            if isinstance(event, TaskCompleted):
                return event.value
            
            if isinstance(event, TaskFailed):
                raise SimulationRuntimeError(str(event.error), event.error)
            
            if isinstance(event, AllTasksComplete):
                result = event.state.main_result()
                if result is not None:
                    value, is_success = result
                    if is_success:
                        return value
                    raise SimulationRuntimeError(
                        str(value),
                        value if isinstance(value, BaseException) else None,
                    )
                raise SimulationRuntimeError("Program completed without result")
            
            if isinstance(event, EffectSuspended):
                effect = event.effect
                
                from doeff.effects import DelayEffect, WaitUntilEffect
                if isinstance(effect, DelayEffect):
                    self._current_time += timedelta(seconds=effect.seconds)
                    task = event.state.get_task(event.task_id)
                    if task is not None:
                        from doeff.cesk.state import Value
                        new_task = task.with_control(Value(None))
                        state = event.state.update_task(new_task).with_time(self._current_time)
                    continue
                
                if isinstance(effect, WaitUntilEffect):
                    if effect.target_time > self._current_time:
                        self._current_time = effect.target_time
                    task = event.state.get_task(event.task_id)
                    if task is not None:
                        from doeff.cesk.state import Value
                        new_task = task.with_control(Value(None))
                        state = event.state.update_task(new_task).with_time(self._current_time)
                    continue
                
                raise SimulationRuntimeError(
                    f"Unhandled effect: {type(effect).__name__}"
                )
            
            if isinstance(event, TaskBlocked):
                state = self._advance_blocked_tasks(event.state)
                if state is None:
                    raise SimulationRuntimeError("All tasks blocked with no wake condition")
                continue
            
            if isinstance(event, Stepped):
                state = event.state
                continue
            
            raise SimulationRuntimeError(f"Unknown event type: {type(event)}")
        
        raise SimulationRuntimeError("Maximum steps exceeded")
    
    def _advance_blocked_tasks(self, state: CESKState) -> CESKState | None:
        blocked = state.blocked_tasks()
        if not blocked:
            return None
        
        earliest_wake: datetime | None = None
        for task_id, condition in blocked:
            if isinstance(condition, WaitingForTime):
                if earliest_wake is None or condition.target < earliest_wake:
                    earliest_wake = condition.target
        
        if earliest_wake is None:
            return None
        
        self._current_time = earliest_wake
        new_state = state.with_time(self._current_time)
        
        for task_id, condition in blocked:
            if isinstance(condition, WaitingForTime) and condition.target <= self._current_time:
                task = new_state.get_task(task_id)
                if task is not None:
                    from doeff.cesk.state import Value
                    new_task = task.with_control(Value(None)).with_status(TaskStatus.RUNNING)
                    new_state = new_state.update_task(new_task)
        
        return new_state


__all__ = [
    "UnifiedSimulationRuntime",
    "SimulationRuntimeError",
]
