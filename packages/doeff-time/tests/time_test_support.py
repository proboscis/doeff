"""Shared helpers for the doeff-time test suite.

This lives in a uniquely-named module (NOT conftest.py) because several
conftest.py files exist across the repo's pytest testpaths; a plain
``from conftest import ...`` resolves whichever file claimed the ambient
module name ``conftest`` first and breaks when suites are collected
together (e.g. ``pytest packages/doeff-time/tests tests``).
"""

from datetime import datetime, timedelta, timezone

from doeff import handler as _install_raw_handler

SIM_TIME_EPOCH = datetime(2024, 1, 1, tzinfo=timezone.utc)


def sim_time(seconds: float) -> datetime:
    return SIM_TIME_EPOCH + timedelta(seconds=seconds)


def sim_seconds(value: datetime) -> float:
    return (value - SIM_TIME_EPOCH).total_seconds()


def listen(program, types=None):
    """Local listen — collects effects while running program in place.

    OCaml 5 pattern: local match_with, not an effect.
    Returns DoExpr that evaluates to (result, collected_effects).
    """
    from doeff_core_effects import WriterTellEffect

    from doeff import Pass, do

    types_to_collect = types or (WriterTellEffect,)
    collected = []

    @do
    def collector(effect, k):
        if isinstance(effect, tuple(types_to_collect)):
            collected.append(effect)
        yield Pass(effect, k)

    @do
    def _listen():
        result = yield _install_raw_handler(collector)(program)
        return (result, collected)

    return _listen()


def run_with_handlers(program, *, env=None):
    from doeff_core_effects.handlers import (
        await_handler,
        listen_handler,
        reader,
        slog_handler,
        state,
        try_handler,
        writer,
    )
    from doeff_core_effects.scheduler import scheduled

    from doeff import run

    wrapped = program
    if env is not None:
        wrapped = reader(env=env)(wrapped)
    # await_handler uses scheduler effects (CreateExternalPromise, Wait),
    # so it must be inside the scheduler.
    wrapped = await_handler()(wrapped)
    wrapped = scheduled(wrapped)
    wrapped = slog_handler(wrapped)
    wrapped = try_handler(wrapped)
    wrapped = listen_handler(wrapped)
    wrapped = writer(wrapped)
    # slog_handler and writer store their logs via Get/Put and require an
    # outer state handler (see their docstrings in doeff_core_effects).
    wrapped = state()(wrapped)
    return run(wrapped)
