"""Kontinuation frame types for the unified CESK machine."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, TypeAlias, runtime_checkable

from doeff._vendor import Err, Ok
from doeff.cesk.types import Environment, TaskId

if TYPE_CHECKING:
    from doeff.cesk.state import Control, TaskState
    from doeff.program import KleisliProgramCall, Program
    from doeff.types import Effect


@dataclass(frozen=True)
class FrameResult:
    control: Control
    env: Environment
    actions: tuple[Any, ...] = ()
    keep_frame: bool = False


@runtime_checkable
class Frame(Protocol):
    def on_value(self, value: Any, env: Environment) -> FrameResult:
        ...
    
    def on_error(self, error: BaseException, env: Environment) -> FrameResult:
        ...


@dataclass
class ReturnFrame:
    generator: Generator[Any, Any, Any]
    saved_env: Environment
    program_call: KleisliProgramCall | None = None
    
    def on_value(self, value: Any, env: Environment) -> FrameResult:
        from doeff._types_internal import EffectBase
        from doeff.cesk.state import EffectControl, Error, ProgramControl, Value
        from doeff.program import ProgramBase
        
        try:
            item = self.generator.send(value)
            
            if isinstance(item, EffectBase):
                return FrameResult(EffectControl(item), self.saved_env, keep_frame=True)
            elif isinstance(item, ProgramBase):
                return FrameResult(ProgramControl(item), self.saved_env, keep_frame=True)
            else:
                from doeff.cesk.dispatcher import InterpreterInvariantError
                err = InterpreterInvariantError(
                    f"Program yielded unexpected type: {type(item).__name__}"
                )
                return FrameResult(Error(err), self.saved_env)
        except StopIteration as e:
            return FrameResult(Value(e.value), self.saved_env)
        except Exception as ex:
            return FrameResult(Error(ex), self.saved_env)
    
    def on_error(self, error: BaseException, env: Environment) -> FrameResult:
        from doeff._types_internal import EffectBase
        from doeff.cesk.state import EffectControl, Error, ProgramControl, Value
        from doeff.program import ProgramBase
        
        try:
            item = self.generator.throw(error)
            
            if isinstance(item, EffectBase):
                return FrameResult(EffectControl(item), self.saved_env, keep_frame=True)
            elif isinstance(item, ProgramBase):
                return FrameResult(ProgramControl(item), self.saved_env, keep_frame=True)
            else:
                from doeff.cesk.dispatcher import InterpreterInvariantError
                err = InterpreterInvariantError(
                    f"Program yielded unexpected type: {type(item).__name__}"
                )
                return FrameResult(Error(err), self.saved_env)
        except StopIteration as e:
            return FrameResult(Value(e.value), self.saved_env)
        except Exception as propagated:
            return FrameResult(Error(propagated), self.saved_env)


@dataclass(frozen=True)
class LocalFrame:
    restore_env: Environment
    
    def on_value(self, value: Any, env: Environment) -> FrameResult:
        from doeff.cesk.state import Value
        return FrameResult(Value(value), self.restore_env)
    
    def on_error(self, error: BaseException, env: Environment) -> FrameResult:
        from doeff.cesk.state import Error
        return FrameResult(Error(error), self.restore_env)


@dataclass(frozen=True)
class InterceptFrame:
    transforms: tuple[Callable[[Effect], Effect | Program | None], ...]
    
    def on_value(self, value: Any, env: Environment) -> FrameResult:
        from doeff.cesk.state import Value
        return FrameResult(Value(value), env)
    
    def on_error(self, error: BaseException, env: Environment) -> FrameResult:
        from doeff.cesk.state import Error
        return FrameResult(Error(error), env)


@dataclass(frozen=True)
class ListenFrame:
    log_start_index: int
    
    def on_value(self, value: Any, env: Environment) -> FrameResult:
        from doeff._types_internal import ListenResult
        from doeff.cesk.state import Value
        from doeff.utils import BoundedLog
        return FrameResult(
            Value(ListenResult(value=value, log=BoundedLog([]))),
            env,
            actions=(("capture_log", self.log_start_index),),
        )
    
    def on_error(self, error: BaseException, env: Environment) -> FrameResult:
        from doeff.cesk.state import Error
        return FrameResult(Error(error), env)


@dataclass
class GatherFrame:
    remaining_programs: list[Program]
    collected_results: list[Any]
    saved_env: Environment
    
    def on_value(self, value: Any, env: Environment) -> FrameResult:
        from doeff.cesk.state import ProgramControl, Value
        
        new_collected = self.collected_results + [value]
        
        if not self.remaining_programs:
            return FrameResult(Value(new_collected), self.saved_env)
        
        next_prog, *rest = self.remaining_programs
        return FrameResult(
            ProgramControl(next_prog),
            self.saved_env,
            actions=(("push_gather_frame", rest, new_collected, self.saved_env),),
        )
    
    def on_error(self, error: BaseException, env: Environment) -> FrameResult:
        from doeff.cesk.state import Error
        return FrameResult(Error(error), self.saved_env)


@dataclass(frozen=True)
class SafeFrame:
    saved_env: Environment
    
    def on_value(self, value: Any, env: Environment) -> FrameResult:
        from doeff.cesk.state import Value
        return FrameResult(Value(Ok(value)), self.saved_env)
    
    def on_error(self, error: BaseException, env: Environment) -> FrameResult:
        from doeff.cesk.state import Value
        if isinstance(error, Exception):
            return FrameResult(Value(Err(error)), self.saved_env)
        return FrameResult(Value(Err(RuntimeError(str(error)))), self.saved_env)


@dataclass(frozen=True)
class RaceFrame:
    other_task_ids: frozenset[TaskId]
    
    def on_value(self, value: Any, env: Environment) -> FrameResult:
        from doeff.cesk.state import Value
        return FrameResult(
            Value(value),
            env,
            actions=(("cancel_tasks", self.other_task_ids),),
        )
    
    def on_error(self, error: BaseException, env: Environment) -> FrameResult:
        from doeff.cesk.state import Error
        return FrameResult(
            Error(error),
            env,
            actions=(("cancel_tasks", self.other_task_ids),),
        )


@dataclass(frozen=True)
class JoinFrame:
    target_task_id: TaskId
    
    def on_value(self, value: Any, env: Environment) -> FrameResult:
        from doeff.cesk.state import Value
        return FrameResult(Value(value), env)
    
    def on_error(self, error: BaseException, env: Environment) -> FrameResult:
        from doeff.cesk.state import Error
        return FrameResult(Error(error), env)


Kontinuation: TypeAlias = list[Frame]


__all__ = [
    "Frame",
    "FrameResult",
    "ReturnFrame",
    "LocalFrame",
    "InterceptFrame",
    "ListenFrame",
    "GatherFrame",
    "SafeFrame",
    "RaceFrame",
    "JoinFrame",
    "Kontinuation",
]
