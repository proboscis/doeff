from __future__ import annotations

import os
import statistics
import time

import pytest

from doeff.do import do
from doeff.effects.reader import Ask
from doeff.rust_vm import run


@pytest.mark.slow
def test_vm_dispatch_microbenchmark_ceiling() -> None:
    """Dispatch micro-benchmark guardrail for VM step overhead.

    Defaults are tuned to keep the suite stable on the dev-profile VM build.
    For strict release-profile validation, override:
      DOEFF_VM_DISPATCH_CEILING_US=5
      DOEFF_VM_BENCH_DISPATCHES=10000
    """

    import doeff_vm

    dispatches = int(os.getenv("DOEFF_VM_BENCH_DISPATCHES", "3000"))
    warmup_runs = int(os.getenv("DOEFF_VM_BENCH_WARMUPS", "3"))
    sample_runs = int(os.getenv("DOEFF_VM_BENCH_SAMPLES", "7"))
    ceiling_us = float(os.getenv("DOEFF_VM_DISPATCH_CEILING_US", "30"))

    @do
    def program() -> int:
        total = 0
        for _ in range(dispatches):
            total += yield Ask("bench_key")
        return total

    handlers = [doeff_vm.reader]
    env = {"bench_key": 1}

    for _ in range(warmup_runs):
        rr = run(program(), handlers=handlers, env=env, print_doeff_trace=False)
        assert not rr.is_err(), "warmup run failed"

    samples_us: list[float] = []
    for _ in range(sample_runs):
        start = time.perf_counter()
        rr = run(program(), handlers=handlers, env=env, print_doeff_trace=False)
        elapsed = time.perf_counter() - start
        assert not rr.is_err(), "benchmark run failed"
        samples_us.append(elapsed * 1_000_000.0 / dispatches)

    median_us = statistics.median(samples_us)
    assert (
        median_us <= ceiling_us
    ), f"VM dispatch median {median_us:.3f}us exceeded ceiling {ceiling_us:.3f}us"
