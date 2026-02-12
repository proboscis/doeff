# doeff-sim

`doeff-sim` provides a deterministic simulation handler for doeff.

It intercepts:
- time effects from `doeff-time` (`Delay`, `WaitUntil`, `GetTime`, `ScheduleAt`)
- core concurrency effects (`Spawn`, `Wait`, `Race`, `Gather`)
- sim-only effects (`SetTime`, `ForkRun`)

Everything else is delegated with `Delegate()` so it composes with standard doeff handlers.

## Start Time Convention

`simulation_start_time` is the shared environment key convention for simulation start timestamps
(epoch seconds). Pass the same key through your app-level config when wiring handlers.

## Basic Usage

```python
from doeff import WithHandler, run, default_handlers
from doeff_sim.handlers import deterministic_sim_handler

result = run(
    WithHandler(
        deterministic_sim_handler(start_time=1704067200.0),
        my_program(),
    ),
    handlers=default_handlers(),
)
```
