# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = sql_effects.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from typing import TypeAlias
from doeff import Program as _Program
from typing import Never as _Never
from doeff import EffectBase as _doeff_effect_base
from dataclasses import dataclass as _doeff_dataclass
import re as re
import datetime as datetime
from decimal import Decimal as Decimal
from uuid import UUID as UUID
from dataclasses import dataclass as dataclass
from enum import StrEnum as StrEnum
from collections.abc import Sequence as Sequence
from typing import TypeGuard as TypeGuard
from typing import TypeVar as TypeVar
from doeff import Program as Program
from doeff_core_effects._scheduler import ExternalPromise as ExternalPromise
T = TypeVar('T')
SQL_VALUE_TYPES: tuple[type, ...]
PLAIN_VALUE_TYPES: frozenset[type]
SqlValue: TypeAlias = int | float | str | bytes | bool | None
DriverValue: TypeAlias = int | float | str | bytes | bool | None | memoryview | bytearray | Decimal | datetime.date | datetime.time | UUID
TOKEN: re.Pattern[str]
IDENTIFIER: re.Pattern[str]

@dataclass(frozen=True, kw_only=True)
class SqlLock:
    key: str
    shared: bool = False

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

class SqlTransactionMisuse(TypeError):
    ...

class SqlColumnType(StrEnum):
    INTEGER = 'integer'
    FLOAT = 'float'
    TEXT = 'text'
    BYTES = 'bytes'
    BOOLEAN = 'boolean'
    JSON = 'json'

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
    primary_key: tuple[str, ...] = ...
    indexes: tuple[SqlIndex, ...] = ...

@_doeff_dataclass(frozen=True)
class SqlQuery(_doeff_effect_base[SqlRows | SqlFailed | SqlUnreachable]):
    database: str
    statement: str
    params: tuple[SqlParam, ...] = ...
    binary: bool = False

    def __post_init__(self) -> None:
        ...

@_doeff_dataclass(frozen=True)
class SqlInsertRows(_doeff_effect_base[SqlRows | SqlFailed | SqlUnreachable]):
    database: str
    table: str
    columns: tuple[str, ...]
    rows: tuple[tuple[SqlValue, ...], ...]

    def __post_init__(self) -> None:
        ...

@_doeff_dataclass(frozen=True)
class SqlTransaction[T](_doeff_effect_base[T | SqlFailed | SqlUnreachable]):
    database: str
    program: Program[T, object]
    lock_key: str | None = None
    batched: bool = False
    locks: tuple[SqlLock, ...] = ...

    def __post_init__(self) -> None:
        ...

@_doeff_dataclass(frozen=True)
class SqlBatch(_doeff_effect_base[tuple[SqlRows, ...]]):
    database: str
    queries: tuple[SqlQuery, ...]
    commit: bool = False

    def __post_init__(self) -> None:
        ...

@_doeff_dataclass(frozen=True)
class SqlEnsureTables(_doeff_effect_base[SqlSchemaApplied | SqlFailed | SqlUnreachable]):
    database: str
    tables: tuple[SqlTable, ...]

    def __post_init__(self) -> None:
        ...

@_doeff_dataclass(frozen=True)
class SqlNotify(_doeff_effect_base[None | SqlFailed | SqlUnreachable]):
    database: str
    channel: str
    topics: tuple[str, ...] | None

    def __post_init__(self) -> None:
        ...

@_doeff_dataclass(frozen=True)
class SqlHangNotice(_doeff_effect_base[ExternalPromise[bool] | SqlUnreachable]):
    database: str
    channel: str
    topics: tuple[str, ...] | None

    def __post_init__(self) -> None:
        ...

@_doeff_dataclass(frozen=True)
class SqlDropNotice(_doeff_effect_base[None]):
    database: str
    channel: str
    bell: ExternalPromise[bool]

    def __post_init__(self) -> None:
        ...

@_doeff_dataclass(frozen=True)
class SetSqlOutage(_doeff_effect_base[None]):
    database: str
    down: bool

@dataclass(frozen=True, kw_only=True)
class SqlText:
    text: str

@dataclass(frozen=True, kw_only=True)
class SqlPlaceholder:
    name: str
SPLIT_MEMO_LIMIT: int
SPLIT_MEMO: dict[str, tuple[SqlText | SqlPlaceholder, ...]]

def split_statement(statement: str) -> _Program[tuple[SqlText | SqlPlaceholder, ...], object]:
    ...

def checked_params(parts: tuple[SqlText | SqlPlaceholder, ...], params: tuple[SqlParam, ...]) -> _Program[None, object]:
    ...

def param_value(params: tuple[SqlParam, ...], name: str) -> _Program[SqlValue, object]:
    ...

def checked_identifier(name: str) -> _Program[str, object]:
    ...

def checked_identifiers(names: tuple[str, ...]) -> _Program[tuple[str, ...], object]:
    ...

def checked_rows(columns: tuple[str, ...], rows: tuple[tuple[SqlValue, ...], ...]) -> _Program[None, object]:
    ...

def normalized_value(value: DriverValue) -> _Program[SqlValue, _Never]:
    ...

def is_driver_value(value: object) -> TypeGuard[DriverValue]:
    ...

def is_plain_rows(rows: list[tuple[object, ...]] | tuple[tuple[object, ...], ...]) -> TypeGuard[Sequence[Sequence[SqlValue]]]:
    ...

def normalized_rows(rows: list[tuple[object, ...]] | tuple[tuple[object, ...], ...]) -> _Program[tuple[tuple[SqlValue, ...], ...], object]:
    ...
