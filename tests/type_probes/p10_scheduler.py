# scheduler の effect の答えの型(docs/23 の表)
from collections.abc import Generator
from typing import Any, assert_type

from doeff import do, run
from doeff_core_effects.scheduler import (CompletePromise, CreatePromise, Gather, Promise, Race, Spawn,
                                          Task, Wait, scheduled)


@do
def worker(n: int) -> Generator[Any, Any, int]:
    return n
    yield


@do
def namer(n: int) -> Generator[Any, Any, str]:
    return str(n)
    yield


@do
def main() -> Generator[Any, Any, list[int]]:
    t1 = yield from Spawn(worker(1))
    assert_type(t1, Task[int])
    t2 = yield from Spawn(worker(2))
    one = yield from Wait(t1)
    assert_type(one, int)
    both = yield from Gather(t1, t2)
    assert_type(both, list[int])
    ts = yield from Spawn(namer(3))
    mixed = yield from Gather(t1, ts)  # 結果の型の違う task(抜け 4 — 直った)
    assert_type(mixed, list[int | str])
    first = yield from Race(t1, t2)
    assert_type(first, int)
    either = yield from Race(t1, ts)
    assert_type(either, int | str)
    p = yield from CreatePromise[int]()
    assert_type(p, Promise[int])
    yield from CompletePromise(p, "x")  # expect-error (Promise[int] に str)
    s: str = yield from Wait(t1)  # expect-error (int を str へ)
    yield from Wait(42)  # expect-error (task でない物)
    return both


assert_type(run(scheduled(main())), list[int])
