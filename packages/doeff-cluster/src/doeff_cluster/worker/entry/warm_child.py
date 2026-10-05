"""待ちの子: 実行環境の root ごとに 1 つ、module の読み込みだけを済ませて unix socket で待ち、頼みが来たら fork して入口を走らせる常駐の子
(#3646 — task の子の起動の約 2 秒は、ほぼ全部が module の読み込みだった)。

worker は root が READY になったら、この入口を root の venv で起こす(今の task の子と同じ `uv run --no-sync --frozen --project <root> …`)。
待ちの子は、入口(job_entry)と --preload に名指された module(実行環境の宣言の bytecodeEntries — 速さの手がかり)を読み込み、
socket を開いて準備完了の印を書き、頼みを待つ。頼み 1 つ = task の子 1 本:
  待ちの子 ── fork ──▶ A(setsid で group の先頭・子孫の引き取り手・log へ dup2・env と cwd を整え、shim と同じ見張り shim_code)
                          └ fork ──▶ B(読み込み済みの入口の main を同じ process の中で走らせる)
  A の log は task の log なので、shim の --stamp-lines と同じ部品(worker/entry/line_stamp)で 1 行ごとに壁の時計の刻を付ける(#3714)。
  B は書き手の fd を閉じてから入口を走らせる。待ちの子自身の log(warm-<root のキー>.log)には付けない — 待ちの子は fork のために
  thread を 1 本に保つので、書き手の thread を置けない。
  A は setsid を済ませたら待ちの子へ 1 byte で知らせ、job の終了コードを exit の file へ置き換えで書いてから、shim と同じ値で終わる。
  待ちの子は、その知らせを受けてから A の pid と起動の刻(/proc の starttime — pid の使い回しを見分ける)を答える(答えの直後の
  合図が、まだ group の先頭でない A に届いて ESRCH にならないように)。
頼みと答えの 1 行は doeff_core_effects.os_warm_process の約束(WarmRequestWire・WarmForkedWire・WarmRefusedWire)で、読み書きは
同じ module の warm-request-of・warm-answer-line を使う(綴りを 2 つ持たない)。

守る事(この形を決めた時の条件・2026-10-05):
- 待ちの子は module の読み込みだけを済ませた状態で待つ — 接続・thread・event loop・資格・env の値を持たない。読み込みの後に thread が
  2 本以上なら、fork できないので名を挙げて起動を失敗にする。doeff の VM は読み込むだけでは作られない(準備完了の印に数を残し、
  worker の判断の層が「thread 1 本・VM 0 個」の時だけ準備済みに数える)。
- env の値は頼みに載って届き、A の中でだけ置く。待ちの子の log・答え・断りの文に env の値を書かない(形の違う頼みの断りは欄の場所だけ)。
- socket は umask 077 の下で作る(作る時の mode だけ — 相手の身元を確かめる分岐は足さない)。古い root の待ちの子から分かれないのは、
  socket の置き場を root のキーから導く worker の側の作りで守る(待ちの子は 1 つの root だけを読み込んでいる)。
- 待ちの子が落ちても、走っている A と B は落ちない(A は別の session・終わりは exit の file で worker が読む)。
- worker が消えたら(stdin の EOF)、待ちの子は終わる。走っている A は、shim と同じく自分の stdin の EOF で job を止める。
- TERM は B を fork する前に block し、B は signal の扱いを既定に戻してから unblock する(戻す前に届いた TERM を落とさない)。

使い方: python -m doeff_cluster.worker.entry.warm_child --root <root の dir> --socket <socket の path> --ready <準備完了の印の path>
        [--preload <module の名>]…
"""

import argparse
import contextlib
import importlib
import json
import os
import select
import signal
import socket
import sys
import traceback
from collections.abc import Callable
from types import ModuleType
from typing import NoReturn

import hy  # noqa: F401 — Hy の importer を据える(入口と、前もって読む module の多くは Hy)
from doeff import run
from doeff_core_effects.os_warm_process import WarmRequestWire, warm_answer_line, warm_request_of
from doeff_core_effects.warm_effects import WarmForked, WarmRefused
from doeff_hy.wire import Malformed

from doeff_cluster.foundation.child_environ import replace_environ
from doeff_cluster.worker.entry.line_stamp import stamped_lines
from doeff_cluster.worker.entry.shim import become_subreaper, exit_status, shim_code

# 層 entry の文脈と役の名乗り(DOEFF104)— 隣の shim.py と同じ。
MODULE_TAGS = {"context": "worker", "role": "main"}

# worker の子の入口(worker/protocol/declared.hy の JOB-ENTRY と同じ名)— 前もって必ず読む。
JOB_ENTRY = "doeff_cluster.worker.entry.job_entry"
# 頼み 1 行の上限(byte)— env の値を含むが、数 MB は要らない。越えた頼みは断る。
REQUEST_LIMIT_BYTES = 1 << 20
# 1 つの頼みを読み終えるまでと、分かれた子 A の setsid の知らせを待つ期限(秒)— 待ちの子が止まらないように。
REQUEST_TIMEOUT_SECONDS = 5.0
# 読み込みの失敗・thread の残りで起動を断った時の終了コード。
START_REFUSED_CODE = 3
# /proc/<pid>/stat の「)」の後の欄の並びで、起動の刻 starttime(全体の 22 番目)の位置(「)」の後は 3 番目の欄から始まる)。
STARTTIME_INDEX = 22 - 3
# A が setsid を済ませた時に待ちの子へ送る 1 byte。
SESSION_LED = b"1"


def module_named(name: str) -> ModuleType:
    """名の module を読み込むため — 待ちの子の役目の口(入口と前もって読む module は、実行環境の宣言と頼みから名で届く)。動的な読み込みは
    この 1 か所だけ(.agents/code-quality.d/doeff-cluster-warm-child.json の dynamic_imports)。"""
    return importlib.import_module(name)


def entry_main(name: str) -> Callable[..., object]:
    """入口の module の main を引くため(入口は argv を読む main を持つ — job_entry と同じ形)。無ければ名を挙げて落ちる。"""
    main: object = getattr(module_named(name), "main", None)
    if not callable(main):
        raise TypeError(f"入口 {name} に呼べる main が無い")
    return main


def request_of(line: bytes) -> WarmRequestWire | WarmRefused:
    """頼みの 1 行を約束の型に読むため。形が違えば、合わない欄の場所だけを名乗って断る(pydantic の文には値が入りうるので使わない)。"""
    parsed: object = run(warm_request_of(line))
    match parsed:
        case WarmRequestWire():
            return parsed
        case Malformed(fields=fields):
            places = ", ".join(field.field or "(全体)" for field in fields)
            return WarmRefused(detail=f"頼みの形が約束と違う: {places}")
        case _:
            raise TypeError(f"warm-request-of が約束の外の値を返した: {type(parsed).__name__}")


def thread_count() -> int:
    """この process の OS の thread の本数(/proc/self/task の数)— fork の前に 1 本である事を確かめるため。"""
    return len(os.listdir("/proc/self/task"))


def vm_live_counts() -> tuple[int, ...]:
    """doeff の VM の生きている数(segment・continuation・IR の流れ)— 分かれる前に VM が作られていない事を印に残すため。"""
    import doeff_vm  # noqa: PLC0415 — 読み込みの後に数えるだけ(待ちの子の読み込みの一覧に入れない)

    counts: tuple[int, ...] = tuple(doeff_vm.vm_live_counts())
    return counts


def start_ticks(pid: int) -> int:
    """pid の起動の刻(/proc/<pid>/stat の starttime)— worker が pid の使い回しを見分けるため。comm に空白と括弧が入りうるので、
    最後の「)」の後を読む。"""
    with open(f"/proc/{pid}/stat", "rb") as handle:
        stat = handle.read()
    return int(stat.rsplit(b")", 1)[1].split()[STARTTIME_INDEX])


def write_replacing(path: str, text: str) -> None:
    """別名に書いてから置き換える(書きかけを読ませない)。"""
    temporary = f"{path}.tmp-{os.getpid()}"
    with open(temporary, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(temporary, path)


def run_entry(entry: str, args: tuple[str, ...]) -> NoReturn:
    """B の本体: A(shim の見張り)が据えた signal の設定を既定へ戻してから TERM の block を解き、stdin を閉じ、読み込み済みの入口の
    main を同じ process の中で走らせて、その終了コードで終わる(入口の main は argv を読む — 命令の頭は入口の名)。"""
    signal.set_wakeup_fd(-1)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    signal.signal(signal.SIGCHLD, signal.SIG_DFL)
    signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGTERM})
    nothing = os.open(os.devnull, os.O_RDONLY)
    os.dup2(nothing, 0)
    os.close(nothing)
    sys.argv = [entry, *args]
    try:
        entry_main(entry)()
        code = 0
    except SystemExit as stop:
        code = stop.code if isinstance(stop.code, int) else (0 if stop.code is None else 1)
    except BaseException:  # noqa: BLE001 — process の入口の最後: 何で落ちても理由を log に残して 1 で終わる
        traceback.print_exc()
        code = 1
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


class EntrySpawner:
    """読み込み済みの入口を fork で起こす部品(shim_code の spawn)。呼ぶと B の pid を返す。"""

    def __init__(self, entry: str, args: tuple[str, ...], closing: tuple[int, ...]) -> None:
        """走らせる入口と引数と、B が継がずに閉じる fd(A の log の書き手の fd — line_stamp の writer_descriptors)を持つため。"""
        self.entry = entry
        self.args = args
        self.closing = closing

    def __call__(self) -> int:
        """TERM を block してから B を fork して pid を返すため(B は戻らない)。A は fork の後に元の mask へ戻し、block の間に届いた
        TERM は、その時に A の止めの期限として受ける。B は書き手の fd を閉じてから入口を走らせる(B が pipe の読み口を持つと、A が
        先に落ちた時に B の書きが EPIPE にならず詰まる)。"""
        previous = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM})
        pid = os.fork()
        if pid == 0:
            for descriptor in self.closing:
                os.close(descriptor)
            run_entry(self.entry, self.args)
        signal.pthread_sigmask(signal.SIG_SETMASK, previous)
        return pid


def run_forked(request: WarmRequestWire, closing: tuple[int, ...], led: int) -> NoReturn:
    """A の本体: 待ちの子の socket と起こしの fd を閉じ、別の session の先頭になったら待ちの子へ知らせ(led へ 1 byte)、log(1 行ごとに
    刻を付ける書き手を通す)・env・cwd を整えてから、shim と同じ見張りで B を起こして待つ。job の終了コードを exit の file に書いてから、
    shim と同じ値で終わる(exit の file を書く時には、書き手が log を書き終えている)。"""
    for descriptor in closing:
        with contextlib.suppress(OSError):
            os.close(descriptor)
    os.setsid()
    os.write(led, SESSION_LED)
    os.close(led)
    log = os.open(request.log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.dup2(log, 1)
    os.dup2(log, 2)
    os.close(log)
    lines = stamped_lines()
    # 環境の置き換えは foundation の 1 か所(生の環境変数に触ってよい層)— B は fork で A の環境を継ぎ、入口が自分の文脈を環境から読む。
    run(replace_environ(tuple((item.name, item.value) for item in request.env)))
    os.chdir(request.cwd)
    spawner = EntrySpawner(request.entry, tuple(request.args), lines.writer_descriptors)
    # 分かれた task は入れ替えの対象でない(service だけが入れ替わる)— 退きの知らせを中継しない(#3672)。
    code = shim_code(request.grace_seconds, spawner, lines, become_subreaper, relay=None)
    write_replacing(request.exit_path, str(code))
    sys.stderr.flush()
    os._exit(exit_status(code))


def forked(request: WarmRequestWire, closing: tuple[int, ...]) -> WarmForked | WarmRefused:
    """A を fork し、A が session の先頭になった知らせを期限の内に受けてから、pid と起動の刻を答えにするため。知らせが来なければ断る。"""
    led_read, led_write = os.pipe()
    sys.stdout.flush()
    sys.stderr.flush()
    pid = os.fork()
    if pid == 0:
        os.close(led_read)
        run_forked(request, closing, led_write)
    os.close(led_write)
    ready, _, _ = select.select([led_read], [], [], REQUEST_TIMEOUT_SECONDS)
    led = bool(ready) and os.read(led_read, 1) == SESSION_LED
    os.close(led_read)
    if not led:
        return WarmRefused(detail="分かれた子が、session の先頭になる前に終わったか、期限の内に知らせなかった")
    return WarmForked(pid=pid, start_ticks=start_ticks(pid))


def received_line(connection: socket.socket) -> bytes | None:
    """頼みの 1 行を期限と上限の内で読むため(読めなければ None)。"""
    connection.settimeout(REQUEST_TIMEOUT_SECONDS)
    data = b""
    try:
        while b"\n" not in data and len(data) <= REQUEST_LIMIT_BYTES:
            chunk = connection.recv(65536)
            if not chunk:
                break
            data += chunk
    except OSError:
        return None
    line = data.split(b"\n", 1)[0]
    return line if line and len(line) <= REQUEST_LIMIT_BYTES else None


def answered(connection: socket.socket, answer: WarmForked | WarmRefused) -> None:
    """答えの 1 行(約束の形)を送って接続を閉じるため(頼み手が先に切っていても、待ちの子は止まらない)。"""
    line: object = run(warm_answer_line(answer))
    if not isinstance(line, bytes):
        raise TypeError(f"warm-answer-line が bytes でない値を返した: {type(line).__name__}")
    with contextlib.suppress(OSError):
        connection.sendall(line)
    connection.close()


def handled(connection: socket.socket, closing: tuple[int, ...]) -> None:
    """頼み 1 つを受けるため: 読めない・形の違う頼みは断り、受けた頼みは A を fork して pid と起動の刻を答える。"""
    line = received_line(connection)
    request = WarmRefused(detail="頼みの 1 行を読めない(期限か上限を越えた・空)") if line is None else request_of(line)
    match request:
        case WarmRefused():
            answered(connection, request)
        case WarmRequestWire():
            answered(connection, forked(request, (*closing, connection.fileno())))


def reaped() -> None:
    """終わった A を待たずに全部回収するため(zombie を溜めない — A の終わりは exit の file で worker が読む)。"""
    while True:
        try:
            pid, _status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if pid == 0:
            return


def drained(wake: int) -> None:
    """起こしの fd に溜まった印を読み捨てるため。"""
    with contextlib.suppress(BlockingIOError):
        while os.read(wake, 512):
            pass


def served(listener: socket.socket, worker: int, wake: tuple[int, int]) -> None:
    """頼みと A の終わり(SIGCHLD の起こし)と worker の消失(stdin の EOF)を 1 つの select で待つため。worker が消えたら戻る。"""
    closing = (listener.fileno(), *wake)
    while True:
        readable, _, _ = select.select([listener, worker, wake[0]], [], [])
        if wake[0] in readable:
            drained(wake[0])
            reaped()
        if worker in readable and not os.read(worker, 4096):
            return
        if listener in readable:
            connection, _ = listener.accept()
            handled(connection, closing)


def refused_start(reason: str) -> NoReturn:
    """起動を名を挙げて断るため(準備完了の印は書かない — worker は起動の失敗として名乗る)。"""
    print(f"warm_child: 起動を断る: {reason}", file=sys.stderr, flush=True)
    sys.exit(START_REFUSED_CODE)


def main() -> None:
    """module を読み込み、thread が 1 本である事を確かめ、socket を開いて準備完了の印を書き、worker が消えるまで頼みを待つ。"""
    parser = argparse.ArgumentParser(description="待ちの子(実行環境の root ごとに 1 つ・頼みが来たら fork して入口を走らせる)")
    parser.add_argument("--root", required=True, help="この待ちの子の実行環境の root の dir(準備完了の印に名乗る)")
    parser.add_argument("--socket", required=True, help="頼みを受ける unix socket の path")
    parser.add_argument("--ready", required=True, help="準備完了の印(JSON)を書く path")
    parser.add_argument("--preload", action="append", default=[], help="前もって読む module の名(速さの手がかり・何度でも)")
    options = parser.parse_args()
    os.umask(0o077)
    modules = tuple(dict.fromkeys((JOB_ENTRY, *options.preload)))
    for name in modules:
        try:
            module_named(name)
        except Exception as error:  # noqa: BLE001 — 読めない理由は何でも名を挙げて起動を断る
            refused_start(f"前もって読む module {name} を読めない: {type(error).__name__}: {error}")
    threads = thread_count()
    if threads != 1:
        refused_start(f"読み込みの後に thread が {threads} 本ある — fork できない")
    os.makedirs(os.path.dirname(options.socket), mode=0o700, exist_ok=True)
    with contextlib.suppress(FileNotFoundError):
        os.unlink(options.socket)  # 前の待ちの子が残した socket(同じ root の待ちの子は 1 つだけ)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(options.socket)
    listener.listen(16)
    wake = os.pipe()
    os.set_blocking(wake[0], False)
    os.set_blocking(wake[1], False)
    signal.set_wakeup_fd(wake[1], warn_on_full_buffer=False)
    signal.signal(signal.SIGCHLD, lambda _signum, _frame: None)
    root = os.path.realpath(options.root)
    facts = {
        "pid": os.getpid(),
        "root": root,
        "socket": options.socket,
        "modules": len(sys.modules),
        "preloaded": list(modules),
        "threads": thread_count(),
        "vmLive": list(vm_live_counts()),
    }
    write_replacing(options.ready, json.dumps(facts, ensure_ascii=False))
    print(f"warm_child: 準備完了 root={root} module={len(sys.modules)}", file=sys.stderr, flush=True)
    served(listener, sys.stdin.fileno(), wake)
    print("warm_child: worker が消えたので終わります", file=sys.stderr, flush=True)
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
