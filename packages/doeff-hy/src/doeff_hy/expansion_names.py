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

- `is_alias_import`: その綴りの import か(型検査の展開の後処理が、関数・class の本体の中の展開の import を module の段へ移すため —
  static_check.py)。

表は閉じている: 展開が読む名はすべて `_HOMES` に置き場とともに載せ、表に無い名は `home` が断る(知らない名を別名の import と
答えない — for/do の docstring は書き手に `(import doeff [do :as _doeff-do])` を書かせる)。

読み手: handle.hy(`WithHandler`・`Pass`・session の `Get`・`Put`・`Some`)・clause_ops.hy(節の操作の node と、型検査のための展開の
`typed_resume`・`typed_transfer`)・binding_forms.py(`:=` の `Put`・`Some`)・macros.hy(`set!` の `Put`・`Some`)・declarations.hy の
defeffect-form と record.hy の defrecord(:check)・defwire(型の本体が読む typing の `ClassVar`・`cast` — class の本体に書いた型の
本体は名を module の段から引くので、型検査の投影はこの import を module の段へ移す)。
"""

from collections.abc import Iterable
from dataclasses import dataclass

from hy.models import Expression, Keyword, List, Object, Symbol

#: 別名の頭。型検査のための展開が引く補助の名(static_view.deftest_annotation_name)と同じ。
PREFIX = "_doeff_"

#: 展開が読む名と、その置き場の module(閉じた表 — 節の操作の node の名は表 clause_ops.hy の nodes が持ち、ここに置き場を書く)。
_HOMES: dict[str, str] = {
    "Resume": "doeff",
    "Transfer": "doeff",
    "Pass": "doeff",
    "Some": "doeff",
    "typed_resume": "doeff",
    "typed_transfer": "doeff",
    "WithHandler": "doeff_vm",
    "Get": "doeff_core_effects.effects",
    "Put": "doeff_core_effects.effects",
    "ClassVar": "typing",
    "cast": "typing",
}


def alias(name: str) -> str:
    """展開が name を読む時の綴り(`_doeff_` の頭)。"""
    return f"{PREFIX}{name}"


def alias_symbol(name: str) -> Symbol:
    """展開が name を読む Symbol。呼ぶたびに新しい Symbol を作る — 展開のあいだで 1 つの Symbol を使い回すと、位置を付ける
    macro(`locate-synthesized`)が最初の展開で付けた位置が残り、後の式の行がその行へずれる(agora-redesign #1004)。"""
    return Symbol(alias(name))


def effect_symbol() -> Symbol:
    """節を回す関数が受けた effect を束ねる局所名(引数)と、展開がそれを読む Symbol。呼ぶたびに新しい Symbol を作る(`alias_symbol` の註)。

    doeff の名でなく展開の局所名だが、書き手の節の欄・session の名と分け合わないよう、同じ `_doeff_` の別名の決めで綴る。素の `effect` で
    束ねると、欄を `effect` にした節(`(Note [effect extra] …)`)の欄の束ね `(setv effect (. effect effect))` が受けた effect を上書きし、
    続く欄の読みと外へ渡す effect が欄の値になった(card ki-6af9862aed32)。書き手が本体に `effect` と書いた時に読める名は、別に
    `WRITER_VISIBLE` が決める(handle.hy の `_clause-aliases`)。"""
    return alias_symbol("effect")


def continuation_symbol() -> Symbol:
    """節を回す関数が受けた続きを束ねる局所名(引数)と、展開がそれを読む Symbol。`effect_symbol` と同じ決め — 素の `k` で束ねると、
    欄を `k` にした節(KV の `(Kv [k v] …)`)の欄の束ね `(setv k (. effect k))` が続きを上書きし、`(resume …)` が
    `TypeError: argument 'k': 'int' object is not an instance of 'K'` で落ちた(card ki-6af9862aed32)。"""
    return alias_symbol("k")


@dataclass(frozen=True)
class WriterName:
    """書き手が節の本体・`:when` の番に書いた時、節の effect・続きとして読める名 1 つ(card ki-6af9862aed32)。

    - `written`: 書き手が書く名(`effect`・`k`)。
    - `internal`: それを束ねる展開の局所名(`_doeff_` の別名 — 節を回す関数の引数)。
    - `declared`: 型検査のための展開が名に宣言する型(節を回す関数の引数と同じ型)。
    - `narrowed`: 節の `isinstance` が絞る値か。型検査のための展開は、絞る値を節の判定より前に絞る前の値のまま束ね、節の判定が
      書き手の名も絞る(絞った後に代入すると、絞った総称型 `EffectBase[Unknown]` を代入先の変数が受けて
      reportUnknownVariableType の赤になる — handle.hy の `_narrowed-alias-bindings`・`_clause-test`)。
    """

    written: str
    internal: str
    declared: str
    narrowed: bool


#: 書き手が名を書いた節だけが束ねる、書き手の見える名の表。節の欄・session の名が同じ名なら書き手の名が優先する(束ねない)。
WRITER_VISIBLE: tuple[WriterName, ...] = (
    WriterName("effect", alias("effect"), "object", narrowed=True),
    WriterName("k", alias("k"), "_doeff_Continuation", narrowed=False),
)


def home(name: str) -> str:
    """name を import する module。表に無い名は KeyError(展開が新しい名を読む時は、表に置き場を足す)。"""
    if name not in _HOMES:
        raise KeyError(f"展開の名の表(expansion_names._HOMES)に無い名: {name!r} — 置き場とともに表へ足す")
    return _HOMES[name]


def _by_home(names: Iterable[str]) -> dict[str, tuple[str, ...]]:
    """名を置き場ごとにまとめる(置き場も名も、最初に出た順で重ねない)。"""
    unique = tuple(dict.fromkeys(names))
    return {
        module: tuple(name for name in unique if home(name) == module)
        for module in dict.fromkeys(home(name) for name in unique)
    }


def _module_model(module: str) -> Object:
    """module の名の model。点を含む名は、Hy が読む式の形(`(. a b)`)で組む — 文字 1 つの Symbol にすると名が崩れる。
    reader(`hy.read`)を通さない: reader が作る model は 1 行目 1 列目の位置を持ち、位置を付ける macro(`locate-synthesized`)が
    利用者の式の位置と取り違えて、囲む式の範囲を 1 行目へ引く。型検査の診断(未使用の import・未定義の名)の行が、handler の行でなく
    1:1 になった(設計検証 lt-2SJXY98Q643WGJK2ASWJ3N8PWH の盲検 A の実測)。ここが作る model は位置を持たない。"""
    parts = module.split(".")
    if len(parts) == 1:
        return Symbol(module)
    return Expression([Symbol("."), *(Symbol(part) for part in parts)])


def import_form(names: Iterable[str]) -> Expression:
    """names を別名で束ねる Hy の import(置き場ごとの `(import 置き場 [名 :as 別名 …])` を並べた `(do …)`)。名が無ければ
    `(do)`。作る model は位置を持たない(`_module_model` の註)。"""
    statements: list[Object] = [
        Expression(
            [
                Symbol("import"),
                _module_model(module),
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


def is_alias_import(module: str, name: str, asname: str | None) -> bool:
    """`from module import name as asname` が、import_form / import_source が書く別名の import の 1 つの名か(置き場と別名が
    この表の綴りに合う)— 型検査の展開の後処理が、macro の合成した import を書き手の import と分けるため(static_check.py)。"""
    return name in _HOMES and asname == alias(name) and home(name) == module


def import_source(names: Iterable[str]) -> str:
    """names を別名で束ねる Python の import の文(置き場ごとの `from 置き場 import 名 as 別名, …` を 1 行ずつ)。"""
    return "".join(
        f"from {module} import {', '.join(f'{name} as {alias(name)}' for name in grouped)}\n"
        for module, grouped in _by_home(names).items()
    )
