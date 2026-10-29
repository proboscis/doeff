;; 追記の列の読み ReadEvents の答えの分け方(ADR-DOE-CORE-EFFECTS-003 R5・R17 の追補 — card ki-60ce852440ca):
;; defeffect が Events = 成功 / Unreachable = 失敗 / NotIndexed = 失敗(不在と値は無し)と宣言し、呼び手の <- は
;;   - Events を中身のまま束ねる
;;   - Unreachable を Raise(Unreachable) として逃がす(on-raise で値へ戻せる。戻さなければ上へ出る)
;;   - NotIndexed を Raise(NotIndexed) として逃がす(key-lookup を宣言していない列への頭の読み — 続きが無い誤り)
;; 答える memory の handler は今までどおり 3 つとも値で答える(R4 — 作る時の検めも変わらない)。
(require doeff-hy.macros [deftest defk <- val on-raise])
(import pytest)
(import doeff [run with_handlers])
(import doeff [Err UnhandledEffect])
(import doeff_core_effects [result])
(import doeff_core_effects [scheduled])
(import doeff_time [SimClock sim-time-handler])
(import doeff_records [Appended Events NotIndexed Unreachable])
(import doeff_records [AppendEvent ReadEvents])
(import doeff_records [SetStoreOutage])
(import doeff_records._laws [LAW-SCHEMA MAKER])
(import doeff_records.memory [MemoryStore memory-records-handler])

(val DETAIL "記録の service が落ちている(筋書き)")


(defn #^ object in-store [#^ MemoryStore store program]  ; defk にできない: 検の入口で run を撃つ
  "仮想の時計と memory の置き場(書き手 MAKER)の下で program を回して答えを返す。"
  (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) (memory-records-handler store MAKER)] program))))


(defk read-events [stream key-head]
  {:pre [(: stream str) (: key-head (| str None))] :post [(: % Events)] :tags {:context "records" :role "program"}}
  "列 stream を頭から読み、答えを <- で束ねて返す(Unreachable と NotIndexed は束ねずに Raise として逃げる)。"
  (<- answer (ReadEvents stream :key-head key-head))
  answer)


(defk read-events-as-value [stream key-head]
  {:pre [(: stream str) (: key-head (| str None))] :post [(: % (| Events Unreachable NotIndexed))] :tags {:context "records" :role "program"}}
  "read-events の失敗を on-raise で値へ戻して返す(値で受けたい呼び手の形)。"
  (<- answer (on-raise (read-events stream key-head)
                       (Unreachable detail) (Unreachable detail)
                       (NotIndexed fields) (NotIndexed fields)))
  answer)


(deftest test-events-are-bound-as-the-success-answer
  (val store (MemoryStore LAW-SCHEMA))
  ;; 空の列: Events(items = 空)がそのまま束ねられる(不在にしない — Absent にならない)。
  (val empty (in-store store (read-events "journal" None)))
  (assert (isinstance empty Events) empty)
  (assert (= empty.items #()) empty)
  (assert (isinstance (in-store store (AppendEvent "journal" "k1" {"n" 1})) Appended))
  (val one (in-store store (read-events "journal" None)))
  (assert (isinstance one Events) one)
  (assert (= (len one.items) 1) one))


(deftest test-an-unreachable-read-escapes-as-a-raise
  (val store (MemoryStore LAW-SCHEMA))
  (in-store store (SetStoreOutage DETAIL))
  ;; 束ねずに逃がす: result で畳むと Err(Unreachable) — 値として束ねた名には Unreachable が来ない。
  (val settled (in-store store (result (read-events "journal" None))))
  ;; Ok / Err は値で比べられない(Rust の型)ので形を repr で比べる。
  (assert (isinstance settled Err) settled)
  (assert (= (repr settled) (.format "Err({!r})" (Unreachable DETAIL))) settled)
  ;; 逃がしたままだと、受け手の無い Raise として止まる(黙って値にしない)。
  (with [(pytest.raises UnhandledEffect)]
    (in-store store (read-events "journal" None))))


(deftest test-a-key-head-read-of-an-undeclared-stream-escapes-as-a-raise
  (val store (MemoryStore LAW-SCHEMA))
  ;; journal は key-lookup を宣言していない列 — 頭の読みの NotIndexed は失敗として Raise(NotIndexed) になる。
  (val settled (in-store store (result (read-events "journal" "a"))))
  (assert (isinstance settled Err) settled)
  (assert (= (repr settled) (.format "Err({!r})" (NotIndexed #("idempotencyKey")))) settled)
  (with [(pytest.raises UnhandledEffect)]
    (in-store store (read-events "journal" "a")))
  ;; key-lookup を宣言した列 trails への頭の読みは Events のまま束ねられる。
  (assert (isinstance (in-store store (AppendEvent "trails" "a#1" {"n" 1})) Appended))
  (val read (in-store store (read-events "trails" "a")))
  (assert (isinstance read Events) read)
  (assert (= (len read.items) 1) read))


(deftest test-on-raise-brings-both-failures-back-to-the-value
  (val store (MemoryStore LAW-SCHEMA))
  (assert (= (in-store store (read-events-as-value "journal" "a")) (NotIndexed #("idempotencyKey"))))
  (in-store store (SetStoreOutage DETAIL))
  (assert (= (in-store store (read-events-as-value "journal" None)) (Unreachable DETAIL)))
  ;; 戻すと今までどおり — 値で受けた呼び手は、届く間は Events をそのまま受ける。
  (in-store store (SetStoreOutage None))
  (assert (isinstance (in-store store (read-events-as-value "journal" None)) Events)))


(deftest test-the-handler-still-answers-the-failures-as-values
  ;; R4: 答える側は値で答えたまま — <- を通さない撃ち方(in-store の直の撃ち)では Raise にならず、値が返る。
  (val store (MemoryStore LAW-SCHEMA))
  (assert (= (in-store store (ReadEvents "journal" :key-head "a")) (NotIndexed #("idempotencyKey"))))
  (in-store store (SetStoreOutage DETAIL))
  (assert (= (in-store store (ReadEvents "journal")) (Unreachable DETAIL))))


(deftest test-a-read-is-still-refused-at-creation-for-a-bad-field
  ;; 作る時の検め(欄の名・after・limit・key_head)は defclass の時と同じ ValueError で断る。
  (for [bad [{"stream" "Bad Name"} {"stream" "journal" "after" -1} {"stream" "journal" "after" True}
             {"stream" "journal" "limit" 0} {"stream" "journal" "limit" True} {"stream" "journal" "limit" "x"}
             {"stream" "journal" "key_head" ""} {"stream" "journal" "key_head" 3}]]
    (with [(pytest.raises ValueError)]
      (ReadEvents #** bad)))
  ;; 境の値は通る(after = 0・key_head = None は既定と同じ)。
  (assert (= (ReadEvents "journal" :after 0 :key-head None) (ReadEvents "journal"))))
