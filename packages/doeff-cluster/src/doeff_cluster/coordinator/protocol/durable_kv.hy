;;; coordinator の耐久の状態を「キー → JSON の値」の表として見る形(純粋な関数)。
;;;
;;; 永続化(foundation/wal_store.hy)は、1 回の調停(要求のまとまり)ごとに変わったキーだけを追記の log に 1 行で書き、fsync を 1 回して
;;; から返事をする(group commit)。キーに分けるのは、変わった物だけを書くため: 大きな盤の区画(約 2.3 MB)の書きが
;;; Service や Rollout の書きに引きずられず、逆に Service の書きが盤全体を書き直さない。
;;;
;;; キー:
;;;   service/<名>  placement/<名>  worker/<名>  task/<id>  meta/<Kind/名>  rollout/<名>  audit/<番号 10 桁>  counter
;;;   drain/<worker の名>  surge/<名>(2026-09-25 — それより前の置き場には無い = 空として読む。旧い版の coordinator はこの鍵を読まずに無視する)
;;;   handoff/<名>(2026-09-26 — 入れ替えの期限の見張り HandoffWatch。無い置き場は空として読む)
;;;   keep/<名>(#2804 — 途絶しても動かし続けてよい印の約束 KeepMark {job worker boot since_ms}。無い置き場は空として読む。旧い版の
;;;   coordinator はこの鍵を読まずに無視する — 印を約束しない今までの振る舞い)
;;;   board/<盤のキー> = {"value" … "resourceVersion" …}
;;; worker/<名> は最後の連絡の時刻 lastSeenMs を持つ(2026-09-25 — heartbeat ごとではなく api_policy.mark-alive の拍ごとの写し)。
;;; 世代の順 boot・retired と今の世代の起動時刻 bootAt(2026-09-27 — cluster_policy.generation-order)も持つ(無い鍵は世代・起動時刻を知らない)。
;;; task のために空けておく数 taskReserve も必ず持つ(#3489 — この欄の無い行は読まず、次の heartbeat で作り直す)。
;;; 最後に終わったと知れた刻と今の世代で process を持つ job の列 knownExits も持つ(#3672 — state_json.worker-generations-json。
;;; 中身が替わるのは process の起き・終わり・世代の入れ替わりの時だけで、heartbeat ごとには書かない。無い行は空の列として読む)。
;;; 保存しない物(状態の報告・readiness・k8s の観測)は入れない。
;;;
;;; 置き先の鍵の改名(2026-09-25): 置き先(job をどの worker に置いたか)の鍵は placement/<名>。改名の前に書いた置き場には
;;; 旧い接頭辞(LEGACY-PLACEMENT)の鍵が残っているので、読みは両方を読み(同じ名なら新しい鍵が勝つ)、起動時に
;;; legacy-key-moves の 2 つの書きで新しい鍵へ移す — 新しい鍵を書き終えてから旧い鍵を消す。
(require doeff-hy.macros [deff defk <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "coordinator" :role "protocol"})
(import dataclasses [asdict dataclass replace])
(import collections.abc [Callable])
(import functools [partial])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState WorkerInfo Placement Drain BoardRow KeepMark])
(import doeff_cluster.coordinator.core.cluster_rules [component-versions-of])
(import doeff_cluster.coordinator.protocol.cluster_json [task-record-to-json task-record-from-json handoff-watch-from-json])
(import doeff_cluster.coordinator.core.cluster_policy [job-to-json job-from-json board-changes value-size
] doeff_cluster.coordinator.protocol.state_json [rollout-row-to-json rollout-row-from-json audit-event-to-json audit-event-from-json read-service-rows warm-entry-to-json warm-entry-from-json worker-capabilities-of worker-generations-json worker-generations-from-json program-row-to-json program-row-from-json resource-meta-to-json resource-meta-from-json read-each])

(setv BOARD "board/")
(setv PLACEMENT "placement/")
(setv DRAIN "drain/" SURGE "surge/" WARM "warm/" PROGRAM "program/")
(val HANDOFF "handoff/")
(val KEEP "keep/")
;; 改名の前の置き先の鍵(値の形は同じ)。起動時に読んで移すだけ。
(setv LEGACY-PLACEMENT "assignment/") ; 新しく書くのには使わない(語彙の規則の旧い語 — 読みの互換のためだけに残す)


(deff board-entry [#^ ClusterState state #^ str key]  ; defk にできない: SaveState の答え手 durable-states と起動の読み直し(Program の外)が呼ぶ純粋な綴り
  {:pre [(: state ClusterState) (: key str)] :post [(: % dict)] :tags {:context "coordinator" :role "protocol" :spells "json"}}
  "盤の行 1 つの耐久の形 {value resourceVersion [expiresMs]}。期限の無い行は expiresMs を持たない(2026-09-25 より前の形と同じ)。"
  (setv row (get state.board key))
  (| {"value" row.value "resourceVersion" row.version}
     (if (is row.expires-ms None) {} {"expiresMs" row.expires-ms})))


(deff counter-json [#^ ClusterState state]  ; defk にできない: SaveState の答え手 durable-states と起動の読み直し(Program の外)が呼ぶ純粋な綴り
  {:pre [(: state ClusterState)] :post [(: % dict)] :tags {:context "coordinator" :role "protocol" :spells "json"}}
  "鍵 counter の値。"
  (| {"nextTask" state.next-task "revision" state.revision "auditSeq" state.audit-seq "aliveMs" state.alive-ms}
     ;; task の id の頭(以前からの "t" は書かない — 以前の形と同じ)。
     (if (= state.task-prefix "t") {} {"taskPrefix" state.task-prefix})))


(deff worker-json [#^ WorkerInfo w]  ; defk にできない: SaveState の答え手 durable-states と起動の読み直し(Program の外)が呼ぶ純粋な綴り
  {:pre [(: w WorkerInfo)] :post [(: % dict)] :tags {:context "coordinator" :role "protocol" :spells "json"}}
  "鍵 worker/<名> の値。lastSeenMs は生存の印の拍で写した w.seen-mark(heartbeat ごとに進む last-seen-ms は書かない — 書きは
   印の拍ごと・#2903 の前は ClusterState.seen-marks から引いた)。"
  (| {"name" w.name "provides" (list w.provides) "exclusive" (list w.exclusive) "node" w.node "capacity" w.capacity
      "taskReserve" w.task-reserve
      "versions" (dict w.versions)}
     (worker-generations-json w)
     (if (is w.seen-mark None) {} {"lastSeenMs" w.seen-mark})))


(defn #^ object as-stored [#^ object value]
  "そのまま保存する値(JSON の形で持っている行)の直列化 = 値そのもの。"
  value)


;; 鍵の組(#2716): 状態の欄の組 → その欄だけから作る鍵。組ごとに鍵の接頭辞が重ならない(counter・service/・placement/・worker/・
;; task/・meta/・rollout/・drain/・surge/・warm/・program/・handoff/・audit/)ので、組ごとに作った差分の和は、全部をまとめて作った差分と
;; 同じ。durable-delta は、欄が前後で全部同じ object の組の鍵を作らずに飛ばす — 状態は replace で作り直す(欄の写像をその場で書き換え
;; ない)ので、同じ object の欄から作る鍵は前後で同じ。並びは durable-sources の鍵の順。service/ は宣言の行の後に断った行(refused)を
;; 書き、同じ名なら後が勝つ。
;; 組の作り手の答え = 鍵 → #(元の値の tuple 直列化の関数)。
(val Sources (get dict #(str (get tuple #((get tuple #(object ...)) (get Callable #([] object)))))))


(defrecord SourceGroup
  "鍵の組 1 つ(#2716): fields = 組の鍵を決める ClusterState の欄の Python の名(getattr で前後を比べる)・build = その欄だけから
   組の鍵を作る(答えは Sources)。"
  (#^ (get tuple #(str ...)) fields)
  (#^ (get Callable #([ClusterState] Sources)) build))


(val SOURCE-GROUPS
  #((SourceGroup :fields #("next_task" "revision" "audit_seq" "alive_ms" "task_prefix")
                 :build (fn #^ Sources [#^ ClusterState state]
                          {"counter" #(#(state.next-task state.revision state.audit-seq state.alive-ms state.task-prefix)
                                       (partial counter-json state))}))
    (SourceGroup :fields #("jobs" "refused")
                 :build (fn #^ Sources [#^ ClusterState state]
                          (setv #^ Sources declared (dfor j state.jobs (+ "service/" j.spec.name) #(#(j) (partial job-to-json j))))
                          (setv #^ Sources refused
                                (dfor r (.values state.refused) (+ "service/" r.name) #(#(r.row) (partial as-stored r.row))))
                          (| declared refused)))
    (SourceGroup :fields #("placements")
                 :build (fn #^ Sources [#^ ClusterState state]
                          (dfor #(k a) (.items state.placements) (+ PLACEMENT k) #(#(a) (partial asdict a)))))
    (SourceGroup :fields #("workers")
                 :build (fn #^ Sources [#^ ClusterState state]
                          (dfor w (.values state.workers) (+ "worker/" w.name) #(#(w) (partial worker-json w)))))
    (SourceGroup :fields #("tasks")
                 :build (fn #^ Sources [#^ ClusterState state]
                          (dfor t (.values state.tasks) (+ "task/" t.id) #(#(t) (partial task-record-to-json t)))))
    (SourceGroup :fields #("meta")
                 :build (fn #^ Sources [#^ ClusterState state]
                          (dfor #(k m) (.items state.meta) (+ "meta/" k) #(#(m) (partial resource-meta-to-json m)))))
    (SourceGroup :fields #("rollouts")
                 :build (fn #^ Sources [#^ ClusterState state]
                          (dfor #(k r) (.items state.rollouts) (+ "rollout/" k) #(#(r) (partial rollout-row-to-json r)))))
    (SourceGroup :fields #("drains")
                 :build (fn #^ Sources [#^ ClusterState state]
                          (dfor #(k d) (.items state.drains) (+ DRAIN k) #(#(d) (partial asdict d)))))
    (SourceGroup :fields #("surges")
                 :build (fn #^ Sources [#^ ClusterState state]
                          (dfor #(k a) (.items state.surges) (+ SURGE k) #(#(a) (partial asdict a)))))
    (SourceGroup :fields #("warms")
                 :build (fn #^ Sources [#^ ClusterState state]
                          (dfor #(k w) (.items state.warms) (+ WARM k) #(#(w) (partial warm-entry-to-json w)))))
    (SourceGroup :fields #("programs")
                 :build (fn #^ Sources [#^ ClusterState state]
                          (dfor #(k p) (.items state.programs) (+ PROGRAM k) #(#(p) (partial program-row-to-json p)))))
    (SourceGroup :fields #("handoffs")
                 :build (fn #^ Sources [#^ ClusterState state]
                          (dfor #(k w) (.items state.handoffs) (+ HANDOFF k) #(#(w) w.to-json))))
    (SourceGroup :fields #("keep_marks")
                 :build (fn #^ Sources [#^ ClusterState state]
                          (dfor m state.keep-marks (+ KEEP m.job) #(#(m) (partial asdict m)))))
    (SourceGroup :fields #("audit")
                 :build (fn #^ Sources [#^ ClusterState state]
                          (dfor e state.audit (.format "audit/{:010d}" e.seq) #(#(e) (partial audit-event-to-json e)))))))


(defk durable-sources [state]
  {:pre [(: state ClusterState)] :post [(: % dict)] :tags {:context "coordinator" :role "protocol" :spells "json"}}
  "盤を除いた耐久の状態の鍵 → #(元の値の tuple 直列化の関数)。鍵と値の形の定義はここ 1 か所(durable-kv も durable-delta もここから作る)。
  元の値 = 鍵の値を決める状態の部品(dataclass・dict・数)。状態は replace で作り直すので、前と後で同じ物の部品は変わっていない。
  同じ鍵を 2 度書く所(service/ — 宣言の行の後に断った行)は、後の書きが勝つ(以前の形と同じ順)。"
  ;; 鍵の形の定義は上の SOURCE-GROUPS の組ごと(#2716)— ここはその和を組の順に並べるだけ(同じ鍵は後の組・後の行が勝つ)。
  (dict (gfor g SOURCE-GROUPS #(k source) (.items (g.build state)) #(k source))))


(defk durable-kv [state]
  {:pre [(: state ClusterState)] :post [(: % dict)] :tags {:context "coordinator" :role "protocol" :spells "json"}}
  "盤を除いた耐久の状態のキーの表。"
  ;; 鍵ごとの綴り(encode — SOURCE-GROUPS の遅延の関数)は deff のままの素の呼び。綴りを defk にするのは、列を回しながら <- して集める
  ;; 形の道具が決まってから(#2761 の表の「待つ側」)。
  (<- sources dict (durable-sources state))
  (dfor #(k #(_ encode)) (.items sources) k (encode)))


(defn #^ bool same-parts [#^ tuple before #^ tuple after]
  "元の値の tuple が部品ごとに同じ物か(同一性 — 等しさではない。等しいが別の物なら直列化して比べる側へ回す)。"
  (and (= (len before) (len after))
       (all (gfor #(x y) (zip before after) (is x y)))))


(defk durable-delta [before after]
  {:pre [(: before ClusterState) (: after ClusterState)] :post [(: % dict)] :tags {:context "coordinator" :role "protocol" :spells "json"}}
  "変わったキー → 新しい値(消えたキーは None)— 前と後を丸ごと durable-kv にして比べた答えと 1 字も違わない(#1843)。
  元の値が同じ物の鍵は直列化しない(1 拍で変わるのは一握りの鍵なので、拍の費用が状態の大きさに比例しない)。同じ物でない鍵は
  前と後を直列化して比べる(作り直したが中身の同じ値は差分に入れない)。盤は同一性で比べる(cluster_policy.board-changes)。"
  ;; 欄が前後で全部同じ object の組(SOURCE-GROUPS)は、鍵の部品を作らずに飛ばす(#2716 — 1 拍で変わるのは一握りの欄なので、拍の費用が
  ;; 変わらない欄の大きさに比例しない)。残りの組だけ前後の部品を作り、鍵ごとに以前と同じ比べ方をする。組ごとに鍵が重ならないので、
  ;; 答えは全部の組を作った時と同じ。
  (setv groups (tuple (gfor g SOURCE-GROUPS
                            :if (not (all (gfor f g.fields (is (getattr before f) (getattr after f)))))
                            #((g.build before) (g.build after)))))
  (setv written (dfor #(old new) groups
                      #(k #(parts encode)) (.items new)
                      :setv prior (.get old k)
                      :if (or (is prior None) (not (same-parts (get prior 0) parts)))
                      :setv value (encode)
                      :if (or (is prior None) (!= ((get prior 1)) value))
                      k value))
  (setv removed (dfor #(old new) groups k old :if (not-in k new) k None))
  (setv #^ (get dict #(str (| (get dict #(str object)) None))) board
        (dfor key (board-changes before after)
              (+ BOARD key) (if (in key after.board) (board-entry after key) None)))
  (| written removed board))


(defk full-kv [state]
  {:pre [(: state ClusterState)] :post [(: % dict)] :tags {:context "coordinator" :role "protocol" :spells "json"}}
  "盤を含む全部のキーの表(まとめ直しと移しの時だけ)。"
  (| (! (durable-kv state))
     (dfor k state.board (+ BOARD k) (board-entry state k))))


(defk state-from-kv [kv now]
  {:pre [(: kv dict) (: now int)] :post [(: % ClusterState)] :tags {:context "coordinator" :role "protocol" :reads "json"}}
  "キーの表から状態を作り直す。worker の最後の連絡の時刻は保存した lastSeenMs(止まる前の最後の印の拍の値)。呼び手は
   api_policy.resume-after-downtime で止まっていた長さだけずらしてから使う。lastSeenMs の無い鍵(2026-09-25 より前の置き場・
   最後の印より後に加わった worker)は、最後の印の時刻(alive-ms)に連絡があったとみなす(ずらすと「いま」になる = 以前の形と同じ。
   生存を捨てると、最初に heartbeat を送った worker へ全 job が移り、元の担い手がまだ動いていれば二重に動く — 実測 2026-09-23)。
   印の時刻も無い置き場は now。"
  ;; 鍵の接頭辞 prefix の行を #(接頭辞を外した鍵 値) の並び(鍵の順)にする。
  (val part (fn [prefix] (sorted (gfor #(k v) (.items kv) :if (.startswith k prefix) #((cut k (len prefix) None) v)))))
  (val counter (.get kv "counter" {}))
  (val alive-ms (.get counter "aliveMs" 0))
  (val unknown-seen (if (> alive-ms 0) alive-ms now))
  ;; 読めない Service の行(旧い宣言の形)は落とさず RefusedJob にする(改訂 1 の C)。
  (val service-rows (read-service-rows (lfor #(_ v) (part "service/") v)))
  ;; 旧い形(labels だけ・task のために空けておく数 taskReserve の無い行)の worker の行は読まない(state_json.state-from-json と同じ —
  ;; 次の heartbeat で作り直す。taskReserve を既定の値で埋めない)。
  (val stored-workers (tuple (gfor #(k w) (part "worker/") :if (and (in "provides" w) (in "taskReserve" w)) #(k w))))
  (<- generations tuple (read-each worker-generations-from-json stored-workers))
  (<- tasks tuple (read-each task-record-from-json (tuple (part "task/"))))
  (<- warms tuple (read-each warm-entry-from-json (tuple (part WARM))))
  (<- programs tuple (read-each program-row-from-json (tuple (part PROGRAM))))
  (<- audit tuple (read-each audit-event-from-json (tuple (part "audit/"))))
  (ClusterState
    :jobs (get service-rows 0)
    :refused (get service-rows 1)
    :placements (dfor #(k v) (+ (part LEGACY-PLACEMENT) (part PLACEMENT)) k (Placement #** v)) ; 後に並ぶ新しい鍵が勝つ
    :workers (dfor #(#(k w) #(_ generation)) (zip stored-workers generations)
                   :setv caps (worker-capabilities-of w (.format "保存の worker {}" (get w "name")))
                   k (WorkerInfo (get w "name") (get caps 0) (get w "capacity")
                                 (.get w "lastSeenMs" unknown-seen)
                                 (component-versions-of (.get w "versions" {}))
                                 :exclusive (get caps 1) :node (.get w "node" "")
                                 :task-reserve (get w "taskReserve")
                                 :seen-mark (.get w "lastSeenMs")
                                 #** generation))
    :tasks (dict tasks)
    :next-task (.get counter "nextTask" 1)
    :task-prefix (.get counter "taskPrefix" "t")
    :revision (.get counter "revision" 0)
    :audit-seq (.get counter "auditSeq" 0)
    :alive-ms alive-ms
    :meta (dfor #(k v) (part "meta/") k (resource-meta-from-json v))
    :rollouts (dfor #(k v) (part "rollout/") k (rollout-row-from-json v))
    :drains (dfor #(k v) (part DRAIN) k (Drain #** v))
    :surges (dfor #(k v) (part SURGE) k (Placement #** v))
    ;; 旧い形の温める表の行は読みが None を返す(読み直しで捨てる)。
    :warms (dfor #(k entry) warms :if (is-not entry None) k entry)
    :programs (dict programs)
    :handoffs (dfor #(k v) (part HANDOFF) k (handoff-watch-from-json v))
    :keep-marks (tuple (gfor #(_ v) (part KEEP) (KeepMark #** v)))
    :audit (tuple (gfor #(_ event) audit event))
    :board (dfor #(k v) (part BOARD) k (BoardRow :value (get v "value") :version (get v "resourceVersion")
                                                 :expires-ms (.get v "expiresMs") :size (value-size (get v "value"))))
    :started-ms now))


(defk resume-writes [kv state]
  {:pre [(: kv dict) (: state ClusterState)] :post [(: % dict)] :tags {:context "coordinator" :role "protocol" :spells "json"}}
  "起動の時に書く分: 読み直した置き場(kv)と、止まっていた長さだけ時計をずらした状態(api_policy.resume-after-downtime)の、
   値の違う鍵(盤を除く)。ずらした値(worker の lastSeenMs・task の lease・Rollout の段の起点)と生きていた時刻(counter の aliveMs)を
   同じ 1 行で耐久にする。書かないと、ずらした値は次にその鍵が変わるまで置き場に載らず(沈黙している worker の鍵は二度と変わらない)、
   2 回目の再起動で 1 回目の止まっていた長さを沈黙・経過に数えてしまう(2026-09-25)。"
  (<- current dict (durable-kv state))
  (dfor #(k v) (.items current) :if (!= (.get kv k) v) k v))


(defk legacy-key-moves [kv]
  {:pre [(: kv dict)] :post [(: % list)] :tags {:context "coordinator" :role "protocol" :reads "json"}}
  "改名の前の置き先の鍵を新しい鍵へ移す書きの列(順に Persist する)。1 つめ = 新しい鍵がまだ無い分を新しい鍵で書く・
   2 つめ = 旧い鍵を消す。旧い鍵を消すのは、新しい鍵を書き終えた後だけ。同じ名の新しい鍵が既に在れば、そちらを残す。
   旧い鍵が無ければ空(2 回目以降の起動は何も書かない)。"
  (val legacy (sorted (gfor k kv :if (.startswith k LEGACY-PLACEMENT) k)))
  (when (not legacy)
    (return []))
  (val writes (dfor k legacy
                     :setv new (+ PLACEMENT (cut k (len LEGACY-PLACEMENT) None))
                     :if (not-in new kv)
                     new (get kv k)))
  (+ (if writes [writes] []) [(dfor k legacy k None)]))
