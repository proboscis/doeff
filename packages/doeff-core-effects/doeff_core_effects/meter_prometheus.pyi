"""meter_prometheus.hy の公開面の型(計器の断面の Prometheus の text の描き手 — 型検査のための宣言・実行時は meter_prometheus.hy を読む)。

- defk は呼ぶと Program を返す(答えの型は Program の 1 つ目の引数)。
- helps は名 → # HELP の説明(在る名だけ説明の行を描く)。
- 実装との食い違いは packages/doeff-core-effects/tests/test_hy_module_stubs.py が検める。
"""

from typing import Any

from doeff_hy.frozen import FrozenMap

from doeff import Program
from doeff_core_effects.meter_effects import MeterSnapshot

CONTENT_TYPE: str

def render_prometheus(snapshot: MeterSnapshot, helps: FrozenMap[str]) -> Program[str, Any]: ...
