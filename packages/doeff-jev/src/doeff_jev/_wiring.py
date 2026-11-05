"""Assembly-point tools: the way handlers are stacked is written in one place.

From the outside in: scheduled -> await (httpx async) -> try -> slog sink (journal / stderr / discard)
-> HTTP backend -> cache backend -> jev (Judge -> HttpRequest) -> memo (cache lookup) -> program.
Effects flow from the inner handlers outward: the program's Judge is received by memo, and on a miss jev receives it,
emits an HttpRequest, and the HTTP backend handles that.

The caller runs it: run the program with `run(scheduled(with_handlers(judge_stack(...), program)))`
(ADR-DOE-API-001 R1: this package exposes only the handler that answers the judgment effect Judge, and the factory for its stack).
"""

import os
from collections.abc import Callable
from typing import Any

from doeff_core_effects import (
    await_handler,
    http_production_handler,
    slog_discard_handler,
    slog_handler,
    try_handler,
)
from doeff_core_effects import in_memory_cache_handler, sqlite_cache_handler

from doeff.program import ProgramHandler
from doeff_jev._handlers.journal import journal_handler
from doeff_jev._handlers.production import jev_handler, jev_memo_handler
from doeff_jev._target import JevTarget, target_from_process_environment

DEFAULT_CACHE_DB = "~/.cache/doeff-jev/answers.sqlite"
DEFAULT_JOURNAL = "~/.local/state/doeff-jev/journal.log"


def judge_stack(
    target: JevTarget | None = None, *,
    http: Callable[[Any], Any] | None = None,
    cache: Callable[[Any], Any] | None = None,
    journal: str | None = DEFAULT_JOURNAL,
    caller: str = "",
    log_stderr: bool = False,
) -> list[ProgramHandler]:
    """The production handler list (outermost first). Swapping `http` / `cache` gives a test or alternative backend.

    With target=None, the destination is resolved from this process's environment and home files (how it is resolved is in the header note of _target.py; the form called by the assembly-point CLI / hook
    entry). journal=None means no journal. cache=None means in-memory (within the process only).
    caller = the caller name (form `<component>/<event or rule>`, e.g. `rulebook-advisor/Stop`). Sent to the destination in the x-jev-caller header, and
    put in the jev_judge line.
    """
    if target is None:
        target = target_from_process_environment()
    sink = slog_handler if log_stderr else slog_discard_handler
    # Effects flow from the inside out: the journal (inner) consumes the slog("jev_judge") of jev,
    # and other slogs pass out to the sink (outer). If the journal were placed outside the sink, the sink would swallow them first.
    layers: list[ProgramHandler] = [await_handler, try_handler, sink]
    if journal:
        layers.append(journal_handler(os.path.expanduser(journal), caller=caller))
    layers.append(http if http is not None else http_production_handler())
    layers.append(cache if cache is not None else in_memory_cache_handler())
    layers.append(jev_handler(target, caller=caller))
    layers.append(jev_memo_handler(target, caller=caller))
    return layers


def durable_cache(path: str = DEFAULT_CACHE_DB) -> Callable[[Any], Any]:
    """The cache backend (SQLite) that keeps answers across processes."""
    expanded = os.path.expanduser(path)
    os.makedirs(os.path.dirname(expanded), exist_ok=True)
    return sqlite_cache_handler(expanded)
