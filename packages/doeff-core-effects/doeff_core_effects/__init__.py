"""
doeff-core-effects — reference implementation of effects and handlers.

This package provides:
- Core effects: Ask, Get, Put, Tell
- Core handlers: reader, state, writer
- Scheduler: Spawn, Wait, Gather, Race, Cancel, Promise, ExternalPromise, Semaphore
"""

from doeff_core_effects.effects import (  # noqa: F401
    Ask,
    Await,
    Get,
    HttpError,
    HttpRequest,
    HttpResponse,
    Listen,
    Local,
    Put,
    Slog,
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
    slog_handler,
    slog_log,
    state,
    try_handler,
    writer,
    writer_log,
)
from doeff_core_effects.http_handlers import (  # noqa: F401
    http_fixture_handler,
    http_production_handler,
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
