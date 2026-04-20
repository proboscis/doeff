"""Shared test fixtures + legacy-style runner for doeff-openai unit tests.

The old tests relied on ``async_run(program, handlers=default_handlers(), env=env)``
which returned a ``RunResult``-like object with ``is_ok()``, ``is_err()``,
``value``, ``error`` and ``log`` attributes.

Those entry points were removed upstream (see doeff/__init__.py where
``default_handlers`` / ``async_run`` are stubbed via ``_Removed``). This
module re-implements the same surface on top of the supported primitives
(``run(scheduled(...))`` + explicit ``WithHandler`` composition) so the
pre-existing test suite can run unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from doeff import WithHandler, do, run
from doeff_core_effects.effects import Try
from doeff_core_effects.handlers import (
    await_handler,
    lazy_ask,
    listen_handler,
    local_handler,
    slog_handler,
    state,
    try_handler,
    writer,
)
from doeff_core_effects.scheduler import scheduled
from doeff_openai.handlers import calculate_cost_handler
from doeff_vm import Err, Ok


@dataclass
class RunResult:
    """Mimics the legacy ``async_run`` return shape used by these tests."""

    value: Any = None
    error: BaseException | None = None
    log: list[Any] = field(default_factory=list)

    def is_ok(self) -> bool:
        return self.error is None

    def is_err(self) -> bool:
        return self.error is not None


def _build_chain(program: Any, env: dict | None):
    """Build the legacy default handler chain.

    Returns ``(wrapped_program, writer_ref, slog_ref)`` — the two refs let
    the caller read the accumulated ``WriterTellEffect`` / ``Slog`` log
    after ``run`` returns. Composition order mirrors the working pattern
    in ``doeff-traverse/tests/test_traverse_deep_recursion.py`` —
    ``scheduled`` is the outermost wrapper, and every in-chain
    ``try_handler`` / ``listen_handler`` / ``state`` / etc. sits inside
    it so scheduler-owned effects resolve correctly regardless of which
    Try/Listen scope they're nested in.

    ``slog_handler`` is installed but positioned *outside* ``writer`` —
    writer captures the ``Tell`` stream first (so it shows up in
    ``RunResult.log``) and then Pass-es through. ``slog_handler`` is
    retained for tests that explicitly emit structured ``slog`` calls
    with kwargs.
    """
    writer_h = writer()
    slog_h = slog_handler()
    wrapped = program
    wrapped = WithHandler(calculate_cost_handler, wrapped)
    wrapped = WithHandler(await_handler(), wrapped)
    wrapped = WithHandler(listen_handler, wrapped)
    wrapped = WithHandler(local_handler, wrapped)
    wrapped = WithHandler(state(), wrapped)
    wrapped = WithHandler(try_handler, wrapped)
    wrapped = WithHandler(writer_h, wrapped)
    wrapped = WithHandler(slog_h, wrapped)
    wrapped = WithHandler(lazy_ask(env=env or {}), wrapped)
    return scheduled(wrapped), writer_h, slog_h


async def run_program(program: Any, env: dict | None = None) -> RunResult:
    """Run ``program`` with the legacy default handler chain and wrap the
    outcome in a :class:`RunResult`.

    The ``async`` signature is preserved so existing ``@pytest.mark.asyncio``
    tests work unchanged — the implementation itself is synchronous.
    """

    @do
    def _wrap():
        return (yield Try(program))

    chain, writer_h, _slog_h = _build_chain(_wrap(), env)
    outcome = run(chain)

    log = list(writer_h.log)
    if isinstance(outcome, Ok):
        return RunResult(value=outcome.value, log=log)
    if isinstance(outcome, Err):
        return RunResult(error=outcome.error, log=log)
    raise RuntimeError(
        f"unexpected Try outcome: {type(outcome).__name__} — expected Ok/Err"
    )


__all__ = ["RunResult", "run_program"]
