"""盲検 a の実験の共通部(写しの source は変えない)。

for/do の式を Hy の module で評価する(tests/test_for_do_runtime.py の _eval と同じ形)。
葉の件の program の同時の数を数える probe を持つ。
"""

import sys
import types

import doeff_hy  # noqa: F401 — registers extensions
import hy
import hy.macros
from doeff_core_effects.scheduler import Spawn, Wait

from doeff import do

_HEADER = """
(import doeff [do :as _doeff-do])
(import doeff_traverse [Traverse :as _doeff_traverse_Traverse])
(import doeff_traverse [Skip :as _doeff_traverse_Skip])
"""

STATE = {"active": 0, "max": 0, "calls": 0}
ROUNDS = 4  # scheduler へ手を返す回数(1 回では先に Spawn した子が先に走り、他の件へ番が回らない事がある)


def reset() -> None:
    STATE.update(active=0, max=0, calls=0)


@do
def _noop():
    return None


@do
def probe(*args):
    """葉の件の program — 外の API を 1 回呼ぶ所の代わり。走っている間の同時の数を数え、scheduler へ 1 度手を返す。"""
    STATE["active"] += 1
    STATE["calls"] += 1
    STATE["max"] = max(STATE["max"], STATE["active"])
    for _ in range(ROUNDS):
        task = yield Spawn(_noop())
        yield Wait(task)
    STATE["active"] -= 1
    return sum(args)


def evaluate(code: str) -> object:
    mod = types.ModuleType("blind_a_for_do")
    sys.modules["blind_a_for_do"] = mod
    mod.__dict__["probe"] = probe
    hy.macros.require("doeff_hy.macros", mod, assignments=[["for/do", "for/do"], ["<-", "<-"]])
    result = None
    for form in hy.read_many(_HEADER + code):
        result = hy.eval(form, mod.__dict__, module=mod)
    return result
