//! Cooperative scheduler implemented in Rust (`scheduled(..., implementation="rust")`).
//!
//! This is a line-for-line port of `doeff_core_effects/scheduler.py`
//! (`scheduled()`): the same effects, the same task/promise/semaphore state
//! machine, the same ready-heap ordering (a port of `heapq`), the same Cancel
//! semantics and the same diagnostics. Only the place where the logic runs
//! changes: every scheduler effect is decided here in one call instead of in
//! two Python generators per effect.
//!
//! What stays in Python (so user code and downstream handlers see no change):
//! - the effect classes (`Spawn`, `Wait`, `AcquireSemaphore`, ... — subclasses
//!   such as a `CreateNamedSemaphore(CreateSemaphore)` are matched by
//!   `isinstance`, exactly like the Python `if isinstance(...)` chain);
//! - the handle classes (`Task`, `Future`, `Promise`, `ExternalPromise`,
//!   `Semaphore`) and the exception classes;
//! - the root close-out warning (#501) and the task-error traceback
//!   enrichment helper, which run once per run / per failing task.
//!
//! Handler shape. The Python scheduler is a `@do` generator handler; this one
//! is a synchronous `Callable::call_handler`, so it has no handler stream
//! frame of its own. Consequences, all of them identical in effect to the
//! Python version:
//! - switching to another task uses `DoCtrl::Resume` (the Python version's
//!   `TailEval(Transfer(...))` pops its handler frame first; with no frame
//!   there is nothing to pop, and `Transfer` would pop the caller's frame);
//! - effects the scheduler does not handle are declined through
//!   `Callable::accepts` (the VM reperforms them from the scheduler's parent,
//!   exactly as `yield Pass(effect, k)` does);
//! - an error raised while handling an effect is thrown into the performing
//!   continuation when the scheduler still holds it — the Python version gets
//!   the same routing from the VM's `handler_k_handle` recovery.

use std::collections::{BTreeMap, BTreeSet, VecDeque};
use std::hash::{BuildHasherDefault, Hasher};
use std::sync::atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering};
use std::sync::{Arc, Condvar, Mutex, MutexGuard, Weak};
use std::time::Duration;

use pyo3::exceptions::{
    PyBaseException, PyException, PyKeyError, PyKeyboardInterrupt, PyRuntimeError,
    PySystemExit, PyTypeError, PyValueError,
};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList, PyTuple, PyType, PyWeakrefReference};

use doeff_vm_core::continuation::BoundaryKind;
use doeff_vm_core::do_ctrl::DoCtrl;
use doeff_vm_core::ir_stream::{IRStream, IRStreamRef, StreamStep};
use doeff_vm_core::py_shared::PyShared;
use doeff_vm_core::value::{Callable, CallableRef, Value};
use doeff_vm_core::{Continuation, VMError};

use crate::python_generator_stream::{classify_python_object, python_to_value, value_to_python};
use crate::result::{PyResultErr, PyResultOk};

/// The name the VM shows for the scheduler prompt (tracebacks, "handlers in
/// scope"). Same qualname as the Python version's raw handler, so rendered
/// handler chains read `scheduled` in both versions.
const HANDLER_QUALNAME: &str = "scheduled.<locals>.make_handler.<locals>.raw_handler";

/// The scheduler's maps are keyed by small integers it allocates itself; the
/// default SipHash showed up in the profile, so they use a multiplicative
/// hash (keys are not attacker-controlled).
#[derive(Default)]
struct IdHasher(u64);

impl Hasher for IdHasher {
    fn finish(&self) -> u64 {
        self.0
    }

    fn write(&mut self, bytes: &[u8]) {
        for byte in bytes {
            self.write_u64(u64::from(*byte));
        }
    }

    fn write_u64(&mut self, value: u64) {
        self.0 = (self.0.rotate_left(5) ^ value).wrapping_mul(0x51_7c_c1_b7_27_22_0a_95);
    }

    fn write_usize(&mut self, value: usize) {
        self.write_u64(value as u64);
    }

    fn write_isize(&mut self, value: isize) {
        self.write_u64(value as u64);
    }
}

type HashMap<K, V> = std::collections::HashMap<K, V, BuildHasherDefault<IdHasher>>;
type HashSet<K> = std::collections::HashSet<K, BuildHasherDefault<IdHasher>>;

type Tid = u64;
/// The task a continuation belongs to; `None` = the root body.
type Owner = Option<Tid>;
/// A continuation the scheduler holds (key into `State::conts`). The Python
/// version holds `K` objects; a `KRef` whose slot was emptied behaves like a
/// consumed `K` (resuming it is a one-shot violation).
type KRef = u64;
/// A shared "claimed" flag (the Python version's `claimed = [False]` cell)
/// linking a `waiters` registration to its external-wait ready placeholder.
type Claim = Arc<AtomicBool>;

// ---------------------------------------------------------------------------
// Effect kinds
// ---------------------------------------------------------------------------

/// Scheduler effects, in the order of the Python `isinstance` chain.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Kind {
    Spawn,
    TaskCompleted,
    Wait,
    Gather,
    Race,
    Cancel,
    CreatePromise,
    CompletePromise,
    FailPromise,
    CreateExternalPromise,
    Introspection,
    CreateSemaphore,
    AcquireSemaphore,
    ReleaseSemaphore,
}

const KINDS: [(Kind, &str); 14] = [
    (Kind::Spawn, "Spawn"),
    (Kind::TaskCompleted, "TaskCompleted"),
    (Kind::Wait, "Wait"),
    (Kind::Gather, "Gather"),
    (Kind::Race, "Race"),
    (Kind::Cancel, "Cancel"),
    (Kind::CreatePromise, "CreatePromise"),
    (Kind::CompletePromise, "CompletePromise"),
    (Kind::FailPromise, "FailPromise"),
    (Kind::CreateExternalPromise, "CreateExternalPromise"),
    (Kind::Introspection, "_SchedulerIntrospection"),
    (Kind::CreateSemaphore, "CreateSemaphore"),
    (Kind::AcquireSemaphore, "AcquireSemaphore"),
    (Kind::ReleaseSemaphore, "ReleaseSemaphore"),
];

// ---------------------------------------------------------------------------
// Spec — the Python classes and settings a scheduler run uses
// ---------------------------------------------------------------------------

struct Spec {
    effect_types: Vec<Py<PyType>>,
    /// `Py_TYPE` pointers of `effect_types` for the exact-type fast path.
    effect_type_ptrs: Vec<usize>,
    task_cls: Py<PyAny>,
    future_cls: Py<PyAny>,
    promise_cls: Py<PyAny>,
    external_promise_cls: Py<PyAny>,
    semaphore_cls: Py<PyAny>,
    task_cancelled_error: Py<PyAny>,
    callback_error_cls: Py<PyAny>,
    deadlock_error_cls: Py<PyAny>,
    enrich_traceback: Py<PyAny>,
    logger: Py<PyAny>,
    /// The Python scheduler module: settings a test may monkeypatch
    /// (`EXTERNAL_STALL_LOG_INTERVAL_SECONDS`) are read from it when used.
    module: Py<PyAny>,
    priority_idle: i64,
    priority_external_wait: i64,
    priority_normal: i64,
    handle_sweep_interval: u64,
    handle_refs_prune_min: usize,
}

impl Spec {
    fn from_dict(spec: &Bound<'_, PyDict>) -> PyResult<Self> {
        let get = |name: &str| -> PyResult<Bound<'_, PyAny>> {
            spec.get_item(name)?.ok_or_else(|| {
                PyKeyError::new_err(format!("SchedulerCore spec is missing {name:?}"))
            })
        };
        let mut effect_types = Vec::with_capacity(KINDS.len());
        let mut effect_type_ptrs = Vec::with_capacity(KINDS.len());
        for (_, name) in KINDS {
            let ty = get(name)?.cast_into::<PyType>()?;
            effect_type_ptrs.push(ty.as_ptr() as usize);
            effect_types.push(ty.unbind());
        }
        Ok(Self {
            effect_types,
            effect_type_ptrs,
            task_cls: get("Task")?.unbind(),
            future_cls: get("Future")?.unbind(),
            promise_cls: get("Promise")?.unbind(),
            external_promise_cls: get("ExternalPromise")?.unbind(),
            semaphore_cls: get("Semaphore")?.unbind(),
            task_cancelled_error: get("TaskCancelledError")?.unbind(),
            callback_error_cls: get("ExternalPromiseCancelCallbackError")?.unbind(),
            deadlock_error_cls: get("SchedulerDeadlockError")?.unbind(),
            enrich_traceback: get("enrich_exception_traceback")?.unbind(),
            logger: get("logger")?.unbind(),
            module: get("module")?.unbind(),
            priority_idle: get("PRIORITY_IDLE")?.extract()?,
            priority_external_wait: get("PRIORITY_EXTERNAL_WAIT")?.extract()?,
            priority_normal: get("PRIORITY_NORMAL")?.extract()?,
            handle_sweep_interval: get("HANDLE_SWEEP_INTERVAL")?.extract()?,
            handle_refs_prune_min: get("HANDLE_REFS_PRUNE_MIN")?.extract()?,
        })
    }

    fn cancelled_error(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        Ok(self.task_cancelled_error.bind(py).call0()?.unbind())
    }
}

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
enum WKind {
    Task,
    Promise,
}

impl WKind {
    fn name(self) -> &'static str {
        match self {
            WKind::Task => "task",
            WKind::Promise => "promise",
        }
    }
}

/// A waitable key — the Python version's `("task", tid)` / `("promise", pid)`.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
struct WKey {
    kind: WKind,
    id: u64,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Status {
    Pending,
    Running,
    Cancelling,
    Completed,
    Failed,
    Cancelled,
}

impl Status {
    fn is_terminal(self) -> bool {
        matches!(self, Status::Completed | Status::Failed | Status::Cancelled)
    }

    fn name(self) -> &'static str {
        match self {
            Status::Pending => "pending",
            Status::Running => "running",
            Status::Cancelling => "cancelling",
            Status::Completed => "completed",
            Status::Failed => "failed",
            Status::Cancelled => "cancelled",
        }
    }
}

struct TaskEntry {
    status: Status,
    result: Option<Py<PyAny>>,
    program: Option<Py<PyAny>>,
    priority: i64,
    daemon: bool,
    inner_boundaries: Option<Vec<(BoundaryKind, CallableRef)>>,
    /// `None` once released (`_release_task_refs`); the Python version then
    /// reads the missing key as `""`.
    spawn_site: Option<Option<String>>,
}

struct PromiseEntry {
    status: Status,
    result: Option<Py<PyAny>>,
    external: bool,
    cancel_callbacks: Option<Vec<Py<PyAny>>>,
}

struct SemEntry {
    permits: i64,
    max_permits: i64,
    waiters: VecDeque<(Owner, KRef)>,
    /// Diagnostics-only permit holders (#495c); `None` = root.
    holders: Vec<Owner>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum GroupKind {
    Gather,
    Race,
}

/// A Gather / Race registration shared by the waiter entries of every key it
/// waits on (the Python version's `gather_state` / `race_state` dict). Removed
/// from `State::groups` when resolved; a missing group reads as resolved.
struct Group {
    owner: Owner,
    waiter_k: KRef,
    keys: Vec<WKey>,
    pending_keys: Vec<WKey>,
    remaining: usize,
}

enum WaiterEntry {
    Wait {
        owner: Owner,
        k: KRef,
        wake_priority: Option<i64>,
    },
    WaitExternal {
        owner: Owner,
        k: KRef,
        claimed: Claim,
        wake_priority: Option<i64>,
    },
    Group {
        kind: GroupKind,
        owner: Owner,
        group: u64,
        claimed: Option<Claim>,
    },
}

impl WaiterEntry {
    fn owner(&self) -> Owner {
        match self {
            WaiterEntry::Wait { owner, .. }
            | WaiterEntry::WaitExternal { owner, .. }
            | WaiterEntry::Group { owner, .. } => *owner,
        }
    }

    fn type_name(&self) -> &'static str {
        match self {
            WaiterEntry::Wait { .. } => "wait",
            WaiterEntry::WaitExternal { .. } => "wait_external",
            WaiterEntry::Group {
                kind: GroupKind::Gather,
                ..
            } => "gather",
            WaiterEntry::Group {
                kind: GroupKind::Race,
                ..
            } => "race",
        }
    }
}

enum ReadyEntry {
    New {
        tid: Tid,
    },
    Resume {
        owner: Owner,
        k: KRef,
        value: Py<PyAny>,
    },
    Raise {
        owner: Owner,
        k: KRef,
        error: Py<PyAny>,
    },
    SemResume {
        owner: Owner,
        k: KRef,
        sid: u64,
    },
    /// External-wait placeholder (#490/#505). Never resumes anything.
    WaitExternal {
        owner: Owner,
        wk: WKey,
        claimed: Claim,
    },
}

/// A ready-heap item: the Python version's `(-priority, seq, entry)` tuple.
struct HeapItem {
    neg_priority: i64,
    seq: u64,
    entry: ReadyEntry,
}

impl HeapItem {
    fn lt(&self, other: &HeapItem) -> bool {
        (self.neg_priority, self.seq) < (other.neg_priority, other.seq)
    }
}

/// A port of CPython's `heapq` (push / pop / heapify use the same sift
/// algorithms), so the heap array has exactly the layout the Python version
/// has — the diagnostics that list heap entries in array order match.
mod heapq {
    use super::HeapItem;

    fn sift_down(heap: &mut [HeapItem], startpos: usize, mut pos: usize) {
        while pos > startpos {
            let parentpos = (pos - 1) >> 1;
            if heap[pos].lt(&heap[parentpos]) {
                heap.swap(pos, parentpos);
                pos = parentpos;
                continue;
            }
            break;
        }
    }

    fn sift_up(heap: &mut [HeapItem], mut pos: usize) {
        let endpos = heap.len();
        let startpos = pos;
        let mut childpos = 2 * pos + 1;
        while childpos < endpos {
            let rightpos = childpos + 1;
            if rightpos < endpos && !heap[childpos].lt(&heap[rightpos]) {
                childpos = rightpos;
            }
            heap.swap(pos, childpos);
            pos = childpos;
            childpos = 2 * pos + 1;
        }
        sift_down(heap, startpos, pos);
    }

    pub(super) fn push(heap: &mut Vec<HeapItem>, item: HeapItem) {
        heap.push(item);
        let last = heap.len() - 1;
        sift_down(heap, 0, last);
    }

    pub(super) fn pop(heap: &mut Vec<HeapItem>) -> Option<HeapItem> {
        let last = heap.pop()?;
        if heap.is_empty() {
            return Some(last);
        }
        let first = std::mem::replace(&mut heap[0], last);
        sift_up(heap, 0);
        Some(first)
    }

    pub(super) fn heapify(heap: &mut [HeapItem]) {
        let n = heap.len();
        for i in (0..n / 2).rev() {
            sift_up(heap, i);
        }
    }
}

/// An insertion-ordered map (the Python version iterates `waiters`, a dict,
/// in insertion order; a popped and re-added key moves to the end).
struct OrderedMap<V> {
    map: HashMap<WKey, (u64, V)>,
    order: BTreeMap<u64, WKey>,
    next: u64,
}

impl<V> OrderedMap<V> {
    fn new() -> Self {
        Self {
            map: HashMap::default(),
            order: BTreeMap::new(),
            next: 0,
        }
    }

    fn len(&self) -> usize {
        self.map.len()
    }

    fn is_empty(&self) -> bool {
        self.map.is_empty()
    }

    fn contains_key(&self, key: &WKey) -> bool {
        self.map.contains_key(key)
    }

    fn get(&self, key: &WKey) -> Option<&V> {
        self.map.get(key).map(|(_, v)| v)
    }

    fn get_mut(&mut self, key: &WKey) -> Option<&mut V> {
        self.map.get_mut(key).map(|(_, v)| v)
    }

    fn get_or_insert_with(&mut self, key: WKey, make: impl FnOnce() -> V) -> &mut V {
        if !self.map.contains_key(&key) {
            let seq = self.next;
            self.next += 1;
            self.order.insert(seq, key);
            self.map.insert(key, (seq, make()));
        }
        &mut self.map.get_mut(&key).expect("just inserted").1
    }

    fn remove(&mut self, key: &WKey) -> Option<V> {
        let (seq, value) = self.map.remove(key)?;
        self.order.remove(&seq);
        Some(value)
    }

    fn keys(&self) -> Vec<WKey> {
        self.order.values().copied().collect()
    }

    fn iter(&self) -> impl Iterator<Item = (&WKey, &V)> {
        self.order
            .values()
            .map(move |key| (key, &self.map.get(key).expect("ordered key present").1))
    }
}

struct State {
    next_id: u64,
    insertion_seq: u64,
    next_k: u64,
    tasks: HashMap<Tid, TaskEntry>,
    promises: HashMap<u64, PromiseEntry>,
    /// Never swept; ids grow, so the BTreeMap order is insertion order.
    semaphores: BTreeMap<u64, SemEntry>,
    waiters: OrderedMap<Vec<WaiterEntry>>,
    ready: Vec<HeapItem>,
    handle_refs: HashMap<WKey, Vec<Py<PyWeakrefReference>>>,
    handle_prune_at: HashMap<WKey, usize>,
    conts: HashMap<KRef, Continuation>,
    groups: HashMap<u64, Group>,
    next_group: u64,
    /// Continuations and Python objects the scheduler lets go of while it
    /// holds its lock. Dropping a continuation can finalize suspended
    /// generators (running their `finally` blocks), which must not happen
    /// while the lock is held; they are dropped after it is released.
    graveyard_conts: Vec<Continuation>,
    graveyard_objs: Vec<Py<PyAny>>,
}

impl State {
    fn new() -> Self {
        Self {
            next_id: 0,
            insertion_seq: 0,
            next_k: 0,
            tasks: HashMap::default(),
            promises: HashMap::default(),
            semaphores: BTreeMap::new(),
            waiters: OrderedMap::new(),
            ready: Vec::new(),
            handle_refs: HashMap::default(),
            handle_prune_at: HashMap::default(),
            conts: HashMap::default(),
            groups: HashMap::default(),
            next_group: 0,
            graveyard_conts: Vec::new(),
            graveyard_objs: Vec::new(),
        }
    }
}

// ---------------------------------------------------------------------------
// External completion queue (thread-safe)
// ---------------------------------------------------------------------------

enum Settle {
    Complete,
    Fail,
}

struct ExternalQueue {
    items: Mutex<VecDeque<(Settle, u64, Py<PyAny>)>>,
    ready: Condvar,
    len: AtomicUsize,
}

impl ExternalQueue {
    fn new() -> Self {
        Self {
            items: Mutex::new(VecDeque::new()),
            ready: Condvar::new(),
            len: AtomicUsize::new(0),
        }
    }

    fn put(&self, item: (Settle, u64, Py<PyAny>)) {
        let mut items = self.items.lock().expect("external queue lock poisoned");
        items.push_back(item);
        self.len.store(items.len(), Ordering::Release);
        self.ready.notify_all();
    }

    fn try_pop(&self) -> Option<(Settle, u64, Py<PyAny>)> {
        if self.len.load(Ordering::Acquire) == 0 {
            return None;
        }
        let mut items = self.items.lock().expect("external queue lock poisoned");
        let item = items.pop_front();
        self.len.store(items.len(), Ordering::Release);
        item
    }

    fn pop_timeout(&self, timeout: Duration) -> Option<(Settle, u64, Py<PyAny>)> {
        let items = self.items.lock().expect("external queue lock poisoned");
        let (mut items, _) = self
            .ready
            .wait_timeout_while(items, timeout, |items| items.is_empty())
            .expect("external queue lock poisoned");
        let item = items.pop_front();
        self.len.store(items.len(), Ordering::Release);
        item
    }
}

/// The thread-safe queue `ExternalPromise.complete()/fail()` put into.
#[pyclass(name = "SchedulerExternalQueue", module = "doeff_vm.doeff_vm", frozen)]
pub struct PyExternalQueue {
    inner: Arc<ExternalQueue>,
}

#[pymethods]
impl PyExternalQueue {
    /// `put(("complete" | "fail", promise_id, value))` — the shape
    /// `ExternalPromise` puts into the Python version's `queue.Queue`.
    fn put(&self, item: &Bound<'_, PyTuple>) -> PyResult<()> {
        let (action, pid, value): (String, u64, Py<PyAny>) = item.extract()?;
        let settle = if action == "complete" {
            Settle::Complete
        } else {
            Settle::Fail
        };
        self.inner.put((settle, pid, value));
        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Core
// ---------------------------------------------------------------------------

pub struct Core {
    spec: Spec,
    state: Mutex<State>,
    external: Arc<ExternalQueue>,
    /// Thread currently inside the scheduler (0 = none): a Python callback
    /// that re-enters the scheduler from that thread is refused instead of
    /// deadlocking on `state`.
    owner_thread: AtomicU64,
    /// Effect type → kind, for effects whose exact type is not one of the
    /// scheduler classes (subclasses and every effect the scheduler passes on).
    type_cache: Mutex<HashMap<usize, (Py<PyType>, Option<Kind>)>>,
    registrar: Mutex<Option<Py<PyAny>>>,
    cancel_binder: Mutex<Option<Py<PyAny>>>,
    queue_obj: Mutex<Option<Py<PyAny>>>,
}

impl std::fmt::Debug for Core {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("SchedulerCore")
    }
}

fn thread_token() -> u64 {
    thread_local! {
        static TOKEN: u64 = {
            static NEXT: AtomicU64 = AtomicU64::new(1);
            NEXT.fetch_add(1, Ordering::Relaxed)
        };
    }
    TOKEN.with(|t| *t)
}

/// What a dispatched effect asks the VM to do next.
enum Outcome {
    Ctrl(DoCtrl),
    /// Cancel finished its state change; the `on_cancel` callbacks of the
    /// abandoned external promises must run (outside the scheduler lock)
    /// before the Cancel caller is resumed.
    CancelCallbacks {
        k: KRef,
        callbacks: Vec<Py<PyAny>>,
    },
}

struct Locked<'a> {
    core: &'a Arc<Core>,
    guard: MutexGuard<'a, State>,
}

impl Drop for Locked<'_> {
    fn drop(&mut self) {
        self.core.owner_thread.store(0, Ordering::Release);
    }
}

impl Core {
    fn lock<'a>(self: &'a Arc<Self>) -> PyResult<Locked<'a>> {
        let me = thread_token();
        if self.owner_thread.load(Ordering::Acquire) == me {
            return Err(PyRuntimeError::new_err(
                "the scheduler was re-entered from its own dispatch (a callback \
                 touched scheduler state while the scheduler was running)",
            ));
        }
        let guard = self.state.lock().expect("scheduler state lock poisoned");
        self.owner_thread.store(me, Ordering::Release);
        Ok(Locked { core: self, guard })
    }

    fn kind_of(&self, effect: &PyShared) -> Option<Kind> {
        // SAFETY: `effect` holds a strong reference, so the object and its
        // type are alive; reading `ob_type` needs no attached thread state.
        let tp = unsafe { pyo3::ffi::Py_TYPE(effect.inner().as_ptr()) } as usize;
        if let Some(index) = self.spec.effect_type_ptrs.iter().position(|p| *p == tp) {
            return Some(KINDS[index].0);
        }
        if let Some((_, kind)) = self
            .type_cache
            .lock()
            .expect("type cache lock poisoned")
            .get(&tp)
        {
            return *kind;
        }
        Python::attach(|py| {
            let obj = effect.bind(py);
            let mut found = None;
            for (index, ty) in self.spec.effect_types.iter().enumerate() {
                if obj.is_instance(ty.bind(py)).unwrap_or(false) {
                    found = Some(KINDS[index].0);
                    break;
                }
            }
            let ty = obj.get_type().unbind();
            self.type_cache
                .lock()
                .expect("type cache lock poisoned")
                .insert(tp, (ty, found));
            found
        })
    }

    fn registrar(self: &Arc<Self>, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let mut slot = self.registrar.lock().expect("registrar lock poisoned");
        if let Some(obj) = slot.as_ref() {
            return Ok(obj.clone_ref(py));
        }
        let obj = Py::new(
            py,
            HandleRegistrar {
                core: Arc::downgrade(self),
            },
        )?
        .into_any();
        *slot = Some(obj.clone_ref(py));
        Ok(obj)
    }

    fn cancel_binder(self: &Arc<Self>, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let mut slot = self.cancel_binder.lock().expect("binder lock poisoned");
        if let Some(obj) = slot.as_ref() {
            return Ok(obj.clone_ref(py));
        }
        let obj = Py::new(
            py,
            CancelBinder {
                core: Arc::downgrade(self),
            },
        )?
        .into_any();
        *slot = Some(obj.clone_ref(py));
        Ok(obj)
    }

    fn queue_obj(self: &Arc<Self>, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let mut slot = self.queue_obj.lock().expect("queue lock poisoned");
        if let Some(obj) = slot.as_ref() {
            return Ok(obj.clone_ref(py));
        }
        let obj = Py::new(
            py,
            PyExternalQueue {
                inner: self.external.clone(),
            },
        )?
        .into_any();
        *slot = Some(obj.clone_ref(py));
        Ok(obj)
    }

    fn handler(self: &Arc<Self>, tid: Owner) -> CallableRef {
        Arc::new(SchedulerHandler {
            core: self.clone(),
            tid,
        })
    }

    /// `call_handler` of every scheduler prompt.
    fn handle(self: &Arc<Self>, tid: Owner, effect: Value, k: Value) -> Result<DoCtrl, VMError> {
        let Value::Continuation(k) = k else {
            return Err(VMError::internal("scheduler: expected a continuation"));
        };
        let Value::Opaque(effect) = effect else {
            // Not a Python effect: the scheduler never accepts it.
            return Err(VMError::internal("scheduler: effect is not a Python object"));
        };
        let Some(kind) = self.kind_of(&effect) else {
            return Err(VMError::internal(
                "scheduler: dispatched an effect it does not accept",
            ));
        };
        Python::attach(|py| {
            let mut locked = match self.lock() {
                Ok(locked) => locked,
                Err(err) => {
                    return Ok(DoCtrl::ResumeThrow {
                        k,
                        exception: exception_value(py, err),
                    })
                }
            };
            let kref = locked.store_k(k);
            let outcome = locked.dispatch(py, tid, kind, effect.bind(py), kref);
            let result = match outcome {
                Ok(Outcome::Ctrl(ctrl)) => Ok(ctrl),
                Ok(Outcome::CancelCallbacks { k, callbacks }) => {
                    let k = locked.take_k(k);
                    let graveyard = locked.take_graveyard();
                    drop(locked);
                    drop(graveyard);
                    return Ok(finish_cancel(py, &self.spec, k, callbacks));
                }
                Err(err) => {
                    let exception = exception_value(py, err);
                    match locked.take_k(kref) {
                        Some(k) => Ok(DoCtrl::ResumeThrow { k, exception }),
                        None => Err(VMError::uncaught_exception(exception)),
                    }
                }
            };
            let graveyard = locked.take_graveyard();
            drop(locked);
            drop(graveyard);
            result
        })
    }
}

/// Run the `on_cancel` callbacks of the abandoned external promises and
/// resume the Cancel caller (the tail of the Python version's Cancel branch).
fn finish_cancel(
    py: Python<'_>,
    spec: &Spec,
    k: Option<Continuation>,
    callbacks: Vec<Py<PyAny>>,
) -> DoCtrl {
    let mut errors: Vec<Py<PyAny>> = Vec::new();
    for callback in callbacks {
        if let Err(err) = callback.bind(py).call0() {
            if err.is_instance_of::<PyException>(py) {
                errors.push(err.value(py).clone().into_any().unbind());
            } else {
                // A BaseException (KeyboardInterrupt, ...) escapes the Python
                // version's `except Exception` too: it propagates from the
                // Cancel dispatch into the Cancel caller.
                return throw_or_empty(k, err.value(py).clone().into_any().unbind());
            }
        }
    }
    let k = k.unwrap_or_else(Continuation::empty);
    if errors.is_empty() {
        return DoCtrl::Resume {
            k,
            value: Value::None,
        };
    }
    let exception = (|| -> PyResult<Py<PyAny>> {
        let list = PyList::new(py, errors.iter().map(|e| e.bind(py)))?;
        let error = spec.callback_error_cls.bind(py).call1((list,))?;
        error.setattr("__cause__", errors[0].bind(py))?;
        Ok(error.unbind())
    })()
    .unwrap_or_else(|err| err.value(py).clone().into_any().unbind());
    DoCtrl::ResumeThrow {
        k,
        exception: Value::Opaque(PyShared::new(exception)),
    }
}

fn throw_or_empty(k: Option<Continuation>, exception: Py<PyAny>) -> DoCtrl {
    DoCtrl::ResumeThrow {
        k: k.unwrap_or_else(Continuation::empty),
        exception: Value::Opaque(PyShared::new(exception)),
    }
}

fn exception_value(py: Python<'_>, err: PyErr) -> Value {
    Value::Opaque(PyShared::new(err.value(py).clone().into_any().unbind()))
}

fn obj_value(py: Python<'_>, obj: &Py<PyAny>) -> Value {
    python_to_value(py, obj.bind(py))
}

fn key_error(message: impl Into<String>) -> PyErr {
    PyKeyError::new_err(message.into())
}

impl std::ops::Deref for Locked<'_> {
    type Target = State;
    fn deref(&self) -> &State {
        &self.guard
    }
}

impl std::ops::DerefMut for Locked<'_> {
    fn deref_mut(&mut self) -> &mut State {
        &mut self.guard
    }
}

// ---------------------------------------------------------------------------
// The state machine (one method per Python helper, same names)
// ---------------------------------------------------------------------------

impl Locked<'_> {
    fn spec(&self) -> &Spec {
        &self.core.spec
    }

    fn store_k(&mut self, k: Continuation) -> KRef {
        let id = self.next_k;
        self.next_k += 1;
        self.conts.insert(id, k);
        id
    }

    fn take_k(&mut self, k: KRef) -> Option<Continuation> {
        self.conts.remove(&k)
    }

    /// A held continuation as the VM wants it: a consumed slot becomes an
    /// empty continuation (the VM reports the one-shot violation).
    fn take_k_or_empty(&mut self, k: KRef) -> Continuation {
        self.take_k(k).unwrap_or_else(Continuation::empty)
    }

    fn discard_k(&mut self, k: KRef) {
        if let Some(k) = self.conts.remove(&k) {
            self.graveyard_conts.push(k);
        }
    }

    fn bury(&mut self, obj: Py<PyAny>) {
        self.graveyard_objs.push(obj);
    }

    fn take_graveyard(&mut self) -> (Vec<Continuation>, Vec<Py<PyAny>>) {
        (
            std::mem::take(&mut self.graveyard_conts),
            std::mem::take(&mut self.graveyard_objs),
        )
    }

    // --- handles (#502) -------------------------------------------------

    fn register_handle(&mut self, py: Python<'_>, key: WKey, handle: &Bound<'_, PyAny>) -> PyResult<()> {
        let prune_min = self.spec().handle_refs_prune_min;
        let prune_at = self.handle_prune_at.get(&key).copied().unwrap_or(prune_min);
        let refs = self.handle_refs.entry(key).or_default();
        if refs.len() >= prune_at {
            refs.retain(|r| r.bind(py).upgrade().is_some());
            let len = refs.len();
            self.handle_prune_at.insert(key, prune_min.max(2 * len));
        }
        let weak = PyWeakrefReference::new(handle)?.unbind();
        self.handle_refs.entry(key).or_default().push(weak);
        Ok(())
    }

    fn sweep_terminal_unobserved_entries(&mut self, py: Python<'_>) {
        let mut protected: HashSet<WKey> = HashSet::default();
        for (_, entries) in self.waiters.iter() {
            for entry in entries {
                if let WaiterEntry::Group { kind, group, .. } = entry {
                    if let Some(group) = self.groups.get(group) {
                        match kind {
                            GroupKind::Gather => protected.extend(group.keys.iter().copied()),
                            GroupKind::Race => {
                                protected.extend(group.pending_keys.iter().copied())
                            }
                        }
                    }
                }
            }
        }
        let mut dead: Vec<WKey> = Vec::new();
        for (tid, meta) in self.tasks.iter() {
            if !matches!(meta.status, Status::Completed | Status::Failed) {
                continue;
            }
            dead.push(WKey {
                kind: WKind::Task,
                id: *tid,
            });
        }
        for (pid, meta) in self.promises.iter() {
            if !meta.status.is_terminal() {
                continue;
            }
            dead.push(WKey {
                kind: WKind::Promise,
                id: *pid,
            });
        }
        for key in dead {
            if self.waiters.contains_key(&key) || protected.contains(&key) {
                continue;
            }
            let Some(refs) = self.handle_refs.get(&key) else {
                continue;
            };
            if refs.iter().any(|r| r.bind(py).upgrade().is_some()) {
                continue;
            }
            match key.kind {
                WKind::Task => {
                    if let Some(entry) = self.tasks.remove(&key.id) {
                        self.bury_task(entry);
                    }
                }
                WKind::Promise => {
                    if let Some(entry) = self.promises.remove(&key.id) {
                        if let Some(result) = entry.result {
                            self.bury(result);
                        }
                    }
                }
            }
            self.handle_refs.remove(&key);
            self.handle_prune_at.remove(&key);
        }
    }

    fn bury_task(&mut self, entry: TaskEntry) {
        if let Some(result) = entry.result {
            self.bury(result);
        }
        if let Some(program) = entry.program {
            self.bury(program);
        }
    }

    fn fresh_id(&mut self, py: Python<'_>) -> u64 {
        let id = self.next_id;
        self.next_id += 1;
        if self.next_id % self.spec().handle_sweep_interval == 0 {
            self.sweep_terminal_unobserved_entries(py);
        }
        id
    }

    fn waitable_key(&self, py: Python<'_>, obj: &Bound<'_, PyAny>) -> PyResult<WKey> {
        if obj.is_instance(self.spec().task_cls.bind(py))? {
            return Ok(WKey {
                kind: WKind::Task,
                id: obj.getattr(pyo3::intern!(py, "task_id"))?.extract()?,
            });
        }
        if obj.is_instance(self.spec().future_cls.bind(py))? {
            return Ok(WKey {
                kind: WKind::Promise,
                id: obj.getattr(pyo3::intern!(py, "promise_id"))?.extract()?,
            });
        }
        Err(PyTypeError::new_err(format!(
            "expected Task or Future, got {}",
            obj.get_type().name()?
        )))
    }

    fn waitable_status(&self, py: Python<'_>, key: WKey) -> PyResult<(Status, Option<Py<PyAny>>)> {
        let found = match key.kind {
            WKind::Task => self
                .tasks
                .get(&key.id)
                .map(|e| (e.status, e.result.as_ref().map(|r| r.clone_ref(py)))),
            WKind::Promise => self
                .promises
                .get(&key.id)
                .map(|e| (e.status, e.result.as_ref().map(|r| r.clone_ref(py)))),
        };
        found.ok_or_else(|| {
            key_error(format!(
                "{} {} is unknown to this scheduler run: it was swept after reaching a \
                 terminal state with no live Task/Future handle and no registered waiter \
                 (#502). Keep a handle alive to Wait/Gather/Race on it later.",
                key.kind.name(),
                key.id
            ))
        })
    }

    fn enqueue(&mut self, entry: ReadyEntry, priority: i64) {
        let seq = self.insertion_seq;
        self.insertion_seq += 1;
        heapq::push(
            &mut self.ready,
            HeapItem {
                neg_priority: -priority,
                seq,
                entry,
            },
        );
    }

    fn is_owner_cancelled(&self, owner: Owner) -> bool {
        owner
            .and_then(|tid| self.tasks.get(&tid))
            .is_some_and(|t| t.status == Status::Cancelled)
    }

    fn is_daemon(&self, owner: Owner) -> bool {
        owner
            .and_then(|tid| self.tasks.get(&tid))
            .is_some_and(|t| t.daemon)
    }

    fn task_priority(&self, owner: Owner) -> i64 {
        match owner {
            None => self.spec().priority_normal,
            Some(tid) => self
                .tasks
                .get(&tid)
                .map(|t| t.priority)
                .unwrap_or(self.spec().priority_normal),
        }
    }

    fn entry_is_live(&self, entry: &ReadyEntry, include_daemons: bool) -> bool {
        match entry {
            ReadyEntry::New { tid } => {
                self.tasks.get(tid).is_some_and(|t| t.status != Status::Cancelled)
                    && (include_daemons || !self.is_daemon(Some(*tid)))
            }
            ReadyEntry::Resume { owner, .. }
            | ReadyEntry::Raise { owner, .. }
            | ReadyEntry::SemResume { owner, .. } => {
                !self.is_owner_cancelled(*owner) && (include_daemons || !self.is_daemon(*owner))
            }
            ReadyEntry::WaitExternal { .. } => false,
        }
    }

    fn has_live_ready_entry(&self, include_daemons: bool) -> bool {
        self.ready
            .iter()
            .any(|item| self.entry_is_live(&item.entry, include_daemons))
    }

    fn is_live_daemon_entry(&self, entry: &ReadyEntry) -> bool {
        match entry {
            ReadyEntry::New { tid } => {
                self.tasks.get(tid).is_some_and(|t| t.status != Status::Cancelled)
                    && self.is_daemon(Some(*tid))
            }
            ReadyEntry::Resume { owner, .. }
            | ReadyEntry::Raise { owner, .. }
            | ReadyEntry::SemResume { owner, .. } => {
                !self.is_owner_cancelled(*owner) && self.is_daemon(*owner)
            }
            ReadyEntry::WaitExternal { .. } => false,
        }
    }

    fn enqueue_resume(&mut self, owner: Owner, k: KRef, value: Py<PyAny>, priority: Option<i64>) -> i64 {
        let priority = priority.unwrap_or_else(|| self.task_priority(owner));
        self.enqueue(ReadyEntry::Resume { owner, k, value }, priority);
        priority
    }

    fn enqueue_raise(&mut self, owner: Owner, k: KRef, error: Py<PyAny>, priority: Option<i64>) -> i64 {
        let priority = priority.unwrap_or_else(|| self.task_priority(owner));
        self.enqueue(ReadyEntry::Raise { owner, k, error }, priority);
        priority
    }

    fn alloc_promise(&mut self, py: Python<'_>, external: bool) -> u64 {
        let pid = self.fresh_id(py);
        self.promises.insert(
            pid,
            PromiseEntry {
                status: Status::Pending,
                result: None,
                external,
                cancel_callbacks: None,
            },
        );
        pid
    }

    fn settle_external(&mut self, py: Python<'_>, settle: Settle, pid: u64, value: Py<PyAny>) -> PyResult<()> {
        let Some(promise) = self.promises.get_mut(&pid) else {
            self.bury(value);
            return Ok(());
        };
        if promise.status != Status::Pending {
            self.bury(value);
            return Ok(());
        }
        promise.status = match settle {
            Settle::Complete => Status::Completed,
            Settle::Fail => Status::Failed,
        };
        promise.result = Some(value);
        let callbacks = promise.cancel_callbacks.take();
        if let Some(callbacks) = callbacks {
            for callback in callbacks {
                self.bury(callback);
            }
        }
        self.wake_waiters(
            py,
            WKey {
                kind: WKind::Promise,
                id: pid,
            },
        )?;
        Ok(())
    }

    fn drain(&mut self, py: Python<'_>) -> PyResult<()> {
        while let Some((settle, pid, value)) = self.core.external.try_pop() {
            self.settle_external(py, settle, pid, value)?;
        }
        Ok(())
    }

    fn drain_one_external(&mut self, py: Python<'_>) -> PyResult<()> {
        let mut waited = 0.0_f64;
        loop {
            let interval: f64 = self
                .spec()
                .module
                .bind(py)
                .getattr("EXTERNAL_STALL_LOG_INTERVAL_SECONDS")?
                .extract()?;
            let queue = self.core.external.clone();
            let timeout = Duration::from_secs_f64(interval.max(0.0));
            let item = py.detach(move || queue.pop_timeout(timeout));
            if let Some((settle, pid, value)) = item {
                return self.settle_external(py, settle, pid, value);
            }
            waited += interval;
            let parked = self.live_parked_waiter_summary(true);
            let blocked = self.live_semaphore_waiters();
            let parked_obj: Py<PyAny> = if parked.is_empty() {
                "none".into_pyobject(py)?.into_any().unbind()
            } else {
                PyList::new(py, parked)?.into_any().unbind()
            };
            let blocked_obj: Py<PyAny> = if blocked.is_empty() {
                "none".into_pyobject(py)?.into_any().unbind()
            } else {
                blocked_dict(py, &blocked)?.into_any().unbind()
            };
            self.spec().logger.bind(py).call_method1(
                "warning",
                (
                    "scheduler stalled %.0fs waiting on external completions; \
                     parked waiters: %s; semaphore waiters: %s",
                    waited,
                    parked_obj,
                    blocked_obj,
                ),
            )?;
        }
    }

    fn keys_parked_by(&self, tid: Tid) -> Vec<WKey> {
        self.waiters
            .iter()
            .filter(|(_, entries)| entries.iter().any(|e| e.owner() == Some(tid)))
            .map(|(key, _)| *key)
            .collect()
    }

    /// Returns the `on_cancel` callbacks to run (after the lock is released).
    fn cancel_abandoned_external_promises(&mut self, py: Python<'_>, parked_keys: &[WKey]) -> PyResult<Vec<Py<PyAny>>> {
        let mut callbacks = Vec::new();
        for key in parked_keys {
            if key.kind != WKind::Promise {
                continue;
            }
            let has_callbacks = match self.promises.get(&key.id) {
                Some(p) => {
                    p.status == Status::Pending
                        && p.cancel_callbacks.as_ref().is_some_and(|c| !c.is_empty())
                }
                None => false,
            };
            if !has_callbacks {
                continue;
            }
            let live = self
                .waiters
                .get(key)
                .is_some_and(|entries| entries.iter().any(|e| !self.is_owner_cancelled(e.owner())));
            if live {
                continue;
            }
            if let Some(entries) = self.waiters.remove(key) {
                self.bury_entries(entries);
            }
            let error = self.spec().cancelled_error(py)?;
            let promise = self.promises.get_mut(&key.id).expect("checked above");
            promise.status = Status::Cancelled;
            let old = promise.result.replace(error);
            let taken = promise.cancel_callbacks.take().unwrap_or_default();
            if let Some(old) = old {
                self.bury(old);
            }
            callbacks.extend(taken);
        }
        Ok(callbacks)
    }

    fn bury_entries(&mut self, entries: Vec<WaiterEntry>) {
        for entry in entries {
            match entry {
                WaiterEntry::Wait { k, .. } | WaiterEntry::WaitExternal { k, .. } => {
                    self.discard_k(k)
                }
                WaiterEntry::Group { .. } => {}
            }
        }
    }

    fn detach_parked_continuation(&mut self, tid: Tid) -> Option<KRef> {
        if let Some(k) = self.detach_from_waiters(tid) {
            return Some(k);
        }
        if let Some(k) = self.detach_from_ready(tid) {
            return Some(k);
        }
        self.detach_from_semaphores(tid)
    }

    fn detach_from_waiters(&mut self, tid: Tid) -> Option<KRef> {
        for key in self.waiters.keys() {
            let Some(entries) = self.waiters.get(&key) else {
                continue;
            };
            let Some(index) = entries.iter().position(|e| e.owner() == Some(tid)) else {
                continue;
            };
            match &entries[index] {
                WaiterEntry::Group { group, .. } => {
                    let group = *group;
                    let state = self.groups.remove(&group);
                    if let Some(state) = state {
                        self.remove_group_waiters(group, &state.pending_keys);
                        return Some(state.waiter_k);
                    }
                    // A resolved group (cannot be parked) — nothing held.
                    return None;
                }
                WaiterEntry::WaitExternal { claimed, .. } => {
                    claimed.store(true, Ordering::Relaxed);
                }
                WaiterEntry::Wait { .. } => {}
            }
            let entries = self.waiters.get_mut(&key).expect("present");
            let entry = entries.remove(index);
            if entries.is_empty() {
                self.waiters.remove(&key);
            }
            return match entry {
                WaiterEntry::Wait { k, .. } | WaiterEntry::WaitExternal { k, .. } => Some(k),
                WaiterEntry::Group { .. } => None,
            };
        }
        None
    }

    fn detach_from_ready(&mut self, tid: Tid) -> Option<KRef> {
        let index = self.ready.iter().position(|item| match &item.entry {
            ReadyEntry::Resume { owner, .. }
            | ReadyEntry::Raise { owner, .. }
            | ReadyEntry::SemResume { owner, .. } => *owner == Some(tid),
            _ => false,
        })?;
        let item = self.ready.remove(index);
        heapq::heapify(&mut self.ready);
        match item.entry {
            ReadyEntry::Resume { k, value, .. } => {
                self.bury(value);
                Some(k)
            }
            ReadyEntry::Raise { k, error, .. } => {
                self.bury(error);
                Some(k)
            }
            ReadyEntry::SemResume { k, sid, .. } => {
                self.return_inflight_permit(sid, tid);
                Some(k)
            }
            _ => None,
        }
    }

    fn detach_from_semaphores(&mut self, tid: Tid) -> Option<KRef> {
        for sem in self.semaphores.values_mut() {
            if let Some(index) = sem.waiters.iter().position(|(owner, _)| *owner == Some(tid)) {
                let (_, k) = sem.waiters.remove(index).expect("index valid");
                return Some(k);
            }
        }
        None
    }

    fn finish_cancelled(&mut self, py: Python<'_>, tid: Tid, error: Py<PyAny>) -> PyResult<()> {
        if let Some(task) = self.tasks.get_mut(&tid) {
            task.status = Status::Cancelled;
            if let Some(old) = task.result.replace(error) {
                self.bury(old);
            }
        }
        self.wake_waiters(
            py,
            WKey {
                kind: WKind::Task,
                id: tid,
            },
        )?;
        self.release_task_refs(tid);
        Ok(())
    }

    fn who(owner: Owner) -> String {
        match owner {
            None => "root".to_string(),
            Some(tid) => format!("task {tid}"),
        }
    }

    fn live_parked_waiter_summary(&self, include_daemons: bool) -> Vec<String> {
        let mut parked = Vec::new();
        for (key, entries) in self.waiters.iter() {
            for entry in entries {
                let owner = entry.owner();
                if self.is_owner_cancelled(owner) {
                    continue;
                }
                if !include_daemons && self.is_daemon(owner) {
                    continue;
                }
                parked.push(format!(
                    "{} ({}) on {} {}",
                    Self::who(owner),
                    entry.type_name(),
                    key.kind.name(),
                    key.id
                ));
            }
        }
        parked
    }

    fn abandoned_ready_summary(&self) -> Vec<String> {
        let mut abandoned = Vec::new();
        for item in &self.ready {
            match &item.entry {
                ReadyEntry::New { tid } => {
                    let cancelled = self
                        .tasks
                        .get(tid)
                        .is_some_and(|t| t.status == Status::Cancelled);
                    if !cancelled && !self.is_daemon(Some(*tid)) {
                        abandoned.push(format!("unstarted task {tid}"));
                    }
                }
                ReadyEntry::Resume { owner, .. } | ReadyEntry::Raise { owner, .. } => {
                    if !self.is_owner_cancelled(*owner) && !self.is_daemon(*owner) {
                        let kind = if matches!(item.entry, ReadyEntry::Resume { .. }) {
                            "resume"
                        } else {
                            "raise"
                        };
                        abandoned.push(format!("queued {kind} for {}", Self::who(*owner)));
                    }
                }
                ReadyEntry::SemResume { owner, sid, .. } => {
                    if !self.is_owner_cancelled(*owner) && !self.is_daemon(*owner) {
                        let owner_text = match owner {
                            Some(tid) => tid.to_string(),
                            None => "None".to_string(),
                        };
                        abandoned.push(format!(
                            "queued semaphore {sid} permit for task {owner_text}"
                        ));
                    }
                }
                ReadyEntry::WaitExternal { .. } => {}
            }
        }
        abandoned
    }

    /// Live semaphore waiters (pruning cancelled ones): sid → waiting task ids.
    fn live_semaphore_waiters(&mut self) -> BTreeMap<u64, Vec<Tid>> {
        let cancelled: HashSet<Tid> = self
            .tasks
            .iter()
            .filter(|(_, t)| t.status == Status::Cancelled)
            .map(|(tid, _)| *tid)
            .collect();
        let mut blocked = BTreeMap::new();
        let mut pruned_ks = Vec::new();
        for (sid, sem) in self.semaphores.iter_mut() {
            let before = sem.waiters.len();
            let mut kept = VecDeque::with_capacity(before);
            for (owner, k) in sem.waiters.drain(..) {
                if owner.is_some_and(|tid| cancelled.contains(&tid)) {
                    pruned_ks.push(k);
                } else {
                    kept.push_back((owner, k));
                }
            }
            sem.waiters = kept;
            if !sem.waiters.is_empty() {
                blocked.insert(
                    *sid,
                    sem.waiters.iter().filter_map(|(owner, _)| *owner).collect(),
                );
            }
        }
        for k in pruned_ks {
            self.discard_k(k);
        }
        blocked
    }

    fn unreleasable_semaphores(&self, blocked: &BTreeMap<u64, Vec<Tid>>) -> BTreeMap<u64, Vec<Tid>> {
        let mut candidates: BTreeSet<u64> = blocked.keys().copied().collect();
        let mut changed = true;
        while !candidates.is_empty() && changed {
            let parked: HashSet<Owner> = candidates
                .iter()
                .flat_map(|sid| self.semaphores[sid].waiters.iter().map(|(owner, _)| *owner))
                .collect();
            let pruned: Vec<u64> = candidates
                .iter()
                .copied()
                .filter(|sid| {
                    let holders = &self.semaphores[sid].holders;
                    holders.is_empty() || holders.iter().any(|h| !parked.contains(h))
                })
                .collect();
            for sid in &pruned {
                candidates.remove(sid);
            }
            changed = !pruned.is_empty();
        }
        candidates
            .into_iter()
            .map(|sid| (sid, blocked[&sid].clone()))
            .collect()
    }

    fn deadlock_error(&self, py: Python<'_>, blocked: &BTreeMap<u64, Vec<Tid>>, parked: Option<Vec<String>>) -> PyErr {
        let build = || -> PyResult<PyErr> {
            let dict = blocked_dict(py, blocked)?;
            let error = match parked {
                Some(parked) => self
                    .spec()
                    .deadlock_error_cls
                    .bind(py)
                    .call1((dict, PyList::new(py, parked)?))?,
                None => self.spec().deadlock_error_cls.bind(py).call1((dict,))?,
            };
            Ok(PyErr::from_value(error))
        };
        build().unwrap_or_else(|err| err)
    }

    fn raise_if_semaphore_cycle_unresolvable(&self, py: Python<'_>, blocked: &BTreeMap<u64, Vec<Tid>>) -> PyResult<()> {
        if blocked.is_empty() {
            return Ok(());
        }
        let doomed = self.unreleasable_semaphores(blocked);
        if !doomed.is_empty() {
            return Err(self.deadlock_error(py, &doomed, None));
        }
        Ok(())
    }

    fn has_pending_external_waiters(&self) -> bool {
        for (key, entries) in self.waiters.iter() {
            if key.kind != WKind::Promise {
                continue;
            }
            let Some(promise) = self.promises.get(&key.id) else {
                continue;
            };
            if !promise.external || promise.status.is_terminal() {
                continue;
            }
            if entries.iter().any(|e| !self.is_owner_cancelled(e.owner())) {
                return true;
            }
        }
        false
    }

    fn pick_next(&mut self, py: Python<'_>) -> PyResult<DoCtrl> {
        let mut held: Vec<HeapItem> = Vec::new();
        let result = self.pick_next_inner(py, &mut held);
        for item in held {
            heapq::push(&mut self.ready, item);
        }
        result
    }

    fn pick_next_inner(&mut self, py: Python<'_>, held: &mut Vec<HeapItem>) -> PyResult<DoCtrl> {
        let mut shield_deferred = false;
        loop {
            self.drain(py)?;
            while let Some(item) = heapq::pop(&mut self.ready) {
                if shield_deferred
                    && !matches!(item.entry, ReadyEntry::WaitExternal { .. })
                    && self.is_live_daemon_entry(&item.entry)
                {
                    held.push(item);
                    continue;
                }
                match item.entry {
                    ReadyEntry::New { tid } => {
                        let Some(task) = self.tasks.get_mut(&tid) else {
                            return Err(key_error(tid.to_string()));
                        };
                        if task.status == Status::Cancelled {
                            continue;
                        }
                        task.status = Status::Running;
                        let program = task.program.take();
                        let boundaries = task.inner_boundaries.take().unwrap_or_default();
                        let Some(program) = program else {
                            return Err(key_error("program"));
                        };
                        return Ok(start_task(py, self.core, tid, program, boundaries));
                    }
                    ReadyEntry::Resume { owner, k, value } => {
                        if self.is_owner_cancelled(owner) {
                            self.discard_k(k);
                            self.bury(value);
                            continue;
                        }
                        let k = self.take_k_or_empty(k);
                        let value = obj_value(py, &value);
                        return Ok(DoCtrl::Resume { k, value });
                    }
                    ReadyEntry::SemResume { owner, k, sid } => {
                        if self.is_owner_cancelled(owner) {
                            if let Some(tid) = owner {
                                self.return_inflight_permit(sid, tid);
                            }
                            self.discard_k(k);
                            continue;
                        }
                        let k = self.take_k_or_empty(k);
                        return Ok(DoCtrl::Resume {
                            k,
                            value: Value::None,
                        });
                    }
                    ReadyEntry::Raise { owner, k, error } => {
                        if self.is_owner_cancelled(owner) {
                            self.discard_k(k);
                            self.bury(error);
                            continue;
                        }
                        let k = self.take_k_or_empty(k);
                        return Ok(DoCtrl::ResumeThrow {
                            k,
                            exception: Value::Opaque(PyShared::new(error)),
                        });
                    }
                    ReadyEntry::WaitExternal {
                        owner,
                        wk,
                        claimed,
                    } => {
                        if claimed.load(Ordering::Relaxed) || self.is_owner_cancelled(owner) {
                            continue;
                        }
                        if self.has_live_ready_entry(false) {
                            held.push(HeapItem {
                                neg_priority: item.neg_priority,
                                seq: item.seq,
                                entry: ReadyEntry::WaitExternal {
                                    owner,
                                    wk,
                                    claimed,
                                },
                            });
                            shield_deferred = true;
                            continue;
                        }
                        if !self.has_live_ready_entry(true) {
                            let blocked = self.live_semaphore_waiters();
                            self.raise_if_semaphore_cycle_unresolvable(py, &blocked)?;
                        }
                        self.drain_one_external(py)?;
                        self.drain(py)?;
                        if !claimed.load(Ordering::Relaxed) {
                            let priority = self.spec().priority_external_wait;
                            self.enqueue(
                                ReadyEntry::WaitExternal {
                                    owner,
                                    wk,
                                    claimed,
                                },
                                priority,
                            );
                        }
                        continue;
                    }
                }
            }
            let blocked = self.live_semaphore_waiters();
            if !self.has_pending_external_waiters() {
                let parked = self.live_parked_waiter_summary(true);
                if !blocked.is_empty() || !parked.is_empty() {
                    return Err(self.deadlock_error(py, &blocked, Some(parked)));
                }
            } else {
                self.raise_if_semaphore_cycle_unresolvable(py, &blocked)?;
            }
            if self.waiters.is_empty() {
                return Ok(DoCtrl::Pure { value: Value::None });
            }
            self.drain_one_external(py)?;
        }
    }

    fn release_task_refs(&mut self, tid: Tid) {
        let Some(task) = self.tasks.get_mut(&tid) else {
            return;
        };
        let program = task.program.take();
        task.inner_boundaries = None;
        task.spawn_site = None;
        if let Some(program) = program {
            self.bury(program);
        }
    }

    fn resume_with_waitable_result(&mut self, py: Python<'_>, owner: Owner, k: KRef, key: WKey, priority: Option<i64>) -> PyResult<Option<i64>> {
        let (status, result) = self.waitable_status(py, key)?;
        Ok(match status {
            Status::Completed => Some(self.enqueue_resume(owner, k, none_or(py, result), priority)),
            Status::Failed => Some(self.enqueue_raise(owner, k, none_or(py, result), priority)),
            Status::Cancelled => {
                let error = self.spec().cancelled_error(py)?;
                Some(self.enqueue_raise(owner, k, error, priority))
            }
            _ => None,
        })
    }

    fn register_pending_waiter(&mut self, wk: WKey, kind: GroupKind, owner: Owner, group: u64) {
        let external = wk.kind == WKind::Promise
            && self.promises.get(&wk.id).is_some_and(|p| p.external);
        let claimed = if external {
            let claimed: Claim = Arc::new(AtomicBool::new(false));
            let priority = self.spec().priority_external_wait;
            self.enqueue(
                ReadyEntry::WaitExternal {
                    owner,
                    wk,
                    claimed: claimed.clone(),
                },
                priority,
            );
            Some(claimed)
        } else {
            None
        };
        self.waiters
            .get_or_insert_with(wk, Vec::new)
            .push(WaiterEntry::Group {
                kind,
                owner,
                group,
                claimed,
            });
    }

    /// `remove_gather_waiters` / `remove_race_waiters`.
    fn remove_group_waiters(&mut self, group: u64, pending_keys: &[WKey]) {
        let keys: BTreeSet<WKey> = pending_keys.iter().copied().collect();
        for wk in keys {
            let Some(entries) = self.waiters.get_mut(&wk) else {
                continue;
            };
            entries.retain(|entry| match entry {
                WaiterEntry::Group {
                    group: g, claimed, ..
                } if *g == group => {
                    if let Some(claimed) = claimed {
                        claimed.store(true, Ordering::Relaxed);
                    }
                    false
                }
                _ => true,
            });
            if entries.is_empty() {
                self.waiters.remove(&wk);
            }
        }
    }

    fn resolve_group_with_error(&mut self, group: u64, error: Py<PyAny>) -> Option<i64> {
        let state = self.groups.remove(&group)?;
        self.remove_group_waiters(group, &state.pending_keys);
        Some(self.enqueue_raise(state.owner, state.waiter_k, error, None))
    }

    fn wake_gather_waiter(&mut self, py: Python<'_>, group: u64, completed: WKey) -> PyResult<Option<i64>> {
        if !self.groups.contains_key(&group) {
            return Ok(None);
        }
        let (status, result) = self.waitable_status(py, completed)?;
        match status {
            Status::Failed => return Ok(self.resolve_group_with_error(group, none_or(py, result))),
            Status::Cancelled => {
                let error = self.spec().cancelled_error(py)?;
                return Ok(self.resolve_group_with_error(group, error));
            }
            Status::Completed => {}
            _ => return Ok(None),
        }
        let state = self.groups.get_mut(&group).expect("checked above");
        state.remaining -= 1;
        if state.remaining != 0 {
            return Ok(None);
        }
        let state = self.groups.remove(&group).expect("checked above");
        let mut results = Vec::with_capacity(state.keys.len());
        for wk in &state.keys {
            results.push(none_or(py, self.waitable_status(py, *wk)?.1));
        }
        let list = PyList::new(py, results)?.into_any().unbind();
        Ok(Some(self.enqueue_resume(state.owner, state.waiter_k, list, None)))
    }

    fn wake_race_waiter(&mut self, py: Python<'_>, group: u64, completed: WKey) -> PyResult<Option<i64>> {
        if !self.groups.contains_key(&group) {
            return Ok(None);
        }
        let (status, result) = self.waitable_status(py, completed)?;
        if !status.is_terminal() {
            return Ok(None);
        }
        let state = self.groups.remove(&group).expect("checked above");
        self.remove_group_waiters(group, &state.pending_keys);
        Ok(Some(match status {
            Status::Completed => {
                self.enqueue_resume(state.owner, state.waiter_k, none_or(py, result), None)
            }
            Status::Failed => {
                self.enqueue_raise(state.owner, state.waiter_k, none_or(py, result), None)
            }
            _ => {
                let error = self.spec().cancelled_error(py)?;
                self.enqueue_raise(state.owner, state.waiter_k, error, None)
            }
        }))
    }

    fn wake_waiters(&mut self, py: Python<'_>, completed: WKey) -> PyResult<Option<i64>> {
        let entries = self.waiters.remove(&completed).unwrap_or_default();
        let mut woken_min: Option<i64> = None;
        for entry in entries {
            let queued_at = match entry {
                WaiterEntry::Wait {
                    owner,
                    k,
                    wake_priority,
                } => self.resume_with_waitable_result(py, owner, k, completed, wake_priority)?,
                WaiterEntry::WaitExternal {
                    owner,
                    k,
                    claimed,
                    wake_priority,
                } => {
                    claimed.store(true, Ordering::Relaxed);
                    self.resume_with_waitable_result(py, owner, k, completed, wake_priority)?
                }
                WaiterEntry::Group {
                    kind,
                    group,
                    claimed,
                    ..
                } => {
                    if let Some(claimed) = claimed {
                        claimed.store(true, Ordering::Relaxed);
                    }
                    match kind {
                        GroupKind::Gather => self.wake_gather_waiter(py, group, completed)?,
                        GroupKind::Race => self.wake_race_waiter(py, group, completed)?,
                    }
                }
            };
            if let Some(q) = queued_at {
                if woken_min.is_none_or(|m| q < m) {
                    woken_min = Some(q);
                }
            }
        }
        Ok(woken_min)
    }

    fn pop_live_semaphore_waiter(&mut self, sid: u64) -> Option<(Owner, KRef)> {
        loop {
            let waiter = self.semaphores.get_mut(&sid)?.waiters.pop_front()?;
            if self.is_owner_cancelled(waiter.0) {
                self.discard_k(waiter.1);
                continue;
            }
            return Some(waiter);
        }
    }

    fn grant_permit_to_next_waiter(&mut self, sid: u64) -> bool {
        let Some((owner, k)) = self.pop_live_semaphore_waiter(sid) else {
            return false;
        };
        if let Some(sem) = self.semaphores.get_mut(&sid) {
            sem.holders.push(owner);
        }
        let priority = self.task_priority(owner);
        self.enqueue(ReadyEntry::SemResume { owner, k, sid }, priority);
        true
    }

    fn return_inflight_permit(&mut self, sid: u64, cancelled_tid: Tid) {
        if let Some(sem) = self.semaphores.get_mut(&sid) {
            if let Some(index) = sem.holders.iter().position(|h| *h == Some(cancelled_tid)) {
                sem.holders.remove(index);
            }
        }
        if !self.grant_permit_to_next_waiter(sid) {
            if let Some(sem) = self.semaphores.get_mut(&sid) {
                sem.permits += 1;
            }
        }
    }

    // --- effect dispatch ----------------------------------------------------

    fn dispatch(&mut self, py: Python<'_>, current: Owner, kind: Kind, effect: &Bound<'_, PyAny>, k: KRef) -> PyResult<Outcome> {
        self.drain(py)?;
        match kind {
            Kind::Spawn => self.on_spawn(py, current, effect, k),
            Kind::TaskCompleted => self.on_task_completed(py, effect, k),
            Kind::Wait => self.on_wait(py, current, effect, k),
            Kind::Gather => self.on_gather(py, current, effect, k),
            Kind::Race => self.on_race(py, current, effect, k),
            Kind::Cancel => self.on_cancel(py, current, effect, k),
            Kind::CreatePromise => {
                let pid = self.alloc_promise(py, false);
                let registrar = self.core.registrar(py)?;
                let kwargs = PyDict::new(py);
                kwargs.set_item("_register", registrar)?;
                let handle = self
                    .spec()
                    .promise_cls
                    .bind(py)
                    .call((pid,), Some(&kwargs))?;
                self.register_handle(
                    py,
                    WKey {
                        kind: WKind::Promise,
                        id: pid,
                    },
                    &handle,
                )?;
                self.resume_now(py, k, handle.unbind())
            }
            Kind::CompletePromise => self.on_settle_promise(py, current, effect, k, true),
            Kind::FailPromise => self.on_settle_promise(py, current, effect, k, false),
            Kind::CreateExternalPromise => {
                let pid = self.alloc_promise(py, true);
                let kwargs = PyDict::new(py);
                kwargs.set_item("_register", self.core.registrar(py)?)?;
                kwargs.set_item("_bind_cancel", self.core.cancel_binder(py)?)?;
                let handle = self
                    .spec()
                    .external_promise_cls
                    .bind(py)
                    .call((pid, self.core.queue_obj(py)?), Some(&kwargs))?;
                self.register_handle(
                    py,
                    WKey {
                        kind: WKind::Promise,
                        id: pid,
                    },
                    &handle,
                )?;
                self.resume_now(py, k, handle.unbind())
            }
            Kind::Introspection => {
                let dict = PyDict::new(py);
                dict.set_item("tasks", self.tasks.len())?;
                dict.set_item("promises", self.promises.len())?;
                dict.set_item("semaphores", self.semaphores.len())?;
                dict.set_item("waiters", self.waiters.len())?;
                dict.set_item("ready", self.ready.len())?;
                dict.set_item("handle_refs", self.handle_refs.len())?;
                dict.set_item(
                    "handle_ref_total",
                    self.handle_refs.values().map(Vec::len).sum::<usize>(),
                )?;
                self.resume_now(py, k, dict.into_any().unbind())
            }
            Kind::CreateSemaphore => {
                let permits: i64 = effect.getattr(pyo3::intern!(py, "permits"))?.extract()?;
                if permits < 1 {
                    return Err(PyValueError::new_err("permits must be >= 1"));
                }
                let sid = self.fresh_id(py);
                self.semaphores.insert(
                    sid,
                    SemEntry {
                        permits,
                        max_permits: permits,
                        waiters: VecDeque::new(),
                        holders: Vec::new(),
                    },
                );
                let handle = self.spec().semaphore_cls.bind(py).call1((sid,))?.unbind();
                self.resume_now(py, k, handle)
            }
            Kind::AcquireSemaphore => {
                let sid = semaphore_id(py, effect)?;
                let sem = self
                    .semaphores
                    .get_mut(&sid)
                    .ok_or_else(|| key_error(sid.to_string()))?;
                if sem.permits > 0 {
                    sem.permits -= 1;
                    sem.holders.push(current);
                    return self.resume_now(py, k, py.None());
                }
                sem.waiters.push_back((current, k));
                Ok(Outcome::Ctrl(self.pick_next(py)?))
            }
            Kind::ReleaseSemaphore => {
                let sid = semaphore_id(py, effect)?;
                if !self.semaphores.contains_key(&sid) {
                    return Err(key_error(sid.to_string()));
                }
                let transferred = self.grant_permit_to_next_waiter(sid);
                let sem = self.semaphores.get_mut(&sid).expect("checked above");
                if !transferred && sem.permits >= sem.max_permits {
                    return Err(PyRuntimeError::new_err("semaphore released too many times"));
                }
                if let Some(index) = sem.holders.iter().position(|h| *h == current) {
                    sem.holders.remove(index);
                }
                if !transferred {
                    sem.permits += 1;
                }
                self.resume_now(py, k, py.None())
            }
        }
    }

    /// `r = yield Resume(k, value); return r`.
    fn resume_now(&mut self, py: Python<'_>, k: KRef, value: Py<PyAny>) -> PyResult<Outcome> {
        let k = self.take_k_or_empty(k);
        Ok(Outcome::Ctrl(DoCtrl::Resume {
            k,
            value: obj_value(py, &value),
        }))
    }

    /// `return (yield ResumeThrow(k, error))`.
    fn throw_now(&mut self, k: KRef, error: Py<PyAny>) -> Outcome {
        let k = self.take_k_or_empty(k);
        Outcome::Ctrl(DoCtrl::ResumeThrow {
            k,
            exception: Value::Opaque(PyShared::new(error)),
        })
    }

    fn on_spawn(&mut self, py: Python<'_>, current: Owner, effect: &Bound<'_, PyAny>, k: KRef) -> PyResult<Outcome> {
        // get_inner_boundaries(k): the boundary stack between the yield site
        // and this scheduler prompt (the last entry), innermost first.
        let (boundaries, spawn_site) = {
            let cont = self.conts.get(&k).expect("the performing continuation");
            let mut boundaries = cont.boundary_callables().ok_or_else(|| {
                PyRuntimeError::new_err("GetBoundaries: continuation has no detached fiber chain")
            })?;
            if let Some((last_kind, _)) = boundaries.last() {
                if *last_kind != BoundaryKind::Handler {
                    return Err(PyRuntimeError::new_err(
                        "get_inner_boundaries: expected the catching handler as the last \
                         chain entry, got kind='observer'",
                    ));
                }
                boundaries.pop();
            }
            let spawn_site = cont
                .first_source_location()
                .map(|loc| format!("{}  {}:{}", loc.func_name, loc.source_file, loc.source_line));
            (boundaries, spawn_site)
        };
        let program = effect.getattr(pyo3::intern!(py, "program"))?.unbind();
        let priority: i64 = effect.getattr(pyo3::intern!(py, "priority"))?.extract()?;
        let daemon: bool = effect.getattr(pyo3::intern!(py, "daemon"))?.is_truthy()?;
        let tid = self.fresh_id(py);
        self.tasks.insert(
            tid,
            TaskEntry {
                status: Status::Pending,
                result: None,
                program: Some(program),
                priority,
                daemon,
                inner_boundaries: Some(boundaries),
                spawn_site: Some(spawn_site),
            },
        );
        self.enqueue(ReadyEntry::New { tid }, priority);
        let handle = self.spec().task_cls.bind(py).call1((tid,))?;
        self.register_handle(
            py,
            WKey {
                kind: WKind::Task,
                id: tid,
            },
            &handle,
        )?;
        self.enqueue_resume(current, k, handle.unbind(), None);
        Ok(Outcome::Ctrl(self.pick_next(py)?))
    }

    fn on_task_completed(&mut self, py: Python<'_>, effect: &Bound<'_, PyAny>, k: KRef) -> PyResult<Outcome> {
        let tid: Tid = effect.getattr(pyo3::intern!(py, "task_id"))?.extract()?;
        let r = effect.getattr(pyo3::intern!(py, "result"))?;
        let status = self
            .tasks
            .get(&tid)
            .map(|t| t.status)
            .ok_or_else(|| key_error(tid.to_string()))?;
        let error: Option<Bound<'_, PyAny>> = if let Ok(ok) = r.cast::<PyResultOk>() {
            let _ = ok;
            None
        } else if let Ok(err) = r.cast::<PyResultErr>() {
            Some(err.get().error.bind(py).clone())
        } else {
            let is_ok = r.hasattr("is_ok")? && r.call_method0("is_ok")?.is_truthy()?;
            if is_ok {
                None
            } else if r.hasattr("error")? {
                Some(r.getattr("error")?)
            } else {
                Some(r.clone())
            }
        };
        let cancelled_error = self.spec().task_cancelled_error.bind(py).clone();
        if status == Status::Cancelled {
            self.release_task_refs(tid);
        } else if status == Status::Cancelling
            && error
                .as_ref()
                .is_none_or(|e| e.is_instance(&cancelled_error).unwrap_or(false))
        {
            let error = match error {
                Some(e) => e.unbind(),
                None => self.spec().cancelled_error(py)?,
            };
            self.finish_cancelled(py, tid, error)?;
        } else {
            match error {
                None => {
                    let value = r.getattr("value")?.unbind();
                    let task = self.tasks.get_mut(&tid).expect("checked above");
                    task.status = Status::Completed;
                    if let Some(old) = task.result.replace(value) {
                        self.bury(old);
                    }
                }
                Some(error) => {
                    let spawn_site = self
                        .tasks
                        .get(&tid)
                        .and_then(|t| t.spawn_site.clone())
                        .map(|site| match site {
                            Some(s) => s.into_pyobject(py).map(|s| s.into_any().unbind()),
                            None => Ok(py.None()),
                        })
                        .unwrap_or_else(|| Ok("".into_pyobject(py)?.into_any().unbind()))?;
                    if error.is_instance_of::<PyBaseException>() {
                        if let Ok(tb) = error.getattr("__doeff_traceback__") {
                            let entry = PyDict::new(py);
                            entry.set_item("kind", "spawn_boundary")?;
                            entry.set_item("task_id", tid)?;
                            entry.set_item("spawn_site", spawn_site)?;
                            tb.call_method1("insert", (0, entry))?;
                        }
                    }
                    let task = self.tasks.get_mut(&tid).expect("checked above");
                    task.status = Status::Failed;
                    if let Some(old) = task.result.replace(error.unbind()) {
                        self.bury(old);
                    }
                }
            }
            self.wake_waiters(
                py,
                WKey {
                    kind: WKind::Task,
                    id: tid,
                },
            )?;
            self.release_task_refs(tid);
        }
        let next = self.pick_next(py)?;
        // The completing task's continuation is never resumed.
        self.discard_k(k);
        Ok(Outcome::Ctrl(next))
    }

    fn on_wait(&mut self, py: Python<'_>, current: Owner, effect: &Bound<'_, PyAny>, k: KRef) -> PyResult<Outcome> {
        let wk = self.waitable_key(py, &effect.getattr(pyo3::intern!(py, "task"))?)?;
        let (status, result) = self.waitable_status(py, wk)?;
        match status {
            Status::Completed => return self.resume_now(py, k, none_or(py, result)),
            Status::Failed => return Ok(self.throw_now(k, none_or(py, result))),
            Status::Cancelled => {
                let error = self.spec().cancelled_error(py)?;
                return Ok(self.throw_now(k, error));
            }
            _ => {}
        }
        let priority_obj = effect.getattr(pyo3::intern!(py, "priority"))?;
        let priority: Option<i64> = if priority_obj.is_none() {
            None
        } else {
            Some(priority_obj.extract()?)
        };
        let external = wk.kind == WKind::Promise
            && self.promises.get(&wk.id).is_some_and(|p| p.external);
        if external {
            if priority == Some(self.spec().priority_idle) {
                self.waiters
                    .get_or_insert_with(wk, Vec::new)
                    .push(WaiterEntry::Wait {
                        owner: current,
                        k,
                        wake_priority: None,
                    });
            } else {
                let claimed: Claim = Arc::new(AtomicBool::new(false));
                self.waiters
                    .get_or_insert_with(wk, Vec::new)
                    .push(WaiterEntry::WaitExternal {
                        owner: current,
                        k,
                        claimed: claimed.clone(),
                        wake_priority: priority,
                    });
                let shield = self.spec().priority_external_wait;
                self.enqueue(
                    ReadyEntry::WaitExternal {
                        owner: current,
                        wk,
                        claimed,
                    },
                    shield,
                );
            }
        } else {
            self.waiters
                .get_or_insert_with(wk, Vec::new)
                .push(WaiterEntry::Wait {
                    owner: current,
                    k,
                    wake_priority: priority,
                });
        }
        Ok(Outcome::Ctrl(self.pick_next(py)?))
    }

    fn on_gather(&mut self, py: Python<'_>, current: Owner, effect: &Bound<'_, PyAny>, k: KRef) -> PyResult<Outcome> {
        let tasks = effect.getattr(pyo3::intern!(py, "tasks"))?;
        let mut wks = Vec::new();
        for t in tasks.try_iter()? {
            wks.push(self.waitable_key(py, &t?)?);
        }
        let mut pending = Vec::new();
        for wk in &wks {
            let (status, result) = self.waitable_status(py, *wk)?;
            match status {
                Status::Failed => return Ok(self.throw_now(k, none_or(py, result))),
                Status::Cancelled => {
                    let error = self.spec().cancelled_error(py)?;
                    return Ok(self.throw_now(k, error));
                }
                s if !s.is_terminal() => pending.push(*wk),
                _ => {}
            }
        }
        if pending.is_empty() {
            let mut results = Vec::with_capacity(wks.len());
            for wk in &wks {
                results.push(none_or(py, self.waitable_status(py, *wk)?.1));
            }
            let list = PyList::new(py, results)?.into_any().unbind();
            return self.resume_now(py, k, list);
        }
        let group = self.next_group;
        self.next_group += 1;
        self.groups.insert(
            group,
            Group {
                owner: current,
                waiter_k: k,
                keys: wks,
                pending_keys: pending.clone(),
                remaining: pending.len(),
            },
        );
        for wk in pending {
            self.register_pending_waiter(wk, GroupKind::Gather, current, group);
        }
        Ok(Outcome::Ctrl(self.pick_next(py)?))
    }

    fn on_race(&mut self, py: Python<'_>, current: Owner, effect: &Bound<'_, PyAny>, k: KRef) -> PyResult<Outcome> {
        let tasks: Vec<Bound<'_, PyAny>> = effect
            .getattr(pyo3::intern!(py, "tasks"))?
            .try_iter()?
            .collect::<PyResult<_>>()?;
        if tasks.is_empty() {
            let error = PyValueError::new_err("Race() requires at least one Task or Future to race");
            return Ok(self.throw_now(k, error.value(py).clone().into_any().unbind()));
        }
        for t in &tasks {
            let wk = self.waitable_key(py, t)?;
            let (status, result) = self.waitable_status(py, wk)?;
            match status {
                Status::Completed => return self.resume_now(py, k, none_or(py, result)),
                Status::Failed => return Ok(self.throw_now(k, none_or(py, result))),
                Status::Cancelled => {
                    let error = self.spec().cancelled_error(py)?;
                    return Ok(self.throw_now(k, error));
                }
                _ => {}
            }
        }
        let mut pending = Vec::new();
        for t in &tasks {
            let wk = self.waitable_key(py, t)?;
            if !self.waitable_status(py, wk)?.0.is_terminal() {
                pending.push(wk);
            }
        }
        if !pending.is_empty() {
            let group = self.next_group;
            self.next_group += 1;
            self.groups.insert(
                group,
                Group {
                    owner: current,
                    waiter_k: k,
                    keys: Vec::new(),
                    pending_keys: pending.clone(),
                    remaining: 0,
                },
            );
            for wk in pending {
                self.register_pending_waiter(wk, GroupKind::Race, current, group);
            }
        }
        Ok(Outcome::Ctrl(self.pick_next(py)?))
    }

    fn on_cancel(&mut self, py: Python<'_>, current: Owner, effect: &Bound<'_, PyAny>, k: KRef) -> PyResult<Outcome> {
        let tid: Tid = effect
            .getattr(pyo3::intern!(py, "task"))?
            .getattr(pyo3::intern!(py, "task_id"))?
            .extract()?;
        let status = self.tasks.get(&tid).map(|t| t.status);
        let mut callbacks = Vec::new();
        match status {
            Some(Status::Pending) => {
                let error = self.spec().cancelled_error(py)?;
                self.finish_cancelled(py, tid, error)?;
            }
            Some(Status::Running) | Some(Status::Cancelling) => {
                self.tasks.get_mut(&tid).expect("present").status = Status::Cancelling;
                if current == Some(tid) {
                    let error = self.spec().cancelled_error(py)?;
                    return Ok(self.throw_now(k, error));
                }
                let parked_keys = self.keys_parked_by(tid);
                match self.detach_parked_continuation(tid) {
                    None => {
                        self.spec().logger.bind(py).call_method1(
                            "warning",
                            (
                                "Cancel(task %s): the scheduler does not hold its continuation; \
                                 the task is dropped without running its except/finally blocks",
                                tid,
                            ),
                        )?;
                        let error = self.spec().cancelled_error(py)?;
                        self.finish_cancelled(py, tid, error)?;
                    }
                    Some(cont) => {
                        let error = self.spec().cancelled_error(py)?;
                        self.enqueue_raise(Some(tid), cont, error, None);
                    }
                }
                callbacks = self.cancel_abandoned_external_promises(py, &parked_keys)?;
            }
            _ => {}
        }
        if callbacks.is_empty() {
            return self.resume_now(py, k, py.None());
        }
        Ok(Outcome::CancelCallbacks { k, callbacks })
    }

    fn on_settle_promise(&mut self, py: Python<'_>, current: Owner, effect: &Bound<'_, PyAny>, k: KRef, complete: bool) -> PyResult<Outcome> {
        let name = if complete { "CompletePromise" } else { "FailPromise" };
        let pid: u64 = effect
            .getattr(pyo3::intern!(py, "promise"))?
            .getattr(pyo3::intern!(py, "promise_id"))?
            .extract()?;
        let promise = self
            .promises
            .get(&pid)
            .ok_or_else(|| key_error(pid.to_string()))?;
        if promise.external {
            let how = if complete {
                "ExternalPromise.complete()"
            } else {
                "ExternalPromise.fail()"
            };
            let error = PyRuntimeError::new_err(format!(
                "{name} on external promise {pid}: resolve it through {how} instead"
            ));
            return Ok(self.throw_now(k, error.value(py).clone().into_any().unbind()));
        }
        if promise.status != Status::Pending {
            let error = PyRuntimeError::new_err(format!(
                "{name} on promise {pid} which is already {}",
                promise.status.name()
            ));
            return Ok(self.throw_now(k, error.value(py).clone().into_any().unbind()));
        }
        let value = if complete {
            effect.getattr(pyo3::intern!(py, "value"))?.unbind()
        } else {
            effect.getattr(pyo3::intern!(py, "error"))?.unbind()
        };
        let promise = self.promises.get_mut(&pid).expect("checked above");
        promise.status = if complete {
            Status::Completed
        } else {
            Status::Failed
        };
        promise.result = Some(value);
        let woken_min = self.wake_waiters(
            py,
            WKey {
                kind: WKind::Promise,
                id: pid,
            },
        )?;
        let own = self.task_priority(current);
        let completer_priority = match woken_min {
            None => own,
            Some(w) => own.min(w),
        };
        self.enqueue_resume(current, k, py.None(), Some(completer_priority));
        Ok(Outcome::Ctrl(self.pick_next(py)?))
    }
}

fn none_or(py: Python<'_>, value: Option<Py<PyAny>>) -> Py<PyAny> {
    value.unwrap_or_else(|| py.None())
}

fn semaphore_id(py: Python<'_>, effect: &Bound<'_, PyAny>) -> PyResult<u64> {
    effect
        .getattr(pyo3::intern!(py, "semaphore"))?
        .getattr(pyo3::intern!(py, "sem_id"))?
        .extract()
}

fn blocked_dict<'py>(py: Python<'py>, blocked: &BTreeMap<u64, Vec<Tid>>) -> PyResult<Bound<'py, PyDict>> {
    let dict = PyDict::new(py);
    for (sid, tids) in blocked {
        dict.set_item(sid, PyList::new(py, tids)?)?;
    }
    Ok(dict)
}

/// The DoCtrl that starts task `tid`: its program re-wrapped with the
/// boundaries captured at the spawn site, run under the task's own scheduler
/// prompt by a task wrapper (the Python version's `make_handler(tid)(
/// wrap_task(tid, prog))`).
fn start_task(
    py: Python<'_>,
    core: &Arc<Core>,
    tid: Tid,
    program: Py<PyAny>,
    boundaries: Vec<(BoundaryKind, CallableRef)>,
) -> DoCtrl {
    let _ = py;
    let stream = TaskWrapStream {
        core: core.clone(),
        tid,
        phase: Phase::Start {
            program,
            boundaries,
        },
    };
    DoCtrl::WithHandler {
        handler: Value::Callable(core.handler(Some(tid))),
        body: Box::new(DoCtrl::Expand {
            expr: Box::new(DoCtrl::Pure {
                value: Value::Stream(IRStreamRef::new(Box::new(stream))),
            }),
        }),
    }
}

// ---------------------------------------------------------------------------
// The task wrapper (the Python version's `wrap_task`)
// ---------------------------------------------------------------------------

enum Phase {
    Start {
        program: Py<PyAny>,
        boundaries: Vec<(BoundaryKind, CallableRef)>,
    },
    /// Running the task's program (`result = yield prog`).
    Body,
    /// Performed `TaskCompleted(tid, Ok(result))` (never resumed).
    CompletedOk,
    /// Asked for the error-site execution context of `error`.
    Context {
        error: Py<PyAny>,
    },
    /// Performed `TaskCompleted(tid, Err(error))` (never resumed).
    CompletedErr,
    Done,
}

struct TaskWrapStream {
    core: Arc<Core>,
    tid: Tid,
    phase: Phase,
}

impl std::fmt::Debug for TaskWrapStream {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "TaskWrapStream(task {})", self.tid)
    }
}

impl TaskWrapStream {
    fn task_completed(&self, py: Python<'_>, result: Bound<'_, PyAny>) -> PyResult<DoCtrl> {
        let cls = self.core.spec.effect_types[1].bind(py);
        let effect = cls.call1((self.tid, result))?;
        Ok(DoCtrl::Perform {
            effect: Value::Opaque(PyShared::new(effect.unbind())),
        })
    }

    /// `except Exception as e` / `except (KeyboardInterrupt, SystemExit)` of
    /// the Python wrapper, for an error thrown at `yield prog` or at the
    /// `TaskCompleted(Ok)` perform (both inside its `try`).
    fn on_error_in_try(&mut self, py: Python<'_>, error: Bound<'_, PyAny>) -> StreamStep {
        if error.is_instance_of::<PyException>() {
            self.phase = Phase::Context {
                error: error.unbind(),
            };
            return StreamStep::Instruction(DoCtrl::GetExecutionContext);
        }
        if error.is_instance_of::<PyKeyboardInterrupt>() || error.is_instance_of::<PySystemExit>() {
            // #507: record the failure and wake the waiters directly, then
            // re-raise so the interrupt still unwinds out of the whole run.
            if let Ok(mut locked) = self.core.lock() {
                let tid = self.tid;
                let terminal = locked
                    .tasks
                    .get(&tid)
                    .map_or(true, |t| t.status.is_terminal());
                if !terminal {
                    let task = locked.tasks.get_mut(&tid).expect("present");
                    task.status = Status::Failed;
                    let old = task.result.replace(error.clone().unbind());
                    if let Some(old) = old {
                        locked.bury(old);
                    }
                    let _ = locked.wake_waiters(
                        py,
                        WKey {
                            kind: WKind::Task,
                            id: tid,
                        },
                    );
                }
                locked.release_task_refs(tid);
                let graveyard = locked.take_graveyard();
                drop(locked);
                drop(graveyard);
            }
        }
        self.phase = Phase::Done;
        StreamStep::Error(Value::Opaque(PyShared::new(error.unbind())))
    }

    fn first_step(
        &mut self,
        py: Python<'_>,
        program: Py<PyAny>,
        boundaries: Vec<(BoundaryKind, CallableRef)>,
    ) -> StreamStep {
        match classify_python_object(py, program.bind(py)) {
            Ok(mut body) => {
                for (kind, callable) in boundaries {
                    body = match kind {
                        BoundaryKind::Handler => DoCtrl::WithHandler {
                            handler: Value::Callable(callable),
                            body: Box::new(body),
                        },
                        BoundaryKind::Observer => DoCtrl::WithObserve {
                            observer: Value::Callable(callable),
                            body: Box::new(body),
                        },
                    };
                }
                self.phase = Phase::Body;
                StreamStep::Instruction(body)
            }
            Err(message) => {
                // The Python wrapper's `yield prog` of a value that is not a
                // DoExpr: the VM throws its classification message into the
                // wrapper as a RuntimeError, caught by `except Exception`.
                let mut message = message;
                for (kind, _) in boundaries.iter() {
                    message = match kind {
                        BoundaryKind::Handler => format!("WithHandler body: {message}"),
                        BoundaryKind::Observer => format!("WithObserve body: {message}"),
                    };
                }
                let shown = message
                    .clone()
                    .into_pyobject(py)
                    .ok()
                    .and_then(|s| s.repr().ok().map(|r| r.to_string()))
                    .unwrap_or(message);
                let error = PyRuntimeError::new_err(format!(
                    "VM error (non-exception value in Raise): {shown}"
                ));
                self.phase = Phase::Body;
                self.on_error_in_try(py, error.value(py).clone().into_any())
            }
        }
    }
}

impl IRStream for TaskWrapStream {
    fn resume(&mut self, value: Value) -> StreamStep {
        Python::attach(|py| {
            match std::mem::replace(&mut self.phase, Phase::Done) {
                Phase::Start {
                    program,
                    boundaries,
                } => self.first_step(py, program, boundaries),
                Phase::Body => {
                    let result = value_to_python(py, value);
                    let ok = match Bound::new(py, PyResultOk { value: result.unbind() }) {
                        Ok(ok) => ok.into_any(),
                        Err(err) => {
                            return StreamStep::Error(exception_value(py, err));
                        }
                    };
                    match self.task_completed(py, ok) {
                        Ok(ctrl) => {
                            self.phase = Phase::CompletedOk;
                            StreamStep::Instruction(ctrl)
                        }
                        Err(err) => {
                            self.phase = Phase::Body;
                            self.on_error_in_try(py, err.value(py).clone().into_any())
                        }
                    }
                }
                Phase::Context { error } => {
                    let ctx = value_to_python(py, value);
                    // Enrichment failures are ignored (`except Exception: pass`).
                    let _ = self
                        .core
                        .spec
                        .enrich_traceback
                        .bind(py)
                        .call1((error.bind(py), py.None(), ctx));
                    self.complete_with_error(py, error)
                }
                Phase::CompletedOk | Phase::CompletedErr | Phase::Done => {
                    StreamStep::Done(Value::None)
                }
            }
        })
    }

    fn throw(&mut self, error: Value) -> StreamStep {
        Python::attach(|py| {
            let error = value_to_python(py, error);
            let error = if error.is_instance_of::<PyBaseException>() {
                error
            } else {
                let shown = error
                    .repr()
                    .map(|r| r.to_string())
                    .unwrap_or_else(|_| "<value>".to_string());
                PyRuntimeError::new_err(format!("VM error (non-exception value in Raise): {shown}"))
                    .value(py)
                    .clone()
                    .into_any()
            };
            match std::mem::replace(&mut self.phase, Phase::Done) {
                Phase::Start { .. } | Phase::Done | Phase::CompletedErr => {
                    StreamStep::Error(Value::Opaque(PyShared::new(error.unbind())))
                }
                Phase::Body | Phase::CompletedOk => self.on_error_in_try(py, error),
                Phase::Context { error: original } => {
                    // `except Exception: pass` around the context fetch.
                    if error.is_instance_of::<PyException>() {
                        self.complete_with_error(py, original)
                    } else {
                        StreamStep::Error(Value::Opaque(PyShared::new(error.unbind())))
                    }
                }
            }
        })
    }
}

impl TaskWrapStream {
    fn complete_with_error(&mut self, py: Python<'_>, error: Py<PyAny>) -> StreamStep {
        let err = match Bound::new(py, PyResultErr { error, captured_traceback: py.None() }) {
            Ok(err) => err.into_any(),
            Err(err) => return StreamStep::Error(exception_value(py, err)),
        };
        match self.task_completed(py, err) {
            Ok(ctrl) => {
                self.phase = Phase::CompletedErr;
                StreamStep::Instruction(ctrl)
            }
            Err(err) => StreamStep::Error(exception_value(py, err)),
        }
    }
}

// ---------------------------------------------------------------------------
// The prompt handler (one per task + the root)
// ---------------------------------------------------------------------------

#[derive(Debug)]
pub struct SchedulerHandler {
    core: Arc<Core>,
    tid: Owner,
}

impl Callable for SchedulerHandler {
    fn call(&self, _args: Vec<Value>) -> Result<Value, VMError> {
        Err(VMError::type_error(
            "the scheduler prompt is dispatched by the VM, not called",
        ))
    }

    fn call_handler(&self, args: Vec<Value>) -> Result<DoCtrl, VMError> {
        let mut args = args.into_iter();
        let effect = args
            .next()
            .ok_or_else(|| VMError::internal("scheduler: missing effect"))?;
        let k = args
            .next()
            .ok_or_else(|| VMError::internal("scheduler: missing continuation"))?;
        self.core.handle(self.tid, effect, k)
    }

    fn name(&self) -> Option<String> {
        Some(HANDLER_QUALNAME.to_string())
    }

    fn accepts(&self, effect: &Value) -> bool {
        match effect {
            Value::Opaque(obj) => self.core.kind_of(obj).is_some(),
            _ => false,
        }
    }

    fn as_any(&self) -> &dyn std::any::Any {
        self
    }
}

// ---------------------------------------------------------------------------
// Python-facing classes
// ---------------------------------------------------------------------------

/// A scheduler prompt as a Python object (what `GetHandlers` / boundary
/// capture return for the scheduler, and what `WithHandler` accepts back).
/// Carries `__doeff_scheduler_prompt__` like the Python version's raw handler.
#[pyclass(name = "SchedulerPrompt", module = "doeff_vm.doeff_vm", frozen)]
pub struct PySchedulerPrompt {
    pub(crate) callable: CallableRef,
}

impl PySchedulerPrompt {
    pub(crate) fn wraps(callable: &CallableRef) -> bool {
        callable.as_any().is::<SchedulerHandler>()
    }
}

#[pymethods]
impl PySchedulerPrompt {
    #[getter(__doeff_scheduler_prompt__)]
    fn is_scheduler_prompt(&self) -> bool {
        true
    }

    #[getter(__qualname__)]
    fn qualname(&self) -> &'static str {
        HANDLER_QUALNAME
    }

    #[getter(__name__)]
    fn name(&self) -> &'static str {
        "raw_handler"
    }

    #[pyo3(signature = (*_args, **_kwargs))]
    fn __call__(
        &self,
        _args: &Bound<'_, PyTuple>,
        _kwargs: Option<&Bound<'_, PyDict>>,
    ) -> PyResult<()> {
        Err(PyTypeError::new_err(
            "the scheduler prompt is dispatched by the VM; install it with WithHandler",
        ))
    }

    fn __repr__(&self) -> String {
        match self.callable.as_any().downcast_ref::<SchedulerHandler>() {
            Some(h) => match h.tid {
                Some(tid) => format!("<scheduler prompt of task {tid}>"),
                None => "<scheduler prompt of the root>".to_string(),
            },
            None => "<scheduler prompt>".to_string(),
        }
    }
}

#[pyclass(name = "SchedulerCore", module = "doeff_vm.doeff_vm", frozen)]
pub struct PySchedulerCore {
    core: Arc<Core>,
}

#[pymethods]
impl PySchedulerCore {
    #[new]
    fn new(spec: &Bound<'_, PyDict>) -> PyResult<Self> {
        let spec = Spec::from_dict(spec)?;
        if spec.handle_sweep_interval == 0 {
            return Err(PyValueError::new_err("HANDLE_SWEEP_INTERVAL must be >= 1"));
        }
        Ok(Self {
            core: Arc::new(Core {
                spec,
                state: Mutex::new(State::new()),
                external: Arc::new(ExternalQueue::new()),
                owner_thread: AtomicU64::new(0),
                type_cache: Mutex::new(HashMap::default()),
                registrar: Mutex::new(None),
                cancel_binder: Mutex::new(None),
                queue_obj: Mutex::new(None),
            }),
        })
    }

    /// The root prompt (the scheduler boundary of the root body).
    fn prompt(&self) -> PySchedulerPrompt {
        PySchedulerPrompt {
            callable: self.core.handler(None),
        }
    }

    /// `(abandoned ready entries, parked non-daemon waiters)` for the root
    /// close-out warning (#501).
    fn close_out_report(&self) -> PyResult<(Vec<String>, Vec<String>)> {
        let locked = self.core.lock()?;
        Ok((
            locked.abandoned_ready_summary(),
            locked.live_parked_waiter_summary(false),
        ))
    }
}

/// `Promise._register` / `ExternalPromise._register` of a Rust scheduler run.
#[pyclass(name = "SchedulerHandleRegistrar", module = "doeff_vm.doeff_vm", frozen)]
pub struct HandleRegistrar {
    core: Weak<Core>,
}

fn parse_wkey(key: &Bound<'_, PyAny>) -> PyResult<WKey> {
    let (kind, id): (String, u64) = key.extract()?;
    let kind = match kind.as_str() {
        "task" => WKind::Task,
        "promise" => WKind::Promise,
        other => {
            return Err(PyValueError::new_err(format!("unknown waitable kind {other:?}")));
        }
    };
    Ok(WKey { kind, id })
}

#[pymethods]
impl HandleRegistrar {
    fn __call__(&self, py: Python<'_>, key: &Bound<'_, PyAny>, handle: Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
        let Some(core) = self.core.upgrade() else {
            return Ok(handle.unbind());
        };
        let key = parse_wkey(key)?;
        let mut locked = core.lock()?;
        locked.register_handle(py, key, &handle)?;
        let graveyard = locked.take_graveyard();
        drop(locked);
        drop(graveyard);
        Ok(handle.unbind())
    }
}

/// `ExternalPromise._bind_cancel` of a Rust scheduler run.
#[pyclass(name = "SchedulerCancelBinder", module = "doeff_vm.doeff_vm", frozen)]
pub struct CancelBinder {
    core: Weak<Core>,
}

#[pymethods]
impl CancelBinder {
    fn __call__(&self, py: Python<'_>, pid: u64, callback: Py<PyAny>) -> PyResult<()> {
        let Some(core) = self.core.upgrade() else {
            return Err(PyRuntimeError::new_err("the scheduler run has ended"));
        };
        let run_now = {
            let mut locked = core.lock()?;
            let promise = locked
                .promises
                .get_mut(&pid)
                .ok_or_else(|| key_error(pid.to_string()))?;
            match promise.status {
                Status::Cancelled => true,
                Status::Pending => {
                    promise
                        .cancel_callbacks
                        .get_or_insert_with(Vec::new)
                        .push(callback.clone_ref(py));
                    false
                }
                _ => false,
            }
        };
        if run_now {
            callback.bind(py).call0()?;
        }
        Ok(())
    }
}

pub fn register(m: &Bound<'_, pyo3::types::PyModule>) -> PyResult<()> {
    m.add_class::<PySchedulerCore>()?;
    m.add_class::<PySchedulerPrompt>()?;
    m.add_class::<PyExternalQueue>()?;
    m.add_class::<HandleRegistrar>()?;
    m.add_class::<CancelBinder>()?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{heapq, HeapItem, ReadyEntry};

    fn item(priority: i64, seq: u64) -> HeapItem {
        HeapItem {
            neg_priority: -priority,
            seq,
            entry: ReadyEntry::New { tid: seq },
        }
    }

    fn layout(heap: &[HeapItem]) -> Vec<u64> {
        heap.iter().map(|i| i.seq).collect()
    }

    /// The heap array matches CPython's `heapq` step for step (expected values
    /// produced by `heapq.heappush` / `list.pop(i)` + `heapq.heapify` /
    /// `heapq.heappop` on the same `(-priority, seq)` tuples), so diagnostics
    /// that list ready entries in array order read the same in both versions.
    #[test]
    fn heap_layout_matches_cpython_heapq() {
        let priorities = [10, 5, 20, 10, 0, 5, 20, 10, 5, 0, 20, 10];
        let mut heap = Vec::new();
        for (seq, priority) in priorities.iter().enumerate() {
            heapq::push(&mut heap, item(*priority, seq as u64));
        }
        assert_eq!(layout(&heap), vec![2, 10, 6, 7, 3, 11, 0, 1, 8, 9, 4, 5]);
        heap.remove(3);
        heapq::heapify(&mut heap);
        assert_eq!(layout(&heap), vec![2, 10, 6, 3, 11, 0, 1, 8, 9, 4, 5]);
        let mut popped = Vec::new();
        while let Some(i) = heapq::pop(&mut heap) {
            popped.push(i.seq);
        }
        assert_eq!(popped, vec![2, 6, 10, 0, 3, 11, 1, 5, 8, 4, 9]);
    }
}
