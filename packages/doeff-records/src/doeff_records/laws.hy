;;; 記録の handler の適合の筋書き(公開)— どの handler の組も同じ法を満たすことを、同じ Program で確かめる。
;;;
;;; 筋書きは公開 effect と検の口(faults.AdvanceStoreEpoch)だけを撃ち、法が破れたら LawBroken を上げ、撃った effect の答えを
;;; 順に並べた transcript(list)を返す。2 つの handler の組で同じ筋書きを回し、transcript が等しいこと(= 答えが同じ)も確かめられる。
;;;
;;; 組み立ては呼び手: LAW-SCHEMA の宣言で置き場を作り、LawHarness の as-writer(書き手の名・Program → その書き手の handler で包んだ
;;; Program)を渡す。書き手の名は宣言の欄の書き手の名(maker / painter / closer)と、どこにも載らない stranger。
;;; 保持の法(law-transient-rows-expire)は doeff-time の Delay で時間を進めるので、仮想の時計(sim-time-handler)の下で回す。
;;; 待ちの法(law-watch-waits-for-a-change)は doeff の scheduler の Spawn を使う。
;;; 手入れの法(law-maintenance-prunes-and-sweeps)は手入れの effect(maintenance.SweepExpired / PruneChanges — 公開 effect ではない)も撃つ。
(require doeff-hy.macros [defk <-])
(import dataclasses [dataclass])
(import collections.abc [Callable])
(import doeff_hy.frozen [FrozenMap])
(import doeff_core_effects.scheduler [Spawn Wait])
(import doeff_time [Delay])
(import doeff_records.values [FieldDecl TableDecl StreamDecl RecordsSchema KeepFor KeepForever ExpectAbsent ExpectVersion ExpectAny
                              WatchCursor ListCursor Row Missing Page Written Conflict Refused NotIndexed Reset
                              Changes RowChanged RowRemoved Appended Events])
(import doeff_records.effects [ReadRow ListRows PutRow WatchChanges AppendEvent ReadEvents])
(import doeff_records.faults [AdvanceStoreEpoch])
(import doeff_records.maintenance [SweepExpired PruneChanges Swept Pruned])
(import doeff_records.admission [row-matches?])

;; OVERSEER = operator の主体(宣言の operators に入る唯一の書き手)— 法の中で operator の宣言の欄 grant を書ける。
(setv MAKER "maker" PAINTER "painter" CLOSER "closer" STRANGER "stranger" OVERSEER "overseer")
(setv TICKET-KEEP-SECONDS 60)

(setv LAW-SCHEMA
  (RecordsSchema
    :tables (FrozenMap {"parts" (TableDecl :name "parts" :key-fields #("id")
                                :fields #((FieldDecl "id" #(MAKER)) (FieldDecl "label" #(MAKER))
                                          (FieldDecl "color" #(MAKER PAINTER)) (FieldDecl "state" #(MAKER CLOSER))
                                          (FieldDecl "note" #(MAKER)) (FieldDecl "grant" #(MAKER OVERSEER)))
                                :indexes #("color" "label")
                                :states #("open" "held" "closed") :terminal #("closed") :initial "open"
                                :operator-paths #("grant") :size-budget 400)
             "tickets" (TableDecl :name "tickets" :key-fields #("group" "id")
                                  :fields #((FieldDecl "group" #(MAKER)) (FieldDecl "id" #(MAKER))
                                            (FieldDecl "state" #(MAKER)) (FieldDecl "owner" #(MAKER)))
                                  :indexes #("owner")
                                  :states #("open" "done") :terminal #("done") :initial "open"
                                  :retention (KeepFor TICKET-KEEP-SECONDS))})
    :streams (FrozenMap {"journal" (StreamDecl :name "journal" :writers #(MAKER) :size-budget 200)})
    :operators #(OVERSEER)))


(defclass LawBroken [AssertionError]
  "法が破れた(どの法の何が — 文に書く)。")


(defclass [(dataclass :frozen True)] LawHarness []
  "as-writer = (書き手の名 Program) → その書き手の handler で包んだ Program。"
  (#^ Callable as-writer))


(defn #^ None require-law [#^ bool holds #^ str law #^ str detail]
  (when (not holds) (raise (LawBroken (.format "{}: {}" law detail)))))


(defn as-writer [#^ LawHarness harness #^ str writer program]
  (harness.as-writer writer program))


(defk collect-changes [#^ LawHarness harness #^ tuple tables #^ WatchCursor cursor #^ int limit]
  {:pre [(: harness LawHarness) (: tables tuple) (: cursor WatchCursor) (: limit int)] :post [(: % tuple)]}
  "空の答えが来るまで WatchChanges を撃ち、答えを全部並べる: #(答えの list 最後の位置)。"
  (setv answers [] at cursor)
  (while True
    (<- answer (as-writer harness MAKER (WatchChanges tables at :limit limit)))
    (.append answers answer)
    (when (not (isinstance answer Changes)) (return #(answers at)))
    (setv at answer.cursor)
    (when (not answer.items) (return #(answers at)))))


(defk collect-pages [#^ LawHarness harness #^ str table #^ FrozenMap where #^ int limit]
  {:pre [(: harness LawHarness) (: table str) (: where FrozenMap) (: limit int)] :post [(: % list)]}
  "next-cursor が尽きるまで ListRows の頁を読み、頁を全部並べる。"
  (setv pages [] cursor None)
  (while True
    (<- page (as-writer harness MAKER (ListRows table :where where :cursor cursor :limit limit)))
    (.append pages page)
    (when (or (not (isinstance page Page)) (is page.next-cursor None)) (return pages))
    (setv cursor page.next-cursor)))


;; --- 法 1: 古い版の PutRow は Conflict ---------------------------------------------------------------------

(defk law-stale-put-conflicts [#^ LawHarness harness]
  {:pre [(: harness LawHarness)] :post [(: % list)]}
  (setv law "古い版の PutRow は Conflict" t [])
  (<- born (as-writer harness MAKER (PutRow "parts" #("p1") (FrozenMap {"label" "a"}) (ExpectAbsent))))
  (require-law (= born (Written 1 (FrozenMap {"id" "p1" "label" "a" "state" "open"}))) law (.format "生まれる行: {!r}" born))
  (<- second (as-writer harness MAKER (PutRow "parts" #("p1") (FrozenMap {"label" "b"}) (ExpectVersion 1))))
  (require-law (and (isinstance second Written) (= second.version 2)) law (.format "版 1 への書き: {!r}" second))
  (setv now (Row #("p1") (FrozenMap {"id" "p1" "label" "b" "state" "open"}) 2))
  (<- stale (as-writer harness MAKER (PutRow "parts" #("p1") (FrozenMap {"label" "c"}) (ExpectVersion 1))))
  (require-law (= stale (Conflict now)) law (.format "古い版 1 への書き: {!r}" stale))
  (<- absent (as-writer harness MAKER (PutRow "parts" #("p1") (FrozenMap {"label" "d"}) (ExpectAbsent))))
  (require-law (= absent (Conflict now)) law (.format "在る行への ExpectAbsent: {!r}" absent))
  (<- missing (as-writer harness MAKER (PutRow "parts" #("p-none") (FrozenMap {"label" "d"}) (ExpectVersion 3))))
  (require-law (= missing (Conflict (Missing))) law (.format "無い行への ExpectVersion: {!r}" missing))
  (<- read (as-writer harness MAKER (ReadRow "parts" #("p1"))))
  (require-law (= read now) law (.format "衝突は行を変えない: {!r}" read))
  (<- nothing (as-writer harness MAKER (ReadRow "parts" #("p-none"))))
  (require-law (= nothing (Missing)) law (.format "衝突は行を作らない: {!r}" nothing))
  (<- blind (as-writer harness MAKER (PutRow "parts" #("p1") (FrozenMap {"label" "e"}) (ExpectAny))))
  (require-law (and (isinstance blind Written) (= blind.version 3)) law (.format "ExpectAny: {!r}" blind))
  [born second stale absent missing read nothing blind])


;; --- 法 2: 確定した変更は WatchChanges にちょうど 1 回・順序どおり -----------------------------------------

(defk law-committed-changes-appear-once-in-order [#^ LawHarness harness]
  {:pre [(: harness LawHarness)] :post [(: % list)]}
  (setv law "確定した変更は WatchChanges にちょうど 1 回・順序どおり")
  (<- start (as-writer harness MAKER (ListRows "parts" :limit 1)))
  (require-law (isinstance start Page) law (.format "最初の一覧: {!r}" start))
  (setv cursor (WatchCursor start.epoch start.sequence))
  (<- w1 (as-writer harness MAKER (PutRow "parts" #("p1") (FrozenMap {"label" "x"}) (ExpectAbsent))))
  (<- w2 (as-writer harness MAKER (PutRow "tickets" #("g1" "t1") (FrozenMap {"owner" "o1"}) (ExpectAbsent))))
  (<- refused (as-writer harness STRANGER (PutRow "parts" #("p1") (FrozenMap {"label" "y"}) (ExpectAny))))
  (<- conflict (as-writer harness MAKER (PutRow "parts" #("p1") (FrozenMap {"label" "y"}) (ExpectAbsent))))
  (<- w3 (as-writer harness PAINTER (PutRow "parts" #("p1") (FrozenMap {"color" "red"}) (ExpectVersion 1))))
  (<- w4 (as-writer harness MAKER (PutRow "parts" #("p2") (FrozenMap {"label" "z"}) (ExpectAbsent))))
  (<- w5 (as-writer harness MAKER (PutRow "tickets" #("g1" "t1") (FrozenMap {"state" "done"}) (ExpectVersion 1))))
  (require-law (and (isinstance refused Refused) (isinstance conflict Conflict)) law
               (.format "断る書きと衝突する書き: {!r} {!r}" refused conflict))
  (setv committed [#("parts" #("p1") w1) #("tickets" #("g1" "t1") w2) #("parts" #("p1") w3) #("parts" #("p2") w4)
                   #("tickets" #("g1" "t1") w5)])
  (for [#(_ _ written) committed]
    (require-law (isinstance written Written) law (.format "確定するはずの書き: {!r}" written)))
  (<- collected (collect-changes harness #("parts" "tickets") cursor 2))
  (setv #(answers end) collected)
  (setv items (lfor answer answers item answer.items item))
  (require-law (all (gfor answer answers (isinstance answer Changes))) law (.format "Changes 以外の答え: {!r}" answers))
  (require-law (= (lfor item items #(item.table item.key item.version (dict item.value)))
                  (lfor #(table key written) committed #(table key written.version written.value)))
               law (.format "確定した変更と見えた変更が違う: {!r}" items))
  (setv sequences (lfor item items item.sequence))
  (require-law (= sequences (sorted (set sequences))) law (.format "番号が昇順で重複なしでない: {!r}" sequences))
  (require-law (all (gfor s sequences (> s cursor.sequence))) law (.format "位置より前の変更が見えた: {!r}" sequences))
  (<- parts-collected (collect-changes harness #("parts") cursor 100))
  (setv only-parts (get parts-collected 0))
  (setv part-items (lfor answer only-parts item answer.items item))
  (require-law (= (lfor item part-items #(item.key item.version)) [#(#("p1") 1) #(#("p1") 2) #(#("p2") 1)])
               law (.format "表で絞った変更: {!r}" part-items))
  (<- after (as-writer harness MAKER (WatchChanges #("parts" "tickets") end)))
  (require-law (and (isinstance after Changes) (= after.items #())) law (.format "読み終えた位置の後に変更が残る: {!r}" after))
  (+ [start w1 w2 refused conflict w3 w4 w5] answers only-parts [after]))


;; --- 法 3: epoch が変わると Reset ------------------------------------------------------------------------

(defk law-epoch-change-resets [#^ LawHarness harness]
  {:pre [(: harness LawHarness)] :post [(: % list)]}
  (setv law "epoch が変わると Reset")
  (<- first (as-writer harness MAKER (ListRows "parts")))
  (<- w1 (as-writer harness MAKER (PutRow "parts" #("p1") (FrozenMap {"label" "a"}) (ExpectAbsent))))
  (<- epoch (as-writer harness MAKER (AdvanceStoreEpoch)))
  (require-law (and (isinstance epoch int) (!= epoch first.epoch)) law (.format "新しい epoch: {!r}" epoch))
  (<- watched (as-writer harness MAKER (WatchChanges #("parts") (WatchCursor first.epoch first.sequence))))
  (require-law (= watched (Reset epoch)) law (.format "古い epoch の位置の WatchChanges: {!r}" watched))
  (<- listed (as-writer harness MAKER (ListRows "parts" :cursor (ListCursor first.epoch "[\"p0\"]"))))
  (require-law (= listed (Reset epoch)) law (.format "古い epoch の位置の ListRows: {!r}" listed))
  (<- again (as-writer harness MAKER (ListRows "parts")))
  (require-law (and (isinstance again Page) (= again.epoch epoch) (= (lfor row again.rows row.key) [#("p1")]))
               law (.format "読み直した一覧(行は残る): {!r}" again))
  (<- w2 (as-writer harness MAKER (PutRow "parts" #("p2") (FrozenMap {"label" "b"}) (ExpectAbsent))))
  (<- fresh (as-writer harness MAKER (WatchChanges #("parts") (WatchCursor again.epoch again.sequence))))
  (require-law (and (isinstance fresh Changes) (= (lfor item fresh.items item.key) [#("p2")]))
               law (.format "新しい位置からの変更: {!r}" fresh))
  [first w1 epoch watched listed again w2 fresh])


;; --- 法 4: 宣言に無い書き手・欄・状態・上限・operator の欄・終端は Refused -------------------------------------------

(defk law-undeclared-writes-are-refused [#^ LawHarness harness]
  {:pre [(: harness LawHarness)] :post [(: % list)]}
  (setv law "宣言が許さない書きは Refused で、行を変えない")
  (<- born (as-writer harness MAKER (PutRow "parts" #("p1") (FrozenMap {"label" "a"}) (ExpectAbsent))))
  (setv refusals [])
  (for [#(writer table key diff) [#(STRANGER "parts" #("p1") {"label" "z"})
                                  #(PAINTER "parts" #("p1") {"label" "z"})
                                  #(PAINTER "parts" #("p9") {"color" "red"})
                                  #(MAKER "parts" #("p1") {"size" 1})
                                  #(MAKER "parts" #("p1") {"state" "weird"})
                                  #(MAKER "parts" #("p1") {"grant" "yes"})
                                  #(MAKER "parts" #("p1") {"note" (* "n" 500)})
                                  #(MAKER "parts" #("p1") {"id" "p-other"})
                                  #(MAKER "tickets" #("only-one") {"owner" "o"})]]
    (<- answer (as-writer harness writer (PutRow table key diff (ExpectAny))))
    (require-law (isinstance answer Refused) law (.format "{} の {!r} {!r}: {!r}" writer key diff answer))
    (.append refusals answer))
  (<- untouched (as-writer harness MAKER (ReadRow "parts" #("p1"))))
  (require-law (= untouched (Row #("p1") (FrozenMap {"id" "p1" "label" "a" "state" "open"}) 1)) law
               (.format "断った書きが行を変えた: {!r}" untouched))
  (<- nobody (as-writer harness MAKER (ReadRow "parts" #("p9"))))
  (require-law (= nobody (Missing)) law (.format "作ってよくない書き手が行を作った: {!r}" nobody))
  (<- painted (as-writer harness PAINTER (PutRow "parts" #("p1") (FrozenMap {"color" "blue"}) (ExpectVersion 1))))
  (require-law (and (isinstance painted Written) (= painted.version 2)) law (.format "名簿に在る書き手: {!r}" painted))
  (<- same (as-writer harness PAINTER (PutRow "parts" #("p1") (FrozenMap {"color" "blue" "label" "a"}) (ExpectVersion 2))))
  (require-law (and (isinstance same Written) (= same.version 3)) law (.format "値の変わらない欄は照らさない: {!r}" same))
  (<- closed (as-writer harness CLOSER (PutRow "parts" #("p1") (FrozenMap {"state" "closed"}) (ExpectVersion 3))))
  (require-law (and (isinstance closed Written) (= (get closed.value "state") "closed")) law (.format "終端へ: {!r}" closed))
  (<- frozen (as-writer harness MAKER (PutRow "parts" #("p1") (FrozenMap {"label" "after"}) (ExpectVersion 4))))
  (require-law (isinstance frozen Refused) law (.format "終端の行への書き: {!r}" frozen))
  (<- final (as-writer harness MAKER (ReadRow "parts" #("p1"))))
  (require-law (and (isinstance final Row) (= final.version 4)) law (.format "終端の行が変わった: {!r}" final))
  (+ [born] refusals [untouched nobody painted same closed frozen final]))


;; --- 法 4b: operator の宣言の欄は operator の主体だけが書ける・他の欄は欄の書き手の宣言どおり ------------------------

(defk law-operator-paths-need-an-operator [#^ LawHarness harness]
  {:pre [(: harness LawHarness)] :post [(: % list)]}
  "operator の宣言の欄を agent が書けないことを、どの置き場の組でも同じに確かめるための法:
   欄の書き手でも operator の主体でなければ Refused・operator の主体は書ける・operator の主体でも欄の書き手でない欄は書けない・
   operator-paths の外の欄には主体の区別が効かない。"
  (setv law "operator の宣言の欄は operator の主体だけが書き、他の欄は欄の書き手の宣言どおり")
  (<- born (as-writer harness MAKER (PutRow "parts" #("p5") (FrozenMap {"label" "a"}) (ExpectAbsent))))
  (require-law (isinstance born Written) law (.format "行を作る: {!r}" born))
  (setv refusals [])
  (for [writer [MAKER STRANGER PAINTER]]
    (<- answer (as-writer harness writer (PutRow "parts" #("p5") (FrozenMap {"grant" "yes"}) (ExpectAny))))
    (require-law (isinstance answer Refused) law (.format "operator でない {} の grant の書き: {!r}" writer answer))
    (.append refusals answer))
  (<- granted (as-writer harness OVERSEER (PutRow "parts" #("p5") (FrozenMap {"grant" "yes"}) (ExpectVersion 1))))
  (require-law (and (isinstance granted Written) (= granted.version 2) (= (get granted.value "grant") "yes")) law
               (.format "operator の主体の grant の書き: {!r}" granted))
  (<- overreach (as-writer harness OVERSEER (PutRow "parts" #("p5") (FrozenMap {"label" "z"}) (ExpectAny))))
  (require-law (isinstance overreach Refused) law (.format "operator の主体が書き手でない欄を書いた: {!r}" overreach))
  (<- labeled (as-writer harness MAKER (PutRow "parts" #("p5") (FrozenMap {"label" "b"}) (ExpectVersion 2))))
  (require-law (and (isinstance labeled Written) (= labeled.version 3)) law
               (.format "operator の欄の外は欄の書き手の宣言どおり: {!r}" labeled))
  (<- final (as-writer harness MAKER (ReadRow "parts" #("p5"))))
  (require-law (= final (Row #("p5") (FrozenMap {"id" "p5" "label" "b" "state" "open" "grant" "yes"}) 3)) law
               (.format "断った書きが行を変えた: {!r}" final))
  (+ [born] refusals [granted overreach labeled final]))


;; --- 法 5: transient の行は期限で消え、record の行は消えない ------------------------------------------------

(defk law-transient-rows-expire [#^ LawHarness harness]
  {:pre [(: harness LawHarness)] :post [(: % list)]}
  (setv law "transient の行は期限で消え、record の行は消えない")
  (<- t1 (as-writer harness MAKER (PutRow "tickets" #("g1" "t1") (FrozenMap {"owner" "o1"}) (ExpectAbsent))))
  (<- t2 (as-writer harness MAKER (PutRow "tickets" #("g1" "t2") (FrozenMap {"owner" "o2"}) (ExpectAbsent))))
  (<- p1 (as-writer harness MAKER (PutRow "parts" #("p1") (FrozenMap {"state" "closed"}) (ExpectAbsent))))
  (<- done (as-writer harness MAKER (PutRow "tickets" #("g1" "t1") (FrozenMap {"state" "done"}) (ExpectVersion 1))))
  (<- start (as-writer harness MAKER (ListRows "tickets")))
  (<- (Delay (- TICKET-KEEP-SECONDS 1)))
  (<- before (as-writer harness MAKER (ReadRow "tickets" #("g1" "t1"))))
  (require-law (and (isinstance before Row) (= before.version 2)) law (.format "期限の前に消えた: {!r}" before))
  (<- (Delay 2))
  (<- gone (as-writer harness MAKER (ReadRow "tickets" #("g1" "t1"))))
  (require-law (= gone (Missing)) law (.format "期限を過ぎても残る: {!r}" gone))
  (<- listed (as-writer harness MAKER (ListRows "tickets")))
  (require-law (= (lfor row listed.rows row.key) [#("g1" "t2")]) law (.format "一覧に期限切れが残る・終端でない行が消えた: {!r}" listed))
  (<- removed (as-writer harness MAKER (WatchChanges #("tickets") (WatchCursor start.epoch start.sequence))))
  (require-law (and (isinstance removed Changes) (= (lfor item removed.items #((type item) item.key)) [#(RowRemoved #("g1" "t1"))]))
               law (.format "消えた行が変更に 1 回だけ出ない: {!r}" removed))
  (<- (Delay (* 100 TICKET-KEEP-SECONDS)))
  (<- kept (as-writer harness MAKER (ReadRow "parts" #("p1"))))
  (require-law (and (isinstance kept Row) (= (get kept.value "state") "closed")) law (.format "record の行が消えた: {!r}" kept))
  (<- open-kept (as-writer harness MAKER (ReadRow "tickets" #("g1" "t2"))))
  (require-law (isinstance open-kept Row) law (.format "終端でない transient の行が消えた: {!r}" open-kept))
  [t1 t2 p1 done start before gone listed removed kept open-kept])


;; --- 法 6: 索引の ListRows は全件を読んで絞った結果と同じ -------------------------------------------------

(setv COLORS #("red" "blue" "green"))
(setv LABELS #("a" "b"))
(setv WHERES (lfor where [{} {"color" "red"} {"label" "a"} {"color" "blue" "label" "b"} {"id" "p03"} {"color" "none"}] (FrozenMap where)))

(defk law-indexed-list-equals-filtered-scan [#^ LawHarness harness]
  {:pre [(: harness LawHarness)] :post [(: % list)]}
  (setv law "索引の ListRows は全件を読んで絞った結果と同じ" transcript [])
  (for [n (range 1 13)]
    (<- written (as-writer harness MAKER (PutRow "parts" #((.format "p{:02d}" n))
                                                 (FrozenMap {"color" (get COLORS (% n 3)) "label" (get LABELS (% n 2))}) (ExpectAbsent))))
    (.append transcript written))
  (<- everything (collect-pages harness "parts" (FrozenMap) 500))
  (setv all-rows (lfor page everything row page.rows row))
  (require-law (= (len all-rows) 12) law (.format "全件: {!r}" everything))
  (for [where WHERES]
    (<- pages (collect-pages harness "parts" where 5))
    (require-law (all (gfor page pages (isinstance page Page))) law (.format "{!r} の頁: {!r}" where pages))
    (setv paged (lfor page pages row page.rows row))
    (require-law (= paged (lfor row all-rows :if (row-matches? where row.value) row)) law
                 (.format "{!r} の索引の答えが全件を絞った答えと違う: {!r}" where paged))
    (require-law (all (gfor page pages (<= (len page.rows) 5))) law (.format "頁の上限を越えた: {!r}" pages))
    (.extend transcript pages))
  (<- narrow (as-writer harness MAKER (ListRows "parts" :where (FrozenMap {"color" "red"}) :fields #("color") :limit 2)))
  (require-law (all (gfor row narrow.rows (= (set row.value) #{"id" "color"}))) law (.format "欄の絞り: {!r}" narrow))
  (<- loose (as-writer harness MAKER (ListRows "parts" :where (FrozenMap {"note" "x" "color" "red"}))))
  (require-law (= loose (NotIndexed #("note"))) law (.format "索引の無い欄: {!r}" loose))
  (+ transcript [everything narrow loose]))


;; --- 法 7: 追記の冪等キー ----------------------------------------------------------------------------------

(defk law-append-is-idempotent [#^ LawHarness harness]
  {:pre [(: harness LawHarness)] :post [(: % list)]}
  (setv law "同じ冪等キーの再送は前の番号を返し、別の本文は Refused")
  (<- a1 (as-writer harness MAKER (AppendEvent "journal" "k1" {"n" 1})))
  (<- a2 (as-writer harness MAKER (AppendEvent "journal" "k2" {"n" 2})))
  (<- again (as-writer harness MAKER (AppendEvent "journal" "k1" {"n" 1})))
  (require-law (and (isinstance a1 Appended) (isinstance a2 Appended) (= again a1) (> a2.sequence a1.sequence)) law
               (.format "番号: {!r} {!r} {!r}" a1 a2 again))
  (<- other (as-writer harness MAKER (AppendEvent "journal" "k1" {"n" 9})))
  (<- stranger (as-writer harness STRANGER (AppendEvent "journal" "k3" {"n" 3})))
  (<- big (as-writer harness MAKER (AppendEvent "journal" "k4" {"n" (* "x" 300)})))
  (require-law (all (gfor answer [other stranger big] (isinstance answer Refused))) law
               (.format "断るはずの追記: {!r} {!r} {!r}" other stranger big))
  (<- read (as-writer harness MAKER (ReadEvents "journal")))
  (require-law (= (lfor e read.items #(e.idempotency-key e.body e.writer)) [#("k1" {"n" 1} MAKER) #("k2" {"n" 2} MAKER)])
               law (.format "積んだ出来事: {!r}" read))
  (require-law (= read.last-sequence a2.sequence) law (.format "最後の番号: {!r}" read))
  (<- tail (as-writer harness MAKER (ReadEvents "journal" :after a1.sequence)))
  (require-law (= (lfor e tail.items e.idempotency-key) ["k2"]) law (.format "after より後: {!r}" tail))
  [a1 a2 again other stranger big read tail])


;; --- 法 8: WatchChanges は変更が来るまで待つ ---------------------------------------------------------------

(defk late-write [#^ LawHarness harness]
  {:pre [(: harness LawHarness)] :post [(: % Written)]}
  (<- (Delay 5))
  (<- written (as-writer harness MAKER (PutRow "parts" #("late") (FrozenMap {"label" "l"}) (ExpectAbsent))))
  written)

(defk law-watch-waits-for-a-change [#^ LawHarness harness]
  {:pre [(: harness LawHarness)] :post [(: % list)]}
  (setv law "WatchChanges は変更が来るまで timeout まで待つ")
  (<- start (as-writer harness MAKER (ListRows "parts")))
  (setv cursor (WatchCursor start.epoch start.sequence))
  (<- idle (as-writer harness MAKER (WatchChanges #("parts") cursor :timeout 1.0)))
  (require-law (= idle (Changes #() cursor)) law (.format "変更の無い待ち: {!r}" idle))
  (<- task (Spawn (late-write harness)))
  (<- woke (as-writer harness MAKER (WatchChanges #("parts") cursor :timeout 30.0)))
  (<- written (Wait task))
  (require-law (and (isinstance woke Changes) (= (lfor item woke.items #(item.key item.version)) [#(#("late") written.version)]))
               law (.format "待っている間の変更: {!r}" woke))
  [start idle woke written])


;; --- 法 9: 差分の None はその欄を消す(JSON merge patch の null)------------------------------------------------

(defk law-none-removes-a-field [#^ LawHarness harness]
  {:pre [(: harness LawHarness)] :post [(: % list)]}
  (setv law "PutRow の差分の値 None はその欄を消し、行の値は None を持たない")
  (<- born (as-writer harness MAKER (PutRow "parts" #("p1") (FrozenMap {"label" "a" "color" "red"}) (ExpectAbsent))))
  (require-law (= born (Written 1 (FrozenMap {"id" "p1" "label" "a" "color" "red" "state" "open"}))) law (.format "生まれる行: {!r}" born))
  (<- uncolored (as-writer harness PAINTER (PutRow "parts" #("p1") (FrozenMap {"color" None}) (ExpectVersion 1))))
  (require-law (= uncolored (Written 2 (FrozenMap {"id" "p1" "label" "a" "state" "open"}))) law (.format "欄を消す書き: {!r}" uncolored))
  (<- read (as-writer harness MAKER (ReadRow "parts" #("p1"))))
  (require-law (= read (Row #("p1") (FrozenMap {"id" "p1" "label" "a" "state" "open"}) 2)) law (.format "消した欄が読める: {!r}" read))
  (<- not-yours (as-writer harness PAINTER (PutRow "parts" #("p1") (FrozenMap {"label" None}) (ExpectVersion 2))))
  (require-law (isinstance not-yours Refused) law (.format "書き手でない欄を消せた: {!r}" not-yours))
  (<- keyless (as-writer harness MAKER (PutRow "parts" #("p1") (FrozenMap {"id" None}) (ExpectVersion 2))))
  (require-law (isinstance keyless Refused) law (.format "鍵の欄を消せた: {!r}" keyless))
  (<- stateless (as-writer harness MAKER (PutRow "parts" #("p1") (FrozenMap {"state" None}) (ExpectVersion 2))))
  (require-law (isinstance stateless Refused) law (.format "状態の欄を消せた: {!r}" stateless))
  (<- nothing (as-writer harness MAKER (PutRow "parts" #("p1") (FrozenMap {"note" None}) (ExpectVersion 2))))
  (require-law (= nothing (Written 3 (FrozenMap {"id" "p1" "label" "a" "state" "open"}))) law (.format "無い欄を消す書き: {!r}" nothing))
  (<- fresh (as-writer harness MAKER (PutRow "parts" #("p2") (FrozenMap {"label" "b" "color" None}) (ExpectAbsent))))
  (require-law (= fresh (Written 1 (FrozenMap {"id" "p2" "label" "b" "state" "open"}))) law (.format "None の欄を持って生まれる行: {!r}" fresh))
  (<- listed (as-writer harness MAKER (ListRows "parts")))
  (require-law (and (isinstance listed Page) (not (any (gfor row listed.rows v (.values row.value) (is v None)))))
               law (.format "一覧の行が None を持つ: {!r}" listed))
  [born uncolored read not-yours keyless stateless nothing fresh listed])


;; --- 法 10: 刈った変更より前の位置は Reset・回収は期限切れの行を消す ----------------------------------------------

(defk law-maintenance-prunes-and-sweeps [#^ LawHarness harness]
  {:pre [(: harness LawHarness)] :post [(: % list)]}
  (setv law "刈った変更より前の位置は Reset・floor の位置からは続けられ・行は消えない。回収は期限切れの行だけを 1 回消す")
  (<- start (as-writer harness MAKER (ListRows "parts")))
  (<- w1 (as-writer harness MAKER (PutRow "parts" #("p1") (FrozenMap {"label" "a"}) (ExpectAbsent))))
  (<- middle (as-writer harness MAKER (ListRows "parts")))
  (<- (Delay 100))
  (<- w2 (as-writer harness MAKER (PutRow "parts" #("p2") (FrozenMap {"label" "b"}) (ExpectAbsent))))
  (<- pruned (as-writer harness MAKER (PruneChanges 50)))
  (require-law (= pruned (Pruned middle.sequence 1)) law (.format "50 秒より古い変更 1 つを刈る: {!r}" pruned))
  (<- old (as-writer harness MAKER (WatchChanges #("parts") (WatchCursor start.epoch start.sequence))))
  (require-law (= old (Reset start.epoch)) law (.format "刈った変更より前の位置: {!r}" old))
  (<- kept (as-writer harness MAKER (WatchChanges #("parts") (WatchCursor middle.epoch middle.sequence))))
  (require-law (and (isinstance kept Changes) (= (lfor item kept.items #(item.key item.version)) [#(#("p2") 1)]))
               law (.format "floor の位置から続ける: {!r}" kept))
  (<- again (as-writer harness MAKER (PruneChanges 50)))
  (require-law (= again (Pruned middle.sequence 0)) law (.format "2 度目の刈り取りは何も消さない: {!r}" again))
  (<- listed (as-writer harness MAKER (ListRows "parts")))
  (require-law (= (lfor row listed.rows row.key) [#("p1") #("p2")]) law (.format "刈り取りが行を消した: {!r}" listed))
  (<- t1 (as-writer harness MAKER (PutRow "tickets" #("g1" "t1") (FrozenMap {"owner" "o1"}) (ExpectAbsent))))
  (<- done (as-writer harness MAKER (PutRow "tickets" #("g1" "t1") (FrozenMap {"state" "done"}) (ExpectVersion 1))))
  (<- (Delay (+ TICKET-KEEP-SECONDS 1)))
  (<- swept (as-writer harness MAKER (SweepExpired)))
  (require-law (= swept (Swept 1)) law (.format "期限切れの行 1 つを回収する: {!r}" swept))
  (<- idle (as-writer harness MAKER (SweepExpired)))
  (require-law (= idle (Swept 0)) law (.format "2 度目の回収は何も消さない: {!r}" idle))
  (<- removed (as-writer harness MAKER (WatchChanges #("tickets") kept.cursor)))
  (require-law (and (isinstance removed Changes)
                    (= (lfor item removed.items #((. (type item) __name__) item.key))
                       [#("RowChanged" #("g1" "t1")) #("RowChanged" #("g1" "t1")) #("RowRemoved" #("g1" "t1"))]))
               law (.format "回収した行が変更の列に RowRemoved で出ない: {!r}" removed))
  [start w1 middle w2 pruned old kept again listed t1 done swept idle removed])


;; 全部の法(名 → 法)。SHARED-LAWS = 時間を進めない法(仮想の時計を持たない組でも回せる・答えの比べに使う)。
(setv LAWS {"stale-put-conflicts" law-stale-put-conflicts
            "committed-changes-appear-once-in-order" law-committed-changes-appear-once-in-order
            "epoch-change-resets" law-epoch-change-resets
            "undeclared-writes-are-refused" law-undeclared-writes-are-refused
            "operator-paths-need-an-operator" law-operator-paths-need-an-operator
            "transient-rows-expire" law-transient-rows-expire
            "indexed-list-equals-filtered-scan" law-indexed-list-equals-filtered-scan
            "append-is-idempotent" law-append-is-idempotent
            "watch-waits-for-a-change" law-watch-waits-for-a-change
            "none-removes-a-field" law-none-removes-a-field
            "maintenance-prunes-and-sweeps" law-maintenance-prunes-and-sweeps})
(setv SHARED-LAWS #("stale-put-conflicts" "committed-changes-appear-once-in-order" "epoch-change-resets"
                    "undeclared-writes-are-refused" "operator-paths-need-an-operator" "indexed-list-equals-filtered-scan" "append-is-idempotent"
                    "none-removes-a-field"))
