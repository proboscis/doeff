# SPEC-CESK-002: RuntimeResult Protocol

## Status: Draft

## Summary

This spec defines the `RuntimeResult` protocol - the standard return type from runtime execution. It wraps the computation result with full debugging context including three complementary stack trace views.

## Design Goals

1. **Result wrapping** - Users can handle Ok/Err AND access side-effect results (logs, state)
2. **Full debuggability** - Three stack trace views for comprehensive error diagnosis
3. **Protocol-based** - Runtimes implement the protocol, not a concrete class
4. **Optional fields** - Graph capture and other heavy features are opt-in

## RuntimeResult Protocol

```python
from typing import Protocol, Generic, TypeVar, Any, runtime_checkable
from doeff.types import Result, Ok, Err

T = TypeVar('T', covariant=True)

@runtime_checkable
class RuntimeResult(Protocol[T]):
    """Protocol for runtime execution results.
    
    Wraps the computation outcome with execution context and debugging info.
    All runtimes MUST return an object satisfying this protocol.
    """
    
    # ═══════════════════════════════════════════════════════════════
    # CORE - Required fields
    # ═══════════════════════════════════════════════════════════════
    
    @property
    def result(self) -> Result[T]:
        """The computation outcome: Ok(value) or Err(error)."""
        ...
    
    @property
    def state(self) -> dict[str, Any]:
        """Final state after execution (from Put/Modify effects)."""
        ...
    
    @property
    def log(self) -> list[Any]:
        """Accumulated messages from Tell effects."""
        ...
    
    # ═══════════════════════════════════════════════════════════════
    # STACK TRACES - Required for debugging
    # ═══════════════════════════════════════════════════════════════
    
    @property
    def k_stack(self) -> "KStackTrace":
        """CESK continuation stack at termination point.
        
        Shows the frame stack (SafeFrame, LocalFrame, etc.) that was
        active when execution completed or failed.
        """
        ...
    
    @property
    def effect_stack(self) -> "EffectStackTrace":
        """Effect call tree showing which @do functions called which effects.
        
        Hierarchical view: main() -> fetch() -> Ask('key')
        """
        ...
    
    @property
    def python_stack(self) -> "PythonStackTrace":
        """Python source locations where effects were created.
        
        Standard traceback format with file, line, function, code context.
        """
        ...
    
    # ═══════════════════════════════════════════════════════════════
    # OPTIONAL - Heavy/specialized features
    # ═══════════════════════════════════════════════════════════════
    
    @property
    def graph(self) -> "WGraph | None":
        """Computation graph if capture was enabled, else None."""
        ...
    
    @property
    def env(self) -> dict[Any, Any]:
        """Final environment (usually same as initial unless modified)."""
        ...
    
    # ═══════════════════════════════════════════════════════════════
    # CONVENIENCE - Derived from result
    # ═══════════════════════════════════════════════════════════════
    
    @property
    def value(self) -> T:
        """Unwrap Ok value or raise the Err error."""
        ...
    
    def is_ok(self) -> bool:
        """True if result is Ok."""
        ...
    
    def is_err(self) -> bool:
        """True if result is Err."""
        ...
    
    def format(self, *, verbose: bool = False) -> str:
        """Format the result for display with all stack traces."""
        ...
```

## Stack Trace Types

### KStackTrace - Continuation Stack

Shows the CESK K (continuation) stack - what frames were waiting for results.

```python
@dataclass(frozen=True)
class KFrame:
    """Single frame in the continuation stack."""
    frame_type: str          # "SafeFrame", "LocalFrame", etc.
    description: str         # Human-readable details
    source_location: SourceLocation | None  # Where this frame was created

@dataclass(frozen=True)
class KStackTrace:
    """CESK continuation stack snapshot."""
    frames: tuple[KFrame, ...]
    
    def format(self) -> str:
        """Format as readable stack.
        
        Example output:
        
        Continuation Stack (K):
          [0] SafeFrame           - will catch errors
          [1] LocalFrame          - env={'config': 'prod'}
          [2] InterceptFrame      - 1 transform(s)
          [3] GatherFrame         - completed 2/3 children
        """
```

**Purpose:** Understanding what control-flow constructs are active. Essential for:
- Knowing if errors will be caught (SafeFrame above = yes)
- Understanding environment scope (LocalFrame)
- Debugging intercept behavior (InterceptFrame)
- Tracking parallel execution (GatherFrame progress)

### EffectStackTrace - Effect Call Tree

Shows which `@do` decorated functions called which effects.

```python
@dataclass(frozen=True)
class EffectCallNode:
    """Node in the effect call tree."""
    name: str                           # Function name or effect name
    is_effect: bool                     # True if this is a leaf effect
    args_repr: str                      # Short repr of arguments
    count: int                          # How many times (for effects)
    children: tuple["EffectCallNode", ...]
    source_location: SourceLocation | None
    is_error_site: bool                 # True if error occurred here

@dataclass(frozen=True)
class EffectStackTrace:
    """Hierarchical view of effects grouped by program call stack."""
    root: EffectCallNode
    
    def format(self) -> str:
        """Format as ASCII tree.
        
        Example output:
        
        Effect Call Tree:
          main()
          └─ fetch_data(url='https://...')
             ├─ Ask('http_client')
             └─ process_items(count=10)
                ├─ Get('cache') x3
                ├─ Put('result')
                └─ Ask('missing')  <-- ERROR
        """
```

**Purpose:** Understanding the logical flow of your program. Shows:
- Which business logic functions were called
- What effects each function performed
- Where in the call tree the error occurred

### PythonStackTrace - Source Locations

Standard Python traceback showing where effects were created in source code.

```python
@dataclass(frozen=True)
class PythonFrame:
    """Single Python stack frame."""
    filename: str
    line: int
    function: str
    code_context: str | None  # The actual line of code

@dataclass(frozen=True)
class PythonStackTrace:
    """Python source locations for effect creation."""
    frames: tuple[PythonFrame, ...]
    
    def format(self) -> str:
        """Format as Python traceback.
        
        Example output:
        
        Python Stack:
          File "app.py", line 42, in main
            result = yield fetch_data(url)
          File "fetcher.py", line 18, in fetch_data
            client = yield Ask('http_client')
          File "fetcher.py", line 25, in fetch_data
            data = yield Ask('missing')
                         ^^^^^^^^^^^^^
        """
```

**Purpose:** Pinpointing exact source locations for IDE navigation and debugging.

## Display Format

When `format()` is called on a RuntimeResult, display all three stacks:

```
═══════════════════════════════════════════════════════════════════════════════
                              RUNTIME RESULT
═══════════════════════════════════════════════════════════════════════════════

Status: Err(KeyError: 'missing_config')

───────────────────────────────────────────────────────────────────────────────
                               ROOT CAUSE
───────────────────────────────────────────────────────────────────────────────
KeyError: 'missing_config'

───────────────────────────────────────────────────────────────────────────────
                             PYTHON STACK
───────────────────────────────────────────────────────────────────────────────
  File "app.py", line 42, in main
    config = yield load_settings()
  File "settings.py", line 15, in load_settings
    value = yield Ask('missing_config')
                  ^^^^^^^^^^^^^^^^^^^^

───────────────────────────────────────────────────────────────────────────────
                           EFFECT CALL TREE
───────────────────────────────────────────────────────────────────────────────
  main()
  └─ load_settings()
     ├─ Ask('app_name')
     └─ Ask('missing_config')  <-- ERROR

───────────────────────────────────────────────────────────────────────────────
                         CONTINUATION STACK (K)
───────────────────────────────────────────────────────────────────────────────
  [0] SafeFrame            - will catch this error
  [1] LocalFrame           - env={'debug': True}

───────────────────────────────────────────────────────────────────────────────
                              STATE & LOG
───────────────────────────────────────────────────────────────────────────────
State:
  initialized: True
  step: 3

Log:
  [0] "Starting application"
  [1] "Loading settings..."

═══════════════════════════════════════════════════════════════════════════════
```

### Condensed Format (verbose=False)

For quick debugging, show only the most relevant info:

```
Err(KeyError: 'missing_config')

Root Cause: KeyError: 'missing_config'

  File "settings.py", line 15, in load_settings
    value = yield Ask('missing_config')

Effect path: main() -> load_settings() -> Ask('missing_config')

K: [SafeFrame, LocalFrame]
```

## Implementation Notes

### Collecting Stack Information

During execution, the runtime must track:

1. **K-Stack:** Already maintained by CESK machine - just snapshot on termination
2. **Effect Stack:** Build incrementally using `ProgramCallStack` effect or similar mechanism
3. **Python Stack:** Capture at effect creation time via `EffectCreationContext`

### Data Flow

```
Program Execution
       │
       ▼
┌─────────────────┐
│  Effect Yield   │──────► Capture Python frame (inspect.currentframe)
└─────────────────┘
       │
       ▼
┌─────────────────┐
│   Handler       │──────► K-stack is implicit in CESK state
└─────────────────┘
       │
       ▼
┌─────────────────┐
│  Termination    │──────► Build RuntimeResult with all traces
└─────────────────┘
```

### Effect Observations

Effect observations are an **implementation detail** for building the EffectStackTrace. They are NOT exposed in the RuntimeResult protocol.

```python
# Internal - used during execution
@dataclass
class EffectObservation:
    effect_type: str
    key: Any | None
    call_stack: list[CallFrame]
    python_frame: PythonFrame
    timestamp: float  # optional, for profiling

# These are aggregated into EffectStackTrace at termination
# Users never see individual EffectObservation objects
```

## Success Criteria

1. All runtimes return objects satisfying `RuntimeResult` protocol
2. On error, all three stack traces provide useful debugging info
3. `format()` produces readable output for terminal/IDE
4. Stack trace collection has minimal performance overhead
5. Optional fields (graph) are truly optional - None when not enabled

## Related Specs

- SPEC-CESK-001: Separation of Concerns (Runtime/Handler/Frame architecture)
- SPEC-EFF-004: Control Effects (Safe, Intercept - affect K-stack)
- SPEC-EFF-100: Effect Combinations (composition behavior)

## Open Questions

1. Should `format()` support different output formats (plain, ANSI color, HTML)?
2. Should we add timing information to EffectStackTrace for profiling?
3. Maximum depth/size limits for stack traces to prevent memory issues?
