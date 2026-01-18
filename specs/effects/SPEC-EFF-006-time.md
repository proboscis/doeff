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

- **Delay**: Uses `asyncio.sleep(seconds)` (non-blocking to other coroutines)
- **GetTime**: Returns `datetime.now()`
- **WaitUntil**: Uses `asyncio.sleep()` with calculated duration until target
  - If `target_time` is in the past, returns immediately

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

## Testing

Tests for time effects are located in `tests/cesk/test_new_runtime.py`:

```python
class TestWaitUntilHandler:
    def test_wait_until_sync_runtime(self): ...
    def test_wait_until_past_time(self): ...
    def test_wait_until_simulation_runtime(self): ...
    def test_wait_until_handler_registered(self): ...
```

## References

- `doeff/effects/time.py` - Effect definitions
- `doeff/cesk/handlers/time.py` - Handler implementations
- `doeff/cesk/runtime/simulation.py` - SimulationRuntime with time control
- `tests/cesk/test_new_runtime.py` - Test suite
