"""起こした process が終わったら、その子も終わる — StartProcess の約束の本体(agora-redesign #3866)。標準ライブラリだけで書く。

本体は 1 つ: 起こした側だけが書き口を持つ pipe を行ごとに読み、EOF(起こした側の終わり — SIGKILL を含む)で、持っている相手へ TERM を
送る(watched_until_eof)。置き方は 2 つで、どちらもこの本体を呼ぶ。
  * 起こす側の process ごとの見張り(この file を入口として起こす・StartProcess の本物の答え手 os_process.hy が最初の StartProcess の時に
    1 つだけ起こす): 起こす側は子を立てるたびに「group <番号>」か「pid <番号>」の行で知らせ、回収した子は「forget <番号>」で外す。
    EOF で知らされた相手へ TERM を送り、猶予(GRACE_SECONDS)の内に先頭の process が居なくなるのを待って、残る group と process へ KILL を
    送ってから終わる。2 つ目からの StartProcess は 1 行書くだけで、起動の時間は乗らない。
  * doeff-cluster の worker の shim(job ごと・job の祖先): 自分の標準入力の EOF で自分の group へ TERM を送る。pipe は fork の前から
    在るので隙間が無い。猶予と子孫の引き取りの片づけは、job の祖先である shim の main thread が受け持つ(見張りは子の祖先でないので
    引き取りを使えない)。
起こす側の見張りに残る隙間: fork から「group <番号>」を書くまでの間(1 ms 未満)に起こした側が SIGKILL されると、その子は知らされずに残る
(閉じ方は #3866 の記録 — 今回は閉じない)。

置き場は doeff-core-effects の配布の中の、doeff_core_effects と別の top-level の package: doeff_core_effects の package を読むと起動に約 100 ms
乗る(2026-10-07 の実測)ので、job ごとに起こす shim がこの本体だけを読めるように分けた。見張りは `python -I -S -B <この file>` で起こす
(標準ライブラリだけ — 起動は最小の python と同じ)。
"""


import contextlib
import os
import select
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from io import BufferedReader

MODULE_TAGS = {"context": "process", "role": "foundation"}

# EOF の後、TERM を送った相手が居なくなるのを待つ秒(StopProcess の既定の stop-grace と同じ)。過ぎて残る相手には KILL を送る。
GRACE_SECONDS = 10.0


@dataclass(frozen=True)
class Group:
    """止める相手の 1 つ: process group(番号 = 先頭の pid)。TERM と KILL は group 全体へ送る。"""

    pgid: int


@dataclass(frozen=True)
class Single:
    """止める相手の 1 つ: 起こす側の group に入ったままの子 process 1 つ。TERM と KILL はその process だけへ送る。"""

    pid: int


Target = Group | Single


def signalled(target: Target, signum: int) -> None:
    """相手へ signal を送るため。もう居ない相手と、送れない相手(別の uid)は黙って飛ばす — 止める物が無い。"""
    with contextlib.suppress(ProcessLookupError, PermissionError):
        match target:
            case Group(pgid=pgid):
                os.killpg(pgid, signum)
            case Single(pid=pid):
                os.kill(pid, signum)


def watched_until_eof(
    stream: BufferedReader, on_line: Callable[[bytes], None], ended: Callable[[], Iterable[Target]]
) -> None:
    """本体(頭の註): 起こした側の pipe を行ごとに読んで on_line へ渡し、EOF で ended を 1 度呼んで、答えた相手の全部へ TERM を送る。
    行を読むだけで時間を置いて見に行かない — 起こした側の終わりが EOF としてそのまま届く。"""
    for line in iter(stream.readline, b""):
        on_line(line)
    for target in tuple(ended()):
        signalled(target, signal.SIGTERM)


class Registry:
    """見張りが知らされた相手の表(番号 → 相手)。見張りの process の中だけで、読む thread は 1 本なので錠は要らない。"""

    def __init__(self) -> None:
        """空の表で始めるため。"""
        self._mut_targets: dict[int, Target] = {}

    def took(self, line: bytes) -> None:
        """起こす側の 1 行を表へ写すため(group / pid = 足す・forget = 外す)。知らない形の行は名を挙げて落ちる(黙って捨てない)。"""
        match line.decode("ascii").split():
            case ["group", number]:
                self._mut_targets[int(number)] = Group(int(number))
            case ["pid", number]:
                self._mut_targets[int(number)] = Single(int(number))
            case ["forget", number]:
                self._mut_targets.pop(int(number), None)
            case _:
                raise SystemExit(f"lifeline: 知らない行 {line!r}")

    def targets(self) -> tuple[Target, ...]:
        """今の相手の全部を返すため。"""
        return tuple(self._mut_targets.values())


def leader_of(target: Target) -> int:
    """相手の先頭の pid(group の番号は先頭の pid と同じ)。"""
    match target:
        case Group(pgid=pgid):
            return pgid
        case Single(pid=pid):
            return pid


def pidfd_of(target: Target) -> int | None:
    """相手の先頭の pidfd(終わると読める fd)。もう居ない相手は None(待つ物が無い)。"""
    try:
        return os.pidfd_open(leader_of(target))
    except ProcessLookupError:
        return None


def ended_within(targets: tuple[Target, ...], grace: float) -> None:
    """TERM を送った相手の先頭が、猶予の内に居なくなるのを待つため。Linux は pidfd を select で待つので、全部が終われば猶予を待たずに
    返る。pidfd の無い OS(macOS)は猶予の分だけ待つ(待つ間に見に行かない)。"""
    if not hasattr(os, "pidfd_open"):
        time.sleep(grace)
        return
    pidfds = tuple(fd for fd in (pidfd_of(target) for target in targets) if fd is not None)
    deadline = time.monotonic() + grace
    waiting = pidfds
    while waiting:
        left = deadline - time.monotonic()
        if left <= 0:
            break
        readable, _, _ = select.select(waiting, [], [], left)
        waiting = tuple(fd for fd in waiting if fd not in readable)
    for fd in pidfds:
        os.close(fd)


def guard(stream: BufferedReader, grace: float) -> None:
    """起こす側の process ごとの見張りの本体(頭の註): EOF まで相手を表に写し、EOF で TERM → 猶予 → 残る相手へ KILL。"""
    registry = Registry()
    watched_until_eof(stream, registry.took, registry.targets)
    targets = registry.targets()
    ended_within(targets, grace)
    for target in targets:
        signalled(target, signal.SIGKILL)


class Lifeline:
    """起こす側の口(process に 1 つ — os_process.hy の STARTED-CHILDREN と並ぶ): 最初に子を知らせる時に見張りを 1 つ起こし、その標準入力の
    書き口だけを持つ。書き口は他の子へ継がせない(os.pipe の口は継がない設定 — Popen の close_fds も閉じる)。書きは子を立てる thread と
    回収する thread が並んで行うので 1 つの錠の下。資源の係なので値の型ではない。"""

    def __init__(self) -> None:
        """見張りを起こしていない状態で始めるため。"""
        self._mut_lock = threading.Lock()
        self._mut_guard: subprocess.Popen[bytes] | None = None

    def _written(self, line: str) -> None:
        """見張りへ 1 行書くため(錠の下で呼ぶ)。最初の 1 行の前に見張りを起こす — 自分の session(起こす側の group へ送られた signal で
        見張りが一緒に止まらない)・標準入力 = 書き口を起こす側だけが持つ pipe。"""
        if self._mut_guard is None:
            # 見張りの interpreter は、いま動いている interpreter の本体(sys._base_executable)。sys.executable は起動口が書き換える
            # (Hy の入口は hy の起動口にする — doeff-cluster の worker は `exec hy -m …` で起き、見張りが `hy -I -S -B <file>` として
            # 起きて即座に終わっていた)。見張りは標準ライブラリだけなので、venv の外の本体で足りる。
            self._mut_guard = subprocess.Popen(
                [sys._base_executable, "-I", "-S", "-B", __file__],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                start_new_session=True,
            )
        guard_stdin = self._mut_guard.stdin
        if guard_stdin is None:
            raise RuntimeError("lifeline: 見張りの標準入力の pipe が無い")
        guard_stdin.write(line.encode("ascii"))
        guard_stdin.flush()

    def watch(self, pid: int, process_group: bool) -> None:
        """立てた子を見張りへ知らせるため(自分の group の子は group ごと、起こす側の group に入った子はその process だけ)。"""
        with self._mut_lock:
            self._written(f"{'group' if process_group else 'pid'} {pid}\n")

    def forget(self, pid: int) -> None:
        """回収した子を見張りの表から外すため(回収した pid は使い回されうるので、EOF の時に止めない)。見張りを起こしていなければ何もしない。"""
        with self._mut_lock:
            if self._mut_guard is not None:
                self._written(f"forget {pid}\n")


if __name__ == "__main__":
    guard(sys.stdin.buffer, GRACE_SECONDS)
