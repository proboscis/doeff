from __future__ import annotations

import doeff_vm

from doeff import Gather, Spawn, do
from doeff.rust_vm import _wrap_handlers, default_handlers


@do
def _child(i: int):
    return i


@do
def _spawn_gather(n: int):
    tasks = []
    for i in range(n):
        tasks.append((yield Spawn(_child(i), daemon=False)))
    return (yield Gather(*tasks))


def _run_spawn_gather(n: int) -> tuple[object, int, int]:
    vm = doeff_vm.PyVM()
    wrapped = _wrap_handlers(_spawn_gather(n), default_handlers(), api_name="run()")
    result = vm.run_with_result(wrapped)
    return result.result, vm._segment_count(), vm._continuation_count()


def test_spawn_gather_releases_spawn_segments_after_completion() -> None:
    baseline_result, baseline_segments, baseline_continuations = _run_spawn_gather(0)
    assert str(baseline_result) == "Ok([])"
    assert baseline_continuations == 0

    result_1, segments_1, continuations_1 = _run_spawn_gather(1)
    assert str(result_1) == "Ok([0])"
    assert continuations_1 == 0

    result_10, segments_10, continuations_10 = _run_spawn_gather(10)
    assert str(result_10) == "Ok([0, 1, 2, 3, 4, 5, 6, 7, 8, 9])"
    assert continuations_10 == 0
    assert segments_10 > baseline_segments

    result_100, segments_100, continuations_100 = _run_spawn_gather(100)
    assert str(result_100).startswith("Ok([0, 1, 2, 3, 4")
    assert continuations_100 == 0
    assert segments_100 > segments_10
    assert segments_100 <= baseline_segments + 11 * (segments_10 - baseline_segments)
