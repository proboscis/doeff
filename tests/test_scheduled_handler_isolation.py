"""Repro: dynamic handler wrapping inside scheduled() context.

Tests whether handlers built dynamically via yield Ask() + WithHandler
inside a @do function work correctly when the body yields sub-programs
whose handlers emit effects.
"""

from doeff import do, Ask, WithHandler, Local, run
from doeff_vm import EffectBase, Resume, Pass
from doeff_core_effects.scheduler import scheduled
from doeff_core_effects.handlers import lazy_ask


class Transcribe(EffectBase):
    pass

class Resolve(EffectBase):
    pass

class ResolveHandlerKey:
    """Ask key for resolve handler lookup."""
    pass

class TranscribeHandlerKey:
    """Ask key for transcribe handler lookup."""
    pass


@do
def transcribe_handler(effect, k):
    if isinstance(effect, Transcribe):
        result = yield Resolve()
        return (yield Resume(k, f"transcribed({result})"))
    yield Pass(effect, k)


@do
def resolve_handler(effect, k):
    if isinstance(effect, Resolve):
        return (yield Resume(k, "resolved"))
    yield Pass(effect, k)


@do
def dynamic_handler_wrapper(program):
    """Like wrap_with_mediagen_stack: resolves handlers via Ask, wraps program."""
    rh = yield Ask(ResolveHandlerKey)
    th = yield Ask(TranscribeHandlerKey)
    # Build handler chain dynamically
    wrapped = program
    wrapped = WithHandler(rh, wrapped)  # resolve outermost
    wrapped = WithHandler(th, wrapped)  # transcribe innermost
    return (yield wrapped)


def test_static_handlers_with_scheduled():
    """Static handler wrapping + scheduled: works."""
    sub = WithHandler(transcribe_handler, (lambda: (yield Transcribe()))())

    @do
    def inner():
        return (yield Transcribe())

    sub = WithHandler(transcribe_handler, inner())

    @do
    def outer():
        return (yield sub)

    composed = WithHandler(resolve_handler, outer())
    assert run(scheduled(composed)) == "transcribed(resolved)"


def test_dynamic_handlers_with_scheduled():
    """Dynamic handler wrapping (via Ask) + scheduled + sub-program.

    This is what mediagen's wrap_with_mediagen_stack does:
    1. Resolve handler bindings via Ask
    2. Build WithHandler chain
    3. yield the wrapped program
    4. Sub-program has its own WithHandler (like _abepura_311_transcribe)
    """
    @do
    def inner():
        return (yield Transcribe())

    # Sub-program with its own handler (like _abepura_311_transcribe)
    sub = WithHandler(transcribe_handler, inner())

    @do
    def outer():
        return (yield sub)

    env = {
        ResolveHandlerKey: resolve_handler,
        TranscribeHandlerKey: transcribe_handler,
    }

    composed = dynamic_handler_wrapper(outer())
    composed = WithHandler(lazy_ask(env=env), composed)
    assert run(scheduled(composed)) == "transcribed(resolved)"


def test_dynamic_handlers_sub_emits_through_stack():
    """Sub-program's handler emits effect that must traverse the dynamic stack.

    transcribe_handler (on sub-program) emits Resolve.
    resolve_handler (in dynamic stack) must catch it.
    """
    @do
    def inner():
        return (yield Transcribe())

    sub = WithHandler(transcribe_handler, inner())

    @do
    def program():
        return (yield sub)

    env = {
        ResolveHandlerKey: resolve_handler,
        TranscribeHandlerKey: transcribe_handler,
    }

    composed = dynamic_handler_wrapper(program())
    composed = WithHandler(lazy_ask(env=env), composed)
    result = run(scheduled(composed))
    assert result == "transcribed(resolved)", f"got: {result}"
