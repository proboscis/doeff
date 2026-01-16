"""The CESK machine step function.

The step function is pure: (state, task_id, handlers) -> StepOutput

Pattern: Handler → Action → step() → Event → Runtime

step() orchestrates:
1. Get task from state
2. Based on control type:
   - Value/Error with empty K: task done/failed
   - Value/Error with K: process frame
   - EffectControl: call handler, process actions
   - ProgramControl: start program generator
3. Return new state and events for runtime
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from doeff._vendor import NOTHING, Err, FrozenDict, Ok, Some
from doeff._types_internal import EffectBase
from doeff.cesk.types import Environment, FutureId, Store, TaskId
from doeff.cesk.frames import (
    Continue,
    Frame,
    GatherFrame,
    InterceptFrame,
    Kontinuation,
    ListenFrame,
    LocalFrame,
    PopAndContinue,
    Propagate,
    ReturnFrame,
    SafeFrame,
)
from doeff.cesk.state import (
    CESKState,
    Condition,
    Control,
    EffectControl,
    Error,
    FutureState,
    GatherCondition,
    ProgramControl,
    RaceCondition,
    TaskState,
    TaskStatus,
    TimeCondition,
    Value,
    WaitingOn,
)
from doeff.cesk.actions import (
    Action,
    AwaitExternal,
    CancelTasks,
    CreateTask,
    CreateTasks,
    ModifyStore,
    PerformIO,
    RaceOnFutures,
    Resume,
    ResumeError,
    ResumeWithStore,
    RunProgram,
    WaitForDuration,
    WaitOnFuture,
    WaitOnFutures,
    WaitUntilTime,
)
from doeff.cesk.events import (
    AwaitRequested,
    Event,
    FutureRejected,
    FutureResolved,
    IORequested,
    TaskBlocked,
    TaskCancelled,
    TaskCreated,
    TaskDone,
    TaskFailed,
    TaskRacing,
    TaskReady,
    TasksCreated,
    TaskWaitingForDuration,
    TaskWaitingOnFuture,
    TaskWaitingOnFutures,
    TaskWaitingUntilTime,
    TaskYielded,
)
from doeff.cesk.handlers import HandlerContext, HandlerRegistry, HandlerResult

if TYPE_CHECKING:
    from doeff.program import Program


# ============================================================================
# Step Output
# ============================================================================


@dataclass
class StepOutput:
    """Result from a single step.

    Contains the new state and any events emitted during the step.
    Events are used by the runtime to coordinate execution.
    """

    state: CESKState
    events: tuple[Event, ...]


# ============================================================================
# Exceptions
# ============================================================================


class InterpreterInvariantError(Exception):
    """Internal interpreter error - invariant violated."""

    pass


class UnhandledEffectError(Exception):
    """No handler registered for effect."""

    pass


# ============================================================================
# Step Function
# ============================================================================


def step(
    state: CESKState,
    task_id: TaskId,
    handlers: HandlerRegistry,
) -> StepOutput:
    """Execute one step of the CESK machine for a task.

    The step function is pure - it takes state and returns new state plus events.
    It does not perform I/O or side effects. The runtime is responsible for
    executing I/O and feeding results back.

    Args:
        state: Current CESK machine state
        task_id: ID of task to step
        handlers: Handler registry mapping effect types to handler functions

    Returns:
        StepOutput with new state and events for runtime

    Raises:
        InterpreterInvariantError: If interpreter reaches invalid state
        KeyError: If task_id not found in state
    """
    # Get task
    if task_id not in state.tasks:
        raise KeyError(f"Task {task_id} not found in state")

    task = state.tasks[task_id]
    C, E, K = task.C, task.E, task.K
    S = state.S

    # === Terminal: Value with empty K ===
    if isinstance(C, Value) and not K:
        return _task_completed(state, task, C.v)

    # === Terminal: Error with empty K ===
    if isinstance(C, Error) and not K:
        return _task_failed(state, task, C.ex, C.captured_traceback)

    # === Effect: look up handler and process actions ===
    if isinstance(C, EffectControl):
        return _handle_effect(state, task, C.effect, handlers)

    # === Program: start the generator ===
    if isinstance(C, ProgramControl):
        return _start_program(state, task, C.program)

    # === Value with K: process frame ===
    if isinstance(C, Value) and K:
        return _process_value_frame(state, task, C.v)

    # === Error with K: process frame ===
    if isinstance(C, Error) and K:
        return _process_error_frame(state, task, C.ex, C.captured_traceback)

    # Should never reach here
    head_desc = type(K[0]).__name__ if K else "empty"
    raise InterpreterInvariantError(
        f"Unhandled state: C={type(C).__name__}, K head={head_desc}"
    )


# ============================================================================
# Terminal States
# ============================================================================


def _task_completed(state: CESKState, task: TaskState, value: Any) -> StepOutput:
    """Task completed successfully with a value."""
    task_id = task.task_id

    # Update task status
    done_task = TaskState(
        task_id=task_id,
        C=Value(value),
        E=task.E,
        K=[],
        status=TaskStatus.DONE,
        condition=None,
        future_id=task.future_id,
    )
    new_state = state.update_task(done_task)

    events: list[Event] = [TaskDone(task_id=task_id, value=value, store=state.S)]

    # Resolve associated future if present
    if task.future_id is not None:
        future = state.futures.get(task.future_id)
        if future is not None:
            resolved_future = future.with_value(value)
            new_state = new_state.update_future(resolved_future)
            events.append(FutureResolved(future_id=task.future_id, value=value))

            # Wake up any tasks waiting on this future
            for waiter_id in future.waiters:
                events.append(TaskReady(task_id=waiter_id))

    return StepOutput(state=new_state, events=tuple(events))


def _task_failed(
    state: CESKState,
    task: TaskState,
    error: BaseException,
    captured_traceback: Any | None,
) -> StepOutput:
    """Task failed with an exception."""
    task_id = task.task_id

    # Update task status
    failed_task = TaskState(
        task_id=task_id,
        C=Error(error, captured_traceback),
        E=task.E,
        K=[],
        status=TaskStatus.FAILED,
        condition=None,
        future_id=task.future_id,
    )
    new_state = state.update_task(failed_task)

    events: list[Event] = [
        TaskFailed(
            task_id=task_id,
            error=error,
            store=state.S,
            captured_traceback=captured_traceback,
        )
    ]

    # Reject associated future if present
    if task.future_id is not None:
        future = state.futures.get(task.future_id)
        if future is not None:
            rejected_future = future.with_error(error)
            new_state = new_state.update_future(rejected_future)
            events.append(
                FutureRejected(
                    future_id=task.future_id,
                    error=error,
                    captured_traceback=captured_traceback,
                )
            )

            # Wake up any tasks waiting on this future
            for waiter_id in future.waiters:
                events.append(TaskReady(task_id=waiter_id))

    return StepOutput(state=new_state, events=tuple(events))


# ============================================================================
# Effect Handling
# ============================================================================


def _handle_effect(
    state: CESKState,
    task: TaskState,
    effect: EffectBase,
    handlers: HandlerRegistry,
) -> StepOutput:
    """Handle an effect by looking up and calling its handler."""
    task_id = task.task_id
    effect_type = type(effect)

    # Look up handler
    handler = handlers.get(effect_type)
    if handler is None:
        # No handler - raise unhandled effect error
        from doeff.cesk_traceback import capture_traceback_safe

        error = UnhandledEffectError(f"No handler for {effect_type.__name__}")
        captured = capture_traceback_safe(task.K, error)
        new_task = task.with_control(Error(error, captured_traceback=captured))
        new_state = state.update_task(new_task)
        return StepOutput(state=new_state, events=())

    # Create handler context
    ctx = HandlerContext(
        task_id=task_id,
        env=task.E,
        store=state.S,
        kontinuation=task.K,
    )

    # Call handler
    try:
        result = handler(effect, ctx)
    except Exception as ex:
        from doeff.cesk_traceback import capture_traceback_safe

        captured = capture_traceback_safe(task.K, ex)
        new_task = task.with_control(Error(ex, captured_traceback=captured))
        new_state = state.update_task(new_task)
        return StepOutput(state=new_state, events=())

    # Process actions from handler
    return _process_actions(state, task, result.actions)


def _process_actions(
    state: CESKState,
    task: TaskState,
    actions: tuple[Action, ...],
) -> StepOutput:
    """Process actions returned by a handler."""
    task_id = task.task_id
    events: list[Event] = []
    new_state = state
    new_task = task

    for action in actions:
        if isinstance(action, Resume):
            # Resume with value - continue computation
            new_task = new_task.with_control(Value(action.value))

        elif isinstance(action, ResumeError):
            # Resume with error
            new_task = new_task.with_control(Error(action.error))

        elif isinstance(action, ResumeWithStore):
            # Resume with value and update store
            new_task = new_task.with_control(Value(action.value))
            new_state = new_state.with_store(action.store)

        elif isinstance(action, RunProgram):
            # Run a sub-program (control flow effect)
            env = action.env if action.env is not None else new_task.E
            new_task = TaskState(
                task_id=task_id,
                C=ProgramControl(action.program),
                E=env,
                K=new_task.K,
                status=new_task.status,
                condition=new_task.condition,
                future_id=new_task.future_id,
            )

        elif isinstance(action, ModifyStore):
            # Update store
            new_store = dict(new_state.S)
            new_store.update(action.updates)
            new_state = new_state.with_store(new_store)

        elif isinstance(action, CreateTask):
            # Spawn a new task
            new_state, new_task_id = new_state.allocate_task_id()
            new_state, future_id = new_state.allocate_future_id()

            child_task = TaskState(
                task_id=new_task_id,
                C=ProgramControl(action.program),
                E=action.env,
                K=[],
                status=TaskStatus.RUNNING,
                future_id=future_id,
            )
            new_state = new_state.add_task(child_task)

            child_future = FutureState(
                future_id=future_id,
                producer_task=new_task_id,
            )
            new_state = new_state.add_future(child_future)

            events.append(
                TaskCreated(
                    task_id=new_task_id,
                    future_id=future_id,
                    program=action.program,
                    env=action.env,
                    store=action.store_snapshot,
                )
            )

            # Resume parent with the future_id
            new_task = new_task.with_control(Value(future_id))

        elif isinstance(action, CreateTasks):
            # Spawn multiple tasks (for gather/race)
            created_tasks: list[TaskCreated] = []
            future_ids: list[FutureId] = []

            for program in action.programs:
                new_state, new_task_id = new_state.allocate_task_id()
                new_state, future_id = new_state.allocate_future_id()

                child_task = TaskState(
                    task_id=new_task_id,
                    C=ProgramControl(program),
                    E=action.env,
                    K=[],
                    status=TaskStatus.RUNNING,
                    future_id=future_id,
                )
                new_state = new_state.add_task(child_task)

                child_future = FutureState(
                    future_id=future_id,
                    producer_task=new_task_id,
                )
                new_state = new_state.add_future(child_future)

                created_tasks.append(
                    TaskCreated(
                        task_id=new_task_id,
                        future_id=future_id,
                        program=program,
                        env=action.env,
                    )
                )
                future_ids.append(future_id)

            events.append(TasksCreated(tasks=tuple(created_tasks)))
            # Resume parent with the future_ids
            new_task = new_task.with_control(Value(tuple(future_ids)))

        elif isinstance(action, CancelTasks):
            # Cancel tasks
            for cancel_task_id in action.task_ids:
                if cancel_task_id in new_state.tasks:
                    cancelled_task = new_state.tasks[cancel_task_id]
                    cancelled_task = TaskState(
                        task_id=cancel_task_id,
                        C=cancelled_task.C,
                        E=cancelled_task.E,
                        K=cancelled_task.K,
                        status=TaskStatus.CANCELLED,
                        condition=None,
                        future_id=cancelled_task.future_id,
                    )
                    new_state = new_state.update_task(cancelled_task)
                    events.append(TaskCancelled(task_id=cancel_task_id))

        elif isinstance(action, WaitOnFuture):
            # Wait for a single future
            future_id = action.future_id
            future = new_state.futures.get(future_id)

            if future is not None and future.is_done:
                # Future already done - resume immediately
                if future.error is not None:
                    new_task = new_task.with_control(Error(future.error))
                else:
                    new_task = new_task.with_control(Value(future.value))
            else:
                # Wait for future
                new_task = new_task.with_status(
                    TaskStatus.WAITING, WaitingOn(future_id)
                )
                # Register as waiter
                if future is not None:
                    updated_future = future.with_waiter(task_id)
                    new_state = new_state.update_future(updated_future)
                events.append(
                    TaskWaitingOnFuture(task_id=task_id, future_id=future_id)
                )

        elif isinstance(action, WaitOnFutures):
            # Wait for multiple futures (gather)
            future_ids = action.future_ids
            new_task = new_task.with_status(
                TaskStatus.WAITING, GatherCondition(future_ids)
            )
            # Register as waiter on all futures
            for fid in future_ids:
                future = new_state.futures.get(fid)
                if future is not None:
                    updated_future = future.with_waiter(task_id)
                    new_state = new_state.update_future(updated_future)
            events.append(
                TaskWaitingOnFutures(task_id=task_id, future_ids=future_ids)
            )

        elif isinstance(action, RaceOnFutures):
            # Wait for first of multiple futures (race)
            future_ids = action.future_ids
            new_task = new_task.with_status(
                TaskStatus.WAITING, RaceCondition(future_ids)
            )
            # Register as waiter on all futures
            for fid in future_ids:
                future = new_state.futures.get(fid)
                if future is not None:
                    updated_future = future.with_waiter(task_id)
                    new_state = new_state.update_future(updated_future)
            events.append(TaskRacing(task_id=task_id, future_ids=future_ids))

        elif isinstance(action, WaitUntilTime):
            # Wait until specific time
            new_task = new_task.with_status(
                TaskStatus.WAITING, TimeCondition(action.target_time)
            )
            events.append(
                TaskWaitingUntilTime(task_id=task_id, target_time=action.target_time)
            )

        elif isinstance(action, WaitForDuration):
            # Wait for duration
            events.append(
                TaskWaitingForDuration(task_id=task_id, seconds=action.seconds)
            )
            # Task stays RUNNING - runtime handles the wait
            new_task = new_task.with_status(TaskStatus.BLOCKED)

        elif isinstance(action, PerformIO):
            # External I/O needed
            new_task = new_task.with_status(TaskStatus.BLOCKED)
            events.append(IORequested(task_id=task_id, operation=action.operation))

        elif isinstance(action, AwaitExternal):
            # External await needed
            new_task = new_task.with_status(TaskStatus.BLOCKED)
            events.append(
                AwaitRequested(task_id=task_id, awaitable=action.awaitable)
            )

        else:
            raise InterpreterInvariantError(f"Unknown action type: {type(action)}")

    new_state = new_state.update_task(new_task)
    return StepOutput(state=new_state, events=tuple(events))


# ============================================================================
# Program Execution
# ============================================================================


def _start_program(
    state: CESKState,
    task: TaskState,
    program: Program,
) -> StepOutput:
    """Start executing a program (generator)."""
    from doeff.cesk_traceback import capture_traceback_safe, pre_capture_generator
    from doeff.program import KleisliProgramCall, ProgramBase

    task_id = task.task_id
    E = task.E
    K = task.K

    pre_captured = None
    try:
        gen = _to_generator(program)
        program_call = program if isinstance(program, KleisliProgramCall) else None
        pre_captured = pre_capture_generator(gen, is_resumed=False, program_call=program_call)
        item = next(gen)

        if isinstance(item, EffectBase):
            control: Control = EffectControl(item)
        elif isinstance(item, ProgramBase):
            control = ProgramControl(item)
        else:
            error = InterpreterInvariantError(
                f"Program yielded unexpected type: {type(item).__name__}. "
                "Programs must yield Effect or Program instances only."
            )
            new_task = task.with_control(Error(error))
            new_state = state.update_task(new_task)
            return StepOutput(state=new_state, events=())

        new_task = TaskState(
            task_id=task_id,
            C=control,
            E=E,
            K=[ReturnFrame(gen, E, program_call=program_call)] + K,
            status=task.status,
            condition=task.condition,
            future_id=task.future_id,
        )
        new_state = state.update_task(new_task)
        return StepOutput(state=new_state, events=())

    except StopIteration as e:
        # Program returned immediately
        new_task = task.with_control(Value(e.value))
        new_state = state.update_task(new_task)
        return StepOutput(state=new_state, events=())

    except Exception as ex:
        captured = capture_traceback_safe(K, ex, pre_captured=pre_captured)
        new_task = task.with_control(Error(ex, captured_traceback=captured))
        new_state = state.update_task(new_task)
        return StepOutput(state=new_state, events=())


def _to_generator(program: Program) -> Any:
    """Convert program to generator."""
    from doeff.program import KleisliProgramCall, ProgramBase

    # Handle KleisliProgramCall specially
    if isinstance(program, KleisliProgramCall):
        return program.to_generator()

    # Handle other ProgramBase types
    if isinstance(program, ProgramBase):
        to_gen = getattr(program, "to_generator", None)
        if callable(to_gen):
            return to_gen()
        # Fallback to run() method
        return program.run()

    # Already a generator
    return program


# ============================================================================
# Frame Processing
# ============================================================================


def _process_value_frame(
    state: CESKState,
    task: TaskState,
    value: Any,
) -> StepOutput:
    """Process a value through the top frame of the stack."""
    K = task.K
    if not K:
        raise InterpreterInvariantError("Empty K stack with Value control")

    frame = K[0]
    K_rest = K[1:]
    E = task.E
    S = state.S

    if isinstance(frame, ReturnFrame):
        return _process_return_frame_value(state, task, frame, value, K_rest)

    # Use frame's on_value method
    result = frame.on_value(value, E, S)
    return _apply_frame_result(state, task, result, frame, K_rest)


def _process_error_frame(
    state: CESKState,
    task: TaskState,
    error: BaseException,
    captured_traceback: Any | None,
) -> StepOutput:
    """Process an error through the top frame of the stack."""
    K = task.K
    if not K:
        raise InterpreterInvariantError("Empty K stack with Error control")

    frame = K[0]
    K_rest = K[1:]
    E = task.E
    S = state.S

    if isinstance(frame, ReturnFrame):
        return _process_return_frame_error(state, task, frame, error, captured_traceback, K_rest)

    # Use frame's on_error method
    result = frame.on_error(error, captured_traceback, E, S)
    return _apply_frame_result(state, task, result, frame, K_rest)


def _process_return_frame_value(
    state: CESKState,
    task: TaskState,
    frame: ReturnFrame,
    value: Any,
    K_rest: Kontinuation,
) -> StepOutput:
    """Send value to generator in ReturnFrame."""
    from doeff.cesk_traceback import capture_traceback_safe, pre_capture_generator
    from doeff.program import ProgramBase

    pre_captured = pre_capture_generator(
        frame.generator, is_resumed=True, program_call=frame.program_call
    )

    try:
        item = frame.generator.send(value)

        if isinstance(item, EffectBase):
            control: Control = EffectControl(item)
        elif isinstance(item, ProgramBase):
            control = ProgramControl(item)
        else:
            error = InterpreterInvariantError(
                f"Program yielded unexpected type: {type(item).__name__}. "
                "Programs must yield Effect or Program instances only."
            )
            new_task = TaskState(
                task_id=task.task_id,
                C=Error(error),
                E=frame.saved_env,
                K=K_rest,
                status=task.status,
                condition=task.condition,
                future_id=task.future_id,
            )
            new_state = state.update_task(new_task)
            return StepOutput(state=new_state, events=())

        new_task = TaskState(
            task_id=task.task_id,
            C=control,
            E=frame.saved_env,
            K=[ReturnFrame(frame.generator, frame.saved_env, program_call=frame.program_call)] + K_rest,
            status=task.status,
            condition=task.condition,
            future_id=task.future_id,
        )
        new_state = state.update_task(new_task)
        return StepOutput(state=new_state, events=())

    except StopIteration as e:
        new_task = TaskState(
            task_id=task.task_id,
            C=Value(e.value),
            E=frame.saved_env,
            K=K_rest,
            status=task.status,
            condition=task.condition,
            future_id=task.future_id,
        )
        new_state = state.update_task(new_task)
        return StepOutput(state=new_state, events=())

    except Exception as ex:
        captured = capture_traceback_safe(K_rest, ex, pre_captured=pre_captured)
        new_task = TaskState(
            task_id=task.task_id,
            C=Error(ex, captured_traceback=captured),
            E=frame.saved_env,
            K=K_rest,
            status=task.status,
            condition=task.condition,
            future_id=task.future_id,
        )
        new_state = state.update_task(new_task)
        return StepOutput(state=new_state, events=())


def _process_return_frame_error(
    state: CESKState,
    task: TaskState,
    frame: ReturnFrame,
    error: BaseException,
    captured_traceback: Any | None,
    K_rest: Kontinuation,
) -> StepOutput:
    """Throw error into generator in ReturnFrame."""
    from doeff.cesk_traceback import capture_traceback_safe, pre_capture_generator
    from doeff.program import ProgramBase

    pre_captured = pre_capture_generator(
        frame.generator, is_resumed=True, program_call=frame.program_call
    )

    try:
        item = frame.generator.throw(error)

        if isinstance(item, EffectBase):
            control: Control = EffectControl(item)
        elif isinstance(item, ProgramBase):
            control = ProgramControl(item)
        else:
            inv_error = InterpreterInvariantError(
                f"Program yielded unexpected type: {type(item).__name__}. "
                "Programs must yield Effect or Program instances only."
            )
            new_task = TaskState(
                task_id=task.task_id,
                C=Error(inv_error),
                E=frame.saved_env,
                K=K_rest,
                status=task.status,
                condition=task.condition,
                future_id=task.future_id,
            )
            new_state = state.update_task(new_task)
            return StepOutput(state=new_state, events=())

        new_task = TaskState(
            task_id=task.task_id,
            C=control,
            E=frame.saved_env,
            K=[ReturnFrame(frame.generator, frame.saved_env, program_call=frame.program_call)] + K_rest,
            status=task.status,
            condition=task.condition,
            future_id=task.future_id,
        )
        new_state = state.update_task(new_task)
        return StepOutput(state=new_state, events=())

    except StopIteration as e:
        new_task = TaskState(
            task_id=task.task_id,
            C=Value(e.value),
            E=frame.saved_env,
            K=K_rest,
            status=task.status,
            condition=task.condition,
            future_id=task.future_id,
        )
        new_state = state.update_task(new_task)
        return StepOutput(state=new_state, events=())

    except Exception as propagated:
        if propagated is error:
            # Same error re-raised - preserve traceback
            new_task = TaskState(
                task_id=task.task_id,
                C=Error(propagated, captured_traceback=captured_traceback),
                E=frame.saved_env,
                K=K_rest,
                status=task.status,
                condition=task.condition,
                future_id=task.future_id,
            )
        else:
            # New error - capture new traceback
            captured = capture_traceback_safe(K_rest, propagated, pre_captured=pre_captured)
            new_task = TaskState(
                task_id=task.task_id,
                C=Error(propagated, captured_traceback=captured),
                E=frame.saved_env,
                K=K_rest,
                status=task.status,
                condition=task.condition,
                future_id=task.future_id,
            )
        new_state = state.update_task(new_task)
        return StepOutput(state=new_state, events=())


def _apply_frame_result(
    state: CESKState,
    task: TaskState,
    result: Any,
    frame: Frame,
    K_rest: Kontinuation,
) -> StepOutput:
    """Apply a FrameResult to produce new state."""
    from doeff.cesk.frames import Continue, PopAndContinue, Propagate

    if isinstance(result, PopAndContinue):
        # Pop frame and continue with control
        control = _value_to_control(result.control)
        new_task = TaskState(
            task_id=task.task_id,
            C=control,
            E=result.env,
            K=K_rest,
            status=task.status,
            condition=task.condition,
            future_id=task.future_id,
        )
        new_state = state.update_task(new_task)

        # Process any actions
        events: list[Event] = []
        for action in result.actions:
            # Actions from frames would need processing
            pass

        return StepOutput(state=new_state, events=tuple(events))

    elif isinstance(result, Continue):
        # Continue with updated frame on stack
        control = _value_to_control(result.control)

        # For GatherFrame, we need to update the frame with new state
        if isinstance(frame, GatherFrame) and isinstance(result.control, ProgramControl):
            # GatherFrame returns Continue when more programs to run
            # Update frame with new remaining/collected
            if frame.remaining_programs:
                next_prog, *rest = frame.remaining_programs
                new_frame = GatherFrame(
                    remaining_programs=rest,
                    collected_results=frame.collected_results + [task.C.v if isinstance(task.C, Value) else None],
                    saved_env=frame.saved_env,
                )
                new_K = [new_frame] + K_rest
            else:
                new_K = K_rest
        else:
            new_K = [frame] + K_rest if not isinstance(result, PopAndContinue) else K_rest

        new_task = TaskState(
            task_id=task.task_id,
            C=control,
            E=result.env,
            K=new_K,
            status=task.status,
            condition=task.condition,
            future_id=task.future_id,
        )
        new_state = state.update_task(new_task)
        return StepOutput(state=new_state, events=())

    elif isinstance(result, Propagate):
        # Propagate error up the stack
        new_task = TaskState(
            task_id=task.task_id,
            C=Error(result.error, captured_traceback=result.captured_traceback),
            E=task.E,
            K=K_rest,
            status=task.status,
            condition=task.condition,
            future_id=task.future_id,
        )
        new_state = state.update_task(new_task)
        return StepOutput(state=new_state, events=())

    else:
        raise InterpreterInvariantError(f"Unknown frame result type: {type(result)}")


def _value_to_control(value: Any) -> Control:
    """Convert a value to appropriate Control type."""
    from doeff.cesk.state import ProgramControl, Value as ValueControl
    from doeff.program import ProgramBase

    if isinstance(value, (Value, Error, EffectControl, ProgramControl)):
        return value
    # Wrap raw values in Value control
    return ValueControl(value)


# ============================================================================
# Resume Helpers (for runtime)
# ============================================================================


def resume_task(
    state: CESKState,
    task_id: TaskId,
    value: Any,
) -> CESKState:
    """Resume a blocked task with a value.

    Used by runtime after I/O completes.
    """
    if task_id not in state.tasks:
        raise KeyError(f"Task {task_id} not found")

    task = state.tasks[task_id]
    new_task = TaskState(
        task_id=task_id,
        C=Value(value),
        E=task.E,
        K=task.K,
        status=TaskStatus.RUNNING,
        condition=None,
        future_id=task.future_id,
    )
    return state.update_task(new_task)


def resume_task_error(
    state: CESKState,
    task_id: TaskId,
    error: BaseException,
    captured_traceback: Any | None = None,
) -> CESKState:
    """Resume a blocked task with an error.

    Used by runtime when I/O fails.
    """
    if task_id not in state.tasks:
        raise KeyError(f"Task {task_id} not found")

    task = state.tasks[task_id]
    new_task = TaskState(
        task_id=task_id,
        C=Error(error, captured_traceback=captured_traceback),
        E=task.E,
        K=task.K,
        status=TaskStatus.RUNNING,
        condition=None,
        future_id=task.future_id,
    )
    return state.update_task(new_task)


def resume_task_with_store(
    state: CESKState,
    task_id: TaskId,
    value: Any,
    new_store: Store,
) -> CESKState:
    """Resume a blocked task with a value and updated store.

    Used by runtime when I/O completes and updates store.
    """
    if task_id not in state.tasks:
        raise KeyError(f"Task {task_id} not found")

    task = state.tasks[task_id]
    new_task = TaskState(
        task_id=task_id,
        C=Value(value),
        E=task.E,
        K=task.K,
        status=TaskStatus.RUNNING,
        condition=None,
        future_id=task.future_id,
    )
    return state.update_task(new_task).with_store(new_store)


__all__ = [
    "StepOutput",
    "InterpreterInvariantError",
    "UnhandledEffectError",
    "step",
    "resume_task",
    "resume_task_error",
    "resume_task_with_store",
]
