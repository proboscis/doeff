# The page's measurement, taken again: microseconds of CPU time per operation for the flows of
# benchmarks/scheduler_bench.py under both implementations, best of 3 alternating runs each, and the speedup.
# CPU time of the running thread (time.thread_time) is counted, so a busy machine does not inflate it.
# Run from the repository root: uv run python tools/docs-video/src/pages/rust-scheduler/measure/speed.py
import os
import platform
import sys
import time

sys.path.insert(0, "benchmarks")
from scheduler_bench import FLOWS, outside_handler  # noqa: E402

from doeff import run, with_handlers  # noqa: E402
from doeff_core_effects.scheduler import scheduled  # noqa: E402

N = 5_000
RUNS = 3


def cpu_seconds(make, outside: bool, implementation: str) -> float:
    program = scheduled(make(N), implementation=implementation)
    if outside:
        program = with_handlers([outside_handler], program)
    started = time.thread_time()
    run(program)
    return time.thread_time() - started


best: dict[str, dict[str, float]] = {"python": {}, "rust": {}}
for _ in range(RUNS):
    for impl in ("python", "rust"):
        for name, (_label, make, outside) in FLOWS.items():
            us = cpu_seconds(make, outside, impl) / N * 1e6
            best[impl][name] = min(us, best[impl].get(name, us))

print(f"{platform.system()} {platform.machine()}, {os.cpu_count()} logical CPUs, load average {os.getloadavg()[0]:.0f}")
print(f"Python {platform.python_version()} ({'GIL' if sys._is_gil_enabled() else 'free-threaded'}), N = {N}, best of {RUNS}")
print(f"{'flow':24s} {'python':>8s} {'rust':>8s} {'speedup':>8s}")
for name, python_us in best["python"].items():
    rust_us = best["rust"][name]
    print(f"{name:24s} {python_us:8.1f} {rust_us:8.1f} {python_us / rust_us:8.1f}")
