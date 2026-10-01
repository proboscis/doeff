;;; ReportMetrics の handler 2 つ。metrics-http = coordinator の POST /resources/Service/<名>/metrics へ送る(クラスタ)・
;;; metrics-memory = list に記録する(テスト)。HTTP の client は report_client.hy に閉じる(報告には送り手の process の世代が載る)。
(require doeff-hy.macros [defhandler])
(import .metrics_model [ReportMetrics])
(import doeff_cluster.coordinator.core.metrics_policy [checked-metrics])
(import doeff_cluster.coordinator.core.resource_policy [Refused])
(import .report_client [ServiceReportClient])


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


(defhandler metrics-http [#^ ServiceReportClient client]
  (ReportMetrics [metrics]
    (.send client "metrics" {"metrics" metrics})
    (resume None)))
