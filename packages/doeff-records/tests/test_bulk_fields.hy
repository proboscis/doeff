;; 大きい欄(TableDecl.bulk)の版 1: 宣言の制約・大きい欄の絞りの断り・PostgreSQL の置き場の列 bulk の読みの合わせと ListRows の fields の約束。
;;   - 鍵の欄・索引の欄・上から読む欄・等号の欄・状態の欄は大きい欄にできない(作る時に ValueError)。大きい欄は repr に出さず、表の要約は
;;     空でない時だけ変わる。
;;   - 大きい欄の where・where-any は memory と PostgreSQL の置き場が同じ所(admission.where-refusal)で NotIndexed に断る。
;;   - payload と bulk に分けて置いた行を ReadRow・ReadBatch・fields の無い ListRows・書きの土台の読み(行の錠の読み)で読んだ答えが、分ける
;;     前の行と同じ値になる。保存の綴りは解かずに継ぐので欄の並びは違い得る — 綴りを JSON として読んだ写像が分ける前と同じ。同じ欄が両方に
;;     在れば payload の値。
;;   - ListRows の fields が大きい欄を 1 つも含まなければ、DB へ流れた文は列 bulk を選ばない(含むか fields が無ければ選ぶ)。
;; 行を分けるのは今の版の書きでなく検の素の UPDATE(分ける書きは次の版)。
(require doeff-hy.macros [deftest defk <- val var on-raise])
(import hashlib)
(import json)
(import pytest)
(import doeff [run with_handlers])
(import doeff_core_effects [scheduled inline-compute-handler])
(import doeff_core_effects [postgres-sql-handler])
(import doeff_time [SimClock sim-time-handler])
(import doeff_hy._frozen [FrozenMap frozen-json-object])
(import doeff_records [RecordsSchema TableDecl FieldDecl DescendingWithin NotIndexed Row Page BatchRead Written])
(import doeff_records [PutRow ReadRow ListRows ReadBatch ExpectAbsent ExpectVersion])
(import doeff_records [MemoryStore memory-records-handler pg-records-handler])
(import doeff_records._admission [key-text canonical-json])
(import doeff_records._values [stored-text])
(import doeff_records._schema_digest [schema-digests])
(import doeff_records._pg [prepare-records-store drop-records-tables])
(import tests.interpreters [session-dsn PG-DSN-VARIABLE pg-skip-reason DATABASE ORIGIN-HOST open-postgres postgres-connections
                            fresh-prefix run-sql])
(import tests.sql_probes [QueryProbe StatementCounts probe-sql-handler effect-round-trips])

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
(val SCHEMA (RecordsSchema :tables (FrozenMap {"notes" NOTES})))
;; 置く行: 大きい欄を持つ行 2 つと、大きい欄の無い行 1 つ(分けない — 列 bulk が NULL の行)。
(val VALUES {"n1" {"title" "alpha" "rank" 3 "body" (* "long body " 200) "attachments" ["a.png" "b.png"]}
             "n2" {"title" "beta" "rank" 1 "body" "short"}
             "n3" {"title" "gamma" "rank" 2}})


;; --- 宣言 ------------------------------------------------------------------------------------------------

(deftest test-fields-the-store-reads-in-sql-cannot-be-bulk
  ;; 鍵の欄・索引の欄・上から読む欄・等号の欄(等号の欄は索引の欄でもある)・状態の欄・宣言の外の欄は大きい欄にできない。
  (for [#(bulk words) [#(#("id") "鍵の欄")
                       #(#("title") "索引の欄")
                       #(#("rank") "上から読む欄")
                       #(#("state") "状態の欄")
                       #(#("missing") "宣言の外の欄")]]
    (with [raised (pytest.raises ValueError)]
      (TableDecl :name "notes" :key-fields #("id") :fields FIELDS :indexes #("title") :descending #("rank")
                 :states #("open" "closed") :terminal #("closed") :initial "open" :bulk bulk))
    (assert (in words (str raised.value)) (str raised.value)))
  ;; 等号の欄: 索引の欄の断りより先に等号の欄の組を見ない宣言でも、組の等号の欄は大きい欄にできない。
  (with [raised (pytest.raises ValueError)]
    (TableDecl :name "notes" :key-fields #("id") :fields FIELDS :indexes #("title") :descending #("rank")
               :descending-within #((DescendingWithin "title" "rank")) :bulk #("title")))
  ;; 状態の語彙の無い表の state-field は使わないので、その名の欄も大きい欄にできる。
  (val plain (TableDecl :name "notes" :key-fields #("id") :fields FIELDS :bulk #("state")))
  (assert (= plain.bulk #("state")))
  (with [raised (pytest.raises TypeError)]
    (TableDecl :name "notes" :key-fields #("id") :fields FIELDS :bulk ["body"])))


(deftest test-bulk-stays-out-of-the-repr-and-changes-only-a-declaring-table-digest
  (val without (TableDecl :name "notes" :key-fields #("id") :fields FIELDS :indexes #("title") :descending #("rank")
                          :descending-within #((DescendingWithin "title" "rank"))
                          :states #("open" "closed") :terminal #("closed") :initial "open"))
  (assert (= (repr without) (repr NOTES)))
  (assert (= without.bulk #()))
  (val before (run (schema-digests (RecordsSchema :tables (FrozenMap {"notes" without})))))
  (val after (run (schema-digests SCHEMA)))
  (assert (!= (get before "notes") (get after "notes")))
  ;; 大きい欄の無い表の要約は、bulk を足す前の綴り(宣言の repr の sha256)のまま。
  (assert (= (get before "notes") (.hexdigest (hashlib.sha256 (.encode (repr without) "utf-8"))))))


;; --- 絞りの断り(memory と PostgreSQL)------------------------------------------------------------------------

(defk listed [ask]
  {:pre [(: ask ListRows)] :post [(: % Page)] :tags {:context "records" :role "program"}}
  "ListRows を 1 つ撃つため(断りは Raise で上がる)。"
  (<- answer Page ask)
  answer)


(defk listed-as-value [ask]
  {:pre [(: ask ListRows)] :post [(: % (| Page NotIndexed))] :tags {:context "records" :role "program"}}
  "ListRows の断り NotIndexed を値へ戻して返すため(断りを答えとして比べる)。"
  (<- answer (on-raise (listed ask)
                       (NotIndexed fields) (NotIndexed fields)))
  answer)


(defk refused-filters []
  {:pre [] :post [(: % tuple)] :tags {:context "records" :role "program"}}
  "大きい欄の where・where-any の ListRows の答え(と、索引の欄の絞りが通る対照)を集めるため。"
  (<- (PutRow "notes" #("n1") (FrozenMap (get VALUES "n1")) (ExpectAbsent)))
  (<- by-body (listed-as-value (ListRows "notes" :where (FrozenMap {"body" "x"}))))
  (<- by-attachments (listed-as-value (ListRows "notes" :where-any (FrozenMap {"attachments" #("a" "b")}))))
  (<- by-title (listed-as-value (ListRows "notes" :where (FrozenMap {"title" "alpha"}))))
  #(by-body by-attachments by-title))


(defn assert-refused [answers]
  (setv #(by-body by-attachments by-title) answers)
  (assert (= by-body (NotIndexed #("body"))) by-body)
  (assert (= by-attachments (NotIndexed #("attachments"))) by-attachments)
  (assert (and (isinstance by-title Page) (= (len by-title.rows) 1)) by-title))


(deftest test-memory-refuses-a-filter-on-a-bulk-field
  (val store (MemoryStore SCHEMA))
  (assert-refused (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) inline-compute-handler
                                                  (memory-records-handler store WRITER)]
                                                 (refused-filters))))))


(deftest test-postgres-refuses-a-filter-on-a-bulk-field
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  (val connections (postgres-connections))
  (val store (run-sql connections (prepare-records-store DATABASE DATABASE SCHEMA (fresh-prefix))))
  (try
    (assert-refused (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) inline-compute-handler
                                                    (postgres-sql-handler connections)
                                                    (pg-records-handler store WRITER ORIGIN-HOST)]
                                                   (refused-filters)))))
    (finally
      (run-sql connections (drop-records-tables store))
      (.close connections))))


;; --- 読みの合わせと fields の約束(PostgreSQL)----------------------------------------------------------------

(defn split-rows [prefix #^ (get dict #(str str)) overrides]
  "置いた行の大きい欄を、検の素の UPDATE で列 bulk に分けるため(大きい欄の無い行は触らない)。overrides = 鍵 → bulk に置く綴り(分けた
   綴りの代わり — 同じ欄が両方に在る行を作る)。"
  (with [connection (open-postgres)]
    (for [#(name _) (.items VALUES)]
      (setv key (key-text #(name)))
      (setv payload (get (.fetchone (.execute connection (.format "SELECT payload FROM {}state_rows WHERE ledger = 'notes' AND key = %s" prefix)
                                             #(key)))
                        0))
      (setv value (json.loads payload))
      (setv bulk (dfor #(field v) (.items value) :if (in field BULK) field v))
      (when (or bulk (in name overrides))
        (setv kept (dfor #(field v) (.items value) :if (not-in field BULK) field v))
        (.execute connection (.format "UPDATE {}state_rows SET payload = %s, bulk = %s WHERE ledger = 'notes' AND key = %s" prefix)
                  #((if (in name overrides) payload (canonical-json kept))
                    (.get overrides name (canonical-json bulk))
                    key))))))


(defn stored-columns [prefix #^ str name]
  "行 name の列 payload と bulk を素の SELECT で読むため。"
  (with [connection (open-postgres)]
    (.fetchone (.execute connection (.format "SELECT payload, bulk FROM {}state_rows WHERE ledger = 'notes' AND key = %s" prefix)
                         #((key-text #(name)))))))


(defk put-notes []
  {:pre [] :post [(: % None)] :tags {:context "records" :role "program"}}
  "VALUES の行を置くため。"
  (for [#(name value) (.items VALUES)]
    (<- (PutRow "notes" #(name) (FrozenMap value) (ExpectAbsent))))
  None)


(defk read-everything []
  {:pre [] :post [(: % tuple)] :tags {:context "records" :role "program"}}
  "ListRows の外の読みと fields の無い ListRows の答えを全部集めるため(分ける前と後で比べる)。"
  (var rows #())
  (for [name (sorted VALUES)]
    (<- row (ReadRow "notes" #(name)))
    (:= rows (+ rows #(row))))
  (<- page (ListRows "notes"))
  (<- top (ListRows "notes" :descending-by "rank" :limit 2))
  (<- batch (ReadBatch #((ReadRow "notes" #("n1")) (ListRows "notes") (ListRows "notes" :fields #("body")))))
  #(rows page top batch))


(defn pg-run [connections store counts program]
  (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) inline-compute-handler
                                  (postgres-sql-handler connections)
                                  (probe-sql-handler connections DATABASE (QueryProbe counts))
                                  (pg-records-handler store WRITER ORIGIN-HOST)]
                                 program))))


(defn state-row-texts [round-trips]
  "往復の列のうち state_rows を読む文の綴り。"
  (lfor trip round-trips text trip :if (in "state_rows" text) text))


(defn spelled [row]
  "行の保存の綴りを JSON として読んだ写像(欄の並びを問わない比べ)。"
  (json.loads (stored-text row)))


(defn page-rows [answer]
  (tuple (gfor row answer.rows #(row.key row.version (spelled row) row.value))))


(deftest test-split-rows-read-the-same-as-whole-rows
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  (val connections (postgres-connections))
  (val store (run-sql connections (prepare-records-store DATABASE DATABASE SCHEMA (fresh-prefix))))
  (val counts (StatementCounts))
  (try
    (pg-run connections store counts (put-notes))
    (val whole (pg-run connections store counts (read-everything)))
    (split-rows store.prefix {})
    ;; 分けた: n1 と n2 の大きい欄は列 bulk に在り、payload に無い。n3 は bulk が NULL。
    (val t-n1-payload-n1-bulk (stored-columns store.prefix "n1"))
    (val n1-payload (get t-n1-payload-n1-bulk 0))
    (val n1-bulk (get t-n1-payload-n1-bulk 1))
    (assert (and (not-in "body" (json.loads n1-payload)) (= (set (json.loads n1-bulk)) #{"body" "attachments"})) n1-payload)
    (assert (is (get (stored-columns store.prefix "n3") 1) None))
    (val split (pg-run connections store counts (read-everything)))
    (val t-whole-rows-whole-pag whole)
    (val whole-rows (get t-whole-rows-whole-pag 0))
    (val whole-page (get t-whole-rows-whole-pag 1))
    (val whole-top (get t-whole-rows-whole-pag 2))
    (val whole-batch (get t-whole-rows-whole-pag 3))
    (val t-split-rows-read-spli split)
    (val split-rows-read (get t-split-rows-read-spli 0))
    (val split-page (get t-split-rows-read-spli 1))
    (val split-top (get t-split-rows-read-spli 2))
    (val split-batch (get t-split-rows-read-spli 3))
    ;; ReadRow: 同じ鍵・版・値、綴りを読んだ写像も同じ。
    (for [#(a b) (zip whole-rows split-rows-read :strict True)]
      (assert (and (isinstance b Row) (= a b) (= (spelled a) (spelled b))) #(a b)))
    ;; fields の無い ListRows(鍵の順・上から読む読み)と ReadBatch の束の答え。
    (assert (= (page-rows whole-page) (page-rows split-page)) split-page)
    (assert (= #(whole-page.next-cursor whole-page.epoch) #(split-page.next-cursor split-page.epoch)))
    (assert (= (page-rows whole-top) (page-rows split-top)) split-top)
    (assert (= whole-top.next-cursor split-top.next-cursor) split-top)
    (val t-batch-row-batch-page split-batch.items)
    (val batch-row (get t-batch-row-batch-page 0))
    (val batch-page (get t-batch-row-batch-page 1))
    (val batch-fields (get t-batch-row-batch-page 2))
    (val t-whole-batch-row-whol whole-batch.items)
    (val whole-batch-row (get t-whole-batch-row-whol 0))
    (val whole-batch-page (get t-whole-batch-row-whol 1))
    (val whole-batch-fields (get t-whole-batch-row-whol 2))
    (assert (and (= batch-row whole-batch-row) (= (spelled batch-row) (spelled whole-batch-row))) batch-row)
    (assert (= (page-rows batch-page) (page-rows whole-batch-page)) batch-page)
    (assert (= (tuple (gfor row batch-fields.rows row.value)) (tuple (gfor row whole-batch-fields.rows row.value))) batch-fields)
    (finally
      (run-sql connections (drop-records-tables store))
      (.close connections))))


(defk overlay-title []
  {:pre [] :post [(: % Written)] :tags {:context "records" :role "program"}}
  "分けた行に、大きい欄に触らない書き(題だけ)を重ねるため。"
  (<- written (PutRow "notes" #("n1") (FrozenMap {"title" "alpha-2"}) (ExpectVersion 1)))
  written)


(deftest test-a-write-over-a-split-row-keeps-its-bulk-fields
  ;; 書きの土台の読み(行の錠の読み)も payload と bulk を合わせる: 題だけを書く書きの値・行・変更の列は大きい欄を持ち、書いた行は値の全部を
  ;; payload に置いて列 bulk を NULL にする(今の版の書き)。
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  (val connections (postgres-connections))
  (val store (run-sql connections (prepare-records-store DATABASE DATABASE SCHEMA (fresh-prefix))))
  (val counts (StatementCounts))
  (try
    (pg-run connections store counts (put-notes))
    (split-rows store.prefix {})
    (val written (pg-run connections store counts (overlay-title)))
    (val expected (frozen-json-object (| {"id" "n1" "state" "open"} (get VALUES "n1") {"title" "alpha-2"}) "expected"))
    (assert (and (isinstance written Written) (= written.version 2) (= written.value expected)) written)
    (val t-payload-bulk (stored-columns store.prefix "n1"))
    (val payload (get t-payload-bulk 0))
    (val bulk (get t-payload-bulk 1))
    (assert (is bulk None) bulk)
    (assert (= (frozen-json-object (json.loads payload) "payload") expected) payload)
    (with [connection (open-postgres)]
      (val change (get (.fetchone (.execute connection (.format "SELECT payload FROM {}row_changes WHERE ledger = 'notes' AND key = %s ORDER BY seq DESC LIMIT 1"
                                                                store.prefix)
                                            #((key-text #("n1")))))
                       0)))
    (assert (= (frozen-json-object (json.loads change) "change") expected) change)
    (finally
      (run-sql connections (drop-records-tables store))
      (.close connections))))


(deftest test-payload-wins-when-a-field-is-in-both-columns
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  (val connections (postgres-connections))
  (val store (run-sql connections (prepare-records-store DATABASE DATABASE SCHEMA (fresh-prefix))))
  (val counts (StatementCounts))
  (try
    (pg-run connections store counts (put-notes))
    ;; n2 の payload は値の全部(body "short")のまま、bulk に古い body と payload に無い欄を置く。空の写像の bulk は継がない(n3)。
    (split-rows store.prefix {"n2" (canonical-json {"body" "stale" "attachments" ["old.png"]}) "n3" "{}"})
    (val t-rows (pg-run connections store counts (read-everything)))
    (val rows (get t-rows 0))
    (val by-name (dfor row rows (get row.key 0) row.value))
    (assert (= (get (get by-name "n2") "body") "short") by-name)
    (assert (= (get (get by-name "n2") "attachments") #("old.png")) by-name)
    (assert (= (get by-name "n3") (frozen-json-object (| {"id" "n3" "state" "open"} (get VALUES "n3")) "n3")) by-name)
    (finally
      (run-sql connections (drop-records-tables store))
      (.close connections))))


(defk listed-with-fields [counts]
  {:pre [(: counts StatementCounts)] :post [(: % tuple)] :tags {:context "records" :role "program"}}
  "fields の違う ListRows(大きい欄を外す・含む・fields 無し・上から読む読みで外す)の答えと、その間に DB へ流れた文を集めるため。"
  (var seen #())
  (for [ask [(ListRows "notes" :fields #("title" "rank"))
             (ListRows "notes" :fields #("title" "body"))
             (ListRows "notes")
             (ListRows "notes" :fields #("title") :descending-by "rank")
             (ListRows "notes" :fields #("attachments") :descending-by "rank")]]
    (<- run-of (effect-round-trips counts ask))
    (:= seen (+ seen #(run-of))))
  seen)


(deftest test-a-list-without-bulk-fields-does-not-select-the-bulk-column
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  (val connections (postgres-connections))
  (val store (run-sql connections (prepare-records-store DATABASE DATABASE SCHEMA (fresh-prefix))))
  (val counts (StatementCounts))
  (try
    (pg-run connections store counts (put-notes))
    (split-rows store.prefix {})
    (val seen (pg-run connections store counts (listed-with-fields counts)))
    (val narrow (get (get seen 0) 0))
    (val narrow-trips (get (get seen 0) 1))
    (val wide (get (get seen 1) 0))
    (val wide-trips (get (get seen 1) 1))
    (val whole (get (get seen 2) 0))
    (val whole-trips (get (get seen 2) 1))
    (val top (get (get seen 3) 0))
    (val top-trips (get (get seen 3) 1))
    (val top-wide (get (get seen 4) 0))
    (val top-wide-trips (get (get seen 4) 1))
    ;; 大きい欄を外した読みの文は列 bulk を名指さない。含む読み・fields の無い読みは名指す。
    (for [trips [narrow-trips top-trips]]
      (val texts (state-row-texts trips))
      (assert (and texts (not (any (gfor text texts (in "bulk" text))))) texts))
    (for [trips [wide-trips whole-trips top-wide-trips]]
      (val wide-texts (state-row-texts trips))
      (assert (and wide-texts (all (gfor text wide-texts (in "bulk" text)))) wide-texts))
    ;; 答え: 外した読みも題と順位は分ける前の値。含む読みは大きい欄を合わせて返す。
    (assert (= (tuple (gfor row narrow.rows (dict row.value)))
               #({"id" "n1" "title" "alpha" "rank" 3} {"id" "n2" "title" "beta" "rank" 1} {"id" "n3" "title" "gamma" "rank" 2}))
            narrow)
    (assert (= (tuple (gfor row wide.rows (.get row.value "body"))) #((get (get VALUES "n1") "body") "short" None)) wide)
    (assert (= (tuple (gfor row whole.rows row.value)) (tuple (gfor name (sorted VALUES) (frozen-json-object (| {"id" name "state" "open"} (get VALUES name)) name)))) whole)
    (assert (= (tuple (gfor row top.rows (get row.key 0))) #("n1" "n3" "n2")) top)
    (assert (= (tuple (gfor row top-wide.rows (.get row.value "attachments"))) #(#("a.png" "b.png") None None)) top-wide)
    (finally
      (run-sql connections (drop-records-tables store))
      (.close connections))))
