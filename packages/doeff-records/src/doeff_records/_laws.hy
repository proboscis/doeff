;;; 記録の handler の適合の筋書き(公開)— どの handler の組も同じ法を満たすことを、同じ Program で確かめる。
;;;
;;; 筋書きは公開 effect と検の口(faults.AdvanceStoreEpoch)だけを撃ち、法が破れたら LawBroken を上げ、撃った effect の答えを
;;; 順に並べた transcript(list)を返す。2 つの handler の組で同じ筋書きを回し、transcript が等しいこと(= 答えが同じ)も確かめられる。
;;;
;;; 組み立ては呼び手: LAW-SCHEMA の宣言で置き場を作り、LawHarness の as-writer(書き手の名・Program → その書き手の handler で包んだ
;;; Program)を渡す。書き手の名は宣言の欄の書き手の名(maker / painter / closer)と、どこにも載らない stranger。
;;; 保持の法(law-transient-rows-expire)は doeff-time の Delay で時間を進めるので、仮想の時計(sim-time-handler)の下で回す。
;;; 待ちの法(law-watch-waits-for-a-change・law-watch-events-waits-for-an-append)は doeff の scheduler の Spawn を使う。
;;; 手入れの法(law-maintenance-prunes-and-sweeps)と保持の読みの法(law-expired-records-are-unseen-before-a-sweep)は手入れの effect
;;; (maintenance.SweepExpired / PruneChanges — 公開 effect ではない)も撃つ。
(require doeff-hy.macros [defk <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "records" :role "program"})
(import dataclasses [dataclass])
(import datetime [datetime])
(import itertools)
(import collections.abc [Callable])
(import doeff [EffectBase Program])
(import doeff_hy._frozen [FrozenMap])
(import doeff_core_effects [Spawn Wait])
(import doeff_time [Delay GetTime])
(import doeff_records._values [FieldDecl TableDecl DescendingWithin StreamDecl RecordsSchema KeepFor KeepForever ByKeySuffix ByKeyHead ExpectAbsent ExpectVersion ExpectAny
                              WatchCursor ListCursor Row Missing Page Written WrittenRows Conflict Refused NotIndexed Reset
                              Changes RowChanged RowRemoved Appended AppendedEvents Events EventsMoved EventsQuiet RowsConflict RowsRefused
                              StreamEnd StreamEmpty StreamTail StreamTailEmpty Event EventAbsent EventRetired Unreachable
                              RememberKeysFor RememberNoKeys WRITE-KEY-MEMORY-SECONDS BatchRead])
(import doeff_records._effects [ReadRow ListRows PutRow PutRows RowWrite WatchChanges WatchEvents AppendEvent AppendEvents EventAppend
                               ReadEvents ReadStreamEnd ReadEventByKey ReadBatch])
(import doeff_records._faults [AdvanceStoreEpoch])
(import doeff_records.maintenance [SweepExpired PruneChanges ReadChangeLog ChangeLog Swept Pruned ReadExpiredEvents ExpiredEvents
                                   RetireArchivedEvents RetiredEvents EventsArchived archive-expired-events])
(import functools [partial])
(import doeff_records.admission [row-matches? epoch-ms key-text])

;; OVERSEER = 宣言の operators に入る書き手(宣言の形の例 — 置き場は書き手の名では断らない・#2994)。
(setv MAKER "maker" PAINTER "painter" CLOSER "closer" STRANGER "stranger" OVERSEER "overseer")
(setv TICKET-KEEP-SECONDS 60 PAIR-KEEP-SECONDS 60)
;; pulses = 出来事ごとに数える期限つきの列(法 15 — 組で数える列 pairs と並べて、出来事ごとの境も読みが見ることを確かめる)。
(val PULSE-KEEP-SECONDS 60)
;; relays・relay-pairs = 送ってから外す列(法 19〜21 — maintenance.hy の頭の註の「出来事を送ってから外す」)。
(val RELAY-KEEP-SECONDS 60)
;; beats・ticks・relay-beats・relay-ticks = 鍵の覚え方を限った列(法 22・23 — maintenance.hy の頭の註の「鍵の覚えを外す」)。
;; 出来事の保持の秒と、長さを限った覚えの秒(RememberKeysFor)。
(val BEAT-KEEP-SECONDS 60)
(val BEAT-MEMORY-SECONDS 30)

(setv LAW-SCHEMA
  (RecordsSchema
    :tables (FrozenMap {"parts" (TableDecl :name "parts" :key-fields #("id")
                                :fields #((FieldDecl "id" #(MAKER)) (FieldDecl "label" #(MAKER))
                                          (FieldDecl "color" #(MAKER PAINTER)) (FieldDecl "state" #(MAKER CLOSER))
                                          (FieldDecl "note" #(MAKER)) (FieldDecl "grant" #(MAKER OVERSEER)))
                                :indexes #("color" "label")
                                ;; 上から読む欄(法の上から読む読みが並べる欄 — PostgreSQL の置き場は並べる式の索引で辿る)。
                                :descending #("note")
                                ;; 等号の欄 × 上から読む欄の組(color で絞って note の大きい順 — PostgreSQL の置き場は組の索引で辿る)。
                                :descending-within #((DescendingWithin "color" "note"))
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
    ;; pairs = 保持の組(ByKeySuffix「:」)の列 — 法 12。pulses = 出来事ごとに数える期限つきの列 — 法 15。
    ;; trails = 冪等キーの頭(ByKeyHead「#」)で引ける列 — 法 7b。
    :streams (FrozenMap {"journal" (StreamDecl :name "journal" :writers #(MAKER) :size-budget 200)
                         "pairs" (StreamDecl :name "pairs" :writers #(MAKER) :retention (KeepFor PAIR-KEEP-SECONDS)
                                             :retention-group (ByKeySuffix ":"))
                         "pulses" (StreamDecl :name "pulses" :writers #(MAKER) :retention (KeepFor PULSE-KEEP-SECONDS))
                         "trails" (StreamDecl :name "trails" :writers #(MAKER) :key-lookup (ByKeyHead "#"))
                         ;; relays・relay-pairs = 送ってから外す列(出来事ごと・組ごと)— 法 19〜21。
                         "relays" (StreamDecl :name "relays" :writers #(MAKER) :retention (KeepFor RELAY-KEEP-SECONDS)
                                              :archive-before-retire True)
                         "relay-pairs" (StreamDecl :name "relay-pairs" :writers #(MAKER) :retention (KeepFor RELAY-KEEP-SECONDS)
                                                   :retention-group (ByKeySuffix ":") :archive-before-retire True)
                         ;; beats・ticks = 覚えを長さで限る列・覚えを残さない列 — 法 22。relay-beats・relay-ticks = その送ってから外す列 — 法 23。
                         "beats" (StreamDecl :name "beats" :writers #(MAKER) :retention (KeepFor BEAT-KEEP-SECONDS)
                                             :key-memory (RememberKeysFor BEAT-MEMORY-SECONDS))
                         "ticks" (StreamDecl :name "ticks" :writers #(MAKER) :retention (KeepFor BEAT-KEEP-SECONDS)
                                             :key-memory (RememberNoKeys))
                         "tick-pairs" (StreamDecl :name "tick-pairs" :writers #(MAKER) :retention (KeepFor BEAT-KEEP-SECONDS)
                                                  :retention-group (ByKeySuffix ":") :key-memory (RememberNoKeys))
                         "relay-beats" (StreamDecl :name "relay-beats" :writers #(MAKER) :retention (KeepFor BEAT-KEEP-SECONDS)
                                                   :archive-before-retire True :key-memory (RememberKeysFor BEAT-MEMORY-SECONDS))
                         "relay-ticks" (StreamDecl :name "relay-ticks" :writers #(MAKER) :retention (KeepFor BEAT-KEEP-SECONDS)
                                                   :archive-before-retire True :key-memory (RememberNoKeys))})
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


(defk replay-of [again first]
  {:pre [(: again (| Appended Refused Unreachable)) (: first (| Appended Refused Unreachable))] :post [(: % bool)]}
  "同じ冪等キーの再送の答え again が、1 回目の答え first と同じ番号で、再送と名乗っている(1 回目は今積んだと名乗る)かを判じるため。"
  (and (isinstance again Appended) (isinstance first Appended) (= again.sequence first.sequence)
       (is again.replayed True) (is first.replayed False)))


(defk unplaced [answer]
  {:pre [(: answer (| Written Conflict Refused Unreachable WrittenRows RowsConflict RowsRefused))]
   :post [(: % (| Written Conflict Refused Unreachable WrittenRows RowsConflict RowsRefused))]}
  "書きの答え answer から変更の位置(Written.position)を外した値を返すため — 版と値だけを比べる法の等しさの検めが、置き場の版と番号で
   決まる位置に縛られないため(位置は法 law-a-write-names-its-change-position が見る)。書きの答えでない値はそのまま返す。"
  (match answer
    (Written) (Written answer.version answer.value)
    (WrittenRows) (WrittenRows (tuple (gfor item answer.items (Written item.version item.value))))
    _ answer))


(defk unplaced-all [answers]
  {:pre [(: answers list)] :post [(: % list)]}
  "法の transcript の答えを並べたまま、書きの答えから位置と送り直しの名乗りを外すため(unplaced を 1 つずつ — 答えの比べが位置と
   名乗りに縛られない。名乗りは法が自分で見る)。"
  (var plain [])
  (for [answer answers]
    (if (isinstance answer #(Written WrittenRows))
        (do (<- one (unplaced answer))
            (:= plain (+ plain [one])))
        (:= plain (+ plain [answer]))))
  plain)


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
  (<- (require-law (= (! (unplaced born)) (Written 1 (FrozenMap {"id" "p1" "label" "a" "state" "open"}))) law (.format "生まれる行: {!r}" born)))
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
  ;; 消えた行の「消えた」の変更は回収(SweepExpired — 手入れの係が撃つ)が積む(読みは回収しない — #3561・書きは自分が触る行だけを片付ける
  ;; — #3605 の D)。
  (<- swept (as-writer harness MAKER (SweepExpired)))
  (<- (require-law (= swept (Swept 1)) law (.format "期限を過ぎた行 1 つを回収しない: {!r}" swept)))
  (<- removed (as-writer harness MAKER (WatchChanges #("tickets") (WatchCursor start.epoch start.sequence))))
  (<- (require-law (and (isinstance removed Changes) (= (lfor item removed.items #((type item) item.key)) [#(RowRemoved #("g1" "t1"))]))
               law (.format "消えた行が回収の後の変更に 1 回だけ出ない: {!r}" removed)))
  (<- (Delay (* 100 TICKET-KEEP-SECONDS)))
  (<- kept (as-writer harness MAKER (ReadRow "parts" #("p1"))))
  (<- (require-law (and (isinstance kept Row) (= (get kept.value "state") "closed")) law (.format "record の行が消えた: {!r}" kept)))
  (<- open-kept (as-writer harness MAKER (ReadRow "tickets" #("g1" "t2"))))
  (<- (require-law (isinstance open-kept Row) law (.format "終端でない transient の行が消えた: {!r}" open-kept)))
  [t1 t2 p1 done start before gone listed swept removed kept open-kept])


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


;; --- 法 6b: 上から読む ListRows は全件を読んで並べた上から limit 件と同じ ----------------------------------

;; 欄 note の値(行の番号 → 値): 整数(同じ値の行を含む)と、並べない値(文字列・小数・真偽・欄の無い行)。
(setv TOP-NOTES {1 30 2 "30" 3 10 4 30 5 2.5 6 20 7 True 8 None 9 -5 10 20 11 40 12 7})
(setv TOP-WHERES (lfor where [{} {"color" "red"} {"label" "b"} {"color" "none"}] (FrozenMap where)))

(defk law-descending-list-is-the-head-of-the-sorted-scan [harness]
  {:pre [(: harness LawHarness)] :post [(: % (get list object))]
   :tags {:context "records" :role "program"}}
  "上から読む ListRows の法: descending-by を付けた読みは、where に合う行を全件読み、欄の値が整数の行だけを (値, 鍵) の大きい順に並べた
   上から limit 件と同じ行を、同じ順で 1 頁に返し、続き(next-cursor)は並べた行が limit 件を越える時だけ返す。使い手が最新の何件かを、表の
   行の数でなく limit に比例する読み 1 回で得るため。どの置き場の handler も同じ答えを返すことを確かめる(続きを辿る頁は法 6b2)。"
  (val law "上から読む ListRows は全件を読んで並べた上から limit 件と同じ")
  (var transcript [])
  (for [#(n note) (.items TOP-NOTES)]
    (val value (| {"color" (get COLORS (% n 3)) "label" (get LABELS (% n 2))} (if (is note None) {} {"note" note})))
    (<- written (as-writer harness MAKER (PutRow "parts" #((.format "p{:02d}" n)) (FrozenMap value) (ExpectAbsent))))
    (:= transcript (+ transcript [written])))
  (for [where TOP-WHERES]
    (<- pages (collect-pages harness "parts" where 500))
    ;; 並べる行 = 欄 note の値が JSON の整数(真偽は整数に数えない)の行 — 法は置き場の判じを借りずに書く。
    (val scanned (lfor page pages row page.rows :setv noted (.get row.value "note")
                       :if (and (isinstance noted int) (not (isinstance noted bool))) row))
    (val ordered (sorted scanned :key (fn [row] #((get row.value "note") row.key)) :reverse True))
    (for [limit [1 3 50]]
      (<- top (as-writer harness MAKER (ListRows "parts" :where where :descending-by "note" :limit limit)))
      (<- (require-law (and (isinstance top Page) (= (is top.next-cursor None) (<= (len ordered) limit))) law
                       (.format "{!r} の上から {} 件の答えが頁でない・続きの有無が並べた行の数({} 件)と合わない: {!r}" where limit (len ordered)
                                top)))
      (<- (require-law (= top.rows (tuple (cut ordered limit))) law
                       (.format "{!r} の上から {} 件が全件を並べた上の {} 件と違う: {!r} / {!r}" where limit limit top.rows
                                (cut ordered limit))))
      (:= transcript (+ transcript [top]))))
  (<- narrow (as-writer harness MAKER (ListRows "parts" :where (FrozenMap {"color" "red"}) :fields #("color") :descending-by "note" :limit 2)))
  (<- (require-law (and (= (len narrow.rows) 2) (all (gfor row narrow.rows (= (set row.value) #{"id" "color"})))) law
                   (.format "上から読む読みの欄の絞り: {!r}" narrow)))
  (<- loose (as-writer harness MAKER (ListRows "parts" :where (FrozenMap {"note" 30}) :descending-by "note")))
  (<- (require-law (= loose (NotIndexed #("note"))) law (.format "上から読む読みの索引の無い欄: {!r}" loose)))
  (+ transcript [narrow loose]))


;; --- 法 6b2: 上から読む ListRows の頁を続きの位置で辿ると全件を並べた物と同じ ----------------------------------

;; 上から読む読みの頁を辿る数の上限(法の行は 20 行に満たず、1 件ずつの頁でも届かない)。
(val DESCENDING-PAGE-CAP 64)

(defk sorted-descending-scan [harness where]
  {:pre [(: harness LawHarness) (: where FrozenMap)] :post [(: % tuple)]
   :tags {:context "records" :role "program"}}
  "上から読む読みが辿るはずの行を、置き場の判じを借りずに鍵の順の読みだけで組むため: where に合う行を全件読み、欄 note の値が JSON の
   整数(真偽は数えない)の行を (値, 鍵の綴り) の大きい順に並べる。答え = #(行の tuple 読んだ頁の list)。"
  (<- pages (collect-pages harness "parts" where 500))
  (val scanned (lfor page pages row page.rows :setv noted (.get row.value "note")
                     :if (and (isinstance noted int) (not (isinstance noted bool))) row))
  #((tuple (sorted scanned :key (fn [row] #((get row.value "note") (key-text row.key))) :reverse True)) pages))


(defk collect-descending-pages [harness where fields cursor limit]
  {:pre [(: harness LawHarness) (: where FrozenMap) (: fields (| tuple None)) (: cursor (| ListCursor None)) (: limit int)]
   :post [(: % (get list object))]
   :tags {:context "records" :role "program"}}
  "上から読む ListRows(欄 note の大きい順)の頁を、位置 cursor(None = 上から)から next-cursor が尽きるまで読み、頁を全部並べるため。
   DESCENDING-PAGE-CAP 頁で止める(位置を読み捨てて上から読み直す handler でも法が終わり、重なりで赤になる)。"
  (var pages [])
  (var at cursor)
  (while True
    (<- page (as-writer harness MAKER (ListRows "parts" :where where :fields fields :cursor at :limit limit :descending-by "note")))
    (:= pages (+ pages [page]))
    (when (or (not (isinstance page Page)) (is page.next-cursor None) (>= (len pages) DESCENDING-PAGE-CAP)) (return pages))
    (:= at page.next-cursor)))


(defk law-descending-pages-follow-the-sorted-scan [harness]
  {:pre [(: harness LawHarness)] :post [(: % (get list object))]
   :tags {:context "records" :role "program"}}
  "上から読む ListRows の続きの法: descending-by の読みの next-cursor を辿った頁を連ねると、where に合う行の欄の値が整数の行を (値, 鍵) の
   大きい順に並べた全件と同じ(頁は上限を越えず、重なりも欠けも無く、最後の頁だけが続きを返さない — 返す欄 fields が並べる欄を外しても)。
   頁の間に書かれた行は、位置の (値, 鍵) より小さければ後の頁に出て、大きければ出ない(位置は前の頁の最後の行の値と鍵 — keyset)。
   古い置き場の版の位置は Reset。使い手が鍵の順でなく新しい順に古い頁を送る(鍵が時刻の順でない表でも)ため。どの置き場の handler も
   同じ答えを返すことを確かめる。"
  (val law "上から読む ListRows の頁を続きの位置で辿ると全件を並べた物と同じ")
  (var transcript [])
  (for [#(n note) (.items TOP-NOTES)]
    (val value (| {"color" (get COLORS (% n 3)) "label" (get LABELS (% n 2))} (if (is note None) {} {"note" note})))
    (<- written (as-writer harness MAKER (PutRow "parts" #((.format "p{:02d}" n)) (FrozenMap value) (ExpectAbsent))))
    (:= transcript (+ transcript [written])))
  (for [where TOP-WHERES]
    (<- expected (sorted-descending-scan harness where))
    (val ordered (get expected 0))
    (:= transcript (+ transcript (get expected 1)))
    (for [#(limit fields) [#(1 None) #(2 None) #(3 #("color")) #(50 None)]]
      (<- pages (collect-descending-pages harness where fields None limit))
      (<- (require-law (all (gfor page pages (isinstance page Page))) law (.format "{!r} の {} 件ずつの頁: {!r}" where limit pages)))
      (<- (require-law (all (gfor page pages (<= (len page.rows) limit))) law (.format "{!r} の頁が上限 {} を越えた: {!r}" where limit pages)))
      (<- (require-law (all (gfor page (cut pages -1) (is-not page.next-cursor None))) law
                       (.format "{!r} の {} 件ずつの頁の途中で続きが切れた: {!r}" where limit pages)))
      (val paged (tuple (gfor page pages row page.rows row.key)))
      (<- (require-law (= paged (tuple (gfor row ordered row.key))) law
                       (.format "{!r} の {} 件ずつ辿った頁が全件を並べた物と違う(重なりか欠け): {!r} / {!r}" where limit paged
                                (tuple (gfor row ordered row.key)))))
      (<- (require-law (or (is fields None) (all (gfor page pages row page.rows (= (set row.value) (| #{"id"} (set fields)))))) law
                       (.format "{!r} の欄の絞りの頁: {!r}" where pages)))
      (:= transcript (+ transcript pages))))
  ;; 頁の間の書き: 上から 3 件の頁の後に、位置より大きい組の行(p13 = 100)と小さい組の行(p14 = -100)を書き、位置の値と同じ値の行の鍵の
  ;; 前後(p00 < 位置の鍵 < p99)も足す。続きは位置より小さい組の行だけを読む。
  (<- first (as-writer harness MAKER (ListRows "parts" :descending-by "note" :limit 3)))
  (<- (require-law (and (isinstance first Page) (is-not first.next-cursor None)) law (.format "上から 3 件の頁に続きが無い: {!r}" first)))
  (val at first.next-cursor)
  (for [#(key note) [#("p13" 100) #("p14" -100) #("p00" at.after-value) #("p99" at.after-value)]]
    (<- written (as-writer harness MAKER (PutRow "parts" #(key) (FrozenMap {"note" note}) (ExpectAbsent))))
    (:= transcript (+ transcript [written])))
  (<- after (sorted-descending-scan harness (FrozenMap)))
  (val below (tuple (gfor row (get after 0) :if (< #((get row.value "note") (key-text row.key)) #(at.after-value at.after-key)) row.key)))
  (<- rest (collect-descending-pages harness (FrozenMap) None at 2))
  (val rest-keys (tuple (gfor page rest :if (isinstance page Page) row page.rows row.key)))
  (<- (require-law (and (= rest-keys below) (in #("p14") rest-keys) (in #("p00") rest-keys) (not-in #("p13") rest-keys) (not-in #("p99") rest-keys))
                   law (.format "頁の間に書いた行の続きが位置より小さい組の行と違う: {!r} / {!r}" rest-keys below)))
  ;; 置き場の版が変われば、上から読む読みの位置も Reset。
  (<- epoch (as-writer harness MAKER (AdvanceStoreEpoch)))
  (<- stale (as-writer harness MAKER (ListRows "parts" :descending-by "note" :cursor at :limit 3)))
  (<- (require-law (and (isinstance stale Reset) (= stale.epoch epoch)) law (.format "古い版の上から読む読みの位置: {!r}" stale)))
  (+ transcript [first] (get after 1) rest [epoch stale]))


;; --- 法 6c: 値の組の ListRows は値ごとの等号の ListRows の和を鍵の順に並べた物と同じ ----------------------

;; 表 parts の行 p01 … p12 の値(鍵の欄は書く時に足す)。color は文字列・整数と同じ数の小数・真偽(1 と等しくない)を持ち、color の無い行
;; (p08)と label の無い行(p12)を含む。note は上から読む読みが並べる整数。
(val ANY-OF-ROWS (tuple (gfor value [{"color" "red" "label" "a" "note" 5} {"color" "blue" "label" "b" "note" 9}
                                     {"color" "green" "label" "a" "note" 1} {"color" "red" "label" "b" "note" 9}
                                     {"color" 1 "label" "a" "note" 3} {"color" 1.0 "label" "b" "note" 7}
                                     {"color" True "label" "a" "note" 2} {"label" "b" "note" 8}
                                     {"color" "blue" "label" "a" "note" 4} {"color" "red" "label" "b" "note" 6}
                                     {"color" "green" "label" "b" "note" 10} {"color" "blue" "note" 0}]
                              (FrozenMap value))))
;; #(where where-any): どの行にも無い値・同じ値の重ね・同じ数の整数と小数・真偽・2 つの欄の組(欄どうしは AND)・鍵の欄・where との AND。
(val ANY-OF-CASES
  (lfor #(where any-of) [#({} {"color" ["red" "blue"]})
                         #({} {"color" ["red" "none-such"]})
                         #({} {"color" [1]})
                         #({} {"color" [1.0 True]})
                         #({} {"color" ["red" "red"]})
                         #({"label" "a"} {"color" ["red" "green"]})
                         #({} {"color" ["red" "blue"] "label" ["b"]})
                         #({} {"id" ["p02" "p05" "p99"]})
                         ;; 鍵の欄の絞り(PG の置き場は主キーの列で引く): 文字列でない値が混ざる組・where の等号・他の欄との AND。
                         #({} {"id" ["p02" 5 "p05"]})
                         #({"id" "p02"} {"color" ["red" "blue"]})]
        #((FrozenMap where) (FrozenMap any-of))))
;; 組の絞りの頁の上限(頁の続きの位置を辿る)と、上から読む読みの件数。
(val ANY-OF-PAGE 2)
(val ANY-OF-TOP 3)


(defk collect-any-pages [harness table where where-any limit]
  {:pre [(: harness LawHarness) (: table str) (: where FrozenMap) (: where-any FrozenMap) (: limit int)] :post [(: % (get list object))]
   :tags {:context "records" :role "program"}}
  "next-cursor が尽きるまで、値の組で絞る ListRows(where-any)の頁を読み、頁を全部並べるため。"
  (var pages [])
  (var cursor None)
  (while True
    (<- page (as-writer harness MAKER (ListRows table :where where :where-any where-any :cursor cursor :limit limit)))
    (:= pages (+ pages [page]))
    (when (or (not (isinstance page Page)) (is page.next-cursor None)) (return pages))
    (:= cursor page.next-cursor)))


(defk union-of-equal-lists [harness table where where-any]
  {:pre [(: harness LawHarness) (: table str) (: where FrozenMap) (: where-any FrozenMap)] :post [(: % tuple)]
   :tags {:context "records" :role "program"}}
  "値の組の読みが返すはずの行を、置き場の判じを借りずに等号の読みだけで組むため: 組の欄ごとの値の積(欄どうしは AND)の 1 つずつを where に
   足した等号の ListRows の全件を読み、和(同じ鍵の行は 1 つ)を鍵の綴りの順に並べる。答え = #(行の tuple 読んだ頁の list)。"
  (val names (sorted where-any))
  (var found {})
  (var pages [])
  (for [values (itertools.product #* (gfor name names (get where-any name)))]
    (<- equal (collect-pages harness table (FrozenMap (| (dict where) (dict (zip names values :strict True)))) 500))
    (:= pages (+ pages equal))
    (for [page equal :if (isinstance page Page) row page.rows]
      (:= found (| found {(key-text row.key) row}))))
  #((tuple (gfor text (sorted found) (get found text))) pages))


(defk law-any-of-list-equals-the-union-of-equal-lists [harness]
  {:pre [(: harness LawHarness)] :post [(: % (get list object))]
   :tags {:context "records" :role "program"}}
  "値の組の ListRows(where-any)の法: 頁を辿った全件は、組の値ごとの等号の ListRows の全件の和を鍵の綴りの順に並べた物と同じ(頁は上限を
   越えず、続きの位置で欠けも重なりも無い)。上から読む読み(descending-by)を重ねた答えは、その和の欄の値が整数の行を (値, 鍵) の大きい順に
   並べた上から limit 件。索引でない欄の組は NotIndexed(where と同じく欄の名の順)。使い手が値の組 1 つを、値ごとの往復でなく 1 回の
   読みで引くため。どの置き場の handler も同じ答えを返すことを確かめる。"
  (val law "値の組の ListRows は値ごとの等号の ListRows の和を鍵の順に並べた物と同じ")
  (var transcript [])
  (for [#(n value) (enumerate ANY-OF-ROWS 1)]
    (<- written (as-writer harness MAKER (PutRow "parts" #((.format "p{:02d}" n)) value (ExpectAbsent))))
    (<- (require-law (isinstance written Written) law (.format "p{:02d} の書き: {!r}" n written)))
    (:= transcript (+ transcript [written])))
  (for [#(where where-any) ANY-OF-CASES]
    (<- expected (union-of-equal-lists harness "parts" where where-any))
    (val union (get expected 0))
    (val equal-pages (get expected 1))
    (<- pages (collect-any-pages harness "parts" where where-any ANY-OF-PAGE))
    (<- (require-law (all (gfor page pages (isinstance page Page))) law (.format "{!r} {!r} の頁: {!r}" where where-any pages)))
    (<- (require-law (all (gfor page pages (<= (len page.rows) ANY-OF-PAGE))) law
                     (.format "{!r} {!r} の頁が上限を越えた: {!r}" where where-any pages)))
    (val paged (tuple (gfor page pages row page.rows row)))
    (<- (require-law (= paged union) law
                     (.format "{!r} {!r} の組の答えが値ごとの等号の答えの和と違う: {!r} / {!r}" where where-any paged union)))
    (val ordered (sorted (gfor row union :setv noted (.get row.value "note")
                               :if (and (isinstance noted int) (not (isinstance noted bool))) row)
                         :key (fn [row] #((get row.value "note") (key-text row.key))) :reverse True))
    (<- top (as-writer harness MAKER (ListRows "parts" :where where :where-any where-any :descending-by "note" :limit ANY-OF-TOP)))
    (<- (require-law (and (isinstance top Page) (= (is top.next-cursor None) (<= (len ordered) ANY-OF-TOP))
                          (= top.rows (tuple (cut ordered ANY-OF-TOP))))
                     law
                     (.format "{!r} {!r} の上から {} 件が和を並べた上の {} 件と違う: {!r} / {!r}" where where-any ANY-OF-TOP ANY-OF-TOP
                              top (cut ordered ANY-OF-TOP))))
    (:= transcript (+ transcript equal-pages pages [top])))
  (<- narrow (as-writer harness MAKER (ListRows "parts" :where-any (FrozenMap {"color" ["red" "blue"]}) :fields #("color") :limit 50)))
  (<- (require-law (and (isinstance narrow Page) (= (len narrow.rows) 6) (all (gfor row narrow.rows (= (set row.value) #{"id" "color"}))))
                   law (.format "組の読みの欄の絞り: {!r}" narrow)))
  (<- loose (as-writer harness MAKER (ListRows "parts" :where-any (FrozenMap {"note" [5 9]}))))
  (<- (require-law (= loose (NotIndexed #("note"))) law (.format "組の読みの索引の無い欄: {!r}" loose)))
  (<- both-loose (as-writer harness MAKER (ListRows "parts" :where (FrozenMap {"note" 5}) :where-any (FrozenMap {"grant" ["yes"]}))))
  (<- (require-law (= both-loose (NotIndexed #("grant" "note"))) law (.format "where と組の索引の無い欄: {!r}" both-loose)))
  (+ transcript [narrow loose both-loose]))


;; --- 法 7: 追記の冪等キー ----------------------------------------------------------------------------------

(defk law-append-is-idempotent [#^ LawHarness harness]
  {:pre [(: harness LawHarness)] :post [(: % (get list object))]}
  (setv law "同じ冪等キーの再送は前の番号を返して再送と名乗り(replayed)、別の本文は Refused")
  (<- a1 (as-writer harness MAKER (AppendEvent "journal" "k1" {"n" 1})))
  (<- a2 (as-writer harness MAKER (AppendEvent "journal" "k2" {"n" 2})))
  (<- again (as-writer harness MAKER (AppendEvent "journal" "k1" {"n" 1})))
  ;; 積んだ答えと再送の答えは replayed で分かれる — 書き手は「今積んだ」時だけ知らせを出せる(#3850)。
  (<- (require-law (and (isinstance a1 Appended) (isinstance a2 Appended) (isinstance again Appended)
                        (= again.sequence a1.sequence) (> a2.sequence a1.sequence)
                        (is a1.replayed False) (is a2.replayed False) (is again.replayed True))
                   law (.format "番号と再送の名乗り: {!r} {!r} {!r}" a1 a2 again)))
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


;; --- 法 7c: 追記の束は束の順の追記と同じ答え(card ki-48c34994ed15)----------------------------------------------

(defk law-append-events-answer-like-appends-in-order [harness]
  {:pre [(: harness LawHarness)] :post [(: % (get list object))]
   :tags {:context "records" :role "program"}}
  "追記の束(AppendEvents)の法: 束の答えは、束の順に AppendEvent を 1 つずつ撃った答えと同じ — 追記ごとに冪等キーの再送は前の番号
   (replayed)・別の本文と上限は Refused で、断られた追記は束の他の追記を止めない。今積んだ追記の番号は束の順に増え、束の前の出来事より
   大きい。列をまたぐ束も 1 つで積み、読みは列ごとに束の順の出来事を返す。同じ束の再送は追記ごとに再送と名乗る。1 つの要求で原因と本体の
   出来事を積む書き手が、2 回の追記を 1 回の束に替えても答えが変わらないため。どの置き場の組も同じ答えを返すことを確かめる。"
  (val law "追記の束は束の順に AppendEvent を撃った答えと同じ(断りは他の追記を止めない・番号は束の順)")
  (<- seed (as-writer harness MAKER (AppendEvent "journal" "b1" {"n" 1})))
  (val appends #((EventAppend "pairs" "ask:b" {"n" 2})
                 (EventAppend "journal" "b1" {"n" 1})
                 (EventAppend "journal" "b-big" {"n" (* "x" 300)})
                 (EventAppend "journal" "b2" {"n" 3})))
  (<- batch (as-writer harness MAKER (AppendEvents appends)))
  (<- (require-law (and (isinstance batch AppendedEvents) (= (len batch.items) (len appends))) law (.format "束の答え: {!r}" batch)))
  (val ask (get batch.items 0))
  (val fresh (get batch.items 3))
  (<- (require-law (and (isinstance seed Appended) (isinstance ask Appended) (is ask.replayed False) (> ask.sequence seed.sequence)
                        (! (replay-of (get batch.items 1) seed)) (isinstance (get batch.items 2) Refused)
                        (isinstance fresh Appended) (is fresh.replayed False) (> fresh.sequence ask.sequence))
                   law (.format "追記ごとの答えと番号の順: {!r}(種 {!r})" batch seed)))
  (<- again (as-writer harness MAKER (AppendEvents appends)))
  (<- (require-law (and (isinstance again AppendedEvents) (= (len again.items) (len appends))) law (.format "同じ束の再送の答え: {!r}" again)))
  (<- (require-law (and (! (replay-of (get again.items 0) ask)) (! (replay-of (get again.items 1) seed))
                        (isinstance (get again.items 2) Refused) (! (replay-of (get again.items 3) fresh)))
                   law (.format "同じ束の再送は追記ごとに再送と名乗る: {!r}" again)))
  (<- mixed (as-writer harness MAKER (AppendEvents #((EventAppend "journal" "b2" {"n" 9}) (EventAppend "journal" "b3" {"n" 4})))))
  (<- (require-law (and (isinstance mixed AppendedEvents) (= (len mixed.items) 2) (isinstance (get mixed.items 0) Refused)
                        (isinstance (get mixed.items 1) Appended) (is (. (get mixed.items 1) replayed) False)
                        (> (. (get mixed.items 1) sequence) fresh.sequence))
                   law (.format "前の追記の断りは後の追記を止めない: {!r}" mixed)))
  (<- journal (as-writer harness MAKER (ReadEvents "journal")))
  (<- (require-law (= (lfor e journal.items #(e.idempotency-key e.body e.writer))
                      [#("b1" {"n" 1} MAKER) #("b2" {"n" 3} MAKER) #("b3" {"n" 4} MAKER)])
                   law (.format "列 journal の出来事: {!r}" journal)))
  (<- pairs (as-writer harness MAKER (ReadEvents "pairs")))
  (<- (require-law (= (lfor e pairs.items #(e.idempotency-key e.sequence)) [#("ask:b" ask.sequence)])
                   law (.format "列 pairs の出来事: {!r}" pairs)))
  [seed batch again mixed journal pairs])


;; --- 法 7a2: 読みの束は読みを 1 つずつ撃った答えと同じ ----------------------------------------------------

(defk law-read-batch-answers-like-each-read [harness]
  {:pre [(: harness LawHarness)] :post [(: % (get list object))]
   :tags {:context "records" :role "program"}}
  "読みの束(ReadBatch — card ki-177a4f287c42)の法: 束の答えは、束の読みを 1 つずつ撃った答えを束の順に並べた物と同じ — 表をまたぐ鍵の
   読み(在る行・無い行)・値の組の一覧(where-any)・頁の続きの位置・索引の無い欄の断り(NotIndexed)・版の違う位置の読み直し(Reset)が
   混ざっても、読みごとの答えは変わらない。複数の表から名指す鍵と値の組の行を読む読み手が、読みの数の往復を束 1 つの往復に替えても
   答えが変わらないため。どの置き場の組も同じ答えを返すことを確かめる。"
  (val law "読みの束は束の読みを 1 つずつ撃った答えと同じ")
  (var transcript [])
  (for [n (range 1 7)]
    (<- written (as-writer harness MAKER (PutRow "parts" #((.format "p{:02d}" n))
                                                 (FrozenMap {"color" (get COLORS (% n 3)) "label" (get LABELS (% n 2))}) (ExpectAbsent))))
    (:= transcript (+ transcript [written])))
  (for [#(group id owner) [#("g1" "t1" "o1") #("g1" "t2" "o2") #("g2" "t3" "o1") #("g2" "t4" "o3")]]
    (<- written (as-writer harness MAKER (PutRow "tickets" #(group id) (FrozenMap {"owner" owner}) (ExpectAbsent))))
    (:= transcript (+ transcript [written])))
  (<- first-page (as-writer harness MAKER (ListRows "parts" :limit 2)))
  (<- (require-law (and (isinstance first-page Page) (is-not first-page.next-cursor None)) law (.format "頁の続き: {!r}" first-page)))
  (val reads #((ReadRow "parts" #("p02"))
               (ListRows "tickets" :where-any (FrozenMap {"owner" ["o1" "o3"]}))
               (ReadRow "tickets" #("g1" "t2"))
               (ReadRow "parts" #("p-none"))
               (ListRows "parts" :where-any (FrozenMap {"color" ["red" "blue"]}) :fields #("color"))
               (ListRows "parts" :cursor first-page.next-cursor :limit 2)
               (ListRows "parts" :where-any (FrozenMap {"note" ["x"]}))
               (ListRows "parts" :cursor (ListCursor (+ first-page.epoch 1) "p01"))
               (ListRows "tickets" :where (FrozenMap {"owner" "o2"}))))
  (var singles [])
  (for [one reads]
    (<- single (as-writer harness MAKER one))
    (:= singles (+ singles [single])))
  (<- batch (as-writer harness MAKER (ReadBatch reads)))
  (<- (require-law (and (isinstance batch BatchRead) (= (len batch.items) (len reads))) law (.format "束の答え: {!r}" batch)))
  (<- (require-law (= (list batch.items) singles) law (.format "束の答えと 1 つずつの答えが違う: {!r} / {!r}" batch.items singles)))
  (<- (require-law (and (isinstance (get singles 0) Row) (isinstance (get singles 2) Row) (= (get singles 3) (Missing))
                        (= (len (. (get singles 1) rows)) 3) (isinstance (get singles 6) NotIndexed) (isinstance (get singles 7) Reset))
                   law (.format "読みごとの答えの種: {!r}" singles)))
  (+ transcript [first-page] singles [batch]))


;; --- 法 7b: 冪等キーの頭で引く ReadEvents は全部の出来事を読んで頭で絞った列と同じ ------------------------------

;; 列 trails に積む冪等キー(積む順): 頭 a・b の出来事を混ぜ、頭が a で始まる別の頭 ab・区切りを含まないキー(キー全体が頭)・
;; 区切りで終わるキー(頭 a)を含む。
(val TRAIL-KEYS #("a#1#1" "b#1#1" "ab#1" "a#1#2" "b#1#2" "a" "b" "a#2#1" "ab" "b#2#1" "a#" "a#2#2"))
(val TRAIL-HEADS #("a" "b" "ab" "zz"))

(defk law-key-head-read-equals-filtered-scan [harness]
  {:pre [(: harness LawHarness)] :post [(: % (get list object))]
   :tags {:context "records" :role "program"}}
  "冪等キーの頭で引く ReadEvents の法: key-head を付けた読みは、列の全部の出来事を読み、冪等キーの最初の区切りより前(区切りを含まない
   キーはキー全体)が key-head と等しい出来事だけに絞った列の、after より後の上から limit 件と同じ出来事を返し、次の after は最後の
   出来事の番号(無ければ after のまま)。使い手が 1 つの頭の出来事を、列に混ざる他の頭の出来事の数でなく、その頭の出来事の数に比例する
   読みで得るため。key-lookup を宣言していない列への頭の読みは NotIndexed。どの置き場の handler も同じ答えを返すことを確かめる。"
  (val law "冪等キーの頭で引く ReadEvents は全部の出来事を読んで頭で絞った列と同じ")
  (var transcript [])
  (for [key TRAIL-KEYS]
    (<- appended (as-writer harness MAKER (AppendEvent "trails" key {"key" key})))
    (:= transcript (+ transcript [appended])))
  (<- whole (as-writer harness MAKER (ReadEvents "trails" :limit 500)))
  (<- (require-law (and (isinstance whole Events) (= (len whole.items) (len TRAIL-KEYS))) law
                   (.format "列の全部の出来事: {!r}" whole)))
  (val middle (. (get whole.items 4) sequence))
  (for [head TRAIL-HEADS]
    ;; 頭 = 冪等キーの最初の「#」より前 — 法は置き場の判じを借りずに書く。
    (val mine (lfor event whole.items :if (= (get (.split event.idempotency-key "#" 1) 0) head) event))
    (for [after [0 middle]]
      (val later (lfor event mine :if (> event.sequence after) event))
      (for [limit [1 2 50]]
        (<- read (as-writer harness MAKER (ReadEvents "trails" :after after :limit limit :key-head head)))
        (val expected (tuple (cut later limit)))
        (<- (require-law (and (isinstance read Events) (= read.items expected)
                              (= read.last-sequence (if expected (. (get expected -1) sequence) after)))
                         law (.format "頭 {!r} の after {} の上から {} 件が全部を絞った列と違う: {!r} / {!r}" head after limit read expected)))
        (:= transcript (+ transcript [read])))))
  (<- loose (as-writer harness MAKER (ReadEvents "journal" :key-head "a")))
  (<- (require-law (= loose (NotIndexed #("idempotencyKey"))) law (.format "頭で引けない列の頭の読み: {!r}" loose)))
  (+ transcript [whole loose]))


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
  (<- (require-law (= idle (Changes #() cursor #())) law (.format "変更の無い待ち: {!r}" idle)))
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


;; --- 法 8c: 並んで走る書きの番号は、読み手に積んだ順に見える(card ki-0306f5a614b4)---------------------------------

;; 並んで走る書き手の数と 1 本ごとの書きの数・読み手が書きの間に読む回数(PostgreSQL の置き場では書き手の transaction が DB の中で並ぶ)。
(val CONCURRENT-WRITERS 3)
(val CONCURRENT-ROUNDS 5)
(val FOLLOW-READS 12)
(val FOLLOW-LIMIT 1000)

(defk concurrent-appender [harness n]
  {:pre [(: harness LawHarness) (: n int)] :post [(: % None)]
   :tags {:context "records" :role "program"}}
  "法 8c の追記の書き手 n: 列 journal と trails へ交互に積むため(2 つの列の追記は番号の bigserial を分け合い、列ごとの錠で並んで走る)。"
  (for [round (range CONCURRENT-ROUNDS)]
    (val stream (if (% round 2) "trails" "journal"))
    (<- (as-writer harness MAKER (AppendEvent stream (.format "w{}#{}" n round) {"n" n "round" round})))
    (<- (Delay 0.001)))
  None)

(defk concurrent-row-writer [harness n]
  {:pre [(: harness LawHarness) (: n int)] :post [(: % None)]
   :tags {:context "records" :role "program"}}
  "法 8c の行の書き手 n: 表 parts と tickets へ交互に書くため(2 つの表の書きは変更の列の番号を分け合う — 行の単位の錠 1 つで並ぶ)。"
  (for [round (range CONCURRENT-ROUNDS)]
    (if (% round 2)
        (<- (as-writer harness MAKER (PutRow "tickets" #("c" (.format "w{}-{}" n round)) (FrozenMap {"owner" (str n)}) (ExpectAny))))
        (<- (as-writer harness MAKER (PutRow "parts" #((.format "w{}-{}" n round)) (FrozenMap {"label" (str round)}) (ExpectAny)))))
    (<- (Delay 0.001)))
  None)

(defrecord FollowedEvents
  "法 8c の列の追いの 1 回: sequences = 読んだ出来事の番号・after = 次に読む位置。"
  {:tags {:context "records" :role "type"}}
  (#^ (get tuple #(int ...)) sequences)
  (#^ int after))

(defrecord Followed
  "法 8c の読み手が書きの間に追った物: events = 読んだ列の番号・after = 列の位置・changes = 読んだ変更の番号・at = 変更の位置。"
  {:tags {:context "records" :role "type"}}
  (#^ (get tuple #(int ...)) events)
  (#^ int after)
  (#^ (get tuple #(int ...)) changes)
  (#^ WatchCursor at))

(defk followed-events [harness after]
  {:pre [(: harness LawHarness) (: after int)] :post [(: % FollowedEvents)]
   :tags {:context "records" :role "program"}}
  "列 journal の after より後の出来事を読み、読んだ番号と次の位置を返すため(読み手が位置を進めながら追う 1 回)。"
  (<- page (as-writer harness MAKER (ReadEvents "journal" :after after :limit FOLLOW-LIMIT)))
  (val sequences (tuple (gfor event page.items event.sequence)))
  (FollowedEvents :sequences sequences :after (if sequences (get sequences -1) after)))

(defk concurrent-follower [harness cursor]
  {:pre [(: harness LawHarness) (: cursor WatchCursor)] :post [(: % Followed)]
   :tags {:context "records" :role "program"}}
  "法 8c の読み手: 書き手が走る間に、列 journal(位置 0 から)と表 parts・tickets の変更(位置 cursor から)を、位置を進めながら FOLLOW-READS
   回追うため。"
  (var events #())
  (var after 0)
  (var changes #())
  (var at cursor)
  (for [_ (range FOLLOW-READS)]
    (<- followed (followed-events harness after))
    (:= events (+ events followed.sequences))
    (:= after followed.after)
    (<- moved (as-writer harness MAKER (WatchChanges #("parts" "tickets") at :limit FOLLOW-LIMIT)))
    (when (isinstance moved Changes)
      (:= changes (+ changes (tuple (gfor item moved.items item.sequence))))
      (:= at moved.cursor))
    (<- (Delay 0.002)))
  (Followed :events events :after after :changes changes :at at))

(defk law-concurrent-writes-appear-in-number-order [harness]
  {:pre [(: harness LawHarness)] :post [(: % (get list object))]
   :tags {:context "records" :role "program"}}
  "番号の読みの約束の法(pg_sql.hy の頭の註の「書きの錠」): 並んで走る追記(2 つの列)と行の書き(2 つの表)の間に、位置を進めながら追う
   読み手は、列の出来事も表の変更も番号の順に 1 回ずつ見て、どれも取りこぼさない — 書きが終わった後の追いつきの読みと合わせた並びが、
   列の全部の読み・変更の全部の読みと同じ。PostgreSQL の置き場は書きの錠を列ごとと行の単位に分けており、番号(bigserial)を取ってから
   commit するまでを、同じ読みが 1 つの位置で読む範囲(列 1 つ・表の全部)ごとに 1 本にしている事を確かめる。どの置き場の組も同じ。"
  (val law "並んで走る書きの番号は、読み手に積んだ順に 1 回ずつ見える(取りこぼさない)")
  (<- start (as-writer harness MAKER (ListRows "parts" :limit 1)))
  (<- (require-law (isinstance start Page) law (.format "最初の一覧: {!r}" start)))
  (val cursor (WatchCursor start.epoch start.sequence))
  (var writers #())
  (for [n (range CONCURRENT-WRITERS)]
    (<- appender (Spawn (concurrent-appender harness n)))
    (<- row-writer (Spawn (concurrent-row-writer harness n)))
    (:= writers (+ writers #(appender row-writer))))
  (<- follower (Spawn (concurrent-follower harness cursor)))
  (for [task writers]
    (<- (Wait task)))
  (<- followed (Wait follower))
  (<- caught (followed-events harness followed.after))
  (<- tail (collect-changes harness #("parts" "tickets") followed.at FOLLOW-LIMIT))
  (val seen-events (+ followed.events caught.sequences))
  (val seen-changes (+ followed.changes (tuple (gfor answer (get tail 0) :if (isinstance answer Changes) item answer.items item.sequence))))
  (<- whole (as-writer harness MAKER (ReadEvents "journal" :limit FOLLOW-LIMIT)))
  (val all-events (tuple (gfor event whole.items event.sequence)))
  (<- every (collect-changes harness #("parts" "tickets") cursor FOLLOW-LIMIT))
  (val all-changes (tuple (gfor answer (get every 0) :if (isinstance answer Changes) item answer.items item.sequence)))
  (val expected (* CONCURRENT-WRITERS (// (+ CONCURRENT-ROUNDS 1) 2)))
  (<- (require-law (= (len all-events) expected) law (.format "列 journal の出来事の数: {} ≠ {}" (len all-events) expected)))
  (<- (require-law (= (len all-changes) (* CONCURRENT-WRITERS CONCURRENT-ROUNDS)) law
                   (.format "表の変更の数: {} ≠ {}" (len all-changes) (* CONCURRENT-WRITERS CONCURRENT-ROUNDS))))
  (<- (require-law (= seen-events all-events) law (.format "追った列の番号 {!r} と列の全部の番号 {!r} が違う" seen-events all-events)))
  (<- (require-law (= seen-changes all-changes) law
                   (.format "追った変更の番号 {!r} と変更の全部の番号 {!r} が違う" seen-changes all-changes)))
  [(len seen-events) (len seen-changes)])


;; --- 法 9: 差分の None はその欄を消す(JSON merge patch の null)------------------------------------------------

(defk law-none-removes-a-field [#^ LawHarness harness]
  {:pre [(: harness LawHarness)] :post [(: % (get list object))]}
  (setv law "PutRow の差分の値 None はその欄を消し、行の値は None を持たない")
  (<- born (as-writer harness MAKER (PutRow "parts" #("p1") (FrozenMap {"label" "a" "color" "red"}) (ExpectAbsent))))
  (<- (require-law (= (! (unplaced born)) (Written 1 (FrozenMap {"id" "p1" "label" "a" "color" "red" "state" "open"}))) law (.format "生まれる行: {!r}" born)))
  (<- uncolored (as-writer harness PAINTER (PutRow "parts" #("p1") (FrozenMap {"color" None}) (ExpectVersion 1))))
  (<- (require-law (= (! (unplaced uncolored)) (Written 2 (FrozenMap {"id" "p1" "label" "a" "state" "open"}))) law (.format "欄を消す書き: {!r}" uncolored)))
  (<- read (as-writer harness MAKER (ReadRow "parts" #("p1"))))
  (<- (require-law (= read (Row #("p1") (FrozenMap {"id" "p1" "label" "a" "state" "open"}) 2)) law (.format "消した欄が読める: {!r}" read)))
  (<- keyless (as-writer harness MAKER (PutRow "parts" #("p1") (FrozenMap {"id" None}) (ExpectVersion 2))))
  (<- (require-law (isinstance keyless Refused) law (.format "鍵の欄を消せた: {!r}" keyless)))
  (<- stateless (as-writer harness MAKER (PutRow "parts" #("p1") (FrozenMap {"state" None}) (ExpectVersion 2))))
  (<- (require-law (isinstance stateless Refused) law (.format "状態の欄を消せた: {!r}" stateless)))
  (<- nothing (as-writer harness MAKER (PutRow "parts" #("p1") (FrozenMap {"note" None}) (ExpectVersion 2))))
  (<- (require-law (= (! (unplaced nothing)) (Written 3 (FrozenMap {"id" "p1" "label" "a" "state" "open"}))) law (.format "無い欄を消す書き: {!r}" nothing)))
  (<- fresh (as-writer harness MAKER (PutRow "parts" #("p2") (FrozenMap {"label" "b" "color" None}) (ExpectAbsent))))
  (<- (require-law (= (! (unplaced fresh)) (Written 1 (FrozenMap {"id" "p2" "label" "b" "state" "open"}))) law (.format "None の欄を持って生まれる行: {!r}" fresh)))
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

;; 消す前に送る(maintenance.hy の頭の註・card ki-c32e43651f2e): 刈りの上限 upto-seq は送り先に在ると確かめた番号で、
;; それを越えて消せば送っていない変更が失われる。
(defk law-prune-stops-at-the-archived-seq [#^ LawHarness harness]
  {:pre [(: harness LawHarness)] :post [(: % (get list object))]}
  "刈りは送って確かめた番号(PruneChanges の upto-seq)を越えて変更を消さない — 上限の内の古い変更だけを消し、上限より後の古い変更は
   次の刈り(上限を上げた時)まで残るため。"
  (setv law "刈りは upto-seq を越えて消さない・上限より後の古い変更は残り読める・上限を外せば残りを刈る")
  (<- start (as-writer harness MAKER (ListRows "parts")))
  (<- w1 (as-writer harness MAKER (PutRow "parts" #("p1") (FrozenMap {"label" "a"}) (ExpectAbsent))))
  (<- first (as-writer harness MAKER (ListRows "parts")))
  (<- w2 (as-writer harness MAKER (PutRow "parts" #("p2") (FrozenMap {"label" "b"}) (ExpectAbsent))))
  (<- second (as-writer harness MAKER (ListRows "parts")))
  (<- (Delay 100))
  ;; 2 つの変更はどちらも 50 秒より古い。送って確かめた番号は 1 つ目まで。
  (<- bounded (as-writer harness MAKER (PruneChanges 50 :upto-seq first.sequence)))
  (<- (require-law (= bounded (Pruned first.sequence 1)) law (.format "上限の内の 1 つだけを刈る: {!r}" bounded)))
  (<- kept (as-writer harness MAKER (WatchChanges #("parts") (WatchCursor start.epoch first.sequence))))
  (<- (require-law (and (isinstance kept Changes) (= (lfor item kept.items #(item.key item.version)) [#(#("p2") 1)]))
               law (.format "上限より後の古い変更が残り読める: {!r}" kept)))
  (<- again (as-writer harness MAKER (PruneChanges 50 :upto-seq first.sequence)))
  (<- (require-law (= again (Pruned first.sequence 0)) law (.format "同じ上限の 2 度目の刈りは何も消さない: {!r}" again)))
  (<- rest (as-writer harness MAKER (PruneChanges 50 :upto-seq second.sequence)))
  (<- (require-law (= rest (Pruned second.sequence 1)) law (.format "上限を上げれば残りを刈る: {!r}" rest)))
  [w1 w2 bounded kept again rest])


;; 送りの読みは絞らない(maintenance.hy の頭の註・card ki-e4ca6fc87109): 刈りは表を問わず番号で消すので、送りの読みが表や保持の期限で
;; 絞れば、絞られた変更は送られずに刈られる。
(defk law-change-log-reads-every-change [#^ LawHarness harness]
  {:pre [(: harness LawHarness)] :post [(: % (get list object))]}
  "変更の列の生の読み(ReadChangeLog)が、位置より後の変更を表と保持の期限で絞らずに番号の順に答え、limit で切り、先端の後は空で、
   置き場の版を添えるため — 期限を過ぎた終端の行の変わり(WatchChanges が出さない)も答える。"
  (setv law "生の読みは位置より後の変更を表と期限で絞らずに番号の順に答え・limit で切り・先端の後は空・版を添える")
  (<- start (as-writer harness MAKER (ListRows "parts")))
  (<- w1 (as-writer harness MAKER (PutRow "parts" #("p1") (FrozenMap {"label" "a"}) (ExpectAbsent))))
  (<- t1 (as-writer harness MAKER (PutRow "tickets" #("g1" "t1") (FrozenMap {"owner" "o1"}) (ExpectAbsent))))
  (<- done (as-writer harness MAKER (PutRow "tickets" #("g1" "t1") (FrozenMap {"state" "done"}) (ExpectVersion 1))))
  ;; tickets の終端の行は期限を過ぎる(回収はまだ — WatchChanges はこの行の変わりを出さない)。
  (<- (Delay (+ TICKET-KEEP-SECONDS 1)))
  (<- log (as-writer harness MAKER (ReadChangeLog start.sequence 10)))
  (setv seen (if (isinstance log ChangeLog) (lfor change log.changes #(change.table change.key)) []))
  (setv sequences (if (isinstance log ChangeLog) (lfor change log.changes change.sequence) []))
  (<- (require-law (and (= seen [#("parts" #("p1")) #("tickets" #("g1" "t1")) #("tickets" #("g1" "t1"))])
                        (= sequences (sorted (set sequences))) (> (min sequences :default 0) start.sequence)
                        (= log.epoch start.epoch))
               law (.format "位置より後の 3 つの変更を番号の順に・版を添えて: {!r}" log)))
  (<- head (as-writer harness MAKER (ReadChangeLog start.sequence 2)))
  (<- (require-law (and (isinstance head ChangeLog) (= (lfor change head.changes change.sequence) (cut sequences 2)))
               law (.format "limit 2 は頭の 2 つ: {!r}" head)))
  (<- tail (as-writer harness MAKER (ReadChangeLog (get sequences -1) 10)))
  (<- (require-law (and (isinstance tail ChangeLog) (= tail.changes #())) law (.format "先端の後は空: {!r}" tail)))
  [w1 t1 done log head tail])


(defk law-put-rows-is-all-or-nothing [harness]
  {:pre [(: harness LawHarness)] :post [(: % (get list object))]}
  "複数行の書き(PutRows)が 1 transaction の約束を守ることを、どの置き場の組でも同じに確かめるための法:
   全部通る束は束の順の Written を返し、期待のずれ 1 行・書きの断り 1 行の束は 1 行も書かない・期待のずれは断りより先に答える・
   確定した束の変更は束の順に続いた番号で 1 回ずつ見え、通らなかった束の変更は見えない。"
  (val law "PutRows は全部か 0 で書き、確定した束の変更は束の順に続いた番号で見える")
  (<- start (as-writer harness MAKER (ListRows "parts" :limit 1)))
  (<- (require-law (isinstance start Page) law (.format "最初の一覧: {!r}" start)))
  (<- seed (as-writer harness MAKER (PutRow "parts" #("p1") (FrozenMap {"label" "a"}) (ExpectAbsent))))
  (<- (require-law (= (! (unplaced seed)) (Written 1 (FrozenMap {"id" "p1" "label" "a" "state" "open"}))) law (.format "種の行: {!r}" seed)))
  (<- batch (as-writer harness MAKER
              (PutRows #((RowWrite "parts" #("p1") (FrozenMap {"label" "b"}) (ExpectVersion 1))
                         (RowWrite "parts" #("p2") (FrozenMap {"label" "c"}) (ExpectAbsent))
                         (RowWrite "tickets" #("g1" "t1") (FrozenMap {"owner" "o1"}) (ExpectAbsent))))))
  (<- (require-law (= (! (unplaced batch)) (WrittenRows #((Written 2 (FrozenMap {"id" "p1" "label" "b" "state" "open"}))
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
   組ごと消える。区切りを含まないキーと、後の出来事の無い組は、出来事ごとに消える。消えた組の鍵の再送は法 14 が見る(鍵は忘れない — #3022)。"
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
  (<- (require-law (! (replay-of again-a ask-a)) law (.format "組が残る間の再送が前の番号でない: {!r} {!r}" again-a ask-a)))
  (<- fresh-c (as-writer harness MAKER (AppendEvent "pairs" "ask:c" {"n" 5})))
  (<- (require-law (and (isinstance fresh-c Appended) (> fresh-c.sequence done-a.sequence)) law
               (.format "新しい組の出来事が新しい出来事でない: {!r}" fresh-c)))
  (<- (Delay 30))
  ;; 組 a の最後の出来事(done:a)から保持の秒を過ぎた: 組ごと消えた。後から積んだ組 c は残る。
  (<- later (as-writer harness MAKER (ReadEvents "pairs")))
  (<- (require-law (and (isinstance later Events) (= (lfor e later.items e.idempotency-key) ["ask:c"])) law
               (.format "組の最後の出来事から保持の秒を過ぎた組が残る: {!r}" later)))
  [ask-a ask-b solo done-a read again-a fresh-c later])


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
  (<- (require-law (and (! (replay-of replay first)) (= replayed tail)) law (.format "再送が末尾を動かした: {!r} {!r}" replay replayed)))
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


;; --- 法 14: 保持の期限で出来事を消しても冪等キーは忘れない ------------------------------------------------------------------

(defk law-expired-keys-are-remembered [harness]
  {:pre [(: harness LawHarness)] :post [(: % (get list object))]
   :tags {:context "records" :role "program"}}
  "保持の期限で消した出来事の冪等キーの法(#3022): 追記の列の「同じ冪等キーは 1 回だけ」は出来事を消した後も続く — 消した鍵の再送は、
   同じ本文なら前の番号を返して列の出来事を増やさず、別の本文なら断る(生きた出来事と同じ規則)。出来事ごとに数える鍵(区切りを含まない)
   と組で数える鍵の両方で、どの置き場の handler も同じ答えを返すことを確かめるため。初めての鍵は今までどおり新しい出来事になる。"
  (val law "保持の期限で出来事を消しても冪等キーは忘れない(同じ本文は前の番号・別の本文は断る)")
  (<- solo (as-writer harness MAKER (AppendEvent "pairs" "keep-solo" {"n" 1})))
  (<- ask (as-writer harness MAKER (AppendEvent "pairs" "ask:keep" {"n" 2})))
  (<- done (as-writer harness MAKER (AppendEvent "pairs" "done:keep" {"n" 3})))
  (<- (Delay (+ PAIR-KEEP-SECONDS 1)))
  (<- gone (as-writer harness MAKER (ReadEvents "pairs")))
  (<- (require-law (and (isinstance gone Events) (= gone.items #())) law (.format "保持の期限を過ぎた出来事が残る: {!r}" gone)))
  ;; 別の本文の再送は断る(断りの文は生きた鍵と同じく「冪等キー」を含む)。
  (<- other-solo (as-writer harness MAKER (AppendEvent "pairs" "keep-solo" {"n" 9})))
  (<- other-done (as-writer harness MAKER (AppendEvent "pairs" "done:keep" {"n" 9})))
  (<- (require-law (all (gfor answer [other-solo other-done] (and (isinstance answer Refused) (in "冪等キー" answer.reason)))) law
               (.format "消した鍵の別の本文が断られない: {!r} {!r}" other-solo other-done)))
  ;; 同じ本文の再送は前の番号(新しい出来事にならない)。
  (<- again-solo (as-writer harness MAKER (AppendEvent "pairs" "keep-solo" {"n" 1})))
  (<- again-ask (as-writer harness MAKER (AppendEvent "pairs" "ask:keep" {"n" 2})))
  (<- (require-law (and (! (replay-of again-solo solo)) (! (replay-of again-ask ask))) law
               (.format "消した鍵の同じ本文の再送が前の番号でない: {!r} {!r} / {!r} {!r}" again-solo solo again-ask ask)))
  (<- after (as-writer harness MAKER (ReadEvents "pairs")))
  (<- end (as-writer harness MAKER (ReadStreamEnd "pairs")))
  (<- (require-law (and (isinstance after Events) (= after.items #()) (= end (StreamEmpty))) law
               (.format "消した鍵の再送で列の出来事が増えた: {!r} {!r}" after end)))
  ;; 初めての鍵は新しい出来事(消した鍵の覚えは他の鍵を断らない)。
  (<- fresh (as-writer harness MAKER (AppendEvent "pairs" "keep-fresh" {"n" 1})))
  (<- (require-law (and (isinstance fresh Appended) (> fresh.sequence done.sequence)) law
               (.format "初めての鍵が新しい出来事でない: {!r}" fresh)))
  [solo ask done gone other-solo other-done again-solo again-ask after end fresh])


;; --- 法 15: 保持の期限を過ぎた行と出来事は、回収の前でも読みに出ない ---------------------------------------------------------

(defk law-expired-records-are-unseen-before-a-sweep [harness]
  {:pre [(: harness LawHarness)] :post [(: % (get list object))]
   :tags {:context "records" :role "program"}}
  "保持の期限の読みの法(#3561): 期限を過ぎた終端の行と出来事は、回収(SweepExpired)が走る前でも、どの読み(ReadRow・ListRows・
   WatchChanges・ReadEvents・WatchEvents・ReadStreamEnd)にも出ない — 期限の判定は読みが自分で持ち、回収は掃除だけ。行の今の値が期限を
   過ぎた終端の行である行の変わりは WatchChanges に出ず、その行の「消えた」は回収が 1 回だけ積む — 別の表への書きは積まない(書きは自分が
   触る行だけを片付ける・#3605 の D — 触る行は法 16)。出来事は出来事ごとに数える列(pulses)と組で数える列(pairs)の両方で
   見る。どの置き場の handler も同じ答えを返すことを確かめるため。"
  (val law "保持の期限を過ぎた行と出来事は回収の前でも読みに出ず、消えた は別の表への書きでなく回収が 1 回だけ積む")
  (<- start (as-writer harness MAKER (ListRows "tickets")))
  (val cursor (WatchCursor start.epoch start.sequence))
  (<- born (as-writer harness MAKER (PutRow "tickets" #("g5" "u1") (FrozenMap {"owner" "o1"}) (ExpectAbsent))))
  (<- open (as-writer harness MAKER (PutRow "tickets" #("g5" "u2") (FrozenMap {"owner" "o2"}) (ExpectAbsent))))
  (<- closed (as-writer harness MAKER (PutRow "tickets" #("g5" "u1") (FrozenMap {"state" "done"}) (ExpectVersion 1))))
  (<- pulse (as-writer harness MAKER (AppendEvent "pulses" "pulse-1" {"n" 1})))
  (<- ask (as-writer harness MAKER (AppendEvent "pairs" "ask:unseen" {"n" 2})))
  (<- (Delay (- TICKET-KEEP-SECONDS 1)))
  ;; 境の 1 秒前: 終端の行はまだ見える。
  (<- before (as-writer harness MAKER (ReadRow "tickets" #("g5" "u1"))))
  (<- (require-law (and (isinstance before Row) (= before.version 2)) law (.format "期限の前に行が見えない: {!r}" before)))
  (<- (Delay 2))
  ;; 境を過ぎた: 書きも SweepExpired も撃たずに、全部の読みで見る。
  (<- row (as-writer harness MAKER (ReadRow "tickets" #("g5" "u1"))))
  (<- (require-law (= row (Missing)) law (.format "回収の前の ReadRow に期限を過ぎた行が出た: {!r}" row)))
  (<- listed (as-writer harness MAKER (ListRows "tickets")))
  (<- (require-law (and (isinstance listed Page) (= (lfor r listed.rows r.key) [#("g5" "u2")])) law
               (.format "回収の前の ListRows に期限を過ぎた行が出た・終端でない行が消えた: {!r}" listed)))
  (<- watched (as-writer harness MAKER (WatchChanges #("tickets") cursor :timeout 0.0)))
  (<- (require-law (and (isinstance watched Changes) (= (lfor item watched.items #(item.key item.version)) [#(#("g5" "u2") 1)]))
               law (.format "回収の前の WatchChanges に期限を過ぎた行の変わりが出た: {!r}" watched)))
  (<- pulses (as-writer harness MAKER (ReadEvents "pulses")))
  (<- pairs (as-writer harness MAKER (ReadEvents "pairs")))
  (<- (require-law (and (isinstance pulses Events) (= pulses.items #()) (isinstance pairs Events) (= pairs.items #())) law
               (.format "回収の前の ReadEvents に期限を過ぎた出来事が出た: {!r} {!r}" pulses pairs)))
  (<- pulse-moved (as-writer harness MAKER (WatchEvents "pulses" :after 0 :timeout 0.0)))
  (<- pair-moved (as-writer harness MAKER (WatchEvents "pairs" :after 0 :timeout 0.0)))
  (<- (require-law (and (= pulse-moved (EventsQuiet)) (= pair-moved (EventsQuiet))) law
               (.format "回収の前の WatchEvents が期限を過ぎた出来事で動いた: {!r} {!r}" pulse-moved pair-moved)))
  (<- pulse-end (as-writer harness MAKER (ReadStreamEnd "pulses")))
  (<- pair-end (as-writer harness MAKER (ReadStreamEnd "pairs")))
  (<- (require-law (and (= pulse-end (StreamEmpty)) (= pair-end (StreamEmpty))) law
               (.format "回収の前の ReadStreamEnd が期限を過ぎた出来事を数えた: {!r} {!r}" pulse-end pair-end)))
  ;; 別の表への書きは期限を過ぎた行を消さない(変更の列に 消えた が出ない)。回収(SweepExpired)が行を消し、消えた を 1 回だけ積む。
  (<- nudge (as-writer harness MAKER (PutRow "parts" #("p-nudge") (FrozenMap {"label" "n"}) (ExpectAbsent))))
  (<- quiet (as-writer harness MAKER (WatchChanges #("tickets") watched.cursor :timeout 0.0)))
  (<- (require-law (and (isinstance quiet Changes) (= quiet.items #())) law
               (.format "別の表への書きが期限を過ぎた行の消えた を積んだ: {!r}" quiet)))
  (<- swept (as-writer harness MAKER (SweepExpired)))
  (<- (require-law (= swept (Swept 1)) law (.format "回収が期限を過ぎた行 1 つを消さない: {!r}" swept)))
  (<- removed (as-writer harness MAKER (WatchChanges #("tickets") watched.cursor :timeout 0.0)))
  (<- (require-law (and (isinstance removed Changes) (= (lfor item removed.items #((type item) item.key)) [#(RowRemoved #("g5" "u1"))]))
               law (.format "回収の後の変更に期限を過ぎた行の消えた が 1 回だけ出ない: {!r}" removed)))
  [start born open closed pulse ask before row listed watched pulses pairs pulse-moved pair-moved pulse-end pair-end nudge quiet swept
   removed])


;; --- 法 16: 期限を過ぎた行への書きは、その行を消してから無い行として判じる ------------------------------------------------

(defk law-a-write-clears-the-expired-row-it-touches [harness]
  {:pre [(: harness LawHarness)] :post [(: % (get list object))]
   :tags {:context "records" :role "program"}}
  "書きが触る期限を過ぎた行の法(#3605 の D): 期限を過ぎて回収(SweepExpired)されていない終端の行へ書く PutRow・PutRows は、
   その行を消して変更の列に「消えた」を積んでから無い行として判じる — 回収の後の書きと同じ答え(ExpectAbsent は版 1 で生まれ、消えた行の版を
   期待する ExpectVersion は Conflict(Missing)で、行は消えたまま)。行ごとの変更の列は 消えた → 新しい版 の順。書きの前に置き場の全部を
   回収していた形(#3605 の前)と同じ答えを、どの置き場の handler も返すことを確かめるため。"
  (val law "期限を過ぎた行への書きは、その行を消して 消えた を積んでから無い行として判じる")
  (var closed #())
  (for [name #("u1" "u2" "u3")]
    (<- born (as-writer harness MAKER (PutRow "tickets" #("g7" name) (FrozenMap {"owner" "o1"}) (ExpectAbsent))))
    (<- done (as-writer harness MAKER (PutRow "tickets" #("g7" name) (FrozenMap {"state" "done"}) (ExpectVersion born.version))))
    (:= closed (+ closed #(born done))))
  (<- mark (as-writer harness MAKER (ListRows "tickets" :limit 1)))
  (<- (Delay (+ TICKET-KEEP-SECONDS 1)))
  ;; ExpectAbsent の書き: 無い行として版 1 で生まれる。
  (<- reborn (as-writer harness MAKER (PutRow "tickets" #("g7" "u1") (FrozenMap {"owner" "o2"}) (ExpectAbsent))))
  (<- (require-law (= (! (unplaced reborn)) (Written 1 (FrozenMap {"group" "g7" "id" "u1" "owner" "o2" "state" "open"}))) law
               (.format "期限を過ぎた行への ExpectAbsent の書き: {!r}" reborn)))
  ;; 消えた行の版(2)を期待する書き: Conflict(Missing)で、行は戻らない。
  (<- stale (as-writer harness MAKER (PutRow "tickets" #("g7" "u2") (FrozenMap {"owner" "o3"}) (ExpectVersion 2))))
  (<- (require-law (= stale (Conflict (Missing))) law (.format "期限を過ぎた行の版を期待する書き: {!r}" stale)))
  (<- gone (as-writer harness MAKER (ReadRow "tickets" #("g7" "u2"))))
  (<- (require-law (= gone (Missing)) law (.format "Conflict の後に期限を過ぎた行が読めた: {!r}" gone)))
  ;; PutRows の束の行も同じ。
  (<- batch (as-writer harness MAKER (PutRows #((RowWrite "tickets" #("g7" "u3") (FrozenMap {"owner" "o4"}) (ExpectAbsent))))))
  (<- (require-law (= (! (unplaced batch)) (WrittenRows #((Written 1 (FrozenMap {"group" "g7" "id" "u3" "owner" "o4" "state" "open"}))))) law
               (.format "期限を過ぎた行への束の書き: {!r}" batch)))
  (<- collected (collect-changes harness #("tickets") (WatchCursor mark.epoch mark.sequence) 100))
  (val items (tuple (gfor answer (get collected 0) :if (isinstance answer Changes) item answer.items item)))
  (val per-key (dfor name #("u1" "u2" "u3")
                     name (lfor item items :if (= item.key #("g7" name))
                                #((. (type item) __name__) (if (isinstance item RowChanged) item.version None)))))
  (<- (require-law (= per-key {"u1" [#("RowRemoved" None) #("RowChanged" 1)] "u2" [#("RowRemoved" None)]
                               "u3" [#("RowRemoved" None) #("RowChanged" 1)]})
               law (.format "行ごとの変更の列が 消えた → 新しい版 の順でない: {!r}" items)))
  (+ (list closed) [mark reborn stale gone batch] (get collected 0)))


;; --- 法 17: 期限を過ぎた鍵への追記は、回収の前でも後でも同じ答え -------------------------------------------------------

(defk expired-key-answers [harness]
  {:pre [(: harness LawHarness)] :post [(: % tuple)]
   :tags {:context "records" :role "program"}}
  "法 17 の追記 4 つ(出来事ごとに数える列 pulses と組で数える列 pairs の期限を過ぎた鍵へ、前と同じ本文と別の本文)を撃ち、答えを並べるため。"
  (<- beat (as-writer harness MAKER (AppendEvent "pulses" "beat-17" {"n" 1})))
  (<- beat-other (as-writer harness MAKER (AppendEvent "pulses" "beat-17" {"n" 9})))
  (<- ask (as-writer harness MAKER (AppendEvent "pairs" "ask:t17" {"n" 2})))
  (<- ask-other (as-writer harness MAKER (AppendEvent "pairs" "ask:t17" {"n" 9})))
  #(beat beat-other ask ask-other))


(defk law-an-expired-key-answers-the-same-before-and-after-a-sweep [harness]
  {:pre [(: harness LawHarness)] :post [(: % (get list object))]
   :tags {:context "records" :role "program"}}
  "書きが触る期限を過ぎた出来事の法(#3605 の D): 保持の期限を過ぎて回収(SweepExpired)されていない出来事の冪等キーへの
   追記は、回収の後の追記と同じ答え — 同じ本文は前の番号・別の本文は同じ文の断り(出来事ごとに数える列 pulses と組で数える列 pairs の両方)。
   期限を過ぎた組に新しい鍵を積んでも、組の古い出来事は読みに戻らない(新しい出来事が組の最後の刻を延ばしても、前の出来事は回収の後と同じく
   消えたまま・鍵は覚えている)。書きの前に置き場の全部を回収していた形(#3605 の前)と同じ答えを、どの置き場の handler も返すことを確かめるため。"
  (val law "期限を過ぎた鍵への追記は回収の前でも後でも同じ答えで、期限を過ぎた組の古い出来事は新しい鍵の追記で読みに戻らない")
  (<- beat (as-writer harness MAKER (AppendEvent "pulses" "beat-17" {"n" 1})))
  (<- ask (as-writer harness MAKER (AppendEvent "pairs" "ask:t17" {"n" 2})))
  (<- side (as-writer harness MAKER (AppendEvent "pairs" "ask:u17" {"n" 3})))
  (<- (Delay (+ (max PULSE-KEEP-SECONDS PAIR-KEEP-SECONDS) 1)))
  ;; 回収の前(期限を過ぎた出来事が置き場に残る)の答え。
  (<- before (expired-key-answers harness))
  (<- (require-law (and (! (replay-of (get before 0) beat)) (! (replay-of (get before 2) ask))) law
               (.format "期限を過ぎた鍵の同じ本文の再送が前の番号でない: {!r} {!r} {!r}" before beat ask)))
  (<- (require-law (all (gfor answer #((get before 1) (get before 3)) (and (isinstance answer Refused) (in "冪等キー" answer.reason))))
               law (.format "期限を過ぎた鍵の別の本文が断られない: {!r}" before)))
  ;; 期限を過ぎた組 u17 に新しい鍵を積む: 新しい出来事で、組の古い出来事(ask:u17)は読みに戻らない。
  (<- done (as-writer harness MAKER (AppendEvent "pairs" "done:u17" {"n" 4})))
  (<- (require-law (and (isinstance done Appended) (> done.sequence side.sequence)) law (.format "新しい鍵が新しい出来事でない: {!r}" done)))
  (<- read (as-writer harness MAKER (ReadEvents "pairs")))
  (<- (require-law (and (isinstance read Events) (= (lfor e read.items e.idempotency-key) ["done:u17"])) law
               (.format "期限を過ぎた組の古い出来事が新しい鍵の追記で読みに戻った: {!r}" read)))
  (<- swept (as-writer harness MAKER (SweepExpired)))
  ;; 回収の後(鍵の覚えで答える)の答えは、回収の前と同じ。
  (<- after (expired-key-answers harness))
  (<- (require-law (= after before) law (.format "回収の前と後で期限を過ぎた鍵の答えが違う: 前 {!r} / 後 {!r}" before after)))
  (<- side-again (as-writer harness MAKER (AppendEvent "pairs" "ask:u17" {"n" 3})))
  (<- (require-law (! (replay-of side-again side)) law (.format "新しい鍵の追記の前に消えた鍵の再送が前の番号でない: {!r} {!r}" side-again side)))
  (<- end (as-writer harness MAKER (ReadStreamEnd "pairs")))
  (<- (require-law (= end (StreamEnd done.sequence)) law (.format "列の末尾が新しい鍵の出来事でない: {!r}" end)))
  (+ [beat ask side] (list before) [done read swept] (list after) [side-again end]))


;; --- 法 18: 名指した列の末尾は ReadEvents の最後の出来事と同じ位置と刻・名指さない待ちは末尾を持たない ------------------------

(defk tails-of-last-events [harness streams]
  {:pre [(: harness LawHarness) (: streams (get tuple #(str ...)))] :post [(: % (get tuple #((| StreamTail StreamTailEmpty) ...)))]
   :tags {:context "records" :role "program"}}
  "列 streams を名指した順に 1 つずつ ReadEvents で読み、最後の出来事(保持の期限を過ぎた出来事は ReadEvents に出ない)の番号と刻を
   WatchChanges の tails と比べる形(出来事が在れば StreamTail・無ければ StreamTailEmpty)にするため(法 18 の基準)。"
  (var tails #())
  (for [stream streams]
    (<- read (as-writer harness MAKER (ReadEvents stream)))
    (:= tails (+ tails #((match read
                            (Events :items #()) (StreamTailEmpty :stream stream)
                            (Events :items items) (StreamTail :stream stream :sequence (. (get items -1) sequence) :at (. (get items -1) at))
                            other (raise (LawBroken (.format "法 18 の基準: 列 {} の ReadEvents が Events でない: {!r}" stream other))))))))
  tails)


(defk law-watch-tails-match-last-events [harness]
  {:pre [(: harness LawHarness)] :post [(: % (get list object))]
   :tags {:context "records" :role "program"}}
  "WatchChanges の tails の法(#3718): 列を名指した WatchChanges の答えの tails は、名指した順に、同じ時点の ReadEvents の最後の出来事と
   同じ位置と刻(出来事が無ければ StreamTailEmpty)。列に積むと次の読みで新しい末尾・保持の期限が全部過ぎた列は空の印。列への追記は
   表の変化ではない(items に出ない)。列を名指さない答えの tails は空。どの置き場の handler も同じ答えを返すことを確かめるため。"
  (val law "名指した列の tails は ReadEvents の最後の出来事と同じ位置と刻・名指さない待ちの tails は空")
  (val streams #("pulses" "journal"))
  (<- start (as-writer harness MAKER (ListRows "parts")))
  (val cursor (WatchCursor start.epoch start.sequence))
  ;; 空の列: 名指した順にどちらも空の印。
  (<- empty (as-writer harness MAKER (WatchChanges #("parts") cursor :streams streams)))
  (<- (require-law (and (isinstance empty Changes) (= empty.tails #((StreamTailEmpty :stream "pulses") (StreamTailEmpty :stream "journal"))))
                   law (.format "空の列の末尾: {!r}" empty)))
  ;; 積んだ後: 名指した順の末尾が ReadEvents の最後の出来事と同じ位置と刻・追記は items に出ない。
  (<- beat (as-writer harness MAKER (AppendEvent "pulses" "tail-beat" {"n" 1})))
  (<- (Delay 1))
  (<- entry (as-writer harness MAKER (AppendEvent "journal" "tail-entry" {"n" 2})))
  (<- named (as-writer harness MAKER (WatchChanges #("parts") cursor :streams streams)))
  (<- named-lasts (tails-of-last-events harness streams))
  (<- (require-law (and (isinstance named Changes) (= named.items #()) (= named.tails named-lasts)
                        (= (lfor tail named.tails tail.sequence) [beat.sequence entry.sequence]))
                   law (.format "積んだ後の末尾が ReadEvents の最後の出来事 {!r} と違う: {!r}" named-lasts named)))
  ;; 名指す順を替えると tails の順も替わる。
  (<- reversed-watch (as-writer harness MAKER (WatchChanges #("parts") cursor :streams #("journal" "pulses"))))
  (<- (require-law (and (isinstance reversed-watch Changes) (= reversed-watch.tails (tuple (reversed named.tails))))
                   law (.format "名指した順でない末尾: {!r}" reversed-watch)))
  ;; 名指さない待ちの tails は空。
  (<- plain (as-writer harness MAKER (WatchChanges #("parts") cursor)))
  (<- (require-law (and (isinstance plain Changes) (= plain.tails #())) law (.format "名指さない待ちの末尾: {!r}" plain)))
  ;; 表の変化と末尾は同じ答えに載る。
  (<- written (as-writer harness MAKER (PutRow "parts" #("p-tail") (FrozenMap {"label" "t"}) (ExpectAbsent))))
  (<- changed (as-writer harness MAKER (WatchChanges #("parts") cursor :streams streams)))
  (<- (require-law (and (isinstance changed Changes) (= (lfor item changed.items #(item.key item.version)) [#(#("p-tail") written.version)])
                        (= changed.tails named.tails))
                   law (.format "表の変化の答えの末尾: {!r}" changed)))
  ;; 保持の期限(PULSE-KEEP-SECONDS)が全部過ぎた列は、回収の前でも空の印。
  (<- (Delay (+ PULSE-KEEP-SECONDS 1)))
  (<- expired (as-writer harness MAKER (WatchChanges #("parts") changed.cursor :streams streams)))
  (<- expired-lasts (tails-of-last-events harness streams))
  (<- (require-law (and (isinstance expired Changes) (= expired.tails expired-lasts)
                        (= expired.tails #((StreamTailEmpty :stream "pulses") (get named.tails 1))))
                   law (.format "期限を過ぎた列の末尾が ReadEvents の最後の出来事 {!r} と違う・空の印でない: {!r}" expired-lasts expired)))
  [start empty beat entry named reversed-watch plain written changed expired])


;; --- 法 19: 出来事を冪等キーで 1 つ引く読みは、列の読みと同じ出来事を答え、来ていない鍵と消えた鍵を分ける ---------------------------

(defk law-event-by-key-reads-the-same-event [harness]
  {:pre [(: harness LawHarness)] :post [(: % (get list object))]
   :tags {:context "records" :role "program"}}
  "冪等キーの読みの法(#3750): ReadEventByKey は、その列にその鍵で積んだ出来事を ReadEvents が出すのと同じ Event で答える(別の列の同じ鍵・
   同じ本文の再送で答えが変わらない)。まだ積んでいない鍵は EventAbsent・保持の期限を過ぎた鍵は回収の前でも後でも消えた出来事の番号の
   EventRetired(組で数える列は組の最後の出来事から — 他の読みと同じ境)。使い手が鍵 1 つの問いのために列を頭から読み切らずに済み、
   どの置き場の handler も同じ答えを返すことを確かめるため。"
  (val law "ReadEventByKey は列の読みと同じ出来事を答え、来ていない鍵は EventAbsent・期限を過ぎた鍵は EventRetired")
  (<- first (as-writer harness MAKER (AppendEvent "journal" "by-key-1" {"n" 1})))
  (<- second (as-writer harness MAKER (AppendEvent "journal" "by-key-2" {"n" 2})))
  (<- elsewhere (as-writer harness MAKER (AppendEvent "pulses" "by-key-elsewhere" {"n" 3})))
  (<- listed (as-writer harness MAKER (ReadEvents "journal" :after (- first.sequence 1) :limit 2)))
  (<- one (as-writer harness MAKER (ReadEventByKey "journal" "by-key-1")))
  (<- two (as-writer harness MAKER (ReadEventByKey "journal" "by-key-2")))
  (<- (require-law (and (isinstance listed Events) (= #(one two) listed.items)) law
                   (.format "鍵の読みが列の読みと違う出来事を答えた: {!r} {!r} / {!r}" one two listed)))
  (<- absent (as-writer harness MAKER (ReadEventByKey "journal" "by-key-never")))
  (<- crossed (as-writer harness MAKER (ReadEventByKey "journal" "by-key-elsewhere")))
  (<- (require-law (and (= absent (EventAbsent)) (= crossed (EventAbsent))) law
                   (.format "積んでいない鍵・別の列の鍵が EventAbsent でない: {!r} {!r}" absent crossed)))
  (<- replay (as-writer harness MAKER (AppendEvent "journal" "by-key-1" {"n" 1})))
  (<- again (as-writer harness MAKER (ReadEventByKey "journal" "by-key-1")))
  (<- (require-law (and (! (replay-of replay first)) (= again one)) law (.format "再送が鍵の読みを変えた: {!r} {!r}" replay again)))
  ;; 保持: pulses は出来事ごとに PULSE-KEEP-SECONDS 秒・pairs は組(区切り「:」の後ろ)の最後の出来事から PAIR-KEEP-SECONDS 秒。
  (<- old-ask (as-writer harness MAKER (AppendEvent "pairs" "ask:by-key" {"n" 4})))
  (<- (Delay 30))
  (<- young-done (as-writer harness MAKER (AppendEvent "pairs" "done:by-key" {"n" 5})))
  (<- (Delay (- (max PULSE-KEEP-SECONDS PAIR-KEEP-SECONDS) 29)))
  (<- pulse-gone (as-writer harness MAKER (ReadEventByKey "pulses" "by-key-elsewhere")))
  (<- grouped (as-writer harness MAKER (ReadEventByKey "pairs" "ask:by-key")))
  (<- (require-law (and (= pulse-gone (EventRetired elsewhere.sequence)) (isinstance grouped Event)
                        (= grouped.sequence old-ask.sequence))
                   law (.format "期限を過ぎた鍵が回収の前に EventRetired でない・組の若い出来事が残す鍵が消えた: {!r} {!r}" pulse-gone grouped)))
  (<- (Delay 30))
  (<- group-gone (as-writer harness MAKER (ReadEventByKey "pairs" "ask:by-key")))
  (<- (as-writer harness MAKER (SweepExpired)))
  (<- swept-pulse (as-writer harness MAKER (ReadEventByKey "pulses" "by-key-elsewhere")))
  (<- swept-done (as-writer harness MAKER (ReadEventByKey "pairs" "done:by-key")))
  (<- (require-law (and (= group-gone (EventRetired old-ask.sequence)) (= swept-pulse pulse-gone)
                        (= swept-done (EventRetired young-done.sequence)))
                   law (.format "組の期限・回収の後の鍵の答えが EventRetired でない: {!r} {!r} {!r}" group-gone swept-pulse swept-done)))
  (<- kept (as-writer harness MAKER (ReadEventByKey "journal" "by-key-2")))
  (<- (require-law (= kept two) law (.format "保持の無い列の鍵が時間で変わった: {!r}" kept)))
  [first second elsewhere listed one two absent crossed replay again old-ask young-done pulse-gone grouped group-gone swept-pulse
   swept-done kept])


;; --- 法 20: 上限より短い頁は列の終わり ----------------------------------------------------------------------------

(val SHORT-PAGE-LIMIT 3)

(defk short-page-is-the-end [harness stream page limit law]
  {:pre [(: harness LawHarness) (: stream str) (: page Events) (: limit int) (: law str)] :post [(: % (get list object))]
   :tags {:context "records" :role "program"}}
  "ReadEvents の答え page の items が上限 limit より少なければ、続けて読んだ答えが空で、出来事を運ぶ頁なら列の末尾(ReadStreamEnd)が
   page の最後の番号である事を確かめ、確かめに使った答えを返すため(上限ちょうどの頁は何も約束しないので、確かめずに空の list を返す)。"
  (when (>= (len page.items) limit)
    (return []))
  (<- rest (as-writer harness MAKER (ReadEvents stream :after page.last-sequence :limit limit)))
  (<- (require-law (and (isinstance rest Events) (= rest.items #())) law
                   (.format "上限 {} より短い頁 {!r} の後に出来事が残る: {!r}" limit page rest)))
  (when (= page.items #())
    (return [rest]))
  (<- end (as-writer harness MAKER (ReadStreamEnd stream)))
  (<- (require-law (= end (StreamEnd page.last-sequence)) law
                   (.format "上限 {} より短い頁 {!r} の最後の番号が列の末尾と違う: {!r}" limit page end)))
  [rest end])

(defk law-short-page-ends-the-stream [harness]
  {:pre [(: harness LawHarness)] :post [(: % (get list object))]
   :tags {:context "records" :role "program"}}
  "短い頁の法(#3986): ReadEvents の答えの items が limit より少なければ、その答えを作った時に after より後の出来事は items の他に無い
   (= 上限より短い頁は列の終わり)。使い手が「空の頁が返るまで読む」でなく「短い頁で止める」で列を読み切り、空の頁の確かめの読みを
   1 回減らせるため。上限ちょうどの頁の後には出来事が残っていてよい(何も約束しない)。どの置き場の handler も同じ答えを返すことを確かめる。"
  (val law "ReadEvents の答えが上限より短ければ、after より後の出来事は答えの他に無い")
  ;; 上限の 2 倍より 1 つ少ない数(上限ちょうどの頁 1 つと、短い頁 1 つに分かれる)。
  (val keys ["short-page-0" "short-page-1" "short-page-2" "short-page-3" "short-page-4"])
  (<- first (as-writer harness MAKER (AppendEvent "journal" "short-page-0" {"n" 0})))
  ;; 別の列の出来事を間に積む(番号は全部の列で 1 つなので journal の番号に飛びができる — 飛びを列の終わりと取り違えない事も見る)。
  (<- elsewhere (as-writer harness MAKER (AppendEvent "pulses" "short-page-elsewhere" {"n" 0})))
  (<- second (as-writer harness MAKER (AppendEvent "journal" "short-page-1" {"n" 1})))
  (<- third (as-writer harness MAKER (AppendEvent "journal" "short-page-2" {"n" 2})))
  (<- fourth (as-writer harness MAKER (AppendEvent "journal" "short-page-3" {"n" 3})))
  (<- fifth (as-writer harness MAKER (AppendEvent "journal" "short-page-4" {"n" 4})))
  (val appended [first elsewhere second third fourth fifth])
  (val start (- first.sequence 1))
  (<- full (as-writer harness MAKER (ReadEvents "journal" :after start :limit SHORT-PAGE-LIMIT)))
  (<- full-checked (short-page-is-the-end harness "journal" full SHORT-PAGE-LIMIT law))
  (<- short (as-writer harness MAKER (ReadEvents "journal" :after full.last-sequence :limit SHORT-PAGE-LIMIT)))
  (<- short-checked (short-page-is-the-end harness "journal" short SHORT-PAGE-LIMIT law))
  (<- (require-law (and (= (len full.items) SHORT-PAGE-LIMIT) (< (len short.items) SHORT-PAGE-LIMIT)
                        (= (lfor e (+ full.items short.items) e.idempotency-key) keys))
                   law (.format "上限ちょうどの頁と短い頁で積んだ出来事を読み切れない: {!r} {!r}" full short)))
  ;; 上限が残りの数より大きい読み(1 頁で列の終わりまで)も短い頁。
  (val wide-limit (* 4 SHORT-PAGE-LIMIT))
  (<- whole (as-writer harness MAKER (ReadEvents "journal" :after start :limit wide-limit)))
  (<- whole-checked (short-page-is-the-end harness "journal" whole wide-limit law))
  (<- (require-law (= (lfor e whole.items e.idempotency-key) keys) law (.format "1 頁で読んだ列: {!r}" whole)))
  (+ appended [full short whole] full-checked short-checked whole-checked))


;; --- 法 19〜21: 送ってから外す列(maintenance.hy の頭の註の「出来事を送ってから外す」— card ki-707aad0f88e8)--------------------------

(defk expired-keys [harness at]
  {:pre [(: harness LawHarness) (: at datetime)] :post [(: % tuple)]
   :tags {:context "records" :role "program"}}
  "送ってから外す列の、刻 at の境で期限を過ぎて置き場に残る出来事を ReadExpiredEvents の頁で読み切り、#(列 冪等キー) を読んだ順に
   並べるため(法 19〜21 が「置き場に残るか」を読む口 — 読みの effect は期限を過ぎた出来事を出さない)。"
  (var found #())
  (var after None)
  (var going True)
  (while going
    (<- page (as-writer harness MAKER (ReadExpiredEvents at :after after :limit 2)))
    (when (not (isinstance page ExpiredEvents))
      (raise (LawBroken (.format "法 19〜21 の基準: ReadExpiredEvents が ExpiredEvents でない: {!r}" page))))
    (:= found (+ found (tuple (gfor event page.items #(event.stream event.idempotency-key)))))
    (:= after page.next)
    (:= going (is-not after None)))
  found)


(defk confirm-keys [keys events]
  {:pre [(: keys frozenset) (: events tuple)] :post [(: % EventsArchived)]
   :tags {:context "records" :role "program"}}
  "送り先の顔: 渡された頁 events のうち、冪等キーが keys に在る出来事だけを送り先に在ると答えるため(法 19・21 の送りの関数 —
   partial で keys を束ねて渡す)。"
  (EventsArchived (tuple (gfor event events :if (in event.idempotency-key keys) event.sequence))))


(defk unreachable-destination [events]
  {:pre [(: events tuple)] :post [(: % Unreachable)] :tags {:context "records" :role "program"}}
  "送り先に届かない送りの関数(法 19)。"
  (Unreachable "送り先に届かない"))


(defk law-only-confirmed-events-are-retired [harness]
  {:pre [(: harness LawHarness)] :post [(: % (get list object))]
   :tags {:context "records" :role "program"}}
  "送ってから外す列の回収の法(card ki-707aad0f88e8): 期限を過ぎた出来事は回収(SweepExpired)では置き場から外れず、手入れの送りの関数が
   送り先に在ると確かめた出来事だけが外れる — 送り先に届かない回は 1 つも外れず、確かめた番号に無い出来事は残る(出来事ごとの列 relays と
   組の列 relay-pairs の両方)。外れる前も後も読みには出ず、外した鍵は覚えている(同じ本文は前の番号・別の本文は断り)。"
  (val law "送ってから外す列の期限を過ぎた出来事は、送り先に在ると確かめた物だけが外れる")
  (<- r1 (as-writer harness MAKER (AppendEvent "relays" "r-1" {"n" 1})))
  (<- r2 (as-writer harness MAKER (AppendEvent "relays" "r-2" {"n" 2})))
  (<- ask (as-writer harness MAKER (AppendEvent "relay-pairs" "ask:k" {"n" 3})))
  (<- done (as-writer harness MAKER (AppendEvent "relay-pairs" "done:k" {"n" 4})))
  (<- (Delay (+ RELAY-KEEP-SECONDS 1)))
  (val all-keys #(#("relay-pairs" "ask:k") #("relay-pairs" "done:k") #("relays" "r-1") #("relays" "r-2")))
  ;; 回収は送ってから外す列を外さない。読みには出ない。
  (<- swept (as-writer harness MAKER (SweepExpired)))
  (<- t1 (GetTime))
  (<- kept (expired-keys harness t1))
  (<- (require-law (= kept all-keys) law (.format "回収が送っていない出来事を外した: {!r} {!r}" swept kept)))
  (<- read (as-writer harness MAKER (ReadEvents "relays")))
  (<- (require-law (and (isinstance read Events) (= read.items #())) law (.format "期限を過ぎた出来事が読みに出た: {!r}" read)))
  ;; 送り先に届かない回は外さない(最初の頁 — 組の列の 2 つ — で回を終える)。
  (<- down (as-writer harness MAKER (archive-expired-events unreachable-destination)))
  (<- t2 (GetTime))
  (<- still (expired-keys harness t2))
  (<- (require-law (and (= still all-keys) (= #(down.unreached down.retired) #(2 0)) (isinstance down.halted Unreachable)) law
                   (.format "送り先に届かない回に出来事が外れた: {!r} {!r}" down still)))
  ;; 確かめた番号の出来事だけが外れる(r-1・ask:k)。組の残り(done:k)と確かめていない r-2 は残る。
  (<- part (as-writer harness MAKER (archive-expired-events (partial confirm-keys (frozenset #("r-1" "ask:k"))))))
  (<- t3 (GetTime))
  (<- left (expired-keys harness t3))
  (<- (require-law (and (= left #(#("relay-pairs" "done:k") #("relays" "r-2")))
                        (= #(part.sent part.confirmed part.unreached part.retired) #(4 2 0 2)) (is part.halted None))
                   law (.format "確かめた番号の出来事だけが外れない: {!r} {!r}" part left)))
  ;; 外した鍵も外していない鍵も、冪等の答えは前と同じ(覚えで答える)。
  (<- again-r1 (as-writer harness MAKER (AppendEvent "relays" "r-1" {"n" 1})))
  (<- other-r2 (as-writer harness MAKER (AppendEvent "relays" "r-2" {"n" 9})))
  (<- again-ask (as-writer harness MAKER (AppendEvent "relay-pairs" "ask:k" {"n" 3})))
  (<- (require-law (and (! (replay-of again-r1 r1)) (! (replay-of again-ask ask))
                        (isinstance other-r2 Refused) (in "冪等キー" other-r2.reason))
                   law (.format "送って外した鍵の冪等の答えが違う: {!r} {!r} {!r}" again-r1 other-r2 again-ask)))
  (<- by-key-1 (as-writer harness MAKER (ReadEventByKey "relays" "r-1")))
  (<- by-key-2 (as-writer harness MAKER (ReadEventByKey "relays" "r-2")))
  (<- (require-law (and (= by-key-1 (EventRetired r1.sequence)) (= by-key-2 (EventRetired r2.sequence))) law
                   (.format "外した鍵・残る期限を過ぎた鍵の引きが退いた でない: {!r} {!r}" by-key-1 by-key-2)))
  ;; 残りを確かめれば全部外れる。
  (<- rest (as-writer harness MAKER (archive-expired-events (partial confirm-keys (frozenset #("r-2" "done:k"))))))
  (<- t4 (GetTime))
  (<- gone (expired-keys harness t4))
  (<- (require-law (and (= gone #()) (= rest.retired 2)) law (.format "確かめた残りが外れない: {!r} {!r}" rest gone)))
  [r1 r2 ask done swept kept read down still part left again-r1 other-r2 again-ask by-key-1 by-key-2 rest gone])


(defk confirm-with-a-late-guess [late events]
  {:pre [(: late int) (: events tuple)] :post [(: % EventsArchived)]
   :tags {:context "records" :role "program"}}
  "送り先の顔(法 20): 送りの間に時計を 2 秒進め(読みの後に別の出来事が期限を過ぎる)、頁の番号と頁に無い番号 late を送り先に在ると
   答えるため(partial で late を束ねて渡す)。"
  (<- (Delay 2))
  (EventsArchived (+ (tuple (gfor event events event.sequence)) #(late))))


(defk law-retirement-keeps-the-read-boundary [harness]
  {:pre [(: harness LawHarness)] :post [(: % (get list object))]
   :tags {:context "records" :role "program"}}
  "送りの読みの境の法(card ki-707aad0f88e8): 手入れの回は刻 T の境で期限を過ぎた出来事を読んで送り、外しも同じ刻 T の境で行う —
   読みの後(送りの間)に新しく期限を過ぎた出来事は、送りの関数がその番号を答えても、同じ回では外れない。外しの effect に境 T と
   T の後に期限を過ぎた出来事の番号を直に渡しても外れない。"
  (val law "送りの読みの後に期限を過ぎた出来事は、同じ回の外しで外れない")
  (<- early (as-writer harness MAKER (AppendEvent "relays" "edge-early" {"n" 1})))
  (<- (Delay 2))
  (<- late (as-writer harness MAKER (AppendEvent "relays" "edge-late" {"n" 2})))
  ;; 刻 T: early は期限を過ぎ、late はまだ(early の 2 秒後に積んだ)。
  (<- (Delay (- RELAY-KEEP-SECONDS 1)))
  (<- t (GetTime))
  (<- report (as-writer harness MAKER (archive-expired-events (partial confirm-with-a-late-guess late.sequence))))
  (<- t-after (GetTime))
  (<- left (expired-keys harness t-after))
  (<- (require-law (and (= #(report.sent report.confirmed report.retired) #(1 1 1))
                        (= left #(#("relays" "edge-late"))))
                   law (.format "読みの後に期限を過ぎた出来事が同じ回に外れた・送られた: {!r} {!r}" report left)))
  ;; 外しの effect の境: T の境では late は期限を過ぎていないので外れない(今の刻では過ぎている)。
  (<- direct (as-writer harness MAKER (RetireArchivedEvents "relays" t #(late.sequence))))
  (<- t-direct (GetTime))
  (<- still (expired-keys harness t-direct))
  (<- (require-law (and (= direct (RetiredEvents 0)) (= still #(#("relays" "edge-late")))) law
                   (.format "境 T の外しが T の後に期限を過ぎた出来事を外した: {!r} {!r}" direct still)))
  [early late report left direct still])


(defk relay-answers [harness]
  {:pre [(: harness LawHarness)] :post [(: % tuple)]
   :tags {:context "records" :role "program"}}
  "法 21 の追記 4 つ(出来事ごとの列 relays と組の列 relay-pairs の期限を過ぎた鍵へ、前と同じ本文と別の本文)を撃ち、答えを並べるため。"
  (<- beat (as-writer harness MAKER (AppendEvent "relays" "beat-21" {"n" 1})))
  (<- beat-other (as-writer harness MAKER (AppendEvent "relays" "beat-21" {"n" 9})))
  (<- ask (as-writer harness MAKER (AppendEvent "relay-pairs" "ask:t21" {"n" 2})))
  (<- ask-other (as-writer harness MAKER (AppendEvent "relay-pairs" "ask:t21" {"n" 9})))
  #(beat beat-other ask ask-other))


(defk law-a-write-keeps-the-unarchived-event-it-touches [harness]
  {:pre [(: harness LawHarness)] :post [(: % (get list object))]
   :tags {:context "records" :role "program"}}
  "送ってから外す列の書きの法(card ki-707aad0f88e8): 期限を過ぎた鍵(出来事ごと)と期限を過ぎた組(組ごと)に触る追記は、出来事を
   置き場から外さない(送りの関数が確かめるまで残る)が、答えは前の形と同じ — 同じ本文は前の番号・別の本文は同じ文の断り。期限を過ぎた
   組に新しい鍵を積んでも組の古い出来事は読みに戻らず、鍵の引きは退いたと答える。送って外した後の答えも同じ(法 17 の送ってから外す形)。"
  (val law "送ってから外す列の期限を過ぎた鍵への追記は出来事を外さず、答えは前と同じ")
  (<- beat (as-writer harness MAKER (AppendEvent "relays" "beat-21" {"n" 1})))
  (<- ask (as-writer harness MAKER (AppendEvent "relay-pairs" "ask:t21" {"n" 2})))
  (<- side (as-writer harness MAKER (AppendEvent "relay-pairs" "ask:u21" {"n" 3})))
  (<- (Delay (+ RELAY-KEEP-SECONDS 1)))
  (<- before (relay-answers harness))
  (<- (require-law (and (! (replay-of (get before 0) beat)) (! (replay-of (get before 2) ask))) law
                   (.format "期限を過ぎた鍵の同じ本文の再送が前の番号でない: {!r} {!r} {!r}" before beat ask)))
  (<- (require-law (all (gfor answer #((get before 1) (get before 3)) (and (isinstance answer Refused) (in "冪等キー" answer.reason))))
                   law (.format "期限を過ぎた鍵の別の本文が断られない: {!r}" before)))
  ;; 期限を過ぎた組 u21 に新しい鍵を積む: 新しい出来事で、組の古い出来事(ask:u21)は読みに戻らず、鍵の引きは退いた。
  (<- done (as-writer harness MAKER (AppendEvent "relay-pairs" "done:u21" {"n" 4})))
  (<- read (as-writer harness MAKER (ReadEvents "relay-pairs")))
  (<- by-key (as-writer harness MAKER (ReadEventByKey "relay-pairs" "ask:u21")))
  (<- end (as-writer harness MAKER (ReadStreamEnd "relay-pairs")))
  (<- (require-law (and (isinstance done Appended) (> done.sequence side.sequence) (isinstance read Events)
                        (= (lfor e read.items e.idempotency-key) ["done:u21"]) (= by-key (EventRetired side.sequence))
                        (= end (StreamEnd done.sequence)))
                   law (.format "期限を過ぎた組の古い出来事が新しい鍵の追記で読みに戻った: {!r} {!r} {!r} {!r}" done read by-key end)))
  ;; 書きは触った出来事を外さない: 送りの読みにまだ出る(新しい鍵の出来事は期限の前なので出ない)。
  (<- t (GetTime))
  (<- kept (expired-keys harness t))
  (<- (require-law (= kept #(#("relay-pairs" "ask:t21") #("relay-pairs" "ask:u21") #("relays" "beat-21"))) law
                   (.format "期限を過ぎた鍵に触る追記が、送っていない出来事を外した: {!r}" kept)))
  ;; 送って外した後の答えは、外す前と同じ。
  (<- archived (as-writer harness MAKER (archive-expired-events (partial confirm-keys (frozenset #("ask:t21" "ask:u21" "beat-21"))))))
  (<- after (relay-answers harness))
  (<- side-again (as-writer harness MAKER (AppendEvent "relay-pairs" "ask:u21" {"n" 3})))
  (<- end-after (as-writer harness MAKER (ReadStreamEnd "relay-pairs")))
  (<- (require-law (and (= archived.retired 3) (= after before) (! (replay-of side-again side)) (= end-after (StreamEnd done.sequence))) law
                   (.format "送って外した前と後で答えが違う: 前 {!r} / 後 {!r} {!r} {!r} {!r}" before after archived side-again end-after)))
  (+ [beat ask side] (list before) [done read by-key end kept archived] (list after) [side-again end-after]))


;; --- 法 22・23: 鍵の覚え方を限った列(maintenance.hy の頭の註の「鍵の覚えを外す」— card ki-4ebaefac6668)---------------------------

(defk fresh-append? [answer earlier]
  {:pre [(: answer (| Appended Refused Unreachable)) (: earlier (| Appended Refused Unreachable))] :post [(: % bool)]}
  "追記の答え answer が、earlier より後の番号で今積んだ新しい出来事かを判じるため(覚えの外れた鍵・覚えを残さない列の鍵の追記の答え)。"
  (and (isinstance answer Appended) (isinstance earlier Appended) (is answer.replayed False) (> answer.sequence earlier.sequence)))


(defk law-keys-are-forgotten-after-their-memory [harness]
  {:pre [(: harness LawHarness)] :post [(: % (get list object))]
   :tags {:context "records" :role "program"}}
  "鍵の覚え方の法(card ki-4ebaefac6668): 覚えを長さで限った列(beats — RememberKeysFor)の置き場を去った出来事の鍵は、覚えた刻から長さの
   間は覚えで答え(同じ本文は前の番号・別の本文は断り・鍵の引きは退いた)、長さの後の回収(SweepExpired)が覚えを外す — 外れた鍵の追記は
   新しい出来事で、鍵の引きは来ていない(EventAbsent)。覚えを残さない列(ticks — RememberNoKeys)は、置き場を去った鍵を覚えない —
   回収の後も、回収の前に書きが触って片付けた時も、同じ鍵の追記は新しい出来事(組で数える列 tick-pairs は、書きが片付けた組の他の鍵も
   覚えない)。"
  (val law "長さを限った覚えは長さの後の回収で外れ、覚えの無い鍵の追記は新しい出来事")
  (<- beat (as-writer harness MAKER (AppendEvent "beats" "b-1" {"n" 1})))
  (<- tick (as-writer harness MAKER (AppendEvent "ticks" "t-1" {"n" 1})))
  (<- touched-beat (as-writer harness MAKER (AppendEvent "beats" "b-2" {"n" 2})))
  (<- touched-tick (as-writer harness MAKER (AppendEvent "ticks" "t-2" {"n" 2})))
  (<- asked (as-writer harness MAKER (AppendEvent "tick-pairs" "ask:g" {"n" 3})))
  (<- settled (as-writer harness MAKER (AppendEvent "tick-pairs" "done:g" {"n" 4})))
  (<- (Delay (+ BEAT-KEEP-SECONDS 1)))
  ;; 回収の前に書きが期限を過ぎた鍵に触る: 長さを限った列は覚えで答え、覚えを残さない列は新しい出来事(回収の後と同じ答え)。組で数える
  ;; 覚えを残さない列は、書きが触った鍵の組の出来事を全部片付け、どの鍵も覚えない(組の他の鍵の引きも来ていない)。
  (<- beat-touched (as-writer harness MAKER (AppendEvent "beats" "b-2" {"n" 2})))
  (<- tick-touched (as-writer harness MAKER (AppendEvent "ticks" "t-2" {"n" 2})))
  (<- asked-touched (as-writer harness MAKER (AppendEvent "tick-pairs" "ask:g" {"n" 3})))
  (<- settled-key (as-writer harness MAKER (ReadEventByKey "tick-pairs" "done:g")))
  (<- (require-law (and (! (replay-of beat-touched touched-beat)) (! (fresh-append? tick-touched touched-tick))
                        (! (fresh-append? asked-touched tick-touched)) (= settled-key (EventAbsent)))
                   law (.format "回収の前に書きが触った鍵の答えが違う: {!r} {!r} {!r} {!r}" beat-touched tick-touched asked-touched settled-key)))
  (<- swept (as-writer harness MAKER (SweepExpired)))
  (<- (require-law (= swept.forgotten 0) law (.format "長さの内の覚えが外れた: {!r}" swept)))
  ;; 長さの内: 長さを限った列は覚えで答え、覚えを残さない列は鍵を知らない。
  (<- beat-again (as-writer harness MAKER (AppendEvent "beats" "b-1" {"n" 1})))
  (<- beat-other (as-writer harness MAKER (AppendEvent "beats" "b-1" {"n" 9})))
  (<- beat-key (as-writer harness MAKER (ReadEventByKey "beats" "b-1")))
  (<- tick-key (as-writer harness MAKER (ReadEventByKey "ticks" "t-1")))
  (<- tick-again (as-writer harness MAKER (AppendEvent "ticks" "t-1" {"n" 1})))
  (<- (require-law (and (! (replay-of beat-again beat)) (isinstance beat-other Refused) (in "冪等キー" beat-other.reason)
                        (= beat-key (EventRetired beat.sequence)) (= tick-key (EventAbsent)) (! (fresh-append? tick-again tick-touched)))
                   law (.format "長さの内の答えが違う: {!r} {!r} {!r} {!r} {!r}" beat-again beat-other beat-key tick-key tick-again)))
  ;; 長さの後: 回収が覚えを外す(回収が捨てた b-1 と書きが片付けた b-2 の 2 つ)— 外れた鍵の追記は新しい出来事。
  (<- (Delay (+ BEAT-MEMORY-SECONDS 1)))
  (<- forgot (as-writer harness MAKER (SweepExpired)))
  (<- beat-gone (as-writer harness MAKER (ReadEventByKey "beats" "b-1")))
  (<- beat-new (as-writer harness MAKER (AppendEvent "beats" "b-1" {"n" 9})))
  (<- read (as-writer harness MAKER (ReadEvents "beats")))
  (<- (require-law (and (= forgot.forgotten 2) (= beat-gone (EventAbsent)) (! (fresh-append? beat-new tick-again))
                        (isinstance read Events) (= (lfor e read.items #(e.idempotency-key (get e.body "n"))) [#("b-1" 9)]))
                   law (.format "長さの後の覚えが外れない・外れた鍵の追記が新しい出来事でない: {!r} {!r} {!r} {!r}" forgot beat-gone beat-new read)))
  [beat tick touched-beat touched-tick asked settled beat-touched tick-touched asked-touched settled-key swept beat-again beat-other beat-key tick-key tick-again
   forgot beat-gone beat-new read])


(defk law-unarchived-keys-are-kept-until-retired [harness]
  {:pre [(: harness LawHarness)] :post [(: % (get list object))]
   :tags {:context "records" :role "program"}}
  "送ってから外す列の鍵の覚えの法(card ki-4ebaefac6668): 覚え方を限った送ってから外す列(relay-beats — 長さ・relay-ticks — 残さない)の
   覚えは、送り先に届かない間は覚えの長さを過ぎても外れない(出来事も覚えも置き場に残り、答えは変わらない)。送り先に在ると確かめて
   出来事を外した後、残さない列の覚えは同じ外しで外れ(鍵の追記は新しい出来事)、長さの列の覚えは覚えた刻から長さの後の回収で外れる
   (書きが前に覚えた鍵は外しの後の最初の回収・外しの時に覚えた鍵は外した刻から長さの後)。"
  (val law "送ってから外す列の覚えは、送り先に在ると確かめて出来事を外すまで外れない")
  (<- held (as-writer harness MAKER (AppendEvent "relay-beats" "rb-1" {"n" 1})))
  (<- spare (as-writer harness MAKER (AppendEvent "relay-beats" "rb-2" {"n" 2})))
  (<- mark (as-writer harness MAKER (AppendEvent "relay-ticks" "rt-1" {"n" 3})))
  (<- (Delay (+ BEAT-KEEP-SECONDS 1)))
  ;; 書きが期限を過ぎた鍵に触る: 鍵を覚えるだけで出来事は置き場に残る(答えは覚えで前の番号)。
  (<- held-touched (as-writer harness MAKER (AppendEvent "relay-beats" "rb-1" {"n" 1})))
  (<- mark-touched (as-writer harness MAKER (AppendEvent "relay-ticks" "rt-1" {"n" 3})))
  (<- (require-law (and (! (replay-of held-touched held)) (! (replay-of mark-touched mark))) law
                   (.format "期限を過ぎた鍵の同じ本文の再送が前の番号でない: {!r} {!r}" held-touched mark-touched)))
  ;; 送り先に届かない回は外さず、覚えの長さを過ぎた後の回収も覚えを外さない(出来事が置き場に残る)。
  (<- down (as-writer harness MAKER (archive-expired-events unreachable-destination)))
  (<- (Delay (+ BEAT-MEMORY-SECONDS 1)))
  (<- kept (as-writer harness MAKER (SweepExpired)))
  (<- held-late (as-writer harness MAKER (AppendEvent "relay-beats" "rb-1" {"n" 1})))
  (<- held-other (as-writer harness MAKER (AppendEvent "relay-beats" "rb-1" {"n" 9})))
  (<- mark-late (as-writer harness MAKER (AppendEvent "relay-ticks" "rt-1" {"n" 3})))
  (<- held-key (as-writer harness MAKER (ReadEventByKey "relay-beats" "rb-1")))
  (<- mark-key (as-writer harness MAKER (ReadEventByKey "relay-ticks" "rt-1")))
  (<- (require-law (and (= down.retired 0) (isinstance down.halted Unreachable) (= kept.forgotten 0)
                        (! (replay-of held-late held)) (isinstance held-other Refused) (in "冪等キー" held-other.reason)
                        (! (replay-of mark-late mark))
                        (= held-key (EventRetired held.sequence)) (= mark-key (EventRetired mark.sequence)))
                   law (.format "送り先に届かない間に覚えが外れた: {!r} {!r} {!r} {!r} {!r} {!r} {!r}"
                                down kept held-late held-other mark-late held-key mark-key)))
  ;; 送り先に在ると確かめて外す: 残さない列の覚えは同じ外しで外れる。
  (<- sent (as-writer harness MAKER (archive-expired-events (partial confirm-keys (frozenset #("rb-1" "rb-2" "rt-1"))))))
  (<- mark-gone (as-writer harness MAKER (ReadEventByKey "relay-ticks" "rt-1")))
  (<- held-retired (as-writer harness MAKER (ReadEventByKey "relay-beats" "rb-1")))
  (<- (require-law (and (= sent.retired 3) (= mark-gone (EventAbsent)) (= held-retired (EventRetired held.sequence))) law
                   (.format "外しの後の覚えの答えが違う: {!r} {!r} {!r}" sent mark-gone held-retired)))
  ;; 長さの列: 書きが前に覚えた rb-1 は次の回収で外れ、外しの時に覚えた rb-2 は外した刻から長さの間残る。
  (<- forgot (as-writer harness MAKER (SweepExpired)))
  (<- held-gone (as-writer harness MAKER (ReadEventByKey "relay-beats" "rb-1")))
  (<- spare-again (as-writer harness MAKER (AppendEvent "relay-beats" "rb-2" {"n" 2})))
  (<- (require-law (and (= forgot.forgotten 1) (= held-gone (EventAbsent)) (! (replay-of spare-again spare))) law
                   (.format "外しの後の回収の覚えの外しが違う: {!r} {!r} {!r}" forgot held-gone spare-again)))
  (<- (Delay (+ BEAT-MEMORY-SECONDS 1)))
  (<- last (as-writer harness MAKER (SweepExpired)))
  (<- spare-gone (as-writer harness MAKER (ReadEventByKey "relay-beats" "rb-2")))
  (<- mark-new (as-writer harness MAKER (AppendEvent "relay-ticks" "rt-1" {"n" 3})))
  (<- (require-law (and (= last.forgotten 1) (= spare-gone (EventAbsent)) (! (fresh-append? mark-new spare))) law
                   (.format "外しの時に覚えた鍵が長さの後に外れない・覚えの無い鍵の追記が新しい出来事でない: {!r} {!r} {!r}"
                            last spare-gone mark-new)))
  [held spare mark held-touched mark-touched down kept held-late held-other mark-late held-key mark-key sent mark-gone held-retired
   forgot held-gone spare-again last spare-gone mark-new])


;; --- 法 24: 書きの答えは、その書きが変更の列に積んだ変更の位置を名乗る -----------------------------------------

(defk law-a-write-names-its-change-position [harness]
  {:pre [(: harness LawHarness)] :post [(: % (get list object))]}
  "書き手が自分の書きの入った位置を答えから知り、次の読みでその位置まで追いついた答えを求められるための法: PutRow の Written と
   PutRows の束の各項の position は、その書きの変更が WatchChanges に現れる番号と置き場の版(epoch)で、束の中では束の順に 1 つずつ続き、
   最後の書きの位置は書いた直後の一覧の頭(Page の epoch・sequence)と同じ。位置から読む WatchChanges はその書きより後の変更だけを返し、
   版を上げた置き場の書きは新しい版を名乗る。断り・衝突の答えは位置を持たない(書いていない)。答えの列は memory・PostgreSQL・HTTP の口越しで
   等しい(test_parity_memory_pg.hy — 位置まで同じ値)。"
  (val law "書きの答えは、その書きが変更の列に積んだ変更の位置を名乗る")
  (<- start (as-writer harness MAKER (ListRows "parts" :limit 1)))
  (<- (require-law (isinstance start Page) law (.format "最初の一覧: {!r}" start)))
  (<- w1 (as-writer harness MAKER (PutRow "parts" #("p1") (FrozenMap {"label" "a"}) (ExpectAbsent))))
  (<- (require-law (and (isinstance w1 Written) (= w1.position (WatchCursor start.epoch (+ start.sequence 1)))) law
                   (.format "最初の書きの位置: {!r} (一覧の頭 {!r} {!r})" w1 start.epoch start.sequence)))
  (<- refused (as-writer harness MAKER (PutRow "parts" #("p1") (FrozenMap {"size" 1}) (ExpectAny))))
  (<- conflict (as-writer harness MAKER (PutRow "parts" #("p1") (FrozenMap {"label" "b"}) (ExpectAbsent))))
  (<- (require-law (and (isinstance refused Refused) (isinstance conflict Conflict)) law
                   (.format "断る書きと衝突する書き: {!r} {!r}" refused conflict)))
  (<- batch (as-writer harness MAKER
              (PutRows #((RowWrite "parts" #("p1") (FrozenMap {"label" "c"}) (ExpectVersion 1))
                         (RowWrite "tickets" #("g1" "t1") (FrozenMap {"owner" "o1"}) (ExpectAbsent))
                         (RowWrite "parts" #("p2") (FrozenMap {"label" "d"}) (ExpectAbsent))))))
  (<- (require-law (and (isinstance batch WrittenRows)
                        (= (lfor item batch.items item.position)
                           (lfor n [1 2 3] (WatchCursor start.epoch (+ w1.position.sequence n)))))
                   law (.format "束の各項の位置が束の順に続かない: {!r}" batch)))
  (val written [#("parts" #("p1") w1) #("parts" #("p1") (get batch.items 0)) #("tickets" #("g1" "t1") (get batch.items 1))
                #("parts" #("p2") (get batch.items 2))])
  (<- collected (collect-changes harness #("parts" "tickets") (WatchCursor start.epoch start.sequence) 100))
  (val items (tuple (gfor answer (get collected 0) :if (isinstance answer Changes) item answer.items item)))
  (<- (require-law (= (lfor item items #(item.table item.key item.version item.sequence))
                      (lfor #(table key one) written #(table key one.version one.position.sequence)))
                   law (.format "書きの位置と WatchChanges の変更の番号が違う: {!r}" items)))
  (val last (get batch.items -1))
  (<- head (as-writer harness MAKER (ListRows "parts" :limit 1)))
  (<- (require-law (and (isinstance head Page) (= (WatchCursor head.epoch head.sequence) last.position)) law
                   (.format "最後の書きの位置が書いた直後の一覧の頭でない: {!r} {!r}" last head)))
  (<- beyond (as-writer harness MAKER (WatchChanges #("parts" "tickets") w1.position)))
  (<- (require-law (and (isinstance beyond Changes)
                        (= (lfor item beyond.items item.sequence) (lfor item batch.items item.position.sequence)))
                   law (.format "最初の書きの位置から読んだ変更が、その後の書きの変更だけでない: {!r}" beyond)))
  (<- epoch (as-writer harness MAKER (AdvanceStoreEpoch)))
  (<- after-epoch (as-writer harness MAKER (PutRow "parts" #("p3") (FrozenMap {"label" "e"}) (ExpectAbsent))))
  (<- (require-law (and (isinstance after-epoch Written) (= after-epoch.position (WatchCursor epoch (+ last.position.sequence 1))))
                   law (.format "版を上げた置き場の書きの位置: {!r} (新しい版 {!r})" after-epoch epoch)))
  [start w1 refused conflict batch #* (get collected 0) head beyond epoch after-epoch])


;; --- 法 25〜27: 行の書きの冪等キー(card ki-78c6d663491f)-----------------------------------------------------------

(defk law-a-keyed-write-replays-its-answer [harness]
  {:pre [(: harness LawHarness)] :post [(: % (get list object))]
   :tags {:context "records" :role "program"}}
  "答えの切れた書きを送り直す書き手が、自分の着いた書きの答えを受け取れるための法: 冪等キーつきの書き(PutRow・PutRows)の同じ鍵・同じ
   本文の送り直しは、間に別の書き手が行を変えていても、期待(ExpectAbsent・ExpectVersion・ExpectAny)を見ずに前の答え(Written の
   その時の版・値・位置 — 束は束の順の Written)を送り直しと名乗って(replayed)返し、行を変えず、変更の列に何も積まない。同じ鍵の
   別の本文は冪等キーの断り(PutRow は Refused・PutRows は束の 0 行目の RowsRefused)で、これも行を変えない。"
  (val law "冪等キーつきの書きの送り直しは、間に行が変わっても前の答えを返し、行を変えない")
  ;; ExpectAbsent の書き → 別の書き手が版 2 へ → 同じ鍵の送り直しは版 1 の Written(Conflict でない)。
  (<- born (as-writer harness MAKER (PutRow "parts" #("p1") (FrozenMap {"label" "a"}) (ExpectAbsent) :idempotency-key "w-born")))
  (<- (require-law (and (isinstance born Written) (= born.version 1) (is born.replayed False)) law (.format "生まれる書き: {!r}" born)))
  (<- moved (as-writer harness PAINTER (PutRow "parts" #("p1") (FrozenMap {"color" "red"}) (ExpectVersion 1))))
  (<- (require-law (and (isinstance moved Written) (= moved.version 2)) law (.format "間の別の書き: {!r}" moved)))
  ;; ExpectAny の書き → 別の書き手が欄を書く → 同じ鍵の送り直しはその欄を書き戻さない。
  (<- blind (as-writer harness MAKER (PutRow "parts" #("p2") (FrozenMap {"color" "blue" "label" "b"}) (ExpectAny) :idempotency-key "w-blind")))
  (<- painted (as-writer harness PAINTER (PutRow "parts" #("p2") (FrozenMap {"color" "green"}) (ExpectAny))))
  ;; 束の書き → 束の行の 1 つを別の書き手が変える → 同じ鍵の送り直しは束の前の答え。
  (<- batch (as-writer harness MAKER
              (PutRows #((RowWrite "parts" #("p3") (FrozenMap {"label" "c"}) (ExpectAbsent))
                         (RowWrite "tickets" #("g1" "t1") (FrozenMap {"owner" "o1"}) (ExpectAbsent)))
                       :idempotency-key "w-batch")))
  (<- (require-law (and (isinstance batch WrittenRows) (is batch.replayed False)) law (.format "束の書き: {!r}" batch)))
  (<- moved-batch (as-writer harness PAINTER (PutRow "parts" #("p3") (FrozenMap {"color" "red"}) (ExpectVersion 1))))
  (<- mark (as-writer harness MAKER (ListRows "parts" :limit 1)))
  (<- again (as-writer harness MAKER (PutRow "parts" #("p1") (FrozenMap {"label" "a"}) (ExpectAbsent) :idempotency-key "w-born")))
  (<- blind-again (as-writer harness MAKER
                    (PutRow "parts" #("p2") (FrozenMap {"color" "blue" "label" "b"}) (ExpectAny) :idempotency-key "w-blind")))
  (<- batch-again (as-writer harness MAKER
                    (PutRows #((RowWrite "parts" #("p3") (FrozenMap {"label" "c"}) (ExpectAbsent))
                               (RowWrite "tickets" #("g1" "t1") (FrozenMap {"owner" "o1"}) (ExpectAbsent)))
                             :idempotency-key "w-batch")))
  (<- (require-law (and (isinstance again Written) (is again.replayed True)
                        (= #(again.version again.value again.position) #(born.version born.value born.position)))
                   law (.format "ExpectAbsent の送り直しが前の答えでない: {!r}(前 {!r})" again born)))
  (<- (require-law (and (isinstance blind-again Written) (is blind-again.replayed True)
                        (= #(blind-again.version blind-again.value blind-again.position) #(blind.version blind.value blind.position)))
                   law (.format "ExpectAny の送り直しが前の答えでない: {!r}(前 {!r})" blind-again blind)))
  (<- (require-law (and (isinstance batch-again WrittenRows) (is batch-again.replayed True)
                        (= (lfor item batch-again.items #(item.version item.value item.position item.replayed))
                           (lfor item batch.items #(item.version item.value item.position True))))
                   law (.format "束の送り直しが前の答えでない: {!r}(前 {!r})" batch-again batch)))
  ;; 同じ鍵の別の本文は冪等キーの断り。
  (<- other (as-writer harness MAKER (PutRow "parts" #("p1") (FrozenMap {"label" "z"}) (ExpectAbsent) :idempotency-key "w-born")))
  (<- other-batch (as-writer harness MAKER
                    (PutRows #((RowWrite "parts" #("p3") (FrozenMap {"label" "z"}) (ExpectAbsent)))
                             :idempotency-key "w-batch")))
  (<- (require-law (and (isinstance other Refused) (in "冪等キー" other.reason)
                        (isinstance other-batch RowsRefused) (= #(other-batch.index other-batch.table other-batch.key) #(0 "parts" #("p3")))
                        (in "冪等キー" other-batch.reason))
                   law (.format "同じ鍵の別の本文が断られない: {!r} {!r}" other other-batch)))
  ;; 送り直しと断りは行を変えず、変更の列に何も積まない。
  (<- p1 (as-writer harness MAKER (ReadRow "parts" #("p1"))))
  (<- p2 (as-writer harness MAKER (ReadRow "parts" #("p2"))))
  (<- p3 (as-writer harness MAKER (ReadRow "parts" #("p3"))))
  (<- (require-law (and (isinstance p1 Row) (= p1.version 2) (= (get p1.value "color") "red")
                        (isinstance p2 Row) (= p2.version 2) (= (get p2.value "color") "green")
                        (isinstance p3 Row) (= p3.version 2) (= (get p3.value "color") "red"))
                   law (.format "送り直しが行を変えた: {!r} {!r} {!r}" p1 p2 p3)))
  (<- after (as-writer harness MAKER (WatchChanges #("parts" "tickets") (WatchCursor mark.epoch mark.sequence))))
  (<- (require-law (and (isinstance after Changes) (= after.items #())) law (.format "送り直しが変更を積んだ: {!r}" after)))
  (! (unplaced-all [born moved blind painted batch moved-batch again blind-again batch-again other other-batch p1 p2 p3 after])))


(defk law-another-key-is-judged-by-its-expectation [harness]
  {:pre [(: harness LawHarness)] :post [(: % (get list object))]
   :tags {:context "records" :role "program"}}
  "冪等キーが書きの身元で、値の一致ではないことを確かめる法: 前の書きと同じ値でも、別の鍵の書きと鍵の無い書きは今までどおり期待で判じる
   (在る行への ExpectAbsent・古い版の ExpectVersion は Conflict)— 別の書き手の同じ値の行を、自分の書きと読まない。"
  (val law "別の鍵と鍵の無い書きは、同じ値でも期待で判じる")
  (<- first (as-writer harness MAKER (PutRow "parts" #("p1") (FrozenMap {"label" "a"}) (ExpectAbsent) :idempotency-key "k-first")))
  (<- (require-law (and (isinstance first Written) (= first.version 1)) law (.format "最初の書き: {!r}" first)))
  (val now (Row #("p1") (FrozenMap {"id" "p1" "label" "a" "state" "open"}) 1))
  (<- other-key (as-writer harness MAKER (PutRow "parts" #("p1") (FrozenMap {"label" "a"}) (ExpectAbsent) :idempotency-key "k-other")))
  (<- keyless (as-writer harness MAKER (PutRow "parts" #("p1") (FrozenMap {"label" "a"}) (ExpectAbsent))))
  (<- (require-law (and (= other-key (Conflict now)) (= keyless (Conflict now))) law
                   (.format "別の鍵・鍵の無い同じ値の ExpectAbsent: {!r} {!r}" other-key keyless)))
  (<- second (as-writer harness MAKER (PutRow "parts" #("p1") (FrozenMap {"label" "b"}) (ExpectVersion 1) :idempotency-key "k-second")))
  (val later (Row #("p1") (FrozenMap {"id" "p1" "label" "b" "state" "open"}) 2))
  (<- stale (as-writer harness MAKER (PutRow "parts" #("p1") (FrozenMap {"label" "b"}) (ExpectVersion 1) :idempotency-key "k-stale")))
  (<- stale-batch (as-writer harness MAKER (PutRows #((RowWrite "parts" #("p1") (FrozenMap {"label" "b"}) (ExpectVersion 1)))
                                                    :idempotency-key "k-stale-batch")))
  (<- (require-law (and (isinstance second Written) (= stale (Conflict later)) (= stale-batch (RowsConflict 0 "parts" #("p1") later)))
                   law (.format "別の鍵の同じ値の古い版の書き: {!r} {!r} {!r}" second stale stale-batch)))
  ;; 衝突した書きは覚えない — 同じ鍵の送り直しも今の行で判じる(書いていない書きに答えを作らない)。
  (<- stale-again (as-writer harness MAKER (PutRow "parts" #("p1") (FrozenMap {"label" "b"}) (ExpectVersion 2) :idempotency-key "k-stale")))
  (<- (require-law (and (isinstance stale-again Written) (= stale-again.version 3) (is stale-again.replayed False)) law
                   (.format "衝突の後の同じ鍵の書きが今の行で判じられない: {!r}" stale-again)))
  (! (unplaced-all [first other-key keyless second stale stale-batch stale-again])))


(defk law-write-keys-are-forgotten-after-their-memory [harness]
  {:pre [(: harness LawHarness)] :post [(: % (get list object))]
   :tags {:context "records" :role "program"}}
  "行の書きの冪等キーの覚えが際限なく伸びないための法: 置き場は鍵の答えを覚えた刻から WRITE-KEY-MEMORY-SECONDS の間は送り直しに前の
   答えを返し(その間の回収 SweepExpired は外さない)、過ぎた後の回収が覚えを外す(答え Swept.write-keys)— 外れた鍵の書きは新しい書きとして
   期待で判じる。"
  (val law "行の書きの冪等キーの覚えは、決まった期間の後の回収で外れる")
  (<- first (as-writer harness MAKER (PutRow "parts" #("p1") (FrozenMap {"label" "a"}) (ExpectAbsent) :idempotency-key "m-1")))
  (<- (Delay (- WRITE-KEY-MEMORY-SECONDS 1)))
  (<- kept (as-writer harness MAKER (SweepExpired)))
  (<- inside (as-writer harness MAKER (PutRow "parts" #("p1") (FrozenMap {"label" "a"}) (ExpectAbsent) :idempotency-key "m-1")))
  (<- (require-law (and (= kept.write-keys 0) (isinstance inside Written) (is inside.replayed True) (= inside.version first.version))
                   law (.format "期間の内の覚えが外れた: {!r} {!r}" kept inside)))
  (<- (Delay 2))
  (<- forgot (as-writer harness MAKER (SweepExpired)))
  (<- outside (as-writer harness MAKER (PutRow "parts" #("p1") (FrozenMap {"label" "a"}) (ExpectAbsent) :idempotency-key "m-1")))
  (<- (require-law (and (= forgot.write-keys 1) (isinstance outside Conflict)) law
                   (.format "期間の後の覚えが外れない・外れた鍵の書きが期待で判じられない: {!r} {!r}" forgot outside)))
  (! (unplaced-all [first kept inside forgot outside])))


;; 全部の法(名 → 法)。SHARED-LAWS = 時間を進めない法(仮想の時計を持たない組でも回せる・答えの比べに使う)。
;; law-put-rows-is-all-or-nothing・law-append-events-answer-like-appends-in-order・law-read-batch-answers-like-each-read は SHARED-LAWS に
;; 入れない — SHARED-LAWS は前からの 6 つの effect だけで回る法の名簿で、PutRows・AppendEvents・ReadBatch を答えない handler の組(呼び手の系の
;; 写しの handler など)もこの名簿で答えを比べている。
(setv LAWS {"stale-put-conflicts" law-stale-put-conflicts
            "committed-changes-appear-once-in-order" law-committed-changes-appear-once-in-order
            "epoch-change-resets" law-epoch-change-resets
            "undeclared-writes-are-refused" law-undeclared-writes-are-refused
            "transient-rows-expire" law-transient-rows-expire
            "indexed-list-equals-filtered-scan" law-indexed-list-equals-filtered-scan
            "descending-list-is-the-head-of-the-sorted-scan" law-descending-list-is-the-head-of-the-sorted-scan
            "descending-pages-follow-the-sorted-scan" law-descending-pages-follow-the-sorted-scan
            "any-of-list-equals-the-union-of-equal-lists" law-any-of-list-equals-the-union-of-equal-lists
            "append-is-idempotent" law-append-is-idempotent
            "append-events-answer-like-appends-in-order" law-append-events-answer-like-appends-in-order
            "read-batch-answers-like-each-read" law-read-batch-answers-like-each-read
            "key-head-read-equals-filtered-scan" law-key-head-read-equals-filtered-scan
            "watch-waits-for-a-change" law-watch-waits-for-a-change
            "watch-events-waits-for-an-append" law-watch-events-waits-for-an-append
            "concurrent-writes-appear-in-number-order" law-concurrent-writes-appear-in-number-order
            "none-removes-a-field" law-none-removes-a-field
            "maintenance-prunes-and-sweeps" law-maintenance-prunes-and-sweeps
            "prune-stops-at-the-archived-seq" law-prune-stops-at-the-archived-seq
            "change-log-reads-every-change" law-change-log-reads-every-change
            "put-rows-is-all-or-nothing" law-put-rows-is-all-or-nothing
            "grouped-events-expire-together" law-grouped-events-expire-together
            "stream-end-is-the-last-sequence" law-stream-end-is-the-last-sequence
            "expired-keys-are-remembered" law-expired-keys-are-remembered
            "expired-records-are-unseen-before-a-sweep" law-expired-records-are-unseen-before-a-sweep
            "a-write-clears-the-expired-row-it-touches" law-a-write-clears-the-expired-row-it-touches
            "an-expired-key-answers-the-same-before-and-after-a-sweep" law-an-expired-key-answers-the-same-before-and-after-a-sweep
            "watch-tails-match-last-events" law-watch-tails-match-last-events
            "event-by-key-reads-the-same-event" law-event-by-key-reads-the-same-event
            "short-page-ends-the-stream" law-short-page-ends-the-stream
            "only-confirmed-events-are-retired" law-only-confirmed-events-are-retired
            "retirement-keeps-the-read-boundary" law-retirement-keeps-the-read-boundary
            "a-write-keeps-the-unarchived-event-it-touches" law-a-write-keeps-the-unarchived-event-it-touches
            "keys-are-forgotten-after-their-memory" law-keys-are-forgotten-after-their-memory
            "unarchived-keys-are-kept-until-retired" law-unarchived-keys-are-kept-until-retired
            "a-write-names-its-change-position" law-a-write-names-its-change-position
            "a-keyed-write-replays-its-answer" law-a-keyed-write-replays-its-answer
            "another-key-is-judged-by-its-expectation" law-another-key-is-judged-by-its-expectation
            "write-keys-are-forgotten-after-their-memory" law-write-keys-are-forgotten-after-their-memory})
(setv SHARED-LAWS #("stale-put-conflicts" "committed-changes-appear-once-in-order" "epoch-change-resets"
                    "undeclared-writes-are-refused" "indexed-list-equals-filtered-scan" "append-is-idempotent" "none-removes-a-field"))
