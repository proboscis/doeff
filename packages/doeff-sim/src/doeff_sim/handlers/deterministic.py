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

from doeff_sim._internals.scheduling import TimeQueue
from doeff_sim._internals.sim_clock import SimClock
from doeff_sim._internals.task_tracking import TaskRegistry
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

    clock = SimClock(current_time=float(start_time) if start_time is not None else 0.0)
    time_queue = TimeQueue()
    task_registry = TaskRegistry()
    sequence_counter = 0

    def next_sequence() -> int:
        nonlocal sequence_counter
        sequence_counter += 1
        return sequence_counter

    @do
    def run_due_scheduled_programs():
        while True:
            due = time_queue.pop_due(clock.current_time)
            if due is None:
                return None

            try:
                yield due.program
            except BaseException as exc:
                wrapped = SimulationTaskError(task_id=-1, cause=exc)
                if fail_on_task_error:
                    raise wrapped

        return None

    @do
    def run_task(task_id: int):
        record = task_registry.get(task_id)

        if record.status == "completed":
            return record.result

        if record.status == "failed":
            if record.error is None:
                raise RuntimeError(f"Task {task_id} is failed without an error")
            raise record.error

        if record.status == "running":
            raise SimulationTaskError(task_id=task_id, cause=RuntimeError("task re-entry detected"))

        task_registry.mark_running(task_id)

        try:
            value = yield record.program
        except BaseException as exc:
            wrapped = SimulationTaskError(task_id=task_id, cause=exc)
            task_registry.mark_failed(task_id, wrapped)
            raise wrapped

        task_registry.mark_completed(task_id, value)
        return value

    @do
    def wait_task(task_id: int):
        record = task_registry.get(task_id)
        record.awaited = True
        return (yield run_task(task_id))

    def gather_item_supported(item: Any) -> bool:
        item_task_id = task_id_of(item)
        if item_task_id is not None and task_registry.has_task(item_task_id):
            return True
        return isinstance(item, ProgramBase | EffectBase)

    def maybe_format_writer_message(effect: Any):
        if log_formatter is None:
            return None
        if not isinstance(effect, WriterTellEffect):
            return None
        return WriterTellEffect(log_formatter(clock.current_time, effect.message))

    def handler(effect, k):
        replacement = maybe_format_writer_message(effect)
        if replacement is not None:
            yield Delegate(replacement)
            return

        if _is_delay_effect(effect):
            clock.advance_by(_extract_delay_seconds(effect))
            yield run_due_scheduled_programs()
            return (yield Resume(k, None))

        if _is_wait_until_effect(effect):
            clock.advance_to(_extract_wait_until_time(effect))
            yield run_due_scheduled_programs()
            return (yield Resume(k, None))

        if _is_get_time_effect(effect):
            return (yield Resume(k, clock.current_time))

        if _is_schedule_at_effect(effect):
            run_at, program = _extract_schedule_payload(effect)
            time_queue.push(run_at=run_at, sequence=next_sequence(), program=program)
            if run_at <= clock.current_time:
                yield run_due_scheduled_programs()
            return (yield Resume(k, None))

        if isinstance(effect, SetTimeEffect):
            clock.set_time(effect.time)
            yield run_due_scheduled_programs()
            return (yield Resume(k, None))

        if isinstance(effect, ForkRunEffect):
            fork_start = effect.start_time if effect.start_time is not None else clock.current_time
            fork_handler = deterministic_sim_handler(
                start_time=fork_start,
                fail_on_task_error=fail_on_task_error,
                warn_on_unawaited_task_failures=warn_on_unawaited_task_failures,
                log_formatter=log_formatter,
            )
            value = yield WithHandler(fork_handler, effect.program)
            return (yield Resume(k, value))

        if isinstance(effect, SpawnEffect):
            record = task_registry.create_task(effect.program)
            return (yield Resume(k, record.handle))

        if isinstance(effect, WaitEffect):
            target_task_id = task_id_of(effect.future)
            if target_task_id is not None and task_registry.has_task(target_task_id):
                value = yield wait_task(target_task_id)
                return (yield Resume(k, value))
            yield Delegate()
            return

        if isinstance(effect, GatherEffect):
            items = tuple(effect.items)
            if not all(gather_item_supported(item) for item in items):
                yield Delegate()
                return

            values: list[Any] = []
            for item in items:
                target_task_id = task_id_of(item)
                if target_task_id is not None and task_registry.has_task(target_task_id):
                    values.append((yield wait_task(target_task_id)))
                    continue
                values.append((yield item))

            return (yield Resume(k, values))

        if isinstance(effect, RaceEffect):
            futures = tuple(effect.futures)
            if not futures:
                raise ValueError("Race requires at least one future")

            first = futures[0]
            target_task_id = task_id_of(first)

            if target_task_id is not None and task_registry.has_task(target_task_id):
                value = yield wait_task(target_task_id)
                return (yield Resume(k, value))

            if isinstance(first, ProgramBase | EffectBase):
                value = yield first
                return (yield Resume(k, value))

            yield Delegate()
            return

        yield Delegate()

    return handler


__all__ = [
    "SIMULATION_START_TIME_ENV_KEY",
    "SimulationTaskError",
    "deterministic_sim_handler",
]
