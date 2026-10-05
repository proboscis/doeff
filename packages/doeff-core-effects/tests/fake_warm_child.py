"""待ちの子の契約テストの、本物の側の小さな偽の待ちの子(tests/warm_contract_handlers.hy が socket ごとに 1 つ起こす)。

本物の待ちの子(この package の外)が守る約束だけを、検の中で再現するための script:
  * socket-path で頼みを受け、約束の 1 行を読む・答える(読み書きは本物の答え手と同じ関数 warm-request-of・warm-answer-line)
  * mode = accept: 子 A を fork し、A は setsid して group の先頭になり、log へ dup2 し、子 B で入口(module)を走らせ、B の終わり
    (signal なら負の値)を exit の file へ置き換えで書いて終わる。待ちの子は A の pid と /proc の start-ticks を答える
    (env の置き換えは本物の待ちの子の役目で、この偽物はしない — run_entry の註)
  * mode = refuse:<文> は断りを答え、mode = silent は答えずに接続を持ったまま待つ(頼み手の期限切れを見るため)
A の終わりは待ちの子が待たない(SIGCHLD を捨てて自動で回収する)ので、終わった A は /proc から消え、頼み手は exit の file で読む。
"""

from __future__ import annotations

import os
import runpy
import signal
import socket
import sys

import hy  # noqa: F401  - doeff_core_effects の Hy の module を読むため
from doeff import run
from doeff_core_effects.os_warm_process import WarmRequestWire, warm_answer_line, warm_request_of
from doeff_core_effects.scheduler import scheduled
from doeff_core_effects.warm_effects import WarmForked, WarmRefused
from doeff_hy.wire import Malformed


def start_ticks(pid: int) -> int:
    """pid の process の始まりの刻(/proc/<pid>/stat の 22 番目の欄)を読むため — 頼み手が pid の使い回しを見分ける印。"""
    with open(f"/proc/{pid}/stat", encoding="utf-8", errors="surrogateescape") as f:
        text = f.read()
    return int(text.rsplit(")", 1)[1].split()[19])


def run_entry(entry: str, args: tuple[str, ...], cwd: str) -> int:
    """子 B: 入口の module を、渡された cwd で同じ process の中で走らせ、終了 code を返すため。
    TERM は A が塞いだまま継いでいるので、扱いを既定に戻してから塞ぎを解く — 先に届いて待っていた TERM もここで効く。
    頼みの env で子の環境変数を置き換えるのは本物の待ちの子(この package の外)の役目で、この偽物は置き換えない — 契約テストは
    env が約束の 1 行で運ばれる事(往復の検)と、答えに出ない事だけを見る。"""
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGTERM})
    os.chdir(cwd)
    sys.path.insert(0, cwd)
    sys.argv = [entry, *args]
    try:
        runpy.run_module(entry, run_name="__main__", alter_sys=True)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1
    return 0


def write_exit(exit_path: str, code: int) -> None:
    """子 A: 入口の終了 code を exit の file へ置き換えで書くため(半端な中身を頼み手に読ませない)。"""
    partial = f"{exit_path}.partial"
    with open(partial, "w", encoding="utf-8") as f:
        f.write(f"{code}\n")
    os.replace(partial, exit_path)


def become_a(listener: socket.socket, connection: socket.socket, request: WarmRequestWire, ready: int) -> None:
    """子 A: group の先頭になり、log へ出力を向け、子 B の終わりを exit の file に書いて終わるため(戻らない)。
    TERM は塞ぐ(A は group へ送られた TERM で死なず、B の終わりを書く)。setsid と塞ぎを済ませたら ready の pipe へ 1 byte 書く —
    待ちの子はそれを読んでから頼み手へ答える(済ませる前に届いた signal が「居ない」で外れないため)。"""
    listener.close()
    connection.close()
    signal.signal(signal.SIGCHLD, signal.SIG_DFL)
    signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM})
    os.setsid()
    os.write(ready, b"r")
    os.close(ready)
    log = os.open(request.log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.dup2(log, 1)
    os.dup2(log, 2)
    b = os.fork()
    if b == 0:
        os._exit(run_entry(request.entry, tuple(request.args), request.cwd))
    _, status = os.waitpid(b, 0)
    write_exit(request.exit_path, os.waitstatus_to_exitcode(status))
    os._exit(0)


def received_line(connection: socket.socket) -> bytes:
    """頼みの 1 行(改行まで)を受けるため。"""
    received = b""
    while b"\n" not in received:
        chunk = connection.recv(4096)
        if not chunk:
            break
        received += chunk
    return received.split(b"\n", 1)[0]


def answer(listener: socket.socket, connection: socket.socket, mode: str) -> None:
    """頼み 1 つに mode の通りに答えるため(accept は子 A を立てる)。"""
    line = received_line(connection)
    if mode == "silent":
        # 答えずに、頼み手が期限で接続を閉じるのを待つ(閉じられると recv が空を返す)。
        while connection.recv(4096):
            pass
        return
    if mode.startswith("refuse:"):
        connection.sendall(run(scheduled(warm_answer_line(WarmRefused(detail=mode.removeprefix("refuse:"))))))
        return
    request = run(scheduled(warm_request_of(line)))
    if isinstance(request, Malformed):
        connection.sendall(run(scheduled(warm_answer_line(WarmRefused(detail="頼みの形が約束と違う")))))
        return
    ready_read, ready_write = os.pipe()
    a = os.fork()
    if a == 0:
        os.close(ready_read)
        become_a(listener, connection, request, ready_write)
    os.close(ready_write)
    os.read(ready_read, 1)
    os.close(ready_read)
    connection.sendall(run(scheduled(warm_answer_line(WarmForked(pid=a, start_ticks=start_ticks(a))))))


def main() -> None:
    """socket-path で頼みを待ち続けるため(親が止めるまで)。"""
    socket_path, mode = sys.argv[1], sys.argv[2]
    signal.signal(signal.SIGCHLD, signal.SIG_IGN)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(socket_path)
        listener.listen(8)
        # 組み立ての側(warm_contract_handlers.hy)は、この 1 行を読んで頼み始める(時間で待たない)。
        print("ready", flush=True)
        while True:
            connection, _ = listener.accept()
            with connection:
                answer(listener, connection, mode)


if __name__ == "__main__":
    main()
