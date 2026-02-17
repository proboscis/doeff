# API Reference

Complete API reference for doeff.

## Core Types

### Program[T]

The core abstraction representing a lazy, reusable computation.

```python
class Program[T]:
    def map(self, f: Callable[[T], U]) -> Program[U]
    def flat_map(self, f: Callable[[T], Program[U]]) -> Program[U]
    def intercept(self, transform: Callable[[Effect], Effect | Program]) -> Program[T]
```

**Methods:**

- **`map(f)`** - Transform the result value
- **`flat_map(f)`** - Chain with another Program
- **`intercept(transform)`** - Intercept and transform effects

**Static Methods:**

```python
@staticmethod
def pure(value: T) -> Program[T]
```

- **`Program.pure(value)`** - Create a Program that immediately returns a value

**See:** [Core Concepts](02-core-concepts.md#program)

---

### Effect

Protocol for algebraic effect operations.

```python
class Effect(Protocol):
    def intercept(self, transform: Callable[[Effect], Effect | Program]) -> Effect | Program
```

All effects implement this protocol.

**See:** [Core Concepts](02-core-concepts.md#effect-protocol)

---

### ExecutionContext

Mutable context for Program execution.

```python
@dataclass
class ExecutionContext:
    env: dict[str, Any] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)
    log: list[LogEntry] = field(default_factory=list)
    graph: WGraph = field(default_factory=WGraph.empty)
    memo: dict[str, Any] = field(default_factory=dict)
    cache_handler: CacheHandler | None = None
    atomic_state: dict[str, Any] = field(default_factory=dict)
    atomic_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
```

**Fields:**

- **`env`** - Environment variables (Ask/Local effects)
- **`state`** - Mutable state (Get/Put/Modify effects)
- **`log`** - Log entries (Log/Tell effects)
- **`graph`** - Execution graph (Step/Annotate effects)
- **`memo`** - Within-execution cache (for internal use)
- **`cache_handler`** - Persistent cache handler (CacheGet/CachePut)
- **`atomic_state`** - Thread-safe state (AtomicGet/AtomicUpdate)
- **`atomic_lock`** - Lock for atomic operations

**See:** [Core Concepts](02-core-concepts.md#executioncontext)

---

### RuntimeResult[T]

Result of Program execution returned by `run()` and `async_run()`.

```python
class RuntimeResult[T](Protocol):
    result: Result[T]      # Ok(value) or Err(error)
    raw_store: dict        # Final store state
    env: dict              # Final environment

    # Stack traces for debugging
    k_stack: KStackTrace
    effect_stack: EffectStackTrace
    python_stack: PythonStackTrace

    @property
    def value(self) -> T        # Unwraps Ok or raises

    @property
    def error(self) -> BaseException  # Get error or raises

    def is_ok(self) -> bool     # True if result is Ok
    def is_err(self) -> bool    # True if result is Err
    def format(self, *, verbose: bool = False) -> str
```

**Core Properties:**

- **`result`** - `Ok(value)` or `Err(error)`
- **`value`** - Unwraps Ok value (raises if Err)
- **`error`** - Gets the exception (raises if Ok)
- **`raw_store`** - Final store state dictionary

**Methods (NOT properties!):**

- **`is_ok()`** - Returns True if result is Ok
- **`is_err()`** - Returns True if result is Err
- **`format(verbose=False)`** - Format result for display

**Accessing logs and graph:**

```python
result = run(my_program(), default_handlers())
logs = result.raw_store.get("__log__", [])
graph = result.raw_store.get("__graph__")
```

**Stack traces (for debugging errors):**

- **`k_stack`** - Continuation stack snapshot
- **`effect_stack`** - Effect call tree
- **`python_stack`** - Python source locations

**See:** [Error Handling](05-error-handling.md#runtimeresult-protocol)

---

### Result[T]

Represents success (Ok) or failure (Err).

```python
@dataclass
class Ok[T]:
    value: T

@dataclass
class Err:
    error: Exception

Result = Ok[T] | Err
```

**See:** [Error Handling](05-error-handling.md#result-type)

---

## Decorators

### @do

Converts generator function to KleisliProgram.

```python
@do
def my_program(x: int) -> Program[int]:
    value = yield Get("key")
    return value + x

# Creates KleisliProgram with automatic Program unwrapping
```

**Parameters:** Generator function that yields Effects or Programs

**Returns:** KleisliProgram[T]

**See:** [Core Concepts](02-core-concepts.md#do-decorator), [Kleisli Arrows](11-kleisli-arrows.md)

---

### @cache

Caches Program result based on arguments.

```python
@cache(ttl=60, lifecycle=CacheLifecycle.SESSION)
@do
def expensive_computation(x: int):
    return x * 2
```

**Parameters:**

- **`ttl`** (int, optional) - Time-to-live in seconds
- **`lifecycle`** (CacheLifecycle, optional) - SESSION, PERSISTENT, TEMPORARY
- **`storage`** (CacheStorage, optional) - MEMORY, DISK, DISTRIBUTED
- **`**kwargs`** - Additional policy fields

**Returns:** Decorated KleisliProgram with caching

**See:** [Cache System](07-cache-system.md), [cache.md](cache.md)

---

## Reader Effects

### Ask(key)

Request environment variable.

```python
config = yield Ask("database_url")
```

**Parameters:** `key: str` - Environment variable name

**Returns:** Value from environment

**Raises:** `MissingEnvKeyError` if key not in environment

**See:** [Basic Effects](03-basic-effects.md#reader-effects)

---

### Local(env, program)

Run program with modified environment.

```python
result = yield Local({"timeout": 30}, sub_program())
```

**Parameters:**

- **`env: dict[str, Any]`** - Environment overrides
- **`program: Program[T]`** - Program to run

**Returns:** Result of program with merged environment

**See:** [Basic Effects](03-basic-effects.md#local)

---

## State Effects

### Get(key)

Read state value.

```python
count = yield Get("counter")
```

**Parameters:** `key: str` - State key

**Returns:** Value from store

**Raises:** `KeyError` if key not found

**See:** [Basic Effects](03-basic-effects.md#state-effects)

---

### Put(key, value)

Write state value.

```python
yield Put("counter", 42)
```

**Parameters:**

- **`key: str`** - State key
- **`value: Any`** - Value to store

**Returns:** None

**See:** [Basic Effects](03-basic-effects.md#put)

---

### Modify(key, f)

Update state value with function.

```python
yield Modify("counter", lambda x: x + 1)
```

**Parameters:**

- **`key: str`** - State key
- **`f: Callable[[T], T]`** - Transformation function

**Returns:** The new (transformed) value

**See:** [Basic Effects](03-basic-effects.md#modify)

---

## Writer Effects

### Log(message)

Add log entry.

```python
yield Log("Processing data")
```

**Parameters:** `message: str` - Log message

**Returns:** None

**See:** [Basic Effects](03-basic-effects.md#writer-effects)

---

### Tell(message)

Add a log entry. Alias: `Log(message)`.

```python
yield Tell("Processing started")
yield Log("Step 1 complete")  # Log is alias for Tell
```

**Parameters:** `message: object` - Message to log (string or any object)

**Returns:** None

**See:** [Basic Effects](03-basic-effects.md#tell)

---

### Listen(program)

Capture logs from sub-program.

```python
result = yield Listen(sub_program())
value, logs = result  # Tuple unpacking
# or
value = result.value
logs = result.log
```

**Parameters:** `program: Program[T]` - Program to run

**Returns:** ListenResult with `.value` and `.log` attributes

**See:** [Basic Effects](03-basic-effects.md#listen)

---

### StructuredLog(data)

Add structured log entry.

```python
yield StructuredLog({"level": "info", "message": "Processing", "count": 42})
```

**Parameters:** `data: dict[str, Any]` - Structured log data

**Returns:** None

**See:** [Basic Effects](03-basic-effects.md#structuredlog)

---

## Async Effects

### Await(awaitable)

Wait for async operation.

```python
result = yield Await(async_function())
```

**Parameters:** `awaitable: Awaitable[T]` - Async operation

**Returns:** Result of awaitable

**See:** [Async Effects](04-async-effects.md#await)

---

## Error Handling Effects

### Safe(program)

Wrap execution in a `Result` type for explicit error handling.

```python
result = yield Safe(risky_operation())
if result.is_ok():
    return result.value
else:
    return "fallback"
```

**Parameters:**

- **`program: Program[T]`** - Program that may fail

**Returns:** `Result[T]` - Ok(value) on success, Err(error) on failure

**See:** [Error Handling](05-error-handling.md#safe-effect)

---

## IO Effects

### IO(thunk)

Execute side effect.

```python
timestamp = yield IO(lambda: time.time())
```

**Parameters:** `thunk: Callable[[], T]` - Function to execute

**Returns:** Result of thunk

**See:** [IO Effects](06-io-effects.md#io)

---

## Cache Effects

### CacheGet(key)

Retrieve cached value.

```python
value = yield CacheGet("expensive_key")
```

**Parameters:** `key: str` - Cache key

**Returns:** Cached value

**Raises:** KeyError if not in cache

**See:** [Cache System](07-cache-system.md)

---

### CachePut(key, value, **policy)

Store value in cache.

```python
yield CachePut(
    "key",
    value,
    ttl=300,
    lifecycle=CacheLifecycle.PERSISTENT
)
```

**Parameters:**

- **`key: str`** - Cache key
- **`value: Any`** - Value to cache
- **`**policy`** - Policy fields (ttl, lifecycle, storage, metadata)

**Returns:** None

**See:** [Cache System](07-cache-system.md), [cache.md](cache.md)

---

## Graph Effects

### Step(name, description)

Add named step to graph.

```python
yield Step("initialize", "Setup phase")
```

**Parameters:**

- **`name: str`** - Step name
- **`description: str | dict`** (optional) - Step description or metadata

**Returns:** None

**See:** [Graph Tracking](08-graph-tracking.md#step)

---

### Annotate(metadata)

Add metadata to current step.

```python
yield Annotate({"user_id": 123, "operation": "fetch"})
```

**Parameters:** `metadata: dict[str, Any]` - Metadata dictionary

**Returns:** None

**See:** [Graph Tracking](08-graph-tracking.md#annotate)

---

### Snapshot()

Capture current graph state.

```python
graph = yield Snapshot()
```

**Parameters:** None

**Returns:** Current WGraph

**See:** [Graph Tracking](08-graph-tracking.md#snapshot)

---

### CaptureGraph(program)

Capture graph from sub-program.

```python
result, graph = yield CaptureGraph(sub_program())
```

**Parameters:** `program: Program[T]` - Program to run

**Returns:** Tuple of (result, graph)

**See:** [Graph Tracking](08-graph-tracking.md#capturegraph)

---

## Advanced Effects

### Spawn(program)

Spawn a Program in the background and return a Task handle.

```python
task = yield Spawn(worker())
result = yield Wait(task)
```

**Parameters:**

- **`program: Program[T]`** - Program to execute

**Returns:** `Task[T]` - use `Wait(task)` to get result

**See:** [Advanced Effects](09-advanced-effects.md)

### Gather(*programs)

Execute Programs in parallel.

```python
results = yield Gather(
    fetch_user(1),
    fetch_user(2),
    fetch_user(3)
)
```

**Parameters:** `*programs: Program[T]` - Programs to run in parallel

**Returns:** `list[T]` - Results in order

**See:** [Advanced Effects](09-advanced-effects.md#gather-effects)

---

### AtomicGet(key)

Thread-safe state read.

```python
count = yield AtomicGet("counter")
```

**Parameters:** `key: str` - State key

**Returns:** Value from atomic state

**See:** [Advanced Effects](09-advanced-effects.md#atomic-effects)

---

### AtomicUpdate(key, f)

Thread-safe state update.

```python
new_value = yield AtomicUpdate("counter", lambda x: x + 1)
```

**Parameters:**

- **`key: str`** - State key
- **`f: Callable[[T], T]`** - Update function

**Returns:** Updated value

**See:** [Advanced Effects](09-advanced-effects.md#atomicupdate)

---

## Pinjected Integration

### program_to_injected(program)

Convert Program to Injected.

```python
from doeff_pinjected import program_to_injected

injected = program_to_injected(my_program())
result = await resolver.provide(injected)
```

**Parameters:** `program: Program[T]` - Program to convert

**Returns:** `Injected[T]` - pinjected Injected value

**Package:** doeff-pinjected

**See:** [Pinjected Integration](10-pinjected-integration.md#program_to_injected)

---

### program_to_injected_result(program)

Convert Program to Injected[RunResult].

```python
from doeff_pinjected import program_to_injected_result

injected = program_to_injected_result(my_program())
result = await resolver.provide(injected)
# result is RunResult[T] with state, log, graph
```

**Parameters:** `program: Program[T]` - Program to convert

**Returns:** `Injected[RunResult[T]]` - Injected returning full context

**Package:** doeff-pinjected

**See:** [Pinjected Integration](10-pinjected-integration.md#program_to_injected_result)

---

### program_to_iproxy(program)

Convert Program to IProxy.

```python
from doeff_pinjected import program_to_iproxy

iproxy = program_to_iproxy(my_program())
```

**Parameters:** `program: Program[T]` - Program to convert

**Returns:** `IProxy[T]` - pinjected IProxy value

**Package:** doeff-pinjected

**See:** [Pinjected Integration](10-pinjected-integration.md#program_to_iproxy)

---

### program_to_iproxy_result(program)

Convert Program to IProxy[RunResult].

```python
from doeff_pinjected import program_to_iproxy_result

iproxy = program_to_iproxy_result(my_program())
```

**Parameters:** `program: Program[T]` - Program to convert

**Returns:** `IProxy[RunResult[T]]` - IProxy returning full context

**Package:** doeff-pinjected

**See:** [Pinjected Integration](10-pinjected-integration.md#program_to_iproxy_result)

---

## Execution

### run

Executes Programs synchronously with cooperative scheduling.

```python
from doeff import run, default_handlers

# Run a program synchronously
result = run(my_program(), default_handlers())

# With initial environment and store
result = run(
    my_program(),
    default_handlers(),
    env={"key": "value"},
    store={"state": 0}
)
```

**Signature:**

```python
def run(
    program: Program[T],
    handlers: list[Handler],
    env: dict | None = None,
    store: dict | None = None,
) -> RuntimeResult[T]
```

**Parameters:**

- **`program`** - Program to execute
- **`handlers`** - List of effect handlers (use `default_handlers()` for defaults)
- **`env`** (optional) - Initial environment (Reader effects)
- **`store`** (optional) - Initial store (State effects)

**Returns:** RuntimeResult with result value

**See:** [Core Concepts](02-core-concepts.md#execution-model)

---

### async_run

Executes Programs asynchronously with real async I/O support.

```python
from doeff import async_run, default_async_handlers

# Run a program asynchronously
result = await async_run(my_program(), handlers=default_async_handlers())

# With initial environment and store
result = await async_run(
    my_program(),
    handlers=default_async_handlers(),
    env={"key": "value"},
    store={"state": 0}
)
```

**Signature:**

```python
async def async_run(
    program: Program[T],
    handlers: list[Handler],
    env: dict | None = None,
    store: dict | None = None,
) -> RuntimeResult[T]
```

**Parameters:**

- **`program`** - Program to execute
- **`handlers`** - List of effect handlers (use `default_async_handlers()` for defaults)
- **`env`** (optional) - Initial environment (Reader effects)
- **`store`** (optional) - Initial store (State effects)

**Returns:** RuntimeResult with result value

**See:** [Core Concepts](02-core-concepts.md#execution-model)

---

### run_program(program, *, interpreter=None, envs=None, apply=None, transform=None, report=False, report_verbose=False, quiet=False, load_default_env=True)

Run a Program from Python with the same discovery defaults as `doeff run`.

```python
from doeff import run_program

result = run_program("pkg.features.login_program", quiet=True, report=True)
assert result.value == "ok"
```

**Parameters:**

- **`program`** (`str | Program`) - Program path (enables discovery) or Program instance.
- **`interpreter`** (`str | AsyncioRuntime | callable | None`) - Override interpreter/runtime.
- **`envs`** (`list[str | Program[dict] | Mapping] | None`) - Environments to merge.
- **`apply`** (`str | KleisliProgram | callable | None`) - Kleisli applied before run.
- **`transform`** (`list[str | callable] | None`) - Additional Program transformers.
- **`report` / `report_verbose`** - Print RunResult report (string-path mode).
- **`quiet`** - Suppress discovery stderr output.
- **`load_default_env`** - Load `~/.doeff.py::__default_env__` when running with object inputs.

**Returns:** ProgramRunResult with final value, RunResult, and discovery metadata

**See:** [Python run_program API](16-run-program-api.md)

---

### ProgramRunResult

Result container returned by `run_program()`.

```python
from doeff import ProgramRunResult
```

**Fields:**

- **`value`** - Final Program value (None on errors; inspect `run_result`).
- **`run_result`** - Full RunResult with context/log/graph data.
- **`interpreter_path`** - Resolved interpreter path or description of callable used.
- **`env_sources`** - Environment sources applied (`<dict>`, `<Program[dict]>`, or paths).
- **`applied_kleisli`** - Description of applied Kleisli (if any).
- **`applied_transforms`** - Descriptions of applied transformers.

---

## KleisliProgram

Auto-unwrapping Program composition.

```python
from doeff import KleisliProgram

@do
def add(x: int, y: int):
    return x + y

# Automatic unwrapping
prog_x = Program.pure(5)
result = add(prog_x, 10)  # x unwrapped automatically
```

**Operators:**

- **`>>`** (and_then_k) - Chain KleisliPrograms
- **`<<`** (fmap) - Map pure function

**See:** [Kleisli Arrows](11-kleisli-arrows.md)

---

## Utilities

### graph_to_html(graph)

Generate HTML visualization of execution graph.

```python
from doeff import graph_to_html

html = await graph_to_html(result.graph)
```

**Parameters:** `graph: WGraph` - Execution graph

**Returns:** str - HTML visualization

**See:** [Graph Tracking](08-graph-tracking.md#visualization)

---

### write_graph_html(graph, path)

Write graph HTML to file.

```python
from doeff import write_graph_html

await write_graph_html(result.graph, "output.html")
```

**Parameters:**

- **`graph: WGraph`** - Execution graph
- **`path: str`** - Output file path

**Returns:** None

**See:** [Graph Tracking](08-graph-tracking.md#export-to-html)

---

## Quick Reference

### Effect Categories

| Category | Effects |
|----------|---------|
| **Reader** | Ask, Local |
| **State** | Get, Put, Modify, AtomicGet, AtomicUpdate |
| **Writer** | Log, Tell, Listen, StructuredLog |
| **Async** | Await, Gather, Spawn, Wait |
| **Error** | Safe |
| **IO** | IO |
| **Cache** | CacheGet, CachePut |
| **Graph** | Step, Annotate, Snapshot, CaptureGraph |
| **Advanced** | Spawn, Gather |

### Common Imports

```python
# Core
from doeff import do, Program

# Execution
from doeff import run, default_handlers
from doeff import async_run, default_async_handlers

# Basic Effects
from doeff import Ask, Local, Get, Put, Modify, Log, Tell, Listen

# Async
from doeff import Await, Gather, Spawn, Wait

# Error Handling
from doeff import Safe

# IO
from doeff import IO

# Cache
from doeff import CacheGet, CachePut, cache

# Graph
from doeff import Step, Annotate, Snapshot, CaptureGraph, graph_to_html

# Advanced
from doeff import Gather, AtomicGet, AtomicUpdate

# Pinjected (separate package)
from doeff_pinjected import program_to_injected, program_to_injected_result
```

## Next Steps

- **[Getting Started](01-getting-started.md)** - Begin using doeff
- **[Core Concepts](02-core-concepts.md)** - Understand fundamentals
- **[Patterns](12-patterns.md)** - Learn best practices
