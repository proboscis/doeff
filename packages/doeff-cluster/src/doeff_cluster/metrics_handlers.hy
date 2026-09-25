;;; ReportMetrics の handler 2 つ。metrics-http = coordinator の POST /resources/Service/<名>/metrics へ送る(クラスタ)・
;;; metrics-memory = list に記録する(テスト)。HTTP の client は report_client.hy に閉じる(報告には送り手の process の世代が載る)。
(require doeff-hy.macros [defhandler])
(import .metrics_model [ReportMetrics])
(import .report_client [ServiceReportClient])


(defhandler metrics-memory [#^ list reports]
  (ReportMetrics [metrics]
    (.append reports metrics)
    (resume None)))


(defhandler metrics-http [#^ ServiceReportClient client]
  (ReportMetrics [metrics]
    (.send client "metrics" {"metrics" metrics})
    (resume None)))
