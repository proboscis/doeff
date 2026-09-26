;;; memory の handler — 公開 effect 7 つに手元の表と番号の列で答える(模擬環境・手元の 1 process・単体の検)。
;;; 行の値は凍らせた写像なので、答えに出す Row は置き場の Row そのもの(写し取らなくても呼び手は変えられない)。
;;;
;;; 判断(期待・書きの許可・保持・索引・頁)は admission.hy の純関数ちょうど 1 つ。ここは置き場の data と番号の採り方だけを持つ。
;;; 時刻は doeff-time の GetTime(仮想の時計の下では保持の期限も一瞬で来る)、WatchChanges の待ちは Delay。
;;; 書き手の身元は handler を組む時の引数 writer(effect の欄にしない)。同じ MemoryStore を別の writer の handler で包めば、
;;; 1 つの置き場を複数の書き手が使う形になる。
(require doeff-hy.macros [defhandler defk <-])
(import doeff [Pure])
(import doeff_time [GetTime])
(import doeff_records.watching [wait-for-changes])
(import doeff_hy.frozen [FrozenMap])
(import doeff_records.values [KeepFor RecordsSchema Row Missing Page Written WrittenRows RowChanged RowRemoved Changes Appended
                              Event Events Reset WatchCursor ListCursor Refused RowsConflict RowsRefused])
(import doeff_records.effects [ReadRow ListRows PutRow PutRows WatchChanges AppendEvent ReadEvents])
(import doeff_records.faults [AdvanceStoreEpoch])
(import doeff_records.maintenance [SweepExpired PruneChanges Swept Pruned])
(import doeff_records.admission [Admitted AppendNew AppendReplay judge-expect judge-put judge-put-rows judge-append row-expired?
                                 event-expired? where-refusal row-matches? listed-row key-text next-watch-sequence
                                 epoch-ms])

(setv DEFAULT-POLL-SECONDS 0.05)


(defclass StoredRow []
  "置き場の行 1 つ: row = 答えに出す Row / updated-ms = 最後に書かれた刻。"
  (defn #^ None __init__ [self #^ Row row #^ int updated-ms]
    (setv self.row row self.updated-ms updated-ms)))


(defclass MemoryStore []
  "memory の置き場: schema = 宣言(operator の欄を書ける主体の一覧 operators を含む)/ poll-seconds = WatchChanges が変更を待つ間の眠りの刻み。"
  (defn #^ None __init__ [self #^ RecordsSchema schema * #^ float [poll-seconds DEFAULT-POLL-SECONDS]]
    (setv self.schema schema
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
          self.by-idempotency {})))


;; --- 保持 ------------------------------------------------------------------------------------------------

(defn #^ int purge-expired [#^ MemoryStore store #^ int now-ms]
  "保持の期限を過ぎた行を消して RowRemoved を積み、期限を過ぎた出来事を捨てる(どの操作の前にも呼ぶ — 読みに期限切れが見えない)。
   答え = 消した行の数。"
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
  (setv kept (lfor event store.events
                   :if (not (event-expired? (store.schema.stream event.stream) event.at now-ms))
                   event))
  (for [event store.events]
    (when (event-expired? (store.schema.stream event.stream) event.at now-ms)
      (.pop store.by-idempotency #(event.stream event.idempotency-key) None)))
  (setv store.events kept)
  removed)


;; --- 行 --------------------------------------------------------------------------------------------------

(defn #^ (| Row Missing) memory-read-row [#^ MemoryStore store #^ ReadRow ask]
  (store.schema.table ask.table)
  (setv stored (.get (get store.rows ask.table) (key-text ask.key)))
  (if (is stored None) (Missing) stored.row))


(defn #^ object memory-list-rows [#^ MemoryStore store #^ ListRows ask]
  (setv decl (store.schema.table ask.table))
  (when (and (is-not ask.cursor None) (!= ask.cursor.epoch store.epoch))
    (return (Reset store.epoch)))
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
  (.append store.changes (RowChanged table key version value store.head))
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
    (return (Reset store.epoch)))
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
             (Appended event.sequence))))


(defn #^ Events memory-read-events [#^ MemoryStore store #^ ReadEvents ask]
  (store.schema.stream ask.stream)
  (setv items (tuple (cut (lfor event store.events
                                :if (and (= event.stream ask.stream) (> event.sequence ask.after))
                                event)
                          ask.limit)))
  (Events items (if items (. (get items -1) sequence) ask.after)))


(defn #^ int memory-advance-epoch [#^ MemoryStore store]
  (+= store.epoch 1)
  (setv store.floor store.head
        store.changes []
        store.changed-at {})
  store.epoch)


;; --- 手入れ ------------------------------------------------------------------------------------------------

(defk memory-prune-changes [store ask now-ms]
  {:pre [(: store MemoryStore) (: ask PruneChanges) (: now-ms int)] :post [(: % Pruned)]}
  "変更の列が際限なく伸びないように、keep-seconds より古い変更(刻 <= 今 − 保持)を消して floor を上げる。"
  (setv before (- now-ms (int (* 1000 ask.keep-seconds)))
        ;; 刈る範囲は番号の前方の連なり(刻が古い変更の最大の番号まで — PostgreSQL の handler と同じ)。
        edge (max (gfor change store.changes :if (<= (get store.changed-at change.sequence) before) change.sequence) :default 0)
        old (lfor change store.changes :if (<= change.sequence edge) change))
  (for [change old] (del (get store.changed-at change.sequence)))
  (when old (setv store.floor (max store.floor (. (get old -1) sequence))))
  (setv store.changes (lfor change store.changes :if (> change.sequence store.floor) change))
  (Pruned store.floor (len old)))


;; --- handler ------------------------------------------------------------------------------------------------

(defhandler memory-records-handler [#^ MemoryStore store #^ str writer]
  (ReadRow [table key]
    (<- now (GetTime))
    (purge-expired store (epoch-ms now))
    (resume (memory-read-row store effect)))
  (ListRows [table where fields cursor limit]
    (<- now (GetTime))
    (purge-expired store (epoch-ms now))
    (resume (memory-list-rows store effect)))
  (PutRow [table key value expect]
    (<- now (GetTime))
    (purge-expired store (epoch-ms now))
    (resume (memory-put-row store writer effect (epoch-ms now))))
  (PutRows [writes]
    (<- now (GetTime))
    (purge-expired store (epoch-ms now))
    (resume (memory-put-rows store writer effect (epoch-ms now))))
  (WatchChanges [tables cursor timeout limit]
    (<- answer (wait-for-changes (fn [now-ms] (purge-expired store now-ms) (Pure (memory-watch-scan store effect)))
                                 store.poll-seconds timeout))
    (resume answer))
  (AppendEvent [stream idempotency-key body]
    (<- now (GetTime))
    (purge-expired store (epoch-ms now))
    (resume (memory-append store writer effect (epoch-ms now))))
  (ReadEvents [stream after limit]
    (<- now (GetTime))
    (purge-expired store (epoch-ms now))
    (resume (memory-read-events store effect)))
  (AdvanceStoreEpoch []
    (resume (memory-advance-epoch store)))
  (SweepExpired []
    (<- now (GetTime))
    (resume (Swept (purge-expired store (epoch-ms now)))))
  (PruneChanges [keep-seconds]
    (<- now (GetTime))
    (<- pruned (memory-prune-changes store effect (epoch-ms now)))
    (resume pruned)))
