"""service_rules.hy の公開面の型(型検査のための宣言 — 実行時は service_rules.hy を読む)。

service_rules.hy は Hy の module なので、pyright は中を読めない。identity と検めの判断(#2540 で service_model から分けた)の
型をここで宣言する。型(CallShape・Job・System・RecordArgument)と identity の形(_Identity)は service_model.pyi のものを使う。
deff は普通の関数、defk(foundation-needs-refusal・callables-in)は呼ぶと Program を返す。
"""

from collections.abc import Callable
from typing import Any

from doeff import Program
from doeff_cluster.shared.intent.service_model import (
    CallShape,
    Job,
    RecordArgument,
    System,
    _Identity,
)
from doeff_hy.json_value import JsonValue

def function_reference(function: Callable[..., object], where: str) -> str: ...
def canonical_record(value: RecordArgument, where: str) -> dict[str, JsonValue]: ...
def canonical_argument(value: object, where: str) -> JsonValue: ...
def identity_of(call: CallShape, where: str) -> _Identity: ...
def describe_identity(identity: _Identity) -> str: ...
def job_named(system: System, name: str) -> Job | None: ...
def foundation_needs_refusal(
    system: System, foundation: Callable[..., object]
) -> Program[str | None, Any]: ...
def callables_in(values: list[object]) -> Program[list[Callable[..., object]], Any]: ...
def environ_overlay_refusal(system: System, environ: dict[str, dict[str, str]]) -> str | None: ...
