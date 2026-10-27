"""defwire の自前の解き手の型の木 — 読む側・書く側・JSON Schema(card ki-923c16d33551・ki-f6551d717654・親 card ki-2e0dce8d1ebb)。

JSON を defwire の型の値へ、pydantic を読まずに strict に解いて確かめ、同じ木で値を JSON へ書き、JSON Schema を出す。契約は設計の記録
``docs/design/defwire-decoder/design.md``(節 2 の型ごとの判じ方と書き出し・節 3 の共通の規則・節 3b の書き出しと JSON Schema・付録 B の
腕の名)で、今の pydantic の strict と同じ物を受け、同じ物を断る(文字列を数にしない・真偽を数にしない・整数は float の欄に入る・配列は
tuple の欄・defenum は値の綴り・:names の wire の名だけを受ける・:unknown の reject / ignore・:check の落ちは Malformed)。違うのは 1 つだけ:
union の何でも受ける腕(OpaqueJson・object)は、他のどの腕も受けない時だけ受ける(節 3 の規則 4)。書き出しは今の pydantic の dump・
dump-json と同じ JSON を出す(違いは節 3b の表の 3 つ — どれも pydantic が欄の Python の名や OpaqueJson の中の欄を書いていた所)。

使い方:

    codec = codec_of(LandingRow)          # 最初の使いで型の木を 1 度組み、型に覚える(2 回目からは組まない)
    codec.decode_json(text)               # JSON の文字列(bytes も)から — 答え = LandingRow の値か Malformed
    codec.decode(raw)                     # JSON の値(凍らせた FrozenMap・tuple・OpaqueJson も)から
    codec.encode(row)                     # JSON の値へ(欄は wire の名・既定値と同じ欄は書かない — 今の dump)
    codec.encode_json(row)                # JSON の文字列へ(OpaqueJson の欄は持っている文字列をそのまま埋める — 今の dump-json)
    codec.schema()                        # JSON Schema(今の json-schema)

- 型の木は欄の型ごとの判じ手(``_Node``)の木。defwire の型は 1 つの ``_WireNode`` を型に覚え、入れ子の欄は子の判じ手を参照する
  (親ごとに組み直さない)。語彙の外の型・解けない前方参照・型の輪は、組む時に TypeError で断る(黙って通さない)。
- まだ wire.hy(parse・parse-json・dump・dump-json・json-schema)は替えない。替えるのは照らしの test(card 3)が pydantic との不一致 0 を
  示した後。
- この module は pydantic を import しない(標準の module と doeff_hy.json_value・doeff_hy.wire_malformed だけを読む —
  ``tests/test_wire_codec.py`` と ``tests/test_wire_codec_write.py`` が import の閉包を確かめる)。
"""

import dataclasses
import enum
import inspect
import json
import json.encoder
import math
import re
import sys
import threading
import types
import typing
from collections import Counter
from collections.abc import Callable, Collection, Mapping
from typing import Any, Final, ForwardRef, Generic, TypeAlias, TypeGuard, TypeVar, cast, final

# 名を _ で始めて読む: この module の公開の名は Codec と codec_of だけ(Malformed は wire_malformed・OpaqueJson は json_value が出す)。
from doeff_hy.json_value import OpaqueJson as _OpaqueJson
from doeff_hy.wire_malformed import Malformed as _Malformed
from doeff_hy.wire_malformed import MalformedField as _MalformedField

_T = TypeVar("_T")

#: 誤り 1 つ: (場所の列・種類・msg)。場所は wire の名・配列の位置・dict の key・union の腕の名の列(値の全体は空の列)。
_Problem = tuple[tuple[str | int, ...], str, str]

#: 書き出した JSON の値と JSON Schema の値(判じ手の encode・schema の答え)。_Raw は文字列へ書く途中の OpaqueJson の文字列・
#: _RefMark は組み終える前の $ref の行き先で、どちらも Codec の答えには残らない。
_Json: TypeAlias = (
    "dict[str, _Json] | list[_Json] | str | int | float | bool | None | _Raw | _RefMark"
)
#: JSON Schema の形 1 つ(object)。
_Schema: TypeAlias = "dict[str, _Json]"


class _RefusedError(Exception):
    """判じ手が値を断った(誤りの列を運ぶ — 木の外へは出さず、Codec が Malformed へ写す)。"""

    __slots__ = ("problems",)

    def __init__(self, problems: list[_Problem]) -> None:
        super().__init__()
        self.problems = problems


def _refuse(kind: str, msg: str) -> _RefusedError:
    """判じ手が値を断る時の 1 つの誤り(場所は判じ手の所 — 呼び手が鍵を足す)。"""
    return _RefusedError([((), kind, msg)])


def _under(key: str | int, problems: list[_Problem]) -> list[_Problem]:
    """子の誤りに、親が見た鍵(欄の wire の名・位置・key・腕の名)を場所の頭として足す。"""
    return [((key, *loc), kind, msg) for loc, kind, msg in problems]


def _expected(values: tuple[str, ...]) -> str:
    # pydantic の綴り: 値を repr で包み、最後の 2 つを " or "・前を ", " でつなぐ('a', 'b' or 'c')。
    """defenum と Literal の断りの msg の、受ける値の並べ方(pydantic の綴りに揃える)。"""
    quoted = [repr(value) for value in values]
    if len(quoted) == 1:
        return quoted[0]
    return f"{', '.join(quoted[:-1])} or {quoted[-1]}"


# --- 判じ手 -------------------------------------------------------------------------------------------------------------------
# どの判じ手も、JSON を json.loads で読んだ素の値(dict・list・str・int・float・bool・None)を受ける。凍らせた値と OpaqueJson は
# Codec.decode が先に素の値へ戻す(_plain)。decode は値を返すか _RefusedError を投げる。


class _Node:
    """欄の型 1 つの判じ手。label = union の誤りの場所に挟む腕の名(設計の記録の付録 B — pydantic の綴り)。"""

    __slots__ = ("label",)
    #: union の腕として受ける JSON の種類(str・int・float・bool・array・object)。
    kinds: frozenset[str] = frozenset()
    #: 何でも受ける腕(OpaqueJson・object)— union では他のどの腕も受けない時だけ選ぶ(節 3 の規則 4)。
    catch_all: bool = False
    #: union で同じ種類の腕が並んだ時の順(小さい方が先 — pydantic の「ちょうど合う腕」が「直して合う腕」より先)。
    rank: int = 0

    def __init__(self, label: str) -> None:
        self.label = label

    def decode(self, value: object) -> object:
        """JSON の素の値を、この型の値へ解いて確かめる(受けない値は _RefusedError — 誤りの場所はこの判じ手の所から)。"""
        raise NotImplementedError

    def encode(self, value: object, raw: bool) -> _Json:
        """この型の値を JSON の素の値へ書く(raw = 文字列へ書く途中 — OpaqueJson の文字列を _Raw のまま残す)。型の合わない値は、
        今の pydantic と同じく値の型から書く(_inferred)。"""
        return _inferred(value, raw)

    def fits(self, value: object, strict: bool) -> bool:
        """union の腕として、値がこの型の値か(書く時に腕を選ぶ — strict は型ちょうど・lax は書き直せる型も)。"""
        raise NotImplementedError

    def schema(self, defs: "_Definitions") -> _Schema:
        """この型の JSON Schema(defwire の型と defenum は defs に置いて $ref で指す)。"""
        raise NotImplementedError

    def titled(self) -> bool:
        """この型の欄の JSON Schema に、欄の名からの title を付けるか(型の側が title を持つ defwire・defenum は付けない — pydantic と同じ)。"""
        return True


@final
class _Str(_Node):
    __slots__ = ()
    kinds = frozenset({"str"})

    def decode(self, value: object) -> object:
        if type(value) is str:
            return value
        raise _refuse("string_type", "Input should be a valid string")

    def fits(self, value: object, strict: bool) -> bool:
        return isinstance(value, str)

    def schema(self, defs: "_Definitions") -> _Schema:
        return {"type": "string"}


@final
class _Int(_Node):
    __slots__ = ()
    kinds = frozenset({"int"})

    def decode(self, value: object) -> object:
        if type(value) is int:
            return value
        raise _refuse("int_type", "Input should be a valid integer")

    def encode(self, value: object, raw: bool) -> _Json:
        if type(value) is int:
            return value
        if isinstance(value, int):  # 真偽と IntEnum も整数で書く(今の pydantic と同じ)
            return int(value)
        return _inferred(value, raw)

    def fits(self, value: object, strict: bool) -> bool:
        return isinstance(value, int) and not (strict and isinstance(value, bool))

    def schema(self, defs: "_Definitions") -> _Schema:
        return {"type": "integer"}


@final
class _Float(_Node):
    __slots__ = ()
    kinds = frozenset({"int", "float"})
    rank = 1  # 整数は int の腕が先に受ける((| float int) でも 1 は int のまま)

    def decode(self, value: object) -> object:
        kind = type(value)
        if kind is float:
            return value
        if kind is int:
            try:
                return float(cast(int, value))
            except OverflowError:  # 桁あふれの整数は今の pydantic と同じく inf
                return math.inf if cast(int, value) > 0 else -math.inf
        raise _refuse("float_type", "Input should be a valid number")

    def encode(self, value: object, raw: bool) -> _Json:
        # 型のある float の欄は NaN・inf も float のまま(dump)— 文字列へ書く時に null になる。整数と真偽は float に直す(今と同じ)。
        if type(value) is float:
            return value
        if isinstance(value, float | int):
            try:
                return float(value)
            except (
                OverflowError
            ):  # float にできない桁の整数は、今の pydantic の dump と同じく整数のまま書く
                return _inferred(value, raw)
        return _inferred(value, raw)

    def fits(self, value: object, strict: bool) -> bool:
        return isinstance(value, float) or (not strict and isinstance(value, int))

    def schema(self, defs: "_Definitions") -> _Schema:
        return {"type": "number"}


@final
class _Bool(_Node):
    __slots__ = ()
    kinds = frozenset({"bool"})

    def decode(self, value: object) -> object:
        if type(value) is bool:
            return value
        raise _refuse("bool_type", "Input should be a valid boolean")

    def fits(self, value: object, strict: bool) -> bool:
        return isinstance(value, bool)

    def schema(self, defs: "_Definitions") -> _Schema:
        return {"type": "boolean"}


@final
class _NoneOnly(_Node):
    __slots__ = ()

    def decode(self, value: object) -> object:
        if value is None:
            return None
        raise _refuse("none_required", "Input should be None")

    def fits(self, value: object, strict: bool) -> bool:
        return value is None

    def schema(self, defs: "_Definitions") -> _Schema:
        return {"type": "null"}


@final
class _Enum(_Node):
    """defenum(StrEnum)— member の値の綴りの文字列だけを受ける(member の名では受けない)。"""

    __slots__ = ("_members", "_msg", "_spellings", "enum_type")
    kinds = frozenset({"str"})
    rank = 2  # str と Literal の腕が先(pydantic は文字列に str の腕を選ぶ)

    def __init__(self, enum_type: type[enum.Enum]) -> None:
        super().__init__(f"str-enum[{enum_type.__name__}]")
        self.enum_type = enum_type
        self._members: dict[str, enum.Enum] = {
            cast(str, member.value): member for member in enum_type
        }
        self._spellings: dict[enum.Enum, str] = {
            member: spelling for spelling, member in self._members.items()
        }
        self._msg = f"Input should be {_expected(tuple(self._members))}"

    def decode(self, value: object) -> object:
        if type(value) is str:
            member = self._members.get(value)
            if member is not None:
                return member
        raise _refuse("enum", self._msg)

    def encode(self, value: object, raw: bool) -> _Json:
        if isinstance(value, self.enum_type):
            return self._spellings[value]
        return _inferred(value, raw)

    def fits(self, value: object, strict: bool) -> bool:
        return isinstance(value, self.enum_type)

    def schema(self, defs: "_Definitions") -> _Schema:
        return defs.ref(self.enum_type, self.definition)

    def titled(self) -> bool:
        return False

    def definition(self, defs: "_Definitions") -> _Schema:
        """$defs に置く形(値の綴りの並び・型の名・型の doc を description に — 今の pydantic と同じく inspect.cleandoc で整え、
        Enum の既定の doc は出さない。照らしの test が見つけた差)。"""
        doc = self.enum_type.__doc__
        description = inspect.cleandoc(doc) if doc else None
        shape: _Schema = {
            "enum": [*self._members],
            "title": self.enum_type.__name__,
            "type": "string",
        }
        if description is None or description == "An enumeration.":
            return shape
        return {"description": description, **shape}


@final
class _Literal(_Node):
    """文字列の Literal — 並べた文字列のどれか。"""

    __slots__ = ("_msg", "_order", "_values")
    kinds = frozenset({"str"})

    def __init__(self, values: tuple[str, ...]) -> None:
        super().__init__(f"literal[{','.join(repr(value) for value in values)}]")
        self._order = values
        self._values = frozenset(values)
        self._msg = f"Input should be {_expected(values)}"

    def decode(self, value: object) -> object:
        if type(value) is str and value in self._values:
            return value
        raise _refuse("literal_error", self._msg)

    def fits(self, value: object, strict: bool) -> bool:
        return isinstance(value, str) and value in self._values

    def schema(self, defs: "_Definitions") -> _Schema:
        if len(self._order) == 1:
            return {"const": self._order[0], "type": "string"}
        return {"enum": [*self._order], "type": "string"}


#: 判じ手 1 回の結末: (True, 値) か (False, 誤りの列)。誤りを全部集める所(欄・要素・腕)が、例外を内包表記の中で受けるために使う。
_Outcome = tuple[typing.Literal[True], object] | tuple[typing.Literal[False], list[_Problem]]


def _attempt(node: _Node, value: object) -> _Outcome:
    """判じ手 1 回を結末の値にする(誤りを全部集める所が、例外を内包表記の中で受けるため)。"""
    try:
        return (True, node.decode(value))
    except _RefusedError as invalid:
        return (False, invalid.problems)


#: 在るべき所(既定値の無い欄・長さちょうどの配列の位置)に値が無かった結末。
_MISSING: Final[_Outcome] = (False, [((), "missing", "Field required")])


def _problems_of(key: str | int, outcome: _Outcome) -> list[_Problem]:
    """結末 1 つの誤りに場所の鍵を足す(受けた結末は誤り無し)— 誤りを集める所が、鍵の順のまま 1 列にするため。"""
    if outcome[0] is True:
        return []
    return _under(key, outcome[1])


def _settled(keyed: list[tuple[str | int, _Outcome]]) -> list[object]:
    """鍵(位置・key)ごとの結末から値の列を返す。1 つでも断っていれば、誤りに鍵を場所として足して全部まとめて断る。"""
    problems = [problem for key, outcome in keyed for problem in _problems_of(key, outcome)]
    if problems:
        raise _RefusedError(problems)
    return [outcome[1] for _, outcome in keyed]


def _items(node: _Node, values: list[object]) -> list[object]:
    """配列の要素を同じ判じ手で読む(誤りは位置を場所に足して全部集める)。"""
    return _settled([(index, _attempt(node, item)) for index, item in enumerate(values)])


@final
class _Array(_Node):
    """長さの決まらない配列: (get tuple #(T ...))・(get list T)・(get frozenset T)。"""

    __slots__ = ("_item", "_kind", "_make", "_own")
    kinds = frozenset({"array"})

    def __init__(
        self,
        label: str,
        item: _Node,
        kind: str,
        make: Callable[[list[object]], object],
        own: tuple[type, ...],
    ) -> None:
        super().__init__(label)
        self._item = item
        self._kind = kind
        self._make = make
        self._own = (
            own  # 書く時に strict で受ける入れ物(宣言の入れ物ちょうど — frozenset の欄は set も)
        )

    def decode(self, value: object) -> object:
        if type(value) is list:
            return self._make(_items(self._item, cast(list[object], value)))
        raise _refuse(self._kind, "Input should be a valid array")

    def encode(self, value: object, raw: bool) -> _Json:
        if _is_array(value):
            item = self._item
            return [item.encode(element, raw) for element in value]
        return _inferred(value, raw)

    def fits(self, value: object, strict: bool) -> bool:
        # strict は宣言の入れ物ちょうど・lax はどの配列の入れ物も。要素も同じ strict で確かめる。
        item = self._item
        return (
            _is_array(value)
            and isinstance(value, self._own if strict else _ARRAYS)
            and all(item.fits(element, strict) for element in value)
        )

    def schema(self, defs: "_Definitions") -> _Schema:
        shape: _Schema = {"items": self._item.schema(defs), "type": "array"}
        return {**shape, "uniqueItems": True} if self._make is frozenset else shape


@final
class _FixedTuple(_Node):
    """長さちょうどの配列: (get tuple #(T1 T2 …))。"""

    __slots__ = ("_items",)
    kinds = frozenset({"array"})

    def __init__(self, label: str, items: tuple[_Node, ...]) -> None:
        super().__init__(label)
        self._items = items

    def decode(self, value: object) -> object:
        if type(value) is not list:
            raise _refuse("tuple_type", "Input should be a valid array")
        values = cast(list[object], value)
        expected = len(self._items)
        if len(values) > expected:  # 長い時は要素を判じない(今の pydantic と同じ)
            raise _refuse(
                "too_long",
                f"Tuple should have at most {expected} items after validation, not {len(values)}",
            )
        # 短い時は、足りない位置ごとに missing(在る位置の誤りと位置の順に並べる)。
        return tuple(
            _settled(
                [
                    (index, _attempt(node, values[index]) if index < len(values) else _MISSING)
                    for index, node in enumerate(self._items)
                ]
            )
        )

    def encode(self, value: object, raw: bool) -> _Json:
        if _is_array(value) and isinstance(value, list | tuple) and len(value) == len(self._items):
            return [
                node.encode(element, raw) for node, element in zip(self._items, value, strict=True)
            ]
        return _inferred(value, raw)

    def fits(self, value: object, strict: bool) -> bool:
        return (
            _is_array(value)
            and isinstance(value, tuple if strict else list | tuple)
            and len(value) == len(self._items)
            and all(
                node.fits(element, strict) for node, element in zip(self._items, value, strict=True)
            )
        )

    def schema(self, defs: "_Definitions") -> _Schema:
        count = len(self._items)
        shape: _Schema = {"maxItems": count, "minItems": count, "type": "array"}
        prefix: list[_Json] = [node.schema(defs) for node in self._items]
        return {**shape, "prefixItems": prefix} if count else shape


@final
class _Dict(_Node):
    """(get dict #(str T))— object の値を T で読む(key は JSON の object の key なので文字列)。"""

    __slots__ = ("_item",)
    kinds = frozenset({"object"})

    def __init__(self, label: str, item: _Node) -> None:
        super().__init__(label)
        self._item = item

    def decode(self, value: object) -> object:
        if type(value) is not dict:
            raise _refuse("dict_type", "Input should be an object")
        source = cast(dict[str, object], value)
        return dict(
            zip(
                source,
                _settled([(key, _attempt(self._item, item)) for key, item in source.items()]),
                strict=True,
            )
        )

    def encode(self, value: object, raw: bool) -> _Json:
        if _is_mapping(value):
            item = self._item
            return {_json_key(key): item.encode(element, raw) for key, element in value.items()}
        return _inferred(value, raw)

    def fits(self, value: object, strict: bool) -> bool:
        item = self._item
        return (
            _is_mapping(value)
            and (not strict or isinstance(value, dict))
            and all(
                isinstance(key, str) and item.fits(element, strict)
                for key, element in value.items()
            )
        )

    def schema(self, defs: "_Definitions") -> _Schema:
        # 値の形が何でもよい時(object・OpaqueJson の値)は true(今の pydantic と同じ — 照らしの test が見つけた差)。
        item = self._item.schema(defs)
        return {"additionalProperties": item if item else True, "type": "object"}


@final
class _BareDict(_Node):
    """素の dict — object である事だけを確かめ、値は読まずに json.loads の値のまま(census の特例 — 新しく書く欄は型にする)。"""

    __slots__ = ()
    kinds = frozenset({"object"})

    def decode(self, value: object) -> object:
        if type(value) is dict:
            return value
        raise _refuse("dict_type", "Input should be an object")

    def fits(self, value: object, strict: bool) -> bool:
        return isinstance(value, dict if strict else Mapping)

    def schema(self, defs: "_Definitions") -> _Schema:
        return {"additionalProperties": True, "type": "object"}


@final
class _Anything(_Node):
    """object — どの JSON も json.loads の値のまま受ける(型の check は無い — 今と同じ)。"""

    __slots__ = ()
    catch_all = True

    def decode(self, value: object) -> object:
        return value

    def fits(self, value: object, strict: bool) -> bool:
        return True

    def schema(self, defs: "_Definitions") -> _Schema:
        return {}


@final
class _Opaque(_Node):
    """OpaqueJson — どの JSON も中を読まず、最小の直列化の文字列で包む。"""

    __slots__ = ()
    catch_all = True

    def decode(self, value: object) -> object:
        return _OpaqueJson.of(cast(Any, value))

    def encode(self, value: object, raw: bool) -> _Json:
        if isinstance(value, _OpaqueJson):
            return _opaque_out(value, raw)
        return _inferred(value, raw)

    def fits(self, value: object, strict: bool) -> bool:
        return isinstance(value, _OpaqueJson)

    def schema(self, defs: "_Definitions") -> _Schema:
        return {}


@final
class _WireField:
    """defwire の型の欄 1 つ: Python の名・wire の名・判じ手・既定値(無ければ dataclasses.MISSING — 既定値の作り手 factory も)。"""

    __slots__ = ("default", "factory", "node", "python", "required", "wire")

    def __init__(
        self,
        python: str,
        wire: str,
        node: _Node,
        default: object,
        factory: Callable[[], object] | None,
    ) -> None:
        self.python = python
        self.wire = wire
        self.node = node
        self.default = default
        self.factory = factory
        self.required = default is dataclasses.MISSING and factory is None

    def omits(self, value: object) -> bool:
        """書く時にこの欄を省くか — 値が既定値(作り手の作る値)と == で等しい欄は書かない(今の pydantic の exclude_defaults)。"""
        if self.default is not dataclasses.MISSING:
            return bool(value == self.default)
        if self.factory is not None:
            return bool(value == self.factory())
        return False


@final
class _WireNode(_Node):
    """入れ子の defwire の型 — object を、その型の :names・:unknown・:check で判じる。

    欄は宣言の順に判じて誤りを全部集め、その後に知らない欄を入力の順に並べる(:unknown :reject の時)。:check は欄が全部通った時だけ
    走る(型の構築子が __post_init__ を呼ぶ)— ValueError と AssertionError だけを Malformed へ写し、他の例外は投げたまま。
    """

    __slots__ = ("_reject", "_wires", "fields", "wire_type")
    kinds = frozenset({"object"})

    def __init__(self, wire_type: type) -> None:
        super().__init__(wire_type.__name__)
        self.wire_type = wire_type
        self.fields: tuple[_WireField, ...] = ()
        self._wires: frozenset[str] = frozenset()
        self._reject = False

    def fill(self, fields: tuple[_WireField, ...], reject: bool) -> None:
        """欄の判じ手を、組み終えてから入れる(組む途中の型の輪は _BUILDING が見張る)。"""
        self.fields = fields
        self._wires = frozenset(field.wire for field in fields)
        self._reject = reject

    def decode(self, value: object) -> object:
        return self.decode_counted(value)[0]

    def decode_counted(self, value: object) -> tuple[object, int]:
        """値と、入力から埋めた欄の数(union で object を受ける腕が 2 つ以上受けた時に、多い方を選ぶ — pydantic の smart の union)。"""
        if type(value) is not dict:
            raise _refuse("dataclass_type", "Input should be an object")
        source = cast(dict[str, object], value)
        # 欄が無い時は、既定値の在る欄なら引数に渡さず(構築子が既定値で埋める)・無い欄なら missing。
        outcomes = [
            (field, _attempt(field.node, source[field.wire]) if field.wire in source else _MISSING)
            for field in self.fields
            if field.wire in source or field.required
        ]
        unknown: list[_Problem] = (
            [
                ((key,), "unexpected_keyword_argument", "Unexpected keyword argument")
                for key in source
                if key not in self._wires
            ]
            if self._reject and not self._wires.issuperset(source)
            else []
        )
        problems = [
            problem for field, outcome in outcomes for problem in _problems_of(field.wire, outcome)
        ] + unknown
        if problems:
            raise _RefusedError(problems)
        arguments = {field.python: outcome[1] for field, outcome in outcomes}
        try:
            return self.wire_type(**arguments), len(arguments)
        except ValueError as error:
            raise _refuse("value_error", f"Value error, {error}") from None
        except AssertionError as error:
            raise _refuse("assertion_error", f"Assertion failed, {error}") from None

    def encode(self, value: object, raw: bool) -> _Json:
        # 欄は宣言の順に wire の名で書き、既定値と等しい欄は書かない(入れ子の型の欄も、その型の書き出しが同じく省く)。
        # 既定値の無い欄は None でも書く(null)。
        if not isinstance(value, self.wire_type):
            return _inferred(value, raw)
        return {
            field.wire: field.node.encode(item, raw)
            for field, item in ((field, getattr(value, field.python)) for field in self.fields)
            if not field.omits(item)
        }

    def fits(self, value: object, strict: bool) -> bool:
        return type(value) is self.wire_type if strict else isinstance(value, self.wire_type)

    def schema(self, defs: "_Definitions") -> _Schema:
        return defs.ref(self.wire_type, self.definition)

    def titled(self) -> bool:
        return False

    def definition(self, defs: "_Definitions") -> _Schema:
        """この型の object の形(欄は wire の名・required は既定値の無い欄・:unknown :reject は additionalProperties false・title は
        型の名)— 今の pydantic の json_schema と同じ形(節 3b)。"""
        properties: _Schema = {field.wire: _field_schema(field, defs) for field in self.fields}
        required: list[_Json] = [field.wire for field in self.fields if field.required]
        shape: _Schema = {
            "properties": properties,
            "title": self.wire_type.__name__,
            "type": "object",
        }
        return {
            **shape,
            **({"required": required} if required else {}),
            **({"additionalProperties": False} if self._reject else {}),
        }


@final
class _Union(_Node):
    """(| T1 T2 …)— 腕の選び方は設計の記録の節 3「union の腕の選び方」。

    1. null は None の腕が受ける。
    2. 文字列・数・真偽は、JSON の種類が合う腕だけが受ける(同じ種類の腕は rank の順 — 整数は int の腕が float の腕より先・文字列は
       str と Literal の腕が defenum の腕より先)。配列は受ける腕のうち宣言の先の腕。
    3. object を受ける腕は宣言の順に試し、dict の腕が受ければそれ。入れ子の defwire の腕が 2 つ以上受ければ入力から埋めた欄の多い腕・
       同じ数なら宣言の先の腕。
    4. 何でも受ける腕(OpaqueJson・object)は、他のどの腕も受けない時だけ受ける。

    None でない腕が 1 つなら、その腕の誤りを場所に腕の名を付けずに返す((| T None) — 今と同じ)。2 つ以上で、どの腕も受けない時は、
    腕ごとに場所 <腕の名> で誤りを宣言の順に並べる。
    """

    __slots__ = ("_arms", "_by_kind", "_fallback", "_nullable")

    def __init__(self, arms: tuple[_Node, ...], nullable: bool) -> None:
        super().__init__(f"union[{','.join(arm.label for arm in arms)}]")
        self._arms = arms
        self._nullable = nullable
        typed = [arm for arm in arms if not arm.catch_all]
        self._by_kind: dict[str, tuple[_Node, ...]] = {
            kind: tuple(
                sorted((arm for arm in typed if kind in arm.kinds), key=lambda arm: arm.rank)
            )
            for kind in ("str", "int", "float", "bool", "array", "object")
        }
        self._fallback = next((arm for arm in arms if arm.catch_all), None)
        if nullable:
            self.label = f"nullable[{arms[0].label if len(arms) == 1 else self.label}]"

    def decode(self, value: object) -> object:
        if value is None and self._nullable:
            return None
        if len(self._arms) == 1:
            return self._arms[0].decode(value)
        chosen = self._choose(value)
        if chosen is not _NO_ARM:
            return chosen
        if self._fallback is not None:
            return self._fallback.decode(value)
        raise _RefusedError(
            [
                problem
                for arm in self._arms
                for problem in _problems_of(arm.label, _attempt(arm, value))
            ]
        )

    def _choose(self, value: object) -> object:
        """規則 1〜3 で腕を選んで解く(どの腕も受けなければ _NO_ARM — 呼び手が規則 4 と誤りの列を受け持つ)。"""
        kind = _KIND_OF.get(type(value))
        if kind is None:
            return _NO_ARM
        if kind != "object":
            for arm in self._by_kind[kind]:
                try:
                    return arm.decode(value)
                except _RefusedError:
                    continue
            return _NO_ARM
        best: tuple[object, int] | None = None
        for arm in self._by_kind["object"]:
            try:
                if isinstance(arm, _WireNode):
                    counted = arm.decode_counted(value)
                    if best is None or counted[1] > best[1]:
                        best = counted
                else:
                    return arm.decode(value)
            except _RefusedError:
                continue
        return _NO_ARM if best is None else best[0]

    def encode(self, value: object, raw: bool) -> _Json:
        # 書く腕の選び方は今の pydantic と同じ: 宣言の順に型ちょうどの腕(strict)、無ければ宣言の順に書き直せる腕(lax — float の腕は
        # 整数と真偽も)、どれも合わなければ値の型から書く。
        if value is None and self._nullable:
            return None
        arms = self._arms
        if len(arms) == 1:
            return arms[0].encode(value, raw)
        for strict in (True, False):
            for arm in arms:
                if arm.fits(value, strict):
                    return arm.encode(value, raw)
        return _inferred(value, raw)

    def fits(self, value: object, strict: bool) -> bool:
        return (value is None and self._nullable) or any(
            arm.fits(value, strict) for arm in self._arms
        )

    def schema(self, defs: "_Definitions") -> _Schema:
        nulls: tuple[_Schema, ...] = ({"type": "null"},) if self._nullable else ()
        return _any_of((*(arm.schema(defs) for arm in self._arms), *nulls))

    def titled(self) -> bool:
        # (| T None) は T に従う(pydantic は nullable の中を見る)。腕が 2 つ以上の union は title を付ける。
        return not (self._nullable and len(self._arms) == 1 and not self._arms[0].titled())


_NO_ARM: Final = object()
_KIND_OF: Final[dict[type, str]] = {
    str: "str",
    int: "int",
    float: "float",
    bool: "bool",
    list: "array",
    dict: "object",
}


# --- 型の木を組む --------------------------------------------------------------------------------------------------------------

_STR: Final = _Str("str")
_INT: Final = _Int("int")
_FLOAT: Final = _Float("float")
_BOOL: Final = _Bool("bool")
_NONE: Final = _NoneOnly("none")
_BARE_DICT: Final = _BareDict("dict[any,any]")
_ANYTHING: Final = _Anything("any")
_OPAQUE: Final = _Opaque("opaque-json")
_SCALARS: Final[dict[object, _Node]] = {
    str: _STR,
    int: _INT,
    float: _FLOAT,
    bool: _BOOL,
    type(None): _NONE,
    None: _NONE,
    dict: _BARE_DICT,
    object: _ANYTHING,
    _OpaqueJson: _OPAQUE,
}

#: 組む・覚えるを 1 度にする lock(free-threading の Python — 同じ型を 2 つの thread が同時に組まない)。入れ子の型を組む時に
#: 同じ thread が入り直すので RLock。
_LOCK: Final = threading.RLock()
#: 組んでいる途中の defwire の型(輪を見つける — A → B → A の間接の輪も自分を参照する型も断る)。lock の中だけで触る。
_BUILDING: Final[set[type]] = set()
_CODEC_ATTRIBUTE: Final = "__doeff_codec__"


def _is_wire_type(annotation: object) -> bool:
    """defwire で建てた型か(dataclass で、型が __doeff_wire__ に wire の形を持つ)。"""
    return (
        isinstance(annotation, type)
        and dataclasses.is_dataclass(annotation)
        and getattr(annotation, "__doeff_wire__", None) is not None
    )


def _resolved(annotation: object, namespace: dict[str, Any], where: str) -> object:
    """文字列と前方参照で残った注記を、型の module の名の空間で解く(解けなければ型と欄を示して断る)。"""
    text = (
        annotation
        if isinstance(annotation, str)
        else annotation.__forward_arg__
        if isinstance(annotation, ForwardRef)
        else None
    )
    if text is None:
        return annotation
    try:
        return eval(
            text, namespace
        )  # 型の注記の綴り — dataclass と typing.get_type_hints が同じく読む
    except Exception as error:
        raise TypeError(
            f"{where}: 型の注記 {text!r} を解けない({type(error).__name__}: {error})"
        ) from error


def _build(annotation: object, namespace: dict[str, Any], where: str) -> _Node:
    """注記 1 つの判じ手を組む(型の木を組む唯一の入口)。語彙の外の型は TypeError(どの型のどの欄のどの綴りかを示す)。"""
    annotation = _resolved(annotation, namespace, where)
    named = _named_node(annotation, where)
    if named is not None:
        return named
    builder = _GENERIC_BUILDERS.get(typing.get_origin(annotation))
    if builder is not None:
        return builder(annotation, typing.get_args(annotation), namespace, where)
    if isinstance(annotation, type) and dataclasses.is_dataclass(annotation):
        raise TypeError(
            f"{where}: 素の defrecord(defwire でない dataclass){annotation.__name__} は語彙の外 — defwire にする"
        )
    raise TypeError(f"{where}: 語彙の外の型 {annotation!r}")


def _named_node(annotation: object, where: str) -> _Node | None:
    """名で書く型(str・int などの素の型・defenum・defwire の型)の判じ手。総称の型の形なら None(_GENERIC_BUILDERS が組む)。"""
    try:
        scalar = _SCALARS.get(annotation)
    except TypeError:  # hash できない注記は素の型でない
        scalar = None
    if scalar is not None:
        return scalar
    if isinstance(annotation, type) and issubclass(annotation, enum.Enum):
        if not all(isinstance(member.value, str) for member in annotation):
            raise TypeError(
                f"{where}: 値が文字列でない Enum {annotation.__name__} は語彙の外(defenum を使う)"
            )
        return _Enum(annotation)
    if _is_wire_type(annotation):
        return _wire_node(cast(type, annotation))
    return None


def _union(
    annotation: object, args: tuple[object, ...], namespace: dict[str, Any], where: str
) -> _Node:
    """(| T1 T2 … None)— None の腕は印(nullable)にし、残りの腕を組む。"""
    nullable = any(arg is type(None) or arg is None for arg in args)
    arms = tuple(
        _build(arg, namespace, f"{where}|{index}")
        for index, arg in enumerate(args)
        if not (arg is type(None) or arg is None)
    )
    if not arms:
        return _NONE
    return _Union(arms, nullable)


def _literal(
    annotation: object, args: tuple[object, ...], namespace: dict[str, Any], where: str
) -> _Node:
    """文字列の Literal だけを語彙に入れる(今の defwire の Literal は全部文字列)。"""
    if not args or not all(type(arg) is str for arg in args):
        raise TypeError(f"{where}: 文字列でない Literal {annotation!r} は語彙の外")
    return _Literal(cast(tuple[str, ...], args))


def _tuple(
    annotation: object, args: tuple[object, ...], namespace: dict[str, Any], where: str
) -> _Node:
    """(get tuple #(T ...))は長さの決まらない配列・(get tuple #(T1 T2 …))は長さちょうどの配列。"""
    if len(args) == 2 and args[1] is Ellipsis:
        item = _build(args[0], namespace, f"{where}[]")
        return _Array(f"tuple[{item.label}, ...]", item, "tuple_type", tuple, (tuple,))
    items = tuple(_build(arg, namespace, f"{where}[{index}]") for index, arg in enumerate(args))
    return _FixedTuple(f"tuple[{', '.join(item.label for item in items)}]", items)


def _list(
    annotation: object, args: tuple[object, ...], namespace: dict[str, Any], where: str
) -> _Node:
    """(get list T)— 配列を list にする。"""
    if len(args) != 1:
        raise TypeError(f"{where}: 要素の型の無い list {annotation!r} は語彙の外")
    item = _build(args[0], namespace, f"{where}[]")
    return _Array(f"list[{item.label}]", item, "list_type", list, (list,))


def _frozenset(
    annotation: object, args: tuple[object, ...], namespace: dict[str, Any], where: str
) -> _Node:
    """(get frozenset T)— 配列の重なりを 1 つにまとめる(要素は hash できる str か int だけ)。"""
    item = _build(args[0], namespace, f"{where}[]") if len(args) == 1 else None
    if item is None or (item is not _STR and item is not _INT):
        raise TypeError(f"{where}: frozenset の要素は str か int だけ: {annotation!r}")
    return _Array(f"frozenset[{item.label}]", item, "frozen_set_type", frozenset, (frozenset, set))


def _dict(
    annotation: object, args: tuple[object, ...], namespace: dict[str, Any], where: str
) -> _Node:
    """(get dict #(str T))— JSON の object の key は文字列なので、key が str の dict だけを語彙に入れる。"""
    if len(args) != 2 or _resolved(args[0], namespace, where) is not str:
        raise TypeError(f"{where}: key が文字列でない dict {annotation!r} は語彙の外")
    item = _build(args[1], namespace, f"{where}[]")
    return _Dict(f"dict[str,{item.label}]", item)


#: 総称の型の形(typing.get_origin の答え)→ 判じ手を組む関数。ここに無い形(Annotated・datetime など)は語彙の外。
_GENERIC_BUILDERS: Final[
    dict[object, Callable[[object, tuple[object, ...], dict[str, Any], str], _Node]]
] = {
    typing.Union: _union,
    types.UnionType: _union,
    typing.Literal: _literal,
    tuple: _tuple,
    list: _list,
    frozenset: _frozenset,
    dict: _dict,
}


def _wire_node(wire_type: type) -> _WireNode:
    """defwire の型の判じ手(型に覚えた物か、無ければ lock の中で 1 度だけ組む)。"""
    known = wire_type.__dict__.get(_CODEC_ATTRIBUTE)
    if known is not None:
        return cast(_WireNode, cast(Codec[Any], known).node)
    with _LOCK:
        known = wire_type.__dict__.get(_CODEC_ATTRIBUTE)
        if known is not None:
            return cast(_WireNode, cast(Codec[Any], known).node)
        if wire_type in _BUILDING:
            raise TypeError(f"defwire {wire_type.__qualname__}: 型の輪(自分を参照する欄)は語彙の外")
        _BUILDING.add(wire_type)
        try:
            node = _WireNode(wire_type)
            shape = wire_type.__doeff_wire__
            names = cast(Mapping[str, str], shape.names)
            module = sys.modules.get(wire_type.__module__)
            namespace: dict[str, Any] = dict(vars(module)) if module is not None else {}
            fields = tuple(
                _WireField(
                    field.name,
                    names[field.name],
                    _build(
                        field.type, namespace, f"defwire {wire_type.__qualname__} の欄 {field.name}"
                    ),
                    field.default,
                    None if field.default_factory is dataclasses.MISSING else field.default_factory,
                )
                for field in dataclasses.fields(wire_type)
                if field.init
            )
            node.fill(fields, shape.unknown == "reject")
            setattr(wire_type, _CODEC_ATTRIBUTE, Codec(node, wire_type.__name__))
        finally:
            _BUILDING.discard(wire_type)
        return node


# --- JSON の読み ---------------------------------------------------------------------------------------------------------------

#: 孤立しうる surrogate の escape(\ud800〜\udfff)。在る時だけ読んだ値を歩いて、組にならなかった surrogate を探す。
_SURROGATE_ESCAPE: Final = re.compile(r"\\u[dD][89a-fA-F][0-9a-fA-F]{2}")
_SURROGATE: Final = re.compile("[\ud800-\udfff]")


def _json_invalid(error: object) -> _RefusedError:
    """JSON として読めない入力の誤り(場所は値の全体)。"""
    return _refuse("json_invalid", f"Invalid JSON: {error}")


def _has_lone_surrogate(value: object) -> bool:
    """読んだ値に組にならなかった surrogate が在るか(今の pydantic が断る文字列を、同じく断るため)。"""
    if type(value) is str:
        return _SURROGATE.search(cast(str, value)) is not None
    if type(value) is list:
        return any(_has_lone_surrogate(item) for item in cast(list[object], value))
    if type(value) is dict:
        return any(
            _has_lone_surrogate(key) or _has_lone_surrogate(item)
            for key, item in cast(dict[str, object], value).items()
        )
    return False


#: 配列と object の入れ子の深さの上限 — 今の pydantic(jiter)は 200 階層まで読み、201 階層目を json_invalid(recursion limit
#: exceeded)で断る(照らしの test が見つけた差 — json.loads は 1000 階層ほどまで読むので、上限を置かないと受ける物が広がる)。
_DEPTH_LIMIT: Final = 200
_TOO_DEEP: Final = f"recursion limit exceeded (nesting deeper than {_DEPTH_LIMIT})"


def _deeper_than(value: object, room: int) -> bool:
    """読んだ値の配列と object の入れ子が、あと room 階層に収まらないか(room = 0 の所に入れ物が在れば越える)。"""
    match value:
        case list():
            return room == 0 or any(
                _deeper_than(item, room - 1) for item in cast(list[object], value)
            )
        case dict():
            return room == 0 or any(
                _deeper_than(item, room - 1) for item in cast(dict[str, object], value).values()
            )
        case _:
            return False


def _read_json(text: str | bytes) -> object:
    """JSON の文字列を素の値へ読む。今の pydantic と同じ物だけを受ける(設計の記録の節 3「JSON の読み」): bytes は UTF-8 だけ・
    孤立した surrogate は断る・json.loads の ValueError(4300 桁を越える整数も)と RecursionError と、配列と object の入れ子が
    _DEPTH_LIMIT を越える物は json_invalid。"""
    if isinstance(text, bytes | bytearray | memoryview):
        try:
            text = bytes(text).decode("utf-8")
        except UnicodeDecodeError as error:
            raise _json_invalid(error) from None
    elif not text.isascii():
        try:
            text.encode("utf-8")
        except UnicodeEncodeError as error:
            raise _json_invalid(error) from None
    try:
        value = json.loads(text)
    except (ValueError, RecursionError) as error:
        raise _json_invalid(error) from None
    if text.count("[") + text.count("{") > _DEPTH_LIMIT and _deeper_than(value, _DEPTH_LIMIT):
        raise _json_invalid(_TOO_DEEP)
    if "\\u" in text and _SURROGATE_ESCAPE.search(text) is not None and _has_lone_surrogate(value):
        raise _json_invalid("lone surrogate in a string")
    return value


def _plain_key(key: object) -> str:
    # json.dumps と同じく、文字列でない key を文字列にする(真偽・None・数)。
    """json.dumps と同じく、文字列でない object の key を文字列にする(真偽・None・数)。"""
    if type(key) is str:
        return cast(str, key)
    if isinstance(key, str):
        return str.__str__(key)
    if key is True or key is False or key is None or isinstance(key, int | float):
        return json.dumps(key)
    raise TypeError(f"JSON の object の key でない: {type(key).__name__}")


def _plain(value: object, room: int = _DEPTH_LIMIT) -> object:
    """JSON の値(凍らせた FrozenMap・tuple・OpaqueJson・str や int の派生も)を、json.loads が返す素の値へ戻す。

    答えは「json.dumps で書いて json.loads で読んだ値」と同じ(文字列を経ずに作る)。JSON の値でない物(set・任意の object)は
    TypeError(今の parse の json.dumps と同じ)。配列と object の入れ子が _DEPTH_LIMIT を越える物は、文字列から読んだ時と同じく
    json_invalid(room = まだ入れる入れ子の階層の数)。
    """
    kind = type(value)
    if kind is str:
        text = cast(str, value)
        if not text.isascii() and _SURROGATE.search(text) is not None:
            raise _json_invalid("lone surrogate in a string")
        return text
    if kind is int or kind is float or kind is bool or value is None:
        return value
    if kind is dict or isinstance(value, Mapping):
        if room == 0:
            raise _json_invalid(_TOO_DEEP)
        return {
            _plain_key(key): _plain(item, room - 1)
            for key, item in cast(Mapping[object, object], value).items()
        }
    if kind is list or kind is tuple or isinstance(value, list | tuple):
        if room == 0:
            raise _json_invalid(_TOO_DEEP)
        return [_plain(item, room - 1) for item in cast(list[object], value)]
    return _plain_derived(value, room)


def _plain_derived(value: object, room: int) -> object:
    """素の JSON の型でない値(OpaqueJson と str・int・float の派生 — StrEnum・IntEnum など)を、json.dumps が書く値へ戻す。"""
    if isinstance(value, _OpaqueJson):
        loaded = json.loads(value.text)
        if _deeper_than(loaded, room):
            raise _json_invalid(_TOO_DEEP)
        return loaded
    if isinstance(value, str):
        return _plain(str.__str__(value))
    if isinstance(value, int | float):  # bool は _plain で済んでいる
        return (int if isinstance(value, int) else float)(value)
    raise TypeError(f"JSON の値でない: {type(value).__name__}")


# --- 書き出し ------------------------------------------------------------------------------------------------------------------
# 判じ手の encode は値を JSON の素の値(dict・list・str・int・float・bool・None)へ書く。文字列へ書く時(raw)は OpaqueJson の文字列を
# _Raw のまま残し、_json_text が出力へそのまま埋める(今の OpaqueSplice と同じ意味 — 中を読み直さない)。


@final
class _Raw:
    """文字列へ書く途中の、OpaqueJson が持っている JSON の文字列(読み直さずに出力へ埋める)。"""

    __slots__ = ("text",)

    def __init__(self, text: str) -> None:
        self.text = text


#: そのまま埋めると JSON でなくなる綴り(Python の json.dumps が NaN・inf をそう書く)。含む文字列は読み直して書く(NaN は null — 今と同じ)。
_NOT_STRICT_JSON: Final = ("NaN", "Infinity")
_ENCODE_STRING: Final = json.encoder.encode_basestring
#: 配列に書く入れ物(値の型から書く時と、union の腕を lax で当てる時)。
_ARRAYS: Final = (list, tuple, set, frozenset)


def _is_array(value: object) -> TypeGuard[Collection[object]]:
    """配列に書く入れ物か(list・tuple・set・frozenset — 文字列と dict は入れない)。要素は読むだけなので object として絞る。"""
    return isinstance(value, _ARRAYS)


def _is_mapping(value: object) -> TypeGuard[Mapping[object, object]]:
    """object に書く写像か(dict と凍らせた FrozenMap)。key と値は読むだけなので object として絞る。"""
    return isinstance(value, Mapping)


def _opaque_out(value: _OpaqueJson, raw: bool) -> _Json:
    """OpaqueJson の書き出し: 文字列へ書く時は持っている文字列をそのまま埋め、JSON の値へ書く時と NaN・Infinity を含む時は読み直して
    値の型から書く(今の pydantic の dump・dump-json と同じ)。"""
    text = value.text
    if raw and not any(spelling in text for spelling in _NOT_STRICT_JSON):
        return _Raw(text)
    return _inferred(json.loads(text), raw)


def _json_key(key: object) -> str:
    """値の型から書く object の key の綴り(今の pydantic と同じ: 文字列の派生と defenum は値の綴り・真偽は true / false・None は None・
    数は数の綴り)。他の key は TypeError。"""
    if isinstance(key, enum.Enum):
        return _json_key(key.value)
    if isinstance(key, str):
        return str.__str__(key)
    if key is None:
        return "None"
    if isinstance(key, int | float):  # 真偽は true / false・数は書き出しと同じ綴り
        return _json_text(_inferred(key, False))
    raise TypeError(f"JSON の object の key に書けない: {type(key).__name__}")


def _inferred(value: object, raw: bool) -> _Json:
    """型の木の外の値(object・素の dict の欄の中身と、欄の型に合わない値)を、値の型から JSON の素の値へ書く。今の pydantic の値の型
    からの書き出しと同じ: NaN・inf は null・tuple と set は配列・defenum は値の綴り。defwire の値と OpaqueJson はその型の書き出し
    (pydantic は欄の Python の名・OpaqueJson の欄 text を書いていた — 節 3b)。JSON に書けない値は TypeError。"""
    if value is None or type(value) is str or type(value) is int or type(value) is bool:
        return value
    if type(value) is float:
        return value if math.isfinite(value) else None
    if _is_mapping(value):
        return {_json_key(key): _inferred(item, raw) for key, item in value.items()}
    if _is_array(value):
        return [_inferred(item, raw) for item in value]
    return _inferred_named(value, raw)


def _inferred_named(value: object, raw: bool) -> _Json:
    """_inferred の続き — 素の JSON の型でない値(OpaqueJson・defwire の値・defenum・str や数の派生)を書く。"""
    if isinstance(value, _OpaqueJson):
        return _opaque_out(value, raw)
    if _is_wire_type(type(value)):
        return codec_of(type(value)).node.encode(value, raw)
    if isinstance(value, enum.Enum):
        return _inferred(value.value, raw)
    if isinstance(value, str):
        return str.__str__(value)
    if isinstance(value, int):  # 真偽は _inferred で済んでいる
        return int(value)
    if isinstance(value, float):
        return _inferred(float(value), raw)
    raise TypeError(f"JSON の値でない: {type(value).__name__}")


def _float_text(number: float) -> str:
    """float の JSON の綴り。今の pydantic(serde_json と ryu)と同じ: 最短で戻る桁(Python の repr と同じ桁)・指数の頭の 0 を書かない
    (`1e-7`)・10 の -5 乗の桁は小数で書く(`0.00001`)・NaN と inf は null(設計の記録の節 2「float の綴り」)。"""
    if not math.isfinite(number):
        return "null"
    text = float.__repr__(number)
    if "e" not in text:
        return text
    mantissa, exponent = text.split("e")
    power = int(exponent)
    if power == -5:
        sign, digits = ("-", mantissa[1:]) if mantissa.startswith("-") else ("", mantissa)
        return f"{sign}0.0000{digits.replace('.', '')}"
    return f"{mantissa}e{'+' if power > 0 else '-'}{abs(power)}"


def _string_text(text: str) -> str:
    """文字列 1 つの JSON の綴り(今の pydantic と同じ: `"` と `\\` と制御文字だけを escape し、非 ASCII・U+2028・DEL はそのまま)。
    孤立した surrogate は UTF-8 にできないので ValueError(今の pydantic の dump-json も書かずに断る)。"""
    if not text.isascii() and _SURROGATE.search(text) is not None:
        raise ValueError("孤立した surrogate を含む文字列は JSON の文字列(UTF-8)に書けない")
    return _ENCODE_STRING(text)


def _json_text(value: _Json) -> str:
    """書き出した JSON の素の値を JSON の文字列にする(区切りの空白無し・ensure_ascii 無し — 今の dump-json と同じ bytes)。"""
    # 多い順に型を見る(文字列・object・配列)。join には内包表記の list を渡す(generator より速い — 1 回の書き出しで数十回呼ぶ所)。
    if isinstance(value, str):
        return _string_text(value)
    if isinstance(value, dict):
        return (
            "{"
            + ",".join([_string_text(key) + ":" + _json_text(item) for key, item in value.items()])
            + "}"
        )
    if isinstance(value, list):
        return "[" + ",".join([_json_text(item) for item in value]) + "]"
    return _scalar_text(value)


def _scalar_text(value: _Json) -> str:
    """入れ物でない書き出しの値の綴り(数・真偽・null と、OpaqueJson の文字列 — 真偽は int より先に見る)。"""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return int.__repr__(value)
    if isinstance(value, float):
        return _float_text(value)
    if isinstance(value, _Raw):
        return value.text
    if value is None:
        return "null"
    raise TypeError(f"書き出しの値でない: {type(value).__name__}")


# --- JSON Schema ---------------------------------------------------------------------------------------------------------------


@final
class _RefMark:
    """$ref の行き先の印(型 1 つに 1 つ)。組み終えてから $defs の名に替える — 同じ名の違う型が在れば名に module を付けるため。"""

    __slots__ = ("target",)

    def __init__(self, target: type) -> None:
        self.target = target


@final
class _Definitions:
    """JSON Schema 1 つを組む間の $defs。defwire の型と defenum を 1 度ずつ組んで置き、欄からは $ref で指す。"""

    __slots__ = ("marks", "shapes")

    def __init__(self) -> None:
        self.marks: dict[type, _RefMark] = {}
        self.shapes: dict[type, _Schema] = {}

    def ref(self, target: type, build: Callable[["_Definitions"], _Schema]) -> _Schema:
        """型を $defs に(まだ無ければ組んで)置き、それを指す $ref を返す。"""
        mark = self.marks.get(target)
        if mark is None:
            mark = _RefMark(target)
            self.marks[target] = mark
            self.shapes[target] = build(self)
        return {"$ref": mark}

    def names(self) -> dict[type, str]:
        """$defs の名: 型の名。同じ名の違う型が在れば、それらは module と qualname の綴り(今の pydantic と同じ — `.` は `__`)。"""
        counts = Counter(target.__name__ for target in self.shapes)
        return {
            target: target.__name__
            if counts[target.__name__] == 1
            else re.sub(
                r"[^a-zA-Z0-9.\-_]", "_", f"{target.__module__}.{target.__qualname__}"
            ).replace(".", "__")
            for target in self.shapes
        }


def _schema_default(value: object) -> _Json:
    """JSON Schema の欄の default — 既定値を値の型から書く(型のある float の NaN・inf は float のまま — 今の pydantic と同じ)。
    入れ子の defwire と OpaqueJson の既定値は書き手の書く形(pydantic は欄の Python の名と OpaqueJson の欄 text を書いていた — 節 3b)。"""
    if type(value) is float:
        return value
    return _inferred(value, False)


def _field_schema(field: _WireField, defs: _Definitions) -> _Schema:
    """欄 1 つの JSON Schema: 型の形に、既定値(作り手の欄は書かない)と title(wire の名から — 型の側が title を持つ defwire・defenum と
    その (| T None) には付けない)を足す(今の pydantic と同じ)。"""
    default: _Schema = (
        {"default": _schema_default(field.default)}
        if field.default is not dataclasses.MISSING
        else {}
    )
    title: _Schema = (
        {"title": field.wire.title().replace("_", " ").strip()} if field.node.titled() else {}
    )
    return {**field.node.schema(defs), **default, **title}


def _json_hashable(value: _Json) -> object:
    """JSON Schema の値を、同じ形の腕を 1 つにするための hash できる形へ(pydantic の _make_json_hashable と同じ)。"""
    if isinstance(value, dict):
        return tuple(sorted((key, _json_hashable(item)) for key, item in value.items()))
    if isinstance(value, list):
        return tuple(_json_hashable(item) for item in value)
    return value


def _arms_of(schema: _Schema) -> list[_Schema]:
    """union の腕 1 つの形を、anyOf だけの形なら開いて並べる(pydantic の get_flattened_anyof と同じ)。"""
    arms = schema.get("anyOf")
    if len(schema) == 1 and isinstance(arms, list):
        return [arm for arm in arms if isinstance(arm, dict)]
    return [schema]


def _any_of(members: tuple[_Schema, ...]) -> _Schema:
    """union の JSON Schema(今の pydantic と同じ: anyOf だけの腕は開き・同じ形の腕は 1 つにし・腕が 1 つなら anyOf で包まない)。"""
    unique = list(
        {_json_hashable(arm): arm for member in members for arm in _arms_of(member)}.values()
    )
    if len(unique) == 1:
        return unique[0]
    arms: list[_Json] = [*unique]
    return {"anyOf": arms}


def _sorted_schema(value: _Json, parent: str | None, names: dict[type, str]) -> _Json:
    """組んだ JSON Schema の鍵を今の pydantic と同じ順に並べ($defs の名も鍵の順)、$ref の印を名に替える。並べ方は pydantic の sort と
    同じ: properties と default の直下は宣言の順のまま・他は鍵の文字の順・list の要素は親の鍵を引き継ぐ(契約の file を bytes で照らす
    書き手が在るので順も揃える)。"""
    if isinstance(value, dict):
        keys = list(value) if parent in ("properties", "default") else sorted(value)
        return {key: _sorted_schema(value[key], key, names) for key in keys}
    if isinstance(value, list):
        return [_sorted_schema(item, parent, names) for item in value]
    if isinstance(value, _RefMark):
        return f"#/$defs/{names[value.target]}"
    return value


def _schema_of(node: _Node) -> _Schema:
    """型の木の根の JSON Schema。最上位の defwire の型と defenum は $ref でなく形をそのまま置き、入れ子の型は $defs に置く。"""
    defs = _Definitions()
    top = node.definition(defs) if isinstance(node, _WireNode | _Enum) else node.schema(defs)
    names = defs.names()
    nested: _Schema = {names[target]: shape for target, shape in defs.shapes.items()}
    whole = _sorted_schema({**top, "$defs": nested} if nested else top, None, names)
    if not isinstance(whole, dict):
        raise TypeError(
            "JSON Schema の根が object でない"
        )  # 根は object の形を並べ替えた物 — 起きない
    return whole


# --- 公開の口 ------------------------------------------------------------------------------------------------------------------


@final
class Codec(Generic[_T]):
    """語彙の型 1 つの解き手(型の木の根と、Malformed に書く型の名)。``codec_of`` だけが作る。"""

    __slots__ = ("name", "node")

    def __init__(self, node: _Node, name: str) -> None:
        self.node = node
        self.name = name

    def decode_json(self, text: str | bytes) -> _T | _Malformed:
        """JSON の文字列(bytes も)を解いて確かめる。答え = 型の値か Malformed(JSON として読めない文字列も — 場所は空の文字列)。"""
        try:
            return cast(_T, self.node.decode(_read_json(text)))
        except _RefusedError as invalid:
            return self._malformed(invalid.problems)

    def decode(self, value: object) -> _T | _Malformed:
        """JSON の値(凍らせた FrozenMap・tuple と、中を読まずに運んだ OpaqueJson も)を解いて確かめる。答えは同じ値を JSON の
        文字列にして decode_json に渡した時と同じ。JSON の値でない物は TypeError。"""
        try:
            if isinstance(value, _OpaqueJson):
                return cast(_T, self.node.decode(_read_json(value.text)))
            return cast(_T, self.node.decode(_plain(value)))
        except _RefusedError as invalid:
            return self._malformed(invalid.problems)

    def encode(self, value: _T) -> object:
        """値を JSON の値へ書く(今の dump と同じ: 欄は wire の名・tuple と frozenset は配列・defenum は値の綴り・既定値と同じ欄は書かない
        — 入れ子も・既定値の無い欄は None でも null・型のある float の欄の NaN と inf は float のまま)。JSON に書けない値は TypeError。"""
        return self.node.encode(value, False)

    def encode_json(self, value: _T) -> str:
        """値を JSON の文字列へ書く(今の dump-json と同じ bytes: encode と同じ形・区切りの空白無し・NaN と inf は null・OpaqueJson の欄は
        持っている文字列を読み直さずに埋める)。孤立した surrogate を含む文字列は ValueError・JSON に書けない値は TypeError。"""
        return _json_text(self.node.encode(value, True))

    def schema(self) -> Mapping[str, object]:
        """この型の JSON Schema(今の json-schema と同じ形・鍵の順 — 違いは設計の記録の節 3b)。呼ぶたびに組む(契約の file と照らす
        所だけが使う — 型には覚えない)。"""
        return _schema_of(self.node)

    def _malformed(self, problems: list[_Problem]) -> _Malformed:
        """集めた誤りの列を Malformed へ写す(場所は . でつなぐ・reason は `<msg> [<種類>]`)。"""
        return _Malformed(
            wire_type=self.name,
            fields=tuple(
                _MalformedField(field=".".join(str(part) for part in loc), reason=f"{msg} [{kind}]")
                for loc, kind, msg in problems
            ),
        )


#: defwire でない語彙の型(最上位の (get dict #(str OpaqueJson)) など)の Codec。defwire の型の Codec は型の属性に覚える。
_ALIAS_CODECS: Final[dict[object, Codec[Any]]] = {}


@typing.overload
def codec_of(wire_type: type[_T]) -> Codec[_T]: ...
@typing.overload
def codec_of(wire_type: object) -> Codec[Any]: ...
def codec_of(wire_type: object) -> Codec[Any]:
    """語彙の型 1 つの解き手。defwire の型は最初の使いで型の木を 1 度組んで型に覚え(``__doeff_codec__``)、2 回目からは組まない。
    defwire でない語彙の型(``str``・``(get dict #(str OpaqueJson))`` など — 最上位として解く)も同じ規則で組んで覚える。
    語彙の外の型・解けない前方参照・型の輪は TypeError。"""
    if _is_wire_type(wire_type):
        known = cast(type, wire_type).__dict__.get(_CODEC_ATTRIBUTE)
        if known is None:
            _wire_node(cast(type, wire_type))
            known = cast(type, wire_type).__dict__[_CODEC_ATTRIBUTE]
        return cast(Codec[Any], known)
    known_alias = _ALIAS_CODECS.get(wire_type)
    if known_alias is not None:
        return known_alias
    with _LOCK:
        known_alias = _ALIAS_CODECS.get(wire_type)
        if known_alias is None:
            node = _build(wire_type, {}, f"型 {wire_type!r}")
            known_alias = Codec(node, node.label)
            _ALIAS_CODECS[wire_type] = known_alias
        return known_alias
