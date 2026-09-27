"""JSON の値の型の唯一の定義(agora-redesign #840)。

``JsonValue`` は JSON を解いたままの値(``dict`` / ``list`` / 文字列 / 数 / 真偽 / ``None``)の型で、名前を変えた素の
dict にすぎない。構造を持つ値は、型を宣言した値(doeff-hy の ``defwire`` / ``defrecord``)へ解いて確かめてから運ぶ
(operator 2026-09-28 逐語 "and i dont think we should make anyone use that directry instead of actually parsing and
validating it like pydantic does")。この型に触ってよいのは、汎用の解き手(``doeff_hy.wire`` の parse / dump)と、送受信
そのものを行う foundation の module だけ — それ以外で使うと doeff-linter の DOEFF120 が誤りにする。

置き場: doeff-hy(解き手の置き場)の Python の module。doeff-hy は doeff-records を import できない(依存の向きが逆)
ので、以前の正本 ``doeff_records.wire`` はここを import する。Python の module にしたのは、型検査器(pyright)が Hy の
module を読めないため — Python の読み手(agora-controllers の model の ``.py``)もこの 1 か所から同じ型を得る。

2 つの見え方(同じ名前・同じ定義点):
- 型検査の時(``TYPE_CHECKING``)は再帰の型の別名 — ``isinstance(v, dict)`` で ``dict[str, JsonValue]`` に絞れる。
- 実行の時は ``isinstance`` に渡せる union — defk の契約 ``(: x JsonValue)`` は実行の時に ``isinstance`` で確かめるので、
  文字列の型の別名(前方参照)は渡せない。``JsonObject`` は実行の時は ``dict``。
"""

from typing import TYPE_CHECKING, TypeAlias

if TYPE_CHECKING:
    JsonValue: TypeAlias = "dict[str, JsonValue] | list[JsonValue] | str | int | float | bool | None"
    JsonObject: TypeAlias = "dict[str, JsonValue]"
else:
    JsonValue = dict | list | str | int | float | bool | None
    JsonObject = dict
