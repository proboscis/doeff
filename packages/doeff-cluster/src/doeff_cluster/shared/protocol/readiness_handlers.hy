;;; ReportReady の handler 2 つ。readiness-http = coordinator の POST /resources/Service/<名>/readiness へ送る(クラスタ)・
;;; readiness-memory = list に記録する(テスト)。送り方は service_report.hy(宛先の部品の上の HttpRequest — 報告には送り手の process の
;;; 世代が載る・#2337 の 4a)。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "doeff-cluster" :role "protocol"})
(require doeff-hy.macros [defhandler <-])
(import doeff_cluster.shared.intent.readiness_model [ReportReady])
(import doeff_cluster.shared.core.readiness_report [reported-readiness])
(import doeff_cluster.shared.protocol.coordinator_route [RouteCell RouteOptions])
(import doeff_cluster.shared.protocol.service_report [ServiceReport sent-report])


;; fake。本物(readiness-http → coordinator)と同じ契約を tests/test_readiness_contract.hy が両方で回す: coordinator が残す形
;; (reported-readiness — reason は先頭 300 字・role は standby 以外を active)で記録する。
(defhandler readiness-memory [#^ (get list (get dict #(str object))) reports]
  (ReportReady [ready reason role]
    (<- report dict (reported-readiness ready reason role))
    (.append reports report)
    (resume None)))


(defhandler readiness-http [#^ RouteCell cell #^ RouteOptions options #^ ServiceReport report]
  (ReportReady [ready reason role]
    (<- (sent-report cell options report "readiness" {"ready" ready "reason" reason "role" role}))
    (resume None)))
