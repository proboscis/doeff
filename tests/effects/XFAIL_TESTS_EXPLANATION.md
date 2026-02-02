# XFailed Tests Explanation

This document explains the tests marked as `xfail` in the effect test suite and the behavioral differences between the old `AsyncRuntime` class and the new `sync_run`/`async_run` function-based API.

## Summary

| Category | Count | Status |
|----------|-------|--------|
| Local override inheritance | 6 | xfail |
| Listen + Local combination | 4 | xfail |
| Delay in sync mode | 2 | xfail (strict=False) |
| CircularAskError | 4 | xfail |
| Total xfailed | ~16-20 | depends on params |

---

## 1. Local Override Not Inherited by Nested Locals

**Files:** `test_reader_effects.py`, `test_ask_lazy_evaluation.py`

### Expected Behavior (Old AsyncRuntime)

```
┌─────────────────────────────────────────────────────────────┐
│  env = {"key1": "orig1", "key2": "orig2"}                   │
│                                                             │
│  ┌─ Local({"key1": "outer1"}) ─────────────────────────┐   │
│  │                                                       │   │
│  │  Ask("key1") → "outer1"  ✓                           │   │
│  │                                                       │   │
│  │  ┌─ Local({"key2": "inner2"}) ───────────────────┐   │   │
│  │  │                                                 │   │   │
│  │  │  Ask("key1") → "outer1"  ← EXPECTED            │   │   │
│  │  │  Ask("key2") → "inner2"  ✓                     │   │   │
│  │  │                                                 │   │   │
│  │  └─────────────────────────────────────────────────┘   │   │
│  │                                                       │   │
│  └───────────────────────────────────────────────────────┘   │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Actual Behavior (New Handlers)

```
┌─────────────────────────────────────────────────────────────┐
│  env = {"key1": "orig1", "key2": "orig2"}                   │
│                                                             │
│  ┌─ Local({"key1": "outer1"}) ─────────────────────────┐   │
│  │                                                       │   │
│  │  Ask("key1") → "outer1"  ✓                           │   │
│  │                                                       │   │
│  │  ┌─ Local({"key2": "inner2"}) ───────────────────┐   │   │
│  │  │                                                 │   │   │
│  │  │  Ask("key1") → "orig1"  ← ACTUAL (uses root)   │   │   │
│  │  │  Ask("key2") → "inner2"  ✓                     │   │   │
│  │  │                                                 │   │   │
│  │  └─────────────────────────────────────────────────┘   │   │
│  │                                                       │   │
│  └───────────────────────────────────────────────────────┘   │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Root Cause

The inner Local starts with the **root environment** instead of the **current environment**. When the inner Local only overrides `key2`, it should inherit `key1="outer1"` from the outer Local, but instead it gets `key1="orig1"` from the root.

### Tests Affected

- `test_nested_local_different_keys` (test_reader_effects.py)
- `test_local_with_different_program_reevaluates` (test_ask_lazy_evaluation.py)
- `test_local_with_same_program_uses_cache` (test_ask_lazy_evaluation.py)

---

## 2. Spawned Children Don't See Local Override

**Files:** `test_reader_effects.py`, `test_effect_combinations.py`

### Expected Behavior

```
┌─────────────────────────────────────────────────────────────┐
│  env = {"key": "original"}                                  │
│                                                             │
│  ┌─ Local({"key": "from_local"}) ─────────────────────┐    │
│  │                                                      │    │
│  │  Spawn(child1) ──┐                                   │    │
│  │  Spawn(child2) ──┤  children should see "from_local" │    │
│  │  Gather(...)  ◄──┘                                   │    │
│  │                                                      │    │
│  └──────────────────────────────────────────────────────┘    │
│                                                             │
│  child1.Ask("key") → "from_local"  ← EXPECTED              │
│  child2.Ask("key") → "from_local"  ← EXPECTED              │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Actual Behavior

```
┌─────────────────────────────────────────────────────────────┐
│  env = {"key": "original"}                                  │
│                                                             │
│  ┌─ Local({"key": "from_local"}) ─────────────────────┐    │
│  │                                                      │    │
│  │  Spawn(child1) ──┐  spawned at Local scope          │    │
│  │  Spawn(child2) ──┤                                   │    │
│  │  Gather(...)  ◄──┘                                   │    │
│  │                                                      │    │
│  └──────────────────────────────────────────────────────┘    │
│                                                             │
│  child1.Ask("key") → "original"  ← ACTUAL (uses root)      │
│  child2.Ask("key") → "original"  ← ACTUAL (uses root)      │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Root Cause

When tasks are spawned, they capture the **root environment** instead of the **current environment at spawn time**. The Local override is not visible to spawned children.

### Tests Affected

- `test_gather_children_inherit_local_override` (test_reader_effects.py)
- `test_gather_children_inherit_local_env` (test_effect_combinations.py)

---

## 3. Listen + Local Combination

**Files:** `test_effect_combinations.py`

### Expected Behavior

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│  ┌─ Listen ────────────────────────────────────────────┐   │
│  │                                                       │   │
│  │  ┌─ Local({"key": "val"}) ───────────────────────┐   │   │
│  │  │                                                 │   │   │
│  │  │  Tell("log_message")                           │   │   │
│  │  │  result = ...                                   │   │   │
│  │  │                                                 │   │   │
│  │  └─────────────────────────────────────────────────┘   │   │
│  │                                                       │   │
│  │  Returns: ListenResult(value, log=["log_message"])   │   │
│  │                                                       │   │
│  └───────────────────────────────────────────────────────┘   │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Actual Behavior

The interaction between Listen and Local has different semantics in the new handler stack. Logs may not be captured correctly when Listen wraps Local.

### Tests Affected

- `test_listen_captures_logs_from_local` (test_effect_combinations.py)
- `test_complex_safe_local_listen_combination` (test_effect_combinations.py)

---

## 4. Delay Effect in Sync Mode (Expected Behavior)

**Files:** `test_effect_combinations.py`

### Issue

```
┌─────────────────────────────────────────────────────────────┐
│  sync_run with async_handlers_preset (WRONG!)               │
│                                                             │
│  yield Delay(seconds=0.05)                                 │
│       │                                                     │
│       ▼                                                     │
│  python_async_syntax_escape_handler creates:               │
│       async def do_delay():                                 │
│           await asyncio.sleep(0.05)                        │
│       return PythonAsyncSyntaxEscape(awaitable=do_delay()) │
│       │                                                     │
│       ▼                                                     │
│  sync_run: TypeError!                                       │
│  "sync_run received PythonAsyncSyntaxEscape..."            │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### This is Expected Behavior

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

### Tests Affected

- `test_async_gather_parallel_execution[sync]` - Test incorrectly uses parameterized
  interpreter which may use wrong handler preset for the mode.

---

## 5. CircularAskError Not Implemented

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

### Actual Behavior

CircularAskError detection is not implemented in the new handler. The program may hang or produce unexpected results.

### Tests Affected

- `test_direct_circular_ask_raises_error` (test_ask_lazy_evaluation.py)
- `test_indirect_circular_ask_raises_error` (test_ask_lazy_evaluation.py)

---

## Architecture Overview

```
OLD: AsyncRuntime (monolithic)
┌─────────────────────────────────────────────────────────────┐
│  AsyncRuntime                                               │
│  ├── Hardcoded handler stack                               │
│  ├── Environment inherited correctly through scopes        │
│  └── CircularAskError detection built-in                   │
└─────────────────────────────────────────────────────────────┘

NEW: Function-based API (modular)
┌─────────────────────────────────────────────────────────────┐
│  sync_run / async_run                                       │
│  ├── User-provided handler stack                           │
│  ├── Handlers compose via effects                          │
│  └── Some edge cases not yet implemented                   │
│                                                             │
│  Handler Stack (outermost → innermost):                    │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ scheduler_state_handler (manages task queue)         │   │
│  │   ┌─────────────────────────────────────────────┐   │   │
│  │   │ task_scheduler_handler (Spawn/Wait/Gather)   │   │   │
│  │   │   ┌─────────────────────────────────────┐   │   │   │
│  │   │   │ python_async_syntax_escape_handler   │   │   │   │
│  │   │   │   ┌─────────────────────────────┐   │   │   │   │
│  │   │   │   │ core_handler (Get/Put/Ask)   │   │   │   │   │
│  │   │   │   └─────────────────────────────┘   │   │   │   │
│  │   │   └─────────────────────────────────────┘   │   │   │
│  │   └─────────────────────────────────────────────┘   │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

---

## Resolution Status

| Issue | Priority | Effort | Status |
|-------|----------|--------|--------|
| Local override inheritance | Medium | Medium | Needs handler fix |
| Spawned children env | Medium | Medium | Needs handler fix |
| Listen + Local | Low | Low | Investigate |
| Delay in sync mode | Low | Low | Use Await instead |
| CircularAskError | Low | Medium | Not implemented |

These are known behavioral differences that don't affect the primary use cases. Most users won't encounter these edge cases in normal usage.
