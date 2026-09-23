"""doeff-hy の macro を「型検査のための展開」に切り替える口(1 点)。

`doeff-hy-check`(doeff_hy/static_check.py)は Hy の source を本物の macro で展開し、
得た Python を pyright に読ませる。実行時の展開のうち、型検査に要る情報を持たない所
(`(<- x T e)` の `x = yield e` は x の型を e から導けない)だけを、この切替が入っている
間は型の分かる形で出す。切替は macro の展開の時にだけ読む(実行時には読まない)ので、
通常の import・bytecode の cache・実行の意味は変わらない。

切替で形が変わる所(macros.hy の `_static-view?` を読む所がすべて):
- `_bind-yield`: `(setv x (yield e))` → `x: T = _doeff_perform(e)`
  (`_doeff_perform` は e の答えの型を返すと宣言した静的な関数。doeff_hy/static_types.pyi。
  Python の `@effectful` の `x = perform(e)` と同じ形)
- defk / defp / do! / deftest / fnk: `do` を型付きの `doeff_hy.static_types.do` から取る
  (呼んだ結果が core の `Expand[T, E]` = 答えの型 T を持つ Program になる)
- defhandler の `resume` / `transfer`: core の `typed_resume` / `typed_transfer`
  (答えの値を effect の `EffectBase[T]` の T と突き合わせる)

どちらの展開にも共通で型のための形を持つ所(実行時に評価されない・意味を変えない):
引数と deff の戻り値の文字列の注記、結果の変数 `_contract_result: T`、補助の import
(型は macros.pyi)。
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_STATIC_VIEW: ContextVar[bool] = ContextVar("doeff_hy_static_view", default=False)


def static_view_enabled() -> bool:
    return _STATIC_VIEW.get()


@contextmanager
def static_view() -> Iterator[None]:
    token = _STATIC_VIEW.set(True)
    try:
        yield
    finally:
        _STATIC_VIEW.reset(token)
