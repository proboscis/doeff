# IMPL-VM-014: Implementation Plan

Companion to [SPEC-VM-014.md](SPEC-VM-014-yield-site-and-spawn-traceback.md).

This document inventories current state, describes the phased implementation, and tracks code-level
changes needed to achieve the spec.

---

## 1. Current State Inventory

### 1.1 Continuation struct (no parent field)

`Continuation` at `continuation.rs:27-53` has: `cont_id`, `segment_id`, `frames_snapshot`,
`scope_chain`, `marker`, `dispatch_id`, `started`, `program`, `handlers`, `handler_identities`,
`metadata`. NO `parent` field. Registry: `HashMap<ContId, Continuation>` at `vm.rs:161`.

`Continuation::capture` at `continuation.rs:56-74` initializes all fields. No parent parameter.

### 1.2 handle_delegate destroys original k_user

`handle_delegate` at `vm.rs:2926-2939` captures K_new from current segment frames, clears segment,
overwrites `top.k_user = k_new` in place. The original `k_user` is destroyed — no parent field, no
chain. There is no way to reconstruct the delegation history from K_new alone.

### 1.3 Duplicated frame-walking logic

`spawn_site_from_continuation` at `scheduler.rs:488-522` is nearly identical to
`effect_site_from_continuation` at `vm.rs:544-579`. Both walk `k.frames_snapshot.iter().rev()`,
skip internal source files, return first non-internal frame. This duplication violates C3 (DoCtrl
is the only instruction vocabulary).

### 1.4 Wrong spawn site under Delegate

At `scheduler.rs:1985`, `spawn_site_from_continuation(&k_user)` receives `K_new` (the delegating
handler's continuation), not the original user continuation. The reported spawn site is the
handler's yield location, not the user code that called `Spawn`.

### 1.5 PyK exposes no frame data

`PyK` at `pyvm.rs:1632` stores only `cont_id`. No frame data accessible from Python.

### 1.6 Partially implemented GetYieldSite (from prior revision)

The following artifacts exist on the current branch but need replacement:
- `DoCtrl::GetYieldSite` variant
- `Value::YieldSite`
- `PyGetYieldSite` class
- `DoExprTag::GetYieldSite`
- `handle_get_yield_site` function
- `spawn_site: Option<Py<PyAny>>` on `PySpawn` in `effect.rs`

These are renamed/extended to the GetTraceback equivalents.

---

## 2. Implementation Phases

### Phase 1: Parent chain and GetTraceback DoCtrl

#### Add parent field to Continuation

```rust
// continuation.rs
pub struct Continuation {
    pub cont_id: ContId,
    pub segment_id: SegmentId,
    pub frames_snapshot: Arc<Vec<Frame>>,
    pub scope_chain: Arc<Vec<Marker>>,
    pub marker: Marker,
    pub dispatch_id: Option<DispatchId>,
    pub started: bool,
    pub program: Option<PyShared>,
    pub handlers: Vec<Handler>,
    pub handler_identities: Vec<Option<PyShared>>,
    pub metadata: Option<CallMetadata>,
    pub parent: Option<Arc<Continuation>>,  // NEW
}
```

`Continuation::capture` initializes `parent: None`. Update `Clone`/`clone_ref` impls.

#### Update handle_delegate

```rust
// vm.rs handle_delegate (at vm.rs:2926-2939)
let old_k_user = top.k_user.clone();
let mut k_new = self.capture_continuation(Some(dispatch_id))?;
k_new.parent = Some(Arc::new(old_k_user));  // preserve chain
// ... clear segment, swap k_user ...
top.k_user = k_new;
```

#### DoCtrl variant and Value

```rust
// do_ctrl.rs
pub enum DoCtrl {
    // ... existing ...
    GetTraceback { continuation: Continuation },
}

// value.rs
pub enum Value {
    // ... existing ...
    Traceback(Vec<TraceHop>),
}
```

#### VM handler

```rust
// vm.rs
fn handle_get_traceback(&mut self, continuation: Continuation) -> StepEvent {
    let Some(_top) = self.dispatch_stack.last() else {
        return StepEvent::Error(VMError::internal(
            "GetTraceback called outside of dispatch context",
        ));
    };
    let hops = Self::collect_traceback(&continuation);
    self.mode = Mode::Deliver(Value::Traceback(hops));
    StepEvent::Continue
}

fn collect_traceback(k: &Continuation) -> Vec<TraceHop> {
    let mut hops = Vec::new();
    let mut current: Option<&Continuation> = Some(k);

    while let Some(cont) = current {
        let mut frames = Vec::new();
        // Natural order: index 0 = outermost, last = innermost
        for frame in cont.frames_snapshot.iter() {
            if let Frame::Program { stream, metadata: Some(metadata) } = frame {
                let (source_file, source_line) = match stream_debug_location(stream) {
                    Some(loc) => (loc.source_file, loc.source_line),
                    None => (metadata.source_file.clone(), metadata.source_line),
                };
                frames.push(TraceFrame {
                    func_name: metadata.func_name.clone(),
                    source_file,
                    source_line,
                });
            }
        }
        hops.push(TraceHop { frames });
        current = cont.parent.as_deref();
    }
    hops
}
```

#### step_handle_yield routing

```rust
DoCtrl::GetContinuation => self.handle_get_continuation(),
DoCtrl::GetHandlers => self.handle_get_handlers(),
DoCtrl::GetTraceback { continuation } => self.handle_get_traceback(continuation),
```

#### Python classes

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

#[pyclass(name = "GetTraceback", extends = PyDoCtrlBase)]
pub struct PyGetTraceback {
    #[pyo3(get)]
    pub continuation: Py<PyAny>,  // PyK object
}

#[pymethods]
impl PyGetTraceback {
    #[new]
    fn new(py: Python<'_>, continuation: Py<PyAny>) -> PyResult<PyClassInitializer<Self>> {
        Ok(PyClassInitializer::from(PyDoExprBase)
            .add_subclass(PyDoCtrlBase { tag: DoExprTag::GetTraceback as u8 })
            .add_subclass(PyGetTraceback { continuation }))
    }
}
```

#### DoExprTag and classify_yielded

```rust
pub enum DoExprTag {
    // ... existing ...
    GetTraceback = <next_available_tag>,
}

// In classify_yielded:
DoExprTag::GetTraceback => {
    let k_obj: Py<PyAny> = obj.getattr("continuation")?;
    let cont_id = extract_cont_id(&k_obj)?;
    let continuation = vm.lookup_continuation(cont_id)?;
    DoCtrl::GetTraceback { continuation: continuation.clone() }
}
```

#### Rework existing GetYieldSite

Replace all GetYieldSite artifacts with GetTraceback equivalents:
- `DoCtrl::GetYieldSite` → `DoCtrl::GetTraceback`
- `Value::YieldSite` → `Value::Traceback`
- `PyGetYieldSite` → `PyGetTraceback`
- `DoExprTag::GetYieldSite` → `DoExprTag::GetTraceback`
- `handle_get_yield_site` → `handle_get_traceback`
- Remove `spawn_site: Option<Py<PyAny>>` from `PySpawn` in `effect.rs`

### Phase 2: Delete spawn_site_from_continuation, simplify spawn_intercept_handler

#### Simplify spawn_intercept_handler

```python
# doeff/effects/spawn.py
def spawn_intercept_handler(effect, k):
    if isinstance(effect, SpawnEffect):
        raw = yield doeff_vm.Delegate()
        return (yield doeff_vm.Resume(k, coerce_task_handle(raw)))
    yield doeff_vm.Pass()
```

#### Scheduler uses GetTraceback for spawn site

```rust
// scheduler.rs - new phase
SpawnAwaitTraceback {
    k_user: Continuation,
    effect: DispatchEffect,
}
```

When scheduler receives Spawn, it yields `GetTraceback(k_user)`. VM delivers traceback. Scheduler
extracts spawn site from the outermost hop with user-code frames.

#### Deletions

- `spawn_site_from_continuation` function at `scheduler.rs:488-522`
- Call site at `scheduler.rs:1985`

---

## 3. Files Changed

| File | Change | Phase |
|---|---|---:|
| `packages/doeff-vm/src/continuation.rs` | Add `parent: Option<Arc<Continuation>>`, update `capture`, `Clone`/`clone_ref` | 1 |
| `packages/doeff-vm/src/vm.rs` | Update `handle_delegate` (parent linkage); add `handle_get_traceback`, `collect_traceback`; `step_handle_yield` routing; rework `handle_get_yield_site` | 1 |
| `packages/doeff-vm/src/do_ctrl.rs` | Replace `GetYieldSite` with `GetTraceback { continuation }` | 1 |
| `packages/doeff-vm/src/value.rs` | Replace `Value::YieldSite` with `Value::Traceback(Vec<TraceHop>)`, update `to_pyobject`, `clone_ref` | 1 |
| `packages/doeff-vm/src/pyvm.rs` | Replace `DoExprTag::GetYieldSite`/`PyGetYieldSite` with GetTraceback equivalents; add `PyTraceFrame`, `PyTraceHop`; update `classify_yielded`; module export | 1 |
| `packages/doeff-vm/src/effect.rs` | Remove `spawn_site: Option<Py<PyAny>>` from `PySpawn` | 1 |
| `doeff/rust_vm.py` | Export `GetTraceback`, `TraceFrame`, `TraceHop` (replace `GetYieldSite`) | 1 |
| `tests/` | Unit tests for GetTraceback and parent chain | 1 |
| `doeff/effects/spawn.py` | Simplify `spawn_intercept_handler` to coercion-only | 2 |
| `packages/doeff-vm/src/scheduler.rs` | Remove `spawn_site_from_continuation`; add `SpawnAwaitTraceback` phase; update spawn handling | 2 |
| `tests/` | Integration tests for spawn site attribution under Delegate | 2 |

---

## 4. References

- `packages/doeff-vm/src/continuation.rs:27-53` — Continuation struct (no parent field today)
- `packages/doeff-vm/src/continuation.rs:56-74` — `Continuation::capture`
- `packages/doeff-vm/src/vm.rs:544-579` — `effect_site_from_continuation` (frame scan algorithm)
- `packages/doeff-vm/src/vm.rs:2926-2939` — `handle_delegate` (K_new capture, k_user overwrite)
- `packages/doeff-vm/src/vm.rs:2461-2465` — `capture_continuation`
- `packages/doeff-vm/src/vm.rs:161` — `continuation_registry: HashMap<ContId, Continuation>`
- `packages/doeff-vm/src/vm.rs:3219-3229` — `handle_get_continuation` (query DoCtrl pattern)
- `packages/doeff-vm/src/vm.rs:3231-3247` — `handle_get_handlers` (query DoCtrl pattern)
- `packages/doeff-vm/src/vm.rs:1962-1963` — `step_handle_yield` DoCtrl routing
- `packages/doeff-vm/src/do_ctrl.rs` — DoCtrl enum
- `packages/doeff-vm/src/pyvm.rs:1632` — PyK stores `cont_id` only
- `packages/doeff-vm/src/scheduler.rs:488-522` — `spawn_site_from_continuation`
- `packages/doeff-vm/src/scheduler.rs:1985` — `spawn_site_from_continuation(&k_user)` call site
- `packages/doeff-vm/src/effect.rs:59-69` — PySpawn (has `spawn_site` to remove)
- `doeff/effects/spawn.py:180-185` — spawn_intercept_handler
- OCaml Multicore runtime — `Handler_parent` chain, `caml_reperform` precedent
