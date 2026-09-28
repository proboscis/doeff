;;; memory の handler — 公開 effect 7 つと列の待ち WatchEvents に手元の表と番号の列で答える(模擬環境・手元の 1 process・単体の検)。
;;; 行の値は凍らせた写像なので、答えに出す Row は置き場の Row そのもの(写し取らなくても呼び手は変えられない)。
;;;
;;; 判断(期待・書きの許可・保持・索引・頁)は admission.hy の純関数ちょうど 1 つ。ここは置き場の data と番号の採り方だけを持つ。
;;; 時刻は doeff-time の GetTime(仮想の時計の下では保持の期限も一瞬で来る)。
;;; WatchChanges と WatchEvents の待ちは読み直しの繰り返し(ポーリング)ではなく呼び鈴: 待ち手は置き場に外の promise(呼び鈴)を、待つ名
;;; (表か列)と一緒に掛けて眠る。書きはその名の待ち手だけを鳴らす — 行の書きと保持の刈りの行の消し = その表・追記 = その列・版の更新と
;;; 変更の刈り = 全部(出自の issue は #1019 — 前は変更の列を動かす書きが全部の待ち手を鳴らし、追記は鳴らさなかった)。
;;; 期限(timeout と、保持の期限で行が消え得る刻の早い方)は doeff-time の ScheduleAt で 1 回だけ鳴らす。呼び鈴を外の promise にするのは、同期の書き(handler の外から置き場の関数を直に呼ぶ模擬の支度)と別の
;;; thread の書きからも鳴らせるため。待ちは PRIORITY_IDLE で park する(仮想の時計を止めない — 期限の刻まで時計が進める)。
;;; 書き手の身元は handler を組む時の引数 writer(effect の欄にしない)。同じ MemoryStore を別の writer の handler で包めば、
;;; 1 つの置き場を複数の書き手が使う形になる。
;;;
;;; 置き場は thread の間で共有してよい(書き手の thread と実況の読みの thread が同じ MemoryStore を使う — この系の Python は GIL の無い
;;; free-threaded)。置き場の不変条件(列・番号・索引)の持ち主は MemoryStore なので、錠(MemoryStore.lock・RLock)も置き場が持ち、
;;; handler の各節は「保持の刈り(purge-expired)と操作」の組を錠の内で 1 つずつ行う(guarded)。WatchChanges と WatchEvents の待ち(呼び鈴で眠る間)は
;;; 錠を持たない — 走査と呼び鈴を掛けるのを同じ錠の内で 1 回にする(間に積まれた変更を取りこぼさない・持ったまま眠ると他の書きが止まる)。
(require doeff-hy.macros [defhandler defk deff <- val var])
(require doeff-hy.record [defrecord])
(import bisect)
(import heapq)
(import threading)
(import collections.abc [Callable])
(import dataclasses [dataclass])
(import datetime [datetime timedelta])
(import doeff [EffectBase Program])
(import doeff_core_effects.scheduler [Cancel CreateExternalPromise ExternalPromise PRIORITY-IDLE Spawn Task TaskCancelledError Wait])
(import doeff_time [GetTime ScheduleAt])
(import doeff_hy.frozen [FrozenMap])
(import doeff_records.values [KeepFor RecordsSchema StreamDecl Row Missing Page Written WrittenRows RowChanged RowRemoved Changes Appended
                              Event Events EventsMoved EventsQuiet Reset WatchCursor ListCursor Refused RowsConflict RowsRefused
                              Unreachable Conflict NotIndexed])
(import doeff_records.effects [ReadRow ListRows PutRow PutRows WatchChanges WatchEvents AppendEvent ReadEvents])
(import doeff_records.faults [AdvanceStoreEpoch SetStoreOutage StoreFault StoreOperation AddStoreFault ClearStoreFaults])
(import doeff_records.maintenance [SweepExpired PruneChanges Swept Pruned])
(import doeff_records.admission [Admitted AppendNew AppendReplay judge-expect judge-put judge-put-rows judge-append
                                 retention-group-of where-refusal row-matches? listed-row key-text next-watch-sequence
                                 epoch-ms terminal-row?])

(defclass StoredRow []
  "置き場の行 1 つ: row = 答えに出す Row / updated-ms = 最後に書かれた刻。"
  (defn #^ None __init__ [self #^ Row row #^ int updated-ms]
    (setv self.row row self.updated-ms updated-ms)))


(defclass StoredGroup []
  "組で数える列(ByKeySuffix)の組 1 つ: last-at = 組の最後の出来事を積んだ刻(保持を数え始める刻)/ events = 組の出来事(積んだ順)。"
  (defn #^ None __init__ [self #^ int last-at]
    (setv self.last-at last-at self.events [])))


;; 保持の期限の索引の項(3 種)— 索引(MemoryStore.expiry)は期限の刻で並ぶ heap で、刈りは期限の来た項だけを取り出す。
;; 項は書いた時に積み、指す物が置き場から変わっていたら取り出した時に読み捨てる(遅延の無効化 — due-live?)。
(defrecord RowDue
  "表の行 1 つの期限: table = 表の名 / text = 鍵の文字列 / stored = 終端になった書きの StoredRow(置き場の行がまだこれの時だけ有効)。"
  #^ str table
  #^ str text
  #^ StoredRow stored)


(defrecord EventDue
  "出来事ごとに数える列の出来事 1 つの期限: event = 積んだ出来事(冪等キーの引きがまだこの出来事の時だけ有効)。"
  #^ Event event)


(defrecord GroupDue
  "組で数える列の組 1 つの期限: stream = 列の名 / group = 組の名 / last-at = 数え始めた刻(組に後の出来事が積まれて組の最後の刻が
   動いたら、この項は読み捨てる — 動いた時に新しい項を積む)。"
  #^ str stream
  #^ str group
  #^ int last-at)


(defclass MemoryStore []
  "memory の置き場: schema = 宣言(operator の欄を書ける主体の一覧 operators を含む)/
   lock = 置き場を読み書きする操作を 1 つずつにする錠(thread の間で置き場を共有するため — 同じ thread の入れ子は通す RLock)/
   bells = WatchChanges と WatchEvents の待ち手が掛けた呼び鈴(外の promise → 待つ名の frozenset の dict — 掛けた順。名は #(\"table\" 表)と
   #(\"stream\" 列)の組で、表と列が同じ綴りでも混ざらない。書きは待つ名が重なる呼び鈴を掛けた順に鳴らして外す。
   set にしないのは、set の順は object の番地で決まり、走らせるたびに待ち手の起きる順が変わって模擬の結果が揺れるため)。
   待ちは読み直さず呼び鈴で起きるので、見回りの刻みは受けない(使い手が渡すのをやめたので 2026-09-29 に外した — 出自の issue は #1017)。"
  (defn #^ None __init__ [self #^ RecordsSchema schema]
    (setv self.schema schema
          self.lock (threading.RLock)
          self.bells {}
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
          ;; 保持の期限で何かが消え得る最も早い刻(epoch ミリ秒・None = 消え得る物が無い)— purge-expired はこの刻より前なら索引を見ない。
          self.purge-due-ms None
          ;; 保持の期限の索引(heap — 項は #(期限の刻 積んだ順 項)・項 = RowDue | EventDue | GroupDue)と、積んだ順の番号(同じ刻の項を
          ;; 比べるための一意の番号 — heap は項そのものを比べない)。
          self.expiry []
          self.expiry-order 0
          ;; 組で数える列の組(#(列 組) → StoredGroup)。
          self.groups {}))
  ;; 錠と呼び鈴は置き場の中身ではなく、この process の thread と実行の間の取り決め — pickle と copy は錠と呼び鈴を除いた中身だけを運び、
  ;; 戻した側で新しい錠と空の呼び鈴を作る(置き場を含む値を pickle する使い手 — worker の結果の file・coordinator の状態 — を壊さないため。
  ;; 呼び鈴は掛けた実行の中でしか意味を持たない)。
  (defn #^ dict __getstate__ [self]
    "pickle / copy が運ぶ中身を返すため(錠と呼び鈴を除く)。"
    (dfor [name value] (.items self.__dict__) :if (not-in name #("lock" "bells")) name value))
  (defn #^ None __setstate__ [self #^ dict state]
    "pickle / copy から戻す時に中身を入れ、新しい錠を作るため。"
    (.update self.__dict__ state)
    ;; 消え得る刻を持つ前に pickle した置き場は、次の操作で 1 度走査して刻を数え直す(0 = もう来ている)。
    (when (not-in "purge_due_ms" state)
      (setv self.purge-due-ms 0))
    ;; 故障の列を持つ前に pickle した置き場は故障なし。
    (when (not-in "faults" state)
      (setv self.faults #()))
    (setv self.lock (threading.RLock)
          self.bells {})
    ;; 期限の索引を持つ前に pickle した置き場は、行と出来事から 1 度だけ索引を作り直す。
    (when (not-in "expiry" state)
      (rebuild-expiry self)
      (when (in "purge_due_ms" state)
        (setv self.purge-due-ms (get state "purge_due_ms"))))))


;; --- 呼び鈴(WatchChanges と WatchEvents の待ち手を起こす)---------------------------------------------------

(deff ring-bells [store names]  ; defk にできない: 錠の内で同期に置き場を書く関数(handler の節と、handler の外から置き場を直に書く模擬の支度の両方)が呼ぶ
  {:pre [(: store MemoryStore) (: names (| frozenset None))] :post [(: % None)]
   :tags {:context "records" :role "foundation"}}
  "書きが動かした名(表か列 — None = 全部: 版が進んだ・床が上がった)を待っている呼び鈴へ知らせて外すため。待つ名の重ならない待ち手は
   起こさない(起きても走査し直して掛け直すだけ — その費用を書きの数 × 待ち手の数にしない)。起きた待ち手は走査し直し、答えが無ければ
   呼び鈴を掛け直す。外の promise の complete は thread の間で安全で、2 度目(期限の鳴らしと重なる)は効かない。"
  (with [store.lock]
    (setv bells (tuple (gfor #(bell waiting) (.items store.bells) :if (or (is names None) (& waiting names)) bell)))
    (for [bell bells]
      (.pop store.bells bell)))
  (for [bell bells]
    (.complete bell None))
  None)


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


(defn #^ None push-due [#^ MemoryStore store #^ int due-ms #^ (| RowDue EventDue GroupDue) item]  ; defk にできない: 錠の内で同期に呼ぶ置き場の書き
  "期限の索引に項を 1 つ積み、消え得る刻を早める。heap の要素は #(刻 積んだ順 項)の組 — heapq は要素どうしの比べでしか並べないので、
   刻と一意の番号を前に置き、項そのものは比べさせない。"
  (+= store.expiry-order 1)
  (heapq.heappush store.expiry #(due-ms store.expiry-order item))
  (note-purge-due store due-ms))


(defn #^ None note-event-due [#^ MemoryStore store #^ StreamDecl decl #^ Event event]  ; defk にできない: 錠の内で同期に呼ぶ置き場の書き
  "期限つき(KeepFor)の列に積んだ出来事 1 つを期限の索引に入れる。出来事ごとに数える列は出来事の項・組で数える列は組の最後の刻が
   動いた時(組が生まれた時を含む)だけ組の項を積む — 前の組の項は last-at が合わなくなり、取り出した時に読み捨てる。"
  (setv keep (keep-ms decl.retention))
  (setv group (retention-group-of decl event.idempotency-key))
  (when (is group None)
    (push-due store (+ event.at keep) (EventDue :event event))
    (return None))
  (setv slot #(event.stream group))
  (setv held (.get store.groups slot))
  (setv fresh (is held None))
  (when fresh
    (setv held (StoredGroup event.at))
    (setv (get store.groups slot) held))
  (.append held.events event)
  (when (or fresh (> event.at held.last-at))
    (setv held.last-at (max held.last-at event.at))
    (push-due store (+ held.last-at keep) (GroupDue :stream event.stream :group group :last-at held.last-at)))
  None)


(defn #^ None rebuild-expiry [#^ MemoryStore store]  ; defk にできない: pickle から戻す時(__setstate__)に同期に呼ぶ置き場の書き
  "期限の索引を行と出来事の全部から作り直す(索引を持つ前に pickle した置き場を戻す時の 1 度だけ)。消え得る刻も数え直す。"
  (setv store.expiry [] store.expiry-order 0 store.groups {} store.purge-due-ms None)
  (for [#(name decl) (sorted (.items store.schema.tables)) :if (isinstance decl.retention KeepFor)]
    (for [#(text stored) (sorted (.items (get store.rows name))) :if (terminal-row? decl stored.row.value)]
      (push-due store (+ stored.updated-ms (keep-ms decl.retention)) (RowDue :table name :text text :stored stored))))
  (for [event store.events]
    (setv decl (store.schema.stream event.stream))
    (when (isinstance decl.retention KeepFor)
      (note-event-due store decl event)))
  (setv store.purge-due-ms (next-purge-due store))
  None)


(defn #^ bool due-live? [#^ MemoryStore store #^ (| RowDue EventDue GroupDue) item]  ; defk にできない: 錠の内で同期に読む置き場の読み
  "期限の索引の項が、まだ置き場に在る物を指すか(偽 = 読み捨てる — 行が消えた・出来事が消えた・組の最後の刻が動いた)。"
  (match item
    (RowDue :table table :text text :stored stored)
      (is (.get (get store.rows table) text) stored)
    (EventDue :event event)
      (is (.get store.by-idempotency #(event.stream event.idempotency-key)) event)
    (GroupDue :stream stream :group group)
      (do (setv held (.get store.groups #(stream group)))
          (and (is-not held None) (= held.last-at item.last-at)))
    _ (raise (TypeError (.format "期限の索引の項ではない: {!r}" item)))))


(defn #^ (| int None) next-purge-due [#^ MemoryStore store]  ; defk にできない: 錠の内で同期に呼ぶ置き場の読みと索引の読み捨て
  "残っている行と出来事のうち、最も早く消える物の刻(row-expired? / event-expired? が真になる最初の刻)。無ければ None。
   索引の先頭の読み捨てられる項を外してから先頭の刻を読む — 費用は外した項の数 × log(索引の長さ)で、行と出来事の数に比例しない。
   終端でない行は索引に無い — 終端になる書きの時に項を積む。"
  (while (and store.expiry (not (due-live? store (get (get store.expiry 0) 2))))
    (heapq.heappop store.expiry))
  (if store.expiry (get (get store.expiry 0) 0) None))


(defn #^ int purge-expired-locked [#^ MemoryStore store #^ int now-ms]  ; defk にできない: purge-expired が錠の内で同期に呼ぶ置き場の書き
  "purge-expired の中身(呼び手が錠を持つ)。消え得る刻(purge-due-ms)より前なら索引を見ない。刈った後は刻を数え直す。"
  (when (or (is store.purge-due-ms None) (< now-ms store.purge-due-ms))
    (return 0))
  (setv removed (purge-expired-scan store now-ms))
  (setv store.purge-due-ms (next-purge-due store))
  removed)


(defn #^ int purge-expired-scan [#^ MemoryStore store #^ int now-ms]  ; defk にできない: purge-expired-locked が錠の内で同期に呼ぶ置き場の書き
  "期限の来た項だけを期限の索引から取り出し、指す行と出来事を消す。費用は取り出した項の数 × log(索引の長さ)— 行と出来事の数に
   比例しない(2026-09-29 の実測: 前の形は刈りのたびに全部の行と出来事を読み、手番の模擬の筋書き 1 つで刈り 731 回 × 出来事の全部 —
   ここの出自の issue は #907)。行の RowRemoved は表の名・鍵の文字列の順に積む(前の全部の走査と同じ順)。"
  (setv rows [] events [])
  (while (and store.expiry (<= (get (get store.expiry 0) 0) now-ms))
    (setv item (get (heapq.heappop store.expiry) 2))
    (when (due-live? store item)
      (match item
        (RowDue) (.append rows item)
        (EventDue :event event) (.append events event)
        ;; 組の出来事は同時に消える — 組を外すので、同じ組の残りの項は読み捨てになる。
        (GroupDue :stream stream :group group) (.extend events (. (.pop store.groups #(stream group)) events))
        _ (raise (TypeError (.format "期限の索引の項ではない: {!r}" item))))))
  (setv removed 0)
  (for [item (sorted rows :key (fn [item] #(item.table item.text)))]
    (.pop (get store.rows item.table) item.text)
    (+= store.head 1)
    (+= removed 1)
    (setv (get store.changed-at store.head) now-ms)
    (.append store.changes (RowRemoved item.table item.stored.row.key store.head)))
  (when removed
    (ring-bells store (frozenset (gfor item rows #("table" item.table)))))
  (when events
    (drop-events store events))
  removed)


(defn #^ None drop-events [#^ MemoryStore store #^ list events]  ; defk にできない: purge-expired-scan が錠の内で同期に呼ぶ置き場の書き
  "期限の来た出来事を冪等キーの引きと出来事の列から外す。列は番号の順なので、1 つずつ番号で二分探索して外す。列は差し替える
   (写しは 1 回の C の複写 — 錠の外で前の列を読んでいる読み手の走査を壊さないため。前の形も差し替えていた)。"
  (for [event events]
    (.pop store.by-idempotency #(event.stream event.idempotency-key) None))
  (setv kept (list store.events))
  (for [sequence (sorted (gfor event events event.sequence) :reverse True)]
    (setv at (bisect.bisect-left kept sequence :key (fn [event] event.sequence)))
    (when (and (< at (len kept)) (= (. (get kept at) sequence) sequence))
      (del (get kept at))))
  (setv store.events kept)
  None)


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
  (setv text (key-text key)
        stored (StoredRow (Row key value version) now-ms))
  (setv (get (get store.rows table) text) stored)
  (+= store.head 1)
  (setv (get store.changed-at store.head) now-ms)
  (.append store.changes (RowChanged table key version value store.head now-ms))
  (ring-bells store (frozenset [#("table" table)]))
  (setv decl (store.schema.table table))
  (when (and (isinstance decl.retention KeepFor) (terminal-row? decl value))
    (push-due store (+ now-ms (keep-ms decl.retention)) (RowDue :table table :text text :stored stored)))
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


(defn #^ (| EventsMoved EventsQuiet) memory-events-scan [#^ MemoryStore store #^ WatchEvents ask]  ; defk にできない: 錠の内(watch-round)で同期に呼ぶ置き場の走査(memory-watch-scan と同じ作法)
  "WatchEvents の今の答え(待たない): 列 ask.stream に after より後の出来事が在れば EventsMoved。出来事の列は番号の順なので、after の
   位置から後ろだけを見る(番号は全部の列で 1 本 — 他の列の出来事は読み捨てる)。"
  (store.schema.stream ask.stream)
  (setv start (bisect.bisect-right store.events ask.after :key (fn [event] event.sequence)))
  (if (any (gfor event (cut store.events start None) (= event.stream ask.stream)))
      (EventsMoved)
      (EventsQuiet)))


;; --- WatchChanges と WatchEvents の待ち ---------------------------------------------------------------------

(defrecord WatchRound
  "待ちの 1 周の走査: answer = 今の答え(Changes | Reset | EventsMoved | EventsQuiet)/ quiet = 待ち続ける答え(空の Changes か EventsQuiet)か /
   due-ms = 保持の期限で行が消え得る最も早い刻(epoch ミリ秒・None = 無い — WatchChanges の待ちはこの刻にも起きて刈りを走らせる。
   WatchEvents の待ちは None — 出来事が消えても列の頭は進まない)。"
  #^ object answer
  #^ bool quiet
  #^ (| int None) due-ms)


(deff watch-round [store ask now-ms bell]  ; defk にできない: 錠の内(guarded の fn)で同期に呼ぶ置き場の走査と呼び鈴の掛け
  {:pre [(: store MemoryStore) (: ask (| WatchChanges WatchEvents)) (: now-ms int) (: bell (| ExternalPromise None))] :post [(: % WatchRound)]
   :tags {:context "records" :role "foundation"}}
  "保持の刈りの後に 1 回走査し、待ち続ける答えなら呼び鈴 bell を待つ名(WatchChanges = 頼んだ表・WatchEvents = その列)と一緒に掛ける
   (None = 掛けない)。走査と掛けを同じ錠の内で行うのは、その間に積まれた書きの鳴らしを取りこぼさないため。"
  (with [store.lock]
    (purge-expired store now-ms)
    (match ask
      (WatchChanges :tables tables)
        (setv answer (memory-watch-scan store ask)
              quiet (and (isinstance answer Changes) (not answer.items))
              names (frozenset (gfor name tables #("table" name)))
              due-ms store.purge-due-ms)
      (WatchEvents :stream stream)
        (setv answer (memory-events-scan store ask)
              quiet (isinstance answer EventsQuiet)
              names (frozenset [#("stream" stream)])
              due-ms None))
    (when (and quiet (is-not bell None))
      (setv (get store.bells bell) names))
    (WatchRound :answer answer :quiet quiet :due-ms due-ms)))


(defk rung [bell]
  {:pre [(: bell ExternalPromise)] :post [(: % None)]
   :tags {:context "records" :role "foundation"}}
  "待ちの期限の刻に呼び鈴を鳴らすため(書きが先に鳴らしていれば効かない — 外の promise は最初の 1 回だけが効く)。"
  (.complete bell None)
  None)


(defk wake-time [now deadline due-ms]
  {:pre [(: now datetime) (: deadline datetime) (: due-ms (| int None))] :post [(: % datetime)]
   :tags {:context "records" :role "foundation"}}
  "待ち手が起きる刻 = timeout の刻と、保持の期限で行が消え得る刻の早い方(消え得る刻が今以前なら 1 ミリ秒先 — 刈りは走査の度に済む)。"
  (if (is due-ms None)
      deadline
      (min deadline (+ now (timedelta :milliseconds (max 1 (- due-ms (epoch-ms now))))))))


(defk withdraw-timer [timer]
  {:pre [(: timer Task)] :post [(: % None)]
   :tags {:context "records" :role "foundation"}}
  "期限の鳴らし timer を取り消し、解け終わるまで待つため(待たずに実行の根が返ると、解けていない task が置き去りの仕事として残る)。
   取り消した timer の Wait は TaskCancelledError で返るので、それをここで飲む。飲むのはこの片付けの task の中だけ — 待ち手の中で飲むと、
   同じ刻に待ち手自身へ届いた取り消し(同じ書きで起きた別の task が待ち手を取り消す — 複数の表の待ちを Race した使い手が、負けた側を
   取り消す片付け)も同じ TaskCancelledError なので見分けられずに消え、待ち手は呼び鈴を掛け直して timeout まで生き、取り消した側も
   そこまで止まる(使い手の模擬で、Race の後の片付けが 4 秒の書きの後 31 秒まで止まった)。先に鳴らし終えていれば取り消しは効かず、すぐ返る。"
  (<- (Cancel timer))
  (try
    (<- (Wait timer))
    (except [TaskCancelledError]
      None))
  None)


(defk bell-or-timer [store bell at]
  {:pre [(: store MemoryStore) (: bell ExternalPromise) (: at datetime)] :post [(: % None)]
   :tags {:context "records" :role "foundation"}}
  "掛けた呼び鈴 bell が鳴るか、刻 at が来るまで眠るため。刻の鳴らしは ScheduleAt の 1 回(仮想の時計の下では時計の列の 1 項)。
   待ちは PRIORITY_IDLE で park する — 外の promise の既定の待ちは仮想の時計を止める(期限の刻へ進めなくなる)ため。
   起きた後(と、待ち手が取り消された時)は呼び鈴を外し、期限の鳴らしを別の task(withdraw-timer)で取り消して、その終わりを待つ。
   その待ちの最中に待ち手自身が取り消されたら、片付けの task の終わりを待ってから取り消しを上へ渡す(飲まない)。"
  (<- timer (ScheduleAt at (rung bell)))
  (try
    (<- (Wait bell.future :priority PRIORITY-IDLE))
    (finally
      (with [store.lock]
        (.pop store.bells bell None))
      (<- withdrawing (Spawn (withdraw-timer timer)))
      (try
        (<- (Wait withdrawing))
        (except [cancelled TaskCancelledError]
          (<- (Wait withdrawing))
          (raise cancelled)))))
  None)


(val CLOCK-TICK (timedelta :microseconds 1))


(defk wait-span [seconds]
  {:pre [(: seconds (| int float))] :post [(: % timedelta)]
   :tags {:context "records" :role "foundation"}}
  "timeout の秒 → 待つ長さ。正の timeout は少なくとも時刻の 1 刻み(1 マイクロ秒)を過ぎさせる — timedelta は 0.5 マイクロ秒未満を 0 に
   丸めるので、呼び手が「残りの秒」(浮動小数の誤差で 1e-7 秒ほど)を渡すと、待たずに返って時計が進まず、呼び手が同じ刻で回り続ける
   (前の形は Delay で眠り、仮想の時計が正の Delay を 1 刻みに切り上げていた — doeff-time の _delay_span と同じ約束)。"
  (val span (timedelta :seconds seconds))
  (if (and (> seconds 0) (< span CLOCK-TICK)) CLOCK-TICK span))


(defk memory-watch [store ask]
  {:pre [(: store MemoryStore) (: ask (| WatchChanges WatchEvents))] :post [(: % (| Changes Reset EventsMoved EventsQuiet))]
   :tags {:context "records" :role "foundation"}}
  "WatchChanges と WatchEvents の答え: 待つ名(頼んだ表・列)に書きが来るか timeout 秒が過ぎるまで待つ(Reset はすぐ返す)。読み直しを
   繰り返さず、その名の書きが鳴らす呼び鈴と、期限(timeout・保持の期限)の 1 回の鳴らしで起きる。timeout を過ぎたら最後に 1 回走査した
   答えを返す。"
  (<- started (GetTime))
  (<- span (wait-span ask.timeout))
  (val deadline (+ started span))
  (var now started)
  (var round (guarded store (fn [] (watch-round store ask (epoch-ms now) None))))
  (while (and round.quiet (< now deadline))
    (<- bell (CreateExternalPromise))
    (:= round (guarded store (fn [] (watch-round store ask (epoch-ms now) bell))))
    (when round.quiet
      (<- at (wake-time now deadline round.due-ms))
      (<- (bell-or-timer store bell at))
      (<- woke (GetTime))
      (:= now woke)
      (when (>= now deadline)
        (:= round (guarded store (fn [] (watch-round store ask (epoch-ms now) None)))))))
  round.answer)


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
               (note-event-due store decl event))
             ;; 新しく積んだ時だけ、この列を待つ待ち手を鳴らす(再送は列の頭を動かさない)。
             (ring-bells store (frozenset [#("stream" ask.stream)]))
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
    (ring-bells store None)
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
  (when old
    (setv store.floor (max store.floor (. (get old -1) sequence)))
    (ring-bells store None))
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
   ;; 答え = 公開 effect 7 つと WatchEvents の答えの型のどれか(program の答えか、故障の答え Refused / Unreachable)。
   :post [(: % (| Row Missing Page Reset NotIndexed Written Conflict Refused Unreachable WrittenRows RowsConflict RowsRefused
                  Changes Appended Events EventsMoved EventsQuiet))]
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
                         (memory-watch store effect)))
    (resume answer))
  (WatchEvents [stream after timeout]
    (<- answer (answered store READ #(stream) effect (memory-watch store effect)))
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
