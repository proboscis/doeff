;; #3605 の D: PostgreSQL の置き場の書きの効果(PutRow・PutRows・AppendEvent)は、期限切れの回収(終端の行の候補の読み・期限つきの列ごとの
;; 期限を過ぎた出来事の在るかの読み・期限の来た物の回収の transaction)を流さず、書きの transaction 1 つの文だけを流す — 記録の service と DB が
;; 別の機体に在ると文 1 つが DB への往復 1 回(数十 ms)で、前の形は書き 1 回ごとに 候補の読み 1 + 期限つきの列の数 の文を足していた(期限の
;; 来た物が在れば回収の transaction も)。回収は手入れの SweepExpired だけ。往復を数える(検の代役の SQL の答え手 probe-sql-handler —
;; transaction の区切りと書きの合図も 1 往復に数える・tests/sql_probes.hy)。置き場は期限つきの表 1 つ(tickets)と期限つきの列 2 本(pulses・
;; pairs)を持つ宣言(LAW-SCHEMA)で、期限を過ぎた行と出来事を置いてから書く。
;; 書きが触る物の片付け: 期限を過ぎた行への書きは、その行を消す文と変更の列の 消えた の文の 2 つを同じ transaction に足す。組で数える列
;; (pairs)への追記は、鍵の組の期限を過ぎた出来事を捨てる文を 1 つ足す(捨てた物が在れば鍵の覚えの文も)— 出来事ごとに数える列への新しい鍵の
;; 追記は足さない。
(require doeff-hy.macros [deftest defk <- val var])
(import doeff [run with_handlers])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_core_effects.postgres_sql [postgres-sql-handler])
(import doeff_time [SimClock sim-time-handler Delay])
(import doeff_hy.frozen [FrozenMap])
(import doeff_records.values [ExpectAbsent ExpectVersion Written WrittenRows Appended])
(import doeff_records.effects [PutRow PutRows RowWrite AppendEvent])
(import doeff_records.laws [MAKER TICKET-KEEP-SECONDS])
(import doeff_records.pg [pg-records-handler drop-records-tables])
(import tests.interpreters [session-dsn PG-DSN-VARIABLE pg-skip-reason DATABASE ORIGIN-HOST postgres-connections fresh-prefix run-sql prepared-store])
(import tests.sql_probes [QueryProbe StatementCounts probe-sql-handler effect-statements])

(val PG-DSN (session-dsn PG-DSN-VARIABLE))
;; env が無ければ conftest が使い捨ての PostgreSQL を立てて置く(#2830)— 無いのは立てられなかった時で、その理由を名指す。
(val PG-SKIP-REASON (pg-skip-reason))

;; 書き 1 回の往復の上限: BEGIN・書きの錠・置き場の頭・(行の錠の読み | 冪等キーの引きと鍵の覚えの引き)・(行の書き + 変更の列 | 出来事の書き)・
;; 書きの合図・COMMIT = 8。前の形は同じ書きに 候補の読み 3 文(終端の行 1・期限つきの列 2)を足して 11(期限の来た物が在れば回収の
;; transaction の分も)。
(val WRITE-ROUND-TRIPS 8)
;; 書きが触る期限を過ぎた物を片付ける書き: 上の 8 に 2 文(行の DELETE と 消えた の変更 / 組の出来事の DELETE と鍵の覚えの INSERT)。
(val TOUCHED-WRITE-ROUND-TRIPS 10)


(defk writes-past-expiry [counts]
  {:pre [(: counts StatementCounts)] :post [(: % tuple)]
   :tags {:context "records" :role "program"}}
  "期限を過ぎた終端の行と出来事(出来事ごとに数える列 pulses・組で数える列 pairs)を置き、SweepExpired を撃たずに書きの効果を 1 つずつ撃つため。
   答え = 書きの #(名 答え 流れた文) の tuple。"
  (<- (PutRow "tickets" #("g1" "t1") (FrozenMap {"owner" "o1"}) (ExpectAbsent)))
  (<- (PutRow "tickets" #("g1" "t1") (FrozenMap {"state" "done"}) (ExpectVersion 1)))
  (<- (AppendEvent "pulses" "pulse-1" {"n" 1}))
  (<- (AppendEvent "pairs" "ask:1" {"n" 2}))
  (<- (Delay (+ TICKET-KEEP-SECONDS 1)))
  (val writes #(#("PutRow" (PutRow "parts" #("p-nudge") (FrozenMap {"label" "n"}) (ExpectAbsent)))
                #("PutRows" (PutRows #((RowWrite "parts" #("p-batch") (FrozenMap {"label" "b"}) (ExpectAbsent)))))
                #("AppendEvent pulses" (AppendEvent "pulses" "pulse-2" {"n" 2}))
                #("AppendEvent journal" (AppendEvent "journal" "j-1" {"n" 3}))
                #("PutRow over an expired row" (PutRow "tickets" #("g1" "t1") (FrozenMap {"owner" "o2"}) (ExpectAbsent)))
                #("AppendEvent into an expired group" (AppendEvent "pairs" "done:1" {"n" 4}))))
  (var seen #())
  (for [#(name ask) writes]
    (<- run-of (effect-statements counts ask))
    (:= seen (+ seen #(#(name #* run-of)))))
  seen)


(deftest test-write-effects-run-only-their-write-transaction
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  (val connections (postgres-connections))
  (val store (prepared-store connections (fresh-prefix)))
  (val counts (StatementCounts))
  (try
    (val seen (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) (postgres-sql-handler connections)
                                              (probe-sql-handler connections DATABASE (QueryProbe counts))
                                              (pg-records-handler store MAKER ORIGIN-HOST)]
                                             (writes-past-expiry counts)))))
    (val answers (dfor #(name answer _) seen name answer))
    (val ran (dfor #(name _ statements) seen name statements))
    (assert (and (isinstance (get answers "PutRow") Written) (isinstance (get answers "PutRows") WrittenRows)
                 (isinstance (get answers "AppendEvent pulses") Appended) (isinstance (get answers "AppendEvent journal") Appended)
                 (= (get answers "PutRow over an expired row") (Written 1 (FrozenMap {"group" "g1" "id" "t1" "owner" "o2" "state" "open"})))
                 (isinstance (get answers "AppendEvent into an expired group") Appended))
            answers)
    ;; 書き 1 回の文は、どれも書きの transaction 1 つの中(BEGIN で始まり COMMIT で終わる)— 回収の候補の読みも回収の transaction も流れない。
    (assert (all (gfor #(_ statements) (.items ran) (and (= (get statements 0) "BEGIN") (= (get statements -1) "COMMIT")
                                                         (= (.count statements "BEGIN") 1))))
            ran)
    (val plain #("PutRow" "PutRows" "AppendEvent pulses" "AppendEvent journal"))
    (assert (all (gfor name plain (<= (len (get ran name)) WRITE-ROUND-TRIPS)))
            (dfor name plain name (len (get ran name))))
    (assert (not (any (gfor name plain text (get ran name) (in "DELETE" text)))) ran)
    ;; 書きが触る期限を過ぎた物だけを片付ける: 行への書きは state_rows の DELETE 1 つ・組への追記は append_rows の DELETE 1 つ。
    (val deletes (dfor #(name statements) (.items ran)
                       name (lfor text statements :if (.startswith (.lstrip text) "DELETE FROM") (get (.split (.lstrip text)) 2))))
    (assert (= (get deletes "PutRow over an expired row") [(+ store.prefix "state_rows")]) deletes)
    (assert (= (get deletes "AppendEvent into an expired group") [(+ store.prefix "append_rows")]) deletes)
    (assert (all (gfor name #("PutRow over an expired row" "AppendEvent into an expired group")
                       (<= (len (get ran name)) TOUCHED-WRITE-ROUND-TRIPS)))
            ran)
    (finally
      (run-sql connections (drop-records-tables store))
      (.close connections))))
