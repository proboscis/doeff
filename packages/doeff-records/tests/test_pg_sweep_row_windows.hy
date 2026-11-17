;; PostgreSQL の置き場の行の回収(SweepExpired の行 — _pg.hy の sweep-table)は、表ごと・回収の窓(SWEEP-ROW-BATCH 行)ごとに書きの錠の
;; transaction を分け、1 つの transaction が持つ部分の錠はその表の単位 1 つだけである事(#2830 の回収の割り)。
;; 2026-11-17 の本番: 回収 1 回が期限と終端の語の在る表の全部(21 表)の表の単位と番号の錠を 1 つの transaction で持ち、期限を過ぎた行を
;; 1 行ずつ DELETE してから変更を積んで、錠を平均 7.8 秒持った。その間 21 表への行の書きと、番号の錠を待つ全部の行の書きが後ろで待った。
;; 性質: 期限の在る 2 つの表(tickets に期限を過ぎた終端の行 TICKET-EXPIRED 行 — 窓 2 つを越える数・chores に CHORE-EXPIRED 行)と、期限の
;; 前の終端の行・終端でない行の置き場で SweepExpired を 1 回撃つと、
;;   - 表の単位を持つ transaction は、どれも置き場全体の共有の錠と表の単位 1 つだけを持ち(batched — 消す文・番号の錠・積む文・COMMIT を
;;     1 往復)、数は表ごとに窓の数(tickets は ceil(TICKET-EXPIRED / SWEEP-ROW-BATCH)・chores は 1)
;;   - 消えた行の数と、変更の列に積まれた RowRemoved(表と鍵)は、期限を過ぎた行の全部とちょうど同じ(重ねず・漏らさず)で、期限の前の
;;     終端の行と終端でない行は読める
;; 反例(前の形 — 期限の在る表の全部の単位を 1 つの transaction で持つ): 表の単位を持つ transaction が 1 つで単位を 2 つ持ち、赤。
;; env DOEFF_RECORDS_TEST_PG_DSN が無ければ conftest が使い捨ての PostgreSQL を立てて置く(#2830)。立てられなければ理由を名指して skip。
(require doeff-hy.macros [defeffect defhandler deftest defk <- val var])
(require doeff-hy.record [defrecord])
(import math)
(import doeff [Perform run with_handlers])
(import doeff_hy._frozen [FrozenMap])
(import doeff_core_effects [state])
(import doeff_core_effects [scheduled])
(import doeff_core_effects [postgres-sql-handler])
(import doeff_core_effects [SqlTransaction])
(import doeff_time [SimClock sim-time-handler Delay])
(import doeff_records [Row Missing ExpectAbsent Written RowRemoved])
(import doeff_records [PutRow ReadRow])
(import doeff_records [SweepExpired Swept ReadChangeLog])
(import doeff_records._values [FieldDecl TableDecl RecordsSchema KeepFor])
(import doeff_records._laws [MAKER LAW-SCHEMA TICKET-KEEP-SECONDS])
(import doeff_records [pg-records-handler writer-lock-key]
        doeff_records._pg [drop-records-tables SWEEP-ROW-BATCH]
        doeff_records._pg_sql [table-unit unit-lock-key])
(import tests.interpreters [session-dsn PG-DSN-VARIABLE pg-skip-reason ORIGIN-HOST postgres-connections fresh-prefix run-sql
                            prepared-store])

(val PG-DSN (session-dsn PG-DSN-VARIABLE))
;; env が無ければ conftest が使い捨ての PostgreSQL を立てて置く(#2830)— 無いのは立てられなかった時で、その理由を名指す。
(val PG-SKIP-REASON (pg-skip-reason))

;; 期限を過ぎる終端の行の数: tickets は窓 2 つを越える(窓の続きと端の窓を通す)・chores は窓 1 つに収まる。
(val TICKET-EXPIRED (+ (* 2 SWEEP-ROW-BATCH) 7))
(val CHORE-EXPIRED 3)
;; 期限の前の終端の行と終端でない行の数(回収が消さない行)。
(val FRESH-ROWS 2)
(val OPEN-ROWS 2)
;; 書く行の鍵(tickets は #("g" 頭n)・chores は #(頭n))。
(val OLD-TICKETS (tuple (gfor n (range 1 (+ TICKET-EXPIRED 1)) #("g" (.format "old-{}" n)))))
(val OLD-CHORES (tuple (gfor n (range 1 (+ CHORE-EXPIRED 1)) #((.format "old-{}" n)))))
(val FRESH-TICKETS (tuple (gfor n (range 1 (+ FRESH-ROWS 1)) #("g" (.format "fresh-{}" n)))))
(val OPEN-TICKETS (tuple (gfor n (range 1 (+ OPEN-ROWS 1)) #("g" (.format "open-{}" n)))))
(val OPEN-CHORES (tuple (gfor n (range 1 (+ OPEN-ROWS 1)) #((.format "open-{}" n)))))
;; 期限の在る 2 つ目の表(tickets と同じ形 — 回収 1 回が 2 つの表を触る置き場にする)。
(val SWEEP-SCHEMA
     (RecordsSchema :tables {"tickets" (get LAW-SCHEMA.tables "tickets")
                             "chores" (TableDecl :name "chores" :key-fields #("id")
                                                 :fields #((FieldDecl "id" #(MAKER)) (FieldDecl "state" #(MAKER)))
                                                 :states #("open" "done") :terminal #("done") :initial "open"
                                                 :retention (KeepFor TICKET-KEEP-SECONDS))}))


(defrecord SeenTransaction
  "置き場の handler が開いた書きの錠の transaction 1 つ: batched = 往復をまとめる transaction か / keys = 錠の並びの鍵(SqlLock.key の順)。"
  (#^ bool batched)
  (#^ tuple keys))


(defeffect ReadTransactions
  "transaction-tally が控えた SeenTransaction の列(開いた順)を読む。"
  {:fields [] :answer tuple :tags {:context "records" :role "program"}})


(defhandler transaction-tally
  "置き場の handler が開いた transaction ごとに、batched かと錠の鍵の並びを控えるため(回収 1 回が開いた transaction の錠の単位を数で読む)。
   答えはそのまま返す。"
  {:tags {:context "records" :role "program"}}
  (session var tally #())
  (SqlTransaction [database program lock-key batched locks]
    (<- answer effect)
    (:= tally (+ tally #((SeenTransaction :batched batched :keys (tuple (gfor lock locks lock.key))))))
    (resume answer))
  (ReadTransactions []
    (resume tally)))


(defk put-rows [table keys state]
  {:pre [(: table str) (: keys tuple) (: state str)] :post [(: % None)]
   :tags {:context "records" :role "program"}}
  "表 table に状態 state の行を鍵 keys ごとに 1 行書くため(書いた刻は今の仮想の時計)。"
  (for [key keys]
    (<- written (PutRow table key (FrozenMap (| (dict (zip (. (get SWEEP-SCHEMA.tables table) key-fields) key)) {"state" state}))
                        (ExpectAbsent)))
    (assert (isinstance written Written) (repr written)))
  None)


(defk sweep-after-expiry []
  {:pre [] :post [(: % dict)]
   :tags {:context "records" :role "program"}}
  "期限を過ぎる終端の行を 2 つの表に書き、保持の秒の後に期限の前の終端の行と終端でない行を書いてから SweepExpired を 1 回撃ち、
   {名 値} で回収の答え・回収が開いた transaction の控え・回収の後に積まれた変更・残る行の読みを返すため。"
  (<- (put-rows "tickets" OLD-TICKETS "done"))
  (<- (put-rows "chores" OLD-CHORES "done"))
  (<- (Delay (+ TICKET-KEEP-SECONDS 1)))
  (<- (put-rows "tickets" FRESH-TICKETS "done"))
  (<- (put-rows "tickets" OPEN-TICKETS "open"))
  (<- (put-rows "chores" OPEN-CHORES "open"))
  (<- before (ReadChangeLog :after 0 :limit 1000))
  (<- opened tuple (ReadTransactions))
  (<- swept (SweepExpired))
  (<- tally tuple (ReadTransactions))
  (<- after (ReadChangeLog :after (. (get before.changes -1) sequence) :limit 1000))
  ;; 回収した行の不在 Missing を値のまま受ける(<- だと ReadRow の宣言が Absent にする)。
  (<- old (Perform (ReadRow "tickets" (get OLD-TICKETS -1))))
  (<- fresh (ReadRow "tickets" (get FRESH-TICKETS 0)))
  (<- open (ReadRow "chores" (get OPEN-CHORES 0)))
  {"swept" swept "transactions" (cut tally (len opened) None) "changes" after.changes "old" old "fresh" fresh "open" open})


(deftest test-a-sweep-removes-expired-rows-one-table-and-one-window-per-transaction
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  (val connections (postgres-connections))
  (val store (prepared-store connections (fresh-prefix) SWEEP-SCHEMA))
  (try
    (val outcome (run (scheduled (with_handlers [(state) (sim-time-handler :clock (SimClock)) (postgres-sql-handler connections)
                                                 transaction-tally (pg-records-handler store MAKER ORIGIN-HOST)]
                                                (sweep-after-expiry)))))
    (val whole (run (writer-lock-key store.prefix)))
    (val units {(run (unit-lock-key store.prefix (run (table-unit "tickets")))) "tickets"
                (run (unit-lock-key store.prefix (run (table-unit "chores")))) "chores"})
    ;; 表の単位を持つ transaction は、どれも置き場全体の共有の錠と表の単位 1 つだけを持つ batched の transaction で、数は表ごとに窓の数。
    (val sweeps (tuple (gfor seen (get outcome "transactions") :if (any (gfor key seen.keys (in key units))) seen)))
    (for [seen sweeps]
      (assert seen.batched (repr seen))
      (assert (= (len seen.keys) 2) (repr seen))
      (assert (= (get seen.keys 0) whole) (repr seen))
      (assert (in (get seen.keys 1) units) (repr seen)))
    (val per-table (tuple (gfor seen sweeps (get units (get seen.keys 1)))))
    (assert (= (.count per-table "tickets") (math.ceil (/ TICKET-EXPIRED SWEEP-ROW-BATCH))) (repr per-table))
    (assert (= (.count per-table "chores") 1) (repr per-table))
    ;; 消えた行の数と積まれた RowRemoved は、期限を過ぎた行の全部とちょうど同じ。
    (assert (= (. (get outcome "swept") rows) (+ TICKET-EXPIRED CHORE-EXPIRED)) (repr (get outcome "swept")))
    (val changes (get outcome "changes"))
    (assert (all (gfor change changes (isinstance change RowRemoved))) (repr changes))
    (val removed (sorted (gfor change changes #(change.table change.key))))
    (val expected (sorted (+ (lfor key OLD-TICKETS #("tickets" key)) (lfor key OLD-CHORES #("chores" key)))))
    (assert (= removed expected) (repr removed))
    ;; 期限を過ぎた行は消え、期限の前の終端の行と終端でない行は読める。
    (assert (isinstance (get outcome "old") Missing) (repr outcome))
    (assert (isinstance (get outcome "fresh") Row) (repr outcome))
    (assert (isinstance (get outcome "open") Row) (repr outcome))
    (finally
      (run-sql connections (drop-records-tables store))
      (.close connections))))
