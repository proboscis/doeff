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
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeAlias, final

if TYPE_CHECKING:
    from pydantic import GetCoreSchemaHandler
    from pydantic_core import CoreSchema

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
    ``defwire`` の型の欄に書けば、解き手は任意の JSON をこの型へ包み、書く時は元の JSON の値へ戻す。
    """

    text: str

    def __post_init__(self) -> None:
        json.loads(self.text)

    @classmethod
    def of(cls, value: "JsonValue") -> "OpaqueJson":
        """JSON の値(dict / list / 文字列 / 数 / 真偽 / None・凍らせた JSON の FrozenMap / tuple も)を包む。"""
        return cls(json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=_thawed))

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
            serialization=core_schema.plain_serializer_function_ser_schema(_opaque_value),
        )


def _thawed(value: object) -> object:
    # 凍らせた JSON(doeff_hy.frozen の FrozenMap)を json.dumps が書ける形へ。tuple は json.dumps がそのまま配列にする。
    items = getattr(value, "items", None)
    if callable(items):
        return dict(items())
    raise TypeError(f"JSON の値でない: {type(value).__name__}")


def _opaque_of(value: object) -> OpaqueJson:
    return value if isinstance(value, OpaqueJson) else OpaqueJson.of(value)  # type: ignore[arg-type]  # 解き手が JSON から読んだ値だけが来る


def _opaque_value(value: OpaqueJson) -> "JsonValue":
    return json.loads(value.text)
