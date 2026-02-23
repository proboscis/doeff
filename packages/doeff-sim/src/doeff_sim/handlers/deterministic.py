"""Deterministic simulation handler for doeff."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from doeff import Delegate, Resume, WithHandler, do
from doeff.effects.gather import GatherEffect
from doeff.effects.race import RaceEffect
from doeff.effects.spawn import SpawnEffect, task_id_of
from doeff.effects.wait import WaitEffect
from doeff.effects.writer import WriterTellEffect
from doeff.program import ProgramBase
from doeff.types import EffectBase

from doeff_sim._internals.scheduling import FutureCompletion, ScheduledProgram, SimScheduler
from doeff_sim._internals.task_tracking import TaskRecord
from doeff_sim.effects import ForkRunEffect, SetTimeEffect

SIMULATION_START_TIME_ENV_KEY = "simulation_start_time"


try:  # pragma: no cover - exercised when doeff-time is installed in host env
    from doeff_time.effects import DelayEffect as _DelayEffect
    from doeff_time.effects import GetTimeEffect as _GetTimeEffect
    from doeff_time.effects import ScheduleAtEffect as _ScheduleAtEffect
    from doeff_time.effects import WaitUntilEffect as _WaitUntilEffect
except Exception:  # pragma: no cover - local tests use compatibility effects
    _DelayEffect = None
    _WaitUntilEffect = None
    _GetTimeEffect = None
    _ScheduleAtEffect = None


class SimulationTaskError(RuntimeError):
    """Raised when a simulated task fails."""

    def __init__(self, *, task_id: int, cause: BaseException) -> None:
        self.task_id = task_id
        self.cause = cause
        super().__init__(f"Simulation task {task_id} failed: {cause}")
        self.__cause__ = cause


def _effect_is(effect: Any, effect_type: type[Any] | None, name: str) -> bool:
    if effect_type is not None and isinstance(effect, effect_type):
        return True
    return type(effect).__name__ == name


def _extract_numeric_attr(effect: Any, names: tuple[str, ...]) -> float:
    for name in names:
        value = getattr(effect, name, None)
        if isinstance(value, int | float):
            return float(value)
    joined = ", ".join(names)
    raise TypeError(f"{type(effect).__name__} must provide one of ({joined}) as a float")


def _extract_delay_seconds(effect: Any) -> float:
    return _extract_numeric_attr(effect, ("seconds", "delay", "duration", "value"))


def _extract_wait_until_time(effect: Any) -> float:
    return _extract_numeric_attr(effect, ("time", "target_time", "until", "timestamp"))


def _extract_schedule_payload(effect: Any) -> tuple[float, Any]:
    run_at = _extract_numeric_attr(effect, ("time", "at", "target_time", "timestamp"))
    for program_name in ("program", "sub_program", "action", "body"):
        if hasattr(effect, program_name):
            return run_at, getattr(effect, program_name)
    raise TypeError(
        f"{type(effect).__name__} must provide a program field (program/sub_program/action/body)"
    )


def _is_delay_effect(effect: Any) -> bool:
    return _effect_is(effect, _DelayEffect, "DelayEffect")


def _is_wait_until_effect(effect: Any) -> bool:
    return _effect_is(effect, _WaitUntilEffect, "WaitUntilEffect")


def _is_get_time_effect(effect: Any) -> bool:
    return _effect_is(effect, _GetTimeEffect, "GetTimeEffect")


def _is_schedule_at_effect(effect: Any) -> bool:
    return _effect_is(effect, _ScheduleAtEffect, "ScheduleAtEffect")


def deterministic_sim_handler(
    *,
    start_time: float | None = None,
    fail_on_task_error: bool = True,
    warn_on_unawaited_task_failures: bool = True,
    log_formatter: Callable[[float, Any], str] | None = None,
):
    """Create a deterministic simulation handler.

    Intercepted effects:
    - doeff-time: Delay, WaitUntil, GetTime, ScheduleAt
    - doeff core concurrency: Spawn, Wait, Race, Gather
    - doeff-sim: SetTime, ForkRun

    All other effects are delegated with ``Delegate()``.
    """

    scheduler: SimScheduler = SimScheduler(start_time=start_time)

    @do
    def run_due_scheduled_programs(target_time: float):
        final_time: float = max(target_time, scheduler.clock.current_time)
        while True:
            threshold: float = max(target_time, scheduler.clock.current_time)
            due: ScheduledProgram | None = scheduler.pop_due_program(threshold)
            if due is None:
                break

            scheduled_time: float = due.run_at
            scheduler.clock.set_time(scheduled_time)

            try:
                yield due.program
            except BaseException as exc:
                wrapped: SimulationTaskError = SimulationTaskError(task_id=-1, cause=exc)
                if fail_on_task_error:
                    raise wrapped

            final_time = max(final_time, scheduler.clock.current_time)
            scheduler.clock.set_time(final_time)

        scheduler.clock.set_time(final_time)
        return None

    @do
    def run_task(task_id: int):
        completion: FutureCompletion | None = scheduler.completion_for(task_id)
        if completion is not None:
            error: BaseException | None = completion.error
            if error is not None:
                raise error
            return completion.value

        record: TaskRecord = scheduler.task_registry.get(task_id)

        if record.status == "completed":
            return record.result

        if record.status == "failed":
            if record.error is None:
                raise RuntimeError(f"Task {task_id} is failed without an error")
            raise record.error

        if record.status == "running":
            raise SimulationTaskError(task_id=task_id, cause=RuntimeError("task re-entry detected"))

        scheduler.mark_running(task_id)

        try:
            value: Any = yield record.program
        except BaseException as exc:
            wrapped: SimulationTaskError = SimulationTaskError(task_id=task_id, cause=exc)
            scheduler.mark_failed(task_id, wrapped)
            raise wrapped

        scheduler.mark_completed(task_id, value)
        return value

    @do
    def wait_task(task_id: int):
        scheduler.register_waiter(task_id)
        record: TaskRecord = scheduler.task_registry.get(task_id)
        record.awaited = True
        return (yield run_task(task_id))

    def gather_item_supported(item: Any) -> bool:
        item_task_id: int | None = task_id_of(item)
        if item_task_id is not None and scheduler.has_task(item_task_id):
            return True
        return isinstance(item, ProgramBase | EffectBase)

    def maybe_format_writer_message(effect: Any) -> WriterTellEffect | None:
        if log_formatter is None:
            return None
        if not isinstance(effect, WriterTellEffect):
            return None
        formatted: str = log_formatter(scheduler.clock.current_time, effect.message)
        return WriterTellEffect(formatted)

    def handler(effect: Any, k: Any):
        replacement: WriterTellEffect | None = maybe_format_writer_message(effect)
        if replacement is not None:
            formatted_result: Any = yield replacement
            return (yield Resume(k, formatted_result))

        if _is_delay_effect(effect):
            delay_seconds: float = _extract_delay_seconds(effect)
            target_time: float = scheduler.clock.current_time + delay_seconds
            scheduler.clock.set_time(target_time)
            yield run_due_scheduled_programs(target_time)
            return (yield Resume(k, None))

        if _is_wait_until_effect(effect):
            requested_time: float = _extract_wait_until_time(effect)
            target_time: float = max(scheduler.clock.current_time, requested_time)
            scheduler.clock.set_time(target_time)
            yield run_due_scheduled_programs(target_time)
            return (yield Resume(k, None))

        if _is_get_time_effect(effect):
            now: float = scheduler.clock.current_time
            return (yield Resume(k, now))

        if _is_schedule_at_effect(effect):
            schedule_payload: tuple[float, Any] = _extract_schedule_payload(effect)
            run_at: float = schedule_payload[0]
            program: Any = schedule_payload[1]
            scheduler.schedule_program(run_at=run_at, program=program)
            if run_at <= scheduler.clock.current_time:
                yield run_due_scheduled_programs(scheduler.clock.current_time)
            return (yield Resume(k, None))

        if isinstance(effect, SetTimeEffect):
            target_time: float = effect.time
            scheduler.clock.set_time(target_time)
            yield run_due_scheduled_programs(target_time)
            return (yield Resume(k, None))

        if isinstance(effect, ForkRunEffect):
            fork_start: float = (
                effect.start_time if effect.start_time is not None else scheduler.clock.current_time
            )
            fork_handler: Callable[[Any, Any], Any] = deterministic_sim_handler(
                start_time=fork_start,
                fail_on_task_error=fail_on_task_error,
                warn_on_unawaited_task_failures=warn_on_unawaited_task_failures,
                log_formatter=log_formatter,
            )
            value: Any = yield WithHandler(fork_handler, effect.program)
            return (yield Resume(k, value))

        if isinstance(effect, SpawnEffect):
            record: TaskRecord = scheduler.create_task(effect.program)
            return (yield Resume(k, record.handle))

        if isinstance(effect, WaitEffect):
            target_task_id: int | None = task_id_of(effect.future)
            if target_task_id is not None and scheduler.has_task(target_task_id):
                value: Any = yield wait_task(target_task_id)
                return (yield Resume(k, value))
            yield Delegate()
            return

        if isinstance(effect, GatherEffect):
            items: tuple[Any, ...] = tuple(effect.items)
            if not all(gather_item_supported(item) for item in items):
                yield Delegate()
                return

            values: list[Any] = []
            for item in items:
                target_task_id: int | None = task_id_of(item)
                if target_task_id is not None and scheduler.has_task(target_task_id):
                    values.append((yield wait_task(target_task_id)))
                    continue
                values.append((yield item))

            return (yield Resume(k, values))

        if isinstance(effect, RaceEffect):
            futures: tuple[Any, ...] = tuple(effect.futures)
            if not futures:
                raise ValueError("Race requires at least one future")

            task_ids: list[int] = []
            for future in futures:
                task_id: int | None = task_id_of(future)
                if task_id is None or not scheduler.has_task(task_id):
                    yield Delegate()
                    return
                task_ids.append(task_id)

            ordered_task_ids: list[int] = scheduler.order_task_ids(task_ids)
            completed: list[FutureCompletion] = []
            for task_id in ordered_task_ids:
                completion: FutureCompletion | None = scheduler.completion_for(task_id)
                if completion is not None:
                    completed.append(completion)

            if completed:
                completed.sort(
                    key=lambda record: (
                        record.completed_at,
                        record.sequence,
                        scheduler.task_order(record.task_id),
                    )
                )
                winner_id: int = completed[0].task_id
                winner_value: Any = yield wait_task(winner_id)
                return (yield Resume(k, winner_value))

            winner_id: int = ordered_task_ids[0]
            winner_value: Any = yield wait_task(winner_id)
            return (yield Resume(k, winner_value))

        yield Delegate()

    return handler


__all__ = [
    "SIMULATION_START_TIME_ENV_KEY",
    "SimulationTaskError",
    "deterministic_sim_handler",
]
