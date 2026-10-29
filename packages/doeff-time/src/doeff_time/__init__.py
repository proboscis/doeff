"""Public API for doeff-time."""

from importlib import import_module
from typing import TYPE_CHECKING

from doeff_time._effects.time import Delay as Delay
from doeff_time._effects.time import DelayEffect as DelayEffect
from doeff_time._effects.time import GetMonotonic as GetMonotonic
from doeff_time._effects.time import GetMonotonicEffect as GetMonotonicEffect
from doeff_time._effects.time import GetTime as GetTime
from doeff_time._effects.time import GetTimeEffect as GetTimeEffect
from doeff_time._effects.time import RaceOutcome as RaceOutcome
from doeff_time._effects.time import RaceWithin as RaceWithin
from doeff_time._effects.time import RaceWithinEffect as RaceWithinEffect
from doeff_time._effects.time import ScheduleAt as ScheduleAt
from doeff_time._effects.time import ScheduleAtEffect as ScheduleAtEffect
from doeff_time._effects.time import SetTime as SetTime
from doeff_time._effects.time import SetTimeEffect as SetTimeEffect
from doeff_time._effects.time import TicksOutcome as TicksOutcome
from doeff_time._effects.time import WaitTicks as WaitTicks
from doeff_time._effects.time import WaitTicksEffect as WaitTicksEffect
from doeff_time._effects.time import WaitUntil as WaitUntil
from doeff_time._effects.time import WaitUntilEffect as WaitUntilEffect
from doeff_time._effects.time import WaitWithin as WaitWithin
from doeff_time._effects.time import WaitWithinEffect as WaitWithinEffect
from doeff_time._effects.time import delay as delay
from doeff_time._effects.time import get_monotonic as get_monotonic
from doeff_time._effects.time import get_time as get_time
from doeff_time._effects.time import schedule_at as schedule_at
from doeff_time._effects.time import set_time as set_time
from doeff_time._effects.time import wait_until as wait_until
from doeff_time._effects.time import wait_within as wait_within
from doeff_time._epoch_ms import epoch_ms_of as epoch_ms_of
from doeff_time._handlers.async_time import async_time_handler as async_time_handler
from doeff_time._handlers.sim_time import sim_time_handler as sim_time_handler
from doeff_time._handlers.sync_time import sync_time_handler as sync_time_handler
from doeff_time._internals.sim_clock import SimClock as SimClock

# Names of the submodules behind `_` (ADR-DOE-API-001 R5), read on first use: the root does not import
# them while it loads. `X as X` shows each name to type checkers. A root that already has `__all__`
# lists the names there too; the tool does not add `__all__` to a root without one (DOEFF021).
if TYPE_CHECKING:
    from doeff_time._handlers.async_time import AsyncTimeRuntime as AsyncTimeRuntime
    from doeff_time._handlers.sim_time import LogFormatter as LogFormatter
    from doeff_time._handlers.sync_time import SyncTimeRuntime as SyncTimeRuntime

_LAZY_EXPORTS: dict[str, str] = {
    "AsyncTimeRuntime": "doeff_time._handlers.async_time",
    "LogFormatter": "doeff_time._handlers.sim_time",
    "SyncTimeRuntime": "doeff_time._handlers.sync_time",
}


def __getattr__(name: str) -> object:
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_LAZY_EXPORTS})
