"""handle・defhandler の展開が読む doeff の名の綴りの唯一の定義元(card ki-16180480129e)。

展開は、書き手の module・class・関数の中に import と名の読みを置く。節の操作と handler のために読む `Resume`・`Transfer`・
`Pass`・`WithHandler`(session があれば `Get`・`Put`・`Some`)を素の名で置くと、書き手が自分の effect を同じ名にした時
(振込の `Transfer`・KV の `Put`)、1 つの名を書き手の物と展開の物が分け合う。節の頭の `isinstance` と注記が展開の名を指して
effect が外の handler へ黙って抜け、module の段では展開の import が書き手の名を上書きした。

展開が読む名は、すべて `_doeff_` の頭の別名で import し、読む綴りもここから引く(型検査のための展開が引く補助の名
`_doeff_<名>` と同じ決め — static_view.py)。書き手の module に `_doeff_` の頭の名を置く書き手は居ない。

- `alias`・`alias_symbol`: 別名の綴り。展開が名を読む所は、名を直に書かずここから引く。
- `import_form`: 別名で束ねる Hy の import(実行時の展開が書いた場所に置く)。
- `import_source`: 別名で束ねる Python の import(型検査のための展開が module の頭に 1 度だけ置く — static_view.py)。

読み手: handle.hy(`WithHandler`・`Pass`・session の `Get`・`Put`・`Some`)・clause_ops.hy(節の操作の node と、型検査のための展開の
`typed_resume`・`typed_transfer`)・binding_forms.py(`:=` の `Put`・`Some`)・macros.hy(`set!` の `Put`・`Some`)。
"""

from collections.abc import Iterable

import hy
from hy.models import Expression, Keyword, List, Object, Symbol

#: 別名の頭。型検査のための展開が引く補助の名(static_view.deftest_annotation_name)と同じ。
PREFIX = "_doeff_"

#: 名の置き場の module。ここに無い名は doeff から import する(節の操作の node は表 clause_ops.hy の nodes が名を持つ)。
_DOEFF = "doeff"
_HOMES: dict[str, str] = {
    "WithHandler": "doeff_vm",
    "Get": "doeff_core_effects.effects",
    "Put": "doeff_core_effects.effects",
}


def alias(name: str) -> str:
    """展開が name を読む時の綴り(`_doeff_` の頭)。"""
    return f"{PREFIX}{name}"


def alias_symbol(name: str) -> Symbol:
    """展開が name を読む Symbol。呼ぶたびに新しい Symbol を作る — 展開のあいだで 1 つの Symbol を使い回すと、位置を付ける
    macro(`locate-synthesized`)が最初の展開で付けた位置が残り、後の式の行がその行へずれる(agora-redesign #1004)。"""
    return Symbol(alias(name))


def home(name: str) -> str:
    """name を import する module。"""
    return _HOMES.get(name, _DOEFF)


def _by_home(names: Iterable[str]) -> dict[str, tuple[str, ...]]:
    """名を置き場ごとにまとめる(置き場も名も、最初に出た順で重ねない)。"""
    unique = tuple(dict.fromkeys(names))
    return {
        module: tuple(name for name in unique if home(name) == module)
        for module in dict.fromkeys(home(name) for name in unique)
    }


def import_form(names: Iterable[str]) -> Expression:
    """names を別名で束ねる Hy の import(置き場ごとの `(import 置き場 [名 :as 別名 …])` を並べた `(do …)`)。名が無ければ
    `(do)`。点を含む module の名は、Hy が読む式の形(`(. a b)`)で渡す — 文字から Symbol を作ると名が崩れる。"""
    statements: list[Object] = [
        Expression(
            [
                Symbol("import"),
                hy.read(module),
                List(
                    [
                        part
                        for name in grouped
                        for part in (Symbol(name), Keyword("as"), alias_symbol(name))
                    ]
                ),
            ]
        )
        for module, grouped in _by_home(names).items()
    ]
    return Expression([Symbol("do"), *statements])


def import_source(names: Iterable[str]) -> str:
    """names を別名で束ねる Python の import の文(置き場ごとの `from 置き場 import 名 as 別名, …` を 1 行ずつ)。"""
    return "".join(
        f"from {module} import {', '.join(f'{name} as {alias(name)}' for name in grouped)}\n"
        for module, grouped in _by_home(names).items()
    )
