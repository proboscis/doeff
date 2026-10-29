;; 記録の service が書きを確定した後で答えを落とし、HTTP の client が止まりを越えて同じ書きを撃ち直す時の失敗ケース(card ki-78c6d663491f)。
;; 確かめること:
;;   期待つきの書き(PutRow の ExpectAbsent・PutRows)の撃ち直しは、自分の着いた 1 回目に負けず、着いた書きの答え(Written・WrittenRows の
;;   その時の版)を送り直しと名乗って返す — client が要求ごとに冪等キーを 1 つ作り、撃ち直しにも同じ鍵を付けるため
;;   鍵の無い書きを送り直す前の形(client が鍵を付けない撃ち直し)は、同じ故障の下で自分の着いた書きに Conflict で負ける(直す前の答え)
;;   故障の口の当たる回数(StoreFault.times)は当たるたびに減り、尽きた故障は外れる(1 度だけ答えを落とす故障を作れる)
;; 反例 = 直す前の client(鍵を作らない・置き場が鍵を覚えない)は、1 本目と 2 本目の検で Written の代わりに Conflict / RowsConflict を返して赤になる。
;; 組は http-memory(tests/interpreters.hy — client の要求を同じ scheduler の中で service の respond に渡す)と memory。故障は検の口
;; faults.AddStoreFault を置き場の handler に撃って置く(lands = 書きを置き場に着けてから答えを Unreachable に差し替える)。
(require doeff-hy.macros [deftest defk <- val])
(import doeff [with_handlers])
(import doeff_hy.frozen [FrozenMap])
(import doeff_records [ExpectAbsent Written WrittenRows Conflict RowsConflict Refused RowsRefused Row Unreachable])
(import doeff_records [ReadRow PutRow PutRows RowWrite])
(import doeff_records [AddStoreFault StoreFault StoreOperation])
(import doeff_records._laws [LawHarness MAKER as-writer])
(import doeff_records.http_client [RequestPatience request-patience-handler])
(import tests.interpreters [LawSetup])

(val DETAIL "記録の service が書きを確定した後で答えを落とした(撃ち直しの検の筋書き)")
(val PATIENCE (RequestPatience :seconds 60.0))


(defk dropped-once []
  {:pre [] :post [(: % StoreFault)] :tags {:context "records" :role "program"}}
  "表 parts・tickets の書きを置き場に着けてから、答えを 1 度だけ Unreachable に差し替える故障を作るため(確定した後で答えが切れた形)。"
  (StoreFault (frozenset #("parts" "tickets")) StoreOperation.WRITE (Unreachable DETAIL) :lands True :times 1))


(defk written-through-a-dropped-answer [harness ask]
  {:pre [(: harness LawHarness) (: ask (| PutRow PutRows))]
   :post [(: % (| Written WrittenRows Conflict RowsConflict Refused RowsRefused Unreachable))] :tags {:context "records" :role "program"}}
  "答えを 1 度だけ落とす故障を置いてから、止まりを越える client(待つ時間 PATIENCE)で ask を撃ち、その答えを返すため。"
  (<- fault StoreFault (dropped-once))
  (<- (as-writer harness MAKER (AddStoreFault fault)))
  (<- answer (with_handlers [(request-patience-handler PATIENCE)] (as-writer harness MAKER ask)))
  answer)


(deftest test-a-put-row-retried-after-a-dropped-answer-is-written
  {:interpreters ["http-memory"]}
  ;; ExpectAbsent の書きが確定した後で答えが切れても、client の撃ち直しは自分の着いた書きの Written(版 1)を送り直しと名乗って返す。
  (<- harness (LawSetup))
  (<- answer (written-through-a-dropped-answer harness (PutRow "parts" #("p1") (FrozenMap {"label" "a"}) (ExpectAbsent))))
  (assert (isinstance answer Written) answer)
  (assert (= #(answer.version answer.replayed) #(1 True)) answer)
  (<- read (as-writer harness MAKER (ReadRow "parts" #("p1"))))
  (assert (= read (Row #("p1") (FrozenMap {"id" "p1" "label" "a" "state" "open"}) 1)) read))


(deftest test-a-put-rows-retried-after-a-dropped-answer-is-written
  {:interpreters ["http-memory"]}
  ;; 束の書き(PutRows)も同じ — 撃ち直しは束の順の Written(各行の版 1)を送り直しと名乗って返す。
  (<- harness (LawSetup))
  (<- answer (written-through-a-dropped-answer harness
               (PutRows #((RowWrite "parts" #("p2") (FrozenMap {"label" "b"}) (ExpectAbsent))
                          (RowWrite "tickets" #("g1" "t1") (FrozenMap {"owner" "o1"}) (ExpectAbsent))))))
  (assert (isinstance answer WrittenRows) answer)
  (assert (is answer.replayed True) answer)
  (assert (= (lfor item answer.items #(item.version item.replayed)) [#(1 True) #(1 True)]) answer))


(deftest test-a-keyless-resend-loses-to-its-own-landed-write
  {:interpreters ["memory"]}
  ;; 直す前の形: 鍵の無い書きを、答えが切れた後に同じ形で送り直すと、自分の着いた 1 回目に Conflict で負ける(行は 1 回目の書きの版 1)。
  ;; 当たる回数 1 の故障は 1 度で外れ、送り直しは置き場に届く。
  (<- harness (LawSetup))
  (<- fault StoreFault (dropped-once))
  (<- (as-writer harness MAKER (AddStoreFault fault)))
  (val ask (PutRow "parts" #("p3") (FrozenMap {"label" "c"}) (ExpectAbsent)))
  (<- dropped (as-writer harness MAKER ask))
  (<- resent (as-writer harness MAKER ask))
  (assert (= dropped (Unreachable DETAIL)) dropped)
  (assert (= resent (Conflict (Row #("p3") (FrozenMap {"id" "p3" "label" "c" "state" "open"}) 1))) resent))


(deftest test-a-fault-with-times-is-spent
  {:interpreters ["memory"]}
  ;; 当たる回数 2 の故障は 2 度当たってから外れる(3 度目の書きは置き場の答え)。当たる回数の無い故障(既定)は外すまで当たり続ける。
  (<- harness (LawSetup))
  (<- (as-writer harness MAKER (AddStoreFault (StoreFault (frozenset #("parts")) StoreOperation.WRITE (Unreachable DETAIL) :times 2))))
  (<- one (as-writer harness MAKER (PutRow "parts" #("p4") (FrozenMap {"label" "d"}) (ExpectAbsent))))
  (<- two (as-writer harness MAKER (PutRow "parts" #("p4") (FrozenMap {"label" "d"}) (ExpectAbsent))))
  (<- three (as-writer harness MAKER (PutRow "parts" #("p4") (FrozenMap {"label" "d"}) (ExpectAbsent))))
  (assert (= #(one two) #((Unreachable DETAIL) (Unreachable DETAIL))) #(one two))
  (assert (and (isinstance three Written) (= three.version 1)) three))
