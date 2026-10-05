"""型の式を、実行時に ``isinstance`` の第 2 引数へ渡せる form へ写す — 契約の ``(: x T)`` と束縛 ``(<- x T e)`` が
実行時に確かめる型の 1 点。

macros.hy の契約の検査と ``<-`` の束縛の展開がこの関数を呼ぶ。Hy の file を Python へ写して型を検べる外の道具(品質検査の
Hy の投影)も、束縛の実行時の保証を写す時にこの関数を呼び、規則の 2 つ目の写しを持たない(#3366 の根 2 — 写しが規則を
持たず ``(get tuple #(int int))`` を書いたまま ``isinstance`` に置き、実行時は通る束縛を型の赤にしていた)。

- ``None``: 型の注記では「None という値の型」を意味する(PEP 484)が、``isinstance`` の第 2 引数に ``None`` は渡せない
  (``TypeError: isinstance() arg 2 must be a type ...``)。``(: % None)`` が実行時に型エラーになっていた(2026-09-23
  ``sim_clock.hy`` の ``clock-driver`` で実測)ので ``None.__class__``(NoneType)へ写す。組 ``#(int None)`` の中も同じ。
  写し先は名前を引かない形にする — 展開は利用者の関数の中に置かれるので、素の名 ``type`` を呼ぶと局所の名 ``type``
  (例: ``(val type (.get value "type"))``)に隠されて TypeError になる(#1825)。定数 None の属性は局所の名に隠されない。
  以前の ``hy.I.types.NoneType`` は確かめのたびに hy.__getattr__ → slashes2dots を通り、profile の約 4% を使っていた(#1845)。
- 要素の型つきの総称型 ``(get tuple #(X ...))`` / ``(of tuple X ...)`` / ``(of dict K V)`` / ``(get dict #(K V))``:
  ``isinstance`` が受けない(``TypeError: isinstance() argument 2 cannot be a parameterized generic``)ので外側の型
  (tuple・dict)へ写す。実行時に確かめるのは外側の型だけで、要素の型は静的な型検査(doeff-hy-check の注記)が見る
  (#1790 の決め: 要素まで実行時に見ると確かめのたびに全要素を回し、入れ子の型の再帰も要る)。
- ``(| A B)``: 中の総称型を写す(``(| (get tuple #(str ...)) None)`` を ``(| tuple None)`` にする — 総称型を含む和も
  ``isinstance`` は断る)。和の中の ``None`` は写さない(Python 3.10 以降の ``isinstance`` が ``int | None`` をそのまま受ける)。
- それ以外の form はそのまま返す(同じ object)。
"""

from hy.models import Expression, Object, Sequence, Symbol, Tuple

#: 総称型の頭(``(get 基 …)`` / ``(of 基 …)``)— 外側の型 ``基`` だけを実行時に確かめる。
_GENERIC_HEADS = frozenset({"get", "of"})


def runtime_type_form(tp: Object) -> Object:
    """型の式 ``tp`` を ``isinstance`` の第 2 引数に置ける form にする(上の規則・変わらない form は同じ object)。"""
    match tp:
        case Symbol() if str(tp) == "None":
            return Expression([Symbol("."), Symbol("None"), Symbol("__class__")])
        case Tuple():
            return Tuple([runtime_type_form(item) for item in _children(tp)])
        case Expression() if (base := _generic_base(tp)) is not None:
            return runtime_type_form(base)
        case Expression() if _head(tp) == "|" and len(tp) >= 2:
            head, *members = _children(tp)
            return Expression([head, *(item if _is_none(item) else runtime_type_form(item) for item in members)])
        case _:
            return tp


def _children(node: Sequence) -> tuple[Object, ...]:
    """form の子 — Hy の form の子は Hy の model(列の型の上では object で返るので、ここで model に絞る)。"""
    return tuple(_model(item) for item in node)


def _model(item: object) -> Object:
    """form の子を Hy の model として受ける(model でない子は reader が作らない形なので、黙って写さずに止める)。"""
    match item:
        case Object():
            return item
        case _:
            raise TypeError(f"Hy の form の子が Hy の model でない: {item!r}")


def _head(node: Expression) -> str | None:
    """丸括弧の form の頭の記号の綴り(頭が記号でない・空の form は None)。"""
    match tuple(node):
        case (Symbol() as head, *_):
            return str(head)
        case _:
            return None


def _generic_base(node: Expression) -> Symbol | None:
    """``(get 基 …)`` / ``(of 基 …)`` の ``基``(記号の時だけ)。総称型の形でなければ None。"""
    match tuple(node):
        case (Symbol() as head, Symbol() as base, *_) if str(head) in _GENERIC_HEADS:
            return base
        case _:
            return None


def _is_none(node: Object) -> bool:
    """記号 ``None`` か。"""
    return isinstance(node, Symbol) and str(node) == "None"
