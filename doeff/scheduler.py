"""
Cooperative scheduler — envelope-based, per-yield preemption.

The scheduler is an effect handler that manages tasks. Each spawned task's
program is wrapped in an envelope generator that:
1. Forwards the task's yields to the VM
2. Inserts SchedulerYield after each step (preemption point)
3. Catches completion/failure → emits TaskCompleted

The scheduler handler handles: Spawn, SchedulerYield, TaskCompleted,
Wait, Gather. Everything else is passed to outer handlers.

Usage:
    from doeff import do, run, WithHandler
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
from doeff.program import Resume, Transfer, Pass, Perform, WithHandler


# ---------------------------------------------------------------------------
# Effects
# ---------------------------------------------------------------------------


class Spawn(EffectBase):
    """Spawn a program as a background task. Returns Task handle."""
    def __init__(self, program):
        super().__init__()
        self.program = program


class SchedulerYield(EffectBase):
    """Preemption point inserted by envelope after each step."""
    def __init__(self, task_id):
        super().__init__()
        self.task_id = task_id


class TaskCompleted(EffectBase):
    """Task finished (success or failure). Inserted by envelope."""
    def __init__(self, task_id, result):
        super().__init__()
        self.task_id = task_id
        self.result = result  # Ok(value) or Err(error)


class Gather(EffectBase):
    """Wait for all tasks to complete. Returns list of results."""
    def __init__(self, *tasks):
        super().__init__()
        self.tasks = tasks


class Wait(EffectBase):
    """Wait for a single task to complete. Returns result."""
    def __init__(self, task):
        super().__init__()
        self.task = task


# ---------------------------------------------------------------------------
# Task handle
# ---------------------------------------------------------------------------


class Task:
    """Handle to a spawned task."""
    def __init__(self, task_id):
        self.task_id = task_id

    def __repr__(self):
        return f"Task({self.task_id})"


# ---------------------------------------------------------------------------
# Envelope
# ---------------------------------------------------------------------------


def _envelope(task_id):
    """Create an envelope generator factory for a task.

    The envelope wraps the task's program: forwards yields, inserts
    SchedulerYield after each step, catches completion/failure.

    Returns a @do function that takes the task program as argument.
    """
    @do
    def run_enveloped(program):
        result = None
        try:
            # Start the program — yield it to the VM, get first DoExpr
            result = yield program
        except Exception as e:
            yield Perform(TaskCompleted(task_id, result=Err(e)))
            return
        yield Perform(TaskCompleted(task_id, result=Ok(result)))

    return run_enveloped


# ---------------------------------------------------------------------------
# Scheduler state
# ---------------------------------------------------------------------------


class _SchedulerState:
    def __init__(self):
        self.next_task_id = 0
        self.tasks = {}        # task_id → dict
        self.ready_queue = []  # task_ids
        self.waiters = {}      # task_id → list of waiter info

    def alloc_task(self, program):
        tid = self.next_task_id
        self.next_task_id += 1
        self.tasks[tid] = {
            "status": "pending",
            "program": program,
            "cont": None,
            "result": None,
        }
        self.ready_queue.append(tid)
        return tid

    def next_ready(self):
        while self.ready_queue:
            tid = self.ready_queue.pop(0)
            task = self.tasks.get(tid)
            if task and task["status"] in ("pending", "suspended"):
                return tid
        return None


# ---------------------------------------------------------------------------
# Scheduler handler
# ---------------------------------------------------------------------------


def scheduled(body_program):
    """Wrap a program with the scheduler handler.

    Returns a DoExpr: WithHandler(scheduler_handler, body_program)
    """
    state = _SchedulerState()

    @do
    def handler(effect, k):
        if isinstance(effect, Spawn):
            tid = state.alloc_task(effect.program)
            result = yield Resume(k, Task(tid))
            return result

        elif isinstance(effect, SchedulerYield):
            tid = effect.task_id
            # Save continuation
            task = state.tasks[tid]
            task["status"] = "suspended"
            task["cont"] = k
            state.ready_queue.append(tid)
            # Switch to next
            return (yield _switch_to_next(state))

        elif isinstance(effect, TaskCompleted):
            tid = effect.task_id
            task = state.tasks[tid]
            result = effect.result
            if hasattr(result, 'is_ok') and result.is_ok():
                task["status"] = "completed"
                task["result"] = result.value
            else:
                task["status"] = "failed"
                task["result"] = result
            # Wake waiters
            _wake_waiters(state, tid)
            # Switch to next
            return (yield _switch_to_next(state))

        elif isinstance(effect, Wait):
            tid = effect.task.task_id
            task = state.tasks[tid]
            if task["status"] == "completed":
                result = yield Resume(k, task["result"])
                return result
            elif task["status"] == "failed":
                # TODO: propagate error
                result = yield Resume(k, None)
                return result
            else:
                # Block
                if tid not in state.waiters:
                    state.waiters[tid] = []
                state.waiters[tid].append(("wait", k))
                return (yield _switch_to_next(state))

        elif isinstance(effect, Gather):
            all_done = all(
                state.tasks[t.task_id]["status"] in ("completed", "failed")
                for t in effect.tasks
            )
            if all_done:
                results = [state.tasks[t.task_id]["result"] for t in effect.tasks]
                result = yield Resume(k, results)
                return result
            else:
                for t in effect.tasks:
                    tid = t.task_id
                    if state.tasks[tid]["status"] not in ("completed", "failed"):
                        if tid not in state.waiters:
                            state.waiters[tid] = []
                        state.waiters[tid].append(("gather", k, effect.tasks))
                        break
                return (yield _switch_to_next(state))

        else:
            yield Pass(effect, k)

    return WithHandler(handler, body_program)


def _switch_to_next(state):
    """Pick next ready task and return DoExpr to run it."""
    tid = state.next_ready()
    if tid is None:
        # No more tasks — return Unit
        from doeff.program import Pure
        return Pure(None)

    task = state.tasks[tid]

    if task["status"] == "pending":
        task["status"] = "running"
        # Wrap program in envelope and start it
        envelope = _envelope(tid)
        return envelope(task["program"])

    elif task["status"] == "suspended":
        task["status"] = "running"
        cont = task["cont"]
        task["cont"] = None
        return Transfer(cont, None)

    from doeff.program import Pure
    return Pure(None)


def _wake_waiters(state, completed_tid):
    """Wake tasks waiting on the completed task."""
    waiters = state.waiters.pop(completed_tid, [])
    for waiter in waiters:
        if waiter[0] == "wait":
            _, waiter_k = waiter
            task = state.tasks[completed_tid]
            # Re-queue the waiter with its continuation
            # Create a synthetic "ready" entry
            wake_tid = state.next_task_id
            state.next_task_id += 1
            state.tasks[wake_tid] = {
                "status": "suspended",
                "cont": waiter_k,
                "program": None,
                "result": None,
            }
            state.ready_queue.append(wake_tid)

        elif waiter[0] == "gather":
            _, waiter_k, gather_tasks = waiter
            all_done = all(
                state.tasks[t.task_id]["status"] in ("completed", "failed")
                for t in gather_tasks
            )
            if all_done:
                wake_tid = state.next_task_id
                state.next_task_id += 1
                state.tasks[wake_tid] = {
                    "status": "suspended",
                    "cont": waiter_k,
                    "program": None,
                    "result": None,
                }
                state.ready_queue.append(wake_tid)
            else:
                # Not all done — re-register
                for t in gather_tasks:
                    tid = t.task_id
                    if state.tasks[tid]["status"] not in ("completed", "failed"):
                        if tid not in state.waiters:
                            state.waiters[tid] = []
                        state.waiters[tid].append(("gather", waiter_k, gather_tasks))
                        break
