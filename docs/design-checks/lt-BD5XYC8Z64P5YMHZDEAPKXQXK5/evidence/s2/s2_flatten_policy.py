"""S2 の失敗例の対照: 平らにした件の決まり(落ちた外の件を答えから外す)を Collection.flatten だけで変える。

M1(展開)と M3(handler)は変えない。期待: 答えの有効な値は変わらず、件の並び・履歴を撃つ検
test_skips_and_failures_of_both_froms_keep_their_places が、外の skip の件が消えた所で赤になる(決まりの変更を検が名指す)。
"""

import sys

sys.path.insert(0, "tests")

import test_for_do_runtime as t  # noqa: E402
from doeff_traverse.collection import Collection  # noqa: E402

CASE = (
    '(for/do (<- a (From [0 1 2] :label "outer")) (When (!= a 1))'
    ' (<- b (From (range a 3) :label "inner")) (<- y (half b)) #(a y))'
)
original = Collection.flatten


def dropping_fallen_outer(self):
    """S2 の変更の写し: 落ちた外の件を外してから継ぐ。"""
    return original(Collection([item for item in self.all_items if not item.failed]))


for name, flatten in (("今の flatten", original), ("S2 の flatten(落ちた外の件を外す)", dropping_fallen_outer)):
    Collection.flatten = flatten
    try:
        collection = t._collect(CASE)
        print(f"[{name}] valid_values={collection.valid_values}")
        print(f"[{name}] all_items={[(i.index, i.failed, [(h.stage, h.event) for h in i.history]) for i in collection.all_items]}")
        try:
            t.test_skips_and_failures_of_both_froms_keep_their_places()
            print(f"[{name}] test_skips_and_failures_of_both_froms_keep_their_places: passed")
        except AssertionError as error:
            print(f"[{name}] test_skips_and_failures_of_both_froms_keep_their_places: AssertionError (pytest の外の素の assert の比べが偽 — 文は空)")
    finally:
        Collection.flatten = original
