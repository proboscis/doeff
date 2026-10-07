# doeff-time

Provider-agnostic time effects for `doeff`.

## Effects

- `Delay(seconds)`
- `WaitUntil(target: datetime)` (`target` must be timezone-aware)
- `GetTime()`
- `GetMonotonic()` — monotonic seconds (`float`); only differences are meaningful
- `ScheduleAt(time: datetime, program)` (`time` must be timezone-aware) — answers the spawned `Task`;
  `Wait(task)` answers `program`'s value, or raises its failure
- `SetTime(time: datetime)` (for simulation handlers, `time` must be timezone-aware)

## Conversions

- `epoch_ms_of(at: datetime) -> int` — timezone-aware time → epoch milliseconds, floored by integer
  `timedelta` division (no float on the way). This is the one rounding for every stamp that is laid
  next to another stamp: two places rounding the same instant differently (round vs floor) make a
  write at 329.6 ms look 1 ms later than a read at 329.9 ms.

## Handlers

- `async_time_handler()` for `asyncio` runtimes
- `sync_time_handler()` for blocking runtimes
- `sim_time_handler(start_time=...)` for deterministic virtual time (`start_time` is timezone-aware
  `datetime`), or `sim_time_handler(clock=SimClock(start))` when the caller wants to read or move
  the virtual clock outside the program (tests)

### Shared contract (all three handlers)

`tests/test_time_contract.hy` runs the same deftests under each handler (`:interpreters
["async" "sync" "sim"]`): readings never go back, `Delay(d)` / `WaitUntil(t)` advance the clock by
`d` / up to `t` (exactly on the virtual clock, with a small jitter bound on the wall clock), past
targets return at once, other effects pass through, and `ScheduleAt` tasks run in time order and
answer their program's value or failure through `Wait`. `SetTime` is simulation-only and stays out
of the shared contract. Note: the scheduled program runs under the handlers outside the time
handler, so it cannot itself perform time effects.

### Virtual time contract (`sim_time_handler`)

- Install inside `scheduled(...)` and outside every `Spawn` whose tasks sleep on it; spawned tasks
  inherit the handler and share one clock.
- Time advances to the earliest pending wake-up only when no normal-priority task is runnable, so a
  task woken at `t` reads `t` however many scheduler steps it takes.
- Wake-ups at the same instant resume in the order they were requested; the trace is identical on
  the Python and the Rust scheduler (`DOEFF_SCHEDULER=rust`).
- The run ends when the root program returns, even while daemon services are still sleeping.
- `GetMonotonic()` answers the virtual clock's POSIX timestamp.
