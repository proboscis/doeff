# The limits scene: what one effect costs (docs/positioning/what-doeff-is.md, "Other limits"). Times a program
# that yields N effects, each answered by a handler that resumes k at once, against a loop of N plain function
# calls, five times each, and prints the CPU time per effect and per call in microseconds.
import time
from dataclasses import dataclass

from doeff import EffectBase, Resume, do, handler, run

N = 100_000


@dataclass(frozen=True)
class Tick(EffectBase):
    n: int


@do
def ticks(count):
    total = 0
    for n in range(count):
        total += yield Tick(n)
    return total


@do
def answer(effect: Tick, k):
    return (yield Resume(k, effect.n))


def plain(n):
    return n


def per_effect():
    started = time.process_time()
    total = run(handler(answer)(ticks(N)))
    assert total == N * (N - 1) // 2
    return (time.process_time() - started) / N * 1e6


def per_call():
    started = time.process_time()
    total = 0
    for n in range(N):
        total += plain(n)
    assert total == N * (N - 1) // 2
    return (time.process_time() - started) / N * 1e6


effects = [per_effect() for _ in range(5)]
calls = [per_call() for _ in range(5)]
print(f"{N} effects per run, 5 runs")
print("per effect (us):", " ".join(f"{t:.2f}" for t in effects))
print("per call (us):  ", " ".join(f"{t:.3f}" for t in calls))
