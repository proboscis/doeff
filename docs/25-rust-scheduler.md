# 25. The Rust Scheduler

The scheduler behind `scheduled()` (Spawn, Wait, Gather, Race, Cancel, Discard, Semaphore, Promise,
ExternalPromise) has two implementations, one in Rust and one in Python. They have the same semantics.
**The Rust implementation is the default**; the Python implementation is opt-in.
The Rust implementation lives in `packages/doeff-vm/src/scheduler.rs`, the Python one is
`_scheduled_python` in `packages/doeff-core-effects/doeff_core_effects/scheduler.py`.

[![A narrated video, about two and a half minutes: the scheduler as the handler that answers Spawn, Wait and Gather; how scheduled() chooses between the Rust and the Python implementation; one program giving the same order, Cancel and trace events under both; what moved to Rust and what stays in Python; and the benchmark taken again](assets/videos/rust-scheduler.jpg)](assets/videos/rust-scheduler.mp4)

## Choosing an implementation

```python
from doeff_core_effects.scheduler import scheduled

run(scheduled(main()))                              # the default (DEFAULT_IMPLEMENTATION = "rust")
run(scheduled(main(), implementation="python"))    # the Python implementation for this call only
```

```sh
DOEFF_SCHEDULER=python python app.py                # the Python implementation for the whole process
```

The choice is made in this order: the argument, then the environment variable `DOEFF_SCHEDULER`, then
`DEFAULT_IMPLEMENTATION`. The environment variable is read on every call to `scheduled()`, so a test can
switch it with `monkeypatch.setenv`. Any value other than `python` or `rust` raises `ValueError`.
When the Rust implementation is chosen (including by default) but the loaded Rust extension is too old
to contain the scheduler, `scheduled()` does not fall back to Python silently: it raises `ImportError`,
naming the two ways out (rebuild the Rust extension, or set `DOEFF_SCHEDULER=python`).

## Why Rust is the default, and when to choose Python

- Decision (2026-10-17, reversible): the default is `"rust"`. The CPU time per task drops to about a
  ninth (see "Measurements after changing the default" below), and with many tasks the cost comes down
  to 1.4–1.5 times that of asyncio. doeff 0.7.0 is the first release with this default.
- The earlier decision (2026-09-23) kept the default at `"python"`, because a checkout that follows the
  Python sources without rebuilding the Rust extension raises `ImportError` from `scheduled()`. The
  doeff-vm wheels on PyPI from 0.5.0 on, and a checkout installed with `make sync` (`uv sync`), contain
  the Rust scheduler. A checkout with an old extension can rebuild it, as the `ImportError` message says,
  or set `DOEFF_SCHEDULER=python`.
- Evidence that the semantics are the same: on 2026-09-23, 1,382 scheduler-related tests (scheduler,
  Cancel, Semaphore, external Promise, Await, the virtual clock of doeff-time, doeff-agents,
  doeff-conductor, doeff-traverse and others) and 100 tests of a downstream worker gave the same result
  one by one under both implementations (the same set of passed, failed and skipped tests). Since the
  default changed, tests that do not name an implementation run on Rust, and the daily full run catches
  differences between the two. Tests of behavior that depends on the implementation name it.
- The trace events of `set_scheduler_trace` (including `OpenStepTally` and `OpenTaskTally` of the step
  tally handler `step-tally-handler`) have come from both implementations since 2026-10-18: the same
  kinds, with the same fields, in the same order (see "The trace hook" below). Counting steps no longer
  requires choosing the Python implementation.
- The step limit `arm_step_budget` (#3143, set by the runaway guard of doeff-hy-pytest) has been read by
  both implementations since 2026-10-18 (see "The step limit" below). Between the change of the default
  on 2026-10-17 and that fix, the Rust implementation ignored the limit, and a run over the limit kept
  going instead of failing.
- Choose the Python implementation:
  1. To step through the scheduler line by line with pdb (the Rust implementation decides inside Rust,
     where there is no Python frame).
  2. To run a checkout in a hurry when its Rust extension cannot be rebuilt.
- How to choose it, and how to go back (all reversible):
  1. For one process: `DOEFF_SCHEDULER=python`.
  2. For one call: `scheduled(..., implementation="python")`.
  3. For the default itself: set `DEFAULT_IMPLEMENTATION` in `scheduler.py` back to `"python"`.

## What moved to Rust, and what stays in Python

Moved to Rust: every per-effect decision — the state of tasks, promises and semaphores; the priority
queue of ready tasks (a port of the same algorithm as CPython's `heapq`, so the queue order matches the
Python implementation); Cancel; receiving external completions; deadlock detection; and the sweep of
#502 (including #3504, which sweeps cancelled tasks whose cleanup has finished;
`tests/test_scheduler.py::TestTerminalEntrySweep` checks it under both implementations).

Kept in Python, so that nothing visible to users changes:

- The effect classes (`Spawn`, `Wait`, `AcquireSemaphore` and so on). The Rust implementation tells
  them apart with `isinstance`, so subclasses (such as `CreateNamedSemaphore` below) are still handled
  as their parent.
- The handle classes (`Task`, `Future`, `Promise`, `ExternalPromise`, `Semaphore`) and the exception
  classes.
- The warnings issued once per run at the end (#501, and the warning for task exceptions nobody
  retrieved), and, once per failed task, the traceback enrichment and the text that describes an
  unretrieved exception.
- The trace event dicts and the clock (`_trace`, `_trace_step_boundary`). See "The trace hook" below.
- The step limit check (`_check_step_budget`). See "The step limit" below.

### The trace hook (`set_scheduler_trace`)

- When a sink is installed, and only then, both implementations emit the same kinds of events (start,
  enqueued, resumed, spawned, task-enter, task-leave, external-complete, external-drained,
  external-ignored) with the same fields in the same order. Only the clock values, the doeff-vm step
  counts (`step_vm_steps`, `step_handler_calls` — the Rust implementation does the same work in fewer
  steps) and the run number differ. `tests/test_scheduler_trace.py` compares the sequences of both
  implementations case by case, and the step tally tests (`test_step_tally.py`, `test_task_tally.py`)
  run under both and check that the same program gives the same step counts and the same task tree.
- The Rust implementation builds the event dicts and reads the clock through the same Python helpers as
  the Python implementation (one definition). What the Rust implementation computes itself is only how a
  task was woken (wake), how many entries were ahead in the queue (ahead, ahead_by_wake), and where a
  step boundary falls.
- The Python handler receives every effect that crosses its prompt; for an effect that is not a
  scheduler effect it still emits task-leave and drains external completions before it `Pass`es.
  The Rust implementation declines non-scheduler effects through `accepts`, so when a sink is installed
  it emits the same task-leave and drain at the moment it declines (an effect answered by an outer
  handler also marks a step boundary).
- Cost while no sink is installed: each emit point reads one process-wide flag (`TRACE_INSTALLED`,
  switched together with the sink by `set_scheduler_trace`) once — no dict, no clock read, no Python
  call. Installing or removing a sink in the middle of a run takes effect from the next event (a sink
  installed after the Rust scheduler was built still receives events).
- When the sink raises: the emit points inside the Rust implementation are in the middle of a state
  update, so the exception is not raised there. An `Exception` goes to `sys.unraisablehook` and the run
  continues; `KeyboardInterrupt` and the like are thrown into the `yield` of the next task to resume
  (in the Python implementation, it is raised where the event was emitted).

### The handler forms differ (the meaning does not)

The Python implementation is a `@do` generator handler; the Rust implementation is a synchronous
handler (`call_handler` returns the next instruction at once). So three things are written differently,
with the same result as seen by the VM:

- To switch to another task, the Python implementation uses `TailEval(Transfer(k, v))` and the Rust one
  `Resume(k, v)`. The Python `Transfer` exists to drop the handler's own frame; the Rust implementation
  has no frame to drop (`Transfer` would drop the caller's frame instead).
- For an effect the scheduler does not handle, the Python implementation does `yield Pass(effect, k)`,
  the Rust one declines in `Callable::accepts`. In both cases the VM re-performs the effect outside the
  scheduler.
- An exception raised while an effect is handled is thrown into the `yield` that performed the effect,
  in both implementations (the Python one through the VM's recovery for handlers, the Rust one directly
  into the continuation it holds).

## Extension points downstream code relies on (kept in both implementations)

`tests/test_scheduler_implementation.py` checks them under both implementations.

1. **Effect subclasses**: a subclass such as `class CreateNamedSemaphore(CreateSemaphore)` is resolved
   by the scheduler as its parent effect when no handler picks it up.
2. **Handlers inside `scheduled`**: a handler for named semaphores (for example a downstream worker's
   process-local and cluster-wide semaphore handlers) can receive scheduler effects first and answer
   them itself, or `Pass` them on to the scheduler. The scheduler receives only the effects an inner
   handler passed on.
3. **The scheduler in a captured handler list**: a scheduler obtained from `GetHandlers`,
   `GetOuterHandlers` or a boundary capture is an object whose `__doeff_scheduler_prompt__` is true
   (for the Rust implementation, `doeff_vm.doeff_vm.SchedulerPrompt`). It can be reinstalled as it is
   with `handler(h)(program)`. doeff-agents' `mcp_server_loop` recognizes the scheduler by this mark.
4. **Handlers that cooperate with the scheduler**: a handler such as the virtual clock may perform
   `CreatePromise`, `Spawn(..., priority=PRIORITY_IDLE, daemon=True)` and `Wait` from inside the
   handler. Priorities, daemons and the shield for external waits (#505) behave as in the Python
   implementation.

## Key points of the shared semantics

Cancel in particular maps line by line to the Python implementation (`on_cancel` in `scheduler.rs`).

- A task that is unwinding counts as alive (`cancelling`).
- Waiters wake only after the unwinding finishes.
- One Cancel throws the exception in once. A task that swallows it keeps running until the next Cancel.
- If cleanup raises a different exception, it is reported as `failed`, not hidden.
- A task that has not started yet is cancelled without running its body.
- An external Promise that only cancelled tasks were waiting for is cancelled through its `on_cancel`
  (a failure there reaches the side that issued Cancel as `ExternalPromiseCancelCallbackError`).

Discard (drop without unwinding, the same shape as SIGKILL) also maps line by line to the Python
implementation (`on_discard` in `scheduler.rs`). In both implementations the continuation of a
discarded task is simply dropped, and effects inside its `finally` do not run.

Task exceptions that nobody retrieves are also handled the same way in both implementations
(`tests/test_scheduler_unretrieved_task_exception.py` checks them under both).

- A task passed to any of Wait, Gather, Race, Cancel or Discard counts as retrieved, whether before or
  after it finishes.
- When a task that was never retrieved ends with an exception (other than `TaskCancelledError`), a
  description is written at that moment. The only function that writes it is the Python
  `_describe_unretrieved_exception`; the program name comes from the function recorded at Spawn (or, if
  it was not a `@do` call, from the program's type).
- At the end of the run these descriptions are issued as one `RuntimeWarning`. Descriptions of tasks
  whose rows were removed by the #502 sweep are kept too; up to `UNRETRIEVED_EXCEPTIONS_NAMED` tasks
  are named, and the rest are only counted.

### The step limit (`arm_step_budget`)

After `arm_step_budget(N)`, a run that tries to wake a task beyond N steps fails while it runs, in both
implementations, with the same `StepBudgetExceeded` (a `BaseException`) and the same message
"step budget exceeded: … doeff-vm steps since the budget was armed, limit N (while waking …)"
(`tests/test_scheduler_step_budget.py` checks it under both).

- The only function that checks is the Python `_check_step_budget`. The Rust implementation reads a
  flag (`set_scheduler_step_budget_armed`, switched together with the limit by `arm_step_budget` and
  `disarm_step_budget`) each time it takes an entry from the ready queue, and calls that function only
  while the flag is set. So the counting, and the rule that an exhausted limit raises the same error on
  later wakes, are the same too.
- A run without a limit (production) does not read step counts. All the Rust implementation does per
  wake is read one flag.
- Setting a limit needs a doeff_vm extension that has the flag (with an old extension,
  `arm_step_budget` raises an `ImportError` that says to rebuild it).

Behavior inherited from the Python implementation (unchanged): when the root body finishes, the
continuations of tasks left behind (daemon listeners and the like) are not released after the run, and
their `finally` does not run. The Python implementation does the same (the continuation objects form a
cycle the garbage collector cannot see).

## Measurements

Tool: `benchmarks/scheduler_bench.py` (`--impl python|rust`). Microseconds per operation, with the same
release build as shipped (LTO on, no VM self-checks), on an Apple silicon Mac, free-threaded
Python 3.14, best of 3 alternating runs each (2026-09-23).

| Flow | One operation | Python | Rust | Speedup |
|---|---|---:|---:|---:|
| spawn_wait | Spawn + Wait | 33.9 | 4.2 | 8.0 |
| gather | one Gather child (including its Spawn) | 32.0 | 4.1 | 7.8 |
| race | one Race (including 2 Spawns) | 64.3 | 8.1 | 7.9 |
| semaphore_uncontended | Acquire + Release | 13.1 | 1.1 | 12.0 |
| semaphore_contended | Acquire + Spawn + Wait + Release (4 tasks) | 46.6 | 5.5 | 8.5 |
| promise | CreatePromise + Spawn(Complete) + Wait | 47.1 | 6.3 | 7.5 |
| cancel | Spawn + Cancel + finally + Wait | 106.2 | 16.5 | 6.4 |
| pass_through | one effect that passes through the scheduler | 4.2 | 1.1 | 3.7 |

### Where the time goes

Native samples (macOS `sample`) and Python samples (`sys._current_frames`) were taken from the same
process and split by leaf function (with a build that has LTO off for profiling, and a profiling script
kept outside this repository). The shares are for spawn_wait.

| Part | Python | Rust |
|---|---:|---:|
| Round trips across the Rust–Python boundary | 26–31% | 55–60% |
| Scheduler work in Python | 22–27% | 6% (creating handle objects) |
| doeff's Python-side pieces (the `@do` wrapper, `get_inner_boundaries` and so on) | 20–24% | 3–4% |
| Scheduler work in Rust | — | 7–9% |
| The VM itself | 17–20% | 12–16% |
| The user's program | 3–5% | 2–4% |

- In the Python implementation, a large part of the boundary round trips was spent, on every
  `yield Resume(k, ...)` in the handler, recounting the current line number of the large handler
  function to decide whether the Resume was in tail position (30% of the time for an uncontended
  Semaphore). The Rust implementation does not do this.
- Most of the time left in the Rust implementation is the boundary between the VM and Python that
  drives the user's generators (`generator.send`, getting the Python thread state, converting objects),
  outside the scheduler. The scheduler itself (Rust) takes 0.4–0.8 µs per round trip.

### Where the gain is small, and why

- **pass_through (3.7×)**: most of this flow's time is spent not in the scheduler but in the outer
  Python handler and the VM boundary. The scheduler's share is now only "decline the effect by its
  type", and has nearly vanished.
- **cancel (6.4×)**: cancelling throws an exception into the task, runs `finally`, and enriches the
  failed task's traceback with a Python helper. The enrichment and the exception creation stay in
  Python to keep the same meaning as the Python implementation.
- **Creating handles**: `Task(...)` and `Promise(...)` are Python classes that downstream code
  subclasses, so creating them stays a Python call (0.1–0.3 µs each).

### Measurements after changing the default

Tool: `benchmarks/compare_libraries.py --runs 30 --library python --library doeff`. The case that calls
`scheduled(body)` without naming an implementation (`default = <implementation> scheduler`) was measured
on the same tree before and after the change. Median CPU µs per task (p90 in parentheses). Linux,
36 logical CPUs (a busy machine, load average 96–110), free-threaded Python 3.14.3, 2026-10-17.

| Flow | Before (default = Python) | After (default = Rust) | Speedup | asyncio |
|---|---:|---:|---:|---:|
| spawn_gather (spawn 1,000 tasks that do nothing, then gather them) | 153.5 (165.5) | 19.4 (27.4) | 7.9 | 11.8–11.9 |
| fan_out_wait (1,000 tasks wait on one promise and wake at once) | 199.5 (223.7) | 32.6 (59.4) | 6.1 | 16.7–17.4 |

- The default went from 13 and 12 times the cost of asyncio to 1.6 and 1.9 times.
- In the same runs, the medians of the cases that name an implementation were 149–152 and 193–196 for
  Python, and 17.5–19.2 and 30.2–31.9 for Rust. The default cases are in the same range as the named
  ones, so choosing the default costs nothing measurable.
- On a quiet machine (load average 27–30), the Rust implementation took 12.5 for spawn_gather and 19.9
  for fan_out_wait.
