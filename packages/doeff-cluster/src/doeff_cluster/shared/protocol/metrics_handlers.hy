;;; ReportMetrics の本物の handler。metrics-http = coordinator の POST /resources/Service/<名>/metrics へ送る(クラスタ)。
;;; 送り方は service_report.hy(宛先の部品の上の HttpRequest — 報告には送り手の process の世代が載る・#2337 の 4a)。
;;; fake の metrics-memory は metrics_memory.hy に置く: fake は coordinator と同じ解きと検め(MetricsPayload・checked-metrics・
;;; doeff_hy._wire)を通すので coordinator の型の閉包を読むが、本物は送るだけで読まない。どの job の foundation もこの module を読むので、
;;; 同じ module に置くと全部の job が coordinator の型と pydantic を起動で読んでいた。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "doeff-cluster" :role "protocol"})
(require doeff-hy.macros [defhandler <-])
(import doeff_cluster.shared.intent.metrics_model [ReportMetrics])
(import doeff_cluster.shared.protocol.coordinator_route [RouteCell RouteOptions])
(import doeff_cluster.shared.protocol.service_report [ServiceReport sent-report])


(defhandler metrics-http [#^ RouteCell cell #^ RouteOptions options #^ ServiceReport report]
  (ReportMetrics [metrics]
    (<- (sent-report cell options report "metrics" {"metrics" metrics}))
    (resume None)))
