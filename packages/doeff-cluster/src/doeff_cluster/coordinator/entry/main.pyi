"""coordinator/entry/main.hy の公開面の型(型検査のための宣言 — 実行時は main.hy を読む・#2760)。

main.hy は Hy の module なので、pyright は中を読めず、名が全部 Unknown になる。coordinator の起動の読み直し(load-state)を
模擬の世界で使う使い手(使い手の repo の模擬の世界の検)に、書き手に直せない赤(Type of "load_state" is unknown ほか)が出る。
ここで型を宣言する(store.pyi と同じ形)。

- board-file-rows・legacy-state・load-state は defk(呼ぶと Program を返す)。load-state の置き場は store.pyi の閉じた和 DurableStore。
- main は console script の入口(素の関数)。
"""

from typing import Any

from doeff_cluster.coordinator.intent.cluster_model import ClusterState
from doeff_cluster.coordinator.protocol.store import DurableStore

from doeff import Program

MODULE_TAGS: dict[str, str]

def board_file_rows(board_dir: str) -> Program[tuple[dict[str, Any], ...], Any]: ...
def legacy_state(state_file: str, now: int) -> Program[ClusterState | None, Any]: ...
def load_state(state_file: str, store: DurableStore, now: int) -> Program[ClusterState, Any]: ...
def main() -> None: ...
