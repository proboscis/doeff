"""World phase marks for the step budget.

A simulated world (doeff-cluster's ``sim-main``) marks four events of its life: it ``begun``, it is
``up`` (every declared job started and the world settled), it began ``stopping`` and it ``ended``.
While a phase log is open (``open_phase_log`` — the pytest step-budget plugin opens one for the call
of each test it judges in steps), each mark is recorded with the doeff-vm step total of the process
at that moment, so the plugin can judge only the steps a world takes while it runs, and report the
steps of its start-up and shut-down apart. The world's own events cut the phases, not the clock.

With no log open (production, tests without the plugin) a mark records nothing and reads no count.
"""

from dataclasses import dataclass
from typing import Literal

WorldPhase = Literal["begun", "up", "stopping", "ended"]


@dataclass(frozen=True)
class PhaseMark:
    """One world event and the doeff-vm step total of the process when it happened."""

    phase: WorldPhase
    steps: int


# The one phase log of the process, like the scheduler's armed step budget. None (the default)
# means a mark records nothing.
_open_log: tuple[PhaseMark, ...] | None = None


def _read_vm_steps() -> int:
    """The doeff-vm steps taken so far by the whole process (a running total)."""
    from doeff._vm import native

    steps, _handler_calls = native._vm_work_counts()
    return steps


def open_phase_log() -> None:
    """Start recording world marks (a log already open is dropped: one test call at a time)."""
    global _open_log  # noqa: PLW0603 - one log per process, written by every world of the call
    _open_log = ()


def close_phase_log() -> tuple[PhaseMark, ...]:
    """Stop recording and return the marks recorded since ``open_phase_log``, in order."""
    global _open_log  # noqa: PLW0603 - one log per process, written by every world of the call
    marks = _open_log or ()
    _open_log = None
    return marks


def mark_world_phase(phase: WorldPhase) -> None:
    """Record that a world reached ``phase``, with the step total now (nothing when no log is open)."""
    global _open_log  # noqa: PLW0603 - one log per process, written by every world of the call
    if _open_log is not None:
        _open_log = (*_open_log, PhaseMark(phase, _read_vm_steps()))
