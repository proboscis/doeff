from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from typing import Any, Generic, Protocol, TypeVar

_T = TypeVar("_T")


class CallFrame(Protocol):
    source_file: str
    source_line: int
    function_name: str


class DoExpr:
    def __init__(self, *_args: Any, **_kwargs: Any) -> None: ...
    def to_generator(self) -> Any: ...


class EffectBase:
    tag: int
    def __init__(self, *_args: Any, **_kwargs: Any) -> None: ...


class DoCtrlBase(DoExpr):
    tag: int


class K:
    def __repr__(self) -> str: ...


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


class Err(Generic[_T]):
    error: _T
    captured_traceback: Any
    def __init__(self, error: _T, captured_traceback: Any | None = None) -> None: ...
    def is_ok(self) -> bool: ...
    def is_err(self) -> bool: ...


class DoeffTracebackData:
    entries: Any
    active_chain: Any
    def __init__(self, entries: Any, active_chain: Any | None = None) -> None: ...


class RunResult(Generic[_T]):
    traceback_data: DoeffTracebackData | None
    raw_store: dict[str, Any]
    log: list[Any]
    trace: list[Any]
    @property
    def value(self) -> _T: ...
    @property
    def error(self) -> BaseException: ...
    @property
    def result(self) -> Ok[_T] | Err[Any]: ...
    def is_ok(self) -> bool: ...
    def is_err(self) -> bool: ...
    def display(self, verbose: bool = False) -> str: ...


class DoeffGeneratorFn:
    callable: Any
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


class PyKleisli:
    def __init__(
        self,
        func: Callable[..., Any],
        name: str,
        file: str | None = None,
        line: int | None = None,
    ) -> None: ...
    def __call__(self, *args: Any, **kwargs: Any) -> Any: ...
    def __rshift__(self, binder: Any) -> Any: ...
    def partial(self, *args: Any, **kwargs: Any) -> Any: ...
    def and_then_k(self, binder: Any) -> Any: ...
    def fmap(self, mapper: Any) -> Any: ...


class RustHandler:
    def __repr__(self) -> str: ...


class WithHandler(DoCtrlBase):
    handler: Any
    expr: Any
    types: tuple[type[Any], ...] | None
    return_clause: Callable[..., Any] | None
    handler_name: str | None
    handler_file: str | None
    handler_line: int | None
    def __init__(
        self,
        handler: Any,
        expr: Any,
        return_clause: Callable[..., Any] | None = None,
        *,
        types: Iterable[type[Any]] | None = None,
        handler_name: str | None = None,
        handler_file: str | None = None,
        handler_line: int | None = None,
    ) -> None: ...


class WithIntercept(DoCtrlBase):
    f: Any
    expr: Any
    types: tuple[type[Any], ...] | None
    mode: str
    meta: dict[str, Any] | None
    def __init__(
        self,
        f: Any,
        expr: Any,
        types: Iterable[type[Any]] | None = None,
        mode: str = "include",
        meta: dict[str, Any] | None = None,
    ) -> None: ...


class Finally(DoCtrlBase):
    cleanup: Any
    def __init__(self, cleanup: Any) -> None: ...


class Pure(DoCtrlBase):
    value: Any
    def __init__(self, value: Any) -> None: ...


class Apply(DoCtrlBase):
    f: Any
    args: Any
    kwargs: dict[str, Any]
    meta: dict[str, Any] | None
    evaluate_result: bool
    def __init__(
        self,
        f: Any,
        args: Any,
        kwargs: dict[str, Any],
        meta: dict[str, Any] | None = None,
        evaluate_result: bool = True,
    ) -> None: ...


class Expand(DoCtrlBase):
    factory: Any
    args: Any
    kwargs: dict[str, Any]
    meta: dict[str, Any] | None
    def __init__(
        self,
        factory: Any,
        args: Any,
        kwargs: dict[str, Any],
        meta: dict[str, Any] | None = None,
    ) -> None: ...


class Map(DoCtrlBase):
    source: Any
    mapper: Callable[..., Any]
    mapper_meta: dict[str, Any]
    def __init__(
        self,
        source: Any,
        mapper: Callable[..., Any],
        mapper_meta: dict[str, Any] | None = None,
    ) -> None: ...


class FlatMap(DoCtrlBase):
    source: Any
    binder: Callable[..., Any]
    binder_meta: dict[str, Any]
    def __init__(
        self,
        source: Any,
        binder: Callable[..., Any],
        binder_meta: dict[str, Any] | None = None,
    ) -> None: ...


class Eval(DoCtrlBase):
    expr: Any
    def __init__(self, expr: Any) -> None: ...


class EvalInScope(DoCtrlBase):
    expr: Any
    scope: K
    def __init__(self, expr: Any, scope: K) -> None: ...


class Perform(DoCtrlBase):
    effect: Any
    def __init__(self, effect: EffectBase) -> None: ...


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
    program: Any
    handlers: Any
    def __init__(self, program: Any, handlers: Any) -> None: ...


class GetContinuation(DoCtrlBase):
    def __init__(self) -> None: ...


class GetHandlers(DoCtrlBase):
    def __init__(self) -> None: ...


class GetTraceback(DoCtrlBase):
    continuation: K
    def __init__(self, continuation: K) -> None: ...


class GetCallStack(DoCtrlBase):
    def __init__(self) -> None: ...


class PythonAsyncSyntaxEscape(DoCtrlBase):
    action: Callable[..., Awaitable[Any]]
    def __init__(self, action: Callable[..., Awaitable[Any]]) -> None: ...


class _NestingStep:
    def to_generator(self) -> Any: ...


class _NestingGenerator:
    def __iter__(self) -> _NestingGenerator: ...
    def __next__(self) -> Any: ...
    def send(self, value: Any) -> Any: ...
    def throw(self, exc: BaseException) -> Any: ...


class GetExecutionContext(EffectBase):
    def __init__(self) -> None: ...


class ExecutionContext:
    entries: list[Any]
    active_chain: Any | None
    def __init__(self) -> None: ...
    def add(self, entry: Any) -> None: ...
    def set_active_chain(self, active_chain: Any | None) -> None: ...


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
    sub_program: Any
    def __init__(self, env_update: Any, sub_program: Any) -> None: ...


class PyTell(EffectBase):
    message: Any
    def __init__(self, message: Any) -> None: ...


class SpawnEffect(EffectBase):
    program: Any
    options: Any
    handlers: Any
    store_mode: Any
    priority: Any
    def __init__(
        self,
        program: Any,
        options: Any | None = None,
        handlers: Any | None = None,
        store_mode: Any | None = None,
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
    error: Any
    def __init__(self, promise: Any, error: Any) -> None: ...


class CreateExternalPromiseEffect(EffectBase):
    def __init__(self) -> None: ...


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


class CreateSemaphoreEffect(EffectBase):
    permits: int
    def __init__(self, permits: int) -> None: ...


class AcquireSemaphoreEffect(EffectBase):
    semaphore: Any
    def __init__(self, semaphore: Any) -> None: ...


class ReleaseSemaphoreEffect(EffectBase):
    semaphore: Any
    def __init__(self, semaphore: Any) -> None: ...


class PythonAsyncioAwaitEffect(EffectBase):
    awaitable: Awaitable[Any]
    def __init__(self, awaitable: Awaitable[Any]) -> None: ...


class ResultSafeEffect(EffectBase):
    sub_program: Any
    def __init__(self, sub_program: Any) -> None: ...


class ProgramCallStackEffect(EffectBase):
    def __init__(self) -> None: ...


class ProgramCallFrameEffect(EffectBase):
    depth: int
    def __init__(self, depth: int = 0) -> None: ...


class UnhandledEffectError(TypeError): ...
class NoMatchingHandlerError(UnhandledEffectError): ...
class TaskCancelledError(RuntimeError): ...


class PyVM:
    def __init__(self) -> None: ...
    def run(self, program: Any) -> Any: ...
    def run_with_result(self, program: Any) -> RunResult[Any]: ...
    def state_items(self) -> dict[str, Any]: ...
    def logs(self) -> list[Any]: ...
    def put_state(self, key: str, value: Any) -> None: ...
    def put_env(self, key: Any, value: Any) -> None: ...
    def env_items(self) -> dict[Any, Any]: ...
    def enable_debug(self, level: str) -> None: ...
    def py_store(self) -> dict[str, Any] | None: ...
    def set_store(self, key: str, value: Any) -> None: ...
    def get_store(self, key: str) -> Any: ...
    def build_run_result(self, value: Any) -> RunResult[Any]: ...
    def build_run_result_error(
        self, error: BaseException, traceback_data: Any | None = None
    ) -> RunResult[Any]: ...
    def start_program(self, program: Any) -> None: ...
    def step_once(self) -> tuple[Any, ...]: ...
    def feed_async_result(self, value: Any) -> None: ...
    def feed_async_error(self, error_value: BaseException) -> None: ...


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
) -> Awaitable[RunResult[Any]]: ...


def _coerce_handler(handler: Any, *, api_name: str, role: str) -> Any: ...
def _notify_semaphore_handle_dropped(state_id: int, semaphore_id: int) -> None: ...
def _debug_scheduler_semaphore_count(state_id: int) -> int | None: ...


DoThunkBase: type[Any] | None
ResultOk: type[Ok[Any]]
ResultErr: type[Err[Any]]

state: RustHandler
reader: RustHandler
writer: RustHandler
result_safe: RustHandler
scheduler: RustHandler
lazy_ask: RustHandler
await_handler: RustHandler

PySpawn: type[SpawnEffect]
PyGather: type[GatherEffect]
PyRace: type[RaceEffect]
PyCreatePromise: type[CreatePromiseEffect]
PyCompletePromise: type[CompletePromiseEffect]
PyFailPromise: type[FailPromiseEffect]
PyCreateExternalPromise: type[CreateExternalPromiseEffect]
TaskCancelEffect: type[PyCancelEffect]
PyTaskCompleted: type[_SchedulerTaskCompleted]

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
TAG_FINALLY: int
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

__all__: list[str]
