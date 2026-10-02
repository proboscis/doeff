"""client_foundation.hy の公開面の型(手元の道具が切り離した task を送り・待つ口 — 型検査のための宣言・実行時は client_foundation.hy を読む・#2782)。

client_foundation.hy は Hy の module で、口を組む部品(detached-cluster・DetachedSender・RouteCell・route-of・coordinator-route-options
ほか)にも型の宣言が無い。使い手の repo がその部品を直に組むと、strict の型検査に書き手に直せない Unknown の赤(型の分からない import
23 件)が出た。使い手は部品を直に組まず、この口 1 つを型つきで呼ぶ。

- with-detached-client(Python の名 with_detached_client)は defk — 呼ぶと、本体の答えをそのまま答える Program を返す。
- 名乗りの型 RuntimeEnv は runtime_env_model.pyi が宣言する。
- 実装との食い違いは packages/doeff-cluster/tests/test_client_foundation_static_types.py が検める。
"""

from typing import TypeVar

from doeff import Program
from doeff_cluster.shared.intent.runtime_env_model import RuntimeEnv

_A = TypeVar("_A")

def with_detached_client(
    coordinator: str, revision: str, runtime_env: RuntimeEnv | None, body: Program[_A, object]
) -> Program[_A, object]: ...
