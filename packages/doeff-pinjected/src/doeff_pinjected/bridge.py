"""
Pinjected bridge for the pragmatic comprehensive free monad implementation.

This module provides functions to convert Program[T] from the pragmatic
implementation into pinjected's Injected[T] and IProxy[T] types.
"""

from typing import TypeVar

from loguru import logger
from pinjected import AsyncResolver, Injected, IProxy

from doeff.effects import Await
from doeff.interpreter import ProgramInterpreter
from doeff.program import Program
from doeff.types import Effect, ExecutionContext, RunResult

T = TypeVar("T")


def _program_with_dependency_interception(
    prog: Program[T], resolver: AsyncResolver
) -> Program[T]:
    """Attach dependency-resolution interception using ``Program.intercept``."""

    if not isinstance(prog, Program):  # Defensive: keep API expectations clear
        raise TypeError(
            f"Pinjected bridge expects a Program instance, got {type(prog)!r}"
        )

    def _transform(effect: Effect) -> Effect | Program:
        if effect.tag in ("reader.ask", "dep.inject"):
            key = effect.payload
            logger.debug(f"Resolving dependency for key: {key}")
            return Await(resolver.provide(key))
        return effect

    return prog.intercept(_transform)


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
        wrapped_program = _program_with_dependency_interception(prog, __resolver__)

        # Run with env containing the resolver for use by ask effects
        context = ExecutionContext(env={"__resolver__": __resolver__})

        # Run the program
        result = await engine.run(wrapped_program, context)

        # Handle the result
        if result.is_err:
            error = result.result.error
            # Unwrap EffectFailure to get the original cause
            from doeff.types import EffectFailure
            if isinstance(error, EffectFailure):
                error = error.cause
            raise error

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
        wrapped_program = _program_with_dependency_interception(prog, __resolver__)

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
