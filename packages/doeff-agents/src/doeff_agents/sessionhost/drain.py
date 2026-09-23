"""停止(SIGTERM)の排水の待ち — host と ACP 側の腕が同じ 1 点を使う(ADR-DOE-AGENTS-012 R39 / R58)。

``drain_until`` は「走っている手番の数が 0 になるか、期限(monotonic)に届くか、呼び手が諦める
(``abandon``)まで poll ごとに読み直す」純関数で、判断はここだけ。呼び手は 3 つ:

* ``acp.runtime.AgentdRun.drain_for_stop`` — 役 both の停止(器と ACP の腕が一緒に死ぬ)
* ``acp.runtime.AgentdRun.drain_then_exit`` — 役 agentd の停止(host の socket が消えれば諦める)
* ``host.hy`` の停止の腕(``stop-for-signal``)— 役 host(手番の途中の行が 0 になるまで待つ)

この module は stdlib だけを import する: host.hy(器)が ACP の腕(``acp.runtime`` — Hy の program
を引く)を import せずに同じ待ちを使えるための置き場。ACP の runtime はここから re-export する。
"""

# pyright: strict
from __future__ import annotations

import time
from collections.abc import Callable
from typing import NamedTuple

#: 排水の待ちの poll の間隔(秒・宣言はここ 1 点)。
DRAIN_POLL_SECONDS = 1.0


class DrainOutcome(NamedTuple):
    remaining: int
    elapsed_seconds: float


def drain_until(
    running: Callable[[], int],
    deadline: float,
    now: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    poll: float = DRAIN_POLL_SECONDS,
    abandon: Callable[[], bool] | None = None,
) -> DrainOutcome:
    """排水の待ち(段 12 lane 12j・agora-redesign #304 便 2): 走っている手番の数が 0 になるか、期限(monotonic)に届くまで
    poll ごとに読み直す。戻り = (残った手番の数, 待った秒)。判断はこの 1 点(停止の腕はこれを呼ぶだけ)。

    ``abandon``(card acp:kanban-issue:ki-18d6c4851b21)= 呼び手が「もう待つ理由が無い」と答える読み口
    (役 agentd: host の socket が消えた)。真を返した拍に残りの数を持って戻る — 期限と同じ形で戻るので、
    呼び手は戻った後に ``abandon()`` を読み直して区別する(戻りの型に第 2 の欄を足さない)。"""
    started = now()
    while True:
        left = running()
        current = now()
        if left == 0 or current >= deadline:
            return DrainOutcome(left, current - started)
        if abandon is not None and abandon():
            return DrainOutcome(left, current - started)
        sleep(min(poll, max(0.0, deadline - current)))
