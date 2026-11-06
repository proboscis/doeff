;;; Properties of only the real meter answerer process-meter-handler. Contract properties are in test_meter_contract.hy.
;;;
;;;   * answerers installed under the same name read and write one store, even across different runs or threads (the processing loop's run writes, the probe's other run reads)
;;;   * different names mean different stores
;;;   * installing different settings under the same name is refused (the same store is not written with mismatched bucket tables)
;;;   * a read from another thread never sees a half-written cross-section within one name (the inf bucket and the count are always equal)
;;;   * if the settings name a GC pause, the seconds of each collection pause are accumulated under that name
;;; The store persists for the whole test process, so each test uses a different name.
(require doeff-hy.macros [defk deftest <- val])
(import gc)
(import threading)
(import pytest)
(import doeff [run with_handlers])
(import doeff_core_effects [state])
(import doeff_core_effects [CountMetric MeterBucket MeterSettings MeterSnapshot ObserveSeconds ReadMeter])
(import doeff_core_effects [process-meter-handler])

(val SETTINGS (MeterSettings :buckets #((MeterBucket :label "le_1s" :ceiling 1.0)) :inf-label "le_inf"))


(defk count-once [metric]
  {:pre [(: metric str)] :post [(: % None)] :tags {:context "meter-test" :role "program"}}
  "Count one metric (a Program run in a different run)."
  (<- (CountMetric metric))
  None)


(defk read-meter []
  {:pre [] :post [(: % MeterSnapshot)] :tags {:context "meter-test" :role "program"}}
  "Read the meter's cross-section (a Program run in a different run or thread)."
  (<- snapshot MeterSnapshot (ReadMeter))
  snapshot)


(defk observe-many [metric times]
  {:pre [(: metric str) (: times int)] :post [(: % None)] :tags {:context "meter-test" :role "program"}}
  "Observe seconds for a metric, times times (the writer's run)."
  (for [index (range times)]
    (<- (ObserveSeconds metric (* 0.001 (% index 3000)))))
  None)


(deftest test-two-runs-with-the-same-name-share-one-meter
  (run (with_handlers [(state) (process-meter-handler "process-meter-shared" SETTINGS)] (count-once "written")))
  (val snapshot (run (with_handlers [(state) (process-meter-handler "process-meter-shared" SETTINGS)] (read-meter))))
  (assert (= (.get snapshot.counters "written") 1.0) (.format "cannot read written counted by another run: {!r}" (dict snapshot.counters))))


(deftest test-a-run-in-another-thread-reads-the-same-meter
  (run (with_handlers [(state) (process-meter-handler "process-meter-thread" SETTINGS)] (count-once "written")))
  (val seen [])
  (val reader (threading.Thread
                :target (fn [] (.append seen (run (with_handlers [(state) (process-meter-handler "process-meter-thread" SETTINGS)] (read-meter)))))))
  (.start reader)
  (.join reader 10.0)
  (assert (= (len seen) 1) "the read from another thread does not finish")
  (assert (= (.get (. (get seen 0) counters) "written") 1.0) (.format "the read from another thread was {!r}" (dict (. (get seen 0) counters)))))


(deftest test-different-names-do-not-share
  (run (with_handlers [(state) (process-meter-handler "process-meter-left" SETTINGS)] (count-once "written")))
  (val snapshot (run (with_handlers [(state) (process-meter-handler "process-meter-right" SETTINGS)] (read-meter))))
  (assert (not-in "written" snapshot.counters) (.format "written exists in a store under a different name: {!r}" (dict snapshot.counters))))


(deftest test-the-same-name-with-other-settings-is-refused
  (run (with_handlers [(state) (process-meter-handler "process-meter-settled" SETTINGS)] (count-once "written")))
  (val other (MeterSettings :buckets #((MeterBucket :label "le_2s" :ceiling 2.0)) :inf-label "le_inf"))
  (with [(pytest.raises ValueError :match "process-meter-settled")]
    (run (with_handlers [(state) (process-meter-handler "process-meter-settled" other)] (count-once "written")))))


(deftest test-a-reader-in-another-thread-never-sees-half-of-one-name
  ;; While the writer's run keeps accumulating observations under the same name, a run in another thread keeps reading and compares the inf bucket and count for each cross-section read.
  (val name "process-meter-torn")
  (val done (threading.Event))
  (val torn [])
  (val reads [0])
  (defn read-until-done []  ; defk にできない: target of threading.Thread (entry of a thread outside the VM)
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
  (assert (> (get reads 0) 0) "the read from another thread never ran")
  (assert (= torn []) (.format "read a half-written cross-section within one name {} times (count and inf bucket): {!r}" (len torn) (cut torn 5))))


(deftest test-a-collection-pause-is-observed-under-its-name
  (val settings (MeterSettings :buckets #((MeterBucket :label "le_1s" :ceiling 1.0)) :inf-label "le_inf" :gc-pause-name "gc_pause"))
  (val handler (process-meter-handler "process-meter-gc" settings))
  (run (with_handlers [(state) handler] (count-once "warm")))
  (gc.collect)
  (val snapshot (run (with_handlers [(state) handler] (read-meter))))
  (val row (.get snapshot.durations "gc_pause"))
  (assert (is-not row None) (.format "no gc_pause after collection: {!r}" (dict snapshot.durations)))
  (assert (>= row.count 1) (.format "gc_pause count was {!r}" row.count))
  (assert (= (.get snapshot.counters "gc_pause_le_inf") (float row.count)) "the bucket counter is attached to GC pauses too"))
