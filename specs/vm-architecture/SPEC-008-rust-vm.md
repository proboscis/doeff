# SPEC-008: Rust VM for Algebraic Effects

## Status: Draft (Revision 9)

### Revision 9 Changelog

Changes from Rev 8. Driven by SPEC-TYPES-001 reconciliation (Program/Effect separation).

| Tag | Section | Change |
|-----|---------|--------|
| **R9-A** | Control Primitives | New: `Call { f, args, kwargs, metadata }` — call a function and run the result with call stack metadata. |
| **R9-B** | Control Primitives | New: `GetCallStack` — walk frames and return `Vec<CallMetadata>`. |
| **R9-C** | Frame enum | `PythonGenerator` gains `metadata: Option<CallMetadata>` field for call stack tracking. |
| **R9-D** | New struct | `CallMetadata { function_name, source_file, source_line, program_call }` — carried on frames. |
| **R9-E** | Yielded / classify | `Yielded::Program` kept for backward compat (metadata: None). KPC-originated programs upgraded to `Call` with metadata by driver. |
| **R9-F** | handle_do_ctrl | New arms for `Call` (emit CallFunc/StartProgram + store metadata), `Eval` (create+resume continuation), and `GetCallStack` (walk frames). |
| **R9-G** | receive_python_result | `StartProgramFrame` pending now carries `Option<CallMetadata>` to attach to pushed frame. |
| **R9-H** | Control Primitives | New: `Eval { expr, handlers }` — evaluate a DoExpr in a fresh scope with explicit handler chain. Atomic CreateContinuation + ResumeContinuation. |

### Revision 8 Changelog

Changes from Rev 7, grouped by section. Each change is marked with a tag
so reviewers can accept/reject individually.

| Tag | Section | Change |
|-----|---------|--------|
| **R8-A** | ADR-2 | Rewritten: "Unified Handler Protocol" replaces "No Built-in Bypass". Drops stdlib as separate concept. |
| **R8-B** | ADR-8 (new) | New ADR: Drop `Handler::Stdlib`, unify to `RustProgram`/`Python` only. |
| **R8-C** | ADR-9 (new) | New ADR: PyO3-exposed primitive types (`WithHandler`, `Resume`, `Delegate`, `Transfer`, `K`). |
| **R8-D** | ADR-10 (new) | New ADR: Handler identity preservation — `GetHandlers` returns original Python objects. |
| **R8-E** | ADR-11 (new) | New ADR: Store/env initialization via `put_state`/`put_env` + `env_items` extraction. |
| **R8-F** | Principle 3 | Removed "immediate stdlib handler" signature. Only `RustHandlerProgram` + Python handler. |
| **R8-G** | Handler enum | Reduced to two variants: `RustProgram` and `Python`. `Stdlib` variant deleted. |
| **R8-H** | Stdlib Handlers | `StdlibHandler`, `HandlerAction`, `HandlerContext(ModifyPending)`, `NeedsPython` deleted. Replaced by `RustProgramHandler` impls. |
| **R8-I** | Handler section | `StateHandlerFactory`, `ReaderHandlerFactory`, `WriterHandlerFactory` added as `RustProgramHandler` implementations. |
| **R8-J** | Public API Contract (new) | New section: `run()`/`async_run()` contract, `RunResult`, `@do`/`Program[T]`, store init/extract flow, handler nesting order. Closes SPEC-009 support gaps. |

---

## Summary

This spec defines a **Rust-based VM** for doeff's algebraic effects system, with Python integration via PyO3.

**Key insight**: The VM core (segments, frames, dispatch, primitives) is unified Rust. Python generators are leaf nodes at the FFI boundary.

```
┌─────────────────────────────────────────────────────────────────┐
│  Python Layer (doeff library)                                   │
│    - @do decorated generators (user code)                       │
│    - Python handlers (user-defined effects)                     │
│    - High-level API (run, with_handler, etc.)                   │
└─────────────────────────────────────────────────────────────────┘
                              │ PyO3 FFI
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  Rust VM (doeff-vm crate)                                       │
│                                                                 │
│    Segments          Frames           Dispatch                  │
│    ┌────────┐       ┌────────┐       ┌────────────┐            │
│    │ marker │       │ PyGen  │       │ dispatch_  │            │
│    │ frames │◄─────►│ RustProg│      │ stack      │            │
│    │ caller │       │ RustCb │       │            │            │
│    │ scope  │       └────────┘       │ visible_   │            │
│    └────────┘                        │ handlers() │            │
│        │                             └────────────┘            │
│        │              Primitives                                │
│        │             ┌─────────────────────────────┐           │
│        └────────────►│ Resume, Transfer, Delegate  │           │
│                      │ WithHandler, Call,           │           │
│                      │ GetContinuation, GetCallStack│          │
│                      └─────────────────────────────┘           │
│                                                                 │
│    3-Layer State Model                                          │
│    ┌──────────────────────────────────────────────┐            │
│    │ L1: Internals (hidden)                       │            │
│    │     dispatch_stack, segments, callbacks      │            │
│    ├──────────────────────────────────────────────┤            │
│    │ L2: RustStore (standard handler state)        │            │
│    │     state, env, log (HashMap/Vec<Value>)     │            │
│    ├──────────────────────────────────────────────┤            │
│    │ L3: PyStore (optional escape hatch)          │            │
│    │     Python dict for user handlers            │            │
│    └──────────────────────────────────────────────┘            │
└─────────────────────────────────────────────────────────────────┘
```

---

## Design Decisions

### ADR-1: Hybrid Frame Architecture

**Decision**: Rust manages the frame stack; Python generators are leaf nodes.

**Rationale**:
- Rust controls continuation structure (segments, caller links, scope_chain)
- Python generators handle user code execution
- Frame switching is Rust-native (fast)
- Python calls happen at frame boundaries (GIL acquired/released cleanly)

### ADR-2: Unified Handler Protocol (No Stdlib Special Case)

**Decision**: ALL handlers — Rust-native and Python-implemented — share one
dispatch protocol. There is no separate "stdlib" handler path. The `Handler`
enum has exactly two variants: `RustProgram` and `Python`.

**Rationale**:
- Algebraic effects principle: "handlers give meaning to effects"
- Users can intercept, override, or replace any effect (logging, persistence, testing)
- Single dispatch path simplifies spec and implementation
- Rust-native handlers (state, reader, writer, scheduler) are an **optimization**,
  not a protocol difference — they implement the same `RustProgramHandler` trait
- No hard-coded effect→handler matching; each handler's `can_handle()` decides

**Performance**: Rust-native handlers avoid Python calls and GIL acquisition.
The `RustProgramHandler` trait adds negligible overhead vs. the old `HandlerAction`
path (one vtable call + one match arm).

**Handler Installation** (all handlers are explicit, no defaults):
```python
from doeff import run, WithHandler
from doeff.handlers import state, reader, writer

# Standard handlers are just handlers — no special treatment
result = run(
    my_program(),
    handlers=[state, reader, writer],
    store={"x": 0},
)

# User can replace any standard handler with custom implementation
result = run(
    my_program(),
    handlers=[my_persistent_state, reader, writer],
)

# Handlers composable via WithHandler anywhere
@do
def my_program():
    result = yield WithHandler(
        handler=cache_handler,
        program=sub_program(),
    )
    return result
```

**Built-in Scheduler (Explicit)**:
```python
from doeff.handlers import scheduler
result = run(my_program(), handlers=[scheduler, state, reader, writer])
```

### ADR-3: GIL Release Strategy

**Decision**: Release GIL during pure Rust computation, reacquire at Python boundaries.

**Rationale**:
- Rust frame management doesn't need GIL
- Pure Rust handlers (State, Reader) don't need GIL
- Python handler invocation requires GIL
- Enables better concurrency when multiple threads run independent programs

### ADR-4: Synchronous Rust VM

**Decision**: Rust VM is synchronous. Async is handled by Python wrapper.

**Rationale**:
- Simpler FFI boundary (no async trait objects across FFI)
- Python's asyncio can call `vm.step()` in a loop
- Rust `async` would complicate lifetime management with PyO3
- Can add async Rust later if needed

### ADR-4a: Asyncio Integration (Reference)

**Decision**: Provide a Python-level async driver (`async_run` / `VM.run_async`) and the
`PythonAsyncSyntaxEscape` DoCtrl for handlers that need `await`.

**Rationale**:
- Python's asyncio APIs require an `async def` context and a running event loop
- Handlers execute during `step()` (synchronous) and cannot call asyncio directly
- `PythonAsyncSyntaxEscape` lets handlers request "run this action in async context"
- `sync_run`/`VM.run` remains the canonical path; `async_run` is a wrapper for interop

**Invariant**: `sync_run` MUST NOT see `PythonAsyncSyntaxEscape` / `CallAsync`. It raises
`TypeError` if it does.

### ADR-5: Typed Store with Escape Hatch

**Decision**: Known VM state in typed Rust fields; user state in `HashMap<String, Value>`.

**Rationale**:
- `handlers`, `dispatch_stack`, `consumed_ids` are VM internals → typed Rust
- User state (Get/Put) can be arbitrary Python objects → Value::Python
- Type safety for VM operations; flexibility for user code
- Can optimize hot paths (state lookups) in Rust

### ADR-6: Callbacks in VM Table (FnOnce Support)

**Decision**: FnOnce callbacks are stored in a VM-owned table; Frames hold CallbackId.

**Rationale**:
- `Box<dyn FnOnce>` is not Clone, but Frames need to be cloneable for continuation capture
- CallbackId is Copy, so Frames become Clone
- On execution, `callbacks.remove(id)` consumes the FnOnce
- Clean separation of "what to run" (callback) from "continuation structure" (frame)

### ADR-7: Mutable Execution Segments + Snapshot Continuations

**Decision**: Running Segments have mutable `Vec<Frame>`; captured Continuations hold `Arc<Vec<Frame>>` snapshots.

**Rationale**:
- Segments need push/pop during execution
- Continuations need immutable snapshots for one-shot semantics
- Resume materializes snapshot back to mutable Vec (shallow clone, Frame is small)
- Future optimization: persistent cons-list for O(1) sharing

### ADR-8: Drop Handler::Stdlib — Unified to RustProgram/Python [R8-B]

**Decision**: The `Handler` enum has exactly two variants: `RustProgram` and
`Python`. The former `Handler::Stdlib` variant is removed. State, Reader, and
Writer handlers become `RustProgramHandler` implementations — the same trait
the scheduler already uses.

**Rationale**:
- `Handler::Stdlib` had a separate dispatch path: hard-coded `can_handle()` matching,
  direct `RustStore` mutation via `HandlerAction`, and a special `NeedsPython` flow
  for `Modify`. This created three dispatch protocols instead of one.
- `Handler::RustProgram` already provides a generator-like protocol
  (`start`/`resume`/`throw`) that handles the same cases — including calling Python
  mid-handler (the `Modify` modifier callback).
- Unifying to two variants means one dispatch path, one matching mechanism
  (`can_handle()`), and one handler-invocation protocol per variant.
- The scheduler is already a `RustProgram` handler and works correctly. State, Reader,
  and Writer are simpler — they're a subset of what the scheduler does.

**What is deleted**: `StdlibHandler` enum, `HandlerAction` enum,
`HandlerContext(ModifyPending)`, `NeedsPython` variant, `continue_after_python()`.

**What replaces it**: `StateHandlerFactory`, `ReaderHandlerFactory`,
`WriterHandlerFactory` — each implementing `RustProgramHandler`.

### ADR-9: PyO3-Exposed Primitive Types [R8-C]

**Decision**: Control primitives and composition primitives are Rust `#[pyclass]`
types exposed to Python, not Python dataclasses parsed by `classify_yielded`.

**Types exposed**:
- `WithHandler(handler, expr)` — composition primitive (usable anywhere)
- `Resume(k, value)` — dispatch primitive (handler-only)
- `Delegate(effect?)` — dispatch primitive (handler-only)
- `Transfer(k, value)` — dispatch primitive (handler-only)
- `K` — opaque continuation handle (no Python-visible fields)

**Rationale**:
- Eliminates fragile attribute-name parsing in `classify_yielded` (e.g., reading
  `.body` vs `.program` from a Python dataclass)
- Type checking via `isinstance` against a Rust-defined class is faster and
  unambiguous
- The `K` type is created by the VM and passed to Python handlers; Python code
  can pass it around but cannot inspect or construct it
- `WithHandler` field names are defined once in Rust, no Python/Rust mismatch

### ADR-10: Handler Identity Preservation [R8-D]

**Decision**: `GetHandlers` returns the original Python objects the user passed
to `run()` or `WithHandler`, at `id()` level.

**Mechanism**:
- When a handler is installed (via `WithHandler` or `run()`), the VM stores the
  original Python object (`Py<PyAny>`) alongside the internal `Handler` variant
- For `Handler::RustProgram` handlers recognized from Python sentinel objects
  (e.g., `state`, `reader`, `writer`), the VM stashes the sentinel's `Py<PyAny>`
- For `Handler::Python` handlers, the callable is already stored as `Py<PyAny>`
- `GetHandlers` traverses the scope chain and returns the stashed Python objects

**Rationale**:
- Users expect `state in (yield GetHandlers())` to work
- Handler identity matters for patterns like "am I inside this handler?"
- The `HandlerEntry` struct gains a `py_identity: Option<Py<PyAny>>` field

### ADR-11: Store/Env Initialization and Extraction [R8-E]

**Decision**: PyVM exposes `put_state()`, `put_env()`, `env_items()` for the
`run()` function to seed initial state and read back results.

**New PyVM methods**:
- `put_state(key: str, value: PyAny)` — sets `RustStore.state[key]`
- `put_env(key: str, value: PyAny)` — sets `RustStore.env[key]`
- `env_items() -> dict` — returns `RustStore.env` as Python dict

**Existing** (unchanged):
- `state_items() -> dict` — returns `RustStore.state` as Python dict
- `logs() -> list` — returns `RustStore.log` as Python list

**Rationale**:
- The `run()` function (SPEC-009) takes `env={}` and `store={}` parameters
- It needs to seed the VM before running and extract results after
- These methods are implementation details — users never call them directly

---

## Core Design Principles

### Principle 1: Segment = Delimited Continuation Frame (Rust)

Segment is a Rust struct representing a delimited continuation frame:
- Frames (K) - Vec of Rust Frame enums (mutable during execution)
- Caller link (Option<SegmentId>)
- Marker (handler identity this segment belongs to)
- scope_chain (Vec<Marker>) - evidence vector snapshot
- kind (Normal or PromptBoundary)

### Principle 2: Three Distinct Contexts

| Context | What it is | Tracked by |
|---------|------------|------------|
| User code location | Where effect was performed | `k_user.segment_id` |
| Handler scope boundary | Where WithHandler was installed | **PromptSegment** (kind=PromptBoundary) |
| Handler execution | Where handler code runs | `handler_exec_seg` |

### Principle 3: Explicit Continuations [R8-F]

Handlers (Rust or Python) receive continuations explicitly. There is one
handler protocol with two implementations:

```rust
// Rust handler (generator-like, used by state/reader/writer/scheduler)
trait RustHandlerProgram {
    fn start(&mut self, effect: Effect, k: Continuation, store: &mut RustStore) -> RustProgramStep;
    fn resume(&mut self, value: Value, store: &mut RustStore) -> RustProgramStep;
    fn throw(&mut self, exc: PyException, store: &mut RustStore) -> RustProgramStep;
}

// Python handler (via PyO3)
// def handler(effect, k) -> Program[Any]
// VM converts the Program to a generator via to_generator().
```

Both produce the same observable behavior: `Resume(k, value)`, `Delegate(effect)`,
or `Transfer(k, value)`. The distinction is purely an implementation optimization.

### Principle 4: Ownership and Lifetimes

All Rust data structures use ownership semantics:
- Segments owned by VM's segment arena
- Continuations hold SegmentId + Arc<frames snapshot>
- Callbacks owned by VM's callback table
- PyObjects use Py<T> for GIL-independent storage
- No `unsafe` in core logic (PyO3 handles FFI safety)

---

## Rust Data Structures

### Marker and IDs

```rust
/// Unique identifier for prompts/handlers.
/// All segments under the same with_handler share the same Marker.
#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug)]
pub struct Marker(u64);

/// Unique identifier for segments (arena index)
#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug)]
pub struct SegmentId(u32);

// SegmentId(0) may be used as a placeholder for unstarted continuations.

/// Unique identifier for continuations (one-shot tracking)
#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug)]
pub struct ContId(u64);

/// Unique identifier for dispatches
#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug)]
pub struct DispatchId(u64);

/// Unique identifier for callbacks in VM table
#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug)]
pub struct CallbackId(u32);

impl Marker {
    pub fn fresh() -> Self {
        static COUNTER: AtomicU64 = AtomicU64::new(1);
        Marker(COUNTER.fetch_add(1, Ordering::Relaxed))
    }
}

// Marker(0) is reserved for internal placeholders (e.g., unstarted continuations).
```

### Frame (Clone-able via CallbackId)

```rust
/// A frame in the continuation stack.
/// 
/// Frames are Clone because they may be captured in continuations.
/// FnOnce callbacks are stored separately in VM.callbacks table.
#[derive(Debug, Clone)]
pub enum Frame {
    /// Rust-native return frame (for standard handlers).
    /// The actual callback is in VM.callbacks[cb].
    RustReturn {
        cb: CallbackId,
    },
    
    /// Rust program handler frame (generator-like, no Python calls).
    /// program is a shared reference (see RustProgramRef in Handler section).
    RustProgram {
        program: RustProgramRef,
    },
    
    /// Python generator frame (user code or Python handlers)
    PythonGenerator {
        /// The Python generator object (GIL-independent storage)
        generator: Py<PyAny>,
        /// Whether this generator has been started (first __next__ called)
        started: bool,
        /// Call stack metadata (populated by Call primitive, None for legacy Yielded::Program) [R9-C]
        metadata: Option<CallMetadata>,
    },
}

/// Metadata about a program call for call stack reconstruction. [R9-D]
///
/// Extracted by the driver (with GIL) during classify_yielded or by
/// RustHandlerPrograms that emit Call primitives. Stored on PythonGenerator
/// frames. Read by GetCallStack (no GIL needed for the Rust fields).
#[derive(Debug, Clone)]
pub struct CallMetadata {
    /// Human-readable function name (e.g., "fetch_user", from KPC.function_name)
    pub function_name: String,
    /// Source file where the @do function is defined
    pub source_file: String,
    /// Line number in source file
    pub source_line: u32,
    /// Optional: reference to the full KleisliProgramCall Python object.
    /// Enables rich introspection (args, kwargs, kleisli_source) via GIL.
    /// None for non-KPC programs or when metadata is extracted from Rust-side only.
    pub program_call: Option<Py<PyAny>>,
}

/// Callback type stored in VM.callbacks table.
/// Consumed (removed) when executed.
pub type Callback = Box<dyn FnOnce(Value, &mut VM) -> Mode + Send>;
```

### Segment

```rust
/// Segment kind - distinguishes prompt boundaries from normal segments.
#[derive(Debug, Clone)]
pub enum SegmentKind {
    /// Normal segment (user code, handler execution)
    Normal,
    /// Prompt boundary segment (created by WithHandler)
    PromptBoundary {
        /// Which handler this prompt delimits
        handled_marker: Marker,
    },
}

/// Delimited continuation frame.
/// 
/// Represents a continuation delimited by a prompt (marker).
/// Frames are mutable during execution; captured via Arc snapshot.
#[derive(Debug)]
pub struct Segment {
    /// Handler identity this segment belongs to
    pub marker: Marker,
    
    /// Frames in this segment (stack, top = LAST index for O(1) pop)
    pub frames: Vec<Frame>,
    
    /// Caller link - who to return value to
    pub caller: Option<SegmentId>,
    
    /// Evidence vector - handlers in scope [innermost, ..., outermost]
    pub scope_chain: Vec<Marker>,
    
    /// Segment kind (Normal or PromptBoundary)
    pub kind: SegmentKind,
}

impl Segment {
    pub fn new(marker: Marker, caller: Option<SegmentId>, scope_chain: Vec<Marker>) -> Self {
        Segment {
            marker,
            frames: Vec::new(),
            caller,
            scope_chain,
            kind: SegmentKind::Normal,
        }
    }
    
    pub fn new_prompt(
        marker: Marker, 
        caller: Option<SegmentId>, 
        scope_chain: Vec<Marker>,
        handled_marker: Marker,
    ) -> Self {
        Segment {
            marker,
            frames: Vec::new(),
            caller,
            scope_chain,
            kind: SegmentKind::PromptBoundary { handled_marker },
        }
    }
    
    /// Push a frame (O(1) - adds to end)
    pub fn push_frame(&mut self, frame: Frame) {
        self.frames.push(frame);
    }
    
    /// Pop a frame (O(1) - removes from end)
    pub fn pop_frame(&mut self) -> Option<Frame> {
        self.frames.pop()
    }
    
    pub fn is_prompt_boundary(&self) -> bool {
        matches!(self.kind, SegmentKind::PromptBoundary { .. })
    }
    
    pub fn handled_marker(&self) -> Option<Marker> {
        match &self.kind {
            SegmentKind::PromptBoundary { handled_marker } => Some(*handled_marker),
            SegmentKind::Normal => None,
        }
    }
}
```

### Continuation (with Snapshot)

```rust
/// Captured or created continuation (subject to one-shot check).
/// 
/// Two kinds:
/// - Captured (started=true): frames_snapshot/scope_chain/marker/dispatch_id are valid
/// - Created (started=false): program/handlers are valid; frames_snapshot is empty
#[derive(Debug, Clone)]
pub struct Continuation {
    /// Unique identifier for one-shot tracking
    pub cont_id: ContId,
    
    /// Original segment this was captured from (for debugging/reference).
    /// Meaningful only when started=true.
    pub segment_id: SegmentId,
    
    /// Frozen frames at capture time (captured only)
    pub frames_snapshot: Arc<Vec<Frame>>,
    
    /// Frozen scope_chain at capture time (captured only)
    pub scope_chain: Arc<Vec<Marker>>,
    
    /// Handler marker this continuation belongs to (captured only).
    /// 
    /// SEMANTICS: This is the innermost handler at capture time (scope_chain[0]).
    /// Used primarily for debugging/tracing. The authoritative handler info
    /// is in scope_chain, not marker alone.
    /// 
    /// When Resume materializes, new segment gets marker = k.marker,
    /// but scope_chain is what actually determines which handlers are in scope.
    pub marker: Marker,
    
    /// Which dispatch created this (for completion detection).
    /// RULE: Only callsite continuations (k_user) have Some here.
    /// Handler-local and scheduler continuations have None.
    pub dispatch_id: Option<DispatchId>,
    
    /// Whether this continuation is already started.
    /// started=true  => captured continuation
    /// started=false => created (unstarted) continuation
    pub started: bool,
    
    /// Program object to start when started=false (ProgramBase: KleisliProgramCall or EffectBase).
    pub program: Option<Py<PyAny>>,
    
    /// Handlers to install when started=false (innermost first).
    pub handlers: Vec<Handler>,
}

impl Continuation {
    /// Capture a continuation from a segment.
    pub fn capture(
        segment: &Segment, 
        segment_id: SegmentId,
        dispatch_id: Option<DispatchId>,
    ) -> Self {
        Continuation {
            cont_id: ContId::fresh(),
            segment_id,
            frames_snapshot: Arc::new(segment.frames.clone()),
            scope_chain: Arc::new(segment.scope_chain.clone()),
            marker: segment.marker,
            dispatch_id,
            started: true,
            program: None,
            handlers: Vec::new(),
        }
    }
    
    /// Create an unstarted continuation from a program and handlers.
    pub fn create(program: Py<PyAny>, handlers: Vec<Handler>) -> Self {
        Continuation {
            cont_id: ContId::fresh(),
            segment_id: SegmentId(0),  // unused when started=false
            frames_snapshot: Arc::new(Vec::new()),
            scope_chain: Arc::new(Vec::new()),
            marker: Marker(0),  // ignored when started=false
            dispatch_id: None,
            started: false,
            program: Some(program),
            handlers,
        }
    }
}
```

### DispatchContext

```rust
/// Tracks state of a specific effect dispatch.
#[derive(Debug, Clone)]
pub struct DispatchContext {
    /// Unique identifier
    pub dispatch_id: DispatchId,
    
    /// The effect being dispatched
    pub effect: Effect,
    
    /// Snapshot of handler markers [innermost, ..., outermost]
    pub handler_chain: Vec<Marker>,
    
    /// Current position (0 = innermost)
    pub handler_idx: usize,
    
    /// Callsite continuation (for completion detection and Delegate)
    pub k_user: Continuation,
    
    /// Prompt boundary for the root handler of this dispatch.
    /// Used to detect handler return that abandons the callsite.
    pub prompt_seg_id: SegmentId,
    
    /// Marked true when callsite is resolved (Resume/Transfer/Return)
    pub completed: bool,
}
```

### Value (Python-Rust Interop)

```rust
/// A value that can flow through the VM.
/// 
/// Can be Rust-native, Python objects, or VM-level objects (Continuation/Handlers).
#[derive(Debug, Clone)]
pub enum Value {
    /// Python object (GIL-independent)
    Python(Py<PyAny>),

    /// Captured or created continuation
    Continuation(Continuation),

    /// Handler list (innermost first)
    Handlers(Vec<Handler>),

    /// Task handle (scheduler)
    Task(TaskHandle),

    /// Promise handle (scheduler)
    Promise(PromiseHandle),

    /// External promise handle (scheduler)
    ExternalPromise(ExternalPromise),
    
    /// Rust unit (for primitives that don't return meaningful values)
    Unit,
    
    /// Rust integer (optimization for common case)
    Int(i64),
    
    /// Rust string (optimization for common case)
    String(String),
    
    /// Rust boolean
    Bool(bool),
    
    /// None/null
    None,
    
    /// [D8] Call stack metadata (returned by GetCallStack)
    CallStack(Vec<CallMetadata>),
    
    /// [D11] List of values (returned by Gather/try_collect)
    List(Vec<Value>),
}

impl Value {
    /// Convert to Python object (requires GIL)
    pub fn to_pyobject<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        match self {
            Value::Python(obj) => Ok(obj.bind(py).clone()),
            Value::Continuation(k) => k.to_pyobject(py),
            Value::Handlers(handlers) => {
                let py_list = PyList::empty(py);
                for h in handlers {
                    py_list.append(h.to_pyobject(py)?)?;
                }
                Ok(py_list.into_any())
            }
            Value::Task(handle) => handle.to_pyobject(py),
            Value::Promise(handle) => handle.to_pyobject(py),
            Value::ExternalPromise(handle) => handle.to_pyobject(py),
            Value::Unit => Ok(py.None().into_bound(py)),
            Value::Int(i) => Ok(i.into_pyobject(py)?.into_any()),
            Value::String(s) => Ok(s.into_pyobject(py)?.into_any()),
            Value::Bool(b) => Ok(b.into_pyobject(py)?.into_any()),
            Value::None => Ok(py.None().into_bound(py)),
        }
    }
    
    /// Create from Python object (requires GIL)
    pub fn from_pyobject(obj: &Bound<'_, PyAny>) -> Self {
        // Check None first
        if obj.is_none() {
            return Value::None;
        }
        // Scheduler handle wrappers are left as Value::Python; effect extraction
        // is responsible for decoding them when needed.
        // Check bool before int (bool is subclass of int in Python)
        if let Ok(b) = obj.extract::<bool>() {
            return Value::Bool(b);
        }
        if let Ok(i) = obj.extract::<i64>() {
            return Value::Int(i);
        }
        if let Ok(s) = obj.extract::<String>() {
            return Value::String(s);
        }
        Value::Python(obj.clone().unbind())
    }
}
```

### Effect (Tagged Union)

```rust
/// An effect that can be yielded by user code.
///
/// ALL effects go through dispatch. Standard effects (Get, Put, Ask, Tell)
/// are handled by the corresponding RustProgram handlers when installed.
#[derive(Debug, Clone)]
pub enum Effect {
    // === Standard effects (handled by StateHandlerFactory, ReaderHandlerFactory, WriterHandlerFactory) ===
    
    /// Get(key) -> value (State effect)
    Get { key: String },
    
    /// Put(key, value) -> () (State effect)
    Put { key: String, value: Value },
    
    /// Modify(key, f) -> old_value (State effect)
    Modify { key: String, modifier: Py<PyAny> },
    
    /// Ask(key) -> value (Reader effect)
    Ask { key: String },
    
    /// Tell(message) -> () (Writer effect)
    Tell { message: Value },
    
    // === Built-in scheduler effects (Rust-native, but open to Python handlers) ===
    Scheduler(SchedulerEffect),
    
    // === User-defined effects ===
    
    /// Any Python effect object (handled by Python handlers)
    Python(Py<PyAny>),
}

impl Effect {
    /// Get the effect type name (for handler matching).
    pub fn type_name(&self) -> &'static str {
        match self {
            Effect::Get { .. } => "Get",
            Effect::Put { .. } => "Put",
            Effect::Modify { .. } => "Modify",
            Effect::Ask { .. } => "Ask",
            Effect::Tell { .. } => "Tell",
            Effect::Scheduler(_) => "Scheduler",
            Effect::Python(_) => "Python",
        }
    }
    
    /// Check if this is a standard effect (state/reader/writer only).
    /// NOTE: This does NOT mean bypass - all effects still go through dispatch.
    pub fn is_standard(&self) -> bool {
        matches!(
            self,
            Effect::Get { .. }
                | Effect::Put { .. }
                | Effect::Modify { .. }
                | Effect::Ask { .. }
                | Effect::Tell { .. }
        )
    }
}
```

### Python FFI Wrappers (Effect, Continuation, Handler) [R8-C]

Effect, Continuation, and Handler are exposed to Python as PyO3 classes.
These conversions require the GIL and are performed by the driver.

Control primitives and the continuation handle are Rust `#[pyclass]` types
exposed to Python (see ADR-9):

```rust
/// Opaque continuation handle passed to Python handlers. [R8-C]
/// Python code can pass K around but cannot inspect its internals.
#[pyclass]
pub struct K {
    // Internal: cont_id, looked up in VM continuation registry
    cont_id: ContId,
}

/// Composition primitive — usable in any Program. [R8-C]
#[pyclass]
pub struct WithHandler {
    #[pyo3(get)] pub handler: PyObject,
    #[pyo3(get)] pub expr: PyObject,
}

/// Dispatch primitive — handler-only, during effect handling. [R8-C]
#[pyclass]
pub struct Resume {
    #[pyo3(get)] pub continuation: PyObject,  // K instance
    #[pyo3(get)] pub value: PyObject,
}

/// Dispatch primitive — handler-only. [R8-C]
#[pyclass]
pub struct Delegate {
    #[pyo3(get)] pub effect: Option<PyObject>,  // None = use current dispatch effect
}

/// Dispatch primitive — handler-only, one-shot. [R8-C]
#[pyclass]
pub struct Transfer {
    #[pyo3(get)] pub continuation: PyObject,  // K instance
    #[pyo3(get)] pub value: PyObject,
}
```

FFI conversions for VM-internal types:

```rust
impl Effect {
    /// Convert to Python object (driver only, requires GIL).
    /// Standard effects map to the Python effect classes (Get/Put/Modify/Ask/Tell).
    /// Scheduler effects map to Python scheduler classes.
    /// Effect::Python returns the wrapped object.
    pub fn to_pyobject<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>>;
}

impl Continuation {
    /// Convert to Python object (driver only, requires GIL).
    /// Returns a K instance (opaque handle).
    pub fn to_pyobject<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>>;

    /// Convert from Python object (driver only, requires GIL).
    /// Accepts K instances, extracts cont_id.
    pub fn from_pyobject(obj: &Bound<'_, PyAny>) -> PyResult<Self>;
}

impl Handler {
    /// Convert to Python object (driver only, requires GIL). [R8-D]
    /// Returns the py_identity stored in HandlerEntry — preserving the
    /// original Python object the user passed to run() or WithHandler.
    pub fn to_pyobject<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>>;

    /// Convert from Python object (driver only, requires GIL).
    /// Recognizes Rust sentinel objects (state/reader/writer/scheduler)
    /// and wraps them as Handler::RustProgram. All others become Handler::Python.
    pub fn from_pyobject(obj: &Bound<'_, PyAny>) -> PyResult<Self>;
}

// Scheduler handle wrappers (built-in).
impl TaskHandle {
    /// Convert to Python object (driver only, requires GIL).
    pub fn to_pyobject<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>>;
}

impl PromiseHandle {
    /// Convert to Python object (driver only, requires GIL).
    pub fn to_pyobject<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>>;
}

impl ExternalPromise {
    /// Convert to Python object (driver only, requires GIL).
    pub fn to_pyobject<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>>;
}
```

### Handler (RustProgram + Python) [R8-G]

```rust
/// A handler that can process effects.
///
/// Handlers are installed via WithHandler and matched during dispatch.
/// Two implementation strategies, one dispatch protocol.
#[derive(Debug, Clone)]
pub enum Handler {
    /// Rust-native handler (generator-like protocol).
    /// Used by state, reader, writer, scheduler, and any custom Rust handler.
    RustProgram(RustProgramHandlerRef),

    /// Python handler function.
    /// Signature: def handler(effect, k) -> Program[Any]
    Python(Py<PyAny>),
}

/// Shared reference to a Rust program handler factory.
pub type RustProgramHandlerRef = Arc<dyn RustProgramHandler + Send + Sync>;

/// Shared reference to a running Rust handler program (cloneable for continuations).
pub type RustProgramRef = Arc<Mutex<Box<dyn RustHandlerProgram + Send>>>;

/// Result of stepping a Rust handler program.
pub enum RustProgramStep {
    /// Yield a DoCtrl / effect / program
    Yield(Yielded),
    /// Return a value (like generator return)
    Return(Value),
    /// Throw an exception into the VM
    Throw(PyException),
    /// [R8-H] Need to call a Python function (e.g., Modify calling modifier).
    /// The program is suspended; result feeds back via resume().
    NeedsPython(PythonCall),
}

/// A Rust handler program instance (generator-like).
///
/// start/resume/throw mirror Python generator protocol but run in Rust.
pub trait RustHandlerProgram {
    fn start(&mut self, effect: Effect, k: Continuation, store: &mut RustStore)
        -> RustProgramStep;
    fn resume(&mut self, value: Value, store: &mut RustStore) -> RustProgramStep;
    fn throw(&mut self, exc: PyException, store: &mut RustStore) -> RustProgramStep;
}

/// Factory for Rust handler programs.
///
/// Each dispatch creates a fresh RustHandlerProgram instance.
pub trait RustProgramHandler {
    fn can_handle(&self, effect: &Effect) -> bool;
    fn create_program(&self) -> RustProgramRef;
}

impl Handler {
    /// Check if this handler can handle the given effect.
    pub fn can_handle(&self, effect: &Effect) -> bool {
        match self {
            Handler::RustProgram(handler) => handler.can_handle(effect),
            Handler::Python(_) => {
                // Python handlers are considered capable of handling any effect.
                // They yield Delegate() for effects they don't handle.
                true
            }
        }
    }
}
```

### Handler Entry with Identity Preservation [R8-D]

```rust
/// Entry in the handler table, linking a Handler to its prompt segment
/// and preserving the original Python object for GetHandlers.
#[derive(Debug, Clone)]
pub struct HandlerEntry {
    pub handler: Handler,
    pub prompt_seg_id: SegmentId,
    /// Original Python object passed by the user.
    /// Returned by GetHandlers to preserve id()-level identity.
    pub py_identity: Option<Py<PyAny>>,
}
```

### Standard Handlers as RustProgramHandler [R8-H] [R8-I]

The standard handlers (state, reader, writer) implement the same
`RustProgramHandler` trait as the scheduler. No separate `StdlibHandler`
enum, no `HandlerAction`, no `NeedsPython` special case.

```rust
/// State handler factory. Handles Get, Put, Modify.
/// Backed by RustStore.state.
#[derive(Debug, Clone)]
pub struct StateHandlerFactory;

impl RustProgramHandler for StateHandlerFactory {
    fn can_handle(&self, effect: &Effect) -> bool {
        matches!(effect, Effect::Get { .. } | Effect::Put { .. } | Effect::Modify { .. })
    }
    fn create_program(&self) -> RustProgramRef {
        Arc::new(Mutex::new(Box::new(StateHandlerProgram::new())))
    }
}

/// State handler program instance.
///
/// Get/Put: start() handles immediately, yields Resume(k, value).
/// Modify:  start() reads old value, yields the modifier call as a
///          sub-program; resume() receives new value, stores it,
///          yields Resume(k, old_value).
struct StateHandlerProgram { /* state machine fields */ }

impl RustHandlerProgram for StateHandlerProgram {
    fn start(&mut self, effect: Effect, k: Continuation, store: &mut RustStore)
        -> RustProgramStep
    {
        match effect {
            Effect::Get { key } => {
                let value = store.get(&key).cloned().unwrap_or(Value::None);
                RustProgramStep::Yield(Yielded::DoCtrl(
                    DoCtrl::Resume { continuation: k, value }
                ))
            }
            Effect::Put { key, value } => {
                store.put(key, value);
                RustProgramStep::Yield(Yielded::DoCtrl(
                    DoCtrl::Resume { continuation: k, value: Value::Unit }
                ))
            }
            Effect::Modify { key, modifier } => {
                // Save state for resume()
                self.pending_key = Some(key.clone());
                self.pending_k = Some(k);
                let old_value = store.get(&key).cloned().unwrap_or(Value::None);
                self.pending_old_value = Some(old_value.clone());
                // [R8-H] Need Python call: modifier(old_value).
                // VM suspends handler, calls Python, then resume() with result.
                RustProgramStep::NeedsPython(PythonCall::CallFunc {
                    func: modifier,
                    args: vec![old_value],
                })
            }
            _ => RustProgramStep::Yield(Yielded::DoCtrl(
                DoCtrl::Delegate { effect }
            )),
        }
    }

    fn resume(&mut self, value: Value, store: &mut RustStore) -> RustProgramStep {
        // Called after Modify's modifier(old_value) returns
        let key = self.pending_key.take().unwrap();
        let k = self.pending_k.take().unwrap();
        let old_value = self.pending_old_value.take().unwrap();
        store.put(key, value);  // value = new_value from modifier
        RustProgramStep::Yield(Yielded::DoCtrl(
            DoCtrl::Resume { continuation: k, value: old_value }
        ))
    }

    fn throw(&mut self, exc: PyException, _store: &mut RustStore) -> RustProgramStep {
        RustProgramStep::Throw(exc)
    }
}

/// Reader handler factory. Handles Ask.
/// Backed by RustStore.env.
#[derive(Debug, Clone)]
pub struct ReaderHandlerFactory;

impl RustProgramHandler for ReaderHandlerFactory {
    fn can_handle(&self, effect: &Effect) -> bool {
        matches!(effect, Effect::Ask { .. })
    }
    fn create_program(&self) -> RustProgramRef {
        Arc::new(Mutex::new(Box::new(ReaderHandlerProgram)))
    }
}

struct ReaderHandlerProgram;

impl RustHandlerProgram for ReaderHandlerProgram {
    fn start(&mut self, effect: Effect, k: Continuation, store: &mut RustStore)
        -> RustProgramStep
    {
        match effect {
            Effect::Ask { key } => {
                let value = store.ask(&key).cloned().unwrap_or(Value::None);
                RustProgramStep::Yield(Yielded::DoCtrl(
                    DoCtrl::Resume { continuation: k, value }
                ))
            }
            _ => RustProgramStep::Yield(Yielded::DoCtrl(
                DoCtrl::Delegate { effect }
            )),
        }
    }
    fn resume(&mut self, _: Value, _: &mut RustStore) -> RustProgramStep {
        unreachable!("ReaderHandler never yields mid-handling")
    }
    fn throw(&mut self, exc: PyException, _: &mut RustStore) -> RustProgramStep {
        RustProgramStep::Throw(exc)
    }
}

/// Writer handler factory. Handles Tell.
/// Backed by RustStore.log.
#[derive(Debug, Clone)]
pub struct WriterHandlerFactory;

impl RustProgramHandler for WriterHandlerFactory {
    fn can_handle(&self, effect: &Effect) -> bool {
        matches!(effect, Effect::Tell { .. })
    }
    fn create_program(&self) -> RustProgramRef {
        Arc::new(Mutex::new(Box::new(WriterHandlerProgram)))
    }
}

struct WriterHandlerProgram;

impl RustHandlerProgram for WriterHandlerProgram {
    fn start(&mut self, effect: Effect, k: Continuation, store: &mut RustStore)
        -> RustProgramStep
    {
        match effect {
            Effect::Tell { message } => {
                store.tell(message);
                RustProgramStep::Yield(Yielded::DoCtrl(
                    DoCtrl::Resume { continuation: k, value: Value::Unit }
                ))
            }
            _ => RustProgramStep::Yield(Yielded::DoCtrl(
                DoCtrl::Delegate { effect }
            )),
        }
    }
    fn resume(&mut self, _: Value, _: &mut RustStore) -> RustProgramStep {
        unreachable!("WriterHandler never yields mid-handling")
    }
    fn throw(&mut self, exc: PyException, _: &mut RustStore) -> RustProgramStep {
        RustProgramStep::Throw(exc)
    }
}
```

### Built-in Scheduler Handler (Rust, Explicit Installation)

This is a built-in Rust program handler that implements cooperative scheduling for
Spawn/Race/Gather/Task/Promise/ExternalPromise using **Transfer-only** semantics.
It is **not** auto-installed; users must install it explicitly via `WithHandler`.
It can be replaced by custom Python or Rust handlers.
The VM ships this as the default scheduler implementation (explicit installation only).

Assumptions for the reference implementation:
- Spawn effects carry the callsite handler chain (`handlers: Vec<Handler>`) so the
  scheduler can create child continuations without calling `GetHandlers` in Rust.
- Spawn includes `store_mode` to opt into RustStore isolation.
- Task/Promise handles are opaque IDs exposed to Python via `Value` variants and
  PyO3 wrappers.
- Scheduling decisions **always** yield `DoCtrl::Transfer` to avoid stack growth.

Integration note:
- Driver `extract_effect` maps built-in Python scheduler classes
  (Spawn/Gather/Race/Promise/ExternalPromise/...) to `Effect::Scheduler`.
  Spawn defaults `store_mode` to `StoreMode::Shared` if not specified.
- `Effect::to_pyobject` maps `Effect::Scheduler` back to Python scheduler objects
  so Python handlers can intercept or override scheduling.

```rust
#[derive(Debug, Clone)]
pub enum SchedulerEffect {
    Spawn {
        program: Py<PyAny>,
        handlers: Vec<Handler>,
        /// Default is StoreMode::Shared if not specified by the Python effect.
        store_mode: StoreMode,
    },
    Gather {
        items: Vec<Waitable>,
    },
    Race {
        items: Vec<Waitable>,
    },
    CreatePromise,
    CompletePromise { promise: PromiseId, value: Value },
    FailPromise { promise: PromiseId, error: PyException },
    CreateExternalPromise,
    TaskCompleted { task: TaskId, result: Result<Value, PyException> },
    /// [D6] Raw Python scheduler effect — classified as Effect::Scheduler for dispatch,
    /// field extraction deferred to handler start().
    PythonSchedulerEffect(Py<PyAny>),
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct TaskId(u64);

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct PromiseId(u64);

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Waitable {
    Task(TaskId),
    Promise(PromiseId),
    ExternalPromise(PromiseId),
}

#[derive(Clone, Copy, Debug)]
pub enum StoreMode {
    Shared,
    /// Isolated RustStore per task (PyStore remains shared).
    Isolated { merge: StoreMergePolicy },
}

#[derive(Clone, Copy, Debug)]
pub enum StoreMergePolicy {
    /// Merge only logs (append in Gather items order). State/env changes are not merged.
    LogsOnly,
    // Future: add policies for metrics/caches, or custom merges.
}

#[derive(Debug, Clone)]
pub enum TaskStore {
    Shared,
    Isolated { store: RustStore, merge: StoreMergePolicy },
}

#[derive(Debug)]
enum TaskState {
    Pending { cont: Continuation, store: TaskStore },
    Done { result: Result<Value, PyException>, store: TaskStore },
}

#[derive(Debug)]
enum PromiseState {
    Pending,
    Done(Result<Value, PyException>),
}

#[derive(Clone, Copy, Debug)]
pub struct TaskHandle {
    pub id: TaskId,
}

#[derive(Clone, Copy, Debug)]
pub struct PromiseHandle {
    pub id: PromiseId,
}

#[derive(Clone, Copy, Debug)]
pub struct ExternalPromise {
    pub id: PromiseId,
}

pub struct SchedulerState {
    ready: VecDeque<TaskId>,
    tasks: HashMap<TaskId, TaskState>,
    promises: HashMap<PromiseId, PromiseState>,
    waiters: HashMap<Waitable, Vec<Continuation>>,
    next_task: u64,
    next_promise: u64,
    current_task: Option<TaskId>,
}

impl SchedulerState {
    fn transfer_next_or(&mut self, k: Continuation, store: &mut RustStore) -> RustProgramStep {
        if let Some(task_id) = self.ready.pop_front() {
            return self.transfer_task(task_id, store);
        }
        RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Transfer {
            continuation: k,
            value: Value::Unit,
        }))
    }

    fn transfer_task(&mut self, task_id: TaskId, store: &mut RustStore) -> RustProgramStep {
        // Save current store into current task (if isolated) before switching.
        if let Some(current_id) = self.current_task {
            self.save_task_store(current_id, store);
        }
        self.load_task_store(task_id, store);
        self.current_task = Some(task_id);
        // transfer_task should return Transfer to the task continuation.
        // (implementation omitted in this spec snippet)
        RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Transfer {
            continuation: self.task_cont(task_id),
            value: Value::Unit,
        }))
    }

    fn save_task_store(&mut self, task_id: TaskId, store: &RustStore) {
        let Some(state) = self.tasks.get_mut(&task_id) else { return; };
        match state {
            TaskState::Pending { store: TaskStore::Isolated { store: task_store, .. }, .. }
            | TaskState::Done { store: TaskStore::Isolated { store: task_store, .. }, .. } => {
                *task_store = store.clone();
            }
            _ => {}
        }
    }

    fn load_task_store(&mut self, task_id: TaskId, store: &mut RustStore) {
        let Some(state) = self.tasks.get(&task_id) else { return; };
        let task_store = match state {
            TaskState::Pending { store, .. } => store,
            TaskState::Done { store, .. } => store,
        };
        if let TaskStore::Isolated { store: task_store, .. } = task_store {
            *store = task_store.clone();
        }
    }

    fn merge_task_logs(&mut self, task_id: TaskId, store: &mut RustStore) {
        let Some(state) = self.tasks.get(&task_id) else { return; };
        let task_store = match state {
            TaskState::Pending { store, .. } => store,
            TaskState::Done { store, .. } => store,
        };
        if let TaskStore::Isolated { store: task_store, merge: StoreMergePolicy::LogsOnly } =
            task_store
        {
            store.log.extend(task_store.log.iter().cloned());
        }
    }

    fn mark_task_done(&mut self, task_id: TaskId, result: Result<Value, PyException>) {
        let Some(state) = self.tasks.get(&task_id) else { return; };
        let task_store = match state {
            TaskState::Pending { store, .. } => store.clone(),
            TaskState::Done { store, .. } => store.clone(),
        };
        self.tasks.insert(task_id, TaskState::Done { result, store: task_store });
    }

    fn merge_gather_logs(&mut self, items: &[Waitable], store: &mut RustStore) {
        for item in items {
            if let Waitable::Task(task_id) = item {
                self.merge_task_logs(*task_id, store);
            }
        }
    }

    fn task_cont(&self, task_id: TaskId) -> Continuation {
        // Retrieve task continuation from TaskState (pending only).
        match self.tasks.get(&task_id) {
            Some(TaskState::Pending { cont, .. }) => cont.clone(),
            _ => panic!("task continuation not available"),
        }
    }

    // helper methods omitted: try_collect, try_race, wait_on_all, wait_on_any, wake_waiters
}

#[derive(Clone)]
pub struct SchedulerHandler {
    state: Arc<Mutex<SchedulerState>>,
}

impl SchedulerHandler {
    pub fn new() -> Self {
        SchedulerHandler {
            state: Arc::new(Mutex::new(SchedulerState {
                ready: VecDeque::new(),
                tasks: HashMap::new(),
                promises: HashMap::new(),
                waiters: HashMap::new(),
                next_task: 0,
                next_promise: 0,
                current_task: None,
            })),
        }
    }
}

impl RustProgramHandler for SchedulerHandler {
    fn can_handle(&self, effect: &Effect) -> bool {
        matches!(effect, Effect::Scheduler(_))
    }

    fn create_program(&self) -> RustProgramRef {
        Arc::new(Mutex::new(Box::new(SchedulerProgram::new(
            self.state.clone(),
        ))))
    }
}

#[derive(Debug)]
enum SchedulerPhase {
    Idle,
    SpawnPending {
        k_user: Continuation,
        store_mode: StoreMode,
        store_snapshot: Option<RustStore>,
    },
}

pub struct SchedulerProgram {
    state: Arc<Mutex<SchedulerState>>,
    phase: SchedulerPhase,
}

impl SchedulerProgram {
    pub fn new(state: Arc<Mutex<SchedulerState>>) -> Self {
        SchedulerProgram {
            state,
            phase: SchedulerPhase::Idle,
        }
    }
}

impl RustHandlerProgram for SchedulerProgram {
    fn start(
        &mut self,
        effect: Effect,
        k_user: Continuation,
        store: &mut RustStore,
    ) -> RustProgramStep {
        let Effect::Scheduler(effect) = effect else {
            return RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Delegate {
                effect,
            }));
        };
        match effect {
            SchedulerEffect::Spawn { program, handlers, store_mode } => {
                let store_snapshot = match store_mode {
                    StoreMode::Shared => None,
                    StoreMode::Isolated { .. } => Some(store.clone()),
                };
                self.phase = SchedulerPhase::SpawnPending {
                    k_user,
                    store_mode,
                    store_snapshot,
                };
                RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::CreateContinuation {
                    program,
                    handlers,
                }))
            }
            SchedulerEffect::TaskCompleted { task, result } => {
                let mut state = self.state.lock().expect("Scheduler lock poisoned");
                state.save_task_store(task, store);
                state.mark_task_done(task, result);
                state.wake_waiters(Waitable::Task(task));
                state.transfer_next_or(k_user, store)
            }
            SchedulerEffect::Gather { items } => {
                let mut state = self.state.lock().expect("Scheduler lock poisoned");
                if let Some(results) = state.try_collect(&items) {
                    state.merge_gather_logs(&items, store);
                    return RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Transfer {
                        continuation: k_user,
                        value: results,
                    }));
                }
                state.wait_on_all(&items, k_user);
                state.transfer_next_or(k_user, store)
            }
            SchedulerEffect::Race { items } => {
                let mut state = self.state.lock().expect("Scheduler lock poisoned");
                if let Some(result) = state.try_race(&items) {
                    return RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Transfer {
                        continuation: k_user,
                        value: result,
                    }));
                }
                state.wait_on_any(&items, k_user);
                state.transfer_next_or(k_user, store)
            }
            SchedulerEffect::CreatePromise => {
                let mut state = self.state.lock().expect("Scheduler lock poisoned");
                let pid = PromiseId(state.next_promise);
                state.next_promise += 1;
                state.promises.insert(pid, PromiseState::Pending);
                RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Transfer {
                    continuation: k_user,
                    value: Value::Promise(PromiseHandle { id: pid }),
                }))
            }
            SchedulerEffect::CompletePromise { promise, value } => {
                let mut state = self.state.lock().expect("Scheduler lock poisoned");
                state.promises.insert(promise, PromiseState::Done(Ok(value)));
                state.wake_waiters(Waitable::Promise(promise));
                state.transfer_next_or(k_user, store)
            }
            SchedulerEffect::FailPromise { promise, error } => {
                let mut state = self.state.lock().expect("Scheduler lock poisoned");
                state.promises.insert(promise, PromiseState::Done(Err(error)));
                state.wake_waiters(Waitable::Promise(promise));
                state.transfer_next_or(k_user, store)
            }
            SchedulerEffect::CreateExternalPromise => {
                let mut state = self.state.lock().expect("Scheduler lock poisoned");
                let pid = PromiseId(state.next_promise);
                state.next_promise += 1;
                state.promises.insert(pid, PromiseState::Pending);
                RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Transfer {
                    continuation: k_user,
                    value: Value::ExternalPromise(ExternalPromise { id: pid }),
                }))
            }
        }
    }

    fn resume(&mut self, value: Value, _store: &mut RustStore) -> RustProgramStep {
        match std::mem::replace(&mut self.phase, SchedulerPhase::Idle) {
            SchedulerPhase::SpawnPending { k_user, store_mode, store_snapshot } => {
                let cont = match value {
                    Value::Continuation(c) => c,
                    _ => {
                        return RustProgramStep::Throw(PyException::type_error(
                            "expected continuation",
                        ));
                    }
                };
                let task_store = match store_mode {
                    StoreMode::Shared => TaskStore::Shared,
                    StoreMode::Isolated { merge } => {
                        let Some(store_snapshot) = store_snapshot else {
                            return RustProgramStep::Throw(PyException::runtime_error(
                                "missing store snapshot for isolated task",
                            ));
                        };
                        TaskStore::Isolated {
                            store: store_snapshot,
                            merge,
                        }
                    }
                };
                let mut state = self.state.lock().expect("Scheduler lock poisoned");
                let task_id = TaskId(state.next_task);
                state.next_task += 1;
                state.tasks.insert(
                    task_id,
                    TaskState::Pending { cont, store: task_store },
                );
                state.ready.push_back(task_id);
                RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Transfer {
                    continuation: k_user,
                    value: Value::Task(TaskHandle { id: task_id }),
                }))
            }
            SchedulerPhase::Idle => RustProgramStep::Throw(PyException::runtime_error(
                "Unexpected resume in scheduler",
            )),
        }
    }

    fn throw(&mut self, exc: PyException, _store: &mut RustStore) -> RustProgramStep {
        RustProgramStep::Throw(exc)
    }
}
```

**Notes**:
- This handler assumes `Value::Task/Promise/ExternalPromise` variants with
  `Value::to_pyobject` mappings to PyO3 wrapper classes.
- Stores are shared by default (RustStore + PyStore). Spawn can request
  `StoreMode::Isolated` to give the child a RustStore snapshot.
- PyStore remains shared unless a GIL-aware copy/merge path is added.
- On Gather, logs from isolated tasks are merged into the current RustStore
  according to `StoreMergePolicy` (default: append logs).
- Child programs should be wrapped to emit `TaskCompleted` on return/throw so the
  scheduler can record results and wake waiters.

### Python API for Scheduler (Explicit)

Users install the built-in scheduler explicitly:

```python
vm = doeff.VM()
scheduler = vm.scheduler()  # PyRustProgramHandler
prog = with_handler(scheduler, user_program())
result = vm.run(prog)
```

Spawn can request `store_mode=Isolated` (RustStore snapshot). Gather merges logs
back into the current RustStore according to `StoreMergePolicy`.
Standard handlers can be layered inside or outside the scheduler with `with_handler`.

### Python API for Standard Handlers [R8-H]

Users install standard handlers via `run()` or `WithHandler` (see SPEC-009):

```python
from doeff import run, WithHandler
from doeff.handlers import state, reader, writer

# Install standard handlers explicitly
result = run(
    user_program(),
    handlers=[state, reader, writer],
    store={"x": 0},
    env={"key": "val"},
)

# Observe state after execution
print(result.raw_store)    # Dict of state key-value pairs

# Users can replace standard handlers with custom ones
@do
def my_persistent_state(effect, k):
    if isinstance(effect, Get):
        value = db.get(effect.key)
        result = yield Resume(k, value)
        return result
    elif isinstance(effect, Put):
        db.put(effect.key, effect.value)
        result = yield Resume(k, None)
        return result
    else:
        yield Delegate()

# Custom handler intercepts state effects instead of standard state handler
result = run(
    user_program(),
    handlers=[my_persistent_state, reader, writer],
    env={"key": "val"},
)
```

### PythonCall and PendingPython (Purpose-Tagged Calls)

**CRITICAL**: When VM returns `NeedsPython`, it must also store `pending_python` 
to know what to do with the result. Different call types have different result handling.

**GIL RULE**: The driver converts Python objects to `Value` before returning
`PyCallOutcome::Value` to the VM. The VM never calls `Value::from_pyobject`.

**ASYNC RULE**: `PythonAsyncSyntaxEscape` maps to `PythonCall::CallAsync` and
`PendingPython::AsyncEscape`. Only async_run may execute CallAsync; sync_run errors.

```rust
/// A pending call into Python code.
/// 
/// IMPORTANT: Generators are NOT callables. This enum correctly
/// distinguishes between calling functions and advancing generators.
#[derive(Debug, Clone)]
pub enum PythonCall {
    /// Start a Program object (ProgramBase: KleisliProgramCall or EffectBase).
    /// Driver calls to_generator() and returns Value::Python(generator).
    StartProgram {
        program: Py<PyAny>,
    },
    
    /// Call a Python function for pure computation (non-program).
    CallFunc {
        func: Py<PyAny>,
        args: Vec<Value>,
    },
    
    /// Call a Python function that returns an awaitable (async_run only).
    /// Driver awaits the result and returns PyCallOutcome::Value.
    CallAsync {
        func: Py<PyAny>,
        args: Vec<Value>,
    },
    
    /// Call a Python handler with effect and continuation.
    /// Driver wraps Effect/Continuation into Python objects (PyEffect/PyContinuation).
    /// Handler must return a ProgramBase (KleisliProgramCall or EffectBase);
    /// driver calls to_generator() on it.
    CallHandler {
        handler: Py<PyAny>,
        effect: Effect,
        continuation: Continuation,
    },
    
    /// Start a generator (first iteration, equivalent to __next__)
    GenNext {
        gen: Py<PyAny>,
    },
    
    /// Send a value to a running generator
    GenSend {
        gen: Py<PyAny>,
        value: Value,
    },
    
    /// Throw an exception into a generator
    GenThrow {
        gen: Py<PyAny>,
        exc: Py<PyAny>,
    },
}

/// What to do when Python call returns.
/// 
/// INVARIANT: When step() returns NeedsPython, VM.pending_python is set.
/// When receive_python_result() is called, VM uses pending_python to route the result.
#[derive(Debug, Clone)]
pub enum PendingPython {
    /// StartProgram for a Program body - result is Value::Python(generator).
    /// Carries optional CallMetadata to attach to the PythonGenerator frame. [R9-G]
    /// When metadata is Some, the frame was created via DoCtrl::Call.
    /// When metadata is None, the frame was created via Yielded::Program (legacy).
    StartProgramFrame {
        metadata: Option<CallMetadata>,
    },
    
    /// GenNext/GenSend/GenThrow on a user generator frame
    /// On GenYield: re-push generator with started=true and preserved metadata [R9-C]
    /// On GenReturn/GenError: generator is done, don't re-push
    StepUserGenerator {
        /// The generator being stepped (needed for re-push)
        generator: Py<PyAny>,
        /// CallMetadata from the original frame (preserved across yields) [R9-C]
        metadata: Option<CallMetadata>,
    },
    
    /// CallHandler for Python handler invocation
    /// Result is Value::Python(generator) after converting Program via to_generator()
    /// The resulting generator is treated as a handler program; StopIteration
    /// triggers implicit handler return semantics.
    CallPythonHandler {
        /// Continuation to pass to handler
        k_user: Continuation,
        /// Effect being handled
        effect: Effect,
    },
    
    /// [R8-H] RustProgram handler needs Python callback (e.g., Modify calling modifier function).
    /// The handler's RustHandlerProgram is suspended; result feeds back via resume().
    RustProgramContinuation {
        /// Handler marker (to locate handler in scope_chain)
        marker: Marker,
        /// Continuation from the dispatch context
        k: Continuation,
    },

    /// PythonAsyncSyntaxEscape awaiting (async_run only)
    AsyncEscape,
}

**DoExpr Input Rule**: `StartProgram`, `CallHandler`, and `Yielded::Program`
require a DoThunk value (has `to_generator()`). The driver calls `to_generator()`
to obtain the generator. After SPEC-TYPES-001, KPC becomes an Effect (no
`to_generator()`) and goes through dispatch; only DoThunks pass this rule.
Raw generators are rejected at these entry points; only low-level
`start_with_generator()` accepts raw generators.
```

### Program Frame Re-Push Rule (Python + Rust)

**CRITICAL INVARIANT**: When stepping a Python generator or a Rust handler program
and it yields (not returns/errors), the program frame must be re-pushed to the
current segment.

```
GenNext/GenSend/GenThrow → driver executes → PyCallOutcome::GenYield(yielded)
  ↓
receive_python_result:
  1. Re-push generator as Frame::PythonGenerator { generator, started: true, metadata }
     (metadata is preserved from the original frame — it does not change across yields)
  2. Set mode = HandleYield(yielded)
  
GenReturn/GenError → generator is DONE, do NOT re-push
  ↓
receive_python_result:
  1. Do NOT push any frame (generator consumed)
  2. Set mode = Deliver(value) or Throw(exception)
```

Rust program step:

```
RustProgramStep::Yield(yielded)
  ↓
apply_rust_program_step:
  1. Re-push Frame::RustProgram { program }
  2. Set mode = HandleYield(yielded)

RustProgramStep::Return/Throw → program is DONE, do NOT re-push
  ↓
apply_rust_program_step:
  1. Do NOT push any frame (program consumed)
  2. Set mode = Deliver(value) or Throw(exception)
```

This ensures the program frame exists when we need to send the next value.

**Handler Return Hook**: When pushing a handler program frame (Python or Rust),
the VM also installs a handler-return hook (e.g., a RustReturn callback or a
special frame). When the handler returns (StopIteration/Return), the hook runs
`handle_handler_return(value)` so implicit Return semantics apply. User programs
do not install this hook.

---

## VM State (3-Layer Model)

The VM state is organized into three layers with clear separation of concerns:

| Layer | Name | Contents | Visibility |
|-------|------|----------|------------|
| **1** | `Internals` | dispatch_stack, consumed_ids, segments, callbacks | **NEVER** exposed to users |
| **2** | `RustStore` | state, env, log (standard handler data) | User-observable via `RunResult.raw_store` |
| **3** | `PyStore` | Python dict (optional) | User-owned free zone |

### Design Principles

1. **Internals are sacred**: Control flow structures that could break VM invariants are hidden
2. **RustStore is the source of truth**: Standard handlers read/write here; fast Rust access
3. **PyStore is an escape hatch**: Python handlers can store arbitrary data; VM doesn't read it
4. **No synchronization**: RustStore and PyStore are independent; no mirroring or sync
5. **Continuations don't snapshot S**: State is global (no backtracking by default).
   Stores are shared by default; Spawn may request isolated RustStore with explicit
   merge policies (PyStore remains shared unless a GIL-aware copy path is added).

### Layer 1: Internals (VM-internal, invisible to users)

Layer 1 fields are defined directly in the VM struct (see "VM Struct" below).
They include: `segments`, `free_segments`, `dispatch_stack`, `callbacks`, 
`consumed_cont_ids`, `handlers`.

These structures maintain VM invariants and must NOT be accessible or 
modifiable by user code directly.

### Layer 2: RustStore (user-space, Rust HashMap)

```rust
/// Standard handler state. Rust-native for performance.
///
/// This is the "main memory" for standard effects (Get/Put/Ask/Tell).
/// Python handlers can access via PyO3-exposed read/write APIs.
/// 
/// Key design: Value can hold Py<PyAny>, so Python objects flow through.
/// StoreMode::Isolated requires RustStore to be cloneable.
#[derive(Clone)]
pub struct RustStore {
    /// State for Get/Put/Modify effects
    pub state: HashMap<String, Value>,
    
    /// Environment for Ask/Local effects
    pub env: HashMap<String, Value>,
    
    /// Log for Tell/Listen effects
    pub log: Vec<Value>,
    
    // Future: cache, metrics, etc.
}

impl RustStore {
    pub fn new() -> Self {
        RustStore {
            state: HashMap::new(),
            env: HashMap::new(),
            log: Vec::new(),
        }
    }
    
    // === State operations (used by StateHandlerFactory) ===
    
    pub fn get(&self, key: &str) -> Option<&Value> {
        self.state.get(key)
    }
    
    pub fn put(&mut self, key: String, value: Value) {
        self.state.insert(key, value);
    }
    
    pub fn modify<F>(&mut self, key: &str, f: F) -> Option<Value> 
    where F: FnOnce(&Value) -> Value 
    {
        self.state.get(key).map(|old| {
            let new = f(old);
            let old_clone = old.clone();
            self.state.insert(key.to_string(), new);
            old_clone
        })
    }
    
    // === Environment operations (used by ReaderHandlerFactory) ===
    
    pub fn ask(&self, key: &str) -> Option<&Value> {
        self.env.get(key)
    }
    
    pub fn with_local<F, R>(&mut self, bindings: HashMap<String, Value>, f: F) -> R
    where F: FnOnce(&mut Self) -> R
    {
        let old: HashMap<String, Value> = bindings.keys()
            .filter_map(|k| self.env.get(k).map(|v| (k.clone(), v.clone())))
            .collect();
        
        // Apply new bindings
        for (k, v) in bindings {
            self.env.insert(k, v);
        }
        
        let result = f(self);
        
        // Restore old bindings
        for (k, v) in old {
            self.env.insert(k, v);
        }
        
        result
    }
    
    // === Log operations (used by WriterHandlerFactory) ===
    
    pub fn tell(&mut self, message: Value) {
        self.log.push(message);
    }
    
    pub fn logs(&self) -> &[Value] {
        &self.log
    }
    
    pub fn clear_logs(&mut self) -> Vec<Value> {
        std::mem::take(&mut self.log)
    }
}
```

### Layer 3: PyStore (user-space, Python dict, optional)

```rust
/// Optional Python dict for user-defined handler state.
/// 
/// This is a "free zone" - VM doesn't read it, users can do anything.
/// Use cases:
/// - Python custom handlers storing arbitrary info
/// - Debug/tracing metadata
/// - Prototyping before solidifying Rust key model
/// 
/// NOTE: No synchronization with RustStore. They are independent.
/// PyStore is shared across tasks even when RustStore is isolated.
pub struct PyStore {
    dict: Py<PyDict>,
}

impl PyStore {
    pub fn new(py: Python<'_>) -> Self {
        PyStore {
            dict: PyDict::new(py).unbind(),
        }
    }
    
    /// Get the underlying Python dict (for Python handlers)
    pub fn as_dict<'py>(&self, py: Python<'py>) -> &Bound<'py, PyDict> {
        self.dict.bind(py)
    }
}
```

### VM Struct (Unified Definition)

**Note**: Level 1 and Level 2 are logical subsystems; implementation is a single mode-based VM.

```rust
/// The algebraic effects VM.
/// 
/// Single unified struct combining all three state layers.
/// The step() function is the single execution entry point.
pub struct VM {
    // === Layer 1: Internals (invisible to users) ===
    
    /// Segment arena (owns all segments)
    segments: Vec<Segment>,
    
    /// Free list for segment reuse
    free_segments: Vec<SegmentId>,
    
    /// Dispatch stack (tracks effect dispatch in progress)
    dispatch_stack: Vec<DispatchContext>,
    
    /// Callback table for FnOnce (Frame::RustReturn references these)
    callbacks: SlotMap<CallbackId, Callback>,
    
    /// One-shot tracking for continuations
    consumed_cont_ids: HashSet<ContId>,
    
    /// Handler registry: marker -> HandlerEntry
    /// NOTE: Includes prompt_seg_id to avoid linear search
    handlers: HashMap<Marker, HandlerEntry>,
    
    // === Layer 2: RustStore (user-observable via RunResult.raw_store) ===

    /// Standard handler state (State/Reader/Writer handlers use this)
    pub rust_store: RustStore,
    
    // === Layer 3: PyStore (optional escape hatch) ===
    
    /// User Python dict for custom handler state
    py_store: Option<PyStore>,
    
    // === Execution State ===
    
    /// Current segment being executed
    current_segment: SegmentId,
    
    /// Current execution mode (state machine)
    mode: Mode,
    
    /// Pending Python call context (set when NeedsPython returned).
    /// INVARIANT: Some when step() returned NeedsPython, None otherwise.
    /// Used by receive_python_result() to know what to do with result.
    pending_python: Option<PendingPython>,

    /// Debug configuration (off by default).
    debug: DebugConfig,
    
    /// Monotonic step counter for debug output.
    step_counter: u64,
    
    /// [D7] Registry of all continuations created during execution.
    /// Keyed by ContId, used for one-shot enforcement and cleanup.
    continuation_registry: HashMap<ContId, Continuation>,
}

/// Handler registry entry.
/// 
/// Includes prompt_seg_id to avoid linear search during dispatch.
/// Created by WithHandler, looked up by start_dispatch.
#[derive(Debug, Clone)]
pub struct HandlerEntry {
    /// The handler implementation
    pub handler: Handler,
    
    /// Prompt segment for this handler (set at WithHandler time)
    /// Abandon/return goes here. No search needed.
    pub prompt_seg_id: SegmentId,
    
    /// [D8] Original Python object passed by the user.
    /// Returned by GetHandlers to preserve id()-level identity.
    pub py_identity: Option<Py<PyAny>>,
}
```

### Debug Mode (Step Tracing)

Debug mode prints useful runtime state while stepping. It is **off by default**
and must not call into Python (no GIL usage in debug output).

```rust
#[derive(Debug, Clone)]
pub enum DebugLevel {
    Off,
    Steps,  // One-line summary per step
    Trace,  // Includes handler/dispatch/yield details
}

#[derive(Debug, Clone)]
pub struct DebugConfig {
    pub level: DebugLevel,
    pub show_frames: bool,
    pub show_dispatch: bool,
    pub show_store: bool,
}
```

**Step output (Steps)**:
- step_id, mode kind, current_segment, frames_len
- dispatch_stack depth, pending_python kind

**Additional output (Trace)**:
- top frame kind (RustReturn/RustProgram/PythonGenerator)
- effect type_name when handling Yielded::Effect
- handler_idx and handler chain length (if dispatch active)
- continuation ids when Resume/Transfer/Delegate is applied

**Python values**: printed as placeholders (e.g., `<pyobject>`) to avoid GIL.

**Integration**:
- `VM::step()` increments `step_counter` and emits a debug line before/after state transitions.
- `receive_python_result()` may emit a debug line showing the PyCallOutcome kind.
- The driver may optionally emit Python-level debug info (with GIL) if requested.

### Python API for Debug

```python
vm = doeff.VM(debug=True)  # Steps level
vm.set_debug(DebugConfig(level="trace", show_frames=True, show_dispatch=True))
result = vm.run(program)
```

Debug output defaults to stderr.

---

## Step State Machine

The VM executes via a mode-based state machine. Each `step()` call transitions the mode exactly once.

### StepEvent (External Interface)

`step()` returns one of these events to the driver (PyO3 wrapper):

```rust
/// Result of a single VM step.
/// 
/// The driver loop calls step() repeatedly until Done or Error.
/// When NeedsPython is returned, driver executes Python call and feeds result back.
pub enum StepEvent {
    /// Internal transition occurred; keep stepping (pure Rust)
    Continue,
    
    /// Need to call into Python (GIL boundary)
    NeedsPython(PythonCall),
    
    /// Computation completed successfully
    Done(Value),
    
    /// Computation failed
    Error(VMError),
}
```

**Note**: `Continue` means the VM made progress internally. The value being delivered is stored in `VM.mode`, not returned. This simplifies the state machine.

**Async note**: `PythonCall::CallAsync` is only valid under `async_run` / `VM.run_async`.
The sync driver must raise `TypeError` if it receives CallAsync.

### Mode (Internal State)

```rust
/// VM's internal execution mode.
/// 
/// Each step() transitions mode exactly once.
pub enum Mode {
    /// Deliver a value to the next frame
    Deliver(Value),
    
    /// Throw an exception to the next frame
    Throw(PyException),
    
    /// Handle something yielded by a generator or Rust program
    HandleYield(Yielded),
    
    /// Current segment is empty; return value to caller
    Return(Value),
}
```

### Yielded (Generator Output Classification)

**IMPORTANT**: Classification of Python generator yields happens in the **driver**
(with GIL), not in the VM. Rust program handlers yield `Yielded` directly.
The VM receives pre-classified `Yielded` values and operates without GIL.

```rust
/// Classification of what a generator yielded.
/// 
/// INVARIANT: Python generator yields are classified by the DRIVER (GIL held),
/// not by the VM. Rust program handlers return Yielded directly.
/// The VM receives Yielded and processes it without needing GIL.
pub enum Yielded {
    /// A DoCtrl (Resume, Transfer, WithHandler, Call, GetCallStack, etc.)
    DoCtrl(DoCtrl),
    
    /// An effect to be handled
    Effect(Effect),
    
    /// A nested DoThunk object to execute — LEGACY PATH (no call metadata). [R9-E]
    /// After SPEC-TYPES-001 separation, DoThunks with metadata should use
    /// Primitive(Call { f, args: [], kwargs: [], metadata }) instead. This variant
    /// is kept for backward compatibility with DoThunks that don't carry metadata.
    Program(Py<PyAny>),
    
    /// Unknown object (will cause TypeError)
    Unknown(Py<PyAny>),
}

impl Yielded {
    /// Classify a Python object yielded by a generator.
    /// 
    /// MUST be called by DRIVER with GIL held.
    /// Result is passed to VM via PyCallOutcome::GenYield(Yielded).
    ///
    /// [R9-E] Programs with recognizable metadata (KPC-like objects with
    /// function_name/kleisli_source) are upgraded to Call primitives.
    /// Programs without metadata fall through to Yielded::Program (legacy).
    pub fn classify(py: Python<'_>, obj: &Bound<'_, PyAny>) -> Self {
        // Check for DoCtrl
        if let Ok(prim) = extract_control_primitive(py, obj) {
            return Yielded::DoCtrl(prim);
        }
        
        // Check for Effect (including KPC after SPEC-TYPES-001 separation)
        if let Ok(effect) = extract_effect(py, obj) {
            return Yielded::Effect(effect);
        }
        
        // Check for DoThunk (nested) — with metadata upgrade [R9-E]
        if is_do_thunk(py, obj) {
            // Try to extract CallMetadata for Call upgrade
            if let Some(metadata) = extract_call_metadata(py, obj) {
                return Yielded::DoCtrl(DoCtrl::Call {
                    f: obj.clone().unbind(),
                    args: vec![],
                    kwargs: vec![],
                    metadata,
                });
            }
            // No metadata — legacy path
            return Yielded::Program(obj.clone().unbind());
        }
        
        // Unknown
        Yielded::Unknown(obj.clone().unbind())
    }
}

/// Extract CallMetadata from a Python program object (with GIL). [R9-E]
///
/// Returns Some(CallMetadata) if the object has recognizable metadata
/// (function_name, kleisli_source with __code__). Returns None otherwise.
fn extract_call_metadata(py: Python<'_>, obj: &Bound<'_, PyAny>) -> Option<CallMetadata> {
    let function_name = obj.getattr("function_name").ok()?.extract::<String>().ok()?;
    let (source_file, source_line) = if let Ok(kleisli) = obj.getattr("kleisli_source") {
        if let Ok(func) = kleisli.getattr("original_func") {
            if let Ok(code) = func.getattr("__code__") {
                let file = code.getattr("co_filename").ok()?.extract::<String>().ok()?;
                let line = code.getattr("co_firstlineno").ok()?.extract::<u32>().ok()?;
                (file, line)
            } else { return None; }
        } else { return None; }
    } else { return None; };
    Some(CallMetadata {
        function_name,
        source_file,
        source_line,
        program_call: Some(obj.clone().unbind()),
    })
}
```

**Note**: A yielded Program is a ProgramBase object. The driver must call
`to_generator()` to start it. Raw generators are rejected; only low-level
entry points like `start_with_generator()` accept raw generators.

**[R9-E] Call upgrade**: When the driver detects a DoThunk with metadata
(function_name, kleisli_source), it emits `Yielded::DoCtrl(Call { f, args: [], kwargs: [], metadata })`
instead of `Yielded::Program(obj)`. This enables call stack tracking without
changing user code. After SPEC-TYPES-001 separation, KPC will be classified as
`Yielded::Effect` (not Program), and the KPC handler will emit `Call` primitives
for kernel invocation and `Eval` primitives for arg resolution. Direct
`yield some_thunk` from user code still goes through classify → Call upgrade
(if metadata available) or Yielded::Program (legacy).

**Note**: Rust program handlers yield `Yielded` directly (already classified),
so no driver-side classification or GIL is required for those yields.

`extract_control_primitive` uses `Handler::from_pyobject` to decode `WithHandler`
and `CreateContinuation` handler arguments, and `Continuation::from_pyobject`
to decode `Resume`/`Transfer`/`ResumeContinuation`.
It also recognizes `PythonAsyncSyntaxEscape` and extracts the `action` callable.
`extract_effect` recognizes built-in scheduler effect classes and maps them to
`Effect::Scheduler`.

### PyCallOutcome (Python Call Results)

**CRITICAL**: StartProgram/CallFunc/CallAsync/CallHandler and Gen* have different semantics:
- `StartProgram` returns a **Value** (Value::Python(generator) after to_generator())
- `CallFunc` returns a **Value** (non-generator result)
- `CallAsync` returns a **Value** (awaited result; async_run only)
- `CallHandler` returns a **Value** (Value::Python(generator) after to_generator())
- `GenNext/GenSend/GenThrow` interact with a running generator (yield/return/error)

```rust
/// Result of executing a PythonCall.
/// 
/// IMPORTANT: This enum correctly separates:
/// - StartProgram/CallFunc/CallAsync/CallHandler results (a Value)
/// - Generator step results (yield/return/error)
pub enum PyCallOutcome {
    /// StartProgram returns Value::Python(generator) after to_generator().
    /// CallFunc returns Value (non-generator).
    /// CallAsync returns Value (awaited result).
    /// CallHandler returns Value::Python(generator) after to_generator().
    /// VM should push Frame::PythonGenerator with started=false and metadata for generator Values.
    /// The driver performs Python->Value conversion while holding the GIL.
    Value(Value),
    
    /// Generator yielded a value.
    /// Driver has already classified it (requires GIL).
    GenYield(Yielded),
    
    /// Generator returned via StopIteration.
    GenReturn(Value),
    
    /// Generator (or StartProgram/CallFunc/CallAsync/CallHandler) raised an exception.
    GenError(PyException),
}

/// Wrapper for Python exceptions in Rust.
#[derive(Debug, Clone)]
pub struct PyException {
    pub exc_type: Py<PyAny>,
    pub exc_value: Py<PyAny>,
    pub exc_tb: Option<Py<PyAny>>,
}
```

**Key insight**: `GenYield(Yielded)` contains a *classified* `Yielded`, not a raw `Py<PyAny>`. 
Classification and Python->Value conversion require GIL, so driver does them. VM receives
pre-classified data and `Value` only, and stays GIL-free.

---

## Mode Transitions

### Overview

```
                    ┌─────────────────────────────────────────┐
                    │              VM.step()                   │
                    │                                         │
   ┌────────────────┼─────────────────────────────────────────┼────────────────┐
   │                │                                         │                │
   ▼                ▼                                         ▼                ▼
Deliver(v)      Throw(e)                              HandleYield(y)      Return(v)
   │                │                                         │                │
   │                │                                         │  (y already    │
   ▼                ▼                                         │   classified   │
frames.pop()   frames.pop()                                   │   by driver or Rust)│
   │                │                                         │                │
   ├─RustReturn─────┼──────────────────────────────────┬──────┘                │
   │  callback(v)   │  callback(e)                     │                       │
   ├─RustProg───────┼──────────────────────────────────┤                       │
   │  step()/yield  │                                  │                       │
   │                │                                  ├─Primitive────────────►│
   ├─PyGen──────────┼──────────────────────────────────┤  handle_do_ctrl()   │
   │  NeedsPython   │  NeedsPython(GenThrow)           │                       │
   │  (GenSend/Next)│                                  ├─Effect───────────────►│
   │                │                                  │  start_dispatch()     │
   ▼                ▼                                  │  (all effects)        │
                                                       │                       │
                                                       ├─Program──────────────►│
                                                       │  NeedsPython(StartProgram)│
                                                       │                       │
                                                       └─Unknown──────────────►│
                                                          Throw(TypeError)     │
                                                                               │
                                                                               ▼
                                                                        ┌──────────┐
                                                                        │ Yes: goto│
                                                                        │  caller  │
                                                                        │ segment  │
                                                                        ├──────────┤
                                                                        │ No: Done │
                                                                        │  or Err  │
                                                                        └──────────┘
```

### Rule 1: Deliver(value) / Throw(exception)

```rust
fn step_deliver_or_throw(&mut self) -> StepEvent {
    let segment = &mut self.segments[self.current_segment.index()];
    
    // If segment has no frames, transition to Return
    if segment.frames.is_empty() {
        match &self.mode {
            Mode::Deliver(v) => self.mode = Mode::Return(v.clone()),
            Mode::Throw(e) => {
                // Exception with no handler - propagate up
                if let Some(caller_id) = segment.caller {
                    self.current_segment = caller_id;
                    // mode stays Throw
                    return StepEvent::Continue;
                } else {
                    return StepEvent::Error(VMError::UncaughtException(e.clone()));
                }
            }
            _ => unreachable!(),
        }
        return StepEvent::Continue;
    }
    
    // Pop frame (O(1) from end)
    let frame = segment.frames.pop().unwrap();
    
    match frame {
        Frame::RustReturn { cb } => {
            // Consume callback and execute
            let callback = self.callbacks.remove(cb)
                .expect("callback must exist");
            
            match &self.mode {
                Mode::Deliver(v) => {
                    // Callback returns new Mode
                    self.mode = callback(v.clone(), self);
                    StepEvent::Continue
                }
                Mode::Throw(e) => {
                    // Rust callbacks don't handle exceptions; propagate
                    self.mode = Mode::Throw(e.clone());
                    StepEvent::Continue
                }
                _ => unreachable!(),
            }
        }
        
        Frame::RustProgram { program } => {
            let step = match &self.mode {
                Mode::Deliver(v) => {
                    let mut guard = program.lock().expect("Rust program lock poisoned");
                    guard.resume(v.clone(), &mut self.rust_store)
                }
                Mode::Throw(e) => {
                    let mut guard = program.lock().expect("Rust program lock poisoned");
                    guard.throw(e.clone(), &mut self.rust_store)
                }
                _ => unreachable!(),
            };
            self.apply_rust_program_step(step, program)
        }
        
        Frame::PythonGenerator { generator, started, metadata } => {
            // Need to call Python
            // CRITICAL: Set pending_python so receive_python_result knows to re-push
            // [R9-C] metadata is preserved across yields (carried in StepUserGenerator)
            self.pending_python = Some(PendingPython::StepUserGenerator {
                generator: generator.clone(),
                metadata: metadata.clone(),  // Carry metadata for re-push [R9-C]
            });
            
            match &self.mode {
                Mode::Deliver(v) => {
                    if started {
                        StepEvent::NeedsPython(PythonCall::GenSend {
                            gen: generator,
                            value: v.clone(),
                        })
                    } else {
                        // First call uses GenNext
                        StepEvent::NeedsPython(PythonCall::GenNext {
                            gen: generator,
                        })
                    }
                }
                Mode::Throw(e) => {
                    StepEvent::NeedsPython(PythonCall::GenThrow {
                        gen: generator,
                        exc: e.exc_value.clone(),
                    })
                }
                _ => unreachable!(),
            }
        }
    }
}
```

### Rule 2: Receive Python Result → Route Based on PendingPython

```rust
impl VM {
    /// Called by driver after executing PythonCall.
    /// 
    /// Uses pending_python to know what to do with the result.
    /// INVARIANT: pending_python is Some when this is called.
    /// Driver has already converted Python objects to Value.
    pub fn receive_python_result(&mut self, outcome: PyCallOutcome) {
        let pending = self.pending_python.take()
            .expect("pending_python must be set when receiving result");
        
        match (pending, outcome) {
            // === StartProgramFrame: StartProgram returned Value::Python(generator) ===
            (PendingPython::StartProgramFrame { metadata }, PyCallOutcome::Value(Value::Python(gen_obj))) => {
                // Push generator as new frame with started=false and CallMetadata [R9-G]
                let segment = &mut self.segments[self.current_segment.index()];
                segment.push_frame(Frame::PythonGenerator {
                    generator: gen_obj,
                    started: false,
                    metadata,  // Some for Call primitive, None for Yielded::Program
                });
                // Mode stays Deliver (will trigger GenNext on next step)
            }
            (PendingPython::StartProgramFrame { .. }, PyCallOutcome::Value(_)) => {
                self.mode = Mode::Throw(PyException::type_error(
                    "program did not return a generator"
                ));
            }
            (PendingPython::StartProgramFrame { .. }, PyCallOutcome::GenError(e)) => {
                // StartProgram raised exception
                self.mode = Mode::Throw(e);
            }
            
            // === StepUserGenerator: Generator stepped ===
            (PendingPython::StepUserGenerator { generator, metadata }, PyCallOutcome::GenYield(yielded)) => {
                // CRITICAL: Re-push generator with started=true + preserved metadata [R9-C]
                // Otherwise we lose the frame and can't continue it later
                let segment = &mut self.segments[self.current_segment.index()];
                segment.push_frame(Frame::PythonGenerator {
                    generator,
                    started: true,
                    metadata,  // Preserve call stack metadata across yields
                });
                self.mode = Mode::HandleYield(yielded);
            }
            (PendingPython::StepUserGenerator { .. }, PyCallOutcome::GenReturn(v)) => {
                // Generator completed - do NOT re-push
                // Value flows to next frame
                self.mode = Mode::Deliver(v);
            }
            (PendingPython::StepUserGenerator { .. }, PyCallOutcome::GenError(e)) => {
                // Generator raised exception - do NOT re-push
                self.mode = Mode::Throw(e);
            }
            
            // === CallPythonHandler: Handler returned Value::Python(generator) ===
            (PendingPython::CallPythonHandler { k_user, effect }, PyCallOutcome::Value(Value::Python(handler_gen))) => {
                // Handler returned a Program converted to a generator that yields primitives
                // Push handler-return hook (implicit Return), then generator frame (started=false)
                let segment = &mut self.segments[self.current_segment.index()];
                // segment.push_frame(Frame::RustReturn { cb: handler_return_cb });
                segment.push_frame(Frame::PythonGenerator {
                    generator: handler_gen,
                    started: false,
                });
                // k_user is stored in DispatchContext for completion detection and Delegate
            }
            (PendingPython::CallPythonHandler { .. }, PyCallOutcome::Value(_)) => {
                self.mode = Mode::Throw(PyException::type_error(
                    "handler did not return a ProgramBase (KleisliProgramCall or EffectBase)"
                ));
            }
            (PendingPython::CallPythonHandler { .. }, PyCallOutcome::GenError(e)) => {
                self.mode = Mode::Throw(e);
            }
            
            // === [R8-H] RustProgramContinuation: RustProgram handler's Python call returned ===
            (PendingPython::RustProgramContinuation { marker, k }, PyCallOutcome::Value(result)) => {
                // Feed result back to the RustHandlerProgram via resume()
                // The handler program is located via marker in the scope_chain
                // and resumed with the Python call result as a Value
                self.mode = Mode::Deliver(result);
            }
            (PendingPython::RustProgramContinuation { .. }, PyCallOutcome::GenError(e)) => {
                self.mode = Mode::Throw(e);
            }
            
            // === AsyncEscape: PythonAsyncSyntaxEscape awaited ===
            (PendingPython::AsyncEscape, PyCallOutcome::Value(result)) => {
                self.mode = Mode::Deliver(result);
            }
            (PendingPython::AsyncEscape, PyCallOutcome::GenError(e)) => {
                self.mode = Mode::Throw(e);
            }
            
            // Unexpected combinations
            (pending, outcome) => {
                panic!("Unexpected pending/outcome combination: {:?} / {:?}", pending, outcome);
            }
        }
    }
}
```

### Rule 3: HandleYield → Interpret Yielded Value

```rust
fn step_handle_yield(&mut self) -> StepEvent {
    let yielded = match std::mem::replace(&mut self.mode, Mode::Deliver(Value::Unit)) {
        Mode::HandleYield(y) => y,
        _ => unreachable!(),
    };
    
    match yielded {
        Yielded::DoCtrl(prim) => {
            // Handle DoCtrl
            self.handle_do_ctrl(prim)
        }
        
        Yielded::Effect(effect) => {
            // ALL effects go through dispatch — no bypass [R8-B]
            // Standard effects handled by RustProgram handlers (fast)
            // Custom effects handled by Python handlers
            match self.start_dispatch(effect) {
                Ok(event) => event,
                Err(e) => StepEvent::Error(e),
            }
        }
        
        Yielded::Program(program) => {
            // Nested program - need to call Python to get generator [R9-E]
            // Legacy path: no metadata. Use Call primitive for metadata-carrying calls.
            self.pending_python = Some(PendingPython::StartProgramFrame {
                metadata: None,
            });
            StepEvent::NeedsPython(PythonCall::StartProgram { program })
        }
        
        Yielded::Unknown(obj) => {
            // Type error
            self.mode = Mode::Throw(PyException::type_error(
                format!("generator yielded unexpected type: {:?}", obj)
            ));
            StepEvent::Continue
        }
    }
}
```

### Rule 4: Return → Go to Caller or Complete

```rust
fn step_return(&mut self) -> StepEvent {
    let value = match std::mem::replace(&mut self.mode, Mode::Deliver(Value::Unit)) {
        Mode::Return(v) => v,
        _ => unreachable!(),
    };
    
    let segment = &self.segments[self.current_segment.index()];
    
    if let Some(caller_id) = segment.caller {
        // Switch to caller segment
        self.current_segment = caller_id;
        self.mode = Mode::Deliver(value);
        StepEvent::Continue
    } else {
        // No caller - computation complete
        StepEvent::Done(value)
    }
}
```

### Main Step Function

```rust
impl VM {
    /// Execute one step of the VM.
    pub fn step(&mut self) -> StepEvent {
        match &self.mode {
            Mode::Deliver(_) | Mode::Throw(_) => self.step_deliver_or_throw(),
            Mode::HandleYield(_) => self.step_handle_yield(),
            Mode::Return(_) => self.step_return(),
        }
    }
}
```

### Continuation Primitive Semantics (Summary)

- **GetContinuation**: returns the current dispatch callsite continuation (`k_user`) to the handler
  without consuming it. Error if called outside handler context.
- **GetHandlers**: returns the full handler chain from the callsite scope (innermost → outermost).
  These handlers can be passed back to `WithHandler` or `CreateContinuation`.
- **CreateContinuation**: returns an unstarted continuation storing `(expr, handlers)`.
- **ResumeContinuation**: if `started=true`, behaves like `Resume` (call-resume). If
  `started=false`, installs handlers (outermost first) and starts the program, returning
  to the current handler when it finishes; `value` is ignored.
- **Implicit Handler Return**: if a handler program (Python/Rust) returns, the VM
  treats it as handler return. The returned value becomes the result of
  `yield Delegate(effect)` for inner handlers; for the root handler it abandons the
  callsite (marks dispatch completed) and returns to the prompt boundary.
- **Single Resume per Dispatch**: The callsite continuation (`k_user`) is one-shot.
  Exactly one of Resume/Transfer/Return may consume it in a dispatch. After
  `yield Delegate(effect)` returns, the handler must return (not Resume). Any
  double-resume or resume-after-delegate is a runtime error.
- **No Multi-shot**: Multi-shot continuations are not supported. All continuations
  are one-shot and cannot be resumed more than once.

**Delegate Data Flow (Koka/OCaml semantics)**:

```
User --perform E--> H1
H1: z = yield Delegate(E)
      |
      v
     H2 handles E
     H2: u = yield Resume(k_user, v)
     User: r = ...; return r
     H2: u == r; return h2
H1: z == h2; return h1
```

Notes:
- Only one handler in the chain resumes the callsite (`k_user`).
- `yield Delegate(E)` returns the outer handler's return value (`h2`).
- After Delegate returns, the handler must return (no Resume).

**Delegate/Resume Pseudocode**:

```python
@do
def user():
    x = yield SomeEffect()
    return x * 2

@do
def outer_handler(effect, k_user):
    if isinstance(effect, SomeEffect):
        user_ret = yield Resume(k_user, 10)
        return user_ret + 5
    return (yield Delegate(effect))

@do
def inner_handler(effect, k_user):
    outer_ret = yield Delegate(effect)
    return outer_ret + 1

# INVALID: Resume after Delegate (double-resume)
@do
def bad_handler(effect, k_user):
    outer_ret = yield Delegate(effect)
    return (yield Resume(k_user, outer_ret))  # runtime error
```

### Scheduler Pattern: Spawn with Transfer (Reference)

User-space schedulers can avoid stack growth without a special primitive by
returning the task handle via `Transfer` and enqueueing the child continuation.

```
handler --Transfer--> parent (Task handle)
queue   --ResumeContinuation--> child (later)
```

```python
def spawn_handler(effect, k_user):
    def program():
        if isinstance(effect, Spawn):
            task_k = (yield CreateContinuation(effect.expr, effect.handlers))
            queue.append(task_k)
            return (yield Transfer(k_user, Task(task_k)))
        return (yield Delegate(effect))
    return program()
```

---

## Driver Loop (PyO3 Side)

The driver handles GIL boundaries and **classifies yielded values** before passing to VM.
The sync driver is `run`; async integration is provided by `async_run` (see below).

```rust
impl PyVM {
    /// Run a program to completion.
    pub fn run(&mut self, py: Python<'_>, program: Bound<'_, PyAny>) -> PyResult<PyObject> {
        // Initialize: convert Program object to generator
        let gen = self.to_generator(py, program)?;
        self.vm.start_with_generator(gen.unbind());
        
        loop {
            // Release GIL for pure Rust steps
            let event = py.allow_threads(|| {
                loop {
                    match self.vm.step() {
                        StepEvent::Continue => continue,
                        other => return other,
                    }
                }
            });
            
            match event {
                StepEvent::Done(value) => {
                    return value.to_pyobject(py).map(|v| v.unbind());
                }
                
                StepEvent::Error(e) => {
                    return Err(e.to_pyerr(py));
                }
                
                StepEvent::NeedsPython(call) => {
                    let outcome = self.execute_python_call(py, call)?;
                    self.vm.receive_python_result(outcome);
                }
                
                StepEvent::Continue => unreachable!("handled in inner loop"),
            }
        }
    }
    
    /// Execute a Python call and return the outcome.
    /// 
    /// CRITICAL: This correctly distinguishes StartProgram/CallFunc/CallAsync/CallHandler from Gen* results:
    /// - StartProgram → Value::Python(generator)
    /// - CallFunc → Value (non-generator)
    /// - CallAsync → Value (awaited result; async_run only)
    /// - CallHandler → Value::Python(generator) after to_generator()
    /// - Gen* → GenYield/GenReturn/GenError (generator step result)
    /// 
    /// Classification of yielded values happens HERE (with GIL).
    fn execute_python_call(&self, py: Python<'_>, call: PythonCall) -> PyResult<PyCallOutcome> {
        match call {
            PythonCall::StartProgram { program } => {
                if is_program(py, &program.bind(py)) {
                    let gen = self.to_generator(py, program.bind(py))?;
                    Ok(PyCallOutcome::Value(Value::Python(gen.unbind())))
                } else {
                    Ok(PyCallOutcome::GenError(PyException::type_error(
                        "StartProgram requires a ProgramBase (KleisliProgramCall or EffectBase)",
                    )))
                }
            }
            PythonCall::CallFunc { func, args } => {
                let py_args = args.to_py_tuple(py)?;
                match func.bind(py).call1(py_args) {
                    Ok(result) => {
                        // CallFunc returns a Value (not a generator yield!)
                        Ok(PyCallOutcome::Value(Value::from_pyobject(&result)))
                    }
                    Err(e) => {
                        Ok(PyCallOutcome::GenError(PyException::from_pyerr(py, e)))
                    }
                }
            }
            
            PythonCall::CallAsync { .. } => {
                Ok(PyCallOutcome::GenError(PyException::type_error(
                    "CallAsync requires async_run (PythonAsyncSyntaxEscape handler)",
                )))
            }
            
            PythonCall::CallHandler { handler, effect, continuation } => {
                // Wrap Effect/Continuation into Python objects while holding GIL
                let py_effect = effect.to_pyobject(py)?;
                let py_k = continuation.to_pyobject(py)?;
                match handler.bind(py).call1((py_effect, py_k)) {
                    Ok(result) => {
                        // Handler must return a ProgramBase (KleisliProgramCall or EffectBase)
                        if is_program(py, &result) {
                            let gen = self.to_generator(py, result)?;
                            Ok(PyCallOutcome::Value(Value::Python(gen.unbind())))
                        } else {
                            Ok(PyCallOutcome::GenError(PyException::type_error(
                                "handler must return a ProgramBase (KleisliProgramCall or EffectBase)",
                            )))
                        }
                    }
                    Err(e) => {
                        Ok(PyCallOutcome::GenError(PyException::from_pyerr(py, e)))
                    }
                }
            }
            
            PythonCall::GenNext { gen } => {
                self.step_generator(py, gen, "__next__", None)
            }
            
            PythonCall::GenSend { gen, value } => {
                let py_value = value.to_pyobject(py)?;
                self.step_generator(py, gen, "send", Some(py_value))
            }
            
            PythonCall::GenThrow { gen, exc } => {
                let exc_bound = exc.bind(py);
                self.step_generator(py, gen, "throw", Some(exc_bound.clone()))
            }
        }
    }

    /// Convert a ProgramBase object to a generator.
    /// 
    /// Accepts ProgramBase (KleisliProgramCall or EffectBase) and calls to_generator().
    /// This preserves the KleisliProgramCall stack for effect debugging.
    /// Raw generators are rejected here (only allowed by start_with_generator()).
    fn to_generator(
        &self,
        py: Python<'_>,
        program: Bound<'_, PyAny>,
    ) -> PyResult<Bound<'_, PyAny>> {
        if program.is_instance_of::<PyGenerator>()? {
            return Err(PyException::type_error(
                "ProgramBase required; raw generators are not accepted"
            ).to_pyerr(py));
        }
        let to_gen = program.getattr("to_generator")?;
        to_gen.call0()
    }
    
    /// Step a generator and classify the result.
    /// 
    /// IMPORTANT: Classification happens HERE with GIL held.
    /// VM receives pre-classified Yielded and operates without GIL.
    fn step_generator(
        &self, 
        py: Python<'_>, 
        gen: Py<PyAny>, 
        method: &str, 
        arg: Option<Bound<'_, PyAny>>
    ) -> PyResult<PyCallOutcome> {
        let gen_bound = gen.bind(py);
        
        let result = match arg {
            Some(a) => gen_bound.call_method1(method, (a,)),
            None => gen_bound.call_method0(method),
        };
        
        match result {
            Ok(yielded_obj) => {
                // Generator yielded - classify it HERE (with GIL)
                let classified = Yielded::classify(py, &yielded_obj);
                Ok(PyCallOutcome::GenYield(classified))
            }
            Err(e) if e.is_instance_of::<pyo3::exceptions::PyStopIteration>(py) => {
                // Generator completed
                let stop_iter = e.value(py);
                let return_value = stop_iter.getattr("value")?;
                Ok(PyCallOutcome::GenReturn(Value::from_pyobject(&return_value)))
            }
            Err(e) => {
                Ok(PyCallOutcome::GenError(PyException::from_pyerr(py, e)))
            }
        }
    }
}
```

---

## Asyncio Integration (Reference)

This section mirrors SPEC-CESK-006's asyncio bridge, adapted to the Rust VM.
The VM core remains synchronous; async integration is implemented by a driver
wrapper and a handler that yields `PythonAsyncSyntaxEscape`.

### Async Driver (async_run)

`async_run` uses the same step loop but awaits `PythonCall::CallAsync` events.
All other PythonCall variants are handled synchronously via `execute_python_call`.

```python
async def async_run(vm, program):
    gen = program.to_generator()
    vm.start_with_generator(gen)
    while True:
        event = vm.step()
        if isinstance(event, Done):
            return event.value
        if isinstance(event, Error):
            raise event.error
        if isinstance(event, NeedsPython):
            call = event.call
            if isinstance(call, CallAsync):
                outcome = await execute_python_call_async(call)
            else:
                outcome = execute_python_call(call)
            vm.receive_python_result(outcome)
        await asyncio.sleep(0)
```

`execute_python_call_async` is a thin wrapper:

```python
async def execute_python_call_async(call):
    py_args = to_py_args(call.args)
    awaitable = call.func(*py_args)
    result = await awaitable
    return PyCallOutcome.Value(Value.from_pyobject(result))
```

Argument conversion uses the same `Value` → Python path as `CallFunc`.

### Await Effect (Reference)

`Await(awaitable)` is a Python-level effect (see SPEC-EFF-005). The Rust VM
treats it as `Effect::Python` and dispatches to user handlers.

Two reference handlers are provided:
- `sync_await_handler`: runs the awaitable in a background thread/executor and
  resumes the continuation with the result.
- `python_async_syntax_escape_handler`: yields `PythonAsyncSyntaxEscape` so
  `async_run` can await in the event loop.

```python
@do
def sync_await_handler(effect, k):
    if isinstance(effect, Await):
        promise = yield CreateExternalPromise()
        thread_pool.submit(run_and_complete, effect.awaitable, promise)
        return (yield Wait(promise.future))
    return (yield Delegate(effect))
```

```python
@do
def python_async_syntax_escape_handler(effect, k):
    if isinstance(effect, Await):
        promise = yield CreateExternalPromise()
        async def fire_task():
            try:
                result = await effect.awaitable
                promise.complete(result)
            except BaseException as exc:
                promise.fail(exc)
        yield PythonAsyncSyntaxEscape(
            action=lambda: asyncio.create_task(fire_task())
        )
        return (yield Wait(promise.future))
    return (yield Delegate(effect))
```

`python_async_syntax_escape_handler` must only be used with `async_run`;
the sync driver raises `TypeError` if it sees `CallAsync`.

**Usage**:
- Sync: `vm.run(with_handler(sync_await_handler, program))`
- Async: `await vm.run_async(with_handler(python_async_syntax_escape_handler, program))`

---

## Public API Contract (SPEC-009 Support) [R8-J]

This section specifies the user-facing types and contracts that the VM must
expose to satisfy SPEC-009. Everything in this section is part of the
**public boundary** — the layer between user code and VM internals.

### run() and async_run() — Entrypoint Contract

```python
def run(
    program: Program[T],
    handlers: list[Handler] = [],
    env: dict[str, Any] = {},
    store: dict[str, Any] = {},
) -> RunResult[T]: ...

async def async_run(
    program: Program[T],
    handlers: list[Handler] = [],
    env: dict[str, Any] = {},
    store: dict[str, Any] = {},
) -> RunResult[T]: ...
```

These are **Python-side** functions that wrap `PyVM`. They are NOT methods on
PyVM — they create and configure a PyVM internally.

#### Implementation Contract

`run(program, handlers, env, store)` does the following in order:

```
1. Create PyVM instance
       vm = PyVM::new()

2. Initialize store (SPEC-009 API-6)
       for key, value in store.items():
           vm.put_state(key, Value::from_pyobject(value))

3. Initialize environment (SPEC-009 API-5)
       for key, value in env.items():
           vm.put_env(key, Value::from_pyobject(value))

4. Wrap program with handlers (nesting order — see below)
       wrapped = program
       for h in reversed(handlers):
           wrapped = WithHandler(handler=h, expr=wrapped)

5. Execute via driver loop
       final_value_or_error = vm.run(wrapped)   # driver loop from §Driver Loop

6. Extract results into RunResult
       raw_store = {k: v.to_pyobject() for k, v in vm.state_items()}
       result = Ok(final_value) or Err(exception)
       return RunResult(result=result, raw_store=raw_store)
```

`async_run` is identical except step 5 uses the async driver loop (§Async Driver).

#### Handler Nesting Order

`handlers=[h0, h1, h2]` produces:

```
WithHandler(h0,           ← outermost, sees effects LAST
  WithHandler(h1,
    WithHandler(h2,       ← innermost, sees effects FIRST
      program)))
```

`h2` is closest to the program — it sees effects first. `h0` is outermost —
it sees effects that `h1` and `h2` delegate. This matches `reversed(handlers)`.

**No default handlers** (SPEC-009 API-1). If `handlers=[]`, the program runs
with zero handlers. Yielding any effect raises `UnhandledEffect`.

### RunResult — Execution Output [R8-J]

```rust
/// The public result of a run()/async_run() call.
///
/// This is a #[pyclass] exposed to Python. It is immutable (SPEC-009 API-7).
/// The concrete type is internal; users interact via the RunResult protocol.
#[pyclass(frozen)]
pub struct PyRunResult {
    /// Ok(value) or Err(exception)
    result: Result<Py<PyAny>, PyException>,
    /// Final store snapshot (extracted from RustStore at run completion)
    raw_store: Py<PyDict>,
}

#[pymethods]
impl PyRunResult {
    /// Ok(value) or Err(exception).
    #[getter]
    fn result(&self, py: Python<'_>) -> PyObject {
        match &self.result {
            Ok(v) => Ok(v.clone_ref(py)).to_pyobject(py),
            Err(e) => Err(e.clone()).to_pyobject(py),
        }
    }

    /// Final store snapshot after execution.
    #[getter]
    fn raw_store(&self, py: Python<'_>) -> PyObject {
        self.raw_store.clone_ref(py).into()
    }

    /// Unwrap Ok or raise the Err.
    #[getter]
    fn value(&self, py: Python<'_>) -> PyResult<PyObject> {
        match &self.result {
            Ok(v) => Ok(v.clone_ref(py)),
            Err(e) => Err(e.to_pyerr(py)),
        }
    }

    /// Get Err or raise ValueError if Ok.
    #[getter]
    fn error(&self, py: Python<'_>) -> PyResult<PyObject> {
        match &self.result {
            Err(e) => Ok(e.to_pyobject(py)),
            Ok(_) => Err(PyValueError::new_err("RunResult is Ok, not Err")),
        }
    }

    fn is_ok(&self) -> bool {
        self.result.is_ok()
    }

    fn is_err(&self) -> bool {
        self.result.is_err()
    }
}
```

**Construction** (inside `run()`/`async_run()` only):

```rust
/// Build RunResult after VM execution completes.
fn build_run_result(
    py: Python<'_>,
    vm: &VM,
    outcome: Result<Value, PyException>,
) -> PyResult<PyRunResult> {
    // Extract final store as Python dict (SPEC-009 API-6)
    let raw_store = PyDict::new(py);
    for (key, value) in vm.rust_store.state.iter() {
        raw_store.set_item(key, value.to_pyobject(py)?)?;
    }

    let result = match outcome {
        Ok(value) => Ok(value.to_pyobject(py)?.unbind()),
        Err(exc) => Err(exc),
    };

    Ok(PyRunResult {
        result,
        raw_store: raw_store.unbind(),
    })
}
```

**Invariants**:
- `raw_store` is always populated, even on error (SPEC-009 API-6).
  The store snapshot reflects state at the point execution stopped.
- `RunResult` is frozen/immutable (SPEC-009 API-7).
- `raw_store` contains only `state` entries (not `env` or `log`).
  Logs are accessible via `writer` handler if the user installed it.

### @do Decorator and Program[T] [R8-J]

`@do` is a **Python-side** decorator. It is NOT part of the Rust VM — it lives
in the `doeff` Python package. SPEC-008 defines how the VM processes its output.

#### What @do Does

```python
def do(fn):
    """Convert a generator function into a Program factory.

    @do
    def counter(start: int):
        x = yield Get("count")
        yield Put("count", x + start)
        return x + start

    # counter(10) returns a KleisliProgramCall (which is a ProgramBase)
    # The VM calls to_generator() on it to get the actual generator
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        return KleisliProgramCall(fn, args, kwargs)
    return wrapper
```

#### How the VM Processes @do Output

1. User calls `counter(10)` → returns `KleisliProgramCall(counter, (10,), {})`
2. This is a `ProgramBase` — accepted by `run()`, `WithHandler`, `Resume`, etc.
3. When the VM needs to step this program, the driver calls `to_generator()`:
   ```python
   gen = program_base.to_generator()  # → calls counter(10), returns generator
   ```
4. The generator is stepped via `GenNext`/`GenSend`/`GenThrow` PythonCalls.
5. `yield <effect>` → classified by driver as `Yielded::Effect`/`Yielded::DoCtrl`
6. `return <value>` → `StopIteration(value)` → `PyCallOutcome::GenReturn(value)`
7. Generator return produces the final `T` in `Program[T]`.

#### DoExpr Input Rule (Reiterated)

All DoExpr entry points (`StartProgram`, `CallHandler`, `Yielded::Program`,
`DoCtrl::Call`) require a value with `to_generator()` (a DoThunk).
The driver calls `to_generator()` to obtain the generator. Raw generators are
rejected except via the low-level `start_with_generator()`.

**[R9-E] After SPEC-TYPES-001**: KleisliProgramCall will no longer have
`to_generator()` (it becomes an Effect). It goes through effect dispatch to
the KPC handler, which emits `Call` primitives for kernel invocation and `Eval`
primitives for arg resolution. Only DoThunks pass this rule.

### Store and Env Lifecycle [R8-J]

End-to-end data flow from `run()` parameters to `RunResult`:

```
 User calls run(program, handlers=[state, reader], env={"a": 1}, store={"x": 0})
      │
      ▼
 ┌─────────────────────────────────────────────────┐
 │  Step 1: Initialize RustStore                    │
 │                                                  │
 │  vm.rust_store.state = {"x": Value(0)}          │
 │  vm.rust_store.env   = {"a": Value(1)}          │
 │  vm.rust_store.log   = []                        │
 └─────────────────────┬───────────────────────────┘
                       │
                       ▼
 ┌─────────────────────────────────────────────────┐
 │  Step 2: Wrap handlers + execute                 │
 │                                                  │
 │  WithHandler(state,                              │
 │    WithHandler(reader,                           │
 │      program))                                   │
 │                                                  │
 │  During execution:                               │
 │    yield Get("x")  → state handler reads         │
 │                       rust_store.state["x"]      │
 │    yield Put("x",1) → state handler writes       │
 │                       rust_store.state["x"] = 1  │
 │    yield Ask("a")  → reader handler reads        │
 │                       rust_store.env["a"]        │
 │    yield Tell("hi") → writer handler appends     │
 │                       rust_store.log.push("hi")  │
 └─────────────────────┬───────────────────────────┘
                       │
                       ▼
 ┌─────────────────────────────────────────────────┐
 │  Step 3: Extract RunResult                       │
 │                                                  │
 │  result.result    = Ok(return_value)             │
 │  result.raw_store = {"x": 1}   ← state only     │
 │                                                  │
 │  NOT in raw_store:                               │
 │    env (read-only, user already has it)          │
 │    log (accessible via writer handler if needed) │
 └─────────────────────────────────────────────────┘
```

**RustStore field mapping**:

| RustStore field | Initialized from | Modified by | Extracted into |
|-----------------|------------------|-------------|----------------|
| `state` | `run(store={...})` | `Put`, `Modify` effects | `RunResult.raw_store` |
| `env` | `run(env={...})` | Never (read-only, API-5) | Not extracted |
| `log` | Empty `[]` | `Tell` effect | Not extracted (handler-specific) |

**Error case**: If the program raises an exception, `RunResult` still contains
`raw_store` reflecting the store state at the point of failure. The `result`
field is `Err(exception)`.

### PyVM Internal Methods for Lifecycle [R8-J]

These methods support the `run()`/`async_run()` lifecycle but are **internal** —
users never call them directly (SPEC-009 §9).

```rust
#[pymethods]
impl PyVM {
    /// Initialize state entries from Python dict.
    /// Called by run() before execution.
    fn put_state(&mut self, key: String, value: PyObject) {
        self.vm.rust_store.put(key, Value::Python(value));
    }

    /// Initialize environment entries from Python dict.
    /// Called by run() before execution.
    fn put_env(&mut self, key: String, value: PyObject) {
        self.vm.rust_store.env.insert(key, Value::Python(value));
    }

    /// Extract final state as Python dict.
    /// Called by run() after execution to build RunResult.raw_store.
    fn state_items(&self, py: Python<'_>) -> PyResult<PyObject> {
        let dict = PyDict::new(py);
        for (k, v) in &self.vm.rust_store.state {
            dict.set_item(k, v.to_pyobject(py)?)?;
        }
        Ok(dict.into())
    }

    /// Extract environment items (for debugging; not in RunResult).
    fn env_items(&self, py: Python<'_>) -> PyResult<PyObject> {
        let dict = PyDict::new(py);
        for (k, v) in &self.vm.rust_store.env {
            dict.set_item(k, v.to_pyobject(py)?)?;
        }
        Ok(dict.into())
    }

    /// Extract logs (for debugging; not in RunResult).
    fn logs(&self, py: Python<'_>) -> PyResult<PyObject> {
        let list = PyList::new(py, &[])?;
        for v in &self.vm.rust_store.log {
            list.append(v.to_pyobject(py)?)?;
        }
        Ok(list.into())
    }
}
```

---

## Control Primitives

```rust
/// Control primitives that can be yielded by handlers.
#[derive(Debug, Clone)]
pub enum DoCtrl {
    /// Resume(k, v) - Call-resume (returns to handler after k completes)
    Resume {
        continuation: Continuation,
        value: Value,
    },
    
    /// Transfer(k, v) - Tail-transfer (non-returning, abandons handler)
    Transfer {
        continuation: Continuation,
        value: Value,
    },
    
    /// Delegate(effect) - Delegate to outer handler.
    /// Yield result is the outer handler's return value.
    Delegate {
        effect: Effect,
    },
    
    /// WithHandler(handler, expr) - Install handler and evaluate DoExpr under it
    WithHandler {
        handler: Handler,
        expr: Py<PyAny>,
    },

    /// PythonAsyncSyntaxEscape(action) - Request async context execution.
    /// Used by async_run to await Python coroutines.
    PythonAsyncSyntaxEscape {
        /// Callable returning an awaitable
        action: Py<PyAny>,
    },
    
    /// GetContinuation - Capture current continuation (callsite k_user)
    GetContinuation,
    
    /// GetHandlers - Get handlers from callsite scope (full chain, innermost first)
    GetHandlers,
    
    /// CreateContinuation(expr, handlers) - Create unstarted continuation
    CreateContinuation {
        /// DoExpr to evaluate (DoThunk or Effect)
        expr: Py<PyAny>,
        /// Handlers in innermost-first order (as returned by GetHandlers)
        handlers: Vec<Handler>,
    },
    
    /// ResumeContinuation(k, v) - Resume captured or created continuation
    /// (v is ignored for unstarted continuations)
    ResumeContinuation {
        continuation: Continuation,
        value: Value,
    },

    /// Call(f, args, kwargs, metadata) - Call a function and run the result. [R9-A]
    ///
    /// Semantics: the VM emits NeedsPython(CallFunc { f, args, kwargs }) to the
    /// driver. The driver calls f(*args, **kwargs), gets a generator or DoThunk.
    /// If DoThunk, calls to_generator(). Pushes PythonGenerator frame with
    /// metadata onto current segment. No dispatch, no handler stack involvement.
    /// This is the doeff equivalent of a function call — not an effect.
    ///
    /// Two usage patterns:
    /// - DoThunk (no args): Call { f: thunk, args: [], kwargs: {}, metadata }
    ///   → driver calls to_generator() on the thunk, pushes frame.
    /// - Kernel call (with args): Call { f: kernel, args, kwargs, metadata }
    ///   → driver calls kernel(*args, **kwargs), gets result, pushes frame.
    ///
    /// Metadata is extracted by the driver (with GIL) during classify_yielded
    /// for user-yielded DoThunks, or constructed by RustHandlerPrograms (e.g.,
    /// KPC handler) for kernel invocations.
    ///
    /// Backward compat: Yielded::Program (without metadata) is still supported
    /// and handled identically but with metadata: None on the frame.
    Call {
        /// The callable (DoThunk or kernel function)
        f: Py<PyAny>,
        /// Positional arguments (empty for DoThunk path)
        args: Vec<Value>,
        /// Keyword arguments (empty for DoThunk path)
        kwargs: Vec<(String, Value)>,
        /// Call stack metadata (function name, source location)
        metadata: CallMetadata,
    },

    /// Eval(expr, handlers) - Evaluate a DoExpr in a fresh scope. [R9-H]
    ///
    /// Atomically creates an unstarted continuation with the given handler
    /// chain and evaluates the DoExpr within it. The caller is suspended;
    /// when the evaluation completes, the VM resumes the caller with the
    /// result. Equivalent to CreateContinuation + ResumeContinuation but
    /// as a single atomic step.
    ///
    /// The DoExpr can be any yieldable value:
    /// - DoThunk: VM calls to_generator(), runs generator in continuation scope
    /// - Effect: VM dispatches through continuation's handler stack
    ///
    /// Primary use: KPC handler resolving args with the full callsite handler
    /// chain (captured via GetHandlers), avoiding busy boundary issues.
    Eval {
        /// The DoExpr to evaluate (Effect or DoThunk)
        expr: Py<PyAny>,
        /// Handler chain for the continuation's scope (from GetHandlers)
        handlers: Vec<Handler>,
    },

    /// GetCallStack - Walk frames and return call stack metadata. [R9-B]
    ///
    /// Pure Rust frame walk — no GIL, no Python interaction.
    /// Returns Vec<CallMetadata> from PythonGenerator frames that have metadata.
    /// Walks current segment + caller chain (innermost frame first).
    /// Analogous to GetHandlers (structural VM inspection, not an effect).
    GetCallStack,
}
```

**Note**: There is no `Return` DoCtrl. Handler return is implicit:
when a handler program finishes, the VM applies `handle_handler_return(value)`
semantics (return to caller; root handler return abandons callsite).

**Async note**: `PythonAsyncSyntaxEscape` yields `PythonCall::CallAsync` via
`handle_do_ctrl`. It is only valid under `async_run` / `VM.run_async`.

---

## Primitive Handlers

These implementations show how DoCtrls modify VM state and return the next Mode.

### WithHandler (Creates Prompt + Body Structure)

```rust
impl VM {
    /// Install a handler and run a program under it.
    /// 
    /// Creates the following structure:
    /// 
    ///   outside_seg          <- current_segment (where result goes)
    ///        ^
    ///        |
    ///   prompt_seg           <- handler boundary (abandon returns here)
    ///        ^                  kind = PromptBoundary { handled_marker }
    ///        |
    ///   body_seg             <- body program runs here
    ///                           scope_chain = [handler_marker] ++ outside.scope_chain
    ///
    /// Returns: PythonCall to start body program (caller returns NeedsPython)
    fn handle_with_handler(&mut self, handler: Handler, expr: Py<PyAny>) -> PythonCall {
        let handler_marker = Marker::fresh();
        let outside_seg_id = self.current_segment;
        let outside_scope = self.segments[outside_seg_id.index()].scope_chain.clone();
        
        // 1. Create prompt segment (handler boundary)
        //    scope_chain = outside's scope (handler NOT in scope at prompt level)
        let prompt_seg = Segment::new_prompt(
            handler_marker,
            Some(outside_seg_id),  // returns to outside
            outside_scope.clone(),
            handler_marker,
        );
        let prompt_seg_id = self.alloc_segment(prompt_seg);
        
        // 2. Register handler WITH prompt_seg_id (no search needed later)
        self.handlers.insert(handler_marker, HandlerEntry {
            handler,
            prompt_seg_id,
        });
        
        // 3. Create body segment with handler in scope
        //    scope_chain = [handler_marker] ++ outside_scope (innermost first)
        let mut body_scope = vec![handler_marker];
        body_scope.extend(outside_scope);
        
        let body_seg = Segment::new(
            handler_marker,
            Some(prompt_seg_id),  // returns to PROMPT, not outside
            body_scope,
        );
        let body_seg_id = self.alloc_segment(body_seg);
        
        // 4. Switch to body segment
        self.current_segment = body_seg_id;
        
        // 5. Return PythonCall to start body DoExpr
        PythonCall::StartProgram { program: expr }
    }
}
```

### Dispatch (All Effects, Top-Only Busy Boundary)

```rust
impl VM {
    /// Start dispatching an effect to handlers. [R8-G]
    ///
    /// ALL effects go through this path. Two handler variants,
    /// one dispatch protocol.
    ///
    /// Returns Ok(StepEvent) if dispatch started successfully.
    /// Returns Err(VMError) if no handler found.
    fn start_dispatch(&mut self, effect: Effect) -> Result<StepEvent, VMError> {
        // Lazy pop completed dispatch contexts
        self.lazy_pop_completed();

        // Get current scope_chain
        let scope_chain = self.current_scope_chain();

        // Compute visible handlers (top-only busy exclusion)
        let handler_chain = self.visible_handlers(&scope_chain);

        if handler_chain.is_empty() {
            return Err(VMError::UnhandledEffect(effect));
        }

        // Find first handler that can handle this effect
        // RustProgram: can_handle() checks effect type
        // Python: can_handle() always true (handler decides via Delegate)
        let (handler_idx, handler_marker, entry) =
            self.find_matching_handler(&handler_chain, &effect)?;

        let prompt_seg_id = entry.prompt_seg_id;
        let handler = entry.handler.clone();

        let dispatch_id = DispatchId::fresh();

        // Capture callsite continuation
        let current_seg = &self.segments[self.current_segment.index()];
        let k_user = Continuation::capture(current_seg, self.current_segment, Some(dispatch_id));

        // Push dispatch context
        self.dispatch_stack.push(DispatchContext {
            dispatch_id,
            effect: effect.clone(),
            handler_chain: handler_chain.clone(),
            handler_idx,
            k_user: k_user.clone(),
            prompt_seg_id,
            completed: false,
        });

        // Create handler execution segment
        let handler_seg = Segment::new(
            handler_marker,
            Some(prompt_seg_id),
            scope_chain,
        );
        let handler_seg_id = self.alloc_segment(handler_seg);
        self.current_segment = handler_seg_id;

        // Invoke handler — two variants, same dispatch chain
        Ok(self.invoke_handler(handler, &effect, k_user))
    }

    /// Invoke a handler and return the next StepEvent. [R8-G]
    fn invoke_handler(
        &mut self,
        handler: Handler,
        effect: &Effect,
        k_user: Continuation,
    ) -> StepEvent {
        match handler {
            Handler::RustProgram(rust_handler) => {
                // Rust program handler: create program instance and step it.
                // Used by state, reader, writer, scheduler, and custom Rust handlers.
                let program = rust_handler.create_program();
                let step = {
                    let mut guard = program.lock().expect("Rust program lock poisoned");
                    guard.start(effect.clone(), k_user.clone(), &mut self.rust_store)
                };
                self.apply_rust_program_step(step, program)
            }
            Handler::Python(py_handler) => {
                // Python handler: call with (effect, k_user) and expect a Program
                // Driver converts Program to generator via to_generator()
                self.pending_python = Some(PendingPython::CallPythonHandler {
                    k_user: k_user.clone(),
                    effect: effect.clone(),
                });
                StepEvent::NeedsPython(PythonCall::CallHandler {
                    handler: py_handler,
                    effect: effect.clone(),
                    continuation: k_user,
                })
            }
        }
    }

    /// Apply a RustProgramStep and return the next StepEvent.
    fn apply_rust_program_step(
        &mut self,
        step: RustProgramStep,
        program: RustProgramRef,
    ) -> StepEvent {
        match step {
            RustProgramStep::Yield(yielded) => {
                let segment = &mut self.segments[self.current_segment.index()];
                segment.push_frame(Frame::RustProgram { program });
                self.mode = Mode::HandleYield(yielded);
                StepEvent::Continue
            }
            RustProgramStep::Return(value) => {
                self.mode = Mode::Deliver(value);
                StepEvent::Continue
            }
            RustProgramStep::Throw(exc) => {
                self.mode = Mode::Throw(exc);
                StepEvent::Continue
            }
            RustProgramStep::NeedsPython(call) => {
                // [R8-H] Handler needs a Python callback (e.g., Modify).
                // Re-push handler frame so resume() returns to it.
                let segment = &mut self.segments[self.current_segment.index()];
                segment.push_frame(Frame::RustProgram { program });
                self.pending_python = Some(PendingPython::RustProgramContinuation {
                    marker: self.dispatch_stack.last()
                        .map(|d| d.handler_marker)
                        .unwrap_or(Marker::fresh()),
                    k: Continuation::empty(),
                });
                StepEvent::NeedsPython(call)
            }
        }
    }

    // [R8-H] apply_handler_action() DELETED.
    // Was: fn apply_handler_action(&mut self, action: HandlerAction) -> StepEvent
    // HandlerAction/NeedsPython/StdlibContinuation no longer exist.
    // All handler actions now flow through RustProgramStep::Yield(Yielded::*)
    // and apply_rust_program_step(). Python callbacks from Modify are yielded
    // as RustProgramStep::Yield(Yielded::PythonCall(...)) by the handler program.

    /// Handle handler return (explicit or implicit).
    /// 
    /// Returns to the handler's caller segment. If the caller is the current
    /// dispatch's prompt boundary (root handler), this abandons the callsite
    /// and marks the dispatch completed.
    fn handle_handler_return(&mut self, value: Value) -> StepEvent {
        let Some(top) = self.dispatch_stack.last_mut() else {
            self.mode = Mode::Throw(PyException::runtime_error(
                "Return outside of dispatch"
            ));
            return StepEvent::Continue;
        };
        
        if let Some(caller_id) = self.segments[self.current_segment.index()].caller {
            if caller_id == top.prompt_seg_id {
                top.completed = true;
                self.consumed_cont_ids.insert(top.k_user.cont_id);
            }
        }
        
        self.mode = Mode::Deliver(value);
        StepEvent::Continue
    }
    
    /// Find first handler in chain that can handle the effect.
    /// 
    /// Returns (index, marker, entry) - index is the position in handler_chain.
    /// This index is CRITICAL for busy boundary computation.
    fn find_matching_handler(
        &self, 
        handler_chain: &[Marker], 
        effect: &Effect
    ) -> Result<(usize, Marker, HandlerEntry), VMError> {
        for (idx, &marker) in handler_chain.iter().enumerate() {
            if let Some(entry) = self.handlers.get(&marker) {
                if entry.handler.can_handle(effect) {
                    return Ok((idx, marker, entry.clone()));
                }
            }
        }
        Err(VMError::UnhandledEffect(effect.clone()))
    }
    
    /// Compute visible handlers (TOP-ONLY busy exclusion).
    /// 
    /// Only the current (topmost non-completed) dispatch creates a busy boundary.
    /// Visibility is computed from the CURRENT scope_chain so handlers installed
    /// inside a handler remain visible unless they are busy.
    fn visible_handlers(&self, scope_chain: &[Marker]) -> Vec<Marker> {
        let Some(top) = self.dispatch_stack.last() else {
            return scope_chain.to_vec();
        };
        
        if top.completed {
            return scope_chain.to_vec();
        }
        
        // Busy = handlers at indices 0..=handler_idx in top dispatch
        // Visible = current scope_chain minus busy handlers (preserve order)
        let busy: HashSet<Marker> = top.handler_chain[..=top.handler_idx]
            .iter()
            .copied()
            .collect();
        scope_chain
            .iter()
            .copied()
            .filter(|marker| !busy.contains(marker))
            .collect()
    }
    
    fn lazy_pop_completed(&mut self) {
        while let Some(top) = self.dispatch_stack.last() {
            if top.completed {
                self.dispatch_stack.pop();
            } else {
                break;
            }
        }
    }
    
    fn current_scope_chain(&self) -> Vec<Marker> {
        self.segments[self.current_segment.index()].scope_chain.clone()
    }
    
    // NOTE: find_prompt_seg_for_marker is REMOVED.
    // prompt_seg_id is now stored in HandlerEntry at WithHandler time.
    // No linear search needed - O(1) lookup via handlers.get(marker).
}
```

### Resume + Continuation Primitives

The following functions cover captured continuations (Resume/Transfer) and created
continuations (ResumeContinuation). They also define handler introspection primitives
(GetContinuation/GetHandlers).

```rust
impl VM {
    /// Resume a continuation with call-resume semantics.
    /// 
    /// The continuation's frames_snapshot is materialized into a new segment.
    /// The current segment becomes the caller (returns here after k completes).
    fn handle_resume(&mut self, k: Continuation, value: Value) -> Mode {
        if !k.started {
            return Mode::Throw(PyException::runtime_error(
                "Resume on unstarted continuation; use ResumeContinuation"
            ));
        }
        // One-shot check
        if self.consumed_cont_ids.contains(&k.cont_id) {
            return Mode::Throw(PyException::runtime_error(
                "Continuation already resumed"
            ));
        }
        self.consumed_cont_ids.insert(k.cont_id);
        
        // Lazy pop completed dispatches
        self.lazy_pop_completed();
        
        // Check dispatch completion
        // RULE: dispatch_id is only Some for callsite continuations
        if let Some(dispatch_id) = k.dispatch_id {
            if let Some(top) = self.dispatch_stack.last_mut() {
                if top.dispatch_id == dispatch_id && top.k_user.cont_id == k.cont_id {
                    top.completed = true;
                }
            }
        }
        
        // Materialize continuation into new execution segment
        // (shallow clone of frames, Frame is small)
        let exec_seg = Segment {
            marker: k.marker,
            frames: (*k.frames_snapshot).clone(),
            caller: Some(self.current_segment),  // call-resume: returns here
            scope_chain: (*k.scope_chain).clone(),
            kind: SegmentKind::Normal,
        };
        let exec_seg_id = self.alloc_segment(exec_seg);
        
        // Switch to execution segment
        self.current_segment = exec_seg_id;
        
        Mode::Deliver(value)
    }
    
    /// Transfer to a continuation (tail-transfer, non-returning).
    /// 
    /// Does NOT set up return link. Current handler is abandoned.
    /// Marks dispatch completed when the target is k_user.
    fn handle_transfer(&mut self, k: Continuation, value: Value) -> Mode {
        if !k.started {
            return Mode::Throw(PyException::runtime_error(
                "Transfer on unstarted continuation; use ResumeContinuation"
            ));
        }
        // One-shot check
        if self.consumed_cont_ids.contains(&k.cont_id) {
            return Mode::Throw(PyException::runtime_error(
                "Continuation already resumed"
            ));
        }
        self.consumed_cont_ids.insert(k.cont_id);
        
        // Lazy pop completed dispatches
        self.lazy_pop_completed();
        
        // Check dispatch completion (Transfer completes when resuming callsite)
        if let Some(dispatch_id) = k.dispatch_id {
            if let Some(top) = self.dispatch_stack.last_mut() {
                if top.dispatch_id == dispatch_id && top.k_user.cont_id == k.cont_id {
                    top.completed = true;
                }
            }
        }
        
        // Materialize continuation
        let exec_seg = Segment {
            marker: k.marker,
            frames: (*k.frames_snapshot).clone(),
            caller: None,  // tail-transfer: no return
            scope_chain: (*k.scope_chain).clone(),
            kind: SegmentKind::Normal,
        };
        let exec_seg_id = self.alloc_segment(exec_seg);
        
        // Switch to execution segment
        self.current_segment = exec_seg_id;
        
        Mode::Deliver(value)
    }

    /// Resume a captured or created continuation.
    /// 
    /// Captured: same as Resume (call-resume semantics).
    /// Created: installs handlers, starts program, returns to current segment.
    fn handle_resume_continuation(&mut self, k: Continuation, value: Value) -> StepEvent {
        if k.started {
            self.mode = self.handle_resume(k, value);
            return StepEvent::Continue;
        }
        
        // Unstarted continuation: value is ignored, program starts fresh.
        if self.consumed_cont_ids.contains(&k.cont_id) {
            self.mode = Mode::Throw(PyException::runtime_error(
                "Continuation already resumed"
            ));
            return StepEvent::Continue;
        }
        self.consumed_cont_ids.insert(k.cont_id);
        
        let Some(program) = k.program.clone() else {
            self.mode = Mode::Throw(PyException::runtime_error(
                "Unstarted continuation missing program"
            ));
            return StepEvent::Continue;
        };
        
        // Install handlers (outermost first, so innermost ends up closest to program)
        let mut outside_seg_id = self.current_segment;
        let mut outside_scope = self.segments[outside_seg_id.index()].scope_chain.clone();
        
        for handler in k.handlers.iter().rev() {
            let handler_marker = Marker::fresh();
            let prompt_seg = Segment::new_prompt(
                handler_marker,
                Some(outside_seg_id),
                outside_scope.clone(),
                handler_marker,
            );
            let prompt_seg_id = self.alloc_segment(prompt_seg);
            
            self.handlers.insert(handler_marker, HandlerEntry {
                handler: handler.clone(),
                prompt_seg_id,
            });
            
            let mut body_scope = vec![handler_marker];
            body_scope.extend(outside_scope);
            
            let body_seg = Segment::new(
                handler_marker,
                Some(prompt_seg_id),
                body_scope,
            );
            let body_seg_id = self.alloc_segment(body_seg);
            
            outside_seg_id = body_seg_id;
            outside_scope = self.segments[body_seg_id.index()].scope_chain.clone();
        }
        
        self.current_segment = outside_seg_id;
        // WithHandler body has no call metadata (it's a handler scope, not a @do call)
        self.pending_python = Some(PendingPython::StartProgramFrame {
            metadata: None,
        });
        StepEvent::NeedsPython(PythonCall::StartProgram { program })
    }

    /// Handle a DoCtrl, returning the next StepEvent.
    fn handle_do_ctrl(&mut self, prim: DoCtrl) -> StepEvent {
        // Drop completed dispatches before inspecting handler context.
        self.lazy_pop_completed();
        match prim {
            DoCtrl::Resume { continuation, value } => {
                self.mode = self.handle_resume(continuation, value);
                StepEvent::Continue
            }
            DoCtrl::Transfer { continuation, value } => {
                self.mode = self.handle_transfer(continuation, value);
                StepEvent::Continue
            }
            DoCtrl::Delegate { effect } => {
                // Delegate to OUTER handler (advance in SAME dispatch, not new dispatch)
                self.handle_delegate(effect)
            }
            DoCtrl::WithHandler { handler, expr } => {
                // WithHandler needs PythonCall to start body DoExpr (no call metadata)
                let call = self.handle_with_handler(handler, expr);
                self.pending_python = Some(PendingPython::StartProgramFrame {
                    metadata: None,
                });
                StepEvent::NeedsPython(call)
            }
            DoCtrl::PythonAsyncSyntaxEscape { action } => {
                // Async-only escape to event loop
                self.pending_python = Some(PendingPython::AsyncEscape);
                StepEvent::NeedsPython(PythonCall::CallAsync {
                    func: action,
                    args: vec![],
                })
            }
            DoCtrl::GetContinuation => {
                let Some(top) = self.dispatch_stack.last() else {
                    self.mode = Mode::Throw(PyException::runtime_error(
                        "GetContinuation called outside handler context"
                    ));
                    return StepEvent::Continue;
                };
                self.mode = Mode::Deliver(Value::Continuation(top.k_user.clone()));
                StepEvent::Continue
            }
            DoCtrl::GetHandlers => {
                let Some(top) = self.dispatch_stack.last() else {
                    self.mode = Mode::Throw(PyException::runtime_error(
                        "GetHandlers called outside handler context"
                    ));
                    return StepEvent::Continue;
                };
                // Return full handler_chain from callsite scope (innermost first)
                let mut handlers = Vec::new();
                for marker in top.handler_chain.iter() {
                    let Some(entry) = self.handlers.get(marker) else {
                        self.mode = Mode::Throw(PyException::runtime_error(
                            "GetHandlers: missing handler entry"
                        ));
                        return StepEvent::Continue;
                    };
                    handlers.push(entry.handler.clone());
                }
                self.mode = Mode::Deliver(Value::Handlers(handlers));
                StepEvent::Continue
            }
            DoCtrl::CreateContinuation { expr, handlers } => {
                let cont = Continuation::create(expr, handlers);
                self.mode = Mode::Deliver(Value::Continuation(cont));
                StepEvent::Continue
            }
            DoCtrl::ResumeContinuation { continuation, value } => {
                self.handle_resume_continuation(continuation, value)
            }
            DoCtrl::Call { f, args, kwargs, metadata } => {
                // [R9-A] Call f(*args, **kwargs) and push result as generator frame.
                // Store metadata so receive_python_result attaches it to the frame.
                self.pending_python = Some(PendingPython::StartProgramFrame {
                    metadata: Some(metadata),
                });
                if args.is_empty() && kwargs.is_empty() {
                    // DoThunk path: f is a DoThunk, driver calls to_generator()
                    StepEvent::NeedsPython(PythonCall::StartProgram { program: f })
                } else {
                    // Kernel path: driver calls f(*args, **kwargs), gets generator
                    StepEvent::NeedsPython(PythonCall::CallFunc {
                        func: f,
                        args: CallArgs::from_values(args, kwargs),
                    })
                }
            }
            DoCtrl::Eval { expr, handlers } => {
                // [R9-H] Evaluate a DoExpr in a fresh scope with given handlers.
                // Atomically equivalent to CreateContinuation + ResumeContinuation.
                let cont = Continuation::create_unstarted(expr, handlers);
                self.handle_resume_continuation(cont, Value::None)
            }
            DoCtrl::GetCallStack => {
                // [R9-B] Walk frames across segments, collect CallMetadata.
                let mut stack = Vec::new();
                let mut seg_id = self.current_segment;
                while let Some(id) = seg_id {
                    let seg = &self.segments[id.index()];
                    for frame in seg.frames.iter().rev() {
                        if let Frame::PythonGenerator { metadata: Some(m), .. } = frame {
                            stack.push(m.clone());
                        }
                    }
                    seg_id = seg.caller;
                }
                self.mode = Mode::Deliver(Value::CallStack(stack));
                StepEvent::Continue
            }
            _ => {
                self.mode = Mode::Throw(PyException::not_implemented(
                    format!("Primitive not yet implemented: {:?}", prim)
                ));
                StepEvent::Continue
            }
        }
    }
    
    /// Handle Delegate: advance to outer handler in SAME DispatchContext.
    /// 
    /// Unlike start_dispatch (which creates NEW dispatch for perform-site effects),
    /// Delegate advances handler_idx within the current dispatch.
    /// 
    /// INVARIANT: Delegate can only be called from a handler execution context.
    /// The top of dispatch_stack is the current dispatch.
    fn handle_delegate(&mut self, effect: Effect) -> StepEvent {
        // Get current dispatch context
        let top = self.dispatch_stack.last_mut()
            .expect("Delegate called outside of dispatch context");
        
        // Capture the inner handler segment so Delegate can return to it.
        let inner_seg_id = self.current_segment;
        
        // [D3] Clear the delegating handler's frames so return values pass through
        // without trying to resume the handler generator (Delegate is tail-position).
        if let Some(seg) = self.segments.get_mut(inner_seg_id) {
            seg.frames.clear();
        }
        
        // Advance handler_idx to find next handler that can handle this effect
        let handler_chain = &top.handler_chain;
        let start_idx = top.handler_idx + 1;  // Start from next handler (outer)
        
        // Find matching handler in remaining chain
        for idx in start_idx..handler_chain.len() {
            let marker = handler_chain[idx];
            if let Some(entry) = self.handlers.get(&marker) {
                if entry.handler.can_handle(&effect) {
                    // Found matching outer handler
                    // Update handler_idx (SAME dispatch, not new)
                    top.handler_idx = idx;
                    top.effect = effect.clone();  // May be a different effect than original
                    
                    let handler = entry.handler.clone();
                    
                    // Use original callsite continuation (k_user) for outer handler
                    
                    // Create new handler execution segment for outer handler.
                    // NOTE: caller is the inner handler segment so outer return
                    // flows back to inner (result of Delegate).
                    let scope_chain = self.current_scope_chain();
                    let handler_seg = Segment::new(
                        marker,
                        Some(inner_seg_id),
                        scope_chain,
                    );
                    let handler_seg_id = self.alloc_segment(handler_seg);
                    self.current_segment = handler_seg_id;
                    
                    // Invoke outer handler
                    return self.invoke_handler(handler, &effect, top.k_user.clone());
                }
            }
        }
        
        // No outer handler found
        self.mode = Mode::Throw(PyException::runtime_error(
            format!("Delegate: no outer handler for effect {:?}", effect)
        ));
        StepEvent::Continue
    }
}
```

---

## Memory Management

### Segment Pool

```rust
impl VM {
    fn alloc_segment(&mut self, segment: Segment) -> SegmentId {
        if let Some(id) = self.free_segments.pop() {
            self.segments[id.0 as usize] = segment;
            id
        } else {
            let id = SegmentId(self.segments.len() as u32);
            self.segments.push(segment);
            id
        }
    }
    
    fn free_segment(&mut self, id: SegmentId) {
        self.segments[id.0 as usize] = Segment::new(
            Marker(0), None, Vec::new()
        );
        self.free_segments.push(id);
    }
}
```

### Callback Lifecycle

```rust
// Callbacks are stored in Store.callbacks (SlotMap).
// 
// 1. Register: store.register_callback(Box::new(|v, vm| ...)) -> CallbackId
// 2. Frame holds: Frame::RustReturn { cb: CallbackId }
// 3. Execute: store.consume_callback(cb) removes and returns the callback
// 4. Callback is consumed (FnOnce) and dropped after execution
//
// This allows Frames to be Clone (CallbackId is Copy) while
// still supporting FnOnce semantics for callbacks.
```

### PyObject Lifecycle

```rust
// PyObjects in Value::Python are Py<PyAny> which are GIL-independent.
// They are reference-counted by Python's GC.
// 
// When a Value::Python is dropped, the Py<PyAny> decrements the refcount.
// This happens automatically via Drop.
//
// IMPORTANT: Dropping Py<PyAny> without GIL is safe but may defer
// the actual Python object destruction until next GIL acquisition.
```

---

## Invariants

### INV-1: GIL Boundaries

```
GIL is ONLY held during:
  - PythonCall execution
  - Value::to_pyobject / from_pyobject (driver only)
  - Effect::to_pyobject / Continuation::to_pyobject (driver only)
  - Final result extraction

GIL is RELEASED during:
  - vm.step() execution
  - RustProgram handler execution (standard handlers)
  - Segment/frame management
```

### INV-2: Segment Ownership

```
All segments are owned by VM.segments arena.
Continuations hold snapshots (Arc<Vec<Frame>>), not segment references.
Resume materializes snapshot into fresh segment.
Segment can only be mutated via VM methods.
```

### INV-3: One-Shot Continuations

```
ContId is checked in consumed_cont_ids before resume.
Double-resume returns Error, not panic.

Within a single dispatch, the callsite continuation (k_user) must be consumed
exactly once (Resume/Transfer/Return). Any attempt to resume again (including
after Delegate returns) is a runtime error.

Multi-shot continuations are not supported. All continuations are one-shot only.
```

### INV-4: Scope Chain in Segment

```
Each Segment carries its own scope_chain.
Switching segments automatically restores scope.
No separate "current scope_chain" in VM state.
```

### INV-5: WithHandler Structure

```
WithHandler(h, body) at current_segment creates:

  prompt_seg:
    marker = handler_marker
    kind = PromptBoundary { handled_marker: handler_marker }
    caller = current_segment (outside)
    scope_chain = outside.scope_chain  // handler NOT in scope

  body_seg:
    marker = handler_marker
    kind = Normal
    caller = prompt_seg_id
    scope_chain = [handler_marker] ++ outside.scope_chain  // handler IN scope
```

### INV-6: Handler Execution Structure

```
start_dispatch creates:

  handler_exec_seg:
    marker = handler_marker
    kind = Normal
    caller = prompt_seg_id  // root handler return goes to prompt, not callsite
    scope_chain = callsite.scope_chain  // same scope as effect callsite
```

### INV-7: Dispatch ID Assignment

```
dispatch_id is Some IFF continuation is callsite (k_user).
All other continuations (handler-local, scheduler) have dispatch_id = None.

Completion check requires BOTH:
  k.dispatch_id == Some(top.dispatch_id) AND
  k.cont_id == top.k_user.cont_id

Resume, Transfer, and Return all mark completion when they resolve k_user.
```

### INV-8: Busy Boundary (Top-Only)

```
Only the topmost non-completed dispatch creates a busy boundary.
Busy handlers = top.handler_chain[0..=top.handler_idx]
Visible handlers = current scope_chain minus busy handlers (preserve order)

This is MORE PERMISSIVE than union-all. Nested dispatches can see
handlers that are busy in outer dispatches, which matches algebraic
effect semantics (handlers are in scope based on their installation
point, not based on what's currently executing). Handlers installed
inside a handler remain visible unless they are busy.
```

### INV-9: All Effects Go Through Dispatch

```
ALL effects (including standard Get, Put, Modify, Ask, Tell) go through
the dispatch stack. There is NO bypass for any effect type. [R8-B]

Standard handlers are Rust-implemented (RustProgram) for performance but still:
  - Are installed via WithHandler or run(handlers=[...]) (explicit)
  - Go through dispatch (found via handler_chain lookup)
  - Can be intercepted, overridden, or replaced by users

To intercept state operations, install a custom handler that handles
Get/Put effects before the standard state handler in the scope chain.
```

### INV-10: Frame Stack Order

```
Frame stack top = LAST element of Vec (index frames.len()-1).
push_frame = frames.push() [O(1)]
pop_frame = frames.pop() [O(1)]

This avoids O(n) shifts from remove(0).
```

### INV-11: Segment Frames Are the Only Mutable Continuation State

```
Segment.frames is the ONLY mutable state during execution.

- Segment.frames: mutable Vec<Frame>, push/pop during execution
- Continuation.frames_snapshot: immutable Arc<Vec<Frame>>, frozen at capture

When a Continuation is captured:
  frames_snapshot = Arc::new(segment.frames.clone())

When a Continuation is resumed:
  new_segment.frames = (*k.frames_snapshot).clone()

This allows multiple Continuations to share frames via Arc while
each execution gets its own mutable working copy.
```

### INV-12: Continuation Kinds

```
Continuation has two kinds:

  started=true  (captured):
    - frames_snapshot/scope_chain/marker/dispatch_id are valid
    - program=None, handlers=[]

  started=false (created):
    - program/handlers are valid
    - frames_snapshot empty, scope_chain empty, dispatch_id=None
```

### INV-13: Step Event Classification

```
step() returns exactly one of:
  - Continue: internal transition, no Python needed, keep stepping
  - NeedsPython(call): must execute Python call, then receive_python_result()
  - Done(value): computation completed successfully
  - Error(e): computation failed

The driver loop spins on Continue (in allow_threads), only acquiring
GIL when NeedsPython is returned.
```

### INV-14: Mode Transitions

```
Mode transitions are deterministic:

  Deliver(v) + frames.pop() →
    - RustReturn: callback returns new Mode
    - RustProgram: resume → Yield/Return/Throw
    - PythonGenerator: NeedsPython(GenSend/GenNext)
    - empty frames: Return(v)

  Throw(e) + frames.pop() →
    - RustReturn: propagate (callbacks don't catch)
    - RustProgram: throw → Yield/Return/Throw
    - PythonGenerator: NeedsPython(GenThrow)
    - empty frames + caller: propagate up
    - empty frames + no caller: Error

  HandleYield(y) →
    - Primitive: handle_do_ctrl returns StepEvent (Continue or NeedsPython)
    - Effect: start_dispatch returns StepEvent (Continue or NeedsPython)
    - Program: NeedsPython(StartProgram)
    - Unknown: Throw(TypeError)

  Return(v) →
    - caller exists: switch to caller, Deliver(v)
    - no caller: Done(v)
```

### INV-15: Generator Protocol

```
Python generators have three outcomes:

  yield value → PyCallOutcome::GenYield(Yielded)
    → Driver classifies (with GIL): Primitive/Effect/Program/Unknown
    → VM receives pre-classified Yielded
    → Mode::HandleYield(yielded)

  return value (StopIteration) → PyCallOutcome::GenReturn(value)
    → frame consumed, value flows to caller
    → Mode::Deliver(value)

  raise exception → PyCallOutcome::GenError(exc)
    → Mode::Throw(exc)

StartProgram/CallFunc/CallAsync/CallHandler return PyCallOutcome::Value(value) - NOT a generator step.
CallHandler returns Value::Python(generator) after to_generator().
VM pushes Frame::PythonGenerator with started=false and metadata (from pending) when value is Value::Python(generator).

Generator start uses GenNext (__next__).
Generator resume uses GenSend (send).
Exception injection uses GenThrow (throw).

Rust program handlers mirror this protocol in Rust:
  - Yield → Mode::HandleYield(yielded)
  - Return → Mode::Deliver(value)
  - Throw → Mode::Throw(exc)
```

---

## Legacy Specs (Deprecated) — Differences

SPEC-CESK-006 and SPEC-CESK-007 are deprecated. This spec (008) is authoritative.
Key differences and decisions in 008:

- Busy boundary is **top-only**: only the topmost non-completed dispatch excludes
  busy handlers; nested dispatch does not consider older frames.
- `Delegate` is the only forwarding primitive; yielding a raw effect starts a new
  dispatch (does not forward).
- `yield Delegate(effect)` returns the **outer handler's return value**; after
  Delegate returns, the handler must return (no Resume).
- Handler return is implicit; there is no `Return` DoCtrl.
- Program input is **ProgramBase only** (KleisliProgramCall or EffectBase); raw
  generators are rejected except via `start_with_generator()`.
- Continuations are one-shot only; multi-shot is not supported.
- Rust program handlers (RustProgramHandler/RustHandlerProgram) are first-class.
- `Call(f, args, kwargs, metadata)` is a DoCtrl for function invocation with
  call stack metadata (R9-A). `Eval(expr, handlers)` evaluates a DoExpr in a fresh scope
  (R9-H). `GetCallStack` walks frames (R9-B). `Yielded::Program` is kept as legacy
  fallback (R9-E).

---

## Crate Structure

```
doeff-vm/
├── Cargo.toml
├── pyproject.toml
├── src/
│   ├── lib.rs           # Module root, PyO3 bindings
│   ├── vm.rs            # VM struct, Mode, step loop
│   ├── step.rs          # StepEvent, step_* functions
│   ├── segment.rs       # Segment, SegmentKind
│   ├── frame.rs         # Frame enum, Callback type
│   ├── continuation.rs  # Continuation with Arc snapshot
│   ├── dispatch.rs      # DispatchContext, visible_handlers
│   ├── do_ctrl.rs       # DoCtrl enum
│   ├── yielded.rs       # Yielded enum, classification
│   ├── handlers/
│   │   ├── mod.rs
│   │   ├── state.rs     # State handler (Get, Put)
│   │   ├── reader.rs    # Reader handler (Ask)
│   │   └── writer.rs    # Writer handler (Tell)
│   ├── rust_store.rs    # RustStore (standard handler state: state, env, log)
│   ├── value.rs         # Value enum (Rust/Python interop)
│   ├── python_call.rs   # PythonCall, PyCallOutcome, PyException
│   ├── driver.rs        # PyVM wrapper, driver loop
│   └── error.rs         # VMError enum
└── tests/
    └── ...
```

Implementation tasks and migration phases are tracked in
`ISSUE-rust-vm-implementation.md`, not in this spec.

---

## References

- PyO3 Guide: https://pyo3.rs/
- Rust Book: Ownership and Lifetimes
- "Retrofitting Effect Handlers onto OCaml" (PLDI 2021) - segment-based continuation design
- slotmap crate: https://docs.rs/slotmap/
- maturin: https://www.maturin.rs/
