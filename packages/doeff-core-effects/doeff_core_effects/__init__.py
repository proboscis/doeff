"""
doeff-core-effects — reference implementation of effects and handlers.

This package provides:
- Core effects: Ask, Get, Put, Tell
- Core handlers: reader, state, writer
- Scheduler: Spawn, Wait, Gather, Race, Cancel, Promise, ExternalPromise, Semaphore
"""

import importlib as _importlib

from doeff_core_effects import _hy_submodules  # noqa: F401 - installs the lazy Hy finder first
from doeff_core_effects.effects import (  # noqa: F401
    Ask,
    Await,
    Get,
    Listen,
    Local,
    Put,
    Slog,
    SlogEffect,
    Tell,
    Try,
    WriterTellEffect,
    slog,
)
from doeff_core_effects.handlers import (  # noqa: F401
    await_handler,
    env_var_ask,
    lazy_ask,
    listen_handler,
    local_handler,
    reader,
    slog_discard_handler,
    slog_handler,
    state,
    try_handler,
    writer,
    writer_log,
)
from doeff_core_effects.scheduler import (  # noqa: F401
    PRIORITY_HIGH,
    PRIORITY_IDLE,
    PRIORITY_NORMAL,
    AcquireSemaphore,
    Cancel,
    CompletePromise,
    CreateExternalPromise,
    CreatePromise,
    CreateSemaphore,
    ExternalPromise,
    ExternalPromiseCancelCallbackError,
    FailPromise,
    Future,
    Gather,
    Promise,
    Race,
    ReleaseSemaphore,
    SchedulerDeadlockError,
    Semaphore,
    Spawn,
    Task,
    TaskCancelledError,
    Wait,
    scheduled,
)

# HTTP effects and handlers live in Hy and pull in httpx; load them on first use.
_LAZY_EXPORTS = {
    "HttpError": "doeff_core_effects.effects",
    "HttpRequest": "doeff_core_effects.effects",
    "HttpResponse": "doeff_core_effects.effects",
    "http_fixture_handler": "doeff_core_effects.http_handlers",
    "http_production_handler": "doeff_core_effects.http_handlers",
}


def __getattr__(name: str) -> object:
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(_importlib.import_module(module_name), name)
    globals()[name] = value
    return value
