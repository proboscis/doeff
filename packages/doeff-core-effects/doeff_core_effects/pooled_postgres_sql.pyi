# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = pooled_postgres_sql.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from _typeshed import Incomplete
from doeff import Program as _Program
from doeff_hy.static_types import Handler as _Handler
from concurrent.futures import Executor as Executor
from doeff import Program as Program
from doeff_core_effects._scheduler import CreateSemaphore as CreateSemaphore
from doeff_core_effects._scheduler import AcquireSemaphore as AcquireSemaphore
from doeff_core_effects._scheduler import ReleaseSemaphore as ReleaseSemaphore
from doeff_core_effects.sql_effects import SqlQuery as SqlQuery
from doeff_core_effects.sql_effects import SqlInsertRows as SqlInsertRows
from doeff_core_effects.sql_effects import SqlBatch as SqlBatch
from doeff_core_effects.sql_effects import SqlTransaction as SqlTransaction
from doeff_core_effects.sql_effects import SqlEnsureTables as SqlEnsureTables
from doeff_core_effects.sql_effects import SqlNotify as SqlNotify
from doeff_core_effects.sql_effects import SqlHangNotice as SqlHangNotice
from doeff_core_effects.sql_effects import SqlDropNotice as SqlDropNotice
from doeff_core_effects.sql_transaction import stray_batch as stray_batch
from doeff_core_effects.postgres_sql import PostgresConnections as PostgresConnections
from doeff_core_effects.postgres_sql import postgres_query as postgres_query
from doeff_core_effects.postgres_sql import postgres_insert as postgres_insert
from doeff_core_effects.postgres_sql import postgres_ensure_tables as postgres_ensure_tables
from doeff_core_effects.postgres_sql import offloaded_statement as offloaded_statement
from doeff_core_effects.postgres_sql import offloaded_transaction as offloaded_transaction
from doeff_core_effects.postgres_sql import transaction_locks as transaction_locks
from doeff_core_effects.postgres_sql import notified as notified
from doeff_core_effects.postgres_sql import hung_notice as hung_notice

def permit_for(permits: dict, database: str, size: int) -> _Program[Incomplete, object]:
    ...

def permitted(permit: Incomplete, program: Program) -> _Program[Incomplete, object]:
    ...

def pooled_postgres_sql_handler(connections: PostgresConnections, pool: Executor, serialized_lock_keys: tuple=...) -> _Handler:
    ...
