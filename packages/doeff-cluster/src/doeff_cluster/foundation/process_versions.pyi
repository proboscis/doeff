"""process_versions.hy の公開面の型(この process の版の識別 — 型検査のための宣言・実行時は process_versions.hy を読む・#2765)。

process_versions.hy は Hy の module で型の宣言が無かったので、使い手(宣言を組む検と宣言の CLI が current-versions を、入口と宿の
handler が process-versions を読む)の strict に、書き手に直せない Unknown の赤が出得る(defn を defk に変えた module で使い手の
型の門が止まった前例 — #2777 ほか)。host_contract.pyi と同じ形で宣言する。

- process-versions(Python の名 process_versions)は defk — 環境変数の置き場を受け、版の識別の dict を答える Program を返す。
- current-versions(Python の名 current_versions)は deff — os.environ を渡した版の識別の dict をそのまま返す(後半 #2766 で消す)。
- 実装との食い違いは packages/doeff-cluster/tests/test_process_versions_static_types.py が検める。
"""

from collections.abc import Mapping

from doeff import Program

RUNTIME_ENV_KEY_VAR: str

def process_versions(environ: Mapping[str, str]) -> Program[dict[str, str], object]: ...
def current_versions() -> dict[str, str]: ...
