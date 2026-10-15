"""JSON の値の型の唯一の定義(agora-redesign #840)。

``JsonValue`` は JSON を解いたままの値(``dict`` / ``list`` / 文字列 / 数 / 真偽 / ``None``)の型で、名前を変えた素の
dict にすぎない。構造を持つ値は、型を宣言した値(doeff-hy の ``defwire`` / ``defrecord``)へ解いて確かめてから運ぶ
(operator 2026-09-28 逐語 "and i dont think we should make anyone use that directry instead of actually parsing and
validating it like pydantic does")。この型に触ってよいのは、汎用の解き手(``doeff_hy.wire`` の parse / dump)と、送受信
そのものを行う foundation の module だけ — それ以外で使うと doeff-linter の DOEFF120 が誤りにする。

置き場: doeff-hy(解き手の置き場)の Python の module。doeff-hy は doeff-records を import できない(依存の向きが逆)
ので、以前の正本 ``doeff_records.wire`` はここを import する。Python の module にしたのは、型検査器(pyright)が Hy の
module を読めないため — Python の読み手(agora-controllers の model の ``.py``)もこの 1 か所から同じ型を得る。

形を呼び手が決める任意の JSON(tool の引数と結果・耐久の走行の memo の値)を中を読まずに運ぶ時は、JsonValue ではなく
``OpaqueJson``(下)を使う — 中を分解する口を持たない名のある型で、DOEFF120 は数えない。

2 つの見え方(同じ名前・同じ定義点):
- 型検査の時(``TYPE_CHECKING``)は再帰の型の別名 — ``isinstance(v, dict)`` で ``dict[str, JsonValue]`` に絞れる。
- 実行の時は ``isinstance`` に渡せる union — defk の契約 ``(: x JsonValue)`` は実行の時に ``isinstance`` で確かめるので、
  文字列の型の別名(前方参照)は渡せない。``JsonObject`` は実行の時は ``dict``。
"""

import json
import re
import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeAlias, final

if TYPE_CHECKING:
    from pydantic import GetCoreSchemaHandler
    from pydantic_core import CoreSchema
    from pydantic_core.core_schema import SerializationInfo

    JsonValue: TypeAlias = "dict[str, JsonValue] | list[JsonValue] | str | int | float | bool | None"
    JsonObject: TypeAlias = "dict[str, JsonValue]"
else:
    JsonValue = dict | list | str | int | float | bool | None
    JsonObject = dict


@final
@dataclass(frozen=True)
class OpaqueJson:
    """形を呼び手が決める任意の JSON の値を、中を読まずに運ぶための名のある型(agora-redesign #840)。

    使う所: 運ぶ側が形を知らず、知ってはいけない値 — tool の呼び出しの引数と結果・耐久の走行の memo の値や spec のように、
    書き手ごとに形が違う JSON。形の決まった値は ``defwire`` の型にする(この型へ逃がさない)。

    ``JsonValue`` と違い、この型は中を分解する口を持たない。持つのは JSON の文字列 ``text``(最小の直列化 — 区切りの空白なし・
    UTF-8 のまま・object の欄の順は保つ)だけで、等しさはその文字列の等しさ。中を読むのは次の 2 つだけ:

    - 形を知る読み手が ``doeff_hy.wire`` の ``parse`` で ``defwire`` の型へ解く(``(parse T opaque)``)。
    - 送受信そのものを行う foundation の module(doeff-linter DOEFF120 の ``:wire-modules``)が ``json.loads(opaque.text)`` で読む。

    作るのは ``OpaqueJson.of(value)``(JSON の値から)か ``OpaqueJson.from_text(text)``(JSON の文字列から — 読めなければ ValueError)。
    ``defwire`` の型の欄に書けば、解き手は任意の JSON をこの型へ包み、書く時は元の JSON の値へ戻す(``doeff_hy.wire`` の ``dump`` は
    JSON の値として。``dump-json`` は持っている文字列を解き直さずそのまま出力へ埋める — ``OpaqueSplice``)。
    """

    text: str

    def __post_init__(self) -> None:
        json.loads(self.text)

    @classmethod
    def of(cls, value: "JsonValue") -> "OpaqueJson":
        """JSON の値(dict / list / 文字列 / 数 / 真偽 / None・凍らせた JSON の FrozenMap / tuple も)を包む。

        文字列は ``json.dumps`` が今書いた物なので ``json.loads`` で必ず読める(書けない値は ``json.dumps`` が
        TypeError / ValueError で断る — 受ける値・断る値・綴りは ``cls(text)`` を通した時と同じ)。その文字列を
        ``__post_init__`` でもう一度解くのは同じ答えを 2 度出すだけなので、ここでは解かずに包む(agora-redesign #2449 —
        預かり所の契約の例 124 で 4.2k 回・``of`` の費用の 4 割が、書いた直後の文字列を解き直す ``json.loads`` だった)。
        外から渡る文字列(``cls(text)`` を直に呼ぶ所)は今までどおり ``__post_init__`` が解いて確かめる。
        """
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=_thawed)
        opaque = object.__new__(cls)
        object.__setattr__(opaque, "text", text)
        return opaque

    @classmethod
    def from_text(cls, text: str | bytes) -> "OpaqueJson":
        """JSON の文字列を包む(最小の直列化へ整える)。JSON として読めなければ ValueError。"""
        return cls.of(json.loads(text))

    @property
    def encoded_size(self) -> int:
        """最小の直列化の UTF-8 の byte の数(大きさの上限の検め — 中を読まずに測る)。"""
        return len(self.text.encode("utf-8"))

    @classmethod
    def __get_pydantic_core_schema__(cls, source: Any, handler: "GetCoreSchemaHandler") -> "CoreSchema":
        from pydantic_core import core_schema

        return core_schema.no_info_plain_validator_function(
            _opaque_of,
            json_schema_input_schema=core_schema.any_schema(),
            serialization=core_schema.plain_serializer_function_ser_schema(_opaque_value, info_arg=True),
        )


def _thawed(value: object) -> object:
    # 凍らせた JSON(doeff_hy.frozen の FrozenMap)を json.dumps が書ける形へ。tuple は json.dumps がそのまま配列にする。
    items = getattr(value, "items", None)
    if callable(items):
        return dict(items())
    raise TypeError(f"JSON の値でない: {type(value).__name__}")


def _opaque_of(value: object) -> OpaqueJson:
    return value if isinstance(value, OpaqueJson) else OpaqueJson.of(value)  # type: ignore[arg-type]  # 解き手が JSON から読んだ値だけが来る


def _opaque_value(value: OpaqueJson, info: "SerializationInfo") -> "JsonValue | str":
    # OpaqueJson の書き手。``dump_json`` が控え(OpaqueSplice)を context に渡した時は、持っている文字列の代わりに印を返し、
    # 出力ができた後で文字列を埋める(解いて書き直す二度手間をしない)。控えが無い書き出し(``dump`` など)は値を返す。
    # OpaqueJson でない値が来ると ``value.text`` の AttributeError で落ちる — 和の型の腕として試された時の「この腕ではない」もこれ
    # (OpaqueJson を含む和は、OpaqueJson の腕を最後に置くと、他の値でこの関数を呼ばずに済む)。
    splice = info.context
    if isinstance(splice, OpaqueSplice):
        marker = splice.admit(value.text)
        if marker is not None:
            return marker
    return json.loads(value.text)


# 埋め込みの印の頭。プロセスごとに変わる乱数で、送る値の中の文字列が偶然(または故意に)印と同じになって取り違えられるのを防ぐ。
# 印は書き出しの間だけ在り、出力に残らない(OpaqueSplice.fill が全部を元の文字列に替える)。
_SPLICE_PREFIX = secrets.token_hex(8)
_SPLICE_MARK = re.compile(b'"' + _SPLICE_PREFIX.encode("ascii") + rb"([0-9]+)\"")
# JSON の文字列ではない綴り(Python の json.dumps が NaN / Infinity をそう書く)。そのまま埋めると読み手の JSON.parse が壊れるので、
# これを含む文字列は埋めずに、これまでどおり解いて pydantic に書かせる(NaN は null になる)。
_NOT_STRICT_JSON = ("NaN", "Infinity")


@final
class OpaqueSplice:
    """``dump_json`` 1 回の間だけ生きる、OpaqueJson の文字列を出力へそのまま埋める控え(二度手間 — 文字列 → ``json.loads`` →
    もう一度書く — を無くす)。

    pydantic_core には「書き済みの JSON の文字列をそのまま出力へ入れる」口が無い。OpaqueJson の書き手は値を返すことしかできず、
    文字列を返せば引用符で包まれる。そこで書き手は印の文字列を返し(``admit``)、出力の bytes ができた後で印の所を元の文字列に
    替える(``fill``)。使い方は ``dump_json(..., context=splice)`` の 1 通りで、``doeff_hy.wire.dump_json`` が呼ぶ。
    ``context`` が無い書き方(``dump`` の JSON の値への書き出し・素の ``TypeAdapter`` の直の呼び)は、これまでどおり
    ``json.loads`` で解いた値を返す。

    印は呼びごとの番号つきで、和の型が腕を試して捨てた分(同じ値が 2 度出る)があっても、出力に残った印は番号で元の文字列に戻る。
    """

    __slots__ = ("_by_number",)

    def __init__(self) -> None:
        # 書き手(pydantic が呼ぶ関数)が呼び手へ渡せるのは context だけなので、印の番号 → 元の文字列(UTF-8)の表をここに持つ。
        self._by_number: dict[int, bytes] = {}

    def admit(self, text: str) -> str | None:
        """文字列を埋める控えに入れて印を返す。そのまま埋めると JSON でなくなる綴りを含む時は None(呼び手は解いて返す)。"""
        if any(spelling in text for spelling in _NOT_STRICT_JSON):
            return None
        number = len(self._by_number)
        self._by_number[number] = text.encode("utf-8")
        return f"{_SPLICE_PREFIX}{number}"

    def fill(self, written: bytes) -> str:
        """pydantic が書いた JSON の bytes の印を元の文字列に替え、UTF-8 として読んだ文字列を返す。"""
        by_number = self._by_number
        if not by_number:
            return written.decode("utf-8")
        return _SPLICE_MARK.sub(lambda mark: by_number[int(mark[1])], written).decode("utf-8")
