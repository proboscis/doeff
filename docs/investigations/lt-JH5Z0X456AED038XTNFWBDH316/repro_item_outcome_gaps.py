"""ItemOutcome の 2 つの限界(件 0 の Traverse・parallel_fail_fast の取り消した件)が、
progress_bars の bar の見え方にどう出るかの再現(依頼 lt-JH5Z0X456AED038XTNFWBDH316)。

走らせ方: uv run --project <doeff の worktree> python repro_item_outcome_gaps.py
"""

import asyncio
import io
import sys

from doeff_core_effects.effects import Await, Try
from doeff_core_effects.handlers import await_handler, try_handler
from doeff_core_effects.scheduler import scheduled
from doeff_traverse.effects import ItemOutcome, Traverse
from doeff_traverse.handlers import (
    fail_handler,
    parallel,
    parallel_fail_fast,
    progress_bars,
    sequential,
)
from tqdm import tqdm

from doeff import Pass, do, run
from doeff import handler as program_handler

SINK = io.StringIO()


class Bar(tqdm):
    """本物の tqdm(出力は SINK)。progress_bars が close を呼んだかを残す。"""

    made: tuple["Bar", ...] = ()

    def __init__(self, *, total: int, desc: str) -> None:
        super().__init__(total=total, desc=desc, file=SINK)
        self.closed_by_handler = False
        Bar.made = (*Bar.made, self)

    def close(self) -> None:
        self.closed_by_handler = True
        super().close()


def bar_states() -> list[str]:
    return [
        f"{b.desc}: n={b.n}/total={b.total} pos={b.pos} closed={b.closed_by_handler}"
        for b in Bar.made
    ]


class Recorder:
    def __init__(self) -> None:
        self.seen: tuple[ItemOutcome, ...] = ()

    def handler(self):
        @do
        def watch(effect: ItemOutcome, k):
            if isinstance(effect, ItemOutcome):
                self.seen = (*self.seen, effect)
            yield Pass(effect, k)

        return program_handler(watch)


@do
def first(x: int):
    """3 は失敗、他は 10 倍。"""
    if x == 3:
        raise ValueError("three")
    if False:
        yield
    return x * 10


@do
def always_fail(x: int):
    if True:
        raise ValueError(f"fail {x}")
    yield


@do
def second(v: int):
    if False:
        yield
    return v + 1


@do
def slow(x: int):
    """件ごとに x * 20 ms 待つ(並びの件が同時に走る)。3 は 10 ms で失敗。"""
    if x == 3:
        yield Await(asyncio.sleep(0.01))
        raise ValueError("three")
    yield Await(asyncio.sleep(0.02 * x))
    return x * 10


@do
def two_stage(f1, items):
    a = yield Traverse(f1, items, label="stage1")
    b = yield Traverse(second, a, label="stage2")
    return b


@do
def one_stage(f, items, label="stage1"):
    return (yield Traverse(f, items, label=label))


@do
def nested(x: int):
    inner = yield Traverse(second, list(range(x)), label=f"inner({x})")
    return inner.valid_values


def stack(strategy, inside, program):
    body = program
    for install in [*inside, fail_handler, try_handler, await_handler()]:
        body = install(body)
    return scheduled(strategy(body))


def header(title: str) -> None:
    Bar.made = ()
    print(f"\n=== {title}")


def tail(rec: Recorder | None = None) -> None:
    for line in bar_states():
        print("  bar", line)
    if not Bar.made:
        print("  bar (1 本も開かない)")
    if rec is not None:
        print("  reports", [(r.label, r.item.index, r.outcome, type(r.item.value).__name__) for r in rec.seen])
    print("  tqdm._instances に残る bar:", len(tqdm._instances))


STRATEGIES = {
    "sequential": sequential,
    "parallel": lambda: parallel(concurrency=4),
    "parallel_fail_fast": lambda: parallel_fail_fast(concurrency=4),
}


def scenario_1() -> None:
    print("\n##### 限界 1: 走らせる件の無い Traverse")
    for name in ("sequential", "parallel"):
        header(f"[1a {name}] stage1 で全件失敗 → stage2 は持ち越しだけ (items=[1,2,3], f=always_fail)")
        rec = Recorder()
        out = run(stack(STRATEGIES[name](), [rec.handler(), progress_bars(Bar)], two_stage(always_fail, [1, 2, 3])))
        print("  stage2 の答え:", len(out.failed_items), "件 failed")
        tail(rec)

        header(f"[1b {name}] items=[] の Traverse")
        rec = Recorder()
        run(stack(STRATEGIES[name](), [rec.handler(), progress_bars(Bar)], one_stage(first, [])))
        tail(rec)

        header(f"[1c {name}] 入れ子: 外の件 x=[0,1,2] が内で Traverse(range(x))")
        rec = Recorder()
        run(stack(STRATEGIES[name](), [rec.handler(), progress_bars(Bar)], one_stage(nested, [0, 1, 2], label="outer")))
        tail(rec)


def scenario_2() -> None:
    print("\n##### 限界 2: parallel_fail_fast が最初の失敗で止まる")
    for conc in (1, 4):
        for f, fname in ((first, "即時"), (slow, "Await で待つ")):
            header(f"[2a c={conc} {fname}] items=1..8, 3 が失敗、例外は run の外へ")
            rec = Recorder()
            try:
                run(stack(parallel_fail_fast(concurrency=conc), [rec.handler(), progress_bars(Bar)], one_stage(f, list(range(1, 9)))))
            except Exception as error:
                print("  run raised:", type(error).__name__, error)
            tail(rec)

    header("[2b c=4 Await] 外で Try が例外を受け、同じ run で次の Traverse を走らせる")
    rec = Recorder()

    @do
    def recover():
        r = yield Try(one_stage(slow, list(range(1, 9))))
        print("  Try:", r)
        again = yield Traverse(second, [1, 2, 3], label="after")
        return again

    run(stack(parallel_fail_fast(concurrency=4), [rec.handler(), progress_bars(Bar)], recover()))
    tail(rec)

    header("[2c] 同じ progress_bars を 2 回の run で使い回す(長く生きる process): 1 回目が失敗、2 回目は成功")
    bars = progress_bars(Bar)
    try:
        run(stack(parallel_fail_fast(concurrency=1), [bars], one_stage(first, list(range(1, 9)), label="run1")))
    except Exception as error:
        print("  run1 raised:", type(error).__name__)
    run(stack(parallel_fail_fast(concurrency=1), [bars], one_stage(second, [1, 2, 3], label="run2")))
    tail()


def scenario_3() -> None:
    """失敗を Try で受けた後も同じ run が 0.5 秒続く時、残りの件はどうなるか。"""
    print("\n##### 限界 2 の続き: Try で受けた後、run が続く間の残りの件")
    header("[2d c=4 Await] Try で受け、その後 0.5 秒 Await してから終える")
    rec = Recorder()

    @do
    def recover_then_wait():
        r = yield Try(one_stage(slow, list(range(1, 9))))
        print("  Try:", r, " / Try の直後の reports 数:", len(rec.seen))
        yield Await(asyncio.sleep(0.5))
        return r

    run(stack(parallel_fail_fast(concurrency=4), [rec.handler(), progress_bars(Bar)], recover_then_wait()))
    tail(rec)


def scenario_gc() -> None:
    """bar を検の側で持たない時(progress_bars を run ごとに作る ex08 の形)、失敗した run の bar は GC で閉じるか。"""
    import gc
    import weakref

    print("\n##### 限界 2: bar を誰も持たない時の後始末(ex08 の形)")
    for b in list(tqdm._instances):
        b.close()
    refs: tuple[weakref.ref, ...] = ()

    def make(*, total: int, desc: str):
        nonlocal refs
        bar = tqdm(total=total, desc=desc, file=io.StringIO())
        refs = (*refs, weakref.ref(bar))
        return bar

    try:
        run(stack(parallel_fail_fast(concurrency=1), [progress_bars(make)], one_stage(first, list(range(1, 9)))))
    except Exception:
        pass
    print("  except を抜けた直後: 生きている bar", sum(r() is not None for r in refs), "/ tqdm._instances", len(tqdm._instances))
    gc.collect()
    print("  gc.collect() の後:  生きている bar", sum(r() is not None for r in refs), "/ tqdm._instances", len(tqdm._instances))


@do
def tick(x: int):
    """件ごとに 50 ms 待つ(外から Cancel した時に件が走り続けるかを見るため)。"""
    yield Await(asyncio.sleep(0.05))
    return x


def scenario_cancel() -> None:
    """Traverse を走らせている task を外から Cancel した時、各 handler が件を止めるか(限界 2 の直し方の前提)。"""
    from doeff_core_effects.scheduler import Cancel, Spawn, Wait

    print("\n##### 外からの Cancel: Traverse を走らせる task を 120 ms で取り消す")
    two_at_a_time = {
        "sequential": sequential,
        "parallel(2)": lambda: parallel(concurrency=2),
        "parallel_fail_fast(2)": lambda: parallel_fail_fast(concurrency=2),
    }
    for name, strategy in two_at_a_time.items():
        header(f"[c {name}] items=1..8(各 50 ms)")
        rec = Recorder()

        @do
        def main():
            task = yield Spawn(one_stage(tick, list(range(1, 9))))
            yield Await(asyncio.sleep(0.12))
            yield Cancel(task)
            waited = yield Try(Wait(task))
            print("  Wait:", type(waited).__name__, waited if not hasattr(waited, "value") else type(waited.value).__name__)
            yield Await(asyncio.sleep(0.6))
            print("  Cancel の 0.6 秒後の reports:", [(r.item.index, r.outcome, type(r.item.value).__name__) for r in rec.seen])
            return None

        run(stack(strategy(), [rec.handler(), progress_bars(Bar)], main()))
        tail()


def scenario_terminal() -> None:
    """端末に出る文字(\\r・カーソル移動を含む)をそのまま出す。"""
    print("\n##### 端末への出力(2c の後に run3 を足した時の生の出力)")
    out = io.StringIO()

    def make(*, total: int, desc: str):
        return tqdm(total=total, desc=desc, file=out, ascii=True, ncols=60, mininterval=0)

    for b in list(tqdm._instances):
        b.close()
    bars = progress_bars(make)
    try:
        run(stack(parallel_fail_fast(concurrency=1), [bars], one_stage(first, list(range(1, 9)), label="run1")))
    except Exception:
        pass
    run(stack(parallel_fail_fast(concurrency=1), [bars], one_stage(second, [1, 2, 3], label="run2")))
    run(stack(sequential(), [bars], one_stage(second, [1, 2], label="run3")))
    print("--- 端末の画面(\\r・\\n・ESC[A を描き直した最後の姿)")
    for line in screen(out.getvalue()):
        print("  |" + line)


def screen(raw: str) -> list[str]:
    """tqdm が端末へ書いた文字列を、端末が最後に見せる行の並びに描き直す(\\r・\\n・ESC[A だけを解く)。"""
    rows: dict[int, str] = {}
    row, col = 0, 0
    i = 0
    while i < len(raw):
        if raw.startswith("\x1b[A", i):
            row, i = row - 1, i + 3
            continue
        ch = raw[i]
        if ch == "\r":
            col = 0
        elif ch == "\n":
            row, col = row + 1, 0
        else:
            line = rows.get(row, "").ljust(col)
            rows[row] = line[:col] + ch + line[col + 1 :]
            col += 1
        i += 1
    return [rows.get(r, "").rstrip() for r in range(max(rows, default=-1) + 1)]


if __name__ == "__main__":
    which = sys.argv[1:] or ["1", "2", "t"]
    if "1" in which:
        scenario_1()
    if "2" in which:
        scenario_2()
    if "3" in which:
        scenario_3()
    if "g" in which:
        scenario_gc()
    if "c" in which:
        scenario_cancel()
    if "t" in which:
        scenario_terminal()
