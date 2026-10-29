"""defwire の自前の解き手(wire_codec.py)を pydantic の解き手と照らす道具 — test の中だけで読む(card ki-b7701d5fc0dd)。

利用者の決定 2026-10-27(依頼書 lt-SJK77NQZ8NSESDVVZ0P41407XQ の問い decoder の答え): defwire の解き手は doeff の中に自前で持ち、pydantic は
test で照らす相手としてだけ残す。この module はその「照らす相手」の唯一の置き場で、pydantic を読む。job の道(入口・foundation・本体)は
この module を import しない(doeff_hy._wire・wire_codec は読まない — import の閉包の test が確かめる)。pydantic は doeff-hy の本体の
依存に無く、照らしの test を持つ project が test の依存の群に置く extra doeff-hy[parity] から入る(card ki-5ddadb736f62)。job の道の
module が pydantic を import したら .semgrep.yaml の doeff-job-path-imports-no-pydantic が断り、この module はその範囲の外に置く。

契約は設計の記録 ``docs/design/defwire-decoder/design.md`` の節 7「照らしの test が比べる物」:

- 型ごとに型の木から正しい入力を作り、欄ごとに崩した入力(JSON の種類の違う値・null・欄の欠け・知らない欄・Python の欄の名・定義の外の
  綴り・配列の長さの ±1・入れ子の 2 階層まで)と、文字列そのものの崩し(JSON でない文字列・最上位の種類の違い)を作る。
- 同じ入力を自前の解き手と pydantic の解き手に通し、受ける・断るが等しい事・受けた値が値と欄の値の型まで等しい事・断りの (場所, 種類)
  の列が等しい事を確かめる。msg は種類 missing・enum・value_error・assertion_error の綴りだけを比べる(他の種類の msg は種類が決める)。
- 受けた値を両方の書き手で書き、dump が値と値の型まで・dump-json が bytes まで等しい事を確かめる。
- JSON Schema が鍵の順まで等しい事を確かめる。違うのは節 3b の表の既定値(入れ子の defwire・OpaqueJson)だけで、その欄は差として数える。
- 何でも受ける腕(OpaqueJson・object)と None 以外の腕が混ざった union の欄を持つ型は、受けた値の比べから外して数える(節 3 の規則 4 —
  pydantic と違う答えを正とする所)。受ける・断るの比べには残す。

使い方(agora-controllers の型にも使えるよう、型の一覧を引数に取る):

    types, failures = wire_types_in(["doeff_cluster"])   # package の下の module を全部読み、defwire の型を集める
    report = compare_types(types)                         # 型ごとの照らし
    assert report.mismatches == []                        # 不一致の 1 件ずつ(型・入力・何が違うか)

pydantic の解き手は、今の defwire の型の ``__pydantic_config__``(:names の wire の名だけを受けて書く・:unknown・strict)で組む。型が
持たなければ、この module が同じ設定を置く(wire の形 ``__doeff_wire__`` の names と unknown から)。
"""

import dataclasses
import importlib
import json
import math
import re
import secrets
import sys
import warnings
from collections.abc import Callable, Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any, Final, cast

from pydantic import ConfigDict, TypeAdapter, ValidationError

from doeff_hy._frozen import thaw_json
from doeff_hy._json_value import OpaqueJson
from doeff_hy._wire_codec import (
    Codec,
    _Anything,
    _Array,
    _BareDict,
    _Bool,
    _Dict,
    _Enum,
    _FixedTuple,
    _Float,
    _Int,
    _Literal,
    _Node,
    _NoneOnly,
    _Opaque,
    _Str,
    _Union,
    _WireNode,
    codec_of,
)
from doeff_hy._wire_malformed import Malformed, MalformedField

# --- pydantic の解き手(今の wire.hy の parse-json・dump・dump-json・json-schema と同じ設定と呼び)----------------------------------

#: 知らない欄の扱いの綴り(defwire の :unknown の値)→ pydantic の extra。
_EXTRA: Final = {"reject": "forbid", "ignore": "ignore"}


def pydantic_config(names: Mapping[str, str], unknown: str) -> ConfigDict:
    """defwire の型の pydantic の設定: 欄の名は names の写しだけを受けて書き、知らない欄は unknown に従い、型の検めは厳しい。"""
    return ConfigDict(
        alias_generator=names.__getitem__,
        validate_by_alias=True,
        validate_by_name=False,
        serialize_by_alias=True,
        extra=cast(Any, _EXTRA[unknown]),
        strict=True,
        defer_build=True,
    )


def _configured(wire_type: type) -> None:
    """型(と入れ子の defwire の型)が pydantic の設定を持たなければ、wire の形から置く。"""
    if "__pydantic_config__" in wire_type.__dict__:
        return
    shape = wire_type.__dict__["__doeff_wire__"]
    wire_type.__pydantic_config__ = pydantic_config(shape.names, shape.unknown)  # type: ignore[attr-defined]


_ADAPTERS: Final[dict[type, TypeAdapter[Any]]] = {}


def adapter_of(wire_type: type) -> TypeAdapter[Any]:
    """defwire の型の pydantic の解き手(型ごとに 1 度組んで覚える)。"""
    known = _ADAPTERS.get(wire_type)
    if known is None:
        for nested in _wire_types_under(codec_of(wire_type).node):
            _configured(nested)
        known = TypeAdapter(wire_type)
        _ADAPTERS[wire_type] = known
    return known


def _malformed_of(wire_type: type, error: ValidationError) -> Malformed:
    """pydantic の検めの誤りを Malformed へ写す(欄の場所は wire の名・配列の位置を . でつなぐ — 今の wire.hy の malformed-of)。"""
    return Malformed(
        wire_type=wire_type.__name__,
        fields=tuple(
            MalformedField(
                field=".".join(str(part) for part in problem["loc"]),
                reason=f"{problem['msg']} [{problem['type']}]",
            )
            for problem in error.errors(include_url=False)
        ),
    )


def pydantic_parse_json(wire_type: type, text: str | bytes) -> object:
    """今の wire.hy の parse-json と同じ答え(型の値か Malformed)。"""
    try:
        return adapter_of(wire_type).validate_json(text)
    except ValidationError as error:
        return _malformed_of(wire_type, error)


def pydantic_parse(wire_type: type, raw: object) -> object:
    """今の wire.hy の parse と同じ答え(JSON の値を文字列へ書き直して parse-json へ)。"""
    text = (
        raw.text if isinstance(raw, OpaqueJson) else json.dumps(thaw_json(raw), ensure_ascii=False)
    )
    return pydantic_parse_json(wire_type, text)


def pydantic_dump(value: object) -> object:
    """今の wire.hy の dump と同じ答え。"""
    return adapter_of(type(value)).dump_python(
        value, mode="json", by_alias=True, exclude_defaults=True
    )


def pydantic_dump_json(value: object) -> str:
    """今の wire.hy の dump-json と同じ答え(OpaqueJson の欄は持っている文字列をそのまま埋める — _Splice)。"""
    splice = _Splice()
    written = adapter_of(type(value)).dump_json(
        value, by_alias=True, exclude_defaults=True, context=splice
    )
    return splice.fill(written)


def pydantic_json_schema(wire_type: type) -> object:
    """今の wire.hy の json-schema と同じ答え。"""
    return adapter_of(wire_type).json_schema(by_alias=True)


# 埋め込みの印(今の json_value.py の OpaqueSplice と同じ仕掛け — pydantic_core に書き済みの JSON の文字列を出力へ入れる口が無いので、
# OpaqueJson の書き手が印を返し、出力の bytes ができた後で印を元の文字列に替える)。OpaqueJson の pydantic 向けの書き手
# (json_value.py の _opaque_value)は、context が admit を持つ時に印を返す。
_SPLICE_PREFIX: Final = secrets.token_hex(8)
_SPLICE_MARK: Final = re.compile(b'"' + _SPLICE_PREFIX.encode("ascii") + rb"([0-9]+)\"")
_NOT_STRICT_JSON: Final = ("NaN", "Infinity")


class _Splice:
    """pydantic_dump_json 1 回の間だけ生きる、OpaqueJson の文字列を出力へそのまま埋める控え。"""

    __slots__ = ("_by_number",)

    def __init__(self) -> None:
        self._by_number: dict[int, bytes] = {}

    def admit(self, text: str) -> str | None:
        if any(spelling in text for spelling in _NOT_STRICT_JSON):
            return None
        number = len(self._by_number)
        self._by_number[number] = text.encode("utf-8")
        return f"{_SPLICE_PREFIX}{number}"

    def fill(self, written: bytes) -> str:
        by_number = self._by_number
        if not by_number:
            return written.decode("utf-8")
        return _SPLICE_MARK.sub(lambda mark: by_number[int(mark[1])], written).decode("utf-8")


# --- 型を集める -----------------------------------------------------------------------------------------------------------------


def _is_wire_type(candidate: object) -> bool:
    return (
        isinstance(candidate, type)
        and dataclasses.is_dataclass(candidate)
        and "__doeff_wire__" in candidate.__dict__
    )


def _module_names(package_name: str) -> list[str]:
    """package の下の module の名を、file の置き場から全部並べる(__init__ の無い名の空間の dir と Hy の module も — pkgutil は
    どちらも辿らない)。"""
    package = importlib.import_module(package_name)
    roots = [Path(entry) for entry in getattr(package, "__path__", [])]
    return [package_name] + sorted(
        {
            ".".join((package_name, *path.relative_to(root).with_suffix("").parts)).removesuffix(
                ".__init__"
            )
            for root in roots
            for path in root.rglob("*")
            if path.suffix in (".py", ".hy")
            and all(part.isidentifier() for part in path.relative_to(root).with_suffix("").parts)
        }
    )


def _imported(name: str) -> object:
    """module を読む(読めなければ訳の文 — 任意の依存の無い module も在るので、訳を返して呼び手が決める)。"""
    try:
        return importlib.import_module(name)
    except Exception as error:
        return f"{type(error).__name__}: {error}"
    except SystemExit as error:  # 読んだ時に終わる script の module
        return f"SystemExit: {error}"


def wire_types_in(
    packages: Iterable[str], *, skip: Callable[[str], bool] = lambda _name: False
) -> tuple[list[type], dict[str, str]]:
    """package の下の module を全部読み(skip が真の module の名は読まない)、そこで定義した defwire の型を集める。
    答え = (型の列 — module の名と定義の順・読めなかった module の名 → 訳)。"""
    names = sorted(
        {
            name
            for package_name in packages
            for name in _module_names(package_name)
            if not skip(name)
        }
    )
    modules = [(name, _imported(name)) for name in names]
    failures = {name: module for name, module in modules if isinstance(module, str)}
    found = [
        candidate
        for name, module in modules
        if not isinstance(module, str)
        for candidate in list(vars(module).values())
        if _is_wire_type(candidate) and candidate.__module__ == name
    ]
    return list(dict.fromkeys(found)), failures


# --- 入力を作る -----------------------------------------------------------------------------------------------------------------

#: どの欄にも入れる、JSON の種類の違う値(欄の型の外の値は両方が同じ訳で断る事を見る)。
_WRONG_KINDS: Final[tuple[object, ...]] = (
    "x",
    7,
    2.5,
    False,
    None,
    [],
    {},
    [1],
    {"zz": 1},
    0,
    -1,
    "",
    1e400,
)
_UNKNOWN_KEY: Final = "zzUnknownField"
#: 入れ子に入って崩す深さ(最上位を 0 と数えて、入れ子の object を 2 階層まで)。
_DEPTH: Final = 2


def _nodes(node: _Node, seen: set[int] | None = None) -> Iterator[_Node]:
    """型の木の判じ手を 1 つずつ(自分を含む・同じ判じ手は 1 度 — 入れ子の型は親ごとに組み直さず同じ判じ手を指す)。"""
    seen = set() if seen is None else seen
    if id(node) in seen:
        return
    seen.add(id(node))
    yield node
    for child in _children(node):
        yield from _nodes(child, seen)


def _wire_types_under(node: _Node) -> list[type]:
    """型の木の下の defwire の型(自分を含む・重なり無し)— pydantic の設定を置く型を並べるため。"""
    return [current.wire_type for current in _nodes(node) if isinstance(current, _WireNode)]


def _children(node: _Node) -> tuple[_Node, ...]:
    """判じ手の子(欄・要素・腕の判じ手)— 型の木を辿るため。"""
    match node:
        case _WireNode():
            return tuple(field.node for field in node.fields)
        case _Array() | _Dict():
            return (node._item,)
        case _FixedTuple():
            return node._items
        case _Union():
            return node._arms
        case _:
            return ()


def mixed_catch_all_unions(node: _Node) -> int:
    """何でも受ける腕と None 以外の腕が混ざった union の数(型の木の下 — 規則 4 で pydantic と違う答えを正とする所を数えるため)。"""
    return sum(
        1
        for current in _nodes(node)
        if isinstance(current, _Union)
        and len(current._arms) > 1
        and any(arm.catch_all for arm in current._arms)
    )


#: 子を持たない判じ手の見本(判じ手の型 → 受ける JSON の値の候補 — 先の物ほど使う)。
_LEAF_SAMPLES: Final[dict[type, list[object]]] = {
    _Str: ["s", "ki-000000000001", "a"],
    _Float: [1.5, 2],
    _Int: [1, 0, 2],
    _Bool: [True, False],
    _NoneOnly: [None],
    _BareDict: [{"k": 1}, {}],
    _Anything: [{"a": [1, "b"]}, "x", 1],
    _Opaque: [{"o": [1, "x", None]}, "x", 1],
}


def _union_samples(node: _Union, depth: int, full: bool) -> list[object]:
    """union の見本 — 腕ごとに 1 つと、None の腕が在れば null。"""
    arms = [sample for arm in node._arms for sample in _samples(arm, depth, full)[:1]]
    return arms + ([None] if node._nullable else [])


#: 子を持つ判じ手の見本の作り手(判じ手の型 → (判じ手・深さ・既定値の在る欄も書くか) → 候補)。
_SAMPLERS: Final[dict[type, Callable[[Any, int, bool], list[object]]]] = {
    _Enum: lambda node, _depth, _full: list(node._members)[:2],
    _Literal: lambda node, _depth, _full: list(node._order[:2]),
    _Array: lambda node, depth, full: [[_samples(node._item, depth + 1, full)[0]], []],
    _FixedTuple: lambda node, depth, full: [
        [_samples(item, depth + 1, full)[0] for item in node._items]
    ],
    _Dict: lambda node, depth, full: [{"k": _samples(node._item, depth + 1, full)[0]}, {}],
    _WireNode: lambda node, depth, full: [
        _object_sample(node, depth, full),
        _object_sample(node, depth, False),
    ],
    _Union: _union_samples,
}


def _samples(node: _Node, depth: int, full: bool) -> list[object]:
    """型の受ける JSON の値の候補(先の物ほど使う)— 正しい入力と崩す値を作るため。full = 既定値の在る欄も書く。"""
    leaf = _LEAF_SAMPLES.get(type(node))
    if leaf is not None:
        return leaf
    return _SAMPLERS[type(node)](node, depth, full)


def _object_sample(node: _WireNode, depth: int, full: bool) -> dict[str, object]:
    """defwire の型の正しい object の見本(full = 既定値の在る欄も書く)。"""
    # 入れ子が深い所(輪は木が断るが、深い入れ子は在る)は既定値の無い欄だけ書く。
    write_all = full and depth < 4
    return {
        field.wire: _samples(field.node, depth + 1, write_all)[0]
        for field in node.fields
        if field.required or write_all
    }


def _accepted_base(codec: Codec[Any], wire_type: type) -> dict[str, object]:
    """崩す元の正しい入力 — 欄を全部書いた物と既定値の無い欄だけの物のうち、pydantic が受けた方(どちらも断れば全部書いた方)。
    受けなければ、欄ごとに見本の候補を替えて受ける形を探す(:check が値を選ぶ型のため)。"""
    node = cast(_WireNode, codec.node)
    candidates = [_object_sample(node, 0, True), _object_sample(node, 0, False)]
    for candidate in candidates:
        if not isinstance(pydantic_parse_json(wire_type, json.dumps(candidate)), Malformed):
            return candidate
    base = dict(candidates[0])
    for field in node.fields:
        if field.wire not in base:
            continue
        for alternative in _samples(field.node, 1, True)[1:]:
            trial = {**base, field.wire: alternative}
            if not isinstance(pydantic_parse_json(wire_type, json.dumps(trial)), Malformed):
                return trial
    return base


def _mutations(node: _Node, value: object, depth: int) -> Iterator[tuple[str, object]]:
    """正しい値 value(型 node の JSON)を 1 か所ずつ崩した値の列(名・値)。"""
    if isinstance(node, _WireNode) and isinstance(value, dict):
        source = cast(dict[str, object], value)
        yield "unknown-field", {**source, _UNKNOWN_KEY: 1}
        for field in node.fields:
            here = field.wire
            if here in source:
                rest = {key: item for key, item in source.items() if key != here}
                yield f"{here}:missing", rest
                if field.python != field.wire:
                    yield f"{here}:python-name", {**rest, field.python: source[here]}
                for wrong in _WRONG_KINDS:
                    yield f"{here}:={json.dumps(wrong)}", {**source, here: wrong}
                for name, inner in _field_mutations(field.node, source[here], depth):
                    yield f"{here}.{name}", {**source, here: inner}
            else:
                for sample in _samples(field.node, depth + 1, False)[:2]:
                    yield f"{here}:+", {**source, here: sample}
        return
    yield from _field_mutations(node, value, depth)


def _spelling_mutations(node: Any, value: object, depth: int) -> Iterator[tuple[str, object]]:
    """defenum と Literal の崩し: 定義の外の綴りと、defenum の member の名(値の綴りでない)。"""
    yield "outside", "no-such-spelling"
    names = [member.name for member in node.enum_type] if isinstance(node, _Enum) else []
    yield from (("member-name", name) for name in names[:1])


def _array_mutations(node: Any, value: object, depth: int) -> Iterator[tuple[str, object]]:
    """長さの決まらない配列の崩し: 長くする・空にする・要素の種類の違い・最初の要素の中。"""
    if not isinstance(value, list):
        return
    sample = _samples(node._item, depth + 1, False)[0]
    yield "longer", [*value, sample, sample]
    yield "empty", []
    yield from ((f"item={json.dumps(wrong)}", [wrong]) for wrong in ("x", None, {"zz": 1}))
    if value and depth < _DEPTH:
        yield from (
            (f"0.{name}", [inner, *value[1:]])
            for name, inner in _mutations(node._item, value[0], depth + 1)
        )


def _fixed_tuple_mutations(node: Any, value: object, depth: int) -> Iterator[tuple[str, object]]:
    """長さちょうどの配列の崩し: 長さの ±1・位置ごとの種類の違い・位置ごとの中。"""
    if not isinstance(value, list):
        return
    yield "shorter", value[:-1]
    yield "longer", [*value, value[0] if value else 1]
    for index, item in enumerate(node._items):
        yield from (
            (f"{index}={json.dumps(wrong)}", [*value[:index], wrong, *value[index + 1 :]])
            for wrong in ("x", 7, None)
        )
        if depth < _DEPTH:
            yield from (
                (f"{index}.{name}", [*value[:index], inner, *value[index + 1 :]])
                for name, inner in _mutations(item, value[index], depth + 1)
            )


def _dict_mutations(node: Any, value: object, depth: int) -> Iterator[tuple[str, object]]:
    """key が文字列の dict の崩し: 値の種類の違い・最初の値の中。"""
    if not isinstance(value, dict):
        return
    yield from ((f"value={json.dumps(wrong)}", {**value, "w": wrong}) for wrong in ("x", None, [1]))
    if value and depth < _DEPTH:
        key = next(iter(value))
        yield from (
            (f"{key}.{name}", {**value, key: inner})
            for name, inner in _mutations(node._item, value[key], depth + 1)
        )


def _nested_mutations(node: Any, value: object, depth: int) -> Iterator[tuple[str, object]]:
    """入れ子の defwire の崩し(2 階層まで)。"""
    if depth < _DEPTH:
        yield from _mutations(node, value, depth + 1)


def _union_mutations(node: Any, value: object, depth: int) -> Iterator[tuple[str, object]]:
    """union の崩し: 腕ごとの値と、入れ子の defwire の腕の中。"""
    for arm in node._arms:
        yield from (
            (f"arm[{arm.label}]{index}", sample)
            for index, sample in enumerate(_samples(arm, depth + 1, True)[:2])
        )
        if isinstance(arm, _WireNode) and depth < _DEPTH:
            yield from (
                (f"arm[{arm.label}].{name}", inner)
                for name, inner in _mutations(arm, _object_sample(arm, depth + 1, True), depth + 1)
            )


#: 欄の型ごとの崩しの作り手(判じ手の型 → 崩し)。ここに無い型(素の型・何でも受ける型)は欄の種類の違い(_WRONG_KINDS)だけで崩す。
_FIELD_MUTATIONS: Final[dict[type, Callable[[Any, object, int], Iterator[tuple[str, object]]]]] = {
    _Enum: _spelling_mutations,
    _Literal: _spelling_mutations,
    _Array: _array_mutations,
    _FixedTuple: _fixed_tuple_mutations,
    _Dict: _dict_mutations,
    _WireNode: _nested_mutations,
    _Union: _union_mutations,
}


def _field_mutations(node: _Node, value: object, depth: int) -> Iterator[tuple[str, object]]:
    """欄の型ごとの崩し(定義の外の綴り・配列の長さの ±1・union の腕ごとの値・入れ子の 2 階層まで)。"""
    mutate = _FIELD_MUTATIONS.get(type(node))
    if mutate is not None:
        yield from mutate(node, value, depth)


#: 文字列そのものの崩し(JSON でない文字列・最上位の種類の違い・文字列の読みの差 — 設計の記録の節 3「JSON の読み」)。
_TEXT_CASES: Final[tuple[tuple[str, str | bytes], ...]] = (
    ("not-json", "{"),
    ("trailing", '{"a":1} x'),
    ("top-array", "[]"),
    ("top-null", "null"),
    ("top-string", '"x"'),
    ("top-number", "1"),
    ("empty", ""),
    ("lone-surrogate", '{"a":"\\ud800"}'),
    ("bom", "﻿{}"),
    ("not-utf8", b'{"a":"\xff"}'),
    ("deep", "[" * 3000 + "]" * 3000),
)


def inputs_for(wire_type: type) -> list[tuple[str, str | bytes]]:
    """型 1 つの照らしの入力(名・JSON の文字列 — 同じ文字列は 1 度)。"""
    codec = codec_of(wire_type)
    base = _accepted_base(codec, wire_type)
    full = _object_sample(cast(_WireNode, codec.node), 0, True)
    named = [("base", base), ("full", full), *_mutations(codec.node, base, 0)]
    by_text = {
        json.dumps(value): name for name, value in reversed(named)
    }  # 同じ文字列は先の名で 1 度
    return [(name, text) for text, name in reversed(by_text.items())] + list(_TEXT_CASES)


# --- 比べる ---------------------------------------------------------------------------------------------------------------------


def typed(value: object) -> object:
    """値を (型, 中身) の木へ — == では見分けない 1 と 1.0・tuple と list・str と StrEnum を見分けて比べる。"""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return (
            type(value),
            tuple(
                (field.name, typed(getattr(value, field.name)))
                for field in dataclasses.fields(value)
            ),
        )
    if isinstance(value, list | tuple):
        return (type(value), tuple(typed(item) for item in value))
    if isinstance(value, dict):
        return (type(value), tuple((key, typed(item)) for key, item in value.items()))
    if isinstance(value, frozenset):
        return (type(value), tuple(sorted(value, key=repr)))
    if isinstance(value, float) and math.isnan(value):  # NaN は自分と等しくない
        return (float, "nan")
    return (type(value), value)


#: msg まで比べる種類(他の種類の msg は種類が決める綴り)。JSON として読めない入力の msg は Python と pydantic で違う(節 3)。
_MSG_KINDS: Final = frozenset(
    {"missing", "enum", "value_error", "assertion_error", "literal_error", "too_long"}
)


def _problems(answer: Malformed) -> list[tuple[str, str]]:
    """断りの (場所, 種類) の列(_MSG_KINDS の種類は reason の全体)— 両方の解き手の断りを比べるため。"""
    return [
        (field.field, field.reason if kind in _MSG_KINDS else kind)
        for field in answer.fields
        for kind in (field.reason.rpartition(" [")[2].rstrip("]"),)
    ]


@dataclasses.dataclass(frozen=True)
class Mismatch:
    """不一致 1 件: 型の名・入力の名・入力・何が違うか(weaker = 自前が受けて pydantic が断った — 型の check を弱める側)。"""

    wire_type: str
    case: str
    text: str
    what: str
    ours: str
    theirs: str
    weaker: bool = False


@dataclasses.dataclass(frozen=True)
class InputVerdict:
    """入力 1 つの照らしの結末: 両方が受けたか・不一致の列。"""

    accepted: bool
    mismatches: tuple[Mismatch, ...]


@dataclasses.dataclass(frozen=True)
class Report:
    """照らしの結果: 型の数・入力の数・両方が受けた入力の数(= 比べた書き出しの数)・不一致・数えた差(節 3b の既定値・規則 4 の型)・
    組めなかった型(型の名 → 訳)。"""

    types: int = 0
    inputs: int = 0
    accepted: int = 0
    mismatches: tuple[Mismatch, ...] = ()
    schema_default_differences: tuple[str, ...] = ()
    catch_all_types: tuple[str, ...] = ()
    unbuildable: Mapping[str, str] = dataclasses.field(default_factory=dict)


def _call(function: Callable[[], object]) -> tuple[bool, object]:
    """呼びの答えか例外の文 — どちらの解き手の例外も答えとして比べるため。"""
    try:
        return True, function()
    except Exception as error:  # 照らしの道具 — どちらの解き手の例外も答えとして比べる
        return False, f"{type(error).__name__}: {error}"


def _text(value: object) -> str:
    """不一致の記録に書く綴り。"""
    return value if isinstance(value, str) else repr(value)


def compare_input(wire_type: type, case: str, text: str | bytes, *, values: bool) -> InputVerdict:
    """入力 1 つを両方の解き手に通して比べ、両方が受けた値を両方の書き手で書いて比べる(values = 受けた値まで比べる)。"""
    codec = codec_of(wire_type)
    shown = text if isinstance(text, str) else repr(text)
    shown = shown if len(shown) <= 400 else shown[:400] + "…"

    def miss(what: str, ours: object, theirs: object, weaker: bool = False) -> InputVerdict:
        found = Mismatch(
            wire_type.__qualname__,
            case,
            shown,
            what,
            _text(ours)[:600],
            _text(theirs)[:600],
            weaker,
        )
        return InputVerdict(accepted=False, mismatches=(found,))

    ok_ours, ours = _call(lambda: codec.decode_json(text))
    ok_theirs, theirs = _call(lambda: pydantic_parse_json(wire_type, text))
    match (ok_ours and ok_theirs, isinstance(ours, Malformed), isinstance(theirs, Malformed)):
        case (False, _, _):
            same = ok_ours == ok_theirs and ours == theirs
            return InputVerdict(False, ()) if same else miss("exception", ours, theirs)
        case (True, True, True):
            ours_problems = _problems(cast(Malformed, ours))
            theirs_problems = _problems(cast(Malformed, theirs))
            same = ours_problems == theirs_problems
            return (
                InputVerdict(False, ()) if same else miss("refusal", ours_problems, theirs_problems)
            )
        case (True, True, False) | (True, False, True):
            return miss("accept", ours, theirs, weaker=isinstance(theirs, Malformed))
        case _:
            if values and typed(ours) != typed(theirs):
                return miss("value", ours, theirs)
            return InputVerdict(
                True, tuple(_write_mismatches(ours, wire_type.__qualname__, case, shown))
            )


def _write_mismatches(value: object, name: str, case: str, shown: str) -> Iterator[Mismatch]:
    """受けた値 1 つを両方の書き手で書いた答えの違い(dump は値と値の型まで・dump-json は bytes まで)。"""
    codec = codec_of(type(value))
    with warnings.catch_warnings():
        warnings.simplefilter(
            "ignore"
        )  # 欄の型に合わない値の pydantic の書き出しの警告は読み捨てる
        dumped = (_call(lambda: codec.encode(value)), _call(lambda: pydantic_dump(value)))
        texts = (_call(lambda: codec.encode_json(value)), _call(lambda: pydantic_dump_json(value)))
    for what, ((ok_ours, ours), (ok_theirs, theirs)) in (("dump", dumped), ("dump-json", texts)):
        same = ok_ours == ok_theirs and (
            typed(ours) == typed(theirs) if ok_ours else ours == theirs
        )
        if not same:
            yield Mismatch(name, case, shown, what, _text(ours)[:600], _text(theirs)[:600])


def _schema_differences(ours: object, theirs: object, where: str) -> tuple[bool, tuple[str, ...]]:
    """JSON Schema を鍵の順まで比べる。答え = (default の外が等しいか, default の値だけが違う所 — 節 3b の差として数える)。"""
    match ours, theirs:
        case dict(), dict():
            if list(ours) != list(theirs):
                return False, ()
            defaults = tuple(
                f"{where}: ours={json.dumps(ours[key])} theirs={json.dumps(theirs[key])}"
                for key in ours
                if key == "default" and typed(ours[key]) != typed(theirs[key])
            )
            inner = [
                _schema_differences(ours[key], theirs[key], f"{where}/{key}")
                for key in ours
                if not (key == "default" and typed(ours[key]) != typed(theirs[key]))
            ]
            return all(same for same, _ in inner), defaults + tuple(
                line for _, lines in inner for line in lines
            )
        case list(), list():
            if len(ours) != len(theirs):
                return False, ()
            inner = [
                _schema_differences(a, b, f"{where}/{index}")
                for index, (a, b) in enumerate(zip(ours, theirs, strict=True))
            ]
            return all(same for same, _ in inner), tuple(
                line for _, lines in inner for line in lines
            )
        case _:
            return typed(ours) == typed(theirs), ()


@dataclasses.dataclass(frozen=True)
class _TypeVerdict:
    """型 1 つの照らしの結末。"""

    inputs: int
    accepted: int
    mismatches: tuple[Mismatch, ...]
    schema_default_differences: tuple[str, ...]
    catch_all: bool


def _compare_type(wire_type: type, codec: Codec[Any]) -> _TypeVerdict:
    """型 1 つの入力を両方の解き手に通し、書き出しと JSON Schema も比べる。"""
    catch_all = mixed_catch_all_unions(codec.node) > 0
    verdicts = [
        compare_input(wire_type, case, text, values=not catch_all)
        for case, text in inputs_for(wire_type)
    ]
    ok_ours, ours = _call(lambda: json.loads(json.dumps(codec.schema())))
    ok_theirs, theirs = _call(lambda: json.loads(json.dumps(pydantic_json_schema(wire_type))))
    same, defaults = (
        _schema_differences(ours, theirs, wire_type.__qualname__)
        if ok_ours and ok_theirs
        else (False, ())
    )
    schema_mismatch = (
        ()
        if same
        else (
            Mismatch(
                wire_type.__qualname__,
                "json-schema",
                "",
                "json-schema",
                _text(json.dumps(ours))[:600],
                _text(json.dumps(theirs))[:600],
            ),
        )
    )
    return _TypeVerdict(
        inputs=len(verdicts),
        accepted=sum(1 for verdict in verdicts if verdict.accepted),
        mismatches=tuple(found for verdict in verdicts for found in verdict.mismatches)
        + schema_mismatch,
        schema_default_differences=defaults,
        catch_all=catch_all,
    )


def _codec_or_refusal(wire_type: type) -> Codec[Any] | str:
    """型の木を組む(語彙の外の型は TypeError の文)— 組めない型を数えるため。"""
    try:
        return codec_of(wire_type)
    except TypeError as error:
        return str(error)


def compare_types(wire_types: Iterable[type]) -> Report:
    """型ごとに入力を作って両方の解き手に通し、書き出しと JSON Schema も比べる。"""
    built = [(wire_type, _codec_or_refusal(wire_type)) for wire_type in wire_types]
    unbuildable = {
        f"{wire_type.__module__}.{wire_type.__qualname__}": codec
        for wire_type, codec in built
        if isinstance(codec, str)
    }
    verdicts = [
        (wire_type, _compare_type(wire_type, codec))
        for wire_type, codec in built
        if not isinstance(codec, str)
    ]
    return Report(
        types=len(verdicts),
        inputs=sum(verdict.inputs for _, verdict in verdicts),
        accepted=sum(verdict.accepted for _, verdict in verdicts),
        mismatches=tuple(found for _, verdict in verdicts for found in verdict.mismatches),
        schema_default_differences=tuple(
            line for _, verdict in verdicts for line in verdict.schema_default_differences
        ),
        catch_all_types=tuple(
            f"{wire_type.__module__}.{wire_type.__qualname__}"
            for wire_type, verdict in verdicts
            if verdict.catch_all
        ),
        unbuildable=unbuildable,
    )


def summary(report: Report) -> str:
    """結果の要約(test の失敗の文と測りの記録に使う)。"""
    head = (
        f"types={report.types} inputs={report.inputs} accepted={report.accepted} "
        f"mismatches={len(report.mismatches)} weaker={sum(1 for item in report.mismatches if item.weaker)} "
        f"schema-default-differences={len(report.schema_default_differences)} catch-all-types={len(report.catch_all_types)} "
        f"unbuildable={len(report.unbuildable)}"
    )
    mismatches = [
        f"- {item.wire_type} [{item.case}] {item.what}{' (weaker)' if item.weaker else ''}: input={item.text}\n"
        f"    ours={item.ours}\n    theirs={item.theirs}"
        for item in report.mismatches[:40]
    ]
    unbuildable = [
        f"- unbuildable {name}: {error}" for name, error in list(report.unbuildable.items())[:20]
    ]
    return "\n".join([head, *mismatches, *unbuildable])


def main(argv: list[str]) -> int:
    """package の名を引数に、全 defwire の型を照らして要約を出す(test の外で全体を眺める時)。"""
    types, failures = wire_types_in(argv)
    report = compare_types(types)
    print(summary(report))
    for name, error in failures.items():
        print(f"- import failed {name}: {error}")
    for line in report.schema_default_differences[:20]:
        print(f"- schema default difference {line}")
    return 1 if report.mismatches else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
