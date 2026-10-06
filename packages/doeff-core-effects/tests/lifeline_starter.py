"""StartProcess の「起こした process が終わったら、その子も終わる」の検で、起こす側になる小さな script(agora-redesign #3866)。

本物の答え手(subprocess-handler)で子を 1 つ StartProcess し(process-group True — 新しい session)、子の pid を out-path へ 1 行で書く。
  * mode = hold: 書いた後は終わらずに待つ — 検が外から SIGKILL する(起こした側が片づけを走らせずに死ぬ形)
  * mode = exit: 書いた後に StopProcess を呼ばず普通に終わる
  * mode = outlive: 子を「起こした側より長く生きる」(ChildLifetime.OUTLIVES-STARTER)で起こし、書いた後に普通に終わる
子の命令は child-out が "-" なら `sleep 600`、そうでなければこの script 自身を mode hold・out-path = child-out で起こす(子が更に別の
session の孫を起こす形)。
使い方: python lifeline_starter.py <mode> <out-path> <child-out>
"""

from __future__ import annotations

import signal
import sys

import hy  # noqa: F401  - doeff_core_effects の Hy の module を読むため
from doeff_core_effects.os_process import subprocess_handler
from doeff_core_effects.process_effects import ChildLifetime, ProcessStarted, StartProcess
from doeff_core_effects.scheduler import scheduled

from doeff import run, with_handlers


def child_argv(child_out: str) -> tuple[str, ...]:
    """起こす子の命令 — child-out が "-" なら眠るだけの子、そうでなければ孫を起こす子(この script の hold)。"""
    if child_out == "-":
        return ("sleep", "600")
    return (sys.executable, __file__, "hold", child_out, "-")


def start_of(mode: str, child_out: str) -> StartProcess:
    """起こす頼み — 既定(lifetime を書かない)= 起こした側と一緒に終わる。outlive だけが OUTLIVES-STARTER を書く。"""
    match mode:
        case "outlive":
            return StartProcess(
                argv=child_argv(child_out),
                process_group=True,
                lifetime=ChildLifetime.OUTLIVES_STARTER,
            )
        case "hold" | "exit":
            return StartProcess(argv=child_argv(child_out), process_group=True)
        case _:
            raise SystemExit(f"知らない mode: {mode}")


def main(mode: str, out_path: str, child_out: str) -> None:
    started = run(scheduled(with_handlers([subprocess_handler], start_of(mode, child_out))))
    if not isinstance(started, ProcessStarted):
        raise SystemExit(f"子を起こせなかった: {started}")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"{started.pid}\n")
    if mode == "hold":
        signal.pause()


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3])
