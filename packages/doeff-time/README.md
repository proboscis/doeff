# doeff-time

Provider-agnostic time effects for `doeff`.

## Effects

- `Delay(seconds)`
- `WaitUntil(target)`
- `GetTime()`
- `ScheduleAt(time, program)`

## Handlers

- `async_time_handler()` for `asyncio` runtimes
- `sync_time_handler()` for blocking runtimes
