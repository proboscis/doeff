# SPEC-CESK-008: Rust VM for Algebraic Effects

## Status: Draft (Revision 7)

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
│    │ frames │◄─────►│ RustCb │       │ stack      │            │
│    │ caller │       └────────┘       │            │            │
│    │ scope  │                        │ visible_   │            │
│    └────────┘                        │ handlers() │            │
│        │                             └────────────┘            │
│        │              Primitives                                │
│        │             ┌─────────────────────────────┐           │
│        └────────────►│ Resume, Transfer, Delegate  │           │
│                      │ WithHandler, GetContinuation│           │
│                      └─────────────────────────────┘           │
│                                                                 │
│    3-Layer State Model                                          │
│    ┌──────────────────────────────────────────────┐            │
│    │ L1: Internals (hidden)                       │            │
│    │     dispatch_stack, segments, callbacks      │            │
│    ├──────────────────────────────────────────────┤            │
│    │ L2: RustStore (stdlib state)                 │            │
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

### ADR-2: All Effects Go Through Dispatch (No Built-in Bypass)

**Decision**: ALL effects go through dispatch, including State/Reader/Writer. No special-case bypass.

**Rationale**:
- Algebraic effects principle: "handlers give meaning to effects"
- Users can intercept, override, or replace any effect (logging, persistence, testing)
- Single dispatch path simplifies spec and implementation
- Stdlib handlers are Rust-implemented for speed, but still user-installable

**Performance**: Stdlib handlers in Rust avoid Python calls. Dispatch overhead is minimal (lookup + function pointer call).

**Stdlib Installation**:
```python
vm = doeff.VM()
stdlib = vm.stdlib()  # Returns Rust-implemented handlers

# User explicitly installs stdlib handlers
prog = with_handler(stdlib.state,
         with_handler(stdlib.writer,
           user_program()))
result = vm.run(prog)

# User can observe stdlib state
print(stdlib.state.items())
print(stdlib.writer.logs())

# User can replace stdlib with custom implementation
custom_state = MyPersistentStateHandler()
prog = with_handler(custom_state, user_program())
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

### Principle 3: Explicit Continuations

Handlers (Rust or Python) receive continuations explicitly:

```rust
// Rust handler signature
fn handle_effect(effect: &Effect, k: Continuation, vm: &mut VM) -> Control;

// Python handler signature (via PyO3)
// def handler(effect, k) -> Program[Any]
```

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

/// Unique identifier for continuations (one-shot tracking)
#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug)]
pub struct ContId(u64);

/// Unique identifier for dispatches
#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug)]
pub struct DispatchId(u64);

/// Unique identifier for callbacks in VM table
#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug)]
pub struct CallbackId(u32);

/// Unique identifier for runnable continuations
#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug)]
pub struct RunnableId(u64);

impl Marker {
    pub fn fresh() -> Self {
        static COUNTER: AtomicU64 = AtomicU64::new(1);
        Marker(COUNTER.fetch_add(1, Ordering::Relaxed))
    }
}
```

### Frame (Clone-able via CallbackId)

```rust
/// A frame in the continuation stack.
/// 
/// Frames are Clone because they may be captured in continuations.
/// FnOnce callbacks are stored separately in VM.callbacks table.
#[derive(Debug, Clone)]
pub enum Frame {
    /// Rust-native return frame (for stdlib handlers).
    /// The actual callback is in VM.callbacks[cb].
    RustReturn {
        cb: CallbackId,
    },
    
    /// Python generator frame (user code or Python handlers)
    PythonGenerator {
        /// The Python generator object (GIL-independent storage)
        generator: Py<PyAny>,
        /// Whether this generator has been started (first __next__ called)
        started: bool,
    },
}

/// Callback type stored in VM.callbacks table.
/// Consumed (removed) when executed.
pub type Callback = Box<dyn FnOnce(Value, &mut VM) -> Control + Send>;
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
/// Capturable continuation (subject to one-shot check).
/// 
/// Contains a snapshot of frames and scope_chain at capture time.
/// Resume materializes this snapshot into a new execution segment.
#[derive(Debug, Clone)]
pub struct Continuation {
    /// Unique identifier for one-shot tracking
    pub cont_id: ContId,
    
    /// Original segment this was captured from (for debugging/reference)
    pub segment_id: SegmentId,
    
    /// Frozen frames at capture time
    pub frames_snapshot: Arc<Vec<Frame>>,
    
    /// Frozen scope_chain at capture time
    pub scope_chain: Arc<Vec<Marker>>,
    
    /// Handler marker this continuation belongs to.
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
        }
    }
}
```

### RunnableContinuation (Internal)

```rust
/// Ready-to-run continuation. INTERNAL to scheduler.
/// 
/// Created by ResumeThenTransfer for scheduler queues.
#[derive(Debug)]
pub(crate) struct RunnableContinuation {
    /// Unique identifier for execution tracking
    pub runnable_id: RunnableId,
    
    /// The continuation to resume
    pub continuation: Continuation,
    
    /// Value to deliver when executed
    pub pending_value: Value,
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
    
    /// cont_id of k_user (for completion detection)
    pub callsite_cont_id: ContId,
    
    /// Prompt segment for this handler (captured at WithHandler time)
    /// Handler returns and abandon go HERE.
    pub prompt_seg_id: SegmentId,
    
    /// Marked true when Resume targets callsite
    pub completed: bool,
}
```

### Value (Python-Rust Interop)

```rust
/// A value that can flow through the VM.
/// 
/// Can be either a Rust-native value or a Python object.
#[derive(Debug, Clone)]
pub enum Value {
    /// Python object (GIL-independent)
    Python(Py<PyAny>),
    
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
}

impl Value {
    /// Convert to Python object (requires GIL)
    pub fn to_pyobject<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        match self {
            Value::Python(obj) => Ok(obj.bind(py).clone()),
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
/// ALL effects go through dispatch - no bypass, no special cases.
/// Stdlib effects (Get, Put, Ask, Tell) are handled by stdlib handlers
/// which are Rust-implemented for speed but still user-installable.
#[derive(Debug, Clone)]
pub enum Effect {
    // === Stdlib effects (handled by StdStateHandler, StdReaderHandler, StdWriterHandler) ===
    
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
            Effect::Python(_) => "Python",
        }
    }
    
    /// Check if this is a stdlib effect (for handler matching convenience).
    /// NOTE: This does NOT mean bypass - all effects still go through dispatch.
    pub fn is_stdlib(&self) -> bool {
        !matches!(self, Effect::Python(_))
    }
}
```

### Handler (Rust Stdlib + Python User)

```rust
/// A handler that can process effects.
/// 
/// Handlers are installed via WithHandler and matched during dispatch.
/// Stdlib handlers are Rust-implemented for speed but still go through dispatch.
#[derive(Debug, Clone)]
pub enum Handler {
    /// Stdlib handler (Rust-implemented, fast path)
    /// These handlers have direct access to RustStore.
    Stdlib(StdlibHandler),
    
    /// Python handler function
    /// Signature: def handler(effect, k) -> Program[Any]
    Python(Py<PyAny>),
}

/// Stdlib handlers (Rust-implemented).
/// 
/// These are the "batteries included" handlers for common effects.
/// They're Rust for performance but users can replace them with
/// custom Python handlers if needed.
#[derive(Debug, Clone)]
pub enum StdlibHandler {
    /// State handler (Get, Put, Modify)
    /// Backed by RustStore.state
    State(StdStateHandler),
    
    /// Reader handler (Ask)
    /// Backed by RustStore.env
    Reader(StdReaderHandler),
    
    /// Writer handler (Tell)
    /// Backed by RustStore.log
    Writer(StdWriterHandler),
}

impl Handler {
    /// Check if this handler can handle the given effect.
    pub fn can_handle(&self, effect: &Effect) -> bool {
        match self {
            Handler::Stdlib(stdlib) => stdlib.can_handle(effect),
            Handler::Python(py_handler) => {
                // Python handlers handle Python effects
                // (actual matching done by Python code)
                matches!(effect, Effect::Python(_))
            }
        }
    }
}

impl StdlibHandler {
    pub fn can_handle(&self, effect: &Effect) -> bool {
        match (self, effect) {
            (StdlibHandler::State(_), Effect::Get { .. }) => true,
            (StdlibHandler::State(_), Effect::Put { .. }) => true,
            (StdlibHandler::State(_), Effect::Modify { .. }) => true,
            (StdlibHandler::Reader(_), Effect::Ask { .. }) => true,
            (StdlibHandler::Writer(_), Effect::Tell { .. }) => true,
            _ => false,
        }
    }
    
    /// Handle an effect and return a HandlerAction.
    /// 
    /// Most stdlib handlers operate on RustStore directly - no Python calls needed.
    /// Exception: Modify returns NeedsPython to call the modifier function.
    pub fn handle(
        &self, 
        effect: &Effect, 
        k: Continuation, 
        store: &mut RustStore
    ) -> HandlerAction {
        match self {
            StdlibHandler::State(h) => h.handle(effect, k, store),
            StdlibHandler::Reader(h) => h.handle(effect, k, store),
            StdlibHandler::Writer(h) => h.handle(effect, k, store),
        }
    }
}
```

### Stdlib Handler Implementations

The stdlib handlers provide Rust-native implementations of common effects.
They read/write `RustStore` directly, avoiding Python calls for maximum performance.

**IMPORTANT**: Some stdlib effects (like `Modify`) need to call Python.
The `HandlerAction` enum supports this via `NeedsPython`.

```rust
/// Action returned by handlers (stdlib or user).
/// 
/// This tells the VM what to do after handling an effect.
/// CRITICAL: Includes NeedsPython for effects that require Python calls (e.g., Modify).
pub enum HandlerAction {
    /// Resume the continuation with a value (pure Rust, no Python needed)
    Resume { k: Continuation, value: Value },
    
    /// Transfer control to continuation (tail call, no return)
    Transfer { k: Continuation, value: Value },
    
    /// Return a value (abandon continuation)
    Return { value: Value },
    
    /// Need to call Python before completing the handler action.
    /// Used by Modify (calls modifier function), async escapes, etc.
    /// 
    /// After Python returns, VM will call handler.continue_after_python(result).
    NeedsPython {
        /// The Python call to make
        call: PythonCall,
        /// Continuation to resume after Python returns
        k: Continuation,
        /// Context for continue_after_python (handler-specific)
        context: HandlerContext,
    },
}

/// Context for continuing handler after Python call.
/// 
/// Used by NeedsPython to remember what the handler was doing.
#[derive(Debug, Clone)]
pub enum HandlerContext {
    /// Modify: waiting for modifier(old_value) result
    ModifyPending { key: String, old_value: Value },
    /// Future extensions...
}

/// State handler (Get, Put, Modify).
/// 
/// Backed by RustStore.state.
#[derive(Debug, Clone, Default)]
pub struct StdStateHandler;

impl StdStateHandler {
    pub fn new() -> Self {
        StdStateHandler
    }
    
    pub fn handle(
        &self, 
        effect: &Effect, 
        k: Continuation, 
        store: &mut RustStore
    ) -> HandlerAction {
        match effect {
            Effect::Get { key } => {
                let value = store.get(key).cloned().unwrap_or(Value::None);
                // Resume with the value - this is "resumptive" handling
                HandlerAction::Resume { k, value }
            }
            
            Effect::Put { key, value } => {
                store.put(key.clone(), value.clone());
                HandlerAction::Resume { k, value: Value::Unit }
            }
            
            Effect::Modify { key, modifier } => {
                // Modify requires calling Python (the modifier function)
                // 1. Get old value
                let old_value = store.get(key).cloned().unwrap_or(Value::None);
                
                // 2. Return NeedsPython to call modifier(old_value)
                HandlerAction::NeedsPython {
                    call: PythonCall::CallFunc {
                        func: modifier.clone(),
                        args: vec![old_value.clone()],
                    },
                    k,
                    context: HandlerContext::ModifyPending {
                        key: key.clone(),
                        old_value,
                    },
                }
            }
            
            _ => panic!("StdStateHandler cannot handle {:?}", effect),
        }
    }
    
    /// Continue handling after Python call returned.
    /// 
    /// Called by VM after NeedsPython completes.
    pub fn continue_after_python(
        &self,
        result: Value,
        context: HandlerContext,
        k: Continuation,
        store: &mut RustStore,
    ) -> HandlerAction {
        match context {
            HandlerContext::ModifyPending { key, old_value } => {
                // result = new_value from modifier(old_value)
                store.put(key, result);
                // Resume with old_value (Modify returns old value)
                HandlerAction::Resume { k, value: old_value }
            }
        }
    }
}

/// Reader handler (Ask).
/// 
/// Backed by RustStore.env.
#[derive(Debug, Clone, Default)]
pub struct StdReaderHandler;

impl StdReaderHandler {
    pub fn new() -> Self {
        StdReaderHandler
    }
    
    pub fn handle(
        &self, 
        effect: &Effect, 
        k: Continuation, 
        store: &mut RustStore
    ) -> HandlerAction {
        match effect {
            Effect::Ask { key } => {
                let value = store.ask(key).cloned().unwrap_or(Value::None);
                HandlerAction::Resume { k, value }
            }
            
            _ => panic!("StdReaderHandler cannot handle {:?}", effect),
        }
    }
}

/// Writer handler (Tell).
/// 
/// Backed by RustStore.log.
#[derive(Debug, Clone, Default)]
pub struct StdWriterHandler;

impl StdWriterHandler {
    pub fn new() -> Self {
        StdWriterHandler
    }
    
    pub fn handle(
        &self, 
        effect: &Effect, 
        k: Continuation, 
        store: &mut RustStore
    ) -> HandlerAction {
        match effect {
            Effect::Tell { message } => {
                store.tell(message.clone());
                HandlerAction::Resume { k, value: Value::Unit }
            }
            
            _ => panic!("StdWriterHandler cannot handle {:?}", effect),
        }
    }
}
```

### Python API for Stdlib

Users install stdlib handlers via `vm.stdlib()`:

```python
# Python usage
vm = doeff.VM()
stdlib = vm.stdlib()

# Install stdlib handlers explicitly
prog = with_handler(stdlib.state,
         with_handler(stdlib.reader,
           with_handler(stdlib.writer,
             user_program())))

result = vm.run(prog)

# Observe stdlib state after execution
print(stdlib.state.items())    # Dict of state key-value pairs
print(stdlib.reader.env())     # Dict of environment bindings  
print(stdlib.writer.logs())    # List of logged messages

# Users can replace stdlib with custom handlers
class MyPersistentState:
    """Custom state handler that persists to database."""
    def __call__(self, effect, k):
        if isinstance(effect, Get):
            value = self.db.get(effect.key)
            return Resume(k, value)
        elif isinstance(effect, Put):
            self.db.put(effect.key, effect.value)
            return Resume(k, None)

# Custom handler intercepts state effects instead of stdlib
prog = with_handler(MyPersistentState(db),
         with_handler(stdlib.reader,
           user_program()))
```

### PythonCall and PendingPython (Purpose-Tagged Calls)

**CRITICAL**: When VM returns `NeedsPython`, it must also store `pending_python` 
to know what to do with the result. Different call types have different result handling.

```rust
/// A pending call into Python code.
/// 
/// IMPORTANT: Generators are NOT callables. This enum correctly
/// distinguishes between calling functions and advancing generators.
#[derive(Debug, Clone)]
pub enum PythonCall {
    /// Call a Python function (often returns a generator)
    CallFunc {
        func: Py<PyAny>,
        args: Vec<Value>,
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
    /// CallFunc for a Program body - result is generator to push as frame
    StartProgramFrame,
    
    /// GenNext/GenSend/GenThrow on a user generator frame
    /// On GenYield: re-push generator with started=true
    /// On GenReturn/GenError: generator is done, don't re-push
    StepUserGenerator {
        /// The generator being stepped (needed for re-push)
        generator: Py<PyAny>,
    },
    
    /// CallFunc for Python handler invocation
    /// Result is a Program generator that yields control primitives
    CallPythonHandler {
        /// Continuation to pass to handler
        k_user: Continuation,
        /// Effect being handled
        effect: Effect,
    },
    
    /// Stdlib handler needs Python (e.g., Modify calling modifier function)
    /// Result feeds back to handler's continue_after_python()
    StdlibContinuation {
        /// Which stdlib handler
        handler: StdlibHandler,
        /// Continuation (from HandlerAction::NeedsPython)
        k: Continuation,
        /// Context for continue_after_python
        context: HandlerContext,
    },
}
```

### Generator Frame Re-Push Rule

**CRITICAL INVARIANT**: When stepping a generator and it yields (not returns/errors),
the generator must be re-pushed to the current segment with `started=true`.

```
GenNext/GenSend/GenThrow → driver executes → PyCallOutcome::GenYield(yielded)
  ↓
receive_python_result:
  1. Re-push generator as Frame::PythonGenerator { generator, started: true }
  2. Set mode = HandleYield(yielded)
  
GenReturn/GenError → generator is DONE, do NOT re-push
  ↓
receive_python_result:
  1. Do NOT push any frame (generator consumed)
  2. Set mode = Deliver(value) or Throw(exception)
```

This ensures the generator frame exists when we need to send the next value.

---

## VM State (3-Layer Model)

The VM state is organized into three layers with clear separation of concerns:

| Layer | Name | Contents | Visibility |
|-------|------|----------|------------|
| **1** | `Internals` | dispatch_stack, consumed_ids, segments, callbacks | **NEVER** exposed to users |
| **2** | `RustStore` | state, env, log (stdlib data) | User-observable via stdlib handler APIs |
| **3** | `PyStore` | Python dict (optional) | User-owned free zone |

### Design Principles

1. **Internals are sacred**: Control flow structures that could break VM invariants are hidden
2. **RustStore is the source of truth**: Stdlib handlers read/write here; fast Rust access
3. **PyStore is an escape hatch**: Python handlers can store arbitrary data; VM doesn't read it
4. **No synchronization**: RustStore and PyStore are independent; no mirroring or sync
5. **Continuations don't snapshot S**: State is global (no backtracking by default)

### Layer 1: Internals (VM-internal, invisible to users)

Layer 1 fields are defined directly in the VM struct (see "VM Struct" below).
They include: `segments`, `free_segments`, `dispatch_stack`, `callbacks`, 
`consumed_cont_ids`, `consumed_runnable_ids`, `handlers`.

These structures maintain VM invariants and must NOT be accessible or 
modifiable by user code directly.

### Layer 2: RustStore (user-space, Rust HashMap)

```rust
/// Stdlib handler state. Rust-native for performance.
/// 
/// This is the "main memory" for stdlib effects (Get/Put/Ask/Tell).
/// Python handlers can access via PyO3-exposed read/write APIs.
/// 
/// Key design: Value can hold Py<PyAny>, so Python objects flow through.
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
    
    // === State operations (used by StdStateHandler) ===
    
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
    
    // === Environment operations (used by StdReaderHandler) ===
    
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
    
    // === Log operations (used by StdWriterHandler) ===
    
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
    
    /// One-shot tracking for runnables
    consumed_runnable_ids: HashSet<RunnableId>,
    
    /// Handler registry: marker -> HandlerEntry
    /// NOTE: Includes prompt_seg_id to avoid linear search
    handlers: HashMap<Marker, HandlerEntry>,
    
    // === Layer 2: RustStore (user-observable via stdlib APIs) ===
    
    /// Stdlib state (State/Reader/Writer handlers use this)
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
}
```

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
    
    /// Handle something yielded by a generator
    HandleYield(Yielded),
    
    /// Current segment is empty; return value to caller
    Return(Value),
}
```

### Yielded (Generator Output Classification)

**IMPORTANT**: Classification happens in the **driver** (with GIL), not in the VM.
The VM receives pre-classified `Yielded` values and operates without GIL.

```rust
/// Classification of what a generator yielded.
/// 
/// INVARIANT: Classification is done by DRIVER (GIL held), not VM.
/// VM receives Yielded and processes it without needing GIL.
pub enum Yielded {
    /// A control primitive (Resume, Transfer, WithHandler, etc.)
    Primitive(ControlPrimitive),
    
    /// An effect to be handled
    Effect(Effect),
    
    /// A nested Program to execute (if yield-based DSL supports this)
    Program(Py<PyAny>),
    
    /// Unknown object (will cause TypeError)
    Unknown(Py<PyAny>),
}

impl Yielded {
    /// Classify a Python object yielded by a generator.
    /// 
    /// MUST be called by DRIVER with GIL held.
    /// Result is passed to VM via PyCallOutcome::GenYield(Yielded).
    pub fn classify(py: Python<'_>, obj: &Bound<'_, PyAny>) -> Self {
        // Check for ControlPrimitive
        if let Ok(prim) = extract_control_primitive(py, obj) {
            return Yielded::Primitive(prim);
        }
        
        // Check for Effect
        if let Ok(effect) = extract_effect(py, obj) {
            return Yielded::Effect(effect);
        }
        
        // Check for Program (nested)
        if is_program(py, obj) {
            return Yielded::Program(obj.clone().unbind());
        }
        
        // Unknown
        Yielded::Unknown(obj.clone().unbind())
    }
}
```

### PyCallOutcome (Python Call Results)

**CRITICAL**: CallFunc and Gen* have fundamentally different result semantics:
- `CallFunc` returns a **value** (usually a generator object)
- `GenNext/GenSend/GenThrow` interact with a running generator (yield/return/error)

```rust
/// Result of executing a PythonCall.
/// 
/// IMPORTANT: This enum correctly separates:
/// - CallFunc results (a value, typically a generator object)
/// - Generator step results (yield/return/error)
pub enum PyCallOutcome {
    /// CallFunc returned a value (usually a generator object).
    /// VM should push Frame::PythonGenerator with started=false.
    Value(Py<PyAny>),
    
    /// Generator yielded a value.
    /// Driver has already classified it (requires GIL).
    GenYield(Yielded),
    
    /// Generator returned via StopIteration.
    GenReturn(Value),
    
    /// Generator (or CallFunc) raised an exception.
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
Classification requires GIL, so driver does it. VM receives pre-classified data and stays GIL-free.

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
frames.pop()   frames.pop()                                   │   by driver)   │
   │                │                                         │                │
   ├─RustReturn─────┼──────────────────────────────────┬──────┘                │
   │  callback(v)   │  callback(e)                     │                       │
   │                │                                  ├─Primitive────────────►│
   ├─PyGen──────────┼──────────────────────────────────┤  handle_primitive()   │
   │  NeedsPython   │  NeedsPython(GenThrow)           │                       │
   │  (GenSend/Next)│                                  ├─Effect───────────────►│
   │                │                                  │  start_dispatch()     │
   ▼                ▼                                  │  (all effects)        │
                                                       │                       │
                                                       ├─Program──────────────►│
                                                       │  NeedsPython(CallFunc)│
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
        
        Frame::PythonGenerator { generator, started } => {
            // Need to call Python
            // CRITICAL: Set pending_python so receive_python_result knows to re-push
            self.pending_python = Some(PendingPython::StepUserGenerator {
                generator: generator.clone(),
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
    pub fn receive_python_result(&mut self, outcome: PyCallOutcome) {
        let pending = self.pending_python.take()
            .expect("pending_python must be set when receiving result");
        
        match (pending, outcome) {
            // === StartProgramFrame: CallFunc returned generator object ===
            (PendingPython::StartProgramFrame, PyCallOutcome::Value(gen_obj)) => {
                // Push generator as new frame with started=false
                let segment = &mut self.segments[self.current_segment.index()];
                segment.push_frame(Frame::PythonGenerator {
                    generator: gen_obj,
                    started: false,
                });
                // Mode stays Deliver (will trigger GenNext on next step)
            }
            (PendingPython::StartProgramFrame, PyCallOutcome::GenError(e)) => {
                // CallFunc raised exception
                self.mode = Mode::Throw(e);
            }
            
            // === StepUserGenerator: Generator stepped ===
            (PendingPython::StepUserGenerator { generator }, PyCallOutcome::GenYield(yielded)) => {
                // CRITICAL: Re-push generator with started=true
                // Otherwise we lose the frame and can't continue it later
                let segment = &mut self.segments[self.current_segment.index()];
                segment.push_frame(Frame::PythonGenerator {
                    generator,
                    started: true,
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
            
            // === CallPythonHandler: Handler returned Program generator ===
            (PendingPython::CallPythonHandler { k_user, effect }, PyCallOutcome::Value(handler_gen)) => {
                // Handler returned a generator that yields control primitives
                // Push it as frame with started=false
                let segment = &mut self.segments[self.current_segment.index()];
                segment.push_frame(Frame::PythonGenerator {
                    generator: handler_gen,
                    started: false,
                });
                // Store k_user somewhere accessible to handler primitives
                // (e.g., in dispatch context or handler_exec_seg)
            }
            (PendingPython::CallPythonHandler { .. }, PyCallOutcome::GenError(e)) => {
                self.mode = Mode::Throw(e);
            }
            
            // === StdlibContinuation: Stdlib handler's Python call returned ===
            (PendingPython::StdlibContinuation { handler, k, context }, PyCallOutcome::Value(result)) => {
                // Feed result back to stdlib handler's continue_after_python
                let value = Value::from_pyobject_unbound(result);
                let action = match handler {
                    StdlibHandler::State(h) => h.continue_after_python(value, context, k, &mut self.rust_store),
                    _ => panic!("Only State handler uses StdlibContinuation currently"),
                };
                self.apply_handler_action(action);
            }
            (PendingPython::StdlibContinuation { .. }, PyCallOutcome::GenError(e)) => {
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
        Yielded::Primitive(prim) => {
            // Handle control primitive
            self.mode = self.handle_primitive(prim);
            StepEvent::Continue
        }
        
        Yielded::Effect(effect) => {
            // ALL effects go through dispatch - no bypass
            // Stdlib effects are handled by stdlib handlers (Rust, fast)
            // User effects are handled by Python handlers
            match self.start_dispatch(effect) {
                Ok(()) => StepEvent::Continue,
                Err(e) => StepEvent::Error(e),
            }
        }
        
        Yielded::Program(program) => {
            // Nested program - need to call Python to get generator
            self.pending_python = Some(PendingPython::StartProgramFrame);
            StepEvent::NeedsPython(PythonCall::CallFunc {
                func: program,
                args: vec![],
            })
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

---

## Driver Loop (PyO3 Side)

The driver handles GIL boundaries and **classifies yielded values** before passing to VM.

```rust
impl PyVM {
    /// Run a program to completion.
    pub fn run(&mut self, py: Python<'_>, program: Bound<'_, PyAny>) -> PyResult<PyObject> {
        // Initialize: call program to get generator
        let gen = program.call0()?;
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
    /// CRITICAL: This correctly distinguishes CallFunc from Gen* results:
    /// - CallFunc → Value (the returned object, usually a generator)
    /// - Gen* → GenYield/GenReturn/GenError (generator step result)
    /// 
    /// Classification of yielded values happens HERE (with GIL).
    fn execute_python_call(&self, py: Python<'_>, call: PythonCall) -> PyResult<PyCallOutcome> {
        match call {
            PythonCall::CallFunc { func, args } => {
                let py_args = args.to_py_tuple(py)?;
                match func.bind(py).call1(py_args) {
                    Ok(result) => {
                        // CallFunc returns a Value (not a generator yield!)
                        // VM will push this as Frame::PythonGenerator with started=false
                        Ok(PyCallOutcome::Value(result.unbind()))
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

## Control Primitives

```rust
/// Control primitives that can be yielded by handlers.
#[derive(Debug, Clone)]
pub enum ControlPrimitive {
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
    
    /// ResumeThenTransfer(k_return, v, k_next) - Atomic return-and-switch
    ResumeThenTransfer {
        k_return: Continuation,
        value: Value,
        k_next: Continuation,
    },
    
    /// Delegate(effect) - Delegate to outer handler
    Delegate {
        effect: Effect,
    },
    
    /// WithHandler(handler, program) - Install handler
    WithHandler {
        handler: Handler,
        program: Py<PyAny>,
    },
    
    /// GetContinuation - Capture current continuation
    GetContinuation,
    
    /// GetHandlers - Get handlers from yielder's scope
    GetHandlers,
    
    /// CreateContinuation(program, handlers) - Create unstarted continuation
    CreateContinuation {
        program: Py<PyAny>,
        handlers: Vec<Marker>,
    },
    
    /// ResumeContinuation(k, v) - Resume any captured continuation
    ResumeContinuation {
        continuation: Continuation,
        value: Value,
    },
}
```

---

## Primitive Handlers

These implementations show how control primitives modify VM state and return the next Mode.

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
    /// Returns: PythonCall to start body program, or Mode for next step
    fn handle_with_handler(&mut self, handler: Handler, program: Py<PyAny>) -> PythonCall {
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
        
        // 5. Return PythonCall to start body program
        PythonCall::CallFunc {
            func: program,
            args: vec![],
        }
    }
}
```

### Dispatch (All Effects, Top-Only Busy Boundary)

```rust
impl VM {
    /// Start dispatching an effect to handlers.
    /// 
    /// ALL effects go through this path - no bypass for stdlib effects.
    /// Stdlib handlers are Rust-native for speed but still dispatched normally.
    /// 
    /// Returns Ok(()) if dispatch started successfully (mode updated).
    /// Returns Err(VMError) if no handler found.
    fn start_dispatch(&mut self, effect: Effect) -> Result<(), VMError> {
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
        // Returns (index, marker, entry) - index is critical for busy boundary
        let (handler_idx, handler_marker, entry) = 
            self.find_matching_handler(&handler_chain, &effect)?;
        
        // Get prompt_seg_id directly from HandlerEntry (NO linear search!)
        let prompt_seg_id = entry.prompt_seg_id;
        let handler = entry.handler.clone();
        
        // Generate IDs
        let dispatch_id = DispatchId::fresh();
        
        // Capture callsite continuation
        let current_seg = &self.segments[self.current_segment.index()];
        let k_user = Continuation::capture(current_seg, self.current_segment, Some(dispatch_id));
        
        // Push dispatch context
        // CRITICAL: handler_idx is the ACTUAL position in handler_chain where match was found
        self.dispatch_stack.push(DispatchContext {
            dispatch_id,
            effect: effect.clone(),
            handler_chain: handler_chain.clone(),
            handler_idx,  // <-- actual matched position, not hardcoded 0
            callsite_cont_id: k_user.cont_id,
            prompt_seg_id,
            completed: false,
        });
        
        // Create handler execution segment
        //    caller = prompt_seg (abandon goes to prompt)
        //    scope_chain = same as callsite (handler in scope during handling)
        let handler_seg = Segment::new(
            handler_marker,
            Some(prompt_seg_id),
            scope_chain,
        );
        let handler_seg_id = self.alloc_segment(handler_seg);
        
        // Switch to handler segment
        self.current_segment = handler_seg_id;
        
        // Invoke handler based on type
        match handler {
            Handler::Stdlib(stdlib_handler) => {
                // Stdlib handler: Rust-native, direct invocation
                // These handlers read/write RustStore directly
                let action = stdlib_handler.handle(&effect, k_user, &mut self.rust_store);
                self.apply_handler_action(action);
            }
            Handler::Python(py_handler) => {
                // Python handler: need to call Python
                // Return NeedsPython from step()
                // (implementation detail: set pending_python_call field)
            }
        }
        
        Ok(())
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
    /// This is more permissive than union-all and matches effect semantics.
    fn visible_handlers(&self, scope_chain: &[Marker]) -> Vec<Marker> {
        let Some(top) = self.dispatch_stack.last() else {
            return scope_chain.to_vec();
        };
        
        if top.completed {
            return scope_chain.to_vec();
        }
        
        // Busy = handlers at indices 0..=handler_idx in top dispatch
        // Visible = handlers at indices handler_idx+1.. (outer side)
        let outer_handlers = &top.handler_chain[(top.handler_idx + 1)..];
        outer_handlers.to_vec()
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

### Resume Primitive (Materializes Snapshot)

```rust
impl VM {
    /// Resume a continuation with call-resume semantics.
    /// 
    /// The continuation's frames_snapshot is materialized into a new segment.
    /// The current segment becomes the caller (returns here after k completes).
    fn handle_resume(&mut self, k: Continuation, value: Value) -> Mode {
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
                if top.dispatch_id == dispatch_id && top.callsite_cont_id == k.cont_id {
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
    fn handle_transfer(&mut self, k: Continuation, value: Value) -> Mode {
        // One-shot check
        if self.consumed_cont_ids.contains(&k.cont_id) {
            return Mode::Throw(PyException::runtime_error(
                "Continuation already resumed"
            ));
        }
        self.consumed_cont_ids.insert(k.cont_id);
        
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
    
    /// Handle a control primitive, returning the next Mode.
    fn handle_primitive(&mut self, prim: ControlPrimitive) -> Mode {
        match prim {
            ControlPrimitive::Resume { continuation, value } => {
                self.handle_resume(continuation, value)
            }
            ControlPrimitive::Transfer { continuation, value } => {
                self.handle_transfer(continuation, value)
            }
            ControlPrimitive::Delegate { effect } => {
                // Delegate to OUTER handler (advance in SAME dispatch, not new dispatch)
                self.handle_delegate(effect)
            }
            ControlPrimitive::WithHandler { handler, program } => {
                // WithHandler needs PythonCall - handled specially in step
                Mode::Deliver(Value::Unit)  // placeholder
            }
            _ => {
                Mode::Throw(PyException::not_implemented(
                    format!("Primitive not yet implemented: {:?}", prim)
                ))
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
    fn handle_delegate(&mut self, effect: Effect) -> Mode {
        // Get current dispatch context
        let top = self.dispatch_stack.last_mut()
            .expect("Delegate called outside of dispatch context");
        
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
                    let prompt_seg_id = entry.prompt_seg_id;
                    
                    // Capture continuation from current point (inner handler's exec segment)
                    // callsite_cont_id stays the same (original callsite)
                    
                    // Create new handler execution segment for outer handler
                    let scope_chain = self.current_scope_chain();
                    let handler_seg = Segment::new(
                        marker,
                        Some(prompt_seg_id),
                        scope_chain,
                    );
                    let handler_seg_id = self.alloc_segment(handler_seg);
                    self.current_segment = handler_seg_id;
                    
                    // Invoke outer handler
                    return self.invoke_handler(handler, &effect, top.callsite_cont_id);
                }
            }
        }
        
        // No outer handler found
        Mode::Throw(PyException::runtime_error(
            format!("Delegate: no outer handler for effect {:?}", effect)
        ))
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
  - Value::to_pyobject / from_pyobject
  - Final result extraction

GIL is RELEASED during:
  - vm.step() execution
  - Stdlib handler execution (Rust-native)
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
RunnableId is checked in consumed_runnable_ids before execute.
Double-resume/execute returns Error, not panic.
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
    caller = prompt_seg_id  // abandon returns to prompt, not callsite
    scope_chain = callsite.scope_chain  // same scope as effect callsite
```

### INV-7: Dispatch ID Assignment

```
dispatch_id is Some IFF continuation is callsite (k_user).
All other continuations (handler-local, scheduler) have dispatch_id = None.

Completion check requires BOTH:
  k.dispatch_id == Some(top.dispatch_id) AND
  k.cont_id == top.callsite_cont_id
```

### INV-8: Busy Boundary (Top-Only)

```
Only the topmost non-completed dispatch creates a busy boundary.
Busy handlers = top.handler_chain[0..=top.handler_idx]
Visible handlers = top.handler_chain[(top.handler_idx + 1)..]

This is MORE PERMISSIVE than union-all. Nested dispatches can see
handlers that are busy in outer dispatches, which matches algebraic
effect semantics (handlers are in scope based on their installation
point, not based on what's currently executing).
```

### INV-9: All Effects Go Through Dispatch

```
ALL effects (including stdlib Get, Put, Modify, Ask, Tell) go through
the dispatch stack. There is NO bypass for any effect type.

Stdlib handlers are Rust-implemented for performance but still:
  - Are installed via WithHandler (explicit)
  - Go through dispatch (found via handler_chain lookup)
  - Can be intercepted, overridden, or replaced by users

To intercept state operations, install a custom handler that handles
Get/Put effects before the stdlib handler in the scope chain.
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

### INV-12: Step Event Classification

```
step() returns exactly one of:
  - Continue: internal transition, no Python needed, keep stepping
  - NeedsPython(call): must execute Python call, then receive_python_result()
  - Done(value): computation completed successfully
  - Error(e): computation failed

The driver loop spins on Continue (in allow_threads), only acquiring
GIL when NeedsPython is returned.
```

### INV-13: Mode Transitions

```
Mode transitions are deterministic:

  Deliver(v) + frames.pop() →
    - RustReturn: callback returns new Mode
    - PythonGenerator: NeedsPython(GenSend/GenNext)
    - empty frames: Return(v)

  Throw(e) + frames.pop() →
    - RustReturn: propagate (callbacks don't catch)
    - PythonGenerator: NeedsPython(GenThrow)
    - empty frames + caller: propagate up
    - empty frames + no caller: Error

  HandleYield(y) →
    - Primitive: handle_primitive returns Mode
    - Effect: start_dispatch, then Deliver or NeedsPython
    - Program: NeedsPython(CallFunc)
    - Unknown: Throw(TypeError)

  Return(v) →
    - caller exists: switch to caller, Deliver(v)
    - no caller: Done(v)
```

### INV-14: Generator Protocol

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

CallFunc returns PyCallOutcome::Value(obj) - NOT a generator step!
VM pushes Frame::PythonGenerator with started=false.

Generator start uses GenNext (__next__).
Generator resume uses GenSend (send).
Exception injection uses GenThrow (throw).
```

---

## Implementation Checklist

### Rust Crate Structure

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
│   ├── primitives.rs    # ControlPrimitive enum
│   ├── yielded.rs       # Yielded enum, classification
│   ├── handlers/
│   │   ├── mod.rs
│   │   ├── state.rs     # State handler (Get, Put)
│   │   ├── reader.rs    # Reader handler (Ask)
│   │   └── writer.rs    # Writer handler (Tell)
│   ├── rust_store.rs    # RustStore (stdlib state: state, env, log)
│   ├── value.rs         # Value enum (Rust/Python interop)
│   ├── python_call.rs   # PythonCall, PyCallOutcome, PyException
│   ├── driver.rs        # PyVM wrapper, driver loop
│   └── error.rs         # VMError enum
└── tests/
    └── ...
```

### Tasks

**Phase 1: Core Types**
- [ ] Set up Rust crate with PyO3 and maturin
- [ ] Implement core IDs (Marker, SegmentId, ContId, CallbackId)
- [ ] Implement Value with Python interop
- [ ] Implement VMError enum

**Phase 2: Continuation Structure**
- [ ] Implement Segment and SegmentKind
- [ ] Implement Frame with CallbackId
- [ ] Implement Continuation with Arc snapshots
- [ ] Implement Store with callback table (SlotMap)

**Phase 3: Step State Machine**
- [ ] Implement Mode enum
- [ ] Implement StepEvent enum
- [ ] Implement Yielded classification
- [ ] Implement step() main loop
- [ ] Implement step_deliver_or_throw()
- [ ] Implement step_handle_yield()
- [ ] Implement step_return()

**Phase 4: Effects & Handlers**
- [ ] Implement stdlib handlers (StdStateHandler, StdReaderHandler, StdWriterHandler)
- [ ] Implement WithHandler (prompt + body structure)
- [ ] Implement start_dispatch with visible_handlers (all effects dispatch)
- [ ] Implement Resume (materialize snapshot)
- [ ] Implement Transfer (tail-transfer)
- [ ] Implement Delegate

**Phase 5: Python Integration**
- [ ] Implement PythonCall (CallFunc/GenNext/GenSend/GenThrow)
- [ ] Implement PyCallOutcome handling (Value vs GenYield/GenReturn/GenError)
- [ ] Implement Yielded::classify() in driver (with GIL)
- [ ] Implement PyException wrapper
- [ ] Implement PyVM driver loop (step_generator classifies yields)
- [ ] Implement receive_python_result() (handles Value vs Gen* correctly)

**Phase 6: Testing & Validation**
- [ ] Test basic effects (Get, Put, Ask, Tell)
- [ ] Test single-level handlers
- [ ] Test nested handlers
- [ ] Test abandon semantics
- [ ] Test one-shot continuation enforcement
- [ ] Benchmark against pure Python implementation
- [ ] Document public API

---

## Migration Path

### Phase 1: Core VM (With Stdlib Handlers)
- Implement Mode-based step loop
- Implement stdlib handlers for effects (Get, Put, Ask, Tell)
- Stdlib handlers go through dispatch (no bypass)
- Test with simple Python generators
- Validate: `step()` returns correct StepEvent sequence

### Phase 2: Single-Level Handlers
- Implement WithHandler (prompt + body + scope_chain)
- Implement start_dispatch (capture k_user)
- Implement Resume (materialize snapshot)
- Test: handler receives effect, resumes continuation
- Validate: value flows correctly callsite → handler → callsite

### Phase 3: Nested Handlers & Delegate
- Implement visible_handlers (top-only busy boundary)
- Implement Delegate (re-dispatch to outer)
- Test: nested `with_handler` with inner delegation
- Validate: busy boundary prevents inner handler from seeing itself

### Phase 4: Abandon & Transfer
- Implement Transfer (tail-transfer, no return link)
- Test: handler returns without Resume (abandon)
- Validate: body_seg is orphaned, control goes to prompt_seg

### Phase 5: Python Integration
- Implement PyVM driver loop
- Implement correct generator protocol
- Integrate with existing doeff Python API
- Test: run existing doeff test suite with Rust VM
- Ensure backward compatibility

### Phase 6: Optimization
- Profile hot paths (step loop, frame pop, segment alloc)
- Consider persistent cons-list for frames (if profiling shows need)
- Consider `#[inline]` for step_* functions
- Evaluate segment pooling strategies

---

## References

- PyO3 Guide: https://pyo3.rs/
- Rust Book: Ownership and Lifetimes
- "Retrofitting Effect Handlers onto OCaml" (PLDI 2021) - segment-based continuation design
- slotmap crate: https://docs.rs/slotmap/
- maturin: https://www.maturin.rs/
