"""設計比較用の計測。#2760 の実際の read-each の受入検証ではない。"""
from statistics import median
from time import perf_counter, process_time

from test_sequence_contract import reference_sequence

from doeff import do, run


@do
def row(pair):
    return pair


@do
def repeated_tuple(pairs):
    result = ()
    for pair in pairs:
        value = yield row(pair)
        result = (*result, value)
    return dict(result)


@do
def linear_tuple(pairs):
    values = yield reference_sequence(*(row(pair) for pair in pairs))
    return dict(values)


def sample(call, repeats=5):
    call()
    readings = []
    for _ in range(repeats):
        cpu = process_time()
        wall = perf_counter()
        result = call()
        readings.append((process_time() - cpu, perf_counter() - wall))
        assert len(result) > 0
    return tuple(median(sample[index] for sample in readings) for index in (0, 1))


if __name__ == "__main__":
    # root pytest と同じ VM invariant checks を明示的に有効にする。
    from doeff_vm.doeff_vm import set_invariant_checks

    set_invariant_checks(True)
    print("rows,dict_cpu_ms,quadratic_cpu_ms,linear_cpu_ms,linear_wall_ms,linear/dict")
    for size in (300, 3000, 10000, 30000):
        pairs = tuple((str(index), index) for index in range(size))
        expected = dict(pairs)
        assert run(linear_tuple(pairs)) == expected
        baseline = sample(lambda pairs=pairs: {key: value for key, value in pairs})  # noqa: C416 - issue の比較対象を保つ
        quadratic = sample(lambda pairs=pairs: run(repeated_tuple(pairs)))
        linear = sample(lambda pairs=pairs: run(linear_tuple(pairs)))
        print(
            f"{size},{baseline[0] * 1000:.3f},{quadratic[0] * 1000:.3f},"
            f"{linear[0] * 1000:.3f},{linear[1] * 1000:.3f},{linear[0] / baseline[0]:.1f}",
            flush=True,
        )
