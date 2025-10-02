# Program Architecture Refactoring

## Executive Summary

**Proposal**: Refactor `Program` from a concrete wrapper class to a protocol/union, where only `KleisliProgramCall` holds generator functions. Effect becomes a first-class Program type.

**Key Change**:
```python
# Before
Program = wrapper class holding generator_func
  where generator yields: Effect | Program

# After
Program = Effect | KleisliProgramCall
  where only KleisliProgramCall holds generator_func
```

## Current Architecture

### Type Hierarchy (Before)

```python
@dataclass(frozen=True)
class Program(Generic[T]):
    """Generic wrapper around generator"""
    generator_func: Callable[[], Generator[Effect | Program, Any, T]]

    def map(self, f): ...
    def flat_map(self, f): ...
    def intercept(self, transform): ...

# Everything returns Program
Program.pure(5)              # Returns Program wrapping generator
Program.from_effect(Ask("x")) # Returns Program wrapping generator
kleisli_program(args)        # Returns Program wrapping generator
```

### Interpreter Handles

```python
YieldValue = Effect | Program

while True:
    if isinstance(current, Program):
        # Recursive call
    elif isinstance(current, Effect):
        # Primitive operation
```

**Issue**: Asymmetric - `Effect` is structured ADT, `Program` is opaque wrapper.

## Proposed Architecture

### Type Hierarchy (After)

```python
class Program(Protocol[T]):
    """Interface for all executable computations"""
    def map(self, f: Callable[[T], U]) -> Program[U]: ...
    def flat_map(self, f: Callable[[T], Program[U]]) -> Program[U]: ...
    def intercept(self, transform: Callable[[Effect], Effect | Program]) -> Program[T]: ...

# Effect implements Program
@dataclass(frozen=True)
class EffectBase(Program):
    """Primitive operations - directly executable"""
    created_at: EffectCreationContext | None

    def map(self, f):
        """Map by wrapping in KPCall"""
        def mapped_gen():
            value = yield self
            return f(value)
        return KleisliProgramCall.create(mapped_gen)

# KleisliProgramCall implements Program
@dataclass(frozen=True)
class KleisliProgramCall(Program[T]):
    """
    Compound computation with bound arguments.

    Like partial application: holds the generator-creating function + its arguments.
    Call to_generator() to create the actual generator.
    """
    generator_func: Callable[P, Generator[Program, Any, T]]
    # ^ The generator-creating function (same as in KleisliProgram!)
    args: tuple  # Bound arguments
    kwargs: dict[str, Any]

    def to_generator(self) -> Generator[Program, Any, T]:
        """Create generator by calling generator_func(*args, **kwargs)"""
        return self.generator_func(*self.args, **self.kwargs)

    # Metadata (source of the call)
    kleisli_source: KleisliProgram | None  # The KleisliProgram that created this
    function_name: str
    args: tuple  # Arguments passed to the KleisliProgram
    kwargs: dict[str, Any]
    created_at: EffectCreationContext | None

    def map(self, f):
        """Map over result"""
        def mapped_gen():
            value = yield self
            return f(value)
        return KleisliProgramCall.create(mapped_gen)

# Type alias
Program = Effect | KleisliProgramCall
```

### Special Effect: PureEffect

To handle `Program.pure(value)`:

```python
@dataclass(frozen=True)
class PureEffect(EffectBase):
    """Represents immediate value (Pure case of Free monad)"""
    value: Any
```

Interpreter handles:
```python
async def handle_effect(self, effect: Effect) -> Any:
    if isinstance(effect, PureEffect):
        return effect.value
    # ... other effects
```

### What Holds Generator Functions?

| Construct | What It Holds | What It Returns |
|-----------|---------------|-----------------|
| `def f(x): yield ...` | Nothing (raw function) | `Generator[Program, Any, T]` when called |
| `@do def f(x): ...` | `Callable[P, Generator[Program, Any, T]]` | `KleisliProgram` (unbound) |
| `kleisli_prog(x=1)` | Nothing (calls KP.__call__) | `KleisliProgramCall` (bound function + args) |
| `kpcall.to_generator()` | Nothing (calls generator_func with args) | `Generator[Program, Any, T]` (actual generator!) |
| `Program.pure(5)` | Nothing | `PureEffect(5)` (no generator) |
| `Program.from_effect(Ask("x"))` | Nothing | `Ask("x")` directly |
| `ask_x.map(f)` | Nothing | `KleisliProgramCall` (wraps in generator function) |

**Distinction:**
- **KleisliProgram**: Unbound (holds `Callable[P, Generator[Program, Any, T]]`)
- **KleisliProgramCall**: Bound (holds SAME function + args, like partial application)
- **Effect**: Primitive (no generator, directly executable)

**Key Insight**: `KP.func` and `KPCall.generator_func` are THE SAME FUNCTION!
```python
kp = do(some_func)          # kp.func = some_func
kpcall = kp('hello')        # kpcall.generator_func = some_func (same!)
gen = kpcall.to_generator() # Calls: some_func('hello')
```

**Generator yields**: `Program` where `Program = Effect | KPCall`

## Semantic Validation

### Free Monad Structure

The Free monad in Haskell:
```haskell
data Free f a where
  Pure :: a -> Free f a           -- Immediate value
  Free :: f (Free f a) -> Free f a -- Effect with continuation
```

Our mapping:
```python
Free Effect T = PureEffect(T)                    # Pure case
              | AskEffect | DepEffect | ...      # Primitive effects
              | KleisliProgramCall               # Compound computation
```

This is **semantically correct**! The structure matches the Free monad perfectly.

### Interpreter Perspective

```python
Instruction = Effect | KleisliProgramCall

where:
  - Effect: Primitive/atomic operations (+ PureEffect for termination)
  - KleisliProgramCall: Compound/composite operations (holds generator)
```

This is **symmetric and clean**! Both are concrete, structured types.

### Traditional Interpreter Comparison

```python
# Traditional
class Expr(ABC): ...
class Literal(Expr): value: int
class Call(Expr): function: str, args: List[Expr]

interpret(expr: Expr):
    match expr:
        case Literal(v): return v
        case Call(f, args): return call(f, [interpret(a) for a in args])

# Doeff (Proposed)
Program = Effect | KleisliProgramCall

interpret(program: Program):
    match program:
        case Effect(): return handle_effect(program)
        case KleisliProgramCall(): return run_generator(program)
```

**Clean abstraction!** Matches traditional interpreter design.

## Implementation Details

### How map/flat_map Work

#### On Effect
```python
class EffectBase:
    def map(self, f: Callable[[T], U]) -> Program[U]:
        """Effect doesn't have value yet, so wrap in KPCall"""
        def mapped_gen():
            value = yield self
            return f(value)
        return KleisliProgramCall.create_anonymous(mapped_gen)

    def flat_map(self, f: Callable[[T], Program[U]]) -> Program[U]:
        def flatmapped_gen():
            value = yield self
            next_prog = f(value)
            result = yield next_prog
            return result
        return KleisliProgramCall.create_anonymous(flatmapped_gen)
```

#### On KleisliProgramCall
```python
class KleisliProgramCall:
    def map(self, f: Callable[[T], U]) -> KleisliProgramCall[U]:
        def mapped_gen():
            # Run original computation
            gen = self.generator_func()
            try:
                current = next(gen)
                while True:
                    value = yield current
                    current = gen.send(value)
            except StopIteration as e:
                # Apply f to result
                return f(e.value)

        return KleisliProgramCall.create_derived(
            mapped_gen,
            parent=self
        )
```

### How intercept Works

#### On Effect
```python
class EffectBase:
    def intercept(self, transform: Callable[[Effect], Effect | Program]) -> Program:
        """Transform this effect"""
        transformed = transform(self)
        if isinstance(transformed, Effect):
            return transformed
        elif isinstance(transformed, KleisliProgramCall):
            return transformed
        else:
            raise TypeError(...)
```

#### On KleisliProgramCall
```python
class KleisliProgramCall:
    def intercept(self, transform) -> KleisliProgramCall:
        """Apply transform to all yielded effects"""
        # Existing _InterceptedProgram logic, but return KPCall
        return _InterceptedProgram.compose(self, (transform,))
```

### KleisliProgram vs KleisliProgramCall

**KleisliProgram** (defined in `doeff/kleisli.py`):
```python
@dataclass(frozen=True)
class KleisliProgram(Generic[P, T]):
    """
    Holds a generator-creating function (unbound).

    The @do decorator wraps the original generator function here.
    Calling with arguments produces a KPCall (partial application).
    """
    func: Callable[P, Generator[Program, Any, T]]
    # ^ The original generator-creating function
    # ^ NOT Callable[P, Program[T]] - it's the raw generator function!
    # ^ Where Program = Effect | KPCall

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> KleisliProgramCall[T]:
        """Bind arguments, return KPCall (like partial application)"""
        # ... unwrapping logic for Program arguments ...

        return KleisliProgramCall.create_from_kleisli(
            generator_func=self.func,  # SAME function (not called yet!)
            kleisli=self,
            args=args,  # Captured arguments
            kwargs=kwargs
        )
```

**KleisliProgramCall** (new class in `doeff/program.py`):
```python
@dataclass(frozen=True)
class KleisliProgramCall(Generic[T]):
    """
    Holds generator-creating function + bound arguments.

    Like a partially applied function - has the function and its arguments,
    but hasn't been called yet. Call to_generator() to create the generator.
    """
    generator_func: Callable[P, Generator[Program, Any, T]]
    # ^ The SAME function as KleisliProgram.func (captured for this call)
    # ^ Where Program = Effect | KPCall

    args: tuple  # Bound arguments
    kwargs: dict[str, Any]

    kleisli_source: KleisliProgram | None  # Which KP created this
    function_name: str
    created_at: EffectCreationContext | None

    def to_generator(self) -> Generator[Program, Any, T]:
        """Create the generator by calling the function with bound args"""
        return self.generator_func(*self.args, **self.kwargs)
        # ^ NOW the generator is created!

    @classmethod
    def create_from_kleisli(
        cls,
        generator_func: Callable[[], Generator],
        kleisli: KleisliProgram,
        args: tuple,
        kwargs: dict
    ) -> KleisliProgramCall[T]:
        """Create from KleisliProgram.__call__ (knows its source)"""
        return cls(
            generator_func=generator_func,
            kleisli_source=kleisli,
            function_name=getattr(kleisli, '__name__', '<unknown>'),
            args=args,
            kwargs=kwargs,
            created_at=capture_creation_context(skip_frames=2)
        )

    @classmethod
    def create_anonymous(
        cls,
        generator_func: Callable[[], Generator]
    ) -> KleisliProgramCall[T]:
        """Create from map/flat_map (no source KleisliProgram)"""
        return cls(
            generator_func=generator_func,
            kleisli_source=None,
            function_name='<anonymous>',
            args=(),
            kwargs={},
            created_at=None
        )

    @classmethod
    def create_derived(
        cls,
        generator_func: Callable[[], Generator],
        parent: KleisliProgramCall
    ) -> KleisliProgramCall[T]:
        """Create from transforming another KPCall (preserve metadata)"""
        return cls(
            generator_func=generator_func,
            kleisli_source=parent.kleisli_source,
            function_name=parent.function_name,
            args=parent.args,
            kwargs=parent.kwargs,
            created_at=parent.created_at
        )
```

### Interpreter Changes

```python
async def _execute_program_loop(
    self, program: Program[T], ctx: ExecutionContext
) -> RunResult[T]:
    """Execute a Program (Effect or KleisliProgramCall)"""

    # If it's just an Effect, handle it directly
    if isinstance(program, Effect):
        value = await self._handle_effect(program, ctx)
        return RunResult(ctx, Ok(value))

    # Must be KleisliProgramCall - run the generator
    if not isinstance(program, KleisliProgramCall):
        raise TypeError(f"Expected Effect or KleisliProgramCall, got {type(program)}")

    # Track call if it has source metadata
    if program.kleisli_source is not None:
        frame = CallFrame(
            kleisli=program.kleisli_source,
            function_name=program.function_name,
            args=program.args,
            kwargs=program.kwargs,
            depth=len(ctx.program_call_stack),
            created_at=program.created_at
        )
        ctx.program_call_stack.append(frame)

    try:
        # Create generator by calling with bound arguments
        gen = program.to_generator()
        # ^ Calls: generator_func(*args, **kwargs)
        # ^ Returns: Generator[Program, Any, T]

        # Start generator
        try:
            current = next(gen)
        except StopIteration as e:
            return RunResult(ctx, Ok(e.value))

        # Process yielded values
        while True:
            if isinstance(current, KleisliProgramCall):
                # Recursive call to another KPCall
                sub_result = await self.run_async(current, ctx)
                if isinstance(sub_result.result, Err):
                    return sub_result
                ctx = sub_result.context
                try:
                    current = gen.send(sub_result.value)
                except StopIteration as e:
                    return RunResult(ctx, Ok(e.value))

            elif isinstance(current, Effect):
                # Handle effect
                try:
                    value = await self._handle_effect(current, ctx)
                except Exception as exc:
                    # ... error handling ...
                    return RunResult(ctx, Err(effect_failure))

                try:
                    current = gen.send(value)
                except StopIteration as e:
                    return RunResult(ctx, Ok(e.value))
            else:
                return RunResult(ctx, Err(TypeError(f"Unknown yield type: {type(current)}")))
    finally:
        if program.kleisli_source is not None:
            ctx.program_call_stack.pop()
```

## Benefits of This Refactoring

### 1. Clean Abstraction
- **Before**: `Effect | Program` (asymmetric - Effect is ADT, Program is opaque)
- **After**: `Effect | KleisliProgramCall` (symmetric - both are concrete ADTs)

### 2. Semantic Clarity
- `Effect`: Primitive operation
- `KleisliProgramCall`: Compound computation with call metadata
- Matches Free monad structure precisely

### 3. Call Tracking Built-In
- Every `KleisliProgramCall` carries metadata (source, args, location)
- No need for separate tracking mechanism
- `intercept()` automatically preserves metadata

### 4. Type Safety
- Type checker knows exactly what holds generator functions
- `Program` protocol ensures uniform interface
- No confusion about when metadata is available

### 5. Interpreter Simplicity
- Clean dispatch: Effect vs KleisliProgramCall
- Call stack tracking is trivial (check `kleisli_source`)
- Matches traditional interpreter design patterns

## Potential Issues & Solutions

### Issue 1: Breaking Change
**Problem**: All code using `Program(generator_func)` breaks.

**Solution**:
- Provide migration helper: `Program.wrap(gen_func)` → `KleisliProgramCall.create_anonymous(gen_func)`
- Most user code uses `@do` decorator (unchanged) or `Program.pure/from_effect` (minor API change)

### Issue 2: Type Annotations
**Problem**: Code has `-> Program[int]` everywhere.

**Solution**:
- `Program = Effect | KleisliProgramCall` still works as type annotation
- Return type remains valid (both Effect and KPCall satisfy it)
- No changes needed in user code type signatures

### Issue 3: _InterceptedProgram
**Problem**: `_InterceptedProgram` currently wraps `Program`.

**Solution**:
- Make `_InterceptedProgram` a subclass of `KleisliProgramCall`
- It already has `base_program` and `transforms` - these become metadata fields
- Composition logic unchanged

### Issue 4: Combinators (sequence, first_success, etc.)
**Problem**: These currently return `Program`.

**Solution**:
- They wrap results in `KleisliProgramCall.create_anonymous`
- Example:
  ```python
  def sequence(programs: list[Program[T]]) -> Program[list[T]]:
      def sequence_gen():
          effect = gather(*programs)
          results = yield effect
          return list(results)
      return KleisliProgramCall.create_anonymous(sequence_gen)
  ```

## Migration Strategy

See `todo.md` for detailed phase breakdown.

**High-level steps**:
1. Add `PureEffect` and `KleisliProgramCall` alongside existing `Program`
2. Update `KleisliProgram.__call__` to return `KleisliProgramCall`
3. Migrate helpers (`pure`, `from_effect`) to return Effects
4. Update interpreter to handle both old and new types
5. Deprecate old `Program` class
6. Remove old `Program` class

## Success Criteria

- ✅ Interpreter handles `Effect | KleisliProgramCall` cleanly
- ✅ Call tracking works automatically via `KleisliProgramCall` metadata
- ✅ `map/flat_map/intercept` work on both Effect and KPCall
- ✅ All existing tests pass
- ✅ Type checker validates new structure
- ✅ Effect call tree can be built from `ctx.program_call_stack`

## Open Questions

1. Should `Program` be a Protocol or a type alias?
   - Protocol: Better for type checker, explicit interface
   - Alias: Simpler, more direct

2. Should `_InterceptedProgram` be merged into `KleisliProgramCall`?
   - Pro: Fewer classes
   - Con: Mixing concerns (interception vs metadata)

3. How to handle `Program.sequence`, `Program.first_success` etc.?
   - Keep as static methods on Protocol?
   - Move to standalone functions?
   - Module-level helpers?

## References

- Current `Program` implementation: `doeff/program.py:25-416`
- Interpreter loop: `doeff/interpreter.py:208-258`
- KleisliProgram: `doeff/kleisli.py:156-321`
- Effect base: `doeff/types.py:470-491`
