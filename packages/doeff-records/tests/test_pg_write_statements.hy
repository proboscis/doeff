;; #3605: PostgreSQL の置き場の書きの効果(PutRow・PutRows・AppendEvent)は DB へ 2 往復で書く — 1 往復目 = BEGIN・書きの錠・判じるための
;; 読みの束(置き場の頭・錠つきの行 | 冪等キーの引き・鍵の覚え・組の期限を過ぎた出来事)、2 往復目 = 片付け・書き・合図・COMMIT の束。記録の
;; service と DB が別の機体に在ると往復 1 回(数十 ms)が書き 1 回の待ちに乗る。前の形は文 1 つが往復 1 回で、BEGIN・書きの錠・置き場の頭・
;; (行の錠の読み | 冪等キーの引きと鍵の覚えの引き)・(行の書き + 変更の列 | 出来事の書き)・書きの合図・COMMIT = 8 往復(PutRows は行ごとに
;; 3 往復増え、期限を過ぎた物を片付ける書きは 2 往復増える)。
;; 書きは期限切れの回収(終端の行の候補の読み・期限つきの列ごとの期限を過ぎた出来事の在るかの読み・回収の transaction)も流さない(#3605 の D
;; — 回収は手入れの SweepExpired だけ)。往復を数える(検の代役の SQL の答え手 probe-sql-handler — 往復ごとに流した文を数える・
;; tests/sql_probes.hy)。置き場は期限つきの表 1 つ(tickets)と期限つきの列 2 本(pulses・pairs)を持つ宣言(LAW-SCHEMA)で、期限を過ぎた行と
;; 出来事を置いてから書く。
;; 書きが触る物の片付け: 期限を過ぎた行への書きは、その行を消す文と変更の列の 消えた の文の 2 つを 2 往復目の先頭に足す。組で数える列
;; (pairs)への追記は、鍵の組の期限を過ぎた出来事の読みを 1 往復目に足し、捨てる物が在れば捨てる文と鍵の覚えの文を 2 往復目に足す —
;; 往復は増えない。出来事ごとに数える列への新しい鍵の追記は文を足さない。
(require doeff-hy.macros [deftest defk <- val var])
(import doeff [run with_handlers])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_core_effects.postgres_sql [postgres-sql-handler NOTICE-STATEMENT])
(import doeff_time [SimClock sim-time-handler Delay])
(import doeff_hy.frozen [FrozenMap])
(import doeff_records.values [ExpectAbsent ExpectVersion Written WrittenRows Appended])
(import doeff_records.effects [PutRow PutRows RowWrite AppendEvent])
(import doeff_records.laws [MAKER TICKET-KEEP-SECONDS])
(import doeff_records.pg [pg-records-handler drop-records-tables])
(import tests.interpreters [session-dsn PG-DSN-VARIABLE pg-skip-reason DATABASE ORIGIN-HOST postgres-connections fresh-prefix run-sql prepared-store])
(import tests.sql_probes [QueryProbe StatementCounts probe-sql-handler effect-round-trips])

(val PG-DSN (session-dsn PG-DSN-VARIABLE))
;; env が無ければ conftest が使い捨ての PostgreSQL を立てて置く(#2830)— 無いのは立てられなかった時で、その理由を名指す。
(val PG-SKIP-REASON (pg-skip-reason))

;; 書き 1 回の往復の上限: 読みの束(BEGIN と書きの錠と同じ往復)と書きの束(合図と COMMIT と同じ往復)= 2。片付けの付く書きも、束の文が
;; 増えるだけで往復は 2。
(val WRITE-ROUND-TRIPS 2)


(defk writes-past-expiry [counts]
  {:pre [(: counts StatementCounts)] :post [(: % tuple)]
   :tags {:context "records" :role "program"}}
  "期限を過ぎた終端の行と出来事(出来事ごとに数える列 pulses・組で数える列 pairs)を置き、SweepExpired を撃たずに書きの効果を 1 つずつ撃つため。
   答え = 書きの #(名 答え 往復の tuple) の tuple。"
  (<- (PutRow "tickets" #("g1" "t1") (FrozenMap {"owner" "o1"}) (ExpectAbsent)))
  (<- (PutRow "tickets" #("g1" "t1") (FrozenMap {"state" "done"}) (ExpectVersion 1)))
  (<- (AppendEvent "pulses" "pulse-1" {"n" 1}))
  (<- (AppendEvent "pairs" "ask:1" {"n" 2}))
  (<- (Delay (+ TICKET-KEEP-SECONDS 1)))
  (val writes #(#("PutRow" (PutRow "parts" #("p-nudge") (FrozenMap {"label" "n"}) (ExpectAbsent)))
                #("PutRows" (PutRows #((RowWrite "parts" #("p-batch-1") (FrozenMap {"label" "b"}) (ExpectAbsent))
                                       (RowWrite "parts" #("p-batch-2") (FrozenMap {"label" "c"}) (ExpectAbsent))
                                       (RowWrite "parts" #("p-batch-3") (FrozenMap {"label" "d"}) (ExpectAbsent)))))
                #("AppendEvent pulses" (AppendEvent "pulses" "pulse-2" {"n" 2}))
                #("AppendEvent journal" (AppendEvent "journal" "j-1" {"n" 3}))
                #("PutRow over an expired row" (PutRow "tickets" #("g1" "t1") (FrozenMap {"owner" "o2"}) (ExpectAbsent)))
                #("AppendEvent into an expired group" (AppendEvent "pairs" "done:1" {"n" 4}))
                #("PutRow conflict" (PutRow "parts" #("p-nudge") (FrozenMap {"label" "x"}) (ExpectAbsent)))))
  (var seen #())
  (for [#(name ask) writes]
    (<- run-of (effect-round-trips counts ask))
    (:= seen (+ seen #(#(name #* run-of)))))
  seen)


(deftest test-write-effects-reach-the-store-in-two-round-trips
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
    (val trips (dfor #(name _ round-trips) seen name round-trips))
    (val statements (dfor #(name round-trips) (.items trips) name (tuple (gfor trip round-trips text trip text))))
    ;; 往復の数と文の数(赤の時の文に出す)。
    (val shape (dfor name trips name #((len (get trips name)) (len (get statements name)))))
    (assert (and (isinstance (get answers "PutRow") Written) (isinstance (get answers "PutRows") WrittenRows)
                 (= (len (. (get answers "PutRows") items)) 3)
                 (isinstance (get answers "AppendEvent pulses") Appended) (isinstance (get answers "AppendEvent journal") Appended)
                 (= (get answers "PutRow over an expired row") (Written 1 (FrozenMap {"group" "g1" "id" "t1" "owner" "o2" "state" "open"})))
                 (isinstance (get answers "AppendEvent into an expired group") Appended)
                 (= (. (type (get answers "PutRow conflict")) __name__) "Conflict"))
            answers)
    ;; 書き 1 回は DB へ 2 往復: 1 往復目は BEGIN と書きの錠で始まり、2 往復目は書きの合図と COMMIT で終わる(BEGIN は 1 度だけ)— 回収の
    ;; 候補の読みも回収の transaction も流れない。片付けの付く書きと、衝突で何も書かない書きも往復は同じ。
    (assert (all (gfor #(_ round-trips) (.items trips) (<= (len round-trips) WRITE-ROUND-TRIPS))) shape)
    (assert (all (gfor #(_ round-trips) (.items trips)
                       (and (= (get round-trips 0 0) "BEGIN") (= (get round-trips -1 -1) "COMMIT")
                            (= (get round-trips -1 -2) NOTICE-STATEMENT))))
            trips)
    (assert (all (gfor #(_ texts) (.items statements) (= (.count texts "BEGIN") 1))) statements)
    (val plain #("PutRow" "PutRows" "AppendEvent pulses" "AppendEvent journal"))
    (assert (not (any (gfor name plain text (get statements name) (in "DELETE" text)))) statements)
    ;; 書きが触る期限を過ぎた物だけを片付ける: 行への書きは state_rows の DELETE 1 つ・組への追記は append_rows の DELETE 1 つ(どちらも
    ;; 2 往復目の先頭)。
    (val deletes (dfor #(name texts) (.items statements)
                       name (lfor text texts :if (.startswith (.lstrip text) "DELETE FROM") (get (.split (.lstrip text)) 2))))
    (assert (= (get deletes "PutRow over an expired row") [(+ store.prefix "state_rows")]) deletes)
    (assert (= (get deletes "AppendEvent into an expired group") [(+ store.prefix "append_rows")]) deletes)
    (assert (all (gfor name #("PutRow over an expired row" "AppendEvent into an expired group")
                       (.startswith (.lstrip (get trips name 1 0)) "DELETE FROM")))
            trips)
    (finally
      (run-sql connections (drop-records-tables store))
      (.close connections))))
