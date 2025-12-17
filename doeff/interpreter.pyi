from __future__ import annotations

from typing import Any, TypeVar

from doeff.program import Program
from doeff.types import Effect, ExecutionContext, RunResult

T = TypeVar("T")

logger: Any
_NO_HANDLER: object

def _effect_is(effect: Effect, cls: type[Any]) -> bool: ...
def force_eval(prog: Program[T]) -> Program[T]: ...

class ProgramInterpreter:
    reader_handler: Any
    state_handler: Any
    atomic_handler: Any
    writer_handler: Any
    future_handler: Any
    thread_handler: Any
    result_handler: Any
    io_handler: Any
    graph_handler: Any
    memo_handler: Any
    cache_handler: Any

    def __init__(
        self,
        custom_handlers: dict[str, Any] | None = ...,
        *,
        max_log_entries: int | None = ...,
    ) -> None: ...
    def run(self, program: Program[T], context: ExecutionContext | None = ...) -> RunResult[T]: ...
    async def run_async(
        self,
        program: Program[T],
        context: ExecutionContext | None = ...,
    ) -> RunResult[T]: ...

__all__ = ["ProgramInterpreter", "force_eval"]
