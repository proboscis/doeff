"""metrics_model.hy の公開面の型(service の計器の報告 ReportMetrics — 型検査のための宣言・実行時は metrics_model.hy を読む・#2810)。

metrics_model.hy は Hy の module なので、pyright は中を読めず、報告の effect に答える使い手(模擬の土台が報告を落とす答え手を並べる
所など)の strict に、書き手に直せない Unknown の赤(Type of "ReportMetrics" is unknown)が出た。readiness_model.pyi と同じ形で宣言する。

- ReportMetrics は凍った dataclass の EffectBase[None](欄 metrics = counters・gauges・durations の写像 — 形は metrics_model.hy の頭の註)。
- ReadProcessGauges は欄の無い EffectBase(答え = 名 → float)。
- 記録の仕方の印 __record_spec__ は class の値で、欄ではないので宣言しない。
- 実装との食い違いは packages/doeff-cluster/tests/test_meter_report_static_types.py が検める。
"""

from dataclasses import dataclass

from doeff import EffectBase

@dataclass(frozen=True)
class ReportMetrics(EffectBase[None]):
    """service の計器の報告(その時点の累計と値)。答えは None。"""

    metrics: dict[str, object]

@dataclass(frozen=True)
class ReadProcessGauges(EffectBase[dict[str, float]]):
    """この process の memory の gauge を読む(答え = 名 → float)。"""
