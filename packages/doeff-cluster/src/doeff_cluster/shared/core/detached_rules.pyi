"""detached_rules.hy の公開面の型(型検査のための宣言 — 実行時は detached_rules.hy を読む)。

detached_rules.hy は Hy の module なので、pyright は中の defk の形を読めない(runtime_env_rules.pyi と同じ理由・同じ形)。
submit-detached-task は needs と置き方の名乗り(priority・limits)を検めてから SubmitDetached を出し、答え(DetachedSubmitAnswer)を返す Program(#2564)。
task-*-refusal・task-limits-of・task-limits-json・task-placing-json は送り手と coordinator の受け口が同じ規則で読む置き方の名乗りの判断と綴り。
group-limit-refusal・group-limit-of・group-limit-spec は系の構成子・coordinator の資源の口・保存の読み直しが同じ規則で読む、系が宣言する
組の上限(GroupLimit)の判断と綴り(2026-10-12)。
"""

from typing import Any

from doeff import Program

from doeff_cluster.shared.intent.detached_model import DetachedSubmitAnswer, GroupLimit, TaskLimit
from doeff_cluster.shared.intent.runtime_env_model import EnvVar

def submit_detached_task(
    program: Program[object, Any],
    key: str,
    *,
    needs: frozenset[str] = ...,
    name: str = ...,
    lease_seconds: float = ...,
    retain_seconds: float = ...,
    environ: tuple[EnvVar, ...] = ...,
    priority: int = ...,
    limits: tuple[TaskLimit, ...] = ...,
) -> Program[DetachedSubmitAnswer, Any]: ...

type JsonValue = dict[str, object] | list[object] | tuple[object, ...] | str | int | float | bool | None

def task_priority_refusal(value: JsonValue) -> str | None: ...
def task_limit_refusal(value: JsonValue) -> str | None: ...
def task_limits_refusal(value: JsonValue) -> str | None: ...
def task_limits_of(value: JsonValue) -> tuple[TaskLimit, ...]: ...
def task_limit_json(limit: TaskLimit) -> dict[str, object]: ...
def task_limits_json(limits: tuple[TaskLimit, ...]) -> list[dict[str, object]]: ...
def task_placing_json(priority: int, limits: tuple[TaskLimit, ...]) -> dict[str, object]: ...
def group_limit_refusal(name: JsonValue, spec: JsonValue) -> str | None: ...
def group_limit_of(name: str, spec: dict[str, object]) -> GroupLimit: ...
def group_limit_spec(limit: GroupLimit) -> dict[str, object]: ...
