"""Shared run helper for the doeff-events test suite.

``event_handler`` blocks listeners on scheduler promises (CreatePromise / Wait)
and the tests delay publishers with ``Await``, so programs run under the
scheduler with ``await_handler`` inside it. This replaces the removed
``run(program, handlers=default_handlers())`` surface by composing the
handlers explicitly. Uniquely named (not ``conftest``) so it does not collide
with other suites' helper modules when collected together.
"""

from typing import Any

from doeff_core_effects.handlers import await_handler
from doeff_core_effects.scheduler import scheduled

from doeff import run


def run_scheduled(program: Any) -> Any:
    """Run ``program`` under the scheduler with ``Await`` support and return its value."""

    # await_handler performs scheduler effects, so it sits inside ``scheduled``.
    return run(scheduled(await_handler()(program)))
