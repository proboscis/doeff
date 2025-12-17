from __future__ import annotations

import traceback
from collections.abc import Callable, Generator, Hashable, Iterator
from typing import TYPE_CHECKING, Any, Generic, Protocol, TypeAlias, TypeVar, runtime_checkable

from doeff._vendor import (
    NOTHING,
    Err,
    FrozenDict,
    Maybe,
    Nothing,
    Ok,
    Result,
    Some,
    TraceError,
    WGraph,
    WNode,
    WStep,
    trace_err,
)
from doeff.program import Program, ProgramBase
from doeff.utils import BoundedLog

if TYPE_CHECKING:
    from phart.styles import NodeStyle

T = TypeVar("T")
E = TypeVar("E", bound="EffectBase")

EnvKey: TypeAlias = Hashable

DEFAULT_REPR_LIMIT: int
REPR_LIMIT_KEY: str

_TRACEBACK_ATTR: str

def _truncate_repr(obj: object, limit: int | None) -> str: ...

class EffectCreationContext:
    filename: str
    line: int
    function: str
    code: str | None
    stack_trace: list[dict[str, Any]]
    frame_info: Any

    def format_location(self) -> str: ...
    def format_full(self) -> str: ...
    def build_traceback(self) -> str: ...
    def without_frames(self) -> EffectCreationContext: ...

class CapturedTraceback:
    traceback: traceback.TracebackException

    def _raw_lines(self) -> list[str]: ...
    def _sanitize_lines(self) -> list[str]: ...
    def lines(self, *, condensed: bool = ..., max_lines: int = ...) -> list[str]: ...
    def format(self, *, condensed: bool = ..., max_lines: int = ...) -> str: ...

def capture_traceback(exc: BaseException) -> CapturedTraceback: ...
def get_captured_traceback(exc: BaseException) -> CapturedTraceback | None: ...
@runtime_checkable
class Effect(Protocol):
    created_at: EffectCreationContext | None

    def intercept(self, transform: Callable[[Effect], Effect | Program]) -> Effect: ...
    def with_created_at(self: E, created_at: EffectCreationContext | None) -> E: ...

class EffectFailureError(Exception):
    effect: Effect
    cause: BaseException
    runtime_traceback: CapturedTraceback | None
    creation_context: EffectCreationContext | None

    def __str__(self) -> str: ...
    def __post_init__(self) -> None: ...

EffectFailure = EffectFailureError

class EffectBase(ProgramBase[Any]):
    created_at: EffectCreationContext | None

    def intercept(self: E, transform: Callable[[Effect], Effect | Program]) -> E: ...
    def with_created_at(self: E, created_at: EffectCreationContext | None) -> E: ...
    def to_generator(self) -> Generator[Effect | Program, Any, Any]: ...

EffectGenerator: TypeAlias = Generator[Effect | Program, Any, T]
ProgramGenerator: TypeAlias = Generator[Effect, Any, T]

class CallFrame:
    kleisli: Any
    function_name: str
    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    depth: int
    created_at: EffectCreationContext | None

class EffectObservation:
    effect_type: str
    key: EnvKey | None
    context: EffectCreationContext | None
    call_stack_snapshot: tuple[CallFrame, ...]

class ExecutionContext:
    env: dict[Any, Any]
    state: dict[str, Any]
    log: BoundedLog
    graph: WGraph
    io_allowed: bool
    cache: dict[str, Any]
    effect_observations: list[EffectObservation]
    program_call_stack: list[CallFrame]

    def __post_init__(self) -> None: ...
    def copy(self) -> ExecutionContext: ...
    def with_env_update(self, updates: dict[Any, Any]) -> ExecutionContext: ...

class EffectFailureInfo:
    effect: Effect
    creation_context: EffectCreationContext | None
    cause: BaseException | None
    runtime_trace: CapturedTraceback | None
    cause_trace: CapturedTraceback | None

class ExceptionFailureInfo:
    exception: BaseException
    trace: CapturedTraceback | None

FailureEntry: TypeAlias = EffectFailureInfo | ExceptionFailureInfo

class RunFailureDetails:
    entries: tuple[FailureEntry, ...]

    @classmethod
    def from_error(cls, error: Any) -> RunFailureDetails | None: ...

class ListenResult:
    value: Any
    log: BoundedLog

    def __iter__(self) -> Iterator[Any]: ...

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
    def env(self) -> dict[Any, Any]: ...
    @property
    def state(self) -> dict[str, Any]: ...
    @property
    def shared_state(self) -> dict[str, Any]: ...
    @property
    def log(self) -> list[Any]: ...
    @property
    def graph(self) -> WGraph: ...
    @property
    def effect_observations(self) -> list[EffectObservation]: ...
    def format_error(self, *, condensed: bool = ...) -> str: ...
    @property
    def formatted_error(self) -> str: ...
    def __repr__(self) -> str: ...
    def display(self, verbose: bool = ..., indent: int = ...) -> str: ...
    def visualize_graph_ascii(
        self,
        *,
        node_style: NodeStyle | str = ...,
        node_spacing: int = ...,
        margin: int = ...,
        layer_spacing: int = ...,
        show_arrows: bool = ...,
        use_ascii: bool | None = ...,
        max_value_length: int = ...,
        include_ops: bool = ...,
        custom_decorators: dict[WNode | str, tuple[str, str]] | None = ...,
    ) -> str: ...

def _intercept_value(value: Any, transform: Callable[[Effect], Effect | Program]) -> Any: ...

__all__ = [
    "NOTHING",
    "Effect",
    "EffectFailure",
    "EffectFailureError",
    "EffectGenerator",
    "EffectObservation",
    "EnvKey",
    "Err",
    "ExecutionContext",
    "FrozenDict",
    "ListenResult",
    "Maybe",
    "Nothing",
    "Ok",
    "Program",
    "ProgramBase",
    "Result",
    "RunResult",
    "Some",
    "TraceError",
    "WGraph",
    "WNode",
    "WStep",
    "trace_err",
]
