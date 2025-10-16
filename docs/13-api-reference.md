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

Protocol for effect requests.

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
- **`memo`** - Within-execution cache (MemoGet/MemoPut)
- **`cache_handler`** - Persistent cache handler (CacheGet/CachePut)
- **`atomic_state`** - Thread-safe state (AtomicGet/AtomicUpdate)
- **`atomic_lock`** - Lock for atomic operations

**See:** [Core Concepts](02-core-concepts.md#executioncontext)

---

### RunResult[T]

Result of Program execution with full context.

```python
@dataclass
class RunResult[T]:
    result: Result[T]
    state: dict[str, Any]
    log: list[LogEntry]
    graph: WGraph
    
    @property
    def value(self) -> T  # Unwraps result or raises
    
    @property
    def is_ok(self) -> bool
    
    @property
    def is_err(self) -> bool
```

**Fields:**

- **`result`** - Ok(value) or Err(error)
- **`state`** - Final state after execution
- **`log`** - All log entries
- **`graph`** - Full execution graph

**Properties:**

- **`value`** - Unwraps Ok value or raises error
- **`is_ok`** - True if result is Ok
- **`is_err`** - True if result is Err

**See:** [Core Concepts](02-core-concepts.md#runresult)

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

**Returns:** Value from ExecutionContext.env

**Raises:** KeyError if key not in environment

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

**Returns:** Value from ExecutionContext.state, or None if not found

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

**Returns:** None

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

### Tell(messages)

Add multiple log entries.

```python
yield Tell(["Step 1", "Step 2", "Step 3"])
```

**Parameters:** `messages: list[str]` - List of log messages

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

### Parallel(*awaitables)

Run async operations concurrently.

```python
results = yield Parallel(
    async_func1(),
    async_func2(),
    async_func3()
)
```

**Parameters:** `*awaitables: Awaitable[T]` - Multiple async operations

**Returns:** `list[T]` - Results in order

**See:** [Async Effects](04-async-effects.md#parallel)

---

## Error Handling Effects

### Fail(error)

Raise error in Program.

```python
yield Fail(ValueError("Invalid input"))
```

**Parameters:** `error: Exception` - Error to raise

**Returns:** Never returns (raises error)

**See:** [Error Handling](05-error-handling.md#fail)

---

### Catch(program, handler)

Catch errors from program.

```python
result = yield Catch(
    risky_operation(),
    lambda e: f"Error: {e}"
)
```

**Parameters:**

- **`program: Program[T]`** - Program that may fail
- **`handler: Callable[[Exception], T]`** - Error handler

**Returns:** Result of program or handler result

**See:** [Error Handling](05-error-handling.md#catch)

---

### Retry(program, max_attempts, delay_ms=0, delay_strategy=None)

Retry failed program.

```python
result = yield Retry(
    unstable_operation(),
    max_attempts=5,
    delay_ms=100
)
```

**Parameters:**

- **`program: Program[T]`** - Program to retry
- **`max_attempts: int`** - Maximum retry attempts (default: 3)
- **`delay_ms: int`** - Fixed delay between retries in milliseconds (default: 0)
- **`delay_strategy: Callable[[int, Exception | None], float | int | None]`** -
  Optional callback returning the delay in seconds for the next retry.
  Receives the 1-based attempt number and the last error. Return ``None`` or ``0``
  to skip waiting for that retry.

**Returns:** First successful result

**Raises:** Last error if all attempts fail

**See:** [Error Handling](05-error-handling.md#retry)

---

### Recover(program, fallback)

Provide fallback on error.

```python
result = yield Recover(
    risky_operation(),
    fallback=default_value()
)
```

**Parameters:**

- **`program: Program[T]`** - Program that may fail
- **`fallback: Program[T]`** - Fallback program

**Returns:** Result of program or fallback

**See:** [Error Handling](05-error-handling.md#recover)

---

### Safe(program, default)

Return default value on error.

```python
result = yield Safe(risky_operation(), default=None)
```

**Parameters:**

- **`program: Program[T]`** - Program that may fail
- **`default: T`** - Default value

**Returns:** Result of program or default value

**See:** [Error Handling](05-error-handling.md#safe)

---

### Finally(program, cleanup)

Ensure cleanup runs.

```python
result = yield Finally(
    operation(),
    cleanup()
)
```

**Parameters:**

- **`program: Program[T]`** - Main program
- **`cleanup: Program[Any]`** - Cleanup program (always runs)

**Returns:** Result of program

**See:** [Error Handling](05-error-handling.md#finally)

---

### FirstSuccess(*programs)

Return first successful result.

```python
result = yield FirstSuccess(
    try_primary(),
    try_secondary(),
    try_fallback()
)
```

**Parameters:** `*programs: Program[T]` - Programs to try in order

**Returns:** First successful result

**Raises:** Last error if all fail

**See:** [Error Handling](05-error-handling.md#firstsuccess)

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

### Print(message)

Print to stdout.

```python
yield Print("Hello, world!")
```

**Parameters:** `message: str` - Message to print

**Returns:** None

**See:** [IO Effects](06-io-effects.md#print)

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

### GatherDict(programs)

Execute named Programs in parallel.

```python
results = yield GatherDict({
    "user": fetch_user(123),
    "posts": fetch_posts(123),
    "comments": fetch_comments(123)
})
```

**Parameters:** `programs: dict[str, Program[T]]` - Named programs

**Returns:** `dict[str, T]` - Results with same keys

**See:** [Advanced Effects](09-advanced-effects.md#gatherdict)

---

### MemoGet(key)

Get memoized value.

```python
value = yield MemoGet("key")
```

**Parameters:** `key: str` - Memo key

**Returns:** Memoized value

**Raises:** KeyError if not memoized

**See:** [Advanced Effects](09-advanced-effects.md#memo-effects)

---

### MemoPut(key, value)

Store memoized value.

```python
yield MemoPut("key", value)
```

**Parameters:**

- **`key: str`** - Memo key
- **`value: Any`** - Value to memoize

**Returns:** None

**See:** [Advanced Effects](09-advanced-effects.md#memoput)

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

### Dep(key)

Request dependency from pinjected.

```python
database = yield Dep("database")
```

**Parameters:** `key: str` - Dependency key

**Returns:** Resolved dependency

**Requires:** doeff-pinjected package

**See:** [Pinjected Integration](10-pinjected-integration.md#dep-effect)

---

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

### ProgramInterpreter

Executes Programs by handling effects.

```python
from doeff import ProgramInterpreter, ExecutionContext

interpreter = ProgramInterpreter()
result = await interpreter.run(my_program(), ExecutionContext())
```

**Methods:**

```python
async def run(
    self,
    program: Program[T],
    context: ExecutionContext | None = None
) -> RunResult[T]
```

**Parameters:**

- **`program`** - Program to execute
- **`context`** (optional) - Initial execution context

**Returns:** RunResult with result and full context

**See:** [Core Concepts](02-core-concepts.md#execution-model)

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
| **Async** | Await, Parallel |
| **Error** | Fail, Catch, Retry, Recover, Safe, Finally, FirstSuccess |
| **IO** | IO, Print |
| **Cache** | CacheGet, CachePut |
| **Graph** | Step, Annotate, Snapshot, CaptureGraph |
| **Advanced** | Gather, GatherDict, MemoGet, MemoPut |
| **DI** | Dep (doeff-pinjected) |

### Common Imports

```python
# Core
from doeff import do, Program, ProgramInterpreter, ExecutionContext, RunResult

# Basic Effects
from doeff import Ask, Local, Get, Put, Modify, Log, Tell, Listen

# Async
from doeff import Await, Parallel

# Error Handling
from doeff import Fail, Catch, Retry, Recover, Safe, Finally, FirstSuccess

# IO
from doeff import IO, Print

# Cache
from doeff import CacheGet, CachePut, cache

# Graph
from doeff import Step, Annotate, Snapshot, CaptureGraph, graph_to_html

# Advanced
from doeff import Gather, GatherDict, MemoGet, MemoPut, AtomicGet, AtomicUpdate

# Pinjected (separate package)
from doeff import Dep
from doeff_pinjected import program_to_injected, program_to_injected_result
```

## Next Steps

- **[Getting Started](01-getting-started.md)** - Begin using doeff
- **[Core Concepts](02-core-concepts.md)** - Understand fundamentals
- **[Patterns](12-patterns.md)** - Learn best practices
