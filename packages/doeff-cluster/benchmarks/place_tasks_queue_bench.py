"""How long one place-tasks (and one tick) takes as the queue of unplaceable tasks grows (2026-11-12).

The shape copies a coordinator seen on that day: 11 workers whose measured room is mostly used up, a group with a
per-worker limit of 2 that is already full on the one worker with room, and queued tasks whose memory need fits no
worker. Each request to the coordinator runs one settle (a tick) on its single loop, so the column "slow" models how
many replies of a 10-minute window would wait over 1 second (the coordinator's "slow reply" line) when requests
arrive at --rate per second and each costs the measured tick.
The column "request s" is one request's settle as the coordinator runs it: a heartbeat first writes one task row (a
new task table object, as every write makes), then the settle runs on it. --held sets how many finished rows are kept
for callers; a request's cost should not grow with it (only the queued and placed rows are walked).
Usage: uv run python benchmarks/place_tasks_queue_bench.py [--counts 0,100,200,400] [--held 1400] [--repeat 3] [--rate 10] [--profile]
"""

import argparse
import cProfile
import pstats
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from functools import partial

import hy  # noqa: F401 — loads the Hy modules below
from doeff_cluster import ClusterState, ClusterTiming, GroupLimit, TaskLimit, WorkerInfo
from doeff_cluster._coordinator.core.api_policy import tick
from doeff_cluster._coordinator.core.cluster_policy import place_tasks
from doeff_cluster._coordinator.intent.cluster_model import TaskRecord
from doeff_cluster._coordinator.intent.task_table import task_table_of
from doeff_cluster._coordinator.intent.request_bodies import MeasuredLoad
from doeff_hy._table import TableWrite, table_of

from doeff import run

NOW = 10_000_000
TIMING = ClusterTiming(lease_ms=10_000, fence_ms=10_000, reassign_after_ms=30_000)
PROGRAM = "sha-of-a-program"


@dataclass(frozen=True)
class Machine:
    """One worker of the modelled cluster and the room it reports."""

    name: str
    cpus: int
    available_mib: int
    total_mib: int
    busy: float


# Rooms in the proportions read on 2026-11-12: most workers have no memory room, a few have some.
MACHINES = (
    Machine("node-a", 16, 1564, 17191, 0.45),
    Machine("node-b", 4, 4090, 7800, 0.3),
    Machine("node-c", 16, 27201, 64000, 0.6),
    Machine("node-d", 4, 990, 7800, 0.5),
    Machine("node-e", 4, 427, 7800, 0.5),
    Machine("node-f", 4, 814, 7800, 0.5),
    Machine("node-g", 6, 2072, 11000, 0.5),
    Machine("node-h", 10, 11738, 32000, 0.2),
    Machine("node-i", 36, 0, 120000, 0.9),
    Machine("node-j", 36, 33585, 120000, 0.4),
    Machine("node-k", 16, 1200, 17191, 0.5),
)


def worker_of(machine: Machine) -> WorkerInfo:
    """Build the worker row that reports the machine's measured room at NOW."""
    load = MeasuredLoad(
        at_ms=NOW - 500,
        cpus=machine.cpus,
        cpu_busy=machine.busy,
        memory_total_MiB=machine.total_mib,
        memory_available_MiB=machine.available_mib,
        cpu_some_avg10=5.0,
        cpu_full_avg10=0.0,
        memory_some_avg10=0.0,
        memory_full_avg10=0.0,
    )
    return WorkerInfo(machine.name, (), NOW - 200, measured=load)


def queued_task(i: int, kind: str) -> TaskRecord:
    """Build the i-th queued task of a kind that no worker can take now (group limit full, or memory too big)."""
    base = TaskRecord(
        f"q{i:05d}",
        kind,
        PROGRAM,
        "",
        (),
        (),
        60_000,
        NOW + 600_000,
        NOW - 1000 * i,
        detached=True,
        key=f"{kind}/{i}",
        rerun=True,
        retain_ms=3_600_000,
    )
    if kind == "prewarm":
        return replace(base, limits=(TaskLimit(group="prewarm"),), memory_mib=2560)
    if kind == "long":
        return replace(base, limits=(TaskLimit(group="long"),), memory_mib=40_000)
    return replace(base, memory_mib=40_000, priority=-1)


def held_tasks(held: int) -> list[TaskRecord]:
    """Two running prewarm tasks on node-c (its group limit is full) and `held` finished rows kept for callers."""
    running = [
        replace(
            queued_task(90_000 + i, "prewarm"),
            id=f"r{i}",
            phase="running",
            worker="node-c",
            started_ms=NOW - 60_000,
            lease_until_ms=NOW + 600_000,
        )
        for i in range(2)
    ]
    finished = [
        replace(
            queued_task(100_000 + i, "long"),
            id=f"f{i}",
            phase="finished",
            finished_ms=NOW - 1000,
            shape=f"long-{i % 40}",
            peak_mib=1000 + i,
        )
        for i in range(held)
    ]
    return running + finished


def state_with(count: int, held: int = 1400) -> ClusterState:
    """Build the cluster state with `count` unplaceable queued tasks on top of `held` finished rows."""
    kinds = ("prewarm", "durable", "long")
    rows = held_tasks(held) + [queued_task(i, kinds[i % 3]) for i in range(count)]
    groups = table_of(
        (
            TableWrite("prewarm", GroupLimit(group="prewarm", scope="worker", limit=2)),
            TableWrite("long", GroupLimit(group="long", scope="worker", limit=6)),
        )
    )
    return ClusterState(
        (),
        table_of(tuple(TableWrite(m.name, worker_of(m)) for m in MACHINES)),
        table_of(()),
        task_table_of(tuple(TableWrite(r.id, r) for r in rows)),
        group_limits=groups,
    )


def best_seconds(step: Callable[[], object], repeat: int) -> float:
    """Return the fastest of `repeat` runs of `step` (the least disturbed reading)."""
    best = float("inf")
    for _ in range(repeat):
        start = time.perf_counter()
        step()
        best = min(best, time.perf_counter() - start)
    return best


def slow_replies(service_s: float, rate: float, seconds: float = 600.0, seed: int = 7) -> int:
    """Count the replies that wait over 1 s in one FIFO loop serving Poisson arrivals at `rate`/s, each taking `service_s`."""
    rng = random.Random(seed)
    arrival, free_at, slow = 0.0, 0.0, 0
    while True:
        arrival += rng.expovariate(rate)
        if arrival > seconds:
            return slow
        free_at = max(free_at, arrival) + service_s
        slow += free_at - arrival > 1.0


def place_once(state: ClusterState) -> object:
    """Run one place-tasks on the state one ms after it was settled (the decision every request repeats)."""
    return run(place_tasks(NOW + 1, state, state.placements, TIMING))


def tick_once(state: ClusterState) -> object:
    """Run one whole tick on the state one ms after it was settled (the settle every request repeats)."""
    return run(tick(state, NOW + 1, TIMING))


def request_once(state: ClusterState, step: int) -> object:
    """Run one request as the coordinator does: a heartbeat writes the measured peak of a running task, then the settle."""
    running = state.tasks.row("r0")
    heard = replace(
        state, tasks=state.tasks.with_writes((TableWrite("r0", replace(running, peak_mib=100 + step)),))
    )
    return run(tick(heard, NOW + 1, TIMING))


def request_seconds(state: ClusterState, repeat: int) -> float:
    """Return the fastest of `repeat` requests, each on a fresh task table (one written row, as a heartbeat makes)."""
    best = float("inf")
    for step in range(repeat):
        start = time.perf_counter()
        request_once(state, step)
        best = min(best, time.perf_counter() - start)
    return best


def main() -> None:
    """Print, for each queue length, the seconds of one place-tasks and of one whole tick in the steady state."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--counts", default="0,50,100,200,400")
    parser.add_argument("--held", type=int, default=1400)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--rate", type=float, default=10.0)
    parser.add_argument("--profile", action="store_true")
    args = parser.parse_args()
    counts = [int(c) for c in args.counts.split(",")]
    print(f"held rows {args.held}")
    print(f"{'queued':>7} {'place-tasks s':>14} {'tick s':>8} {'request s':>10} {'writes':>7} {'slow':>6}")
    for count in counts:
        # The first tick writes the waiting text into each row; the ticks after it are the steady state.
        state = run(tick(state_with(count, args.held), NOW, TIMING))
        writes = run(place_tasks(NOW + 1, state, state.placements, TIMING))
        place = best_seconds(partial(place_once, state), args.repeat)
        whole = best_seconds(partial(tick_once, state), args.repeat)
        request = request_seconds(state, args.repeat)
        print(
            f"{count:>7} {place:>14.3f} {whole:>8.3f} {request:>10.4f} {len(writes):>7}"
            f" {slow_replies(request, args.rate):>6}"
        )
        if args.profile and count == max(counts):
            profiler = cProfile.Profile()
            profiler.enable()
            request_once(state, args.repeat)
            profiler.disable()
            pstats.Stats(profiler).sort_stats("tottime").print_stats(30)


if __name__ == "__main__":
    main()
