;;; Tests for the Prometheus text renderer of the meter snapshot (render-prometheus in meter_prometheus.hy, #2709).
;;;
;;; Properties checked:
;;;   * when helps is empty, the output differs by not even 1 byte from the consumer's screen meter renderer (names ascending, counter -> gauge -> seconds observation, counter _total,
;;;     seconds observation _seconds_sum and _seconds_count, one trailing newline -- the screen can switch over)
;;;   * only names present in helps get a # HELP line before # TYPE, with \ and newlines in the description escaped
;;;   * an empty snapshot is a single newline
;;;   * the same snapshot gives the same string regardless of accumulation order
(require doeff-hy.macros [deftest <-])
(import doeff_hy._frozen [FrozenMap])
(import doeff_core_effects [MeterSettings MeterSnapshot]
        doeff_core_effects._meter_effects [EMPTY-METER counted gauged observed])
(import doeff_core_effects._meter_prometheus [render-prometheus])


(deftest test-without-helps-the-text-matches-the-screen-meter-rendering
  ;; Same accumulation and same literals as the screen meter renderer test (test-metrics-fold-and-render in the consumer repo's test_metrics.hy).
  (<- m1 MeterSnapshot (counted EMPTY-METER "loop_reconcile" 1.0))
  (<- m2 MeterSnapshot (counted m1 "loop_reconcile" 2.0))
  (<- m3 MeterSnapshot (gauged m2 "loop_queue_depth" 4.0))
  (<- m4 MeterSnapshot (observed m3 (MeterSettings) "loop_reconcile" 0.5))
  (<- m5 MeterSnapshot (observed m4 (MeterSettings) "loop_reconcile" 0.25))
  (<- text str (render-prometheus m5 (FrozenMap)))
  (assert (= text (+ "# TYPE loop_reconcile_total counter\nloop_reconcile_total 3.0\n"
                     "# TYPE loop_queue_depth gauge\nloop_queue_depth 4.0\n"
                     "# TYPE loop_reconcile_seconds summary\nloop_reconcile_seconds_sum 0.75\nloop_reconcile_seconds_count 2\n"))
          text))


(deftest test-helps-add-escaped-help-lines-only-for-named-series
  (<- m1 MeterSnapshot (counted EMPTY-METER "requests" 2.0))
  (<- m2 MeterSnapshot (counted m1 "refusals" 0.0))
  (<- m3 MeterSnapshot (gauged m2 "depth" 1.0))
  (<- m4 MeterSnapshot (observed m3 (MeterSettings) "answer" 0.5))
  (<- text str (render-prometheus m4 (FrozenMap {"requests" "counted\\requests\nline 2" "depth" "queue depth" "answer" "answer seconds"})))
  (assert (= text (+ "# TYPE refusals_total counter\nrefusals_total 0.0\n"
                     "# HELP requests_total counted\\\\requests\\nline 2\n# TYPE requests_total counter\nrequests_total 2.0\n"
                     "# HELP depth queue depth\n# TYPE depth gauge\ndepth 1.0\n"
                     "# HELP answer_seconds answer seconds\n# TYPE answer_seconds summary\nanswer_seconds_sum 0.5\nanswer_seconds_count 1\n"))
          text))


(deftest test-an-empty-snapshot-renders-one-newline
  (<- text str (render-prometheus EMPTY-METER (FrozenMap)))
  (assert (= text "\n") text))


(deftest test-the-text-does-not-depend-on-the-order-of-counting
  (<- a1 MeterSnapshot (counted EMPTY-METER "b" 1.0))
  (<- a2 MeterSnapshot (counted a1 "a" 1.0))
  (<- b1 MeterSnapshot (counted EMPTY-METER "a" 1.0))
  (<- b2 MeterSnapshot (counted b1 "b" 1.0))
  (<- forward str (render-prometheus a2 (FrozenMap)))
  (<- backward str (render-prometheus b2 (FrozenMap)))
  (assert (= forward backward) #(forward backward))
  (assert (.startswith forward "# TYPE a_total counter\n") forward))
