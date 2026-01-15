# Core Concepts

This chapter explores the fundamental architecture of doeff: how Programs work, what Effects are, and how the execution model operates.

## Table of Contents

- [Program[T] - The Core Abstraction](#programt---the-core-abstraction)
- [Effect Protocol](#effect-protocol)
- [The @do Decorator](#the-do-decorator)
- [Generator-Based Do-Notation](#generator-based-do-notation)
- [Execution Model](#execution-model)
- [Monadic Operations](#monadic-operations)
- [Type System](#type-system)

## Program[T] - The Core Abstraction

`Program[T]` is the fundamental building block - a lazy, reusable computation that produces a value of type `T`.

### Structure

```python
@dataclass(frozen=True)
class Program(Generic[T]):
    generator_func: Callable[[], Generator[Effect | Program, Any, T]]
```

A Program wraps a **generator function** that:
1. Yields `Effect` or `Program` instances to request operations
2. Eventually returns a value of type `T`
3. Can be called multiple times to create fresh generators

### Key Properties

**Lazy Evaluation**
```python
@do
def expensive_computation():
    yield Log("This doesn't execute yet")
    return perform_heavy_work()

# No execution happens here
program = expensive_computation()

# Execution happens here
runtime = create_runtime()
result = await runtime.run(program)
```

**Reusability**
```python
@do
def get_timestamp():
    import time
    return yield IO(lambda: time.time())

# Unlike generators, this works:
runtime = create_runtime()
prog = get_timestamp()
result1 = await runtime.run(prog)  # 1234567890.123
result2 = await runtime.run(prog)  # 1234567890.456
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
log_program = Log("test")
```

**Method 4: Manual construction (advanced)**
```python
def generator_func():
    value = yield Get("key")
    return value + 1

program = Program(generator_func)
```

## Effect Protocol

Effects are the vocabulary of doeff programs. An effect represents a request for an operation.

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
| **Writer** | `Log`, `Tell`, `Listen` | Accumulate output/logs |
| **Future** | `Await`, `Parallel` | Async operations |
| **Result** | `Fail`, `Catch`, `Retry` | Error handling |
| **IO** | `IO`, `Print` | Side effects |
| **Cache** | `CacheGet`, `CachePut` | Caching with policies |
| **Graph** | `Step`, `Annotate`, `Snapshot` | Execution tracking |
| **Atomic** | `AtomicGet`, `AtomicUpdate` | Thread-safe state |
| **Dep** | `Dep` | Dependency injection |

### Effect Lifecycle

1. **Creation**: Effect is instantiated with parameters
2. **Yielding**: Effect is yielded in a `@do` function
3. **Interpretation**: EffectRuntime handles the effect
4. **Resolution**: Effect produces a value
5. **Continuation**: Value is sent back to the generator

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

Then add a handler to `EffectRuntime`.

## The @do Decorator

The `@do` decorator is the bridge between Python generators and monadic do-notation.

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
    value = yield Catch(
        risky_operation(),
        lambda e: "fallback"
    )
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

1. **Generator Protocol**: `yield` suspends execution and produces a value
2. **Effect Yielding**: The yielded value is an Effect instance
3. **Interpreter Loop**: ProgramInterpreter processes each effect
4. **Value Sending**: Result is sent back via `.send(value)`
5. **Continuation**: Generator resumes with the value

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

## Execution Model

### EffectRuntime

The runtime executes programs by processing effects:

```python
from doeff import create_runtime, EffectRuntime

# Create runtime with default scheduler
runtime = create_runtime()

# Or configure with custom scheduler/handlers
runtime = EffectRuntime(scheduler=my_scheduler, handlers=my_handlers)

# Execute program
result = await runtime.run(
    program,
    env={"key": "value"},  # Optional environment
    store={"state": 123}   # Optional initial store
)
```

### Execution Steps

1. **Initialize State**: Create CESK machine state from program, environment, and store
2. **Create Generator**: Call `program.generator_func()`
3. **Process Loop**:
   - Get next yielded value
   - If `Program`: recursively run it
   - If `Effect`: handle via appropriate handler
   - Send result back to generator
4. **Handle Completion**: Catch `StopIteration` and extract return value
5. **Return Result**: `RuntimeResult` with value

### Environment and Store

The runtime uses environment and store for execution:

```python
# Environment: Read-only configuration (Ask/Local effects)
env = {"database_url": "postgres://...", "api_key": "..."}

# Store: Mutable state (Get/Put/Modify effects)
store = {"counter": 0, "status": "ready"}

# Pass to runtime
result = await runtime.run(program, env=env, store=store)
```

### RuntimeResult

Contains execution outcome:

```python
@dataclass
class RuntimeResult(Generic[T]):
    result: Result[T]            # Ok(value) or Err(error)
    captured_traceback: Any      # Traceback for debugging
    
    @property
    def value(self) -> T:
        """Extract value (raises if Err)"""
    
    @property
    def is_ok(self) -> bool:
        """Check if succeeded"""
    
    @property
    def is_err(self) -> bool:
        """Check if failed"""
    
    @property
    def error(self) -> BaseException:
        """Extract error (raises if Ok)"""
```

### Stack Safety

doeff uses trampolining to prevent stack overflow:

```python
def force_eval(prog: Program[T]) -> Program[T]:
    """Trampoline for stack safety with deep monadic chains"""
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

## Monadic Operations

`Program[T]` is a monad, providing standard monadic operations.

### Functor: map

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

### Monad: flat_map

Chain computations (monadic bind):

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

### Sequencing: then

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
single_log = Log("test")
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
    EffectRuntime,   # Runtime executor
    create_runtime,  # Factory function
    RuntimeResult,   # Result container
    Result,          # Ok[T] | Err[E]
    Ok,              # Success value
    Err,             # Error value
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

# Runtime execution
async def execute(prog: Program[T]) -> RuntimeResult[T]:
    runtime = create_runtime()
    return await runtime.run(prog)
```

### Generic Programs

```python
from typing import TypeVar, Generic

T = TypeVar("T")

@do
def identity(x: T) -> EffectGenerator[T]:
    yield Log(f"Identity: {x}")
    return x

# Type: KleisliProgram[(T,), T]
result = identity(42)  # Program[int]
result = identity("hello")  # Program[str]
```

## Summary

- **Program[T]**: Lazy, reusable computation producing `T`
- **Effect**: Request for an operation (Reader, State, Writer, etc.)
- **@do**: Converts generator functions to Programs with monadic operations
- **Generator**: Python's coroutine mechanism enables do-notation syntax
- **EffectRuntime**: Executes programs by handling effects
- **Environment/Store**: Tracks environment and mutable state during execution
- **RuntimeResult[T]**: Contains final value and result
- **Monadic Ops**: map, flat_map, sequence, pure enable composition

## Next Steps

- **[Basic Effects](03-basic-effects.md)** - Reader, State, Writer in detail
- **[Async Effects](04-async-effects.md)** - Future, Await, Parallel
- **[Error Handling](05-error-handling.md)** - Result monad and error effects
