"""service.hy の公開面の型(型検査のための宣言 — 実行時は service.hy を読む)。

service.hy は Hy の module なので、型の宣言が無いと pyright は中を読めず、HTTP の口の組 RecordsService と入口 respond が
Unknown になる(使い手 — 模擬の記録の service の相手役 — の strict の型検査で、書き手に直せない赤が連なる)。ここで型を宣言する。

- `(defclass [(dataclass :frozen True)] …)` は位置でも渡せる frozen の dataclass。
- handler-for は「書き手の名 → その書き手の記録の handler」。handler の形は置き場ごとに違う(memory は defhandler・PG は defk の組み立て)
  ので、答えは object に留める(実装の注記も素の Callable — store_choice.pyi と同じ扱い)。
- defk は呼ぶと Program を返す(答えの型 = 実装の :post の型)。
- PublicEffect は service.hy が wire.hy から import して使う公開 effect の union(undeclared-name の ask の型)。wire.hy には型の
  宣言が無いので、同じ並びを effects.pyi の名で書く(並びが実装と同じことは packages/doeff-records/tests/test_static_stubs.py が検める)。
- 実装との食い違いは同じ検が名・欄の名と順・既定値の有無・引数の名で検める。
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeAlias

from doeff import Program
from doeff_records.effects import (
    AppendEvent,
    ListRows,
    PutRow,
    PutRows,
    ReadEvents,
    ReadRow,
    ReadStreamEnd,
    WatchChanges,
)
from doeff_records.principals import Principal, Roster
from doeff_records.values import RecordsSchema

METHOD_GET: str
METHOD_POST: str
PATH_HEALTHZ: str

PublicEffect: TypeAlias = (
    ReadRow | ListRows | PutRow | WatchChanges | AppendEvent | ReadEvents | PutRows | ReadStreamEnd
)

@dataclass(frozen=True)
class HttpRequest:
    method: str
    path: str
    authorization: str | None
    body: bytes
    writer: str | None = None

@dataclass(frozen=True)
class HttpAnswer:
    status: int
    body: str

@dataclass(frozen=True)
class RecordsService:
    schema: RecordsSchema
    roster: Roster
    handler_for: Callable[[str], object]

def json_answer(status: int, body: dict[str, object]) -> Program[HttpAnswer, object]: ...
def refusal_answer(error: str, reason: str) -> Program[HttpAnswer, object]: ...
def undeclared_name(schema: RecordsSchema, ask: PublicEffect) -> Program[str | None, object]: ...
def serve_operation(
    service: RecordsService, principal: Principal, operation: str, body: bytes
) -> Program[HttpAnswer, object]: ...
def respond(service: RecordsService, request: HttpRequest) -> Program[HttpAnswer, object]: ...
