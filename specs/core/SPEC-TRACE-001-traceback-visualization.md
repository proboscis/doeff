# SPEC-TRACE-001: Traceback Visualization

## Overview

This spec defines the **default error traceback format** for doeff — what users see when a program fails. The current implementation dumps the full chronological event log, which is noisy and hard to read. This spec replaces it with a **call-stack-at-crash-time** view that shows only what matters.

---

## Motivation

### Current Problems

1. **Line numbers wrong** — shows `@do` decorator line (`co_firstlineno`), not the actual `yield` site
2. **Starts from handler, not user program** — trace begins with `sync_await_handler`, not the user's `@do` function
3. **No handler stack context** — can't see which handlers were active at each yield
4. **Full history, not crash state** — dumps every effect dispatch since `run()` started, even successful ones from earlier iterations
5. **Python traceback noise** — `_effect_wrap` / `generator_wrapper` frames are VM plumbing, meaningless to users

### Design Goals

- Show the **active call chain** at crash time (like Python's traceback, but through `yield` boundaries)
- Show the **handler stack** with per-handler status markers at each effect yield
- Use **human-readable effect repr** (not `<builtins.PyPut object at 0x...>`)
- **No hardcoded filtering or omission** — every handler in the chain is shown, regardless of name or action
- No Python traceback section (the doeff trace IS the trace)

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

> **Note**: Handler `return` (⏎ — abandon continuation) is not shown in the default format.
> By the time a trace is captured (error or `GetStackTrace`), a handler return has already
> completed — the `WithHandler` delivered its value, the parent continued. The return is
> historical, never in the active call chain. It only appears in the full chronological
> event log (`format_chained()`).

### Handler Stack Display Rules

- Show the full handler stack per effect frame, with markers
- Handlers are ordered left-to-right from innermost (first to see effects) to outermost
- All handlers are shown — no handler is ever hidden, filtered, or abbreviated based on name, kind, or action
- No abbreviation protocol exists — every handler is shown by its actual name, never replaced with `...` or similar shorthand
- When the handler stack is unchanged from the previous effect frame, display `[same]` instead of repeating
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
    [StateHandler↗ > ReaderHandler↗ > WriterHandler· > ResultSafeHandler· > SchedulerHandler· > LazyAskHandler✓ > sync_await_handler·]
    → resumed with 30

  process_item()  app.py:14
    raise RuntimeError("Connection refused: https://api.example.com/items/item/2")

RuntimeError: Connection refused: https://api.example.com/items/item/2
```

Notes:
- Items 0 and 1 succeeded — not in trace
- `fetch_config` shows only its last yield before returning (the one active when control returned to `process_item`)
- All handlers from `default_handlers()` are shown with their actual status
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
    [rate_limiter✓ > auth_handler· > StateHandler· > ReaderHandler· > WriterHandler· > ResultSafeHandler· > SchedulerHandler· > LazyAskHandler· > sync_await_handler·]
    → resumed with 100

  call_api()  app.py:17
    raise ConnectionError("timeout")

ConnectionError: timeout
```

Notes:
- Handler stack shows `rate_limiter` innermost (added by inner `WithHandler`)
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
    [strict_handler✗ > StateHandler· > ReaderHandler· > WriterHandler· > ResultSafeHandler· > SchedulerHandler· > LazyAskHandler· > sync_await_handler·]
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
    [StateHandler↗ > ReaderHandler↗ > WriterHandler· > ResultSafeHandler· > SchedulerHandler· > LazyAskHandler✗ > sync_await_handler·]
    ✗ LazyAskHandler raised MissingEnvKeyError("Environment key not found: 'database_url'")

MissingEnvKeyError: Environment key not found: 'database_url'
Hint: Provide this key via `env={'database_url': value}` or wrap with `Local({'database_url': value}, ...)`
```

### Example 5: Handler stack changes with [same]

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
    [StateHandler✓ > ReaderHandler· > WriterHandler· > ResultSafeHandler· > SchedulerHandler· > LazyAskHandler· > sync_await_handler·]
    → resumed with None

  outer()  app.py:4
    yield WithHandler(my_handler, inner())

  inner()  app.py:8
    yield Put("y", 2)
    [my_handler↗ > StateHandler✓ > ReaderHandler· > WriterHandler· > ResultSafeHandler· > SchedulerHandler· > LazyAskHandler· > sync_await_handler·]
    → resumed with None

  inner()  app.py:9
    raise ValueError("inner error")

ValueError: inner error
```

Notes:
- `outer` shows stack without `my_handler`
- `inner` shows stack with `my_handler` prepended (innermost, added by `WithHandler`)

### Example 6: Handler returns value (⏎ — abandoned continuation)

> **Note**: In doeff's one-shot continuation model, handler `return` (abandon continuation)
> is practically redundant. Every use case is covered by `raise` (abort with error) or
> `yield Resume(k, fallback)` (provide value, let program continue). The only pattern that
> truly requires discarding continuations — backtracking/nondeterminism — needs multi-shot
> continuations, which doeff does not support. We keep this for algebraic effects completeness
> but don't expect it to be used in practice.

When a handler does `return value` instead of `yield Resume(k, value)`, the continuation is abandoned — the `WithHandler` expression evaluates to the returned value. The program that yielded the effect never resumes.

```python
def short_circuit_handler(effect, k):
    if isinstance(effect, AskEffect) and effect.key == "mode":
        return "fallback"   # abandon continuation, WithHandler returns "fallback"
    yield Delegate()

@do
def inner():
    mode = yield Ask("mode")       # handler returns here, inner() never resumes
    yield Put("result", mode)      # never reached
    return mode                    # never reached

@do
def outer():
    result = yield WithHandler(short_circuit_handler, inner())
    # result is "fallback" (handler's return value, NOT inner()'s return)
    raise ValueError(f"Unexpected: {result}")
```

Since the abandoned `inner()` is no longer in the active call chain at crash time, the trace only shows `outer()`:

```
doeff Traceback (most recent call last):

  outer()  app.py:13
    raise ValueError("Unexpected: fallback")

ValueError: Unexpected: fallback
```

If the `return` itself is the terminal event (the `WithHandler` result causes the crash), and we want to show WHY the value was `"fallback"`, we may need to include the abandoned frame as context:

```
doeff Traceback (most recent call last):

  outer()  app.py:11
    yield WithHandler(short_circuit_handler, inner())

  inner()  app.py:7                                          (abandoned)
    yield Ask("mode")
    [short_circuit_handler⏎ > StateHandler· > ReaderHandler· > WriterHandler· > ResultSafeHandler· > SchedulerHandler· > LazyAskHandler· > sync_await_handler·]
    ⏎ short_circuit_handler returned "fallback" (continuation abandoned)

  outer()  app.py:13
    raise ValueError("Unexpected: fallback")

ValueError: Unexpected: fallback
```

Open question: when to include the abandoned frame vs omit it. For the default implementation, we omit it (it's not in the active call chain). A verbose/debug mode could include it.

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
    [redirect_handler⇢ > StateHandler· > ReaderHandler· > WriterHandler· > ResultSafeHandler· > SchedulerHandler· > LazyAskHandler· > sync_await_handler·]
    ⇢ redirect_handler transferred to program_a

  program_a()  app.py:7
    yield Put("result", "redirected_value")
    [StateHandler✓ > ReaderHandler· > WriterHandler· > ResultSafeHandler· > SchedulerHandler· > LazyAskHandler· > sync_await_handler·]
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
    [StateHandler· > ReaderHandler· > WriterHandler· > ResultSafeHandler· > SchedulerHandler⇢ > LazyAskHandler· > async_await_handler·]
    ⇢ task 3 failed during Gather

  ── in task 3 (spawned at process_batch() app.py:11) ──

  fetch_data("https://api.example.com/item/3")  app.py:3
    yield Await(http_get(url))
    [StateHandler· > ReaderHandler· > WriterHandler· > ResultSafeHandler· > SchedulerHandler· > LazyAskHandler· > async_await_handler✓]
    → resumed with Response(status=500)

  fetch_data()  app.py:5
    raise ConnectionError("Failed: .../item/3 → 500")

ConnectionError: Failed: https://api.example.com/item/3 → 500
```

Notes:
- Top section: the **spawn chain** — `main` → `process_batch` → `Gather` (where the error was received)
- `── in task 3 (spawned at ...) ──` separator shows the task boundary with spawn-site attribution
- Bottom section: the **task's own trace** — `fetch_data`'s execution to the crash
- Tasks 0, 1, 2 succeeded — not shown (only the crashing task matters)
- The scheduler's `⇢` shows it transferred to the failing task (scheduler uses Transfer for task switches)

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
    [StateHandler· > ReaderHandler· > WriterHandler· > ResultSafeHandler· > SchedulerHandler⇢ > LazyAskHandler· > sync_await_handler·]
    ⇢ task 2 failed during Gather

  ── in task 2 (spawned at orchestrator() app.py:14) ──

  batch_worker(1)  app.py:10
    yield Gather(*tasks)
    [StateHandler· > ReaderHandler· > WriterHandler· > ResultSafeHandler· > SchedulerHandler⇢ > LazyAskHandler· > sync_await_handler·]
    ⇢ task 5 failed during Gather

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

### Effect creation site (from effect object)

Every effect carries its Python-side creation location via `EffectBase.created_at: EffectCreationContext`. This is set by `create_effect_with_trace()` at construction time and captures:
- `function` — the function that constructed the effect (e.g., the user's `@do` function)
- `filename` — source file path
- `line` — line number of the constructor call

The VM reads `created_at` from every dispatched Python effect object at dispatch time. This is a **generic protocol** — it applies uniformly to all effects, not just specific ones like Spawn. The creation site is stored in `CaptureEvent::DispatchStarted` alongside the effect repr and handler chain snapshot.

Consumers of the creation site:
- **Spawn chain**: the scheduler reads the creation site from the Spawn effect's dispatch to populate `TaskMetadata.spawn_site` — no frame walking or positional heuristics needed
- **Traceback rendering**: effect frames can show where the effect was constructed
- **Capture log**: richer trace data for debugging

### Last effect dispatch per frame (from capture log)

For the most recent effect yielded by each active generator:
- `effect_repr` — human-readable repr of the effect
- `effect_creation_site` — where the effect was constructed (from `created_at`)
- `handler_stack` — ordered list of handler names from innermost to outermost
- `handler_status` — per-handler: delegated / resumed / threw / transferred / pending
- `resume_value_repr` — value returned to the generator (if resumed)

### Transfer chain (from capture log)

When a Transfer severs the caller chain, pre-transfer frames are not reachable from the live segment walk (`caller: None`). The capture_log retains the full chronological history:
- `CaptureEvent::Transferred` links the dispatch to the transfer target via `dispatch_id`
- Pre-transfer frames are reconstructed by walking the capture_log backwards from the Transfer event
- Multiple transfers create multiple segments in the trace, each linked by the Transfer event

### Spawn chain

To show the full path from root to a crashing spawned task, the VM must track:

- `parent_task: Option<TaskId>` — which task spawned this one (stored at spawn time)
- `spawn_site` — from the Spawn effect's `created_at` (see "Effect creation site" above). The scheduler reads the creation site from the dispatched SpawnEffect at task creation time and stores it in `TaskMetadata`. No frame walking or positional heuristics.
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

## Design Constraints

### All Effects and Handlers Are User-Space

**There is no concept of "internal" effects or "internal" handlers in doeff.** Every handler is a user-space entity — `sync_await_handler`, `LazyAskHandler`, `ReaderHandler`, `StateHandler` are all handlers the user passes via `default_handlers()` or installs via `WithHandler`. Every effect (`Ask`, `Put`, `Await`, `Spawn`, etc.) is a user-space value yielded by user programs.

**Consequently: neither Rust nor Python may filter, hide, or omit any handler or effect from the traceback.** Every handler present in the handler chain at dispatch time appears in the rendered output. Every effect yielded appears as an effect frame. There are no special cases, no hardcoded name lists, no filtering by action or status, no abbreviation.

If the output is noisy, the fix is to change the handler architecture (e.g., don't install the handler), not to filter it after the fact.

### Effect Creation Site Protocol

Effect location metadata flows through a single, generic protocol — not per-effect-type heuristics:

1. **Python**: `create_effect_with_trace()` captures the user's call site into `EffectBase.created_at` at construction time
2. **Rust VM**: At dispatch time, reads `created_at` from the Python effect object and extracts `(function, filename, line)`
3. **Consumers**: Any VM subsystem that needs to know where an effect was created reads it from the dispatch context

This protocol replaces any frame-walking, position-skipping, or continuation-inspection heuristics. The effect object is the single source of truth for its creation location. If a subsystem needs the creation site (e.g., scheduler for spawn site), it reads it from the dispatched effect — it does not reconstruct it from stack frames.

### No Hardcoded Handler/Effect Name Matching in VM Logic

VM control flow (e.g., where to insert spawn boundaries, which entries to suppress) must NOT match against hardcoded handler or effect name strings. For example, checking `handler_name == "SchedulerHandler"` to decide where to insert a spawn boundary is forbidden — the boundary carries its own structural metadata (task_id, parent_task, spawn_site) and should be positioned based on that, not by matching handler names.

### `@do` Wrapper Inner Generator Access

The `@do` decorator wraps user generators in a `generator_wrapper`. The VM needs access to the user's inner generator to read correct line numbers (`f_lineno`). The wrapper exposes the inner generator as an **attribute on the wrapper generator object** (`__doeff_inner__`), not as a magic local variable in `f_locals`. The VM reads this via `getattr(wrapper_gen, "__doeff_inner__")`.

This is a documented cross-language contract between `@do` (Python) and `generator_current_line` (Rust). The attribute is set on the generator object itself — the VM never inspects `f_locals`.

### Render-Only Python Layer

Python's `format_default()` is a pure renderer. It takes the assembled `active_chain` from Rust and formats it. It does not walk segments, reconstruct chains, filter entries, or make decisions about what to show. What Rust assembles is what Python renders.

### Implementation Notes

Implementation-level notes (what changes in Rust VM, Python projection, etc.) are in [SPEC-TRACE-001-implementation-notes.md](SPEC-TRACE-001-implementation-notes.md).

---

## Acceptance Criteria

### Core format
1. Default stderr output on error shows the new format (via `format_default()`)
2. Only active call chain frames shown — no historical dispatches
3. Line numbers match actual yield sites, not decorator lines
4. Handler stack with status markers shown per effect yield
5. All handlers shown — no hardcoded filtering or omission of any handler by name, kind, or action
6. Handler stack diff (`[same]`) used when stack unchanged between consecutive effect frames
7. Effect repr is human-readable (`Put("key", value)` not `<builtins.PyPut ...>`)
8. Handler throw shown with `✗` marker and exception info
9. `format_chained()`, `format_sectioned()`, `format_short()` continue to work unchanged

### Transfer
10. Transfer shown inline with `⇢` marker on the handler that transferred
11. Pre-transfer call chain included in trace (reconstructed from capture_log)
12. No visual separator for Transfer — it's a regular handler action

### Spawn chain
13. Spawned task crash shows spawn chain from root to waiting site (Gather/Wait/Race)
14. `── in task N (spawned at <function> <file>:<line>) ──` separator between parent and task trace
15. Nested spawn chains show multiple separators (one per spawn level)
16. Only the crashing task's branch is shown — other tasks omitted

### Scheduler
17. Scheduler uses Transfer for task switches (no unbounded segment growth)
18. Scheduler transfers only visible when relevant to the crash path
