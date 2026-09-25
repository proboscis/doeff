"""job と worker の間に挟む見張り。worker が死んだら job の process group を止める。

worker は shim を新しい process group の先頭として起動し、stdin をパイプでつなぐ。shim は job を同じ group の中で
起動し、stdin の EOF(= worker が消えた。kill -9 を含む)を待つ。EOF を見たら group へ TERM、猶予の後に KILL を送る。
worker が普通に止める時は group へ signal を送るので、shim は job の終了を待って同じ終了コードで終わる。
macOS には親の死を子へ知らせる仕組み(Linux の PR_SET_PDEATHSIG)が無いので、パイプの EOF で代える。

使い方: python -m doeff_cluster.shim <猶予秒> -- <job の命令…>
"""

import os
import signal
import subprocess
import sys
import threading


def main() -> None:
    grace = float(sys.argv[1])
    command = sys.argv[sys.argv.index("--") + 1 :]
    child = subprocess.Popen(command, stdin=subprocess.DEVNULL)  # 同じ process group に入る
    # worker からの TERM は group 全体に届くので、shim は job の終了を待つだけにする。
    signal.signal(signal.SIGTERM, lambda _signum, _frame: None)

    # group へ送ってよいのは shim が group の先頭の時だけ(worker は start_new_session で起動する)。
    # 先頭でなければ group は起動した側と共有なので、送ると起動した側まで止めてしまう — 子だけを止める。
    leads_group = os.getpgid(0) == os.getpid()

    def send(sig: signal.Signals) -> None:
        if leads_group:
            os.killpg(os.getpid(), sig)
        else:
            child.send_signal(sig)

    def watch_parent() -> None:
        sys.stdin.buffer.read()  # EOF まで待つ = worker が消えた
        if child.poll() is None:
            print("shim: worker が消えたので job を止めます", file=sys.stderr, flush=True)
            send(signal.SIGTERM)
            try:
                child.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                send(signal.SIGKILL)

    threading.Thread(target=watch_parent, daemon=True).start()
    code = child.wait()
    # 終了処理を経ずに抜ける: stdin を読んでいる補助の thread が残ったまま終了処理に入ると、Python が
    # abort して終了コードが -6 に化ける(job 自身の終了コードを worker へ正しく返せない — 実測 2026-09-23)。
    sys.stderr.flush()
    os._exit(code if code >= 0 else 128 - code)


if __name__ == "__main__":
    main()
