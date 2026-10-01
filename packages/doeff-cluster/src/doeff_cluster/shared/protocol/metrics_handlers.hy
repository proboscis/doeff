;;; ReportMetrics の handler 2 つ。metrics-http = coordinator の POST /resources/Service/<名>/metrics へ送る(クラスタ)・
;;; metrics-memory = list に記録する(テスト)。送り方は service_report.hy(宛先の部品の上の HttpRequest — 報告には送り手の process の
;;; 世代が載る・#2337 の 4a)。
(require doeff-hy.macros [defhandler <-])
(import doeff_cluster.shared.intent.metrics_model [ReportMetrics])
(import doeff_cluster.coordinator.core.metrics_policy [checked-metrics])
(import doeff_cluster.coordinator.core.resource_policy [Refused])
(import doeff_cluster.shared.protocol.coordinator_route [RouteCell RouteOptions])
(import doeff_cluster.shared.protocol.service_report [ServiceReport sent-report])


;; fake。本物(metrics-http → coordinator)と同じ契約を tests/test_metrics_contract.hy が両方で回す: coordinator と同じ検め
;; (checked-metrics)を通った形(欠けた族は空の dict)で記録し、coordinator が断る形は記録しない — 本物も断りを log に出すだけで
;; 答えは None(報告は観測なので業務を止めない)。
(defhandler metrics-memory [#^ list reports]
  (ReportMetrics [metrics]
    (try
      (.append reports (checked-metrics metrics))
      (except [Refused]
        None))
    (resume None)))


(defhandler metrics-http [#^ RouteCell cell #^ RouteOptions options #^ ServiceReport report]
  (ReportMetrics [metrics]
    (<- (sent-report cell options report "metrics" {"metrics" metrics}))
    (resume None)))
