"""Check that, even when the stop signal (SIGTERM) hits the main thread while it is inside an operation on the scheduler's external completion queue, the AwaitStop wait wakes and the run
ends.

Path (real): the receiver StopBox.receive installed by os-signal-stop-handler runs on the main thread, interrupting at a bytecode boundary, and calls complete of the promise
outside AwaitStop (ExternalPromise) that is sleeping. complete does a put onto the scheduler's external completion queue (external_queue of _scheduled_python).
The main thread reads that same queue with drain (empty, get) and _drain_one_external (get(timeout=...)). If the receiver's
put waits on a lock that the same thread holds inside the queue operation (it cannot be taken in a nested way), the process stalls waiting on itself,
and even a later SIGTERM does not get it out (KILL's 137 in the cluster).

How built: in a child process, run the real assembly (the scheduled python implementation, state, os-signal-stop-handler), put one AwaitStop wait to sleep, and
wait for its answer. There are 2 kinds of places to deliver the signal:
  * Moments inside a queue operation -- after the wait sleeps, count with sys.settrace the moments when the main thread runs a Python line in a queue operation (a frame that has the queue as self, and below it),
    and fire signal.raise_signal(SIGTERM) at the k-th moment (the receiver runs at the same place as when a real signal hits a bytecode
    boundary). The child delivers once per fresh run with k = 0, 1, ...
  * While blocked inside the queue's get -- in a run that has used up the moments inside queue operations, another thread sees the main thread's stack
    stopped (blocked) under _drain_one_external, and delivers with signal.pthread_kill. If the queue operation is written in C, the only place where Python
    runs inside the operation is this one, where the blocked get is interrupted by the signal and calls the receiver.
What is judged: the child ends within the limit, at every delivery point the wait wakes for the signal's reason, and the last one was delivered while blocked in the get of _drain_one_external.
When the child stalls, the last delivery point (file:line function) is put in the red message.

Before the fix (queue.Queue), at the 2nd moment inside a queue operation (inside the `with self.mutex:` of drain's empty), the receiver's put waits on the same lock
and stalls. After the fix (queue.SimpleQueue -- put is reentrant and may be called from a signal receiver), there is no Python moment inside a queue operation, and
it is judged that the receiver's put runs inside the blocked get and the get wakes (on this repo's default free-threading Python).
The Rust implementation's (implementation="rust") queue is a Mutex outside Python, and is out of this check's scope.
"""

from __future__ import annotations

import signal
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from types import FrameType

import hy  # noqa: F401  - so that Hy modules (stop_signal_handlers.hy and others) can be imported
import pytest
from doeff_core_effects import AwaitStop, Spawn, Wait, os_signal_stop_handler, scheduled, state
from doeff_core_effects._stop_signal_handlers import StopBox

from doeff import do, run, with_handlers

#: Flag that also makes this file the child process's entry point.
CHILD_FLAG = "--child"
#: Upper limit of the child's time (seconds). A few seconds for imports, under 1 second per delivering run. A stalled child is KILLed at this limit (SIGTERM does not get it out).
CHILD_LIMIT_SECONDS = 30.0
#: Interval for watching for blockage (seconds), and the number of times the main thread's stack must stay at the same place in a row to be seen as blocked (0.02 x 25 = 0.5 seconds).
POLL_SECONDS = 0.02
BLOCKED_SAMPLES = 25
#: Upper limit for counting moments inside queue operations (if the limit is hit without using them up, the child ends in red).
MOMENT_LIMIT = 500
#: The function that should be on the main thread's stack when delivering at a blockage -- where the scheduler waits for an external completion and blocks in the queue's get.
BLOCKING_READ = "_drain_one_external"
#: The reason the real receiver (StopBox.receive) writes on SIGTERM.
STOP_REASON = f"signal {int(signal.SIGTERM)}"

TraceFunction = Callable[[FrameType, str, object], object]


class Delivery:
    """The only mutable box for the place where the signal is delivered in one run and the result of delivering (written by the main thread's tracer and by the blockage-watching thread).
    target = at which moment inside the queue operations to deliver, seen = the number of moments counted, where = the place delivered (None if not yet)."""

    __slots__ = ("seen", "target", "where")

    def __init__(self, target: int) -> None:
        self.target = target
        self.seen = 0
        self.where: str | None = None


def _parked_queue() -> object | None:
    """The queue the receiver puts onto while the stop receiver holds a sleeping wait (the queue held by the promise outside the wait). None otherwise."""
    box = getattr(signal.getsignal(signal.SIGTERM), "__self__", None)
    if not isinstance(box, StopBox) or box.reason is not None or not box.waiters:
        return None
    # Look at the receiver's put destination itself -- ExternalPromise does not expose the queue by a public name (a value inside the scheduler).
    return box.waiters[0]._queue  # noqa: SLF001


def _outward(frame: FrameType | None) -> Iterator[FrameType]:
    """The stack traced outward from frame (toward the caller)."""
    while frame is not None:
        yield frame
        frame = frame.f_back


def _inside_operation_of(frame: FrameType | None, queue: object) -> bool:
    """Whether frame is running in or below a queue operation (a frame that has the queue as self)."""
    return any(outer.f_locals.get("self") is queue for outer in _outward(frame))


def _place(frame: FrameType) -> str:
    return f"{Path(frame.f_code.co_filename).name}:{frame.f_lineno} {frame.f_code.co_name}"


def _stack_names(frame: FrameType | None) -> str:
    """The names of the main thread's stack functions, from the outer BLOCKING_READ inward (the inner 5 if absent)."""
    names = tuple(outer.f_code.co_name for outer in _outward(frame))
    shown = names[: names.index(BLOCKING_READ) + 1] if BLOCKING_READ in names else names[:5]
    return " > ".join(reversed(shown))


@dataclass(frozen=True)
class OperationTracer:
    """After the wait sleeps, count the moments when the main thread runs a Python line inside a queue operation, and fire the signal at the delivery.target-th."""

    delivery: Delivery

    def on_call(self, frame: FrameType, event: str, arg: object) -> TraceFunction | None:
        del event, arg
        if self.delivery.where is not None:
            return None
        queue = _parked_queue()
        if queue is None or not _inside_operation_of(frame, queue):
            return None
        return self.on_line

    def on_line(self, frame: FrameType, event: str, arg: object) -> TraceFunction:
        del arg
        if event == "line" and self.delivery.where is None:
            if self.delivery.seen == self.delivery.target:
                self.delivery.where = f"inside {_place(frame)}"
                print(f"deliver {self.delivery.target} {self.delivery.where}", flush=True)
                signal.raise_signal(signal.SIGTERM)
            self.delivery.seen += 1
        return self.on_line


def _deliver_when_blocked(delivery: Delivery, main: int, finished: threading.Event) -> None:
    """If the wait sleeps and the main thread's stack has stayed at the same place (under BLOCKING_READ) for BLOCKED_SAMPLES times in a row, send
    SIGTERM to the main thread. Does nothing in a run that has already delivered inside a queue operation. frame is held as the object itself and compared with is, not by id.
    Why it watches at intervals: there is no event that announces "another thread got blocked", and a blockage is visible only by the stack not moving (a form only for the check's tooling)."""
    last: FrameType | None = None
    last_offset = -1
    same = 0
    while not finished.wait(POLL_SECONDS):
        if delivery.where is not None:
            return
        frame = sys._current_frames().get(main)  # noqa: SLF001 - this is the only place that reads another thread's stack
        if frame is None or _parked_queue() is None or BLOCKING_READ not in _stack_names(frame):
            last, last_offset, same = None, -1, 0
            continue
        same = same + 1 if (frame is last and frame.f_lasti == last_offset) else 0
        last, last_offset = frame, frame.f_lasti
        if same >= BLOCKED_SAMPLES:
            delivery.where = f"blocked {_stack_names(frame)}"
            print(f"deliver {delivery.target} {delivery.where}", flush=True)
            signal.pthread_kill(main, signal.SIGTERM)
            return


@do
def _await_stop_once():
    reason = yield AwaitStop()
    return reason


@do
def _park_then_wait():
    waiter = yield Spawn(_await_stop_once())
    reason = yield Wait(waiter)
    return reason


def _run_once(target: int) -> Delivery:
    """Run the real assembly once, and deliver SIGTERM at the target-th moment inside a queue operation (or, if none, inside the blocked get)."""
    delivery = Delivery(target)
    finished = threading.Event()
    helper = threading.Thread(
        target=_deliver_when_blocked, args=(delivery, threading.get_ident(), finished), daemon=True
    )
    helper.start()
    sys.settrace(OperationTracer(delivery).on_call)
    try:
        reason = run(
            scheduled(with_handlers([state(), os_signal_stop_handler], _park_then_wait()), implementation="python")
        )
    finally:
        sys.settrace(None)
        finished.set()
        helper.join()
    print(f"woke {target} {reason!r}", flush=True)
    return delivery


def _child() -> int:
    """Deliver once each with k = 0, 1, ..., and finish at the run that delivered inside the blocked get (the one that used up the moments inside queue operations)."""
    for target in range(MOMENT_LIMIT):
        delivery = _run_once(target)
        if delivery.where is None:
            print(f"undelivered {target}", flush=True)
            return 1
        if delivery.where.startswith("blocked"):
            print(f"moments {target}", flush=True)
            return 0
    print(f"moments-exceeded {MOMENT_LIMIT}", flush=True)
    return 1


def _text(output: str | bytes | None) -> str:
    if output is None:
        return ""
    return output.decode(errors="replace") if isinstance(output, bytes) else output


def test_a_stop_signal_inside_the_external_queue_operations_wakes_the_wait() -> None:
    command = [sys.executable, str(Path(__file__).resolve()), CHILD_FLAG]
    try:
        finished = subprocess.run(command, capture_output=True, text=True, timeout=CHILD_LIMIT_SECONDS, check=False)
    except subprocess.TimeoutExpired as stuck:
        pytest.fail(
            f"The child did not end within {CHILD_LIMIT_SECONDS:.0f} seconds -- the SIGTERM receiver that was last delivered (StopBox.receive -> "
            "ExternalPromise.complete -> put onto the external completion queue) does not return. This is how it stalls when a receiver on the same thread"
            f" waits on a lock that the main thread holds inside a queue operation (KILL's 137 in the cluster). Child output:\n{_text(stuck.stdout)}{_text(stuck.stderr)[-2000:]}"
        )
    out = finished.stdout
    assert finished.returncode == 0, f"Child rc {finished.returncode}:\n{out}\n{finished.stderr[-4000:]}"
    lines = out.splitlines()
    delivered = [line for line in lines if line.startswith("deliver ")]
    woke = [line for line in lines if line.startswith("woke ")]
    assert delivered, f"The signal was never delivered:\n{out}"
    assert len(woke) == len(delivered), f"Of {len(delivered)} deliveries, the wait woke in {len(woke)}:\n{out}"
    assert all(line.endswith(repr(STOP_REASON)) for line in woke), f"The wait's answer is not {STOP_REASON!r}:\n{out}"
    assert f"blocked {BLOCKING_READ}" in delivered[-1], f"The last signal was not delivered while blocked in the get of {BLOCKING_READ}:\n{out}"


if __name__ == "__main__":
    if sys.argv[1:] != [CHILD_FLAG]:
        raise SystemExit(f"Usage: python {Path(__file__).name} {CHILD_FLAG}")
    raise SystemExit(_child())
