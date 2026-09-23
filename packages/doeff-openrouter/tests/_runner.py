"""Test-only runner that replaces the removed ``run(program, handlers=default_handlers(), ...)``.

``doeff.default_handlers`` / ``doeff.async_run`` were removed ("compose
handlers by calling handler(program)") and :func:`doeff.run` now takes a
single program and returns its value. The tests here still want the legacy
surface — a result object with ``is_ok()`` / ``is_err()`` / ``value`` /
``error``, the final store (``raw_store``) and the emitted log (``log``) — so
this module rebuilds it on
top of the supported primitives: the core-effects handler chain composed by
calling each Program -> Program handler, with ``scheduled`` outermost.

The final store is read through an effect answered by the store handler
itself (``_SnapshotStore``), and the log is captured as values with
``Listen`` (ADR-DOE-CORE-EFFECTS-001) — no side channel on any handler.
``log`` holds each ``Tell`` message as-is and each ``slog(msg, **kwargs)`` as
the dict ``{"msg": msg, **kwargs}``.
"""

from collections.abc import Callable, Generator, Iterable
from dataclasses import dataclass, field
from typing import Any

from doeff_core_effects.effects import Get, Listen, Put, SlogEffect, Try, WriterTellEffect
from doeff_core_effects.handlers import (
    await_handler,
    lazy_ask,
    listen_handler,
    local_handler,
    reader,
    slog_handler,
    try_handler,
    writer,
)
from doeff_core_effects.scheduler import scheduled
from doeff_vm import Err, Ok

from doeff import EffectBase, Pass, Transfer, do, run, with_handlers
from doeff import handler as _program_handler


@dataclass
class RunResult:
    """Legacy-shaped outcome of :func:`run_program`."""

    value: Any = None
    error: BaseException | None = None
    raw_store: dict[Any, Any] = field(default_factory=dict)
    log: list[Any] = field(default_factory=list)

    def is_ok(self) -> bool:
        return self.error is None

    def is_err(self) -> bool:
        return self.error is not None


class _SnapshotStore(EffectBase):
    """Ask the store handler for a copy of every key it holds."""


def _store(initial: dict[Any, Any]) -> Callable[[Any], Any]:
    """``state``-equivalent handler that can also answer ``_SnapshotStore``."""
    store = dict(initial)

    @do
    def handle(effect: Any, k: Any) -> Generator[Any, Any, Any]:
        if isinstance(effect, Get):
            return (yield Transfer(k, store.get(effect.key)))
        if isinstance(effect, Put):
            store[effect.key] = effect.value
            return (yield Transfer(k, None))
        if isinstance(effect, _SnapshotStore):
            return (yield Transfer(k, dict(store)))
        yield Pass(effect, k)

    return _program_handler(handle)


def _log_entry(effect: Any) -> Any:
    if isinstance(effect, SlogEffect):
        return {"msg": effect.msg, **effect.kwargs}
    return effect.msg


def run_program(
    program: Any,
    *,
    handlers: Iterable[Any] = (),
    env: dict[Any, Any] | None = None,
    store: dict[Any, Any] | None = None,
) -> RunResult:
    """Run ``program`` under ``handlers`` plus the standard core-effects chain.

    ``handlers`` (raw ``(effect, k)`` dispatchers or Program -> Program
    handlers) are listed outermost first and sit *inside* the core chain, so
    they see the program's effects before the defaults. Core chain, outer ->
    inner: reader, store, writer, try, slog, local, listen, await, lazy_ask
    (the store must be outside ``writer`` / ``slog_handler``, which keep their
    logs in state).
    """

    @do
    def _wrap() -> Generator[Any, Any, tuple[Any, dict[Any, Any], list[Any]]]:
        outcome, emitted = yield Listen(
            Try(with_handlers(list(handlers), program)),
            types=(WriterTellEffect, SlogEffect),
        )
        final_store = yield _SnapshotStore()
        return (outcome, final_store, [_log_entry(effect) for effect in emitted])

    chain = with_handlers(
        [
            reader(env=env),
            _store(dict(store or {})),
            writer,
            try_handler,
            slog_handler,
            local_handler,
            listen_handler,
            await_handler(),
            lazy_ask(env=env),
        ],
        _wrap(),
    )
    outcome, final_store, log = run(scheduled(chain))
    if isinstance(outcome, Ok):
        return RunResult(value=outcome.value, raw_store=final_store, log=log)
    if isinstance(outcome, Err):
        return RunResult(error=outcome.error, raw_store=final_store, log=log)
    raise RuntimeError(f"unexpected Try outcome: {type(outcome).__name__} — expected Ok/Err")


async def async_run_program(
    program: Any,
    *,
    handlers: Iterable[Any] = (),
    env: dict[Any, Any] | None = None,
    store: dict[Any, Any] | None = None,
) -> RunResult:
    """``async`` spelling of :func:`run_program` for ``pytest.mark.asyncio`` tests."""
    return run_program(program, handlers=handlers, env=env, store=store)

