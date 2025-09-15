"""
Comprehensive pragmatic experiment: Supporting all monad types (Refactored).

This explores how to handle:
- Reader: Environment dependencies (forced via env)
- State: Mutable state (threaded through computation)
- Writer: Log accumulation (collected during execution)
- Future: Async operations (forced via await)
- Result: Error handling (preserved as data)
- IO: Side effects (executed immediately)

================================================================================
ARCHITECTURAL DECISIONS AND PYTHON LIMITATIONS
================================================================================

This implementation represents a pragmatic approach to free monads in Python,
making specific trade-offs due to Python's limitations and prioritizing
usability over theoretical purity.

1. GENERATOR-BASED SUSPENDING MONADS
-------------------------------------
We use Python generators with yield for monadic do-notation because:
- Python lacks true lazy evaluation primitives
- Generators provide built-in suspension/resumption via yield/send
- The yield keyword naturally expresses effect suspension points
- Familiar Python syntax reduces the learning curve

Limitation: Generators execute until the first yield (not truly lazy),
and the generator protocol forces us to use Any for send types, losing
type safety at suspension points.

2. STRING-TAGGED EFFECTS VS TYPE-BASED DISPATCH
------------------------------------------------
We use Effect(tag="string", payload=Any) instead of type-based dispatch:
- Python lacks GADTs and proper sum types
- String tags are simple, extensible, and debuggable
- Runtime interpretation allows dynamic effect handling
- Single Effect type simplifies the interpreter

Trade-off: No compile-time type safety for effects. Typos in effect tags
are runtime errors, and payload types are unchecked.

3. STACK SAFETY THROUGH FORCE EVALUATION
-----------------------------------------
Python's recursion limit (~1000 frames) requires trampolining:
- force_eval flattens nested generator chains
- Prevents stack overflow in deep monadic computations (10,000+ ops)
- Essential for production use with complex workflows

Trade-off: Loses laziness benefits and adds complexity to execution.

4. UNIFIED ExecutionContext INSTEAD OF MONAD TRANSFORMERS
----------------------------------------------------------
Single context containing all monad states instead of transformer stacks:
- Avoids complex type-level monad composition
- All effects available simultaneously
- Simple to understand and modify
- Better IDE support and debugging

Trade-off: Cannot restrict effects at type level. All programs have
access to all effects, no compile-time effect checking.

5. THE @do DECORATOR PATTERN
-----------------------------
Decorator transforms generators into Program objects:
- Clean syntax similar to Haskell's do-notation
- Automatic Program wrapping
- Preserves generator function for reuse

Trade-off: Changes function signature from () -> T to () -> Program,
which can be confusing and breaks type hints.

6. GENERATOR TYPE AMBIGUITY
----------------------------
Python's Generator[YieldType, SendType, ReturnType] doesn't map cleanly
to monadic patterns:
- YieldType = Effect (what we suspend on)
- SendType = Any (what the runtime sends back)
- ReturnType = T (the final monadic value)

This is a fundamental mismatch between Python's generator protocol and
monadic composition patterns.

7. ASYNC/AWAIT INTEGRATION
--------------------------
Mixing sync generators with async operations requires wrapping:
- Future effect wraps async operations
- ProgramInterpreter runs async internally
- Allows sync-looking code to perform async operations

Trade-off: Additional layer of abstraction over native async/await.

8. PRACTICAL OVER PURE
----------------------
Key principle: This implementation prioritizes Python developer experience
over theoretical correctness:
- Effects as methods for better autocomplete
- Multiple API styles (Effects.x.y, Capitalized, lowercase)
- Built-in compatibility with pinjected DI
- Comprehensive effect set out of the box

The goal is a free monad system that Python developers can actually use
in production, not a perfect theoretical implementation.
================================================================================
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    Generator,
    Generic,
    List,
    Optional,
    ParamSpec,
    Tuple,
    TypeVar,
    Union,
)
from datetime import datetime

from doeff._vendor import (
    WGraph,
    WNode,
    WStep,
    Ok,
    Err,
    Result,
    trace_err,
)

T = TypeVar("T")
U = TypeVar("U")
P = ParamSpec("P")


# ============================================
# Comprehensive Effect System
# ============================================


@dataclass(frozen=True)
class Effect:
    """Effect with tag and payload.

    This single type represents ALL effects in our system. We use string tags
    instead of separate types because Python lacks proper sum types/GADTs.
    The trade-off is runtime type checking vs compile-time safety.
    """

    tag: str  # String discrimination instead of type-based
    payload: Any  # Untyped payload - Python can't express effect-specific types


# Reader effects (forced)
# ============================================
# Effects: Organized effect creation API
# ============================================


class Effects:
    """Organized effect creation API with grouped categories."""

    class reader:
        """Reader monad effects (forced evaluation)."""

        @staticmethod
        def ask(key: str) -> Effect:
            """Ask for environment value."""
            return Effect("reader.ask", key)

        @staticmethod
        def local(env_update: Dict[str, Any], sub_program: Any) -> Effect:
            """Run sub-program with modified environment.

            Args:
                env_update: Environment updates to apply
                sub_program: A Program or a thunk that returns a Program
            """
            return Effect("reader.local", {"env": env_update, "program": sub_program})

    class state:
        """State monad effects (threaded through computation)."""

        @staticmethod
        def get(key: str) -> Effect:
            """Get value from state."""
            return Effect("state.get", key)

        @staticmethod
        def put(key: str, value: Any) -> Effect:
            """Update state value."""
            return Effect("state.put", {"key": key, "value": value})

        @staticmethod
        def modify(key: str, f: Callable[[Any], Any]) -> Effect:
            """Modify state value with function."""
            return Effect("state.modify", {"key": key, "func": f})

    class writer:
        """Writer monad effects (accumulated logs)."""

        @staticmethod
        def tell(message: Any) -> Effect:
            """Add to the log."""
            return Effect("writer.tell", message)

        @staticmethod
        def listen(sub_program: Any) -> Effect:
            """Run sub-program and return its log.

            Args:
                sub_program: A Program or a thunk that returns a Program
            """
            return Effect("writer.listen", sub_program)

    class future:
        """Future/async effects (forced evaluation)."""

        @staticmethod
        def await_(awaitable: Awaitable[Any]) -> Effect:
            """Await an async operation."""
            return Effect("future.await", awaitable)

        @staticmethod
        def parallel(*awaitables: Awaitable[Any]) -> Effect:
            """Run multiple async operations in parallel."""
            return Effect("future.parallel", awaitables)

    class result:
        """Result/error handling effects."""

        @staticmethod
        def fail(exc: Exception) -> Effect:
            """Signal failure."""
            return Effect("result.fail", exc)

        @staticmethod
        def catch(sub_program: Any, handler: Callable[[Exception], Any]) -> Effect:
            """Try sub-program with error handler.

            Args:
                sub_program: A Program or a thunk that returns a Program
                handler: Function that takes an Exception and returns a Program or value
            """
            return Effect("result.catch", {"program": sub_program, "handler": handler})

    class io:
        """IO/side-effect operations."""

        @staticmethod
        def run(action: Callable[[], Any]) -> Effect:
            """Execute side effect."""
            return Effect("io.run", action)

        @staticmethod
        def print(message: str) -> Effect:
            """Print to console."""
            return Effect("io.print", message)

    class graph:
        """Graph effects for SGFR compatibility."""

        @staticmethod
        def step(value: Any, meta: Optional[Dict[str, Any]] = None) -> Effect:
            """Add a step to the computation graph."""
            return Effect("graph.step", {"value": value, "meta": meta})

        @staticmethod
        def annotate(meta: Dict[str, Any]) -> Effect:
            """Annotate current graph node."""
            return Effect("graph.annotate", meta)

    class program:
        """Program-specific effects for composition."""

        @staticmethod
        def gather(programs: List["Program"]) -> Effect:
            """Gather multiple Programs and return their values as a list."""
            return Effect("program.gather", programs)

        @staticmethod
        def gather_dict(programs: Dict[str, "Program"]) -> Effect:
            """Gather multiple Programs in a dict and return their values."""
            return Effect("program.gather_dict", programs)


# ============================================
# Capitalized Effect Aliases (idiomatic FP style)
# ============================================


# Reader aliases
def Ask(key: str) -> Effect:
    """Ask for environment value (Reader effect)."""
    return Effects.reader.ask(key)


def Local(env_update: Dict[str, Any], sub_program: Any) -> Effect:
    """Run sub-program with modified environment (Reader effect).

    Args:
        env_update: Environment updates to apply
        sub_program: A Program or a thunk that returns a Program
    """
    return Effects.reader.local(env_update, sub_program)


# State aliases
def Get(key: str) -> Effect:
    """Get value from state (State effect)."""
    return Effects.state.get(key)


def Put(key: str, value: Any) -> Effect:
    """Update state value (State effect)."""
    return Effects.state.put(key, value)


def Modify(key: str, f: Callable[[Any], Any]) -> Effect:
    """Modify state value with function (State effect)."""
    return Effects.state.modify(key, f)


# Writer aliases
def Log(message: Any) -> Effect:
    """Add to the log (Writer effect)."""
    return Effects.writer.tell(message)


def Tell(message: Any) -> Effect:
    """Add to the log (Writer effect)."""
    return Effects.writer.tell(message)


def Listen(sub_program: Any) -> Effect:
    """Run sub-program and return its log (Writer effect).

    Args:
        sub_program: A Program or a thunk that returns a Program
    """
    return Effects.writer.listen(sub_program)


# Future aliases
def Await(awaitable: Awaitable[Any]) -> Effect:
    """Await an async operation (Future effect)."""
    return Effects.future.await_(awaitable)


def Parallel(*awaitables: Awaitable[Any]) -> Effect:
    """Run multiple async operations in parallel (Future effect)."""
    return Effects.future.parallel(*awaitables)


# Result aliases
def Fail(exc: Exception) -> Effect:
    """Signal failure (Result effect)."""
    return Effects.result.fail(exc)


def Catch(sub_program: Any, handler: Callable[[Exception], Any]) -> Effect:
    """Try sub-program with error handler (Result effect).

    Args:
        sub_program: A Program or a thunk that returns a Program
        handler: Function that takes an Exception and returns a Program or value
    """
    return Effects.result.catch(sub_program, handler)


# IO aliases
def IO(action: Callable[[], Any]) -> Effect:
    """Execute side effect (IO effect)."""
    return Effects.io.run(action)


def Print(message: str) -> Effect:
    """Print to console (IO effect)."""
    return Effects.io.print(message)


# Graph aliases
def Step(value: Any, meta: Optional[Dict[str, Any]] = None) -> Effect:
    """Add a step to the computation graph (Graph effect)."""
    return Effects.graph.step(value, meta)


def Annotate(meta: Dict[str, Any]) -> Effect:
    """Annotate current graph node (Graph effect)."""
    return Effects.graph.annotate(meta)


# Program aliases
def Gather(programs: List["Program"]) -> Effect:
    """Gather multiple Programs and return their values as a list (Program effect)."""
    return Effects.program.gather(programs)


def GatherDict(programs: Dict[str, "Program"]) -> Effect:
    """Gather multiple Programs in a dict and return their values (Program effect)."""
    return Effects.program.gather_dict(programs)


# Special aliases
def Dep(key: str) -> Effect:
    """Dependency injection via Reader effect (for pinjected compatibility)."""
    return Effects.reader.ask(key)


# ============================================
# Backwards compatibility - lowercase functions
# ============================================


def ask(key: str) -> Effect:
    """Reader: Ask for environment value."""
    return Effects.reader.ask(key)


def local(env_update: Dict[str, Any], sub_program: Any) -> Effect:
    """Reader: Run sub-program with modified environment.

    Args:
        env_update: Environment updates to apply
        sub_program: A Program or a thunk that returns a Program
    """
    return Effects.reader.local(env_update, sub_program)


def get(key: str) -> Effect:
    """State: Get value from state."""
    return Effects.state.get(key)


def put(key: str, value: Any) -> Effect:
    """State: Update state value."""
    return Effects.state.put(key, value)


def modify(key: str, f: Callable[[Any], Any]) -> Effect:
    """State: Modify state value with function."""
    return Effects.state.modify(key, f)


def tell(message: Any) -> Effect:
    """Writer: Add to the log."""
    return Effects.writer.tell(message)


def listen(sub_program: Any) -> Effect:
    """Writer: Run sub-program and return its log.

    Args:
        sub_program: A Program or a thunk that returns a Program
    """
    return Effects.writer.listen(sub_program)


def await_(awaitable: Awaitable[Any]) -> Effect:
    """Future: Await an async operation."""
    return Effects.future.await_(awaitable)


def parallel(*awaitables: Awaitable[Any]) -> Effect:
    """Future: Run multiple async operations in parallel."""
    return Effects.future.parallel(*awaitables)


def fail(exc: Exception) -> Effect:
    """Result: Signal failure."""
    return Effects.result.fail(exc)


def catch(sub_program: Any, handler: Callable[[Exception], Any]) -> Effect:
    """Result: Try sub-program with error handler.

    Args:
        sub_program: A Program or a thunk that returns a Program
        handler: Function that takes an Exception and returns a Program or value
    """
    return Effects.result.catch(sub_program, handler)


def io(action: Callable[[], Any]) -> Effect:
    """IO: Execute side effect."""
    return Effects.io.run(action)


def print_(message: str) -> Effect:
    """IO: Print to console."""
    return Effects.io.print(message)


def step(value: Any, meta: Optional[Dict[str, Any]] = None) -> Effect:
    """Graph: Add a step to the computation graph."""
    return Effects.graph.step(value, meta)


def annotate(meta: Dict[str, Any]) -> Effect:
    """Graph: Annotate current graph node."""
    return Effects.graph.annotate(meta)


# ============================================
# Result type for pragmatic engine
# ============================================


@dataclass(frozen=True)
class RunResult(Generic[T]):
    """
    Result from running a Program through the pragmatic engine.

    Contains both the execution context (state, log, graph) and the computation result.
    """

    context: ExecutionContext
    result: Result[T]

    @property
    def value(self) -> T:
        """Get the unwrapped value if Ok, raises if Err."""
        if isinstance(self.result, Err):
            raise self.result.error.exc
        return self.result.value

    @property
    def is_ok(self) -> bool:
        """Check if the result is Ok."""
        return isinstance(self.result, Ok)

    @property
    def is_err(self) -> bool:
        """Check if the result is Err."""
        return isinstance(self.result, Err)

    @property
    def graph(self) -> WGraph:
        """Get the computation graph."""
        return self.context.graph

    @property
    def state(self) -> Dict[str, Any]:
        """Get the final state."""
        return self.context.state

    @property
    def log(self) -> List[Any]:
        """Get the accumulated log."""
        return self.context.log

    @property
    def env(self) -> Dict[str, Any]:
        """Get the environment."""
        return self.context.env


# ============================================
# Execution Context (all monad states)
# ============================================


@dataclass
class ExecutionContext:
    """
    Complete execution context containing all monad states.

    ARCHITECTURAL DECISION: UNIFIED CONTEXT VS MONAD TRANSFORMERS

    Instead of complex monad transformer stacks (ReaderT (StateT (WriterT IO))),
    we use a single flat context containing all states. This is a deliberate
    trade-off prioritizing Python usability:

    Advantages:
    - Simple to understand and debug
    - All effects always available (no lifting)
    - Better IDE support and introspection
    - Easy to extend with new effect states

    Disadvantages:
    - No compile-time effect restrictions
    - All programs can access all effects
    - Less type safety than transformer stacks

    The context is split into two categories:

    Forced (not returned):
    - env: Reader environment (read-only, shared)
    - io_allowed: Whether IO is permitted (capability flag)

    Threaded (returned):
    - state: State dictionary (mutable, threaded)
    - log: Writer log (append-only, accumulated)
    - graph: Computation graph (append-only, tracked)

    This design reflects our "practical over pure" philosophy - we sacrifice
    theoretical purity for a system that Python developers can actually use.
    """

    # Forced during execution
    env: Dict[str, Any] = field(default_factory=dict)
    io_allowed: bool = True

    # Threaded and returned
    state: Dict[str, Any] = field(default_factory=dict)
    log: List[Any] = field(default_factory=list)
    graph: WGraph = field(default_factory=lambda: WGraph.single(None))

    def copy(self) -> ExecutionContext:
        """Create a copy of the context."""
        return ExecutionContext(
            env=self.env.copy(),
            io_allowed=self.io_allowed,
            state=self.state.copy(),
            log=self.log.copy(),
            graph=self.graph,
        )


# ============================================
# Program type and @do decorator
# ============================================


@dataclass(frozen=True)
class KleisliProgram(Generic[P, T]):
    """
    A Kleisli arrow that represents a function from parameters to Program[T].

    This class enables automatic unwrapping of Program arguments when called,
    allowing natural composition of Programs. When called with some arguments
    being Programs, it will automatically yield them to unwrap their values
    before passing them to the underlying function.

    Example:
        @do
        def add(x: int, y: int) -> Generator[..., ..., int]:
            return x + y

        # add is now KleisliProgram[(x: int, y: int), int]

        prog_x = Program.pure(5)
        result = add(x=prog_x, y=10)  # KleisliProgram unwraps prog_x automatically
    """

    func: Callable[P, "Program[T]"]

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> "Program[T]":
        """
        Call the Kleisli arrow, automatically unwrapping any Program arguments.

        This method uses the Gather effect to efficiently unwrap multiple Program
        arguments at once, then passes the unwrapped values to the underlying function.
        """

        def unwrapping_generator() -> Generator[Union[Effect, "Program"], Any, T]:
            # Collect Program arguments and their indices/keys
            program_args = []
            program_indices = []
            regular_args = []

            for i, arg in enumerate(args):
                if isinstance(arg, Program):
                    program_args.append(arg)
                    program_indices.append(i)
                    regular_args.append(None)  # Placeholder
                else:
                    regular_args.append(arg)

            program_kwargs = {}
            regular_kwargs = {}

            for key, value in kwargs.items():
                if isinstance(value, Program):
                    program_kwargs[key] = value
                else:
                    regular_kwargs[key] = value

            # Gather all Program arguments at once if there are any
            if program_args or program_kwargs:
                # Gather positional Program arguments
                if program_args:
                    unwrapped_args_values = yield Gather(program_args)
                    # Place unwrapped values back in their positions
                    for idx, value in zip(program_indices, unwrapped_args_values):
                        regular_args[idx] = value

                # Gather keyword Program arguments
                if program_kwargs:
                    unwrapped_kwargs_values = yield GatherDict(program_kwargs)
                    # Merge unwrapped values with regular kwargs
                    regular_kwargs.update(unwrapped_kwargs_values)

            # Call the original function with unwrapped arguments
            # The function already returns a Program[T]
            result_program = self.func(*regular_args, **regular_kwargs)

            # Yield the Program to unwrap its value
            result = yield result_program
            return result

        return Program(unwrapping_generator)


@dataclass(frozen=True)
class Program(Generic[T]):
    """
    A program that can be executed by the engine.

    This is just a container for a generator function that yields effects.
    The engine will call the generator_func to get a fresh generator each time.

    The type parameter T represents the return type of the program.
    """

    generator_func: Callable[[], Generator[Union[Effect, "Program"], Any, T]]

    def __iter__(self):
        """Allow iteration by returning a fresh generator."""
        return self.generator_func()

    def map(self, f: Callable[[T], U]) -> "Program[U]":
        """Map a function over the result of this program (functor map)."""

        def mapped_generator():
            # First run the original program to completion
            gen = self.generator_func()
            try:
                # Check if it's a real generator or just returns immediately
                try:
                    current = next(gen)
                    # It's a real generator, run it
                    while True:
                        value = yield current
                        current = gen.send(value)
                except StopIteration as e:
                    # Generator completed
                    result = getattr(e, "value", None)
                    return f(result)
            except TypeError:
                # Not a generator, it returned immediately
                result = gen
                return f(result)

        return Program(mapped_generator)

    def flat_map(self, f: Callable[[T], "Program[U]"]) -> "Program[U]":
        """Flat map (bind) a function that returns a Program over the result of this program (monadic bind)."""

        def flat_mapped_generator():
            # First run the original program to completion
            gen = self.generator_func()
            result = None
            try:
                # Check if it's a real generator or just returns immediately
                try:
                    current = next(gen)
                    # It's a real generator, run it
                    while True:
                        value = yield current
                        current = gen.send(value)
                except StopIteration as e:
                    # Generator completed
                    result = getattr(e, "value", None)
            except TypeError:
                # Not a generator, it returned immediately
                result = gen

            # Now run the program returned by f
            next_program = f(result)
            # Yield the program to be executed by the engine
            # The engine will run it and return its result
            final_result = yield next_program
            return final_result

        return Program(flat_mapped_generator)

    @staticmethod
    def pure(value: T) -> "Program[T]":
        """Create a Program that returns a pure value (monadic return)."""

        def pure_generator():
            # Make it a proper generator that immediately returns the value
            if False:  # This makes it a generator
                yield
            return value

        return Program(pure_generator)


def do(
    func: Callable[P, Generator[Union[Effect, Program], Any, T]],
) -> KleisliProgram[P, T]:
    """
    Decorator that converts a generator function into a KleisliProgram.

    ARCHITECTURAL SIGNIFICANCE:
    This decorator is the bridge between Python's generator syntax and our
    monadic do-notation. It transforms a generator function into a KleisliProgram,
    which is a Kleisli arrow that enables:
    - Clean syntax similar to Haskell's do-notation
    - Automatic unwrapping of Program arguments
    - Natural composition of Programs
    - Reusable programs (generators are single-use)
    - Deferred execution until run with an engine

    PYTHON LIMITATION WORKAROUND:
    Python generators execute immediately until first yield, but we need
    lazy evaluation. The KleisliProgram wrapper delays generator creation until
    execution time, achieving the laziness we need.

    TYPE SIGNATURE CHANGE:
    @do changes: (args) -> Generator[Union[Effect, Program], Any, T]
    into:        KleisliProgram[P, T] where P is the parameter spec
    This preserves type information and enables automatic Program unwrapping.

    Usage:
        @do
        def my_program(x: int) -> Generator[Union[Effect, Program], Any, str]:
            config = yield ask("config")
            result = yield await_(process(x))
            return f"Result: {result}"

        # Now my_program is a KleisliProgram that returns Program[str] when called
        prog_x = Program.pure(5)
        program = my_program(x=prog_x)  # Automatically unwraps prog_x
        result = await engine.run(program)
    """

    # Wrap the generator function to return a Program
    def program_returning_func(*args: P.args, **kwargs: P.kwargs) -> Program[T]:
        # Create a function that returns a proper generator
        def generator_func():
            result = func(*args, **kwargs)
            # Check if it's already a generator
            if hasattr(result, "__next__"):
                # It's a generator, yield from it
                # IMPORTANT: Must capture and return the final value
                final = yield from result
                return final
            else:
                # It's a direct return value (no yields in the function)
                # Return it directly (StopIteration will have the value)
                return result

        return Program(generator_func)

    return KleisliProgram(program_returning_func)


# ============================================
# Handler classes for each effect category
# ============================================


class ReaderEffectHandler:
    """Handles Reader monad effects."""

    async def handle_ask(self, key: str, ctx: ExecutionContext) -> Any:
        """Handle reader.ask effect."""
        if key not in ctx.env:
            raise KeyError(f"Missing environment key: {key}")
        return ctx.env[key]

    async def handle_local(
        self, payload: Dict, ctx: ExecutionContext, engine: "ProgramInterpreter"
    ) -> Any:
        """Handle reader.local effect."""
        sub_ctx = ctx.copy()
        sub_ctx.env.update(payload["env"])
        # Check if payload["program"] is already a Program or a callable
        sub_program = payload["program"]
        if callable(sub_program) and not isinstance(sub_program, Program):
            # It's a thunk, call it to get the Program
            sub_program = sub_program()
        pragmatic_result = await engine.run(sub_program, sub_ctx)
        if pragmatic_result.is_err:
            raise pragmatic_result.result.error.exc
        return pragmatic_result.value


class StateEffectHandler:
    """Handles State monad effects."""

    async def handle_get(self, key: str, ctx: ExecutionContext) -> Any:
        """Handle state.get effect."""
        return ctx.state.get(key)

    async def handle_put(self, payload: Dict, ctx: ExecutionContext) -> None:
        """Handle state.put effect."""
        ctx.state[payload["key"]] = payload["value"]

    async def handle_modify(self, payload: Dict, ctx: ExecutionContext) -> Any:
        """Handle state.modify effect."""
        key = payload["key"]
        old_value = ctx.state.get(key)
        new_value = payload["func"](old_value)
        ctx.state[key] = new_value
        return new_value


@dataclass(frozen=True)
class ListenResult:
    """Result from writer.listen effect."""

    value: Any
    log: List[Any]

    def __iter__(self):
        """Allow tuple unpacking for backward compatibility."""
        return iter([self.value, self.log])


class WriterEffectHandler:
    """Handles Writer monad effects."""

    async def handle_tell(self, message: Any, ctx: ExecutionContext) -> None:
        """Handle writer.tell effect."""
        ctx.log.append(message)

    async def handle_listen(
        self,
        sub_program_func: Callable,
        ctx: ExecutionContext,
        engine: "ProgramInterpreter",
    ) -> ListenResult:
        """Handle writer.listen effect."""
        # Check if it's already a Program or a callable
        sub_program = sub_program_func
        if callable(sub_program) and not isinstance(sub_program, Program):
            # It's a thunk, call it to get the Program
            sub_program = sub_program()
        sub_ctx = ctx.copy()
        sub_ctx.log = []  # Fresh log for sub-program
        pragmatic_result = await engine.run(sub_program, sub_ctx)
        if pragmatic_result.is_err:
            raise pragmatic_result.result.error.exc
        # Return dataclass instead of tuple
        return ListenResult(value=pragmatic_result.value, log=pragmatic_result.log)


class FutureEffectHandler:
    """Handles Future monad effects."""

    async def handle_await(self, awaitable: Awaitable[Any]) -> Any:
        """Handle future.await effect."""
        return await awaitable

    async def handle_parallel(
        self, awaitables: Tuple[Awaitable[Any], ...]
    ) -> List[Any]:
        """Handle future.parallel effect."""
        results = await asyncio.gather(*awaitables)
        return results


class ResultEffectHandler:
    """Handles Result monad effects."""

    async def handle_fail(self, exc: Exception) -> None:
        """Handle result.fail effect."""
        raise exc

    async def handle_catch(
        self, payload: Dict, ctx: ExecutionContext, engine: "ProgramInterpreter"
    ) -> Any:
        """Handle result.catch effect."""
        # Check if payload["program"] is already a Program or a callable
        sub_program = payload["program"]
        if callable(sub_program) and not isinstance(sub_program, Program):
            # It's a thunk, call it to get the Program
            sub_program = sub_program()

        try:
            pragmatic_result = await engine.run(sub_program, ctx)
            if pragmatic_result.is_err:
                # Run error handler - handler should return a Program
                handler_result_or_program = payload["handler"](
                    pragmatic_result.result.error.exc
                )
                if isinstance(handler_result_or_program, Program):
                    handler_result = await engine.run(handler_result_or_program, ctx)
                    if handler_result.is_err:
                        raise handler_result.result.error.exc
                    return handler_result.value
                else:
                    # Handler returned a value directly
                    return handler_result_or_program
            return pragmatic_result.value
        except Exception as exc:
            # Run error handler
            handler_result_or_program = payload["handler"](exc)
            if isinstance(handler_result_or_program, Program):
                handler_result = await engine.run(handler_result_or_program, ctx)
                if handler_result.is_err:
                    raise handler_result.result.error.exc
                return handler_result.value
            else:
                # Handler returned a value directly
                return handler_result_or_program


class IOEffectHandler:
    """Handles IO monad effects."""

    async def handle_run(self, action: Callable[[], Any], ctx: ExecutionContext) -> Any:
        """Handle io.run effect."""
        if not ctx.io_allowed:
            raise PermissionError("IO not allowed in this context")
        return action()

    async def handle_print(self, message: str, ctx: ExecutionContext) -> None:
        """Handle io.print effect."""
        if not ctx.io_allowed:
            raise PermissionError("IO not allowed in this context")
        print(message)


class GraphEffectHandler:
    """Handles Graph effects for SGFR compatibility."""

    async def handle_step(self, payload: Dict, ctx: ExecutionContext) -> Any:
        """Handle graph.step effect."""
        value = payload["value"]
        meta = payload.get("meta", {})

        # Update graph
        new_node = WNode(value)
        new_step = WStep(inputs=(ctx.graph.last.output,), output=new_node, meta=meta)
        ctx.graph = WGraph(last=new_step, steps=ctx.graph.steps | {new_step})
        return value

    async def handle_annotate(
        self, meta: Dict[str, Any], ctx: ExecutionContext
    ) -> None:
        """Handle graph.annotate effect."""
        ctx.graph = ctx.graph.with_last_meta(meta)


# ============================================
# Comprehensive Engine (Refactored)
# ============================================


class ProgramInterpreter:
    """
    Engine that handles all monad types according to our pragmatic contract.

    Uses separate handler classes for each effect category to maintain
    single responsibility and reduce complexity.
    """

    def __init__(self):
        """Initialize effect handlers."""
        self.reader_handler = ReaderEffectHandler()
        self.state_handler = StateEffectHandler()
        self.writer_handler = WriterEffectHandler()
        self.future_handler = FutureEffectHandler()
        self.result_handler = ResultEffectHandler()
        self.io_handler = IOEffectHandler()
        self.graph_handler = GraphEffectHandler()

        # Dispatch table
        self._dispatchers = {
            "reader.ask": self._dispatch_reader_ask,
            "reader.local": self._dispatch_reader_local,
            "state.get": self._dispatch_state_get,
            "state.put": self._dispatch_state_put,
            "state.modify": self._dispatch_state_modify,
            "writer.tell": self._dispatch_writer_tell,
            "writer.listen": self._dispatch_writer_listen,
            "future.await": self._dispatch_future_await,
            "future.parallel": self._dispatch_future_parallel,
            "result.fail": self._dispatch_result_fail,
            "result.catch": self._dispatch_result_catch,
            "io.run": self._dispatch_io_run,
            "io.print": self._dispatch_io_print,
            "graph.step": self._dispatch_graph_step,
            "graph.annotate": self._dispatch_graph_annotate,
            "program.gather": self._dispatch_program_gather,
            "program.gather_dict": self._dispatch_program_gather_dict,
        }

    async def run(
        self, program: Program[T], context: Optional[ExecutionContext] = None
    ) -> RunResult[T]:
        """
        Run a program with full monad support.

        Returns a RunResult[T] containing:
        - context: final execution context (state, log, graph)
        - result: Ok(value) or Err(error)
        """
        ctx = context or ExecutionContext()

        try:
            gen = iter(program)
            current_yield = next(gen)

            while True:
                try:
                    # Check if yielded value is a Program or an Effect
                    if isinstance(current_yield, Program):
                        # Run the sub-program and get its result
                        sub_result = await self.run(current_yield, ctx)
                        if sub_result.is_err:
                            raise sub_result.result.error.exc
                        value = sub_result.value
                    elif isinstance(current_yield, Effect):
                        # Handle effect and get value
                        value = await self._handle_effect(current_yield, ctx)
                    else:
                        raise TypeError(
                            f"Expected Program or Effect, got {type(current_yield).__name__}"
                        )

                    # Send value back to generator
                    current_yield = gen.send(value)

                except StopIteration as e:
                    # Program completed successfully
                    final_value = getattr(e, "value", None)
                    return RunResult(context=ctx, result=Ok(final_value))

        except StopIteration as e:
            # Program completed without yielding
            final_value = getattr(e, "value", None)
            return RunResult(context=ctx, result=Ok(final_value))

        except Exception as e:
            # Unhandled exception
            return RunResult(context=ctx, result=Err(trace_err(e)))

    async def _handle_effect(self, effect: Effect, ctx: ExecutionContext) -> Any:
        """Handle a single effect using the dispatch table."""
        dispatcher = self._dispatchers.get(effect.tag)
        if dispatcher is None:
            raise ValueError(f"Unknown effect tag: {effect.tag}")
        return await dispatcher(effect, ctx)

    # Reader dispatchers
    async def _dispatch_reader_ask(self, effect: Effect, ctx: ExecutionContext) -> Any:
        return await self.reader_handler.handle_ask(effect.payload, ctx)

    async def _dispatch_reader_local(
        self, effect: Effect, ctx: ExecutionContext
    ) -> Any:
        return await self.reader_handler.handle_local(effect.payload, ctx, self)

    # State dispatchers
    async def _dispatch_state_get(self, effect: Effect, ctx: ExecutionContext) -> Any:
        return await self.state_handler.handle_get(effect.payload, ctx)

    async def _dispatch_state_put(self, effect: Effect, ctx: ExecutionContext) -> Any:
        return await self.state_handler.handle_put(effect.payload, ctx)

    async def _dispatch_state_modify(
        self, effect: Effect, ctx: ExecutionContext
    ) -> Any:
        return await self.state_handler.handle_modify(effect.payload, ctx)

    # Writer dispatchers
    async def _dispatch_writer_tell(self, effect: Effect, ctx: ExecutionContext) -> Any:
        return await self.writer_handler.handle_tell(effect.payload, ctx)

    async def _dispatch_writer_listen(
        self, effect: Effect, ctx: ExecutionContext
    ) -> ListenResult:
        listen_result = await self.writer_handler.handle_listen(
            effect.payload, ctx, self
        )
        # Return the ListenResult dataclass directly
        return listen_result

    # Future dispatchers
    async def _dispatch_future_await(
        self, effect: Effect, ctx: ExecutionContext
    ) -> Any:
        return await self.future_handler.handle_await(effect.payload)

    async def _dispatch_future_parallel(
        self, effect: Effect, ctx: ExecutionContext
    ) -> Any:
        return await self.future_handler.handle_parallel(effect.payload)

    # Result dispatchers
    async def _dispatch_result_fail(self, effect: Effect, ctx: ExecutionContext) -> Any:
        return await self.result_handler.handle_fail(effect.payload)

    async def _dispatch_result_catch(
        self, effect: Effect, ctx: ExecutionContext
    ) -> Any:
        return await self.result_handler.handle_catch(effect.payload, ctx, self)

    # IO dispatchers
    async def _dispatch_io_run(self, effect: Effect, ctx: ExecutionContext) -> Any:
        return await self.io_handler.handle_run(effect.payload, ctx)

    async def _dispatch_io_print(self, effect: Effect, ctx: ExecutionContext) -> Any:
        return await self.io_handler.handle_print(effect.payload, ctx)

    # Graph dispatchers
    async def _dispatch_graph_step(self, effect: Effect, ctx: ExecutionContext) -> Any:
        return await self.graph_handler.handle_step(effect.payload, ctx)

    async def _dispatch_graph_annotate(
        self, effect: Effect, ctx: ExecutionContext
    ) -> Any:
        return await self.graph_handler.handle_annotate(effect.payload, ctx)

    async def _dispatch_program_gather(
        self, effect: Effect, ctx: ExecutionContext
    ) -> List[Any]:
        """Handle gathering multiple Programs - runs them in parallel using asyncio."""
        import asyncio

        programs = effect.payload  # List[Program]

        # Create tasks for parallel execution
        async def run_program(prog: Program) -> tuple[Any, ExecutionContext]:
            # Create a fresh context for each program with shared state/log
            prog_ctx = ExecutionContext(
                env=ctx.env.copy(),
                io_allowed=ctx.io_allowed,
                state=ctx.state.copy(),  # Start with current state
                log=[],  # Fresh log for this program
                graph=ctx.graph,
            )
            # Run the program
            result = await self.run(prog, prog_ctx)
            if result.is_err:
                # Return error to be handled after gathering all results
                raise result.result.error.exc
            return result.value, prog_ctx

        # Run all programs in parallel
        tasks = [run_program(prog) for prog in programs]
        completed_results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results and update context
        results = []
        for i, result in enumerate(completed_results):
            if isinstance(result, Exception):
                # One of the programs failed - propagate the error
                raise result
            value, prog_ctx = result
            # Update the main context with changes from each program
            ctx.state.update(prog_ctx.state)
            ctx.log.extend(prog_ctx.log)
            results.append(value)

        return results

    async def _dispatch_program_gather_dict(
        self, effect: Effect, ctx: ExecutionContext
    ) -> Dict[str, Any]:
        """Handle gathering multiple Programs from a dict - runs them in parallel using asyncio."""
        import asyncio

        programs = effect.payload  # Dict[str, Program]

        # Create tasks for parallel execution
        async def run_program(
            key: str, prog: Program
        ) -> tuple[str, Any, ExecutionContext]:
            # Create a fresh context for each program with shared state/log
            prog_ctx = ExecutionContext(
                env=ctx.env.copy(),
                io_allowed=ctx.io_allowed,
                state=ctx.state.copy(),  # Start with current state
                log=[],  # Fresh log for this program
                graph=ctx.graph,
            )
            # Run the program
            result = await self.run(prog, prog_ctx)
            if result.is_err:
                # Return error to be handled after gathering all results
                raise result.result.error.exc
            return key, result.value, prog_ctx

        # Run all programs in parallel
        tasks = [run_program(key, prog) for key, prog in programs.items()]
        completed_results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results and update context
        results = {}
        for result in completed_results:
            if isinstance(result, Exception):
                # One of the programs failed - propagate the error
                raise result
            key, value, prog_ctx = result
            # Update the main context with changes from each program
            ctx.state.update(prog_ctx.state)
            ctx.log.extend(prog_ctx.log)
            results[key] = value

        return results


# ============================================
# Example: Using all monad types
# ============================================


@do
def comprehensive_example() -> Generator[Effect, Any, Dict[str, Any]]:
    """Example using all monad types."""

    # Reader: Get configuration
    config = yield ask("config")
    yield print_(f"Config: {config}")

    # State: Initialize counter
    yield put("counter", 0)

    # Writer: Log start
    yield tell({"event": "started", "time": datetime.now().isoformat()})

    # IO: Print message
    yield print_("Starting comprehensive example...")

    # Future: Async operation
    data = yield await_(fetch_data())

    # Graph: Add step
    yield step(data, meta={"source": "fetch"})

    # State: Update counter
    yield modify("counter", lambda x: x + len(data))

    # Parallel async operations
    results = yield parallel(
        process_item(data[0]), process_item(data[1]), process_item(data[2])
    )

    # Error handling
    safe_result = yield catch(
        risky_computation(),
        lambda e: {"error": str(e)},  # Simple value return for error case
    )

    # Local environment modification
    inner_result = yield local(
        {"config": {**config, "modified": True}}, inner_computation()
    )

    # Writer: Listen to sub-computation
    listen_result = yield listen(logged_computation)
    yield tell({"sub_log": listen_result.log})

    # State: Get final counter
    final_count = yield get("counter")

    # Graph: Annotate
    yield annotate({"final_count": final_count})

    # IO: Final print
    yield print_(f"Completed with count: {final_count}")

    return {
        "results": results,
        "safe_result": safe_result,
        "inner_result": inner_result,
        "logged_value": listen_result.value,
        "final_count": final_count,
    }


async def fetch_data() -> list:
    """Simulate async data fetch."""
    await asyncio.sleep(0.1)
    return [1, 2, 3, 4, 5]


async def process_item(item: int) -> int:
    """Process a single item asynchronously."""
    await asyncio.sleep(0.05)
    return item * 2


@do
def risky_computation() -> Generator[Effect, Any, Dict]:
    """A computation that might fail."""
    risk = yield ask("risk_level")
    if risk > 0.5:
        yield fail(ValueError(f"Risk too high: {risk}"))
    return {"status": "success", "risk": risk}


@do
def inner_computation() -> Generator[Effect, Any, str]:
    """Computation using modified environment."""
    config = yield ask("config")
    return f"Modified: {config.get('modified', False)}"


@do
def logged_computation() -> Generator[Effect, Any, int]:
    """Computation that produces logs."""
    yield tell("Starting logged computation")
    for i in range(3):
        yield tell(f"Step {i}")
        yield put(f"step_{i}", i * 10)
    yield tell("Completed logged computation")
    return 42


async def main():
    """Run the comprehensive example."""
    engine = ProgramInterpreter()

    # Create initial context
    context = ExecutionContext(
        env={"config": {"version": "1.0", "debug": True}, "risk_level": 0.3},
        io_allowed=True,
    )

    # Run program
    program = comprehensive_example()
    result = await engine.run(program, context)

    print("\n" + "=" * 50)
    print("RESULTS:")
    print("=" * 50)
    print(f"Result: {result.result}")
    print(f"Final state: {result.state}")
    print(f"Log entries: {len(result.log)}")
    print(f"Graph steps: {len(result.graph.steps)}")

    # Test with high risk (should handle error)
    context2 = ExecutionContext(env={"config": {"version": "2.0"}, "risk_level": 0.8})
    result2 = await engine.run(program, context2)
    print(f"\nHigh risk result: {result2.result}")


if __name__ == "__main__":
    asyncio.run(main())
