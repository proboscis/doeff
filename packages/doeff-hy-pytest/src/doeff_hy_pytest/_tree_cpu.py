"""検の call の間の process の木の CPU 秒 — pytest の process と、その子孫の process の CPU 秒(user+sys)の和を測る
(agora-redesign card ki-ef6d57782313)。印ごとの壁の時計の上限(``doeff_test_call_budget_by_marker``)の代わりに、この値で判じると
宣言した検(``budget.declare_process_tree_cpu``)だけが使う。

何のためにあるか: 印の付いた検(real_world)は壁の時計で判じる。重さが子 process と外の待ちに在り、process の CPU 秒
(``time.process_time``)には出ないため(card ki-f35bb95eddda)。ところが重さが子 process の CPU に在る検(型検査の道具を子で撃つ検
など)の壁の時計は、検の中身が同じでも機体の混みで伸びる。日次 2026-10-22 04:00 JST の回(zeus・load 75〜104 / 36 core)で、
doeff-hy-check を子で撃つ検が壁の時計 33 秒・自分の CPU 0.96 秒で上限 30 秒を越え、上限を外すと緑だった。手元の測りでは
その伸びの多くは disk の待ち(blocked の process 53・io の PSI some 35〜54%)で、CPU の待ち(schedstat の run_delay)は伸びの
一部しか説明しない — 壁の時計から混みの分を引く形は I/O の混みを引けない。子孫の CPU 秒は検がした仕事の量で、混みでは
ほとんど変わらない。

測り方: 自分の process は ``time.process_time`` の差(読み手の thread の CPU は ``time.thread_time`` で引く)。子孫は間隔
``TICK_SECONDS`` ごとに /proc で木を辿り、process ごとの ``/proc/<pid>/stat`` の utime+stime を読み、最後に見た値から初めて
見た値(call の始めに居た process)か 0(後から生まれた process)を引いて足す。読みの間に終わった process の最後の分と、
読みの間に生まれて終わった短い process は数え落とすので、pytest の process が刈り取った子孫の CPU 秒(``RUSAGE_CHILDREN`` の差)
と比べて大きい方を採る(どちらも真の値の下限 — 足さないのは、同じ process を 2 度数えないため)。

読めない機体(``/proc`` の無い macOS)では測らず(``TreeCpuUnmeasured``)、呼び手は壁の時計で判じる — 今までの判定より
緩くならない。
"""

import os
import resource
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

# 読みの間隔(秒)。1 回の読みは /proc の pid の一覧と、新しく見えた pid と木の process の stat だけ(全部の process の stat を
# 毎回読むと 400 process で CPU 20 ms — 新しい pid の分だけ読めば 1 ms 前後)。
TICK_SECONDS = 0.1
PROC = Path("/proc")


@dataclass(frozen=True)
class TreeCpu:
    """call の間の process の木の CPU 秒 — own = 自分の process(読み手の thread を除く)・descendants = 子孫の process の和・
    processes = 読みで見た子孫の process の数。"""

    own_seconds: float
    descendants_seconds: float
    processes: int

    @property
    def seconds(self) -> float:
        """木の全体の CPU 秒(自分と子孫の和)。"""
        return self.own_seconds + self.descendants_seconds


@dataclass(frozen=True)
class TreeCpuUnmeasured:
    """木の CPU 秒を測れない(理由つき)— 呼び手は壁の時計で判じる。"""

    reason: str


@dataclass(frozen=True)
class ProcessStat:
    """/proc の stat の 1 行から読む 2 つ — 親の pid と、その process の CPU 秒(user+sys・終わった thread の分も含む)。"""

    parent: int
    cpu_seconds: float


def parse_stat(text: str) -> ProcessStat:
    """純粋: /proc/<pid>/stat の 1 行を読む(名の欄は括弧の中に空白や括弧を持ちうるので、最後の閉じ括弧の後ろを数える)。"""
    rest = text[text.rindex(")") + 2 :].split()
    return ProcessStat(
        parent=int(rest[1]), cpu_seconds=(int(rest[11]) + int(rest[12])) / os.sysconf("SC_CLK_TCK")
    )


@dataclass(frozen=True)
class ProcessGone:
    """読もうとした process は、もう居ない(読みの間に終わった — 数えない)。"""

    pid: int


def read_stat(pid: int) -> ProcessStat | ProcessGone:
    """1 つの process の stat を読む(file が無い・読めない = 終わった process)。形の読めない行は ValueError で上へ返す
    (読み手は測れないと名乗る — 0 秒と読まない)。"""
    try:
        text = (PROC / str(pid) / "stat").read_text()
    except OSError:
        return ProcessGone(pid)
    try:
        return parse_stat(text)
    except (ValueError, IndexError) as error:
        raise ValueError(f"{PROC / str(pid) / 'stat'} の形が読めない: {text[:120]!r}") from error


def descendants_of(root: int, parents: Mapping[int, int]) -> frozenset[int]:
    """純粋: pid → 親の pid の表から、root の子孫の pid の組を作る(root は含めない)。"""
    tree = {root}
    grew = True
    while grew:
        grew = False
        for pid, parent in parents.items():
            if parent in tree and pid not in tree:
                tree.add(pid)
                grew = True
    return frozenset(tree - {root})


def descendants_cpu(first: Mapping[int, float], last: Mapping[int, float]) -> float:
    """純粋: 子孫ごとの初めて見た CPU 秒(call の始めに居た process だけ — 後から生まれた process は 0)と最後に見た CPU 秒から、
    call の間に子孫が使った CPU 秒の和を出す。"""
    return sum(seconds - first.get(pid, 0.0) for pid, seconds in last.items())


def unmeasurable_reason() -> str | None:
    """この機体で木の CPU 秒を読めない理由(読めるなら None)。"""
    try:
        own = read_stat(os.getpid())
    except ValueError as error:
        return str(error)
    if isinstance(own, ProcessGone):
        return f"{PROC / str(os.getpid()) / 'stat'} を読めない"
    return None


class TreeCpuSampler:
    """call の間だけ thread で子孫の CPU 秒を読む。start → stop の 1 回だけ使う。"""

    def __init__(self, tick_seconds: float = TICK_SECONDS) -> None:
        self._tick = tick_seconds
        self._stop = threading.Event()
        self._root = os.getpid()
        self._mut_parents: dict[int, int] = {}
        self._mut_first: dict[int, float] = {}
        self._mut_last: dict[int, float] = {}
        self._mut_reader_cpu = 0.0
        self._mut_thread: threading.Thread | None = None
        self._mut_own_started = 0.0
        self._mut_reaped_started = 0.0
        self._mut_failure: str | None = None

    def start(self) -> TreeCpuUnmeasured | None:
        """読みを始める(読めない機体では始めずに理由を返す)。始めに居た子孫の CPU 秒を初めの値として読んでから返す。"""
        reason = unmeasurable_reason()
        if reason is not None:
            return TreeCpuUnmeasured(reason)
        try:
            self._read(starting=True)
        except (OSError, ValueError) as error:
            return TreeCpuUnmeasured(
                f"子孫の CPU 秒を読めない({error.__class__.__name__}: {error})"
            )
        self._mut_reaped_started = _reaped_children_cpu()
        self._mut_own_started = time.process_time()
        self._mut_thread = threading.Thread(
            target=self._run, name="doeff-hy-pytest-tree-cpu", daemon=True
        )
        self._mut_thread.start()
        return None

    def stop(self) -> TreeCpu | TreeCpuUnmeasured:
        """読みを止め(最後にもう 1 度読む)、call の間の木の CPU 秒を返す。"""
        if self._mut_thread is None:
            return TreeCpuUnmeasured("読みを始めていない")
        own = time.process_time() - self._mut_own_started
        reaped = _reaped_children_cpu() - self._mut_reaped_started
        self._stop.set()
        self._mut_thread.join()
        if self._mut_failure is None:
            self._guarded_read()
        if self._mut_failure is not None:
            return TreeCpuUnmeasured(self._mut_failure)
        sampled = descendants_cpu(self._mut_first, self._mut_last)
        return TreeCpu(
            own_seconds=max(0.0, own - self._mut_reader_cpu),
            descendants_seconds=max(sampled, reaped),
            processes=len(self._mut_last),
        )

    def _run(self) -> None:
        """読みの thread の本体 — 止めの合図まで間隔ごとに子孫を読み、自分の thread の CPU 秒を控える(検の CPU から引くため)。"""
        while self._mut_failure is None and not self._stop.wait(self._tick):
            self._guarded_read()
        self._mut_reader_cpu = time.thread_time()

    def _guarded_read(self) -> None:
        """読みを 1 度して、/proc の形が読めなかった時は理由を控える(stop が測れないと名乗る — 黙って 0 秒にしない)。"""
        try:
            self._read(starting=False)
        except (OSError, ValueError) as error:
            self._mut_failure = f"子孫の CPU 秒を読めない({error.__class__.__name__}: {error})"

    def _read(self, starting: bool) -> None:
        """/proc を 1 度読み、子孫の CPU 秒の最後の値を改める(starting = call の始めの読み — 居た子孫の値を初めの値に置く)。
        親の pid は新しく見えた pid だけ読み、消えた pid は表から外す(同じ pid が使い回されても別の process として読み直す)。"""
        present = {int(name) for name in os.listdir(PROC) if name.isdigit()}
        for gone in self._mut_parents.keys() - present:
            del self._mut_parents[gone]
        for pid in present - self._mut_parents.keys():
            match read_stat(pid):
                case ProcessStat(parent=parent):
                    self._mut_parents[pid] = parent
                case ProcessGone():
                    pass
        for pid in descendants_of(self._root, self._mut_parents):
            match read_stat(pid):
                case ProcessStat(cpu_seconds=seconds):
                    if starting:
                        self._mut_first[pid] = seconds
                    self._mut_last[pid] = max(self._mut_last.get(pid, 0.0), seconds)
                case ProcessGone():
                    pass


def _reaped_children_cpu() -> float:
    """この process が刈り取った子孫の CPU 秒の累計(user+sys)。"""
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    return usage.ru_utime + usage.ru_stime
