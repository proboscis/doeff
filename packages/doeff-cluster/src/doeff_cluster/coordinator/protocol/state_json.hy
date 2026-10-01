;;; coordinator の状態の保存の綴り(state file の JSON と、durable の KV の行の値)— ClusterState と欄の型を JSON へ書き・JSON から読む
;;; 純粋な関数(core/cluster_policy から移した・#2448)。書き手の手前の protocol(durable_kv・store の durable-states・entry の
;;; state file の読み)だけが呼ぶ。core の判断は JSON の保存の形を知らない。
(require doeff-hy.macros [deff val])
(val MODULE-TAGS {:context "coordinator" :role "protocol"})
(import dataclasses [asdict])
(import doeff_cluster.coordinator.intent.cluster_model [WorkerInfo AuditEvent RolloutRow BoardRow ClusterState WarmEntry RefusedJob ProgramRow ResourceMeta Placement Drain])
(import doeff_cluster.coordinator.core.cluster_rules [component-versions-of])
(import doeff_cluster.coordinator.protocol.cluster_json [task-record-to-json task-record-from-json handoff-watch-from-json])
(import doeff_cluster.coordinator.core.rollout_policy [validate-rollout-spec rollout-spec-to-json rollout-status-to-json rollout-status-from-json])
(import doeff_cluster.coordinator.core.cluster_policy [job-from-json job-to-json named-capabilities value-size audit-event-to-json boot-at-of])
(import doeff_cluster.shared.core.capabilities [capabilities-of])


(deff read-service-rows [#^ list rows]  ; defk にできない: 保存の読み直し(state file・durable KV — Program の外)が呼ぶ純粋な判断
  {:pre [(: rows list)] :post [(: % tuple) (= (len %) 2)] :tags {:context "doeff-cluster" :role "protocol"}}
  "保存の Service の行の列 → #(受け付けた ClusterJob の tuple  名 → RefusedJob)。読めない行(旧い宣言の形・壊れた行)は落とさずに
   RefusedJob にして理由を持つ — 新しい coordinator が旧い置き場を読んで落ちないため(改訂 1 の C)。"
  (setv jobs [] refused {})
  (for [row rows]
    (try
      (.append jobs (job-from-json row))
      (except [error [KeyError TypeError ValueError]]
        (setv name (str (.get row "name" "?")))
        (setv (get refused name) (RefusedJob :name name :row row :reason (.format "{}: {}" (. (type error) __name__) error))))))
  #((tuple jobs) refused))


(defn #^ dict state-to-json [#^ ClusterState state]
  "資源の状態の保存の形。盤は入れない(盤は行ごとに別の file — SaveBoardRow)。"
  {"formatVersion" 2
   "jobs" (+ (lfor j state.jobs (job-to-json j)) (lfor r (.values state.refused) r.row))
   "programs" (dfor #(k p) (.items state.programs) k (program-row-to-json p))
   "placements" (dfor #(k v) (.items state.placements) k (asdict v))
   "workers" (lfor w (.values state.workers)
                   (| {"name" w.name "provides" (list w.provides) "exclusive" (list w.exclusive) "node" w.node "capacity" w.capacity
                       "versions" (dict w.versions)}
                      (worker-generations-json w)))
   "tasks" (lfor t (.values state.tasks) (task-record-to-json t))
   "nextTask" state.next-task
   "taskPrefix" state.task-prefix
   "meta" (dfor #(k m) (.items state.meta) k (resource-meta-to-json m))
   "revision" state.revision
   "audit" (lfor e state.audit (audit-event-to-json e))
   "auditSeq" state.audit-seq
   "rollouts" (dfor #(k r) (.items state.rollouts) k (rollout-row-to-json r))
   "drains" (dfor #(k v) (.items state.drains) k (asdict v))
   "surges" (dfor #(k v) (.items state.surges) k (asdict v))
   "warms" (dfor #(k v) (.items state.warms) k (warm-entry-to-json v))
   "handoffs" (dfor #(k w) (.items state.handoffs) k (.to-json w))})


(defn #^ dict worker-generations-json [#^ WorkerInfo worker]
  "worker の世代の順(今の世代と退いた世代)を保存の形へ写すため(state file と durable の KV が使う)。読み直した後も、退いた世代の
   heartbeat を新しい世代と取り違えない。世代を知らない worker は欄を持たない(2026-09-27 より前の形と同じ)。
   bootAt = 今の世代の起動時刻(知る時だけ — generation-order)。"
  (| (if (is worker.boot None) {} {"boot" worker.boot})
     (if worker.retired {"retired" (list worker.retired)} {})
     (if (is worker.boot-at None) {} {"bootAt" worker.boot-at})))


(defn #^ dict worker-generations-from-json [#^ dict data]
  "保存の形 → WorkerInfo の世代の欄(worker-generations-json の逆)。欄の無い旧い形は世代・起動時刻を知らない。"
  {"boot" (.get data "boot") "retired" (tuple (.get data "retired" [])) "boot_at" (boot-at-of data)})


(defn #^ dict resource-meta-to-json [#^ ResourceMeta meta]
  "資源の版の記録 → 保存の JSON の形(state file と durable の KV が使う — #2447 の前の形と同じ)。"
  {"resourceVersion" meta.resource-version "generation" meta.generation "createdBy" meta.created-by "createdMs" meta.created-ms
   "updatedBy" meta.updated-by "updatedMs" meta.updated-ms})


(defn #^ ResourceMeta resource-meta-from-json [#^ dict data]
  "保存の JSON の形 → 資源の版の記録(resource-meta-to-json の逆)。"
  (ResourceMeta :resource-version (int (get data "resourceVersion")) :generation (int (get data "generation"))
                :created-by (str (.get data "createdBy" "")) :created-ms (int (.get data "createdMs" 0))
                :updated-by (str (.get data "updatedBy" "")) :updated-ms (int (.get data "updatedMs" 0))))


(defn #^ dict rollout-row-to-json [#^ RolloutRow row]
  "Rollout 1 つ → 保存の JSON の形 {spec status}(state file と durable の KV が使う — #2447 の前の形と同じ)。"
  {"spec" (rollout-spec-to-json row.spec) "status" (rollout-status-to-json row.status)})


(defn #^ RolloutRow rollout-row-from-json [#^ dict data]
  "保存の JSON の形 → Rollout 1 つ(rollout-row-to-json の逆)。"
  (RolloutRow :spec (validate-rollout-spec (get data "spec")) :status (rollout-status-from-json (get data "status"))))


(defn #^ AuditEvent audit-event-from-json [#^ dict data]
  "保存の JSON の形 → 出来事の記録 1 件(audit-event-to-json の逆)。"
  (AuditEvent :seq (int (get data "seq")) :at (int (get data "at")) :actor (str (get data "actor")) :verb (str (get data "verb"))
              :kind (str (get data "kind")) :name (str (get data "name"))
              :from-version (.get data "fromVersion") :to-version (.get data "toVersion") :generation (.get data "generation")
              :changes (dict (.get data "changes" {}))))


(defn #^ dict program-row-to-json [#^ ProgramRow row]
  "置き場の Program の行 → 保存の JSON の形 {blob versions putMs}(state file と durable の KV が使う — #2447 の前の形と同じ)。"
  {"blob" row.blob "versions" row.versions "putMs" row.put-ms})


(defn #^ ProgramRow program-row-from-json [#^ dict data]
  "保存の JSON の形 → 置き場の Program の行(program-row-to-json の逆)。"
  (ProgramRow :blob (get data "blob") :versions (dict (.get data "versions" {})) :put-ms (int (get data "putMs"))))


(defn #^ dict warm-entry-to-json [#^ WarmEntry entry]
  "温める表の行 → 保存の JSON の形(state file と durable の KV が使う)。"
  (| (asdict entry) {"needs" (list entry.needs)}))


(defn #^ (| WarmEntry None) warm-entry-from-json [#^ dict data]
  "保存の JSON の形 → 温める表の行(warm-entry-to-json の逆)。旧い形(requires の object)の行は None(読み直しで捨てる — 期限つきの
   頼みなので、頼み手が新しい形で頼み直す)。"
  (when (in "requires" data)
    (return None))
  ;; needs だけを能力の組に読み替える。(| data {…}) で合わせると値の型に tuple が混ざり、他の欄の型と食い違って見える(#1690)
  (setv fields (dict data))
  (setv (get fields "needs") (capabilities-of (.get data "needs" []) "温める表の行の needs"))
  (WarmEntry #** fields))


(defn #^ dict board-rows-of [#^ dict values #^ dict versions]
  "読み直した盤の値(鍵 → 値)と版(鍵 → 版・無い鍵は 1)→ 盤の行の表(期限なし・大きさは測り直す)。"
  (dfor #(k v) (.items values) k (BoardRow :value v :version (.get versions k 1) :expires-ms None :size (value-size v))))


(defn #^ ClusterState state-from-json [#^ dict data #^ int now #^ (| dict None) [board None] #^ (| dict None) [board-versions None]]
  "保存した状態から作り直す。知っていた worker は全員「いま生きていた」とみなす。生存を捨てると、最初に heartbeat を
   送った worker へ全 job が移り、元の担い手がまだ動いていれば二重に動く(実測 2026-09-23)。戻らない worker の job は、
   この時点から移し替えの期限が過ぎた後に移る(その頃には自分で止まっている)。
   board = 行ごとの file から読んだ盤(渡さなければ、旧い形の file に在った盤)。資源の版の欄が無い旧い形の file は、
   読んだ後の最初の書きで resource_policy.stamp が版を振る(送り手 = 移し替え)。"
  (setv #(jobs refused) (read-service-rows (list (get data "jobs"))))
  (ClusterState
    :jobs jobs
    :refused refused
    :programs (dfor #(k v) (.items (.get data "programs" {})) k (program-row-from-json v))
    ;; 旧い形(labels だけ — 2026-09-27 より前)の worker の行は読まない。能力を知らない worker に置かないため(次の heartbeat で
    ;; 新しい形の名乗りから作り直す)。
    :workers (dfor w (.get data "workers" [])
                   :if (in "provides" w)
                   :setv caps (worker-capabilities-of w (.format "保存の worker {}" (get w "name")))
                   (get w "name")
                   (WorkerInfo (get w "name") (get caps 0) (get w "capacity") now
                               (component-versions-of (.get w "versions" {}))
                               :exclusive (get caps 1) :node (.get w "node" "")
                               #** (worker-generations-from-json w)))
    ;; 改名の前の file は置き先を旧い名の欄に持つ(durable_kv.LEGACY-PLACEMENT と同じ改名)。両方を読み、新しい欄が勝つ。
    :placements (dfor #(k v) (.items (| (.get data "assignments" {}) (.get data "placements" {}))) k (Placement #** v))
    :tasks (dfor t (.get data "tasks" [])
                 (get t "id")
                 (task-record-from-json t))
    :next-task (.get data "nextTask" 1)
    :task-prefix (.get data "taskPrefix" "t")
    :board (board-rows-of (if (is board None) (.get data "board" {}) board) (or board-versions {}))
    :meta (dfor #(k v) (.items (.get data "meta" {})) k (resource-meta-from-json v))
    :revision (.get data "revision" 0)
    :audit (tuple (gfor e (.get data "audit" []) (audit-event-from-json e)))
    :audit-seq (.get data "auditSeq" 0)
    :rollouts (dfor #(k r) (.items (.get data "rollouts" {})) k (rollout-row-from-json r))
    ;; drain の欄(2026-09-25)は、それより前の file には無い(空として読む)。
    :drains (dfor #(k v) (.items (.get data "drains" {})) k (Drain #** v))
    :surges (dfor #(k v) (.items (.get data "surges" {})) k (Placement #** v))
    :warms (dfor #(k v) (.items (.get data "warms" {})) :setv entry (warm-entry-from-json v) :if (is-not entry None) k entry)
    ;; 入れ替えの期限の見張り(2026-09-26)は、それより前の file には無い(空として読む)。
    :handoffs (dfor #(k v) (.items (.get data "handoffs" {})) k (handoff-watch-from-json v))
    :started-ms now))


(deff worker-capabilities-of [#^ dict body #^ str what]  ; defk にできない: heartbeat と保存の JSON を読む境界(Program の外)が呼ぶ
  {:pre [(: body dict) (: what str)] :post [(: % tuple) (= (len %) 2)] :tags {:context "doeff-cluster" :role "protocol"}}
  "保存の worker の行(JSON)の名乗り → #(provides exclusive)。規則は heartbeat の本文と同じ named-capabilities(旧い labels・exclusive は
   provides の一部 — ADR-DOE-CLUSTER-001 R4b)。"
  (named-capabilities (.get body "provides") (.get body "exclusive") (in "labels" body) what))
