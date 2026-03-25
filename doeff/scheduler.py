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

from doeff_vm import EffectBase, Ok, Err
from doeff.do import do
from doeff.program import Pure, Resume, Transfer, Pass, Perform, WithHandler


# ---------------------------------------------------------------------------
# Effects
# ---------------------------------------------------------------------------

class Spawn(EffectBase):
    def __init__(self, program):
        super().__init__()
        self.program = program


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


class ExternalPromise:
    """Write-side handle for an external promise. Thread-safe complete/fail."""
    def __init__(self, promise_id, queue):
        self.promise_id = promise_id
        self._queue = queue

    @property
    def future(self):
        return Future(self.promise_id)

    def complete(self, value):
        """Complete the promise with a value. Thread-safe."""
        self._queue.append(("complete", self.promise_id, value))

    def fail(self, error):
        """Fail the promise with an error. Thread-safe."""
        self._queue.append(("fail", self.promise_id, error))

    def __repr__(self):
        return f"ExternalPromise({self.promise_id})"


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

def scheduled(body_program):
    """Wrap a program with the scheduler. Returns a DoExpr."""
    from collections import deque

    # --- State ---
    next_id = [0]
    tasks = {}           # tid → {status, result, program}
    promises = {}        # pid → {status, result}
    waiters = {}         # waitable_key → [(type, k, ...)]
    ready = []           # [(type, ...)]
    external_queue = deque()  # thread-safe completion queue

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

    def alloc_task(program):
        tid = fresh_id()
        tasks[tid] = {"status": "pending", "result": None, "program": program}
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
                yield Perform(TaskCompleted(tid, Err(e)))
        return wrapped()

    def drain_external():
        """Drain external completion queue into promise state."""
        while external_queue:
            action, pid, value = external_queue.popleft()
            if pid not in promises or promises[pid]["status"] != "pending":
                continue
            if action == "complete":
                promises[pid]["status"] = "completed"
                promises[pid]["result"] = value
            elif action == "fail":
                promises[pid]["status"] = "failed"
                promises[pid]["result"] = value
            wake_waiters(("promise", pid))

    def pick_next():
        import time
        while True:
            drain_external()
            while ready:
                entry = ready.pop(0)
                if entry[0] == "new":
                    _, tid = entry
                    tasks[tid]["status"] = "running"
                    return WithHandler(handler, wrap_task(tid, tasks[tid]["program"]))
                elif entry[0] == "resume":
                    _, cont, value = entry
                    return Transfer(cont, value)
            # Nothing ready. If there are waiters, external completions may arrive.
            if waiters or external_queue:
                time.sleep(0.001)  # yield CPU, wait for external completions
                continue
            # No waiters, no external — truly done
            return Pure(None)

    def wake_waiters(completed_key):
        ws = waiters.pop(completed_key, [])
        for w in ws:
            if w[0] == "wait":
                _, waiter_k = w
                status, result = waitable_status(completed_key)
                ready.append(("resume", waiter_k, result))
            elif w[0] == "gather":
                _, waiter_k, gather_waitables = w
                all_done = all(
                    waitable_status(waitable_key(t))[0] in ("completed", "failed")
                    for t in gather_waitables
                )
                if all_done:
                    results = [waitable_status(waitable_key(t))[1] for t in gather_waitables]
                    ready.append(("resume", waiter_k, results))
                else:
                    # Re-register for next incomplete
                    for t in gather_waitables:
                        wk = waitable_key(t)
                        if waitable_status(wk)[0] not in ("completed", "failed"):
                            waiters.setdefault(wk, []).append(("gather", waiter_k, gather_waitables))
                            break
            elif w[0] == "race":
                _, waiter_k, race_waitables = w
                # First completed wins
                status, result = waitable_status(completed_key)
                if status == "completed":
                    ready.append(("resume", waiter_k, result))
                elif status == "failed":
                    ready.append(("resume", waiter_k, result))  # TODO: raise

    @do
    def handler(effect, k):
        if isinstance(effect, Spawn):
            tid = alloc_task(effect.program)
            ready.append(("new", tid))
            ready.append(("resume", k, Task(tid)))
            return (yield pick_next())

        elif isinstance(effect, TaskCompleted):
            tid = effect.task_id
            r = effect.result
            if hasattr(r, 'is_ok') and r.is_ok():
                tasks[tid]["status"] = "completed"
                tasks[tid]["result"] = r.value
            else:
                tasks[tid]["status"] = "failed"
                tasks[tid]["result"] = r.error if hasattr(r, 'error') else r
            wake_waiters(("task", tid))
            return (yield pick_next())

        elif isinstance(effect, Wait):
            wk = waitable_key(effect.task)
            status, result = waitable_status(wk)
            if status == "completed":
                r = yield Resume(k, result)
                return r
            elif status == "failed":
                raise result
            else:
                waiters.setdefault(wk, []).append(("wait", k))
                return (yield pick_next())

        elif isinstance(effect, Gather):
            wks = [waitable_key(t) for t in effect.tasks]
            all_done = all(waitable_status(wk)[0] in ("completed", "failed") for wk in wks)
            if all_done:
                results = [waitable_status(wk)[1] for wk in wks]
                r = yield Resume(k, results)
                return r
            else:
                for i, wk in enumerate(wks):
                    if waitable_status(wk)[0] not in ("completed", "failed"):
                        waiters.setdefault(wk, []).append(("gather", k, effect.tasks))
                        break
                return (yield pick_next())

        elif isinstance(effect, Race):
            for t in effect.tasks:
                wk = waitable_key(t)
                status, result = waitable_status(wk)
                if status == "completed":
                    r = yield Resume(k, result)
                    return r
                elif status == "failed":
                    raise result
            # All pending — block on all
            for t in effect.tasks:
                wk = waitable_key(t)
                if waitable_status(wk)[0] == "pending":
                    waiters.setdefault(wk, []).append(("race", k, effect.tasks))
                    break
            return (yield pick_next())

        elif isinstance(effect, CreatePromise):
            pid = alloc_promise()
            r = yield Resume(k, Promise(pid))
            return r

        elif isinstance(effect, CompletePromise):
            pid = effect.promise.promise_id
            promises[pid]["status"] = "completed"
            promises[pid]["result"] = effect.value
            wake_waiters(("promise", pid))
            r = yield Resume(k, None)
            return r

        elif isinstance(effect, FailPromise):
            pid = effect.promise.promise_id
            promises[pid]["status"] = "failed"
            promises[pid]["result"] = effect.error
            wake_waiters(("promise", pid))
            r = yield Resume(k, None)
            return r

        elif isinstance(effect, CreateExternalPromise):
            pid = alloc_promise()
            ep = ExternalPromise(pid, external_queue)
            r = yield Resume(k, ep)
            return r

        else:
            yield Pass(effect, k)

    return WithHandler(handler, body_program)
