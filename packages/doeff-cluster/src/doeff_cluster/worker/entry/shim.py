"""job と worker の間に挟む見張り。job の子孫を引き取り、止める時・worker が消えた時・job が終わった時に片づけてから終わる。

worker は shim を新しい session(process group の先頭)として起動し、stdin をパイプでつなぐ。shim は job を同じ group の中で起動する。
shim は job を起こす前に自分を子孫の引き取り手にする(Linux の PR_SET_CHILD_SUBREAPER — #2940 の 2 段目)。job が別の session・process
group で起こした孫も、親が死ぬと PID 1 ではなく shim の子になる。shim の子の回収は main thread だけが行い(job が走っている間に引き取った
子が終われば、その場で回収する — zombie を溜めない)、job の終了コードは回収した時の status から読む。
3 つの道は、どれも main thread の同じ片づけ(settled)を 1 度だけ通ってから、job と同じ終了コードで終わる — 「shim が終わる ⇒ job の
子孫は 0」(条 C4b — packages/doeff-cluster/architecture.hy):
  (a) 止めの合図(worker が group へ送る TERM): 期限 = 合図 + 猶予(argv の 1 つ目 — worker の方針から worker/core/shim_timing の
      shim-spans が導く)を記し、job の終わりか期限の早い方まで待つ。
  (b) worker の消失(stdin の EOF — worker の kill -9 を含む): group へ TERM を送り(shim 自身にも届いて (a) と同じ期限が付く)、(a) と
      同じに待つ。worker の KILL は来ないので、shim が最後までやり切る。EOF を見る thread は合図を送るだけで回収しない。
  (c) job が自分で終わった: すぐ片づける(job が残した子孫 — 別の session の daemon を含む — も止める)。
片づけ: /proc を走査して PPid が shim の process を KILL → waitpid(-1, WNOHANG) で回収、を子が 0 になり waitpid が ECHILD を返すまで
繰り返す(KILL した子の子は shim に引き取られ、次の周で見つかる。期限までに終わらなかった job もここで KILL する)。走査・KILL・回収は
main thread でこの順に行う — 死んだ子は回収するまで zombie として pid を占めるので、pid の再利用で無関係な process を殺さない。
group へは KILL を送らない: shim 自身が group の先頭で、KILL は shim も止める(引き取った子孫には KILL の周で全部届く)。
引き取りを使えない時(Linux 以外・libc に prctl が無い・/proc を読めない — 部品 become_subreaper の答え。検は「使えない」と答える部品を
渡す): 理由を stderr に 1 行出し、今までの動き(process group への合図だけ)で続ける — 別の session の孫は止められない。期限を過ぎても
job が終わらなければ group へ KILL を送る(shim 自身も止まる)。
残る穴: shim 自身が外から KILL された時(worker の停止の猶予の後の KILL・手の kill -9)は、引き取った子孫が PID 1 へ逃げる。
macOS には親の死を子へ知らせる仕組み(Linux の PR_SET_PDEATHSIG)が無いので、worker の消失はパイプの EOF で知る。

job の出力(#3714): --stamp-lines の時は、job の stdout・stderr(worker が同じ log の file へ向けた物)を pipe で受け、1 行ごとに壁の
時計の刻の頭を付けて log へ書く(部品 worker/entry/line_stamp — 待ちの子から分かれた子 A と同じ部品)。旗が無ければ、job の出力は
worker の向け替えのまま(入口の検め — worker が stdout の行を読んで判じる)。

使い方: python -m doeff_cluster.worker.entry.shim <猶予秒> [--stamp-lines] -- <job の命令…>(#2028 でここへ移した — 旧い path の
doeff_cluster.shim は #2113 で消し、worker が送る名もこの名にした)
"""

import contextlib
import ctypes
import os
import select
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from types import FrameType

from doeff_cluster.worker.entry.line_stamp import OutputLines, RawLines, stamped_lines

# 層 entry の文脈と役の名乗り(DOEFF104・#2031)— 隣の入口 job_entry.hy と同じ。module は移さない(本番の worker が
# 版をまたいで名前で読む)。
MODULE_TAGS = {"context": "worker", "role": "main"}

# prctl の option の番号(linux/prctl.h の PR_SET_CHILD_SUBREAPER)— 子孫の引き取り手になる。
PR_SET_CHILD_SUBREAPER = 36
# 片づけの周の間の待ち(秒): KILL を届けた周は、子が死に切るのを短く待つ。届けられなかった周(残る子が KILL を受け付けない — 別の uid の
# setuid の子など)は、走査を詰めて CPU を使い切らないよう長く待つ(その子が自分で終わるのを待つ)。
SWEEP_PAUSE_SECONDS = 0.001
SWEEP_IDLE_SECONDS = 0.05


@dataclass(frozen=True)
class Adopting:
    """子孫の引き取りが効いている — 片づけで別の session の孫まで止められる。"""


@dataclass(frozen=True)
class NotAdopting:
    """子孫の引き取りを使えない — reason = 理由(stderr の 1 行に載せる)。止めは process group への合図だけ。"""

    reason: str


Adoption = Adopting | NotAdopting


@dataclass(frozen=True)
class Harvest:
    """終わった子を待たずに回収した結果: job_code = 回収した中に job が居ればその終了コード(signal で終わったなら負・居なければ None)・
    childless = 回収の後に子が 1 つも無い(waitpid が ECHILD を返した)。"""

    job_code: int | None
    childless: bool


class StopClock:
    """止めの期限(time.monotonic の秒)。TERM の handler だけが記す(Python の signal handler は main thread で走る)— 最初の合図の期限
    だけが効き、2 度目の合図で延びない。"""

    def __init__(self, grace: float) -> None:
        """止めの合図から job を待つ猶予(秒)を持ち、期限の無い(止めが始まっていない)状態で始めるため。"""
        self.grace = grace
        self._mut_deadline: float | None = None

    def begin(self, _signum: int, _frame: FrameType | None) -> None:
        """TERM の handler: 止めが始まった時刻から期限を記すため(main thread の待ちが読む)。"""
        if self._mut_deadline is None:
            self._mut_deadline = time.monotonic() + self.grace

    def deadline(self) -> float | None:
        """main thread の待ちが読む期限(止めが始まっていなければ None)を返すため。"""
        return self._mut_deadline


def become_subreaper() -> Adoption:
    """この process を子孫の引き取り手にする(job を起こす前に呼ぶ)。片づけは /proc の走査と libc の prctl に頼るので、どちらかが無ければ
    使えないと答える(OS の名では判じない)。"""
    if not os.path.exists("/proc/self/stat"):
        return NotAdopting("/proc を読めない")
    try:
        prctl = ctypes.CDLL(None, use_errno=True).prctl
    except (AttributeError, OSError) as missing:
        return NotAdopting(f"libc の prctl を読めない: {missing}")
    prctl.argtypes = (ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong)
    prctl.restype = ctypes.c_int
    if prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
        return NotAdopting(f"prctl(PR_SET_CHILD_SUBREAPER) が errno {ctypes.get_errno()} で断った")
    return Adopting()


def signal_wakeups() -> int:
    """TERM と SIGCHLD(子の終わり)が来たら読める fd を作り、その読む側を返す — main thread の待ち(select)を signal で起こすため。
    SIGCHLD は SIG_IGN にしない(子を回収できなくなる)— 起こすだけの handler を置く。"""
    readable, writable = os.pipe()
    os.set_blocking(readable, False)
    os.set_blocking(writable, False)
    signal.set_wakeup_fd(writable, warn_on_full_buffer=False)
    signal.signal(signal.SIGCHLD, lambda _signum, _frame: None)
    return readable


def drained(wake: int) -> None:
    """起こしの fd に溜まった印を読み捨てる(何が起きたかは、読み捨てた後に状態から読む)。"""
    with contextlib.suppress(BlockingIOError):
        while os.read(wake, 512):
            pass


def harvested(job: int) -> Harvest:
    """終わった子(引き取った孫を含む)を待たずに全部回収する — 回収は main thread だけが行う。"""
    job_code: int | None = None
    while True:
        try:
            pid, status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return Harvest(job_code=job_code, childless=True)
        if pid == 0:
            return Harvest(job_code=job_code, childless=False)
        if pid == job:
            job_code = os.waitstatus_to_exitcode(status)


def awaited(job: int, clock: StopClock, wake: int, leads_group: bool) -> int | None:
    """job の終わりか止めの期限の早い方まで待つ(main thread)。待つ間に終わった子はその場で回収する。答え = job の終了コード(期限が
    先なら None)。group の先頭でない shim は、止めが始まったら job へ TERM を回す(先頭なら合図は group で job にも届いている)。"""
    forwarded = leads_group
    while True:
        drained(wake)
        harvest = harvested(job)
        if harvest.job_code is not None:
            return harvest.job_code
        if harvest.childless:
            raise RuntimeError(f"shim: job {job} を回収する前に子が居なくなった")
        deadline = clock.deadline()
        if deadline is not None and not forwarded:
            os.kill(job, signal.SIGTERM)
            forwarded = True
        left = None if deadline is None else deadline - time.monotonic()
        if left is not None and left <= 0:
            return None
        select.select([wake], [], [], left)


def parent_of(pid: int) -> int | None:
    """/proc/<pid>/stat の PPid(読めない = もう居ない process は None)。comm に空白と括弧が入りうるので、最後の「)」の後を読む。"""
    try:
        with open(f"/proc/{pid}/stat", "rb") as handle:
            stat = handle.read()
    except OSError:
        return None
    return int(stat.rsplit(b")", 1)[1].split()[1])


def children_of(parent: int) -> tuple[int, ...]:
    """PPid が parent の process を /proc の走査で並べる(名前・session・group・環境では探さない)。"""
    return tuple(pid for pid in (int(name) for name in os.listdir("/proc") if name.isdigit()) if parent_of(pid) == parent)


def killed(pid: int) -> bool:
    """片づけの KILL を自分の子 1 つへ送るため。答え = 届けたか(KILL を受け付けない子 — 別の uid の setuid の子など — は False)。"""
    try:
        os.kill(pid, signal.SIGKILL)
    except PermissionError:
        return False
    return True


def swept(job: int, job_code: int | None) -> int:
    """片づけ(引き取りが効いている時): PPid が自分の process を KILL して回収する周を、子が 0 になり waitpid が ECHILD を返すまで
    繰り返す。走査 → KILL → 回収はこの順(回収するまで死んだ子は zombie で pid を占める — 再利用された pid を殺さない)。
    job_code = 待ちの間に回収した job の終了コード(期限が先なら None — job もここで KILL して回収する)。答え = job の終了コード。"""
    me = os.getpid()
    code = job_code
    while True:
        delivered = [pid for pid in children_of(me) if killed(pid)]
        harvest = harvested(job)
        code = code if harvest.job_code is None else harvest.job_code
        if harvest.childless:
            break
        time.sleep(SWEEP_PAUSE_SECONDS if delivered else SWEEP_IDLE_SECONDS)
    if code is None:
        raise RuntimeError(f"shim: 片づけの後も job {job} の終了コードが無い")
    return code


def group_killed(job: int, leads_group: bool) -> int:
    """引き取りを使えない時の期限切れ: group の先頭なら group へ KILL(shim 自身も止まる — 今までの動き)、先頭でなければ job へ KILL
    して回収する。答え = job の終了コード。"""
    if leads_group:
        os.killpg(os.getpid(), signal.SIGKILL)
    else:
        os.kill(job, signal.SIGKILL)
    _, status = os.waitpid(job, 0)
    return os.waitstatus_to_exitcode(status)


def settled(job: int, job_code: int | None, adoption: Adoption, leads_group: bool) -> int:
    """3 つの道が 1 度だけ通る片づけ(main thread)。答え = job の終了コード。引き取りが効いていれば子孫を全部片づける。使えなければ
    今までの動き: 期限を過ぎても job が終わっていなければ group へ KILL、終わっていれば何もしない。"""
    match adoption:
        case Adopting():
            return swept(job, job_code)
        case NotAdopting():
            return job_code if job_code is not None else group_killed(job, leads_group)


def watch_parent(leads_group: bool) -> None:
    """worker の消失(stdin の EOF — kill -9 を含む)を待ち、止めの合図を自分へ送る(group の先頭なら group へ — job にも届く)。期限と
    片づけは main thread が受け持つ(この thread は回収しない)。"""
    sys.stdin.buffer.read()
    print("shim: worker が消えたので job を止めます", file=sys.stderr, flush=True)
    if leads_group:
        os.killpg(os.getpid(), signal.SIGTERM)
    else:
        os.kill(os.getpid(), signal.SIGTERM)


class PopenSpawner:
    """job の命令を exec で起こす部品(本物の shim の起こし方)。呼ぶと job の pid を返す。起こした Popen の object は部品の中に終わりまで
    持つ(捨てると後始末が job を回収し、終了コードを奪う)— 部品は shim-code の呼びの間、呼び手が持っている。"""

    def __init__(self, command: list[str]) -> None:
        """job の命令を持ち、まだ起こしていない状態で始めるため。"""
        self.command = command
        self._mut_child: subprocess.Popen[bytes] | None = None

    def __call__(self) -> int:
        """job を同じ process group に起こし、pid を返すため(1 つの部品で 1 度だけ)。"""
        if self._mut_child is not None:
            raise RuntimeError("shim: 同じ部品で job を 2 度起こそうとした")
        self._mut_child = subprocess.Popen(self.command, stdin=subprocess.DEVNULL)
        return self._mut_child.pid


def shim_code(grace: float, spawn: Callable[[], int], lines: OutputLines, adopt: Callable[[], Adoption] = become_subreaper) -> int:
    """job を起こし、3 つの道のどれかで片づけるまで見張って、job の終了コード(signal で終わったなら負)を返すため。grace = 止めの合図から
    job を待つ猶予(秒)・spawn = job を起こして pid を返す部品(本物の shim は PopenSpawner — 待ちの子から分かれた子は、読み込み済みの
    入口を fork で起こす部品を渡す)・lines = job の出力の書き手(刻を付ける StampedLines か、そのまま運ぶ RawLines — 書き手の thread は
    job を起こした後に始め、片づけの後に残りを読み切ってから止める。job の終わりの判定と止めの合図には触れない)・adopt = 子孫の引き取りを
    有効にする部品(検は「使えない」と答える物を渡す — tests/fixtures/shim_without_adoption)。signal の据え付け(TERM・SIGCHLD・起こしの
    fd)は process 全体の設定なので、1 つの process で 1 回だけ、main thread から呼ぶ。返った後も stdin を読む補助の thread が残るので、
    呼び手は終了処理を経ずに os._exit で抜ける(exit-status)。"""
    try:
        adoption = adopt()
        match adoption:
            case NotAdopting(reason=reason):
                print(f"shim: 子孫の引き取りを使えないので、process group への合図だけで止めます({reason})", file=sys.stderr, flush=True)
            case Adopting():
                pass
        # group へ送ってよいのは shim が group の先頭の時だけ(worker は start_new_session で起動する)。
        # 先頭でなければ group は起動した側と共有なので、送ると起動した側まで止めてしまう — 子だけを止める。
        leads_group = os.getpgid(0) == os.getpid()
        clock = StopClock(grace)
        wake = signal_wakeups()
        signal.signal(signal.SIGTERM, clock.begin)
        # job は同じ process group に入る。書き手の thread は job を起こした後(fork の時の thread を 1 本に保つ)。
        job = spawn()
        lines.begin()
        threading.Thread(target=watch_parent, args=(leads_group,), daemon=True).start()
        return settled(job, awaited(job, clock, wake, leads_group), adoption, leads_group)
    finally:
        lines.end()


def exit_status(code: int) -> int:
    """job の終了コード(signal なら負)を、shim が os._exit に渡す 0〜255 の値にするため(signal は 128 + 番号)。"""
    return code if code >= 0 else 128 - code


def output_of(flags: list[str]) -> OutputLines:
    """猶予と「--」の間の旗から job の出力の書き手を決めるため(--stamp-lines = 1 行ごとに刻を付ける job の log・無し = そのまま)。
    知らない旗は名を挙げて断る(黙ってそのままの形に倒れない)。"""
    match flags:
        case []:
            return RawLines()
        case ["--stamp-lines"]:
            return stamped_lines()
        case _:
            raise SystemExit(f"shim: 知らない旗 {flags}(猶予と「--」の間に置けるのは --stamp-lines だけ)")


def main(adopt: Callable[[], Adoption] = become_subreaper) -> None:
    """argv の猶予・旗・job の命令で job を起こし、3 つの道のどれかで片づけてから job と同じ終了コードで終わる。adopt = 子孫の引き取りを
    有効にする部品(検は「使えない」と答える物を渡す — tests/fixtures/shim_without_adoption)。"""
    grace = float(sys.argv[1])
    split = sys.argv.index("--")
    lines = output_of(sys.argv[2:split])
    code = shim_code(grace, PopenSpawner(sys.argv[split + 1 :]), lines, adopt)
    # 終了処理を経ずに抜ける: stdin を読んでいる補助の thread が残ったまま終了処理に入ると、Python が
    # abort して終了コードが -6 に化ける(job 自身の終了コードを worker へ正しく返せない — 実測 2026-09-23)。
    sys.stderr.flush()
    os._exit(exit_status(code))


if __name__ == "__main__":
    main()
