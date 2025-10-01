T: Any
U: Any
_TRACEBACK_ATTR: Any
E: Any
ProgramGenerator: Any
FailureEntry: Any
__all__: Any

def capture_traceback(exc: BaseException) -> CapturedTraceback: ...

def get_captured_traceback(exc: BaseException) -> CapturedTraceback | None: ...

def _intercept_value(value: Any, transform: Callable[Any, Effect | Program]) -> Any: ...

def _wrap_callable(func: Callable[Any, Any], transform: Callable[Any, Effect | Program]) -> Any: ...

class EffectFailure:
    effect: Effect
    cause: BaseException
    runtime_traceback: CapturedTraceback | None
    creation_context: EffectCreationContext | None
    def __str__(self) -> str: ...
    def __post_init__(self) -> Any: ...

class Effect:
    created_at: EffectCreationContext | None
    def intercept(self, transform: Callable[Any, Effect | Program]) -> Effect: ...
    def with_created_at(self: E, created_at: EffectCreationContext | None) -> E: ...

class EffectBase:
    created_at: EffectCreationContext | None
    def intercept(self: E, transform: Callable[Any, Effect | Program]) -> E: ...
    def with_created_at(self: E, created_at: EffectCreationContext | None) -> E: ...

class ExecutionContext:
    env: dict[str, Any]
    state: dict[str, Any]
    log: list[Any]
    graph: WGraph
    io_allowed: bool
    cache: dict[str, Any]
    effect_observations: list[EffectObservation]
    def copy(self) -> ExecutionContext: ...
    def with_env_update(self, updates: dict[str, Any]) -> ExecutionContext: ...

class EffectObservation:
    effect_type: str
    key: str | None
    context: EffectCreationContext | None

class EffectFailureInfo:
    effect: Effect
    creation_context: EffectCreationContext | None
    cause: BaseException | None
    runtime_trace: CapturedTraceback | None
    cause_trace: CapturedTraceback | None

class _StatusSection:
    def render(self) -> list[str]: ...

class _SharedStateSection:
    def render(self) -> list[str]: ...

class ListenResult:
    value: Any
    log: list[Any]
    def __iter__(self) -> Any: ...

class _BaseSection:
    context: RunResultDisplayContext
    def indent(self, level: int, text: str) -> str: ...
    def format_value(self, value: Any, max_length: int = ...) -> str: ...

class RunResultDisplayContext:
    run_result: RunResult[Any]
    verbose: bool
    indent_unit: str
    failure_details: RunFailureDetails | None
    def indent(self, level: int, text: str) -> str: ...

class _HeaderSection:
    def render(self) -> list[str]: ...

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

class RunFailureDetails:
    entries: tuple[FailureEntry, Any]
    def from_error(cls, error: Any) -> RunFailureDetails | None: ...

class _StateSection:
    def render(self) -> list[str]: ...

class _GraphSection:
    def render(self) -> list[str]: ...

class ExceptionFailureInfo:
    exception: BaseException
    trace: CapturedTraceback | None

class _LogSection:
    def render(self) -> list[str]: ...

class RunResultDisplayRenderer:
    context: RunResultDisplayContext
    def render(self) -> str: ...

class _ErrorSection:
    def render(self) -> list[str]: ...
    def _render_effect_entry(self, idx: int, entry: EffectFailureInfo, is_primary: bool) -> list[str]: ...
    def _render_exception_entry(self, idx: int, entry: ExceptionFailureInfo) -> list[str]: ...
    def _render_trace(self, trace: CapturedTraceback, label: str) -> list[str]: ...

class _EnvironmentSection:
    def render(self) -> list[str]: ...

class _SummarySection:
    def render(self) -> list[str]: ...

class CapturedTraceback:
    traceback: traceback.TracebackException
    def _raw_lines(self) -> list[str]: ...
    def _sanitize_lines(self) -> list[str]: ...
    def lines(self, condensed: bool = ..., max_lines: int = ...) -> list[str]: ...
    def format(self, condensed: bool = ..., max_lines: int = ...) -> str: ...

class RunResult:
    context: ExecutionContext
    result: Result[T]
    def value(self) -> T: ...
    def is_ok(self) -> bool: ...
    def is_err(self) -> bool: ...
    def env(self) -> dict[str, Any]: ...
    def state(self) -> dict[str, Any]: ...
    def shared_state(self) -> dict[str, Any]: ...
    def log(self) -> list[Any]: ...
    def graph(self) -> WGraph: ...
    def effect_observations(self) -> list[EffectObservation]: ...
    def _failure_details(self) -> RunFailureDetails | None: ...
    def _failure_summary(self) -> str: ...
    def format_error(self, condensed: bool = ...) -> str: ...
    def formatted_error(self) -> str: ...
    def __repr__(self) -> str: ...
    def display(self, verbose: bool = ..., indent: int = ...) -> str: ...
    def visualize_graph_ascii(self, node_style: NodeStyle | str = ..., node_spacing: int = ..., margin: int = ..., layer_spacing: int = ..., show_arrows: bool = ..., use_ascii: bool | None = ..., max_value_length: int = ..., include_ops: bool = ..., custom_decorators: dict[WNode | str, tuple[str, str]] | None = ...) -> str: ...
    def _format_value(self, value: Any, indent: int, max_length: int = ...) -> str: ...

class _EffectUsageSection:
    def render(self) -> list[str]: ...
    def _render_effect_type_stats(
        self,
        lines: list[str],
        effect_type: str,
        groups: dict[str | None, list[EffectObservation]],
    ) -> None: ...
    def _render_key_stats(
        self,
        lines: list[str],
        key: str | None,
        obs_list: list[EffectObservation],
    ) -> None: ...

class _CompactKeysSection:
    def render(self) -> list[str]: ...


# Additional symbols:

# Additional symbols:

# Additional symbols:

# Additional symbols:

# Additional symbols:

# Additional symbols:

# Additional symbols:

# Additional symbols:

# Additional symbols:

# Additional symbols:

# Additional symbols:

# Additional symbols:

# Additional symbols:

# Additional symbols:

# Additional symbols:

# Additional symbols:

# Additional symbols:

# Additional symbols:

# Additional symbols:

# Additional symbols:

# Additional symbols:

# Additional symbols:

# Additional symbols:

# Additional symbols:

# Additional symbols:

# Additional symbols:

# Additional symbols:

# Additional symbols:

# Additional symbols:

# Additional symbols:

# Additional symbols:

# Additional symbols:

# Additional symbols:

# Additional symbols:

# Additional symbols:

# Additional symbols:
