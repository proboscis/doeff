"""When the process it started ends, its children end too - the core of the StartProcess promise. Written with the standard library only.

There is one core: read, line by line, a pipe whose write end only the starter holds; on EOF (the starter ended - including SIGKILL), send TERM
to the targets it holds (watched_until_eof). There are two ways to deploy it, and both call this core.
  * A per-starter-process watcher (started with this file as the entry point; the real StartProcess handler os_process.hy starts exactly one
    on the first StartProcess): each time the starter spawns a child it reports it with a "group <number>" or "pid <number>" line, and removes a reaped child with "forget <number>".
    On EOF it sends TERM to the reported targets, waits within the grace period (GRACE_SECONDS) for the leading processes to disappear, then sends KILL to the remaining
    groups and processes and exits. StartProcess calls from the second one on only write one line, so no startup time is added.
  * The doeff-cluster worker shim (one per job, the job's ancestor): on EOF of its own stdin it sends TERM to its own group. The pipe exists
    before the fork, so there is no gap. The grace period and the cleanup that adopts descendants are handled by the shim's main thread, which is the job's ancestor (the watcher is not an ancestor of the children, so it
    cannot use adoption).
Remaining gap in the starter-side watcher: if the starter is SIGKILLed between the fork and writing "group <number>" (under 1 ms), that child is left unreported
(how to close it is recorded in #3866 - not closed this time).

It lives inside doeff_lifeline, a top-level package separate from doeff_core_effects within the doeff-core-effects distribution (see the package header comment).
The names are neither effects nor handlers, so they are not made public (ADR-DOE-API-001 R1, R5). The watcher is started with `python -I -S -B <this file>`
(standard library only - startup is the same as a minimal python).
"""

import contextlib
import os
import select
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from io import BufferedReader

MODULE_TAGS = {"context": "process", "role": "foundation"}

# Seconds to wait, after EOF, for the targets that received TERM to disappear (same as the default stop-grace of StopProcess). Targets still remaining after that get KILL.
GRACE_SECONDS = 10.0


@dataclass(frozen=True)
class Group:
    """One target to stop: a process group (number = leading pid). TERM and KILL go to the whole group."""

    pgid: int


@dataclass(frozen=True)
class Single:
    """One target to stop: a single child process left in the starter's group. TERM and KILL go to that process only."""

    pid: int


Target = Group | Single


def signalled(target: Target, signum: int) -> None:
    """Send a signal to a target. Targets that are already gone and targets we cannot signal (another uid) are skipped silently - there is nothing to stop."""
    with contextlib.suppress(ProcessLookupError, PermissionError):
        match target:
            case Group(pgid=pgid):
                os.killpg(pgid, signum)
            case Single(pid=pid):
                os.kill(pid, signum)


def watched_until_eof(
    stream: BufferedReader, on_line: Callable[[bytes], None], ended: Callable[[], Iterable[Target]]
) -> None:
    """The core (see the header comment): read the starter's pipe line by line and pass each line to on_line; on EOF call ended once and send TERM to every target that answered.
    It only reads lines and never polls on a timer - the starter's end arrives as EOF as is."""
    for line in iter(stream.readline, b""):
        on_line(line)
    for target in tuple(ended()):
        signalled(target, signal.SIGTERM)


class Registry:
    """Table of targets the watcher was told about (number -> target). Lives only inside the watcher process and has a single reading thread, so no lock is needed."""

    def __init__(self) -> None:
        """Start with an empty table."""
        self._mut_targets: dict[int, Target] = {}

    def took(self, line: bytes) -> None:
        """Mirror one line from the starter into the table (group / pid = add, forget = remove). A line of an unknown form fails by name (it is not silently dropped)."""
        match line.decode("ascii").split():
            case ["group", number]:
                self._mut_targets[int(number)] = Group(int(number))
            case ["pid", number]:
                self._mut_targets[int(number)] = Single(int(number))
            case ["forget", number]:
                self._mut_targets.pop(int(number), None)
            case _:
                raise SystemExit(f"lifeline: unknown line {line!r}")

    def targets(self) -> tuple[Target, ...]:
        """Return all current targets."""
        return tuple(self._mut_targets.values())


def leader_of(target: Target) -> int:
    """Leading pid of the target (a group's number equals its leading pid)."""
    match target:
        case Group(pgid=pgid):
            return pgid
        case Single(pid=pid):
            return pid


def pidfd_of(target: Target) -> int | None:
    """Leading pidfd of the target (an fd that becomes readable when it ends). None for a target already gone (there is nothing to wait for)."""
    try:
        return os.pidfd_open(leader_of(target))
    except ProcessLookupError:
        return None


def ended_within(targets: tuple[Target, ...], grace: float) -> None:
    """Wait for the leaders of the targets that received TERM to disappear within the grace period. Linux waits on pidfds with select, so when all have ended it returns without waiting out the grace period.
    On an OS without pidfd (macOS) it waits the full grace period (without polling in between)."""
    if not hasattr(os, "pidfd_open"):
        time.sleep(grace)
        return
    pidfds = tuple(fd for fd in (pidfd_of(target) for target in targets) if fd is not None)
    deadline = time.monotonic() + grace
    waiting = pidfds
    while waiting:
        left = deadline - time.monotonic()
        if left <= 0:
            break
        readable, _, _ = select.select(waiting, [], [], left)
        waiting = tuple(fd for fd in waiting if fd not in readable)
    for fd in pidfds:
        os.close(fd)


def guard(stream: BufferedReader, grace: float) -> None:
    """Core of the per-starter-process watcher (see the header comment): mirror targets into the table until EOF; on EOF, TERM -> grace period -> KILL the remaining targets."""
    registry = Registry()
    watched_until_eof(stream, registry.took, registry.targets)
    targets = registry.targets()
    ended_within(targets, grace)
    for target in targets:
        signalled(target, signal.SIGKILL)


class Lifeline:
    """The starter-side handle (one per process - sits alongside STARTED-CHILDREN in os_process.hy): on the first report of a child it starts one watcher and holds only
    the write end of its stdin. The write end is not inherited by other children (an os.pipe end is non-inheritable - Popen's close_fds closes it too). Writes come from the thread that spawns children and
    the thread that reaps them, so they happen under a single lock. It is a resource handle, not a value type."""

    def __init__(self) -> None:
        """Start in the state where no watcher has been started."""
        self._mut_lock = threading.Lock()
        self._mut_guard: subprocess.Popen[bytes] | None = None

    def _written(self, line: str) -> None:
        """Write one line to the watcher (call under the lock). Start the watcher before the first line - in its own session (signals sent to the starter's group
        do not stop the watcher along with it); stdin = a pipe whose write end only the starter holds."""
        if self._mut_guard is None:
            # The watcher's interpreter is the underlying interpreter currently running (sys._base_executable). sys.executable is rewritten by the launcher
            # (a Hy entry point becomes the hy launcher - a doeff-cluster worker starts with `exec hy -m ...`, and the watcher started as `hy -I -S -B <file>`
            # and exited immediately). The watcher uses only the standard library, so the underlying interpreter outside the venv is enough.
            self._mut_guard = subprocess.Popen(
                [sys._base_executable, "-I", "-S", "-B", __file__],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                start_new_session=True,
            )
        guard_stdin = self._mut_guard.stdin
        if guard_stdin is None:
            raise RuntimeError("lifeline: the watcher's stdin pipe is missing")
        guard_stdin.write(line.encode("ascii"))
        guard_stdin.flush()

    def watch(self, pid: int, process_group: bool) -> None:
        """Report a spawned child to the watcher (a child in our own group goes as a whole group; a child that stayed in the starter's group goes as that process only)."""
        with self._mut_lock:
            self._written(f"{'group' if process_group else 'pid'} {pid}\n")

    def forget(self, pid: int) -> None:
        """Remove a reaped child from the watcher's table (a reaped pid can be reused, so it must not be stopped at EOF). Do nothing if no watcher was started."""
        with self._mut_lock:
            if self._mut_guard is not None:
                self._written(f"forget {pid}\n")


if __name__ == "__main__":
    guard(sys.stdin.buffer, GRACE_SECONDS)
