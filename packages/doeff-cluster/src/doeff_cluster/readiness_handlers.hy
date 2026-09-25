;;; ReportReady の handler 2 つ。readiness-http = coordinator の POST /resources/Service/<名>/readiness へ送る(クラスタ)・
;;; readiness-memory = list に記録する(テスト)。HTTP の client は report_client.hy に閉じる(報告には送り手の process の世代が載る)。
(require doeff-hy.macros [defhandler])
(import .readiness_model [ReportReady])
(import .report_client [ServiceReportClient report-client])


(defhandler readiness-memory [#^ list reports]
  (ReportReady [ready reason role]
    (.append reports {"ready" ready "reason" reason "role" role})
    (resume None)))


(defhandler readiness-http [#^ ServiceReportClient client]
  (ReportReady [ready reason role]
    (.send client "readiness" {"ready" ready "reason" reason "role" role})
    (resume None)))
