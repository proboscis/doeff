;; 大きい欄(TableDecl.bulk)の版 2: 書きが大きい欄を列 bulk に分けて置き、手入れの移し(MoveBulkFields)が前の版の書いた行を分け直す。
;;   - PutRow・PutRows・PutRowsAppendEvents の書きは、大きい欄を列 bulk に・残りを payload に置き(値に大きい欄の無い行は bulk が NULL)、
;;     変更の列には値の全部を積む。読みの答えは、分けない memory の置き場の答えと同じ値と版。
;;   - 大きい欄で並べる読み(descending-by・ascending-by)と下限で数える読み(CountRows の at-least)は、絞りと同じ所で NotIndexed に断る
;;     (並べる式も payload から読む — memory と PostgreSQL の置き場が同じ答え)。
;;   - 移しは表ごとに limit 行まで分け直し、値・版・刻・書き手・置き場の版・変更の列を動かさない。候補を読んだ後に書かれた行は上書きせず、
;;     後から前の版の形で書かれた行は次の回が拾う。残りの数は計器の gauge records_bulk_unmoved_<表> に出る。
(require doeff-hy.macros [deftest defhandler defk <- val var on-raise])
(import json)
(import doeff [run with_handlers EffectBase])
(import doeff_core_effects [scheduled inline-compute-handler memory-meter-handler MeterSettings ReadMeter state])
(import doeff_core_effects [postgres-sql-handler])
(import doeff_time [SimClock sim-time-handler])
(import doeff_hy._frozen [FrozenMap])
(import doeff_records [RecordsSchema TableDecl FieldDecl StreamDecl DescendingWithin NotIndexed Row Page Count Written])
(import doeff_records [PutRow PutRows RowWrite PutRowsAppendEvents EventAppend ReadRow ListRows ReadBatch CountRows ExpectAbsent ExpectVersion])
(import doeff_records [MemoryStore memory-records-handler pg-records-handler])
(import doeff_records._maintenance [MoveBulkFields BulkMoved SweepExpired Swept PruneChanges Pruned maintenance-tick bulk-metric-helps])
(import doeff_records._admission [key-text canonical-json])
(import doeff_records._pg [PreparedStore prepare-records-store drop-records-tables query-rows bulk-moves-json])
(import doeff_records._pg_sql [bulk-candidates-statement move-bulk-statement])
(import tests.interpreters [session-dsn PG-DSN-VARIABLE pg-skip-reason DATABASE ORIGIN-HOST open-postgres postgres-connections
                            fresh-prefix run-sql])

(val PG-DSN (session-dsn PG-DSN-VARIABLE))
(val PG-SKIP-REASON (pg-skip-reason))
(val WRITER "notes-writer")
(val FIELDS #((FieldDecl "id" #(WRITER)) (FieldDecl "title" #(WRITER)) (FieldDecl "state" #(WRITER))
              (FieldDecl "rank" #(WRITER)) (FieldDecl "body" #(WRITER)) (FieldDecl "attachments" #(WRITER))))
(val BULK #("body" "attachments"))
(val NOTES (TableDecl :name "notes" :key-fields #("id") :fields FIELDS :indexes #("title") :descending #("rank")
                      :descending-within #((DescendingWithin "title" "rank"))
                      :states #("open" "closed") :terminal #("closed") :initial "open"
                      :bulk BULK))
(val JOURNAL (StreamDecl :name "journal" :writers #(WRITER)))
(val SCHEMA (RecordsSchema :tables (FrozenMap {"notes" NOTES}) :streams (FrozenMap {"journal" JOURNAL})))
;; 大きい欄を持つ行・大きい欄の無い行・指数で綴る小数を持つ行(jsonb で組み直すと整数の綴りになる数 — 移しが綴りを変えない事の照らし)。
(val VALUES {"n1" {"title" "alpha" "rank" 3 "body" (* "long body " 200) "attachments" ["a.png" "b.png"]}
             "n2" {"title" "beta" "rank" 1 "body" "short"}
             "n3" {"title" "gamma" "rank" 2}
             "n4" {"title" "delta" "rank" 4 "body" "ü 本文" "attachments" [1e+16 0.5]}})


(defn whole-value [#^ str name #^ dict value]
  "置いた行の値(宣言が足す欄 — 鍵の欄と生まれる行の状態 — を合わせた写像)。"
  (| {"id" name "state" "open"} value))


(defn split-of [#^ dict value]
  "値を、列 payload と列 bulk に置かれるべき 2 つの綴りにする(大きい欄の無い値は bulk が None)。"
  (setv moved (dfor #(field v) (.items value) :if (in field BULK) field v))
  #((canonical-json (dfor #(field v) (.items value) :if (not-in field BULK) field v))
    (if moved (canonical-json moved) None)))


(defn stored-columns [prefix #^ str name]
  "行 name の列 payload・bulk・version・updated_at・updated_by・epoch を素の SELECT で読む。"
  (with [connection (open-postgres)]
    (.fetchone (.execute connection (.format "SELECT payload, bulk, version, updated_at, updated_by, epoch FROM {}state_rows WHERE ledger = 'notes' AND key = %s"
                                             prefix)
                         #((key-text #(name)))))))


(defn change-count [prefix]
  (with [connection (open-postgres)]
    (get (.fetchone (.execute connection (.format "SELECT count(*) FROM {}row_changes" prefix))) 0)))


(defn join-rows [prefix names]
  "行 names を、検の素の UPDATE で値の全部を payload に置いた形(列 bulk が NULL — 前の版の書きの形)に戻す(版と刻は触らない)。"
  (with [connection (open-postgres)]
    (for [name names]
      (setv key (key-text #(name)))
      (setv #(stored stored-bulk) (.fetchone (.execute connection (.format "SELECT payload, bulk FROM {}state_rows WHERE ledger = 'notes' AND key = %s" prefix)
                                                       #(key))))
      (.execute connection (.format "UPDATE {}state_rows SET payload = %s, bulk = NULL WHERE ledger = 'notes' AND key = %s" prefix)
                #((canonical-json (| (if (is stored-bulk None) {} (json.loads stored-bulk)) (json.loads stored))) key)))))


(defn pg-run [connections store program]
  (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) inline-compute-handler
                                  (postgres-sql-handler connections)
                                  (pg-records-handler store WRITER ORIGIN-HOST)]
                                 program))))


(defn memory-run [store program]
  (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) inline-compute-handler
                                  (memory-records-handler store WRITER)]
                                 program))))


(defn with-pg [test]
  "使い捨ての接頭辞の置き場を用意して test(connections store) を走らせ、表を片付ける。"
  (setv connections (postgres-connections))
  (setv store (run-sql connections (prepare-records-store DATABASE DATABASE SCHEMA (fresh-prefix))))
  (try
    (test connections store)
    (finally
      (run-sql connections (drop-records-tables store))
      (.close connections))))


;; --- 書きの分け(c09eb0df9)--------------------------------------------------------------------------------

(defk write-everything []
  {:pre [] :post [(: % None)] :tags {:context "records" :role "program"}}
  "3 つの書き(PutRow・PutRows・PutRowsAppendEvents)で VALUES を置き、大きい欄に触らない書きと大きい欄を外す書きを重ねるため。"
  (<- (PutRow "notes" #("n1") (FrozenMap (get VALUES "n1")) (ExpectAbsent)))
  (<- (PutRows #((RowWrite "notes" #("n2") (get VALUES "n2") (ExpectAbsent))
                 (RowWrite "notes" #("n3") (get VALUES "n3") (ExpectAbsent)))))
  (<- (PutRowsAppendEvents #((RowWrite "notes" #("n4") (get VALUES "n4") (ExpectAbsent)))
                           #((EventAppend "journal" "k1" {"n" 1}))))
  ;; n1 は題だけを書く(大きい欄は今の行から残る)・n2 は本文を null にして外す(大きい欄の無くなった行は列 bulk が NULL に戻る)。
  (<- (PutRow "notes" #("n1") (FrozenMap {"title" "alpha-2"}) (ExpectVersion 1)))
  (<- (PutRows #((RowWrite "notes" #("n2") {"title" "beta" "rank" 1 "body" None} (ExpectVersion 1)))))
  None)


(defn listed-rows [answer]
  "頁の行を鍵と版と値の比べられる形にする。"
  (tuple (gfor row answer.rows #(row.key row.version (dict row.value)))))


(defk read-everything []
  {:pre [] :post [(: % tuple)] :tags {:context "records" :role "program"}}
  "行の読み・fields の無い一覧・大きい欄を外した一覧・束の読みの答えを、鍵と版と値の比べられる形で集めるため。"
  (var rows #())
  (for [name (sorted VALUES)]
    (<- row (ReadRow "notes" #(name)))
    (:= rows (+ rows #(#(name row.version (dict row.value))))))
  (<- page (ListRows "notes"))
  (<- narrow (ListRows "notes" :fields #("title" "rank")))
  (<- batch (ReadBatch #((ReadRow "notes" #("n4")) (ListRows "notes" :fields #("body")))))
  #(rows (listed-rows page) (listed-rows narrow) (dict (. (get batch.items 0) value)) (listed-rows (get batch.items 1))))


(defk write-then-read []
  {:pre [] :post [(: % tuple)] :tags {:context "records" :role "program"}}
  "書きの全部を撃ってから読みの答えを集めるため。"
  (<- (write-everything))
  (<- answers (read-everything))
  answers)


(deftest test-writes-put-bulk-fields-in-the-bulk-column-and-read-like-memory
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  (val memory (memory-run (MemoryStore SCHEMA) (write-then-read)))
  (defn check [connections store]
    (setv answers (pg-run connections store (write-then-read)))
    ;; 読みの答えは分けない memory の置き場と同じ値と版。
    (assert (= answers memory) #(answers memory))
    ;; 置いた列: n1(題を重ねた行)は大きい欄を bulk に・n2(本文を外した行)と n3 は bulk が NULL・n4(束の書き)は bulk に。
    (setv n1 (| (whole-value "n1" (get VALUES "n1")) {"title" "alpha-2"}))
    (setv n2 (dfor #(f v) (.items (whole-value "n2" (get VALUES "n2"))) :if (!= f "body") f v))
    (for [#(name value) [#("n1" n1) #("n2" n2) #("n3" (whole-value "n3" (get VALUES "n3"))) #("n4" (whole-value "n4" (get VALUES "n4")))]]
      (setv columns (stored-columns store.prefix name))
      (setv expected (split-of value))
      (assert (= #((get columns 0) (get columns 1)) expected) #(name columns expected)))
    ;; 変更の列は値の全部を積む(束の書きの n4 の最後の変更)。
    (with [connection (open-postgres)]
      (setv change (get (.fetchone (.execute connection (.format "SELECT payload FROM {}row_changes WHERE ledger = 'notes' AND key = %s ORDER BY seq DESC LIMIT 1"
                                                                store.prefix)
                                            #((key-text #("n4")))))
                       0)))
    (assert (= change (canonical-json (whole-value "n4" (get VALUES "n4")))) change))
  (with-pg check))


;; --- 大きい欄で並べる・数える読みの断り -----------------------------------------------------------------------

(defk asked [ask]
  {:pre [(: ask EffectBase)] :post [(: % (| Page Count Written BulkMoved NotIndexed))] :tags {:context "records" :role "program"}}
  "読み ask を 1 つ撃つため(断りは Raise で上がる)。"
  (<- answer ask)
  answer)


(defk caught [ask]
  {:pre [(: ask EffectBase)] :post [(: % (| Page Count Written BulkMoved NotIndexed))] :tags {:context "records" :role "program"}}
  "読み ask の答えか、上がった NotIndexed を値にして返すため。"
  (<- answer (on-raise (asked ask) (NotIndexed fields) (NotIndexed fields)))
  answer)


(defk refused-orders []
  {:pre [] :post [(: % tuple)] :tags {:context "records" :role "program"}}
  "大きい欄で並べる読み・下限で数える読み(と、大きくない欄で並べる対照)の答えを集めるため。"
  (<- (PutRow "notes" #("n1") (FrozenMap (get VALUES "n1")) (ExpectAbsent)))
  (<- down (caught (ListRows "notes" :descending-by "body")))
  (<- up (caught (ListRows "notes" :ascending-by "attachments")))
  (<- counted (caught (CountRows "notes" :at-least #("body" 0))))
  (<- control (caught (ListRows "notes" :ascending-by "rank")))
  #(down up counted control))


(defn assert-orders-refused [answers]
  (setv #(down up counted control) answers)
  (assert (= down (NotIndexed #("body"))) down)
  (assert (= up (NotIndexed #("attachments"))) up)
  (assert (= counted (NotIndexed #("body"))) counted)
  (assert (and (isinstance control Page) (= (len control.rows) 1)) control))


(deftest test-memory-refuses-ordering-or-counting-by-a-bulk-field
  (assert-orders-refused (memory-run (MemoryStore SCHEMA) (refused-orders))))


(deftest test-postgres-refuses-ordering-or-counting-by-a-bulk-field
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  (with-pg (fn [connections store] (assert-orders-refused (pg-run connections store (refused-orders))))))


;; --- 移し(c1185fa84)----------------------------------------------------------------------------------------

(defk put-values []
  {:pre [] :post [(: % None)] :tags {:context "records" :role "program"}}
  "VALUES の行を置くため。"
  (for [#(name value) (.items VALUES)]
    (<- (PutRow "notes" #(name) (FrozenMap value) (ExpectAbsent))))
  None)


(defk put-then-move []
  {:pre [] :post [(: % BulkMoved)] :tags {:context "records" :role "program"}}
  "VALUES を置いてから移しを 1 回撃つため。"
  (<- (put-values))
  (<- answer (MoveBulkFields))
  answer)


(defk moved-rounds [count]
  {:pre [(: count int)] :post [(: % tuple)] :tags {:context "records" :role "program"}}
  "1 行ずつの移しを count 回撃ち、答えの列を返すため。"
  (var answers #())
  (for [_ (range count)]
    (<- answer (MoveBulkFields :limit 1))
    (:= answers (+ answers #(answer))))
  answers)


(deftest test-the-move-splits-old-rows-without-moving-values-versions-or-changes
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  (defn check [connections store]
    (pg-run connections store (put-values))
    (join-rows store.prefix (sorted VALUES))
    (setv before (dfor name VALUES name (stored-columns store.prefix name)))
    (setv changes (change-count store.prefix))
    (setv read-before (pg-run connections store (read-everything)))
    ;; 大きい欄を持つ行は n1・n2・n4 の 3 つ。1 行ずつの移しは 3 回で移し終え、残りは 2・1・0、4 回目は移す行が無い。
    (setv answers (pg-run connections store (moved-rounds 4)))
    (assert (= (tuple (gfor a answers (get a.moved "notes"))) #(1 1 1 0)) answers)
    (assert (= (tuple (gfor a answers (get a.unmoved "notes"))) #(2 1 0 0)) answers)
    (for [name VALUES]
      (setv after (stored-columns store.prefix name))
      ;; 列は書きと同じ綴りに分かれ(指数の小数も綴りのまま)、版・刻・書き手・置き場の版は動かない。
      (assert (= #((get after 0) (get after 1)) (split-of (whole-value name (get VALUES name)))) #(name after))
      (assert (= (cut after 2 None) (cut (get before name) 2 None)) #(name after (get before name))))
    (assert (= (change-count store.prefix) changes))
    (assert (= (pg-run connections store (read-everything)) read-before)))
  (with-pg check))


(defk candidate-moves [store]
  {:pre [(: store PreparedStore)] :post [(: % str)] :tags {:context "records" :role "program"}}
  "移しの候補を読み、分け直しの引数の綴りを作るため(移しの文を流す前に書きを挟む検の口)。"
  (<- statement (bulk-candidates-statement store.prefix "notes" BULK 1000))
  (<- records (query-rows store.database statement))
  (<- moves (bulk-moves-json BULK records))
  moves)


(defk moved-keys [store moves]
  {:pre [(: store PreparedStore) (: moves str)] :post [(: % tuple)] :tags {:context "records" :role "program"}}
  "分け直しの文を流し、書き換えた行の鍵を返すため。"
  (<- statement (move-bulk-statement store.prefix "notes" moves))
  (<- rows (query-rows store.database statement))
  (tuple (sorted (gfor row rows (get row 0)))))


(deftest test-the-move-does-not-overwrite-a-write-after-its-read
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  (defn check [connections store]
    (pg-run connections store (put-values))
    (join-rows store.prefix #("n1" "n2"))
    ;; 候補を読んだ後に n1 へ書きが入る(版 2 になる)。読んだ時の綴りで分け直す文は n1 を書き換えず、n2 だけを分ける。
    (setv moves (run-sql connections (candidate-moves store)))
    (pg-run connections store (asked (PutRow "notes" #("n1") (FrozenMap {"title" "alpha-2" "body" "new body"}) (ExpectVersion 1))))
    (setv keys (run-sql connections (moved-keys store moves)))
    (assert (= keys #((key-text #("n2")))) keys)
    (setv n1 (| (whole-value "n1" (get VALUES "n1")) {"title" "alpha-2" "body" "new body"}))
    (setv columns (stored-columns store.prefix "n1"))
    (assert (= #((get columns 0) (get columns 1) (get columns 2)) (+ (split-of n1) #(2))) columns)
    ;; 入れ替えの途中に前の版が値の全部を payload に置いた行(n3 に本文を足した行)は、次の移しの回が拾う。
    (with [connection (open-postgres)]
      (.execute connection (.format "UPDATE {}state_rows SET payload = %s, bulk = NULL, version = version + 1 WHERE ledger = 'notes' AND key = %s"
                                    store.prefix)
                #((canonical-json (whole-value "n3" (| (get VALUES "n3") {"body" "late"}))) (key-text #("n3")))))
    (setv answer (pg-run connections store (asked (MoveBulkFields))))
    (assert (= answer (BulkMoved :moved (FrozenMap {"notes" 1}) :unmoved (FrozenMap {"notes" 0}))) answer)
    (setv n3 (stored-columns store.prefix "n3"))
    (assert (= #((get n3 0) (get n3 1)) (split-of (whole-value "n3" (| (get VALUES "n3") {"body" "late"})))) n3))
  (with-pg check))


;; --- 計器(cd4144dda)-----------------------------------------------------------------------------------------

(defhandler moving-store [#^ list asked]
  ;; 引数に残す理由: asked は検が読む控え — 置き場の顔が撃たれた MoveBulkFields をそこへ積む。回収と刈りは何もしない。
  (SweepExpired []
    (resume (Swept 0)))
  (PruneChanges [keep-seconds upto-seq]
    (resume (Pruned 0 0)))
  (MoveBulkFields [limit]
    (.append asked effect)
    (resume (BulkMoved :moved (FrozenMap {"notes" 2 "turn-job" 0}) :unmoved (FrozenMap {"notes" 5 "turn-job" 0})))))


(defk ticked-meter [move-bulk asked]
  {:pre [(: move-bulk bool) (: asked list)] :post [(: % tuple)] :tags {:context "records" :role "program"}}
  "手入れを 1 回撃ち、#(結果 計器の断面) を返すため。"
  (<- report (with_handlers [(moving-store asked)] (maintenance-tick 60.0 None :move-bulk move-bulk)))
  (<- meter (ReadMeter))
  #(report meter))


(deftest test-the-tick-moves-bulk-fields-and-gauges-the-unmoved-rows
  (val asked [])
  (val seen (run (scheduled (with_handlers [(state) (memory-meter-handler (MeterSettings))] (ticked-meter True asked)))))
  (val report (get seen 0))
  (val meter (get seen 1))
  (assert (= asked [(MoveBulkFields)]) asked)
  (assert (= report.bulk (BulkMoved :moved (FrozenMap {"notes" 2 "turn-job" 0}) :unmoved (FrozenMap {"notes" 5 "turn-job" 0}))) report)
  (assert (= (get meter.gauges "records_bulk_unmoved_notes") 5.0) meter)
  (assert (= (get meter.gauges "records_bulk_unmoved_turn_job") 0.0) meter)
  (assert (= (get meter.counters "records_bulk_moved_notes") 2.0) meter)
  ;; 移さない手入れ(既定)は MoveBulkFields を撃たない。
  (val idle [])
  (val quiet (run (scheduled (with_handlers [(state) (memory-meter-handler (MeterSettings))] (ticked-meter False idle)))))
  (assert (and (= idle []) (is (. (get quiet 0) bulk) None)) quiet)
  ;; /metrics の説明は大きい欄を宣言した表ごとに 2 つ。
  (val helps (run (bulk-metric-helps #("turn-job"))))
  (assert (= (set helps) #{"records_bulk_unmoved_turn_job" "records_bulk_moved_turn_job"}) helps))


(deftest test-the-memory-store-has-nothing-to-move
  (val answer (memory-run (MemoryStore SCHEMA) (put-then-move)))
  (assert (= answer (BulkMoved :moved (FrozenMap {"notes" 0}) :unmoved (FrozenMap {"notes" 0}))) answer))
