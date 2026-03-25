"""
doeff-core-effects — reference implementation of effects and handlers.

This package provides:
- Core effects: Ask, Get, Put, Tell
- Core handlers: reader, state, writer
- Scheduler: Spawn, Wait, Gather, Race, Cancel, Promise, ExternalPromise, Semaphore
"""

from doeff_core_effects.effects import (
    Ask, Get, Put, Tell, Try, Slog, WriterTellEffect,
    Local, Listen, Await, slog,
)
from doeff_core_effects.handlers import (
    reader, state, writer, try_handler, slog_handler,
    local_handler, listen_handler, await_handler,
)
from doeff_core_effects.scheduler import (
    Cancel,
    CreateExternalPromise,
    CreatePromise,
    CreateSemaphore,
    AcquireSemaphore,
    ReleaseSemaphore,
    CompletePromise,
    ExternalPromise,
    FailPromise,
    Future,
    Gather,
    PRIORITY_HIGH,
    PRIORITY_IDLE,
    PRIORITY_NORMAL,
    Promise,
    Race,
    Semaphore,
    Spawn,
    Task,
    TaskCancelledError,
    Wait,
    scheduled,
)
