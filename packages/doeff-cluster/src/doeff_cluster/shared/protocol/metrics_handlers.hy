;;; ReportMetrics の handler 2 つ。metrics-http = coordinator の POST /resources/Service/<名>/metrics へ送る(クラスタ)・
;;; metrics-memory = list に記録する(テスト)。送り方は service_report.hy(宛先の部品の上の HttpRequest — 報告には送り手の process の
;;; 世代が載る・#2337 の 4a)。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "doeff-cluster" :role "protocol"})
(require doeff-hy.macros [defhandler <-])
(import doeff_cluster.shared.intent.metrics_model [ReportMetrics])
(import doeff_cluster.coordinator.core.metrics_policy [checked-metrics])
(import doeff_cluster.coordinator.intent.request_bodies [MetricsPayload])
(import doeff_hy.wire [parse Malformed])
(import doeff_cluster.coordinator.core.resource_policy [Refused])
(import doeff_cluster.shared.protocol.coordinator_route [RouteCell RouteOptions])
(import doeff_cluster.shared.protocol.service_report [ServiceReport sent-report])


;; fake。本物(metrics-http → coordinator)と同じ契約を tests/test_metrics_contract.hy が両方で回す: coordinator と同じ解きと検め
;; (本文の型 MetricsPayload に解いて checked-metrics を通す — #2445)を通った形(欠けた族は空の dict)で記録し、coordinator が断る形は
;; 記録しない — 本物も断りを log に出すだけで答えは None(報告は観測なので業務を止めない)。
(defhandler metrics-memory [#^ list reports]
  (ReportMetrics [metrics]
    (<- payload (parse MetricsPayload metrics))
    (when (not (isinstance payload Malformed))
      (try
        (.append reports (checked-metrics payload))
        (except [Refused]
          None)))
    (resume None)))


(defhandler metrics-http [#^ RouteCell cell #^ RouteOptions options #^ ServiceReport report]
  (ReportMetrics [metrics]
    (<- (sent-report cell options report "metrics" {"metrics" metrics}))
    (resume None)))
