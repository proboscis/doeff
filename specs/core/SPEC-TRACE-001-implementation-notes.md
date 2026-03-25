# SPEC-TRACE-001: Implementation Notes

These notes document the traceback architecture in this repository.

---

## Fiber chain walk model

All traceback data is obtained by walking the live fiber/segment chain on-demand.
No persistent TraceState, no frame snapshots, no dispatch recording.

The fiber chain IS the state. `GetExecutionContext` (DoCtrl tag 25) walks
`current_segment` upward via parent pointers and collects structured data.

---

## Data sources

### Program frames

Each `Frame::Program { stream, .. }` has an `IRStream` with `source_location()`:
- `func_name` from `gi_code.co_qualname`
- `source_file` from `gi_code.co_filename`
- `source_line` from `gi_frame.f_lineno` (live yield site, not decorator line)

### Handler boundaries

Each fiber with `handler: Some(Handler)` where `handler.prompt` is set:
- Handler callable from `prompt.handler` (CallableRef)
- Handler name from `Callable::name()` (reads `__qualname__` via Python bridge)

### Handler chain at each point

`VM::handlers_in_caller_chain(seg_id)` collects all prompt handlers walking
up from a given segment. Returns handler name + segment ID for each.

### Source line content

Python-level rendering reads source lines via `linecache.getline()` to show
`yield Put("processed", 1)` etc.

---

## Collection points

### On error (scheduler)

Scheduler's `wrap_task` catches exceptions and enriches with `__doeff_traceback__`
extracted from Python's `__traceback__`.

### On error (run fallback)

`doeff.run.run()` catches exceptions and enriches as fallback for non-scheduled programs.

### On demand

User code can yield `GetExecutionContext()` to get the active chain.

---

## Spawn boundaries

Scheduler stores spawn metadata per task. On error propagation through Wait/Gather,
spawn boundary info is added to `__doeff_traceback__`.

---

## Invariants

- No persistent TraceState or frame_stack on VM
- No dispatch recording or event logging
- Traceback assembly is on-demand from live fiber chain
- Python `format_default()` is render-only
