;; 表の alias(_values.hy の頭の註の「表の alias」— Elasticsearch の index alias swap と同じ形)を memory の置き場が答える形の検。
;;   付け替えの前の論理の名の読みは slot a を・後は slot b を読む(ReadRow・ListRows・CountRows・ReadBatch)
;;   付け替えは PutRows の行と同じ 1 回の書きで、世代の期待が違う・索引の用意が終わっていない先への付け替えは束ごと断る
;;   論理の名への書きは断る
;;   ListRows の頁の間に付け替えが入った続きの読みは、2 つの世代を混ぜずに Reset(tables = その名)
;;   WatchChanges は付け替えた表を見ている待ち手にだけ表に絞った Reset を返し、影の表に書いた変更を付け替えの後に論理の名の変更として出さない
(require doeff-hy.macros [deftest defk <- val])
(import doeff [Perform run with_handlers])
(import doeff_core_effects [scheduled])
(import doeff_time [SimClock sim-time-handler])
(import doeff_hy._frozen [FrozenMap])
(import doeff_records [ExpectAbsent ExpectAny Written WrittenRows Missing Refused RowsRefused Row Page Reset Changes RowChanged
                        WatchCursor Count BatchRead])
(import doeff_records [ReadRow ListRows CountRows ReadBatch PutRow PutRows RowWrite WatchChanges])
(import doeff_records [RecordsSchema TableAlias AliasState ExpectAlias SwapAlias HoldTableIndexes])
(import doeff_records [MemoryStore memory-records-handler])
(import doeff_records._laws [LAW-SCHEMA MAKER])

(val ALIAS-SCHEMA (RecordsSchema :tables LAW-SCHEMA.tables :streams LAW-SCHEMA.streams :operators LAW-SCHEMA.operators
                                 :aliases #((TableAlias "parts"))))


(defn #^ object in-store [#^ MemoryStore store program]  ; cannot be a defk: 検の入口で run を撃つ
  "仮想の時計と memory の置き場(書き手 MAKER)の下で program を回して答えを返すため。"
  (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) (memory-records-handler store MAKER)] program))))


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
  "論理の名 parts の読み 4 つ(p1 の ReadRow・全部の ListRows の label・CountRows・ReadBatch の p1)を並べるため。"
  (<- row (Perform (ReadRow "parts" #("p1"))))
  (<- page (ListRows "parts"))
  (<- counted (Perform (CountRows "parts")))
  (<- batch (Perform (ReadBatch #((ReadRow "parts" #("p1"))))))
  #((if (isinstance row Row) (get row.value "label") row)
    (tuple (gfor r page.rows #((get r.key 0) (get r.value "label"))))
    counted
    (get (. (get batch.items 0) value) "label")
    page.alias))


(deftest test-the-logical-name-reads-slot-a-before-the-swap-and-slot-b-after
  (val store (MemoryStore ALIAS-SCHEMA))
  (in-store store (seeded))
  (val before (in-store store (logical-view)))
  (assert (= before #("old" #(#("p1" "old") #("p2" "old")) (Count 2) "old" (AliasState "parts" "a" 0))) before)
  (val swapped (in-store store (swap 0)))
  (assert (isinstance swapped WrittenRows) swapped)
  (val after (in-store store (logical-view)))
  (assert (= after #("new" #(#("p1" "new") #("p3" "new")) (Count 2) "new" (AliasState "parts" "b" 1))) after)
  ;; 物理の名は付け替えに依らず同じ slot を読む。
  (val physical (in-store store (Perform (ReadRow "parts~a" #("p1")))))
  (assert (= (get physical.value "label") "old") physical)
  ;; 付け替えは変更の列の番号を 1 つ採る(位置の行の変更の後)。
  (assert (= store.head (+ (. (get swapped.items -1) position sequence) 1)) #(store.head swapped)))


(deftest test-a-write-to-the-logical-name-is-refused
  (val store (MemoryStore ALIAS-SCHEMA))
  (val rows (in-store store (Perform (PutRows #((part "tickets" "x" "y") (part "parts" "p1" "z"))))))
  (assert (and (isinstance rows RowsRefused) (= rows.index 1) (= rows.table "parts")) rows)
  (val one (in-store store (Perform (PutRow "parts" #("p1") (FrozenMap {"label" "z"}) (ExpectAbsent)))))
  (assert (isinstance one Refused) one)
  (assert (= store.head 0) store.head))


(deftest test-a-stale-alias-generation-refuses-the-whole-batch
  (val store (MemoryStore ALIAS-SCHEMA))
  (in-store store (seeded))
  (in-store store (swap 0))
  (val head store.head)
  ;; 前の版の書き手(世代 0 を期待)の書きは、付け替えの後に束ごと断られる。
  (val stale (in-store store (Perform (PutRows #((part "parts~a" "p1" "late")) :aliases #((ExpectAlias "parts" 0))))))
  (assert (and (isinstance stale RowsRefused) (= stale.index 1) (= stale.table "parts") (= stale.key #())) stale)
  (assert (= store.head head) "断った束は何も書かない")
  ;; 2 度目の付け替え(世代 0 のまま)も断る。今の世代なら書ける。
  (val again (in-store store (swap 0)))
  (assert (isinstance again RowsRefused) again)
  (val fresh (in-store store (Perform (PutRows #((part "parts~b" "p4" "new")) :aliases #((ExpectAlias "parts" 1))))))
  (assert (isinstance fresh WrittenRows) fresh)
  ;; 既に指している slot への付け替えは断る。
  (val same (in-store store (Perform (PutRows #((part "tickets" "x" "y")) :aliases #((SwapAlias "parts" 1 "b"))))))
  (assert (isinstance same RowsRefused) same))


(deftest test-a-swap-before-the-shadow-indexes-are-ready-is-refused
  (val store (MemoryStore ALIAS-SCHEMA))
  (in-store store (seeded))
  (in-store store (HoldTableIndexes (frozenset #("parts~b"))))
  (val held (in-store store (swap 0)))
  (assert (and (isinstance held RowsRefused) (in "索引" held.reason)) held)
  (assert (= (get (in-store store (logical-view)) 4) (AliasState "parts" "a" 0)))
  (in-store store (HoldTableIndexes (frozenset)))
  (assert (isinstance (in-store store (swap 0)) WrittenRows)))


(deftest test-a-page-continued-across-a-swap-resets-instead-of-mixing-generations
  (val store (MemoryStore ALIAS-SCHEMA))
  (in-store store (seeded))
  (val first (in-store store (ListRows "parts" :limit 1)))
  (assert (= (. first next-cursor generation) 0) first)
  (in-store store (swap 0))
  (val rest (in-store store (ListRows "parts" :cursor first.next-cursor :limit 1)))
  (assert (and (isinstance rest Reset) (= rest.tables #("parts"))) rest)
  ;; 頭から読み直すと新しい世代だけ。
  (val head (in-store store (ListRows "parts" :limit 1)))
  (val tail (in-store store (ListRows "parts" :cursor head.next-cursor :limit 1)))
  (assert (= (lfor page #(head tail) r page.rows (get r.value "label")) ["new" "new"]) #(head tail)))


(defk watched [tables cursor]
  {:pre [(: tables tuple) (: cursor WatchCursor)] :post [(: % (| Changes Reset))]}
  "tables の変更を cursor から待たずに読むため。"
  (<- answer (WatchChanges tables cursor :timeout 0.0))
  answer)


(deftest test-watchers-of-the-swapped-table-reset-and-never-see-shadow-writes-as-logical-changes
  (val store (MemoryStore ALIAS-SCHEMA))
  (in-store store (Perform (PutRows #((part "parts~a" "p1" "old")))))
  (val start (WatchCursor store.epoch store.head))
  ;; 作り直しの間: 前の版が slot a を書き続け、新しい版が影の slot b を組む。位置の後の tickets の変更も積む。
  (in-store store (Perform (PutRows #((part "parts~a" "p1" "old2")))))
  (in-store store (Perform (PutRows #((part "parts~b" "p1" "new")))))
  (val swapped (in-store store (swap 0)))
  (val swap-at store.head)
  (in-store store (Perform (PutRows #((part "parts~b" "p2" "new")))))
  ;; parts と tickets を見る待ち手: 付け替えの前は slot a の変更と tickets の変更だけ(影の slot の書きは出ない)。
  (val early (in-store store (watched #("parts" "tickets") start)))
  (assert (isinstance early Changes) early)
  (assert (= (lfor c early.items #(c.table (.get c.value "label" (.get c.value "owner"))))
             [#("parts" "old2") #("tickets" "rebuilt")])
          early.items)
  (assert (= early.cursor.sequence (- swap-at 1)) early.cursor)
  ;; 次は付け替えた表に絞った Reset(floor = 付け替えの位置)。
  (val reset (in-store store (watched #("parts" "tickets") early.cursor)))
  (assert (= reset (Reset store.epoch swap-at :tables #("parts"))) reset)
  ;; 付け替えの位置から続けると、付け替えの後の影の slot の書きだけが論理の名の変更として来る(付け替えの前の影の書き p1 = new は出ない)。
  (val late (in-store store (watched #("parts" "tickets") (WatchCursor reset.epoch reset.floor))))
  (assert (= (lfor c late.items #(c.table (get c.key 0))) [#("parts" "p2")]) late.items)
  ;; 付け替えた表を見ていない待ち手には Reset を返さない。
  (val others (in-store store (watched #("tickets") start)))
  (assert (and (isinstance others Changes) (= (lfor c others.items c.table) ["tickets"])) others)
  ;; 物理の名を見る待ち手は名のとおりの slot の変更を受ける(付け替えで Reset しない)。
  (val shadow (in-store store (watched #("parts~b") start)))
  (assert (= (lfor c shadow.items #(c.table (get c.key 0))) [#("parts~b" "p1") #("parts~b" "p2")]) shadow))
