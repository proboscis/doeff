"""
Cooperative scheduler — OCaml 5 recursive match_with pattern.

Each task gets its own WithHandler. Spawn creates a new WithHandler
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

from doeff_vm import EffectBase, Ok, Err, TailEval
from doeff.do import do
from doeff.program import Pure, Resume, Transfer, Pass, Perform, WithHandler
from doeff.handler_utils import get_inner_handlers


def _enrich_exception_traceback(exc, task_meta=None, vm_ctx=None):
    """Build doeff traceback from VM execution context + task metadata.

    vm_ctx: from GetExecutionContext — fiber chain at error site (before unwinding).
            Contains ["frame", ...] and ["handler", ...] entries from the live fiber chain.
    task_meta: scheduler task metadata with inner_handlers.
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
                if any(p in fn for p in ('/doeff_vm/', '/doeff/do.py', '/doeff/run.py',
                                          '/doeff_core_effects/')):
                    continue
                entries.append(["frame", fs.name, fs.filename, fs.lineno])

    if entries:
        existing = getattr(exc, '__doeff_traceback__', None) or []
        exc.__doeff_traceback__ = entries + existing


# ---------------------------------------------------------------------------
# Effects
# ---------------------------------------------------------------------------

PRIORITY_IDLE = 0
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
    def __init__(self, task):
        super().__init__()
        self.task = task


class Cancel(EffectBase):
    """Cancel a task cooperatively."""
    def __init__(self, task):
        super().__init__()
        self.task = task


class TaskCancelledError(Exception):
    """Raised when waiting on a cancelled task."""
    pass


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

def scheduled(body_program):
    """Wrap a program with the scheduler. Returns a DoExpr."""
    import queue as queue_mod
    import heapq

    # --- State ---
    next_id = [0]
    insertion_seq = [0]  # tie-breaker for priority queue (FIFO within same priority)
    tasks = {}           # tid → {status, result, program, priority}
    promises = {}        # pid → {status, result}
    semaphores = {}      # sid → {permits, max_permits, waiters: deque of k}
    waiters = {}         # waitable_key → [(type, k, ...)]
    ready = []           # heapq: (-priority, seq, entry)
    cancel_requested = set()  # task ids pending cancellation
    external_queue = queue_mod.Queue()  # thread-safe, blocking get()

    def fresh_id():
        i = next_id[0]
        next_id[0] += 1
        return i

    def waitable_key(obj):
        """Convert Task or Future to a dict key."""
        if isinstance(obj, Task):
            return ("task", obj.task_id)
        elif isinstance(obj, Future):
            return ("promise", obj.promise_id)
        raise TypeError(f"expected Task or Future, got {type(obj).__name__}")

    def waitable_status(key):
        kind, wid = key
        if kind == "task":
            return tasks[wid]["status"], tasks[wid].get("result")
        elif kind == "promise":
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

    def alloc_task(program, priority=PRIORITY_NORMAL, inner_handlers=None):
        tid = fresh_id()
        tasks[tid] = {
            "status": "pending", "result": None,
            "program": program, "priority": priority,
            "inner_handlers": inner_handlers or [],
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

    def pick_next():
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
                    # Re-wrap task with inner handlers captured at spawn site
                    for h in tasks[tid].pop("inner_handlers", []):
                        prog = WithHandler(h, prog)
                    return WithHandler(handler, wrap_task(tid, prog))
                elif entry[0] == "resume":
                    _, cont, value = entry
                    return Transfer(cont, value)
                elif entry[0] == "raise":
                    _, cont, error = entry
                    return ResumeThrow(cont, error)
            if not waiters:
                return Pure(None)
            # All tasks blocked — block for one external completion
            action, pid, value = external_queue.get()
            if pid in promises and promises[pid]["status"] == "pending":
                promises[pid]["status"] = "completed" if action == "complete" else "failed"
                promises[pid]["result"] = value
                wake_waiters(("promise", pid))

    TERMINAL = ("completed", "failed", "cancelled")

    def _release_task_refs(tid):
        """Drop heavy references from a terminal task.

        Keeps status/result (needed by Wait/Gather) but releases the
        program, inner_handlers, and spawn_site closures that pin large
        Python object graphs into memory.
        """
        t = tasks.get(tid)
        if t is None:
            return
        t.pop("program", None)
        t.pop("inner_handlers", None)
        t.pop("spawn_site", None)

    def resume_with_waitable_result(waiter_k, key):
        """Add a ready entry that resumes waiter with the waitable's result.
        For failed/cancelled, uses ("raise", k, error) so handler can throw."""
        status, result = waitable_status(key)
        if status == "completed":
            enqueue(("resume", waiter_k, result))
        elif status == "failed":
            enqueue(("raise", waiter_k, result))
        elif status == "cancelled":
            enqueue(("raise", waiter_k, TaskCancelledError()))

    def wake_waiters(completed_key):
        ws = waiters.pop(completed_key, [])
        for w in ws:
            if w[0] == "wait":
                _, waiter_k = w
                resume_with_waitable_result(waiter_k, completed_key)
            elif w[0] == "gather":
                _, waiter_k, gather_waitables = w
                all_done = all(
                    waitable_status(waitable_key(t))[0] in TERMINAL
                    for t in gather_waitables
                )
                if all_done:
                    # Fail-fast: check for first error
                    for t in gather_waitables:
                        s, r = waitable_status(waitable_key(t))
                        if s == "failed":
                            enqueue(("raise", waiter_k, r))
                            break
                        if s == "cancelled":
                            enqueue(("raise", waiter_k, TaskCancelledError()))
                            break
                    else:
                        # All completed successfully
                        results = [waitable_status(waitable_key(t))[1] for t in gather_waitables]
                        enqueue(("resume", waiter_k, results))
                else:
                    # Fail-fast: check if any already failed/cancelled
                    for t in gather_waitables:
                        s, r = waitable_status(waitable_key(t))
                        if s == "failed":
                            enqueue(("raise", waiter_k, r))
                            return
                        if s == "cancelled":
                            enqueue(("raise", waiter_k, TaskCancelledError()))
                            return
                    # Re-register for next incomplete
                    for t in gather_waitables:
                        wk = waitable_key(t)
                        if waitable_status(wk)[0] not in TERMINAL:
                            waiters.setdefault(wk, []).append(("gather", waiter_k, gather_waitables))
                            break
            elif w[0] == "race":
                _, waiter_k, _race_waitables = w
                resume_with_waitable_result(waiter_k, completed_key)

    def drain():
        """Drain all pending external completions into promise state."""
        while not external_queue.empty():
            action, pid, value = external_queue.get()
            if pid in promises and promises[pid]["status"] == "pending":
                promises[pid]["status"] = "completed" if action == "complete" else "failed"
                promises[pid]["result"] = value
                wake_waiters(("promise", pid))

    @do
    def handler(effect, k):
        drain()
        if isinstance(effect, Spawn):
            # Capture inner handlers from continuation (between yield site and scheduler).
            inner_handlers = yield get_inner_handlers(k)

            # Capture spawn site from continuation's traceback
            from doeff.program import GetTraceback
            spawn_frames = yield GetTraceback(k)
            spawn_site = None
            if spawn_frames:
                f = spawn_frames[0]  # innermost = yield Spawn(...) site
                if isinstance(f, (list, tuple)) and len(f) >= 3:
                    spawn_site = f"{f[0]}  {f[1]}:{f[2]}"

            tid = alloc_task(effect.program, effect.priority, inner_handlers=inner_handlers)
            tasks[tid]["spawn_site"] = spawn_site
            enqueue(("new", tid), effect.priority)
            enqueue(("resume", k, Task(tid)))  # spawner resumes at normal priority
            yield TailEval(pick_next())

        elif isinstance(effect, TaskCompleted):
            tid = effect.task_id
            r = effect.result
            if hasattr(r, 'is_ok') and r.is_ok():
                tasks[tid]["status"] = "completed"
                tasks[tid]["result"] = r.value
            else:
                tasks[tid]["status"] = "failed"
                error = r.error if hasattr(r, 'error') else r
                # Add spawn boundary to traceback
                if isinstance(error, BaseException) and hasattr(error, '__doeff_traceback__'):
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
                waiters.setdefault(wk, []).append(("wait", k))
                yield TailEval(pick_next())

        elif isinstance(effect, Gather):
            wks = [waitable_key(t) for t in effect.tasks]
            # Fail-fast: check for first error/cancelled
            for wk in wks:
                s, r = waitable_status(wk)
                if s == "failed":
                    from doeff.program import ResumeThrow
                    return (yield ResumeThrow(k, r))
                if s == "cancelled":
                    from doeff.program import ResumeThrow
                    return (yield ResumeThrow(k, TaskCancelledError()))
            all_done = all(waitable_status(wk)[0] in TERMINAL for wk in wks)
            if all_done:
                results = [waitable_status(wk)[1] for wk in wks]
                r = yield Resume(k, results)
                return r
            else:
                for wk in wks:
                    if waitable_status(wk)[0] not in TERMINAL:
                        waiters.setdefault(wk, []).append(("gather", k, effect.tasks))
                        break
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
            for t in effect.tasks:
                wk = waitable_key(t)
                if waitable_status(wk)[0] not in TERMINAL:
                    waiters.setdefault(wk, []).append(("race", k, effect.tasks))
                    break
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
            # Re-queue completer rather than resuming immediately,
            # so higher-priority woken tasks run first.
            enqueue(("resume", k, None))
            yield TailEval(pick_next())

        elif isinstance(effect, FailPromise):
            pid = effect.promise.promise_id
            promises[pid]["status"] = "failed"
            promises[pid]["result"] = effect.error
            wake_waiters(("promise", pid))
            enqueue(("resume", k, None))
            yield TailEval(pick_next())

        elif isinstance(effect, CreateExternalPromise):
            pid = alloc_promise()
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
                sem["waiters"].append(k)
                yield TailEval(pick_next())

        elif isinstance(effect, ReleaseSemaphore):
            sid = effect.semaphore.sem_id
            sem = semaphores[sid]
            if sem["waiters"]:
                # Transfer permit directly to first waiter
                waiter_k = sem["waiters"].popleft()
                enqueue(("resume", waiter_k, None))
            else:
                if sem["permits"] >= sem["max_permits"]:
                    from doeff.program import ResumeThrow
                    return (yield ResumeThrow(k, RuntimeError("semaphore released too many times")))
                sem["permits"] += 1
            r = yield Resume(k, None)
            return r

        else:
            yield Pass(effect, k)

    return WithHandler(handler, body_program)
