;; ListRows.join_tables の境界と PostgreSQL 固有の性質の検:
;;   (a) PostgreSQL で 1 往復・1 本の SQL(先頭の頁は BEGIN・頭・CTE の 1 文・COMMIT の 1 往復、続きの頁と ReadBatch も 1 往復)
;;   (b) KeepFor で期限を過ぎた添える表の終端の行は、回収の前でも joined に出ない(memory と PostgreSQL の両方)
;;   (c) 添える表の大きい欄(TableDecl.bulk)は主の表の fields 射影に依らず結合して返り、PostgreSQL の答えの行は保存の綴りをそのまま持つ
;;   (d) 宣言に無い表を join-tables に名指すと UndeclaredTable(tables=(その名,)) で断る(memory・PostgreSQL・HTTP wire の理由)
;;   (e) 添える表への SetStoreOutage / AddStoreFault は Unreachable に当たる(memory)
(require doeff-hy.macros [deftest defk <- val on-raise])
(import json)
(import doeff [run with_handlers])
(import doeff_core_effects [scheduled inline-compute-handler postgres-sql-handler])
(import doeff_time [SimClock sim-time-handler Delay])
(import doeff_hy._frozen [FrozenMap])
(import doeff_records [FieldDecl TableDecl RecordsSchema KeepForever KeepFor UndeclaredTable])
(import doeff_records [ExpectAbsent ExpectVersion Page Reset BatchRead Row Unreachable])
(import doeff_records [ListRows ReadBatch PutRow SetStoreOutage AddStoreFault ClearStoreFaults StoreFault StoreOperation])
(import doeff_records [MemoryStore memory-records-handler pg-records-handler]
        doeff_records._pg [drop-records-tables]
        doeff_records._values [stored-text]
        doeff_records._wire [undeclared-reason undeclared-refusal answer-text encode-answer])
(import doeff_records._laws [MAKER])
(import tests.interpreters [session-dsn PG-DSN-VARIABLE pg-skip-reason DATABASE ORIGIN-HOST
                            postgres-connections fresh-prefix run-sql prepared-store])
(import tests.sql_probes [QueryProbe StatementCounts probe-sql-handler effect-round-trips])

(val PG-DSN (session-dsn PG-DSN-VARIABLE))
(val PG-SKIP-REASON (pg-skip-reason))
(val NOTE-KEEP-SECONDS 10)

(val JOIN-SCHEMA
  (RecordsSchema
    :tables (FrozenMap
              {"items" (TableDecl :name "items" :key-fields #("id")
                                  :fields #((FieldDecl :name "id" :writers #(MAKER))
                                            (FieldDecl :name "group" :writers #(MAKER))
                                            (FieldDecl :name "label" :writers #(MAKER))
                                            (FieldDecl :name "created_at" :writers #(MAKER)))
                                  :indexes #("group")
                                  :descending #("created_at")
                                  :retention (KeepForever))
               "notes" (TableDecl :name "notes" :key-fields #("id")
                                  :fields #((FieldDecl :name "id" :writers #(MAKER))
                                            (FieldDecl :name "state" :writers #(MAKER))
                                            (FieldDecl :name "detail" :writers #(MAKER)))
                                  :state-field "state"
                                  :states #("open" "done")
                                  :terminal #("done")
                                  :initial "open"
                                  :bulk #("detail")
                                  :retention (KeepFor NOTE-KEEP-SECONDS))
               "tags" (TableDecl :name "tags" :key-fields #("id")
                                 :fields #((FieldDecl :name "id" :writers #(MAKER))
                                           (FieldDecl :name "tag" :writers #(MAKER)))
                                 :retention (KeepForever))})
    :streams (FrozenMap)))


(defk joined-round-trip-and-expiry [counts]
  {:pre [(: counts StatementCounts)] :post [(: % tuple)]
   :tags {:context "records" :role "program"}}
  "主の表 3 行と添える表(大きい欄と期限つき終端を含む)を書き、1 頁目・2 頁目・ReadBatch の往復数と、期限切れ後の joined を集めるため。"
  (<- (PutRow "items" #("k1") (FrozenMap {"group" "g" "label" "one" "created_at" 10}) (ExpectAbsent)))
  (<- (PutRow "items" #("k2") (FrozenMap {"group" "g" "label" "two" "created_at" 20}) (ExpectAbsent)))
  (<- (PutRow "items" #("k3") (FrozenMap {"group" "g" "label" "three" "created_at" 30}) (ExpectAbsent)))
  (<- (PutRow "notes" #("k3") (FrozenMap {"detail" "big-k3"}) (ExpectAbsent)))
  (<- (PutRow "notes" #("k2") (FrozenMap {"detail" "big-k2"}) (ExpectAbsent)))
  (<- (PutRow "notes" #("k2") (FrozenMap {"state" "done"}) (ExpectVersion 1)))
  (<- (PutRow "tags" #("k3") (FrozenMap {"tag" "pinned"}) (ExpectAbsent)))
  ;; 先頭の頁(limit 2、descending_by created_at、fields 射影あり): k3・k2 に対し notes(bulk 含む)と tags(k3 だけ)が 1 往復で載る。
  (val first-ask (ListRows "items" :where (FrozenMap {"group" "g"}) :descending-by "created_at"
                           :fields #("label") :limit 2 :join-tables #("notes" "tags")))
  (<- first-run (effect-round-trips counts first-ask))
  (val first-page (get first-run 0))
  ;; 2 頁目(cursor 付き): k1 は notes・tags どちらも無い。
  (val second-ask (ListRows "items" :where (FrozenMap {"group" "g"}) :descending-by "created_at"
                            :fields #("label") :limit 2 :cursor first-page.next-cursor :join-tables #("notes" "tags")))
  (<- second-run (effect-round-trips counts second-ask))
  ;; ReadBatch の中でも 1 往復で同じ joined を返す。
  (<- batch-run (effect-round-trips counts (ReadBatch #(first-ask))))
  ;; 期限を過ぎた k2 の終端行は回収の前でも joined から消える(k3 の open 行は残る)。
  (<- (Delay (+ NOTE-KEEP-SECONDS 1)))
  (<- after-expiry-run (effect-round-trips counts first-ask))
  #(first-run second-run batch-run after-expiry-run))


(deftest test-pg-joined-list-runs-in-one-round-trip-merges-bulk-and-hides-expired
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  (val connections (postgres-connections))
  (val store (prepared-store connections (fresh-prefix) :schema JOIN-SCHEMA))
  (val counts (StatementCounts))
  (try
    (val outcomes
      (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) inline-compute-handler
                                      (postgres-sql-handler connections)
                                      (probe-sql-handler connections DATABASE (QueryProbe counts))
                                      (pg-records-handler store MAKER ORIGIN-HOST)]
                                     (joined-round-trip-and-expiry counts)))))
    (val first-page (get (get outcomes 0) 0))
    (val first-trips (get (get outcomes 0) 1))
    (val second-page (get (get outcomes 1) 0))
    (val second-trips (get (get outcomes 1) 1))
    (val batch-answer (get (get outcomes 2) 0))
    (val batch-trips (get (get outcomes 2) 1))
    (val expired-page (get (get outcomes 3) 0))
    (val expired-trips (get (get outcomes 3) 1))
    ;; 先頭頁と ReadBatch は batched transaction の 1 往復(4 文)、続きの頁は頭の確認 + CTE の 1 文の 2 往復(添えない ListRows と同じ)。
    (assert (= (len first-trips) 1) first-trips)
    (assert (= (len (get first-trips 0)) 4) first-trips)
    (assert (= (len second-trips) 2) second-trips)
    (assert (= (len (get second-trips 1)) 1) second-trips)
    (assert (= (len batch-trips) 1) batch-trips)
    (assert (= (len expired-trips) 1) expired-trips)
    ;; 主の表は fields 射影され(鍵の欄 + 名指した欄)、添えた表の行は bulk 欄 detail を含めて全欄が返る。
    (assert (= (tuple (gfor row first-page.rows #(row.key (dict row.value))))
               #(#(#("k3") {"id" "k3" "label" "three"}) #(#("k2") {"id" "k2" "label" "two"})))
            first-page)
    (assert (= (tuple (gfor row (get first-page.joined "notes") #(row.key (get row.value "detail") (get row.value "state"))))
               #(#(#("k3") "big-k3" "open") #(#("k2") "big-k2" "done")))
            first-page.joined)
    (assert (= (tuple (gfor row (get first-page.joined "tags") #(row.key (get row.value "tag"))))
               #(#(#("k3") "pinned")))
            first-page.joined)
    ;; PostgreSQL の添えた行も保存の綴り(_stored_text)をそのまま持ち、answer-text が encode-answer と同じ JSON に綴る。
    (assert (all (gfor row (get first-page.joined "notes") (is-not (stored-text row) None))) first-page.joined)
    (assert (= (json.loads (run (answer-text first-page))) (run (encode-answer first-page))))
    ;; 2 頁目は k1 だけ、添える表はどちらも空。
    (assert (= (tuple (gfor row second-page.rows row.key)) #(#("k1"))) second-page)
    (assert (= (get second-page.joined "notes") #()) second-page.joined)
    (assert (= (get second-page.joined "tags") #()) second-page.joined)
    ;; ReadBatch の答えは単体の ListRows と同じ。
    (assert (and (isinstance batch-answer BatchRead) (= (get batch-answer.items 0) first-page)) batch-answer)
    ;; 期限切れ後: 終端の k2 の notes 行は回収前でも消え、open の k3 だけ残る。
    (assert (= (tuple (gfor row (get expired-page.joined "notes") row.key)) #(#("k3"))) expired-page.joined)
    (finally
      (run-sql connections (drop-records-tables store))
      (.close connections))))


(defk memory-expiry-and-outage-check []
  {:pre [] :post [(: % tuple)]
   :tags {:context "records" :role "program"}}
  "memory 置き場で、添える表の期限切れの除外と、添える表への障害(outage・fault)が Unreachable に当たることを確かめるため。"
  (<- (PutRow "items" #("k1") (FrozenMap {"group" "g" "label" "one" "created_at" 10}) (ExpectAbsent)))
  (<- (PutRow "notes" #("k1") (FrozenMap {"detail" "big-k1"}) (ExpectAbsent)))
  (<- (PutRow "notes" #("k1") (FrozenMap {"state" "done"}) (ExpectVersion 1)))
  (val ask (ListRows "items" :fields #("label") :join-tables #("notes")))
  (<- before (| Page Reset) ask)
  (<- (Delay (+ NOTE-KEEP-SECONDS 1)))
  (<- after (| Page Reset) ask)
  ;; 添える表 notes に outage を掛けると Unreachable。
  (<- (SetStoreOutage "notes down" :names (frozenset #("notes"))))
  (<- down-answer (on-raise ask (Unreachable detail) (Unreachable detail)))
  (<- (SetStoreOutage None :names (frozenset #("notes"))))
  ;; 添える表 notes への StoreFault も Unreachable に当たる。
  (<- (AddStoreFault (StoreFault (frozenset #("notes")) StoreOperation.READ (Unreachable "notes fault"))))
  (<- fault-answer (on-raise ask (Unreachable detail) (Unreachable detail)))
  (<- (ClearStoreFaults))
  (<- recovered (| Page Reset) ask)
  #(before after down-answer fault-answer recovered))


(deftest test-memory-joined-list-hides-expired-and-honors-outage-and-faults
  (val store (MemoryStore JOIN-SCHEMA))
  (val outcomes
    (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) (memory-records-handler store MAKER)]
                                   (memory-expiry-and-outage-check)))))
  (val before (get outcomes 0))
  (val after (get outcomes 1))
  (val down-answer (get outcomes 2))
  (val fault-answer (get outcomes 3))
  (val recovered (get outcomes 4))
  (assert (= (tuple (gfor row (get before.joined "notes") #(row.key (get row.value "detail"))))
             #(#(#("k1") "big-k1")))
          before.joined)
  (assert (= (get after.joined "notes") #()) after.joined)
  (assert (= down-answer (Unreachable "notes down")) down-answer)
  (assert (= fault-answer (Unreachable "notes fault")) fault-answer)
  (assert (= recovered after) recovered))


(deftest test-undeclared-joined-table-is-refused-with-its-name
  ;; wire の理由と client の戻し(HTTP 404 経路): join-tables の宣言に無い名だけが tables に載る。
  (val declared (tuple JOIN-SCHEMA.tables))
  (val ask (ListRows "items" :join-tables #("notes" "no-such")))
  (val reason (run (undeclared-reason ask declared #())))
  (assert (is-not reason None))
  (val wire-err (run (undeclared-refusal ask reason)))
  (assert (= #(wire-err.tables wire-err.streams) #(#("no-such") #())) wire-err)
  ;; memory 置き場でも UndeclaredTable(tables=("no-such",)) で断る。
  (val store (MemoryStore JOIN-SCHEMA))
  (try
    (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) (memory-records-handler store MAKER)] ask)))
    (assert False "宣言に無い添える表を通した")
    (except [err UndeclaredTable]
      (assert (= err.tables #("no-such")) err)))
  ;; PostgreSQL 置き場でも同じ UndeclaredTable(tables=("no-such",)) で断る。
  (when PG-DSN
    (val connections (postgres-connections 1))
    (val pg-store (prepared-store connections (fresh-prefix) :schema JOIN-SCHEMA))
    (try
      (try
        (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) inline-compute-handler
                                        (postgres-sql-handler connections)
                                        (pg-records-handler pg-store MAKER ORIGIN-HOST)]
                                       ask)))
        (assert False "PostgreSQL で宣言に無い添える表を通した")
        (except [err UndeclaredTable]
          (assert (= err.tables #("no-such")) err)))
      (finally
        (run-sql connections (drop-records-tables pg-store))
        (.close connections)))))
