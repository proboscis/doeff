"""defk の本体の ``match`` で、class pattern の keyword の欄の名を Python の属性の名(``hy.mangle``)へ直す。

Hy 1.3.1 の ``match`` は class pattern の keyword の欄の名を mangle せずに Python の ``case`` へ出す
(hy/core/result_macros.py の ``compile_pattern`` — ``kwd_attrs=[kwd.name for kwd in keywords]``)。
``(match r (Rec :ended-reason None) …)`` は ``case Rec(ended-reason=None):`` になり、属性 ``ended-reason`` は
どの値にも無いので、その節は値が何でも当たらず、黙って次の節へ倒れる(agora-redesign #2036)。Hy の欄の名
``ended-reason`` は defclass / defrecord では属性 ``ended_reason`` になるので、pattern の側も同じ ``hy.mangle`` で
揃えれば、利用者は Hy の自然な綴り ``(Rec :ended-reason None)`` のまま書ける。

Hy の本体は直さない(フォーク・monkeypatch・hy の版の指定の変更はしない — ADR-DOE-HY-008・operator 2026-10-01
"i said no. i asked you to make workaround and report me")。直すのは defk の本体(引数・契約を含む)だけで、
defk の外の ``match`` は doeff-linter の DOEFF169 が止める(規則は defk の form の中を当てない)。

直す所は class pattern の keyword の欄の名だけ。値の pattern としての keyword(``(Rec :kind :a-b)`` の ``:a-b``)・
節の本体と ``:if`` の守りの式・quote / quasiquote の中・mapping pattern の鍵には触れない。変わらない部分木は
同じ object のまま返す(位置の情報もそのまま)。
"""

from collections.abc import Iterator
from typing import TypeVar

from hy import mangle
from hy.models import Dict, Expression, Keyword, List, Object, Sequence, Set, Symbol, Tuple

# 組み直す form の型(Expression・List・Tuple・Set・Dict — 組み直しても同じ型が返る)。
_Form = TypeVar("_Form", bound=Sequence)

# 中を code として歩かない form の頭(quote した form は値で、Hy の match としては compile されない)。
_QUOTING_HEADS = frozenset({"quote", "quasiquote"})
# pattern の中で、丸括弧の form だが class pattern ではない物の頭(値の参照・or pattern・`#*` / `#**`)。
_NOT_CLASS_HEADS = frozenset({".", "|", "unpack-iterable", "unpack-mapping"})


def mangle_match_fields(form: Object) -> Object:
    """form の中のすべての ``match`` の class pattern の keyword の欄の名を ``hy.mangle`` した form を返す。"""
    return _code(form)


def _head(node: Expression) -> str | None:
    """丸括弧の form の頭の記号の綴り(頭が記号でなければ None)。"""
    match tuple(node):
        case (Symbol() as head, *_):
            return str(head)
        case _:
            return None


def _code(node: Object) -> Object:
    """code の位置の form を歩く(``match`` の form を見つけたら節の pattern を直す)。"""
    match node:
        case Expression() if _head(node) in _QUOTING_HEADS:
            return node
        case Expression() if _head(node) == "match":
            return _match_form(node)
        case Expression() | List() | Tuple() | Set() | Dict():
            return _rebuilt(node, [_code(item) for item in node])
        case _:
            return node


def _match_form(node: Expression) -> Expression:
    """``(match 主語 節 …)`` — 主語は code として歩き、節は ``_match_clauses`` で直す。"""
    match tuple(node):
        case (head, subject, *clauses):
            return _rebuilt(node, [head, _code(subject), *_match_clauses(clauses)])
        case _:
            return node


def _match_clauses(forms: list[Object]) -> Iterator[Object]:
    """節の列 ``pattern [:as 名] [:if 守り] 本体 …`` を順に直して返す(pattern は ``_pattern``・守りと本体は ``_code``)。"""
    at = 0
    while at < len(forms):
        yield _pattern(forms[at])
        at += 1
        if _is_keyword(forms, at, ":as"):
            yield from forms[at : at + 2]
            at += 2
        if _is_keyword(forms, at, ":if"):
            yield forms[at]
            yield from map(_code, forms[at + 1 : at + 2])
            at += 2
        yield from map(_code, forms[at : at + 1])
        at += 1


def _is_keyword(items: list[Object], at: int, spelling: str) -> bool:
    """items の at 番目が綴り ``spelling`` の keyword か。"""
    return at < len(items) and isinstance(items[at], Keyword) and str(items[at]) == spelling


def _is_class_pattern(node: Expression) -> bool:
    """``(Rec …)`` / ``(mod.Rec …)`` の形の class pattern か(値の参照・or pattern・unpack ではない)。"""
    match tuple(node):
        case (Symbol() as head, *_):
            return str(head) not in _NOT_CLASS_HEADS
        case (Expression() as head, *_):
            return _head(head) == "."
        case _:
            return False


def _pattern(node: Object) -> Object:
    """pattern 1 つを直す(入れ子の sequence・mapping・or pattern・class pattern の引数も辿る)。"""
    match node:
        case Expression() if _is_class_pattern(node):
            return _class_pattern(node)
        case Expression() if _head(node) == "|":
            return _rebuilt(node, [node[0], *(_pattern(item) for item in node[1:])])
        case List() | Tuple() | Dict():
            return _rebuilt(node, [_pattern(item) for item in node])
        case _:
            return node


def _class_pattern(node: Expression) -> Expression:
    """``(Rec 位置の pattern … :欄 pattern …)`` — keyword の欄の名を mangle し、引数の pattern を辿る。"""
    return _rebuilt(node, [node[0], *_class_arguments(list(node[1:]))])


def _class_arguments(forms: list[Object]) -> Iterator[Object]:
    """class pattern の引数を順に直して返す。

    keyword の直後は必ずその欄の pattern として読む(``(Rec :kind :a-b)`` の ``:a-b`` は値の pattern で直さない)。
    ``:as 名`` も同じ読みで通る(``as`` は mangle しても ``as``・名は記号の pattern でそのまま)。
    """
    at = 0
    while at < len(forms):
        item = forms[at]
        if isinstance(item, Keyword) and at + 1 < len(forms):
            yield _field_name(item)
            yield _pattern(forms[at + 1])
            at += 2
        else:
            yield _pattern(item)
            at += 1


def _field_name(keyword: Keyword) -> Keyword:
    """欄の名の keyword を Python の属性の名へ(変わらなければ同じ object)。"""
    name = mangle(keyword.name)
    if name == keyword.name:
        return keyword
    renamed = Keyword(name)
    renamed.replace(keyword)  # 位置(行・桁)を元の keyword から写す(replace は自分に書き込む)
    return renamed


def _rebuilt(node: _Form, items: list[Object]) -> _Form:
    """子が 1 つでも変わった時だけ同じ型の form を組み直す(位置は元の form から写す)。"""
    if len(items) == len(node) and all(new is old for new, old in zip(items, node, strict=True)):
        return node
    rebuilt = type(node)(items)
    rebuilt.replace(node, recursive=False)  # 子は自分の位置を持つので、写すのはこの form の位置だけ
    return rebuilt
