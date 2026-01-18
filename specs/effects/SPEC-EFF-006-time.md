# SPEC-EFF-006: Time Effects

## Status: Final

## Summary

This spec defines the semantics for time-related effects in doeff: `Delay`, `GetTime`, and `WaitUntil`. These effects provide runtime-agnostic time control, with behavior that varies by runtime implementation.

## Effects

### Delay

Wait for a specified duration.

```python
from doeff.effects import Delay

@do
def program():
    yield Delay(seconds=5.0)  # Wait 5 seconds
    return "done"
```

**Semantics:**
- `seconds` must be non-negative (validated at construction)
- Returns `None` when completed

### GetTime

Get the current time.

```python
from doeff.effects import GetTime

@do
def program():
    now = yield GetTime()
    return f"Current time: {now}"
```

**Semantics:**
- Returns a `datetime` object
- The returned datetime is naive (no timezone info) unless the runtime provides timezone-aware time

### WaitUntil

Wait until a specific point in time.

```python
from doeff.effects import WaitUntil
from datetime import datetime

@do
def program():
    target = datetime(2025, 1, 1, 12, 0, 0)
    yield WaitUntil(target)
    return "arrived at target time"
```

**Semantics:**
- `target_time` is a `datetime` object
- If `target_time` is in the past, returns immediately
- Returns `None` when completed

## Runtime Behavior Matrix

| Effect | SyncRuntime | SimulationRuntime | AsyncRuntime |
|--------|-------------|-------------------|--------------|
| Delay | Real sleep (blocking) | Advances sim time (instant) | Real sleep (async) |
| GetTime | `datetime.now()` | Simulated time | `datetime.now()` |
| WaitUntil | Real sleep until target | Advances sim time to target | Real sleep until target |

### SyncRuntime

- **Delay**: Blocks the thread using `time.sleep(seconds)`
- **GetTime**: Returns `datetime.now()`
- **WaitUntil**: Calculates the duration until `target_time` and blocks using `time.sleep()`
  - If `target_time` is in the past, returns immediately

### SimulationRuntime

- **Delay**: Instantly advances the simulated clock by `seconds` (no real waiting)
- **GetTime**: Returns the current simulated time
- **WaitUntil**: Instantly advances the simulated clock to `target_time`
  - If `target_time` is before current simulated time, returns immediately without advancing

The `SimulationRuntime` maintains an internal clock that starts at either the provided `start_time` or `datetime.now()`. This enables deterministic testing of time-dependent programs.

```python
from doeff.cesk.runtime import SimulationRuntime
from datetime import datetime

start = datetime(2025, 1, 1, 12, 0, 0)
runtime = SimulationRuntime(start_time=start)

# After Delay(60) or WaitUntil(datetime(2025, 1, 1, 12, 1, 0)):
# runtime.current_time == datetime(2025, 1, 1, 12, 1, 0)
```

### AsyncRuntime

The `AsyncRuntime` intercepts time effects and handles them using asyncio primitives, enabling non-blocking concurrent execution.

- **Delay**: Uses `asyncio.sleep(seconds)`
  - Non-blocking: Other coroutines can run during the wait
  - The effect is dispatched as an async task; the runtime waits for completion
  - Returns `None` when completed

- **GetTime**: Returns `datetime.now()` (falls through to default handler)
  - Note: Does not track time via store like `SimulationRuntime`

- **WaitUntil**: Calculates duration and uses `asyncio.sleep(delay_seconds)`
  - Compares `target_time` against `datetime.now()` at effect execution time
  - If `target_time > now`: sleeps for `(target_time - now).total_seconds()`
  - If `target_time <= now`: returns immediately without sleeping
  - Returns `None` when completed

**Implementation Detail**: AsyncRuntime registers placeholder handlers for `DelayEffect` and `WaitUntilEffect` that return `None` immediately. The actual async handling happens in the scheduler loop, which intercepts these effects before dispatching to handlers:

```python
# In AsyncRuntime._run_scheduler():
if isinstance(effect, DelayEffect):
    coro = self._do_delay(effect.seconds, state.store)
    pending_async[task_id] = (asyncio.create_task(coro), result)
    continue

if isinstance(effect, WaitUntilEffect):
    coro = self._do_wait_until(effect.target_time, state.store)
    pending_async[task_id] = (asyncio.create_task(coro), result)
    continue
```

**Concurrency**: Multiple `Delay` or `WaitUntil` effects from different tasks (via `Gather`) can run concurrently. Each becomes an independent `asyncio.Task`.

## Timezone Handling

### Current Design: Naive Datetime

The current implementation uses **naive datetime** (without timezone information):

```python
# GetTime returns:
datetime.now()  # No timezone info

# WaitUntil compares:
(effect.target_time - current_time).total_seconds()
```

### Rationale

1. **Simplicity**: Naive datetime is simpler and covers most use cases
2. **Consistency**: All comparisons use the same naive representation
3. **SimulationRuntime compatibility**: Simulated time doesn't have real-world timezone concerns

### Recommendations

For **real-time applications** requiring timezone awareness:

1. **Provide timezone-aware datetimes to WaitUntil**:
   ```python
   from datetime import datetime, timezone
   
   target = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
   yield WaitUntil(target)
   ```

2. **Be consistent**: If using timezone-aware datetimes, use them everywhere in your program

3. **Handle comparisons carefully**: Comparing timezone-aware and naive datetimes raises TypeError

### Known Limitations

1. **GetTime always returns naive datetime**: Even if `start_time` passed to `SimulationRuntime` is timezone-aware, `GetTime` behavior depends on implementation
2. **Cross-timezone comparisons**: No automatic timezone conversion is performed
3. **DST transitions**: No special handling for daylight saving time transitions

### Future Considerations

A future version may:
- Add an optional `tz` parameter to `GetTime`
- Add timezone validation to `WaitUntil`
- Provide timezone-aware variants of time effects

## Handler Registration

All time effects are registered in `default_handlers()`:

```python
from doeff.cesk.handlers import default_handlers

handlers = default_handlers()
# handlers[DelayEffect] == handle_delay
# handlers[GetTimeEffect] == handle_get_time
# handlers[WaitUntilEffect] == handle_wait_until
```

## Implementation Notes

### Store-Based Time Tracking

The CESK handlers track time through a special store key `__current_time__`:

- When present: Handlers use and update this value
- When absent: Handlers use `datetime.now()` directly

This enables `SimulationRuntime` to inject controlled time into the store.

### SimulationRuntime Override

`SimulationRuntime` intercepts `DelayEffect` and `WaitUntilEffect` before they reach default handlers:

```python
if isinstance(effect, DelayEffect):
    self._current_time += timedelta(seconds=effect.seconds)
    new_store = {**state.store, "__current_time__": self._current_time}
    state = result.resume(None, new_store)
```

This ensures time advancement is instant in simulation mode.

### AsyncRuntime Override

`AsyncRuntime` also intercepts time effects but handles them asynchronously:

```python
async def _do_delay(self, seconds: float, store: Store) -> tuple[Any, Store]:
    await asyncio.sleep(seconds)
    return (None, store)

async def _do_wait_until(self, target_time: datetime, store: Store) -> tuple[Any, Store]:
    now = datetime.now()
    if target_time > now:
        delay_seconds = (target_time - now).total_seconds()
        await asyncio.sleep(delay_seconds)
    return (None, store)
```

Key differences from SyncRuntime:
- Uses `asyncio.sleep()` instead of `time.sleep()`
- Effects are scheduled as async tasks, allowing concurrency
- Does not update `__current_time__` in store (uses real wall-clock time)

## Testing

### Sync/Simulation Runtime Tests

Tests in `tests/cesk/test_new_runtime.py`:

```python
class TestWaitUntilHandler:
    def test_wait_until_sync_runtime(self): ...
    def test_wait_until_past_time(self): ...
    def test_wait_until_simulation_runtime(self): ...
    def test_wait_until_handler_registered(self): ...
```

### AsyncRuntime Tests

Tests in `tests/cesk/test_async_runtime.py`:

```python
class TestAsyncRuntimeTimeEffects:
    async def test_async_delay(self): ...
    async def test_async_get_time(self): ...
    async def test_async_wait_until(self): ...
    async def test_async_wait_until_past(self): ...
```

These tests verify:
- `Delay` actually waits the specified duration
- `GetTime` returns a time between test start and end
- `WaitUntil` waits until the target time is reached
- `WaitUntil` returns immediately for past times

## References

- `doeff/effects/time.py` - Effect definitions
- `doeff/cesk/handlers/time.py` - Handler implementations (Sync)
- `doeff/cesk/runtime/async_.py` - AsyncRuntime with async time handling
- `doeff/cesk/runtime/simulation.py` - SimulationRuntime with time control
- `tests/cesk/test_new_runtime.py` - Sync/Simulation test suite
- `tests/cesk/test_async_runtime.py` - AsyncRuntime test suite
