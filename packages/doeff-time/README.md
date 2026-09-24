# doeff-time

Provider-agnostic time effects for `doeff`.

## Effects

- `Delay(seconds)`
- `WaitUntil(target: datetime)` (`target` must be timezone-aware)
- `GetTime()`
- `GetMonotonic()` — monotonic seconds (`float`); only differences are meaningful
- `ScheduleAt(time: datetime, program)` (`time` must be timezone-aware)
- `SetTime(time: datetime)` (for simulation handlers, `time` must be timezone-aware)

## Handlers

- `async_time_handler()` for `asyncio` runtimes
- `sync_time_handler()` for blocking runtimes
- `sim_time_handler(start_time=...)` for deterministic virtual time (`start_time` is timezone-aware
  `datetime`), or `sim_time_handler(clock=SimClock(start))` when the caller wants to read or move
  the virtual clock outside the program (tests)

### Virtual time contract (`sim_time_handler`)

- Install inside `scheduled(...)` and outside every `Spawn` whose tasks sleep on it; spawned tasks
  inherit the handler and share one clock.
- Time advances to the earliest pending wake-up only when no normal-priority task is runnable, so a
  task woken at `t` reads `t` however many scheduler steps it takes.
- Wake-ups at the same instant resume in the order they were requested; the trace is identical on
  the Python and the Rust scheduler (`DOEFF_SCHEDULER=rust`).
- The run ends when the root program returns, even while daemon services are still sleeping.
- `GetMonotonic()` answers the virtual clock's POSIX timestamp.
