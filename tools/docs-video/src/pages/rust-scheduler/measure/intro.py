# The opening program: main yields Spawn and Gather, and scheduled() answers them, under both implementations.
from doeff_core_effects import Gather, Spawn, scheduled

from doeff import do, run


@do
def work(name: str):
    return name.upper()
    yield  # a generator: @do takes a generator function


@do
def main():
    a = yield Spawn(work("a"))
    b = yield Spawn(work("b"))
    return (yield Gather(a, b))


print("run(scheduled(main()))                        ->", run(scheduled(main())))
for impl in ("rust", "python"):
    print(f'run(scheduled(main(), implementation="{impl}")) ->', run(scheduled(main(), implementation=impl)))
