"""Kontinuation frame types for the unified CESK machine."""

from __future__ import annotations

from collections.abc import Callable, Generator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, TypeAlias

from doeff.cesk.types import Environment, TaskId

if TYPE_CHECKING:
    from doeff.program import KleisliProgramCall, Program
    from doeff.types import Effect


class FrameProtocol(Protocol):
    def on_value(self, value: Any, env: Environment) -> FrameResult: ...
    def on_error(self, ex: BaseException, env: Environment) -> FrameResult: ...


@dataclass(frozen=True)
class ContinueWithValue:
    value: Any
    env: Environment


@dataclass(frozen=True)
class ContinueWithError:
    ex: BaseException
    env: Environment


@dataclass(frozen=True)
class PushProgram:
    program: Program
    env: Environment
    new_frame: Frame | None = None


@dataclass(frozen=True)
class PopFrame:
    value: Any
    env: Environment


FrameResult: TypeAlias = ContinueWithValue | ContinueWithError | PushProgram | PopFrame


@dataclass
class ReturnFrame:
    generator: Generator[Any, Any, Any]
    saved_env: Environment
    program_call: KleisliProgramCall | None = None

    def on_value(self, value: Any, env: Environment) -> FrameResult:
        return ContinueWithValue(value, self.saved_env)

    def on_error(self, ex: BaseException, env: Environment) -> FrameResult:
        return ContinueWithError(ex, self.saved_env)


@dataclass(frozen=True)
class LocalFrame:
    restore_env: Environment

    def on_value(self, value: Any, env: Environment) -> FrameResult:
        return ContinueWithValue(value, self.restore_env)

    def on_error(self, ex: BaseException, env: Environment) -> FrameResult:
        return ContinueWithError(ex, self.restore_env)


@dataclass(frozen=True)
class InterceptFrame:
    transforms: tuple[Callable[[Effect], Effect | Program | None], ...]

    def on_value(self, value: Any, env: Environment) -> FrameResult:
        return ContinueWithValue(value, env)

    def on_error(self, ex: BaseException, env: Environment) -> FrameResult:
        return ContinueWithError(ex, env)


@dataclass(frozen=True)
class ListenFrame:
    log_start_index: int

    def on_value(self, value: Any, env: Environment) -> FrameResult:
        return ContinueWithValue(value, env)

    def on_error(self, ex: BaseException, env: Environment) -> FrameResult:
        return ContinueWithError(ex, env)


@dataclass(frozen=True)
class GatherFrame:
    remaining_programs: list[Program]
    collected_results: list[Any]
    saved_env: Environment

    def on_value(self, value: Any, env: Environment) -> FrameResult:
        new_results = self.collected_results + [value]
        if not self.remaining_programs:
            return ContinueWithValue(new_results, self.saved_env)
        next_prog, *rest = self.remaining_programs
        new_frame = GatherFrame(rest, new_results, self.saved_env)
        return PushProgram(next_prog, self.saved_env, new_frame)

    def on_error(self, ex: BaseException, env: Environment) -> FrameResult:
        return ContinueWithError(ex, self.saved_env)


@dataclass(frozen=True)
class SafeFrame:
    saved_env: Environment

    def on_value(self, value: Any, env: Environment) -> FrameResult:
        from doeff._vendor import Ok
        return ContinueWithValue(Ok(value), self.saved_env)

    def on_error(self, ex: BaseException, env: Environment) -> FrameResult:
        from doeff._vendor import Err
        if isinstance(ex, Exception):
            return ContinueWithValue(Err(ex), self.saved_env)
        return ContinueWithError(ex, self.saved_env)


@dataclass(frozen=True)
class JoinFrame:
    target_task_id: TaskId
    saved_env: Environment

    def on_value(self, value: Any, env: Environment) -> FrameResult:
        return ContinueWithValue(value, self.saved_env)

    def on_error(self, ex: BaseException, env: Environment) -> FrameResult:
        return ContinueWithError(ex, self.saved_env)


@dataclass(frozen=True)
class RaceFrame:
    remaining_task_ids: list[TaskId]
    saved_env: Environment

    def on_value(self, value: Any, env: Environment) -> FrameResult:
        return ContinueWithValue(value, self.saved_env)

    def on_error(self, ex: BaseException, env: Environment) -> FrameResult:
        return ContinueWithError(ex, self.saved_env)


Frame: TypeAlias = (
    ReturnFrame
    | LocalFrame
    | InterceptFrame
    | ListenFrame
    | GatherFrame
    | SafeFrame
    | JoinFrame
    | RaceFrame
)

Kontinuation: TypeAlias = list[Frame]


__all__ = [
    "FrameProtocol",
    "FrameResult",
    "ContinueWithValue",
    "ContinueWithError",
    "PushProgram",
    "PopFrame",
    "ReturnFrame",
    "LocalFrame",
    "InterceptFrame",
    "ListenFrame",
    "GatherFrame",
    "SafeFrame",
    "JoinFrame",
    "RaceFrame",
    "Frame",
    "Kontinuation",
]
