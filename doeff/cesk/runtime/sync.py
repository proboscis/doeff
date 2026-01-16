"""Synchronous runtime for single-threaded execution."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, TypeVar

from doeff._vendor import Err, Ok
from doeff.cesk.runtime.base import BaseRuntime
from doeff.cesk.state import DoneStatus

if TYPE_CHECKING:
    from doeff.cesk.handlers import Handler
    from doeff.cesk.types import Environment, Store, TaskId
    from doeff.program import Program

T = TypeVar("T")


class SyncRuntime(BaseRuntime):
    def __init__(self, handlers: dict[type, Handler] | None = None):
        super().__init__(handlers)
    
    def run(
        self,
        program: Program[T],
        env: Environment | dict | None = None,
        store: Store | None = None,
    ) -> T:
        main_task_id = self._setup(program, env, store)
        
        max_steps = 1_000_000
        step_count = 0
        
        while not self._is_done(main_task_id) and step_count < max_steps:
            step_count += 1
            
            self._current_time = datetime.now()
            self._check_time_conditions()
            
            if not self._ready_queue:
                if self._waiting:
                    raise RuntimeError("Deadlock detected: tasks waiting but none ready")
                break
            
            task_id = self._ready_queue.popleft()
            task = self._state.tasks.get(task_id)
            if not task:
                continue
            
            task.store["__current_time__"] = self._current_time
            
            new_task = self._step_task(task_id)
            self._process_task_status(task_id, new_task)
        
        if step_count >= max_steps:
            raise RuntimeError(f"Maximum step count ({max_steps}) exceeded")
        
        return self._get_result(main_task_id)


__all__ = ["SyncRuntime"]
