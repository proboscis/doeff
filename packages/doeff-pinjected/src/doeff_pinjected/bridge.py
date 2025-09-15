"""
Pinjected bridge for the pragmatic comprehensive free monad implementation.

This module provides functions to convert Program[T] from the pragmatic
implementation into pinjected's Injected[T] and IProxy[T] types.
"""

from typing import TypeVar, Generator

from loguru import logger
from pinjected import AsyncResolver, Injected, IProxy

from doeff.core import (
    ProgramInterpreter,
    ExecutionContext,
    Program,
    Effects,
    RunResult,
)

T = TypeVar("T")


def _create_dep_aware_generator(prog: Program, resolver: AsyncResolver) -> Generator:
    """
    Create a generator that intercepts Dep effects and converts them to Await effects.

    This also properly handles the case where a Program is yielded.

    Args:
        prog: The Program to wrap
        resolver: The AsyncResolver for dependency injection

    Returns:
        A generator that handles dependency resolution
    """
    # Get the generator from the Program
    if hasattr(prog, "generator_func") and callable(prog.generator_func):
        gen = prog.generator_func()
    elif hasattr(prog, "__call__"):
        gen = prog()
    else:
        # It might already be a generator
        gen = prog

    if gen is None:
        return None

    try:
        current = next(gen)
        while True:
            # Check if this is a Program (yielded Programs should be passed through)
            if isinstance(current, Program):
                # Programs are handled by the interpreter directly
                value = yield current
                current = gen.send(value)
            # Check if this is a Dep effect (reader.ask)
            elif hasattr(current, "tag") and current.tag == "reader.ask":
                # This is a dependency request
                key = current.payload
                logger.debug(f"Resolving dependency for key: {key}")
                future = resolver.provide(key)
                # Wrap in await effect
                await_effect = Effects.future.await_(future)
                # Send to generator and get result
                value = yield await_effect
                # Send resolved value back
                current = gen.send(value)
            else:
                # Pass through other effects unchanged
                value = yield current
                current = gen.send(value)
    except StopIteration as e:
        return getattr(e, "value", None)


def program_to_injected(prog: Program[T]) -> Injected[T]:
    """
    Convert a Program[T] to Injected[T].

    This allows Programs to be used with pinjected's dependency injection system.
    Dependencies are resolved via ask/Dep effects that map to pinjected's resolver.

    Args:
        prog: The Program to convert

    Returns:
        An Injected value that can be resolved with pinjected
    """

    async def _runner(__resolver__: AsyncResolver) -> T:
        # Create the pragmatic engine
        engine = ProgramInterpreter()

        # Create wrapped program that handles dependency resolution
        wrapped_program = Program(
            lambda: _create_dep_aware_generator(prog, __resolver__)
        )

        # Run with env containing the resolver for use by ask effects
        context = ExecutionContext(env={"__resolver__": __resolver__})

        # Run the program
        result = await engine.run(wrapped_program, context)

        # Handle the result
        if result.is_err:
            raise result.result.error.exc

        return result.value

    return Injected.bind(_runner, __resolver__=Injected.by_name("__resolver__"))


def program_to_iproxy(prog: Program[T]) -> IProxy[T]:
    """
    Convert a Program[T] to IProxy[T].

    This is a convenience function that creates an Injected and returns its proxy.

    Args:
        prog: The Program to convert

    Returns:
        An IProxy that can be used with pinjected
    """
    injected = program_to_injected(prog)
    return injected.proxy


def program_to_injected_result(prog: Program[T]) -> Injected[RunResult[T]]:
    """
    Convert a Program[T] to Injected[RunResult[T]].

    This returns the full RunResult[T] containing both the execution context
    (state, log, graph) and the computation result, without unwrapping or raising
    on errors.

    Args:
        prog: The Program to convert

    Returns:
        An Injected that returns RunResult[T] when resolved
    """

    async def _runner(__resolver__: AsyncResolver) -> RunResult[T]:
        # Create the pragmatic engine
        engine = ProgramInterpreter()

        # Create wrapped program that handles dependency resolution
        wrapped_program = Program(
            lambda: _create_dep_aware_generator(prog, __resolver__)
        )

        # Run with env containing the resolver for use by ask effects
        context = ExecutionContext(env={"__resolver__": __resolver__})

        # Run the program and get the result
        result = await engine.run(wrapped_program, context)

        # Return the full RunResult with both context and result
        return result

    return Injected.bind(_runner, __resolver__=Injected.by_name("__resolver__"))


def program_to_iproxy_result(prog: Program[T]) -> IProxy[RunResult[T]]:
    """
    Convert a Program[T] to IProxy[RunResult[T]].

    This is a convenience function that creates an Injected returning RunResult[T]
    and returns its proxy.

    Args:
        prog: The Program to convert

    Returns:
        An IProxy that returns RunResult[T] when resolved
    """
    injected = program_to_injected_result(prog)
    return injected.proxy
