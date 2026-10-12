"""例 1 本を `hy <例>` と同じに走らせる前に、macro の展開を答えを変えない形に変える(test_examples_run.py の子)。

使い方: python run_example_mutated_expansion.py <例の path> <macro の名>…

変異「何もしない handler の下で走らせる」: 名指した macro(traverse・for/do のように、実行時の値 — Traverse の effect か Program —
を作る式)の展開を、その値を作ってから(評価の時期は元のまま)、effect を全部外へ渡す handler の下で走らせる Program で包む:
`(WithHandlerType (do (fn [effect k] (yield (Pass effect k)))) <元の展開>)`。run の後の答えは変わらず、展開が作る値の class 名・型・属性
だけが変わる — 包んだ値は、元が Traverse の effect でも Program(Call)でも WithHandler になるので、macro の今の展開の形に依らず
必ず変わる。例がその形を印字していれば、macro を 1 字も変えていないのに出力が変わる — macro の持ち主が答えを変えずに展開を
変えると例が赤になる事を、先に名指せる。

変異は、例の module(`hy <例>` が走らせる `__main__`)を compile する時の展開だけに当てる。例が import する lib の `.hy` が同じ macro
を使っていても、その展開は変えない — 変えた展開を compile した lib の bytecode が、機体で共有する保存先(DOEFF_HY_CODE_STORE)へ書かれ、
変異を知らない走りが包まれた展開を読むのを、作りで防ぐ。

macro の表は doeff-hy の macros の module の `_hy_macros`(Hy の名 → 展開の関数)で、例の `(require doeff-hy.macros [...])` がそこから
引くので、例を走らせる前にそこを差し替える。名指した macro が無ければ、変異が黙って空振りしないよう 0 以外で終わる。
"""

import functools
import importlib
import sys
from collections.abc import Callable
from pathlib import Path

import hy

# 展開を包む式の頭: doeff の公開の名を、名を束ねずに引く(例が import している名に依らない)。
_WITH_HANDLER = hy.read('(. (__import__ "doeff" :fromlist #("WithHandlerType")) WithHandlerType)')
# 包む handler: 受けた effect を全部外の handler へ渡す。
_PASS_EVERYTHING = hy.read(
    '((. (__import__ "doeff" :fromlist #("do")) do)'
    ' (fn [effect k] (yield ((. (__import__ "doeff" :fromlist #("Pass")) Pass) effect k))))'
)


def _program_of(expansion: hy.models.Object) -> hy.models.Expression:
    """展開が作る値を、何もしない handler の下で走らせて同じ答えを返す Program にした展開(元の展開は、前と同じ時に評価される)。"""
    return hy.models.Expression([_WITH_HANDLER, _PASS_EVERYTHING, expansion])


def _mutated(
    macro: Callable[..., hy.models.Object], example: Path
) -> Callable[..., hy.models.Object]:
    """macro と同じ引数で、example の file を compile する時の展開だけを Program で包んだ物。

    第 1 引数が `_hy_compiler` の macro には、Hy が今の compiler を渡す(macro の口)。macro 自身は compiler を受けないので渡さない。
    compile している module が example かは、module の file で見分ける(`hy <例>` が compile する module の名は file の stem)。
    """

    @functools.wraps(macro)
    def expand(
        _hy_compiler: hy.compiler.HyASTCompiler, *forms: hy.models.Object
    ) -> hy.models.Object:
        expansion = macro(*forms)
        compiled = _hy_compiler.module.__dict__.get("__file__")
        if compiled is not None and Path(compiled).resolve() == example:
            return _program_of(expansion)
        return expansion

    return expand


def main(example: str, macros: list[str]) -> None:
    table: dict[str, Callable[..., hy.models.Object]] = importlib.import_module(
        "doeff_hy.macros"
    )._hy_macros
    for name in macros:
        key = hy.mangle(name)
        if key not in table:
            raise SystemExit(
                f"doeff-hy の macro に {name} が無い — 変異で包む macro の表(test_examples_run.py の"
                f" MUTATED_MACROS)を今の macro に合わせる"
            )
        table[key] = _mutated(table[key], Path(example).resolve())
    sys.argv = ["hy", example]
    from hy.cmdline import hy_main

    hy_main()


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
