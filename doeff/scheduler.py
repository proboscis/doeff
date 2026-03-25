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


class Task:
    def __init__(self, task_id):
        self.task_id = task_id
    def __repr__(self):
        return f"Task({self.task_id})"


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

def scheduled(body_program):
    """Wrap a program with the scheduler. Returns a DoExpr."""

    state = {
        "next_id": 0,
        "tasks": {},       # tid → {status, result, cont, program}
        "waiters": {},     # tid → [(type, k, ...)]
        "ready": [],       # [(type, tid, cont, value)]
    }

    def alloc(program):
        tid = state["next_id"]
        state["next_id"] += 1
        state["tasks"][tid] = {"status": "pending", "result": None, "program": program}
        return tid

    def wrap_task(tid, prog):
        """Wrap a task body to catch completion."""
        @do
        def wrapped():
            try:
                result = yield prog
                yield Perform(TaskCompleted(tid, Ok(result)))
            except Exception as e:
                yield Perform(TaskCompleted(tid, Err(e)))
        return wrapped()

    def pick_next():
        """Return DoExpr to run next ready task, or Pure(None) if empty."""
        while state["ready"]:
            entry = state["ready"].pop(0)
            if entry[0] == "new":
                _, tid = entry
                task = state["tasks"][tid]
                task["status"] = "running"
                return WithHandler(handler, wrap_task(tid, task["program"]))
            elif entry[0] == "resume":
                _, cont, value = entry
                return Transfer(cont, value)
        return Pure(None)

    def wake_waiters(completed_tid):
        waiters = state["waiters"].pop(completed_tid, [])
        for w in waiters:
            if w[0] == "wait":
                _, waiter_k = w
                task = state["tasks"][completed_tid]
                state["ready"].append(("resume", waiter_k, task["result"]))
            elif w[0] == "gather":
                _, waiter_k, gather_tasks = w
                all_done = all(
                    state["tasks"][t.task_id]["status"] in ("completed", "failed")
                    for t in gather_tasks
                )
                if all_done:
                    results = [state["tasks"][t.task_id]["result"] for t in gather_tasks]
                    state["ready"].append(("resume", waiter_k, results))
                else:
                    # Re-register for remaining
                    for t in gather_tasks:
                        tid = t.task_id
                        if state["tasks"][tid]["status"] not in ("completed", "failed"):
                            if tid not in state["waiters"]:
                                state["waiters"][tid] = []
                            state["waiters"][tid].append(("gather", waiter_k, gather_tasks))
                            break

    @do
    def handler(effect, k):
        if isinstance(effect, Spawn):
            tid = alloc(effect.program)
            # Queue: new task first, then resume spawner with handle
            state["ready"].append(("new", tid))
            state["ready"].append(("resume", k, Task(tid)))
            # Start next (the new task)
            return (yield pick_next())

        elif isinstance(effect, TaskCompleted):
            tid = effect.task_id
            task = state["tasks"][tid]
            r = effect.result
            if hasattr(r, 'is_ok') and r.is_ok():
                task["status"] = "completed"
                task["result"] = r.value
            else:
                task["status"] = "failed"
                task["result"] = r.error if hasattr(r, 'error') else r
            wake_waiters(tid)
            return (yield pick_next())

        elif isinstance(effect, Wait):
            tid = effect.task.task_id
            task = state["tasks"][tid]
            if task["status"] == "completed":
                result = yield Resume(k, task["result"])
                return result
            elif task["status"] == "failed":
                raise task["result"]
            else:
                if tid not in state["waiters"]:
                    state["waiters"][tid] = []
                state["waiters"][tid].append(("wait", k))
                return (yield pick_next())

        elif isinstance(effect, Gather):
            all_done = all(
                state["tasks"][t.task_id]["status"] in ("completed", "failed")
                for t in effect.tasks
            )
            if all_done:
                results = [state["tasks"][t.task_id]["result"] for t in effect.tasks]
                result = yield Resume(k, results)
                return result
            else:
                for t in effect.tasks:
                    tid = t.task_id
                    if state["tasks"][tid]["status"] not in ("completed", "failed"):
                        if tid not in state["waiters"]:
                            state["waiters"][tid] = []
                        state["waiters"][tid].append(("gather", k, effect.tasks))
                        break
                return (yield pick_next())

        else:
            yield Pass(effect, k)

    return WithHandler(handler, body_program)
