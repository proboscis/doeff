"""meter_report.hy の公開面の型(計器の断面を coordinator へ送る橋 — 型検査のための宣言・実行時は meter_report.hy を読む・#2810)。

meter_report.hy は Hy の module なので、pyright は中を読めず、service の土台が本体を with-meter-report で包む所の strict に、書き手に
直せない Unknown の赤(Type of "with_meter_report" is unknown・その答えを受けた変数の Unknown)が出た。readiness_handlers.pyi と同じ形で
宣言する。

- defk は呼ぶと Program を返す(答えの型 = 実装の :post の型)。with-meter-report は本体の答えをそのまま返す。
- 実装との食い違いは packages/doeff-cluster/tests/test_meter_report_static_types.py が検める。
"""

from typing import TypeVar

from doeff_core_effects.meter_effects import MeterSnapshot

from doeff import EffectBase, Program

_A = TypeVar("_A")

DEFAULT_REPORT_SECONDS: float

def report_metrics_of(snapshot: MeterSnapshot) -> Program[dict[str, object], object]: ...
def report_meter_once() -> Program[None, object]: ...
def report_meter_every(seconds: float) -> Program[None, object]: ...
def with_meter_report(body: Program[_A, object] | EffectBase[_A], seconds: float = ...) -> Program[_A, object]: ...
