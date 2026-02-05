"""Writer effects and handler for CESK v3.

Writer effects provide output accumulation within a computation:
- Tell(message): Emit a message to the accumulated output (returns None)
- Listen(): Get the current accumulated messages (returns list)

Usage:
    from doeff.cesk_v3.level3_core_effects import Tell, Listen, writer_handler
    from doeff.cesk_v3 import WithHandler, run
    from doeff.do import do

    @do
    def program():
        yield Tell("Starting")
        yield Tell("Processing")
        messages = yield Listen()
        yield Tell("Done")
        return messages

    result = run(WithHandler(writer_handler(), program()))
    # result == ["Starting", "Processing"]
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from doeff.cesk_v3.level2_algebraic_effects.frames import EffectBase
from doeff.cesk_v3.level2_algebraic_effects.primitives import Forward, Resume
from doeff.do import do
from doeff.program import Program


@dataclass(frozen=True)
class WriterTellEffect(EffectBase):
    """Emits a message to the accumulated output.

    Returns None after recording the message.
    """

    message: Any


@dataclass(frozen=True)
class WriterListenEffect(EffectBase):
    """Retrieves all accumulated messages so far.

    Returns a list of all messages emitted via Tell.
    """

    pass


def Tell(message: Any) -> WriterTellEffect:
    """Create a writer tell effect.

    Args:
        message: The message to emit. Can be any value.

    Returns:
        WriterTellEffect that records the message and yields None.
    """
    return WriterTellEffect(message=message)


def Listen() -> WriterListenEffect:
    """Create a writer listen effect.

    Returns:
        WriterListenEffect that yields the list of accumulated messages.
    """
    return WriterListenEffect()


def writer_handler() -> tuple[Any, list[Any]]:
    """Create a writer handler that accumulates messages.

    The handler uses closure-captured state, so messages are accumulated across
    effect invocations within a single WithHandler scope.

    Returns:
        Tuple of (handler function, messages list) where:
        - handler: Handler function compatible with WithHandler
        - messages: The accumulated messages list (for inspection after run)

    Example:
        @do
        def program():
            yield Tell("hello")
            yield Tell("world")
            return (yield Listen())

        handler, messages = writer_handler()
        result = run(WithHandler(handler, program()))
        # result == ["hello", "world"]
        # messages == ["hello", "world"]  # same list, accessible after run
    """
    messages: list[Any] = []

    @do
    def handler(effect: EffectBase) -> Program[Any]:
        if isinstance(effect, WriterTellEffect):
            messages.append(effect.message)
            return (yield Resume(None))
        if isinstance(effect, WriterListenEffect):
            # Return a copy to prevent mutation of internal state
            return (yield Resume(list(messages)))
        forwarded = yield Forward(effect)
        return (yield Resume(forwarded))

    return handler, messages


__all__ = [
    "Listen",
    "Tell",
    "WriterListenEffect",
    "WriterTellEffect",
    "writer_handler",
]
