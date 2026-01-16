"""Base runtime for the unified CESK machine."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import defaultdict, deque
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol, TypeVar

from doeff._vendor import Err, Ok
from doeff.cesk.handlers import Handler, default_handlers
from doeff.cesk.state import (
    BlockedStatus,
    CESKState,
    Condition,
    CreateFutureRequest,
    CreateSpawnRequest,
    CreateTaskRequest,
    DoneStatus,
    FutureCondition,
    PerformIORequest,
    ReadyStatus,
    RequestingStatus,
    ResolveFutureRequest,
    SpawnCondition,
    TaskCondition,
    TaskState,
    TimeCondition,
)
from doeff.cesk.step import step
from doeff.cesk.types import FutureHandle, FutureId, SpawnId, TaskHandle, TaskId

if TYPE_CHECKING:
    from doeff.cesk.types import Environment, Store
    from doeff.program import Program

T = TypeVar("T")


class RuntimeProtocol(Protocol):
    def run(
        self,
        program: Program[T],
        env: Environment | dict | None = None,
        store: Store | None = None,
    ) -> T: ...


class BaseRuntime(ABC):
    def __init__(self, handlers: dict[type, Handler] | None = None):
        self._handlers = handlers or default_handlers()
        self._state: CESKState = CESKState()
        self._ready_queue: deque[TaskId] = deque()
        self._waiting: dict[Condition, list[TaskId]] = defaultdict(list)
        self._current_time: datetime = datetime.now()
    
    @abstractmethod
    def run(
        self,
        program: Program[T],
        env: Environment | dict | None = None,
        store: Store | None = None,
    ) -> T:
        pass
    
    def _setup(
        self,
        program: Program,
        env: Environment | dict | None,
        store: Store | None,
    ) -> TaskId:
        from doeff._vendor import FrozenDict
        
        env_frozen = FrozenDict(env) if env else FrozenDict()
        store_dict = dict(store) if store else {}
        store_dict["__current_time__"] = self._current_time
        
        self._state, main_task_id = CESKState.initial(program, env_frozen, store_dict)
        self._ready_queue = deque([main_task_id])
        self._waiting = defaultdict(list)
        
        return main_task_id
    
    def _step_task(self, task_id: TaskId) -> TaskState:
        task = self._state.tasks[task_id]
        return step(task, self._handlers)
    
    def _process_task_status(self, task_id: TaskId, new_task: TaskState) -> None:
        match new_task.status:
            case ReadyStatus(_):
                self._ready_queue.append(task_id)
            
            case BlockedStatus(condition):
                self._waiting[condition].append(task_id)
            
            case RequestingStatus(request):
                self._handle_request(task_id, new_task, request)
            
            case DoneStatus(result):
                self._wake_waiters(TaskCondition(task_id), result)
        
        self._state = self._state.with_task(task_id, new_task)
    
    def _handle_request(self, task_id: TaskId, task: TaskState, request: Any) -> None:
        match request:
            case CreateTaskRequest(program):
                child_id, self._state = self._state.create_task(program, task.env, task.store)
                self._ready_queue.append(child_id)
                resumed = task.resume_with(TaskHandle(child_id))
                self._state = self._state.with_task(task_id, resumed)
                self._ready_queue.append(task_id)
            
            case CreateFutureRequest():
                future_id, self._state = self._state.create_future()
                resumed = task.resume_with(FutureHandle(future_id))
                self._state = self._state.with_task(task_id, resumed)
                self._ready_queue.append(task_id)
            
            case ResolveFutureRequest(future_id, value):
                self._state = self._state.resolve_future(future_id, Ok(value))
                self._wake_waiters(FutureCondition(future_id), Ok(value))
                resumed = task.resume_with(None)
                self._state = self._state.with_task(task_id, resumed)
                self._ready_queue.append(task_id)
            
            case PerformIORequest(action):
                try:
                    result = action()
                    resumed = task.resume_with(result)
                except Exception as ex:
                    resumed = task.error_with(ex)
                self._state = self._state.with_task(task_id, resumed)
                self._ready_queue.append(task_id)
            
            case CreateSpawnRequest(program, backend):
                spawn_id, self._state = self._state.create_spawn()
                self._handle_spawn(spawn_id, program, backend)
                resumed = task.resume_with(SpawnId(spawn_id))
                self._state = self._state.with_task(task_id, resumed)
                self._ready_queue.append(task_id)
            
            case _:
                self._handle_custom_request(task_id, task, request)
    
    def _handle_spawn(self, spawn_id: SpawnId, program: Program, backend: Any) -> None:
        pass
    
    def _handle_custom_request(self, task_id: TaskId, task: TaskState, request: Any) -> None:
        raise NotImplementedError(f"Unknown request type: {type(request)}")
    
    def _wake_waiters(self, condition: Condition, result: Any) -> None:
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
                self._ready_queue.append(waiter_id)
    
    def _check_time_conditions(self) -> None:
        to_wake = []
        for condition in list(self._waiting.keys()):
            if isinstance(condition, TimeCondition):
                if condition.wake_time <= self._current_time:
                    to_wake.append(condition)
        
        for condition in to_wake:
            self._wake_waiters(condition, None)
    
    def _is_done(self, main_task_id: TaskId) -> bool:
        main_task = self._state.tasks.get(main_task_id)
        if main_task and isinstance(main_task.status, DoneStatus):
            return True
        return False
    
    def _get_result(self, main_task_id: TaskId) -> Any:
        main_task = self._state.tasks.get(main_task_id)
        if main_task and isinstance(main_task.status, DoneStatus):
            return main_task.status.result.unwrap()
        raise RuntimeError("Main task not done")


__all__ = [
    "BaseRuntime",
    "RuntimeProtocol",
]
