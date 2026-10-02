;;; ReportReady の handler 2 つ。readiness-http = coordinator の POST /resources/Service/<名>/readiness へ送る(クラスタ)・
;;; readiness-claims = 入れ物 ReadinessLog に揃えた報告(ReadinessClaim)を積む(テストと模擬 — 3 欄の dict を list に積んだ旧い fake
;;; readiness-memory は使い手が移ったので消した・#3028)。送り方は service_report.hy(宛先の部品の上の HttpRequest — 報告には
;;; 送り手の process の世代が載る・#2337 の 4a)。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "doeff-cluster" :role "protocol"})
(require doeff-hy.macros [defhandler <-])
(import doeff_cluster.shared.intent.readiness_model [ReportReady ReadinessClaim])
(import doeff_cluster.shared.core.readiness_report [reported-readiness])
(import doeff_cluster.shared.protocol.coordinator_route [RouteCell RouteOptions])
(import doeff_cluster.shared.protocol.service_report [ServiceReport sent-report])


(defclass ReadinessLog []
  "fake の答え手 readiness-claims が報告を積む入れ物 — テストと模擬の世界が、走らせた後に受けた報告を読むため。claims = 揃えた報告
   (ReadinessClaim)の組(受けた順)。積むたびに組を作り直す(書き換えるのは欄 claims の値だけ)。"
  (defn #^ None __init__ [self]  ; defk にできない: class の作り手(Python が呼ぶ口)
    "空の入れ物を作るため。"
    (setv #^ (get tuple #(ReadinessClaim ...)) self.claims #())))


;; fake。本物(readiness-http → coordinator)と同じ契約を tests/test_readiness_contract.hy が両方で回す: coordinator が残す形
;; (reported-readiness — reason は先頭 300 字・role は standby 以外を active)で積む。
;; 引数に残す理由: 入れ物は呼び手が作って走らせた後に読む記録の置き場(検と模擬の世界ごとに別の入れ物)。
(defhandler readiness-claims [#^ ReadinessLog log]
  (ReportReady [ready reason role]
    (<- claim ReadinessClaim (reported-readiness ready reason role))
    (setv log.claims (+ log.claims #(claim)))
    (resume None)))


(defhandler readiness-http [#^ RouteCell cell #^ RouteOptions options #^ ServiceReport report]
  (ReportReady [ready reason role]
    (<- (sent-report cell options report "readiness" {"ready" ready "reason" reason "role" role}))
    (resume None)))
