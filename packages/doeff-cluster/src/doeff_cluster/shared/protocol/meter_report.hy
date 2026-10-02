;;; 計器の断面を coordinator へ送る橋(#2740)— service の job が doeff の計器の effect(CountMetric・ObserveSeconds・SetGauge)で
;;; 積んだ断面を、間隔ごとと本体の終わりに、service の計器の報告 ReportMetrics(metrics_model)として送る。
;;;
;;; なぜ: worker の service は置き先の worker が変わりうるので、process は計器を HTTP で出さず、ReportMetrics で coordinator へ送る
;;; (metrics_model の頭の註)。coordinator は今動いている process の最新の報告だけを GET /metrics に出す(180 秒より古い報告は出さない —
;;; 間隔はそれより十分短く取る)。報告は累計なので、送りが 1 つ落ちても次の報告で追いつく。
;;; 計器の答え手(doeff の memory-meter-handler・process-meter-handler)は呼び手が外側に並べる — 本体が数える計器と橋が読む計器を同じ
;;; 答え手にするため(記録の client に渡す計器 — doeff-records の RecordsEndpoint.meter — も同じ答え手を渡す)。
;;;
;;;   (with-handlers [(memory-meter-handler (MeterSettings))]
;;;     (with-meter-report body))      ; 本体の答えをそのまま返す
(require doeff-hy.macros [defk <- val])
(val MODULE-TAGS {:context "doeff-cluster" :role "protocol"})
(import doeff [EffectBase Program])
(import doeff_core_effects.meter_effects [MeterSnapshot ReadMeter])
(import doeff_core_effects.scheduler [Spawn Cancel Task])
(import doeff_time [Delay])
(import doeff_cluster.shared.intent.metrics_model [ReportMetrics])

;; 報告の間隔の既定(秒)。coordinator が報告を古いとみなす 180 秒(metrics_policy の METRICS-STALE-MS)より十分短く、書き手の拍(5〜15 秒)
;; より長い。
(val DEFAULT-REPORT-SECONDS 30.0)


(defk report-metrics-of [snapshot]
  {:pre [(: snapshot MeterSnapshot)] :post [(: % dict)] :tags {:context "doeff-cluster" :role "protocol"}}
  "計器の断面を、ReportMetrics の報告の形(counters・gauges・durations の sum と count — metrics_model の頭の註)に綴るため。断面の秒の
   観測は total と count を持つので、報告の綴り(sum)へ名を替える。"
  {"counters" (dict snapshot.counters)
   "gauges" (dict snapshot.gauges)
   "durations" (dfor #(name row) (.items snapshot.durations) name {"sum" row.total "count" row.count})})


(defk report-meter-once []
  {:pre [] :post [(: % None)] :tags {:context "doeff-cluster" :role "protocol"}}
  "今の計器の断面を 1 回 coordinator へ送るため。"
  (<- snapshot MeterSnapshot (ReadMeter))
  (<- metrics dict (report-metrics-of snapshot))
  (<- (ReportMetrics metrics))
  None)


(defk report-meter-every [seconds]
  {:pre [(: seconds float)] :post [(: % None)] :tags {:context "doeff-cluster" :role "protocol"}}
  "seconds ごとに計器の断面を送り続けるため(止めるのは with-meter-report の Cancel)。"
  (while True
    (<- (Delay seconds))
    (<- (report-meter-once)))
  None)


(defk with-meter-report [body [seconds DEFAULT-REPORT-SECONDS]]
  {:pre [(: body (| Program EffectBase)) (: seconds float)] :post [(: % "body の答え")] :tags {:context "doeff-cluster" :role "protocol"}}
  "本体 body を走らせる間、計器の断面を seconds ごとに coordinator へ送り、本体が終わった時にも 1 回送るため(本体の答えをそのまま返す)。
   最初の報告は本体の前に送る(本体を待たずに、service の計器の行が coordinator に載る)。"
  (<- (report-meter-once))
  (<- loop Task (Spawn (report-meter-every seconds) :daemon True))
  (<- answer body)
  (<- (Cancel loop))
  (<- (report-meter-once))
  answer)
