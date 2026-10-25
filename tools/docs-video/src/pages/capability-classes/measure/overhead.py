# The evidence rule's factor (b): the cost of an effect against the cost of the call behind it
# (docs/22-capability-classes.md, "When Adoption Pays"). Times one effect handled and resumed (Ask under reader)
# against a plain dict lookup in a hot loop, and puts the same per-effect cost next to a call that takes 10 ms.
# Wall-clock times differ from machine to machine; the shape (a few microseconds per effect, a hot loop many times
# slower, a millisecond-scale call barely changed) is what the video shows.
import time

from doeff_core_effects.handlers import reader

from doeff import Ask, do, run

N = 20_000


@do
def asks(n):
    total = 0
    for _ in range(n):
        total += yield Ask("x")
    return total


def lookups(n, env):
    total = 0
    for _ in range(n):
        total += env["x"]
    return total


def best(f, repeat=5):
    times = []
    for _ in range(repeat):
        start = time.perf_counter()
        f()
        times.append(time.perf_counter() - start)
    return min(times)


effect_s = best(lambda: run(reader(env={"x": 1})(asks(N)))) / N
plain_s = best(lambda: lookups(N, {"x": 1})) / N
print(f"hot loop, {N} steps (best of 5):")
print(f"  per effect (Ask under reader): {effect_s * 1e6:.1f} us")
print(f"  per plain dict lookup: {plain_s * 1e9:.0f} ns")
print(f"  the effect is {effect_s / plain_s:.0f}x the plain step")

CALL_S = 0.010
start = time.perf_counter()
time.sleep(CALL_S)
call_s = time.perf_counter() - start
print("one call that takes 10 ms:")
print(f"  measured: {call_s * 1e3:.1f} ms")
print(f"  one effect around it adds {effect_s / call_s * 100:.2f} %")
