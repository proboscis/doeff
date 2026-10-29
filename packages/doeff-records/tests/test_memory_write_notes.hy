;; memory の置き場の書きの控え(memory.hy の頭の註の「書きの控え」— card ki-3876dadb767c)の検。置き場の外の読み手は、変更の列と出来事の列に
;; 控えを添えて、誰がどの欄をどの順に書いたかを置き場の持ち主の側から読む。
;;   行の書きの変更 1 つごとに、書き手の名と判定にかけた差分の欄(宣言の外で値が None の欄は落とす)を控える — 変更の値(差分を重ねた後の
;;   行の全部)からは書いた欄が分からない
;;   PutRows の束は行ごとに控える・断られた書きと送り直しは控えない・保持の刈りの RowRemoved は控えない
;;   積んだ出来事 1 つごとに、積んだ時の変更の番号の頭を控える(送り直しは控えない)
;;   控えは控えた物と一緒に置き場を去る(変更の刈り・版の更新・出来事の回収)・置き場の写しは控えを写す
(require doeff-hy.macros [deftest defk <- val])
(import copy)
(import dataclasses)
(import doeff [run with_handlers Program])
(import doeff_core_effects [scheduled])
(import doeff_time [SimClock sim-time-handler Delay])
(import doeff_hy._frozen [FrozenMap])
(import doeff_records [ExpectAbsent ExpectAny RowChanged RowRemoved StreamDecl KeepFor TableDecl FieldDecl Written Conflict Refused Unreachable])
(import doeff_records [PutRow PutRows RowWrite AppendEvent AdvanceStoreEpoch])
(import doeff_records.maintenance [SweepExpired PruneChanges])
(import doeff_records [memory-records-handler]
        doeff_records._memory [MemoryStore WriteNote])
(import doeff_records._laws [LAW-SCHEMA MAKER PAINTER CLOSER])

;; 期限つきの列 pulses(60 秒)と、終端の状態を持ち保持の期限で刈られる表 leases(60 秒)を足した宣言。
(val KEEP-SECONDS 60)
(val SCHEMA
  (dataclasses.replace LAW-SCHEMA
    :tables (FrozenMap (| (dict LAW-SCHEMA.tables)
                          {"leases" (TableDecl :name "leases" :key-fields #("id")
                                               :fields #((FieldDecl "id" #(MAKER)) (FieldDecl "state" #(MAKER)))
                                               :states #("held" "freed") :initial "held" :terminal #("freed")
                                               :retention (KeepFor KEEP-SECONDS))}))
    :streams (FrozenMap (| (dict LAW-SCHEMA.streams)
                           {"pulses" (StreamDecl :name "pulses" :writers #(MAKER) :retention (KeepFor KEEP-SECONDS))}))))


(defn #^ object as-writer [#^ MemoryStore store #^ str writer #^ Program program]  ; defk にできない: 検の入口で run を撃つ
  "仮想の時計と、書き手 writer の memory の置き場 store の handler の下で program を回して答えを返すため。"
  (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) (memory-records-handler store writer)] program))))


(defk put [table key value [expect None] [idempotency-key None]]
  {:pre [(: table str) (: key tuple) (: value dict) (: expect (| ExpectAbsent ExpectAny None)) (: idempotency-key (| str None))]
   :post [(: % (| Written Conflict Refused Unreachable))]}
  "表 table の行 key に差分 value を書く書きを撃つため(期待の既定は ExpectAny)。"
  (<- answer (PutRow table key (FrozenMap value) (if (is expect None) (ExpectAny) expect) :idempotency-key idempotency-key))
  answer)


(deftest test-a-row-write-notes-its-writer-and-the-fields-it-wrote
  ;; 作る書き(MAKER)と色だけの書き(PAINTER — 宣言の外の欄 nope の None は判定で落ちる)。2 つ目の変更の値は行の全部を持つが、控えは
  ;; PAINTER が書いた欄 color だけ。
  (val store (MemoryStore SCHEMA))
  (as-writer store MAKER (put "parts" #("p1") {"id" "p1" "label" "a" "state" "open"} (ExpectAbsent)))
  (as-writer store PAINTER (put "parts" #("p1") {"color" "red" "nope" None}))
  (assert (= (lfor change store.changes change.sequence) [1 2]) store.changes)
  (assert (= (. (get store.changes 1) value) (FrozenMap {"id" "p1" "label" "a" "state" "open" "color" "red"})) store.changes)
  (assert (= store.written-by {1 (WriteNote :writer MAKER :fields #("id" "label" "state"))
                               2 (WriteNote :writer PAINTER :fields #("color"))})
          store.written-by))


(defk batch-then-refused-then-resent []
  {:pre [] :post [(: % tuple)]}
  "束の書き 2 行・宣言の外の欄の書き(断られる)・鍵つきの書きとその送り直しを続けて撃つため。答え = 断りと送り直しの答え。"
  (<- (PutRows #((RowWrite "parts" #("p1") (FrozenMap {"id" "p1" "label" "a"}) (ExpectAbsent))
                 (RowWrite "tickets" #("g" "t1") (FrozenMap {"group" "g" "id" "t1" "owner" "o"}) (ExpectAbsent)))))
  (<- refused (put "parts" #("p1") {"ghost" 1}))
  (<- first (put "parts" #("p1") {"note" "n"} :idempotency-key "w-1"))
  (<- again (put "parts" #("p1") {"note" "n"} :idempotency-key "w-1"))
  #(refused first again))


(deftest test-a-batch-notes-each-row-and-refused-and-resent-writes-note-nothing
  (val store (MemoryStore SCHEMA))
  (val answers (as-writer store CLOSER (batch-then-refused-then-resent)))
  (assert (isinstance (get answers 0) Refused) answers)
  (assert (and (not (. (get answers 1) replayed)) (. (get answers 2) replayed)) answers)
  (assert (= store.written-by {1 (WriteNote :writer CLOSER :fields #("id" "label"))
                               2 (WriteNote :writer CLOSER :fields #("group" "id" "owner"))
                               3 (WriteNote :writer CLOSER :fields #("note"))})
          store.written-by))


(defk writes-between-appends []
  {:pre [] :post [(: % tuple)]}
  "追記・行の書き 2 つ・追記・同じ追記の送り直しを続けて撃つため。答え = 3 つの追記の答え。"
  (<- before (AppendEvent "journal" "k1" {"n" 1}))
  (<- (put "parts" #("p1") {"id" "p1"} (ExpectAbsent)))
  (<- (put "parts" #("p2") {"id" "p2"} (ExpectAbsent)))
  (<- after (AppendEvent "journal" "k2" {"n" 2}))
  (<- resent (AppendEvent "journal" "k2" {"n" 2}))
  #(before after resent))


(deftest test-an-append-notes-the-change-head-it-landed-after
  ;; 変更と出来事は別の番号で数える — 控えの頭で、出来事 1 は変更の前・出来事 2 は変更 2 つの後と分かる。送り直しは出来事も控えも増やさない。
  (val store (MemoryStore SCHEMA))
  (val appended (as-writer store MAKER (writes-between-appends)))
  (assert (= (tuple (gfor a appended a.sequence)) #(1 2 2)) appended)
  (assert (= (tuple (gfor a appended a.replayed)) #(False False True)) appended)
  (assert (= store.appended-at-head {1 0 2 2}) store.appended-at-head)
  (assert (= (lfor event store.events event.writer) [MAKER MAKER]) store.events))


(defk expire-then-sweep []
  {:pre [] :post [(: % None)]}
  "終端の行 1 つと期限つきの出来事 1 つを置き、期限の後に回収(SweepExpired)を撃つため。"
  (<- (put "leases" #("l1") {"id" "l1" "state" "freed"} (ExpectAbsent)))
  (<- (AppendEvent "pulses" "p1" {"n" 1}))
  (<- (Delay (+ KEEP-SECONDS 1)))
  (<- (SweepExpired))
  None)


(deftest test-the-notes-leave-with-what-they-note
  ;; 回収は出来事の控えを外し、刈った行の RowRemoved は控えない(書き手を持たない)。変更の刈りは刈った変更の控えを、版の更新は全部の
  ;; 変更の控えを外す。置き場の写しは控えを写す(写しへの書きは元の控えに届かない)。
  (val store (MemoryStore SCHEMA))
  (as-writer store MAKER (expire-then-sweep))
  (assert (= (lfor change store.changes (type change)) [RowChanged RowRemoved]) store.changes)
  (assert (= (sorted store.written-by) [1]) store.written-by)
  (assert (= store.appended-at-head {}) store.appended-at-head)
  (val copied (copy.deepcopy store))
  (as-writer copied MAKER (put "parts" #("p1") {"id" "p1"} (ExpectAbsent)))
  (assert (= (sorted copied.written-by) [1 3]) copied.written-by)
  (assert (= (sorted store.written-by) [1]) store.written-by)
  (as-writer copied MAKER (PruneChanges 0.0 :upto-seq 1))
  (assert (= (sorted copied.written-by) [3]) copied.written-by)
  (as-writer copied MAKER (AdvanceStoreEpoch))
  (assert (= copied.written-by {}) copied.written-by))
