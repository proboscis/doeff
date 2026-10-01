"""coordinator/protocol/request_bodies.hy の公開面の型(型検査のための宣言 — 実行時は request_bodies.hy を読む)。

request_bodies.hy は Hy の module なので、pyright は中を読めず、名が全部 Unknown になる。判断を直に呼ぶ検と模擬の世界(agora の
agora_sim の検)が responded と request-bodies を使うと、書き手に直せない赤(Type of "responded" is unknown ほか)が出た(#2445)。
ここで型を宣言する(launch.pyi・job_context.pyi と同じ形)。状態と要求の型(ClusterState・Request)は型の宣言の無い Hy の module に
在るので Any で受ける。

- defn(body-type-of・responded)は普通の関数。defk(body-of)は呼ぶと Program を返す。request-bodies は handler の値。
"""

from typing import Any

from doeff import Program

MODULE_TAGS: dict[str, str]

def body_type_of(method: str, parts: tuple[str, ...]) -> type | None: ...
def body_of(request: Any) -> Program[Any, Any]: ...
def responded(state: Any, request: Any, now: int, timing: Any) -> tuple[Any, int, Any]: ...

request_bodies: Any
