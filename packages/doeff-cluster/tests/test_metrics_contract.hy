;;; 計器の報告の契約テスト — 同じ effect ReportMetrics に答える本物(metrics-http → coordinator)と fake(metrics-memory)が、
;;; 同じ deftest を通る。解釈器の組み立ては coordinator_contract_handlers.hy。
;;;
;;;   * 答えはいつも None・coordinator の側に最後に残るのは最後に報告した計器(同じ形 — 欠けた counters / gauges / durations は空)
;;;   * coordinator が断る形の計器(名か値が正しくない)も答えは None で、残るのはその前の報告
;;;   * coordinator へ届かない間も答えは None(業務を止めない)・届くようになった後の報告は残る
;;; 報告の送り手の世代(worker・pid・版)は本物だけが載せる欄なので契約の外(test_resources.hy)。
(require doeff-hy.macros [deftest <- val])
(import doeff_cluster.shared.intent.metrics_model [ReportMetrics])
(import tests.coordinator_contract_handlers [ReportSeen SetReachable METRICS])

(val FIRST {"counters" {"writes" 3.0} "gauges" {"queue_depth" 2.0} "durations" {"write_seconds" {"sum" 1.5 "count" 3}}})
(val SECOND {"counters" {"writes" 5.0} "gauges" {"queue_depth" 0.0} "durations" {"write_seconds" {"sum" 2.0 "count" 5}}})


(deftest test-the-last-report-is-kept-on-the-coordinator-in-the-same-form
  {:interpreters ["metrics-memory" "metrics-http"]}
  (<- nothing (ReportSeen METRICS))
  (<- answer (ReportMetrics FIRST))
  (<- first (ReportSeen METRICS))
  (<- (ReportMetrics SECOND))
  (<- second (ReportSeen METRICS))
  (<- (ReportMetrics {"counters" {"writes" 6.0}}))
  (<- partial (ReportSeen METRICS))
  (assert (is nothing None) nothing)
  (assert (is answer None) answer)
  (assert (= first FIRST) first)
  (assert (= second SECOND) second)
  (assert (= partial {"counters" {"writes" 6.0} "gauges" {} "durations" {}})
          (.format "欠けた族を空にした形で残らない: {}" partial)))


(deftest test-a-refused-report-answers-none-and-leaves-the-previous-one
  {:interpreters ["metrics-memory" "metrics-http"]}
  (<- (ReportMetrics FIRST))
  (<- bad-value (ReportMetrics {"gauges" {"queue_depth" "many"}}))
  (<- bad-name (ReportMetrics {"counters" {"has space" 1.0}}))
  (<- bad-shape (ReportMetrics {"counters" [1 2]}))
  (<- seen (ReportSeen METRICS))
  (assert (= #(bad-value bad-name bad-shape) #(None None None)))
  (assert (= seen FIRST) (.format "断られる形の報告が残った: {}" seen)))


(deftest test-an-unreachable-coordinator-does-not-stop-the-reporter
  {:interpreters ["metrics-memory" "metrics-http"]}
  (<- (ReportMetrics FIRST))
  (<- (SetReachable False))
  (<- cut-off (ReportMetrics SECOND))
  (<- (SetReachable True))
  (<- (ReportMetrics FIRST))
  (<- seen (ReportSeen METRICS))
  (assert (is cut-off None) cut-off)
  (assert (= seen FIRST) seen))
