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
from collections.abc import Callable, Generator
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from doeff_vm import Callable as _VmCallable
from doeff_vm import EffectBase, Err, Ok, TailEval
from doeff_vm import WithObserve as _WithObserveRaw

from doeff.do import do
from doeff.handler_utils import get_inner_boundaries
from doeff.program import Pass, Perform, Pure, Resume, Transfer
from doeff.program import handler as _program_handler

if TYPE_CHECKING:
    from doeff import Program

_logger = logging.getLogger(__name__)

# Answer types (docs/23-static-typing.md): Spawn answers Task[T], Wait / Race answer T,
# Gather answers list[T]. Spawn's static __iter__ also yields the spawned program's
# effects E: the task runs under the spawner's handlers, so the spawner declares them.
_T = TypeVar("_T")
_E = TypeVar("_E")

# How long a blocking wait for external completions may stay silent before a
# stall diagnostic is logged (#495b). Blocking semantics are unchanged: the
# scheduler logs the parked-waiter summary and keeps waiting.
EXTERNAL_STALL_LOG_INTERVAL_SECONDS = 30.0

# How many fresh_id allocations pass between two sweeps of terminal,
# unobserved task/promise entries (#502). The sweep runs synchronously inside
# the scheduler's own dispatch path (fresh_id is only called from effect
# handlers), never from a GC callback, so it can safely mutate the state dicts.
HANDLE_SWEEP_INTERVAL = 1024

# Minimum handle_refs list length before dead weakrefs are pruned at
# registration time (#502): a long-lived PENDING promise whose owner mints
# `.future` repeatedly must not accumulate dead refs until terminality.
HANDLE_REFS_PRUNE_MIN = 8


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


class Spawn(EffectBase["Task[_T]"], Generic[_T, _E]):
    """Spawn a task; resumes the spawner with a Task handle.

    ``daemon=True`` declares that the task is background work the run does
    not need for progress (sim clock driver, persistent listener loops):

    - Daemon tasks and their queued wakes are excluded from the root
      close-out diagnostic (#501) — abandoning them at root return is
      their declared lifecycle.
    - Daemon tasks are the only tasks the PRIORITY_EXTERNAL_WAIT shield
      may starve while an external completion is pending (#505): a
      non-daemon entry below the shield always runs before the scheduler
      blocks, because the run may need it to progress (it may be the very
      task producing the awaited completion, or the releaser of a blocked
      semaphore).

    Daemon-ness changes nothing else — scheduling priority, deadlock
    detection (#495), and the stall log still see daemon tasks like any
    other task.
    """

    def __init__(
        self, program: "Program[_T, _E]", priority: int = PRIORITY_NORMAL, daemon: bool = False
    ) -> None:
        super().__init__()
        self.program = program
        self.priority = priority
        self.daemon = daemon

    if TYPE_CHECKING:
        # Static model only (runtime: EffectBase.__iter__ yields just the Spawn). The spawned
        # program runs under the spawner's handler stack, so its effects E are the spawner's
        # to declare — yielding them here makes `yield from Spawn(p)` add p's E to the caller.
        # The Spawn itself is yielded as `Spawn[Any, Any]` (the form a row declares), not
        # `Spawn[_T, _E]`: with the self-referential form, pyright solving `perform(Spawn(p))`
        # against the declared row filled a child with no effects (E = Never) with
        # `Spawn[Unknown, Unknown]` — partially Unknown under strict (docs/24-effectful-perform.md).
        def __iter__(  # pyright: ignore[reportIncompatibleMethodOverride]
            self,
        ) -> Generator["Spawn[Any, Any] | _E", Any, "Task[_T]"]: ...


class TaskCompleted(EffectBase):
    def __init__(self, task_id, result):
        super().__init__()
        self.task_id = task_id
        self.result = result


class Gather(EffectBase[list[_T]], Generic[_T]):
    def __init__(self, *tasks: "Task[_T] | Future[_T]") -> None:
        super().__init__()
        self.tasks = tasks


class Wait(EffectBase[_T], Generic[_T]):
    """Wait for a Task or Future to resolve.

    ``priority`` controls the priority at which the waiter is re-enqueued
    when the waitable resolves (#504). ``None`` (default) means the waiter
    wakes at its own task's spawn priority. For an *external* promise,
    ``PRIORITY_IDLE`` is a park mode, not a wake priority: the wait is
    parked without a ready-heap placeholder (so the sim clock driver may
    run) and the waiter still wakes at its own task priority — an IDLE
    wake would starve it behind the PRIORITY_EXTERNAL_WAIT shield.
    """

    def __init__(self, task: "Task[_T] | Future[_T]", priority: int | None = None) -> None:
        super().__init__()
        self.task = task
        self.priority = priority


class Cancel(EffectBase[None]):
    """Request cancellation of a task; the requester resumes immediately.

    - A task that never started is cancelled without running its body.
    - A started task receives ``TaskCancelledError`` at the point where it
      is suspended (Wait / Gather / Race / AcquireSemaphore / a queued
      resume; a self-cancel at its own Cancel). Its ``except``/``finally``
      blocks run and may perform effects; the task stays live
      (``cancelling``) until it finishes unwinding.
    - Waiters are woken when the task has finished unwinding. They observe
      ``TaskCancelledError`` if it ended by propagating it or by swallowing
      it and returning (the late value is discarded), or the other
      exception its cleanup raised.
    - Each Cancel delivers the exception once; a task that swallows it
      keeps running until it ends or is cancelled again.
    - Cancelling a terminal task is a no-op.
    """
    def __init__(self, task: "Task[Any]") -> None:
        super().__init__()
        self.task = task


class TaskCancelledError(Exception):
    """Thrown into a cancelled task, and raised to whoever waits on it."""


class ExternalPromiseCancelCallbackError(RuntimeError):
    """Raised to the ``Cancel`` caller when an ``on_cancel`` callback failed.

    The cancellation itself has already taken effect (the task was sent
    ``TaskCancelledError`` and the abandoned external promises are
    ``cancelled``); ``errors`` lists every callback exception so none is
    lost.
    """

    def __init__(self, errors: list[Exception]) -> None:
        self.errors = list(errors)
        super().__init__(
            "external promise cancel callback(s) failed: "
            + "; ".join(repr(error) for error in self.errors)
        )


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


class Race(EffectBase[_T], Generic[_T]):
    def __init__(self, *tasks: "Task[_T] | Future[_T]") -> None:
        super().__init__()
        self.tasks = tasks


class CreatePromise(EffectBase["Promise[_T]"], Generic[_T]):
    """``promise = yield from CreatePromise[int]()`` gives ``Promise[int]``."""

    def __init__(self) -> None:
        super().__init__()


class CompletePromise(EffectBase[None], Generic[_T]):
    def __init__(self, promise: "Promise[_T]", value: _T) -> None:
        super().__init__()
        self.promise = promise
        self.value = value


class FailPromise(EffectBase[None]):
    def __init__(self, promise: "Promise[Any]", error: BaseException) -> None:
        super().__init__()
        self.promise = promise
        self.error = error


class CreateSemaphore(EffectBase["Semaphore"]):
    def __init__(self, permits: int = 1) -> None:
        super().__init__()
        self.permits = permits


class AcquireSemaphore(EffectBase[None]):
    def __init__(self, semaphore: "Semaphore") -> None:
        super().__init__()
        self.semaphore = semaphore


class ReleaseSemaphore(EffectBase[None]):
    def __init__(self, semaphore: "Semaphore") -> None:
        super().__init__()
        self.semaphore = semaphore


class CreateExternalPromise(EffectBase["ExternalPromise[_T]"], Generic[_T]):
    def __init__(self) -> None:
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

class Task(Generic[_T]):
    """Handle of a spawned task; ``Task[T]`` = a task whose program returns T."""

    def __init__(self, task_id):
        self.task_id = task_id
    def __repr__(self):
        return f"Task({self.task_id})"


class Future(Generic[_T]):
    """Read-side handle for a promise."""
    def __init__(self, promise_id):
        self.promise_id = promise_id
    def __repr__(self):
        return f"Future({self.promise_id})"


class Promise(Generic[_T]):
    """Write-side handle for an internal promise.

    ``_register`` is the owning run's handle registrar (#502): every derived
    ``Future`` must be registered too, so a promise entry is only swept once
    the write handle AND every read handle created from it are dead.
    """
    def __init__(self, promise_id, _register=None):
        self.promise_id = promise_id
        self._register = _register

    @property
    def future(self) -> "Future[_T]":
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


class ExternalPromise(Generic[_T]):
    """Write-side handle for an external promise.

    ``complete``/``fail`` are the ONLY thread-safe operations — they may be
    called from foreign threads (the primary pattern) or from in-run tasks
    (a non-daemon in-run completer stays runnable below the external-wait
    shield, #505). Minting ``.future`` and registering ``on_cancel`` must
    happen on the scheduler thread: handle registration (#502) and the
    promise state are scheduler-thread-confined.
    """
    def __init__(self, promise_id, queue, _register=None, _bind_cancel=None) -> None:
        self.promise_id = promise_id
        self._queue = queue
        self._register = _register
        self._bind_cancel = _bind_cancel

    def on_cancel(self, callback: Callable[[], object]) -> None:
        """Register ``callback()`` to stop the external producer on cancel.

        The scheduler cancels a PENDING external promise when a ``Cancel``
        removes its last live waiter (every task parked on it via Wait /
        Gather / Race is cancelled): the promise becomes ``cancelled`` —
        later ``complete``/``fail`` calls are ignored and later waits raise
        ``TaskCancelledError`` — and each registered callback runs once, on
        the scheduler thread, inside the ``Cancel`` dispatch. Callbacks must
        therefore be quick and thread-safe towards the producer (e.g.
        ``concurrent.futures.Future.cancel``). A callback exception is
        re-raised to the ``Cancel`` caller as
        ``ExternalPromiseCancelCallbackError`` after the cancellation took
        effect.

        Registering on an already-cancelled promise runs ``callback``
        immediately; on a completed/failed promise it is a no-op. Must be
        called on the scheduler thread (like minting ``.future``).
        """
        if not callable(callback):
            raise TypeError(
                f"on_cancel expects a callable, got {type(callback).__name__}"
            )
        if self._bind_cancel is None:
            raise RuntimeError(
                f"{self!r} is not bound to a scheduler run; on_cancel is unavailable"
            )
        self._bind_cancel(self.promise_id, callback)

    @property
    def future(self) -> "Future[_T]":
        future = Future(self.promise_id)
        if self._register is not None:
            self._register(("promise", self.promise_id), future)
        return future

    def complete(self, value: _T) -> None:
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

def scheduled(body_program: "Program[_T, Any]") -> "Program[_T, Any]":  # noqa: PLR0915 - baseline cleanup keeps existing control flow unchanged
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
    handle_prune_at = {}  # waitable_key → refs length that triggers a dead-ref prune

    def register_handle(key, handle):
        """Track handle liveness for the terminal-entry sweep (#502).

        Weakrefs carry no callbacks — liveness is polled by the sweep inside
        the scheduler's own dispatch path, never from a GC callback, so no
        reentrant dict mutation can occur.

        Dead refs are pruned amortized at registration time (doubling
        threshold), so a long-lived PENDING promise whose owner repeatedly
        mints `.future` handles does not grow its list until terminality.
        Registration is scheduler-thread-confined — only ``complete``/
        ``fail`` are thread-safe on ExternalPromise — so the in-place prune
        cannot race a foreign-thread append.
        """
        refs = handle_refs.setdefault(key, [])
        if len(refs) >= handle_prune_at.get(key, HANDLE_REFS_PRUNE_MIN):
            refs[:] = [ref for ref in refs if ref() is not None]
            handle_prune_at[key] = max(HANDLE_REFS_PRUNE_MIN, 2 * len(refs))
        refs.append(weakref.ref(handle))
        return handle

    def sweep_terminal_unobserved_entries():
        """Delete task/promise entries nothing can ever observe again (#502).

        An entry is swept when it is terminal, has no registered waiter, is
        not referenced by a live Gather/Race resolution, and every handle
        (Task, Promise/ExternalPromise, and each Future minted from them) is
        dead. Cancelled tasks are exempt: a task dropped by the Cancel
        fallback (continuation not held by the scheduler) may still run to
        its TaskCompleted, which re-reads tasks[tid]; ``cancelling`` tasks
        are live. Semaphores are never swept:
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
        # A cancelled PROMISE (external promise whose last waiter was
        # cancelled) has no such re-read and is as final as completed/failed.
        sweepable = {
            "task": ("completed", "failed"),
            "promise": ("completed", "failed", "cancelled"),
        }
        for kind, store in (("task", tasks), ("promise", promises)):
            dead = []
            for wid, meta in store.items():
                if meta["status"] not in sweepable[kind]:
                    continue
                key = (kind, wid)
                if key in waiters or key in protected:
                    continue
                refs = handle_refs.get(key)
                if refs is None:
                    continue
                # register_handle prunes dead refs amortized; here the whole
                # list is dropped once every handle is dead, at which point
                # no new registration for this key can happen (a Future is
                # only minted from a live parent handle).
                if any(ref() is not None for ref in refs):
                    continue
                dead.append(wid)
            for wid in dead:
                del store[wid]
                del handle_refs[(kind, wid)]
                handle_prune_at.pop((kind, wid), None)

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

    def has_live_ready_entry(include_daemons):
        """True when the ready heap holds an entry that can still run.

        ``include_daemons=False`` asks the narrower question the
        external-wait shield needs: is there real (non-daemon) work below
        the shield? Placeholders are never "work"; cancelled owners are
        dead entries.
        """
        for _neg_prio, _seq, entry in ready:
            kind = entry[0]
            if kind == "new":
                tid = entry[1]
                if tasks[tid]["status"] == "cancelled":
                    continue
                if include_daemons or not is_daemon(tid):
                    return True
            elif kind in ("resume", "raise", "sem_resume"):
                owner_tid = entry[1]
                if is_owner_cancelled(owner_tid):
                    continue
                if include_daemons or not is_daemon(owner_tid):
                    return True
        return False

    def is_live_daemon_entry(entry):
        """True for a runnable entry owned by a live daemon task."""
        kind = entry[0]
        if kind == "new":
            tid = entry[1]
            return tasks[tid]["status"] != "cancelled" and is_daemon(tid)
        if kind in ("resume", "raise", "sem_resume"):
            owner_tid = entry[1]
            return not is_owner_cancelled(owner_tid) and is_daemon(owner_tid)
        return False

    def is_owner_cancelled(owner_tid):
        return (
            owner_tid is not None
            and tasks.get(owner_tid, {}).get("status") == "cancelled"
        )

    def is_daemon(owner_tid):
        """True when the owner was spawned with daemon=True (root is not)."""
        return (
            owner_tid is not None
            and tasks.get(owner_tid, {}).get("daemon", False)
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
        """Queue a resume; returns the effective priority used (#493)."""
        if priority is None:
            priority = task_priority(owner_tid)
        enqueue(("resume", owner_tid, cont, value), priority)
        return priority

    def enqueue_raise(owner_tid, cont, error, priority=None):
        """Queue a throw-resume; returns the effective priority used (#493)."""
        if priority is None:
            priority = task_priority(owner_tid)
        enqueue(("raise", owner_tid, cont, error), priority)
        return priority

    def alloc_task(program, priority=PRIORITY_NORMAL, inner_boundaries=None,
                   daemon=False):
        tid = fresh_id()
        tasks[tid] = {
            "status": "pending", "result": None,
            "program": program, "priority": priority,
            "daemon": daemon,
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
        settle_external(action, pid, value)

    def settle_external(action: str, pid: int, value: object) -> None:
        """Apply one drained external completion.

        Only a PENDING promise settles: a late completion of a cancelled
        external promise (its producer finished before observing the
        cancel) — or a double resolution — is ignored.
        """
        promise = promises.get(pid)
        if promise is None or promise["status"] != "pending":
            return
        promise["status"] = "completed" if action == "complete" else "failed"
        promise["result"] = value
        # Settled: nothing left to cancel; drop the callbacks' references.
        promise.pop("cancel_callbacks", None)
        wake_waiters(("promise", pid))

    def bind_cancel_callback(pid: int, callback: Callable[[], object]) -> None:
        """Backs ExternalPromise.on_cancel (scheduler thread only)."""
        promise = promises[pid]
        status = promise["status"]
        if status == "cancelled":
            callback()
        elif status == "pending":
            promise.setdefault("cancel_callbacks", []).append(callback)

    def keys_parked_by(tid: int) -> list[tuple[str, int]]:
        """Waitable keys that hold a Wait/Gather/Race entry owned by ``tid``."""
        return [
            key
            for key, entries in waiters.items()
            if any(entry[1] == tid for entry in entries)
        ]

    def cancel_abandoned_external_promises(
        parked_keys: list[tuple[str, int]],
    ) -> list[Exception]:
        """Propagate a task cancellation to the producers it was waiting on.

        ``parked_keys`` are the waitables the cancelled task was parked on
        (captured before its continuation was detached). A pending external
        promise among them with ``on_cancel`` callbacks is cancelled when no
        live waiter remains (every remaining Wait/Gather/Race entry belongs
        to a cancelled task; the root is never cancelled). Its waiters entry
        is dropped — nothing can resume from it — and the callbacks run.
        Promises without callbacks keep the historical semantics (stay
        pending, a later completion still settles them).

        Returns the callback exceptions; the caller surfaces them only after
        every abandoned promise was cancelled, so one failing producer
        cannot leave the others running.
        """
        errors = []
        for key in parked_keys:
            kind, pid = key
            if kind != "promise":
                continue
            promise = promises.get(pid)
            if (
                promise is None
                or promise["status"] != "pending"
                or not promise.get("cancel_callbacks")
            ):
                continue
            entries = waiters.get(key, [])
            if any(not is_owner_cancelled(entry[1]) for entry in entries):
                continue
            waiters.pop(key, None)
            promise["status"] = "cancelled"
            promise["result"] = TaskCancelledError()
            for callback in promise.pop("cancel_callbacks"):
                try:
                    callback()
                except Exception as error:  # collected, re-raised by Cancel
                    errors.append(error)
        return errors

    def detach_parked_continuation(tid: int) -> object | None:
        """Take the one continuation of started task ``tid`` the scheduler holds.

        A started task that is not the one currently running is suspended in
        exactly one scheduler structure: a Wait/Gather/Race registration in
        ``waiters``, a queued resume/raise/permit in the ready heap, or a
        semaphore wait queue. The continuation is removed from there (so no
        wake can resume it a second time) and returned, so Cancel can throw
        ``TaskCancelledError`` into it. Returns None when the scheduler does
        not hold the task's continuation.
        """
        for detach in (detach_from_waiters, detach_from_ready, detach_from_semaphores):
            cont = detach(tid)
            if cont is not None:
                return cont
        return None

    def detach_from_waiters(tid: int) -> object | None:
        """Detach a Wait/Gather/Race registration of ``tid`` (see above).

        The external-wait placeholder is claimed so it drops itself, and a
        Gather/Race registration is resolved and removed from every key.
        """
        for key in list(waiters):
            entries = waiters[key]
            for index, entry in enumerate(entries):
                if entry[1] != tid:
                    continue
                entry_type = entry[0]
                if entry_type in ("gather", "race"):
                    state = entry[2]
                    state["resolved"] = True
                    if entry_type == "gather":
                        remove_gather_waiters(state)
                    else:
                        remove_race_waiters(state)
                    return state["waiter_k"]
                if entry_type == "wait_external":
                    entry[3][0] = True  # drop the paired ready placeholder
                del entries[index]
                if not entries:
                    del waiters[key]
                return entry[2]
        return None

    def detach_from_ready(tid: int) -> object | None:
        """Detach a queued resume/raise/permit of ``tid`` (see above).

        A permit already transferred to the task is handed on (#496).
        """
        for index, heap_item in enumerate(ready):
            entry = heap_item[2]
            if entry[0] not in ("resume", "raise", "sem_resume") or entry[1] != tid:
                continue
            ready.pop(index)
            heapq.heapify(ready)
            if entry[0] == "sem_resume":
                return_inflight_permit(entry[3], tid)
            return entry[2]
        return None

    def detach_from_semaphores(tid: int) -> object | None:
        """Detach ``tid`` from a semaphore wait queue (see above)."""
        for sem in semaphores.values():
            for waiter in sem["waiters"]:
                if waiter[0] == tid:
                    sem["waiters"].remove(waiter)
                    return waiter[1]
        return None

    def finish_cancelled(tid: int, error: BaseException) -> None:
        """Make ``tid`` terminal as cancelled and wake whoever waits on it."""
        tasks[tid]["status"] = "cancelled"
        tasks[tid]["result"] = error
        wake_waiters(("task", tid))
        _release_task_refs(tid)

    def live_parked_waiter_summary(include_daemons=True):
        """Describe live (non-cancelled) parked waiters for diagnostics.

        ``include_daemons=False`` is the root close-out view (#501): a
        daemon parked at root return is the documented listener pattern,
        not lost work. Deadlock detection and the stall log keep the full
        view — a daemon hung mid-run still hangs the run.
        """
        parked = []
        for (kind, wid), entries in waiters.items():
            for entry in entries:
                wtype, owner_tid = entry[0], entry[1]
                if is_owner_cancelled(owner_tid):
                    continue
                if not include_daemons and is_daemon(owner_tid):
                    continue
                who = "root" if owner_tid is None else f"task {owner_tid}"
                parked.append(f"{who} ({wtype}) on {kind} {wid}")
        return parked

    def abandoned_ready_summary():
        """Describe live ready-heap entries a root return would abandon (#501).

        "wait_external" placeholders are skipped: an unclaimed one is already
        reported through its paired waiters registration and a claimed one is
        inert by construction. Daemon-owned entries are skipped: abandoning
        daemon work at root return is that flag's declared contract.
        """
        abandoned = []
        for _neg_prio, _seq, entry in ready:
            kind = entry[0]
            if kind == "new":
                tid = entry[1]
                if tasks[tid]["status"] != "cancelled" and not is_daemon(tid):
                    abandoned.append(f"unstarted task {tid}")
            elif kind in ("resume", "raise"):
                owner_tid = entry[1]
                if not is_owner_cancelled(owner_tid) and not is_daemon(owner_tid):
                    who = "root" if owner_tid is None else f"task {owner_tid}"
                    abandoned.append(f"queued {kind} for {who}")
            elif kind == "sem_resume":
                owner_tid, sid = entry[1], entry[3]
                if not is_owner_cancelled(owner_tid) and not is_daemon(owner_tid):
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
        parked = live_parked_waiter_summary(include_daemons=False)
        if abandoned or parked:
            warnings.warn(
                "scheduler root body returned while abandoning in-flight "
                f"work (#501): ready entries [{'; '.join(abandoned)}]; "
                f"parked waiters [{'; '.join(parked)}]. Spawned work that "
                "must finish has to be awaited (Wait/Gather) before the "
                "root body returns; intentional background work should be "
                "spawned with Spawn(..., daemon=True).",
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
        """Blocked semaphores whose permit holders can never release (#495c).

        A holder is stuck when it is itself parked in the waiter queue of
        a semaphore still presumed deadlocked — computed as a greatest
        fixpoint so a holder awaiting an external event keeps its
        semaphore (and everything parked behind it) blocking quietly, and
        cross-semaphore cycles are still caught.

        MODEL: permits are released by their holders. #495b/c require the
        deadlock diagnosed even while unrelated live external waiters
        exist, so the check cannot soundly assume "anyone might release
        later". A program that instead uses a semaphore as a cross-task
        SIGNAL — every holder parked on the semaphore family while a
        non-holder plans to release it after an external event — is
        diagnosed as a deadlock by this model; use a Promise for
        signalling. A stale holder left by such a cross-task release can
        additionally make the check miss a later deadlock (never the
        reverse: a missing/extra holder that is not parked only prunes).
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

    def pick_next():  # noqa: PLR0912, PLR0915 - scheduler dispatch loop has one branch per ready entry
        from doeff.program import ResumeThrow
        # Heap tuples held out of the heap for the duration of this call:
        # unclaimed external-wait placeholders deferring to live non-daemon
        # work below them (#505), plus the daemon entries those placeholders
        # shield. Re-pushed with their ORIGINAL seq in the finally block, so
        # heap order is preserved exactly across pick_next calls.
        held = []
        shield_deferred = False
        try:
            while True:
                drain()
                while ready:
                    heap_item = heapq.heappop(ready)
                    entry = heap_item[2]
                    if (
                        shield_deferred
                        and entry[0] != "wait_external"
                        and is_live_daemon_entry(entry)
                    ):
                        # An unclaimed external-wait placeholder was deferred
                        # to let non-daemon work run (#505); daemon work stays
                        # shielded exactly as if the placeholder were still
                        # ahead of it in the heap.
                        held.append(heap_item)
                        continue
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
                        # Keeps DAEMON tasks (sim clock driver) from running
                        # while the wait is pending, and drives the blocking
                        # drain. The resume itself is owned by the paired
                        # `waiters` registration: wake_waiters claims and
                        # enqueues it the moment the completion is drained —
                        # even while this loop is blocked on a DIFFERENT
                        # unresolved external wait (#490). This entry never
                        # resumes the continuation; once claimed it is simply
                        # dropped.
                        _, owner_tid, _cont, _wk, claimed = entry
                        if claimed[0] or is_owner_cancelled(owner_tid):
                            continue
                        if has_live_ready_entry(include_daemons=False):
                            # Real (non-daemon) work is runnable below this
                            # placeholder. The shield may starve only daemon
                            # tasks: starving a non-daemon entry can starve
                            # the very task that would produce the awaited
                            # completion (in-run completer, #505) or release
                            # a blocked semaphore (#495c). Defer the
                            # placeholder for this pass and let the
                            # non-daemon entry run; daemon entries popped
                            # after this stay held via the guard above.
                            held.append(heap_item)
                            shield_deferred = True
                            continue
                        if not has_live_ready_entry(include_daemons=True):
                            # Deadlock diagnostics must stay reachable while
                            # this branch keeps the loop from ever reaching
                            # the bottom of pick_next (#495c/d) — but only
                            # when NOTHING else can run: any live entry
                            # (shielded daemon included) may still release a
                            # blocked semaphore once the external completion
                            # arrives, so declaring doom while one exists
                            # would be a false positive.
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
        finally:
            # Restore every held tuple with its original (priority, seq) so
            # the heap looks exactly as if the deferral never happened.
            for heap_item in held:
                heapq.heappush(ready, heap_item)

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
        priority=None wakes the waiter at its own task priority (#504).
        Returns the effective wake priority, or None if nothing was queued."""
        status, result = waitable_status(key)
        if status == "completed":
            return enqueue_resume(owner_tid, waiter_k, result, priority)
        if status == "failed":
            return enqueue_raise(owner_tid, waiter_k, result, priority)
        if status == "cancelled":
            return enqueue_raise(owner_tid, waiter_k, TaskCancelledError(), priority)
        return None

    def register_pending_waiter(wk, entry_type, owner_tid, state):
        """Register a gather/race waiter for a pending waitable (#505).

        A pending EXTERNAL promise additionally gets a claimed-style
        placeholder at PRIORITY_EXTERNAL_WAIT — the same shape Wait uses
        (#490/#491): the placeholder only blocks DAEMON tasks (the sim
        clock driver) and drives the blocking drain; non-daemon work below
        it still runs (in-run completers must not be starved) and it never
        resumes anything. wake_waiters
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
        """Resolve a Gather with an error; returns the wake priority used."""
        if gather_state["resolved"]:
            return None
        gather_state["resolved"] = True  # noqa: DOEFF007 - the scheduler-owned Gather/Race state cell (pre-existing)
        gather_state["failure"] = error  # noqa: DOEFF007 - the scheduler-owned Gather/Race state cell (pre-existing)
        remove_gather_waiters(gather_state)
        return enqueue_raise(gather_state["owner_tid"], gather_state["waiter_k"], error)

    def wake_gather_waiter(gather_state, completed_key):
        """Returns the wake priority if the Gather owner was queued, else None."""
        if gather_state["resolved"]:
            return None

        status, result = waitable_status(completed_key)
        if status == "failed":
            return resolve_gather_with_error(gather_state, result)
        if status == "cancelled":
            return resolve_gather_with_error(gather_state, TaskCancelledError())

        if status != "completed":
            return None

        gather_state["remaining"] -= 1  # noqa: DOEFF007 - the scheduler-owned Gather/Race state cell (pre-existing)
        if gather_state["remaining"] == 0:
            gather_state["resolved"] = True  # noqa: DOEFF007 - the scheduler-owned Gather/Race state cell (pre-existing)
            results = [waitable_status(wk)[1] for wk in gather_state["keys"]]
            return enqueue_resume(
                gather_state["owner_tid"], gather_state["waiter_k"], results
            )
        return None

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
        """Returns the wake priority if the Race owner was queued, else None."""
        if race_state["resolved"]:
            return None

        status, result = waitable_status(completed_key)
        if status not in terminal_statuses:
            return None

        race_state["resolved"] = True  # noqa: DOEFF007 - the scheduler-owned Gather/Race state cell (pre-existing)
        remove_race_waiters(race_state)
        if status == "completed":
            return enqueue_resume(race_state["owner_tid"], race_state["waiter_k"], result)
        if status == "failed":
            return enqueue_raise(race_state["owner_tid"], race_state["waiter_k"], result)
        return enqueue_raise(
            race_state["owner_tid"], race_state["waiter_k"], TaskCancelledError()
        )

    def wake_waiters(completed_key):
        """Wake everything parked on ``completed_key``.

        Returns the MINIMUM priority at which a waiter was queued (None when
        nothing was queued), so CompletePromise/FailPromise can guarantee the
        completer resumes only after every task its resolution woke (#493).
        """
        ws = waiters.pop(completed_key, [])
        woken_min = None
        for w in ws:
            queued_at = None
            if w[0] == "wait":
                _, owner_tid, waiter_k, wake_priority = w
                queued_at = resume_with_waitable_result(
                    owner_tid, waiter_k, completed_key, wake_priority
                )
            elif w[0] == "wait_external":
                # Eager wake for a non-IDLE external wait (#490): enqueue the
                # resume now and claim it so the paired ready-heap placeholder
                # drops itself instead of resuming a second time.
                _, owner_tid, waiter_k, claimed, wake_priority = w
                claimed[0] = True
                queued_at = resume_with_waitable_result(
                    owner_tid, waiter_k, completed_key, wake_priority
                )
            elif w[0] == "gather":
                _, _owner_tid, gather_state, claimed = w
                if claimed is not None:
                    # External-promise key (#505): drop the paired
                    # ready-heap placeholder now that the completion is in.
                    claimed[0] = True
                queued_at = wake_gather_waiter(gather_state, completed_key)
            elif w[0] == "race":
                _, _owner_tid, race_state, claimed = w
                if claimed is not None:
                    claimed[0] = True
                queued_at = wake_race_waiter(race_state, completed_key)
            if queued_at is not None and (woken_min is None or queued_at < woken_min):
                woken_min = queued_at
        return woken_min

    def drain():
        """Drain all pending external completions into promise state."""
        while not external_queue.empty():
            action, pid, value = external_queue.get()
            settle_external(action, pid, value)

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

            tid = alloc_task(effect.program, effect.priority,
                             inner_boundaries=inner_boundaries,
                             daemon=effect.daemon)
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
            status = tasks[tid]["status"]
            error = None if (hasattr(r, "is_ok") and r.is_ok()) else (
                r.error if hasattr(r, "error") else r
            )
            if status == "cancelled":
                _release_task_refs(tid)
            elif status == "cancelling" and (
                error is None or isinstance(error, TaskCancelledError)
            ):
                # The task finished unwinding a Cancel: by propagating the
                # TaskCancelledError, or by swallowing it and returning — the
                # late value is discarded, the Cancel still wins.
                finish_cancelled(
                    tid, error if error is not None else TaskCancelledError()
                )
            else:
                # A cancelling task whose cleanup raised something else is
                # reported as failed with that error (it is not hidden).
                if error is None:
                    tasks[tid]["status"] = "completed"
                    tasks[tid]["result"] = r.value
                else:
                    tasks[tid]["status"] = "failed"
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
                    # External promise: by default hold a ready-heap
                    # placeholder that shields DAEMON tasks (the sim clock
                    # driver) from running past the pending completion.
                    # Callers that are just listening in the background
                    # (MCP server loop, result file polling, etc.) pass
                    # priority=PRIORITY_IDLE, which parks the task in
                    # `waiters` just like an internal promise — drain()
                    # wakes it when the promise resolves, and the clock
                    # driver is free to advance sim time.
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
            # Cancellation is delivered INTO the task (see Cancel's docstring):
            # a started task receives TaskCancelledError at its suspension
            # point, so its except/finally run; it stays live ("cancelling")
            # until it finishes unwinding, and only then are its waiters woken.
            from doeff.program import ResumeThrow
            tid = effect.task.task_id
            task = tasks.get(tid)
            cancel_errors = []
            if task is not None and task["status"] == "pending":
                # Never started: no body ran, so there is nothing to unwind.
                finish_cancelled(tid, TaskCancelledError())
            elif task is not None and task["status"] in ("running", "cancelling"):
                task["status"] = "cancelling"
                if tid == current_tid:
                    # Self-cancel: this Cancel is the task's suspension point.
                    return (yield ResumeThrow(k, TaskCancelledError()))
                parked_keys = keys_parked_by(tid)
                cont = detach_parked_continuation(tid)
                if cont is None:
                    # The scheduler does not hold the task's continuation, so
                    # it cannot deliver the exception; fall back to the old
                    # drop-the-task behaviour, loudly.
                    _logger.warning(
                        "Cancel(task %s): the scheduler does not hold its "
                        "continuation; the task is dropped without running "
                        "its except/finally blocks",
                        tid,
                    )
                    finish_cancelled(tid, TaskCancelledError())
                else:
                    enqueue_raise(tid, cont, TaskCancelledError())
                # Stop the external work the task was parked on (#498).
                cancel_errors = cancel_abandoned_external_promises(parked_keys)
            if cancel_errors:
                error = ExternalPromiseCancelCallbackError(cancel_errors)
                error.__cause__ = cancel_errors[0]
                return (yield ResumeThrow(k, error))
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
            woken_min = wake_waiters(("promise", pid))
            # Re-queue the completer at min(its OWN task priority, the
            # lowest wake priority it just caused): the tasks a resolution
            # woke ALWAYS run before the completer resumes — waiters are
            # enqueued first, so FIFO within equal priority finishes the
            # guarantee. Pub/sub loops depend on it: a HIGH publisher
            # resuming ahead of the woken NORMAL listener publishes the
            # next event before the listener re-registers, silently losing
            # it (doeff_events memory handler; adversarial finding on the
            # first #493 fix). #493 itself stays fixed because there is no
            # hard-coded IDLE demotion: with no woken waiter the completer
            # keeps its own priority, and it is never demoted below the
            # tasks it woke — so it cannot freeze behind an external-wait
            # placeholder unless the tasks it woke are equally frozen.
            own = task_priority(current_tid)
            completer_priority = own if woken_min is None else min(own, woken_min)
            enqueue_resume(current_tid, k, None, completer_priority)
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
            woken_min = wake_waiters(("promise", pid))
            # Same as CompletePromise: the completer resumes only after the
            # tasks its resolution woke (#493 + pub/sub ordering guarantee).
            own = task_priority(current_tid)
            completer_priority = own if woken_min is None else min(own, woken_min)
            enqueue_resume(current_tid, k, None, completer_priority)
            yield TailEval(pick_next())

        elif isinstance(effect, CreateExternalPromise):
            pid = alloc_promise()
            promises[pid]["external"] = True
            ep = register_handle(
                ("promise", pid),
                ExternalPromise(
                    pid, external_queue,
                    _register=register_handle,
                    _bind_cancel=bind_cancel_callback,
                ),
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
                "handle_ref_total": sum(len(refs) for refs in handle_refs.values()),
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
