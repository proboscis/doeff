"""worker/protocol/heartbeat.hy の公開面の型(型検査のための宣言 — 実行時は heartbeat.hy を読む・#2824)。

heartbeat.hy は Hy の module なので、pyright は中を読めず、名が全部 Unknown になる。#2751 で defk にした status-rows-json を、
使い手の repo の模擬の世界が本番の worker と同じ綴りとして `<-` で受けると、書き手に直せない赤(Type of "status_rows_json" is
unknown・受けた値の Argument type is unknown)が出た。ここで型を宣言する(drain_requests.pyi・launch.pyi と同じ形)。

- deff(env-report・env-heartbeat-part・heartbeat-body)は普通の関数で、答えは heartbeat の本文に載せる JSON の object。
  heartbeat-body の引数はキーワードだけ。
- defk(status-report・status-rows-json・status-row)は呼ぶと Program を返す。答えは状態の行(JSON の object)の列・組・1 行。
- 実装との食い違いは packages/doeff-cluster/tests/test_service_model_stubs.py が検める。
"""

from typing import Any

from doeff import Program
from doeff_cluster.worker.intent.worker_model import CodeView, JobStatus

def env_report(views: tuple[CodeView, ...], capacity: str) -> dict[str, object]: ...
def env_heartbeat_part(report: dict[str, object], platform: str) -> dict[str, object]: ...
def heartbeat_body(
    *,
    name: str,
    provides: tuple[str, ...],
    exclusive: tuple[str, ...],
    node: str,
    capacity: int,
    versions: dict[str, str],
    statuses: list[dict[str, object]],
    endpoint: str,
    boot: str,
    boot_at: int,
    tools: dict[str, object],
) -> dict[str, object]: ...
def status_report(
    statuses: tuple[JobStatus, ...], task_echo: dict[str, dict[str, object]], results: dict[str, str | None]
) -> Program[list[dict[str, object]], Any]: ...
def status_rows_json(statuses: tuple[JobStatus, ...]) -> Program[tuple[dict[str, object], ...], Any]: ...
def status_row(s: JobStatus) -> Program[dict[str, object], Any]: ...
