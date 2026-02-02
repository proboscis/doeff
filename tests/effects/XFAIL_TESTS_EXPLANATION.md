# XFailed Tests Explanation

This document explains the tests marked as `xfail` in the effect test suite and the behavioral differences between the old `AsyncRuntime` class and the new `sync_run`/`async_run` function-based API.

## Summary

| Category | Count | Status |
|----------|-------|--------|
| ~~Local override inheritance~~ | ~~6~~ | ✅ FIXED |
| ~~Spawned children env~~ | ~~2~~ | ✅ FIXED |
| Listen + Local (log capture) | 2 | xfail |
| Delay in sync mode | 2 | Expected (use correct preset) |
| CircularAskError | 4 | xfail |
| **Total xfailed** | ~8 | |

---

## ✅ FIXED: Local Override Inheritance

**Status:** Fixed by using `LocalRestoreFrame` to properly merge and restore environments.

The fix ensures:
1. Nested Local inherits outer Local's environment for unmodified keys
2. Spawned children capture the correct (modified) environment at spawn time
3. Environment is properly restored after Local scope exits

```
NOW WORKING:
┌─────────────────────────────────────────────────────────────┐
│  env = {"key1": "orig1", "key2": "orig2"}                   │
│                                                             │
│  ┌─ Local({"key1": "outer1"}) ─────────────────────────┐   │
│  │  merged_env = {"key1": "outer1", "key2": "orig2"}    │   │
│  │                                                       │   │
│  │  Ask("key1") → "outer1"  ✓                           │   │
│  │                                                       │   │
│  │  ┌─ Local({"key2": "inner2"}) ───────────────────┐   │   │
│  │  │  merged_env = {"key1": "outer1", "key2": "inner2"}│   │
│  │  │                                                 │   │   │
│  │  │  Ask("key1") → "outer1"  ✓ (inherits from outer)│   │   │
│  │  │  Ask("key2") → "inner2"  ✓                     │   │   │
│  │  │                                                 │   │   │
│  │  └─────────────────────────────────────────────────┘   │   │
│  └───────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

---

## ✅ FIXED: Spawned Children Environment

**Status:** Fixed by the same `LocalRestoreFrame` fix - `ctx.env` now reflects Local modifications.

```
NOW WORKING:
┌─────────────────────────────────────────────────────────────┐
│  env = {"key": "original"}                                  │
│                                                             │
│  ┌─ Local({"key": "from_local"}) ─────────────────────┐    │
│  │  ctx.env = {"key": "from_local"}  ← properly merged │    │
│  │                                                      │    │
│  │  Spawn(child1) ──┐  children capture ctx.env        │    │
│  │  Spawn(child2) ──┤                                   │    │
│  │  Gather(...)  ◄──┘                                   │    │
│  │                                                      │    │
│  └──────────────────────────────────────────────────────┘    │
│                                                             │
│  child1.Ask("key") → "from_local"  ✓                       │
│  child2.Ask("key") → "from_local"  ✓                       │
└─────────────────────────────────────────────────────────────┘
```

---

## 1. Listen + Local Log Capture

**Files:** `test_effect_combinations.py`

### Issue

When Listen wraps Local, logs from Tell inside the Local scope may not be captured
correctly by the outer Listen.

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│  ┌─ Listen ────────────────────────────────────────────┐   │
│  │                                                       │   │
│  │  ┌─ Local({"key": "val"}) ───────────────────────┐   │   │
│  │  │                                                 │   │   │
│  │  │  Tell("log_message")  ← may not be captured    │   │   │
│  │  │                                                 │   │   │
│  │  └─────────────────────────────────────────────────┘   │   │
│  │                                                       │   │
│  │  Returns: ListenResult(value, log=[???])             │   │
│  │                                                       │   │
│  └───────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

### Tests Affected

- `test_listen_captures_logs_from_local` (test_effect_combinations.py)

---

## 2. Delay Effect in Sync Mode (Expected Behavior)

**Files:** `test_effect_combinations.py`

### This is Expected Behavior - Not a Bug

`python_async_syntax_escape_handler` produces `PythonAsyncSyntaxEscape` which requires
Python's `await` syntax. This handler is **only compatible with `async_run`**.

```
Handler Compatibility:
┌──────────────────────────────────────┬──────────┬───────────┐
│ Handler                              │ sync_run │ async_run │
├──────────────────────────────────────┼──────────┼───────────┤
│ python_async_syntax_escape_handler   │    ✗     │     ✓     │
│ sync_await_handler                   │    ✓     │     ✗     │
└──────────────────────────────────────┴──────────┴───────────┘
```

### Correct Usage

```python
# For sync_run: use sync_handlers_preset
result = sync_run(program(), sync_handlers_preset)

# For async_run: use async_handlers_preset
result = await async_run(program(), async_handlers_preset)
```

If you use the wrong handler preset, `sync_run` will raise a clear `TypeError`:
```
TypeError: sync_run received PythonAsyncSyntaxEscape, which requires async/await.
This typically means python_async_syntax_escape_handler is in your handler stack,
but it is only compatible with async_run.
```

---

## 3. CircularAskError Not Implemented

**Files:** `test_ask_lazy_evaluation.py`

### Expected Behavior

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│  env = {"self": program()}  ← program Asks for "self"      │
│                                                             │
│  Ask("self")                                                │
│       │                                                     │
│       ▼                                                     │
│  Evaluate program() → Ask("self") → Evaluate program() → ...│
│       │                                                     │
│       ▼                                                     │
│  DETECT CYCLE → raise CircularAskError("self")             │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Current Behavior

CircularAskError detection is not implemented. The program raises `RecursionError` instead.

### Tests Affected

- `test_direct_circular_ask_raises_error` (test_ask_lazy_evaluation.py)
- `test_indirect_circular_ask_raises_error` (test_ask_lazy_evaluation.py)

---

## Architecture Overview

```
Handler Stack (outermost → innermost):
┌─────────────────────────────────────────────────────────────┐
│ scheduler_state_handler (manages task queue)                │
│   ┌─────────────────────────────────────────────────────┐   │
│   │ task_scheduler_handler (Spawn/Wait/Gather/Race)      │   │
│   │   ┌─────────────────────────────────────────────┐   │   │
│   │   │ sync_await_handler (sync) OR                 │   │   │
│   │   │ python_async_syntax_escape_handler (async)   │   │   │
│   │   │   ┌─────────────────────────────────────┐   │   │   │
│   │   │   │ core_handler (Get/Put/Ask/Local)     │   │   │   │
│   │   │   └─────────────────────────────────────┘   │   │   │
│   │   └─────────────────────────────────────────────┘   │   │
│   └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

---

## Resolution Status

| Issue | Priority | Status |
|-------|----------|--------|
| Local override inheritance | High | ✅ Fixed |
| Spawned children env | High | ✅ Fixed |
| Listen + Local log capture | Low | Investigate |
| Delay in sync mode | N/A | Expected behavior |
| CircularAskError | Low | Not implemented |
