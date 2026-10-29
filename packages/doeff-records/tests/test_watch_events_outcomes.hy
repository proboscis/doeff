;; 列の待ち WatchEvents の答えの分け方(ADR-DOE-CORE-EFFECTS-003 R5・R17 の追補 — card ki-d038e0cacae5):
;; defeffect が EventsMoved = 成功 / EventsQuiet = 値 / Unreachable = 失敗(不在は無し)と宣言し、呼び手の <- は
;;   - EventsMoved を中身のまま束ねる
;;   - EventsQuiet を値のまま束ねる(Raise にも Absent にもしない — 待つ呼び手が毎回 EventsMoved と分けて扱う)
;;   - Unreachable を Raise(Unreachable) として逃がす(on-raise で値へ戻せる。戻さなければ上へ出る)
;; 答える memory の handler は今までどおり 3 つとも値で答える(R4 — 作る時の検めも変わらない)。
(require doeff-hy.macros [deftest defk <- val on-raise])
(import pytest)
(import doeff [run with_handlers])
(import doeff [Err UnhandledEffect])
(import doeff_core_effects [result])
(import doeff_core_effects [scheduled])
(import doeff_time [SimClock sim-time-handler])
(import doeff_records [Appended EventsMoved EventsQuiet Unreachable])
(import doeff_records [AppendEvent WatchEvents])
(import doeff_records [SetStoreOutage])
(import doeff_records._laws [LAW-SCHEMA MAKER])
(import doeff_records [memory-records-handler]
        doeff_records._memory [MemoryStore])

(val DETAIL "記録の service が落ちている(筋書き)")


(defn #^ object in-store [#^ MemoryStore store program]  ; defk にできない: 検の入口で run を撃つ
  "仮想の時計と memory の置き場(書き手 MAKER)の下で program を回して答えを返す。"
  (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) (memory-records-handler store MAKER)] program))))


(defk watch-once [after]
  {:pre [(: after int)] :post [(: % (| EventsMoved EventsQuiet))] :tags {:context "records" :role "program"}}
  "列 journal の頭が after より進むのを待たずに 1 回だけ見て、答えを <- で束ねて返す(Unreachable は束ねずに Raise として逃げる)。"
  (<- answer (WatchEvents "journal" :after after :timeout 0.0))
  answer)


(defk watch-once-as-value [after]
  {:pre [(: after int)] :post [(: % (| EventsMoved EventsQuiet Unreachable))] :tags {:context "records" :role "program"}}
  "watch-once の失敗を on-raise で Unreachable の値へ戻して返す(値で受けたい呼び手の形)。"
  (<- answer (on-raise (watch-once after) (Unreachable detail) (Unreachable detail)))
  answer)


(deftest test-a-quiet-wait-is-bound-as-a-value-and-a-moved-wait-as-success
  (val store (MemoryStore LAW-SCHEMA))
  ;; 空の列: 頭は進まない — EventsQuiet は Raise にも Absent にもならず、値のまま束ねられる。
  (assert (= (in-store store (watch-once 0)) (EventsQuiet)))
  (assert (isinstance (in-store store (AppendEvent "journal" "k1" {"n" 1})) Appended))
  ;; 追記の後: 頭が after より進んだ — EventsMoved が成功として束ねられる。
  (assert (= (in-store store (watch-once 0)) (EventsMoved)))
  ;; after が頭より先でも誤りにしない(静かな答え)。
  (assert (= (in-store store (watch-once 100)) (EventsQuiet))))


(deftest test-an-unreachable-wait-escapes-as-a-raise
  (val store (MemoryStore LAW-SCHEMA))
  (in-store store (SetStoreOutage DETAIL))
  ;; 束ねずに逃がす: result で畳むと Err(Unreachable) — 値として束ねた名には Unreachable が来ない。
  (val settled (in-store store (result (watch-once 0))))
  ;; Ok / Err は値で比べられない(Rust の型)ので形を repr で比べる。
  (assert (isinstance settled Err) settled)
  (assert (= (repr settled) (.format "Err({!r})" (Unreachable DETAIL))) settled)
  ;; 逃がしたままだと、受け手の無い Raise として止まる(黙って値にしない)。
  (with [(pytest.raises UnhandledEffect)]
    (in-store store (watch-once 0))))


(deftest test-on-raise-brings-an-unreachable-wait-back-to-the-value
  (val store (MemoryStore LAW-SCHEMA))
  (in-store store (SetStoreOutage DETAIL))
  (assert (= (in-store store (watch-once-as-value 0)) (Unreachable DETAIL)))
  ;; 戻すと今までどおり — 値で受けた呼び手は、届く間は EventsQuiet / EventsMoved をそのまま受ける。
  (in-store store (SetStoreOutage None))
  (assert (= (in-store store (watch-once-as-value 0)) (EventsQuiet)))
  (assert (isinstance (in-store store (AppendEvent "journal" "k1" {"n" 1})) Appended))
  (assert (= (in-store store (watch-once-as-value 0)) (EventsMoved))))


(deftest test-the-handler-still-answers-unreachable-as-a-value
  ;; R4: 答える側は値で答えたまま — <- を通さない撃ち方(in-store の直の撃ち)では Raise にならず、Unreachable の値が返る。
  (val store (MemoryStore LAW-SCHEMA))
  (in-store store (SetStoreOutage DETAIL))
  (assert (= (in-store store (WatchEvents "journal" :timeout 0.0)) (Unreachable DETAIL)))
  (assert (= (in-store store (WatchEvents "journal" :timeout 30.0)) (Unreachable DETAIL))))


(deftest test-a-wait-is-still-refused-at-creation-for-a-bad-field
  ;; 作る時の検め(欄の名・after・timeout)は defclass の時と同じ ValueError で断る。
  (for [bad [{"stream" "Bad Name"} {"stream" "journal" "after" -1} {"stream" "journal" "after" True}
             {"stream" "journal" "timeout" -1.0} {"stream" "journal" "timeout" True} {"stream" "journal" "timeout" "x"}]]
    (with [(pytest.raises ValueError)]
      (WatchEvents #** bad)))
  ;; 境の値は通る(after = 0・timeout = 0 は待たずに 1 回だけ見る)。
  (assert (= (WatchEvents "journal" :after 0 :timeout 0) (WatchEvents "journal" :after 0 :timeout 0.0))))
