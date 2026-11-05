;;; Meter contract tests - the real implementation (process-meter-handler) and the fake (memory-meter-handler), both answering the same effects (CountMetric, ObserveSeconds, ObserveSecondsBatch, SetGauge, ReadMeter),
;;; go through the same deftests (the decision of #1107). The interpreter assembly is in meter_contract_handlers.hy.
;;;
;;; Properties checked:
;;;   * The snapshot of a meter with nothing recorded is empty
;;;   * Counts add up, and counting 0 makes the name appear as 0 (readers need not distinguish absent from 0)
;;;   * An observation of seconds accumulates into the total and the count, and the cumulative counter of each matching bucket of the bucket table and the inf counter advance in the same single step
;;;   * A list of seconds observations (ObserveSecondsBatch) gives the same snapshot and the same Prometheus rendering as firing ObserveSeconds one by one in list order
;;;     (down to the floating-point rounding of the total seconds)
;;;   * A gauge is its last value
;;;   * Writes within one name (total seconds, count, bucket counters) are seen at once (law meter-write-is-whole-within-one-name of ADR-DOE-CORE-EFFECTS-004)
;;;   * Writes across names are not necessarily seen at once - a read between writes to 2 names sees a snapshot where only one of them has advanced (the test of the counterexample of the same ADR's law
;;;     meter-writes-across-names-are-not-atomic)
;;; Properties of the real one alone (sharing with another run or thread, refusing a different setting under the same name, stopping GC) are in test_process_meter.hy.
(require doeff-hy.macros [deftest defk <- val])
(import doeff_hy._frozen [FrozenMap])
(import doeff_core_effects [CountMetric MeterSnapshot ObserveSeconds ObserveSecondsBatch ReadMeter SetGauge SecondsObservation SecondsTotal])
(import doeff_core_effects._meter_prometheus [render-prometheus])
(import meter_contract_handlers [MEMORY-METER PROCESS-METER under-meter])

;; An observation comparing the batched way of recording with the one-by-one way: adding to the name answer that already exists, the same name appearing twice in the list, buckets matching and missing, seconds whose rounding
;; changes with the order of adding (0.2 and 0.3 after a preliminary 0.1 - one by one it is (0.1 + 0.2) + 0.3, and adding the list's seconds first gives 0.1 + 0.5, so the last digit of the sum differs).
(val BEFORE-BATCH (SecondsObservation :name "answer" :seconds 0.1))
(val BATCH #((SecondsObservation :name "answer" :seconds 0.2)
             (SecondsObservation :name "slow" :seconds 3.0)
             (SecondsObservation :name "answer" :seconds 0.3)
             (SecondsObservation :name "slow" :seconds 9.0)
             (SecondsObservation :name "fresh" :seconds 0.5)))


(defk observed-one-at-a-time [observations]
  {:pre [(: observations (get tuple #(SecondsObservation ...)))] :post [(: % MeterSnapshot)]
   :tags {:context "meter-test" :role "foundation"}}
  "To read the snapshot after recording observations one by one with ObserveSeconds following the preliminary observation (the baseline to compare with the batched way)."
  (<- (ObserveSeconds BEFORE-BATCH.name BEFORE-BATCH.seconds))
  (for [observation observations]
    (<- (ObserveSeconds observation.name observation.seconds)))
  (<- snapshot MeterSnapshot (ReadMeter))
  snapshot)


(defk observed-at-once [observations]
  {:pre [(: observations (get tuple #(SecondsObservation ...)))] :post [(: % MeterSnapshot)]
   :tags {:context "meter-test" :role "foundation"}}
  "To read the snapshot after recording observations in a single ObserveSecondsBatch following the preliminary observation (to compare with the one-by-one way)."
  (<- (ObserveSeconds BEFORE-BATCH.name BEFORE-BATCH.seconds))
  (<- (ObserveSecondsBatch observations))
  (<- snapshot MeterSnapshot (ReadMeter))
  snapshot)


(deftest test-an-untouched-meter-reads-empty
  {:interpreters ["process-meter" "memory-meter"]}
  (<- snapshot MeterSnapshot (ReadMeter))
  (assert (= (dict snapshot.counters) {}) (.format "counters with nothing recorded is {!r}" (dict snapshot.counters)))
  (assert (= (dict snapshot.gauges) {}) (.format "gauges with nothing recorded is {!r}" (dict snapshot.gauges)))
  (assert (= (dict snapshot.durations) {}) (.format "durations with nothing recorded is {!r}" (dict snapshot.durations))))


(deftest test-counts-add-up-and-a-zero-count-places-the-name
  {:interpreters ["process-meter" "memory-meter"]}
  (<- (CountMetric "requests"))
  (<- (CountMetric "requests"))
  (<- (CountMetric "requests" 0.5))
  (<- (CountMetric "refusals" 0.0))
  (<- snapshot MeterSnapshot (ReadMeter))
  (assert (= (get snapshot.counters "requests") 2.5) (.format "requests after counting 1, 1, 0.5 is {!r}" (get snapshot.counters "requests")))
  (assert (= (get snapshot.counters "refusals") 0.0) (.format "refusals after counting 0 is {!r} (the name appears as 0)" (.get snapshot.counters "refusals"))))


(deftest test-an-observation-adds-to-the-total-and-to-every-bucket-it-fits
  {:interpreters ["process-meter" "memory-meter"]}
  (<- (ObserveSeconds "answer" 0.5))
  (<- (ObserveSeconds "answer" 3.0))
  (<- (ObserveSeconds "answer" 9.0))
  (<- snapshot MeterSnapshot (ReadMeter))
  (assert (= (get snapshot.durations "answer") (SecondsTotal :total 12.5 :count 3))
          (.format "the total and count of answer after observing 0.5, 3, 9 seconds are {!r}" (get snapshot.durations "answer")))
  (assert (= (get snapshot.counters "answer_le_1s") 1.0) (.format "the bucket of 1 second or less is {!r}" (.get snapshot.counters "answer_le_1s")))
  (assert (= (get snapshot.counters "answer_le_5s") 2.0) (.format "the bucket of 5 seconds or less (cumulative) is {!r}" (.get snapshot.counters "answer_le_5s")))
  (assert (= (get snapshot.counters "answer_le_inf") 3.0) (.format "the inf bucket is {!r}" (.get snapshot.counters "answer_le_inf"))))


(deftest test-a-batch-leaves-the-same-meter-as-observing-one-at-a-time
  ;; For both the real and the fake, record the batched way and the one-by-one way into separate fresh meters (under-meter uses a new store on every run) and confirm that
  ;; the snapshot and the Prometheus rendering are the same. Recording into one meter with :interpreters would not let the two ways be read separately, so
  ;; call the interpreter assembly twice.
  (for [meter [PROCESS-METER MEMORY-METER]]
    (<- one-by-one MeterSnapshot (under-meter meter (observed-one-at-a-time BATCH)))
    (<- at-once MeterSnapshot (under-meter meter (observed-at-once BATCH)))
    (assert (= (get one-by-one.durations "answer") (SecondsTotal :total (+ (+ 0.1 0.2) 0.3) :count 3))
            (.format "{}: answer recorded one by one is {!r}" meter (get one-by-one.durations "answer")))
    (assert (= at-once one-by-one) (.format "{}: the batched snapshot {!r} and the one-by-one snapshot {!r} differ" meter at-once one-by-one))
    (<- batched-text str (render-prometheus at-once (FrozenMap)))
    (<- single-text str (render-prometheus one-by-one (FrozenMap)))
    (assert (= batched-text single-text) (.format "{}: the batched rendering and the one-by-one rendering differ:\n{}\n---\n{}" meter batched-text single-text))))


(deftest test-a-gauge-keeps-the-last-value
  {:interpreters ["process-meter" "memory-meter"]}
  (<- (SetGauge "pending_bytes" 1.0))
  (<- (SetGauge "pending_bytes" 4.0))
  (<- snapshot MeterSnapshot (ReadMeter))
  (assert (= (get snapshot.gauges "pending_bytes") 4.0) (.format "gauge set to 1 then 4 is {!r}" (get snapshot.gauges "pending_bytes"))))


(deftest test-within-one-name-the-summary-and-the-buckets-move-together
  {:interpreters ["process-meter" "memory-meter"]}
  ;; Read after every observation, and the inf bucket count and the seconds count are the same (writes within one name are seen at once).
  (for [seconds [0.2 0.7 2.0 6.0]]
    (<- (ObserveSeconds "fold" seconds))
    (<- snapshot MeterSnapshot (ReadMeter))
    (assert (= (get snapshot.counters "fold_le_inf") (float (. (get snapshot.durations "fold") count)))
            (.format "after the observation of {} seconds, the inf bucket {!r} and the count {!r} differ" seconds (get snapshot.counters "fold_le_inf")
                     (. (get snapshot.durations "fold") count)))))


(deftest test-writes-to-two-names-are-seen-one-at-a-time
  {:interpreters ["process-meter" "memory-meter"]}
  ;; Test of the counterexample: if a read comes between writes to 2 names, such as a total and a breakdown, a snapshot where only the total has advanced is seen. The meter does not guarantee this
  ;; (a reader who wants it seen at once across names folds it into one name, or tolerates the mismatch in the snapshot it read).
  (<- (CountMetric "answers"))
  (<- between MeterSnapshot (ReadMeter))
  (<- (CountMetric "answers_entity_board"))
  (<- after MeterSnapshot (ReadMeter))
  (assert (= (get between.counters "answers") 1.0) (.format "the total in the read between is {!r}" (.get between.counters "answers")))
  (assert (not-in "answers_entity_board" between.counters)
          (.format "the read between already has the breakdown: {!r} (the write across names is seen at once)" (dict between.counters)))
  (assert (= (get after.counters "answers_entity_board") 1.0) (.format "the breakdown in the later read is {!r}" (.get after.counters "answers_entity_board"))))
