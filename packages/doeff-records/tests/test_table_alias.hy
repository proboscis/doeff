;; 表の alias(_values.hy の頭の註の「表の alias」— Elasticsearch の index alias swap と同じ形)を memory と PostgreSQL の置き場が同じに
;; 答える形の検。
;;   付け替えの前の論理の名の読みは slot a を・後は slot b を読む(ReadRow・ListRows・CountRows・ReadBatch)
;;   付け替えは PutRows の行と同じ 1 回の書きで、世代の期待が違う・索引の用意が終わっていない先への付け替えは束ごと断る
;;   論理の名への書きは断る
;;   ListRows の頁の間に付け替えが入った続きの読みは、2 つの世代を混ぜずに Reset(tables = その名)
;;   WatchChanges は付け替えた表を見ている待ち手にだけ表に絞った Reset を返し、影の表に書いた変更を付け替えの後に論理の名の変更として出さない
;;   PostgreSQL の論理の名の読みは alias の解きと行の SELECT が 1 つの SQL 文
(require doeff-hy.macros [deftest defk <- val])
(import doeff [Perform run with_handlers])
(import doeff_core_effects [scheduled inline-compute-handler])
(import doeff_core_effects [PostgresConnections PostgresDatabase SqlQuery postgres-sql-handler])
(import doeff_time [SimClock sim-time-handler])
(import doeff_hy._frozen [FrozenMap])
(import doeff_records [ExpectAbsent ExpectAny Written WrittenRows Missing Refused RowsRefused Row Page Reset Changes RowChanged
                        WatchCursor Count BatchRead])
(import doeff_records [ReadRow ListRows CountRows ReadBatch PutRow PutRows RowWrite WatchChanges])
(import doeff_records [RecordsSchema TableAlias AliasState ExpectAlias SwapAlias HoldTableIndexes])
(import doeff_records [MemoryStore memory-records-handler pg-records-handler])
(import doeff_records._pg [prepare-records-store reconcile-indexes drop-records-tables])
(import doeff_records._pg_sql [read-row-statement aliased-statement])
(import doeff_records._main [DDL-DATABASE DDL-TIMEOUTS])
(import doeff_records._laws [LAW-SCHEMA MAKER])
(import tests.interpreters [session-dsn PG-DSN-VARIABLE pg-skip-reason DATABASE ORIGIN-HOST fresh-prefix open-postgres])

(val PG-DSN (session-dsn PG-DSN-VARIABLE))
(val PG-SKIP-REASON (pg-skip-reason))
(val ALIAS-SCHEMA (RecordsSchema :tables LAW-SCHEMA.tables :streams LAW-SCHEMA.streams :operators LAW-SCHEMA.operators
                                 :aliases #((TableAlias "parts"))))


;; --- 置き場の組み立て(memory と PostgreSQL — 筋書きは place = Program → 答え の関数を受ける)-------------------------------------------

(defn memory-place [#^ MemoryStore store]  ; cannot be a defk: 検の入口の組み立て
  "仮想の時計と memory の置き場(書き手 MAKER)の下で Program を回して答えを返す関数を作るため。"
  (fn [program]
    (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) (memory-records-handler store MAKER)] program)))))


(defn pg-connections []  ; cannot be a defk: 検の資源の組み立て(Program の外)
  "検の置き場の query 用と索引の用意の接続の貸し出しを作るため。"
  #((PostgresConnections #((PostgresDatabase :name DATABASE :dsn PG-DSN)) :size 2)
    (PostgresConnections #((PostgresDatabase :name DDL-DATABASE :dsn PG-DSN)) :size 1 :timeouts DDL-TIMEOUTS)))


(defn run-sql [connections program]  ; cannot be a defk: 検の入口で run を撃つ
  "SQL の答え手 2 つと仮想の時計の下で program を 1 回走らせるため(置き場の用意と後片付け)。"
  (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) inline-compute-handler
                                  (postgres-sql-handler (get connections 0)) (postgres-sql-handler (get connections 1))]
                                 program))))


(defn pg-place [connections store]  ; cannot be a defk: 検の入口の組み立て
  "仮想の時計と PostgreSQL の置き場(書き手 MAKER)の下で Program を回して答えを返す関数を作るため。"
  (fn [program]
    (run-sql connections (with_handlers [(pg-records-handler store MAKER ORIGIN-HOST)] program))))


(defn with-places [scenario]  ; cannot be a defk: 検の入口で筋書きを 2 つの置き場で回す
  "筋書き scenario(place → None)を memory の置き場と(在れば)PostgreSQL の置き場で 1 度ずつ回すため。"
  (scenario (memory-place (MemoryStore ALIAS-SCHEMA)))
  (when PG-DSN
    (setv connections (pg-connections)
          store (run-sql connections (prepare-records-store DATABASE DDL-DATABASE ALIAS-SCHEMA (fresh-prefix))))
    (try
      (scenario (pg-place connections store))
      (finally
        (run-sql connections (drop-records-tables store))))))


;; --- 筋書きの部品 --------------------------------------------------------------------------------------------------------------------

(defn #^ RowWrite part [#^ str table #^ str id #^ str label]  ; cannot be a defk: 検の支度が同期に組む値
  "表 table の行 id(label)を無条件に書く書き 1 つを組むため。"
  (RowWrite table #(id) (FrozenMap {"label" label}) (ExpectAny)))


(defk seeded []
  {:pre [] :post [(: % tuple)]}
  "slot a に前の世代(p1・p2 = old)、影の slot b に新しい世代(p1・p3 = new)を書き、答えを返すため。"
  (<- old (Perform (PutRows #((part "parts~a" "p1" "old") (part "parts~a" "p2" "old")))))
  (<- new (Perform (PutRows #((part "parts~b" "p1" "new") (part "parts~b" "p3" "new")))))
  #(old new))


(defk swap [generation]
  {:pre [(: generation int)] :post [(: % (| WrittenRows RowsRefused))]}
  "位置の行(tickets)の書きと同じ束で parts を slot b へ付け替えるため(答えは値のまま)。"
  (<- answer (Perform (PutRows #((RowWrite "tickets" #("g" "pos") (FrozenMap {"owner" "rebuilt"}) (ExpectAny)))
                                :aliases #((SwapAlias "parts" generation "b")))))
  answer)


(defk logical-view []
  {:pre [] :post [(: % tuple)]}
  "論理の名 parts の読み(p1 の ReadRow・全部の ListRows の鍵と label・CountRows・ReadBatch の p1・頁の alias の今)を並べるため。"
  (<- row (Perform (ReadRow "parts" #("p1"))))
  (<- page (ListRows "parts"))
  (<- counted (Perform (CountRows "parts")))
  (<- batch (Perform (ReadBatch #((ReadRow "parts" #("p1")) (ListRows "parts" :limit 1)))))
  #((if (isinstance row Row) (get row.value "label") row)
    (tuple (gfor r page.rows #((get r.key 0) (get r.value "label"))))
    counted
    (get (. (get batch.items 0) value) "label")
    page.alias
    (. (get batch.items 1) alias)))


(defk watched [tables cursor]
  {:pre [(: tables tuple) (: cursor WatchCursor)] :post [(: % (| Changes Reset))]}
  "tables の変更を cursor から待たずに読むため。"
  (<- answer (WatchChanges tables cursor :timeout 0.0))
  answer)


(defk head []
  {:pre [] :post [(: % WatchCursor)]}
  "置き場の今の頭(版と変更の番号 — 一覧の頁の位置)を読むため。"
  (<- page (ListRows "tickets" :limit 1))
  (WatchCursor page.epoch page.sequence))


;; --- 筋書き --------------------------------------------------------------------------------------------------------------------------

(deftest test-the-logical-name-reads-slot-a-before-the-swap-and-slot-b-after
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  (defn scenario [place]
    (place (seeded))
    (setv before (place (logical-view)))
    (assert (= before #("old" #(#("p1" "old") #("p2" "old")) (Count 2) "old" (AliasState "parts" "a" 0) (AliasState "parts" "a" 0)))
            before)
    (setv swapped (place (swap 0)))
    (assert (isinstance swapped WrittenRows) swapped)
    (setv after (place (logical-view)))
    (assert (= after #("new" #(#("p1" "new") #("p3" "new")) (Count 2) "new" (AliasState "parts" "b" 1) (AliasState "parts" "b" 1)))
            after)
    ;; 物理の名は付け替えに依らず同じ slot を読む。
    (setv physical (place (Perform (ReadRow "parts~a" #("p1")))))
    (assert (= (get physical.value "label") "old") physical)
    ;; 付け替えは束の行の変更の前に変更の列の番号を 1 つ採る(束の最後の変更の位置は一覧の頭のまま)。
    (assert (= (place (head)) (. (get swapped.items -1) position)) swapped))
  (with-places scenario))


(deftest test-a-write-to-the-logical-name-is-refused
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  (defn scenario [place]
    (setv start (place (head)))
    (setv rows (place (Perform (PutRows #((part "tickets" "x" "y") (part "parts" "p1" "z"))))))
    (assert (and (isinstance rows RowsRefused) (= rows.index 1) (= rows.table "parts")) rows)
    (setv one (place (Perform (PutRow "parts" #("p1") (FrozenMap {"label" "z"}) (ExpectAbsent)))))
    (assert (isinstance one Refused) one)
    (assert (= (place (head)) start) "断った書きは何も書かない"))
  (with-places scenario))


(deftest test-a-stale-alias-generation-refuses-the-whole-batch
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  (defn scenario [place]
    (place (seeded))
    (place (swap 0))
    (setv at (place (head)))
    ;; 前の版の書き手(世代 0 を期待)の書きは、付け替えの後に束ごと断られる。
    (setv stale (place (Perform (PutRows #((part "parts~a" "p1" "late")) :aliases #((ExpectAlias "parts" 0))))))
    (assert (and (isinstance stale RowsRefused) (= stale.index 1) (= stale.table "parts") (= stale.key #())) stale)
    (assert (= (place (head)) at) "断った束は何も書かない")
    (assert (= (get (. (place (Perform (ReadRow "parts~a" #("p1")))) value) "label") "old"))
    ;; 2 度目の付け替え(世代 0 のまま)も断る。今の世代なら書ける。
    (setv again (place (swap 0)))
    (assert (isinstance again RowsRefused) again)
    (setv fresh (place (Perform (PutRows #((part "parts~b" "p4" "new")) :aliases #((ExpectAlias "parts" 1))))))
    (assert (isinstance fresh WrittenRows) fresh)
    ;; 既に指している slot への付け替えは断る。
    (setv same (place (Perform (PutRows #((part "tickets" "x" "y")) :aliases #((SwapAlias "parts" 1 "b"))))))
    (assert (isinstance same RowsRefused) same))
  (with-places scenario))


(deftest test-a-page-continued-across-a-swap-resets-instead-of-mixing-generations
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  (defn scenario [place]
    (place (seeded))
    (setv first (place (ListRows "parts" :limit 1)))
    (assert (= (. first next-cursor generation) 0) first)
    (place (swap 0))
    (setv rest (place (ListRows "parts" :cursor first.next-cursor :limit 1)))
    (assert (and (isinstance rest Reset) (= rest.tables #("parts"))) rest)
    ;; 頭から読み直すと新しい世代だけ。
    (setv top (place (ListRows "parts" :limit 1)))
    (setv tail (place (ListRows "parts" :cursor top.next-cursor :limit 1)))
    (assert (= (lfor page #(top tail) r page.rows (get r.value "label")) ["new" "new"]) #(top tail)))
  (with-places scenario))


(deftest test-watchers-of-the-swapped-table-reset-and-never-see-shadow-writes-as-logical-changes
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  (defn scenario [place]
    (place (Perform (PutRows #((part "parts~a" "p1" "old")))))
    (setv start (place (head)))
    ;; 作り直しの間: 前の版が slot a を書き続け、新しい版が影の slot b を組む。
    (place (Perform (PutRows #((part "parts~a" "p1" "old2")))))
    (place (Perform (PutRows #((part "parts~b" "p1" "new")))))
    (setv swapped (place (swap 0)))
    (setv swap-at (- (. (get swapped.items 0) position sequence) 1))
    (place (Perform (PutRows #((part "parts~b" "p2" "new")))))
    ;; parts と tickets を見る待ち手: 付け替えの前は slot a の変更だけ(影の slot の書きは出ない)。
    (setv early (place (watched #("parts" "tickets") start)))
    (assert (isinstance early Changes) early)
    (assert (= (lfor c early.items #(c.table (get c.value "label"))) [#("parts" "old2")]) early.items)
    (assert (= early.cursor.sequence (- swap-at 1)) #(early.cursor swap-at))
    ;; 次は付け替えた表に絞った Reset(floor = 付け替えの位置)。
    (setv reset (place (watched #("parts" "tickets") early.cursor)))
    (assert (= reset (Reset start.epoch swap-at :tables #("parts"))) reset)
    ;; 付け替えの位置から続けると、付け替えと同じ束の位置の行と、付け替えの後の影の slot の書きだけが来る(影の slot の書きは論理の名の
    ;; 変更として — 付け替えの前の影の書き p1 = new は出ない)。
    (setv late (place (watched #("parts" "tickets") (WatchCursor reset.epoch reset.floor))))
    (assert (= (lfor c late.items #(c.table (get c.key -1))) [#("tickets" "pos") #("parts" "p2")]) late.items)
    ;; 付け替えた表を見ていない待ち手には Reset を返さない。
    (setv others (place (watched #("tickets") start)))
    (assert (and (isinstance others Changes) (= (lfor c others.items c.table) ["tickets"])) others)
    ;; 物理の名を見る待ち手は名のとおりの slot の変更を受ける(付け替えで Reset しない)。
    (setv shadow (place (watched #("parts~b") start)))
    (assert (= (lfor c shadow.items #(c.table (get c.key 0))) [#("parts~b" "p1") #("parts~b" "p2")]) shadow))
  (with-places scenario))


(deftest test-a-memory-swap-before-the-shadow-indexes-are-ready-is-refused
  (val store (MemoryStore ALIAS-SCHEMA))
  (val place (memory-place store))
  (place (seeded))
  (place (HoldTableIndexes (frozenset #("parts~b"))))
  (val held (place (swap 0)))
  (assert (and (isinstance held RowsRefused) (in "索引" held.reason)) held)
  (assert (= (get (place (logical-view)) 4) (AliasState "parts" "a" 0)))
  (place (HoldTableIndexes (frozenset)))
  (assert (isinstance (place (swap 0)) WrittenRows)))


(deftest test-a-pg-swap-before-the-shadow-indexes-are-built-is-refused
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  ;; alias を宣言する前から在る置き場: 影の表の索引は表の用意(起動の道)では作らず、索引の用意の task が作る(_pg.hy の頭の註の
  ;; 「索引の用意」)。作り終えるまで付け替えは断り、作り終えた後は通る。
  (val connections (pg-connections))
  (val prefix (fresh-prefix))
  (val plain (run-sql connections (prepare-records-store DATABASE DDL-DATABASE LAW-SCHEMA prefix)))
  (try
    (val store (run-sql connections (prepare-records-store DATABASE DDL-DATABASE ALIAS-SCHEMA prefix)))
    (val place (pg-place connections store))
    (place (seeded))
    (val held (place (swap 0)))
    (assert (and (isinstance held RowsRefused) (in "索引" held.reason)) held)
    (assert (= (get (place (logical-view)) 4) (AliasState "parts" "a" 0)))
    (run-sql connections (reconcile-indexes store))
    (assert (isinstance (place (swap 0)) WrittenRows))
    (assert (= (get (place (logical-view)) 0) "new"))
    (finally
      (run-sql connections (drop-records-tables plain)))))


(deftest test-the-pg-logical-read-resolves-the-alias-inside-one-statement
  ;; READ COMMITTED の文ごとの snapshot で、alias の解きと行の SELECT の間に付け替えが入らないよう、論理の名の読みは 1 つの文
  ;; (aliased-statement — 2 つの slot の文を alias の今の副問いの LATERAL の中に並べる)。指していない slot の文は流れない(gating)。
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  (val a (run (read-row-statement "t_" "parts" "[\"p1\"]" None)))
  (val b (run (read-row-statement "t_" "parts~b" "[\"p1\"]" None)))
  (val statement (run (aliased-statement "t_" "parts" a b)))
  (assert (= (.count statement.text "SELECT key") 2) statement.text)
  (assert (not-in ";" statement.text) statement.text)
  (assert (in "t_table_aliases" statement.text) statement.text)
  ;; 引数は slot ごとに名を分ける(2 つの文の :ledger が重ならない)。
  (assert (= (sorted (gfor p statement.params p.name)) ["key__a" "key__b" "ledger__a" "ledger__b"]) statement.params)
  (assert (= (sorted (gfor p statement.params :if (.startswith p.name "ledger") p.value)) ["parts" "parts~b"]) statement.params)
  ;; 本物の置き場で計画を読む: 指していない slot の分枝は gating の条件(One-Time Filter)の下に在る。
  (val connections (pg-connections))
  (val store (run-sql connections (prepare-records-store DATABASE DDL-DATABASE ALIAS-SCHEMA (fresh-prefix))))
  (try
    (val a-real (run (read-row-statement store.prefix "parts" "[\"p1\"]" None)))
    (val b-real (run (read-row-statement store.prefix "parts~b" "[\"p1\"]" None)))
    (val real (run (aliased-statement store.prefix "parts" a-real b-real)))
    (val watcher (open-postgres))
    (try
      (val text (.join "\n" (gfor row (.fetchall (.execute watcher (+ "EXPLAIN " (.replace (.replace (.replace (.replace real.text ":ledger__a" "'parts'") ":ledger__b" "'parts~b'") ":key__a" "'[\"p1\"]'") ":key__b" "'[\"p1\"]'")))) (get row 0))))
      (assert (>= (.count text "One-Time Filter") 2) text)
      (finally (.close watcher)))
    (finally
      (run-sql connections (drop-records-tables store)))))
