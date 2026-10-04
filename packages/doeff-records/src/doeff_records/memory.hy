;;; memory の handler — 公開 effect 8 つと列の待ち WatchEvents に手元の表と番号の列で答える(模擬環境・手元の 1 process・単体の検)。
;;; 行の値は凍らせた写像なので、答えに出す Row は置き場の Row そのもの(写し取らなくても呼び手は変えられない)。
;;;
;;; 判断(期待・書きの許可・保持・索引・頁)は admission.hy の純関数ちょうど 1 つ。ここは置き場の data と番号の採り方だけを持つ。
;;; 時刻は doeff-time の GetTime(仮想の時計の下では保持の期限も一瞬で来る)。
;;; WatchChanges と WatchEvents の待ちは読み直しの繰り返し(ポーリング)ではなく呼び鈴: 待ち手は置き場に外の promise(呼び鈴)を、待つ名
;;; (表か列)と一緒に掛けて眠る。書きはその名の待ち手だけを鳴らす — 行の書きと保持の刈りの行の消し = その表・追記 = その列・版の更新と
;;; 変更の刈り = 全部(出自の issue は #1019 — 前は変更の列を動かす書きが全部の待ち手を鳴らし、追記は鳴らさなかった)。
;;; 届かない状態(検の口 faults.SetStoreOutage)を置くと待ち手を全部鳴らし、待ちの各回の走査が待つ名の届かない状態を見て Unreachable で
;;; 返る — HTTP と PostgreSQL の口の待ちが読み直しの次の問いで不達を知るのと同じ(待ちの頭だけで見ると、待ちの最中に置いた窓に上限まで
;;; 気づかない — 出自の issue は #1020・使い手の画面の読み手で上限を 30 秒に延ばした時に出た)。
;;; 期限(timeout と、保持の期限で行が消え得る刻の早い方)は doeff-time の期限つきの待ち WaitWithin の 1 つ(呼び鈴か期限の早い方 —
;;; 仮想の時計の下では task を作らない・#3054)。呼び鈴を外の promise にするのは、同期の書き(handler の外から置き場の関数を直に呼ぶ模擬の支度)と別の
;;; thread の書きからも鳴らせるため。待ちは park で待つ(仮想の時計を止めない — 期限の刻まで時計が進める)。
;;; 書き手の身元は handler を組む時の引数 writer(effect の欄にしない)。同じ MemoryStore を別の writer の handler で包めば、
;;; 1 つの置き場を複数の書き手が使う形になる。
;;;
;;; 置き場は thread の間で共有してよい(書き手の thread と実況の読みの thread が同じ MemoryStore を使う — この系の Python は GIL の無い
;;; free-threaded)。置き場の不変条件(列・番号・索引)の持ち主は MemoryStore なので、錠(MemoryStore.lock・RLock)も置き場が持ち、
;;; handler の各節は「保持の刈り(purge-expired)と操作」の組を錠の内で 1 つずつ行う(guarded)。WatchChanges と WatchEvents の待ち(呼び鈴で眠る間)は
;;; 錠を持たない — 走査と呼び鈴を掛けるのを同じ錠の内で 1 回にする(間に積まれた変更を取りこぼさない・持ったまま眠ると他の書きが止まる)。
(require doeff-hy.macros [defhandler defk deff <- val var])
(val MODULE-TAGS {:context "records" :role "foundation"})
(require doeff-hy.record [defrecord])
(import bisect)
(import heapq)
(import threading)
(import collections.abc [Callable])
(import dataclasses [dataclass])
(import datetime [datetime timedelta])
(import doeff [EffectBase Program with-handlers])
(import doeff_core_effects.scheduler [CompletePromise CreateExternalPromise CreatePromise ExternalPromise PRIORITY-IDLE Spawn Task
                                      TaskCancelledError Wait Cancel])
(import doeff_events.effects [PublishEffect])
(import doeff_records.event_source [BodyWrapper ChangedRow ReadSignalSource SignalSourceFactory SignalTables checked-bindings failure-announced
                                    first-seen ride-out-stall stop-source waits-beside-sources])
(import doeff_time [GetTime WaitWithin])
(import doeff_time.effects.time [GetTimeEffect WaitWithinEffect])
(import doeff_hy.frozen [FrozenMap])
(import doeff_records.values [KeepFor RecordsSchema StreamDecl Row Missing Page Written WrittenRows RowChanged RowRemoved Changes Appended
                              Event RetiredKey Events EventsMoved EventsQuiet Reset WatchCursor ListCursor Refused RowsConflict RowsRefused
                              Unreachable Conflict NotIndexed StreamEnd StreamEmpty])
(import doeff_records.effects [ReadRow ListRows PutRow PutRows WatchChanges WatchEvents AppendEvent ReadEvents ReadStreamEnd AwaitRecordsBack
                               ReadSourcePatience])
(import doeff_records.faults [AdvanceStoreEpoch SetStoreOutage StoreFault StoreOperation AddStoreFault ClearStoreFaults])
(import doeff_records.maintenance [SweepExpired PruneChanges Swept Pruned])
(import doeff_records.store_choice [StoreChoice])
(import functools [partial])
(import doeff_records.admission [Admitted AppendNew AppendReplay judge-expect judge-put judge-put-rows judge-append
                                 retention-group-of where-refusal row-matches? listed-row key-text next-watch-sequence
                                 epoch-ms terminal-row? body-digest])

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
          ;; 保持の期限で出来事を消した冪等キーの覚え(#(列 冪等キー) → values.RetiredKey — 番号と本文の指紋だけ)。消した後の同じ鍵の
          ;; 追記を生きた出来事と同じ規則で判じるため(#3022)。覚えは消さない(育ち続けてよい)。
          self.retired-keys {}
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
    (setv self.lock (threading.RLock)
          self.bells {}))
  (deff __deepcopy__ [self memo]  ; defk にできない: copy.deepcopy が呼ぶ class の口(同期の呼び — Program を実行しない)
    {:pre [(: self MemoryStore) (: memo dict)] :post [(: % MemoryStore)] :tags {:context "records" :role "foundation"}}
    "置き場の写しを作るため(使い手の模擬の検が、種を置いた置き場を検ごとに写す — 写しへの書きは元の置き場に届かない)。書き換える
     入れ物だけを写し、中身の値は共有する: 表 → 鍵 → 置き場の行の 2 段の dict・変更の列と刻・出来事の列と冪等キーの引き・消した鍵の覚え・
     期限の索引は写す。行 Row・置き場の行 StoredRow・変更・出来事・期限の項は作った後に変えない値(書きは新しい値で置き換える)なので
     辿らない — 5 万行の置き場で行ごとに写しを作らない(#2670 根 E の (a))。組 StoredGroup は出来事を書き足し最後の刻を
     進めるので組ごとに写す。錠と呼び鈴は運ばず新しく作る(__getstate__ / __setstate__ と同じ取り決め)。"
    (setv copied (.__new__ MemoryStore MemoryStore))
    (setv (get memo (id self)) copied)
    (setv state (.__getstate__ self))
    (setv (get state "rows") (dfor [table held] (.items self.rows) table (dict held)))
    (for [name #("changes" "events" "expiry")]
      (setv (get state name) (list (get state name))))
    (for [name #("changed_at" "by_idempotency" "retired_keys")]
      (setv (get state name) (dict (get state name))))
    (setv groups {})
    (for [[slot group] (.items self.groups)]
      (setv held (StoredGroup group.last-at))
      (setv held.events (list group.events))
      (setv (get groups slot) held))
    (setv (get state "groups") groups)
    (.__setstate__ copied state)
    copied))


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


(deff _bell-names [tables streams]  ; defk にできない: 錠の内で同期に呼ぶ(watch-round と書きの鳴らし)
  {:pre [(: tables tuple) (: streams tuple)] :post [(: % frozenset)]
   :tags {:context "records" :role "foundation"}}
  "呼び鈴が待つ名と書きが鳴らす名の形を 1 か所で決めるため: 表は #(\"table\" 表)・列は #(\"stream\" 列)(表と列が同じ綴りでも混ざらない)。
   この形は置き場の中だけの取り決めで、置き場の外の待ち手は hang-bell に表と列の名を渡す(#3028)。"
  (frozenset (+ (tuple (gfor table tables #("table" table))) (tuple (gfor stream streams #("stream" stream))))))


(defk hang-bell [store tables streams]
  {:pre [(: store MemoryStore) (: tables (get tuple #(str ...))) (: streams (get tuple #(str ...)))] :post [(: % ExternalPromise)]
   :tags {:context "records" :role "foundation"}}
  "置き場 store の表 tables か列 streams への書きで鳴る呼び鈴を掛けて返すため — 置き場の外の待ち手(模擬の世界の落ち着きの見張りなど)が、
   置き場の内側(呼び鈴の名の形・錠)に触らずに書きを待つ口(#3028)。鳴った呼び鈴は書きが外す。鳴らずに待ちを終える時は drop-bell。"
  (<- bell ExternalPromise (CreateExternalPromise))
  (with [store.lock]
    (setv (get store.bells bell) (_bell-names tables streams)))
  bell)


(defk drop-bell [store bell]
  {:pre [(: store MemoryStore) (: bell ExternalPromise)] :post [(: % None)]
   :tags {:context "records" :role "foundation"}}
  "掛けた呼び鈴 bell を置き場 store から外すため(鳴らずに待ちを終える時 — 鳴った呼び鈴は書きが外し済み・外し済みでも効かずに返る)。"
  (with [store.lock]
    (.pop store.bells bell None))
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
    (ring-bells store (_bell-names (tuple (gfor item rows item.table)) #())))
  (when events
    (drop-events store events))
  removed)


(defn #^ None drop-events [#^ MemoryStore store #^ list events]  ; defk にできない: purge-expired-scan が錠の内で同期に呼ぶ置き場の書き
  "期限の来た出来事を冪等キーの引きと出来事の列から外し、鍵の覚え(retired-keys)へ番号と本文の指紋だけを移す(#3022 — 消した後の
   同じ鍵の追記も memory-append が同じ規則で判じる)。覚えが既に在る鍵は書き換えない(PostgreSQL の鍵だけの表の ON CONFLICT DO NOTHING と
   同じ)。列は番号の順なので、1 つずつ番号で二分探索して外す。列は差し替える
   (写しは 1 回の C の複写 — 錠の外で前の列を読んでいる読み手の走査を壊さないため。前の形も差し替えていた)。"
  (for [event events]
    (setv slot #(event.stream event.idempotency-key))
    (.pop store.by-idempotency slot None)
    (when (not-in slot store.retired-keys)
      (setv (get store.retired-keys slot) (RetiredKey :idempotency-key event.idempotency-key :sequence event.sequence
                                                       :body-digest (body-digest event.body)))))
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
  (ring-bells store (_bell-names #(table) #()))
  (setv decl (store.schema.table table))
  (when (and (isinstance decl.retention KeepFor) (terminal-row? decl value))
    (push-due store (+ now-ms (keep-ms decl.retention)) (RowDue :table table :text text :stored stored)))
  (Written version value))


(defn #^ object memory-put-row [#^ MemoryStore store #^ str writer #^ PutRow ask #^ int now-ms]
  (setv decl (store.schema.table ask.table)
        current (memory-current-row store ask.table ask.key))
  (setv conflict (judge-expect ask.expect current))
  (when conflict (return conflict))
  (setv verdict (judge-put decl current ask.key ask.value))
  (when (isinstance verdict Refused) (return verdict))
  (memory-store-row store ask.table ask.key current verdict.value now-ms))


(defn #^ (| WrittenRows RowsConflict RowsRefused) memory-put-rows [#^ MemoryStore store #^ str writer #^ PutRows ask #^ int now-ms]  ; defk にできない: memory の handler が同期に呼ぶ置き場の書き(memory-put-row と同じ作法)
  "PutRows の束を全部か 0 で書く: 全部の行の判定(admission.judge-put-rows)が通った時だけ、束の順に 1 行ずつ書いて変更を積む。"
  (for [write ask.writes] (store.schema.table write.table))
  (setv currents (tuple (gfor write ask.writes (memory-current-row store write.table write.key)))
        verdict (judge-put-rows store.schema ask.writes currents))
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
  "待ちの 1 周の走査: answer = 今の答え(Changes | Reset | EventsMoved | EventsQuiet | Unreachable — 待つ名が届かない状態)/ quiet = 待ち続ける答え(空の Changes か EventsQuiet)か /
   due-ms = 保持の期限で行が消え得る最も早い刻(epoch ミリ秒・None = 無い — WatchChanges の待ちはこの刻にも起きて刈りを走らせる。
   WatchEvents の待ちは None — 出来事が消えても列の頭は進まない)。"
  #^ object answer
  #^ bool quiet
  #^ (| int None) due-ms)


(deff watch-round [store ask now-ms bell]  ; defk にできない: 錠の内(guarded の fn)で同期に呼ぶ置き場の走査と呼び鈴の掛け
  {:pre [(: store MemoryStore) (: ask (| WatchChanges WatchEvents)) (: now-ms int) (: bell (| ExternalPromise None))] :post [(: % WatchRound)]
   :tags {:context "records" :role "foundation"}}
  "保持の刈りの後に 1 回走査し、待ち続ける答えなら呼び鈴 bell を待つ名(WatchChanges = 頼んだ表・WatchEvents = その列)と一緒に掛ける
   (None = 掛けない)。走査と掛けを同じ錠の内で行うのは、その間に積まれた書きの鳴らしを取りこぼさないため。待つ名のどれかが届かない
   状態(SetStoreOutage)なら、走査の前に Unreachable を答える(待ちの最中に置いた窓も、起きた回で不達として返す — 頭の註)。"
  (with [store.lock]
    (purge-expired store now-ms)
    (setv down (unreachable-for store (match ask
                                        (WatchChanges :tables tables) (tuple tables)
                                        (WatchEvents :stream stream) #(stream))))
    (when (is-not down None)
      (return (WatchRound :answer down :quiet False :due-ms None)))
    (match ask
      (WatchChanges :tables tables)
        (setv answer (memory-watch-scan store ask)
              quiet (and (isinstance answer Changes) (not answer.items))
              names (_bell-names (tuple tables) #())
              due-ms store.purge-due-ms)
      (WatchEvents :stream stream)
        (setv answer (memory-events-scan store ask)
              quiet (isinstance answer EventsQuiet)
              names (_bell-names #() #(stream))
              due-ms None))
    (when (and quiet (is-not bell None))
      (setv (get store.bells bell) names))
    (WatchRound :answer answer :quiet quiet :due-ms due-ms)))


(defk wake-time [now deadline due-ms]
  {:pre [(: now datetime) (: deadline datetime) (: due-ms (| int None))] :post [(: % datetime)]
   :tags {:context "records" :role "foundation"}}
  "待ち手が起きる刻 = timeout の刻と、保持の期限で行が消え得る刻の早い方(消え得る刻が今以前なら 1 ミリ秒先 — 刈りは走査の度に済む)。"
  (if (is due-ms None)
      deadline
      (min deadline (+ now (timedelta :milliseconds (max 1 (- due-ms (epoch-ms now))))))))


(defk bell-or-timer [store bell seconds]
  {:pre [(: store MemoryStore) (: bell ExternalPromise) (: seconds float)] :post [(: % None)]
   :tags {:context "records" :role "foundation"}}
  "掛けた呼び鈴 bell が鳴るか、seconds 秒が過ぎるまで眠るため。待ちは doeff-time の期限つきの待ち WaitWithin の 1 つ(仮想の時計の下では
   期限は時計の列の 1 項で、task を作らない・呼び鈴が先に鳴れば列から外す — #3054。前は期限の鳴らしを ScheduleAt の task に
   し、起きた後にその取り消しを別の片付けの task で待っていた — 待ち 1 回に task 2 本)。park = 外の promise を仮想の時計を止めずに待つ
   (既定の待ちは時計を止め、期限の刻へ進めなくなる)。起きた後(と、待ち手が取り消された時)は呼び鈴を外す。どちらで起きたかは見ない —
   呼び手は起きた後に走査し直す。"
  (try
    (<- _woke (WaitWithin bell.future seconds :park True))
    (finally
      (<- (drop-bell store bell))))
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
  {:pre [(: store MemoryStore) (: ask (| WatchChanges WatchEvents))] :post [(: % (| Changes Reset EventsMoved EventsQuiet Unreachable))]
   :tags {:context "records" :role "foundation"}}
  "WatchChanges と WatchEvents の答え: 待つ名(頼んだ表・列)に書きが来るか timeout 秒が過ぎるまで待つ(Reset はすぐ返す)。読み直しを
   繰り返さず、その名の書きが鳴らす呼び鈴と、期限(timeout・保持の期限)の 1 回の鳴らしで起きる。timeout を過ぎたら最後に 1 回走査した
   答えを返す。待ちの最中に待つ名が届かない状態になれば(SetStoreOutage が待ち手を鳴らす)、起きた回で Unreachable を返す。"
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
      (<- (bell-or-timer store bell (.total-seconds (- at now))))
      (<- woke (GetTime))
      (:= now woke)
      (when (>= now deadline)
        (:= round (guarded store (fn [] (watch-round store ask (epoch-ms now) None)))))))
  round.answer)


;; --- 追記の列 --------------------------------------------------------------------------------------------

(defn #^ object memory-append [#^ MemoryStore store #^ str writer #^ AppendEvent ask #^ int now-ms]
  "AppendEvent に答えるため: 冪等キーの前の使い(生きた出来事・無ければ保持の期限で消した鍵の覚え)を引いて判じ、新しければ積む。"
  (setv decl (store.schema.stream ask.stream)
        slot #(ask.stream ask.idempotency-key)
        earlier (.get store.by-idempotency slot)
        verdict (judge-append decl ask.body (if (is earlier None) (.get store.retired-keys slot) earlier)))
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
             (ring-bells store (_bell-names #() #(ask.stream)))
             (Appended event.sequence))))


(defn #^ Events memory-read-events [#^ MemoryStore store #^ ReadEvents ask]
  (store.schema.stream ask.stream)
  (setv items (tuple (cut (lfor event store.events
                                :if (and (= event.stream ask.stream) (> event.sequence ask.after))
                                event)
                          ask.limit)))
  (Events items (if items (. (get items -1) sequence) ask.after)))


(defn #^ (| StreamEnd StreamEmpty) memory-read-stream-end [#^ MemoryStore store #^ ReadStreamEnd ask]  ; defk にできない: 錠の内で同期に呼ぶ置き場の読み(at-now の operation)
  "ReadStreamEnd に答えるため: 保持で刈った後の今の断面で、列 stream の最後の出来事の番号(列の出来事は番号の昇順に並ぶので後ろから探す)。"
  (store.schema.stream ask.stream)
  (for [event (reversed store.events)]
    (when (= event.stream ask.stream)
      (return (StreamEnd event.sequence))))
  (StreamEmpty))


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
   ;; 答え = 公開 effect 8 つと WatchEvents の答えの型のどれか(program の答えか、故障の答え Refused / Unreachable)。
   :post [(: % (| Row Missing Page Reset NotIndexed Written Conflict Refused Unreachable WrittenRows RowsConflict RowsRefused
                  Changes Appended Events EventsMoved EventsQuiet StreamEnd StreamEmpty))]
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
   ;; 答え = 置き場の操作の答え(公開 effect 8 つの答えの型のどれか)。
   :post [(: % (| Row Missing Page Reset NotIndexed Written Conflict Refused Unreachable WrittenRows RowsConflict RowsRefused
                  Changes Appended Events StreamEnd StreamEmpty))]
   :tags {:context "records" :role "foundation"}}
  "いまの刻(epoch ミリ秒)を読み、置き場の錠の内で保持の刈りの後に operation(刻 → 答え)を呼ぶ(各節の置き場の操作の 1 つの形)。"
  (<- now (GetTime))
  (val now-ms (epoch-ms now))
  (guarded store (fn [] (purge-expired store now-ms) (operation now-ms))))


;; --- handler ------------------------------------------------------------------------------------------------

(val READ StoreOperation.READ)
(val WRITE StoreOperation.WRITE)


(defk memory-await-back [store names]
  {:pre [(: store MemoryStore) (: names (get tuple #(str ...)))] :post [(: % None)] :tags {:context "records" :role "foundation"}}
  "置き場の止まり(faults.SetStoreOutage)が names から外れるまで待つため(AwaitRecordsBack の memory の答え — #3469)。止まりを置く・外す
   拍に呼び鈴が全部鳴るので、呼び鈴で眠って起きた回に止まりを読み直す(時計を使わずに park — 模擬の時計は進める)。取り消されたら掛けた
   呼び鈴を外してから解ける。"
  (while True
    (<- bell ExternalPromise (hang-bell store names #()))
    (when (is (unreachable-for store names) None)
      (<- (drop-bell store bell))
      (return None))
    (try
      (<- _rang (Wait bell.future :priority PRIORITY-IDLE))
      (except [cancelled TaskCancelledError]
        (<- (drop-bell store bell))
        (raise cancelled))))
  None)


(defk memory-reach [store names]
  {:pre [(: store MemoryStore) (: names (get tuple #(str ...)))] :post [(: % (| Unreachable bool))] :tags {:context "records" :role "foundation"}}
  "memory の置き場が names に答えるかを読み直すため(模擬の源の止まりの越え方 ride-out-stall が、戻りの後に撃ち直す読み — 答える = True・
   止まり = その Unreachable)。"
  (val down (unreachable-for store names))
  (if (is down None) True down))


(defhandler memory-records-handler [#^ MemoryStore store #^ str writer]
  ;; 各節の答えは answered の 1 点を通る — 届かない状態(faults.SetStoreOutage)と故障(faults.AddStoreFault)を見てから置き場に触る。
  ;; 源の工場の問い(ReadSignalSource)には、この組で記録に答えている置き場 store の書きで鳴る模擬の源で答える(#3127 — 源は必ず同じ置き場に
  ;; 結ばれる。組み立ての entry は問うだけで、本番と模擬の違いは記録の handler の差し替えだけになる)。
  (ReadSignalSource []
    (<- source SignalSourceFactory (memory-signal-source store))
    (resume source))
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
  (ReadStreamEnd [stream]
    (<- answer (answered store READ #(stream) effect (at-now store (fn [now-ms] (memory-read-stream-end store effect)))))
    (resume answer))
  (AwaitRecordsBack [names]
    ;; 合図の源の止まりの見張り(#3469)— 止まりが names から外れるまで呼び鈴で眠り、外れたら答える(期限は待つ側の源が持つ)。
    (<- (memory-await-back store names))
    (resume None))
  (SetStoreOutage [detail names]
    ;; 置いた(外した)時に待ち手を全部鳴らす — 眠っている待ちが次の走査で届かない状態を見て Unreachable で返るため(頭の註)。
    (with [store.lock]
      (setv store.outage (if (is detail None) None effect))
      (ring-bells store None))
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


;; --- 置き場の選び(records-serving に渡す値)----------------------------------------------------------------

(defk memory-prepared [store schema prefix host]
  {:pre [(: store MemoryStore) (: schema RecordsSchema) (: prefix str) (: host str)] :post [(: % Callable)]
   :tags {:context "records" :role "foundation"}}
  "memory の置き場 store で表を用意したことにし、「書き手の名 → 記録の handler の関数」を返すため(表の宣言は置き場が持つ — 接頭辞と
   機体の名は PostgreSQL の表の名と行の刻みにだけ効くので使わない・I/O なし)。"
  (partial memory-records-handler store))


(defk memory-store-choice [store]
  {:pre [(: store MemoryStore)] :post [(: % StoreChoice)] :tags {:context "records" :role "foundation"}}
  "memory の置き場 store を使う置き場の選びを作るため(模擬・手元の 1 process・単体の検が records-serving に渡す。/readyz は用意の済みだけで
   ready)。PostgreSQL の選びは doeff_records.main の PG-STORE。"
  (StoreChoice :prepare-of (partial memory-prepared store) :readiness None))


;; --- 模擬の源(memory の置き場の書きで合図を発する — #3127)-----------------------------------------------------------------
;; 記録の置き場の源(event_source.hy の records-signal-handler — WatchChanges・WatchEvents の long-poll と繋ぎ直し)の模擬の組の代わり。置き場の
;; 呼び鈴(hang-bell — 書きが鳴らす外の promise)で起きるので、時計を使わず(poll も Delay も無い)、始まりの位置も effect を出さずに置き場から
;; 直に読む。呼び鈴は置き場 1 つを共有する全部の書き手が鳴らすので、別の handler の組(模擬の別の process)の書きでも起きる。合図の型と keys は
;; 記録の置き場の源と同じ(結び SignalTables・ChangedRow)— 組み立ての違いは土台が鍵 SignalSourceFactory に答える値だけ。

(defrecord MemoryMark
  "模擬の源が読み終えた置き場の位置: epoch = 置き場の版 / sequence = 表の変更の番号 / event = 列の出来事の番号。"
  #^ int epoch
  #^ int sequence
  #^ int event)


(defrecord MemorySignals
  "模擬の源が位置より後の書きを読んだ答え: signals = 発する合図(結びの順)/ mark = 読み終えた位置。"
  #^ tuple signals
  #^ MemoryMark mark)


(deff memory-mark [store]  ; defk にできない: 錠の内で同期に置き場の位置を読む(置き場の書きと同じ作法 — effect を出さない)
  {:pre [(: store MemoryStore)] :post [(: % MemoryMark)]}
  "置き場の今の位置を読むため(模擬の源の始まり — 始まりの位置の effect を出さない)。"
  (with [store.lock]
    (MemoryMark :epoch store.epoch :sequence store.head :event store.event-head)))


(deff binding-keys [binding changes events]  ; defk にできない: 錠の外で同期に組む純粋な判断(memory-signals-since が内包表記の中で呼ぶ)
  {:pre [(: binding SignalTables) (: changes tuple) (: events tuple)] :post [(: % tuple)]}
  "結び 1 つの合図の keys を組むため: 結んだ表の変わった行(ChangedRow(表, 行の鍵の綴り)— 初めて出た順・同じ行は 1 つ)と、結んだ列の
   追記の頭(ChangedRow(列, 頭の番号の綴り))。記録の置き場の源と同じ keys の形。"
  (setv rows (tuple (gfor change changes :if (in change.table binding.tables) (ChangedRow :table change.table :key (key-text change.key))))
        seen (set)
        unique (tuple (gfor row rows :if (not-in row seen) :do (.add seen row) row))
        heads (tuple (gfor stream binding.streams
                           :setv last (max (gfor event events :if (= event.stream stream) event.sequence) :default 0)
                           :if (> last 0)
                           (ChangedRow :table stream :key (str last)))))
  (+ unique heads))


(deff memory-signals-since [store bindings mark]  ; defk にできない: 錠の内で同期に置き場を読む(置き場の書きと同じ作法)
  {:pre [(: store MemoryStore) (: bindings tuple) (: mark MemoryMark)] :post [(: % MemorySignals)]}
  "位置 mark より後の書きを、結びごとの合図にするため(結んだ表と列の書きの無い結びは出さない)。置き場の版が変わった・保持の刈りで位置が床より
   前になった時は、結びごとに keys の空な合図を 1 つ発する(受け手は読み直す — 記録の置き場の源が Reset で読み直すのと同じ)。"
  (with [store.lock]
    (setv now (MemoryMark :epoch store.epoch :sequence store.head :event store.event-head)
          lost (or (!= mark.epoch store.epoch) (< mark.sequence store.floor))
          changes (if lost #() (tuple (gfor change store.changes :if (> change.sequence mark.sequence) change)))
          start (bisect.bisect-right store.events mark.event :key (fn [event] event.sequence))
          events (tuple (cut store.events start None))))
  (MemorySignals :signals (tuple (gfor binding bindings
                                       :setv keys (binding-keys binding changes events)
                                       :if (or lost keys)
                                       (binding.signal :keys (if lost #() keys))))
                 :mark now))


(defk memory-publish [store bindings subscriber tables streams mark]
  {:pre [(: store MemoryStore) (: bindings tuple) (: subscriber str) (: tables (get tuple #(str ...))) (: streams (get tuple #(str ...)))
         (: mark MemoryMark)]
   :post [(: % None)] :tags {:context "records" :role "foundation"}}
  "模擬の源の task: 結んだ表と列の呼び鈴を掛けてから位置より後の書きを読み(掛ける前の書きも拾う — 読みと待ちの間の書きを落とさない)、
   合図が在れば外して Publish し、無ければ呼び鈴が鳴るのを待つ(時計を使わずに park — 模擬の時計は進める)— を繰り返す。止めるのは Cancel だけ。
   置き場が結んだ名に答えない間(記録の service の止まりの模擬 — faults.SetStoreOutage は呼び鈴を全部鳴らす)は、本番の源と同じ越え方
   ride-out-stall で戻りを待つ(#3469 — 模擬と本番で止まりの振る舞いを揃える)。"
  (var at mark)
  (val names (+ tables streams))
  (while True
    (<- bell ExternalPromise (hang-bell store tables streams))
    (val down (unreachable-for store names))
    (if (is-not down None)
        (do (<- (drop-bell store bell))
            (<- _reached (ride-out-stall subscriber names down (memory-reach store names))))
        (do (val found (memory-signals-since store bindings at))
            (:= at found.mark)
            (if found.signals
                (do (<- (drop-bell store bell))
                    (for [signal found.signals]
                      (<- (PublishEffect signal))))
                ;; 取り消されたら(本体が終わった)掛けた呼び鈴を外してから解ける — 鳴らない呼び鈴を置き場に残さない。
                (try
                  (<- _rang (Wait bell.future :priority PRIORITY-IDLE))
                  (except [cancelled TaskCancelledError]
                    (<- (drop-bell store bell))
                    (raise cancelled)))))))
  None)


(defk run-memory-source [store bindings subscriber body]
  {:tp [T] :pre [(: store MemoryStore) (: bindings (get tuple #(SignalTables ...))) (: subscriber str) (: body (| (get Program #(T object)) (get EffectBase T)))]
   :post [(: % T)] :tags {:context "records" :role "foundation"}}
  "memory-signal-handler の包み: 包んだ本体を走らせる頭で置き場の今の位置を読み(effect を出さない)、源の task を Spawn してから本体をこの task の
   まま(Spawn せずに)走らせ、本体が終われば答えでも例外でも源の task を止めるため(記録の置き場の源の包みと同じ形 — 本体の待ちに源の失敗が届く)。"
  (<- checked (get tuple #(SignalTables ...)) (checked-bindings bindings subscriber))
  (<- tables (get tuple #(str ...)) (first-seen (tuple (gfor binding checked name binding.tables name))))
  (<- streams (get tuple #(str ...)) (first-seen (tuple (gfor binding checked name binding.streams name))))
  (val mark (memory-mark store))
  (<- source Task (Spawn (failure-announced subscriber (memory-publish store checked subscriber tables streams mark))))
  (try
    (<- answer (with-handlers [(waits-beside-sources subscriber)] body))
    (finally
      (<- (stop-source source))))
  answer)


;; 模擬の源の包み 1 つが本体の周りで出す effect の全部(memory-signal-handler の宣言 __doeff_effects__ — 閉じの検の道具が工場の中を読めないので
;; 宣言する)。源の task(Spawn と、その中の呼び鈴の CreateExternalPromise・鳴るまでの Wait・Publish — 源の失敗・止まり・戻りの合図も)・
;; 止まりの越え方 ride-out-stall(上限の問い ReadSourcePatience・GetTime・見張りの task の Spawn と約束の CreatePromise / CompletePromise・
;; 戻りの問い AwaitRecordsBack・上限つきの待ち WaitWithin)・源と見張りの止め(Cancel・Wait)。本体の待ちは源と競わない(#3135)。
;; WatchChanges・WatchEvents と位置の読みの effect は出さない。
(val MEMORY-SOURCE-EFFECTS #(CreateExternalPromise PublishEffect ReadSourcePatience GetTimeEffect AwaitRecordsBack CreatePromise CompletePromise
                             WaitWithinEffect Spawn Cancel Wait))


(deff memory-signal-handler [store bindings subscriber]  ; defk にできない: with-handlers の列に置く素の工場の関数 — 閉じの検の道具が宣言を読む形(records-signal-handler と同じ)
  {:pre [(: store MemoryStore) (: bindings (get tuple #(SignalTables ...))) (: subscriber str)] :post [(: % (get Callable #([object] Program)))]}
  "memory の置き場 store の書きを合図として発する源で本体を包む関数を作るため(模擬の組の源 — 素の工場で、with-handlers の列に置く)。bindings =
   SignalTables の tuple / subscriber = 購読者の名前。包んだ本体の間だけ源の task が動く。"
  (BodyWrapper run-memory-source store bindings subscriber))

;; 何にも答えず(__doeff_handles__ = ())、本体の周りで MEMORY-SOURCE-EFFECTS を出す、という宣言(doeff-effect-analyzer の body wrapper の読み)。
(setv memory-signal-handler.__doeff_handles__ #()
      memory-signal-handler.__doeff_effects__ MEMORY-SOURCE-EFFECTS)


(defk memory-signal-source [store]
  {:pre [(: store MemoryStore)] :post [(: % SignalSourceFactory)] :tags {:context "records" :role "foundation"}}
  "模擬の土台が鍵 SignalSourceFactory に答える値を作るため: make = 置き場 store を閉じた memory-signal-handler(本番の土台の
   RECORDS-SIGNAL-SOURCE と同じ鍵・同じ合図の形)。"
  (SignalSourceFactory :make (partial memory-signal-handler store)))
