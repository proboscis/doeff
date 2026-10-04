"""service_build.hy の公開面の型(型検査のための宣言 — 実行時は service_build.hy を読む)。

service_build.hy は Hy の module なので、pyright は中を読めない。defsystem の展開は
`doeff_cluster.shared.entry.service_build.system_of` / `job` を呼ぶので、宣言が無いと defsystem を書いた file ごとに書き手に
直せない Unknown の赤が出る(service_model.pyi の頭の註と同じ理由 — 構成子は #2540 で service_model から移した)。
型(CallShape・Job・System・Declaration)と実行環境の読む欄(_RuntimeEnvView)は service_model.pyi のものを使う。
"""

from collections.abc import Callable

from doeff_cluster.shared.intent.service_model import (
    CallShape,
    Declaration,
    Job,
    System,
    _RuntimeEnvView,
)

def job(
    name: str,
    program: object,
    *,
    call: CallShape,
    needs: frozenset[str] | set[str] | list[str] | tuple[str, ...] | None,
    replicas: int,
    readiness: dict[str, float] | None = None,
    update: str = "recreate",
    environ: dict[str, str] | None = None,
) -> Job: ...
def system_of(name: str, jobs: tuple[Job, ...]) -> System: ...
def system_declaration(
    system: System,
    revision: str,
    runtime_env: _RuntimeEnvView | None = None,
    environ: dict[str, dict[str, str]] | None = None,
    *,
    versions: dict[str, str],
) -> Declaration: ...
def resolve_value(path: str) -> Callable[..., object] | System: ...
def resolve(path: str) -> Callable[..., object]: ...
