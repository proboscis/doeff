# doeff_hy._static_stub が作った型の宣言 — 手で直さない(元 = _sql_transaction.hy・作り直し = python -m doeff_hy._static_stub --write <この .pyi の隣の .hy>)

from typing import TypeAlias
from _typeshed import Incomplete
from doeff import Program as _Program
from collections.abc import Callable as Callable
from collections.abc import Generator as Generator
from dataclasses import dataclass as dataclass
from doeff import do as program_handler
from doeff import Resume as Resume
from doeff import Program as Program
from doeff import EffectBase as EffectBase
from doeff import K as K
from doeff import WithHandlerType as WithHandlerType
from doeff_core_effects._sql_effects import SqlQuery as SqlQuery
from doeff_core_effects._sql_effects import SqlInsertRows as SqlInsertRows
from doeff_core_effects._sql_effects import SqlBatch as SqlBatch
from doeff_core_effects._sql_effects import SqlNotify as SqlNotify
from doeff_core_effects._sql_effects import SqlRows as SqlRows
from doeff_core_effects._sql_effects import SqlFailed as SqlFailed
from doeff_core_effects._sql_effects import SqlUnreachable as SqlUnreachable
from doeff_core_effects._sql_effects import SqlTransactionMisuse as SqlTransactionMisuse
from doeff_core_effects._sql_effects import T as T

@dataclass(frozen=True, kw_only=True)
class TransactionAborted:
    failure: SqlFailed | SqlUnreachable

@dataclass(frozen=True, kw_only=True)
class TransactionMisused:
    reason: str

def transaction_scope(database: str, execute_query: Callable, execute_insert: Callable, execute_notify: Callable | None) -> Callable:
    ...

def run_in_transaction(database: str, program: Program, execute_query: Callable, execute_insert: Callable, begin: Callable, commit: Callable, rollback: Callable, execute_notify: Callable | None=None) -> _Program[Incomplete, object]:
    ...

@dataclass(frozen=True, kw_only=True)
class TransactionFlush:
    opening: bool
    requests: tuple[SqlQuery | SqlInsertRows, ...]
    notices: tuple[SqlNotify, ...]
    closing: bool
ScopeReceived: TypeAlias = TransactionFlush | tuple | SqlFailed | SqlUnreachable | T | None

@dataclass(kw_only=True)
class TransactionProgress:
    opened: bool = False
    closed: bool = False
    notices: tuple[SqlNotify, ...] = ...

def planned_flush(progress: TransactionProgress, requests: tuple, closing: bool) -> _Program[TransactionFlush | None, object]:
    ...

def stray_batch(database: str) -> _Program[SqlTransactionMisuse, object]:
    ...

def batched_transaction_scope(database: str, flush: Callable, accepts_notices: bool, progress: TransactionProgress) -> Callable:
    ...

def run_in_batched_transaction(database: str, program: Program, flush: Callable, rollback: Callable, accepts_notices: bool=False) -> _Program[Incomplete, object]:
    ...
