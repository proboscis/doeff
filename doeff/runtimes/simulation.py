"""SimulationRuntime - Runtime for simulated time execution."""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, TypeVar

from doeff._vendor import Err, Ok
from doeff.cesk.state import CESKState
from doeff.cesk.result import Done, Failed, Suspended
from doeff.runtime import (
    AwaitPayload,
    DelayPayload,
    SchedulePayload,
    SpawnPayload,
    WaitUntilPayload,
)
from doeff.runtimes.base import RuntimeMixin, EffectError, RuntimeResult

if TYPE_CHECKING:
    from doeff.cesk.types import Environment, Store
    from doeff.program import Program

T = TypeVar("T")


@dataclass(order=True)
class SimTask:
    wake_time: datetime
    seq: int = field(compare=True)
    state: CESKState = field(compare=False)
    resume_value: Any = field(compare=False, default=None)


class SimulationRuntime(RuntimeMixin):
    def __init__(
        self,
        handlers: dict | None = None,
        start_time: datetime | None = None,
    ):
        self._init_handlers(handlers)
        self._current_time = start_time or datetime.now()
        self._seq = 0
        self._queue: list[SimTask] = []
        self._mock_results: dict[type, Any] = {}
        self._recorded_awaits: list[Any] = []
    
    @property
    def current_time(self) -> datetime:
        return self._current_time
    
    def mock(self, awaitable_type: type, result: Any) -> "SimulationRuntime":
        self._mock_results[awaitable_type] = result
        return self
    
    def _handle_payload(
        self,
        state: CESKState,
        payload: SchedulePayload,
        store: "Store",
    ) -> None:
        match payload:
            case AwaitPayload(awaitable=aw):
                self._recorded_awaits.append(aw)
                mock_result = self._get_mock_result(aw)
                self._submit(self._current_time, state, mock_result)
            
            case DelayPayload(duration=d):
                wake_time = self._current_time + d
                self._submit(wake_time, state, None)
            
            case WaitUntilPayload(target=t):
                self._submit(t, state, None)
            
            case SpawnPayload(program=prog, env=e, store=s):
                dispatcher = self._create_dispatcher()
                child_E, child_S = self._prepare_env_store(e, s, dispatcher)
                child_state = CESKState.initial(prog, child_E, child_S)
                self._submit(self._current_time, child_state, None)
                self._submit(self._current_time, state, None)
            
            case _:
                raise TypeError(f"Unknown payload: {type(payload)}")
    
    def _submit(self, wake_time: datetime, state: CESKState, resume_value: Any) -> None:
        task = SimTask(wake_time, self._seq, state, resume_value)
        heapq.heappush(self._queue, task)
        self._seq += 1
    
    def _get_mock_result(self, awaitable: Any) -> Any:
        for key, value in self._mock_results.items():
            if isinstance(awaitable, key):
                return value
        return None
    
    def run(
        self,
        program: "Program[T]",
        env: "Environment | dict | None" = None,
        store: "Store | None" = None,
    ) -> T:
        """Run program with simulated time. Raises EffectError on failure."""
        dispatcher = self._create_dispatcher()
        E, S = self._prepare_env_store(env, store, dispatcher)
        
        initial_state = CESKState.initial(program, E, S)
        self._submit(self._current_time, initial_state, None)
        
        final_result: T | None = None
        
        while self._queue:
            task = heapq.heappop(self._queue)
            self._current_time = task.wake_time
            
            # If task has a resume value, we need to resume from suspended state
            # For initial task or tasks without resume, just step from current state
            state = task.state
            
            result = self._step_until_effect(state, dispatcher)
            
            match result:
                case Done(value=v):
                    final_result = v
                    # Continue processing other tasks (spawned children)
                
                case Failed(exception=exc, captured_traceback=tb):
                    raise EffectError(str(exc), exc, tb)
                
                case (Suspended() as suspended, CESKState() as last_state):
                    payload, new_store = self._get_payload_from_suspended(
                        suspended, last_state, dispatcher
                    )
                    k = self._make_continuation(suspended, last_state, new_store)
                    
                    # For simulation, we schedule instead of executing immediately
                    # Create new state that will resume when task is popped
                    next_state = k.resume(self._get_resume_value(payload), new_store)
                    self._handle_payload(next_state, payload, new_store)
        
        if final_result is None:
            raise EffectError("Program completed without result")
        
        return final_result
    
    def _get_resume_value(self, payload: SchedulePayload) -> Any:
        """Get the value to resume with for this payload type."""
        match payload:
            case AwaitPayload(awaitable=aw):
                return self._get_mock_result(aw)
            case DelayPayload() | WaitUntilPayload():
                return None
            case SpawnPayload():
                return None
            case _:
                return None
    
    def run_safe(
        self,
        program: "Program[T]",
        env: "Environment | dict | None" = None,
        store: "Store | None" = None,
    ) -> RuntimeResult[T]:
        """Run program, return Result instead of raising."""
        try:
            value = self.run(program, env, store)
            return RuntimeResult(Ok(value))
        except EffectError as e:
            cause = e.cause if isinstance(e.cause, Exception) else e
            return RuntimeResult(Err(cause), e.effect_traceback)
        except Exception as e:
            return RuntimeResult(Err(e))


__all__ = ["SimulationRuntime"]
