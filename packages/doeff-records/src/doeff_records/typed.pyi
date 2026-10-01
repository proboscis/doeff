"""typed.hy の公開面の型(型検査のための宣言 — 実行時は typed.hy を読む・agora-redesign #2369・#2245)。

typed.hy は Hy の module なので、pyright は中を読めず、`from doeff_records.typed import RowType` の名・`typed-change` の答え・
`list-typed` の頁の行が全部 Unknown になる(使う側が行の型の値 `.value` を読む所に、書き手に直せない reportUnknown* が出る)。
ここで型を宣言する。

- 行の型の値の型 M を総称の引数にする — RowType[M] を渡した読み書きの答え(TypedRow[M]・TypedPage[M]・TypedRowChanged[M] ほか)の
  value が M と読める。
- 値の型は全部 frozen の dataclass(実装と同じ欄の名・順・既定値)。欄の注記は実装の注記に、実装が素の `tuple` と書く所だけ
  中身の型を足す(鍵は文字列の tuple・頁の行は TypedRow[M] の tuple)。RowType.adapter は作る時に組む欄(init=False)。
- defn(普通の関数)は実装の注記どおり。defk は呼ぶと Program を返す(答えの型 = 実装の :post の型・行の型は M)。
"""

from dataclasses import dataclass, field
from typing import Generic, TypeVar

from pydantic import TypeAdapter

from doeff import Program
from doeff_hy.frozen import FrozenMap
from doeff_records.values import (
    Expectation,
    ListCursor,
    Missing,
    NotIndexed,
    Refused,
    Reset,
    Row,
    RowChanged,
    RowRemoved,
    Unreachable,
)

M = TypeVar("M")

@dataclass(frozen=True)
class RowType(Generic[M]):
    table: str
    model: type[M]
    adapter: TypeAdapter[M] = field(init=False, repr=False, compare=False)

@dataclass(frozen=True)
class TypedRow(Generic[M]):
    key: tuple[str, ...]
    value: M
    version: int

@dataclass(frozen=True)
class TypedPage(Generic[M]):
    rows: tuple[TypedRow[M], ...]
    next_cursor: ListCursor | None
    epoch: int
    sequence: int

@dataclass(frozen=True)
class TypedWritten(Generic[M]):
    version: int
    value: M

@dataclass(frozen=True)
class TypedConflict(Generic[M]):
    current: TypedRow[M] | Missing

@dataclass(frozen=True)
class TypedRowChanged(Generic[M]):
    table: str
    key: tuple[str, ...]
    version: int
    value: M
    sequence: int
    at: int

# --- 写し(純関数)---

def model_field_names(model: type) -> tuple[str, ...]: ...
def value_of_fields(row_type: RowType[M], fields: FrozenMap) -> M: ...
def fields_of_value(row_type: RowType[M], value: M) -> FrozenMap: ...
def typed_row(row_type: RowType[M], row: Row) -> TypedRow[M]: ...
def typed_current(row_type: RowType[M], current: Row | Missing) -> TypedRow[M] | Missing: ...
def typed_change(row_type: RowType[M], change: RowChanged | RowRemoved) -> TypedRowChanged[M] | RowRemoved: ...

# --- 読み書き(汎用の effect の上の Program)---

def read_typed(row_type: RowType[M], key: tuple[str, ...]) -> Program[TypedRow[M] | Missing | Unreachable, object]: ...
def list_typed(
    row_type: RowType[M],
    *,
    where: FrozenMap | None = None,
    cursor: ListCursor | None = None,
    limit: int = ...,
) -> Program[TypedPage[M] | Reset | Unreachable | NotIndexed, object]: ...
def put_typed(
    row_type: RowType[M], key: tuple[str, ...], value: M, expect: Expectation
) -> Program[TypedWritten[M] | TypedConflict[M] | Refused | Unreachable, object]: ...
