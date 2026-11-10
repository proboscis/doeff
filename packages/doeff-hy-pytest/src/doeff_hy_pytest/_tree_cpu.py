"""CPU seconds of the process tree during a test call - the sum of CPU seconds (user+sys) of the pytest process and its descendant processes.
Used instead of the per-marker wall-clock limit (``doeff_test_call_budget_by_marker``) to judge by this value;
only tests that declared it (``doeff_hy_pytest.declare_process_tree_cpu``) use it.

Why it exists: a marked test (real_world) is judged by wall clock, because its weight is in child processes and outside waits and does not show in process CPU seconds
(``time.process_time``). But for a test whose weight is the CPU of child processes (e.g. one that runs a type checker in a child),
the wall clock stretches with machine load even when the test body is the same. In the daily run of 2026-10-22 04:00 JST (node-h, load 75-104 / 36 cores),
a test that runs doeff-hy-check in a child had wall clock 33 s and own CPU 0.96 s, exceeding the 30 s limit, and was green once the limit was removed. In local measurements
most of that stretch was disk waiting (53 blocked processes, io PSI some 35-54%), and CPU waiting (schedstat run_delay) explains only part of the
stretch - subtracting a load share from wall clock cannot subtract I/O load. Descendants' CPU seconds are the amount of work the test did and
barely change with load.

How it measures: own process is the difference of ``time.process_time`` (the reader thread's CPU is subtracted via ``time.thread_time``). Descendants are walked via /proc
every ``TICK_SECONDS``, reading utime+stime from each process's ``/proc/<pid>/stat``; the last seen value minus either the first seen
value (a process that existed at call start) or 0 (a process born later) is summed. The last share of a process that ended between reads and
short processes that were born and ended between reads are missed, so the larger of this and the CPU seconds of descendants reaped by the pytest process (difference of ``RUSAGE_CHILDREN``)
is taken (both are lower bounds of the true value - they are not added, to avoid counting the same process twice).

On machines where it cannot be read (macOS, no ``/proc``) it does not measure (``TreeCpuUnmeasured``) and the caller judges by wall clock - no
looser than the previous judgment.
"""

import os
import resource
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

# Read interval (seconds). One read is only the /proc pid list plus the stat of newly seen pids and tree processes (reading the stat of all processes
# every time costs 20 ms CPU for 400 processes - reading only new pids is around 1 ms).
TICK_SECONDS = 0.1
PROC = Path("/proc")


@dataclass(frozen=True)
class TreeCpu:
    """CPU seconds of the process tree during a call - own = own process (excluding the reader thread), descendants = sum over descendant processes,
    processes = number of descendant processes seen by the reads."""

    own_seconds: float
    descendants_seconds: float
    processes: int

    @property
    def seconds(self) -> float:
        """CPU seconds of the whole tree (own plus descendants)."""
        return self.own_seconds + self.descendants_seconds


@dataclass(frozen=True)
class TreeCpuUnmeasured:
    """The tree CPU seconds cannot be measured (with a reason) - the caller judges by wall clock."""

    reason: str


@dataclass(frozen=True)
class ProcessStat:
    """The two values read from one line of /proc stat - the parent pid and the process CPU seconds (user+sys, including finished threads)."""

    parent: int
    cpu_seconds: float


def parse_stat(text: str) -> ProcessStat:
    """Pure: parse one line of /proc/<pid>/stat (the name field may contain spaces or parentheses, so count after the last closing parenthesis)."""
    rest = text[text.rindex(")") + 2 :].split()
    return ProcessStat(
        parent=int(rest[1]), cpu_seconds=(int(rest[11]) + int(rest[12])) / os.sysconf("SC_CLK_TCK")
    )


@dataclass(frozen=True)
class ProcessGone:
    """The process we tried to read is gone (it ended between reads - not counted)."""

    pid: int


def read_stat(pid: int) -> ProcessStat | ProcessGone:
    """Read the stat of one process (no file or unreadable = finished process). A line of unreadable shape is passed up as ValueError
    (the reader declares itself unmeasured - it is not read as 0 seconds)."""
    try:
        text = (PROC / str(pid) / "stat").read_text()
    except OSError:
        return ProcessGone(pid)
    try:
        return parse_stat(text)
    except (ValueError, IndexError) as error:
        raise ValueError(f"cannot parse the shape of {PROC / str(pid) / 'stat'}: {text[:120]!r}") from error


def descendants_of(root: int, parents: Mapping[int, int]) -> frozenset[int]:
    """Pure: build the set of descendant pids of root from a pid -> parent pid table (root is not included)."""
    tree = {root}
    grew = True
    while grew:
        grew = False
        for pid, parent in parents.items():
            if parent in tree and pid not in tree:
                tree.add(pid)
                grew = True
    return frozenset(tree - {root})


def descendants_cpu(first: Mapping[int, float], last: Mapping[int, float]) -> float:
    """Pure: from each descendant's first-seen CPU seconds (only processes present at call start - processes born later are 0) and last-seen CPU seconds,
    compute the sum of CPU seconds the descendants used during the call."""
    return sum(seconds - first.get(pid, 0.0) for pid, seconds in last.items())


def unmeasurable_reason() -> str | None:
    """The reason the tree CPU seconds cannot be read on this machine (None if readable)."""
    try:
        own = read_stat(os.getpid())
    except ValueError as error:
        return str(error)
    if isinstance(own, ProcessGone):
        return f"cannot read {PROC / str(os.getpid()) / 'stat'}"
    return None


class TreeCpuSampler:
    """Read descendants' CPU seconds in a thread only during the call. Use exactly once, start -> stop."""

    def __init__(self, tick_seconds: float = TICK_SECONDS) -> None:
        self._tick = tick_seconds
        self._stop = threading.Event()
        self._root = os.getpid()
        self._mut_parents: dict[int, int] = {}
        self._mut_first: dict[int, float] = {}
        self._mut_last: dict[int, float] = {}
        self._mut_reader_cpu = 0.0
        self._mut_thread: threading.Thread | None = None
        self._mut_own_started = 0.0
        self._mut_reaped_started = 0.0
        self._mut_failure: str | None = None

    def start(self) -> TreeCpuUnmeasured | None:
        """Start reading (on a machine that cannot read, do not start and return the reason). Read the CPU seconds of descendants present at start as the initial values, then return."""
        reason = unmeasurable_reason()
        if reason is not None:
            return TreeCpuUnmeasured(reason)
        try:
            self._read(starting=True)
        except (OSError, ValueError) as error:
            return TreeCpuUnmeasured(
                f"cannot read descendants' CPU seconds ({error.__class__.__name__}: {error})"
            )
        self._mut_reaped_started = _reaped_children_cpu()
        self._mut_own_started = time.process_time()
        self._mut_thread = threading.Thread(
            target=self._run, name="doeff-hy-pytest-tree-cpu", daemon=True
        )
        self._mut_thread.start()
        return None

    def stop(self) -> TreeCpu | TreeCpuUnmeasured:
        """Stop reading (read one last time) and return the tree CPU seconds during the call."""
        if self._mut_thread is None:
            return TreeCpuUnmeasured("reading was not started")
        own = time.process_time() - self._mut_own_started
        reaped = _reaped_children_cpu() - self._mut_reaped_started
        self._stop.set()
        self._mut_thread.join()
        if self._mut_failure is None:
            self._guarded_read()
        if self._mut_failure is not None:
            return TreeCpuUnmeasured(self._mut_failure)
        sampled = descendants_cpu(self._mut_first, self._mut_last)
        return TreeCpu(
            own_seconds=max(0.0, own - self._mut_reader_cpu),
            descendants_seconds=max(sampled, reaped),
            processes=len(self._mut_last),
        )

    def _run(self) -> None:
        """Body of the reader thread - read descendants at each interval until the stop signal, and note its own thread CPU seconds (to subtract from the test CPU)."""
        while self._mut_failure is None and not self._stop.wait(self._tick):
            self._guarded_read()
        self._mut_reader_cpu = time.thread_time()

    def _guarded_read(self) -> None:
        """Do one read; if the /proc shape cannot be parsed, note the reason (stop declares unmeasured - it does not silently become 0 seconds)."""
        try:
            self._read(starting=False)
        except (OSError, ValueError) as error:
            self._mut_failure = f"cannot read descendants' CPU seconds ({error.__class__.__name__}: {error})"

    def _read(self, starting: bool) -> None:
        """Read /proc once and update the last values of descendant CPU seconds (starting = the read at call start - set existing descendants' values as initial values).
        Read parent pids only for newly seen pids, and drop vanished pids from the table (a reused pid is re-read as a different process)."""
        present = {int(name) for name in os.listdir(PROC) if name.isdigit()}
        for gone in self._mut_parents.keys() - present:
            del self._mut_parents[gone]
        for pid in present - self._mut_parents.keys():
            match read_stat(pid):
                case ProcessStat(parent=parent):
                    self._mut_parents[pid] = parent
                case ProcessGone():
                    pass
        for pid in descendants_of(self._root, self._mut_parents):
            match read_stat(pid):
                case ProcessStat(cpu_seconds=seconds):
                    if starting:
                        self._mut_first[pid] = seconds
                    self._mut_last[pid] = max(self._mut_last.get(pid, 0.0), seconds)
                case ProcessGone():
                    pass


def _reaped_children_cpu() -> float:
    """Cumulative CPU seconds (user+sys) of descendants this process has reaped."""
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    return usage.ru_utime + usage.ru_stime
