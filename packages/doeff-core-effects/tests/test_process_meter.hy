;;; 本物の計器の答え手 process-meter-handler だけの性質(agora-redesign #1440)。契約の性質は test_meter_contract.hy。
;;;
;;;   * 同じ名前で入れた答え手どうしは、別の run でも別の thread でも 1 つの置き場を読み書きする(処理ループの run が書き、probe の別の run が読む)
;;;   * 名前が違えば置き場も別
;;;   * 同じ名前で違う設定を入れると断る(桁の表が食い違ったまま同じ置き場を書かない)
;;;   * 別の thread の読みは、1 つの名の中が半分だけ書かれた断面を見ない(inf の桁と回数がいつも同じ)
;;;   * 設定に GC の停止の名があれば、回収 1 回の停止の秒をその名で積む
;;; 置き場は検の process の間ずっと残るので、検ごとに別の名前を使う。
(require doeff-hy.macros [defk deftest <- val])
(import gc)
(import threading)
(import pytest)
(import doeff [run with_handlers])
(import doeff_core_effects.handlers [state])
(import doeff_core_effects.meter_effects [CountMetric MeterBucket MeterSettings MeterSnapshot ObserveSeconds ReadMeter])
(import doeff_core_effects.process_meter [process-meter-handler])

(val SETTINGS (MeterSettings :buckets #((MeterBucket :label "le_1s" :ceiling 1.0)) :inf-label "le_inf"))


(defk count-once [metric]
  {:pre [(: metric str)] :post [(: % None)] :tags {:context "meter-test" :role "program"}}
  "metric を 1 つ数える(別の run で走らせる Program)。"
  (<- (CountMetric metric))
  None)


(defk read-meter []
  {:pre [] :post [(: % MeterSnapshot)] :tags {:context "meter-test" :role "program"}}
  "計器の断面を読む(別の run・別の thread で走らせる Program)。"
  (<- snapshot MeterSnapshot (ReadMeter))
  snapshot)


(defk observe-many [metric times]
  {:pre [(: metric str) (: times int)] :post [(: % None)] :tags {:context "meter-test" :role "program"}}
  "metric に times 回、秒を観る(書き手の run)。"
  (for [index (range times)]
    (<- (ObserveSeconds metric (* 0.001 (% index 3000)))))
  None)


(deftest test-two-runs-with-the-same-name-share-one-meter
  (run (with_handlers [(state) (process-meter-handler "process-meter-shared" SETTINGS)] (count-once "written")))
  (val snapshot (run (with_handlers [(state) (process-meter-handler "process-meter-shared" SETTINGS)] (read-meter))))
  (assert (= (.get snapshot.counters "written") 1.0) (.format "別の run が数えた written を読めない: {!r}" (dict snapshot.counters))))


(deftest test-a-run-in-another-thread-reads-the-same-meter
  (run (with_handlers [(state) (process-meter-handler "process-meter-thread" SETTINGS)] (count-once "written")))
  (val seen [])
  (val reader (threading.Thread
                :target (fn [] (.append seen (run (with_handlers [(state) (process-meter-handler "process-meter-thread" SETTINGS)] (read-meter)))))))
  (.start reader)
  (.join reader 10.0)
  (assert (= (len seen) 1) "別の thread の読みが終わらない")
  (assert (= (.get (. (get seen 0) counters) "written") 1.0) (.format "別の thread の読みが {!r}" (dict (. (get seen 0) counters)))))


(deftest test-different-names-do-not-share
  (run (with_handlers [(state) (process-meter-handler "process-meter-left" SETTINGS)] (count-once "written")))
  (val snapshot (run (with_handlers [(state) (process-meter-handler "process-meter-right" SETTINGS)] (read-meter))))
  (assert (not-in "written" snapshot.counters) (.format "別の名前の置き場に written が在る: {!r}" (dict snapshot.counters))))


(deftest test-the-same-name-with-other-settings-is-refused
  (run (with_handlers [(state) (process-meter-handler "process-meter-settled" SETTINGS)] (count-once "written")))
  (val other (MeterSettings :buckets #((MeterBucket :label "le_2s" :ceiling 2.0)) :inf-label "le_inf"))
  (with [(pytest.raises ValueError :match "process-meter-settled")]
    (run (with_handlers [(state) (process-meter-handler "process-meter-settled" other)] (count-once "written")))))


(deftest test-a-reader-in-another-thread-never-sees-half-of-one-name
  ;; 書き手の run が同じ名へ観測を積み続ける間、別の thread の run が読み続け、読んだ断面ごとに inf の桁と回数を比べる。
  (val name "process-meter-torn")
  (val done (threading.Event))
  (val torn [])
  (val reads [0])
  (defn read-until-done []  ; defk にできない: threading.Thread の target(VM の外の thread の入口)
    (while (not (.is-set done))
      (setv snapshot (run (with_handlers [(state) (process-meter-handler name SETTINGS)] (read-meter))))
      (setv (get reads 0) (+ (get reads 0) 1))
      (setv row (.get snapshot.durations "torn"))
      (setv inf (.get snapshot.counters "torn_le_inf"))
      (when (!= (if (is row None) 0 row.count) (if (is inf None) 0 (int inf)))
        (.append torn #((if (is row None) None row.count) inf)))))
  (val reader (threading.Thread :target read-until-done))
  (.start reader)
  (try
    (run (with_handlers [(state) (process-meter-handler name SETTINGS)] (observe-many "torn" 3000)))
    (finally
      (.set done)
      (.join reader 10.0)))
  (assert (> (get reads 0) 0) "別の thread の読みが 1 度も走らない")
  (assert (= torn []) (.format "1 つの名の中が半分だけ書かれた断面を {} 回読んだ(回数と inf の桁): {!r}" (len torn) (cut torn 5))))


(deftest test-a-collection-pause-is-observed-under-its-name
  (val settings (MeterSettings :buckets #((MeterBucket :label "le_1s" :ceiling 1.0)) :inf-label "le_inf" :gc-pause-name "gc_pause"))
  (val handler (process-meter-handler "process-meter-gc" settings))
  (run (with_handlers [(state) handler] (count-once "warm")))
  (gc.collect)
  (val snapshot (run (with_handlers [(state) handler] (read-meter))))
  (val row (.get snapshot.durations "gc_pause"))
  (assert (is-not row None) (.format "回収の後に gc_pause が無い: {!r}" (dict snapshot.durations)))
  (assert (>= row.count 1) (.format "gc_pause の回数が {!r}" row.count))
  (assert (= (.get snapshot.counters "gc_pause_le_inf") (float row.count)) "GC の停止にも桁の counter が付く"))
