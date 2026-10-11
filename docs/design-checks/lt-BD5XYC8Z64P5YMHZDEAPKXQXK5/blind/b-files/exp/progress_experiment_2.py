"""D2(S2 の変更 = flatten の決まりだけを変える)と D3(handler が持つ重ねの状態が for/do を越えて残る)を測る。

  PYTHONPATH=/tmp/blind-b/overlay:/tmp/blind-b/exp uv run --no-sync python /tmp/blind-b/exp/progress_experiment_2.py
"""

from doeff_traverse.collection import Collection
from doeff_traverse.handlers import sequential
from progress_experiment import _eval, measure  # 同じ dir の測りの道具(上の script の関数)

from doeff import do, run

WHEN_CASE = (
    '(for/do (<- a (From [0 1 2] :label "outer")) (When (!= a 1))'
    ' (<- b (From (range a 3) :label "inner")) (<- y (half b)) #(a y))'
)

print("--- D2: S2 の例「落ちた外の件を答えから外す」を flatten だけで入れる(M1・M3 は変えない) ---")
original_flatten = Collection.flatten


def flatten_dropping_fallen_outer(self):
    """S2 の変更の写し: 落ちた外の件(When の skip・失敗)を答えから外してから、今の flatten で継ぐ。"""
    return original_flatten(Collection([item for item in self.all_items if not item.failed]))


Collection.flatten = flatten_dropping_fallen_outer
try:
    measure("S2 の flatten の下・外の When と内の失敗", WHEN_CASE)
finally:
    Collection.flatten = original_flatten

print("--- D3: 同じ handler の値を 2 度の run で使い、1 度目が BaseException で抜けた時 ---")


class Abort(BaseException):
    """KeyboardInterrupt・asyncio.CancelledError と同じく Exception でない例外の代わり。"""


@do
def boom(x):
    raise Abort(f"stop at {x}")


events = []
shared = sequential(on_progress=lambda label, done, total: events.append((label, done, total)))
import sys  # noqa: E402

first = _eval('(for/do (<- a (From [1 2] :label "first")) (<- y (boom a)) y)')
# _eval が作った module に boom を置く(式の評価は run の時)
sys.modules["blind_b_probe"].__dict__["boom"] = boom
try:
    run(shared(first))
    print("1 度目: 抜けなかった")
except BaseException as error:  # noqa: BLE001 — 測りのため
    print(f"1 度目: {type(error).__name__}: {error}")
second = run(shared(_eval('(for/do (<- x (From [5 6] :label "second")) x)')))
print(f"2 度目の答えの件 = {len(second.all_items)} / 2 度目の間の進捗 = {[e for e in events]}")
