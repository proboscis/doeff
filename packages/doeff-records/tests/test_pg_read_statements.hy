;; #3561: PostgreSQL の置き場の読みの効果(ReadRow・ListRows・WatchChanges・WatchEvents・ReadEvents・ReadStreamEnd)は、期限切れの回収
;; (候補の読みと回収の transaction)を流さず、読みの文だけを流す — 置き場が別の拠点に在ると文 1 つが数十 ms 掛かり、読みのたびの回収の文が
;; 呼び手の待ちに乗っていた。期限を過ぎた終端の行と出来事(出来事ごとに数える列 pulses・組で数える列 pairs)を置いた置き場で、読みの効果 1 回ずつ
;; 流れた文を数える(検の代役の SQL の答え手 probe-sql-handler — tests/sql_probes.hy。前の形なら、読みのたびに候補の読み 3 文と回収の
;; transaction の文が足される)。期限を過ぎた物は、回収の前でも答えに出ない。書きの効果の文の数は test_pg_write_statements.hy(#3605 の D)。
;; 実 PostgreSQL の検は env DOEFF_RECORDS_TEST_PG_DSN の物(無ければ conftest が使い捨ての PostgreSQL を立てて置く — #2830)。立てられなければ理由を名指して skip。
(require doeff-hy.macros [deftest defk <- val var])
(import doeff [run with_handlers])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_core_effects.postgres_sql [postgres-sql-handler])
(import doeff_time [SimClock sim-time-handler Delay])
(import doeff_hy.frozen [FrozenMap])
(import doeff_records.values [ExpectAbsent ExpectVersion Missing Page Changes Events EventsQuiet StreamEmpty WatchCursor])
(import doeff_records.effects [ReadRow ListRows WatchChanges WatchEvents ReadEvents ReadStreamEnd PutRow AppendEvent])
(import doeff_records.laws [MAKER TICKET-KEEP-SECONDS])
(import doeff_records.pg [pg-records-handler drop-records-tables])
(import tests.interpreters [session-dsn PG-DSN-VARIABLE pg-skip-reason DATABASE ORIGIN-HOST postgres-connections fresh-prefix run-sql prepared-store])
(import tests.sql_probes [QueryProbe StatementCounts probe-sql-handler effect-statements])

(val PG-DSN (session-dsn PG-DSN-VARIABLE))
;; env が無ければ conftest が使い捨ての PostgreSQL を立てて置く(#2830)— 無いのは立てられなかった時で、その理由を名指す。
(val PG-SKIP-REASON (pg-skip-reason))


(defk reads-past-expiry [counts]
  {:pre [(: counts StatementCounts)] :post [(: % tuple)]
   :tags {:context "records" :role "program"}}
  "期限を過ぎた終端の行と出来事を置き、書きも SweepExpired も撃たずに読みの効果を 1 つずつ撃つため。答え = 読みの #(名 答え 流れた文) の tuple。"
  (<- start (ListRows "tickets"))
  (<- (PutRow "tickets" #("g1" "t1") (FrozenMap {"owner" "o1"}) (ExpectAbsent)))
  (<- (PutRow "tickets" #("g1" "t1") (FrozenMap {"state" "done"}) (ExpectVersion 1)))
  (<- (AppendEvent "pulses" "pulse-1" {"n" 1}))
  (<- (AppendEvent "pairs" "ask:1" {"n" 2}))
  (<- (Delay (+ TICKET-KEEP-SECONDS 1)))
  (val reads #(#("ReadRow" (ReadRow "tickets" #("g1" "t1")))
               #("ListRows" (ListRows "tickets"))
               #("WatchChanges" (WatchChanges #("tickets") (WatchCursor start.epoch start.sequence) :timeout 0.0))
               #("ReadEvents pulses" (ReadEvents "pulses"))
               #("ReadEvents pairs" (ReadEvents "pairs"))
               #("WatchEvents" (WatchEvents "pulses" :after 0 :timeout 0.0))
               #("ReadStreamEnd" (ReadStreamEnd "pairs"))))
  (var seen #())
  (for [#(name ask) reads]
    (<- run-of (effect-statements counts ask))
    (:= seen (+ seen #(#(name #* run-of)))))
  seen)


(deftest test-read-effects-run-only-their-read-statements
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  (val connections (postgres-connections))
  (val store (prepared-store connections (fresh-prefix)))
  (val counts (StatementCounts))
  (try
    ;; 呼び鈴(WatchChanges・WatchEvents の待ち)は代役の外側の postgres-sql-handler が答える。
    (val seen (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) (postgres-sql-handler connections)
                                              (probe-sql-handler connections DATABASE (QueryProbe counts))
                                              (pg-records-handler store MAKER ORIGIN-HOST)]
                                             (reads-past-expiry counts)))))
    (val answers (dfor #(name answer _) seen name answer))
    (val ran (dfor #(name _ statements) seen name statements))
    ;; 期限を過ぎた物は、回収の前でもどの読みにも出ない。
    (assert (= (get answers "ReadRow") (Missing)) answers)
    (assert (and (isinstance (get answers "ListRows") Page) (= (. (get answers "ListRows") rows) #())) answers)
    (assert (and (isinstance (get answers "WatchChanges") Changes) (= (. (get answers "WatchChanges") items) #())) answers)
    (assert (and (isinstance (get answers "ReadEvents pulses") Events) (= (. (get answers "ReadEvents pulses") items) #())) answers)
    (assert (and (isinstance (get answers "ReadEvents pairs") Events) (= (. (get answers "ReadEvents pairs") items) #())) answers)
    (assert (= (get answers "WatchEvents") (EventsQuiet)) answers)
    (assert (= (get answers "ReadStreamEnd") (StreamEmpty)) answers)
    ;; 読み 1 回が流す文は読みの文だけ: 行・追記の読みは 1 文、置き場の頭を読む一覧と変更の列は 2 文(頭 + 読み)。回収の候補の読み
    ;; (終端の行の読み・期限を過ぎた出来事の在るかの読み)も回収の transaction の文(BEGIN・DELETE)も流れない。
    (assert (= (dfor #(name statements) (.items ran) name (len statements))
               {"ReadRow" 1 "ListRows" 2 "WatchChanges" 2 "ReadEvents pulses" 1 "ReadEvents pairs" 1 "WatchEvents" 1 "ReadStreamEnd" 1})
            ran)
    (assert (not (any (gfor #(_ statements) (.items ran) text statements (in "DELETE" text)))) ran)
    (finally
      (run-sql connections (drop-records-tables store))
      (.close connections))))
