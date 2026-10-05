"""warm_rules.hy の公開面の型(型検査のための宣言 — 実行時は warm_rules.hy を読む)。

warm_rules.hy は Hy の module なので、pyright は中の defk の形を読めない(runtime_env_rules.pyi と同じ理由・同じ形)。
warm-runtime-env は needs を検めてから WarmRuntimeEnv を出し、答え(WarmAnswer)を返す Program(#2564)。
defk(warm-runtime-env・warm-key・warm-state->json・warm-wait-answer)は呼ぶと Program を返す。warm-state-of-json は普通の関数。
"""

from typing import Any

from doeff import Program
from doeff_hy.json_value import JsonValue

from doeff_cluster.shared.intent.runtime_env_model import RuntimeEnv
from doeff_cluster.shared.intent.warm_model import WarmAnswer, WarmFailed, WarmReady, WarmState, WarmWaitExpired

def warm_runtime_env(env: RuntimeEnv, needs: frozenset[str], ttl_seconds: float, holder: str) -> Program[WarmAnswer, Any]: ...
def warm_key(env: RuntimeEnv, needs: tuple[str, ...]) -> Program[str, Any]: ...
def hyx_warm_state_XgreaterHthan_signXjson(state: WarmState) -> Program[dict[str, JsonValue], Any]: ...
def warm_state_of_json(value: dict[str, JsonValue]) -> WarmState: ...
def warm_wait_answer(
    read: WarmAnswer, key: str, waited: float, timeout_seconds: float
) -> Program[WarmReady | WarmFailed | WarmWaitExpired | None, Any]: ...
