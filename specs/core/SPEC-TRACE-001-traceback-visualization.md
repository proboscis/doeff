# SPEC-TRACE-001: Traceback Visualization

## Overview

This spec defines the **default error traceback format** for doeff — what users see when a program fails. The current implementation dumps the full chronological event log, which is noisy and hard to read. This spec replaces it with a **call-stack-at-crash-time** view that shows only what matters.

---

## Motivation

### Current Problems

1. **Line numbers wrong** — shows `@do` decorator line (`co_firstlineno`), not the actual `yield` site
2. **Starts from helper handler** — trace begins with `sync_await_handler`, not the user's program
3. **No handler stack context** — can't see which handlers were active at each yield
4. **Full history, not crash state** — dumps every effect dispatch since `run()` started, even successful ones from earlier iterations
5. **Python traceback noise** — `_effect_wrap` / `generator_wrapper` frames are VM plumbing, meaningless to users

### Design Goals

- Show the **active call chain** at crash time (like Python's traceback, but through `yield` boundaries)
- Show the **handler stack** with per-handler status markers at each effect yield
- Use **human-readable effect repr** (not `<builtins.PyPut object at 0x...>`)
- Show every handler and effect exactly as captured at dispatch/crash time
- No Python traceback section (the doeff trace IS the trace)

### Architectural Invariant

Neither Rust nor Python may filter, hide, omit, or abbreviate handlers/effects in traceback output.
Every handler present in the handler chain at dispatch time must be rendered in order with its
actual status marker. Every yielded effect represented in the active chain must be rendered.

---

## Format Specification

### Structure

A doeff traceback consists of an ordered list of **frames**, from outermost caller to the crash site. Each frame is one of:

- **Program frame** — a `@do` function in the active call chain
- **Effect frame** — a `yield <Effect>` that was dispatched to handlers

Program frames that yield sub-programs (`yield some_program()`) get a single line. Effect frames get a 3-line block showing the effect, handler stack reaction, and result.

### Rendering

```
doeff Traceback (most recent call last):

  <function_name>()  <file>:<line>
    yield <sub_program>()

  <function_name>()  <file>:<line>
    yield <EffectRepr>
    [handler_a↗ > handler_b↗ > handler_c✓ > handler_d· > handler_e·]
    → resumed with <value_repr>

  <function_name>()  <file>:<line>
    raise <ExceptionType>("<message>")

<ExceptionType>: <message>
```

### Handler Status Markers

Each handler in the stack is annotated with what it did for that effect:

| Marker | Meaning | Description |
|--------|---------|-------------|
| `✓` | Resumed | `yield Resume(k, value)` — handler resumed the continuation |
| `↗` | Delegated | `yield Delegate()` — handler passed the effect to the next handler |
| `✗` | Threw | `raise Exception` — handler raised an exception |
| `⇢` | Transferred | `yield Transfer(other_k, value)` — handler sent value to a different continuation (tail-call, severs caller chain) |
| `⚡` | Active | handler yielded its own effect (suspended mid-execution) |
| `·` | Pending | handler never saw this effect (downstream of the handler that resolved it) |

> **Note**: `format_default()` has no separate `⏎` marker. A handler `return value` that resolves
> an effect is rendered as `✓` with `→ resumed with <value>`. Historical return events that are
> no longer on the active call chain are omitted from `format_default()` and remain visible only
> in the full chronological event log (`format_chained()`).

### Handler Stack Display Rules

- Show the full handler stack per effect frame, with markers
- Handlers are ordered left-to-right by dispatch order (first handler invoked to last)
- For `default_handlers()`, the default dispatch order is:
  `sync_await_handler > LazyAskHandler > SchedulerHandler > ResultSafeHandler > WriterHandler > ReaderHandler > StateHandler`
- For `default_async_handlers()`, replace `sync_await_handler` with `async_await_handler`
- No filtering by hardcoded handler names, handler kinds, actions, or statuses
- No abbreviation protocol exists; each stack line renders concrete handler names (never `[same]` or `...`)
- On `WithHandler` boundary crossings, show the full updated stack

### Frame Selection Rules

Only frames in the **active call chain at crash time** are shown:

1. Start from the generator that raised the exception (or the handler that threw)
2. Walk up through parent `yield` sites — each parent generator's current `f_lineno`
3. Stop at the top-level program passed to `run()`

This means:
- Successful sub-program calls from earlier iterations are NOT shown
- Only the current yield per generator is shown
- The trace reads like a regular call stack, not an execution log

**Exception — Transfer**: When a Transfer severs the caller chain (`caller: None`), the pre-transfer call chain is still shown. Pre-transfer frames are reconstructed from the **capture_log** (chronological event log), not from the live segment walk. The trace shows: pre-transfer chain → transfer (inline `⇢`) → post-transfer chain → crash.

**Exception — Spawn chain**: When a spawned task crashes, the trace includes:
1. The **spawn chain** from root to the waiting site (Gather/Wait/Race)
2. A `── in task N (spawned at ...) ──` separator
3. The **task's own trace** from entry to crash

For nested spawns, each level gets its own separator, reading like a nested call stack through task boundaries.

### Scheduler and Transfer

The cooperative scheduler SHOULD use `Transfer` (not `Resume`) for task switches. `Resume` creates segments with `caller: self.current_segment`, which chains segments linearly — over many task switches, this causes unbounded segment growth. `Transfer` creates segments with `caller: None`, preventing chain accumulation.

Since the scheduler uses Transfer for every task switch, Transfer is a **common** operation in concurrent programs. Therefore:
- Transfer is shown **inline** as a handler action (`⇢`), not as a visual separator
- Scheduler transfers between tasks are visible only when relevant to the crash (the trace only follows the failing task's path, not every task switch)
- The `── in task N ──` separator marks **spawn boundaries**, not transfer boundaries

### Line Numbers

Each frame MUST show the line of the `yield` statement (or `raise`) that is currently active in that generator, obtained from the generator's live `f_lineno`. NOT the function definition line or `@do` decorator line.

### Effect Repr

Effects MUST be rendered with their Python `repr()`, showing type and arguments:

- `Put("counter", 1)` not `<builtins.PyPut object at 0x...>`
- `Ask("config")` not `<builtins.PyAsk object at 0x...>`
- `Get("key")` not `<builtins.PyGet object at 0x...>`
- `Tell("message")` not raw object

### Value Repr in Resume

The resumed value shown after `→ resumed with` uses `repr()` truncated to 80 chars.

---

## Examples

### Example 1: Nested program failure

```python
@do
def fetch_config(service):
    base_url = yield Ask("base_url")
    timeout = yield Ask("timeout")
    return {"url": f"{base_url}/{service}", "timeout": timeout}

@do
def process_item(item_id):
    config = yield fetch_config("items")
    yield Tell(f"Processing {item_id}")
    count = yield Get("processed")
    yield Put("processed", count + 1)
    raise RuntimeError(f"Connection refused: {config['url']}/item/{item_id}")

@do
def batch():
    yield Put("processed", 0)
    for i in range(5):
        yield process_item(i)

result = run(batch(), handlers=default_handlers(),
             env={"base_url": "https://api.example.com", "timeout": 30})
```

Output (item 2 fails):

```
doeff Traceback (most recent call last):

  batch()  app.py:18
    yield process_item(2)

  process_item()  app.py:10
    yield fetch_config("items")

  fetch_config()  app.py:5
    yield Ask("timeout")
    [sync_await_handler↗ > LazyAskHandler✓ > SchedulerHandler· > ResultSafeHandler· > WriterHandler· > ReaderHandler· > StateHandler·]
    → resumed with 30

  process_item()  app.py:14
    raise RuntimeError("Connection refused: https://api.example.com/items/item/2")

RuntimeError: Connection refused: https://api.example.com/items/item/2
```

Notes:
- Items 0 and 1 succeeded — not in trace
- `fetch_config` shows only its last yield before returning (the one active when control returned to `process_item`)
- `sync_await_handler` is rendered with its actual status as part of the full handler stack
- Line numbers are yield sites, not decorator lines

### Example 2: Custom handler with WithHandler

```python
def auth_handler(effect, k):
    if isinstance(effect, AskEffect) and effect.key == "token":
        return (yield Resume(k, "Bearer sk-1234"))
    yield Delegate()

def rate_limiter(effect, k):
    if isinstance(effect, AskEffect) and effect.key == "rate_limit":
        return (yield Resume(k, 100))
    yield Delegate()

@do
def call_api():
    token = yield Ask("token")
    limit = yield Ask("rate_limit")
    raise ConnectionError("timeout")

prog = WithHandler(auth_handler,
           WithHandler(rate_limiter, call_api()))
result = run(prog, handlers=default_handlers())
```

Output:

```
doeff Traceback (most recent call last):

  call_api()  app.py:16
    yield Ask("rate_limit")
    [rate_limiter✓ > auth_handler· > sync_await_handler· > LazyAskHandler· > SchedulerHandler· > ResultSafeHandler· > WriterHandler· > ReaderHandler· > StateHandler·]
    → resumed with 100

  call_api()  app.py:17
    raise ConnectionError("timeout")

ConnectionError: timeout
```

Notes:
- Handler stack shows `rate_limiter` first in dispatch order (added by inner `WithHandler`)
- `auth_handler` is `·` because `rate_limiter` handled `Ask("rate_limit")` before it
- Previous `Ask("token")` not shown — only the active yield matters

### Example 3: Handler throws

```python
def strict_handler(effect, k):
    if isinstance(effect, PutEffect) and not isinstance(effect.value, int):
        raise TypeError(f"expected int, got {type(effect.value).__name__}")
    yield Delegate()

@do
def main():
    config = yield Ask("config")
    yield Put("result", config)

prog = WithHandler(strict_handler, main())
result = run(prog, handlers=default_handlers(), env={"config": "not-an-int"})
```

Output:

```
doeff Traceback (most recent call last):

  main()  app.py:10
    yield Put("result", "not-an-int")
    [strict_handler✗ > sync_await_handler· > LazyAskHandler· > SchedulerHandler· > ResultSafeHandler· > WriterHandler· > ReaderHandler· > StateHandler·]
    ✗ strict_handler raised TypeError("expected int, got str")

TypeError: expected int, got str
```

Notes:
- `✗` marker on `strict_handler` — it threw instead of resuming
- Result line shows `✗ handler raised` instead of `→ resumed with`

### Example 4: Missing env key

```python
@do
def needs_db():
    db_url = yield Ask("database_url")
    return f"Connected to {db_url}"

result = run(needs_db(), handlers=default_handlers())
```

Output:

```
doeff Traceback (most recent call last):

  needs_db()  app.py:3
    yield Ask("database_url")
    [sync_await_handler↗ > LazyAskHandler✗ > SchedulerHandler· > ResultSafeHandler· > WriterHandler· > ReaderHandler· > StateHandler·]
    ✗ LazyAskHandler raised MissingEnvKeyError("Environment key not found: 'database_url'")

MissingEnvKeyError: Environment key not found: 'database_url'
Hint: Provide this key via `env={'database_url': value}` or wrap with `Local({'database_url': value}, ...)`
```

### Example 5: Handler stack changes across frames

```python
@do
def outer():
    yield Put("x", 1)
    yield WithHandler(my_handler, inner())

@do
def inner():
    yield Put("y", 2)
    raise ValueError("inner error")
```

Output:

```
doeff Traceback (most recent call last):

  outer()  app.py:3
    yield Put("x", 1)
    [sync_await_handler↗ > LazyAskHandler↗ > SchedulerHandler↗ > ResultSafeHandler↗ > WriterHandler↗ > ReaderHandler↗ > StateHandler✓]
    → resumed with None

  outer()  app.py:4
    yield WithHandler(my_handler, inner())

  inner()  app.py:8
    yield Put("y", 2)
    [my_handler↗ > sync_await_handler↗ > LazyAskHandler↗ > SchedulerHandler↗ > ResultSafeHandler↗ > WriterHandler↗ > ReaderHandler↗ > StateHandler✓]
    → resumed with None

  inner()  app.py:9
    raise ValueError("inner error")

ValueError: inner error
```

Notes:
- `outer` shows stack without `my_handler`
- `inner` shows stack with `my_handler` prepended (first in dispatch order, added by `WithHandler`)

### Example 6: Handler returns value (rendered as resumed `✓`)

In the default traceback format, a plain handler `return value` is rendered as a successful handler
resolution (`✓`) with `→ resumed with <value>`. There is no separate `⏎` rendering in
`format_default()`.

```python
def short_circuit_handler(effect, k):
    if isinstance(effect, AskEffect) and effect.key == "mode":
        return "fallback"   # abandon continuation, WithHandler returns "fallback"
    yield Delegate()

@do
def inner():
    mode = yield Ask("mode")
    yield Put("result", mode)
    return mode

@do
def outer():
    result = yield WithHandler(short_circuit_handler, inner())
    raise ValueError(f"Unexpected: {result}")
```

Output:

```
doeff Traceback (most recent call last):

  outer()  app.py:12
    yield WithHandler(short_circuit_handler, inner())

  inner()  app.py:7
    yield Ask("mode")
    [short_circuit_handler✓ > sync_await_handler· > LazyAskHandler· > SchedulerHandler· > ResultSafeHandler· > WriterHandler· > ReaderHandler· > StateHandler·]
    → resumed with "fallback"

  outer()  app.py:13
    raise ValueError("Unexpected: fallback")

ValueError: Unexpected: fallback
```

### Example 7: Handler transfers to another continuation (⇢)

Transfer (`yield Transfer(other_k, value)`) is a tail-call: the handler abandons the current caller chain and sends a value to a different continuation. In the VM, this creates a new segment with `caller: None`, severing the call chain.

Transfer is shown inline as a handler action (`⇢`), not as a visual separator. This is because the cooperative scheduler uses Transfer for all task switches (see "Scheduler and Transfer" below), so separators would appear everywhere in concurrent programs.

```python
def redirect_handler(effect, k):
    if isinstance(effect, AskEffect) and effect.key == "redirect":
        saved_k = yield Get("saved_continuation")
        yield Transfer(saved_k, "redirected_value")
    yield Delegate()

@do
def program_a():
    value = yield Ask("data")       # will receive "redirected_value" via Transfer
    yield Put("result", value)
    raise ValueError(f"Unexpected: {value}")

@do
def trigger():
    yield Tell("redirecting")
    yield Ask("redirect")           # redirect_handler transfers away, trigger() abandoned
```

Output (Transfer lands in `program_a`, which then crashes):

```
doeff Traceback (most recent call last):

  trigger()  app.py:12
    yield Ask("redirect")
    [redirect_handler⇢ > sync_await_handler· > LazyAskHandler· > SchedulerHandler· > ResultSafeHandler· > WriterHandler· > ReaderHandler· > StateHandler·]
    ⇢ redirect_handler transferred to program_a

  program_a()  app.py:7
    yield Put("result", "redirected_value")
    [sync_await_handler↗ > LazyAskHandler↗ > SchedulerHandler↗ > ResultSafeHandler↗ > WriterHandler↗ > ReaderHandler↗ > StateHandler✓]
    → resumed with None

  program_a()  app.py:8
    raise ValueError("Unexpected: redirected_value")

ValueError: Unexpected: redirected_value
```

Notes:
- `⇢` on `redirect_handler` — it transferred instead of resuming
- The pre-transfer chain (`trigger()`) is shown because the user needs to see how control arrived at `program_a`
- The trace reads top-to-bottom: original chain → transfer → post-transfer execution → crash
- No `── transfer ──` separator — Transfer is a regular handler action, shown inline
- Pre-transfer frames come from **capture_log** (chronological), not from live segment walk (which stops at `caller: None`)

### Example 8: Spawn chain — task crash during Gather

When a spawned task crashes, the trace shows the **spawn chain** — how the crashing task was created and where the parent was waiting — followed by the task's own execution trace.

```python
@do
def fetch_data(url):
    response = yield Await(http_get(url))
    if response.status != 200:
        raise ConnectionError(f"Failed: {url} → {response.status}")
    return response.body

@do
def process_batch(items):
    tasks = []
    for item in items:
        t = yield Spawn(fetch_data(item.url))
        tasks.append(t)
    results = yield Gather(*tasks)
    return results

@do
def main():
    items = yield Ask("items")
    yield process_batch(items)
```

Output (task 3 fails during Gather):

```
doeff Traceback (most recent call last):

  main()  app.py:18
    yield process_batch(items)

  process_batch()  app.py:13
    yield Gather(*tasks)
    [sync_await_handler↗ > LazyAskHandler· > SchedulerHandler✗ > ResultSafeHandler· > WriterHandler· > ReaderHandler· > StateHandler·]
    ✗ SchedulerHandler raised ConnectionError("Failed: https://api.example.com/item/3 → 500")

  ── in task 3 (spawned at process_batch() app.py:11) ──

  fetch_data("https://api.example.com/item/3")  app.py:3
    yield Await(http_get(url))
    [async_await_handler✓ > LazyAskHandler· > SchedulerHandler· > ResultSafeHandler· > WriterHandler· > ReaderHandler· > StateHandler·]
    → resumed with Response(status=500)

  fetch_data()  app.py:5
    raise ConnectionError("Failed: https://api.example.com/item/3 → 500")

ConnectionError: Failed: https://api.example.com/item/3 → 500
```

Notes:
- Top section: the **spawn chain** — `main` → `process_batch` → `Gather` (where the error was received)
- `── in task 3 (spawned at ...) ──` separator shows the task boundary with spawn-site attribution
- Bottom section: the **task's own trace** — `fetch_data`'s execution to the crash
- Tasks 0, 1, 2 succeeded — not shown (only the crashing task matters)
- `SchedulerHandler✗` shows the Gather site where the task failure is re-raised to the parent

### Example 9: Nested spawn chain (A → B → C, C crashes)

When tasks spawn sub-tasks, the trace follows the full spawn chain from root to the crashing leaf task.

```python
@do
def leaf_worker(item_id):
    data = yield Ask("data")
    raise RuntimeError(f"corrupt data for item {item_id}")

@do
def batch_worker(batch_id):
    tasks = []
    for i in range(3):
        t = yield Spawn(leaf_worker(f"{batch_id}-{i}"))
        tasks.append(t)
    return (yield Gather(*tasks))

@do
def orchestrator():
    batches = []
    for b in range(2):
        t = yield Spawn(batch_worker(b))
        batches.append(t)
    return (yield Gather(*batches))
```

Output (batch 1, item 2 crashes):

```
doeff Traceback (most recent call last):

  orchestrator()  app.py:16
    yield Gather(*batches)
    [sync_await_handler↗ > LazyAskHandler· > SchedulerHandler✗ > ResultSafeHandler· > WriterHandler· > ReaderHandler· > StateHandler·]
    ✗ SchedulerHandler raised RuntimeError("corrupt data for item 1-2")

  ── in task 2 (spawned at orchestrator() app.py:14) ──

  batch_worker(1)  app.py:10
    yield Gather(*tasks)
    [sync_await_handler↗ > LazyAskHandler· > SchedulerHandler✗ > ResultSafeHandler· > WriterHandler· > ReaderHandler· > StateHandler·]
    ✗ SchedulerHandler raised RuntimeError("corrupt data for item 1-2")

  ── in task 5 (spawned at batch_worker() app.py:8) ──

  leaf_worker("1-2")  app.py:4
    raise RuntimeError("corrupt data for item 1-2")

RuntimeError: corrupt data for item 1-2
```

Notes:
- Each `── in task N ──` separator follows the spawn chain downward
- The trace reads like a nested call stack through task boundaries
- Each level shows where the parent was waiting (Gather) and which child failed
- Only the failing branch is shown — other batches and items are omitted

### Example 10: Handler yields its own effects (⚡ — active/effectful handler)

TODO: Define visualization for when a handler itself performs effects (e.g., `yield Get("cache_key")` inside a handler body). This creates a nested dispatch — the handler's effect goes through the handlers above it. How deep do we show the nesting?

```python
def caching_handler(effect, k):
    if isinstance(effect, GetEffect):
        cached = yield Get(f"cache:{effect.key}")   # handler performs its own effect
        if cached is not None:
            yield Resume(k, cached)
        else:
            yield Delegate()
    yield Delegate()
```

### Example 11: Handler catches and re-raises

TODO: Define visualization for when a handler catches an exception from Resume and re-raises or wraps it.

```python
def error_wrapper(effect, k):
    try:
        result = yield Resume(k, some_value)
        return result
    except Exception as e:
        raise ApplicationError(f"Failed in handler: {e}") from e
```

---

## Data Requirements from VM

To render this format, the following data is needed per frame:

### Active call chain (from live VM state)

For each generator on the active segment chain:
- `function_name` — from generator `__qualname__` or `__name__`
- `source_file` — from generator `co_filename`
- `source_line` — from generator **live `f_lineno`** (NOT `co_firstlineno`)
- `current_yield_repr` — repr of what was yielded (effect or sub-program)

### Last effect dispatch per frame (from capture log)

For the most recent effect yielded by each active generator:
- `effect_repr` — human-readable repr of the effect
- `handler_stack` — ordered list of handler names in dispatch order (first invoked to last)
- `handler_status` — per-handler: delegated / resumed / threw / transferred / pending
- `handler_identity` — stable per-dispatch identity (index or ID) for each handler stack entry
- Completion events (`resumed` / `threw` / `transferred` / `returned`) must target handlers by `handler_identity`,
  not by handler name, so duplicate names are rendered correctly
- `resume_value_repr` — value returned to the generator (if resumed)

### Transfer chain (from capture log)

When a Transfer severs the caller chain, pre-transfer frames are not reachable from the live segment walk (`caller: None`). The capture_log retains the full chronological history:
- `CaptureEvent::Transferred` links the dispatch to the transfer target via `dispatch_id`
- Pre-transfer frames are reconstructed by walking the capture_log backwards from the Transfer event
- Multiple transfers create multiple segments in the trace, each linked by the Transfer event

### Spawn chain

To show the full path from root to a crashing spawned task, the VM must track:

- `parent_task: Option<TaskId>` — which task spawned this one (stored at spawn time)
- `spawn_site` — function name, source file, and line where `Spawn(...)` was yielded
- `task_trace: Vec<TraceEntry>` — when a task fails, its assembled trace is captured and attached to the exception (via `__doeff_traceback_data__` or similar mechanism)

When assembling a trace for an error propagated through Gather/Wait/Race:
1. Walk the spawn chain upward from the failing task to the root
2. At each level, show the parent's call chain ending at the Gather/Wait site
3. Show a `── in task N (spawned at ...) ──` separator
4. Show the child task's trace

### Exception info

- `exception_type` — exception class name
- `exception_message` — str(exception)
- `source_file` / `source_line` — where the raise occurred (from live `f_lineno`)

---

## Implementation Notes

### What changes in Rust VM

1. `supplement_with_live_state` already reads live `f_lineno` for active frames — this becomes the primary source of line numbers
2. Handler stack snapshot per dispatch — when `DispatchStarted` is recorded, also record the full handler stack names at that point
3. Effect repr — use Python `repr()` on the effect object, not the default `<builtins.X object at 0x...>`
4. **Scheduler: switch from Resume to Transfer for task switches** — `jump_to_continuation` should emit `DoCtrl::Transfer` (not `DoCtrl::Resume`) for started continuations during task switching. This prevents unbounded segment chain growth during cooperative scheduling. `Resume` chains segments via `caller: self.current_segment`; `Transfer` severs with `caller: None`.
5. **Spawn chain tracking** — add `parent_task: Option<TaskId>` and spawn-site metadata to task state. When `Spawn` is handled, record the current task ID and the spawn call site.
6. **Task error trace capture** — when a spawned task fails, assemble its trace and attach it to the exception before storing in `TaskState::Done { result: Err(...) }`. This ensures the task's trace survives propagation through Gather/Wait/Race.

### What changes in Python projection

1. New `format_default()` method on `DoeffTraceback` replacing `format_chained()` as the stderr output
2. Frame selection: filter to active call chain only, not full chronological log
3. Handler stack rendering with markers
4. **Transfer chain reconstruction** — when a Transfer event is found in capture_log, include pre-transfer frames in the trace (not reachable from live segment walk due to `caller: None`)
5. **Spawn chain rendering** — when an exception carries a task trace (from a spawned task), render the spawn chain with `── in task N (spawned at ...) ──` separators between parent and child trace sections

### What does NOT change

- `format_chained()` — kept as-is for full chronological debug view
- `format_sectioned()` — kept as-is for structured summary
- `format_short()` — kept as-is for one-liner logs
- Capture model (SPEC-CORE-004) — only additive changes (handler stack snapshot per dispatch, spawn-site tracking)
- `__doeff_traceback_data__` attachment mechanism unchanged

---

## Acceptance Criteria

### Core format
1. Default stderr output on error shows the new format (via `format_default()`)
2. Only active call chain frames shown — no historical dispatches
3. Line numbers match actual yield sites, not decorator lines
4. Handler stack with status markers shown per effect yield
5. No handler is filtered/hidden; all handlers in the dispatch chain are rendered
6. No abbreviation protocol; stack rows always render concrete handler names and markers
7. Effect repr is human-readable (`Put("key", value)` not `<builtins.PyPut ...>`)
8. Handler throw shown with `✗` marker and exception info
9. `format_chained()`, `format_sectioned()`, `format_short()` continue to work unchanged
10. Duplicate handler names in one stack still produce correct per-entry markers (status mapped by handler identity, not name)

### Transfer
11. Transfer shown inline with `⇢` marker on the handler that transferred
12. Pre-transfer call chain included in trace (reconstructed from capture_log)
13. No visual separator for Transfer — it's a regular handler action

### Spawn chain
14. Spawned task crash shows spawn chain from root to waiting site (Gather/Wait/Race)
15. `── in task N (spawned at <function> <file>:<line>) ──` separator between parent and task trace
16. Nested spawn chains show multiple separators (one per spawn level)
17. Only the crashing task's branch is shown — other tasks omitted

### Scheduler
18. Scheduler uses Transfer for task switches (no unbounded segment growth)
19. Scheduler task-switch transfers only visible when relevant to the crash path
