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

from doeff_vm import Callable as _VmCallable
from doeff_vm import EffectBase, Err, Ok, TailEval
from doeff_vm import WithObserve as _WithObserveRaw

from doeff.do import do
from doeff.handler_utils import get_inner_boundaries
from doeff.program import Pass, Perform, Pure, Resume, Transfer
from doeff.program import handler as _program_handler


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

    def __init__(self, semaphore_waiters):
        self.semaphore_waiters = {
            sem_id: list(task_ids)
            for sem_id, task_ids in semaphore_waiters.items()
        }
        details = []
        for sem_id, task_ids in self.semaphore_waiters.items():
            if task_ids:
                task_list = ", ".join(str(task_id) for task_id in task_ids)
                details.append(f"semaphore {sem_id}: tasks {task_list}")
            else:
                details.append(f"semaphore {sem_id}: root continuation")
        super().__init__(
            "scheduler deadlock: semaphore waiters remain with no runnable tasks "
            f"({'; '.join(details)})"
        )


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
    """Write-side handle for an internal promise."""
    def __init__(self, promise_id):
        self.promise_id = promise_id

    @property
    def future(self):
        return Future(self.promise_id)

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
    def __init__(self, promise_id, queue):
        self.promise_id = promise_id
        self._queue = queue

    @property
    def future(self):
        return Future(self.promise_id)

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

    def fresh_id():
        i = next_id[0]
        next_id[0] += 1
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
            return tasks[wid]["status"], tasks[wid].get("result")
        if kind == "promise":
            return promises[wid]["status"], promises[wid].get("result")
        return "unknown", None

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

    def enqueue_resume(owner_tid, cont, value, priority=PRIORITY_NORMAL):
        enqueue(("resume", owner_tid, cont, value), priority)

    def enqueue_raise(owner_tid, cont, error, priority=PRIORITY_NORMAL):
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
        return wrapped()

    def _drain_one_external():
        """Block for one external completion and process it."""
        action, pid, value = external_queue.get()
        if pid in promises and promises[pid]["status"] == "pending":
            promises[pid]["status"] = "completed" if action == "complete" else "failed"
            promises[pid]["result"] = value
            wake_waiters(("promise", pid))

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
                if entry[0] == "raise":
                    _, owner_tid, cont, error = entry
                    if is_owner_cancelled(owner_tid):
                        continue
                    return ResumeThrow(cont, error)
                if entry[0] == "wait_external":
                    # Task waiting for external promise at NORMAL priority.
                    # Keeps IDLE tasks (clock driver) from running.
                    _, owner_tid, cont, wk = entry
                    if is_owner_cancelled(owner_tid):
                        continue
                    if waitable_status(wk)[0] in terminal_statuses:
                        resume_with_waitable_result(owner_tid, cont, wk)
                        continue
                    # Not yet resolved — block for one completion, drain rest
                    _drain_one_external()
                    drain()
                    if waitable_status(wk)[0] in terminal_statuses:
                        resume_with_waitable_result(owner_tid, cont, wk)
                    else:
                        enqueue(entry, PRIORITY_EXTERNAL_WAIT)
                    continue
            blocked_semaphore_waiters = live_semaphore_waiters()
            if blocked_semaphore_waiters and not has_pending_external_waiters():
                raise SchedulerDeadlockError(blocked_semaphore_waiters)
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

    def resume_with_waitable_result(owner_tid, waiter_k, key):
        """Add a ready entry that resumes waiter with the waitable's result.
        For failed/cancelled, uses ("raise", k, error) so handler can throw."""
        status, result = waitable_status(key)
        if status == "completed":
            enqueue_resume(owner_tid, waiter_k, result)
        elif status == "failed":
            enqueue_raise(owner_tid, waiter_k, result)
        elif status == "cancelled":
            enqueue_raise(owner_tid, waiter_k, TaskCancelledError())

    def remove_gather_waiters(gather_state):
        """Remove unresolved waiter refs for a fail-fast Gather resolution."""
        for wk in set(gather_state["pending_keys"]):
            entries = waiters.get(wk)
            if not entries:
                continue
            remaining_entries = [
                entry
                for entry in entries
                if not (entry[0] == "gather" and entry[2] is gather_state)
            ]
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
            remaining_entries = [
                entry
                for entry in entries
                if not (entry[0] == "race" and entry[2] is race_state)
            ]
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
                _, owner_tid, waiter_k = w
                resume_with_waitable_result(owner_tid, waiter_k, completed_key)
            elif w[0] == "gather":
                _, _owner_tid, gather_state = w
                wake_gather_waiter(gather_state, completed_key)
            elif w[0] == "race":
                _, _owner_tid, race_state = w
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

    def make_handler(current_tid):
        @do
        def raw_handler(effect, k):
            return (yield TailEval(handle_scheduler_effect(current_tid, effect, k)))
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
            enqueue_resume(current_tid, k, Task(tid))  # spawner resumes at normal priority
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
                        waiters.setdefault(wk, []).append(("wait", current_tid, k))
                    else:
                        enqueue(("wait_external", current_tid, k, wk), PRIORITY_EXTERNAL_WAIT)
                else:
                    # Internal promise: use waiters (resolved by other tasks)
                    waiters.setdefault(wk, []).append(("wait", current_tid, k))
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
                waiters.setdefault(wk, []).append(("gather", current_tid, gather_state))
            yield TailEval(pick_next())

        elif isinstance(effect, Race):
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
                    waiters.setdefault(wk, []).append(("race", current_tid, race_state))
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
            r = yield Resume(k, Promise(pid))
            return r

        elif isinstance(effect, CompletePromise):
            pid = effect.promise.promise_id
            promises[pid]["status"] = "completed"
            promises[pid]["result"] = effect.value
            wake_waiters(("promise", pid))
            # Re-queue completer at IDLE so woken tasks (NORMAL) always
            # run first.  The main user is the sim clock driver which is
            # Spawned at IDLE — without this it would be promoted to
            # NORMAL and race with the tasks it just woke.
            enqueue_resume(current_tid, k, None, PRIORITY_IDLE)
            yield TailEval(pick_next())

        elif isinstance(effect, FailPromise):
            pid = effect.promise.promise_id
            promises[pid]["status"] = "failed"
            promises[pid]["result"] = effect.error
            wake_waiters(("promise", pid))
            enqueue_resume(current_tid, k, None, PRIORITY_IDLE)
            yield TailEval(pick_next())

        elif isinstance(effect, CreateExternalPromise):
            pid = alloc_promise()
            promises[pid]["external"] = True
            ep = ExternalPromise(pid, external_queue)
            r = yield Resume(k, ep)
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
            }
            r = yield Resume(k, Semaphore(sid))
            return r

        elif isinstance(effect, AcquireSemaphore):
            sid = effect.semaphore.sem_id
            sem = semaphores[sid]
            if sem["permits"] > 0:
                sem["permits"] -= 1
                r = yield Resume(k, None)
                return r
            else:
                # Park — FIFO queue
                sem["waiters"].append((current_tid, k))
                yield TailEval(pick_next())

        elif isinstance(effect, ReleaseSemaphore):
            sid = effect.semaphore.sem_id
            sem = semaphores[sid]
            waiter = pop_live_semaphore_waiter(sem)
            if waiter is not None:
                # Transfer permit directly to first waiter
                owner_tid, waiter_k = waiter
                enqueue_resume(owner_tid, waiter_k, None)
            else:
                if sem["permits"] >= sem["max_permits"]:
                    from doeff.program import ResumeThrow
                    return (yield ResumeThrow(k, RuntimeError("semaphore released too many times")))
                sem["permits"] += 1
            r = yield Resume(k, None)
            return r

        else:
            yield Pass(effect, k)

    return make_handler(None)(body_program)
