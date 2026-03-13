from collections.abc import Awaitable, Callable, Coroutine, Iterable, Sequence
from types import ModuleType
from typing import Any, Generic, Literal, TypeAlias, TypedDict, TypeVar

from doeff.program import ProgramBase as _ProgramBase

_T = TypeVar("_T")

class CallFrame(TypedDict, total=False):
    frame_id: int
    function_name: str
    source_file: str
    source_line: int
    args_repr: str | None
    program_call: object | None

class DoExpr(_ProgramBase[_T], Generic[_T]):
    def __init__(self, *_args: Any, **_kwargs: Any) -> None: ...
    def to_generator(self) -> DoeffGenerator: ...

class EffectBase:
    tag: int
    def __init__(self, *_args: Any, **_kwargs: Any) -> None: ...

class DoCtrlBase(DoExpr[Any]):
    tag: int
    def __init__(self) -> None: ...

DoThunkBase: type[Any] | None

class K:
    def __repr__(self) -> str: ...

class Discontinued(Exception): ...

class TraceFrame:
    func_name: str
    source_file: str
    source_line: int
    def __init__(self, func_name: str, source_file: str, source_line: int) -> None: ...

class TraceHop:
    frames: list[TraceFrame]
    def __init__(self, frames: list[TraceFrame]) -> None: ...

class Ok(Generic[_T]):
    value: _T
    def __init__(self, value: _T) -> None: ...
    def is_ok(self) -> bool: ...
    def is_err(self) -> bool: ...

class Err:
    error: BaseException
    captured_traceback: object
    def __init__(self, error: BaseException, captured_traceback: object | None = None) -> None: ...
    def is_ok(self) -> bool: ...
    def is_err(self) -> bool: ...

ResultOk = Ok[Any]
ResultErr = Err

class RustHandler:
    def __repr__(self) -> str: ...

class PyKleisli:
    __dict__: dict[str, Any]
    def __init__(
        self,
        func: Callable[..., Any],
        name: str,
        file: str | None = None,
        line: int | None = None,
    ) -> None: ...
    def __call__(self, *args: Any, **kwargs: Any) -> Any: ...
    def __getattr__(self, name: str) -> Any: ...
    def __setattr__(self, name: str, value: Any) -> None: ...
    def partial(self, *args: Any, **kwargs: Any) -> Any: ...
    def and_then_k(self, binder: Callable[..., Any]) -> Any: ...
    def fmap(self, mapper: Callable[..., Any]) -> Any: ...

class DoeffGeneratorFn:
    callable: Callable[..., Any]
    function_name: str
    source_file: str
    source_line: int
    get_frame: Callable[[Any], Any]
    def __init__(
        self,
        callable: Callable[..., Any],
        function_name: str,
        source_file: str,
        source_line: int,
        get_frame: Callable[[Any], Any],
    ) -> None: ...
    def __call__(self, *args: Any, **kwargs: Any) -> DoeffGenerator: ...

class DoeffGenerator:
    generator: Any
    get_frame: Callable[[Any], Any]
    factory: DoeffGeneratorFn | None
    def __init__(
        self,
        generator: Any,
        function_name: str | None = None,
        source_file: str | None = None,
        source_line: int | None = None,
        get_frame: Callable[[Any], Any] | None = None,
        factory: DoeffGeneratorFn | None = None,
    ) -> None: ...
    @property
    def __doeff_inner__(self) -> Any: ...
    @property
    def function_name(self) -> str: ...
    @property
    def source_file(self) -> str: ...
    @property
    def source_line(self) -> int: ...

class DoeffTracebackData:
    entries: Any
    active_chain: Any
    def __init__(self, entries: Any, active_chain: Any | None = None) -> None: ...

RunResultValue: TypeAlias = Ok[_T] | Err

class RunResult(Generic[_T]):
    traceback_data: DoeffTracebackData | None
    @property
    def value(self) -> _T: ...
    @property
    def error(self) -> BaseException: ...
    @property
    def result(self) -> RunResultValue[_T]: ...
    @property
    def raw_store(self) -> dict[str, Any]: ...
    @property
    def log(self) -> list[Any]: ...
    @property
    def trace(self) -> list[Any]: ...
    def is_ok(self) -> bool: ...
    def is_err(self) -> bool: ...
    def display(self, verbose: bool = False) -> str: ...

class PyVM:
    def __init__(self) -> None: ...
    def run(self, program: DoExpr[Any] | EffectBase) -> Any: ...
    def run_with_result(self, program: DoExpr[Any] | EffectBase) -> RunResult[Any]: ...
    def state_items(self) -> dict[str, Any]: ...
    def logs(self) -> list[Any]: ...
    def put_state(self, key: str, value: Any) -> None: ...
    def put_env(self, key: Any, value: Any) -> None: ...
    def env_items(self) -> dict[Any, Any]: ...
    def enable_debug(self, level: str) -> None: ...
    def py_store(self) -> dict[str, Any] | None: ...
    def set_store(self, key: str, value: Any) -> None: ...
    def get_store(self, key: str) -> Any | None: ...
    def build_run_result(self, value: Any) -> RunResult[Any]: ...
    def build_run_result_error(
        self,
        error: BaseException,
        traceback_data: DoeffTracebackData | None = None,
    ) -> RunResult[Any]: ...
    def start_program(self, program: DoExpr[Any] | EffectBase) -> None: ...
    def step_once(
        self,
    ) -> (
        tuple[Literal["done"], Any]
        | tuple[Literal["continue"]]
        | tuple[Literal["call_async"], Callable[..., Awaitable[Any]], tuple[Any, ...]]
        | tuple[Literal["error"], BaseException, DoeffTracebackData | None]
    ): ...
    def feed_async_result(self, value: Any) -> None: ...
    def feed_async_error(self, error_value: BaseException) -> None: ...

class Pure(DoCtrlBase):
    value: Any
    def __init__(self, value: Any) -> None: ...

class Apply(DoCtrlBase):
    f: Callable[..., Any]
    args: Iterable[Any]
    kwargs: dict[str, Any]
    meta: dict[str, Any] | None
    def __init__(
        self,
        f: Callable[..., Any],
        args: Iterable[Any],
        kwargs: dict[str, Any],
        meta: dict[str, Any] | None = None,
    ) -> None: ...

class Expand(DoCtrlBase):
    factory: Callable[..., Any]
    args: Iterable[Any]
    kwargs: dict[str, Any]
    meta: dict[str, Any] | None
    def __init__(
        self,
        factory: Callable[..., Any],
        args: Iterable[Any],
        kwargs: dict[str, Any],
        meta: dict[str, Any] | None = None,
    ) -> None: ...

class Map(DoCtrlBase):
    source: DoExpr[Any]
    mapper: Callable[[Any], Any]
    mapper_meta: dict[str, Any]
    def __init__(
        self,
        source: DoExpr[Any],
        mapper: Callable[[Any], Any],
        mapper_meta: dict[str, Any] | None = None,
    ) -> None: ...

class FlatMap(DoCtrlBase):
    source: DoExpr[Any]
    binder: Callable[[Any], DoExpr[Any]]
    binder_meta: dict[str, Any]
    def __init__(
        self,
        source: DoExpr[Any],
        binder: Callable[[Any], DoExpr[Any]],
        binder_meta: dict[str, Any] | None = None,
    ) -> None: ...

class Eval(DoCtrlBase):
    expr: DoExpr[Any] | EffectBase
    def __init__(self, expr: DoExpr[Any] | EffectBase) -> None: ...

class EvalInScope(DoCtrlBase):
    expr: DoExpr[Any] | EffectBase
    scope: K
    def __init__(self, expr: DoExpr[Any] | EffectBase, scope: K) -> None: ...

class Perform(DoCtrlBase):
    effect: EffectBase
    def __init__(self, effect: EffectBase) -> None: ...

class Discontinue(DoCtrlBase):
    continuation: K
    exception: BaseException | None
    def __init__(self, continuation: K, exception: BaseException | None = None) -> None: ...

class Resume(DoCtrlBase):
    continuation: K
    value: Any
    def __init__(self, continuation: K, value: Any) -> None: ...

class Delegate(DoCtrlBase):
    def __init__(self) -> None: ...

class Pass(DoCtrlBase):
    def __init__(self) -> None: ...

class Transfer(DoCtrlBase):
    continuation: K
    value: Any
    def __init__(self, continuation: K, value: Any) -> None: ...

class ResumeContinuation(DoCtrlBase):
    continuation: K
    value: Any
    def __init__(self, continuation: K, value: Any) -> None: ...

class CreateContinuation(DoCtrlBase):
    program: DoExpr[Any] | EffectBase
    handlers: Sequence[Any]
    def __init__(self, program: DoExpr[Any] | EffectBase, handlers: Sequence[Any]) -> None: ...

class GetTraceback(DoCtrlBase):
    continuation: K
    def __init__(self, continuation: K) -> None: ...

class GetContinuation(DoCtrlBase):
    def __init__(self) -> None: ...

class GetHandlers(DoCtrlBase):
    def __init__(self) -> None: ...

class GetCallStack(DoCtrlBase):
    def __init__(self) -> None: ...

class AsyncEscape(DoCtrlBase):
    action: Callable[..., Awaitable[Any]]
    def __init__(self, action: Callable[..., Awaitable[Any]]) -> None: ...

HandlerLike: TypeAlias = Any

def WithHandler(
    handler: HandlerLike,
    expr: Any,
    *,
    types: Iterable[type[Any]] | None = None,
) -> Any: ...
def WithIntercept(
    f: HandlerLike,
    expr: Any,
    types: Iterable[type[Any]] | None = None,
    mode: Literal["include", "exclude"] = "include",
    meta: dict[str, Any] | None = None,
) -> Any: ...

class PyGet(EffectBase):
    key: str
    def __init__(self, key: str) -> None: ...

class PyPut(EffectBase):
    key: str
    value: Any
    def __init__(self, key: str, value: Any) -> None: ...

class PyModify(EffectBase):
    key: str
    func: Callable[[Any], Any]
    def __init__(self, key: str, func: Callable[[Any], Any]) -> None: ...

class PyAsk(EffectBase):
    key: Any
    def __init__(self, key: Any) -> None: ...

class PyLocal(EffectBase):
    env_update: Any
    sub_program: DoExpr[Any] | EffectBase
    def __init__(self, env_update: Any, sub_program: DoExpr[Any] | EffectBase) -> None: ...

class PyTell(EffectBase):
    message: Any
    def __init__(self, message: Any) -> None: ...

class SpawnEffect(EffectBase):
    program: DoExpr[Any] | EffectBase
    options: Any
    handlers: Sequence[Any]
    priority: Any
    def __init__(
        self,
        program: DoExpr[Any] | EffectBase,
        options: Any | None = None,
        handlers: Sequence[Any] | None = None,
        priority: Any | None = None,
    ) -> None: ...

class GatherEffect(EffectBase):
    items: Any
    _partial_results: Any
    def __init__(self, items: Any, _partial_results: Any | None = None) -> None: ...

class RaceEffect(EffectBase):
    futures: Any
    def __init__(self, futures: Any) -> None: ...

class CreatePromiseEffect(EffectBase):
    def __init__(self) -> None: ...

class CompletePromiseEffect(EffectBase):
    promise: Any
    value: Any
    def __init__(self, promise: Any, value: Any) -> None: ...

class FailPromiseEffect(EffectBase):
    promise: Any
    error: BaseException | Any
    def __init__(self, promise: Any, error: BaseException | Any) -> None: ...

class CreateExternalPromiseEffect(EffectBase):
    def __init__(self) -> None: ...

class ExternalPromise:
    id: int
    _completion_queue: Any
    @property
    def future(self) -> Any: ...
    def complete(self, value: Any) -> None: ...
    def fail(self, error: BaseException | Any) -> None: ...

class PyCancelEffect(EffectBase):
    task: Any
    def __init__(self, task: Any) -> None: ...

class _SchedulerTaskCompleted(EffectBase):
    task: Any
    task_id: Any
    handle_id: Any
    result: Any
    def __init__(
        self,
        *,
        task: Any | None = None,
        task_id: Any | None = None,
        handle_id: Any | None = None,
        result: Any | None = None,
    ) -> None: ...

class TaskCancelledError(RuntimeError): ...

class Semaphore:
    id: int

class CreateSemaphoreEffect(EffectBase):
    permits: int
    def __init__(self, permits: int) -> None: ...

class AcquireSemaphoreEffect(EffectBase):
    semaphore: Semaphore
    def __init__(self, semaphore: Semaphore) -> None: ...

class ReleaseSemaphoreEffect(EffectBase):
    semaphore: Semaphore
    def __init__(self, semaphore: Semaphore) -> None: ...

class PythonAsyncioAwaitEffect(EffectBase):
    awaitable: Awaitable[Any] | Any
    def __init__(self, awaitable: Awaitable[Any] | Any) -> None: ...

class ResultSafeEffect(EffectBase):
    sub_program: DoExpr[Any] | EffectBase
    def __init__(self, sub_program: DoExpr[Any] | EffectBase) -> None: ...

class ProgramCallStackEffect(EffectBase):
    def __init__(self) -> None: ...

class ProgramCallFrameEffect(EffectBase):
    depth: int
    def __init__(self, depth: int = 0) -> None: ...

class GetExecutionContext(EffectBase):
    def __init__(self) -> None: ...

class ExecutionContext:
    entries: list[Any]
    active_chain: Any | None
    def __init__(self) -> None: ...
    def add(self, entry: Any) -> None: ...
    def set_active_chain(self, active_chain: Any | None) -> None: ...

def run(
    program: Any,
    env: dict[Any, Any] | None = None,
    store: dict[str, Any] | None = None,
    trace: bool = False,
) -> RunResult[Any]: ...
def async_run(
    program: Any,
    env: dict[Any, Any] | None = None,
    store: dict[str, Any] | None = None,
    trace: bool = False,
) -> Coroutine[Any, Any, RunResult[Any]]: ...

state: RustHandler
reader: RustHandler
writer: RustHandler
result_safe: RustHandler
scheduler: RustHandler
lazy_ask: RustHandler
await_handler: RustHandler
sync_await_handler: RustHandler

TAG_PURE: int
TAG_MAP: int
TAG_FLAT_MAP: int
TAG_WITH_HANDLER: int
TAG_PERFORM: int
TAG_RESUME: int
TAG_TRANSFER: int
TAG_DELEGATE: int
TAG_PASS: int
TAG_GET_CONTINUATION: int
TAG_GET_HANDLERS: int
TAG_GET_TRACEBACK: int
TAG_WITH_INTERCEPT: int
TAG_DISCONTINUE: int
TAG_GET_CALL_STACK: int
TAG_EVAL: int
TAG_EVAL_IN_SCOPE: int
TAG_APPLY: int
TAG_EXPAND: int
TAG_CREATE_CONTINUATION: int
TAG_RESUME_CONTINUATION: int
TAG_ASYNC_ESCAPE: int
TAG_EFFECT: int
TAG_UNKNOWN: int

PythonAsyncSyntaxEscape = AsyncEscape
PySpawn = SpawnEffect
PyGather = GatherEffect
PyRace = RaceEffect
PyCreatePromise = CreatePromiseEffect
PyCompletePromise = CompletePromiseEffect
PyFailPromise = FailPromiseEffect
PyCreateExternalPromise = CreateExternalPromiseEffect
TaskCancelEffect = PyCancelEffect
PyTaskCompleted = _SchedulerTaskCompleted

class UnhandledEffectError(TypeError): ...
class NoMatchingHandlerError(UnhandledEffectError): ...

doeff_vm: ModuleType

__all__: list[str]
