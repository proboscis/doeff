from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Protocol

from doeff.cesk.types import Environment, TaskId

if TYPE_CHECKING:
    from doeff.program import Program
    from doeff.types import Effect


class FrameResult(Protocol):
    pass


@dataclass(frozen=True)
class Continue:
    actions: list[Any] | None = None


@dataclass(frozen=True)
class PopFrame:
    pass


class Frame(ABC):
    @abstractmethod
    def on_value(self, value: Any) -> tuple[Any, FrameResult]:
        pass
    
    @abstractmethod
    def on_error(self, error: BaseException) -> tuple[BaseException, FrameResult]:
        pass
    
    def on_child_done(self, child_task_id: TaskId, result: Any) -> tuple[Any, FrameResult] | None:
        return None


@dataclass(frozen=True)
class ReturnFrame(Frame):
    generator: Any
    saved_env: Environment
    program_call: Any | None = None
    
    def on_value(self, value: Any) -> tuple[Any, FrameResult]:
        return value, Continue()
    
    def on_error(self, error: BaseException) -> tuple[BaseException, FrameResult]:
        return error, Continue()


@dataclass(frozen=True)
class LocalFrame(Frame):
    restore_env: Environment
    
    def on_value(self, value: Any) -> tuple[Any, FrameResult]:
        return value, PopFrame()
    
    def on_error(self, error: BaseException) -> tuple[BaseException, FrameResult]:
        return error, PopFrame()


@dataclass(frozen=True)
class SafeFrame(Frame):
    saved_env: Environment
    
    def on_value(self, value: Any) -> tuple[Any, FrameResult]:
        from doeff._vendor import Ok
        return Ok(value), PopFrame()
    
    def on_error(self, error: BaseException) -> tuple[BaseException, FrameResult]:
        from doeff._vendor import Err, NOTHING, Some
        from doeff.cesk_traceback import CapturedTraceback
        
        captured_maybe = NOTHING
        if hasattr(error, '__cesk_traceback__'):
            tb = getattr(error, '__cesk_traceback__')
            if isinstance(tb, CapturedTraceback):
                captured_maybe = Some(tb)
        
        err_result = Err(error, captured_traceback=captured_maybe)
        return err_result, PopFrame()  # type: ignore


@dataclass(frozen=True)
class ListenFrame(Frame):
    log_start_index: int
    
    def on_value(self, value: Any) -> tuple[Any, FrameResult]:
        return value, Continue()
    
    def on_error(self, error: BaseException) -> tuple[BaseException, FrameResult]:
        return error, PopFrame()


@dataclass(frozen=True)
class InterceptFrame(Frame):
    transforms: tuple[Callable[[Effect], Effect | Program | None], ...]
    
    def on_value(self, value: Any) -> tuple[Any, FrameResult]:
        return value, PopFrame()
    
    def on_error(self, error: BaseException) -> tuple[BaseException, FrameResult]:
        return error, PopFrame()


@dataclass(frozen=True)
class GatherFrame(Frame):
    remaining_programs: list[Program]
    collected_results: list[Any]
    saved_env: Environment
    child_task_ids: list[TaskId]
    
    def on_value(self, value: Any) -> tuple[Any, FrameResult]:
        new_collected = self.collected_results + [value]
        
        if not self.remaining_programs:
            return new_collected, PopFrame()
        
        return value, Continue()
    
    def on_error(self, error: BaseException) -> tuple[BaseException, FrameResult]:
        return error, PopFrame()
    
    def on_child_done(self, child_task_id: TaskId, result: Any) -> tuple[Any, FrameResult] | None:
        if child_task_id not in self.child_task_ids:
            return None
        
        new_collected = self.collected_results + [result]
        
        if len(new_collected) == len(self.child_task_ids):
            return new_collected, PopFrame()
        
        return result, Continue()


@dataclass(frozen=True)
class RaceFrame(Frame):
    child_task_ids: list[TaskId]
    
    def on_value(self, value: Any) -> tuple[Any, FrameResult]:
        return value, PopFrame()
    
    def on_error(self, error: BaseException) -> tuple[BaseException, FrameResult]:
        return error, PopFrame()
    
    def on_child_done(self, child_task_id: TaskId, result: Any) -> tuple[Any, FrameResult] | None:
        if child_task_id not in self.child_task_ids:
            return None
        
        return result, PopFrame()


__all__ = [
    "Frame",
    "FrameResult",
    "Continue",
    "PopFrame",
    "ReturnFrame",
    "LocalFrame",
    "SafeFrame",
    "ListenFrame",
    "InterceptFrame",
    "GatherFrame",
    "RaceFrame",
]
