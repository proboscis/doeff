# doeff-stream

A runtime that merges input sources into a single stream and folds it with a pure step.

```
host = fold(step, initial_state, merge(subscribe(...), timers()))
```

- A step is either a pure function `step(state, input) -> Next(state, effects)` or a function that returns a Program answering that `Next` (a defk that only calls other defks with `<-`). Effects are values, and the runtime executes them. A step's Program cannot yield any effect (see the section "Writing a step as a Program").
- The runtime runs each `Run(key, program)` in its own task, and always returns the end of that task to the step as exactly one input.
  Finishing with a value gives `Answered(key, value)`, finishing with an exception gives `Raised(key, error)`, and being stopped by the step's `CancelRun(key)` gives `Cancelled(key)`.
- The fold does not wait for effect answers. It waits only for the next input.

There are only four parts. There is no family of operators such as map, window, or backpressure.

| Part | Entry point | Contents |
|---|---|---|
| Source | `subscribe(*types)`, `timers()` | Events that outside tasks publish to the bus, and the deadlines (`TimerFired`) that the step has set. Effect answers, task endings, the stop signal (`StopArrived`), and failures of the signal's source (`SourceFailed`) are the runtime's own sources and are always included in every merge |
| merge | `merge(*sources) -> Inputs` | Turns sources into a single stream of inputs. `Inputs.event_types` is the full set of types the fold waits for |
| fold | `fold(step, initial, inputs)` | Passes inputs to the step one at a time and executes the answered effects in order. `Finish(value)` answers `value` |
| test harness | `replay(step, initial, inputs) -> Replay` | Feeds a sequence of inputs to the step and returns the sequence of states (`states`, `final`) and effects (`effects`). A plain-function step does not use the VM. A step that returns a Program is run on the VM once per input, using the same evaluation as the fold (a handler that rejects every effect) |
| simulation base | `simulate_fold(step, initial, inputs, *, events, handlers, start_time)` | Assembles the fold in the same shape as production, runs it with a simulated clock (`sim_time_handler`) and an in-memory `EventBus`, and returns the fold's answer (see the section "Running on the simulation base") |

## Effects a step returns

| effect | Behavior |
|---|---|
| `Run(key, program, priority=TaskPriority.NORMAL)` | Runs `program` (a Program or an effect) in a new task. Its end comes back as a single input carrying `key`. A `Run` for a key that is already running, and a `program` that is neither a Program nor an effect, are rejected. `priority` is the scheduler priority of the run's task (and of the watcher a `CancelRun` of it starts — see "Why every ending becomes an input"): the `Spawn` default `TaskPriority.NORMAL` unless named (any `int`, higher runs first; a non-`int` is rejected) |
| `CancelRun(key)` | Cancels the task for `key`. Its end still comes back as a single input (`Cancelled` if the cancellation took effect, or its own ending if it had already finished). Does nothing for a key that is not running |
| `Arm(tag, at)` | Sets this fold's deadline `tag` to `at` (a timezone-aware `datetime`). No task is created; the fold hands `ArmTimer` to the timer handler. It comes back at `at` as a `TimerFired(tag)` input. Setting an existing tag again moves the deadline. Rejected in a fold that does not have `timers()` as a source |
| `Disarm(tag)` | Removes this fold's deadline `tag`. Its `TimerFired` does not come back. Does nothing for a tag that is not set (including one already removed or already fired) |
| `Finish(value)` | Ends the fold. At most one per answer, and it is executed after the other effects wherever it is placed. It cancels running tasks, waits for all of them to finish, and then answers `value` (their endings are not passed to the step) |

When the step raises an exception, or when the fold's task is cancelled, the fold removes the deadlines that are still set, cancels the running tasks, and then lets the exception propagate. It also removes the remaining deadlines on `Finish`.

The fold sets an `Arm` deadline in the timer handler with a tag that pairs its own mark with `tag`. `TimerFired` reaches every subscriber on the bus, but the fold turns back only the ones with its own mark into `TimerFired(tag)` for the step, and discards the deadlines of other folds on the same bus. So folds on the same bus do not get mixed up even if they use the same tag. Re-arming a single deadline needs neither a task nor a run ending (`Run(key, ArmTimer(tag, at))` creates a task and adds one ending input).

## Why every ending becomes an input

A single `Run` creates one task. It runs the program, receives the ending in a try/except and does one `Publish` for either a value or an exception (the `TaskCancelledError` of a cancellation included). A `CancelRun` cancels that task and then creates a second task, a watcher that waits for it with `Wait`. The fold never cancels the watcher. A task cancelled before it started, or cancelled again at its `Publish`, publishes nothing, and the watcher publishes its ending instead. So even the ending of a task cancelled before it started becomes an input. A task that did publish also ends as cancelled, and then its watcher publishes a second ending. Each ending carries a mark for its own run, so the fold passes over the second ending of a run whose ending it already took, even when a new run with the same key is started. A `Run` that is never cancelled costs one task and one `Publish`.
When the program's task ends with `TaskCancelledError`, the result is `Cancelled` if the step cancelled that key, and `Raised` if the error was raised by the program itself (waiting on another task that was cancelled).

The `Publish` is queued in the fold's subscriber queue, so a task that ends while the step is handling another input is not lost. The ending of a task run by another fold on the same bus
is told apart from this fold's own and discarded.

## Contracts that steps and runs must keep

| Contract | Reason |
|---|---|
| Separate keys per component (a tuple that begins with the component name, such as `("fetch", item)`) | A `Run` for a key that is already running is rejected, and run endings can only be dispatched by key |
| When two folds share a bus and a run sets a timer (`Run(key, ArmTimer(tag, at))`), separate the tags per fold | The `TimerFired` of a timer set by a run reaches every subscriber on the bus as is, and the fold cannot tell them apart (a deadline set with `Arm` is told apart by the fold, so it needs no separation) |
| A step's Program does not yield effects (none of `Ask`, clock, reads and writes, `Wait`, `Spawn`, `WaitForEvent`) | The fold wraps the step's Program in a handler that rejects every effect and stops any yielded effect with `StepYieldedEffectError`. Read the outside world through inputs, and ask for the work you want done with `Run` |
| A run's program does not do `WaitForEvent` or `WaitForEvents` | A run executes under the fold's bus handler, so such a wait would steal the fold's inputs |
| A run ends when it is cancelled. A run that waits on the outside world puts a deadline on its own wait (`WaitWithin`) | The fold has no clock, and the `Finish` and exception paths wait until the runs end |
| On receiving the stop signal (`StopArrived`), the step decides the order of stopping and returns `Finish` | The fold does not stop on its own on the stop signal |

## Assembly

The only handler the fold wraps is the single one around the step's Program (the one that rejects every effect). It does not wrap runs. The caller places the bus handler that subscribes to `inputs.event_types` (`subscribed_event_handler`), and, when `timers()` is used, the timer handler inside it, and runs the whole thing under `scheduled`.

```python
from doeff_core_effects import scheduled
from doeff_events import EventBus, subscribed_event_handler, timer_handler
from doeff_stream import Answered, Finish, Next, Run, fold, merge, subscribe, timers

def step(state, event):
    match event:
        case Poke():
            return Next(state, (Run("fetch", fetch()),))
        case Answered(key="fetch", value=value):
            return Next(value, (Finish(value),))
        case _:
            return Next(state)

inputs = merge(subscribe(Poke), timers())
host = subscribed_event_handler(EventBus(), "host", inputs.event_types)(
    timer_handler()(fold(step, None, inputs))
)
result = run(scheduled(sim_time_handler(start_time=T0)(host)))
```

## Measuring the fold

The subscriber queue has no bound and the fold takes one input per wait, so a fold that falls behind only shows in numbers. Under a caller's handler annotated with `FoldMeasured`, the fold performs one `FoldMeasured` after each input it gave the step, once it carried out the step's effects:

| Field | What it is |
|---|---|
| `queued` | the inputs waiting in the fold's subscriber queue when it took this one (`CountQueued(*inputs.event_types)`, answered by `subscribed_event_handler`) |
| `running` | the runs started and not yet ended (`0` after a `Finish`) |
| `step_seconds` | the step's time, its Program's evaluation included, as two `GetMonotonic` readings tell it (`0.0` on the virtual clock) |

Where the value goes is the caller's handler — for example the instruments of doeff-core-effects:

```python
from doeff import Resume, handler
from doeff_core_effects import ObserveSeconds, SetGauge
from doeff_stream import FoldMeasured

@handler
def fold_instruments(effect: FoldMeasured, k):
    yield SetGauge("fold_queued", float(effect.queued))
    yield SetGauge("fold_running", float(effect.running))
    yield ObserveSeconds("fold_step", effect.step_seconds)
    return (yield Resume(k, None))
```

The fold reads once, when it starts, whether a handler around it names `FoldMeasured` (a handler that admits every effect is not read as one). Without one it measures nothing — the measures are dropped by default, and the fold performs no `CountQueued` and reads no clock.

## Writing a step as a Program

Instead of a `Next` value, a step may return a Program that answers `Next`. This is the shape where a reducer written with defk calls other defks with `<-` to build its answer.

```python
@do
def ordered(state, item):
    return Next(state | {item}, (Run(("fetch", item), fetch(item)),))

@do
def step(state, event):
    match event:
        case Order(item=item):
            return (yield ordered(state, item))
        case _:
            return Next(state)
```

- When a step's answer is a Program, the fold evaluates it under a handler that rejects every effect and validates the resulting value as a `Next`. `replay` goes through the same evaluation path (`step_answer`), so the same input sequence produces the same sequence of states and effects.
- What is rejected is every effect, not an enumerated list. When a step's Program yields an effect, evaluation ends on the spot without resuming the continuation, and `StepYieldedEffectError` is raised (`effect` = the effect yielded, `step` = the step's name). A `try` inside the step cannot catch this rejection. In a fold, as with other step exceptions, the running runs are cancelled before the exception propagates.
- A step's answer is either a `Next` or a Program that answers a `Next`, nothing else. It is a `TypeError` if the Program answers something other than `Next` (a tuple or another Program), or if `effects` contains a value that is not a `StepEffect` (such as a bare Program).
- A plain-function step behaves as before, and `replay` does not use the VM for it.

Cost (2026-11-03, x86_64, Python 3.14, one call of a plain-function step is about 0.5 µs): evaluating one step as a Program takes about 5 µs inside a fold with one level of composition, about 6 µs in `replay` including the per-input `run`, and about 25 µs when it calls 10 levels of other Programs (about 2 µs per level). If the overhead matters for a step that judges many candidates per input, the step can call that judgment as a plain function.

## Test harness

```python
from doeff_stream import Answered, Finish, Run, replay

trace = replay(step, None, [Poke(), Answered("fetch", 3)])
assert [type(effect) for effect in trace.effects] == [Run, Finish]
assert trace.effects[-1] == Finish(3)
assert trace.final == 3
```

`replay` validates the step's answers with the same checks as the fold, and rejects inputs the runtime would never deliver (the ending of a key that is not running, and inputs after `Finish`).

`Arm` and `Disarm` appear in the `effects` sequence like other effects. For the case where a deadline arrives, the test writes `TimerFired(tag)` into the inputs. `Replay.armed` is the set of tags that have been set and have neither been removed nor fired yet.

## Running on the simulation base

`replay` measures only the step. To measure by running the fold including the run's programs, use `simulate_fold`.

```python
from doeff_stream import merge, simulate_fold, subscribe, timers

result = simulate_fold(
    step,
    None,
    merge(subscribe(Poke), timers()),
    events=(Poke(),),                 # the fold's first inputs (in this order)
    handlers=(simulated_fetch,),      # the caller's simulated handlers that answer the run's effects (first is outermost)
    start_time=T0,                    # the start of the simulated clock (None uses sim_time_handler's default)
)
```

- The assembly is the same as the production shape in the section "Assembly". There are only two differences, the clock and the bus: the clock is `sim_time_handler` (time advances only when every task is waiting), and the bus is an in-memory `EventBus` used only by this call. `scheduled`, `subscribed_event_handler` (subscribing to `inputs.event_types`), `timer_handler`, and `fold` are the same as in production.
- `events` are published to the bus in order before the fold first waits, and become the step's first inputs. Their types are limited to the types `inputs` subscribes to. Later inputs come from run endings, timers the step set, and events the runs publish.
- `handlers` are wrapped inside the bus and timer handlers (on the fold's side) in the order of `with_handlers`. The effects of a run's program reach these handlers first, and the handlers can use `Publish`, timer setting, and waits on the simulated clock.
- Exceptions from the fold (step exceptions, `StepYieldedEffectError`, and so on) propagate out of this call as they are.
