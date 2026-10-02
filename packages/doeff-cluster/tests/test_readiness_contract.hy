;;; readiness の報告の契約テスト — 同じ effect ReportReady に答える本物(readiness-http → coordinator)と fake(readiness-memory)が、
;;; 同じ deftest を通る。解釈器の組み立ては coordinator_contract_handlers.hy。
;;;
;;;   * 答えはいつも None・coordinator の側に最後に残るのは最後の報告の ReadinessClaim(ready reason role)(既定は reason "" と role active)
;;;   * 残る形は coordinator の形: reason は先頭 300 字・role は standby 以外を active と読む
;;;   * coordinator へ届かない間も答えは None(業務を止めない)・届くようになった後の報告は残る
;;; 報告の送り手の世代と、Ready の数え方(window)は本物だけの性質なので契約の外(test_resources.hy)。
(require doeff-hy.macros [deftest <-])
(import doeff_cluster.shared.intent.readiness_model [ReportReady ReadinessClaim ROLE-ACTIVE ROLE-STANDBY])
(import tests.coordinator_contract_handlers [ReportSeen SetReachable READINESS])


(deftest test-the-last-report-is-kept-on-the-coordinator
  {:interpreters ["readiness-claims" "readiness-memory" "readiness-http"]}
  (<- nothing (ReportSeen READINESS))
  (<- answer (ReportReady True))
  (<- plain (ReportSeen READINESS))
  (<- (ReportReady False "書き先へ届かない" ROLE-STANDBY))
  (<- standby (ReportSeen READINESS))
  (assert (is nothing None) nothing)
  (assert (is answer None) answer)
  (assert (= plain (ReadinessClaim :ready True :reason "" :role ROLE-ACTIVE)) plain)
  (assert (= standby (ReadinessClaim :ready False :reason "書き先へ届かない" :role ROLE-STANDBY)) standby))


(deftest test-the-report-is-kept-in-the-coordinator-form
  {:interpreters ["readiness-claims" "readiness-memory" "readiness-http"]}
  (<- (ReportReady True (* "理" 400) "leader"))
  (<- seen ReadinessClaim (ReportSeen READINESS))
  (assert (= seen (ReadinessClaim :ready True :reason (* "理" 300) :role ROLE-ACTIVE))
          (.format "coordinator の形で残らない: reason {} 字・role {}" (len seen.reason) seen.role)))


(deftest test-an-unreachable-coordinator-does-not-stop-the-reporter
  {:interpreters ["readiness-claims" "readiness-memory" "readiness-http"]}
  (<- (ReportReady True "first"))
  (<- (SetReachable False))
  (<- cut-off (ReportReady False "cut off"))
  (<- (SetReachable True))
  (<- (ReportReady True "back"))
  (<- seen (ReportSeen READINESS))
  (assert (is cut-off None) cut-off)
  (assert (= seen (ReadinessClaim :ready True :reason "back" :role ROLE-ACTIVE)) seen))
