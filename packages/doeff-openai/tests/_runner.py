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

import os  # noqa: PINJ050 — test-only env bridge for Ask("openai_api_key")
import runpy
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from doeff import AskEffect, Pass, Resume, WithHandler, do, run
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


@do
def openai_api_key_from_env_handler(effect, k):
    """Resolve ``Ask("openai_api_key")`` from the ``OPENAI_API_KEY`` env var.

    Keeps ``os.environ`` access confined to a single handler: the
    program under test still yields a plain ``Ask`` effect and never
    touches environment variables directly. Any other effect — or an
    ``Ask`` for a different key — is passed through so an outer handler
    (e.g. a ``lazy_ask`` with an env dict) can resolve it.

    When the ``OPENAI_API_KEY`` variable is absent the effect is
    ``Pass``-ed rather than resolved with ``None`` — that matches the
    loud-fail contract for missing keys.
    """
    if isinstance(effect, AskEffect) and effect.key == "openai_api_key":
        value = os.environ.get("OPENAI_API_KEY")
        if value is not None:
            return (yield Resume(k, value))
    yield Pass(effect, k)


DOEFF_PY_PATH = Path("~/.doeff.py").expanduser()


@lru_cache(maxsize=1)
def _load_doeff_py_env() -> dict[str, Any]:
    """Load ``__default_env__`` from ``~/.doeff.py`` as a plain dict.

    ``~/.doeff.py`` defines ``__default_env__ = Pure({...})``. We read
    the file via :func:`runpy.run_path` and extract the literal dict
    from the ``Pure`` wrapper so callers can use it without running a
    doeff program. Cached because the file is read-only per process.
    Returns an empty dict if the file does not exist.
    """
    if not DOEFF_PY_PATH.exists():
        return {}
    module_globals = runpy.run_path(str(DOEFF_PY_PATH))
    default_env = module_globals.get("__default_env__")
    if default_env is None or not hasattr(default_env, "value"):
        return {}
    return dict(default_env.value)


@do
def openai_api_key_from_doeff_py_handler(effect, k):
    """Resolve ``Ask("openai_api_key")`` from ``~/.doeff.py``.

    The personal API key lives at ``openai_api_key__personal`` in the
    user's ``~/.doeff.py``. This handler bridges the unqualified
    ``openai_api_key`` the OpenAI call path expects onto that entry.
    Any other Ask (or a missing ``~/.doeff.py``) is passed through.
    """
    if isinstance(effect, AskEffect) and effect.key == "openai_api_key":
        env = _load_doeff_py_env()
        value = env.get("openai_api_key__personal")
        if value is not None:
            return (yield Resume(k, value))
    yield Pass(effect, k)


def doeff_py_has_openai_key() -> bool:
    """True when ``~/.doeff.py`` has an ``openai_api_key__personal`` entry."""
    return bool(_load_doeff_py_env().get("openai_api_key__personal"))


__all__ = [
    "RunResult",
    "doeff_py_has_openai_key",
    "openai_api_key_from_doeff_py_handler",
    "openai_api_key_from_env_handler",
    "run_program",
]
