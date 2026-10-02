# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = sqlite_file_sql.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from doeff_hy.static_types import Handler as _Handler
from pathlib import Path as Path
from dataclasses import dataclass as dataclass
from doeff_core_effects.sql_effects import SqlQuery as SqlQuery
from doeff_core_effects.sql_effects import SqlInsertRows as SqlInsertRows
from doeff_core_effects.sql_effects import SqlTransaction as SqlTransaction
from doeff_core_effects.sql_effects import SqlEnsureTables as SqlEnsureTables
from doeff_core_effects.sql_effects import SetSqlOutage as SetSqlOutage
from doeff_core_effects.sqlite_sql import SqliteConnection as SqliteConnection
from doeff_core_effects.sqlite_sql import sqlite_connection as sqlite_connection
from doeff_core_effects.sqlite_sql import connection_of as connection_of
from doeff_core_effects.sqlite_sql import outage_marked as outage_marked
from doeff_core_effects.sqlite_sql import sqlite_answer_query as sqlite_answer_query
from doeff_core_effects.sqlite_sql import sqlite_answer_insert as sqlite_answer_insert
from doeff_core_effects.sqlite_sql import sqlite_answer_tables as sqlite_answer_tables
from doeff_core_effects.sqlite_sql import sqlite_answer_transaction as sqlite_answer_transaction
from doeff import Pass as Pass
from doeff_vm import WithHandler as WithHandler
from doeff import Some as Some
from doeff_core_effects.effects import Put as Put
JOURNAL_MODE: str

@dataclass(frozen=True, kw_only=True)
class SqliteFile:
    name: str
    path: str

@dataclass(frozen=True, kw_only=True)
class SqliteFiles:
    connections: tuple[SqliteConnection, ...]

def sqlite_file_connection(file: SqliteFile) -> _Program[SqliteConnection, object]:
    ...

def open_sqlite_files(files: tuple[SqliteFile, ...]) -> _Program[SqliteFiles, object]:
    ...

def close_sqlite_files(files: SqliteFiles) -> _Program[None, object]:
    ...

def sqlite_file_sql_handler(files: SqliteFiles) -> _Handler:
    ...
