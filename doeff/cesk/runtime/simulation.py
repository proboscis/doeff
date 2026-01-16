"""Simulation runtime for testing with simulated time."""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, TypeVar

from doeff._vendor import Err, Ok
from doeff.cesk.runtime.base import BaseRuntime
from doeff.cesk.state import (
    DoneStatus,
    ReadyStatus,
    TaskState,
    TimeCondition,
)
from doeff.cesk.types import TaskId

if TYPE_CHECKING:
    from doeff.cesk.handlers import Handler
    from doeff.cesk.types import Environment, Store
    from doeff.program import Program

T = TypeVar("T")


@dataclass(order=True)
class SimTask:
    wake_time: datetime
    seq: int = field(compare=True)
    task_id: TaskId = field(compare=False)


class SimulationRuntime(BaseRuntime):
    def __init__(
        self,
        handlers: dict[type, Handler] | None = None,
        start_time: datetime | None = None,
    ):
        super().__init__(handlers)
        self._current_time = start_time or datetime.now()
        self._seq = 0
        self._sim_queue: list[SimTask] = []
        self._mock_results: dict[type, Any] = {}
    
    def mock(self, awaitable_type: type, result: Any) -> SimulationRuntime:
        self._mock_results[awaitable_type] = result
        return self
    
    def run(
        self,
        program: Program[T],
        env: Environment | dict | None = None,
        store: Store | None = None,
    ) -> T:
        main_task_id = self._setup(program, env, store)
        
        for task_id in self._ready_queue:
            self._schedule(self._current_time, task_id)
        self._ready_queue.clear()
        
        while self._sim_queue:
            sim_task = heapq.heappop(self._sim_queue)
            self._current_time = sim_task.wake_time
            task_id = sim_task.task_id
            
            task = self._state.tasks.get(task_id)
            if not task:
                continue
            
            task.store["__current_time__"] = self._current_time
            
            new_task = self._step_task(task_id)
            self._process_task_status(task_id, new_task)
            
            for tid in list(self._ready_queue):
                self._schedule(self._current_time, tid)
            self._ready_queue.clear()
            
            if self._is_done(main_task_id):
                break
        
        return self._get_result(main_task_id)
    
    def _schedule(self, wake_time: datetime, task_id: TaskId) -> None:
        heapq.heappush(self._sim_queue, SimTask(wake_time, self._seq, task_id))
        self._seq += 1
    
    def _wake_waiters(self, condition: Any, result: Any) -> None:
        waiters = self._waiting.pop(condition, [])
        for waiter_id in waiters:
            waiter = self._state.tasks.get(waiter_id)
            if waiter:
                if isinstance(result, Ok):
                    resumed = waiter.resume_with(result.ok())
                elif isinstance(result, Err):
                    resumed = waiter.error_with(result.error)
                else:
                    resumed = waiter.resume_with(result)
                self._state = self._state.with_task(waiter_id, resumed)
                
                wake_time = self._current_time
                if isinstance(condition, TimeCondition):
                    wake_time = max(condition.wake_time, self._current_time)
                self._schedule(wake_time, waiter_id)


__all__ = ["SimulationRuntime"]
