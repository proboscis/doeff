"""What one coordinator request costs in the three owners left after the task index (2026-11-13).

The coordinator's main thread stayed at 95-99% after the task table index (card ki-9990cc7adcff). The cpu-samples
owners left were the reply encoding (inbox:encoded_reply and replies:spelled_each), the placement class hash
(cluster_model:__hash__ and __init__ under place-tasks) and program_policy:program_refs. This measures one request's
cost in each owner while one size of the state grows, so the column shows what the cost is proportional to:

  rows      finished rows kept for callers (the task table's total)      — program-refs (sweep-due, every step)
  queued    unplaceable queued tasks                                     — place-tasks (one settle)
  board     semaphore rows that a GET /board?prefix=… returns            — the reply of that read (respond + encode)

Each request is run as the coordinator runs it (request_bodies.responded, then inbox.encoded-reply for the bytes).
Usage: uv run python benchmarks/request_owner_costs_bench.py [--repeat 5] [--profile owner]
"""

import argparse
import cProfile
import pstats
import time
from collections.abc import Callable
from dataclasses import replace
from functools import partial

import hy  # noqa: F401 — loads the Hy modules below
from doeff_cluster import BoardRow, ClusterState, http_request, responded
from doeff_cluster._coordinator.core.api_policy import tick
from doeff_cluster._coordinator.core.cluster_policy import board_tally_of, place_tasks, sweep_due
from doeff_cluster._coordinator.intent.cluster_model import ProgramRow, TaskRecord
from doeff_cluster._shared.protocol.inbox import encoded_reply
from doeff_hy._table import TableWrite, table_of

from doeff import run

from place_tasks_queue_bench import MACHINES, NOW, PROGRAM, TIMING, state_with


def best_seconds(step: Callable[[], object], repeat: int) -> float:
    """Return the fastest of `repeat` runs of `step` (the least disturbed reading)."""
    best = float("inf")
    for _ in range(repeat):
        start = time.perf_counter()
        step()
        best = min(best, time.perf_counter() - start)
    return best


def with_programs(state: ClusterState, count: int) -> ClusterState:
    """Put `count` stored Programs, old enough to be swept if nothing referred to them (sweep-due reads the refs)."""
    rows = [TableWrite(PROGRAM, ProgramRow(blob="x", versions={}, put_ms=0))]
    rows += [TableWrite(f"{i:064x}", ProgramRow(blob="x", versions={}, put_ms=NOW)) for i in range(count)]
    return replace(state, programs=table_of(tuple(rows)))


def with_board(state: ClusterState, rows: int) -> ClusterState:
    """Put `rows` empty semaphore rows of one machine's holders on the board (the shape read on 2026-11-13)."""
    board = table_of(
        tuple(
            TableWrite(
                f"semaphore/hold-node-c-{i:026d}",
                BoardRow(value={"holders": {}, "permits": 1}, version=i + 1, expires_ms=None, size=32),
            )
            for i in range(rows)
        )
    )
    return replace(state, board=board, board_tally=board_tally_of(board))


def settled(queued: int, held: int, board: int) -> ClusterState:
    """The steady state: built, with Programs and the board, and settled once (the waiting texts written)."""
    state = with_board(with_programs(state_with(queued, held), 20), board)
    return run(tick(state, NOW, TIMING))


def reply_seconds(state: ClusterState, method: str, path: str, query: dict, body: object, repeat: int) -> tuple[float, int]:
    """One request's respond + spelling + encoding (the coordinator's work for it), and the reply's bytes."""
    request = run(http_request(method, path, query, body, actor="bench"))

    def once() -> int:
        _after, _status, spelled = responded(state, request, NOW + 1, TIMING)
        data, _kind = encoded_reply(spelled)
        return len(data)

    size = once()
    return best_seconds(once, repeat), size


def heartbeat_body(name: str) -> dict:
    """A heartbeat of one worker of the model (it reports the room it measured)."""
    machine = next(m for m in MACHINES if m.name == name)
    return {
        "name": name,
        "provides": [],
        "measured": {
            "atMs": NOW - 500,
            "cpus": machine.cpus,
            "cpuBusy": machine.busy,
            "memoryTotalMiB": machine.total_mib,
            "memoryAvailableMiB": machine.available_mib,
            "cpuSomeAvg10": 5.0,
            "cpuFullAvg10": 0.0,
            "memorySomeAvg10": 0.0,
            "memoryFullAvg10": 0.0,
        },
    }


def program_refs_seconds(state: ClusterState, repeat: int) -> float:
    """One sweep-due (every coordinator step asks it for the next wake; it reads the Program refs)."""
    return best_seconds(lambda: run(sweep_due(state, NOW + 1, TIMING)), repeat)


def counted_hashes(step: Callable[[], object]) -> int:
    """Count how many times one run of `step` hashes a task row, to show what the placement keys hash per request."""
    original = TaskRecord.__hash__
    calls = [0]

    def counting(row: TaskRecord) -> int:
        calls[0] += 1
        return original(row)

    TaskRecord.__hash__ = counting  # type: ignore[method-assign]
    try:
        step()
    finally:
        TaskRecord.__hash__ = original  # type: ignore[method-assign]
    return calls[0]


def place_seconds(state: ClusterState, repeat: int) -> float:
    """One place-tasks one ms after the settle (every request's settle runs it)."""
    return best_seconds(lambda: run(place_tasks(NOW + 1, state, state.placements, TIMING)), repeat)


def main() -> None:
    """Print each owner's cost of one request as one size grows and the others stay."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeat", type=int, default=5)
    parser.add_argument("--profile", choices=("board", "heartbeat", "state", "place", "refs"))
    args = parser.parse_args()
    repeat = args.repeat

    print("program-refs (sweep-due) by the task table's rows — queued 170, board 100")
    for held in (1700, 3400, 6800):
        state = settled(170, held, 100)
        print(f"  rows {len(state.tasks.rows()):>6}  sweep-due s {program_refs_seconds(state, repeat):.5f}")

    print("place-tasks (placement class) by queued — rows 1700, board 100")
    for queued in (85, 170, 340):
        state = settled(queued, 1700, 100)
        # The settle's first place-tasks meets each queued row object once; count the requests after it.
        place_seconds(state, 1)
        hashes = counted_hashes(partial(place_seconds, state, 1))
        print(f"  queued {queued:>5}  place-tasks s {place_seconds(state, repeat):.5f}  task row hashes {hashes}")

    print("replies (respond + spell + encode) by the reply's size — queued 170, rows 1700")
    for board in (6000, 12000, 24000):
        state = settled(170, 1700, board)
        seconds, size = reply_seconds(state, "GET", "/board", {"prefix": "semaphore/hold-node-c-"}, None, repeat)
        print(f"  board rows {board:>6}  GET /board s {seconds:.5f}  bytes {size}")
    for held in (1700, 3400):
        state = settled(170, held, 100)
        seconds, size = reply_seconds(state, "GET", "/state", {}, None, repeat)
        print(f"  task rows {len(state.tasks.rows()):>6}  GET /state s {seconds:.5f}  bytes {size}")
    for queued in (170, 340):
        state = settled(queued, 1700, 100)
        seconds, size = reply_seconds(state, "POST", "/heartbeat", {}, heartbeat_body("node-c"), repeat)
        print(f"  queued {queued:>5}  POST /heartbeat s {seconds:.5f}  bytes {size}")

    if args.profile:
        state = settled(170, 1700, 24000)
        steps: dict[str, Callable[[], object]] = {
            "board": partial(reply_seconds, state, "GET", "/board", {"prefix": "semaphore/hold-node-c-"}, None, 1),
            "state": partial(reply_seconds, state, "GET", "/state", {}, None, 1),
            "heartbeat": partial(reply_seconds, state, "POST", "/heartbeat", {}, heartbeat_body("node-c"), 1),
            "place": partial(place_seconds, state, 1),
            "refs": partial(program_refs_seconds, state, 1),
        }
        profiler = cProfile.Profile()
        profiler.enable()
        steps[args.profile]()
        profiler.disable()
        pstats.Stats(profiler).sort_stats("tottime").print_stats(25)


if __name__ == "__main__":
    main()
