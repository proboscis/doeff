# Abstraction Concern: Interpreter Design for Effect Tracking

## Context

We want to add call tracking to doeff to build a tree structure showing which `KleisliProgram` calls lead to which effects. This will help with debugging, profiling, and understanding effect flow.

However, this raises fundamental questions about the interpreter's abstraction.

## Current Architecture

### What the Interpreter Handles

The `ProgramInterpreter._execute_program_loop()` processes a generator and handles two types:

```python
while True:
    current = next(gen)

    if isinstance(current, Program):
        # Execute subroutine recursively
        sub_result = await self.run_async(current, ctx)
        current = gen.send(sub_result.value)

    elif isinstance(current, Effect):
        # Handle primitive effect
        value = await self._handle_effect(current, ctx)
        current = gen.send(value)

    else:
        # Error: unknown type
```

**Interpreter handles:** `Effect | Program`

### Type Hierarchy

```python
# Effect is an ADT (algebraic data type):
Effect = AskEffect | DepEffect | StateGetEffect | IOEffect | ...

# Program is opaque (just a generator wrapper):
@dataclass(frozen=True)
class Program(Generic[T]):
    generator_func: Callable[[], Generator[Effect | Program, Any, T]]
```

### The Asymmetry

- **Effect**: Structured ADT with many subclasses representing different operations
- **Program**: Opaque wrapper around generator, no internal structure exposed

## The Problem

To track which `KleisliProgram` calls lead to which effects, we need to attach metadata to Programs created by KleisliProgram calls.

### Naive Approach: Create New Type

```python
@dataclass(frozen=True)
class KleisliProgramCall(Program[T]):
    """A Program created by calling a KleisliProgram"""
    kleisli_source: KleisliProgram
    function_name: str
    call_args: tuple
    call_kwargs: dict[str, Any]
```

**Issue**: Now the interpreter must handle THREE types:

```python
if isinstance(current, KleisliProgramCall):
    # Track call metadata
elif isinstance(current, Program):
    # Regular program
elif isinstance(current, Effect):
    # Effect
```

This breaks the abstraction! The interpreter should handle a clean set of expression types.

## Traditional Interpreter Design

For comparison, a traditional expression-based interpreter:

```python
class Expr(ABC):
    """Base class for all expressions"""
    pass

class Literal(Expr):
    value: Any

class Variable(Expr):
    name: str

class BinOp(Expr):
    op: str
    left: Expr
    right: Expr

class Call(Expr):
    function: str
    args: list[Expr]

def interpret(expr: Expr, env: Env) -> Value:
    match expr:
        case Literal(v): return v
        case Variable(n): return env[n]
        case BinOp(op, l, r): return apply_op(op, interpret(l), interpret(r))
        case Call(f, args): return call_function(f, [interpret(a) for a in args])
```

**Clean abstraction**: Interpreter handles `Expr`, which is a well-defined ADT.

## What Should Doeff's "Expr" Be?

The generator-based design uses Python generators to encode a Free monad:

```python
def my_program():
    x = yield Ask("config")   # Free (Ask "config") >>= \x ->
    y = yield other_program   # Free (Call other_program) >>= \y ->
    return x + y              # Pure (x + y)
```

When the interpreter iterates:
- `yield Effect` → Execute primitive effect
- `yield Program` → Execute subroutine
- `return value` → Computation complete (Pure case)

So the "expression type" is implicitly: `Step Effect | Call Program | Pure Value`

But we don't reify this structure! The interpreter just sees `Effect | Program`.

## Core Question

**Should the interpreter maintain asymmetric types (`Effect | Program`), or should we unify under a common abstraction?**

## Design Options

### Option 1: Metadata as Field (Asymmetric but Pragmatic)

Add optional metadata to `Program` base class:

```python
@dataclass(frozen=True)
class KleisliCallMetadata:
    kleisli: KleisliProgram
    function_name: str
    args: tuple
    kwargs: dict[str, Any]
    created_at: EffectCreationContext | None

@dataclass(frozen=True)
class Program(Generic[T]):
    generator_func: Callable[[], Generator[Effect | Program, Any, T]]
    call_metadata: KleisliCallMetadata | None = None  # Optional metadata
```

**Interpreter remains unchanged:**
```python
if isinstance(current, Program):
    if current.call_metadata:  # Extract metadata when present
        track_call(current.call_metadata)
    # Execute program...
elif isinstance(current, Effect):
    # Handle effect...
```

**Pros:**
- Minimal change to interpreter
- Metadata doesn't affect semantics (like debug info in LLVM)
- `Effect | Program` abstraction preserved

**Cons:**
- Types remain asymmetric (Effect is ADT, Program is wrapper)
- Feels ad-hoc compared to classic interpreter design

### Option 2: Unify Under `Instruction` Protocol

Create a common base for all executable things:

```python
class Instruction(Protocol):
    """Everything the interpreter can execute"""
    created_at: EffectCreationContext | None

class EffectBase(Instruction):
    """Primitive operations"""
    # Already exists in codebase

@dataclass(frozen=True)
class Program(Instruction):
    """Subroutine call"""
    generator_func: Callable[[], Generator[Instruction, Any, T]]
    call_metadata: KleisliCallMetadata | None = None
```

**Interpreter handles:**
```python
def interpret(instruction: Instruction):
    if isinstance(instruction, Effect):
        # Handle primitive
    elif isinstance(instruction, Program):
        # Handle subroutine
```

**Pros:**
- Symmetric abstraction (like `Expr` in traditional interpreters)
- Clear semantic: "Interpreter executes Instructions"
- Extensible (could add more Instruction types)

**Cons:**
- Adds abstraction layer
- More types for type checker to track
- `Instruction` is just a union anyway

### Option 3: Make Program an Effect

Treat program calls as just another effect:

```python
@dataclass(frozen=True)
class CallEffect(EffectBase):
    """Effect that calls a subroutine program"""
    generator_func: Callable[[], Generator[Effect, Any, T]]
    call_metadata: KleisliCallMetadata | None = None
```

**Interpreter handles:**
```python
# Only Effect now!
if isinstance(effect, CallEffect):
    # Execute subroutine
elif isinstance(effect, AskEffect):
    # Handle ask
# ... etc
```

**Pros:**
- Single type to handle
- Conceptually clean: "everything is an effect"

**Cons:**
- Weird that "call" is an effect
- Breaks intuition (Program vs Effect are fundamentally different)

### Option 4: Keep Current Design, Unwrap Metadata

Don't change `Program` at all. Just unwrap `_InterceptedProgram` layers when needed:

```python
def get_call_metadata(program: Program) -> KleisliCallMetadata | None:
    # Unwrap _InterceptedProgram to find base
    base = program
    while isinstance(base, _InterceptedProgram):
        base = base.base_program

    if isinstance(base, KleisliProgramCall):
        return extract_metadata(base)
    return None
```

**Pros:**
- Zero change to core abstractions
- `_InterceptedProgram` already preserves the original

**Cons:**
- Runtime unwrapping overhead
- Indirect metadata access
- Relies on internal structure of `_InterceptedProgram`

## Resolution: Program = Effect | KleisliProgramCall

After investigation, we've arrived at a clean solution:

### The Correct Abstraction

```python
# Program is a union type (like Expr in traditional interpreters)
Program = Effect | KleisliProgramCall

where:
  - Effect: Primitive operations (Ask, Dep, State, IO, etc.)
  - KleisliProgramCall: Compound computations with bound arguments

# Key insight: Both are concrete, structured types!
```

### How It Works

```python
# Step 1: @do wraps generator function
def some_func(x) -> Generator[Program, Any, T]:
    yield Ask("config")
    return 42

some_func_k = do(some_func)
# some_func_k: KleisliProgram
# some_func_k.func = some_func (generator-creating function)

# Step 2: Calling KleisliProgram returns KPCall (partial application)
some_program = some_func_k('hello')
# some_program: KPCall
# some_program.generator_func = some_func (SAME function!)
# some_program.args = ('hello',)

# Step 3: Interpreter creates generator via to_generator()
gen = some_program.to_generator()
# Calls: some_func('hello')
# Returns: Generator[Program, Any, 42]

# Step 4: Generator yields Programs (Effect | KPCall)
current = next(gen)
# current: Program (either Effect or KPCall)
```

### Why This Is Clean

1. **Symmetric Types**: Both `Effect` and `KPCall` are concrete ADTs
2. **Matches Free Monad**: `Pure | Effect | Call` maps to `PureEffect | Effect | KPCall`
3. **Traditional Interpreter Pattern**: Like `Expr = Literal | BinOp | Call`
4. **Partial Application**: `KPCall` = function + bound args (standard FP pattern)
5. **Call Tracking Built-In**: Metadata naturally lives in `KPCall`

### Comparison to Traditional Interpreter

```python
# Traditional
class Expr(ABC): ...
class Literal(Expr): value: int
class Call(Expr): function: str, args: List[Expr]

interpret(expr: Expr):
    match expr:
        case Literal(v): return v
        case Call(f, args): return call(f, [interpret(a) for a in args])

# Doeff
Program = Effect | KleisliProgramCall

interpret(program: Program):
    match program:
        case Effect(): return handle_effect(program)
        case KleisliProgramCall():
            gen = program.to_generator()
            return run_generator(gen)
```

**Perfect symmetry!** The abstraction is clean and matches PL theory.

## Questions Answered

1. **Is the asymmetry a problem?** → No asymmetry! Both are concrete ADTs.
2. **Should we match traditional interpreter design?** → We do! `Program = Effect | KPCall`
3. **Is metadata semantic?** → Yes, but it's part of the type (KPCall), not ad-hoc
4. **What's the right mental model?** → "Interpreter handles two instruction types" ✓
5. **Future extensibility?** → Easy to add more Program types to the union

## Implementation Impact

The choice affects:
- **Call tracking**: How we attach/extract metadata
- **Interception**: How `Program.intercept()` preserves metadata
- **Type safety**: What the type checker can verify
- **Future features**: How easy to add new "instruction" types

## References

- `doeff/program.py`: Program class definition
- `doeff/interpreter.py:208-258`: Main interpreter loop
- `doeff/types.py:455-491`: Effect protocol and EffectBase
- `doeff/kleisli.py:156-218`: KleisliProgram implementation

---

**Question**: Which option feels cleanest from a programming language design perspective? Or is there a better approach I'm missing?