;;; service が自分の計器(Prometheus の counter・gauge・秒の合計と回数)を報告する effect。
;;;
;;; 本番の Deployment は process の中の HTTP(:889x/metrics)で計器を出し、Prometheus が Pod を走査する。worker の service は
;;; 置き先の worker が変わりうるので、process は計器を HTTP で出さず、拍ごとに ReportMetrics を出す。handler(metrics_handlers.hy)が
;;; coordinator へ送り、coordinator の GET /metrics が「今の宣言で今動いている process」の最新の報告だけを Prometheus の形で出す
;;; (label = service・worker)。名は本番と同じ(counter は _total を足す — Prometheus の text の慣習)。
;;;
;;; metrics の形:
;;;   {"counters": {名: float}, "gauges": {名: float}, "durations": {名: {"sum": float "count": int}}}
(import dataclasses [dataclass])
(import doeff [EffectBase])


(defclass [(dataclass :frozen True)] ReportMetrics [EffectBase]
  "結果は None。metrics = その時点の累計(counter)と値(gauge)。報告が届かなくても業務は止めない。"
  (#^ dict metrics))


(defclass [(dataclass :frozen True)] ReadProcessGauges [EffectBase]
  "この process の memory の gauge を読む(答え = {名: float})。名と読み方は handler が決める(例: <書き手>_rss_bytes / _rss_peak_bytes)。/proc の読みなので
   業務コードは effect で出す。")
