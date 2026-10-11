"""提案の差分(sequential の on_progress)を、for/do の式ごとに走らせ、進捗の最後の (done, total) と答えの件の数を並べる。

使い方(写しの .venv・overlay を先に):
  PYTHONPATH=/tmp/blind-b/overlay uv run --no-sync python /tmp/blind-b/exp/progress_experiment.py
"""

import sys
import types

import doeff_hy  # noqa: F401 — registers extensions
import hy
import hy.macros
import doeff_traverse.handlers as handlers_module
from doeff_traverse.collection import Collection
from doeff_traverse.handlers import sequential

from doeff import do, run

_HEADER = """
(import doeff [do :as _doeff-do])
(import doeff_traverse [Traverse :as _doeff_traverse_Traverse])
(import doeff_traverse [Skip :as _doeff_traverse_Skip])
"""


@do
def _half(x: int):
    if x == 2:
        raise ValueError("two")
    return x // 2


def _eval(code: str) -> object:
    mod = types.ModuleType("blind_b_probe")
    sys.modules["blind_b_probe"] = mod
    mod.__dict__["half"] = _half
    hy.macros.require("doeff_hy.macros", mod, assignments=[["for/do", "for/do"], ["<-", "<-"]])
    result = None
    for form in hy.read_many(_HEADER + code):
        result = hy.eval(form, mod.__dict__, module=mod)
    return result


def measure(name: str, code: str, handler=None) -> None:
    events: list[tuple[object, int, int]] = []
    h = handler if handler is not None else sequential(on_progress=lambda label, done, total: events.append((label, done, total)))
    collection = run(h(_eval(code)))
    last = events[-1] if events else None
    labels = sorted({str(e[0]) for e in events})
    n = len(collection.all_items)
    verdict = "一致" if last is not None and last[1] == last[2] == n else "不一致"
    print(f"[{name}] 答えの件 = {n} / 最後の進捗 (label, done, total) = {last} / 進捗の label = {labels} / {verdict}")
    print(f"    valid_values = {[v if not isinstance(v, Collection) else f'Collection{v.valid_values}' for v in collection.valid_values]}")


print("handlers:", handlers_module.__file__)
print("--- 書き手が確かめそうな形(From を重ねた for/do) ---")
measure("From 1 つ", "(for/do (<- x (From [0 1 2 3 4])) (When (!= x 1)) (<- y (half x)) y)")
measure("From 2 つ", "(for/do (<- a (From [1 2])) (<- b (From [10 20])) (+ a b))")
measure("From 3 つ", "(for/do (<- a (From [1 2])) (<- b (From [10 20])) (<- c (From [100 200])) (+ a b c))")
measure(
    "From 2 つ・外の When と内の失敗",
    '(for/do (<- a (From [0 1 2] :label "outer")) (When (!= a 1))'
    ' (<- b (From (range a 3) :label "inner")) (<- y (half b)) #(a y))',
)
measure("内の From が空", "(for/do (<- a (From [1 2])) (<- b (From (if (= a 1) [] [10 20]))) (+ a b))")

print("--- D1: 本体の中で別の for/do を <- で受ける(From は 1 つ・平らにしない) ---")
measure(
    "本体の for/do・本体の値は数",
    '(for/do (<- a (From [1 2] :label "users"))'
    ' (<- sub (for/do (<- b (From [10 20 30] :label "posts")) (+ a b)))'
    " (len (list sub)))",
)
measure(
    "本体の for/do・本体の値は Collection",
    '(for/do (<- a (From [1 2] :label "users"))'
    ' (<- sub (for/do (<- b (From [10 20 30] :label "posts")) (+ a b)))'
    " sub)",
)
