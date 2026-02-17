# Core Concepts

This chapter explores the fundamental architecture of doeff: **algebraic effects**, **handlers**, **one-shot continuations**, and the **Rust VM** that powers it all.

## Table of Contents

- [Algebraic Effects — The Mental Model](#algebraic-effects--the-mental-model)
- [Program[T] - The Core Abstraction](#programt---the-core-abstraction)
- [Effect Protocol](#effect-protocol)
- [Handlers — Interpreting Effects](#handlers--interpreting-effects)
- [The @do Decorator](#the-do-decorator)
- [Generator-Based Do-Notation](#generator-based-do-notation)
- [Execution Model — One-Shot Continuations and the Rust VM](#execution-model--one-shot-continuations-and-the-rust-vm)
- [Composition Operations](#composition-operations)
- [Type System](#type-system)

## Algebraic Effects — The Mental Model

doeff is built on the concept of **algebraic effects with one-shot continuations**. Here is the core idea:

1. **Effects are data** — when your program needs something (read config, update state, log a message), it yields an *effect value* that describes the operation, without performing it.
2. **Handlers interpret effects** — a handler receives the effect value, decides what to do, and sends the result back. Handlers are composable and swappable.
3. **One-shot continuations** — when an effect is yielded, the program suspends. The handler processes it and resumes the program exactly once with the result. This is a *one-shot continuation* (unlike multi-shot systems like Koka or Eff where a continuation can be invoked multiple times).
4. **Rust VM** — the effect handler runtime is backed by a Rust virtual machine for performance, managing the continuation stack, effect dispatch, and handler resolution.

This **effect-handler duality** is the central design: programs *perform* effects, handlers *interpret* them. The same program can be run with different handlers — production handlers for real I/O, mock handlers for testing, logging handlers for debugging.

## Program[T] - The Core Abstraction

`Program[T]` is the fundamental building block - a lazy, reusable computation that produces a value of type `T`.

### Structure

```python
Program[T]  # effectful computation value
```

Programs are produced by `@do` functions and constructors like `Program.pure(...)` and
`Program.lift(...)`.

A generator-based `@do` program:
1. Yields `Effect` or `Program` instances to request operations
2. Eventually returns a value of type `T`
3. Is executed lazily when passed to `run(...)`/`async_run(...)`

### Key Properties

**Lazy Evaluation**
```python
from doeff import run, default_handlers

@do
def expensive_computation():
    yield Tell("This doesn't execute yet")
    return perform_heavy_work()

# No execution happens here
program = expensive_computation()

# Execution happens here
result = run(program, default_handlers())
```

**Reusability**
```python
@do
def get_timestamp():
    import time
    return yield IO(lambda: time.time())

# Unlike generators, this works:
prog = get_timestamp()
result1 = run(prog, default_handlers())  # 1234567890.123
result2 = run(prog, default_handlers())  # 1234567890.456
```

### Creating Programs

**Method 1: Using @do decorator (recommended)**
```python
@do
def my_program(x: int):
    result = yield some_effect(x)
    return result * 2
```

**Method 2: Program.pure (for constants)**
```python
constant_program = Program.pure(42)
```

**Method 3: Use an effect directly (single effect)**
```python
log_program = Tell("test")
```

**Method 4: Manual construction (advanced)**
```python
def generator_func():
    value = yield Get("key")
    return value + 1

program = Program(generator_func)
```

## Effect Protocol

Effects are the vocabulary of doeff programs. Each effect is a **first-class algebraic effect operation** — a data value that describes an operation to be performed by a handler.

### Effect Base Class

```python
@dataclass(frozen=True)
class EffectBase(Effect):
    created_at: EffectCreationContext | None = None

    def intercept(self, transform):
        """Allow effect transformations"""
        ...
```

### Effect Categories

doeff provides effects for:

| Category | Examples | Purpose |
|----------|----------|---------|
| **Reader** | `Ask`, `Local` | Read-only environment access |
| **State** | `Get`, `Put`, `Modify` | Mutable state management |
| **Writer** | `Tell`, `StructuredLog`, `slog`, `Listen` | Accumulate output/logs |
| **Future** | `Await`, `Gather` | Async operations |
| **Result** | `Safe` | Error handling |
| **IO** | `IO` | Side effects |
| **Cache** | `CacheGet`, `CachePut` | Caching with policies |
| **Graph** | `Step`, `Annotate`, `Snapshot` | Execution tracking |
| **Atomic** | `AtomicGet`, `AtomicUpdate` | Thread-safe state |

### Effect Lifecycle (Perform → Handle → Resume)

1. **Creation**: Effect is instantiated with parameters
2. **Perform**: Effect is yielded in a `@do` function — the program suspends
3. **Handle**: The effect handler runtime dispatches to the appropriate handler
4. **Resolve**: The handler produces a result value
5. **Resume**: The one-shot continuation resumes the program with the result

### Custom Effects

You can create custom effects by extending `EffectBase`:

```python
@dataclass(frozen=True)
class CustomEffect(EffectBase):
    message: str

    def intercept(self, transform):
        result = transform(self)
        return result if isinstance(result, Program) else Program.pure(result)
```

Then add a handler to the handlers list.

## Handlers — Interpreting Effects

Handlers are the other half of the algebraic effects duality. A handler receives an effect value and decides how to interpret it — this is what makes effects composable and testable.

### Built-in (Batteries-Included) Handlers

doeff ships with handlers for all built-in effects:

| Effect | Handler | What It Does |
|--------|---------|-------------|
| `Ask`, `Local` | Reader handler | Reads from / scopes the environment |
| `Get`, `Put`, `Modify` | State handler | Manages mutable store |
| `Tell`, `StructuredLog`, `slog`, `Listen` | Writer handler | Accumulates log entries |
| `Await`, `Gather` | Async handler | Bridges to async I/O |
| `Safe` | Result handler | Captures errors as `Result` values |
| `IO` | IO handler | Executes side-effecting thunks |
| `CacheGet`, `CachePut` | Cache handler | Persistent key-value cache |
| `Step`, `Annotate`, `Snapshot` | Graph handler | Tracks execution graph |
| `AtomicGet`, `AtomicUpdate` | Atomic handler | Thread-safe state |

Use `default_handlers()` to get the standard set:

```python
from doeff import run, default_handlers

result = run(my_program(), default_handlers())
```

### Swappable Handlers

Because effects are data and handlers are separate, you can swap handlers for different contexts:

- **Production**: Real I/O, real database, real network
- **Testing**: Mock handlers that return canned values — no GPU, no network, milliseconds
- **Debugging**: Logging handlers that trace every effect

This is a fundamental architectural advantage of algebraic effects over direct side effects.

## The @do Decorator

The `@do` decorator is the bridge between Python generators and effect-handling do-notation.

### What It Does

```python
# Before @do: Generator function
def my_func() -> Generator[Effect, Any, T]:
    value = yield some_effect()
    return value

# After @do: KleisliProgram
@do
def my_func() -> EffectGenerator[T]:
    value = yield some_effect()
    return value
```

The decorator:
1. Converts the function from returning a Generator to returning a `KleisliProgram`
2. Enables lazy evaluation (generator isn't created until execution)
3. Makes the function reusable (unlike raw generators)
4. Preserves type information via ParamSpec

### Type Transformation

```python
# Input type
Callable[P, Generator[Effect, Any, T]]

# Output type
KleisliProgram[P, T]
```

Where `KleisliProgram[P, T]` is essentially:
```python
Callable[P, Program[T]]
```

### Critical: No try/except Around Yields

**DO NOT** use try/except blocks around `yield` statements:

```python
# WRONG - This will NOT catch exceptions from the effect
@do
def broken():
    try:
        value = yield risky_operation()
    except Exception:
        return "fallback"  # This won't work!

# RIGHT - Use effect-based error handling
@do
def correct():
    result = yield Safe(risky_operation())
    value = result.value if result.is_ok() else "fallback"
    return value
```

**Why?** Python generators receive exceptions via `.throw()`, but doeff uses the Result type to represent errors. Exceptions from effects are captured as `Err` values, not thrown into the generator.

## Generator-Based Do-Notation

doeff uses Python generators to simulate Haskell's do-notation.

### Comparison

**Haskell do-notation:**
```haskell
myProgram = do
    config <- ask
    value <- get "counter"
    tell ["Processing"]
    return (value + 1)
```

**doeff equivalent:**
```python
@do
def my_program():
    config = yield Ask("config")
    value = yield Get("counter")
    yield Tell(["Processing"])
    return value + 1
```

### How It Works

1. **Generator Protocol**: `yield` suspends execution (captures the continuation)
2. **Effect Performing**: The yielded value is an Effect instance — the program is *performing* an effect
3. **Handler Dispatch**: The Rust VM dispatches to the appropriate handler
4. **Result Sending**: Handler result is sent back via `.send(value)`
5. **Continuation Resume**: The one-shot continuation resumes with the value

```python
def generator_func():
    # Suspend and yield effect
    value = yield Get("key")
    # Resume with value here
    return value + 1
```

### Generator Mechanics

```python
gen = generator_func()

# Start generator
effect = next(gen)  # Executes until first yield

# Process effect
result = handle_effect(effect)

# Send result back
try:
    next_effect = gen.send(result)
except StopIteration as e:
    final_value = e.value  # The return value
```

## Execution Model — One-Shot Continuations and the Rust VM

doeff's execution model is based on **one-shot continuations**: when a program performs an effect (via `yield`), the current continuation is captured, the handler processes the effect, and the continuation is resumed exactly once with the result. This suspend-handle-resume cycle is managed by the **Rust VM** for performance.

### Running Programs

Programs are executed using `run` or `async_run`:

```python
from doeff import run, async_run, default_handlers, default_handlers

# Synchronous execution
result = run(
    program,
    default_handlers(),
    env={"key": "value"},  # Optional environment
    store={"state": 123}   # Optional initial store
)

# Async execution
result = await arun(
    program,
    default_handlers(),
    env={"key": "value"},
    store={"state": 123}
)
```

### Execution Steps

The Rust VM drives effect handling through the following steps:

1. **Initialize**: Create runtime state from program, environment, and store
2. **Create Generator**: Call `program.generator_func()`
3. **Effect Loop** (the core of the algebraic effects runtime):
   - Get next yielded value (the performed effect)
   - If `Program`: push continuation frame, enter nested program
   - If `Effect`: dispatch to the appropriate handler, get result
   - Resume the one-shot continuation with the result (via `.send(value)`)
4. **Handle Completion**: Catch `StopIteration` and extract return value
5. **Return Result**: `RuntimeResult` with value

### Environment and Store

The execution uses environment and store:

```python
# Environment: Read-only configuration (Ask/Local effects)
env = {"database_url": "postgres://...", "api_key": "..."}

# Store: Mutable state (Get/Put/Modify effects)
store = {"counter": 0, "status": "ready"}

# Pass to execution function
result = run(program, default_handlers(), env=env, store=store)
```

### RuntimeResult

Contains execution outcome. `RuntimeResult` is a Protocol—the concrete implementation is `RuntimeResultImpl`:

```python
class RuntimeResult(Protocol[T]):
    result: Result[T]       # Ok(value) or Err(error)
    raw_store: dict         # Final store state

    @property
    def value(self) -> T:
        """Extract value (raises if Err)"""

    @property
    def error(self) -> BaseException:
        """Extract error (raises if Ok)"""

    def is_ok(self) -> bool:
        """Check if succeeded"""

    def is_err(self) -> bool:
        """Check if failed"""

    # Stack traces for debugging (available on error)
    k_stack: KStackTrace           # Continuation stack
    effect_stack: EffectStackTrace # Effect call tree
    python_stack: PythonStackTrace # Python source locations

    def format(self, *, verbose: bool = False) -> str:
        """Format result for display"""
```

Access logs and graph from raw_store:
```python
logs = result.raw_store.get("__log__", [])
graph = result.raw_store.get("__graph__")
```

### Stack Safety

doeff uses trampolining to prevent stack overflow:

```python
def force_eval(prog: Program[T]) -> Program[T]:
    """Trampoline for stack safety with deep effect chains"""
    def forced_generator():
        gen = prog.generator_func()
        try:
            current = next(gen)
            while True:
                if isinstance(current, Program):
                    current = force_eval(current)  # Trampoline
                value = yield current
                current = gen.send(value)
        except StopIteration as e:
            return e.value
    return Program(forced_generator)
```

## Composition Operations

`Program[T]` provides composition operations for combining effectful computations. These correspond to standard algebraic operations (map, bind/flat_map, pure) that enable programs to be composed. While these have monadic structure under the hood, the mental model should be **effect composition**: combining smaller effectful programs into larger ones.

### map — Transform Results

Transform the result value:

```python
program = Program.pure(42)
doubled = program.map(lambda x: x * 2)  # Program[int] with value 84
```

```python
@do
def example():
    value = yield Get("count")
    # These are equivalent:
    doubled1 = yield Program.pure(value).map(lambda x: x * 2)
    doubled2 = value * 2  # Direct computation
    return doubled2
```

### flat_map — Chain Effectful Computations

Chain effectful computations (the result of one feeds into the next):

```python
def double_program(x: int) -> Program[int]:
    return Program.pure(x * 2)

program = Program.pure(21)
result = program.flat_map(double_program)  # Program[int] with value 42
```

Equivalent to:
```python
@do
def result():
    value = yield program
    final = yield double_program(value)
    return final
```

### then — Sequence Effects

Run programs in sequence, discarding first result:

```python
setup = Put("ready", True)
work = Get("data")

program = setup.then(work)  # Run setup, then work
```

### Static Constructors

**pure / of**: Wrap a pure value
```python
Program.pure(42)  # Program[int]
Program.of("hello")  # Program[str]
```

**Effects**: Use any effect instance directly when you need a single operation
```python
single_log = Tell("test")
```

**lift**: Smart constructor
```python
Program.lift(42)  # Wraps in Program.pure
Program.lift(Program.pure(42))  # Returns unchanged
Program.lift(Get("key"))  # Converts effect to Program
```

### Collection Operations

**sequence**: Run multiple programs, collect results
```python
programs = [Program.pure(1), Program.pure(2), Program.pure(3)]
result = Program.sequence(programs)  # Program[list[int]]
```

**traverse**: Map and sequence
```python
numbers = [1, 2, 3]
result = Program.traverse(numbers, lambda x: Program.pure(x * 2))
# Program[list[int]] with [2, 4, 6]
```

**list/tuple/set/dict**: Construct collections
```python
Program.list(1, 2, Program.pure(3))  # Program[list[int]]
Program.tuple(Program.pure("a"), "b")  # Program[tuple[str, str]]
Program.dict(x=Program.pure(1), y=2)  # Program[dict[str, int]]
```

### Advanced: intercept

Transform all effects in a program:

```python
def log_transform(effect: Effect) -> Effect | Program:
    print(f"Effect: {effect}")
    return effect

logged_program = my_program().intercept(log_transform)
```

This enables:
- Effect logging/debugging
- Effect mocking for tests
- Effect transformation
- Custom effect handlers

## Type System

doeff provides comprehensive type support.

### Core Types

```python
from doeff import (
    Program,         # Program[T]
    Effect,          # Base effect type
    EffectGenerator, # Generator[Effect | Program, Any, T]
    Result,          # Ok[T] | Err[E]
    Ok,              # Success value
    Err,             # Error value

    # Execution
    run, async_run,
    default_handlers(), default_handlers(),
    RuntimeResult,   # Result container
)
```

### Type Variables

```python
from typing import TypeVar

T = TypeVar("T")  # Return type
U = TypeVar("U")  # Transformed type
P = ParamSpec("P")  # Parameter spec for @do
```

### Function Signatures

```python
# @do function signature
@do
def my_func(x: int, y: str) -> EffectGenerator[bool]:
    ...
# Type: KleisliProgram[(int, str), bool]
# Which is: Callable[[int, str], Program[bool]]

# Manual Program construction
def create_program() -> Program[int]:
    def gen():
        value = yield Get("key")
        return value
    return Program(gen)

# Execution
def execute(prog: Program[T]) -> RuntimeResult[T]:
    return run(prog, default_handlers())
```

### Generic Programs

```python
from typing import TypeVar, Generic

T = TypeVar("T")

@do
def identity(x: T) -> EffectGenerator[T]:
    yield Tell(f"Identity: {x}")
    return x

# Type: KleisliProgram[(T,), T]
result = identity(42)  # Program[int]
result = identity("hello")  # Program[str]
```

## Summary

- **Algebraic effects**: Effects are first-class data values; handlers interpret them. This is the core mental model.
- **One-shot continuations**: Each effect suspends the program, the handler resolves it, and the continuation resumes exactly once.
- **Rust VM**: The runtime manages continuations and handler dispatch for performance.
- **Program[T]**: Lazy, reusable effectful computation producing `T`
- **Effect**: An algebraic effect operation — a data value describing a request (Reader, State, Writer, etc.)
- **Handlers**: Composable, swappable interpreters for effects (`default_handlers()` for the built-in set)
- **@do**: Converts generator functions to Programs using do-notation
- **Generator**: Python's coroutine mechanism implements the suspend/resume of one-shot continuations
- **run/async_run**: Execute programs with provided handlers
- **Environment/Store**: Tracks environment and mutable state during execution
- **RuntimeResult[T]**: Contains final value and raw_store
- **Composition Ops**: map, flat_map, sequence, pure enable combining effectful computations

## Next Steps

- **[Basic Effects](03-basic-effects.md)** - Reader, State, Writer in detail
- **[Async Effects](04-async-effects.md)** - Future, Await, Gather
- **[Error Handling](05-error-handling.md)** - Result type and error handling effects
