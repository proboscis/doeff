;;; 記録の handler の適合の筋書き(公開)— どの handler の組も同じ法を満たすことを、同じ Program で確かめる。
;;;
;;; 筋書きは公開 effect と検の口(faults.AdvanceStoreEpoch)だけを撃ち、法が破れたら LawBroken を上げ、撃った effect の答えを
;;; 順に並べた transcript(list)を返す。2 つの handler の組で同じ筋書きを回し、transcript が等しいこと(= 答えが同じ)も確かめられる。
;;;
;;; 組み立ては呼び手: LAW-SCHEMA の宣言で置き場を作り、LawHarness の as-writer(書き手の名・Program → その書き手の handler で包んだ
;;; Program)を渡す。書き手の名は宣言の欄の書き手の名(maker / painter / closer)と、どこにも載らない stranger。
;;; 保持の法(law-transient-rows-expire)は doeff-time の Delay で時間を進めるので、仮想の時計(sim-time-handler)の下で回す。
;;; 待ちの法(law-watch-waits-for-a-change・law-watch-events-waits-for-an-append)は doeff の scheduler の Spawn を使う。
;;; 手入れの法(law-maintenance-prunes-and-sweeps)は手入れの effect(maintenance.SweepExpired / PruneChanges — 公開 effect ではない)も撃つ。
(require doeff-hy.macros [defk <- val])
(val MODULE-TAGS {:context "records" :role "program"})
(import dataclasses [dataclass])
(import collections.abc [Callable])
(import doeff [EffectBase Program])
(import doeff_hy.frozen [FrozenMap])
(import doeff_core_effects.scheduler [Spawn Wait])
(import doeff_time [Delay GetTime])
(import doeff_records.values [FieldDecl TableDecl StreamDecl RecordsSchema KeepFor KeepForever ByKeySuffix ExpectAbsent ExpectVersion ExpectAny
                              WatchCursor ListCursor Row Missing Page Written WrittenRows Conflict Refused NotIndexed Reset
                              Changes RowChanged RowRemoved Appended Events EventsMoved EventsQuiet RowsConflict RowsRefused
                              StreamEnd StreamEmpty])
(import doeff_records.effects [ReadRow ListRows PutRow PutRows RowWrite WatchChanges WatchEvents AppendEvent ReadEvents ReadStreamEnd])
(import doeff_records.faults [AdvanceStoreEpoch])
(import doeff_records.maintenance [SweepExpired PruneChanges Swept Pruned])
(import doeff_records.admission [row-matches? epoch-ms])

;; OVERSEER = 宣言の operators に入る書き手(宣言の形の例 — 置き場は書き手の名では断らない・#2994)。
(setv MAKER "maker" PAINTER "painter" CLOSER "closer" STRANGER "stranger" OVERSEER "overseer")
(setv TICKET-KEEP-SECONDS 60 PAIR-KEEP-SECONDS 60)

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
                                  :retention (KeepFor TICKET-KEEP-SECONDS))
             ;; 宣言の形の例(FieldDecl.founders・operator-paths)— 置き場は書き手の名では断らない(#2994)。
             "charters" (TableDecl :name "charters" :key-fields #("name")
                                   :fields #((FieldDecl "name" #(OVERSEER) :founders #(MAKER))
                                             (FieldDecl "rule" #(OVERSEER) :founders #(MAKER))
                                             (FieldDecl "note" #(OVERSEER)))
                                   :operator-paths #("rule" "note"))})
    ;; pairs = 保持の組(ByKeySuffix「:」)の列 — 法 12。
    :streams (FrozenMap {"journal" (StreamDecl :name "journal" :writers #(MAKER) :size-budget 200)
                         "pairs" (StreamDecl :name "pairs" :writers #(MAKER) :retention (KeepFor PAIR-KEEP-SECONDS)
                                             :retention-group (ByKeySuffix ":"))})
    :operators #(OVERSEER)))


(defclass LawBroken [AssertionError]
  "法が破れた(どの法の何が — 文に書く)。")


(defclass [(dataclass :frozen True)] LawHarness []
  "as-writer = (書き手の名 Program) → その書き手の handler で包んだ Program。"
  (#^ Callable as-writer))


(defk require-law [holds law detail]
  {:pre [(: holds bool) (: law str) (: detail str)] :post [(: % None)]}
  "法 law が成り立たなければ(holds が偽)、何が破れたか(detail)を名指して LawBroken を上げるため。"
  (when (not holds) (raise (LawBroken (.format "{}: {}" law detail))))
  None)


(defk as-writer [harness writer program]
  {:tp [T] :pre [(: harness LawHarness) (: writer str) (: program (| (get Program #(T object)) (get EffectBase T)))] :post [(: % T)]}
  "program(Program か effect)を書き手 writer の handler で包んで走らせ、その答えを返すため(法の筋書きが書き手を替えて
   同じ置き場を撃つ)。答えは program の答え(型の引数 T)。"
  (<- answer (harness.as-writer writer program))
  answer)


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
  {:pre [(: harness LawHarness) (: table str) (: where FrozenMap) (: limit int)] :post [(: % (get list object))]}
  "next-cursor が尽きるまで ListRows の頁を読み、頁を全部並べる。"
  (setv pages [] cursor None)
  (while True
    (<- page (as-writer harness MAKER (ListRows table :where where :cursor cursor :limit limit)))
    (.append pages page)
    (when (or (not (isinstance page Page)) (is page.next-cursor None)) (return pages))
    (setv cursor page.next-cursor)))


;; --- 法 1: 古い版の PutRow は Conflict ---------------------------------------------------------------------

(defk law-stale-put-conflicts [#^ LawHarness harness]
  {:pre [(: harness LawHarness)] :post [(: % (get list object))]}
  (setv law "古い版の PutRow は Conflict" t [])
  (<- born (as-writer harness MAKER (PutRow "parts" #("p1") (FrozenMap {"label" "a"}) (ExpectAbsent))))
  (<- (require-law (= born (Written 1 (FrozenMap {"id" "p1" "label" "a" "state" "open"}))) law (.format "生まれる行: {!r}" born)))
  (<- second (as-writer harness MAKER (PutRow "parts" #("p1") (FrozenMap {"label" "b"}) (ExpectVersion 1))))
  (<- (require-law (and (isinstance second Written) (= second.version 2)) law (.format "版 1 への書き: {!r}" second)))
  (setv now (Row #("p1") (FrozenMap {"id" "p1" "label" "b" "state" "open"}) 2))
  (<- stale (as-writer harness MAKER (PutRow "parts" #("p1") (FrozenMap {"label" "c"}) (ExpectVersion 1))))
  (<- (require-law (= stale (Conflict now)) law (.format "古い版 1 への書き: {!r}" stale)))
  (<- absent (as-writer harness MAKER (PutRow "parts" #("p1") (FrozenMap {"label" "d"}) (ExpectAbsent))))
  (<- (require-law (= absent (Conflict now)) law (.format "在る行への ExpectAbsent: {!r}" absent)))
  (<- missing (as-writer harness MAKER (PutRow "parts" #("p-none") (FrozenMap {"label" "d"}) (ExpectVersion 3))))
  (<- (require-law (= missing (Conflict (Missing))) law (.format "無い行への ExpectVersion: {!r}" missing)))
  (<- read (as-writer harness MAKER (ReadRow "parts" #("p1"))))
  (<- (require-law (= read now) law (.format "衝突は行を変えない: {!r}" read)))
  (<- nothing (as-writer harness MAKER (ReadRow "parts" #("p-none"))))
  (<- (require-law (= nothing (Missing)) law (.format "衝突は行を作らない: {!r}" nothing)))
  (<- blind (as-writer harness MAKER (PutRow "parts" #("p1") (FrozenMap {"label" "e"}) (ExpectAny))))
  (<- (require-law (and (isinstance blind Written) (= blind.version 3)) law (.format "ExpectAny: {!r}" blind)))
  [born second stale absent missing read nothing blind])


;; --- 法 2: 確定した変更は WatchChanges にちょうど 1 回・順序どおり -----------------------------------------

(defk law-committed-changes-appear-once-in-order [#^ LawHarness harness]
  {:pre [(: harness LawHarness)] :post [(: % (get list object))]}
  (setv law "確定した変更は WatchChanges にちょうど 1 回・順序どおり")
  (<- start (as-writer harness MAKER (ListRows "parts" :limit 1)))
  (<- (require-law (isinstance start Page) law (.format "最初の一覧: {!r}" start)))
  (setv cursor (WatchCursor start.epoch start.sequence))
  (<- w1 (as-writer harness MAKER (PutRow "parts" #("p1") (FrozenMap {"label" "x"}) (ExpectAbsent))))
  (<- w2 (as-writer harness MAKER (PutRow "tickets" #("g1" "t1") (FrozenMap {"owner" "o1"}) (ExpectAbsent))))
  (<- refused (as-writer harness MAKER (PutRow "parts" #("p1") (FrozenMap {"size" 1}) (ExpectAny))))
  (<- conflict (as-writer harness MAKER (PutRow "parts" #("p1") (FrozenMap {"label" "y"}) (ExpectAbsent))))
  (<- w3 (as-writer harness PAINTER (PutRow "parts" #("p1") (FrozenMap {"color" "red"}) (ExpectVersion 1))))
  (<- w4 (as-writer harness MAKER (PutRow "parts" #("p2") (FrozenMap {"label" "z"}) (ExpectAbsent))))
  (<- w5 (as-writer harness MAKER (PutRow "tickets" #("g1" "t1") (FrozenMap {"state" "done"}) (ExpectVersion 1))))
  (<- (require-law (and (isinstance refused Refused) (isinstance conflict Conflict)) law
               (.format "断る書きと衝突する書き: {!r} {!r}" refused conflict)))
  (setv committed [#("parts" #("p1") w1) #("tickets" #("g1" "t1") w2) #("parts" #("p1") w3) #("parts" #("p2") w4)
                   #("tickets" #("g1" "t1") w5)])
  (for [#(_ _ written) committed]
    (<- (require-law (isinstance written Written) law (.format "確定するはずの書き: {!r}" written))))
  (<- collected (collect-changes harness #("parts" "tickets") cursor 2))
  (setv #(answers end) collected)
  (setv items (lfor answer answers item answer.items item))
  (<- (require-law (all (gfor answer answers (isinstance answer Changes))) law (.format "Changes 以外の答え: {!r}" answers)))
  (<- (require-law (= (lfor item items #(item.table item.key item.version (dict item.value)))
                  (lfor #(table key written) committed #(table key written.version written.value)))
               law (.format "確定した変更と見えた変更が違う: {!r}" items)))
  (setv sequences (lfor item items item.sequence))
  (<- (require-law (= sequences (sorted (set sequences))) law (.format "番号が昇順で重複なしでない: {!r}" sequences)))
  (<- (require-law (all (gfor s sequences (> s cursor.sequence))) law (.format "位置より前の変更が見えた: {!r}" sequences)))
  (<- parts-collected (collect-changes harness #("parts") cursor 100))
  (setv only-parts (get parts-collected 0))
  (setv part-items (lfor answer only-parts item answer.items item))
  (<- (require-law (= (lfor item part-items #(item.key item.version)) [#(#("p1") 1) #(#("p1") 2) #(#("p2") 1)])
               law (.format "表で絞った変更: {!r}" part-items)))
  (<- after (as-writer harness MAKER (WatchChanges #("parts" "tickets") end)))
  (<- (require-law (and (isinstance after Changes) (= after.items #())) law (.format "読み終えた位置の後に変更が残る: {!r}" after)))
  (+ [start w1 w2 refused conflict w3 w4 w5] answers only-parts [after]))


;; --- 法 3: epoch が変わると Reset ------------------------------------------------------------------------

(defk law-epoch-change-resets [#^ LawHarness harness]
  {:pre [(: harness LawHarness)] :post [(: % (get list object))]}
  (setv law "epoch が変わると Reset")
  (<- first (as-writer harness MAKER (ListRows "parts")))
  (<- w1 (as-writer harness MAKER (PutRow "parts" #("p1") (FrozenMap {"label" "a"}) (ExpectAbsent))))
  (<- epoch (as-writer harness MAKER (AdvanceStoreEpoch)))
  (<- (require-law (and (isinstance epoch int) (!= epoch first.epoch)) law (.format "新しい epoch: {!r}" epoch)))
  (<- watched (as-writer harness MAKER (WatchChanges #("parts") (WatchCursor first.epoch first.sequence))))
  ;; 版を上げた置き場の floor は上げた時の頭(w1 の変更の番号)— 新しい版の変更はその後から積まれる。
  (<- (require-law (= watched (Reset epoch (+ first.sequence 1))) law (.format "古い epoch の位置の WatchChanges: {!r}" watched)))
  (<- listed (as-writer harness MAKER (ListRows "parts" :cursor (ListCursor first.epoch "[\"p0\"]"))))
  (<- (require-law (= listed (Reset epoch (+ first.sequence 1))) law (.format "古い epoch の位置の ListRows: {!r}" listed)))
  (<- again (as-writer harness MAKER (ListRows "parts")))
  (<- (require-law (and (isinstance again Page) (= again.epoch epoch) (= (lfor row again.rows row.key) [#("p1")]))
               law (.format "読み直した一覧(行は残る): {!r}" again)))
  (<- w2 (as-writer harness MAKER (PutRow "parts" #("p2") (FrozenMap {"label" "b"}) (ExpectAbsent))))
  (<- fresh (as-writer harness MAKER (WatchChanges #("parts") (WatchCursor again.epoch again.sequence))))
  (<- (require-law (and (isinstance fresh Changes) (= (lfor item fresh.items item.key) [#("p2")]))
               law (.format "新しい位置からの変更: {!r}" fresh)))
  (<- resumed (as-writer harness MAKER (WatchChanges #("parts") (WatchCursor watched.epoch watched.floor))))
  (<- (require-law (= resumed fresh) law (.format "Reset の (epoch, floor) から読み直した変更: {!r}" resumed)))
  [first w1 epoch watched listed again w2 fresh resumed])


;; --- 法 4: 宣言に無い欄・状態・上限・鍵の形・終端は Refused・書き手の名では断らない -----------------------------------

(defk law-undeclared-writes-are-refused [#^ LawHarness harness]
  {:pre [(: harness LawHarness)] :post [(: % (get list object))]}
  "表の宣言(欄・状態の語・上限・鍵・終端)が許さない書きを断り、行を変えないことを、どの置き場の組でも同じに確かめるための法。
   書き手の名(欄の writers・operator の宣言の欄)では断らない(#2994)— 欄の書き手でない STRANGER の書きも通る。"
  (setv law "宣言が許さない書きは Refused で、行を変えない")
  (<- born (as-writer harness MAKER (PutRow "parts" #("p1") (FrozenMap {"label" "a"}) (ExpectAbsent))))
  (setv refusals [])
  (for [#(writer table key diff) [#(MAKER "parts" #("p1") {"size" 1})
                                  #(MAKER "parts" #("p1") {"state" "weird"})
                                  #(MAKER "parts" #("p1") {"note" (* "n" 500)})
                                  #(MAKER "parts" #("p1") {"id" "p-other"})
                                  #(MAKER "tickets" #("only-one") {"owner" "o"})]]
    (<- answer (as-writer harness writer (PutRow table key diff (ExpectAny))))
    (<- (require-law (isinstance answer Refused) law (.format "{} の {!r} {!r}: {!r}" writer key diff answer)))
    (.append refusals answer))
  (<- untouched (as-writer harness MAKER (ReadRow "parts" #("p1"))))
  (<- (require-law (= untouched (Row #("p1") (FrozenMap {"id" "p1" "label" "a" "state" "open"}) 1)) law
               (.format "断った書きが行を変えた: {!r}" untouched)))
  (<- stranger (as-writer harness STRANGER (PutRow "parts" #("p1") (FrozenMap {"label" "s" "grant" "yes"}) (ExpectVersion 1))))
  (<- (require-law (and (isinstance stranger Written) (= stranger.version 2)) law
               (.format "欄の書き手でない書き手の書き(operator の宣言の欄を含む)が断られた: {!r}" stranger)))
  (<- closed (as-writer harness CLOSER (PutRow "parts" #("p1") (FrozenMap {"state" "closed"}) (ExpectVersion 2))))
  (<- (require-law (and (isinstance closed Written) (= (get closed.value "state") "closed")) law (.format "終端へ: {!r}" closed)))
  (<- frozen (as-writer harness MAKER (PutRow "parts" #("p1") (FrozenMap {"label" "after"}) (ExpectVersion 3))))
  (<- (require-law (isinstance frozen Refused) law (.format "終端の行への書き: {!r}" frozen)))
  (<- final (as-writer harness MAKER (ReadRow "parts" #("p1"))))
  (<- (require-law (and (isinstance final Row) (= final.version 3)) law (.format "終端の行が変わった: {!r}" final)))
  (+ [born] refusals [untouched stranger closed frozen final]))


;; --- 法 5: transient の行は期限で消え、record の行は消えない ------------------------------------------------

(defk law-transient-rows-expire [#^ LawHarness harness]
  {:pre [(: harness LawHarness)] :post [(: % (get list object))]}
  (setv law "transient の行は期限で消え、record の行は消えない")
  (<- t1 (as-writer harness MAKER (PutRow "tickets" #("g1" "t1") (FrozenMap {"owner" "o1"}) (ExpectAbsent))))
  (<- t2 (as-writer harness MAKER (PutRow "tickets" #("g1" "t2") (FrozenMap {"owner" "o2"}) (ExpectAbsent))))
  (<- p1 (as-writer harness MAKER (PutRow "parts" #("p1") (FrozenMap {"state" "closed"}) (ExpectAbsent))))
  (<- done (as-writer harness MAKER (PutRow "tickets" #("g1" "t1") (FrozenMap {"state" "done"}) (ExpectVersion 1))))
  (<- start (as-writer harness MAKER (ListRows "tickets")))
  (<- (Delay (- TICKET-KEEP-SECONDS 1)))
  (<- before (as-writer harness MAKER (ReadRow "tickets" #("g1" "t1"))))
  (<- (require-law (and (isinstance before Row) (= before.version 2)) law (.format "期限の前に消えた: {!r}" before)))
  (<- (Delay 2))
  (<- gone (as-writer harness MAKER (ReadRow "tickets" #("g1" "t1"))))
  (<- (require-law (= gone (Missing)) law (.format "期限を過ぎても残る: {!r}" gone)))
  (<- listed (as-writer harness MAKER (ListRows "tickets")))
  (<- (require-law (= (lfor row listed.rows row.key) [#("g1" "t2")]) law (.format "一覧に期限切れが残る・終端でない行が消えた: {!r}" listed)))
  (<- removed (as-writer harness MAKER (WatchChanges #("tickets") (WatchCursor start.epoch start.sequence))))
  (<- (require-law (and (isinstance removed Changes) (= (lfor item removed.items #((type item) item.key)) [#(RowRemoved #("g1" "t1"))]))
               law (.format "消えた行が変更に 1 回だけ出ない: {!r}" removed)))
  (<- (Delay (* 100 TICKET-KEEP-SECONDS)))
  (<- kept (as-writer harness MAKER (ReadRow "parts" #("p1"))))
  (<- (require-law (and (isinstance kept Row) (= (get kept.value "state") "closed")) law (.format "record の行が消えた: {!r}" kept)))
  (<- open-kept (as-writer harness MAKER (ReadRow "tickets" #("g1" "t2"))))
  (<- (require-law (isinstance open-kept Row) law (.format "終端でない transient の行が消えた: {!r}" open-kept)))
  [t1 t2 p1 done start before gone listed removed kept open-kept])


;; --- 法 6: 索引の ListRows は全件を読んで絞った結果と同じ -------------------------------------------------

(setv COLORS #("red" "blue" "green"))
(setv LABELS #("a" "b"))
(setv WHERES (lfor where [{} {"color" "red"} {"label" "a"} {"color" "blue" "label" "b"} {"id" "p03"} {"color" "none"}] (FrozenMap where)))

(defk law-indexed-list-equals-filtered-scan [#^ LawHarness harness]
  {:pre [(: harness LawHarness)] :post [(: % (get list object))]}
  (setv law "索引の ListRows は全件を読んで絞った結果と同じ" transcript [])
  (for [n (range 1 13)]
    (<- written (as-writer harness MAKER (PutRow "parts" #((.format "p{:02d}" n))
                                                 (FrozenMap {"color" (get COLORS (% n 3)) "label" (get LABELS (% n 2))}) (ExpectAbsent))))
    (.append transcript written))
  (<- everything (collect-pages harness "parts" (FrozenMap) 500))
  (setv all-rows (lfor page everything row page.rows row))
  (<- (require-law (= (len all-rows) 12) law (.format "全件: {!r}" everything)))
  (for [where WHERES]
    (<- pages (collect-pages harness "parts" where 5))
    (<- (require-law (all (gfor page pages (isinstance page Page))) law (.format "{!r} の頁: {!r}" where pages)))
    (setv paged (lfor page pages row page.rows row))
    (<- (require-law (= paged (lfor row all-rows :if (row-matches? where row.value) row)) law
                 (.format "{!r} の索引の答えが全件を絞った答えと違う: {!r}" where paged)))
    (<- (require-law (all (gfor page pages (<= (len page.rows) 5))) law (.format "頁の上限を越えた: {!r}" pages)))
    (.extend transcript pages))
  (<- narrow (as-writer harness MAKER (ListRows "parts" :where (FrozenMap {"color" "red"}) :fields #("color") :limit 2)))
  (<- (require-law (all (gfor row narrow.rows (= (set row.value) #{"id" "color"}))) law (.format "欄の絞り: {!r}" narrow)))
  (<- loose (as-writer harness MAKER (ListRows "parts" :where (FrozenMap {"note" "x" "color" "red"}))))
  (<- (require-law (= loose (NotIndexed #("note"))) law (.format "索引の無い欄: {!r}" loose)))
  (+ transcript [everything narrow loose]))


;; --- 法 7: 追記の冪等キー ----------------------------------------------------------------------------------

(defk law-append-is-idempotent [#^ LawHarness harness]
  {:pre [(: harness LawHarness)] :post [(: % (get list object))]}
  (setv law "同じ冪等キーの再送は前の番号を返し、別の本文は Refused")
  (<- a1 (as-writer harness MAKER (AppendEvent "journal" "k1" {"n" 1})))
  (<- a2 (as-writer harness MAKER (AppendEvent "journal" "k2" {"n" 2})))
  (<- again (as-writer harness MAKER (AppendEvent "journal" "k1" {"n" 1})))
  (<- (require-law (and (isinstance a1 Appended) (isinstance a2 Appended) (= again a1) (> a2.sequence a1.sequence)) law
               (.format "番号: {!r} {!r} {!r}" a1 a2 again)))
  (<- other (as-writer harness MAKER (AppendEvent "journal" "k1" {"n" 9})))
  (<- big (as-writer harness MAKER (AppendEvent "journal" "k4" {"n" (* "x" 300)})))
  (<- (require-law (all (gfor answer [other big] (isinstance answer Refused))) law
               (.format "断るはずの追記: {!r} {!r}" other big)))
  ;; 列の writers に居ない書き手の追記も通り、書き手の名は出来事に残る(書き手の名では断らない・#2994)。
  (<- stranger (as-writer harness STRANGER (AppendEvent "journal" "k3" {"n" 3})))
  (<- (require-law (and (isinstance stranger Appended) (> stranger.sequence a2.sequence)) law
               (.format "列の writers に居ない書き手の追記: {!r}" stranger)))
  (<- read (as-writer harness MAKER (ReadEvents "journal")))
  (<- (require-law (= (lfor e read.items #(e.idempotency-key e.body e.writer))
                      [#("k1" {"n" 1} MAKER) #("k2" {"n" 2} MAKER) #("k3" {"n" 3} STRANGER)])
               law (.format "積んだ出来事: {!r}" read)))
  (<- (require-law (= read.last-sequence stranger.sequence) law (.format "最後の番号: {!r}" read)))
  (<- tail (as-writer harness MAKER (ReadEvents "journal" :after a1.sequence)))
  (<- (require-law (= (lfor e tail.items e.idempotency-key) ["k2" "k3"]) law (.format "after より後: {!r}" tail)))
  [a1 a2 again other stranger big read tail])


;; --- 法 8: WatchChanges は変更が来るまで待つ ---------------------------------------------------------------

(defk late-write [#^ LawHarness harness]
  {:pre [(: harness LawHarness)] :post [(: % Written)]}
  (<- (Delay 5))
  (<- written (as-writer harness MAKER (PutRow "parts" #("late") (FrozenMap {"label" "l"}) (ExpectAbsent))))
  written)

(defk law-watch-waits-for-a-change [#^ LawHarness harness]
  {:pre [(: harness LawHarness)] :post [(: % (get list object))]}
  (setv law "WatchChanges は変更が来るまで timeout まで待つ")
  (<- start (as-writer harness MAKER (ListRows "parts")))
  (setv cursor (WatchCursor start.epoch start.sequence))
  (<- idle (as-writer harness MAKER (WatchChanges #("parts") cursor :timeout 1.0)))
  (<- (require-law (= idle (Changes #() cursor)) law (.format "変更の無い待ち: {!r}" idle)))
  (<- task (Spawn (late-write harness)))
  (<- woke (as-writer harness MAKER (WatchChanges #("parts") cursor :timeout 30.0)))
  (<- written (Wait task))
  (<- (require-law (and (isinstance woke Changes) (= (lfor item woke.items #(item.key item.version)) [#(#("late") written.version)]))
               law (.format "待っている間の変更: {!r}" woke)))
  [start idle woke written])


;; --- 法 8b: WatchEvents は列の頭が進むまで待つ(行の書きでは起きない)--------------------------------------------

(defk late-append [#^ LawHarness harness]
  {:pre [(: harness LawHarness)] :post [(: % Appended)]
   :tags {:context "records" :role "program"}}
  "待ちの法 8b の書き手: 2 秒後に待つ列の外(表 parts)へ 1 行書き、5 秒後に待つ列 journal へ 1 つ積むため(行の書きで列の待ち手が
   答えを返さないことと、追記で返すことを同じ筋書きで見る)。"
  (<- (Delay 2))
  (<- (as-writer harness MAKER (PutRow "parts" #("side") (FrozenMap {"label" "s"}) (ExpectAbsent))))
  (<- (Delay 3))
  (<- appended (as-writer harness MAKER (AppendEvent "journal" "late" {"n" 1})))
  appended)

(defk law-watch-events-waits-for-an-append [#^ LawHarness harness]
  {:pre [(: harness LawHarness)] :post [(: % (get list object))]
   :tags {:context "records" :role "program"}}
  "列の待ちの法: 頭が after より進んでいれば待たずに EventsMoved・進まなければ timeout で EventsQuiet(after が頭より先でも誤りに
   しない)・待っている間の追記で EventsMoved(列の外の行の書きでは返らない)。どの置き場の handler も同じ答えを返すことを確かめるため。"
  (setv law "WatchEvents は列の頭が after より進むまで timeout まで待つ")
  (<- first (as-writer harness MAKER (AppendEvent "journal" "first" {"n" 0})))
  (<- moved (as-writer harness MAKER (WatchEvents "journal" :after 0 :timeout 30.0)))
  (<- (require-law (= moved (EventsMoved)) law (.format "もう進んでいる列の待ち: {!r}" moved)))
  (<- idle (as-writer harness MAKER (WatchEvents "journal" :after first.sequence :timeout 1.0)))
  (<- (require-law (= idle (EventsQuiet)) law (.format "進まない列の待ち: {!r}" idle)))
  (<- ahead (as-writer harness MAKER (WatchEvents "journal" :after (+ first.sequence 100) :timeout 0.0)))
  (<- (require-law (= ahead (EventsQuiet)) law (.format "頭より先の after: {!r}" ahead)))
  (<- task (Spawn (late-append harness)))
  (<- woke (as-writer harness MAKER (WatchEvents "journal" :after first.sequence :timeout 30.0)))
  (<- appended (Wait task))
  (<- (require-law (= woke (EventsMoved)) law (.format "待っている間の追記: {!r}" woke)))
  (<- read (as-writer harness MAKER (ReadEvents "journal" :after first.sequence)))
  (<- (require-law (= (lfor e read.items e.idempotency-key) ["late"]) law (.format "起きた後の読み: {!r}" read)))
  [first moved idle ahead woke appended read])


;; --- 法 9: 差分の None はその欄を消す(JSON merge patch の null)------------------------------------------------

(defk law-none-removes-a-field [#^ LawHarness harness]
  {:pre [(: harness LawHarness)] :post [(: % (get list object))]}
  (setv law "PutRow の差分の値 None はその欄を消し、行の値は None を持たない")
  (<- born (as-writer harness MAKER (PutRow "parts" #("p1") (FrozenMap {"label" "a" "color" "red"}) (ExpectAbsent))))
  (<- (require-law (= born (Written 1 (FrozenMap {"id" "p1" "label" "a" "color" "red" "state" "open"}))) law (.format "生まれる行: {!r}" born)))
  (<- uncolored (as-writer harness PAINTER (PutRow "parts" #("p1") (FrozenMap {"color" None}) (ExpectVersion 1))))
  (<- (require-law (= uncolored (Written 2 (FrozenMap {"id" "p1" "label" "a" "state" "open"}))) law (.format "欄を消す書き: {!r}" uncolored)))
  (<- read (as-writer harness MAKER (ReadRow "parts" #("p1"))))
  (<- (require-law (= read (Row #("p1") (FrozenMap {"id" "p1" "label" "a" "state" "open"}) 2)) law (.format "消した欄が読める: {!r}" read)))
  (<- keyless (as-writer harness MAKER (PutRow "parts" #("p1") (FrozenMap {"id" None}) (ExpectVersion 2))))
  (<- (require-law (isinstance keyless Refused) law (.format "鍵の欄を消せた: {!r}" keyless)))
  (<- stateless (as-writer harness MAKER (PutRow "parts" #("p1") (FrozenMap {"state" None}) (ExpectVersion 2))))
  (<- (require-law (isinstance stateless Refused) law (.format "状態の欄を消せた: {!r}" stateless)))
  (<- nothing (as-writer harness MAKER (PutRow "parts" #("p1") (FrozenMap {"note" None}) (ExpectVersion 2))))
  (<- (require-law (= nothing (Written 3 (FrozenMap {"id" "p1" "label" "a" "state" "open"}))) law (.format "無い欄を消す書き: {!r}" nothing)))
  (<- fresh (as-writer harness MAKER (PutRow "parts" #("p2") (FrozenMap {"label" "b" "color" None}) (ExpectAbsent))))
  (<- (require-law (= fresh (Written 1 (FrozenMap {"id" "p2" "label" "b" "state" "open"}))) law (.format "None の欄を持って生まれる行: {!r}" fresh)))
  (<- listed (as-writer harness MAKER (ListRows "parts")))
  (<- (require-law (and (isinstance listed Page) (not (any (gfor row listed.rows v (.values row.value) (is v None)))))
               law (.format "一覧の行が None を持つ: {!r}" listed)))
  [born uncolored read keyless stateless nothing fresh listed])


;; --- 法 10: 刈った変更より前の位置は Reset・回収は期限切れの行を消す ----------------------------------------------

(defk law-maintenance-prunes-and-sweeps [#^ LawHarness harness]
  {:pre [(: harness LawHarness)] :post [(: % (get list object))]}
  (setv law "刈った変更より前の位置は Reset・floor の位置からは続けられ・行は消えない。回収は期限切れの行だけを 1 回消す")
  (<- start (as-writer harness MAKER (ListRows "parts")))
  (<- before (GetTime))
  (<- w1 (as-writer harness MAKER (PutRow "parts" #("p1") (FrozenMap {"label" "a"}) (ExpectAbsent))))
  (<- middle (as-writer harness MAKER (ListRows "parts")))
  (<- (Delay 100))
  (<- w2 (as-writer harness MAKER (PutRow "parts" #("p2") (FrozenMap {"label" "b"}) (ExpectAbsent))))
  (<- after (GetTime))
  ;; 変更の確定の刻 at は書いた時の時計: 2 つの書きの間の 100 秒が刻の差に出る(刈り取りの判定と同じ刻)。
  (<- written (as-writer harness MAKER (WatchChanges #("parts") (WatchCursor start.epoch start.sequence))))
  (setv ats (if (isinstance written Changes) (lfor item written.items item.at) []))
  (<- (require-law (and (= (len ats) 2) (<= (epoch-ms before) (get ats 0)) (<= (get ats 1) (epoch-ms after))
                    (>= (- (get ats 1) (get ats 0)) 100000))
               law (.format "変更の確定の刻が書いた時の時計でない: {!r}" written)))
  (<- pruned (as-writer harness MAKER (PruneChanges 50)))
  (<- (require-law (= pruned (Pruned middle.sequence 1)) law (.format "50 秒より古い変更 1 つを刈る: {!r}" pruned)))
  (<- old (as-writer harness MAKER (WatchChanges #("parts") (WatchCursor start.epoch start.sequence))))
  (<- (require-law (= old (Reset start.epoch middle.sequence)) law (.format "刈った変更より前の位置(floor = 刈った位置): {!r}" old)))
  ;; 読み手は Reset の (epoch, floor) だけで、刈り残った変更を頭から全部読める。
  (<- kept (as-writer harness MAKER (WatchChanges #("parts") (WatchCursor old.epoch old.floor))))
  (<- (require-law (and (isinstance kept Changes) (= (lfor item kept.items #(item.key item.version)) [#(#("p2") 1)]))
               law (.format "Reset の floor の位置から残った変更を全部読む: {!r}" kept)))
  (<- again (as-writer harness MAKER (PruneChanges 50)))
  (<- (require-law (= again (Pruned middle.sequence 0)) law (.format "2 度目の刈り取りは何も消さない: {!r}" again)))
  (<- listed (as-writer harness MAKER (ListRows "parts")))
  (<- (require-law (= (lfor row listed.rows row.key) [#("p1") #("p2")]) law (.format "刈り取りが行を消した: {!r}" listed)))
  (<- t1 (as-writer harness MAKER (PutRow "tickets" #("g1" "t1") (FrozenMap {"owner" "o1"}) (ExpectAbsent))))
  (<- done (as-writer harness MAKER (PutRow "tickets" #("g1" "t1") (FrozenMap {"state" "done"}) (ExpectVersion 1))))
  (<- (Delay (+ TICKET-KEEP-SECONDS 1)))
  (<- swept (as-writer harness MAKER (SweepExpired)))
  (<- (require-law (= swept (Swept 1)) law (.format "期限切れの行 1 つを回収する: {!r}" swept)))
  (<- idle (as-writer harness MAKER (SweepExpired)))
  (<- (require-law (= idle (Swept 0)) law (.format "2 度目の回収は何も消さない: {!r}" idle)))
  (<- removed (as-writer harness MAKER (WatchChanges #("tickets") kept.cursor)))
  (<- (require-law (and (isinstance removed Changes)
                    (= (lfor item removed.items #((. (type item) __name__) item.key))
                       [#("RowChanged" #("g1" "t1")) #("RowChanged" #("g1" "t1")) #("RowRemoved" #("g1" "t1"))]))
               law (.format "回収した行が変更の列に RowRemoved で出ない: {!r}" removed)))
  [start w1 middle w2 written pruned old kept again listed t1 done swept idle removed])


;; --- 法 11: PutRows は全部か 0 ----------------------------------------------------------------------------------

(defk law-put-rows-is-all-or-nothing [harness]
  {:pre [(: harness LawHarness)] :post [(: % (get list object))]}
  "複数行の書き(PutRows)が 1 transaction の約束を守ることを、どの置き場の組でも同じに確かめるための法:
   全部通る束は束の順の Written を返し、期待のずれ 1 行・書きの断り 1 行の束は 1 行も書かない・期待のずれは断りより先に答える・
   確定した束の変更は束の順に続いた番号で 1 回ずつ見え、通らなかった束の変更は見えない。"
  (val law "PutRows は全部か 0 で書き、確定した束の変更は束の順に続いた番号で見える")
  (<- start (as-writer harness MAKER (ListRows "parts" :limit 1)))
  (<- (require-law (isinstance start Page) law (.format "最初の一覧: {!r}" start)))
  (<- seed (as-writer harness MAKER (PutRow "parts" #("p1") (FrozenMap {"label" "a"}) (ExpectAbsent))))
  (<- (require-law (= seed (Written 1 (FrozenMap {"id" "p1" "label" "a" "state" "open"}))) law (.format "種の行: {!r}" seed)))
  (<- batch (as-writer harness MAKER
              (PutRows #((RowWrite "parts" #("p1") (FrozenMap {"label" "b"}) (ExpectVersion 1))
                         (RowWrite "parts" #("p2") (FrozenMap {"label" "c"}) (ExpectAbsent))
                         (RowWrite "tickets" #("g1" "t1") (FrozenMap {"owner" "o1"}) (ExpectAbsent))))))
  (<- (require-law (= batch (WrittenRows #((Written 2 (FrozenMap {"id" "p1" "label" "b" "state" "open"}))
                                       (Written 1 (FrozenMap {"id" "p2" "label" "c" "state" "open"}))
                                       (Written 1 (FrozenMap {"group" "g1" "id" "t1" "owner" "o1" "state" "open"})))))
               law (.format "全部通る束: {!r}" batch)))
  (val p1 (Row #("p1") (FrozenMap {"id" "p1" "label" "b" "state" "open"}) 2))
  (val p2 (Row #("p2") (FrozenMap {"id" "p2" "label" "c" "state" "open"}) 1))
  ;; 期待のずれ 1 行(束の 2 行目 — 1 行目は通る書き)。
  (<- stale (as-writer harness MAKER
              (PutRows #((RowWrite "parts" #("p2") (FrozenMap {"label" "d"}) (ExpectVersion 1))
                         (RowWrite "parts" #("p1") (FrozenMap {"label" "e"}) (ExpectVersion 1))))))
  (<- (require-law (= stale (RowsConflict 1 "parts" #("p1") p1)) law (.format "期待のずれ 1 行の束: {!r}" stale)))
  ;; 書きの断り 1 行(状態の語 weird は宣言の外 — 束の 2 行目)。
  (<- refused (as-writer harness MAKER
                (PutRows #((RowWrite "parts" #("p1") (FrozenMap {"color" "red"}) (ExpectVersion 2))
                           (RowWrite "parts" #("p2") (FrozenMap {"state" "weird"}) (ExpectVersion 1))))))
  (<- (require-law (and (isinstance refused RowsRefused) (= #(refused.index refused.table refused.key) #(1 "parts" #("p2"))))
               law (.format "書きの断り 1 行の束: {!r}" refused)))
  ;; 断られる行(0 行目)と期待のずれの行(1 行目)が両方ある束は、期待のずれを先に答える。
  (<- first-conflict (as-writer harness MAKER
                       (PutRows #((RowWrite "parts" #("p2") (FrozenMap {"state" "weird"}) (ExpectVersion 1))
                                  (RowWrite "parts" #("p1") (FrozenMap {"color" "red"}) (ExpectVersion 1))))))
  (<- (require-law (= first-conflict (RowsConflict 1 "parts" #("p1") p1)) law (.format "期待のずれを断りより先に: {!r}" first-conflict)))
  (<- read-p1 (as-writer harness MAKER (ReadRow "parts" #("p1"))))
  (<- read-p2 (as-writer harness MAKER (ReadRow "parts" #("p2"))))
  (<- (require-law (and (= read-p1 p1) (= read-p2 p2)) law
               (.format "通らなかった束が行を変えた: {!r} {!r}" read-p1 read-p2)))
  (<- collected (collect-changes harness #("parts" "tickets") (WatchCursor start.epoch start.sequence) 100))
  (val answers (get collected 0))
  (val items (lfor answer answers :if (isinstance answer Changes) item answer.items item))
  (<- (require-law (= (lfor item items #(item.table item.key item.version))
                  [#("parts" #("p1") 1) #("parts" #("p1") 2) #("parts" #("p2") 1) #("tickets" #("g1" "t1") 1)])
               law (.format "確定した変更と見えた変更が違う(通らなかった束の変更が見えた・束の順でない): {!r}" items)))
  (val batch-sequences (lfor item (cut items 1 None) item.sequence))
  (<- (require-law (= batch-sequences (list (range (get batch-sequences 0) (+ (get batch-sequences 0) 3)))) law
               (.format "束の変更の番号が続いていない: {!r}" batch-sequences)))
  (+ [start seed batch stale refused first-conflict read-p1 read-p2] answers))


;; --- 法 12: 組で数える列の出来事は組の最後の出来事から数えて同時に消える -----------------------------------------------------

(defk law-grouped-events-expire-together [harness]
  {:pre [(: harness LawHarness)] :post [(: % (get list object))]}
  "保持の組(ByKeySuffix)の法: 組の後の出来事が残る間は前の出来事も残り(同じ本文の再送は前の番号)、組の最後の出来事から保持の秒で
   組ごと消える。区切りを含まないキーと、後の出来事の無い組は、出来事ごとに消える。"
  (val law "組で数える列の出来事は組の最後の出来事から数えて同時に消える")
  (<- ask-a (as-writer harness MAKER (AppendEvent "pairs" "ask:a" {"n" 1})))
  (<- ask-b (as-writer harness MAKER (AppendEvent "pairs" "ask:b" {"n" 2})))
  (<- solo (as-writer harness MAKER (AppendEvent "pairs" "solo" {"n" 3})))
  (<- (Delay 30))
  (<- done-a (as-writer harness MAKER (AppendEvent "pairs" "done:a" {"n" 4})))
  (<- (Delay (+ (- PAIR-KEEP-SECONDS 30) 1)))
  ;; 積んでから保持の秒を過ぎた: 組 a は後の出来事(done:a)が残るので ask:a も残る。組 b と solo は消えた。
  (<- read (as-writer harness MAKER (ReadEvents "pairs")))
  (<- (require-law (and (isinstance read Events) (= (lfor e read.items e.idempotency-key) ["ask:a" "done:a"])) law
               (.format "組の後の出来事が残る間に前の出来事が消えた・組の無い出来事が残る: {!r}" read)))
  (<- again-a (as-writer harness MAKER (AppendEvent "pairs" "ask:a" {"n" 1})))
  (<- (require-law (= again-a ask-a) law (.format "組が残る間の再送が前の番号でない: {!r} {!r}" again-a ask-a)))
  (<- fresh-b (as-writer harness MAKER (AppendEvent "pairs" "ask:b" {"n" 2})))
  (<- (require-law (and (isinstance fresh-b Appended) (> fresh-b.sequence done-a.sequence)) law
               (.format "消えた組の再送が新しい出来事でない: {!r}" fresh-b)))
  (<- (Delay 30))
  ;; 組 a の最後の出来事(done:a)から保持の秒を過ぎた: 組ごと消えた。
  (<- later (as-writer harness MAKER (ReadEvents "pairs")))
  (<- (require-law (and (isinstance later Events) (= (lfor e later.items e.idempotency-key) ["ask:b"])) law
               (.format "組の最後の出来事から保持の秒を過ぎた組が残る: {!r}" later)))
  (<- fresh-a (as-writer harness MAKER (AppendEvent "pairs" "ask:a" {"n" 1})))
  (<- (require-law (and (isinstance fresh-a Appended) (> fresh-a.sequence fresh-b.sequence)) law
               (.format "消えた組の再送が新しい出来事でない: {!r}" fresh-a)))
  [ask-a ask-b solo done-a read again-a fresh-b later fresh-a])


;; --- 法 13: 列の末尾の番号は 1 回の読みで答え、空の列は空と答える ----------------------------------------------------

(defk law-stream-end-is-the-last-sequence [harness]
  {:pre [(: harness LawHarness)] :post [(: % (get list object))]
   :tags {:context "records" :role "program"}}
  "列の末尾の法: ReadStreamEnd は列の最後の出来事の番号を StreamEnd で答え(別の列に後から積んだ出来事は数えない・同じ冪等キーの再送は
   末尾を動かさない)、出来事が 1 つも無い列は StreamEmpty で答える。保持で刈った後は残る出来事の最後の番号・全部刈れば StreamEmpty。
   使い手が列の末尾を ReadEvents の倍々の先読みと二分で探さずに 1 回で読むため。"
  (val law "ReadStreamEnd は列の最後の出来事の番号を答え、空の列は StreamEmpty")
  (<- empty (as-writer harness MAKER (ReadStreamEnd "journal")))
  (<- (require-law (= empty (StreamEmpty)) law (.format "空の列: {!r}" empty)))
  (<- first (as-writer harness MAKER (AppendEvent "journal" "end-1" {"n" 1})))
  (<- second (as-writer harness MAKER (AppendEvent "journal" "end-2" {"n" 2})))
  (<- other (as-writer harness MAKER (AppendEvent "pairs" "end-other" {"n" 3})))
  (<- tail (as-writer harness MAKER (ReadStreamEnd "journal")))
  (<- (require-law (= tail (StreamEnd second.sequence)) law
               (.format "積んだ列の末尾(別の列の後の出来事 {} を数えない): {!r}" other.sequence tail)))
  (<- replay (as-writer harness MAKER (AppendEvent "journal" "end-1" {"n" 1})))
  (<- replayed (as-writer harness MAKER (ReadStreamEnd "journal")))
  (<- (require-law (and (= replay first) (= replayed tail)) law (.format "再送が末尾を動かした: {!r} {!r}" replay replayed)))
  (<- other-end (as-writer harness MAKER (ReadStreamEnd "pairs")))
  (<- (require-law (= other-end (StreamEnd other.sequence)) law (.format "別の列の末尾: {!r}" other-end)))
  ;; 保持(pairs は積んでから PAIR-KEEP-SECONDS 秒で消える — 区切りを含まないキーは出来事ごと)。
  (<- (Delay 30))
  (<- young (as-writer harness MAKER (AppendEvent "pairs" "end-young" {"n" 4})))
  (<- (Delay (+ (- PAIR-KEEP-SECONDS 30) 1)))
  (<- partial (as-writer harness MAKER (ReadStreamEnd "pairs")))
  (<- (require-law (= partial (StreamEnd young.sequence)) law (.format "一部を刈った後の末尾: {!r}" partial)))
  (<- (Delay 30))
  (<- gone (as-writer harness MAKER (ReadStreamEnd "pairs")))
  (<- (require-law (= gone (StreamEmpty)) law (.format "全部を刈った列: {!r}" gone)))
  (<- kept (as-writer harness MAKER (ReadStreamEnd "journal")))
  (<- (require-law (= kept tail) law (.format "保持の無い列の末尾が時間で変わった: {!r}" kept)))
  [empty first second other tail replay replayed other-end young partial gone kept])


;; 全部の法(名 → 法)。SHARED-LAWS = 時間を進めない法(仮想の時計を持たない組でも回せる・答えの比べに使う)。
;; law-put-rows-is-all-or-nothing は SHARED-LAWS に入れない — SHARED-LAWS は前からの 6 つの effect だけで回る法の名簿で、
;; PutRows を答えない handler の組(呼び手の系の写しの handler など)もこの名簿で答えを比べている。
(setv LAWS {"stale-put-conflicts" law-stale-put-conflicts
            "committed-changes-appear-once-in-order" law-committed-changes-appear-once-in-order
            "epoch-change-resets" law-epoch-change-resets
            "undeclared-writes-are-refused" law-undeclared-writes-are-refused
            "transient-rows-expire" law-transient-rows-expire
            "indexed-list-equals-filtered-scan" law-indexed-list-equals-filtered-scan
            "append-is-idempotent" law-append-is-idempotent
            "watch-waits-for-a-change" law-watch-waits-for-a-change
            "watch-events-waits-for-an-append" law-watch-events-waits-for-an-append
            "none-removes-a-field" law-none-removes-a-field
            "maintenance-prunes-and-sweeps" law-maintenance-prunes-and-sweeps
            "put-rows-is-all-or-nothing" law-put-rows-is-all-or-nothing
            "grouped-events-expire-together" law-grouped-events-expire-together
            "stream-end-is-the-last-sequence" law-stream-end-is-the-last-sequence})
(setv SHARED-LAWS #("stale-put-conflicts" "committed-changes-appear-once-in-order" "epoch-change-resets"
                    "undeclared-writes-are-refused" "indexed-list-equals-filtered-scan" "append-is-idempotent" "none-removes-a-field"))
