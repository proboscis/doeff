"""User-space effect patterns using WithHandler.

This module provides implementations of scoped effects (Local, Safe, Listen, Intercept)
using only WithHandler + @do + try-except, demonstrating that these patterns don't
require specialized Frame types.

Per SPEC-CESK-003: The minimal Frame architecture requires only ReturnFrame + HandlerFrame.
All other patterns can be implemented in user-space.

IMPORTANT: Handlers should return plain values (not CESKState) to preserve store changes.
HandlerResultFrame.on_value constructs the CESKState using the current store.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, TypeVar

from doeff._types_internal import EffectBase, ListenResult
from doeff._vendor import NOTHING, Err, Ok, Some
from doeff.cesk.handler_frame import HandlerContext, WithHandler
from doeff.cesk.state import CESKState
from doeff.do import do
from doeff.program import Program

if TYPE_CHECKING:
    from doeff.effects._program_types import ProgramLike

T = TypeVar("T")
R = TypeVar("R")


@do
def with_local(env_updates: dict[str, Any], program: "ProgramLike[T]") -> Program[T]:
    """Run program with modified environment (user-space Local).

    Intercepts Ask effects and returns local values for overridden keys,
    forwarding other keys to outer handlers.

    Args:
        env_updates: Dictionary of environment key overrides
        program: The program to run with modified environment

    Returns:
        The result of running the program
    """
    from doeff.effects.reader import AskEffect

    def local_handler(effect: EffectBase, ctx: HandlerContext) -> Program[Any]:
        if isinstance(effect, AskEffect) and effect.key in env_updates:
            # Return plain value - HandlerResultFrame constructs CESKState
            return Program.pure(env_updates[effect.key])

        # Forward other effects
        @do
        def forward():
            result = yield effect
            return result  # Plain value

        return forward()

    result = yield WithHandler(handler=local_handler, program=program)
    return result


@do
def with_safe(program: "ProgramLike[T]") -> "Program[Ok[T] | Err[Exception]]":
    """Run program and catch errors as Result (user-space Safe).

    Wraps the result in Ok on success, Err on failure.
    Uses Python try-except around WithHandler.

    Args:
        program: The program to run safely

    Returns:
        Ok(value) on success, Err(exception) on failure
    """
    from doeff._types_internal import capture_traceback, get_captured_traceback

    def forward_handler(effect: EffectBase, ctx: HandlerContext) -> Program[Any]:
        @do
        def forward():
            result = yield effect
            return result  # Plain value

        return forward()

    try:
        result = yield WithHandler(handler=forward_handler, program=program)
        return Ok(result)
    except Exception as e:
        # Capture traceback if not already captured
        captured = get_captured_traceback(e)
        if captured is None:
            captured = capture_traceback(e)
        captured_maybe = Some(captured) if captured else NOTHING
        return Err(e, captured_traceback=captured_maybe)


@do
def with_listen(program: "ProgramLike[T]") -> Program[ListenResult[T]]:
    """Run program and capture Tell effects (user-space Listen).

    Intercepts Tell effects, captures them locally, and also forwards
    to outer handlers (so global log is also updated).

    Args:
        program: The program to run with log capture

    Returns:
        ListenResult containing value and captured log
    """
    from doeff.effects.writer import WriterTellEffect
    from doeff.utils import BoundedLog

    log: list[Any] = []

    def listen_handler(effect: EffectBase, ctx: HandlerContext) -> Program[Any]:
        if isinstance(effect, WriterTellEffect):
            # Capture locally
            message = effect.message
            if isinstance(message, (list, tuple)):
                log.extend(message)
            else:
                log.append(message)

            # Also forward to outer handler (writes to global __log__)
            @do
            def forward_and_capture():
                result = yield effect
                return result  # Plain value

            return forward_and_capture()

        # Forward other effects
        @do
        def forward():
            result = yield effect
            return result  # Plain value

        return forward()

    result = yield WithHandler(handler=listen_handler, program=program)
    return ListenResult(value=result, log=BoundedLog(log))


@do
def with_intercept(
    transforms: tuple[Callable[[EffectBase], EffectBase | Program[Any] | None], ...],
    program: "ProgramLike[T]",
) -> Program[T]:
    """Run program with effect transformation (user-space Intercept).

    Applies transform functions to effects before forwarding them.
    If a transform returns a different effect, that effect is yielded instead.
    If a transform returns a Program, that program is executed.
    If a transform returns None or the same effect, it passes through.

    Args:
        transforms: Tuple of transform functions
        program: The program to run with effect interception

    Returns:
        The result of running the program
    """
    from doeff.program import ProgramBase

    def intercept_handler(effect: EffectBase, ctx: HandlerContext) -> Program[Any]:
        for transform in transforms:
            try:
                transformed = transform(effect)
            except Exception as ex:
                # Transform raised an exception - return error state
                return Program.pure(CESKState.with_error(ex, ctx.env, ctx.store, ctx.k))

            if transformed is not effect and transformed is not None:
                if isinstance(transformed, (ProgramBase, EffectBase)):
                    # Execute the program and return its result
                    @do
                    def run_transformed_program():
                        result = yield transformed
                        return result  # Plain value

                    return run_transformed_program()
                else:
                    # Yield the transformed effect
                    @do
                    def forward_transformed():
                        result = yield transformed
                        return result  # Plain value

                    return forward_transformed()

        # No transform matched, forward original
        @do
        def forward():
            result = yield effect
            return result  # Plain value

        return forward()

    result = yield WithHandler(handler=intercept_handler, program=program)
    return result


@do
def with_graph_capture(program: "ProgramLike[T]") -> Program[tuple[T, list[Any]]]:
    """Run program and capture graph nodes (user-space GraphCapture).

    Intercepts GraphStep effects and accumulates them,
    returning both the result and the captured graph.

    Args:
        program: The program to run with graph capture

    Returns:
        Tuple of (result, captured_graph_nodes)
    """
    from doeff.effects.graph import GraphStepEffect

    captured_nodes: list[Any] = []

    def capture_handler(effect: EffectBase, ctx: HandlerContext) -> Program[Any]:
        if isinstance(effect, GraphStepEffect):
            node = {"value": effect.value, "meta": effect.meta}
            captured_nodes.append(node)

            # Forward to outer handler AND capture locally
            @do
            def forward_and_capture():
                result = yield effect
                return result  # Plain value

            return forward_and_capture()

        # Forward other effects
        @do
        def forward():
            result = yield effect
            return result  # Plain value

        return forward()

    result = yield WithHandler(handler=capture_handler, program=program)
    return (result, captured_nodes)


__all__ = [
    "with_graph_capture",
    "with_intercept",
    "with_listen",
    "with_local",
    "with_safe",
]
