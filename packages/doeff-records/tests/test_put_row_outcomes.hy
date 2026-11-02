;; 行の書き PutRow の答えの分け方(ADR-DOE-CORE-EFFECTS-003 R5・R17):
;; defeffect が Written = 成功 / Conflict = 値 / Refused = 失敗 / Unreachable = 失敗(不在は無し)と宣言し、呼び手の <- は
;;   - Written を中身のまま束ねる
;;   - Conflict を値のまま束ねる(Raise にも Absent にもしない — 呼び手が読み直して撃ち直す版の負け)
;;   - Refused を Raise(Refused) として逃がす(宣言が許さない書き・同じ冪等キーの別の本文 — on-raise で値へ戻せる)
;;   - Unreachable を Raise(Unreachable) として逃がす(置き場に届かない — on-raise で値へ戻せる)
;; 答える memory の handler は今までどおり 4 つとも値で答える(R4 — 作る時の検めも変わらない)。表ごとの行の型の層 put-typed は
;; この宣言を通り、TypedWritten | TypedConflict を返して Refused・Unreachable は Raise で逃がす。
(require doeff-hy.macros [deftest defk <- val on-raise])
(import dataclasses)
(import pytest)
(import doeff [run with_handlers])
(import doeff [Err Ok UnhandledEffect])
(import doeff_hy._frozen [FrozenMap])
(import doeff_core_effects [result])
(import doeff_core_effects [scheduled])
(import doeff_time [SimClock sim-time-handler])
(import doeff_records [Conflict ExpectAbsent ExpectAny ExpectVersion Missing Refused Row Unreachable Written])
(import doeff_records [PutRow ReadRow])
(import doeff_records [SetStoreOutage])
(import doeff_records._laws [LAW-SCHEMA MAKER])
(import doeff_records [MemoryStore memory-records-handler])

(val DETAIL "記録の service が落ちている(筋書き)")


(defn #^ object in-store [#^ MemoryStore store program]  ; defk にできない: 検の入口で run を撃つ
  "仮想の時計と memory の置き場(書き手 MAKER)の下で program を回して答えを返す。"
  (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) (memory-records-handler store MAKER)] program))))


(defn #^ PutRow put [#^ str id #^ object value #^ object expect * [idempotency-key None]]  ; defk にできない: 検の支度が同期に組む値
  "表 parts の行 id を value の差分で expect の期待のもとに書く PutRow。"
  (PutRow "parts" #(id) (FrozenMap value) expect :idempotency-key idempotency-key))


(defk put-one [ask]
  {:pre [(: ask PutRow)] :post [(: % (| Written Conflict))] :tags {:context "records" :role "program"}}
  "書き ask を撃ち、答えを <- で束ねて返す(Refused・Unreachable は束ねずに Raise として逃げる)。"
  (<- answer ask)
  answer)


(defk put-one-as-value [ask]
  {:pre [(: ask PutRow)] :post [(: % (| Written Conflict Refused Unreachable))] :tags {:context "records" :role "program"}}
  "put-one の失敗を on-raise で Refused・Unreachable の値へ戻して返す(値で受けたい呼び手の形)。"
  (<- answer (on-raise (put-one ask)
                       (Refused reason) (Refused reason)
                       (Unreachable detail) (Unreachable detail)))
  answer)


(deftest test-the-declaration-names-written-as-success-a-conflict-as-a-value-and-the-rest-as-failures
  ;; 割り当ての表: 成功 = Written / 不在 = 無し / 失敗 = Refused・Unreachable / 値 = Conflict。
  (val outcomes PutRow.__doeff_outcomes__)
  (assert (= outcomes.success #(Written)) outcomes)
  (assert (= outcomes.absent #()) outcomes)
  (assert (= outcomes.failure #(Refused Unreachable)) outcomes)
  (assert (= outcomes.value #(Conflict)) outcomes))


(deftest test-a-written-row-is-bound-as-success-and-a-replay-answers-the-same-write
  (val store (MemoryStore LAW-SCHEMA))
  (val first (in-store store (put-one (put "p1" {"label" "a"} (ExpectAbsent) :idempotency-key "k1"))))
  (assert (isinstance first Written) first)
  (assert (= first.version 1) first)
  ;; 同じ冪等キー・同じ本文の送り直しは前の答え — 期待は見ず(行はもう在る)、失敗にも値の分けにもならず、成功のまま束ねられる。
  (val again (in-store store (put-one (put "p1" {"label" "a"} (ExpectAbsent) :idempotency-key "k1"))))
  (assert (isinstance again Written) again)
  (assert (is again.replayed True) again)
  (assert (= again.version first.version) again))


(deftest test-a-version-lost-write-is-bound-as-a-value
  (val store (MemoryStore LAW-SCHEMA))
  (in-store store (put-one (put "p1" {"label" "a"} (ExpectAbsent))))
  ;; 期待の版が今の行と合わない書き: Conflict は Raise にも Absent にもならず、値のまま束ねられる(行は変えない)。
  (val lost (in-store store (put-one (put "p1" {"label" "b"} (ExpectVersion 7)))))
  (assert (isinstance lost Conflict) lost)
  (assert (isinstance lost.current Row) lost)
  (assert (= lost.current.version 1) lost)
  ;; 行が無いのに版を期待した書きの Conflict の今の行は Missing(これも値のまま)。
  (val missing (in-store store (put-one (put "p9" {"label" "z"} (ExpectVersion 3)))))
  (assert (isinstance missing Conflict) missing)
  (assert (isinstance missing.current Missing) missing)
  ;; result で畳んでも失敗にならない(Ok のまま)。
  (assert (isinstance (in-store store (result (put-one (put "p1" {"label" "b"} (ExpectVersion 7))))) Ok))
  ;; 値の中の今の行から読み直して撃ち直せば通る。
  (val retried (in-store store (put-one (put "p1" {"label" "b"} (ExpectVersion lost.current.version)))))
  (assert (isinstance retried Written) retried)
  (assert (= (. (in-store store (ReadRow "parts" #("p1"))) value) (FrozenMap {"id" "p1" "label" "b" "state" "open"}))))


(deftest test-a-refused-write-escapes-as-a-raise
  (val store (MemoryStore LAW-SCHEMA))
  ;; 状態の語 weird は宣言の外 — 宣言が許さない書き。束ねずに逃がす: result で畳むと Err(Refused)。
  (val refused (in-store store (put "p1" {"state" "weird"} (ExpectAbsent))))
  (assert (isinstance refused Refused) refused)
  (val settled (in-store store (result (put-one (put "p1" {"state" "weird"} (ExpectAbsent))))))
  ;; Ok / Err は値で比べられない(Rust の型)ので、直の撃ちの答え(値)と repr で比べる。
  (assert (isinstance settled Err) settled)
  (assert (= (repr settled) (.format "Err({!r})" refused)) settled)
  ;; 逃がしたままだと、受け手の無い Raise として止まる(黙って値にしない)。
  (with [(pytest.raises UnhandledEffect)]
    (in-store store (put-one (put "p1" {"state" "weird"} (ExpectAbsent)))))
  (assert (= (in-store store (ReadRow "parts" #("p1"))) (in-store store (ReadRow "parts" #("nothing")))) "断られた書きが行を書いた")
  ;; 同じ冪等キーの別の本文も断り(Refused)で、Raise として逃げる。
  (in-store store (put-one (put "p2" {"label" "a"} (ExpectAbsent) :idempotency-key "k1")))
  (val other (put "p2" {"label" "z"} (ExpectAbsent) :idempotency-key "k1"))
  (val keyed (in-store store (result (put-one other))))
  (assert (isinstance keyed Err) keyed)
  (assert (= (repr keyed) (.format "Err({!r})" (in-store store other))) keyed))


(deftest test-an-unreachable-write-escapes-as-a-raise
  (val store (MemoryStore LAW-SCHEMA))
  (in-store store (SetStoreOutage DETAIL))
  (val settled (in-store store (result (put-one (put "p1" {"label" "a"} (ExpectAbsent))))))
  (assert (isinstance settled Err) settled)
  (assert (= (repr settled) (.format "Err({!r})" (Unreachable DETAIL))) settled)
  (with [(pytest.raises UnhandledEffect)]
    (in-store store (put-one (put "p1" {"label" "a"} (ExpectAbsent)))))
  (assert (= store.head 0) "届かない間の書きが変更の列に積まれた"))


(deftest test-on-raise-brings-the-failures-back-to-the-values
  (val store (MemoryStore LAW-SCHEMA))
  (assert (isinstance (in-store store (put-one-as-value (put "p1" {"label" "a"} (ExpectAbsent)))) Written))
  ;; 値の分け(版の負け)は on-raise を通っても Conflict のまま。
  (assert (isinstance (in-store store (put-one-as-value (put "p1" {"label" "b"} (ExpectVersion 7)))) Conflict))
  (assert (isinstance (in-store store (put-one-as-value (put "p1" {"state" "weird"} (ExpectAny)))) Refused))
  (in-store store (SetStoreOutage DETAIL))
  (assert (= (in-store store (put-one-as-value (put "p2" {"label" "a"} (ExpectAbsent)))) (Unreachable DETAIL)))
  ;; 戻すと今までどおり — 値で受けた呼び手は、届く間は Written をそのまま受ける。
  (in-store store (SetStoreOutage None))
  (assert (isinstance (in-store store (put-one-as-value (put "p2" {"label" "a"} (ExpectAbsent)))) Written)))


(deftest test-the-handler-still-answers-every-outcome-as-a-value
  ;; R4: 答える側は値で答えたまま — <- を通さない撃ち方(in-store の直の撃ち)では Raise にならず、4 つとも値が返る。
  (val store (MemoryStore LAW-SCHEMA))
  (assert (isinstance (in-store store (put "p1" {"label" "a"} (ExpectAbsent))) Written))
  (assert (isinstance (in-store store (put "p1" {"label" "b"} (ExpectVersion 7))) Conflict))
  (assert (isinstance (in-store store (put "p1" {"state" "weird"} (ExpectAny))) Refused))
  (in-store store (SetStoreOutage DETAIL))
  (assert (= (in-store store (put "p2" {"label" "a"} (ExpectAbsent))) (Unreachable DETAIL))))


(deftest test-a-write-is-still-checked-at-creation
  ;; 作る時の検めは defclass の時と同じ型と文言で断る(表・鍵・欄・期待・冪等キー・NUL)。
  (for [bad-key ["" 3 (* "k" 1000)]]
    (with [(pytest.raises ValueError)]
      (PutRow "parts" #("p1") (FrozenMap {"label" "a"}) (ExpectAbsent) :idempotency-key bad-key)))
  (for [bad-row-key [#() ["p1"] "p1" None #(3)]]
    (with [(pytest.raises TypeError)]
      (PutRow "parts" bad-row-key (FrozenMap {"label" "a"}) (ExpectAbsent))))
  (with [(pytest.raises TypeError)]
    (PutRow "parts" #("p1") (FrozenMap {"label" "a"}) "absent"))
  (with [(pytest.raises ValueError)]
    (PutRow "parts" #("p1") (FrozenMap {"label" (+ "a" (chr 0))}) (ExpectAbsent)))
  ;; 欄・既定値・等しさ・凍結は今までどおり(dict を渡すと凍った写像になる)。
  (val one (PutRow "parts" #("p1") {"label" "a" "tags" ["x"]} (ExpectAbsent)))
  (assert (isinstance one.value FrozenMap) one)
  (assert (is one.idempotency-key None) one)
  (assert (= (PutRow "parts" #("p1") {"label" "a"} (ExpectAbsent) :idempotency-key "k1")
             (PutRow "parts" #("p1") {"label" "a"} (ExpectAbsent) :idempotency-key "k1")))
  (assert (= (hash one) (hash (PutRow "parts" #("p1") {"label" "a" "tags" ["x"]} (ExpectAbsent)))))
  (assert (= #(one.table one.key one.expect) #("parts" #("p1") (ExpectAbsent))) one)
  (with [(pytest.raises Exception)]
    (setattr one "idempotency_key" "other"))
  (assert (= (tuple (gfor f (dataclasses.fields PutRow) f.name)) #("table" "key" "value" "expect" "idempotency_key"))))
