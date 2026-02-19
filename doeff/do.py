"""
The do decorator for the doeff system.

This module provides the @do decorator that converts generator functions
into KleisliPrograms, enabling do-notation for monadic computations.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Generator
from functools import wraps
from typing import Any, ParamSpec, TypeVar, cast
from weakref import WeakKeyDictionary

from doeff.kleisli import KleisliProgram
from doeff.program import Program, _build_auto_unwrap_strategy
from doeff.types import Effect, EffectGenerator

P = ParamSpec("P")
T = TypeVar("T")

# Type alias for the internal generator wrapper signature.
# This is what generator_wrapper actually produces - a generator that yields
# effects/programs and returns T.
_GeneratorFunc = Callable[..., Generator[Effect | Program, Any, T]]

_BRIDGE_INNER_BY_GENERATOR: WeakKeyDictionary[object, Generator[Effect | Program, Any, Any]] = (
    WeakKeyDictionary()
)
_BRIDGE_CODE_OBJECTS: set[object] = set()


def resolve_doeff_inner(generator: object) -> Generator[Effect | Program, Any, Any] | None:
    return _BRIDGE_INNER_BY_GENERATOR.get(generator)


def resolve_generator_line(generator: object) -> int | None:
    current = generator
    for _ in range(8):
        inner = _BRIDGE_INNER_BY_GENERATOR.get(current)
        if inner is None:
            inner = getattr(current, "__doeff_inner__", None)
        if inner is None:
            break
        current = inner
    frame = getattr(current, "gi_frame", None)
    if frame is None:
        return None
    return getattr(frame, "f_lineno", None)


def resolve_generator_location(generator: object) -> tuple[str, int] | None:
    current = generator
    for _ in range(8):
        inner = _BRIDGE_INNER_BY_GENERATOR.get(current)
        if inner is None:
            inner = getattr(current, "__doeff_inner__", None)
        if inner is None:
            break
        current = inner
    frame = getattr(current, "gi_frame", None)
    if frame is None:
        return None
    code = getattr(frame, "f_code", None)
    if code is None:
        return None
    filename = getattr(code, "co_filename", None)
    lineno = getattr(frame, "f_lineno", None)
    if filename is None or lineno is None:
        return None
    return (filename, lineno)


def resolve_exception_location(exc: BaseException) -> tuple[str, str, int] | None:
    origin = getattr(exc, "__doeff_exception_origin__", None)
    if origin is not None:
        fn_name = origin.get("function_name")
        filename = origin.get("source_file")
        line = origin.get("source_line")
        if fn_name is not None and filename is not None and line is not None:
            return (fn_name, filename, line)

    tb = getattr(exc, "__traceback__", None)
    if tb is None:
        return None

    last_tb = tb
    while True:
        next_tb = getattr(last_tb, "tb_next", None)
        if next_tb is None:
            break
        last_tb = next_tb

    frame = getattr(last_tb, "tb_frame", None)
    if frame is None:
        return None
    code = getattr(frame, "f_code", None)
    if code is None:
        return None

    if code not in _BRIDGE_CODE_OBJECTS:
        return None

    locals_dict = getattr(frame, "f_locals", {})
    gen = locals_dict.get("gen")
    if gen is None:
        return None

    gen_code = getattr(gen, "gi_code", None)
    if gen_code is None:
        return None

    fn_name = getattr(gen_code, "co_qualname", None) or getattr(gen_code, "co_name", "<unknown>")
    filename = getattr(gen_code, "co_filename", "<unknown>")

    user_line = 0
    scan = tb
    while scan is not None:
        scan_frame = getattr(scan, "tb_frame", None)
        if scan_frame is not None:
            scan_code = getattr(scan_frame, "f_code", None)
            if scan_code is gen_code:
                user_line = getattr(scan, "tb_lineno", 0)
        scan = getattr(scan, "tb_next", None)

    if user_line == 0:
        return None

    return (fn_name, filename, user_line)


def _resolve_frame_target(generator: object) -> object:
    current = generator
    for _ in range(8):
        inner = _BRIDGE_INNER_BY_GENERATOR.get(current)
        if inner is None:
            inner = getattr(current, "__doeff_inner__", None)
        if inner is None:
            break
        current = inner
    return current


def default_get_frame(generator: object) -> object | None:
    current = _resolve_frame_target(generator)
    return getattr(current, "gi_frame", None)


def make_doeff_generator(
    generator: object,
    *,
    function_name: str | None = None,
    source_file: str | None = None,
    source_line: int | None = None,
    get_frame: Callable[[object], object | None] | None = None,
) -> object:
    import doeff_vm as vm

    if isinstance(generator, vm.DoeffGenerator):
        return generator

    is_generator_like = inspect.isgenerator(generator) or (
        hasattr(generator, "__next__") and hasattr(generator, "send") and hasattr(generator, "throw")
    )
    if not is_generator_like:
        raise TypeError(
            f"make_doeff_generator() requires a generator-like object, got {type(generator).__name__}"
        )

    frame_target = _resolve_frame_target(generator)
    code = getattr(frame_target, "gi_code", None)

    if function_name is None:
        function_name = getattr(code, "co_name", None) or getattr(frame_target, "__name__", None)
        if function_name is None:
            function_name = "<generator>"
    if source_file is None:
        source_file = getattr(code, "co_filename", None) or "<unknown>"
    if source_line is None:
        source_line = getattr(code, "co_firstlineno", None) or 0

    callback = get_frame if get_frame is not None else default_get_frame
    return vm.DoeffGenerator(
        generator=cast(Any, generator),
        function_name=function_name,
        source_file=source_file,
        source_line=int(source_line),
        get_frame=callback,
    )


class _DoGeneratorProxy:
    """Generator-like proxy that carries the user generator for traceback line mapping."""

    __slots__ = ("_outer", "__doeff_inner__", "__weakref__")

    def __init__(
        self,
        outer: Generator[Effect | Program, Any, T],
        inner: Generator[Effect | Program, Any, T],
    ) -> None:
        self._outer = outer
        self.__doeff_inner__ = inner

    def __iter__(self) -> _DoGeneratorProxy:
        return self

    def __next__(self) -> Effect | Program:
        return next(self._outer)

    def send(self, value: Any) -> Effect | Program:
        return self._outer.send(value)

    def throw(self, typ: BaseException, val: Any | None = None, tb: Any | None = None) -> Any:
        if tb is None and val is None:
            return self._outer.throw(typ)
        if tb is None:
            return self._outer.throw(typ, val)
        return self._outer.throw(typ, val, tb)

    def close(self) -> Any:
        return self._outer.close()

    @property
    def gi_frame(self) -> Any:
        return cast(Any, self._outer).gi_frame

    @property
    def gi_code(self) -> Any:
        return cast(Any, self._outer).gi_code

    @property
    def gi_running(self) -> Any:
        return cast(Any, self._outer).gi_running

    def __repr__(self) -> str:
        return repr(self._outer)


class DoYieldFunction(KleisliProgram[P, T]):
    """Specialised KleisliProgram for generator-based @do functions."""

    def __init__(self, func: Callable[P, EffectGenerator[T]]) -> None:
        def bridge_generator(
            gen: Generator[Effect | Program, Any, T],
        ) -> Generator[Effect | Program, Any, T]:
            try:
                current = next(gen)
            except StopIteration as stop_exc:
                return stop_exc.value

            while True:
                try:
                    sent_value = yield current
                except GeneratorExit:
                    gen.close()
                    raise
                except BaseException as e:
                    try:
                        current = gen.throw(e)
                    except StopIteration as stop_exc:
                        return stop_exc.value
                    continue
                try:
                    current = gen.send(sent_value)
                except StopIteration as stop_exc:
                    return stop_exc.value

        _BRIDGE_CODE_OBJECTS.add(bridge_generator.__code__)

        @wraps(func)
        def generator_wrapper(
            *args: P.args, **kwargs: P.kwargs
        ) -> Generator[Effect | Program, Any, T] | T:
            gen_or_value = func(*args, **kwargs)
            if not inspect.isgenerator(gen_or_value):
                return cast(T, gen_or_value)

            gen = cast(Generator[Effect | Program, Any, T], gen_or_value)
            outer = bridge_generator(gen)
            proxy = _DoGeneratorProxy(outer, gen)
            _BRIDGE_INNER_BY_GENERATOR[outer] = gen
            _BRIDGE_INNER_BY_GENERATOR[proxy] = gen
            return cast(Generator[Effect | Program, Any, T], make_doeff_generator(proxy))

        # KleisliProgram.func expects Callable[P, Program[T]], but we pass a
        # generator function. Runtime call dispatch handles both Program and
        # generator returns from the execution kernel. Cast to satisfy pyright.
        super().__init__(cast(Callable[P, Program[T]], generator_wrapper))
        self.original_func = func

        for attr in ("__doc__", "__module__", "__name__", "__qualname__", "__annotations__"):
            value = getattr(func, attr, None)
            if value is not None:
                setattr(self, attr, value)

        try:
            signature = inspect.signature(func)
        except (TypeError, ValueError):
            signature = None
        if signature is not None:
            self.__signature__ = signature

        self.__doeff_do_decorated__ = True

        strategy = _build_auto_unwrap_strategy(self)
        object.__setattr__(self, "_auto_unwrap_strategy", strategy)

    @property
    def original_generator(self) -> Callable[P, EffectGenerator[T]]:
        """Expose the user-defined generator for downstream tooling."""

        return self.original_func


def do(
    func: Callable[P, EffectGenerator[T]],
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

    ERROR HANDLING:
    Native Python try/except blocks work inside @do functions! Exceptions from
    yielded effects and sub-programs will be caught by surrounding try blocks:

    NATIVE TRY-EXCEPT (works as expected):
        @do
        def my_program():
            try:
                value = yield some_effect()
                return value
            except Exception as e:
                # This WILL catch exceptions from some_effect()!
                return default_value

    EFFECT-BASED ALTERNATIVES (for complex error handling):
        @do
        def my_program():
            # Use Try to capture errors as Result values
            safe_result = yield Try(some_effect())
            if safe_result.is_ok():
                value = safe_result.value
            else:
                value = default_value

            return value

    Both approaches work. Use native try-except for simple cases and the Try effect
    for capturing errors as Result values that can be inspected and handled.

    TYPE SIGNATURE CHANGE:
    @do changes: (args) -> EffectGenerator[T]
    into:        KleisliProgram[P, T] where P is the parameter spec
    This preserves type information and enables automatic Program unwrapping.

    IMPORTANT — async def is NOT supported:
        @do requires a generator function (def with yield). Applying @do to an
        async def is always a bug — there is no "async kleisli" concept. async def
        returns a coroutine, not a generator, so the isgenerator() check fails and
        the coroutine is silently returned without executing the body.
        For async I/O, use a regular @do generator with yield Await(coroutine).

    Usage:
        @do
        def my_program(x: int) -> EffectGenerator[str]:
            config = yield ask("config")
            result = yield await_(process(x))
            yield log(f"Processed {x}")
            return f"Result: {result}"

        # my_program is now KleisliProgram[(x: int), str]
        # Can be called with regular or Program arguments
        result1 = my_program(42)  # Returns Program[str]
        result2 = my_program(x=Program.pure(42))  # Also returns Program[str]

    Args:
        func: A generator function that yields Effects/Programs and returns T

    Returns:
        KleisliProgram that wraps the generator function with automatic
        Program argument unwrapping.
    """

    if not callable(func):
        raise TypeError(f"@do expects a callable, got {type(func).__name__}")
    return DoYieldFunction(func)


__all__ = ["DoYieldFunction", "default_get_frame", "do", "make_doeff_generator"]
