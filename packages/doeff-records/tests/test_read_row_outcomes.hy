;; 行の読み ReadRow の答えの分け方(ADR-DOE-CORE-EFFECTS-003 R5・R17):
;; defeffect が Row = 成功 / Missing = 不在 / Unreachable = 失敗(値は無し)と宣言し、呼び手の <- は
;;   - Row を中身のまま束ねる
;;   - Missing を Absent として逃がす(その鍵の行が無い — maybe で Nothing・absent-as で既定の値・<- の :absent で失敗に畳める)
;;   - Unreachable を Raise(Unreachable) として逃がす(置き場に届かない — on-raise で値へ戻せる)
;; 答える memory の handler は今までどおり 3 つとも値で答える(R4 — 作る時の検めも変わらない)。表ごとの行の型の層 read-typed は
;; この宣言を通り、TypedRow だけを返して Missing は Absent で・Unreachable は Raise で逃がす。
(require doeff-hy.macros [deftest defk <- val on-raise absent-as])
(import dataclasses)
(import dataclasses [dataclass])
(import pytest)
(import doeff [run with_handlers])
(import doeff [Err Ok UnhandledEffect])
(import doeff.result [Nothing Some])
(import doeff_hy._frozen [FrozenMap])
(import doeff_core_effects [maybe result])
(import doeff_core_effects [scheduled])
(import doeff_time [SimClock sim-time-handler])
(import doeff_records [ExpectAbsent Missing Row Unreachable])
(import doeff_records [PutRow ReadRow])
(import doeff_records [SetStoreOutage])
(import doeff_records._laws [LAW-SCHEMA MAKER])
(import doeff_records._typed [RowType TypedRow read-typed])
(import doeff_records [MemoryStore memory-records-handler])

(val DETAIL "記録の service が落ちている(筋書き)")


(defclass [(dataclass :frozen True)] Part []
  "表 parts の行の型(LAW-SCHEMA の欄のうち id と label)。"
  (#^ str id)
  (setv #^ (| str None) label None)
  (setv #^ (| str None) state None))

(val PARTS (RowType "parts" Part))


(defn #^ object in-store [#^ MemoryStore store program]  ; cannot be a defk: 検の入口で run を撃つ
  "仮想の時計と memory の置き場(書き手 MAKER)の下で program を回して答えを返す。"
  (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) (memory-records-handler store MAKER)] program))))


(defn #^ ReadRow ask [#^ str id]  ; cannot be a defk: 検の支度が同期に組む値
  "表 parts の行 id を読む ReadRow。"
  (ReadRow "parts" #(id)))


(defn #^ None put-part [#^ MemoryStore store #^ str id #^ str label]  ; cannot be a defk: 検の支度が同期に置く行
  "表 parts に行 id を置く(検の前提)。"
  (in-store store (PutRow "parts" #(id) (FrozenMap {"label" label}) (ExpectAbsent))))


(defk read-one [id]
  {:pre [(: id str)] :post [(: % Row)] :tags {:context "records" :role "program"}}
  "行 id を <- で束ねて返す(Missing は束ねずに Absent・Unreachable は束ねずに Raise として逃げる)。"
  (<- row (ask id))
  row)


(defk read-one-or-nothing [id]
  {:pre [(: id str)] :post [(: % (| Ok Err))] :tags {:context "records" :role "program"}}
  "行 id の読みを宣言どおりに開き、不在は Nothing・失敗は Err に畳んで返すため(maybe を内側・result を外側 — R2 の入れ子の順)。"
  (<- opened (result (maybe (read-one id))))
  opened)


(defk read-one-as-value [id]
  {:pre [(: id str)] :post [(: % (| Row Missing Unreachable))] :tags {:context "records" :role "program"}}
  "read-one の不在と失敗を、今までの答えの値(Missing・Unreachable)へ戻して返す(値で受けたい呼び手の形)。"
  (<- answer (on-raise (absent-as (Missing) (read-one id))
                       (Unreachable detail) (Unreachable detail)))
  answer)


(defk read-typed-one [id]
  {:pre [(: id str)] :post [(: % TypedRow)] :tags {:context "records" :role "program"}}
  "行の型の読みを <- で束ねて返す(不在と失敗は read-typed の外へ逃げる)。"
  (<- typed (read-typed PARTS #(id)))
  typed)


(deftest test-the-declaration-names-row-as-success-missing-as-absence-and-unreachable-as-failure
  ;; 割り当ての表: 成功 = Row / 不在 = Missing / 失敗 = Unreachable / 値 = 無し。
  (val outcomes ReadRow.__doeff_outcomes__)
  (assert (= outcomes.success #(Row)) outcomes)
  (assert (= outcomes.absent #(Missing)) outcomes)
  (assert (= outcomes.failure #(Unreachable)) outcomes)
  (assert (= outcomes.value #()) outcomes))


(deftest test-a-row-that-is-there-is-bound-as-success
  (val store (MemoryStore LAW-SCHEMA))
  (put-part store "p1" "a")
  (val row (in-store store (read-one "p1")))
  (assert (isinstance row Row) row)
  (assert (= row.key #("p1")) row)
  (assert (= row.value (FrozenMap {"id" "p1" "label" "a" "state" "open"})) row)
  (assert (= row.version 1) row)
  ;; 成功は maybe にも result にも畳まれて中身のまま(Some・Ok)。
  (assert (= (repr (in-store store (read-one-or-nothing "p1"))) (repr (Ok (Some row)))))
  (assert (= (in-store store (read-one-as-value "p1")) row)))


(deftest test-a-missing-row-escapes-as-an-absence
  (val store (MemoryStore LAW-SCHEMA))
  ;; その鍵の行が無い: Missing は束ねずに Absent として逃げる — maybe で畳むと Nothing(失敗の Err にはならない)。
  (assert (= (repr (in-store store (read-one-or-nothing "nothing"))) (repr (Ok Nothing))))
  ;; 逃がしたままだと、受け手の無い Absent として止まる(黙って値にしない)。
  (with [(pytest.raises UnhandledEffect)]
    (in-store store (read-one "nothing")))
  ;; 不在を値で持ち回る呼び手は、absent-as で今までの値(Missing)へ戻せる。
  (assert (= (in-store store (read-one-as-value "nothing")) (Missing))))


(deftest test-an-unreachable-read-escapes-as-a-raise
  (val store (MemoryStore LAW-SCHEMA))
  (put-part store "p1" "a")
  (in-store store (SetStoreOutage DETAIL))
  ;; 置き場に届かない: Unreachable は束ねずに Raise として逃げる — result で畳むと Err(Unreachable)。行が在っても無くても同じ。
  (for [id ["p1" "nothing"]]
    (val settled (in-store store (read-one-or-nothing id)))
    (assert (isinstance settled Err) settled)
    (assert (= (repr settled) (.format "Err({!r})" (Unreachable DETAIL))) settled)
    (with [(pytest.raises UnhandledEffect)]
      (in-store store (read-one id))))
  ;; on-raise で値へ戻すと今までの答え(Unreachable)。
  (assert (= (in-store store (read-one-as-value "p1")) (Unreachable DETAIL)))
  ;; 戻すと今までどおり — 届く間は Row をそのまま受ける。
  (in-store store (SetStoreOutage None))
  (assert (isinstance (in-store store (read-one-as-value "p1")) Row)))


(deftest test-the-handler-still-answers-every-outcome-as-a-value
  ;; R4: 答える側は値で答えたまま — <- を通さない撃ち方(in-store の直の撃ち)では Absent にも Raise にもならず、3 つとも値が返る。
  (val store (MemoryStore LAW-SCHEMA))
  (put-part store "p1" "a")
  (assert (isinstance (in-store store (ask "p1")) Row))
  (assert (= (in-store store (ask "nothing")) (Missing)))
  (in-store store (SetStoreOutage DETAIL))
  (assert (= (in-store store (ask "p1")) (Unreachable DETAIL)))
  (assert (= (in-store store (ask "nothing")) (Unreachable DETAIL))))


(deftest test-read-typed-returns-only-the-typed-row-and-lets-absence-and-failure-escape
  (val store (MemoryStore LAW-SCHEMA))
  (put-part store "p1" "a")
  ;; 成功は行の型の値(TypedRow)だけ。
  (val typed (in-store store (read-typed-one "p1")))
  (assert (= typed (TypedRow #("p1") (Part :id "p1" :label "a" :state "open") 1)) typed)
  ;; 不在は Absent・失敗は Raise として呼び手へ逃げる(read-typed の答えの型に Missing・Unreachable は無い)。
  (assert (= (repr (in-store store (result (maybe (read-typed-one "nothing"))))) (repr (Ok Nothing))))
  (with [(pytest.raises UnhandledEffect)]
    (in-store store (read-typed-one "nothing")))
  (in-store store (SetStoreOutage DETAIL))
  (assert (= (repr (in-store store (result (maybe (read-typed-one "p1"))))) (.format "Err({!r})" (Unreachable DETAIL))))
  (with [(pytest.raises UnhandledEffect)]
    (in-store store (read-typed-one "p1"))))


(deftest test-a-read-is-still-checked-at-creation
  ;; 作る時の検めは defclass の時と同じ型と文言で断る(表の名・鍵)。
  (for [bad-table ["" "Parts" "1parts" None 3 (* "p" 64)]]
    (with [(pytest.raises ValueError)]
      (ReadRow bad-table #("p1"))))
  (for [bad-key [#() ["p1"] "p1" None #(3) #("p1" 2)]]
    (with [(pytest.raises TypeError)]
      (ReadRow "parts" bad-key)))
  ;; 欄・等しさ・凍結は今までどおり。
  (val one (ReadRow "parts" #("p1")))
  (assert (= #(one.table one.key) #("parts" #("p1"))) one)
  (assert (= one (ReadRow "parts" #("p1"))))
  (assert (= (hash one) (hash (ReadRow "parts" #("p1")))))
  (with [(pytest.raises Exception)]
    (setattr one "table" "tickets"))
  (assert (= (tuple (gfor f (dataclasses.fields ReadRow) f.name)) #("table" "key"))))
