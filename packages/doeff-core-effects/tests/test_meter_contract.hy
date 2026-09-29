;;; 計器の契約テスト — 同じ effect(CountMetric・ObserveSeconds・SetGauge・ReadMeter)に答える本物(process-meter-handler)と fake
;;; (memory-meter-handler)が、同じ deftest を通る(agora-redesign #1440・#1107 の決め)。解釈器の組み立ては meter_contract_handlers.hy。
;;;
;;; 見る性質:
;;;   * 何も積んでいない計器の断面は空
;;;   * 数えは足し上がり、0 を数えると名が 0 で現れる(無いことと 0 を読み手が区別しなくて済む)
;;;   * 秒の観測は合計と回数に積み、桁の表の当たる桁ごとの累積の counter と inf の counter も同じ 1 回で進む
;;;   * gauge は最後の値
;;;   * 1 つの名の中の書き(秒の合計・回数・桁の counter)は 1 度に見える(ADR-DOE-CORE-EFFECTS-004 の law meter-write-is-whole-within-one-name)
;;;   * 名をまたぐ書きは 1 度に見えるとは限らない — 2 つの名への書きの間の読みは、片方だけ進んだ断面を見る(同じ ADR の law
;;;     meter-writes-across-names-are-not-atomic の反例の検)
;;; 本物だけの性質(別の run・別の thread との共有・同じ名前で違う設定を断る・GC の停止)は test_process_meter.hy。
(require doeff-hy.macros [deftest <-])
(import doeff_core_effects.meter_effects [CountMetric MeterSnapshot ObserveSeconds ReadMeter SetGauge SecondsTotal])


(deftest test-an-untouched-meter-reads-empty
  {:interpreters ["process-meter" "memory-meter"]}
  (<- snapshot MeterSnapshot (ReadMeter))
  (assert (= (dict snapshot.counters) {}) (.format "何も積んでいない counters が {!r}" (dict snapshot.counters)))
  (assert (= (dict snapshot.gauges) {}) (.format "何も積んでいない gauges が {!r}" (dict snapshot.gauges)))
  (assert (= (dict snapshot.durations) {}) (.format "何も積んでいない durations が {!r}" (dict snapshot.durations))))


(deftest test-counts-add-up-and-a-zero-count-places-the-name
  {:interpreters ["process-meter" "memory-meter"]}
  (<- (CountMetric "requests"))
  (<- (CountMetric "requests"))
  (<- (CountMetric "requests" 0.5))
  (<- (CountMetric "refusals" 0.0))
  (<- snapshot MeterSnapshot (ReadMeter))
  (assert (= (get snapshot.counters "requests") 2.5) (.format "1・1・0.5 を数えた requests が {!r}" (get snapshot.counters "requests")))
  (assert (= (get snapshot.counters "refusals") 0.0) (.format "0 を数えた refusals が {!r}(名が 0 で現れる)" (.get snapshot.counters "refusals"))))


(deftest test-an-observation-adds-to-the-total-and-to-every-bucket-it-fits
  {:interpreters ["process-meter" "memory-meter"]}
  (<- (ObserveSeconds "answer" 0.5))
  (<- (ObserveSeconds "answer" 3.0))
  (<- (ObserveSeconds "answer" 9.0))
  (<- snapshot MeterSnapshot (ReadMeter))
  (assert (= (get snapshot.durations "answer") (SecondsTotal :total 12.5 :count 3))
          (.format "0.5・3・9 秒を観た answer の合計と回数が {!r}" (get snapshot.durations "answer")))
  (assert (= (get snapshot.counters "answer_le_1s") 1.0) (.format "1 秒以下の桁が {!r}" (.get snapshot.counters "answer_le_1s")))
  (assert (= (get snapshot.counters "answer_le_5s") 2.0) (.format "5 秒以下の桁(累積)が {!r}" (.get snapshot.counters "answer_le_5s")))
  (assert (= (get snapshot.counters "answer_le_inf") 3.0) (.format "inf の桁が {!r}" (.get snapshot.counters "answer_le_inf"))))


(deftest test-a-gauge-keeps-the-last-value
  {:interpreters ["process-meter" "memory-meter"]}
  (<- (SetGauge "pending_bytes" 1.0))
  (<- (SetGauge "pending_bytes" 4.0))
  (<- snapshot MeterSnapshot (ReadMeter))
  (assert (= (get snapshot.gauges "pending_bytes") 4.0) (.format "1 → 4 と置いた gauge が {!r}" (get snapshot.gauges "pending_bytes"))))


(deftest test-within-one-name-the-summary-and-the-buckets-move-together
  {:interpreters ["process-meter" "memory-meter"]}
  ;; 観測のたびに読み、inf の桁の数と秒の回数が同じであること(1 つの名の中の書きは 1 度に見える)。
  (for [seconds [0.2 0.7 2.0 6.0]]
    (<- (ObserveSeconds "fold" seconds))
    (<- snapshot MeterSnapshot (ReadMeter))
    (assert (= (get snapshot.counters "fold_le_inf") (float (. (get snapshot.durations "fold") count)))
            (.format "{} 秒の観測の後、inf の桁 {!r} と回数 {!r} が食い違う" seconds (get snapshot.counters "fold_le_inf")
                     (. (get snapshot.durations "fold") count)))))


(deftest test-writes-to-two-names-are-seen-one-at-a-time
  {:interpreters ["process-meter" "memory-meter"]}
  ;; 反例の検: 全数と内訳のように 2 つの名へ積む書きの間に読みが入ると、全数だけ進んだ断面が見える。計器はこれを保証しない
  ;; (名をまたいで 1 度に見せたい読み手は、1 つの名に畳むか、読んだ断面の食い違いを許す)。
  (<- (CountMetric "answers"))
  (<- between MeterSnapshot (ReadMeter))
  (<- (CountMetric "answers_entity_board"))
  (<- after MeterSnapshot (ReadMeter))
  (assert (= (get between.counters "answers") 1.0) (.format "間の読みの全数が {!r}" (.get between.counters "answers")))
  (assert (not-in "answers_entity_board" between.counters)
          (.format "間の読みに内訳がもう在る: {!r}(名をまたぐ書きが 1 度に見えている)" (dict between.counters)))
  (assert (= (get after.counters "answers_entity_board") 1.0) (.format "後の読みの内訳が {!r}" (.get after.counters "answers_entity_board"))))
