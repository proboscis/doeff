"""
Cooperative scheduler — envelope-based, per-yield preemption.

The scheduler is an effect handler that manages tasks. Each task's program
is wrapped in an envelope that inserts SchedulerYield after every step
and catches completion/failure.

Usage:
    from doeff import do, run, WithHandler
    from doeff.scheduler import scheduler, Spawn, Gather

    @do
    def main():
        t1 = yield Spawn(task1())
        t2 = yield Spawn(task2())
        results = yield Gather(t1, t2)
        return results

    run(WithHandler(scheduler(), main()))
"""

from doeff_vm import Callable, EffectBase, Ok, Err
from doeff.program import Expand, Apply, Pure, Resume, Transfer, Pass, Perform


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
# Envelope — wraps a task program to insert SchedulerYield after each step
# ---------------------------------------------------------------------------


def _envelope(gen, task_id):
    """Wrap a generator to insert SchedulerYield between each step.

    Catches both success (StopIteration) and failure (Exception),
    yielding TaskCompleted with Ok/Err result.
    """
    result = None
    try:
        while True:
            do_expr = gen.send(result)
            result = yield do_expr
            _ = yield Perform(SchedulerYield(task_id))
    except StopIteration as e:
        yield Perform(TaskCompleted(task_id, result=Ok(e.value)))
    except Exception as e:
        yield Perform(TaskCompleted(task_id, result=Err(e)))


# ---------------------------------------------------------------------------
# Task state
# ---------------------------------------------------------------------------


_PENDING = "pending"
_RUNNING = "running"
_SUSPENDED = "suspended"
_BLOCKED = "blocked"
_COMPLETED = "completed"
_FAILED = "failed"


class _SchedulerState:
    def __init__(self):
        self.next_task_id = 0
        self.tasks = {}        # task_id → { status, cont, result, ... }
        self.ready_queue = []  # task_ids ready to run
        self.waiters = {}      # task_id → list of (waiter_k, waiter_info)
        self.current_task = None

    def alloc_task(self, program):
        tid = self.next_task_id
        self.next_task_id += 1
        self.tasks[tid] = {
            "status": _PENDING,
            "program": program,
            "cont": None,
            "result": None,
        }
        self.ready_queue.append(tid)
        return tid

    def complete_task(self, task_id, result):
        task = self.tasks[task_id]
        if isinstance(result, Ok.__class__) or (hasattr(result, 'is_ok') and result.is_ok()):
            task["status"] = _COMPLETED
        else:
            task["status"] = _FAILED
        task["result"] = result
        task["cont"] = None

    def suspend_task(self, task_id, cont):
        task = self.tasks[task_id]
        task["status"] = _SUSPENDED
        task["cont"] = cont

    def block_task(self, task_id):
        task = self.tasks[task_id]
        task["status"] = _BLOCKED

    def next_ready(self):
        while self.ready_queue:
            tid = self.ready_queue.pop(0)
            task = self.tasks.get(tid)
            if task and task["status"] in (_PENDING, _SUSPENDED):
                return tid
        return None


# ---------------------------------------------------------------------------
# Scheduler handler factory
# ---------------------------------------------------------------------------


def scheduler():
    """Create a scheduler handler.

    Returns a generator function suitable for WithHandler.
    """
    state = _SchedulerState()

    def _handler(effect, k):
        if isinstance(effect, Spawn):
            # Create task, wrap in envelope
            tid = state.alloc_task(effect.program)
            # Return Task handle to spawner immediately
            result = yield Resume(k, Task(tid))
            return result

        elif isinstance(effect, SchedulerYield):
            tid = effect.task_id
            # Save current task's continuation
            state.suspend_task(tid, k)
            state.ready_queue.append(tid)
            # Switch to next ready task
            yield from _switch_to_next(state)

        elif isinstance(effect, TaskCompleted):
            tid = effect.task_id
            state.complete_task(tid, effect.result)
            # Wake any waiters
            _wake_waiters(state, tid)
            # Switch to next ready task
            yield from _switch_to_next(state)

        elif isinstance(effect, Gather):
            # Check if all tasks are done
            all_done = all(
                state.tasks[t.task_id]["status"] in (_COMPLETED, _FAILED)
                for t in effect.tasks
            )
            if all_done:
                results = _collect_results(state, effect.tasks)
                result = yield Resume(k, results)
                return result
            else:
                # Block: register waiter for each incomplete task
                state.current_task = None
                for t in effect.tasks:
                    tid = t.task_id
                    if state.tasks[tid]["status"] not in (_COMPLETED, _FAILED):
                        if tid not in state.waiters:
                            state.waiters[tid] = []
                        state.waiters[tid].append(("gather", k, effect.tasks))
                yield from _switch_to_next(state)

        elif isinstance(effect, Wait):
            tid = effect.task.task_id
            task = state.tasks[tid]
            if task["status"] in (_COMPLETED, _FAILED):
                result = yield Resume(k, _extract_result(task["result"]))
                return result
            else:
                # Block: register waiter
                if tid not in state.waiters:
                    state.waiters[tid] = []
                state.waiters[tid].append(("wait", k))
                yield from _switch_to_next(state)

        else:
            # Not a scheduler effect — pass to outer handlers
            yield Pass(effect, k)

    return _handler


def _switch_to_next(state):
    """Pick next ready task and Transfer to it."""
    tid = state.next_ready()
    if tid is None:
        # No more tasks — scheduler is done
        return

    task = state.tasks[tid]
    state.current_task = tid

    if task["status"] == _PENDING:
        # Start the task: wrap program in envelope
        # The envelope must run UNDER the scheduler handler (via Perform),
        # not inside the handler's generator. So we yield the envelope
        # as a Perform(TaskRun) that the scheduler will handle by evaluating it.
        task["status"] = _RUNNING
        program = task["program"]

        def make_envelope():
            def task_gen():
                return (yield program)
            return _envelope(task_gen(), tid)

        # Yield the enveloped task as a sub-program.
        # This evaluates within the current handler scope, so SchedulerYield
        # effects from the envelope will dispatch to the scheduler handler.
        enveloped = Expand(Apply(Pure(Callable(make_envelope)), []))
        yield enveloped

    elif task["status"] == _SUSPENDED:
        # Resume suspended task
        task["status"] = _RUNNING
        cont = task["cont"]
        task["cont"] = None
        yield Transfer(cont, None)


def _wake_waiters(state, completed_tid):
    """Wake tasks waiting on the completed task."""
    waiters = state.waiters.pop(completed_tid, [])
    for waiter in waiters:
        if waiter[0] == "wait":
            _, waiter_k = waiter
            task = state.tasks[completed_tid]
            result = _extract_result(task["result"])
            # Re-add waiter to ready queue with its continuation
            # We'll resume it when it gets scheduled
            # For simplicity, resume immediately into ready queue
            state.ready_queue.append(("wake", waiter_k, result))

        elif waiter[0] == "gather":
            _, waiter_k, gather_tasks = waiter
            # Check if ALL tasks in the gather are now done
            all_done = all(
                state.tasks[t.task_id]["status"] in (_COMPLETED, _FAILED)
                for t in gather_tasks
            )
            if all_done:
                results = _collect_results(state, gather_tasks)
                state.ready_queue.append(("wake", waiter_k, results))
            else:
                # Not all done yet — re-register for remaining tasks
                for t in gather_tasks:
                    tid = t.task_id
                    if state.tasks[tid]["status"] not in (_COMPLETED, _FAILED):
                        if tid not in state.waiters:
                            state.waiters[tid] = []
                        state.waiters[tid].append(("gather", waiter_k, gather_tasks))
                        break  # Only register once


def _collect_results(state, tasks):
    """Collect results from completed tasks in order."""
    results = []
    for t in tasks:
        task = state.tasks[t.task_id]
        results.append(_extract_result(task["result"]))
    return results


def _extract_result(result):
    """Extract value from Ok or raise from Err."""
    if hasattr(result, 'is_ok') and result.is_ok():
        return result.value
    elif hasattr(result, 'is_err') and result.is_err():
        raise result.error
    return result
