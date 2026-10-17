# Class Architecture: Doeff CLI Enhancement

> **Status**: ✅ IMPLEMENTED - This document describes the original design. Implementation matches this architecture closely.
> See [IMPLEMENTATION_STATUS.md](./IMPLEMENTATION_STATUS.md) for actual code locations.

## Design Principles

### 1. SOLID Principles
- **Single Responsibility**: Each class has one clear purpose
- **Open/Closed**: Extensible through composition, not modification
- **Liskov Substitution**: Subtypes can replace supertypes without breaking behavior
- **Interface Segregation**: Small, focused protocols
- **Dependency Inversion**: Depend on abstractions (Protocols), not concrete implementations

### 2. Composition over Inheritance
- Use Protocol-based composition for flexibility
- Minimize class hierarchies
- Favor injected dependencies

### 3. Testability
- All components have clear interfaces
- Easy to mock/stub for testing
- Pure functions where possible

---

## Layer 1: Indexer Enhancement (Rust + PyO3)

### Purpose
Extend doeff-indexer with Python API and new discovery capabilities

### Components

#### 1.1 SymbolInfo (Rust → Python)
```rust
#[pyclass]
#[derive(Debug, Clone)]
pub struct SymbolInfo {
    #[pyo3(get)]
    pub name: String,

    #[pyo3(get)]
    pub module_path: String,

    #[pyo3(get)]
    pub full_path: String,  // e.g., "some.module.a:function_name"

    #[pyo3(get)]
    pub symbol_type: String,  // "function" | "variable"

    #[pyo3(get)]
    pub tags: Vec<String>,  // ["doeff", "interpreter", "default"]

    #[pyo3(get)]
    pub line_number: usize,

    #[pyo3(get)]
    pub file_path: String,
}
```

**Responsibilities**:
- Immutable data structure
- Exposed to Python via PyO3
- Represents a discovered symbol

**Testing**:
- Rust unit tests for construction
- Python integration tests for access

---

#### 1.2 IndexerCore (Rust)
```rust
pub struct IndexerCore {
    root_path: PathBuf,
    cache: Option<IndexCache>,  // Future: caching
}

impl IndexerCore {
    pub fn new(root_path: PathBuf) -> Result<Self>;

    pub fn index_module(&self, module_path: &str) -> Result<Vec<SymbolInfo>>;

    pub fn find_symbols(
        &self,
        tags: &[String],
        symbol_type: Option<&str>,
        module_filter: Option<&str>
    ) -> Result<Vec<SymbolInfo>>;

    pub fn get_module_hierarchy(&self, target_module: &str) -> Result<Vec<String>>;
}
```

**Responsibilities**:
- Core indexing logic
- File system traversal
- AST parsing
- Symbol categorization
- Module path resolution

**Dependencies**:
- rustpython-parser for AST
- walkdir for file traversal
- Existing doeff-indexer logic

**Testing**:
- Unit tests with temp directories
- Test fixtures with Python code
- Property-based tests for module hierarchy

---

#### 1.3 Indexer (PyO3 Python Wrapper)
```rust
#[pyclass]
pub struct Indexer {
    core: Arc<Mutex<IndexerCore>>,
}

#[pymethods]
impl Indexer {
    #[staticmethod]
    pub fn for_module(module_path: &str) -> PyResult<Self> {
        // Parse module path, create IndexerCore
    }

    pub fn find_symbols(
        &self,
        tags: Vec<String>,
        symbol_type: Option<String>,
        module: Option<String>
    ) -> PyResult<Vec<SymbolInfo>> {
        // Call core.find_symbols()
    }

    pub fn get_module_hierarchy(&self) -> PyResult<Vec<String>> {
        // Return [some, some.module, some.module.a, ...]
    }

    pub fn find_in_module(&self, module: &str, tags: Vec<String>) -> PyResult<Vec<SymbolInfo>> {
        // Find symbols in specific module
    }
}
```

**Responsibilities**:
- Python API facade
- Error conversion (Rust → Python)
- Thread-safe access to IndexerCore

**Testing**:
- Python integration tests
- Test from Python via `from doeff_indexer import Indexer`

---

## Layer 2: Discovery Services (Python)

### Purpose
High-level discovery logic using indexer API

### Components

#### 2.1 Protocols (Abstractions)

```python
from typing import Protocol, Optional
from doeff import Program

class InterpreterDiscovery(Protocol):
    """Discovers default interpreters for programs."""

    def find_default_interpreter(self, program_path: str) -> Optional[str]:
        """Find closest default interpreter for program.

        Args:
            program_path: Full module path to program (e.g., "some.module.a.b.c:program")

        Returns:
            Full path to interpreter function or None if not found
        """
        ...

class EnvDiscovery(Protocol):
    """Discovers default environments in module hierarchy."""

    def discover_default_envs(self, program_path: str) -> list[str]:
        """Discover all default envs from root to program module.

        Args:
            program_path: Full module path to program

        Returns:
            List of env paths in order (root → program)
        """
        ...

class EnvMerger(Protocol):
    """Merges multiple environment sources."""

    def merge_envs(self, env_sources: list[str]) -> Program[dict]:
        """Merge multiple env sources into single Program[dict].

        Args:
            env_sources: List of paths to env objects

        Returns:
            Program[dict] with merged values (left-to-right, later overrides)
        """
        ...

class SymbolLoader(Protocol):
    """Loads Python objects from module paths."""

    def load(self, path: str) -> Any:
        """Load object from module path.

        Args:
            path: Module path with optional :symbol syntax
                 e.g., "some.module:function" or "some.module.a"

        Returns:
            Loaded Python object
        """
        ...
```

**Responsibilities**:
- Define interfaces for discovery services
- Enable dependency injection and testing
- Support multiple implementations

---

#### 2.2 StandardSymbolLoader

```python
class StandardSymbolLoader:
    """Standard implementation of SymbolLoader."""

    def load(self, path: str) -> Any:
        """Load using importlib."""
        return _import_symbol(path)  # Existing __main__.py logic

    def _resolve_attr(self, obj: Any, attr_path: str) -> Any:
        """Resolve nested attributes."""
        ...
```

**Responsibilities**:
- Import modules using importlib
- Resolve :symbol syntax
- Handle import errors

**Dependencies**:
- importlib
- Reuses existing _import_symbol logic

**Testing**:
- Test with real modules
- Test error cases (missing modules, attributes)
- Mock importlib for edge cases

---

#### 2.3 IndexerBasedDiscovery

```python
class IndexerBasedDiscovery:
    """Discovers interpreters and envs using doeff-indexer."""

    def __init__(self, indexer_factory: Callable[[str], Indexer] | None = None):
        """
        Args:
            indexer_factory: Optional factory for creating Indexer instances
                            Default: Indexer.for_module
        """
        from doeff_indexer import Indexer
        self.indexer_factory = indexer_factory or Indexer.for_module

    def find_default_interpreter(self, program_path: str) -> Optional[str]:
        """
        Algorithm:
        1. Parse program_path to extract module (e.g., "some.module.a.b.c")
        2. Create indexer for module
        3. Get module hierarchy: [some, some.module, some.module.a, ...]
        4. For each module (reverse order for closest-first):
            - find_symbols(tags=["doeff", "interpreter", "default"], symbol_type="function")
            - If found, return full_path
        5. Return None if not found
        """
        module_path = self._extract_module_path(program_path)
        indexer = self.indexer_factory(module_path)
        hierarchy = indexer.get_module_hierarchy()

        # Search from closest to root
        for module in reversed(hierarchy):
            symbols = indexer.find_in_module(
                module=module,
                tags=["doeff", "interpreter", "default"]
            )
            if symbols:
                # Validate function signature
                valid = self._validate_interpreter(symbols[0])
                if valid:
                    return symbols[0].full_path

        return None

    def discover_default_envs(self, program_path: str) -> list[str]:
        """
        Algorithm:
        1. Parse program_path to extract module
        2. Create indexer for module
        3. Get module hierarchy: [some, some.module, some.module.a, ...]
        4. For each module (root to program order):
            - find_symbols(tags=["doeff", "default"], symbol_type="variable")
            - Collect all matches
        5. Return ordered list
        """
        module_path = self._extract_module_path(program_path)
        indexer = self.indexer_factory(module_path)
        hierarchy = indexer.get_module_hierarchy()

        all_envs = []
        for module in hierarchy:  # Root → program order
            symbols = indexer.find_in_module(
                module=module,
                tags=["doeff", "default"]
            )
            all_envs.extend(s.full_path for s in symbols)

        return all_envs

    def _extract_module_path(self, program_path: str) -> str:
        """Extract module from program:symbol or module.symbol format."""
        if ":" in program_path:
            return program_path.split(":")[0]
        parts = program_path.split(".")
        return ".".join(parts[:-1]) if len(parts) > 1 else program_path

    def _validate_interpreter(self, symbol: SymbolInfo) -> bool:
        """Validate interpreter has correct signature (future enhancement)."""
        return True  # v1: trust indexer, v2: validate signature
```

**Responsibilities**:
- Interpreter discovery via indexer
- Env discovery via indexer
- Module hierarchy traversal
- Validation (basic in v1, enhanced in v2)

**Dependencies**:
- doeff_indexer.Indexer (from Rust)
- SymbolLoader (for validation)

**Testing**:
- Mock indexer_factory for unit tests
- Integration tests with real indexer
- Test module hierarchy edge cases
- Test validation logic

---

#### 2.4 StandardEnvMerger

```python
class StandardEnvMerger:
    """Merges envs using @do composition."""

    def __init__(self, loader: SymbolLoader):
        self.loader = loader

    def merge_envs(self, env_sources: list[str]) -> Program[dict]:
        """
        Merge using @do composition:
        1. Load each env source
        2. Use @do to compose merging logic
        3. Return merged Program[dict]

        No async/await - uses Program composition
        """
        from doeff import Program, do

        if not env_sources:
            return Program.pure({})

        loaded_envs = [self._load_env(path) for path in env_sources]

        @do()
        def merge() -> Program[dict]:
            merged = {}
            for env_source in loaded_envs:
                if isinstance(env_source, Program):
                    # Use yield to extract dict from Program[dict]
                    env_dict = yield env_source
                else:
                    env_dict = env_source
                merged.update(env_dict)  # Later overrides earlier
            return merged

        return merge()

    def _load_env(self, path: str) -> dict | Program[dict]:
        """Load env from path."""
        obj = self.loader.load(path)

        # Handle callables
        if callable(obj) and not isinstance(obj, Program):
            obj = obj()

        # Validate type
        if not isinstance(obj, (dict, Program)):
            raise TypeError(
                f"Env at {path} must be dict or Program[dict], got {type(obj)}"
            )

        return obj
```

**Responsibilities**:
- Load multiple env sources
- Merge using @do composition (no async/await)
- Validate env types
- Handle both dict and Program[dict]

**Dependencies**:
- SymbolLoader for loading
- doeff.Program and @do for composition

**Testing**:
- Test dict merging
- Test Program[dict] merging
- Test mixed merging
- Test left-to-right override
- Mock loader for unit tests

---

## Layer 3: CLI Integration (Python)

### Purpose
Wire discovery services into CLI commands

### Components

#### 3.1 RunContext (Data Class)

```python
@dataclass(frozen=True)  # Immutable for safety
class RunContext:
    """Immutable context for run command."""

    program_path: str
    interpreter_path: Optional[str]  # None = auto-discover
    apply_path: Optional[str]
    transformer_paths: list[str]
    env_paths: list[str]  # Multiple envs
    output_format: str  # "text" | "json"

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "RunContext":
        """Factory method from parsed args."""
        return cls(
            program_path=args.program,
            interpreter_path=args.interpreter,
            apply_path=args.apply,
            transformer_paths=args.transform or [],
            env_paths=args.env or [],
            output_format=args.format,
        )
```

**Responsibilities**:
- Immutable configuration object
- Factory method for construction
- Type-safe data holder

---

#### 3.2 ProgramExecutor

```python
class ProgramExecutor:
    """Executes programs with interpreters."""

    def __init__(self, loader: SymbolLoader):
        self.loader = loader

    def execute(
        self,
        program: Program[Any],
        interpreter_path: str
    ) -> Any:
        """Execute program with interpreter.

        Args:
            program: Program to execute
            interpreter_path: Path to interpreter function

        Returns:
            Result of interpretation
        """
        interpreter = self.loader.load(interpreter_path)

        # Validate interpreter
        if not callable(interpreter):
            raise TypeError(f"Interpreter {interpreter_path} is not callable")

        # Call interpreter (non-async)
        result = self._call_interpreter(interpreter, program)

        # Finalize result (unwrap if needed)
        return _finalize_result(result)

    def _call_interpreter(self, func: Callable, program: Program[Any]) -> Any:
        """Call interpreter with proper signature binding."""
        # Reuse existing _call_interpreter logic from __main__.py
        return _call_interpreter(func, program)
```

**Responsibilities**:
- Execute programs
- Validate interpreters
- Handle interpreter calling conventions
- Finalize results

**Dependencies**:
- SymbolLoader
- Existing _call_interpreter logic

---

#### 3.3 RunCommandHandler

```python
class RunCommandHandler:
    """Handles 'doeff run' command."""

    def __init__(
        self,
        interpreter_discovery: InterpreterDiscovery,
        env_discovery: EnvDiscovery,
        env_merger: EnvMerger,
        loader: SymbolLoader,
        executor: ProgramExecutor,
    ):
        """Dependency injection for all services."""
        self.interpreter_discovery = interpreter_discovery
        self.env_discovery = env_discovery
        self.env_merger = env_merger
        self.loader = loader
        self.executor = executor

    def handle(self, context: RunContext) -> int:
        """
        Main execution flow:
        1. Discovery Phase
        2. Environment Phase
        3. Transform Phase
        4. Execution Phase
        5. Output Phase
        """
        try:
            # 1. Discovery Phase
            program = self.loader.load(context.program_path)
            interpreter_path = self._discover_interpreter(context)

            # 2. Environment Phase
            program = self._apply_environment(program, context)

            # 3. Transform Phase
            program = self._apply_transforms(program, context)

            # 4. Execution Phase
            result = self.executor.execute(program, interpreter_path)

            # 5. Output Phase
            self._output_result(result, context)

            return 0  # Success

        except Exception as exc:
            self._output_error(exc, context)
            return 1  # Failure

    def _discover_interpreter(self, context: RunContext) -> str:
        """Discover or use provided interpreter."""
        if context.interpreter_path:
            return context.interpreter_path

        discovered = self.interpreter_discovery.find_default_interpreter(
            context.program_path
        )

        if discovered:
            return discovered

        # No interpreter found - raise helpful error
        raise NoInterpreterFoundError(context.program_path)

    def _apply_environment(
        self,
        program: Program[Any],
        context: RunContext
    ) -> Program[Any]:
        """Discover and merge envs, wrap program."""
        # Discover default envs
        default_envs = self.env_discovery.discover_default_envs(
            context.program_path
        )

        # Combine with explicit envs
        all_env_sources = default_envs + context.env_paths

        if not all_env_sources:
            return program  # No env wrapping needed

        # Merge envs
        merged_env = self.env_merger.merge_envs(all_env_sources)

        # Wrap with Local effect
        from doeff.effects import Local
        return Local(merged_env, program)

    def _apply_transforms(
        self,
        program: Program[Any],
        context: RunContext
    ) -> Program[Any]:
        """Apply Kleisli and transformer operations."""
        # Apply --apply (Kleisli)
        if context.apply_path:
            kleisli = self.loader.load(context.apply_path)
            program = kleisli(program)

        # Apply --transform (transformers)
        for transform_path in context.transformer_paths:
            transformer = self.loader.load(transform_path)
            program = transformer(program)

        return program

    def _output_result(self, result: Any, context: RunContext):
        """Output result in requested format."""
        if context.output_format == "json":
            payload = {
                "status": "ok",
                "result": _json_safe(result),
                "result_type": type(result).__name__,
            }
            print(json.dumps(payload))
        else:
            print(result)

    def _output_error(self, exc: Exception, context: RunContext):
        """Output error in requested format."""
        from doeff.types import capture_traceback
        captured = capture_traceback(exc)

        if context.output_format == "json":
            payload = {
                "status": "error",
                "error": exc.__class__.__name__,
                "message": str(exc),
            }
            if captured:
                payload["traceback"] = captured.format(condensed=False, max_lines=200)
            print(json.dumps(payload))
        else:
            if captured:
                print(captured.format(condensed=False, max_lines=200), file=sys.stderr)
            else:
                print(f"Error: {exc}", file=sys.stderr)
```

**Responsibilities**:
- Orchestrate entire run command flow
- Discovery phase
- Environment phase
- Transform phase
- Execution phase
- Output phase
- Error handling

**Dependencies**:
- All discovery and execution services (injected)

**Testing**:
- Unit tests with mocked dependencies
- Integration tests with real services
- Test each phase independently
- Test error cases

---

#### 3.4 NoInterpreterFoundError

```python
class NoInterpreterFoundError(Exception):
    """Raised when no default interpreter found and none specified."""

    def __init__(self, program_path: str):
        self.program_path = program_path
        super().__init__(self._build_message())

    def _build_message(self) -> str:
        return f"""
No default interpreter found for '{self.program_path}'.

To fix this, choose one of:

1. Add a default interpreter to any parent module:
   def my_interpreter(prog: Program[Any]) -> Any:
       '''
       # doeff: interpreter, default
       '''
       # your implementation

2. Specify interpreter explicitly:
   doeff run --program {self.program_path} --interpreter some.module:my_interpreter

3. Use doeff's built-in interpreter:
   doeff run --program {self.program_path} --interpreter doeff:ProgramInterpreter
""".strip()
```

**Responsibilities**:
- Provide helpful error message
- Show 3 options for resolution
- Include program path in message

---

## Layer 4: Effect System Enhancement (Python)

### Purpose
Update Local effect to accept Program[dict]

### Components

#### 4.1 Local Effect (Enhanced)

```python
class Local:
    """Local effect with environment (enhanced)."""

    def __init__(
        self,
        env: dict | Program[dict],
        program: Program[Any]
    ):
        """
        Args:
            env: Environment dict or Program that produces dict
            program: Program to execute with environment
        """
        self.env = env
        self.program = program

    def as_program(self) -> Program[Any]:
        """
        Convert to Program using @do composition.

        No async/await - uses yield for composition.
        """
        from doeff import Program, do

        if isinstance(self.env, Program):
            @do()
            def with_program_env():
                # Extract dict from Program[dict]
                env_dict = yield self.env
                # Apply env and run program
                result = yield self._apply_env(env_dict, self.program)
                return result
            return with_program_env()
        else:
            # env is already dict
            return self._apply_env(self.env, self.program)

    def _apply_env(self, env_dict: dict, program: Program[Any]) -> Program[Any]:
        """Apply environment dict to program (implementation detail)."""
        # Existing Local effect logic
        ...
```

**Responsibilities**:
- Accept both dict and Program[dict]
- Use @do composition (no async/await)
- Maintain backward compatibility

**Dependencies**:
- doeff.Program
- @do decorator

**Testing**:
- Test with dict
- Test with Program.pure(dict)
- Test with complex Program[dict]
- Test backward compatibility

---

## Layer 5: ProgramInterpreter Refactor (Python)

### Purpose
Make ProgramInterpreter.run() non-async

### Components

#### 5.1 ProgramInterpreter (Refactored)

```python
class ProgramInterpreter:
    """Interprets Programs (refactored to be non-async)."""

    def run(self, program: Program[Any]) -> RunResult[Any]:
        """
        Execute program synchronously.

        Calls asyncio.run() internally to handle async execution.
        User-facing API is synchronous.

        Args:
            program: Program to execute

        Returns:
            RunResult with value or error
        """
        import asyncio
        return asyncio.run(self._run_async(program))

    async def _run_async(self, program: Program[Any]) -> RunResult[Any]:
        """Internal async implementation (unchanged logic)."""
        # Existing async implementation
        ...
```

**Responsibilities**:
- Execute programs synchronously
- Hide async implementation detail
- Maintain compatibility with async programs

**Breaking Changes**:
- `await interpreter.run(program)` → `interpreter.run(program)`
- All callers must be updated

**Testing**:
- Test sync execution
- Test async programs still work
- Test error handling
- Migration tests

---

## Dependency Graph

```
┌─────────────────────────────────────────────────────────────┐
│ Layer 5: ProgramInterpreter (Refactored)                    │
└─────────────────────────────────────────────────────────────┘
                            ↑
┌─────────────────────────────────────────────────────────────┐
│ Layer 4: Local Effect (Enhanced)                            │
└─────────────────────────────────────────────────────────────┘
                            ↑
┌─────────────────────────────────────────────────────────────┐
│ Layer 3: CLI Integration                                    │
│                                                              │
│  RunCommandHandler → InterpreterDiscovery (Protocol)        │
│                   → EnvDiscovery (Protocol)                 │
│                   → EnvMerger (Protocol)                    │
│                   → SymbolLoader (Protocol)                 │
│                   → ProgramExecutor                          │
└─────────────────────────────────────────────────────────────┘
                            ↑
┌─────────────────────────────────────────────────────────────┐
│ Layer 2: Discovery Services                                 │
│                                                              │
│  IndexerBasedDiscovery → Indexer (from Rust)                │
│  StandardEnvMerger → SymbolLoader                           │
│  StandardSymbolLoader (standalone)                          │
│  ProgramExecutor → SymbolLoader                             │
└─────────────────────────────────────────────────────────────┘
                            ↑
┌─────────────────────────────────────────────────────────────┐
│ Layer 1: Indexer (Rust + PyO3)                              │
│                                                              │
│  Indexer (PyO3) → IndexerCore → AST Parser                  │
│  SymbolInfo (PyO3 struct)                                   │
└─────────────────────────────────────────────────────────────┘
```

---

## Testing Strategy

### Unit Tests
- Each class tested in isolation
- Mock all dependencies
- Test edge cases and error paths
- Fast execution (< 1s total)

### Integration Tests
- Test interactions between layers
- Use real indexer with fixtures
- Test discovery workflows
- Test env merging
- Moderate speed (< 5s total)

### E2E Tests
- Full CLI workflows
- Real module structures
- Test all features together
- Test backward compatibility
- Slower but comprehensive (< 30s total)

---

## Scalability & Modifiability

### Adding New Discovery Sources
1. Implement `InterpreterDiscovery` or `EnvDiscovery` Protocol
2. Inject into `RunCommandHandler`
3. No changes to other components

### Adding New Env Merging Strategies
1. Implement `EnvMerger` Protocol
2. Inject into `RunCommandHandler`
3. Use in tests or CLI flags

### Adding New Output Formats
1. Extend `RunContext.output_format`
2. Add case in `_output_result()`
3. No changes to execution logic

### Adding New Interpreter Sources
1. Extend `IndexerBasedDiscovery`
2. Or create new `InterpreterDiscovery` implementation
3. Compose multiple discoverers

### Adding Caching
1. Create `CachedIndexer` wrapper
2. Implement `Indexer` interface
3. Inject in place of `Indexer`
4. No changes to discovery logic

---

## Implementation Results

### Phase 1: ProgramInterpreter (Breaking) ✅ COMPLETED
- ✅ Changed async → sync (Commit `5d09215`)
- ✅ Updated all 266 test callers
- ✅ Released as breaking change
- **Actual files**: `doeff/interpreter.py`

### Phase 2: Indexer Enhancement ✅ COMPLETED
- ✅ Added PyO3 bindings (Commit `5d09215`)
- ✅ Added variable indexing
- ✅ Docstring parsing already existed
- ✅ Backward compatible
- **Actual files**: `packages/doeff-indexer/src/python_api.rs`

### Phase 3: Discovery Services ✅ COMPLETED
- ✅ Added Python discovery services (Commit `e3a2721`)
- ✅ No changes to existing code
- ✅ Backward compatible
- **Actual files**: `doeff/cli/discovery.py`, `tests/test_discovery.py`

### Phase 4: CLI Integration ✅ COMPLETED
- ✅ Made --interpreter optional (Commit `e3a2721`)
- ✅ Added --env flag with action="append"
- ✅ Auto-discovery working
- ✅ Backward compatible (existing usage works)
- **Actual files**: `doeff/__main__.py`, `tests/test_cli_run.py`

### Phase 5: Effect Enhancement ⏭️ SKIPPED
- ⏭️ Local effect enhancement not needed
- ✅ Existing Local + Program composition sufficient
- **Reason**: @do composition worked perfectly without changes

---

## Summary

**Key Design Decisions**:
1. **Protocol-based architecture**: Enables testing and flexibility
2. **Dependency injection**: All services injected, easy to swap
3. **Immutable data structures**: RunContext, SymbolInfo frozen
4. **@do composition**: No async/await in user code
5. **Layered architecture**: Clear separation of concerns
6. **Single responsibility**: Each class has one job
7. **Testability**: Mock/stub at protocol boundaries

**Benefits**:
- **Scalable**: Easy to add new discovery sources
- **Modifiable**: Replace implementations without breaking others
- **Testable**: Mock at protocol boundaries
- **Maintainable**: Clear responsibilities
- **Type-safe**: Comprehensive type hints
- **SOLID**: Follows all SOLID principles
