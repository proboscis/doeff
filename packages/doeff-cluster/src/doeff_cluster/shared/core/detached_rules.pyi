"""detached_rules.hy の公開面の型(型検査のための宣言 — 実行時は detached_rules.hy を読む)。

detached_rules.hy は Hy の module なので、pyright は中の defk の形を読めない(runtime_env_rules.pyi と同じ理由・同じ形)。
submit-detached-task は needs を検めてから SubmitDetached を出し、答え(DetachedSubmitAnswer)を返す Program(#2564)。
"""

from typing import Any

from doeff import Program

from doeff_cluster.shared.intent.detached_model import DetachedSubmitAnswer
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
) -> Program[DetachedSubmitAnswer, Any]: ...
