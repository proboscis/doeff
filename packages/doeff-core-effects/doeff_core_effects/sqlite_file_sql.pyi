"""sqlite_file_sql.hy の公開面の型(既存の DB の file を開く SQL の答え手 — 型検査のための宣言・実行時は sqlite_file_sql.hy を読む・
agora-redesign #2237 の前提)。

- sqlite-file-sql-handler(Python の名 sqlite_file_sql_handler)は open-sqlite-files で開いた SqliteFiles を受け、本文の Program に被せる関数を
  返す(defhandler の展開と同じ形: 本文の答えの型をそのまま運ぶ WithHandler)。
- defk は呼ぶと Program を返す(答えの型は Program の 1 つ目の引数)。
- 実装との食い違いは packages/doeff-core-effects/tests/test_hy_module_stubs.py が検める。
"""

from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

from doeff_vm import WithHandler

from doeff import Program
from doeff_core_effects.sqlite_sql import SqliteConnection

_A = TypeVar("_A")

JOURNAL_MODE: str

@dataclass(frozen=True, kw_only=True)
class SqliteFile:
    name: str
    path: str

@dataclass(frozen=True, kw_only=True)
class SqliteFiles:
    connections: tuple[SqliteConnection, ...]

def sqlite_file_connection(file: SqliteFile) -> Program[SqliteConnection, Any]: ...
def open_sqlite_files(files: tuple[SqliteFile, ...]) -> Program[SqliteFiles, Any]: ...
def close_sqlite_files(files: SqliteFiles) -> Program[None, Any]: ...

class _SqliteFileSqlHandler(Protocol):
    """本文の Program に handler を被せる関数(答えの型は本文のまま)。"""

    def __call__(self, body: Program[_A, object], /) -> WithHandler[_A]: ...

def sqlite_file_sql_handler(files: SqliteFiles) -> _SqliteFileSqlHandler: ...
