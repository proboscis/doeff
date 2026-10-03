"""sql_effects.hy の公開面の型(型検査のための宣言 — 実行時は sql_effects.hy を読む・agora-redesign #2320)。

sql_effects.hy は Hy の module なので、型の宣言が無いと pyright は中を読めず、`from doeff_core_effects.sql_effects import SqlQuery`
の名が全部 Unknown になる(消費者の strict の型検査で、書き手に直せない赤が連なる)。ここで型を宣言する。

- defrecord は frozen で keyword だけの dataclass、defenum は StrEnum(値は名の小文字)。
- defeffect は位置でも渡せる frozen の dataclass で、`EffectBase[答えの型]` の下位の型。`(<- x (SqlQuery …))` の x は答えの型
  (SqlRows | SqlFailed | SqlUnreachable)を受ける。SqlTransaction の答えは中の program の答えの型 T と失敗の値の和。
- defk は呼ぶと Program を返す(答えの型は Program の 1 つ目の引数)。
- 実装との食い違いは packages/doeff-core-effects/tests/test_hy_module_stubs.py が名・欄の名と順・既定値の有無・引数の名で検める。
"""

import datetime
import re
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any, Generic, Never, TypeAlias, TypeVar
from uuid import UUID

from doeff_vm import EffectBase
from hy.models import Keyword

from doeff import Program
from doeff_core_effects.scheduler import ExternalPromise

MODULE_TAGS: dict[Keyword, str]

T = TypeVar("T")

SQL_VALUE_TYPES: tuple[type, ...]
#: 値の型そのもの(subclass を含まない)が閉じた集合の型である印。
PLAIN_VALUE_TYPES: frozenset[type]

#: 引数と行の値の閉じた集合。
SqlValue: TypeAlias = int | float | str | bytes | bool | None
#: driver が答える値のうち、閉じた集合 SqlValue へ写す決まりの在る型(normalized_value が受ける型 — 外の型は normalized_rows が
#: TypeError で断る)。
DriverValue: TypeAlias = (
    int | float | str | bytes | bool | None | memoryview | bytearray | Decimal | datetime.date | datetime.time | UUID
)

TOKEN: re.Pattern[str]
IDENTIFIER: re.Pattern[str]

# --- 値 ---

@dataclass(frozen=True, kw_only=True)
class SqlParam:
    name: str
    value: SqlValue

@dataclass(frozen=True, kw_only=True)
class SqlRows:
    rows: tuple[tuple[SqlValue, ...], ...]
    rowcount: int | None

@dataclass(frozen=True, kw_only=True)
class SqlFailed:
    sqlstate: str | None
    reason: str

@dataclass(frozen=True, kw_only=True)
class SqlUnreachable:
    reason: str

@dataclass(frozen=True, kw_only=True)
class SqlSchemaApplied:
    statements: tuple[str, ...]

class SqlTransactionMisuse(TypeError): ...  # noqa: N818 - public exception name is intentionally stable

# --- DDL の宣言 ---

class SqlColumnType(StrEnum):
    INTEGER = "integer"
    FLOAT = "float"
    TEXT = "text"
    BYTES = "bytes"
    BOOLEAN = "boolean"
    JSON = "json"

@dataclass(frozen=True, kw_only=True)
class SqlColumn:
    name: str
    type: SqlColumnType
    nullable: bool = False

@dataclass(frozen=True, kw_only=True)
class SqlIndex:
    name: str
    columns: tuple[str, ...]
    unique: bool = False

@dataclass(frozen=True, kw_only=True)
class SqlTable:
    name: str
    columns: tuple[SqlColumn, ...]
    primary_key: tuple[str, ...] = ()
    indexes: tuple[SqlIndex, ...] = ()

# --- effect ---

@dataclass(frozen=True)
class SqlQuery(EffectBase[SqlRows | SqlFailed | SqlUnreachable]):
    database: str
    statement: str
    params: tuple[SqlParam, ...] = ()

@dataclass(frozen=True)
class SqlInsertRows(EffectBase[SqlRows | SqlFailed | SqlUnreachable]):
    database: str
    table: str
    columns: tuple[str, ...]
    rows: tuple[tuple[SqlValue, ...], ...]

@dataclass(frozen=True)
class SqlTransaction(EffectBase[T | SqlFailed | SqlUnreachable], Generic[T]):
    database: str
    program: Program[T, Any]
    lock_key: str | None = None

@dataclass(frozen=True)
class SqlEnsureTables(EffectBase[SqlSchemaApplied | SqlFailed | SqlUnreachable]):
    database: str
    tables: tuple[SqlTable, ...]

@dataclass(frozen=True)
class SqlNotify(EffectBase[None | SqlFailed | SqlUnreachable]):
    database: str
    channel: str

@dataclass(frozen=True)
class SqlHangNotice(EffectBase[ExternalPromise[bool] | SqlUnreachable]):
    database: str
    channel: str

@dataclass(frozen=True)
class SqlDropNotice(EffectBase[None]):
    database: str
    channel: str
    bell: ExternalPromise[bool]

@dataclass(frozen=True)
class SetSqlOutage(EffectBase[None]):
    database: str
    down: bool

# --- 中立の記法 ---

@dataclass(frozen=True, kw_only=True)
class SqlText:
    text: str

@dataclass(frozen=True, kw_only=True)
class SqlPlaceholder:
    name: str

#: 文の割りの覚え(文 → 割った並び)と、覚える文の数の上限。
SPLIT_MEMO_LIMIT: int
SPLIT_MEMO: dict[str, tuple[SqlText | SqlPlaceholder, ...]]

def split_statement(statement: str) -> Program[tuple[SqlText | SqlPlaceholder, ...], Any]: ...
def checked_params(
    parts: tuple[SqlText | SqlPlaceholder, ...], params: tuple[SqlParam, ...]
) -> Program[None, Any]: ...
def param_value(params: tuple[SqlParam, ...], name: str) -> Program[SqlValue, Any]: ...
def checked_identifier(name: str) -> Program[str, Any]: ...
def checked_identifiers(names: tuple[str, ...]) -> Program[tuple[str, ...], Any]: ...
def checked_rows(
    columns: tuple[str, ...], rows: tuple[tuple[SqlValue, ...], ...]
) -> Program[None, Any]: ...

# --- 行の値の正規化(driver の値は driver ごとに型が違う — 境界で検める)---

#: 写しは effect を出さない(答えの Program は効果を持たない — Never)。
def normalized_value(value: DriverValue) -> Program[SqlValue, Never]: ...
def normalized_rows(
    rows: list[tuple[object, ...]] | tuple[tuple[object, ...], ...],
) -> Program[tuple[tuple[SqlValue, ...], ...], Any]: ...
