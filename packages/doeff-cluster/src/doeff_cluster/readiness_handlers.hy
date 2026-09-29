;;; ReportReady の handler 2 つ。readiness-http = coordinator の POST /resources/Service/<名>/readiness へ送る(クラスタ)・
;;; readiness-memory = list に記録する(テスト)。HTTP の client は report_client.hy に閉じる(報告には送り手の process の世代が載る)。
(require doeff-hy.macros [defhandler <-])
(import .readiness_model [ReportReady reported-readiness])
(import .report_client [ServiceReportClient report-client])


;; fake。本物(readiness-http → coordinator)と同じ契約を tests/test_readiness_contract.hy が両方で回す: coordinator が残す形
;; (reported-readiness — reason は先頭 300 字・role は standby 以外を active)で記録する。
(defhandler readiness-memory [#^ list reports]
  (ReportReady [ready reason role]
    (<- report dict (reported-readiness ready reason role))
    (.append reports report)
    (resume None)))


(defhandler readiness-http [#^ ServiceReportClient client]
  (ReportReady [ready reason role]
    (.send client "readiness" {"ready" ready "reason" reason "role" role})
    (resume None)))
