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

from doeff_core_effects.effects import Try
from doeff_core_effects.handlers import (
    writer_log,
)
from doeff_openai.handlers import calculate_cost_handler
from doeff_vm import Err, Ok

from doeff import AskEffect, Pass, Resume, do, run
from doeff import handler as _install_raw_handler
from tests._run_helpers import wrap_with_defaults


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

    Delegates to ``tests/_run_helpers.py`` -- the one definition of the
    pre-rebuild handler order that the root suite and seven package trees
    already share -- and only adds the openai-specific cost handler on the
    inside. Hand-rolling the order here got it wrong: ``state()`` sat inside
    ``writer``, so ``writer_log()``'s ``Get`` escaped past every handler and
    20 tests died with ``UnhandledEffect: Get('__doeff_writer_log__')``.
    """
    wrapped = _install_raw_handler(calculate_cost_handler)(program)
    return wrap_with_defaults(wrapped, env=env or {})


async def run_program(program: Any, env: dict | None = None) -> RunResult:
    """Run ``program`` with the legacy default handler chain and wrap the
    outcome in a :class:`RunResult`.

    The ``async`` signature is preserved so existing ``@pytest.mark.asyncio``
    tests work unchanged — the implementation itself is synchronous.
    """

    @do
    def _wrap():
        outcome = yield Try(program)
        log = yield writer_log()
        return (outcome, log)

    chain = _build_chain(_wrap(), env)
    result = run(chain)
    outcome, log = result

    if isinstance(outcome, Ok):
        return RunResult(value=outcome.value, log=list(log))
    if isinstance(outcome, Err):
        return RunResult(error=outcome.error, log=list(log))
    raise RuntimeError(f"unexpected Try outcome: {type(outcome).__name__} — expected Ok/Err")


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
