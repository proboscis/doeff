;;; 計器の断面の Prometheus の text の描き手(meter_prometheus.hy の render-prometheus・#2709)の検。
;;;
;;; 見る性質:
;;;   * helps が空なら、使い手の画面の計器の描き手と 1 byte も違わない(名の昇順・counter → gauge → 秒の観測・counter の _total・
;;;     秒の観測の _seconds_sum と _seconds_count・末尾の改行 1 つ — 画面が乗り換えられる)
;;;   * helps に在る名だけ、# TYPE の前に # HELP の行を置き、説明の \ と改行を escape する
;;;   * 空の断面は改行 1 つ
;;;   * 積んだ順に依らず、同じ断面は同じ文字列
(require doeff-hy.macros [deftest <-])
(import doeff_hy.frozen [FrozenMap])
(import doeff_core_effects.meter_effects [EMPTY-METER MeterSettings MeterSnapshot counted gauged observed])
(import doeff_core_effects.meter_prometheus [render-prometheus])


(deftest test-without-helps-the-text-matches-the-screen-meter-rendering
  ;; 画面の計器の描き手の検(使い手の repo の test_metrics.hy の test-metrics-fold-and-render)と同じ積み方・同じ字面。
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
  (<- text str (render-prometheus m4 (FrozenMap {"requests" "数えた\\要求\n2 行目" "depth" "待ちの深さ" "answer" "答えの秒"})))
  (assert (= text (+ "# TYPE refusals_total counter\nrefusals_total 0.0\n"
                     "# HELP requests_total 数えた\\\\要求\\n2 行目\n# TYPE requests_total counter\nrequests_total 2.0\n"
                     "# HELP depth 待ちの深さ\n# TYPE depth gauge\ndepth 1.0\n"
                     "# HELP answer_seconds 答えの秒\n# TYPE answer_seconds summary\nanswer_seconds_sum 0.5\nanswer_seconds_count 1\n"))
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
