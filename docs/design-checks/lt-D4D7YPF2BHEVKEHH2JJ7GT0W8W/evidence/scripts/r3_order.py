"""盲検 A の R3 の反例の再現: 関数の中の handle で、節の終わり方の検めが本文より先に走るかを、本文の形ごとに見るため。

本文が先に評価されると `body-evaluated` の AssertionError か、本文の `!` が出す Bump の UnhandledEffect になる。
検めが先なら ClauseEndingError(TypeError の子)になる。
走らせ方: cd <doeff の作業木> && uv run python -I <この file>
"""

import sys
import types

import doeff_hy  # noqa: F401 — registers extensions
import hy
from doeff_core_effects import try_handler
from doeff_core_effects.scheduler import scheduled
from doeff_traverse.handlers import fail_handler, sequential

from doeff import run

HEADER = """
(require doeff-hy.macros [defk do! <- handle])
(import dataclasses [dataclass])
(import doeff [EffectBase])
(import doeff_core_effects.effects [Resumption])
(defclass [(dataclass :frozen True)] Bump [EffectBase] #^ int n)
(defclass [(dataclass :frozen True)] Halt [EffectBase] (setv __doeff_resumption__ Resumption.NEVER))
(defk bump-n [n] {:pre [(: n int)] :post [(: % int)]} (<- y (Bump n)) y)
(defn body-evaluated [] (raise (AssertionError "本文が検めより先に評価された")))
"""

DEFN_BODIES = {
    "expression": "(body-evaluated)",
    "statement-before-the-program": "(do (body-evaluated) (bump-n 1))",
    "let-binding": "(let [x (body-evaluated)] (bump-n x))",
}


def fresh(code: str) -> types.ModuleType:
    """形ごとに module を分けるため。"""
    mod = types.ModuleType("r3_order")
    sys.modules["r3_order"] = mod
    for form in hy.read_many(HEADER + code):
        hy.eval(form, mod.__dict__, module=mod)
    return mod


def outcome(thunk: object) -> str:
    """結末を 1 語で出すため。"""
    try:
        thunk()
    except TypeError as error:
        return f"検めが先(TypeError: {str(error)[:60]})"
    except Exception as error:  # noqa: BLE001 — 再現の表示だけ
        return f"本文が先({type(error).__name__}: {str(error)[:60]})"
    return "落ちない"


for label, body in DEFN_BODIES.items():
    mod = fresh(f"(defn subject [] (abs 1) (handle {body} (Halt [] (resume 0))))")
    print(f"defn/{label}: {outcome(mod.subject)}")

mod = fresh(
    """
(defk subject [] {:pre [] :post [(: % int)]}
  (<- r (handle (let [x (! (bump-n 5))] (bump-n x)) (Halt [] (resume 0))))
  r)
"""
)
print(
    "defk/let-bang(本文の ! が外の Bump を出す・Bump の handler は無い): "
    + outcome(lambda: run(scheduled(try_handler(fail_handler(sequential()(mod.subject()))))))
)
