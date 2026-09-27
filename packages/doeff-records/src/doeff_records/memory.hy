;;; memory の handler — 公開 effect 7 つに手元の表と番号の列で答える(模擬環境・手元の 1 process・単体の検)。
;;; 行の値は凍らせた写像なので、答えに出す Row は置き場の Row そのもの(写し取らなくても呼び手は変えられない)。
;;;
;;; 判断(期待・書きの許可・保持・索引・頁)は admission.hy の純関数ちょうど 1 つ。ここは置き場の data と番号の採り方だけを持つ。
;;; 時刻は doeff-time の GetTime(仮想の時計の下では保持の期限も一瞬で来る)、WatchChanges の待ちは Delay。
;;; 書き手の身元は handler を組む時の引数 writer(effect の欄にしない)。同じ MemoryStore を別の writer の handler で包めば、
;;; 1 つの置き場を複数の書き手が使う形になる。
;;;
;;; 置き場は thread の間で共有してよい(書き手の thread と実況の読みの thread が同じ MemoryStore を使う — この系の Python は GIL の無い
;;; free-threaded)。置き場の不変条件(列・番号・索引)の持ち主は MemoryStore なので、錠(MemoryStore.lock・RLock)も置き場が持ち、
;;; handler の各節は「保持の刈り(purge-expired)と操作」の組を錠の内で 1 つずつ行う(guarded)。WatchChanges の待ち(Delay で眠る間)は
;;; 錠を持たない — 走査の 1 回だけを錠の内にする(持ったまま眠ると他の書きが止まる)。
(require doeff-hy.macros [defhandler defk <- val])
(import threading)
(import collections.abc [Callable])
(import doeff [EffectBase Program Pure])
(import doeff_time [GetTime])
(import doeff_records.watching [wait-for-changes])
(import doeff_hy.frozen [FrozenMap])
(import doeff_records.values [KeepFor RecordsSchema Row Missing Page Written WrittenRows RowChanged RowRemoved Changes Appended
                              Event Events Reset WatchCursor ListCursor Refused RowsConflict RowsRefused Unreachable
                              Conflict NotIndexed])
(import doeff_records.effects [ReadRow ListRows PutRow PutRows WatchChanges AppendEvent ReadEvents])
(import doeff_records.faults [AdvanceStoreEpoch SetStoreOutage StoreFault StoreOperation AddStoreFault ClearStoreFaults])
(import doeff_records.maintenance [SweepExpired PruneChanges Swept Pruned])
(import doeff_records.admission [Admitted AppendNew AppendReplay judge-expect judge-put judge-put-rows judge-append row-expired?
                                 event-expired? retention-group-of where-refusal row-matches? listed-row key-text next-watch-sequence
                                 epoch-ms terminal-row?])

(setv DEFAULT-POLL-SECONDS 0.05)


(defclass StoredRow []
  "置き場の行 1 つ: row = 答えに出す Row / updated-ms = 最後に書かれた刻。"
  (defn #^ None __init__ [self #^ Row row #^ int updated-ms]
    (setv self.row row self.updated-ms updated-ms)))


(defclass MemoryStore []
  "memory の置き場: schema = 宣言(operator の欄を書ける主体の一覧 operators を含む)/ poll-seconds = WatchChanges が変更を待つ間の眠りの刻み /
   lock = 置き場を読み書きする操作を 1 つずつにする錠(thread の間で置き場を共有するため — 同じ thread の入れ子は通す RLock)。"
  (defn #^ None __init__ [self #^ RecordsSchema schema * #^ float [poll-seconds DEFAULT-POLL-SECONDS]]
    (setv self.schema schema
          self.lock (threading.RLock)
          self.poll-seconds poll-seconds
          self.epoch 1
          self.floor 0
          self.head 0
          self.rows (dfor name schema.tables name {})
          self.changes []
          ;; 変更の番号 → 積んだ刻(epoch ミリ秒)— 刈り取り(PruneChanges)が古さを測るため。
          self.changed-at {}
          self.event-head 0
          self.events []
          self.by-idempotency {}
          ;; 届かない状態(検の口 faults.SetStoreOutage の値 — None = 届く)。
          self.outage None
          ;; 置いた故障の列(検の口 faults.AddStoreFault の値 — 置いた順・空 = 故障なし)。
          self.faults #()
          ;; 保持の期限で何かが消え得る最も早い刻(epoch ミリ秒・None = 消え得る物が無い)— purge-expired はこの刻より前なら走査しない。
          self.purge-due-ms None))
  ;; 錠は置き場の中身ではなく、この process の thread の間の取り決め — pickle と copy は錠を除いた中身だけを運び、戻した側で新しい錠を
  ;; 作る(置き場を含む値を pickle する使い手 — worker の結果の file・coordinator の状態 — を錠の導入で壊さないため)。
  (defn #^ dict __getstate__ [self]
    "pickle / copy が運ぶ中身を返すため(錠を除く)。"
    (dfor [name value] (.items self.__dict__) :if (!= name "lock") name value))
  (defn #^ None __setstate__ [self #^ dict state]
    "pickle / copy から戻す時に中身を入れ、新しい錠を作るため。"
    (.update self.__dict__ state)
    ;; 消え得る刻を持つ前に pickle した置き場は、次の操作で 1 度走査して刻を数え直す(0 = もう来ている)。
    (when (not-in "purge_due_ms" state)
      (setv self.purge-due-ms 0))
    ;; 故障の列を持つ前に pickle した置き場は故障なし。
    (when (not-in "faults" state)
      (setv self.faults #()))
    (setv self.lock (threading.RLock))))


;; --- 保持 ------------------------------------------------------------------------------------------------

(defn #^ object guarded [#^ MemoryStore store #^ Callable operation]  ; defk にできない: 錠の内で同期に置き場を触る関数を 1 つ呼ぶ(pg.hy の guarded と同じ役)
  "operation(引数なしの関数)を置き場の錠の内で呼ぶ — 保持の刈りと操作の組を、他の thread の操作と重ねない。"
  (with [store.lock] (operation)))


(defn #^ int purge-expired [#^ MemoryStore store #^ int now-ms]
  "保持の期限を過ぎた行を消して RowRemoved を積み、期限を過ぎた出来事を捨てる(どの操作の前にも呼ぶ — 読みに期限切れが見えない)。
   答え = 消した行の数。錠の内で走る(単独で呼ばれても — RLock なので guarded の内からの入れ子も通る)。"
  (with [store.lock] (purge-expired-locked store now-ms)))


(defn #^ int keep-ms [#^ KeepFor retention]
  "保持の秒をミリ秒へ(row-expired? / event-expired? と同じ丸め)。"
  (int (* 1000 retention.seconds)))


(defn #^ None note-purge-due [#^ MemoryStore store #^ int due-ms]  ; defk にできない: 錠の内で同期に呼ぶ置き場の書き
  "行か出来事を書いた時、それが消え得る刻で置き場の purge-due-ms を早める(遅くはしない — 下限のまま正しい)。"
  (when (or (is store.purge-due-ms None) (< due-ms store.purge-due-ms))
    (setv store.purge-due-ms due-ms)))


(defn #^ dict event-group-at [#^ MemoryStore store]  ; defk にできない: 錠の内で同期に読む置き場の走査
  "組で数える列(ByKeySuffix)の組ごとの、組の最後の出来事を積んだ刻(#(列 組) → epoch ミリ秒)。"
  (setv group-at {})
  (for [event store.events]
    (setv group (retention-group-of (store.schema.stream event.stream) event.idempotency-key))
    (when (is-not group None)
      (setv (get group-at #(event.stream group)) (max event.at (.get group-at #(event.stream group) event.at)))))
  group-at)


(defn #^ (| int None) next-purge-due [#^ MemoryStore store]  ; defk にできない: 錠の内で同期に読む置き場の走査
  "残っている行と出来事のうち、最も早く消える物の刻(row-expired? / event-expired? が真になる最初の刻)。無ければ None。
   終端でない行は数えない — 終端になる書きの時に note-purge-due が刻を足す。"
  (setv dues [])
  (for [#(name decl) (.items store.schema.tables) :if (isinstance decl.retention KeepFor)]
    (for [stored (.values (get store.rows name)) :if (terminal-row? decl stored.row.value)]
      (.append dues (+ stored.updated-ms (keep-ms decl.retention)))))
  (setv group-at (event-group-at store))
  (for [event store.events]
    (setv decl (store.schema.stream event.stream))
    (when (isinstance decl.retention KeepFor)
      (setv group (retention-group-of decl event.idempotency-key))
      (.append dues (+ (if (is group None) event.at (get group-at #(event.stream group))) (keep-ms decl.retention)))))
  (min dues :default None))


(defn #^ int purge-expired-locked [#^ MemoryStore store #^ int now-ms]  ; defk にできない: purge-expired が錠の内で同期に呼ぶ置き場の書き
  "purge-expired の中身(呼び手が錠を持つ)。消え得る刻(purge-due-ms)より前なら走査しない — 操作ごとに全部の行と出来事を読み直すと、
   出来事の数の 2 乗で遅くなる(2026-09-27 の実測: 出来事 1 万の筋書き 1 つが 5 分を越えた)。走査した後は刻を数え直す。"
  (when (or (is store.purge-due-ms None) (< now-ms store.purge-due-ms))
    (return 0))
  (setv removed (purge-expired-scan store now-ms))
  (setv store.purge-due-ms (next-purge-due store))
  removed)


(defn #^ int purge-expired-scan [#^ MemoryStore store #^ int now-ms]  ; defk にできない: purge-expired-locked が錠の内で同期に呼ぶ置き場の書き
  "期限を過ぎた行と出来事を全部消す走査。期限切れの出来事が 1 つも無ければ列を差し替えない。"
  (setv removed 0)
  ;; 期限を持つ(KeepFor の)表と列だけを走査する — 期限の無い置き場で操作ごとに全部の行と出来事を読み直すと、出来事の数の 2 乗で
  ;; 遅くなる(2026-09-26 の実測: 出来事 1 万で 1 筋書きが 1 分を越えた)。
  (for [#(name decl) (sorted (.items store.schema.tables)) :if (isinstance decl.retention KeepFor)]
    (setv table (get store.rows name))
    (for [text (sorted (lfor #(text stored) (.items table)
                             :if (row-expired? decl stored.row.value stored.updated-ms now-ms)
                             text))]
      (setv stored (.pop table text))
      (+= store.head 1)
      (+= removed 1)
      (setv (get store.changed-at store.head) now-ms)
      (.append store.changes (RowRemoved name stored.row.key store.head))))
  (when (not (any (gfor s (.values store.schema.streams) (isinstance s.retention KeepFor))))
    (return removed))
  ;; 組で数える列(ByKeySuffix)は、組の最後の出来事を積んだ刻から数える — 組の出来事は同時に消える。
  (setv group-at (event-group-at store))
  (setv expired (lfor event store.events
                      :setv decl (store.schema.stream event.stream)
                      :setv group (retention-group-of decl event.idempotency-key)
                      :if (event-expired? decl (if (is group None) event.at (get group-at #(event.stream group))) now-ms)
                      event))
  (when (not expired)
    (return removed))
  (for [event expired]
    (.pop store.by-idempotency #(event.stream event.idempotency-key) None))
  (setv gone (set (gfor event expired event.sequence)))
  (setv store.events (lfor event store.events :if (not-in event.sequence gone) event))
  removed)


;; --- 行 --------------------------------------------------------------------------------------------------

(defn #^ (| Row Missing) memory-read-row [#^ MemoryStore store #^ ReadRow ask]
  (store.schema.table ask.table)
  (setv stored (.get (get store.rows ask.table) (key-text ask.key)))
  (if (is stored None) (Missing) stored.row))


(defn #^ object memory-list-rows [#^ MemoryStore store #^ ListRows ask]
  (setv decl (store.schema.table ask.table))
  (when (and (is-not ask.cursor None) (!= ask.cursor.epoch store.epoch))
    (return (Reset store.epoch store.floor)))
  (setv refusal (where-refusal decl ask.where))
  (when refusal (return refusal))
  (setv after (if (is ask.cursor None) None ask.cursor.after-key)
        table (get store.rows ask.table)
        matching (lfor text (sorted table)
                       :if (and (or (is after None) (> text after)) (row-matches? ask.where (. (get table text) row value)))
                       text)
        taken (cut matching ask.limit)
        rows (tuple (gfor text taken (listed-row decl ask.fields (. (get table text) row)))))
  (Page rows
        (if (> (len matching) ask.limit) (ListCursor store.epoch (get taken -1)) None)
        store.epoch
        store.head))


(defn #^ (| Row None) memory-current-row [#^ MemoryStore store #^ str table #^ tuple key]  ; defk にできない: 同期の置き場の書き(memory-put-row / memory-put-rows)が呼ぶ読み
  "書きの判定に渡す今の行(無ければ None)を置き場から引く。"
  (setv stored (.get (get store.rows table) (key-text key)))
  (if (is stored None) None stored.row))


(defn #^ Written memory-store-row [#^ MemoryStore store #^ str table #^ tuple key #^ (| Row None) current #^ FrozenMap value
                                   #^ int now-ms]  ; defk にできない: 同期の置き場の書き(memory-put-row / memory-put-rows)が呼ぶ置き場の更新
  "判定を通った 1 行を置き場に書き、変更の列に 1 つ積む(PutRow と PutRows の書きを 1 つにし、版と番号の採り方を揃えるため)。"
  (setv version (if (is current None) 1 (+ current.version 1)))
  (setv (get (get store.rows table) (key-text key)) (StoredRow (Row key value version) now-ms))
  (+= store.head 1)
  (setv (get store.changed-at store.head) now-ms)
  (.append store.changes (RowChanged table key version value store.head now-ms))
  (setv decl (store.schema.table table))
  (when (and (isinstance decl.retention KeepFor) (terminal-row? decl value))
    (note-purge-due store (+ now-ms (keep-ms decl.retention))))
  (Written version value))


(defn #^ object memory-put-row [#^ MemoryStore store #^ str writer #^ PutRow ask #^ int now-ms]
  (setv decl (store.schema.table ask.table)
        current (memory-current-row store ask.table ask.key))
  (setv conflict (judge-expect ask.expect current))
  (when conflict (return conflict))
  (setv verdict (judge-put decl writer current ask.key ask.value :operators store.schema.operators))
  (when (isinstance verdict Refused) (return verdict))
  (memory-store-row store ask.table ask.key current verdict.value now-ms))


(defn #^ (| WrittenRows RowsConflict RowsRefused) memory-put-rows [#^ MemoryStore store #^ str writer #^ PutRows ask #^ int now-ms]  ; defk にできない: memory の handler が同期に呼ぶ置き場の書き(memory-put-row と同じ作法)
  "PutRows の束を全部か 0 で書く: 全部の行の判定(admission.judge-put-rows)が通った時だけ、束の順に 1 行ずつ書いて変更を積む。"
  (for [write ask.writes] (store.schema.table write.table))
  (setv currents (tuple (gfor write ask.writes (memory-current-row store write.table write.key)))
        verdict (judge-put-rows store.schema writer ask.writes currents))
  (when (not (isinstance verdict tuple)) (return verdict))
  (WrittenRows (tuple (gfor #(write current admitted) (zip ask.writes currents verdict :strict True)
                            (memory-store-row store write.table write.key current admitted.value now-ms)))))


(defn #^ object memory-watch-scan [#^ MemoryStore store #^ WatchChanges ask]
  "今ある変更から 1 回ぶんの答え(待たない)。位置が今の版の外なら Reset。"
  (for [name ask.tables] (store.schema.table name))
  (setv cursor ask.cursor)
  (when (or (!= cursor.epoch store.epoch) (< cursor.sequence store.floor) (> cursor.sequence store.head))
    (return (Reset store.epoch store.floor)))
  (setv items (tuple (cut (lfor change store.changes
                                :if (and (> change.sequence cursor.sequence) (in change.table ask.tables))
                                change)
                          ask.limit)))
  (Changes items (WatchCursor store.epoch (next-watch-sequence items ask.limit store.head))))


;; --- 追記の列 --------------------------------------------------------------------------------------------

(defn #^ object memory-append [#^ MemoryStore store #^ str writer #^ AppendEvent ask #^ int now-ms]
  (setv decl (store.schema.stream ask.stream)
        slot #(ask.stream ask.idempotency-key)
        verdict (judge-append decl writer ask.body (.get store.by-idempotency slot)))
  (cond
    (isinstance verdict Refused) verdict
    (isinstance verdict AppendReplay) (Appended verdict.sequence)
    True (do (+= store.event-head 1)
             (setv event (Event ask.stream store.event-head ask.idempotency-key ask.body writer now-ms))
             (.append store.events event)
             (setv (get store.by-idempotency slot) event)
             (when (isinstance decl.retention KeepFor)
               (note-purge-due store (+ now-ms (keep-ms decl.retention))))
             (Appended event.sequence))))


(defn #^ Events memory-read-events [#^ MemoryStore store #^ ReadEvents ask]
  (store.schema.stream ask.stream)
  (setv items (tuple (cut (lfor event store.events
                                :if (and (= event.stream ask.stream) (> event.sequence ask.after))
                                event)
                          ask.limit)))
  (Events items (if items (. (get items -1) sequence) ask.after)))


(defn #^ int memory-advance-epoch [#^ MemoryStore store]
  (with [store.lock]
    (+= store.epoch 1)
    (setv store.floor store.head
          store.changes []
          store.changed-at {})
    store.epoch))


;; --- 手入れ ------------------------------------------------------------------------------------------------

(defk memory-prune-changes [store ask now-ms]
  {:pre [(: store MemoryStore) (: ask PruneChanges) (: now-ms int)] :post [(: % Pruned)]}
  "変更の列が際限なく伸びないように、keep-seconds より古い変更(刻 <= 今 − 保持)を消して floor を上げる。"
  (guarded store (fn [] (prune-changes-locked store ask now-ms))))


(defn #^ Pruned prune-changes-locked [#^ MemoryStore store #^ PruneChanges ask #^ int now-ms]  ; defk にできない: 錠の内で同期に呼ぶ置き場の書き
  "memory-prune-changes の中身(呼び手が錠を持つ)。"
  (setv before (- now-ms (int (* 1000 ask.keep-seconds)))
        ;; 刈る範囲は番号の前方の連なり(刻が古い変更の最大の番号まで — PostgreSQL の handler と同じ)。
        edge (max (gfor change store.changes :if (<= (get store.changed-at change.sequence) before) change.sequence) :default 0)
        old (lfor change store.changes :if (<= change.sequence edge) change))
  (for [change old] (del (get store.changed-at change.sequence)))
  (when old (setv store.floor (max store.floor (. (get old -1) sequence))))
  (setv store.changes (lfor change store.changes :if (> change.sequence store.floor) change))
  (Pruned store.floor (len old)))


(defn #^ (| Unreachable None) unreachable-for [#^ MemoryStore store #^ tuple names]  ; defk にできない: handler の節の頭で同期に置き場の状態を読む(guarded と同じ位置)
  "names(effect が触る表と列の名)のどれかが届かない状態なら Unreachable、でなければ None(faults.SetStoreOutage)。"
  (setv outage store.outage)
  (if (and (is-not outage None) (or (is outage.names None) (any (gfor name names (in name outage.names)))))
      (Unreachable outage.detail)
      None))


;; --- 故障(検の口 faults.AddStoreFault / ClearStoreFaults)----------------------------------------------

(defn #^ None memory-add-fault [#^ MemoryStore store #^ StoreFault fault]  ; defk にできない: handler の節と、doeff の実行の外で同期に筋書きを組む使い手(検の世界の支度)が呼ぶ置き場の書き
  "故障を 1 つ置き場の故障の列の末尾に置く(AddStoreFault の節と同じ書き)。"
  (when (not (isinstance fault StoreFault))
    (raise (TypeError (.format "置けるのは StoreFault: {!r}" fault))))
  (with [store.lock]
    (setv store.faults (+ store.faults #(fault))))
  None)


(defn #^ None memory-clear-faults [#^ MemoryStore store #^ (| frozenset None) names]  ; defk にできない: handler の節と、doeff の実行の外で同期に筋書きを組む使い手が呼ぶ置き場の書き
  "names と名が 1 つでも重なる故障を外す(None = 全部 — ClearStoreFaults の節と同じ書き)。"
  (with [store.lock]
    (setv store.faults (if (is names None)
                           #()
                           (tuple (gfor fault store.faults :if (not (& fault.names names)) fault)))))
  None)


(defk fault-for [store operation names ask]
  {:pre [(: store MemoryStore) (: operation StoreOperation) (: names tuple) (: ask EffectBase)]
   :post [(: % (| StoreFault None))]
   :tags {:context "records" :role "foundation"}}
  "ask(操作 operation で names の表と列に触る effect)に当たる故障のうち、置いた順で最初の 1 つ(無ければ None)。"
  (for [fault store.faults]
    (when (and (= fault.operation operation)
               (any (gfor name names (in name fault.names)))
               (or (is fault.matching None) (fault.matching ask)))
      (return fault)))
  None)


(defk answered [store operation names ask program]
  {:pre [(: store MemoryStore) (: operation StoreOperation) (: names tuple) (: ask EffectBase) (: program Program)]
   ;; 答え = 公開 effect 7 つの答えの型のどれか(program の答えか、故障の答え Refused / Unreachable)。
   :post [(: % (| Row Missing Page Reset NotIndexed Written Conflict Refused Unreachable WrittenRows RowsConflict RowsRefused
                  Changes Appended Events))]
   :tags {:context "records" :role "foundation"}}
  "公開 effect ask の答え: 届かない状態(SetStoreOutage)が先・次に当たる故障(lands でなければ置き場に触らずに故障の答え・lands なら
   program で置き場に着けてから故障の答え)・どちらも無ければ program の答え。"
  (val down (unreachable-for store names))
  (when down
    (return down))
  (<- fault (fault-for store operation names ask))
  (when (and (is-not fault None) (not fault.lands))
    (return fault.answer))
  (<- answer program)
  (if (is fault None) answer fault.answer))


(defk at-now [store operation]
  {:pre [(: store MemoryStore) (: operation Callable)]
   ;; 答え = 置き場の操作の答え(公開 effect 7 つの答えの型のどれか)。
   :post [(: % (| Row Missing Page Reset NotIndexed Written Conflict Refused Unreachable WrittenRows RowsConflict RowsRefused
                  Changes Appended Events))]
   :tags {:context "records" :role "foundation"}}
  "いまの刻(epoch ミリ秒)を読み、置き場の錠の内で保持の刈りの後に operation(刻 → 答え)を呼ぶ(各節の置き場の操作の 1 つの形)。"
  (<- now (GetTime))
  (val now-ms (epoch-ms now))
  (guarded store (fn [] (purge-expired store now-ms) (operation now-ms))))


;; --- handler ------------------------------------------------------------------------------------------------

(val READ StoreOperation.READ)
(val WRITE StoreOperation.WRITE)


(defhandler memory-records-handler [#^ MemoryStore store #^ str writer]
  ;; 各節の答えは answered の 1 点を通る — 届かない状態(faults.SetStoreOutage)と故障(faults.AddStoreFault)を見てから置き場に触る。
  (ReadRow [table key]
    (<- answer (answered store READ #(table) effect (at-now store (fn [now-ms] (memory-read-row store effect)))))
    (resume answer))
  (ListRows [table where fields cursor limit]
    (<- answer (answered store READ #(table) effect (at-now store (fn [now-ms] (memory-list-rows store effect)))))
    (resume answer))
  (PutRow [table key value expect]
    (<- answer (answered store WRITE #(table) effect (at-now store (fn [now-ms] (memory-put-row store writer effect now-ms)))))
    (resume answer))
  (PutRows [writes]
    (<- answer (answered store WRITE (tuple (gfor w writes w.table)) effect
                         (at-now store (fn [now-ms] (memory-put-rows store writer effect now-ms)))))
    (resume answer))
  (WatchChanges [tables cursor timeout limit]
    (<- answer (answered store READ (tuple tables) effect
                         (wait-for-changes (fn [now-ms] (Pure (guarded store (fn [] (purge-expired store now-ms) (memory-watch-scan store effect)))))
                                           store.poll-seconds timeout)))
    (resume answer))
  (AppendEvent [stream idempotency-key body]
    (<- answer (answered store WRITE #(stream) effect (at-now store (fn [now-ms] (memory-append store writer effect now-ms)))))
    (resume answer))
  (ReadEvents [stream after limit]
    (<- answer (answered store READ #(stream) effect (at-now store (fn [now-ms] (memory-read-events store effect)))))
    (resume answer))
  (SetStoreOutage [detail names]
    (setv store.outage (if (is detail None) None effect))
    (resume None))
  (AddStoreFault [fault]
    (memory-add-fault store fault)
    (resume None))
  (ClearStoreFaults [names]
    (memory-clear-faults store names)
    (resume None))
  (AdvanceStoreEpoch []
    (resume (memory-advance-epoch store)))
  (SweepExpired []
    (<- now (GetTime))
    (resume (Swept (purge-expired store (epoch-ms now)))))
  (PruneChanges [keep-seconds]
    (<- now (GetTime))
    (<- pruned (memory-prune-changes store effect (epoch-ms now)))
    (resume pruned)))
