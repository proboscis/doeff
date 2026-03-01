# doeff-time

Provider-agnostic time effects for `doeff`.

## Effects

- `Delay(seconds)`
- `WaitUntil(target: datetime)` (`target` must be timezone-aware)
- `GetTime()`
- `ScheduleAt(time: datetime, program)` (`time` must be timezone-aware)
- `SetTime(time: datetime)` (for simulation handlers, `time` must be timezone-aware)

## Handlers

- `async_time_handler()` for `asyncio` runtimes
- `sync_time_handler()` for blocking runtimes
- `sim_time_handler(start_time=...)` for deterministic virtual time (`start_time` is timezone-aware
  `datetime`)
