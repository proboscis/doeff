"""例 1 本を `hy <例>` と同じに走らせる前に、macro の展開を答えを変えない形に変える(test_examples_run.py の子)。

使い方: python run_example_mutated_expansion.py <例の path> <macro の名>…

変異「何もしない handler の下で走らせる」: 展開が作る Program を、effect を全部外へ渡す handler の下で走らせる Program(WithHandler)に
替える。run の後の答えは変わらず、展開が作る値の class 名・型・属性だけが変わる — 包んだ値は、元が Traverse の effect でも Program
(Call)でも WithHandler になるので、macro の今の展開の形に依らず必ず変わる。例がその形を印字していれば、macro を 1 字も変えて
いないのに出力が変わる — macro の持ち主が答えを変えずに展開を変えると例が赤になる事を、先に名指せる。macro ごとの包み方(_MUTATIONS):
- traverse・for/do(展開が Traverse の effect か Program を作る式): その値を包む —
  `(WithHandlerType (do (fn [effect k] (yield (Pass effect k)))) <元の展開>)`(評価の時期は元のまま)。
- fnk(展開が kleisli の関数を作る式): 作った関数を、呼んだ値を包む関数に替える。
- defk(展開が kleisli の関数を定義する文): 定義の後で、その名を、呼んだ値を包む関数に付け替える。
包むのは一番外の値だけ: 件の関数の形や、内側の handler が受ける effect の中身は変えない。

変異は、例の module(`hy <例>` が走らせる module)を compile する時の展開だけに当てる。例が import する lib の `.hy` が同じ macro
を使っていても、その展開は変えない — 変えた展開を compile した lib の bytecode が、機体で共有する保存先(DOEFF_HY_CODE_STORE)へ
書かれ、変異を知らない走りが包まれた展開を読むのを、作りで防ぐ。包む関数は例の path を閉じ込めない(例は sys.argv[0] で見分ける) —
doeff-hy の bytecode の guard は macro の関数の閉包から digest を作るので、例ごとに違う path を持つと、その macro を使う lib の
記録が例ごとに増える。

macro の表は doeff-hy の macros の module の `_hy_macros`(Hy の名 → 展開の関数)で、例の `(require doeff-hy.macros …)` がそこから
(`*`・`:macros`・別名の書き方でも)引くので、例を走らせる前にそこの名指した macro を差し替える。名指した macro が doeff-hy に
無い・この入口が包み方を知らなければ、変異が黙って空振りしないよう 0 以外で終わる。
"""

import functools
import sys
from collections.abc import Callable
from pathlib import Path

import doeff_hy.macros as doeff_hy_macros
import hy

# 展開を包む式の頭: doeff の公開の名を、名を束ねずに引く(例が import している名に依らない)。
_WITH_HANDLER_TEXT = '(. (__import__ "doeff" :fromlist #("WithHandlerType")) WithHandlerType)'
# 包む handler: 受けた effect を全部外の handler へ渡す。
_PASS_EVERYTHING_TEXT = (
    '((. (__import__ "doeff" :fromlist #("do")) do)'
    ' (fn [effect k] (yield ((. (__import__ "doeff" :fromlist #("Pass")) Pass) effect k))))'
)
_WITH_HANDLER = hy.read(_WITH_HANDLER_TEXT)
_PASS_EVERYTHING = hy.read(_PASS_EVERYTHING_TEXT)
# kleisli の関数を、呼んだ値を何もしない handler の下で走らせる Program にする関数に替える関数(関数の名・説明の文などは写す)。
_PROGRAMS_UNDER_HANDLER = hy.read(
    f"""(fn [kleisli]
  (setv pass-everything {_PASS_EVERYTHING_TEXT})
  ((. (__import__ "functools") update_wrapper)
   (fn [#* args #** kwargs] ({_WITH_HANDLER_TEXT} pass-everything (kleisli #* args #** kwargs)))
   kleisli))"""
)

# macro の形ごとの包み方: (macro に渡された form、元の展開) → 変異した展開。
Mutation = Callable[[tuple[hy.models.Object, ...], hy.models.Object], hy.models.Object]


def _value_under_handler(
    forms: tuple[hy.models.Object, ...], expansion: hy.models.Object
) -> hy.models.Object:
    """展開が作る値(Traverse の effect か Program)を、何もしない handler の下で走らせる Program にした展開(元の展開は、前と同じ時に
    評価される)。"""
    return hy.models.Expression([_WITH_HANDLER, _PASS_EVERYTHING, expansion])


def _function_returning_programs(
    forms: tuple[hy.models.Object, ...], expansion: hy.models.Object
) -> hy.models.Object:
    """展開が作る kleisli の関数を、呼んだ値を何もしない handler の下で走らせる Program にする関数に替えた展開。"""
    return hy.models.Expression([_PROGRAMS_UNDER_HANDLER, expansion])


def _definition_returning_programs(
    forms: tuple[hy.models.Object, ...], expansion: hy.models.Object
) -> hy.models.Object:
    """展開が定義する kleisli の関数の名(macro の第 1 引数)を、定義の後で、呼んだ値を包む関数に付け替えた展開。"""
    name = forms[0]
    rebind = hy.models.Expression(
        [
            hy.models.Symbol("setv"),
            name,
            hy.models.Expression([_PROGRAMS_UNDER_HANDLER, name]),
        ]
    )
    return hy.models.Expression([hy.models.Symbol("do"), expansion, rebind])


# この入口が包み方を知る macro(Hy の名 → 包み方)。test_examples_run.py の MUTATED_MACROS は、この表の名から選ぶ。
_MUTATIONS: dict[str, Mutation] = {
    "traverse": _value_under_handler,
    "for/do": _value_under_handler,
    "fnk": _function_returning_programs,
    "defk": _definition_returning_programs,
}


def _takes_compiler(macro: Callable[..., hy.models.Object]) -> bool:
    """Hy は、第 1 引数が `_hy_compiler` の macro にだけ今の compiler を渡す(macro の口)。"""
    return macro.__code__.co_varnames[:1] == ("_hy_compiler",)


def _mutated(
    macro: Callable[..., hy.models.Object], mutation: Mutation
) -> Callable[..., hy.models.Object]:
    """macro と同じ引数と答えの macro で、例の file を compile する時の展開だけを mutation で替えた物。

    Hy は、第 1 引数が `_hy_compiler` の関数に compiler を渡す — 包む関数は常にそう書き、元の macro が compiler を受ける時だけ渡す。
    compile している module が例かは、module の file と sys.argv[0](hy が走らせる例の path に詰め直す)で見分ける(`hy <例>` が
    compile する module の名は file の stem)。compiler の無い展開(hy.macroexpand が compiler を渡さない時)は、元のまま。
    """

    @functools.wraps(macro)
    def expand(
        _hy_compiler: hy.compiler.HyASTCompiler | None, *forms: hy.models.Object
    ) -> hy.models.Object:
        expansion = macro(_hy_compiler, *forms) if _takes_compiler(macro) else macro(*forms)
        if _hy_compiler is None:
            return expansion
        compiled = _hy_compiler.module.__dict__.get("__file__")
        if compiled is not None and Path(compiled).resolve() == Path(sys.argv[0]).resolve():
            return mutation(forms, expansion)
        return expansion

    return expand


def main(example: str, macros: list[str]) -> None:
    table: dict[str, Callable[..., hy.models.Object]] = vars(doeff_hy_macros)["_hy_macros"]
    missing = [name for name in macros if hy.mangle(name) not in table]
    if missing:
        raise SystemExit(
            f"doeff-hy の macro に {' '.join(missing)} が無い — 変異で包む macro の表(test_examples_run.py の"
            f" MUTATED_MACROS)を今の macro に合わせる"
        )
    unknown = [name for name in macros if name not in _MUTATIONS]
    if unknown:
        raise SystemExit(
            f"この入口は {' '.join(unknown)} の包み方を知らない — _MUTATIONS に足すか、MUTATED_MACROS から外す"
        )
    for name in macros:
        key = hy.mangle(name)
        table[key] = _mutated(table[key], _MUTATIONS[name])
    sys.argv = ["hy", example]
    from hy.cmdline import hy_main

    hy_main()


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
