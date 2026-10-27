"""The UnhandledEffect message ends with a hint naming the handler to install.

A first-time user who forgets a handler used to read only the effect that fell
off the top (``unhandled effect: Spawn (<… object at 0x…>)``), which names
neither the handler nor how to install it. Worse, two of the common mistakes
surface an effect the user never wrote: ``writer`` without ``state()`` fails on
``writer``'s own ``Get`` of an internal key, and ``await_handler()`` without
``scheduled()`` fails on the ``CreateExternalPromise`` it performs.

The message now ends with one ``hint:`` line, looked up from the effect's type
in the table of ``doeff_core_effects.unhandled_hints``. The hint names the
handler and the composition to write, never the internal key.

Each hint is also run: installing the handler the way the hint says makes the
same program finish, so a hint cannot drift from how the handlers compose.
"""

from __future__ import annotations

from contextlib import closing

import pytest
from doeff_core_effects.effects import ReadWriterLog
from doeff_core_effects.handlers import (
    await_handler,
    lazy_ask,
    listen_handler,
    reader,
    slog_handler,
    state,
    try_handler,
    writer,
)
from doeff_core_effects.scheduler import (
    CompletePromise,
    CreatePromise,
    CreateSemaphore,
    Gather,
    Spawn,
    Wait,
    scheduled,
)
from doeff_vm import EffectBase, PyVM
from doeff_vm._unhandled_hint import register_unhandled_effect_hint

from doeff import (
    Ask,
    Await,
    Get,
    Listen,
    Local,
    Pass,
    Put,
    Tell,
    Try,
    UnhandledEffect,
    do,
    run,
    slog,
)
from doeff import handler as _install_raw_handler

_WRITER_LOG_KEY = "__doeff_writer_log__"


@do
def tell_program():
    yield Tell("hello")
    return "told"


@do
def child_program():
    return 1


@do
def spawn_program():
    task = yield Spawn(child_program())
    return (yield Wait(task))


async def _answer():
    return 42


@do
def await_program(coroutine):
    return (yield Await(coroutine))


@do
def ask_program():
    return (yield Ask("name"))


@do
def pass_through(effect, k):
    yield Pass(effect, k)


def _message(program) -> str:
    with pytest.raises(UnhandledEffect) as excinfo:
        run(program)
    return str(excinfo.value)


def _unhandled_await_message(install) -> str:
    """The message of an Await nobody ran; its coroutine is closed, not left unawaited."""
    with closing(_answer()) as coroutine:
        return _message(install(await_program(coroutine)))


def _hint_line(message: str) -> str:
    last = message.splitlines()[-1].strip()
    assert last.startswith("hint: "), f"last line is not a hint: {message!r}"
    return last


class TestTheFourFirstLookMistakes:
    """The four mistakes of the first-look report each end with a hint."""

    def test_writer_without_state_names_state_without_the_internal_key(self):
        message = _message(writer(tell_program()))
        hint = _hint_line(message)
        assert "state()(writer(" in hint
        assert _WRITER_LOG_KEY not in hint

    def test_spawn_without_scheduled_names_scheduled(self):
        hint = _hint_line(_message(spawn_program()))
        assert "Spawn" in hint
        assert "run(scheduled(prog))" in hint

    def test_await_handler_without_scheduled_names_scheduled(self):
        """await_handler() performs CreateExternalPromise; without scheduled()
        that is the effect that falls off, so its hint says where it came from."""
        message = _unhandled_await_message(await_handler())
        assert "CreateExternalPromise" in message
        hint = _hint_line(message)
        assert "await_handler()" in hint
        assert "run(scheduled(await_handler()(prog)))" in hint
        assert run(scheduled(await_handler()(await_program(_answer())))) == 42

    def test_await_without_any_handler_names_await_handler_and_scheduled(self):
        hint = _hint_line(_unhandled_await_message(lambda program: program))
        assert "run(scheduled(await_handler()(prog)))" in hint
        assert run(scheduled(await_handler()(await_program(_answer())))) == 42

    def test_ask_without_handler_names_reader(self):
        hint = _hint_line(_message(ask_program()))
        assert "reader(env=" in hint
        assert "lazy_ask(env=" in hint


class TestHintPlacement:
    def test_hint_follows_the_handler_chain_line(self):
        message = _message(_install_raw_handler(pass_through)(ask_program()))
        lines = message.splitlines()
        assert "handlers in scope" in lines[-2]
        assert "pass_through" in lines[-2]
        assert lines[-1].strip().startswith("hint: ")

    def test_hint_is_in_the_vm_message_not_only_in_run(self):
        """The VM builds the hint, so a caller of PyVM.run sees it too."""
        with pytest.raises(UnhandledEffect) as excinfo:
            PyVM().run(ask_program())
        assert "reader(env=" in _hint_line(str(excinfo.value))

    def test_effect_without_a_registered_hint_gets_the_general_hint(self):
        """An effect no package registered a line for (one of the user's own) still ends with
        how to install a handler (tests/test_handler_annotation_messages.py, e23)."""

        class NoHintEffect(EffectBase):
            pass

        @do
        def program():
            yield NoHintEffect()

        message = _message(program())
        assert message.splitlines()[0].endswith("(no handlers in scope)")
        assert _hint_line(message) == (
            "hint: install a handler for NoHintEffect: run(fn(prog)), where fn is a def "
            "(effect: NoHintEffect, k) marked with @handler that returns (yield Resume(k, value))"
        )

    def test_a_failing_hint_lookup_keeps_the_unhandled_effect(self):
        @do
        def program():
            yield BrokenHintEffect()

        message = _message(program())
        assert message.startswith("unhandled effect: BrokenHintEffect")
        hint = _hint_line(message)
        assert "hint lookup failed" in hint
        assert "broken hint table" in hint


class BrokenHintEffect(EffectBase):
    pass


def _broken_hint_for(effect: object) -> str | None:
    if isinstance(effect, BrokenHintEffect):
        raise LookupError("broken hint table")
    return None


# Registered once for the process; it answers no effect but BrokenHintEffect.
register_unhandled_effect_hint(_broken_hint_for)


@do
def _local_program():
    return (yield Local({"name": "inner"}, ask_program()))


@do
def _listen_program():
    return (yield Listen(child_program()))


@do
def _try_program():
    return (yield Try(child_program()))


@do
def _slog_program():
    yield slog("hello")
    return "logged"


@do
def _state_program():
    yield Put("count", 1)
    return (yield Get("count"))


@do
def _read_log_program():
    yield Tell("hello")
    return (yield ReadWriterLog())


@do
def _gather_program():
    first = yield Spawn(child_program())
    second = yield Spawn(child_program())
    return (yield Gather(first, second))


@do
def _promise_program():
    promise = yield CreatePromise()
    yield CompletePromise(promise, "done")
    return (yield Wait(promise.future))


@do
def _semaphore_program():
    semaphore = yield CreateSemaphore(1)
    return semaphore is not None


# (the unhandled program, the composition its hint names, that composition applied).
# The Await rows are the two Await tests above, which close the coroutine nobody ran.
FOLLOW_THE_HINT = [
    pytest.param(
        lambda: writer(tell_program()),
        "state()(writer(",
        lambda: state()(writer(tell_program())),
        id="writer",
    ),
    pytest.param(
        tell_program, "state()(writer(", lambda: state()(writer(tell_program())), id="tell"
    ),
    pytest.param(
        _read_log_program,
        "state()(writer(",
        lambda: state()(writer(_read_log_program())),
        id="read-writer-log",
    ),
    pytest.param(_state_program, "state()(prog)", lambda: state()(_state_program()), id="get-put"),
    pytest.param(
        ask_program, "reader(env=", lambda: reader(env={"name": "x"})(ask_program()), id="ask"
    ),
    pytest.param(
        _local_program,
        "lazy_ask(env=",
        lambda: lazy_ask(env={"name": "outer"})(_local_program()),
        id="local",
    ),
    pytest.param(
        _listen_program,
        "listen_handler(prog)",
        lambda: listen_handler(_listen_program()),
        id="listen",
    ),
    pytest.param(_try_program, "try_handler(prog)", lambda: try_handler(_try_program()), id="try"),
    pytest.param(
        _slog_program, "slog_handler(prog)", lambda: slog_handler(_slog_program()), id="slog"
    ),
    pytest.param(spawn_program, "scheduled(prog)", lambda: scheduled(spawn_program()), id="spawn"),
    pytest.param(
        _gather_program, "scheduled(prog)", lambda: scheduled(_gather_program()), id="gather"
    ),
    pytest.param(
        _promise_program, "scheduled(prog)", lambda: scheduled(_promise_program()), id="promise"
    ),
    pytest.param(
        _semaphore_program,
        "scheduled(prog)",
        lambda: scheduled(_semaphore_program()),
        id="semaphore",
    ),
]


@pytest.mark.parametrize(("make_unhandled", "named", "make_followed"), FOLLOW_THE_HINT)
def test_following_the_hint_runs_the_program(make_unhandled, named, make_followed):
    assert named in _hint_line(_message(make_unhandled()))
    run(make_followed())
