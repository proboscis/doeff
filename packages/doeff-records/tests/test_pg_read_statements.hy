;; #3561: PostgreSQL の置き場の読みの効果(ReadRow・ListRows・WatchChanges・WatchEvents・ReadEvents・ReadStreamEnd)は、期限切れの回収
;; (候補の読みと回収の transaction)を流さず、読みの文だけを流す — 置き場が別の拠点に在ると文 1 つが数十 ms 掛かり、読みのたびの回収の文が
;; 呼び手の待ちに乗っていた。期限を過ぎた終端の行と出来事(出来事ごとに数える列 pulses・組で数える列 pairs)を置いた置き場で、読みの効果 1 回ずつ
;; 流れた往復と文を数える(検の代役の SQL の答え手 probe-sql-handler — tests/sql_probes.hy。前の形なら、読みのたびに候補の読み 3 文と回収の
;; transaction の文が足される)。期限を過ぎた物は、回収の前でも答えに出ない。書きの効果の往復の数は test_pg_write_statements.hy(#3605)。
;; 実 PostgreSQL の検は env DOEFF_RECORDS_TEST_PG_DSN の物(無ければ conftest が使い捨ての PostgreSQL を立てて置く — #2830)。立てられなければ理由を名指して skip。
(require doeff-hy.macros [deftest defk <- val var])
(import doeff [run with_handlers])
(import doeff_core_effects [scheduled inline-compute-handler])
(import doeff_core_effects [postgres-sql-handler])
(import doeff_time [SimClock sim-time-handler Delay])
(import doeff_hy._frozen [FrozenMap])
(import doeff_records [ExpectAbsent ExpectVersion Missing Page Reset NotIndexed ListCursor Changes Events EventsQuiet StreamEmpty StreamTailEmpty WatchCursor])
(import doeff_records [ReadRow ListRows WatchChanges WatchEvents ReadEvents ReadStreamEnd PutRow AppendEvent])
(import doeff_records._laws [MAKER TICKET-KEEP-SECONDS])
(import doeff_records [pg-records-handler]
        doeff_records._pg [drop-records-tables])
(import tests.interpreters [session-dsn PG-DSN-VARIABLE pg-skip-reason DATABASE ORIGIN-HOST postgres-connections fresh-prefix run-sql prepared-store])
(import tests.sql_probes [QueryProbe StatementCounts probe-sql-handler effect-round-trips])

(val PG-DSN (session-dsn PG-DSN-VARIABLE))
;; env が無ければ conftest が使い捨ての PostgreSQL を立てて置く(#2830)— 無いのは立てられなかった時で、その理由を名指す。
(val PG-SKIP-REASON (pg-skip-reason))


(defk reads-past-expiry [counts]
  {:pre [(: counts StatementCounts)] :post [(: % tuple)]
   :tags {:context "records" :role "program"}}
  "期限を過ぎた終端の行と出来事を置き、書きも SweepExpired も撃たずに読みの効果を 1 つずつ撃つため。答え = 読みの #(名 答え 流れた往復) の tuple(往復ごとに流した文の tuple)。"
  (<- start (ListRows "tickets"))
  (<- (PutRow "tickets" #("g1" "t1") (FrozenMap {"owner" "o1"}) (ExpectAbsent)))
  (<- (PutRow "tickets" #("g1" "t1") (FrozenMap {"state" "done"}) (ExpectVersion 1)))
  (<- (AppendEvent "pulses" "pulse-1" {"n" 1}))
  (<- (AppendEvent "pairs" "ask:1" {"n" 2}))
  (<- (Delay (+ TICKET-KEEP-SECONDS 1)))
  (val reads #(#("ReadRow" (ReadRow "tickets" #("g1" "t1")))
               #("ListRows" (ListRows "tickets"))
               #("WatchChanges" (WatchChanges #("tickets") (WatchCursor start.epoch start.sequence) :timeout 0.0))
               ;; 列の末尾を名指した待ち(#3718): 末尾は置き場の頭と同じ 1 文で読む — 往復と文は名指さない待ちと同じ数。
               #("WatchChanges tails" (WatchChanges #("tickets") (WatchCursor start.epoch start.sequence) :timeout 0.0
                                                    :streams #("pulses" "pairs")))
               #("ReadEvents pulses" (ReadEvents "pulses"))
               #("ReadEvents pairs" (ReadEvents "pairs"))
               #("WatchEvents" (WatchEvents "pulses" :after 0 :timeout 0.0))
               #("ReadStreamEnd" (ReadStreamEnd "pairs"))))
  (var seen #())
  (for [#(name ask) reads]
    (<- run-of (effect-round-trips counts ask))
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
    (val trips (dfor #(name _ round-trips) seen name round-trips))
    (val ran (dfor #(name round-trips) (.items trips) name (tuple (gfor trip round-trips text trip text))))
    ;; 期限を過ぎた物は、回収の前でもどの読みにも出ない。
    (assert (= (get answers "ReadRow") (Missing)) answers)
    (assert (and (isinstance (get answers "ListRows") Page) (= (. (get answers "ListRows") rows) #())) answers)
    (assert (and (isinstance (get answers "WatchChanges") Changes) (= (. (get answers "WatchChanges") items) #())) answers)
    (assert (and (isinstance (get answers "WatchChanges tails") Changes) (= (. (get answers "WatchChanges tails") items) #())
                 (= (. (get answers "WatchChanges tails") tails) #((StreamTailEmpty :stream "pulses") (StreamTailEmpty :stream "pairs"))))
            answers)
    (assert (and (isinstance (get answers "ReadEvents pulses") Events) (= (. (get answers "ReadEvents pulses") items) #())) answers)
    (assert (and (isinstance (get answers "ReadEvents pairs") Events) (= (. (get answers "ReadEvents pairs") items) #())) answers)
    (assert (= (get answers "WatchEvents") (EventsQuiet)) answers)
    (assert (= (get answers "ReadStreamEnd") (StreamEmpty)) answers)
    ;; 最初の一覧は BEGIN・頭・行・COMMIT を 1 往復にまとめる。他の読みの文数は変えず、期限切れの DELETE は流さない。
    (val expected {"ReadRow" 1 "ListRows" 1 "WatchChanges" 2 "WatchChanges tails" 2 "ReadEvents pulses" 1 "ReadEvents pairs" 1
                   "WatchEvents" 1 "ReadStreamEnd" 1})
    (assert (= (dfor #(name round-trips) (.items trips) name (len round-trips)) expected) trips)
    (assert (= (dfor #(name statements) (.items ran) name (len statements)) {#** expected "ListRows" 4}) ran)
    (assert (not (any (gfor #(_ statements) (.items ran) text statements (in "DELETE" text)))) ran)
    (finally
      (run-sql connections (drop-records-tables store))
      (.close connections))))


(defk list-pages-across-a-write [counts]
  {:pre [(: counts StatementCounts)] :post [(: % tuple)]
   :tags {:context "records" :role "program"}}
  "先頭の頁の順・射影・続きと、次の読みが直前の書きを見ることを実 DB で確かめるため。版違いは行の SQL を撃たず Reset にする。"
  (<- (PutRow "parts" #("b") {"label" "before" "note" 1} (ExpectAbsent)))
  (<- (PutRow "parts" #("a") {"label" "other" "note" 2} (ExpectAbsent)))
  (<- first (effect-round-trips counts (ListRows "parts" :fields #("label") :limit 1 :descending-by "note")))
  (val page (get first 0))
  (<- next-page (effect-round-trips counts (ListRows "parts" :fields #("label") :limit 1 :descending-by "note" :cursor page.next-cursor)))
  (<- (PutRow "parts" #("b") {"label" "after"} (ExpectVersion 1)))
  (<- fresh (effect-round-trips counts (ListRows "parts" :where-any (FrozenMap {"label" #("after")}) :fields #("label"))))
  (<- reset (effect-round-trips counts (ListRows "parts" :cursor (ListCursor (+ page.epoch 1) ""))))
  (<- unindexed (effect-round-trips counts (ListRows "parts" :where (FrozenMap {"grant" "unindexed"}))))
  #(first next-page fresh reset unindexed))


(deftest test-list-first-page-keeps-order-projection-freshness-and-cursor-reset
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  (val connections (postgres-connections))
  (val store (prepared-store connections (fresh-prefix)))
  (val counts (StatementCounts))
  (try
    (val seen (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) inline-compute-handler (postgres-sql-handler connections)
                                              (probe-sql-handler connections DATABASE (QueryProbe counts))
                                              (pg-records-handler store MAKER ORIGIN-HOST)]
                                             (list-pages-across-a-write counts)))))
    (val pages (tuple (gfor #(answer _) (cut seen 3) answer)))
    (assert (all (gfor page pages (isinstance page Page))) pages)
    (assert (= (tuple (gfor page pages row page.rows #(row.key row.value row.version)))
               #(#(#("a") (FrozenMap {"id" "a" "label" "other"}) 1)
                 #(#("b") (FrozenMap {"id" "b" "label" "before"}) 1)
                 #(#("b") (FrozenMap {"id" "b" "label" "after"}) 2))) pages)
    (assert (> (. (get pages 2) sequence) (. (get pages 0) sequence)) pages)
    (assert (is (. (get pages 1) next-cursor) None))
    (assert (isinstance (get (get seen 3) 0) Reset))
    (assert (isinstance (get (get seen 4) 0) NotIndexed))
    (assert (= (tuple (gfor #(_ trips) seen (len trips))) #(1 2 1 1 1)) seen)
    ;; Reset は頭の SELECT だけ。行の読みに失敗する置き場でも、版違いの答えを先に返す。
    (assert (= (len (get (get (get seen 3) 1) 0)) 1))
    (finally
      (run-sql connections (drop-records-tables store))
      (.close connections))))
