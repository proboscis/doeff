"""
Cooperative scheduler — OCaml 5 recursive match_with pattern.

Each task gets its own handler node. Spawn creates a new handler node
recursively. All handler instances share state via closure.

No envelope needed. Task completion is caught by a @do wrapper that
performs TaskCompleted.

Usage:
    from doeff import do, run
    from doeff.scheduler import scheduled, Spawn, Gather, Wait

    @do
    def main():
        t1 = yield Spawn(task1())
        t2 = yield Spawn(task2())
        results = yield Gather(t1, t2)
        return results

    run(scheduled(main()))
"""

import logging
import warnings
import weakref

from doeff_vm import Callable as _VmCallable
from doeff_vm import EffectBase, Err, Ok, TailEval
from doeff_vm import WithObserve as _WithObserveRaw

from doeff.do import do
from doeff.handler_utils import get_inner_boundaries
from doeff.program import Pass, Perform, Pure, Resume, Transfer
from doeff.program import handler as _program_handler

_logger = logging.getLogger(__name__)

# How long a blocking wait for external completions may stay silent before a
# stall diagnostic is logged (#495b). Blocking semantics are unchanged: the
# scheduler logs the parked-waiter summary and keeps waiting.
EXTERNAL_STALL_LOG_INTERVAL_SECONDS = 30.0

# How many fresh_id allocations pass between two sweeps of terminal,
# unobserved task/promise entries (#502). The sweep runs synchronously inside
# the scheduler's own dispatch path (fresh_id is only called from effect
# handlers), never from a GC callback, so it can safely mutate the state dicts.
HANDLE_SWEEP_INTERVAL = 1024


def _reinstall_boundary(prog, kind, boundary_callable):
    """Re-wrap prog with one boundary captured at the spawn site."""
    if kind == "handler":
        return _program_handler(boundary_callable)(prog)
    if kind == "observer":
        return _WithObserveRaw(_VmCallable(boundary_callable), prog)
    raise RuntimeError(f"unknown boundary kind: {kind!r}")


def _enrich_exception_traceback(exc, task_meta=None, vm_ctx=None):
    """Build doeff traceback from VM execution context + task metadata.

    vm_ctx: from GetExecutionContext — fiber chain at error site (before unwinding).
            Contains ["frame", ...] and ["handler", ...] entries from the live fiber chain.
    task_meta: scheduler task metadata with inner_boundaries.
    """
    entries = []

    if vm_ctx:
        # Use VM fiber chain context (captured at error site)
        entries.extend(vm_ctx)
    else:
        # Fallback: extract from Python __traceback__
        import traceback as tb_mod
        tb = exc.__traceback__
        if tb is not None:
            for fs in tb_mod.extract_tb(tb):
                fn = fs.filename
                if any(p in fn for p in ("/doeff_vm/", "/doeff/do.py", "/doeff/run.py",
                                          "/doeff_core_effects/")):
                    continue
                entries.append(["frame", fs.name, fs.filename, fs.lineno])

    if entries:
        existing = getattr(exc, "__doeff_traceback__", None) or []
        exc.__doeff_traceback__ = entries + existing


# ---------------------------------------------------------------------------
# Effects
# ---------------------------------------------------------------------------

PRIORITY_IDLE = 0
PRIORITY_EXTERNAL_WAIT = 5   # above IDLE (blocks clock driver), below NORMAL (yields to real work)
PRIORITY_NORMAL = 10
PRIORITY_HIGH = 20


class Spawn(EffectBase):
    def __init__(self, program, priority=PRIORITY_NORMAL):
        super().__init__()
        self.program = program
        self.priority = priority


class TaskCompleted(EffectBase):
    def __init__(self, task_id, result):
        super().__init__()
        self.task_id = task_id
        self.result = result


class Gather(EffectBase):
    def __init__(self, *tasks):
        super().__init__()
        self.tasks = tasks


class Wait(EffectBase):
    """Wait for a Task or Future to resolve.

    ``priority`` controls the priority at which the waiter is re-enqueued
    when the waitable resolves (#504). ``None`` (default) means the waiter
    wakes at its own task's spawn priority. For an *external* promise,
    ``PRIORITY_IDLE`` is a park mode, not a wake priority: the wait is
    parked without a ready-heap placeholder (so the sim clock driver may
    run) and the waiter still wakes at its own task priority — an IDLE
    wake would starve it behind the PRIORITY_EXTERNAL_WAIT shield.
    """

    def __init__(self, task, priority=None):
        super().__init__()
        self.task = task
        self.priority = priority


class Cancel(EffectBase):
    """Cancel a task cooperatively."""
    def __init__(self, task):
        super().__init__()
        self.task = task


class TaskCancelledError(Exception):
    """Raised when waiting on a cancelled task."""


class SchedulerDeadlockError(RuntimeError):
    """Raised when the scheduler has parked work that cannot make progress."""

    def __init__(self, semaphore_waiters, parked_waiters=None):
        self.semaphore_waiters = {
            sem_id: list(task_ids)
            for sem_id, task_ids in semaphore_waiters.items()
        }
        self.parked_waiters = list(parked_waiters or [])
        details = []
        for sem_id, task_ids in self.semaphore_waiters.items():
            if task_ids:
                task_list = ", ".join(str(task_id) for task_id in task_ids)
                details.append(f"semaphore {sem_id}: tasks {task_list}")
            else:
                details.append(f"semaphore {sem_id}: root continuation")
        parts = []
        if details:
            parts.append(
                "semaphore waiters remain with no runnable tasks "
                f"({'; '.join(details)})"
            )
        if self.parked_waiters:
            parts.append(
                "parked waiters that no external completion can wake: "
                f"{'; '.join(self.parked_waiters)}"
            )
        super().__init__("scheduler deadlock: " + "; ".join(parts))


class Race(EffectBase):
    def __init__(self, *tasks):
        super().__init__()
        self.tasks = tasks


class CreatePromise(EffectBase):
    def __init__(self):
        super().__init__()


class CompletePromise(EffectBase):
    def __init__(self, promise, value):
        super().__init__()
        self.promise = promise
        self.value = value


class FailPromise(EffectBase):
    def __init__(self, promise, error):
        super().__init__()
        self.promise = promise
        self.error = error


class CreateSemaphore(EffectBase):
    def __init__(self, permits=1):
        super().__init__()
        self.permits = permits


class AcquireSemaphore(EffectBase):
    def __init__(self, semaphore):
        super().__init__()
        self.semaphore = semaphore


class ReleaseSemaphore(EffectBase):
    def __init__(self, semaphore):
        super().__init__()
        self.semaphore = semaphore


class CreateExternalPromise(EffectBase):
    def __init__(self):
        super().__init__()


class _SchedulerIntrospection(EffectBase):
    """Test-only hook: resume with the sizes of the scheduler state dicts.

    Not public API — exists so the #502 sweep can be asserted on without
    exposing the closure state.
    """

    def __init__(self):
        super().__init__()


# ---------------------------------------------------------------------------
# Handles
# ---------------------------------------------------------------------------

class Task:
    def __init__(self, task_id):
        self.task_id = task_id
    def __repr__(self):
        return f"Task({self.task_id})"


class Future:
    """Read-side handle for a promise."""
    def __init__(self, promise_id):
        self.promise_id = promise_id
    def __repr__(self):
        return f"Future({self.promise_id})"


class Promise:
    """Write-side handle for an internal promise.

    ``_register`` is the owning run's handle registrar (#502): every derived
    ``Future`` must be registered too, so a promise entry is only swept once
    the write handle AND every read handle created from it are dead.
    """
    def __init__(self, promise_id, _register=None):
        self.promise_id = promise_id
        self._register = _register

    @property
    def future(self):
        future = Future(self.promise_id)
        if self._register is not None:
            self._register(("promise", self.promise_id), future)
        return future

    def __repr__(self):
        return f"Promise({self.promise_id})"


class Semaphore:
    """Opaque semaphore handle."""
    def __init__(self, sem_id):
        self.sem_id = sem_id
    def __repr__(self):
        return f"Semaphore({self.sem_id})"


class ExternalPromise:
    """Write-side handle for an external promise. Thread-safe complete/fail."""
    def __init__(self, promise_id, queue, _register=None):
        self.promise_id = promise_id
        self._queue = queue
        self._register = _register

    @property
    def future(self):
        future = Future(self.promise_id)
        if self._register is not None:
            self._register(("promise", self.promise_id), future)
        return future

    def complete(self, value):
        """Complete the promise with a value. Thread-safe, wakes scheduler via Queue."""
        self._queue.put(("complete", self.promise_id, value))

    def fail(self, error):
        """Fail the promise with an error. Thread-safe, wakes scheduler via Queue."""
        self._queue.put(("fail", self.promise_id, error))

    def __repr__(self):
        return f"ExternalPromise({self.promise_id})"


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

def scheduled(body_program):  # noqa: PLR0915 - baseline cleanup keeps existing control flow unchanged
    """Wrap a program with the scheduler. Returns a DoExpr."""
    import heapq
    import queue as queue_mod

    # --- State ---
    next_id = [0]
    insertion_seq = [0]  # tie-breaker for priority queue (FIFO within same priority)
    tasks = {}           # tid → {status, result, program, priority}
    promises = {}        # pid → {status, result}
    semaphores = {}      # sid → {permits, max_permits, waiters: deque of (owner_tid, k)}
    waiters = {}         # waitable_key → [(type, owner_tid, k/state, ...)]
    ready = []           # heapq: (-priority, seq, entry)
    external_queue = queue_mod.Queue()  # thread-safe, blocking get()
    handle_refs = {}     # waitable_key → [weakref.ref(Task/Promise/Future/…)]

    def register_handle(key, handle):
        """Track handle liveness for the terminal-entry sweep (#502).

        Weakrefs carry no callbacks — liveness is polled by the sweep inside
        the scheduler's own dispatch path, never from a GC callback, so no
        reentrant dict mutation can occur.
        """
        handle_refs.setdefault(key, []).append(weakref.ref(handle))
        return handle

    def sweep_terminal_unobserved_entries():
        """Delete task/promise entries nothing can ever observe again (#502).

        An entry is swept when it is terminal, has no registered waiter, is
        not referenced by a live Gather/Race resolution, and every handle
        (Task, Promise/ExternalPromise, and each Future minted from them) is
        dead. Cancelled tasks are exempt: a self-cancelled task still runs to
        its TaskCompleted and a cancelled parked waiter is still woken through
        task_priority — both re-read tasks[tid]. Semaphores are never swept:
        "no permits outstanding" is not trackable from handle liveness.
        """
        protected = set()
        for entries in waiters.values():
            for entry in entries:
                if entry[0] == "gather":
                    # Gather re-reads waitable_status for EVERY key (including
                    # already-terminal ones) at final resolution.
                    protected.update(entry[2]["keys"])
                elif entry[0] == "race":
                    protected.update(entry[2]["pending_keys"])
        for kind, store in (("task", tasks), ("promise", promises)):
            dead = []
            for wid, meta in store.items():
                if meta["status"] not in ("completed", "failed"):
                    continue
                key = (kind, wid)
                if key in waiters or key in protected:
                    continue
                refs = handle_refs.get(key)
                if refs is None:
                    continue
                # No in-place pruning: a live parent handle may mint (and
                # register) new Futures from a foreign thread; the list is
                # only dropped once every handle is dead, at which point no
                # new registration for this key can happen.
                if any(ref() is not None for ref in refs):
                    continue
                dead.append(wid)
            for wid in dead:
                del store[wid]
                del handle_refs[(kind, wid)]

    def fresh_id():
        i = next_id[0]
        next_id[0] += 1
        if next_id[0] % HANDLE_SWEEP_INTERVAL == 0:
            sweep_terminal_unobserved_entries()
        return i

    def waitable_key(obj):
        """Convert Task or Future to a dict key."""
        if isinstance(obj, Task):
            return ("task", obj.task_id)
        if isinstance(obj, Future):
            return ("promise", obj.promise_id)
        raise TypeError(f"expected Task or Future, got {type(obj).__name__}")

    def waitable_status(key):
        kind, wid = key
        if kind == "task":
            store = tasks
        elif kind == "promise":
            store = promises
        else:
            return "unknown", None
        entry = store.get(wid)
        if entry is None:
            raise KeyError(
                f"{kind} {wid} is unknown to this scheduler run: it was swept "
                "after reaching a terminal state with no live Task/Future "
                "handle and no registered waiter (#502). Keep a handle alive "
                "to Wait/Gather/Race on it later."
            )
        return entry["status"], entry.get("result")

    def enqueue(entry, priority=PRIORITY_NORMAL):
        """Add entry to priority queue. Higher priority = served first."""
        seq = insertion_seq[0]
        insertion_seq[0] += 1
        heapq.heappush(ready, (-priority, seq, entry))

    def dequeue():
        """Pop highest-priority entry. Returns None if empty."""
        if ready:
            _, _, entry = heapq.heappop(ready)
            return entry
        return None

    def is_owner_cancelled(owner_tid):
        return (
            owner_tid is not None
            and tasks.get(owner_tid, {}).get("status") == "cancelled"
        )

    def task_priority(owner_tid):
        """Stored spawn priority of the owner task; the root (None) is NORMAL.

        Task priority must survive suspension (#493/#504): every wake path
        that does not have a more specific priority re-enqueues the owner
        at the priority it was spawned with.
        """
        if owner_tid is None:
            return PRIORITY_NORMAL
        return tasks[owner_tid]["priority"]

    def enqueue_resume(owner_tid, cont, value, priority=None):
        if priority is None:
            priority = task_priority(owner_tid)
        enqueue(("resume", owner_tid, cont, value), priority)

    def enqueue_raise(owner_tid, cont, error, priority=None):
        if priority is None:
            priority = task_priority(owner_tid)
        enqueue(("raise", owner_tid, cont, error), priority)

    def alloc_task(program, priority=PRIORITY_NORMAL, inner_boundaries=None):
        tid = fresh_id()
        tasks[tid] = {
            "status": "pending", "result": None,
            "program": program, "priority": priority,
            "inner_boundaries": inner_boundaries or [],
        }
        return tid

    def alloc_promise():
        pid = fresh_id()
        promises[pid] = {"status": "pending", "result": None}
        return pid

    def wrap_task(tid, prog):
        @do
        def wrapped():
            try:
                result = yield prog
                yield Perform(TaskCompleted(tid, Ok(result)))
            except Exception as e:
                from doeff.program import GetExecutionContext as _GetExecCtx
                try:
                    # GetExecutionContext returns the error-site context
                    # (captured by VM before unwinding), not the except-site.
                    ctx = yield _GetExecCtx()
                    task_meta = tasks.get(tid, {})
                    _enrich_exception_traceback(e, task_meta, ctx)
                except Exception:
                    pass
                yield Perform(TaskCompleted(tid, Err(e)))
            except (KeyboardInterrupt, SystemExit) as e:
                # #507: an interrupt must neither vanish into ordinary task
                # bookkeeping nor bypass it. Record the failure and wake the
                # waiters directly — performing TaskCompleted here could not
                # re-raise afterwards because its handler never resumes the
                # completing task — then RE-RAISE so the interrupt still
                # unwinds out of the whole run. GeneratorExit and other
                # BaseExceptions are deliberately not intercepted: close() of
                # an abandoned task must stay a plain close, without waking
                # anything.
                if tasks[tid]["status"] not in terminal_statuses:
                    tasks[tid]["status"] = "failed"
                    tasks[tid]["result"] = e
                    wake_waiters(("task", tid))
                _release_task_refs(tid)
                raise
        return wrapped()

    def _drain_one_external():
        """Block for one external completion and process it.

        Blocks with a timeout so a stalled scheduler stays observable
        (#495b): every EXTERNAL_STALL_LOG_INTERVAL_SECONDS a warning with
        the parked-waiter summary is logged, then blocking continues —
        semantics are unchanged.
        """
        waited = 0.0
        while True:
            interval = EXTERNAL_STALL_LOG_INTERVAL_SECONDS
            try:
                action, pid, value = external_queue.get(timeout=interval)
                break
            except queue_mod.Empty:
                waited += interval
                _logger.warning(
                    "scheduler stalled %.0fs waiting on external completions; "
                    "parked waiters: %s; semaphore waiters: %s",
                    waited,
                    live_parked_waiter_summary() or "none",
                    live_semaphore_waiters() or "none",
                )
        if pid in promises and promises[pid]["status"] == "pending":
            promises[pid]["status"] = "completed" if action == "complete" else "failed"
            promises[pid]["result"] = value
            wake_waiters(("promise", pid))

    def live_parked_waiter_summary():
        """Describe live (non-cancelled) parked waiters for diagnostics."""
        parked = []
        for (kind, wid), entries in waiters.items():
            for entry in entries:
                wtype, owner_tid = entry[0], entry[1]
                if is_owner_cancelled(owner_tid):
                    continue
                who = "root" if owner_tid is None else f"task {owner_tid}"
                parked.append(f"{who} ({wtype}) on {kind} {wid}")
        return parked

    def abandoned_ready_summary():
        """Describe live ready-heap entries a root return would abandon (#501).

        "wait_external" placeholders are skipped: an unclaimed one is already
        reported through its paired waiters registration and a claimed one is
        inert by construction.
        """
        abandoned = []
        for _neg_prio, _seq, entry in ready:
            kind = entry[0]
            if kind == "new":
                tid = entry[1]
                if tasks[tid]["status"] != "cancelled":
                    abandoned.append(f"unstarted task {tid}")
            elif kind in ("resume", "raise"):
                owner_tid = entry[1]
                if not is_owner_cancelled(owner_tid):
                    who = "root" if owner_tid is None else f"task {owner_tid}"
                    abandoned.append(f"queued {kind} for {who}")
            elif kind == "sem_resume":
                owner_tid, sid = entry[1], entry[3]
                if not is_owner_cancelled(owner_tid):
                    abandoned.append(
                        f"queued semaphore {sid} permit for task {owner_tid}"
                    )
        return abandoned

    @do
    def root_close_out(prog):
        """Report work the run abandons when the root body returns (#501).

        Diagnostic only: return value and cancellation semantics are
        unchanged — the abandoned entries are still dropped, but loudly.
        """
        result = yield prog
        abandoned = abandoned_ready_summary()
        parked = live_parked_waiter_summary()
        if abandoned or parked:
            warnings.warn(
                "scheduler root body returned while abandoning in-flight "
                f"work (#501): ready entries [{'; '.join(abandoned)}]; "
                f"parked waiters [{'; '.join(parked)}]. Spawned work that "
                "must finish has to be awaited (Wait/Gather) before the "
                "root body returns.",
                RuntimeWarning,
                stacklevel=2,
            )
        return result

    def live_semaphore_waiters():
        """Return live semaphore waiters, pruning cancelled task continuations."""
        blocked = {}
        for sid, sem in semaphores.items():
            original_waiters = list(sem["waiters"])
            live_waiters = [
                waiter
                for waiter in original_waiters
                if not is_owner_cancelled(waiter[0])
            ]
            if len(live_waiters) != len(original_waiters):
                sem["waiters"].clear()
                sem["waiters"].extend(live_waiters)
            if live_waiters:
                blocked[sid] = [
                    owner_tid
                    for owner_tid, _waiter_k in live_waiters
                    if owner_tid is not None
                ]
        return blocked

    def unreleasable_semaphores(blocked):
        """Blocked semaphores that provably no permit holder can release.

        A holder is stuck when it is itself parked in the waiter queue of
        a semaphore still presumed deadlocked — computed as a greatest
        fixpoint so a holder awaiting an external event keeps its
        semaphore (and everything parked behind it) blocking quietly, and
        cross-semaphore cycles are still caught (#495c). Holder tracking
        is diagnostics-only metadata: a release by a task that never
        acquired (legal) leaves a stale holder behind, which can only
        make this check miss a deadlock, never fabricate one.
        """
        candidates = set(blocked)
        changed = True
        while candidates and changed:
            parked_tids = {
                owner_tid
                for sid in candidates
                for owner_tid, _waiter_k in semaphores[sid]["waiters"]
            }
            pruned = {
                sid
                for sid in candidates
                if not semaphores[sid]["holders"]
                or any(h not in parked_tids for h in semaphores[sid]["holders"])
            }
            candidates -= pruned
            changed = bool(pruned)
        return {sid: blocked[sid] for sid in candidates}

    def raise_if_semaphore_cycle_unresolvable(blocked_semaphore_waiters):
        """Fail loudly when a blocked semaphore cycle can never resolve.

        Called wherever pick_next is about to block on external
        completions: unrelated pending external waiters must not mask a
        semaphore deadlock into a silent hang (#495c).
        """
        if not blocked_semaphore_waiters:
            return
        doomed = unreleasable_semaphores(blocked_semaphore_waiters)
        if doomed:
            raise SchedulerDeadlockError(doomed)

    def has_pending_external_waiters():
        for key, entries in waiters.items():
            kind, wid = key
            if kind != "promise":
                continue
            promise = promises.get(wid, {})
            if not promise.get("external") or promise.get("status") in terminal_statuses:
                continue
            for entry in entries:
                if len(entry) >= 2 and not is_owner_cancelled(entry[1]):
                    return True
        return False

    def pick_next():  # noqa: PLR0912 - scheduler dispatch loop has one branch per ready entry
        from doeff.program import ResumeThrow
        while True:
            drain()
            while ready:
                entry = dequeue()
                if entry[0] == "new":
                    _, tid = entry
                    if tasks[tid]["status"] == "cancelled":
                        continue  # skip cancelled tasks
                    tasks[tid]["status"] = "running"
                    prog = tasks[tid].pop("program")
                    # Re-wrap task with the boundary stack (handlers AND
                    # observers) captured at the spawn site, innermost first —
                    # preserves handler/observer nesting order.
                    for kind, boundary_callable in tasks[tid].pop("inner_boundaries", []):
                        prog = _reinstall_boundary(prog, kind, boundary_callable)
                    return make_handler(tid)(wrap_task(tid, prog))
                if entry[0] == "resume":
                    _, owner_tid, cont, value = entry
                    if is_owner_cancelled(owner_tid):
                        continue
                    return Transfer(cont, value)
                if entry[0] == "sem_resume":
                    # A ReleaseSemaphore permit travelling to a parked
                    # waiter (#496): the entry carries the semaphore id so
                    # the permit is recoverable at this drop site.
                    _, owner_tid, cont, sid = entry
                    if is_owner_cancelled(owner_tid):
                        return_inflight_permit(sid, owner_tid)
                        continue
                    return Transfer(cont, None)
                if entry[0] == "raise":
                    _, owner_tid, cont, error = entry
                    if is_owner_cancelled(owner_tid):
                        continue
                    return ResumeThrow(cont, error)
                if entry[0] == "wait_external":
                    # Placeholder for a task waiting on an external promise.
                    # Keeps IDLE tasks (clock driver) from running while the
                    # wait is pending, and drives the blocking drain. The
                    # resume itself is owned by the paired `waiters`
                    # registration: wake_waiters claims and enqueues it the
                    # moment the completion is drained — even while this loop
                    # is blocked on a DIFFERENT unresolved external wait
                    # (#490). This entry never resumes the continuation; once
                    # claimed it is simply dropped.
                    _, owner_tid, _cont, _wk, claimed = entry
                    if claimed[0] or is_owner_cancelled(owner_tid):
                        continue
                    # Deadlock diagnostics must stay reachable while this
                    # branch keeps the loop from ever reaching the bottom
                    # of pick_next (#495c/d): report an unresolvable
                    # semaphore cycle loudly before blocking.
                    raise_if_semaphore_cycle_unresolvable(live_semaphore_waiters())
                    # Not yet resolved — block for one completion, drain rest
                    _drain_one_external()
                    drain()
                    if not claimed[0]:
                        enqueue(entry, PRIORITY_EXTERNAL_WAIT)
                    continue
            blocked_semaphore_waiters = live_semaphore_waiters()
            if not has_pending_external_waiters():
                parked = live_parked_waiter_summary()
                if blocked_semaphore_waiters or parked:
                    # Nothing is runnable and no pending external
                    # completion can ever wake any parked waiter — the run
                    # would hang silently in external_queue.get() forever.
                    # Fail loudly instead (#495a).
                    raise SchedulerDeadlockError(blocked_semaphore_waiters, parked)
            else:
                # External waiters exist, but they must not mask a
                # semaphore cycle that no holder can ever release (#495c).
                raise_if_semaphore_cycle_unresolvable(blocked_semaphore_waiters)
            if not waiters:
                return Pure(None)
            # All tasks blocked — block for one external completion
            _drain_one_external()

    terminal_statuses = ("completed", "failed", "cancelled")

    def _release_task_refs(tid):
        """Drop heavy references from a terminal task.

        Keeps status/result (needed by Wait/Gather) but releases the
        program, inner_boundaries, and spawn_site closures that pin large
        Python object graphs into memory.
        """
        t = tasks.get(tid)
        if t is None:
            return
        t.pop("program", None)
        t.pop("inner_boundaries", None)
        t.pop("spawn_site", None)

    def resume_with_waitable_result(owner_tid, waiter_k, key, priority=None):
        """Add a ready entry that resumes waiter with the waitable's result.
        For failed/cancelled, uses ("raise", k, error) so handler can throw.
        priority=None wakes the waiter at its own task priority (#504)."""
        status, result = waitable_status(key)
        if status == "completed":
            enqueue_resume(owner_tid, waiter_k, result, priority)
        elif status == "failed":
            enqueue_raise(owner_tid, waiter_k, result, priority)
        elif status == "cancelled":
            enqueue_raise(owner_tid, waiter_k, TaskCancelledError(), priority)

    def register_pending_waiter(wk, entry_type, owner_tid, state):
        """Register a gather/race waiter for a pending waitable (#505).

        A pending EXTERNAL promise additionally gets a claimed-style
        placeholder at PRIORITY_EXTERNAL_WAIT — the same shape Wait uses
        (#490/#491): the placeholder only blocks the IDLE clock driver and
        drives the blocking drain; it never resumes anything. wake_waiters
        (or remove_gather_waiters/remove_race_waiters on early resolution)
        sets ``claimed`` and the placeholder drops itself, so Gather/Race
        get the same sim-ordering semantics as Wait.
        """
        claimed = None
        kind, wid = wk
        if kind == "promise" and promises[wid].get("external"):
            claimed = [False]
            enqueue(
                ("wait_external", owner_tid, None, wk, claimed),
                PRIORITY_EXTERNAL_WAIT,
            )
        waiters.setdefault(wk, []).append((entry_type, owner_tid, state, claimed))

    def remove_gather_waiters(gather_state):
        """Remove unresolved waiter refs for a fail-fast Gather resolution."""
        for wk in set(gather_state["pending_keys"]):
            entries = waiters.get(wk)
            if not entries:
                continue
            remaining_entries = []
            for entry in entries:
                if entry[0] == "gather" and entry[2] is gather_state:
                    if entry[3] is not None:
                        # Drop the paired external-wait placeholder (#505) so
                        # it cannot block the run on a completion nobody
                        # observes anymore.
                        entry[3][0] = True
                    continue
                remaining_entries.append(entry)
            if remaining_entries:
                waiters[wk] = remaining_entries
            else:
                waiters.pop(wk, None)

    def resolve_gather_with_error(gather_state, error):
        if gather_state["resolved"]:
            return
        gather_state["resolved"] = True
        gather_state["failure"] = error
        remove_gather_waiters(gather_state)
        enqueue_raise(gather_state["owner_tid"], gather_state["waiter_k"], error)

    def wake_gather_waiter(gather_state, completed_key):
        if gather_state["resolved"]:
            return

        status, result = waitable_status(completed_key)
        if status == "failed":
            resolve_gather_with_error(gather_state, result)
            return
        if status == "cancelled":
            resolve_gather_with_error(gather_state, TaskCancelledError())
            return

        if status != "completed":
            return

        gather_state["remaining"] -= 1
        if gather_state["remaining"] == 0:
            gather_state["resolved"] = True
            results = [waitable_status(wk)[1] for wk in gather_state["keys"]]
            enqueue_resume(gather_state["owner_tid"], gather_state["waiter_k"], results)

    def remove_race_waiters(race_state):
        """Remove unresolved sibling waiter refs after Race has a winner."""
        for wk in set(race_state["pending_keys"]):
            entries = waiters.get(wk)
            if not entries:
                continue
            remaining_entries = []
            for entry in entries:
                if entry[0] == "race" and entry[2] is race_state:
                    if entry[3] is not None:
                        # Drop the paired external-wait placeholder (#505):
                        # the losing external promise may never complete and
                        # must not keep blocking the drain loop.
                        entry[3][0] = True
                    continue
                remaining_entries.append(entry)
            if remaining_entries:
                waiters[wk] = remaining_entries
            else:
                waiters.pop(wk, None)

    def wake_race_waiter(race_state, completed_key):
        if race_state["resolved"]:
            return

        status, result = waitable_status(completed_key)
        if status not in terminal_statuses:
            return

        race_state["resolved"] = True
        remove_race_waiters(race_state)
        if status == "completed":
            enqueue_resume(race_state["owner_tid"], race_state["waiter_k"], result)
        elif status == "failed":
            enqueue_raise(race_state["owner_tid"], race_state["waiter_k"], result)
        elif status == "cancelled":
            enqueue_raise(race_state["owner_tid"], race_state["waiter_k"], TaskCancelledError())

    def wake_waiters(completed_key):
        ws = waiters.pop(completed_key, [])
        for w in ws:
            if w[0] == "wait":
                _, owner_tid, waiter_k, wake_priority = w
                resume_with_waitable_result(
                    owner_tid, waiter_k, completed_key, wake_priority
                )
            elif w[0] == "wait_external":
                # Eager wake for a non-IDLE external wait (#490): enqueue the
                # resume now and claim it so the paired ready-heap placeholder
                # drops itself instead of resuming a second time.
                _, owner_tid, waiter_k, claimed, wake_priority = w
                claimed[0] = True
                resume_with_waitable_result(
                    owner_tid, waiter_k, completed_key, wake_priority
                )
            elif w[0] == "gather":
                _, _owner_tid, gather_state, claimed = w
                if claimed is not None:
                    # External-promise key (#505): drop the paired
                    # ready-heap placeholder now that the completion is in.
                    claimed[0] = True
                wake_gather_waiter(gather_state, completed_key)
            elif w[0] == "race":
                _, _owner_tid, race_state, claimed = w
                if claimed is not None:
                    claimed[0] = True
                wake_race_waiter(race_state, completed_key)

    def drain():
        """Drain all pending external completions into promise state."""
        while not external_queue.empty():
            action, pid, value = external_queue.get()
            if pid in promises and promises[pid]["status"] == "pending":
                promises[pid]["status"] = "completed" if action == "complete" else "failed"
                promises[pid]["result"] = value
                wake_waiters(("promise", pid))

    def pop_live_semaphore_waiter(sem):
        while sem["waiters"]:
            owner_tid, waiter_k = sem["waiters"].popleft()
            if is_owner_cancelled(owner_tid):
                continue
            return owner_tid, waiter_k
        return None

    def grant_permit_to_next_waiter(sid):
        """Transfer one permit to the next live waiter of semaphore ``sid``.

        The ready entry is tagged ``sem_resume`` and carries ``sid`` so the
        permit it holds can be returned if the receiving task is cancelled
        before the resume is dequeued (#496). Returns True when the permit
        was transferred, False when no live waiter exists.
        """
        sem = semaphores[sid]
        waiter = pop_live_semaphore_waiter(sem)
        if waiter is None:
            return False
        owner_tid, waiter_k = waiter
        sem["holders"].append(owner_tid)
        enqueue(("sem_resume", owner_tid, waiter_k, sid), task_priority(owner_tid))
        return True

    def return_inflight_permit(sid, cancelled_tid):
        """Return a permit whose receiving waiter was cancelled in flight.

        The task was cancelled after ReleaseSemaphore transferred the
        permit to it but before its sem_resume entry was dequeued (#496):
        re-run the release so the next live waiter — or the free pool —
        gets the permit instead of it leaking.
        """
        sem = semaphores[sid]
        sem["holders"].remove(cancelled_tid)
        if not grant_permit_to_next_waiter(sid):
            sem["permits"] += 1

    def make_handler(current_tid):
        @do
        def raw_handler(effect, k):
            return (yield TailEval(handle_scheduler_effect(current_tid, effect, k)))
        # The scheduler prompt is execution substrate, not a user handler:
        # exactly one lives in a VM. Stack-capture consumers (e.g.
        # doeff-agents tool tasks) must not reinstall it — a second
        # scheduler prompt inside a spawned task re-routes the exceptions
        # of Transfer-resumed continuations into that task's dynamic scope
        # (2026-07-07 exit-0 incident; see doeff_agents mcp_server_loop).
        raw_handler.__doeff_scheduler_prompt__ = True
        return _program_handler(raw_handler)

    @do
    def handle_scheduler_effect(current_tid, effect, k):  # noqa: PLR0911, PLR0912, PLR0915 - baseline cleanup keeps existing control flow unchanged
        drain()
        if isinstance(effect, Spawn):
            # Capture the boundary stack (handlers AND WithObserve observers)
            # from the continuation (between yield site and scheduler) so the
            # spawned task keeps both — with nesting order preserved.
            inner_boundaries = yield get_inner_boundaries(k)

            # Capture spawn site from continuation's traceback
            from doeff.program import GetTraceback
            spawn_frames = yield GetTraceback(k)
            spawn_site = None
            if spawn_frames:
                f = spawn_frames[0]  # innermost = yield Spawn(...) site
                if isinstance(f, (list, tuple)) and len(f) >= 3:
                    spawn_site = f"{f[0]}  {f[1]}:{f[2]}"

            tid = alloc_task(effect.program, effect.priority, inner_boundaries=inner_boundaries)
            tasks[tid]["spawn_site"] = spawn_site
            enqueue(("new", tid), effect.priority)
            # Spawner resumes at its OWN task priority (#504): a hard-coded
            # NORMAL here would promote an IDLE spawner above the
            # PRIORITY_EXTERNAL_WAIT shield and demote a HIGH spawner.
            enqueue_resume(current_tid, k, register_handle(("task", tid), Task(tid)))
            yield TailEval(pick_next())

        elif isinstance(effect, TaskCompleted):
            tid = effect.task_id
            r = effect.result
            if tasks[tid]["status"] == "cancelled":
                _release_task_refs(tid)
            else:
                if hasattr(r, "is_ok") and r.is_ok():
                    tasks[tid]["status"] = "completed"
                    tasks[tid]["result"] = r.value
                else:
                    tasks[tid]["status"] = "failed"
                    error = r.error if hasattr(r, "error") else r
                    # Add spawn boundary to traceback
                    if isinstance(error, BaseException) and hasattr(error, "__doeff_traceback__"):
                        error.__doeff_traceback__.insert(0, {
                            "kind": "spawn_boundary",
                            "task_id": tid,
                            "spawn_site": tasks[tid].get("spawn_site", ""),
                        })
                    tasks[tid]["result"] = error
                wake_waiters(("task", tid))
                _release_task_refs(tid)
            yield TailEval(pick_next())

        elif isinstance(effect, Wait):
            wk = waitable_key(effect.task)
            status, result = waitable_status(wk)
            if status == "completed":
                r = yield Resume(k, result)
                return r
            elif status == "failed":
                from doeff.program import ResumeThrow
                return (yield ResumeThrow(k, result))
            elif status == "cancelled":
                from doeff.program import ResumeThrow
                return (yield ResumeThrow(k, TaskCancelledError()))
            else:
                kind, wid = wk
                if kind == "promise" and promises.get(wid, {}).get("external"):
                    # External promise: by default stay in ready queue above
                    # IDLE (blocks clock driver). Callers that are just
                    # listening in the background (MCP server loop, result
                    # file polling, etc.) pass priority=PRIORITY_IDLE, which
                    # parks the task in `waiters` just like an internal
                    # promise — drain() wakes it when the promise resolves,
                    # and the clock driver is free to advance sim time.
                    if effect.priority == PRIORITY_IDLE:
                        # PRIORITY_IDLE is the park mode, not the wake
                        # priority: the waiter wakes at its own task priority
                        # (wake_priority=None). Waking at IDLE would starve
                        # it behind the PRIORITY_EXTERNAL_WAIT shield.
                        waiters.setdefault(wk, []).append(
                            ("wait", current_tid, k, None)
                        )
                    else:
                        # Register in `waiters` too so the resume is enqueued
                        # by wake_waiters the moment the completion is drained
                        # — even while pick_next is blocked on a different
                        # unresolved external wait (#490). The ready-heap
                        # placeholder only blocks the clock driver and drives
                        # the blocking drain; `claimed` marks that the waiters
                        # side owns the (one-shot) resume.
                        claimed = [False]
                        waiters.setdefault(wk, []).append(
                            ("wait_external", current_tid, k, claimed, effect.priority)
                        )
                        enqueue(
                            ("wait_external", current_tid, k, wk, claimed),
                            PRIORITY_EXTERNAL_WAIT,
                        )
                else:
                    # Internal waitable: park in `waiters` (resolved by other
                    # tasks). An explicit Wait priority is honored as the
                    # wake priority (#504); None wakes at the waiter's own
                    # task priority.
                    waiters.setdefault(wk, []).append(
                        ("wait", current_tid, k, effect.priority)
                    )
                yield TailEval(pick_next())

        elif isinstance(effect, Gather):
            wks = [waitable_key(t) for t in effect.tasks]
            pending_wks = []
            for wk in wks:
                s, r = waitable_status(wk)
                if s == "failed":
                    from doeff.program import ResumeThrow
                    return (yield ResumeThrow(k, r))
                if s == "cancelled":
                    from doeff.program import ResumeThrow
                    return (yield ResumeThrow(k, TaskCancelledError()))
                if s not in terminal_statuses:
                    pending_wks.append(wk)

            if not pending_wks:
                results = [waitable_status(wk)[1] for wk in wks]
                r = yield Resume(k, results)
                return r

            gather_state = {
                "owner_tid": current_tid,
                "waiter_k": k,
                "keys": wks,
                "pending_keys": pending_wks,
                "remaining": len(pending_wks),
                "failure": None,
                "resolved": False,
            }
            for wk in pending_wks:
                register_pending_waiter(wk, "gather", current_tid, gather_state)
            yield TailEval(pick_next())

        elif isinstance(effect, Race):
            if not effect.tasks:
                # An empty Race has no identity (unlike Gather's []): the
                # caller continuation would otherwise be silently leaked and
                # the run would "succeed" with None (#501).
                from doeff.program import ResumeThrow
                return (yield ResumeThrow(k, ValueError(
                    "Race() requires at least one Task or Future to race"
                )))
            for t in effect.tasks:
                wk = waitable_key(t)
                status, result = waitable_status(wk)
                if status == "completed":
                    r = yield Resume(k, result)
                    return r
                elif status in ("failed", "cancelled"):
                    from doeff.program import ResumeThrow
                    err = result if status == "failed" else TaskCancelledError()
                    return (yield ResumeThrow(k, err))
            # All pending
            pending_wks = []
            for t in effect.tasks:
                wk = waitable_key(t)
                if waitable_status(wk)[0] not in terminal_statuses:
                    pending_wks.append(wk)
            if pending_wks:
                race_state = {
                    "owner_tid": current_tid,
                    "waiter_k": k,
                    "pending_keys": pending_wks,
                    "resolved": False,
                }
                for wk in pending_wks:
                    register_pending_waiter(wk, "race", current_tid, race_state)
            yield TailEval(pick_next())

        elif isinstance(effect, Cancel):
            tid = effect.task.task_id
            task = tasks.get(tid)
            if task and task["status"] in ("pending", "running", "suspended"):
                task["status"] = "cancelled"
                task["result"] = TaskCancelledError()
                wake_waiters(("task", tid))
                _release_task_refs(tid)
            r = yield Resume(k, None)
            return r

        elif isinstance(effect, CreatePromise):
            pid = alloc_promise()
            promise_handle = register_handle(
                ("promise", pid), Promise(pid, _register=register_handle)
            )
            r = yield Resume(k, promise_handle)
            return r

        elif isinstance(effect, CompletePromise):
            pid = effect.promise.promise_id
            promise = promises[pid]
            if promise.get("external"):
                # #507: external promises are resolved through
                # ExternalPromise.complete()/fail(); an internal resolution
                # would silently discard the foreign thread's completion.
                from doeff.program import ResumeThrow
                return (yield ResumeThrow(k, RuntimeError(
                    f"CompletePromise on external promise {pid}: resolve it "
                    "through ExternalPromise.complete() instead"
                )))
            if promise["status"] != "pending":
                # #507: same guard the drain path has — a double resolution
                # would silently rewrite the result after waiters were woken
                # with the old value.
                from doeff.program import ResumeThrow
                return (yield ResumeThrow(k, RuntimeError(
                    f"CompletePromise on promise {pid} which is already "
                    f"{promise['status']}"
                )))
            promise["status"] = "completed"
            promise["result"] = effect.value
            wake_waiters(("promise", pid))
            # Re-queue the completer at its OWN task priority (#493). A
            # hard-coded IDLE here froze every completer behind pending
            # non-IDLE external waits (whose ready-heap placeholders block
            # the loop in _drain_one_external). The sim clock driver still
            # yields to the tasks it wakes because it is Spawned at
            # PRIORITY_IDLE (doeff-time sim_time.py) — its stored task
            # priority, not a demotion at this site, holds it back. Waiters
            # woken above are enqueued first, so equal-priority completers
            # still run after the tasks they woke (FIFO within priority).
            enqueue_resume(current_tid, k, None)
            yield TailEval(pick_next())

        elif isinstance(effect, FailPromise):
            pid = effect.promise.promise_id
            promise = promises[pid]
            if promise.get("external"):
                # #507: see CompletePromise — external promises are resolved
                # through the thread-safe ExternalPromise handle only.
                from doeff.program import ResumeThrow
                return (yield ResumeThrow(k, RuntimeError(
                    f"FailPromise on external promise {pid}: resolve it "
                    "through ExternalPromise.fail() instead"
                )))
            if promise["status"] != "pending":
                from doeff.program import ResumeThrow
                return (yield ResumeThrow(k, RuntimeError(
                    f"FailPromise on promise {pid} which is already "
                    f"{promise['status']}"
                )))
            promise["status"] = "failed"
            promise["result"] = effect.error
            wake_waiters(("promise", pid))
            # Same as CompletePromise: the completer keeps its own task
            # priority (#493); the sim clock driver keeps IDLE via its
            # spawn priority.
            enqueue_resume(current_tid, k, None)
            yield TailEval(pick_next())

        elif isinstance(effect, CreateExternalPromise):
            pid = alloc_promise()
            promises[pid]["external"] = True
            ep = register_handle(
                ("promise", pid),
                ExternalPromise(pid, external_queue, _register=register_handle),
            )
            r = yield Resume(k, ep)
            return r

        elif isinstance(effect, _SchedulerIntrospection):
            r = yield Resume(k, {
                "tasks": len(tasks),
                "promises": len(promises),
                "semaphores": len(semaphores),
                "waiters": len(waiters),
                "ready": len(ready),
                "handle_refs": len(handle_refs),
            })
            return r

        elif isinstance(effect, CreateSemaphore):
            if effect.permits < 1:
                from doeff.program import ResumeThrow
                return (yield ResumeThrow(k, ValueError("permits must be >= 1")))
            sid = fresh_id()
            from collections import deque as deque_type
            semaphores[sid] = {
                "permits": effect.permits,
                "max_permits": effect.permits,
                "waiters": deque_type(),
                # Diagnostics-only permit-holder tracking (#495c): which
                # tasks currently hold a permit (None = root; duplicates
                # allowed for multi-permit holds).
                "holders": [],
            }
            r = yield Resume(k, Semaphore(sid))
            return r

        elif isinstance(effect, AcquireSemaphore):
            sid = effect.semaphore.sem_id
            sem = semaphores[sid]
            if sem["permits"] > 0:
                sem["permits"] -= 1
                sem["holders"].append(current_tid)
                r = yield Resume(k, None)
                return r
            else:
                # Park — FIFO queue
                sem["waiters"].append((current_tid, k))
                yield TailEval(pick_next())

        elif isinstance(effect, ReleaseSemaphore):
            sid = effect.semaphore.sem_id
            sem = semaphores[sid]
            # Transfer the permit directly to the first live waiter (#496:
            # via a recoverable sem_resume entry) or bank it.
            transferred = grant_permit_to_next_waiter(sid)
            if not transferred and sem["permits"] >= sem["max_permits"]:
                from doeff.program import ResumeThrow
                return (yield ResumeThrow(k, RuntimeError("semaphore released too many times")))
            # Holder tracking is diagnostics-only (#495c): releasing from a
            # task that never acquired is legal and just leaves it stale.
            if current_tid in sem["holders"]:
                sem["holders"].remove(current_tid)
            if not transferred:
                sem["permits"] += 1
            r = yield Resume(k, None)
            return r

        else:
            yield Pass(effect, k)

    return make_handler(None)(root_close_out(body_program))
