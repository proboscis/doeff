;; 行の束の書き PutRows の答えの分け方(ADR-DOE-CORE-EFFECTS-003 R5・R17 の追補):
;; defeffect が WrittenRows = 成功 / RowsConflict = 値 / RowsRefused = 失敗 / Unreachable = 失敗(不在は無し)と宣言し、呼び手の <- は
;;   - WrittenRows を中身のまま束ねる
;;   - RowsConflict を値のまま束ねる(Raise にも Absent にもしない — 呼び手が読み直して撃ち直す版の負け)
;;   - RowsRefused を Raise(RowsRefused) として逃がす(束のある行を宣言が許さない・同じ冪等キーの別の束 — on-raise で値へ戻せる)
;;   - Unreachable を Raise(Unreachable) として逃がす(置き場に届かない — on-raise で値へ戻せる)
;; 答える memory の handler は今までどおり 4 つとも値で答える(R4 — 作る時の検めも変わらない)。
(require doeff-hy.macros [deftest defk <- val on-raise])
(import pytest)
(import doeff [run with_handlers])
(import doeff [Err Ok UnhandledEffect])
(import doeff_hy._frozen [FrozenMap])
(import doeff_core_effects [result])
(import doeff_core_effects [scheduled])
(import doeff_time [SimClock sim-time-handler])
(import doeff_records [ExpectAbsent ExpectVersion Row RowsConflict RowsRefused Unreachable WrittenRows])
(import doeff_records [PutRows ReadRow RowWrite])
(import doeff_records [SetStoreOutage])
(import doeff_records._laws [LAW-SCHEMA MAKER])
(import doeff_records [MemoryStore memory-records-handler])

(val DETAIL "記録の service が落ちている(筋書き)")


(defn #^ object in-store [#^ MemoryStore store program]  ; defk にできない: 検の入口で run を撃つ
  "仮想の時計と memory の置き場(書き手 MAKER)の下で program を回して答えを返す。"
  (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) (memory-records-handler store MAKER)] program))))


(defn #^ PutRows batch [#^ str id #^ object value #^ object expect * [idempotency-key None]]  ; defk にできない: 検の支度が同期に組む値
  "表 parts の 1 行の束(行 id を value の差分で expect の期待のもとに書く)。"
  (PutRows #((RowWrite "parts" #(id) (FrozenMap value) expect)) :idempotency-key idempotency-key))


(defk put-batch [ask]
  {:pre [(: ask PutRows)] :post [(: % (| WrittenRows RowsConflict))] :tags {:context "records" :role "program"}}
  "束 ask を撃ち、答えを <- で束ねて返す(RowsRefused・Unreachable は束ねずに Raise として逃げる)。"
  (<- answer ask)
  answer)


(defk put-batch-as-value [ask]
  {:pre [(: ask PutRows)] :post [(: % (| WrittenRows RowsConflict RowsRefused Unreachable))] :tags {:context "records" :role "program"}}
  "put-batch の失敗を on-raise で RowsRefused・Unreachable の値へ戻して返す(値で受けたい呼び手の形)。"
  (<- answer (on-raise (put-batch ask)
                       (RowsRefused index table key reason) (RowsRefused index table key reason)
                       (Unreachable detail) (Unreachable detail)))
  answer)


(deftest test-the-declaration-names-written-rows-as-success-a-conflict-as-a-value-and-the-rest-as-failures
  ;; 割り当ての表: 成功 = WrittenRows / 不在 = 無し / 失敗 = RowsRefused・Unreachable / 値 = RowsConflict。
  (val outcomes PutRows.__doeff_outcomes__)
  (assert (= outcomes.success #(WrittenRows)) outcomes)
  (assert (= outcomes.absent #()) outcomes)
  (assert (= outcomes.failure #(RowsRefused Unreachable)) outcomes)
  (assert (= outcomes.value #(RowsConflict)) outcomes))


(deftest test-written-rows-are-bound-as-success-and-a-replay-answers-the-same-batch
  (val store (MemoryStore LAW-SCHEMA))
  (val first (in-store store (put-batch (batch "p1" {"label" "a"} (ExpectAbsent) :idempotency-key "k1"))))
  (assert (isinstance first WrittenRows) first)
  (assert (= (len first.items) 1) first)
  ;; 同じ冪等キー・同じ束の送り直しは前の答え — 期待は見ず(行はもう在る)、失敗にも値の分けにもならず、成功のまま束ねられる。
  (val again (in-store store (put-batch (batch "p1" {"label" "a"} (ExpectAbsent) :idempotency-key "k1"))))
  (assert (isinstance again WrittenRows) again)
  (assert (is again.replayed True) again)
  (assert (= (. (get again.items 0) version) (. (get first.items 0) version)) again))


(deftest test-a-version-lost-bundle-is-bound-as-a-value
  (val store (MemoryStore LAW-SCHEMA))
  (in-store store (put-batch (batch "p1" {"label" "a"} (ExpectAbsent))))
  ;; 期待の版が今の行と合わない束: RowsConflict は Raise にも Absent にもならず、値のまま束ねられる(束は 1 行も書かない)。
  (val lost (in-store store (put-batch (batch "p1" {"label" "b"} (ExpectVersion 7)))))
  (assert (isinstance lost RowsConflict) lost)
  (assert (= #(lost.index lost.table lost.key) #(0 "parts" #("p1"))) lost)
  ;; result で畳んでも失敗にならない(Ok のまま)。
  (assert (isinstance (in-store store (result (put-batch (batch "p1" {"label" "b"} (ExpectVersion 7))))) Ok))
  ;; 値の中の今の行から読み直して撃ち直せば通る。
  (val retried (in-store store (put-batch (batch "p1" {"label" "b"} (ExpectVersion lost.current.version)))))
  (assert (isinstance retried WrittenRows) retried)
  (assert (= (. (in-store store (ReadRow "parts" #("p1"))) value) (FrozenMap {"id" "p1" "label" "b" "state" "open"}))))


(deftest test-a-refused-bundle-escapes-as-a-raise
  (val store (MemoryStore LAW-SCHEMA))
  ;; 状態の語 weird は宣言の外 — 束のある行を宣言が許さない。束ねずに逃がす: result で畳むと Err(RowsRefused)。
  (val refused (in-store store (batch "p1" {"state" "weird"} (ExpectAbsent))))
  (assert (isinstance refused RowsRefused) refused)
  (val settled (in-store store (result (put-batch (batch "p1" {"state" "weird"} (ExpectAbsent))))))
  ;; Ok / Err は値で比べられない(Rust の型)ので、直の撃ちの答え(値)と repr で比べる。
  (assert (isinstance settled Err) settled)
  (assert (= (repr settled) (.format "Err({!r})" refused)) settled)
  ;; 逃がしたままだと、受け手の無い Raise として止まる(黙って値にしない)。
  (with [(pytest.raises UnhandledEffect)]
    (in-store store (put-batch (batch "p1" {"state" "weird"} (ExpectAbsent)))))
  (assert (= (in-store store (ReadRow "parts" #("p1"))) (in-store store (ReadRow "parts" #("nothing")))) "断られた束が行を書いた")
  ;; 同じ冪等キーの別の束も断り(束の 0 行目の RowsRefused)で、Raise として逃げる。
  (in-store store (put-batch (batch "p2" {"label" "a"} (ExpectAbsent) :idempotency-key "k1")))
  (val other (batch "p2" {"label" "z"} (ExpectAbsent) :idempotency-key "k1"))
  (val keyed (in-store store (result (put-batch other))))
  (assert (isinstance keyed Err) keyed)
  (assert (= (repr keyed) (.format "Err({!r})" (in-store store other))) keyed))


(deftest test-an-unreachable-bundle-escapes-as-a-raise
  (val store (MemoryStore LAW-SCHEMA))
  (in-store store (SetStoreOutage DETAIL))
  (val settled (in-store store (result (put-batch (batch "p1" {"label" "a"} (ExpectAbsent))))))
  (assert (isinstance settled Err) settled)
  (assert (= (repr settled) (.format "Err({!r})" (Unreachable DETAIL))) settled)
  (with [(pytest.raises UnhandledEffect)]
    (in-store store (put-batch (batch "p1" {"label" "a"} (ExpectAbsent)))))
  (assert (= store.head 0) "届かない間の書きが変更の列に積まれた"))


(deftest test-on-raise-brings-the-failures-back-to-the-values
  (val store (MemoryStore LAW-SCHEMA))
  (assert (isinstance (in-store store (put-batch-as-value (batch "p1" {"label" "a"} (ExpectAbsent)))) WrittenRows))
  ;; 値の分け(版の負け)は on-raise を通っても RowsConflict のまま。
  (assert (isinstance (in-store store (put-batch-as-value (batch "p1" {"label" "b"} (ExpectVersion 7)))) RowsConflict))
  (assert (isinstance (in-store store (put-batch-as-value (batch "p1" {"state" "weird"} (ExpectVersion 1)))) RowsRefused))
  (in-store store (SetStoreOutage DETAIL))
  (assert (= (in-store store (put-batch-as-value (batch "p2" {"label" "a"} (ExpectAbsent)))) (Unreachable DETAIL)))
  ;; 戻すと今までどおり — 値で受けた呼び手は、届く間は WrittenRows をそのまま受ける。
  (in-store store (SetStoreOutage None))
  (assert (isinstance (in-store store (put-batch-as-value (batch "p2" {"label" "a"} (ExpectAbsent)))) WrittenRows)))


(deftest test-the-handler-still-answers-every-outcome-as-a-value
  ;; R4: 答える側は値で答えたまま — <- を通さない撃ち方(in-store の直の撃ち)では Raise にならず、4 つとも値が返る。
  (val store (MemoryStore LAW-SCHEMA))
  (assert (isinstance (in-store store (batch "p1" {"label" "a"} (ExpectAbsent))) WrittenRows))
  (assert (isinstance (in-store store (batch "p1" {"label" "b"} (ExpectVersion 7))) RowsConflict))
  (assert (isinstance (in-store store (batch "p1" {"state" "weird"} (ExpectVersion 1))) RowsRefused))
  (in-store store (SetStoreOutage DETAIL))
  (assert (= (in-store store (batch "p2" {"label" "a"} (ExpectAbsent))) (Unreachable DETAIL))))


(deftest test-a-batch-is-still-checked-at-creation
  ;; 作る時の検めは defclass の時と同じ型と文言で断る(冪等キー・束の中身・同じ行の 2 度)。
  (val one (RowWrite "parts" #("p1") (FrozenMap {"label" "a"}) (ExpectAbsent)))
  (for [bad-key ["" 3 (* "k" 1000)]]
    (with [(pytest.raises ValueError)]
      (PutRows #(one) :idempotency-key bad-key)))
  (for [bad-writes [#() [one] "p1" None #(3)]]
    (with [(pytest.raises TypeError)]
      (PutRows bad-writes)))
  (with [(pytest.raises ValueError)]
    (PutRows #(one one)))
  ;; 欄・既定値・等しさ・凍結は今までどおり。
  (assert (is (. (PutRows #(one)) idempotency-key) None))
  (assert (= (PutRows #(one) :idempotency-key "k1") (PutRows #(one) :idempotency-key "k1")))
  (assert (= (hash (PutRows #(one))) (hash (PutRows #(one)))))
  (with [(pytest.raises Exception)]
    (setattr (PutRows #(one)) "idempotency_key" "other")))
