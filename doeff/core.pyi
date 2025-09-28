from collections.abc import Awaitable, Callable, Generator
from dataclasses import dataclass
from typing import Any, Generic, ParamSpec, TypeVar

from doeff.effects import (
    IO,
    Annotate,
    Ask,
    Await,
    Catch,
    Dep,
    Fail,
    Gather,
    GatherDict,
    Get,
    Listen,
    Local,
    Log,
    Modify,
    Parallel,
    Print,
    Put,
    Step,
    Tell,
    annotate,
    ask,
    await_,
    catch,
    fail,
    get,
    io,
    listen,
    local,
    modify,
    parallel,
    print_,
    put,
    step,
    tell,
)
from doeff._vendor import Maybe, Result, WGraph

T = TypeVar("T")
U = TypeVar("U")
P = ParamSpec("P")

@dataclass(frozen=True)
class RunResult(Generic[T]):
    context: ExecutionContext
    result: Result[T]
    @property
    def value(self) -> T: ...
    @property
    def is_ok(self) -> bool: ...
    @property
    def is_err(self) -> bool: ...
    @property
    def graph(self) -> WGraph: ...
    @property
    def state(self) -> dict[str, Any]: ...
    @property
    def log(self) -> list[Any]: ...
    @property
    def env(self) -> dict[str, Any]: ...

def do(
    func: Callable[P, Generator[Effect | Program, Any, T]],
) -> KleisliProgram[P, T]: ...
def comprehensive_example() -> Program[dict[str, Any]]: ...
async def fetch_data() -> list: ...
async def process_item(item: int) -> int: ...
def risky_computation() -> Program[dict]: ...
def inner_computation() -> Program[str]: ...
def logged_computation() -> Program[int]: ...
async def main() -> Any: ...

class ProgramInterpreter:
    def __init__(self) -> None: ...
    async def run(
        self, program: Program[T], context: ExecutionContext | None = ...
    ) -> RunResult[T]: ...
    async def _handle_effect(self, effect: Effect, ctx: ExecutionContext) -> Any: ...
    async def _dispatch_reader_ask(
        self, effect: Effect, ctx: ExecutionContext
    ) -> Any: ...
    async def _dispatch_reader_local(
        self, effect: Effect, ctx: ExecutionContext
    ) -> Any: ...
    async def _dispatch_state_get(
        self, effect: Effect, ctx: ExecutionContext
    ) -> Any: ...
    async def _dispatch_state_put(
        self, effect: Effect, ctx: ExecutionContext
    ) -> Any: ...
    async def _dispatch_state_modify(
        self, effect: Effect, ctx: ExecutionContext
    ) -> Any: ...
    async def _dispatch_writer_tell(
        self, effect: Effect, ctx: ExecutionContext
    ) -> Any: ...
    async def _dispatch_writer_listen(
        self, effect: Effect, ctx: ExecutionContext
    ) -> Any: ...
    async def _dispatch_future_await(
        self, effect: Effect, ctx: ExecutionContext
    ) -> Any: ...
    async def _dispatch_future_parallel(
        self, effect: Effect, ctx: ExecutionContext
    ) -> Any: ...
    async def _dispatch_result_fail(
        self, effect: Effect, ctx: ExecutionContext
    ) -> Any: ...
    async def _dispatch_result_catch(
        self, effect: Effect, ctx: ExecutionContext
    ) -> Any: ...
    async def _dispatch_io_run(self, effect: Effect, ctx: ExecutionContext) -> Any: ...
    async def _dispatch_io_print(
        self, effect: Effect, ctx: ExecutionContext
    ) -> Any: ...
    async def _dispatch_graph_step(
        self, effect: Effect, ctx: ExecutionContext
    ) -> Any: ...
    async def _dispatch_graph_annotate(
        self, effect: Effect, ctx: ExecutionContext
    ) -> Any: ...
    async def _dispatch_program_gather(
        self, effect: Effect, ctx: ExecutionContext
    ) -> list[Any]: ...
    async def _dispatch_program_gather_dict(
        self, effect: Effect, ctx: ExecutionContext
    ) -> dict[str, Any]: ...

@dataclass(frozen=True)
class KleisliProgram(Generic[P, T]):
    func: Callable[P, Program[T]]
    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> Program[T]: ...

class Program(Generic[T]):
    generator_func: Callable[[], Generator[Effect | Program, Any, T]]
    def __iter__(self) -> Generator[Effect | Program, Any, T]: ...
    def map(self, f: Callable[[T], U]) -> Program[U]: ...
    def flat_map(self, f: Callable[[T], Program[U]]) -> Program[U]: ...
    @staticmethod
    def pure(value: T) -> Program[T]: ...
    @staticmethod
    def first_success(*programs: Program[T] | Effect) -> Program[T]: ...
    @staticmethod
    def first_some(*programs: Program[T] | Effect) -> Program[Maybe[T]]: ...

class ExecutionContext:
    env: dict[str, Any]
    io_allowed: bool
    state: dict[str, Any]
    log: list[Any]
    graph: WGraph
    def copy(self) -> ExecutionContext: ...

class Effect:
    tag: str
    payload: Any

# Additional symbols:
class ResultEffectHandler:
    async def handle_fail(self, exc: Exception) -> None: ...
    async def handle_catch(
        self, payload: dict, ctx: ExecutionContext, engine: ProgramInterpreter
    ) -> Any: ...

class IOEffectHandler:
    async def handle_run(
        self, action: Callable[Any, Any], ctx: ExecutionContext
    ) -> Any: ...
    async def handle_print(self, message: str, ctx: ExecutionContext) -> None: ...

class FutureEffectHandler:
    async def handle_await(self, awaitable: Awaitable[Any]) -> Any: ...
    async def handle_parallel(
        self, awaitables: tuple[Awaitable[Any], Any]
    ) -> list[Any]: ...

class GraphEffectHandler:
    async def handle_step(self, payload: dict, ctx: ExecutionContext) -> Any: ...
    async def handle_annotate(
        self, meta: dict[str, Any], ctx: ExecutionContext
    ) -> None: ...

class StateEffectHandler:
    async def handle_get(self, key: str, ctx: ExecutionContext) -> Any: ...
    async def handle_put(self, payload: dict, ctx: ExecutionContext) -> None: ...
    async def handle_modify(self, payload: dict, ctx: ExecutionContext) -> Any: ...

class ReaderEffectHandler:
    async def handle_ask(
        self, effect: Effect, ctx: ExecutionContext, engine: ProgramInterpreter
    ) -> Any: ...
    async def handle_local(
        self, payload: dict, ctx: ExecutionContext, engine: ProgramInterpreter
    ) -> Any: ...

@dataclass(frozen=True)
class ListenResult:
    value: Any
    log: list[Any]
    def __iter__(self) -> Any: ...

class WriterEffectHandler:
    async def handle_tell(self, message: Any, ctx: ExecutionContext) -> None: ...
    async def handle_listen(
        self,
        sub_program_func: Callable,
        ctx: ExecutionContext,
        engine: ProgramInterpreter,
    ) -> ListenResult: ...

# Additional symbols:

# Additional symbols:

# Additional symbols:
