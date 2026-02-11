"""
Pinjected bridge for doeff's algebraic effects system.

This module provides functions to convert Program[T] from the doeff
effects system into pinjected's Injected[T] and IProxy[T] types.
"""

from __future__ import annotations

from typing import TypeVar, cast

from loguru import logger
from pinjected import AsyncResolver, Injected, IProxy

from doeff import Effect, KleisliProgramCall, Program, RunResult, async_run_with_handler_map
from doeff.effects import AskEffect, GraphAnnotateEffect, GraphStepEffect, Intercept, Pure
from doeff_pinjected.effects import PinjectedResolve
from doeff_pinjected.handlers import production_handlers

T = TypeVar("T")
_EFFECT_FAILURE_TYPE_NAMES = {"EffectFailure", "EffectFailureError"}


def _unwrap_effect_failure(error: BaseException) -> BaseException:
    """Unwrap runtime-specific EffectFailure wrappers when available."""
    if error.__class__.__name__ in _EFFECT_FAILURE_TYPE_NAMES:
        cause = getattr(error, "cause", None)
        if isinstance(cause, BaseException):
            return cause
    return error


def _supports_program_intercept(prog: Program[T]) -> bool:
    return callable(getattr(prog, "intercept", None))


def _program_with_dependency_interception(
    prog: Program[T], resolver: AsyncResolver
) -> Program[T] | Effect:
    """Attach dependency-resolution interception using ``Program.intercept``."""

    if not isinstance(
        prog, (Program, KleisliProgramCall)
    ):  # Defensive: keep API expectations clear
        raise TypeError(f"Pinjected bridge expects a Program instance, got {type(prog)!r}")

    def _transform(effect: Effect) -> Effect | Program:
        if isinstance(effect, AskEffect):
            ask_effect = cast(AskEffect, effect)
            key = ask_effect.key
            logger.debug(f"Resolving dependency for key: {key}")
            return PinjectedResolve(key=key)
        return effect

    if _supports_program_intercept(prog):
        return prog.intercept(_transform)

    # Compatibility fallback for runtimes where KleisliProgramCall lacks `.intercept()`.
    # Intercept(...) expects None for the "delegate unchanged" case.
    def _compat_transform(effect: Effect) -> Effect | Program | None:
        if isinstance(effect, AskEffect):
            ask_effect = cast(AskEffect, effect)
            key = ask_effect.key
            logger.debug(f"Resolving dependency for key: {key}")
            return PinjectedResolve(key=key)
        if isinstance(effect, (GraphStepEffect, GraphAnnotateEffect)):
            return Pure(None)
        return None

    return Intercept(prog, _compat_transform)


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
        use_local_passthrough = not _supports_program_intercept(prog)
        original_build_local_handler = None

        if use_local_passthrough:
            import doeff_vm

            from doeff.effects import reader as reader_effects

            original_build_local_handler = reader_effects._build_local_handler

            def _bridge_build_local_handler(_overlay: dict):
                def handle_local_ask(_effect, _k):
                    yield doeff_vm.Delegate()

                return handle_local_ask

            reader_effects._build_local_handler = _bridge_build_local_handler

        # Create wrapped program that handles dependency resolution
        wrapped_program = _program_with_dependency_interception(prog, __resolver__)

        try:
            # Run with env containing the resolver for use by ask effects
            result = await async_run_with_handler_map(
                wrapped_program,
                production_handlers(resolver=__resolver__),
                env={"__resolver__": __resolver__},
            )
        finally:
            if use_local_passthrough and original_build_local_handler is not None:
                from doeff.effects import reader as reader_effects

                reader_effects._build_local_handler = original_build_local_handler

        # Handle the result
        if result.is_err():
            raise _unwrap_effect_failure(result.error)

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
        use_local_passthrough = not _supports_program_intercept(prog)
        original_build_local_handler = None

        if use_local_passthrough:
            import doeff_vm

            from doeff.effects import reader as reader_effects

            original_build_local_handler = reader_effects._build_local_handler

            def _bridge_build_local_handler(_overlay: dict):
                def handle_local_ask(_effect, _k):
                    yield doeff_vm.Delegate()

                return handle_local_ask

            reader_effects._build_local_handler = _bridge_build_local_handler

        # Create wrapped program that handles dependency resolution
        wrapped_program = _program_with_dependency_interception(prog, __resolver__)

        try:
            # Run with env containing the resolver for use by ask effects
            result = await async_run_with_handler_map(
                wrapped_program,
                production_handlers(resolver=__resolver__),
                env={"__resolver__": __resolver__},
            )
        finally:
            if use_local_passthrough and original_build_local_handler is not None:
                from doeff.effects import reader as reader_effects

                reader_effects._build_local_handler = original_build_local_handler

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
