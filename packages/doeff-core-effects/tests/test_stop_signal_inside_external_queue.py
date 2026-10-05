"""止めの合図(SIGTERM)が、scheduler の外からの完了の列の操作の中に居る主 thread に当たっても、AwaitStop の待ちが起きて run が
終わる事の検(agora-redesign #3584)。

道筋(本物): os-signal-stop-handler が据える受け手 StopBox.receive は、主 thread の上で bytecode の区切りに割り込んで走り、寝ている
AwaitStop の外の約束(ExternalPromise)の complete を呼ぶ。complete は scheduler の外からの完了の列(_scheduled_python の
external_queue)へ put する。主 thread はその同じ列を drain(empty・get)と _drain_one_external(get(timeout=…))で読む。受け手の
put が、同じ thread が列の操作の中で持っている lock(入れ子で取れない)を待つと、process は自分を待ったまま止まり、後から来る
SIGTERM でも抜けない(cluster では KILL の 137)。

作り方: 子 process で本物の組み(scheduled の python の実装・state・os-signal-stop-handler)を回し、AwaitStop の待ちを 1 つ寝かせて
その答えを待つ。合図を当てる所は 2 種類:
  * 列の操作の中の瞬間 — 待ちが寝た後、主 thread が列の操作(列を self に持つ frame とその下)で Python の行を走らせる瞬間を
    sys.settrace で数え、k 番目の瞬間に signal.raise_signal(SIGTERM) を撃つ(本物の合図が bytecode の区切りに当たった時と同じ所で
    受け手が走る)。子は k = 0, 1, … と 1 回ずつ新しい run で当てる。
  * 列の get の中で塞がっている間 — 列の操作の中の瞬間を使い切った run では、主 thread の stack が _drain_one_external の下で
    止まった(塞がった)のを別の thread が見て、signal.pthread_kill で当てる。列の操作が C で書かれていれば、操作の中で Python が
    走るのは、塞がった get が合図で割り込まれて受け手を呼ぶこの所だけ。
判じる事: 子が上限の内に終わり、どの当て所でも待ちが合図の理由で起き、最後は _drain_one_external の get で塞がっている間に当てた。
子が止まった時は、最後に当てた所(file:行 関数)を赤の文に出す。

直す前(queue.Queue)は、列の操作の中の 2 番目の瞬間(drain の empty の `with self.mutex:` の内側)で受け手の put が同じ lock を
待って止まる。直した後(queue.SimpleQueue — put は reentrant で signal の受け手から呼んでよい)は列の操作の中に Python の瞬間が無く、
塞がった get の中で受け手の put が走って get が起きる事を判じる(この repo の既定の free-threading の Python の上で)。
Rust の実装(implementation="rust")の列は Python の外の Mutex で、この検の対象の外。
"""

from __future__ import annotations

import signal
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from types import FrameType

import hy  # noqa: F401  - Hy の module(stop_signal_handlers.hy ほか)を import できるようにする
import pytest
from doeff_core_effects.handlers import state
from doeff_core_effects.scheduler import Spawn, Wait, scheduled
from doeff_core_effects.stop_signal_effects import AwaitStop
from doeff_core_effects.stop_signal_handlers import StopBox, os_signal_stop_handler

from doeff import do, run, with_handlers

#: この file を子 process の入口にも使う旗。
CHILD_FLAG = "--child"
#: 子の時間の上限(秒)。import に数秒・当てる run は 1 回ずつ 1 秒未満。止まった子はこの上限で KILL する(SIGTERM では抜けない)。
CHILD_LIMIT_SECONDS = 30.0
#: 塞がりを見る間隔(秒)と、主 thread の stack が同じ所に続けて止まっていれば塞がったと見る回数(0.02 × 25 = 0.5 秒)。
POLL_SECONDS = 0.02
BLOCKED_SAMPLES = 25
#: 列の操作の中の瞬間を数える上限(使い切らずに上限に当たれば子を赤で終える)。
MOMENT_LIMIT = 500
#: 塞がりで当てた時に主 thread の stack に在るべき関数 — scheduler が外からの完了を待って列の get で塞がる所。
BLOCKING_READ = "_drain_one_external"
#: 本物の受け手(StopBox.receive)が SIGTERM で書く理由。
STOP_REASON = f"signal {int(signal.SIGTERM)}"

TraceFunction = Callable[[FrameType, str, object], object]


class Delivery:
    """1 回の run で合図を当てる所と当てた結果の、ただ 1 つの書き換わる箱(主 thread の tracer と、塞がりを見る thread が書く)。
    target = 列の操作の中の何番目の瞬間に当てるか・seen = 数えた瞬間の数・where = 当てた所(まだなら None)。"""

    __slots__ = ("seen", "target", "where")

    def __init__(self, target: int) -> None:
        self.target = target
        self.seen = 0
        self.where: str | None = None


def _parked_queue() -> object | None:
    """止めの受け手が寝ている待ちを持つ間、受け手が put する列(待ちの外の約束が持つ列)。それ以外は None。"""
    box = getattr(signal.getsignal(signal.SIGTERM), "__self__", None)
    if not isinstance(box, StopBox) or box.reason is not None or not box.waiters:
        return None
    # 受け手の put の行き先そのものを見る — ExternalPromise は列を公開の名で出さない(scheduler の内側の値)。
    return box.waiters[0]._queue  # noqa: SLF001


def _outward(frame: FrameType | None) -> Iterator[FrameType]:
    """frame から外へ(呼び手の方へ)たどる stack。"""
    while frame is not None:
        yield frame
        frame = frame.f_back


def _inside_operation_of(frame: FrameType | None, queue: object) -> bool:
    """frame が列の操作(列を self に持つ frame)の中かその下で走っているか。"""
    return any(outer.f_locals.get("self") is queue for outer in _outward(frame))


def _place(frame: FrameType) -> str:
    return f"{Path(frame.f_code.co_filename).name}:{frame.f_lineno} {frame.f_code.co_name}"


def _stack_names(frame: FrameType | None) -> str:
    """主 thread の stack の関数の名を、外の BLOCKING_READ から内へ(無ければ内の 5 つ)。"""
    names = tuple(outer.f_code.co_name for outer in _outward(frame))
    shown = names[: names.index(BLOCKING_READ) + 1] if BLOCKING_READ in names else names[:5]
    return " > ".join(reversed(shown))


@dataclass(frozen=True)
class OperationTracer:
    """待ちが寝た後、主 thread が列の操作の中で Python の行を走らせる瞬間を数え、delivery.target 番目で合図を撃つ。"""

    delivery: Delivery

    def on_call(self, frame: FrameType, event: str, arg: object) -> TraceFunction | None:
        del event, arg
        if self.delivery.where is not None:
            return None
        queue = _parked_queue()
        if queue is None or not _inside_operation_of(frame, queue):
            return None
        return self.on_line

    def on_line(self, frame: FrameType, event: str, arg: object) -> TraceFunction:
        del arg
        if event == "line" and self.delivery.where is None:
            if self.delivery.seen == self.delivery.target:
                self.delivery.where = f"inside {_place(frame)}"
                print(f"deliver {self.delivery.target} {self.delivery.where}", flush=True)
                signal.raise_signal(signal.SIGTERM)
            self.delivery.seen += 1
        return self.on_line


def _deliver_when_blocked(delivery: Delivery, main: int, finished: threading.Event) -> None:
    """待ちが寝て、主 thread の stack が BLOCKED_SAMPLES 回続けて同じ所(BLOCKING_READ の下)に止まっていたら、主 thread へ
    SIGTERM を送る。列の操作の中で既に当てた run では何もしない。frame は id でなく object そのものを持って is で比べる。
    間隔で見る訳: 「別の thread が塞がった」を知らせる出来事は無く、塞がりは stack が動かない事でしか見えない(検の道具だけの形)。"""
    last: FrameType | None = None
    last_offset = -1
    same = 0
    while not finished.wait(POLL_SECONDS):
        if delivery.where is not None:
            return
        frame = sys._current_frames().get(main)  # noqa: SLF001 - 別の thread の stack を読む口は これだけ
        if frame is None or _parked_queue() is None or BLOCKING_READ not in _stack_names(frame):
            last, last_offset, same = None, -1, 0
            continue
        same = same + 1 if (frame is last and frame.f_lasti == last_offset) else 0
        last, last_offset = frame, frame.f_lasti
        if same >= BLOCKED_SAMPLES:
            delivery.where = f"blocked {_stack_names(frame)}"
            print(f"deliver {delivery.target} {delivery.where}", flush=True)
            signal.pthread_kill(main, signal.SIGTERM)
            return


@do
def _await_stop_once():
    reason = yield AwaitStop()
    return reason


@do
def _park_then_wait():
    waiter = yield Spawn(_await_stop_once())
    reason = yield Wait(waiter)
    return reason


def _run_once(target: int) -> Delivery:
    """本物の組みを 1 回回し、列の操作の中の target 番目の瞬間(無ければ塞がった get の中)で SIGTERM を当てる。"""
    delivery = Delivery(target)
    finished = threading.Event()
    helper = threading.Thread(
        target=_deliver_when_blocked, args=(delivery, threading.get_ident(), finished), daemon=True
    )
    helper.start()
    sys.settrace(OperationTracer(delivery).on_call)
    try:
        reason = run(
            scheduled(with_handlers([state(), os_signal_stop_handler], _park_then_wait()), implementation="python")
        )
    finally:
        sys.settrace(None)
        finished.set()
        helper.join()
    print(f"woke {target} {reason!r}", flush=True)
    return delivery


def _child() -> int:
    """k = 0, 1, … と 1 回ずつ当て、塞がった get の中で当てた run(列の操作の中の瞬間を使い切った)で終える。"""
    for target in range(MOMENT_LIMIT):
        delivery = _run_once(target)
        if delivery.where is None:
            print(f"undelivered {target}", flush=True)
            return 1
        if delivery.where.startswith("blocked"):
            print(f"moments {target}", flush=True)
            return 0
    print(f"moments-exceeded {MOMENT_LIMIT}", flush=True)
    return 1


def _text(output: str | bytes | None) -> str:
    if output is None:
        return ""
    return output.decode(errors="replace") if isinstance(output, bytes) else output


def test_a_stop_signal_inside_the_external_queue_operations_wakes_the_wait() -> None:
    command = [sys.executable, str(Path(__file__).resolve()), CHILD_FLAG]
    try:
        finished = subprocess.run(command, capture_output=True, text=True, timeout=CHILD_LIMIT_SECONDS, check=False)
    except subprocess.TimeoutExpired as stuck:
        pytest.fail(
            f"子が {CHILD_LIMIT_SECONDS:.0f} 秒の内に終わらない — 最後に当てた SIGTERM の受け手(StopBox.receive → "
            "ExternalPromise.complete → 外からの完了の列の put)が戻らない。主 thread が列の操作の中で持つ lock を同じ thread の"
            f"受け手が待つと、こう止まる(cluster では KILL の 137)。子の出力:\n{_text(stuck.stdout)}{_text(stuck.stderr)[-2000:]}"
        )
    out = finished.stdout
    assert finished.returncode == 0, f"子の rc {finished.returncode}:\n{out}\n{finished.stderr[-4000:]}"
    lines = out.splitlines()
    delivered = [line for line in lines if line.startswith("deliver ")]
    woke = [line for line in lines if line.startswith("woke ")]
    assert delivered, f"合図を 1 度も当てていない:\n{out}"
    assert len(woke) == len(delivered), f"当てた {len(delivered)} 回のうち待ちが起きたのは {len(woke)} 回:\n{out}"
    assert all(line.endswith(repr(STOP_REASON)) for line in woke), f"待ちの答えが {STOP_REASON!r} でない:\n{out}"
    assert f"blocked {BLOCKING_READ}" in delivered[-1], f"最後の合図を {BLOCKING_READ} の get で塞がっている間に当てていない:\n{out}"


if __name__ == "__main__":
    if sys.argv[1:] != [CHILD_FLAG]:
        raise SystemExit(f"使い方: python {Path(__file__).name} {CHILD_FLAG}")
    raise SystemExit(_child())
