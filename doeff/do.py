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

from doeff.kleisli import KleisliProgram
from doeff.program import Program, _build_auto_unwrap_strategy
from doeff.types import Effect, EffectGenerator

P = ParamSpec("P")
T = TypeVar("T")

# Type alias for the internal generator wrapper signature.
# This is what generator_wrapper actually produces - a generator that yields
# effects/programs and returns T.
_GeneratorFunc = Callable[..., Generator[Effect | Program, Any, T]]

_BRIDGE_CODE_OBJECTS: set[object] = set()


def _default_get_frame(generator: object) -> object | None:
    return getattr(generator, "gi_frame", None)


def _do_get_frame(bridge_gen: object) -> object | None:
    frame = getattr(bridge_gen, "gi_frame", None)
    if frame is None:
        return None

    locals_dict = getattr(frame, "f_locals", None)
    if locals_dict is None:
        return None

    get_value = getattr(locals_dict, "get", None)
    if not callable(get_value):
        return _default_get_frame(bridge_gen)

    user_gen = get_value("gen")
    if user_gen is None:
        return _default_get_frame(bridge_gen)
    return getattr(user_gen, "gi_frame", None)


def resolve_generator_line(generator: object) -> int | None:
    frame = _do_get_frame(generator)
    if frame is None:
        frame = _default_get_frame(generator)
    if frame is None:
        return None
    return getattr(frame, "f_lineno", None)


def resolve_generator_location(generator: object) -> tuple[str, int] | None:
    frame = _do_get_frame(generator)
    if frame is None:
        frame = _default_get_frame(generator)
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


def default_get_frame(generator: object) -> object | None:
    return _default_get_frame(generator)


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
        hasattr(generator, "__next__")
        and hasattr(generator, "send")
        and hasattr(generator, "throw")
    )
    if not is_generator_like:
        raise TypeError(
            f"make_doeff_generator() requires a generator-like object, got {type(generator).__name__}"
        )

    code = getattr(generator, "gi_code", None)

    if function_name is None:
        function_name = getattr(code, "co_name", None) or getattr(generator, "__name__", None)
        if function_name is None:
            function_name = "<generator>"
    if source_file is None:
        source_file = getattr(code, "co_filename", None) or "<unknown>"
    if source_line is None:
        source_line = getattr(code, "co_firstlineno", None) or 0
    resolved_source_line = int(source_line) if source_line is not None else 0

    callback = get_frame if get_frame is not None else _default_get_frame
    return vm.DoeffGenerator(
        generator=cast(Any, generator),
        function_name=function_name,
        source_file=source_file,
        source_line=resolved_source_line,
        get_frame=callback,
    )


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

        def value_generator(value: T) -> Generator[Effect | Program, Any, T]:
            if False:  # pragma: no cover
                yield cast(Any, None)
            return value

        _BRIDGE_CODE_OBJECTS.add(bridge_generator.__code__)
        _BRIDGE_CODE_OBJECTS.add(value_generator.__code__)

        code = func.__code__

        @wraps(func)
        def generator_factory(
            *args: P.args, **kwargs: P.kwargs
        ) -> Generator[Effect | Program, Any, T]:
            gen_or_value = func(*args, **kwargs)
            if not inspect.isgenerator(gen_or_value):
                return value_generator(cast(T, gen_or_value))

            gen = cast(Generator[Effect | Program, Any, T], gen_or_value)
            return bridge_generator(gen)

        @wraps(func)
        def generator_wrapper(
            *args: P.args, **kwargs: P.kwargs
        ) -> Generator[Effect | Program, Any, T] | T:
            return cast(
                Generator[Effect | Program, Any, T],
                make_doeff_generator(
                    generator_factory(*args, **kwargs),
                    function_name=func.__name__,
                    source_file=code.co_filename,
                    source_line=code.co_firstlineno,
                    get_frame=_do_get_frame,
                ),
            )

        # KleisliProgram.func expects Callable[P, Program[T]], but we pass a
        # generator-producing callable. Cast to satisfy pyright.
        super().__init__(cast(Callable[P, Program[T]], generator_wrapper))
        self.original_func = func

        import doeff_vm as vm

        code = func.__code__
        object.__setattr__(
            self,
            "_doeff_generator_factory",
            vm.DoeffGeneratorFn(
                callable=cast(Any, generator_factory),
                function_name=func.__name__,
                source_file=code.co_filename,
                source_line=code.co_firstlineno,
                get_frame=_do_get_frame,
            ),
        )

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
        object.__setattr__(self, "_is_do_decorated", True)

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
    do_yield_fn = DoYieldFunction(func)

    import doeff_vm as vm

    code = func.__code__
    kleisli = vm.PyKleisli(
        func=cast(Any, do_yield_fn),
        name=func.__qualname__,
        file=code.co_filename,
        line=code.co_firstlineno,
    )

    for attr in ("__doc__", "__module__", "__name__", "__qualname__", "__annotations__"):
        value = getattr(do_yield_fn, attr, getattr(func, attr, None))
        if value is not None:
            setattr(kleisli, attr, value)

    signature = getattr(do_yield_fn, "__signature__", None)
    if signature is None:
        try:
            signature = inspect.signature(func)
        except (TypeError, ValueError):
            signature = None
    if signature is not None:
        kleisli.__signature__ = signature

    kleisli.__wrapped__ = func
    kleisli.original_func = func
    kleisli.original_generator = func
    kleisli.__doeff_do_decorated__ = True
    kleisli._is_do_decorated = True
    kleisli._doeff_generator_factory = getattr(do_yield_fn, "_doeff_generator_factory", None)

    return cast(KleisliProgram[P, T], kleisli)


__all__ = [
    "DoYieldFunction",
    "_default_get_frame",
    "default_get_frame",
    "do",
    "make_doeff_generator",
]
