;;; coordinator の耐久の状態を「キー → JSON の値」の表として見る形(純粋な関数)。
;;;
;;; 永続化(wal_store.hy)は、1 回の調停(要求のまとまり)ごとに変わったキーだけを追記の log に 1 行で書き、fsync を 1 回して
;;; から返事をする(group commit)。キーに分けるのは、変わった物だけを書くため: 大きな盤の区画(約 2.3 MB)の書きが
;;; Service や Rollout の書きに引きずられず、逆に Service の書きが盤全体を書き直さない。
;;;
;;; キー:
;;;   service/<名>  placement/<名>  worker/<名>  task/<id>  meta/<Kind/名>  rollout/<名>  audit/<番号 10 桁>  counter
;;;   drain/<worker の名>  surge/<名>(2026-09-25 — それより前の置き場には無い = 空として読む。旧い版の coordinator はこの鍵を読まずに無視する)
;;;   board/<盤のキー> = {"value" … "resourceVersion" …}
;;; worker/<名> は最後の連絡の時刻 lastSeenMs を持つ(2026-09-25 — heartbeat ごとではなく api_policy.mark-alive の拍ごとの写し)。
;;; 保存しない物(状態の報告・readiness・k8s の観測)は入れない。
;;;
;;; 置き先の鍵の改名(2026-09-25): 置き先(job をどの worker に置いたか)の鍵は placement/<名>。改名の前に書いた置き場には
;;; 旧い接頭辞(LEGACY-PLACEMENT)の鍵が残っているので、読みは両方を読み(同じ名なら新しい鍵が勝つ)、起動時に
;;; legacy-key-moves の 2 つの書きで新しい鍵へ移す — 新しい鍵を書き終えてから旧い鍵を消す。
(import dataclasses [asdict replace])
(import .cluster_model [ClusterState WorkerInfo Placement TaskRecord Drain])
(import .cluster_policy [job-to-json job-from-json board-changes value-size])

(setv BOARD "board/")
(setv PLACEMENT "placement/")
(setv DRAIN "drain/" SURGE "surge/")
;; 改名の前の置き先の鍵(値の形は同じ)。起動時に読んで移すだけ。
(setv LEGACY-PLACEMENT "assignment/") ; 新しく書くのには使わない(語彙の規則の旧い語 — 読みの互換のためだけに残す)


(defn #^ dict board-entry [#^ ClusterState state #^ str key]
  "盤の行 1 つの耐久の形 {value resourceVersion [expiresMs]}。期限の無い行は expiresMs を持たない(2026-09-25 より前の形と同じ)。"
  (setv expires (.get state.board-expiry key))
  (| {"value" (get state.board key) "resourceVersion" (.get state.board-versions key 1)}
     (if (is expires None) {} {"expiresMs" expires})))


(defn #^ dict durable-kv [#^ ClusterState state]
  "盤を除いた耐久の状態のキーの表。"
  (setv kv {"counter" {"nextTask" state.next-task "revision" state.revision "auditSeq" state.audit-seq "aliveMs" state.alive-ms}})
  (for [j state.jobs] (setv (get kv (+ "service/" j.spec.name)) (job-to-json j)))
  (for [#(k a) (.items state.placements)] (setv (get kv (+ PLACEMENT k)) (asdict a)))
  (for [w (.values state.workers)]
    (setv seen (.get state.seen-marks w.name))
    (setv (get kv (+ "worker/" w.name)) (| {"name" w.name "labels" (dict w.labels) "capacity" w.capacity "versions" (dict w.versions)}
                                           (if (is seen None) {} {"lastSeenMs" seen}))))
  (for [t (.values state.tasks)]
    (setv (get kv (+ "task/" t.id)) (| (asdict t) {"versions" (dict t.versions) "requires" (dict t.requires)})))
  (for [#(k m) (.items state.meta)] (setv (get kv (+ "meta/" k)) m))
  (for [#(k r) (.items state.rollouts)] (setv (get kv (+ "rollout/" k)) r))
  (for [#(k d) (.items state.drains)] (setv (get kv (+ DRAIN k)) (asdict d)))
  (for [#(k a) (.items state.surges)] (setv (get kv (+ SURGE k)) (asdict a)))
  (for [e state.audit] (setv (get kv (.format "audit/{:010d}" (get e "seq"))) e))
  kv)


(defn #^ dict kv-delta [#^ dict before-kv #^ dict after-kv #^ ClusterState before #^ ClusterState after]
  "変わったキー → 新しい値(消えたキーは None)。盤は同一性で比べる(cluster_policy.board-changes)。"
  (setv delta {})
  (for [#(k v) (.items after-kv)]
    (when (!= (.get before-kv k) v) (setv (get delta k) v)))
  (for [k before-kv]
    (when (not-in k after-kv) (setv (get delta k) None)))
  (for [key (board-changes before after)]
    (setv (get delta (+ BOARD key))
          (if (in key after.board)
              (board-entry after key)
              None)))
  delta)


(defn #^ dict full-kv [#^ ClusterState state]
  "盤を含む全部のキーの表(まとめ直しと移しの時だけ)。"
  (| (durable-kv state)
     (dfor k state.board (+ BOARD k) (board-entry state k))))


(defn #^ ClusterState state-from-kv [#^ dict kv #^ int now]
  "キーの表から状態を作り直す。worker の最後の連絡の時刻は保存した lastSeenMs(止まる前の最後の印の拍の値)。呼び手は
   api_policy.resume-after-downtime で止まっていた長さだけずらしてから使う。lastSeenMs の無い鍵(2026-09-25 より前の置き場・
   最後の印より後に加わった worker)は、最後の印の時刻(alive-ms)に連絡があったとみなす(ずらすと「いま」になる = 以前の形と同じ。
   生存を捨てると、最初に heartbeat を送った worker へ全 job が移り、元の担い手がまだ動いていれば二重に動く — 実測 2026-09-23)。
   印の時刻も無い置き場は now。"
  (defn part [prefix] (sorted (gfor #(k v) (.items kv) :if (.startswith k prefix) #((cut k (len prefix) None) v))))
  (setv counter (.get kv "counter" {}))
  (setv alive-ms (.get counter "aliveMs" 0) unknown-seen (if (> alive-ms 0) alive-ms now))
  (ClusterState
    :jobs (tuple (gfor #(_ v) (part "service/") (job-from-json v)))
    :placements (dfor #(k v) (+ (part LEGACY-PLACEMENT) (part PLACEMENT)) k (Placement #** v)) ; 後に並ぶ新しい鍵が勝つ
    :workers (dfor #(k w) (part "worker/")
                   k (WorkerInfo (get w "name") (tuple (sorted (.items (get w "labels")))) (get w "capacity")
                                 (.get w "lastSeenMs" unknown-seen)
                                 (tuple (sorted (.items (.get w "versions" {}))))))
    :seen-marks (dfor #(k w) (part "worker/") :if (in "lastSeenMs" w) k (get w "lastSeenMs"))
    :tasks (dfor #(k t) (part "task/")
                 k (TaskRecord #** (| t {"versions" (tuple (sorted (.items (get t "versions"))))
                                         "requires" (tuple (sorted (.items (get t "requires"))))})))
    :next-task (.get counter "nextTask" 1)
    :revision (.get counter "revision" 0)
    :audit-seq (.get counter "auditSeq" 0)
    :alive-ms alive-ms
    :meta (dict (part "meta/"))
    :rollouts (dict (part "rollout/"))
    :drains (dfor #(k v) (part DRAIN) k (Drain #** v))
    :surges (dfor #(k v) (part SURGE) k (Placement #** v))
    :audit (tuple (gfor #(_ e) (part "audit/") e))
    :board (dfor #(k v) (part BOARD) k (get v "value"))
    :board-versions (dfor #(k v) (part BOARD) k (get v "resourceVersion"))
    :board-expiry (dfor #(k v) (part BOARD) :if (is-not (.get v "expiresMs") None) k (get v "expiresMs"))
    :board-sizes (dfor #(k v) (part BOARD) k (value-size (get v "value")))
    :started-ms now))


(defn #^ dict resume-writes [#^ dict kv #^ ClusterState state]
  "起動の時に書く分: 読み直した置き場(kv)と、止まっていた長さだけ時計をずらした状態(api_policy.resume-after-downtime)の、
   値の違う鍵(盤を除く)。ずらした値(worker の lastSeenMs・task の lease・Rollout の段の起点)と生きていた時刻(counter の aliveMs)を
   同じ 1 行で耐久にする。書かないと、ずらした値は次にその鍵が変わるまで置き場に載らず(沈黙している worker の鍵は二度と変わらない)、
   2 回目の再起動で 1 回目の止まっていた長さを沈黙・経過に数えてしまう(2026-09-25)。"
  (dfor #(k v) (.items (durable-kv state)) :if (!= (.get kv k) v) k v))


(defn #^ list legacy-key-moves [#^ dict kv]
  "改名の前の置き先の鍵を新しい鍵へ移す書きの列(順に Persist する)。1 つめ = 新しい鍵がまだ無い分を新しい鍵で書く・
   2 つめ = 旧い鍵を消す。旧い鍵を消すのは、新しい鍵を書き終えた後だけ。同じ名の新しい鍵が既に在れば、そちらを残す。
   旧い鍵が無ければ空(2 回目以降の起動は何も書かない)。"
  (setv legacy (sorted (gfor k kv :if (.startswith k LEGACY-PLACEMENT) k)))
  (when (not legacy)
    (return []))
  (setv writes (dfor k legacy
                     :setv new (+ PLACEMENT (cut k (len LEGACY-PLACEMENT) None))
                     :if (not-in new kv)
                     new (get kv k)))
  (+ (if writes [writes] []) [(dfor k legacy k None)]))
