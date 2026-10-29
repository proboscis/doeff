"""doeff_records: the effects and handlers of the package (and their types), read on first use."""

from importlib import import_module
from typing import TYPE_CHECKING

# Names of the submodules behind `_` (ADR-DOE-API-001 R5), read on first use: the root does not import
# them while it loads. `X as X` shows each name to type checkers; `__all__` is not used (DOEFF021).
if TYPE_CHECKING:
    from doeff_records._effects import AppendEvent as AppendEvent
    from doeff_records._effects import AppendEvents as AppendEvents
    from doeff_records._effects import AwaitRecordsBack as AwaitRecordsBack
    from doeff_records._effects import EventAppend as EventAppend
    from doeff_records._effects import ListRows as ListRows
    from doeff_records._effects import PutRow as PutRow
    from doeff_records._effects import PutRows as PutRows
    from doeff_records._effects import ReadBatch as ReadBatch
    from doeff_records._effects import ReadEventByKey as ReadEventByKey
    from doeff_records._effects import ReadEvents as ReadEvents
    from doeff_records._effects import ReadRequestPatience as ReadRequestPatience
    from doeff_records._effects import ReadRow as ReadRow
    from doeff_records._effects import ReadSignalSource as ReadSignalSource
    from doeff_records._effects import ReadSourcePatience as ReadSourcePatience
    from doeff_records._effects import ReadStreamEnd as ReadStreamEnd
    from doeff_records._effects import RowWrite as RowWrite
    from doeff_records._effects import WatchChanges as WatchChanges
    from doeff_records._effects import WatchEvents as WatchEvents
    from doeff_records._faults import AddStoreFault as AddStoreFault
    from doeff_records._faults import AdvanceStoreEpoch as AdvanceStoreEpoch
    from doeff_records._faults import ClearStoreFaults as ClearStoreFaults
    from doeff_records._faults import SetStoreOutage as SetStoreOutage
    from doeff_records._faults import StoreFault as StoreFault
    from doeff_records._faults import StoreOperation as StoreOperation
    from doeff_records._laws import ChangeLog as ChangeLog
    from doeff_records._laws import ExpiredEvents as ExpiredEvents
    from doeff_records._laws import PruneChanges as PruneChanges
    from doeff_records._laws import Pruned as Pruned
    from doeff_records._laws import ReadChangeLog as ReadChangeLog
    from doeff_records._laws import ReadExpiredEvents as ReadExpiredEvents
    from doeff_records._laws import RetireArchivedEvents as RetireArchivedEvents
    from doeff_records._laws import RetiredEvents as RetiredEvents
    from doeff_records._laws import SweepExpired as SweepExpired
    from doeff_records._laws import Swept as Swept
    from doeff_records._main import RecordsListening as RecordsListening
    from doeff_records._main import RecordsPrepared as RecordsPrepared
    from doeff_records._main import pg_records_handler as pg_records_handler
    from doeff_records._main import printed_listening as printed_listening
    from doeff_records._main import writer_gates as writer_gates
    from doeff_records._values import Appended as Appended
    from doeff_records._values import AppendedEvents as AppendedEvents
    from doeff_records._values import AppendEventAnswer as AppendEventAnswer
    from doeff_records._values import AppendEventsAnswer as AppendEventsAnswer
    from doeff_records._values import BatchRead as BatchRead
    from doeff_records._values import ByKeyHead as ByKeyHead
    from doeff_records._values import ByKeySuffix as ByKeySuffix
    from doeff_records._values import Changes as Changes
    from doeff_records._values import Conflict as Conflict
    from doeff_records._values import DescendingWithin as DescendingWithin
    from doeff_records._values import EachEvent as EachEvent
    from doeff_records._values import Event as Event
    from doeff_records._values import EventAbsent as EventAbsent
    from doeff_records._values import EventRetired as EventRetired
    from doeff_records._values import Events as Events
    from doeff_records._values import EventsMoved as EventsMoved
    from doeff_records._values import EventsQuiet as EventsQuiet
    from doeff_records._values import ExpectAbsent as ExpectAbsent
    from doeff_records._values import ExpectAny as ExpectAny
    from doeff_records._values import Expectation as Expectation
    from doeff_records._values import ExpectVersion as ExpectVersion
    from doeff_records._values import FieldDecl as FieldDecl
    from doeff_records._values import KeepFor as KeepFor
    from doeff_records._values import KeepForever as KeepForever
    from doeff_records._values import KeyMemory as KeyMemory
    from doeff_records._values import ListCursor as ListCursor
    from doeff_records._values import ListRowsAnswer as ListRowsAnswer
    from doeff_records._values import Missing as Missing
    from doeff_records._values import NotIndexed as NotIndexed
    from doeff_records._values import Page as Page
    from doeff_records._values import PutRowAnswer as PutRowAnswer
    from doeff_records._values import PutRowsAnswer as PutRowsAnswer
    from doeff_records._values import ReadBatchAnswer as ReadBatchAnswer
    from doeff_records._values import ReadEventByKeyAnswer as ReadEventByKeyAnswer
    from doeff_records._values import ReadEventsAnswer as ReadEventsAnswer
    from doeff_records._values import ReadRowAnswer as ReadRowAnswer
    from doeff_records._values import ReadStreamEndAnswer as ReadStreamEndAnswer
    from doeff_records._values import RecordsSchema as RecordsSchema
    from doeff_records._values import Refused as Refused
    from doeff_records._values import RememberedWrite as RememberedWrite
    from doeff_records._values import RememberKeysFor as RememberKeysFor
    from doeff_records._values import RememberKeysForever as RememberKeysForever
    from doeff_records._values import RememberNoKeys as RememberNoKeys
    from doeff_records._values import Reset as Reset
    from doeff_records._values import Retention as Retention
    from doeff_records._values import RetentionGroup as RetentionGroup
    from doeff_records._values import RetiredKey as RetiredKey
    from doeff_records._values import Row as Row
    from doeff_records._values import RowChanged as RowChanged
    from doeff_records._values import RowRemoved as RowRemoved
    from doeff_records._values import RowsConflict as RowsConflict
    from doeff_records._values import RowsRefused as RowsRefused
    from doeff_records._values import StreamDecl as StreamDecl
    from doeff_records._values import StreamEmpty as StreamEmpty
    from doeff_records._values import StreamEnd as StreamEnd
    from doeff_records._values import StreamTail as StreamTail
    from doeff_records._values import StreamTailEmpty as StreamTailEmpty
    from doeff_records._values import TableDecl as TableDecl
    from doeff_records._values import UndeclaredField as UndeclaredField
    from doeff_records._values import UndeclaredTable as UndeclaredTable
    from doeff_records._values import Unreachable as Unreachable
    from doeff_records._values import WaitsClosed as WaitsClosed
    from doeff_records._values import WatchChangesAnswer as WatchChangesAnswer
    from doeff_records._values import WatchCursor as WatchCursor
    from doeff_records._values import WatchEventsAnswer as WatchEventsAnswer
    from doeff_records._values import Written as Written
    from doeff_records._values import WrittenRows as WrittenRows
    from doeff_records._memory import ExpiredCursor as ExpiredCursor
    from doeff_records._memory import SignalSourceFactory as SignalSourceFactory
    from doeff_records._memory import memory_records_handler as memory_records_handler
    from doeff_records._memory import memory_signal_handler as memory_signal_handler
    from doeff_records._memory import waits_beside_sources as waits_beside_sources
    from doeff_records._http_server import BodyWrapper as BodyWrapper
    from doeff_records._http_server import CloseWaits as CloseWaits
    from doeff_records._http_server import ClosingWaits as ClosingWaits
    from doeff_records._http_server import KeepPreparedHandlers as KeepPreparedHandlers
    from doeff_records._http_server import MarkBook as MarkBook
    from doeff_records._http_server import PreparedHandlers as PreparedHandlers
    from doeff_records._http_server import RequestMark as RequestMark
    from doeff_records._http_server import StampRequest as StampRequest
    from doeff_records._http_server import closing_cuts_waits as closing_cuts_waits
    from doeff_records._http_server import prepared_slot as prepared_slot
    from doeff_records._http_server import ready_handlers as ready_handlers
    from doeff_records._http_server import request_stamps as request_stamps
    from doeff_records._http_server import shell_control as shell_control
    from doeff_records._http_server import store_not_prepared as store_not_prepared
    from doeff_records._http_server import wait_stamps as wait_stamps
    from doeff_records._http_server import waits_closing as waits_closing

_LAZY_EXPORTS: dict[str, str] = {
    "AppendEventAnswer": "doeff_records._values",
    "AppendEventsAnswer": "doeff_records._values",
    "Appended": "doeff_records._values",
    "AppendedEvents": "doeff_records._values",
    "BatchRead": "doeff_records._values",
    "ByKeyHead": "doeff_records._values",
    "ByKeySuffix": "doeff_records._values",
    "Changes": "doeff_records._values",
    "Conflict": "doeff_records._values",
    "DescendingWithin": "doeff_records._values",
    "EachEvent": "doeff_records._values",
    "Event": "doeff_records._values",
    "EventAbsent": "doeff_records._values",
    "EventRetired": "doeff_records._values",
    "Events": "doeff_records._values",
    "EventsMoved": "doeff_records._values",
    "EventsQuiet": "doeff_records._values",
    "ExpectAbsent": "doeff_records._values",
    "ExpectAny": "doeff_records._values",
    "ExpectVersion": "doeff_records._values",
    "Expectation": "doeff_records._values",
    "FieldDecl": "doeff_records._values",
    "KeepFor": "doeff_records._values",
    "KeepForever": "doeff_records._values",
    "KeyMemory": "doeff_records._values",
    "ListCursor": "doeff_records._values",
    "ListRowsAnswer": "doeff_records._values",
    "Missing": "doeff_records._values",
    "NotIndexed": "doeff_records._values",
    "Page": "doeff_records._values",
    "PutRowAnswer": "doeff_records._values",
    "PutRowsAnswer": "doeff_records._values",
    "ReadBatchAnswer": "doeff_records._values",
    "ReadEventByKeyAnswer": "doeff_records._values",
    "ReadEventsAnswer": "doeff_records._values",
    "ReadRowAnswer": "doeff_records._values",
    "ReadStreamEndAnswer": "doeff_records._values",
    "RecordsSchema": "doeff_records._values",
    "Refused": "doeff_records._values",
    "RememberKeysFor": "doeff_records._values",
    "RememberKeysForever": "doeff_records._values",
    "RememberNoKeys": "doeff_records._values",
    "RememberedWrite": "doeff_records._values",
    "Reset": "doeff_records._values",
    "Retention": "doeff_records._values",
    "RetentionGroup": "doeff_records._values",
    "RetiredKey": "doeff_records._values",
    "Row": "doeff_records._values",
    "RowChanged": "doeff_records._values",
    "RowRemoved": "doeff_records._values",
    "RowsConflict": "doeff_records._values",
    "RowsRefused": "doeff_records._values",
    "StreamDecl": "doeff_records._values",
    "StreamEmpty": "doeff_records._values",
    "StreamEnd": "doeff_records._values",
    "StreamTail": "doeff_records._values",
    "StreamTailEmpty": "doeff_records._values",
    "TableDecl": "doeff_records._values",
    "UndeclaredField": "doeff_records._values",
    "UndeclaredTable": "doeff_records._values",
    "Unreachable": "doeff_records._values",
    "WaitsClosed": "doeff_records._values",
    "WatchChangesAnswer": "doeff_records._values",
    "WatchCursor": "doeff_records._values",
    "WatchEventsAnswer": "doeff_records._values",
    "Written": "doeff_records._values",
    "WrittenRows": "doeff_records._values",
    "AppendEvent": "doeff_records._effects",
    "AppendEvents": "doeff_records._effects",
    "AwaitRecordsBack": "doeff_records._effects",
    "EventAppend": "doeff_records._effects",
    "ListRows": "doeff_records._effects",
    "PutRow": "doeff_records._effects",
    "PutRows": "doeff_records._effects",
    "ReadBatch": "doeff_records._effects",
    "ReadEventByKey": "doeff_records._effects",
    "ReadEvents": "doeff_records._effects",
    "ReadRequestPatience": "doeff_records._effects",
    "ReadRow": "doeff_records._effects",
    "ReadSignalSource": "doeff_records._effects",
    "ReadSourcePatience": "doeff_records._effects",
    "ReadStreamEnd": "doeff_records._effects",
    "RowWrite": "doeff_records._effects",
    "WatchChanges": "doeff_records._effects",
    "WatchEvents": "doeff_records._effects",
    "AddStoreFault": "doeff_records._faults",
    "AdvanceStoreEpoch": "doeff_records._faults",
    "ClearStoreFaults": "doeff_records._faults",
    "SetStoreOutage": "doeff_records._faults",
    "StoreFault": "doeff_records._faults",
    "StoreOperation": "doeff_records._faults",
    "ChangeLog": "doeff_records._laws",
    "ExpiredEvents": "doeff_records._laws",
    "PruneChanges": "doeff_records._laws",
    "Pruned": "doeff_records._laws",
    "ReadChangeLog": "doeff_records._laws",
    "ReadExpiredEvents": "doeff_records._laws",
    "RetireArchivedEvents": "doeff_records._laws",
    "RetiredEvents": "doeff_records._laws",
    "SweepExpired": "doeff_records._laws",
    "Swept": "doeff_records._laws",
    "RecordsListening": "doeff_records._main",
    "RecordsPrepared": "doeff_records._main",
    "pg_records_handler": "doeff_records._main",
    "printed_listening": "doeff_records._main",
    "writer_gates": "doeff_records._main",
    "ExpiredCursor": "doeff_records._memory",
    "SignalSourceFactory": "doeff_records._memory",
    "memory_records_handler": "doeff_records._memory",
    "memory_signal_handler": "doeff_records._memory",
    "waits_beside_sources": "doeff_records._memory",
    "AdmitWriters": "doeff_records._pg",
    "RecordsSqlFailed": "doeff_records._pg",
    "ReleaseWriters": "doeff_records._pg",
    "StoreUnreachable": "doeff_records._pg",
    "BodyWrapper": "doeff_records._http_server",
    "CloseWaits": "doeff_records._http_server",
    "ClosingWaits": "doeff_records._http_server",
    "KeepPreparedHandlers": "doeff_records._http_server",
    "MarkBook": "doeff_records._http_server",
    "PreparedHandlers": "doeff_records._http_server",
    "RequestMark": "doeff_records._http_server",
    "StampRequest": "doeff_records._http_server",
    "closing_cuts_waits": "doeff_records._http_server",
    "prepared_slot": "doeff_records._http_server",
    "ready_handlers": "doeff_records._http_server",
    "request_stamps": "doeff_records._http_server",
    "shell_control": "doeff_records._http_server",
    "store_not_prepared": "doeff_records._http_server",
    "wait_stamps": "doeff_records._http_server",
    "waits_closing": "doeff_records._http_server",
}


def __getattr__(name: str) -> object:
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import hy  # noqa: F401 - the modules behind `_` are Hy modules; importing hy lets Python find them

    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_LAZY_EXPORTS})
