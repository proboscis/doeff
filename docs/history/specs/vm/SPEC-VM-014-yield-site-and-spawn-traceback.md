# SPEC-VM-014: Continuation Parent Chain and GetTraceback DoCtrl

## Status: Draft (Revision 4)

Implementation plan: [IMPL-VM-014.md](IMPL-VM-014.md)

---

## 1. Summary

This spec is the companion to SPEC-VM-013 (GetExecutionContext Effect Dispatch). It covers two
tightly coupled changes:

1. **Continuation parent chain**: Add `parent: Option<Arc<Continuation>>` to the `Continuation`
   struct. When `handle_delegate` captures `K_new`, it links the previous `k_user` as
   `K_new.parent`, forming a delegation chain. This follows OCaml Multicore's `Handler_parent` chain
   precedent.
2. **GetTraceback DoCtrl**: A query DoCtrl that walks the continuation's parent chain and returns a
   structured list of `TraceHop`s, each containing `TraceFrame`s. No internal-frame filtering — the
   VM returns all program frames raw; handlers decide what to show.

---

## 2. Design

### 2.1 Continuation parent chain

Add a `parent` field to the `Continuation` struct:

```rust
pub struct Continuation {
    // ... existing fields ...
    pub parent: Option<Arc<Continuation>>,
}
```

In `handle_delegate`, before overwriting `k_user`:

```rust
let old_k_user = top.k_user.clone();
let mut k_new = self.capture_continuation(Some(dispatch_id));
k_new.parent = Some(Arc::new(old_k_user));  // link the chain
top.k_user = k_new;
```

This preserves the full delegation history: `K_new → k_user → (k_user's parent) → ...`. Multiple
Delegates compose naturally — each hop adds one link.

### 2.2 GetTraceback DoCtrl

A query DoCtrl that walks the parent chain and returns structured traceback data:

```rust
pub enum DoCtrl {
    // ... existing variants ...
    GetTraceback { continuation: Continuation },
}
```

Response: `Mode::Deliver(Value::Traceback(Vec<TraceHop>))`.

This follows the pattern of existing query DoCtrl variants:

| Query | Response |
|---|---|
| `GetContinuation` | `Value::Continuation(k)` |
| `GetHandlers` | `Value::Handlers(vec)` |
| `CreateContinuation` | `Value::Continuation(k)` |
| **`GetTraceback`** | **`Value::Traceback(hops)`** |

### 2.3 TraceFrame and TraceHop types

Frozen Rust pyclasses for structured traceback data:

```rust
#[pyclass(frozen, name = "TraceFrame")]
pub struct PyTraceFrame {
    #[pyo3(get)]
    pub func_name: String,
    #[pyo3(get)]
    pub source_file: String,
    #[pyo3(get)]
    pub source_line: u32,
}

#[pyclass(frozen, name = "TraceHop")]
pub struct PyTraceHop {
    #[pyo3(get)]
    pub frames: Vec<Py<PyTraceFrame>>,
}
```

Extensible for future fields (column number, handler identity) without breaking consumers.

### 2.4 No internal-frame filtering

The VM does NOT filter frames by source file path. All `Frame::Program` frames with metadata are
included. Handlers are user-space code and decide what is "internal" based on their own criteria.

This is a departure from `effect_site_from_continuation` which skips files matching `_effect_wrap`
or containing `/doeff/`. `GetTraceback` does not apply those filters.

### 2.5 Traceback algorithm

`GetTraceback` walks the parent chain and collects frames:

1. Start with continuation `k`.
2. For the current continuation, iterate `k.frames_snapshot` in natural order (index 0 = outermost,
   last = innermost, matching Python's "most recent call last" convention).
3. For each `Frame::Program { stream, metadata: Some(metadata) }`:
   - Try `stream_debug_location(stream)` for precise source line.
   - Fall back to `metadata.source_file` and `metadata.source_line`.
   - Emit `TraceFrame { func_name, source_file, source_line }`.
4. Bundle into a `TraceHop`.
5. If `k.parent` is `Some(parent)`, continue with parent.
6. Repeat until `parent` is `None`.

Result: `Vec<TraceHop>` ordered from innermost (current continuation) to outermost (root of parent
chain).

### 2.6 Spawn site data flow

With the parent chain, the scheduler always gets the correct spawn site regardless of Delegate:

1. Scheduler receives `(SpawnEffect, k)` — `k` may be `K_new` (if interceptor delegated) or
   original `k_user`.
2. Scheduler yields `GetTraceback(k)`.
3. VM walks `k → k.parent → ...`, returns all frames across the chain.
4. Scheduler inspects the traceback to find the user's spawn site (typically the outermost hop).

No dual-path logic. No effect enrichment. No `_spawn_site` field on SpawnEffect. One path, always
correct.

### 2.7 spawn_intercept_handler (simplified)

With the parent chain providing correct spawn site attribution, the interceptor is coercion-only:

```python
def spawn_intercept_handler(effect, k):
    if isinstance(effect, SpawnEffect):
        raw = yield doeff_vm.Delegate()
        return (yield doeff_vm.Resume(k, coerce_task_handle(raw)))
    yield doeff_vm.Pass()
```

No `GetTraceback` call in the interceptor. The scheduler handles traceback retrieval.

---

## 3. Semantics

### 3.1 GetTraceback query flow

```
handler code              VM
────────────              ──
yield GetTraceback(k)
                    ───→ handle_get_traceback(k.continuation)
                         walk k → k.parent → k.parent.parent → ...
                         for each: collect Program frames into TraceHop
                         mode = Mode::Deliver(Value::Traceback(hops))
                    ←───
traceback: list[TraceHop]
```

GetTraceback is non-terminal and query-only:
- Input: continuation `k`
- Output: `Value::Traceback(Vec<TraceHop>)`
- No effect dispatch started
- No handler-chain mutation
- No continuation consumption

### 3.2 Parent chain under delegation

```
user code            intercept_handler       scheduler
─────────            ─────────────────       ─────────
yield Spawn ──→      (effect, k_user)
                     yield Delegate()
                       VM: K_new = capture(handler frames)
                            K_new.parent = Some(k_user)
                            top.k_user = K_new
                                              ──→ (effect, K_new)
                                                   yield GetTraceback(K_new)
                                                   VM walks:
                                                     hop 0: K_new.frames (interceptor)
                                                     hop 1: k_user.frames (user code) ← CORRECT
```

### 3.3 Multiple Delegates compose

```
user → handler_A → handler_B → scheduler
        Delegate     Delegate

K_new_B.parent = K_new_A
K_new_A.parent = k_user

GetTraceback(K_new_B) returns:
  hop 0: handler_B frames
  hop 1: handler_A frames
  hop 2: user frames
```

---

## 4. Edge Cases

1. **Continuation with no Program frames**: produces `TraceHop { frames: [] }`. Parent chain is
   still walked.
2. **Continuation with no parent**: returns a single `TraceHop`. Common case for non-delegated
   effects.
3. **Consumed continuation**: `GetTraceback` fails at `classify_yielded` when looking up
   `cont_id`. Handlers must call `GetTraceback(k)` BEFORE resuming.
4. **Outside dispatch context**: returns `VMError` (same guard pattern as `GetContinuation`).
5. **Deep parent chains**: bounded by handler stack depth (typically 1-3 hops).
   `Arc<Continuation>` wrapping is cheap (reference-counted).
6. **Multiple concurrent runs**: parent chain is per-continuation, no shared state.

---

## 5. Acceptance Criteria

### Parent chain
- [ ] `Continuation` has `parent: Option<Arc<Continuation>>` field.
- [ ] `Continuation::capture` initializes `parent: None`.
- [ ] `handle_delegate` sets `k_new.parent = Some(Arc::new(old_k_user))` before overwriting.

### GetTraceback DoCtrl
- [ ] `DoCtrl::GetTraceback { continuation }` variant exists.
- [ ] `Value::Traceback(Vec<TraceHop>)` variant exists.
- [ ] `handle_get_traceback` walks parent chain, collects all `Frame::Program` frames.
- [ ] No internal-frame filtering — all Program frames included.
- [ ] Frame ordering: natural (outermost first, innermost last) within each hop.
- [ ] Hop ordering: innermost continuation first, outermost (root parent) last.
- [ ] Requires dispatch context (returns error outside dispatch).
- [ ] `step_handle_yield` routes `GetTraceback` to handler.

### Python types
- [ ] `PyTraceFrame` pyclass: `func_name`, `source_file`, `source_line`.
- [ ] `PyTraceHop` pyclass: `frames: list[TraceFrame]`.
- [ ] `PyGetTraceback` pyclass: `continuation` field.
- [ ] `DoExprTag::GetTraceback` exists.
- [ ] `GetTraceback`, `TraceFrame`, `TraceHop` exported from Python module.

### Cleanup
- [ ] Previous `GetYieldSite` / `Value::YieldSite` / `PyGetYieldSite` artifacts removed.
- [ ] `spawn_site` field removed from `PySpawn`.

### Spawn site attribution
- [ ] `spawn_intercept_handler` is coercion-only (no GetTraceback call).
- [ ] `spawn_site_from_continuation` deleted from scheduler.
- [ ] Scheduler yields `GetTraceback(k)` for spawn site.
- [ ] Spawn site correct when inner handler delegates (non-terminal Delegate).
- [ ] Spawn site correct when no interceptor installed (single-hop traceback).

---

## 6. Related Specs

- `SPEC-VM-013` (GetExecutionContext Effect Dispatch) — companion spec; handlers use GetTraceback
  for frame-level context enrichment
- `SPEC-VM-010` (Non-Terminal Delegate) — K_new continuation swap, parent chain linkage
- `SPEC-VM-PROTOCOL` — VM invariants (C3: DoCtrl vocabulary, C4: opaque effects)
- `SPEC-SCHED-001` — Scheduler architecture, `TaskMetadata`, `SchedulerState`

---

## 7. Revision Log

| Date | Author | Changes |
|---|---|---|
| 2026-02-22 | OpenCode | Initial: GetYieldSite DoCtrl, scheduler-owned spawn traceback. |
| 2026-02-22 | OpenCode | Fixed oracle findings: filtering, thread-safety, signatures, edge cases. |
| 2026-02-22 | OpenCode | Major: Replaced GetYieldSite with GetTraceback. Added parent chain, TraceFrame/TraceHop. Simplified spawn_intercept_handler. |
| 2026-02-22 | OpenCode | Revision 4: Purified spec to desired-state. Extracted implementation to IMPL-VM-014.md. Phase 3 (EXCEPTION_SPAWN_BOUNDARIES) superseded by SPEC-VM-013. |
