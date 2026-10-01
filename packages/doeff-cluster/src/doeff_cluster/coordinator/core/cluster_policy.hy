;;; coordinator の純粋な判断。宣言された job・task・worker の生存・今の割り当て・時刻から、次の割り当てを導く。
;;; HTTP の要求 1 件への返事も、状態と要求と時刻から (次の状態 status 本文) を返す純粋な関数にする。I/O はしない。
;;; 割り当ては安定させる: 担い手が移し替えの期限内に生きていれば動かさない。
(require doeff-hy.macros [defk deff val])
(val MODULE-TAGS {:context "coordinator" :role "judgment"})
(import dataclasses [replace asdict])
(import functools)
(import hashlib)
(import json)
(import re)

(import doeff_cluster.shared.intent.job_model [JobSpec])
(import doeff_cluster.shared.intent.protocol [ClusterTiming Request BodyInvalid])
(import doeff_cluster.shared.core.capabilities [capabilities-of environ-pairs])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterJob WorkerInfo GenerationOrder Placement ClusterState TaskRecord Drain EnvFailed WarmEntry HandoffPhase RefusedJob UnplacedKind ACCEPTED-FORMATS PLACED-PHASES])
(import doeff_cluster.coordinator.core.cluster_rules [component-versions-of format-refusal])
(import doeff_cluster.coordinator.core.cluster_json [task-record-to-json task-record-from-json handoff-watch-from-json required-field int-field])
(import doeff_cluster.shared.intent.semaphore_model [SEMAPHORE-PREFIX])
(import doeff_cluster.shared.core.lease_rules [lease-op semaphore-write-refusal semaphore-key])
(import doeff_cluster.shared.core.board_rules [board-allows board-ttl-refusal])
(import doeff [run])
(import doeff_cluster.shared.intent.runtime_env_model [runtime-env-of-json RuntimeEnvInvalid env-key child-environ-refusal])
(import doeff_cluster.shared.intent.readiness_model [readiness-refusal])

(setv JOB-ENTRY "doeff_cluster.job_entry")
(setv MAX-EVENTS 200)

;; --- 盤と task の容量(2026-09-25) ---
;; 盤は coordinator の状態の一部で、全部が memory に載り、まとめ直し(snapshot)のたびに全部を書き直す。置き場の volume は 256 MiB。
;; 上限を越える書きは 507 で断る(消す書き・小さくする書きは通す)。上限の 8 割を越えたら計器 doeff_worker_board_bytes から alert。
;; 実測(2026-09-25 01:5x): 292 行・3.9 MB・最大の行 132 KB(大きな shadow の区画)。
(setv BOARD-MAX-VALUE-BYTES (* 1 1024 1024))    ; 1 行の値
(setv BOARD-MAX-ROWS 20000)                       ; 行の数
(setv BOARD-MAX-BYTES (* 64 1024 1024))           ; 値の合計
;; task: 呼び手が問い合わせを止めると lease-ms の後に落ちる(終わった task もそれで回収する)。lease は 1 時間まで・終わっていない
;; task は 2000 本まで(越えたら 429)。
(setv TASK-MAX-LEASE-SECONDS 3600)
(setv TASK-MAX-OPEN 2000)
;; 沈黙した worker を忘れるまで(置き先と task を持たない worker だけ)。Mac は眠り・持ち出しで数日沈黙するので 7 日。
(setv WORKER-FORGET-MS (* 7 24 3600 1000))


;; --- 宣言の読み書き(JSON ⇄ 型) ----------------------------------------------------

(defn #^ (| str None) declared-runtime-env [#^ dict item]
  "宣言 1 行の runtimeEnv(在れば)の正規化した JSON の文字列(JobSpec.runtime-env — 比べる欄)。無ければ None。"
  (setv value (.get item "runtimeEnv"))
  (if (is value None) None (json.dumps value :sort-keys True :ensure-ascii False)))

(defn #^ JobSpec spec-of-declaration [#^ dict item]
  "宣言 1 行 → worker が起動する形(job_entry の service 入口と詰めた Program の置き場のキー)。宣言の job は Program の job だけ —
   run の無い行(生の entry と args を worker に直に起こさせる形)は理由つきで断る(ADR-DOE-CLUSTER-001 R1・R7 — 移行の期間は置かない)。
   runtimeEnv を持つ宣言は、worker が env の root を準備してその venv で起こす(版は worker が env のキーへ置き換える)。"
  (setv run (.get item "run") revision (required-field item "revision") runtime (declared-runtime-env item))
  (cond
    (is run None) (raise (BodyInvalid (raw-entry-refusal item)))
    (not (isinstance run dict)) (raise (BodyInvalid (.format "run は JSON の object: {!r}" run)))
    (not (isinstance revision str)) (raise (BodyInvalid (.format "revision は文字列: {!r}" revision)))
    (= (.get run "kind") "service")
      (do (setv refusal (program-row-refusal item))
          (when refusal (raise (BodyInvalid refusal)))
          (JobSpec (get item "name") JOB-ENTRY #("service" "--identity" (identity-hash run))
                   revision
                   :handoff (= (.get item "update") "handoff") :runtime-env runtime
                   :program (get run "program")
                   :environ (environ-pairs (.get item "environ" {}))))
    True (raise (BodyInvalid (+ "知らない run.kind: " (repr (.get run "kind")))))))


;; --- Program の job の行(ADR-DOE-CLUSTER-001・改訂 1 の A・E・F・G) -------------------------------------

(setv PROGRAM-SHA (re.compile r"[0-9a-f]{64}"))
;; 旧い宣言の run の欄(関数の参照 + 設定 + handler の組の import path — 2026-09-27 より前の形)。
(setv OLD-RUN-KEYS #("factory" "env" "config"))
;; image の版を追う係(base-follow — 2026-09-28 に消した)の行の欄: 追う Deployment・追った commit・定義だけを別の commit で重ねる版。
(val IMAGE-FOLLOW-KEYS #("baseFrom" "base" "overlay"))


(deff raw-entry-refusal [#^ dict item]  ; defk にできない: 宣言の読み(coordinator の純粋な判断)が呼ぶ
  {:pre [(: item dict)] :post [(: % str)] :tags {:context "doeff-cluster" :role "judgment"}}
  "run の無い宣言の行(worker に module と引数を直に起こさせる生の entry の job)を断る理由の文 — 書きの口の 400・保存の読み直しの
   RefusedJob の理由に同じ文を出すため。宣言の job は Program の値 1 つだけ(ADR-DOE-CLUSTER-001 R1・R7)。"
  (.format "生の entry の job(entry {!r}・args {!r}・run が無い)は受け付けない — job は Program の値 1 つで宣言する(defsystem と declare・ADR-DOE-CLUSTER-001 R1)"
           (.get item "entry") (.get item "args")))


(deff identity-hash [#^ dict run]  ; defk にできない: 宣言の読み(coordinator の純粋な判断)が呼ぶ
  {:pre [(: run dict)] :post [(: % str) (= (len %) 16)] :tags {:context "doeff-cluster" :role "judgment"}}
  "Program の job の同一性の指紋 = identity(関数の参照と引数の正規 JSON)と versions の sha256 の頭 16 桁。job の引数に載り、
   spec-hash(revision・environ と一緒)の材料になる。詰めた Program の中身(program の sha)は入れない — 揺れるため(改訂 1 の A)。"
  (cut (.hexdigest (hashlib.sha256 (.encode (json.dumps {"identity" (get run "identity") "versions" (.get run "versions" {})}
                                                        :sort-keys True :ensure-ascii False :separators #("," ":"))
                                            "utf-8")))
       0 16))


(deff program-row-refusal [#^ dict item]  ; defk にできない: 宣言の読み(coordinator の純粋な判断)が呼ぶ
  {:pre [(: item dict)] :post [(: % (| str None))] :tags {:context "doeff-cluster" :role "judgment"}}
  "Program の job の宣言の行が受けられない理由(受けられれば None)。旧い形(run.factory・run.env・run.config・requires)・
   image の版を追う欄(baseFrom・base・overlay — Program を詰めた commit と別の commit で解くことになる — 改訂 1 の E)・置き場のキーの形・identity の欠け・
   environ の名(child-environ-refusal の検め・実行環境の env-vars との重なり — 改訂 1 の G)を検める。"
  (setv run (get item "run")
        old (lfor k OLD-RUN-KEYS :if (in k run) k)
        environ (.get item "environ" {})
        declared (lfor v (.get (or (.get item "runtimeEnv") {}) "envVars" []) (get v "name")))
  (cond
    old (.format "旧い宣言の形(run の {})は受け付けない — Program の値 1 つで宣言し直す(ADR-DOE-CLUSTER-001 R1・R3b)"
                 (.join "・" old))
    (is-not (.get item "requires") None) "旧い宣言の形(requires)は受け付けない — 要る能力 needs で宣言し直す(R4b)"
    (any (gfor k IMAGE-FOLLOW-KEYS (is-not (.get item k) None)))
      (.format "Program の job は {} を持たない(image の版を追う形 — 詰めた commit と別の commit で解くことになる)"
               (.join "・" (gfor k IMAGE-FOLLOW-KEYS :if (is-not (.get item k) None) k)))
    (not (and (isinstance (.get run "program") str) (PROGRAM-SHA.fullmatch (get run "program"))))
      (.format "run.program は詰めた Program の置き場のキー(64 桁の sha256): {!r}" (.get run "program"))
    (not (and (isinstance (.get run "identity") dict) (isinstance (.get (get run "identity") "function") str)))
      "run.identity(呼んだ関数の参照と引数)が無い"
    (not (isinstance environ dict)) (.format "environ は文字列の鍵と値の object: {!r}" environ)
    True (environ-refusal environ declared)))


(deff environ-refusal [#^ dict environ #^ list declared]  ; defk にできない: 宣言の読み(coordinator の純粋な判断)が呼ぶ
  {:pre [(: environ dict) (: declared list)] :post [(: % (| str None))] :tags {:context "doeff-cluster" :role "judgment"}}
  "宣言の行・task の本文の environ が受けられない理由。名と値は実行環境の env-vars と同じ検め(runtime_env_model.child-environ-refusal —
   EnvVar の名の形・worker の予約・秘密の中身の名)で、env-vars と同じ名は断る(子の環境変数の足し口を 1 つにする — 改訂 1 の G)。"
  (setv problem (child-environ-refusal environ))
  (when problem (return problem))
  (setv clash (sorted (gfor k environ :if (in k declared) k)))
  (if clash
      (.format "environ の {} は実行環境の env-vars と同じ名 — どちらか 1 つで宣言する" clash)
      None))


(deff task-environ-refusal [#^ dict body]  ; defk にできない: HTTP の本文を読む境界(Program の外)が呼ぶ純粋な判断
  {:pre [(: body dict)] :post [(: % (| str None))] :tags {:context "doeff-cluster" :role "judgment"}}
  "task(POST /tasks・PUT /detached)の本文の environ(子の環境変数 — 無ければ空)が受けられない理由。規則は service の宣言の行と同じ
   environ-refusal 1 つ(2026-09-28)。runtimeEnv の形の誤りは runtime-env-refusal が断るので、ここでは object の時だけ
   env-vars の名と比べる。"
  (setv environ (.get body "environ" {})
        runtime (.get body "runtimeEnv"))
  (if (not (isinstance environ dict))
      (.format "environ は環境変数の名 → 文字列の object: {!r}" environ)
      (environ-refusal environ (if (isinstance runtime dict)
                                   (lfor v (.get runtime "envVars" []) :if (isinstance v dict) (.get v "name"))
                                   []))))


(defn #^ ClusterJob job-from-json [#^ dict item]
  (setv replicas (.get item "replicas" 1) readiness (.get item "readiness"))
  (when (not-in replicas #(0 1))
    (raise (BodyInvalid (.format "replicas は 0 か 1(Service は 1 つだけ動かす): {!r}" replicas))))
  (setv update (.get item "update" "recreate"))
  (when (not-in update #("recreate" "handoff"))
    (raise (BodyInvalid (.format "update は recreate か handoff: {!r}" update))))
  ;; readiness の形(windowSeconds・入れ替えの期限 handoffTimeoutSeconds)は宣言の側と同じ規則(readiness_model.readiness-refusal)。
  (setv readiness-problem (readiness-refusal readiness update))
  (when (is-not readiness-problem None)
    (raise (BodyInvalid readiness-problem)))
  (setv env-refusal (runtime-env-refusal item))
  (when (is-not env-refusal None)
    (raise (BodyInvalid env-refusal)))
  (ClusterJob (spec-of-declaration item)
              (request-needs item "Service の needs")
              (.get item "pin")
              (.get item "run")
              replicas
              readiness
              (.get item "owner")
              update))


(defn #^ dict job-to-json [#^ ClusterJob job]
  (setv base {"name" job.spec.name "revision" job.spec.revision
              "needs" (list job.needs) "pin" job.pin
              "replicas" job.replicas "readiness" job.readiness "owner" job.owner})
  ;; 入れ替えの欄は、使う宣言にだけ書く(使わない宣言の spec の形・版は以前と同じ)。
  (setv extra (| (if (= job.update "recreate") {} {"update" job.update})
                 (if (is job.spec.runtime-env None) {} {"runtimeEnv" (json.loads job.spec.runtime-env)})
                 (if job.spec.environ {"environ" (dict job.spec.environ)} {})))
  ;; 受け付けた job は Program の job だけ(run を持つ — spec-of-declaration)。行は run を運ぶ(entry と args は worker の内部の形)。
  (| base extra {"run" job.run}))


(deff read-service-rows [#^ list rows]  ; defk にできない: 保存の読み直し(state file・durable KV — Program の外)が呼ぶ純粋な判断
  {:pre [(: rows list)] :post [(: % tuple) (= (len %) 2)] :tags {:context "doeff-cluster" :role "judgment"}}
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


(defn #^ dict spec-json [#^ JobSpec spec]
  (| {"name" spec.name "entry" spec.entry "args" (list spec.args) "revision" spec.revision "once" spec.once}
     ;; Program の job だけ(改訂 1 の F・G): 詰めた Program の置き場のキー(worker が /programs/<sha> から取る)と子の環境変数。
     (if spec.program {"program" spec.program} {})
     (if spec.environ {"environ" (dict spec.environ)} {})
     (if (is spec.placement None) {} {"placement" spec.placement})
     ;; 実行環境の job だけ: 宣言の JSON(worker が env の root を準備し、版を env のキーへ置き換える)。
     (if (is spec.runtime-env None) {} {"runtimeEnv" (json.loads spec.runtime-env)})
     ;; 入れ替え(handoff)の job だけ: 形と、coordinator が Ready と数えている process の世代の名(worker は旧をこの後に止める)。
     (if spec.handoff {"handoff" True "readyInstance" spec.ready-instance} {})
     ;; 入れ替えを諦めた job だけ(2026-09-26 — handoff_policy の期限): worker は新を止めて起こし直さず、旧を動かし続ける。
     (if (and spec.handoff spec.handoff-abandoned) {"handoffAbandoned" True} {})))


(defn #^ dict task-summary [#^ TaskRecord task]
  "状態表示と保存に使う形(結果は大きいので保存の時だけ別に足す)。切り離した task だけ呼び手の job id を足す
   (RemoteJob の task の形は以前と同じ)。"
  (| {"id" task.id "name" task.name "revision" task.revision "phase" task.phase
      "worker" task.worker "detail" task.detail "submittedMs" task.submitted-ms
      "startedMs" task.started-ms "finishedMs" task.finished-ms "leaseUntilMs" task.lease-until-ms}
     (if task.detached {"detached" True "key" task.key} {})))


(defn #^ dict state-to-json [#^ ClusterState state]
  "資源の状態の保存の形。盤は入れない(盤は行ごとに別の file — SaveBoardRow)。"
  {"formatVersion" 2
   "jobs" (+ (lfor j state.jobs (job-to-json j)) (lfor r (.values state.refused) r.row))
   "programs" state.programs
   "placements" (dfor #(k v) (.items state.placements) k (asdict v))
   "workers" (lfor w (.values state.workers)
                   (| {"name" w.name "provides" (list w.provides) "exclusive" (list w.exclusive) "node" w.node "capacity" w.capacity
                       "versions" (dict w.versions)}
                      (worker-generations-json w)))
   "tasks" (lfor t (.values state.tasks) (task-record-to-json t))
   "nextTask" state.next-task
   "taskPrefix" state.task-prefix
   "meta" state.meta
   "revision" state.revision
   "audit" (list state.audit)
   "auditSeq" state.audit-seq
   "rollouts" state.rollouts
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


(defn #^ (| int None) boot-at-of [#^ dict body]
  "heartbeat の本文・保存の形の bootAt(process の起動時刻・epoch ms)。整数でない値(欄の無い旧い worker を含む)は知らない = None。"
  (setv value (.get body "bootAt"))
  (if (and (isinstance value int) (not (isinstance value bool))) value None))


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
    :programs (.get data "programs" {})
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
    :board (if (is board None) (.get data "board" {}) board)
    :board-versions (or board-versions (dfor k (.get data "board" {}) k 1))
    :board-sizes (dfor #(k v) (.items (if (is board None) (.get data "board" {}) board)) k (value-size v))
    :meta (.get data "meta" {})
    :revision (.get data "revision" 0)
    :audit (tuple (.get data "audit" []))
    :audit-seq (.get data "auditSeq" 0)
    :rollouts (.get data "rollouts" {})
    ;; drain の欄(2026-09-25)は、それより前の file には無い(空として読む)。
    :drains (dfor #(k v) (.items (.get data "drains" {})) k (Drain #** v))
    :surges (dfor #(k v) (.items (.get data "surges" {})) k (Placement #** v))
    :warms (dfor #(k v) (.items (.get data "warms" {})) :setv entry (warm-entry-from-json v) :if (is-not entry None) k entry)
    ;; 入れ替えの期限の見張り(2026-09-26)は、それより前の file には無い(空として読む)。
    :handoffs (dfor #(k v) (.items (.get data "handoffs" {})) k (handoff-watch-from-json v))
    :started-ms now))


;; --- 割り当て ------------------------------------------------------------------------

(defn #^ bool alive [#^ int now #^ WorkerInfo worker #^ int window-ms]
  (<= (- now worker.last-seen-ms) window-ms))


(deff note-liveness [#^ ClusterState state #^ int now #^ ClusterTiming timing]  ; defk にできない: 調停の純粋な判断(api_policy.settle — Program の外)が呼ぶ
  {:pre [(: state ClusterState) (: now int) (: timing ClusterTiming)] :post [(: % ClusterState)] :tags {:context "doeff-cluster" :role "judgment"}}
  "生きていないと数える worker の名(ClusterState.silent)を今の時刻で求め直すため(#1934)。変わらなければ同じ値を返す(版を進めない)。
   変われば新しい値 — Worker の資源の status の live が変わり、stamp が版を進めて出来事を 1 行残す(死んだ拍と戻った拍だけ)。"
  (let [silent (frozenset (gfor w (.values state.workers) :if (not (alive now w timing.lease-ms)) w.name))]
    (if (= silent state.silent) state (replace state :silent silent))))


(deff placeable [#^ tuple needs #^ WorkerInfo worker]  ; defk にできない: coordinator と模擬の置き先の選び(Program の外の純粋な判断)が呼ぶ
  {:pre [(: needs tuple) (: worker WorkerInfo)] :post [(: % bool)] :tags {:context "doeff-cluster" :role "judgment"}}
  "needs の job / task をこの worker に置けるか — 置き場所の規則の定義点はここ 1 つ(ADR-DOE-CLUSTER-001 R4b):
   needs ⊆ provides ∪ derived(node の label から coordinator が導いた能力)、かつ worker が専用の能力(exclusive)を持てば、そのどれかを needs に持つ(一般の仕事を専用の担い手に置かない)。"
  (and (<= (set needs) (| (set worker.provides) (set worker.derived)))
       (or (not worker.exclusive) (bool (& (set needs) (set worker.exclusive))))))


(deff worker-capabilities-of [#^ dict body #^ str what]  ; defk にできない: heartbeat と保存の JSON を読む境界(Program の外)が呼ぶ
  {:pre [(: body dict) (: what str)] :post [(: % tuple) (= (len %) 2)] :tags {:context "doeff-cluster" :role "judgment"}}
  "worker の名乗り(heartbeat の本文・保存の行)→ #(provides exclusive)。exclusive は provides の一部でなければならない。
   旧い形(labels だけで provides の無い名乗り)は BodyInvalid(送り手の誤り — ValueError の子) — label の等しさの照合は受け付けない(ADR-DOE-CLUSTER-001 R4b)。"
  (when (and (in "labels" body) (not-in "provides" body))
    (raise (BodyInvalid (.format "{}: 旧い形の labels {!r} は受け付けない — worker は --provides と --exclusive で能力を名乗る"
                                what (get body "labels")))))
  (setv provides (capabilities-of (.get body "provides" []) (+ what " の provides")))
  (setv exclusive (capabilities-of (.get body "exclusive" []) (+ what " の exclusive")))
  (when (not (<= (set exclusive) (set provides)))
    (raise (BodyInvalid (.format "{}: exclusive {} は provides {} の一部で名乗る" what (list exclusive) (list provides)))))
  #(provides exclusive))


(deff request-needs [#^ dict body #^ str what]  ; defk にできない: HTTP の本文・宣言の JSON を読む境界(Program の外)が呼ぶ
  {:pre [(: body dict) (: what str)] :post [(: % tuple)] :tags {:context "doeff-cluster" :role "judgment"}}
  "送られた宣言・task・温める頼みの本文の needs → 名の順の tuple。旧い形の requires を持つ本文・空の needs は BodyInvalid(理由つき・ValueError の子)—
   旧い宣言は受け付けない(operator 2026-09-27)・要る能力は必ず書く(改訂 1 の I)。"
  (when (is-not (.get body "requires") None)
    (raise (BodyInvalid (.format "旧い形の requires {!r} は受け付けない — 要る能力の名の列 needs で書き直す(ADR-DOE-CLUSTER-001 R4b)"
                                (get body "requires")))))
  (setv needs (capabilities-of (.get body "needs" []) what))
  (when (not needs)
    (raise (BodyInvalid (.format "{} が空 — 要る能力の名を 1 つ以上書く(どこにでも置ける仕事は無い・ADR-DOE-CLUSTER-001 R4b・改訂 1 の I)" what))))
  needs)


(deff task-body-refusal [#^ ClusterState state #^ dict body]  ; defk にできない: HTTP の本文を読む境界(Program の外)が呼ぶ純粋な判断
  {:pre [(: state ClusterState) (: body dict)] :post [(: % (| str None))] :tags {:context "doeff-cluster" :role "judgment"}}
  "task(POST /tasks・PUT /detached)の本文が受けられない理由 — 旧い形の env(handler の組の import path)・旧い形の blob(詰めた
   Program を本文に載せる形)と versions(版の写し)・置き場のキー program の形と置き場に在るか・子の環境変数 environ(service の :environ と同じ規則)・needs の欠け。task も Program の値 1 つで、handler は Program の
   中で並べ(ADR-DOE-CLUSTER-001 R1・R2・改訂 1 の J の 11)、詰めた Program は service の宣言と同じく先に /programs/<sha> に置いて
   本文は sha だけを運ぶ(R3b — service と task で運び方を分けない)。"
  (let [program (.get body "program")]
    (cond
      (in "env" body)
        (.format "旧い形の env {!r}(handler の組の import path)は受け付けない — handler は task の Program の中の with-handlers で並べる"
                 (get body "env"))
      (in "blob" body)
        "旧い形の blob(詰めた Program を本文に載せる形)は受け付けない — 先に PUT /programs/<sha> で置き、本文は program に sha を書く"
      ;; 版は Program と一緒に置いた版 1 つ(program-versions)。本文の写しは置いた版と食い違いうるので受けない(黙って捨てない)。
      (in "versions" body)
        "本文の versions は受け付けない — task の版は PUT /programs/<sha> で Program と一緒に置いた版を使う"
      (not (and (isinstance program str) (PROGRAM-SHA.fullmatch program)))
        (.format "program は詰めた Program の置き場のキー(64 桁の sha256): {!r}" program)
      (not-in program state.programs)
        (.format "program {} は置き場に無い — 先に PUT /programs/{} で置く" program program)
      ;; 版(送り手の commit)は行の必須の欄(TaskRecord.revision)— 欠けを行を作る所の KeyError に任せない(#1024)。
      (not (isinstance (.get body "revision") str))
        (.format "revision(送り手の commit の文字列)が無い: {!r}" (.get body "revision"))
      True (or (task-environ-refusal body) (needs-refusal body)))))


(deff program-versions [#^ ClusterState state #^ str sha]  ; defk にできない: HTTP の本文を読む境界(Program の外)が呼ぶ純粋な判断
  {:pre [(: state ClusterState) (: sha str)] :post [(: % tuple)] :tags {:context "doeff-cluster" :role "judgment"}}
  "置き場に置いた Program の送り手の版(名の順の tuple)— task の版は詰めた Program と一緒に置いた版 1 つから取る(本文に版の写しを
   運ばせない・置く worker の版と比べる — can-run-task)。呼ぶ前に task-body-refusal が置き場に在ることを確かめる。"
  (component-versions-of (get state.programs sha "versions")))


(deff needs-refusal [#^ dict body]  ; defk にできない: HTTP の本文を読む境界(Program の外)が呼ぶ純粋な判断
  {:pre [(: body dict)] :post [(: % (| str None))] :tags {:context "doeff-cluster" :role "judgment"}}
  "本文の needs(と旧い形の requires)が受けられない理由(受けられれば None)— 400 の理由の文を 1 か所で作るため。"
  (try
    (request-needs body "needs")
    None
    (except [error ValueError]
      (str error))))


(defn #^ bool eligible [#^ ClusterJob job #^ WorkerInfo worker]
  "Service をこの worker に置けるか(pin と能力)。"
  (and (or (is job.pin None) (= job.pin worker.name))
       (placeable job.needs worker)))


;; probing(2026-09-27)= starting の手前の入口の検めの間(その worker で起こしかけている — 他へ置かない)。
(setv LIVE-PHASES #{"preparing" "probing" "starting" "backoff" "running" "stopping" "stop-unconfirmed"})


(defn #^ tuple service-rows [#^ int now #^ ClusterState state #^ str name #^ ClusterTiming timing]
  "job name の process の行の母集団(worker の名の順): lease の内に報告した全部の worker の最新の報告のうち、名が一致する行と、
   入れ替えで退いた process の行(行の名は <名>#retired-<世代>・retiredFrom = 名)。「まだどこかで動いているか」(still-live-somewhere)と
   「どの版が動いているか」(resource_policy.live-processes)が同じ母集団を読むための定義点。"
  (tuple (gfor #(wname st) (sorted (.items state.statuses))
               :setv w (.get state.workers wname)
               :if (and (is-not w None) (alive now w timing.lease-ms))
               row (.get st "jobs" [])
               :if (or (= (.get row "name") name) (= (.get row "retiredFrom") name))
               row)))


(defn #^ bool still-live-somewhere [#^ int now #^ ClusterState state #^ str name #^ ClusterTiming timing]
  "生きている worker の最新の報告に、その job がまだ動いている形で載っているか。載っている間は他へ置かない
   (動いている担い手から移す時、元の担い手が止め終えるまで新しい担い手を起動しない = 同じ job を 2 つ動かさない)。
   入れ替えで退いた process も、その job がまだ動いていると数える(母集団は service-rows)。"
  (any (gfor row (service-rows now state name timing) (in (.get row "phase") LIVE-PHASES))))


;; --- drain(2026-09-25) -----------------------------------------------------------------
;; worker の Pod を入れ替える前に、その上の書き手を別の worker へ移す。drain 中の worker には新しい置き先(job・task)を割り当てない。
;; 入れ替え(handoff)の Service の並べた置き先(surge)と付け替えは drain_policy.advance-drains(readiness を読む)。ここは置き先の
;; 判断が drain を知る部分だけ。

(defn #^ frozenset draining-workers [#^ ClusterState state #^ int now]
  "期限の内の drain を持つ worker の名。"
  (frozenset (gfor #(name d) (.items state.drains) :if (> d.until-ms now) name)))


(defn #^ bool can-take [#^ int now #^ ClusterState state #^ ClusterJob job #^ WorkerInfo w #^ dict load #^ ClusterTiming timing
                        #^ (| frozenset None) [draining None]]
  "job を新しく置ける worker か(生きている・条件を満たす・drain 中でない・空きがある)。"
  (and (alive now w timing.lease-ms) (eligible job w)
       (not-in w.name (if (is draining None) (draining-workers state now) draining))
       (< (.get load w.name 0) w.capacity)))


;; --- 同じ名の process の世代(2026-09-27) ------------------------------------------------
;; worker の Pod を作り直すと、旧い Pod は preStop の drain の間も worker の process を動かし、新しい Pod の worker は同じ名で名乗る。
;; 2 つの process が交互に heartbeat を送ると、worker の名乗り(世代・生存・label・容量)と drain の印が拍ごとに入れ替わり、新しい
;; 世代に置いた切り離した task を旧い世代の heartbeat が割り当ての 0.05〜0.12 秒後に lost にした(実測 2026-09-27 04:44 JST)。
;; boot の id には順が無いので、coordinator が初めて見た順を世代の順とする: 今の世代でも退いた世代でもない boot の
;; heartbeat = 新しい世代(今の世代を退かせる)・退いた世代の heartbeat = 古い世代(worker の名乗りとしては断る)。
;; 断るのは名乗りだけ: 退いた世代の上でまだ走っている切り離した task の終わりの報告と lease の延長は、その世代に置いた task に
;; 限って受ける(走らせ直さない task を途中で失わない — 旧い世代が消えたら lease 切れで lost)。
;; 起動時刻(2026-09-27 — heartbeat の bootAt): 初めて見た順だけでは、状態を失った coordinator に生きている 2 世代のうち新しい方が
;; 先に届くと、旧が今の世代・新が退いた世代になり、旧が死んだ後も新の heartbeat を断り続けて名が沈黙した。今の世代と来た世代の
;; 両方の起動時刻を知る時は、起動時刻の大きい方を新しい世代とする(generation-order)。起動時刻は worker の node の時計なので、
;; node の間の時計の差が同じ名の作り直しの間隔を越えると新旧が逆に読まれる(その差は作り直しの間隔より十分小さいと前提する)。
(setv RETIRED-BOOTS-KEPT 8)   ; 覚えておく退いた世代の数(1 回の作り直しで 1 つ増える — 旧い Pod が生きている間だけ要る)


(defn #^ GenerationOrder generation-order [#^ (| WorkerInfo None) current #^ (| str None) boot #^ (| int None) boot-at]
  "純粋: heartbeat の世代 boot(起動時刻 boot-at)が、名の今の世代 current に比べて今の世代か・古いか・新しいか。
   両方の起動時刻を知っていて違えば、起動時刻の大きい方が新しい。どちらかを知らない(欄の無い旧い worker・旧い形の置き場)か
   等しい時は初めて見た順: 退いた世代の列に在れば古い・在らなければ新しい。boot を名乗らない旧い worker・初めての名は今の世代。"
  (cond
    (or (is current None) (is boot None) (is current.boot None) (= boot current.boot)) GenerationOrder.CURRENT
    (and (is-not boot-at None) (is-not current.boot-at None) (!= boot-at current.boot-at))
      (if (< boot-at current.boot-at) GenerationOrder.OLDER GenerationOrder.NEWER)
    (in boot current.retired) GenerationOrder.OLDER
    True GenerationOrder.NEWER))


(defn #^ tuple retired-with [#^ tuple retired #^ str boot]
  "純粋: 退いた世代の列(新しい順)の頭に boot を足した列(重ねない・RETIRED-BOOTS-KEPT まで)。"
  (tuple (cut (+ #(boot) (tuple (gfor b retired :if (!= b boot) b))) RETIRED-BOOTS-KEPT)))


(defn #^ bool superseded-boot [#^ ClusterState state #^ str name #^ (| str None) boot]
  "純粋: name の worker の heartbeat・drain の頼みの boot が、この名の退いた世代か(boot を名乗らない旧い worker は退かない)。"
  (setv worker (.get state.workers name))
  (and (is-not boot None) (is-not worker None) (in boot worker.retired)))


(defn #^ bool other-generation-boot [#^ ClusterState state #^ str name #^ (| str None) boot]
  "純粋: drain の頼みの boot が、name の今の世代でない(退いた世代か、一度も見ていない世代)か。今の世代でない頼みに今の世代の
   drain を付けないため。boot の無い頼み・今の世代を知らない worker(読み直しの直後の旧い形の置き場など)は今の世代の頼みとみなす。"
  (setv worker (.get state.workers name))
  (and (is-not boot None) (is-not worker None) (is-not worker.boot None) (!= boot worker.boot)))


(defn #^ tuple retired-after [#^ (| WorkerInfo None) previous #^ (| str None) boot]
  "純粋: 古くない世代 boot(generation-order が CURRENT か NEWER)の heartbeat の後の、name の退いた世代の列(新しい順)。
   今の世代と違う boot = 新しい世代なので、今の世代を列の頭へ足し、boot 自身は列から外す(以前に退いた世代と取り違えていた
   新しい世代が起動時刻で名乗り直した時)。"
  (cond
    (is previous None) #()
    (or (is boot None) (is previous.boot None) (= previous.boot boot)) previous.retired
    True (retired-with (tuple (gfor b previous.retired :if (!= b boot) b)) previous.boot)))


(defn #^ ClusterState absorb-boot [#^ ClusterState state #^ str name #^ (| str None) boot]
  "heartbeat の worker の process の世代を drain へ写す: 頼まれた時の世代と違う世代(Pod を作り直した後の worker)が来たら drain を解く。
   世代を知らずに頼まれた drain(読み直しの直後など)は、最初に来た世代を持つ。退いた世代の heartbeat はここへ来ない
   (register-heartbeat が先に分ける — 旧い世代の heartbeat が新しい世代の drain を解かず、旧い世代の drain を付け直さない)。"
  (setv d (.get state.drains name))
  (cond
    (or (is d None) (is boot None)) state
    (is d.boot None) (replace state :drains (| state.drains {name (replace d :boot boot)}))
    (!= d.boot boot) (replace state :drains (dfor #(k v) (.items state.drains) :if (!= k name) k v))
    True state))


(defn #^ dict load-of [#^ ClusterState state #^ dict placements]
  "worker ごとの担っている数(job・並べた置き先(surge)・実行中の task)。"
  (setv load (dfor name state.workers name 0))
  (for [a (+ (list (.values placements)) (list (.values state.surges)))]
    (when (in a.worker load) (+= (get load a.worker) 1)))
  (for [t (.values state.tasks)]
    (when (and (in t.phase PLACED-PHASES) (in t.worker load)) (+= (get load t.worker) 1)))
  load)


(defn #^ tuple active-jobs [#^ ClusterState state]
  "置く対象の宣言(replicas 0 の Service は宣言に残るが置かない — 担い手は次の heartbeat で止める)。"
  (tuple (gfor job state.jobs :if (> job.replicas 0) job)))


(defn #^ dict place-jobs [#^ int now #^ ClusterState state #^ ClusterTiming timing]
  (setv jobs (active-jobs state)
        names (sfor job jobs job.spec.name)
        draining (draining-workers state now)
        kept {})
  ;; 1. 続けてよい割り当てを残す(宣言に在り・replicas 1・条件を満たし・担い手が移し替えの期限内)。
  ;;    drain 中の worker の上の入れ替えでない job は、他に置ける worker が在る時だけ外す(止めて移す)。置ける先が無ければ残す
  ;;    (空白を作らない)。入れ替えの job は残す(drain_policy が並べてから付け替える)。
  (setv current-load (load-of state state.placements))
  (for [job jobs]
    (setv current (.get state.placements job.spec.name))
    (when (is-not current None)
      (setv worker (.get state.workers current.worker))
      (when (and (is-not worker None) (eligible job worker)
                 (alive now worker timing.reassign-after-ms)
                 (not (and (in current.worker draining) (not job.spec.handoff)
                           (any (gfor w (.values state.workers)
                                      (and (!= w.name current.worker)
                                           (can-take now state job w current-load timing draining)))))))
        (setv (get kept job.spec.name) current))))
  ;; 2. 担い手の無い job を、生きている worker のうち空きの多い順へ置く(同点は名前順)。
  ;;    どこかの生きた worker がまだその job を動かしている間は置かない(条件が変わって生きた担い手から外した job は、
  ;;    元の担い手が止め終えたと報告してから置く)。drain 中の worker には置かない。
  ;;    drain で並べた置き先(surge)を持つ job は、その置き先へ付け替える(そこで動いている process をそのまま使う — 旧い担い手が
  ;;    沈黙して外れた時)。
  (setv load (load-of state kept))
  (setv result (dict kept))
  (for [job (sorted jobs :key (fn [j] j.spec.name))]
    (when (in job.spec.name result) (continue))
    (setv surge (.get state.surges job.spec.name)
          surge-worker (if surge (.get state.workers surge.worker) None))
    (when (and (is-not surge-worker None) (alive now surge-worker timing.lease-ms) (eligible job surge-worker))
      (setv (get result job.spec.name) surge)
      (continue))
    (when (still-live-somewhere now state job.spec.name timing) (continue))
    (setv candidates (sorted
      (lfor w (.values state.workers)
            :if (can-take now state job w load timing draining)
            w)
      :key (fn [w] #((get load w.name) w.name))))
    (when candidates
      (setv chosen (get candidates 0)
            previous (.get state.placements job.spec.name))
      (+= (get load chosen.name) 1)
      (setv (get result job.spec.name)
        (Placement job.spec.name chosen.name (if previous (+ previous.generation 1) 1) now))))
  ;; 宣言から消えた job の割り当ては残さない(担い手は次の heartbeat で止める)。
  (dfor #(name a) (.items result) :if (in name names) name a))


(defn #^ tuple jobs-for [#^ ClusterState state #^ str worker #^ (| dict None) [ready-instances None]]
  "worker に割り当てた job の spec。割り当ての世代(placement)を載せる — worker は起こす process へ渡し、process は readiness と
   計器の報告に載せる(比べない欄なので、世代だけが変わっても worker は process を起こし直さない)。
   ready-instances = Service の名 → Ready と数えている process の世代の名(入れ替えの job に載せる・api_policy が求める)。
   drain で並べた置き先(surge)がこの worker に在る job も載せる(新しい process を起こし、standby で lease を待つ)。
   入れ替えを諦めた job(state.handoffs の ABANDONED — handoff_policy)には諦めの印を載せる(worker は新を止めて旧を残す)。"
  (tuple (gfor job state.jobs
               :setv placed (.get state.placements job.spec.name)
               :setv surge (.get state.surges job.spec.name)
               :setv a (cond (and placed (= placed.worker worker)) placed
                             (and surge (= surge.worker worker) (> job.replicas 0)) surge
                             True None)
               :if a
               :setv watch (.get state.handoffs job.spec.name)
               (replace job.spec :placement a.generation
                        :ready-instance (.get (or ready-instances {}) job.spec.name)
                        :handoff-abandoned (and (is-not watch None) (= watch.phase HandoffPhase.ABANDONED))))))


;; --- task ----------------------------------------------------------------------------

(defn #^ bool tools-satisfy [#^ TaskRecord task #^ WorkerInfo worker]
  "実行環境の宣言の道具(tools)を worker が全部名乗っているか。版が空の要求は名だけ、版の在る要求は同じ版を求める。"
  (setv named (dict worker.tools))
  (all (gfor tool (.get (or task.runtime-env {}) "tools" [])
             (and (in (get tool "name") named)
                  (or (not (.get tool "version" "")) (= (.get tool "version") (get named (get tool "name"))))))))


(defn #^ bool tools-cover [#^ (| dict None) declared #^ WorkerInfo worker]
  "実行環境の宣言の道具(tools)を worker が全部名乗っているか(温める表の行を配る先を選ぶため)。"
  (setv named (dict worker.tools))
  (all (gfor tool (.get (or declared {}) "tools" [])
             (and (in (get tool "name") named)
                  (or (not (.get tool "version" "")) (= (.get tool "version") (get named (get tool "name"))))))))


(defn [(functools.lru-cache :maxsize 4096)] _root-key [#^ str declared-text #^ str platform]
  ;; 同じ宣言と platform の root のキーを拍ごとに計算し直さないための cache(宣言の JSON の文字列で引く)。
  (run (env-key (run (runtime-env-of-json (json.loads declared-text))) platform)))


(defn #^ str root-key-on [#^ dict declared #^ WorkerInfo worker]
  "宣言の root を worker の上で呼ぶキー(worker の platform で計算 — worker が heartbeat で名乗るキーと同じ形)。"
  (_root-key (json.dumps declared :sort-keys True :ensure-ascii False) worker.platform))


(defn #^ bool env-ready-on [#^ (| dict None) declared #^ WorkerInfo worker]
  "worker がその宣言の root を準備済みと名乗っているか(platform を名乗らない worker は準備済みにならない)。"
  (and (is-not declared None) (bool worker.platform) (in (root-key-on declared worker) worker.env-ready)))


(defn #^ bool env-room-on [#^ TaskRecord task #^ WorkerInfo worker]
  "task を worker に置ける disk の条件か: disk の尽きた(exhausted)worker には、準備済みでない env の task を置かない。"
  (or (is task.runtime-env None) (!= worker.env-capacity "exhausted") (env-ready-on task.runtime-env worker)))


(defn #^ bool can-run-task [#^ TaskRecord task #^ WorkerInfo worker]
  "版が同じ worker にだけ送る(cloudpickle は版をまたいで復元できる保証が無い)。実行環境の task は worker の版と比べない —
   子 process は worker の venv ではなく env の root で走り、版の突き合わせは子 process が env の版と行う。準備に一時の失敗をした
   worker(avoid)には置き直さない。"
  (and (or (is-not task.runtime-env None) (= task.versions worker.versions))
       (placeable task.needs worker)
       (not-in worker.name task.avoid)
       (tools-satisfy task worker)))


(defn #^ str versions-note [#^ TaskRecord task #^ ClusterState state #^ int now #^ ClusterTiming timing]
  (setv seen (lfor w (sorted (.values state.workers) :key (fn [w] w.name))
                   :if (alive now w timing.lease-ms)
                   (.format "{}({})" w.name
                            (.join "・" (lfor #(k v) w.versions :if (!= v (.get (dict task.versions) k))
                                              (.format "{}={}" k v))))))
  (.format "版と能力(専用の能力を含む)が合う worker が無い。要る能力 {}・送り手の版 {}。生きている worker と版の違う所: {}"
           (list task.needs) (dict task.versions) (or (.join " / " seen) "(生きている worker が無い)")))


(defn #^ UnplacedKind unplaced-kind [#^ int now #^ ClusterState state #^ ClusterJob job #^ ClusterTiming timing]
  "担い手の無い job を、なぜ置けないかの種類に分ける — 状態の表示(unplaced-jobs の文)と版の判定(running-process の種類 →
   resource_policy.version-state: 前の担い手を待つのは正常な途中・他の 2 つは待っても進まない)が同じ分け方を読むため。"
  (cond
    (still-live-somewhere now state job.spec.name timing) UnplacedKind.WAITING-PREVIOUS-HOLDER
    (not (any (gfor w (.values state.workers) (and (alive now w timing.lease-ms) (eligible job w)
                                                    (not-in w.name (draining-workers state now))))))
      UnplacedKind.NO-ELIGIBLE-WORKER
    True UnplacedKind.NO-ROOM))


(defn #^ str unplaced-text [#^ UnplacedKind kind #^ ClusterJob job]
  "置き先が無い理由の種類 → 人が読む文(状態の表示 GET /state の unplaced と Service の readyReason の綴り)。"
  (match kind
    UnplacedKind.WAITING-PREVIOUS-HOLDER "前の担い手が止め終えるのを待っている"
    UnplacedKind.NO-ELIGIBLE-WORKER
      (.format "置ける worker が無い(要る能力 {}・固定 {}。専用の能力を持つ worker には、そのどれかを要る job だけを置く。drain 中の worker には置かない)"
               (list job.needs) job.pin)
    UnplacedKind.NO-ROOM "置ける worker に空きが無い"))


(defn #^ dict unplaced-jobs [#^ int now #^ ClusterState state #^ ClusterTiming timing]
  "担い手の無い job と、その理由(状態の表示用)。"
  (dfor job (active-jobs state)
        :if (not-in job.spec.name state.placements)
        job.spec.name
        (unplaced-text (unplaced-kind now state job timing) job)))


(setv DETACHED-TERMINAL #("finished" "code-failed" "env-failed" "failed" "version-mismatch" "lost" "cancelled"))
;; 実行環境の準備の一時の失敗を、別の worker へ置き直す回数の上限(起動前なので同じ task を 2 度実行しない)。
(setv ENV-RETRIES 2)


(defn #^ TaskRecord end-detached [#^ TaskRecord task #^ str phase #^ int now #^ str detail #^ (| str None) [result None]]
  "純粋: 切り離した task を終わりの phase にする(終わりの phase は二度と変わらない)。置き場のキー program は持ったまま — 行が在る間
   (結果の保持の間)は置き場の Program を参照し、行が消えたら program_policy.sweep-programs が猶予の後に消す(掃除の規則を 1 つにする)。"
  (replace task :phase phase :finished-ms now :detail detail :result result))


(defn #^ (| TaskRecord None) settle-detached [#^ TaskRecord task #^ int now]
  "純粋: 切り離した task 1 本の期限の判断。保持の期限を過ぎた終わりの行は None(消す)。
   置いた task の lease(担い手の worker の heartbeat が延ばす)が切れた = worker の死 = lost(走らせ直さない)。
   呼び手の問い合わせは lease に触らない(呼び手が消えても task は続く)。"
  (cond
    (in task.phase DETACHED-TERMINAL)
      (if (> now (+ (or task.finished-ms now) task.retain-ms)) None task)
    (and (in task.phase PLACED-PHASES) (> now task.lease-until-ms))
      (end-detached task "lost" now
                    (.format "担い手の worker {} の lease が切れた(worker の死とみなす — task は走らせ直さない)" task.worker))
    True task))


(defn #^ str unplaceable-phase [#^ TaskRecord task #^ ClusterState state #^ int now #^ ClusterTiming timing]
  "置ける worker が無い切り離した task の終わりの phase。能力の合う生きた worker はいるのに版だけが違う = version-mismatch。"
  (if (any (gfor w (.values state.workers)
                 (and (alive now w timing.lease-ms) (placeable task.needs w))))
      "version-mismatch"
      "failed"))


(defn #^ dict place-tasks [#^ int now #^ ClusterState state #^ dict placements #^ ClusterTiming timing]
  "task の期限切れを落とし、担い手が沈黙した task を失敗にし、待っている task を置く。切り離した task は settle-detached の規則。"
  (setv tasks {})
  (for [#(id task) (.items state.tasks)]
    (cond
      task.detached
        (do (setv kept (settle-detached task now))
            (when (is-not kept None) (setv (get tasks id) kept)))
      ;; 呼び手が問い合わせを止めた(止まった)= task も要らない。担い手は次の heartbeat で子 process を止める。
      (> now task.lease-until-ms) None
      (and (in task.phase PLACED-PHASES)
           (or (not-in task.worker state.workers)
               (not (alive now (get state.workers task.worker) timing.reassign-after-ms))))
        (setv (get tasks id) (replace task :phase "failed" :finished-ms now
                                      :detail (.format "担い手の worker {} が沈黙した(task は走らせ直さない)" task.worker)))
      True (setv (get tasks id) task)))
  (setv placed (replace state :tasks tasks))
  (setv load (load-of placed placements)
        draining (draining-workers state now))
  (for [id (sorted tasks)]
    (setv task (get tasks id))
    (when (= task.phase "queued")
      ;; drain 中の worker には新しい task を置かない(置ける先が他に無ければ、drain が解けるまで待つ — 失敗にはしない)。
      (setv able (lfor w (.values state.workers) :if (and (alive now w timing.lease-ms) (can-run-task task w)) w)
            ;; drain 中と、disk の尽きた worker(準備済みでない env の task)は避ける — 置ける先が他に無ければ待つ。
            able-now (lfor w able :if (and (not-in w.name draining) (env-room-on task w)) w))
      ;; 実行環境の task は、その env を準備済みの worker を優先する(空きの多さより先 — 準備を task の待ちに入れない・2026-09-26)。
      (setv free (sorted (lfor w able-now :if (< (get load w.name) w.capacity) w)
                         :key (fn [w] #((not (env-ready-on task.runtime-env w)) (get load w.name) w.name))))
      (cond
        free (do (setv chosen (get free 0)
                       ;; 準備済みの worker が無い置き先 = 冷たい起動(worker が準備してから走る)。phase を preparing にして assigned と分ける。
                       phase (if (and (is-not task.runtime-env None) (not (env-ready-on task.runtime-env chosen)))
                                 "preparing" "assigned"))
                 (+= (get load chosen.name) 1)
                 ;; 切り離した task は置いた worker の process の世代を覚え、lease を置いた時から数える。
                 (setv (get tasks id) (if task.detached
                                          (replace task :phase phase :worker chosen.name :started-ms now
                                                   :boot chosen.boot :lease-until-ms (+ now task.lease-ms))
                                          (replace task :phase phase :worker chosen.name :started-ms now))))
        ;; 準備の一時の失敗の後に、置き直せる別の worker が無い: 最後の失敗で終える。
        (and (not able) task.failure-kind)
          (setv (get tasks id) (end-env-failed task now task.detail))
        ;; 能力の合う生きた worker はいるが、宣言の道具を名乗る worker が無い。
        (and (not able) (is-not task.runtime-env None)
             (any (gfor w (.values state.workers)
                        (and (alive now w timing.lease-ms) (placeable task.needs w)))))
          (setv (get tasks id)
                (end-env-failed (replace task :failure-kind "tool-missing" :retryable False) now
                                (.format "宣言の道具 {} を名乗る worker が無い"
                                         (lfor t (.get task.runtime-env "tools" []) (.format "{}{}" (get t "name")
                                                                                             (if (.get t "version") (+ "=" (get t "version")) ""))))))
        ;; 能力と版の合う worker は登録されているが、いま連絡していない(coordinator を起こし直した直後・worker の Recreate の入れ替えの間・
        ;; 能力を持つ worker が 1 台だけの時の一瞬の沈黙)。待っても晴れない理由ではないので、失敗にせず待つ(#2440)。待ちの上限は
        ;; task の lease: 切り離していない task は呼び手が問い合わせを止めると上で落ち、切り離した task は積んだ時の lease の期限まで。
        ;; 登録された worker のどれも能力と版が合わない時は、今どおり下の枝で失敗にする(待っても晴れない)。
        (and (not able) (<= now task.lease-until-ms) (any (gfor w (.values state.workers) (can-run-task task w))))
          (setv (get tasks id)
                (replace task :detail (.format "要る能力 {} の worker {} がいま連絡していない — 連絡が戻るまで待つ"
                                               (list task.needs)
                                               (.join "・" (sorted (gfor w (.values state.workers) :if (can-run-task task w) w.name))))))
        (and (not able) task.detached)
          (setv (get tasks id) (end-detached task (unplaceable-phase task state now timing) now
                                             (versions-note task state now timing)))
        (not able) (setv (get tasks id) (replace task :phase "failed" :finished-ms now
                                                 :detail (versions-note task state now timing))))))
  tasks)


(defn #^ bool same-boot [#^ TaskRecord task #^ (| str None) boot]
  "切り離した task の担い手の process の世代が、置いた時と同じか(どちらかを知らなければ同じとみなす)。"
  (or (not task.detached) (is task.boot None) (is boot None) (= task.boot boot)))


(defn #^ TaskRecord end-env-failed [#^ TaskRecord task #^ int now #^ str detail]
  "純粋: 実行環境を準備できなかった task を終える(切り離した task は終わりの phase)。"
  (if task.detached
      (replace (end-detached task "env-failed" now detail) :failure-kind task.failure-kind :retryable task.retryable)
      (replace task :phase "env-failed" :finished-ms now :detail detail)))


(defn #^ TaskRecord absorb-env-failure [#^ TaskRecord task #^ str worker #^ dict status #^ int now]
  "純粋: worker の「実行環境を準備できない」の報告 → 一時の失敗で置き直しの回数が残れば、その worker を避けて待ちへ戻す。
   それ以外は終える。どちらも子 process を起こす前(Program は走っていない)。"
  (setv kind (.get status "failureKind" "") retryable (bool (.get status "retryable" False))
        detail (.format "worker {} で実行環境を準備できない({}): {}" worker kind (.get status "detail" ""))
        failed (replace task :failure-kind kind :retryable retryable :detail detail))
  (if (and retryable (< task.env-attempts ENV-RETRIES))
      (replace failed :phase "queued" :worker None :boot None :started-ms None
               :avoid (+ task.avoid #(worker)) :env-attempts (+ task.env-attempts 1))
      (end-env-failed failed now detail)))


(defn #^ list tasks-for [#^ ClusterState state #^ str worker #^ (| str None) [boot None]]
  "heartbeat の返事で worker の process へ走らせる task を渡すため。切り離した task は、置いた時と同じ process の世代にだけ送る
   (作り直した worker の process で走らせ直さない)。boot = 返事を受ける process の世代(渡さなければ coordinator の見る今の世代)。"
  (setv boot (if (is boot None) (. (.get state.workers worker (WorkerInfo worker #() 0 0)) boot) boot))
  (lfor task (sorted (.values state.tasks) :key (fn [t] t.id))
        :if (and (in task.phase PLACED-PHASES) (= task.worker worker) (same-boot task boot))
        ;; 詰めた Program は置き場のキー(sha)だけを運ぶ — worker が /programs/<sha> から取る(service の job と同じ・改訂 1 の F)。
        (| {"id" task.id "name" task.name "revision" task.revision
            "versions" (dict task.versions) "program" task.program}
           ;; 切り離した task は、状態を失った coordinator が引き取れるだけの欄を持つ(worker が状態の報告に写す —
           ;; adopt-running-detached・2026-09-27)。
           (if task.detached {"detached" True "key" task.key "leaseMs" task.lease-ms "retainMs" task.retain-ms
                              "needs" (list task.needs)} {})
           (if (is-not task.runtime-env None) {"runtimeEnv" task.runtime-env} {})
           ;; 子の環境変数(service の job の行の environ と同じ欄 — worker は同じ路で子の環境に置く)。切り離した task は写しにも残る
           ;; (引き取る時に同じ environ で行を作り直す)。
           (if task.environ {"environ" (dict task.environ)} {}))))


(defn #^ TaskRecord task-finished [#^ TaskRecord task #^ int now #^ str detail #^ (| str None) result]
  "純粋: 子 process が結果を持って終わった task の記録(切り離した task は終わりの phase — end-detached)。heartbeat の報告と子 process の
   直の届け(absorb-task-result)が同じ形で終える。result = 詰めた結果(None = 結果なし — 切り離した task では呼ばない)。"
  (if task.detached
      (end-detached task "finished" now detail result)
      (replace task :phase "finished" :finished-ms now :result result :detail detail)))


(defn #^ TaskRecord absorb-detached-report [#^ TaskRecord task #^ dict status #^ int now]
  "切り離した task の終わりの報告 → 終わりの phase。結果を書かずに終わった子 process は lost(結果が無い = 消失)。"
  (setv phase (.get status "phase") detail (.get status "detail" ""))
  (cond
    (and (= phase "finished") (is-not (.get status "result") None))
      (task-finished task now detail (get status "result"))
    (= phase "finished")
      (end-detached task "lost" now (.format "子 process が結果を書かずに終わった({})" detail))
    (= phase "code-failed") (end-detached task "code-failed" now detail)
    True task))


(defn #^ dict absorb-task-reports [#^ ClusterState state #^ str worker #^ list statuses #^ int now #^ (| str None) [boot None]]
  "worker の状態の報告のうち、task の終わりを task の記録へ写す。切り離した task は置いた時と同じ process の世代の報告だけ。"
  (setv tasks (dict state.tasks))
  (for [status statuses]
    (setv name (.get status "name" ""))
    (when (.startswith name "task/")
      (setv id (cut name 5 None) task (.get tasks id))
      (when (and task (in task.phase PLACED-PHASES) (= task.worker worker) (same-boot task boot))
        (setv phase (.get status "phase"))
        (cond
          (= phase "env-failed") (setv (get tasks id) (absorb-env-failure task worker status now))
          task.detached (setv (get tasks id) (absorb-detached-report task status now))
          (= phase "finished")
            (setv (get tasks id) (task-finished task now (.get status "detail" "") (.get status "result")))
          (= phase "code-failed")
            (setv (get tasks id) (replace task :phase "code-failed" :finished-ms now
                                          :detail (.get status "detail" "")))))))
  tasks)


(defn #^ tuple absorb-task-result [#^ ClusterState state #^ str id #^ dict body #^ int now]
  "POST /tasks/<id>/result: task の子 process が終わる前に直に届けた結果を task の記録へ写す(#1387 — 結果の運び手を worker の
   heartbeat だけにすると、子の exit 0 から次の heartbeat までに worker が死んだ時に結果が届かず、起き直した worker が同じ task を
   もう 1 度走らせた)。本文 = {worker instance result format}(report_client.task-result-request)。返り値 #(次の状態 status 答え)。
   - 置いた worker からの、まだ終わっていない task の結果 → 結果を持って終える(task-finished)。200。
   - 終わった task(heartbeat が先に運んだ・同じ結果の 2 度目の届け)→ 状態を変えない。200(冪等 — heartbeat の報告も終わった task には
     何もしない: absorb-task-reports)。
   - 別の worker に置いた task → 409(古い送り手)。知らない task(呼び手が落とした・lease 切れ)→ 404。
   切り離した task も置いた worker の名だけで比べる: 子は worker の process の世代を知らず、切り離した task は置いた世代の process にしか
   渡らない(tasks-for)ので、同じ名の worker の子が届ける結果はその task を走らせた process の物。"
  (setv refusal (format-refusal body))
  (when refusal (return #(state 400 {"error" refusal})))
  (setv worker (required-field body "worker") result (required-field body "result"))
  (when (not (isinstance worker str)) (raise (BodyInvalid (.format "worker は文字列: {!r}" worker))))
  (when (not (isinstance result str)) (raise (BodyInvalid (.format "result は詰めた結果の文字列: {!r}" (type result)))))
  (setv task (.get state.tasks id))
  (cond
    (is task None)
      #(state 404 {"error" (.format "task {} を知らない(呼び手が落とした・lease が切れた)" id)})
    (not-in task.phase PLACED-PHASES)
      #(state 200 {"accepted" False "phase" task.phase})
    (!= task.worker worker)
      #(state 409 {"error" (.format "task {} は worker {} に置いてある(送り手 {})" id task.worker worker)})
    True
      #((replace state :tasks (| state.tasks {id (task-finished task now (.format "子 process {} が終わる前に届けた"
                                                                                 (.get body "instance" "")) result)}))
        200 {"accepted" True "phase" "finished"})))


(defn #^ dict renew-detached [#^ dict tasks #^ str worker #^ (| str None) boot #^ int now]
  "担い手の heartbeat: その worker に置いた切り離した task の lease を延ばす(lease は worker が延ばす)。延ばすのは置いた時と同じ
   process の世代の heartbeat だけ。別の世代の heartbeat では何もしない — 置いた世代の process が消えていれば lease 切れで lost
   (settle-detached・走らせ直さない)。以前は別の世代の heartbeat が来た拍に lost にしていたが、同じ名の 2 つの世代が並んで
   動く間(旧い Pod の preStop の drain)は、旧い世代の heartbeat が新しい世代に置いた task を失わせた。"
  (dfor #(id t) (.items tasks)
        id (if (and t.detached (in t.phase PLACED-PHASES) (= t.worker worker) (same-boot t boot))
               (replace t :lease-until-ms (+ now t.lease-ms))
               t)))


;; --- 1 拍の調停 ------------------------------------------------------------------------

(defn #^ ClusterState sweep-board [#^ ClusterState state #^ int now]
  "純粋: 期限を過ぎた盤の行を消した状態(期限つきの行が無ければ同じ object)。"
  (setv gone (lfor #(k at) (.items state.board-expiry) :if (<= at now) k))
  (if (not gone)
      state
      (do (setv drop (set gone))
          (replace state
                   :board (dfor #(k v) (.items state.board) :if (not-in k drop) k v)
                   :board-versions (dfor #(k v) (.items state.board-versions) :if (not-in k drop) k v)
                   :board-expiry (dfor #(k v) (.items state.board-expiry) :if (not-in k drop) k v)
                   :board-sizes (dfor #(k v) (.items state.board-sizes) :if (not-in k drop) k v)))))


(defn #^ ClusterState forget-silent-workers [#^ ClusterState state #^ int now]
  "純粋: WORKER-FORGET-MS より長く沈黙し、置き先も task も持たない worker を忘れた状態(忘れる物が無ければ同じ object)。"
  (setv busy (| (sfor a (+ (list (.values state.placements)) (list (.values state.surges))) a.worker)
                ;; 終わって結果を持っているだけの切り離した task は worker を引き留めない。
                (sfor t (.values state.tasks) :if (and t.worker (not (and t.detached (in t.phase DETACHED-TERMINAL)))) t.worker))
        gone (lfor #(n w) (.items state.workers) :if (and (> (- now w.last-seen-ms) WORKER-FORGET-MS) (not-in n busy)) n))
  (if (not gone)
      state
      (replace state :workers (dfor #(n w) (.items state.workers) :if (not-in n gone) n w)
                     :statuses (dfor #(n s) (.items state.statuses) :if (not-in n gone) n s)
                     :drains (dfor #(n d) (.items state.drains) :if (not-in n gone) n d))))


(defn #^ ClusterState sweep-drains [#^ ClusterState state #^ int now]
  "純粋: 期限を過ぎた drain を消した状態(消す物が無ければ同じ object)。頼み手(worker の preStop)は期限の内に頼み直し続ける。"
  (if (all (gfor d (.values state.drains) (> d.until-ms now)))
      state
      (replace state :drains (dfor #(n d) (.items state.drains) :if (> d.until-ms now) n d))))


(defn #^ ClusterState sweep-warms [#^ ClusterState state #^ int now]
  "純粋: 期限を過ぎた温める表の行を消した状態(消す物が無ければ同じ object)。"
  (if (all (gfor w (.values state.warms) (> w.until-ms now)))
      state
      (replace state :warms (dfor #(k w) (.items state.warms) :if (> w.until-ms now) k w))))


(defn #^ int cold-starts [#^ dict before #^ dict after]
  "待ちから preparing に置かれた task の数(準備済みの worker が無いまま置いた = 冷たい起動)。"
  (len (lfor #(id t) (.items after)
             :if (and (= t.phase "preparing") (in id before) (= (. (get before id) phase) "queued"))
             id)))


(defn #^ ClusterState reconcile [#^ int now #^ ClusterState state #^ ClusterTiming timing]
  (setv state (forget-silent-workers (sweep-warms (sweep-drains (sweep-board state now) now) now) now))
  ;; 変わらない割り当てと task は元の object のまま引き継ぎ、何も変わらなければ状態そのものを返す(2026-09-29・#1356):
  ;; 版を付ける stamp は同じ object なら資源の写し(snapshot)を作らずに返す。以前は毎拍作り直した dict を返したので、変化の無い
  ;; 1 秒ごとの拍でも写しを 2 つ作って比べていた(模擬の仮想 1700 秒で約 2,000 回)。
  (setv before state.placements
        placed (place-jobs now state timing)
        after (if (= placed before) before placed)
        placed-tasks (place-tasks now state after timing)
        tasks (if (= placed-tasks state.tasks) state.tasks placed-tasks))
  (when (and (is after before) (is tasks state.tasks))
    (return state))
  (setv events (list state.events))
  (for [name (sorted (| (set before) (set after)))]
    (setv old (.get before name) new (.get after name))
    (when (!= old new)
      (.append events {"at" now "job" name
                       "from" (if old old.worker None) "to" (if new new.worker None)
                       "generation" (if new new.generation None)})))
  (replace state :placements after :tasks tasks :events (tuple (cut events (- MAX-EVENTS) None))
                 :env-cold-starts (+ state.env-cold-starts (cold-starts state.tasks tasks))))


(defn #^ bool durable-changed [#^ ClusterState before #^ ClusterState after]
  "資源の状態の file(state.json)の保存が要る変化か。worker の生存の時刻・状態の報告・readiness・k8s の観測は保存しない。
   盤は含まない(盤は行ごとの file へ別に書く — board-changes)。"
  (or (!= before.jobs after.jobs) (!= before.refused after.refused) (!= before.programs after.programs) (!= before.placements after.placements)
      (!= before.tasks after.tasks)
      (!= before.next-task after.next-task) (!= before.task-prefix after.task-prefix)
      (!= before.revision after.revision)
      (!= before.rollouts after.rollouts)
      (!= before.drains after.drains) (!= before.surges after.surges)
      (!= before.warms after.warms)
      (!= before.handoffs after.handoffs)
      (!= (set before.workers) (set after.workers))
      (any (gfor #(n w) (.items after.workers)
                 :setv b (.get before.workers n)
                 (or (is b None) (!= #(b.provides b.exclusive b.derived b.node b.capacity b.versions b.boot b.retired b.boot-at)
                                     #(w.provides w.exclusive w.derived w.node w.capacity w.versions w.boot w.retired w.boot-at)))))))


(defn #^ list board-changes [#^ ClusterState before #^ ClusterState after]
  "書き直しが要る盤の行の鍵(書かれた・消えた)。変わらない行は同じ object のまま引き継がれるので、同一性で比べる(盤全体を
   値で比べない — 大きな shadow の盤は 2.3 MB)。"
  (if (is before.board after.board)
      []
      (+ (lfor #(k v) (.items after.board) :if (is-not (.get before.board k) v) k)
         (lfor k before.board :if (not-in k after.board) k))))


;; --- HTTP の要求への返事(判断の部品。要求の振り分けは api_policy) -----------------------------------

(deff text-map? [value]  ; defk にできない: HTTP の本文を読む境界(Program の外)が呼ぶ純粋な判断
  {:pre [(: value (| dict list str int float bool None))] :post [(: % bool)] :tags {:context "doeff-cluster" :role "judgment"}}
  "JSON の値が「名 → 文字列」の object か — heartbeat の versions・tools(component-versions-of が名の順に並べる)を写す前に確かめるため。"
  (and (isinstance value dict) (all (gfor #(k v) (.items value) (and (isinstance k str) (isinstance v str))))))


(deff heartbeat-body-refusal [#^ dict body]  ; defk にできない: HTTP の本文を読む境界(Program の外)が呼ぶ純粋な判断
  {:pre [(: body dict)] :post [(: % (| str None))] :tags {:context "doeff-cluster" :role "judgment"}}
  "heartbeat の本文の形が受けられない理由(受けられれば None)— 送り手の本文の欠け・型の誤りを、写す途中の KeyError・TypeError・
   AttributeError(受け口では coordinator の中の欠陥と区別できない — #1024)にしないため、写す前に 1 か所で検める。
   能力の名乗り(provides・exclusive)の検めは worker-capabilities-of が持つ。"
  (setv name (.get body "name") statuses (.get body "statuses" []) envs (.get body "envs" {}))
  (cond
    (not (and (isinstance name str) name)) (.format "name(worker の名の文字列)が無い: {!r}" name)
    (not (and (isinstance statuses list) (all (gfor s statuses (isinstance s dict)))))
      (.format "statuses は状態の報告の object の列: {!r}" statuses)
    (not (isinstance envs dict)) (.format "envs は object: {!r}" envs)
    (not (all (gfor k #("ready" "preparing")
                    :setv keys (.get envs k [])
                    (and (isinstance keys list) (all (gfor x keys (isinstance x str)))))))
      "envs.ready・envs.preparing は env のキーの文字列の列"
    (not (and (isinstance (.get envs "failed" []) list)
              (all (gfor f (.get envs "failed" []) (and (isinstance f dict) (in "key" f) (in "kind" f))))))
      "envs.failed は {key kind detail? retryable?} の object の列"
    (not (text-map? (.get body "versions" {}))) "versions は部品の名 → 版の文字列の object"
    (not (text-map? (.get body "tools" {}))) "tools は道具の名 → 版の文字列の object"
    (not (isinstance (.get body "platform" "") str)) (.format "platform は文字列: {!r}" (.get body "platform"))
    True None))


(defn #^ ClusterState register-heartbeat [#^ ClusterState state #^ dict body #^ int now]
  "heartbeat の中身(worker の能力・容量・版と、各 job / task の状態)を状態へ写す。割り当ての調停はしない(呼び手が別の送り手
   = coordinator として調停する)。古い世代の heartbeat(generation-order が OLDER)は名乗りとして受けず、その世代を退いた世代の
   列に載せ、その世代に置いた task の終わりの報告と lease の延長だけを写す(absorb-superseded-heartbeat)。
   知らない切り離した task をその process が走らせていれば、先に引き取る(adopt-running-detached — 状態を失った coordinator)。
   本文の形の誤り(heartbeat-body-refusal)は BodyInvalid(送り手の誤り・400)。"
  (setv refusal (heartbeat-body-refusal body))
  (when (is-not refusal None) (raise (BodyInvalid refusal)))
  (setv name (get body "name") boot (.get body "boot") boot-at (boot-at-of body)
        previous (.get state.workers name)
        order (generation-order previous boot boot-at)
        state (adopt-running-detached state name boot (.get body "statuses" []) now))
  (when (= order GenerationOrder.OLDER)
    ;; OLDER は今の世代と boot の両方が在る時だけ(generation-order の最初の枝が、どちらかの無い時を CURRENT にする)。
    (when (or (is previous None) (is boot None))
      (raise (RuntimeError (.format "世代の比べが OLDER なのに今の世代か boot が無い: {}" name))))
    (return (absorb-superseded-heartbeat
              (replace state :workers (| state.workers {name (replace previous :retired (retired-with previous.retired boot))}))
              name boot (.get body "statuses" []) now)))
  (setv envs (.get body "envs" {})
        caps (worker-capabilities-of body (.format "worker {} の名乗り" name))
        node (str (.get body "node" ""))
        info (WorkerInfo name (tuple (gfor c (get caps 0) :if (not-in c state.derivable) c))
                         (int-field body "capacity" 10) now
                         (component-versions-of (.get body "versions" {}))
                         boot
                         (component-versions-of (.get body "tools" {}))
                         :platform (.get body "platform" "")
                         :env-ready (frozenset (.get envs "ready" []))
                         :env-preparing (frozenset (.get envs "preparing" []))
                         :env-failed (tuple (gfor f (.get envs "failed" [])
                                                  (EnvFailed (get f "key") (get f "kind") (.get f "detail" "")
                                                             (bool (.get f "retryable" False)))))
                         :env-capacity (.get body "envCapacity" "ok")
                         :retired (retired-after previous boot)
                         ;; 同じ世代が起動時刻を名乗らなくなっても(版を戻した worker)、知っている起動時刻は捨てない。
                         :boot-at (if (and (is boot-at None) (= order GenerationOrder.CURRENT) (is-not previous None)
                                           (= previous.boot boot))
                                      previous.boot-at
                                      boot-at)
                         :exclusive (get caps 1)
                         ;; node の label から導いた能力は、同じ node の間だけ前の観測を引き継ぐ(次の調停で読み直す)。
                         :node node
                         :derived (if (and (is-not previous None) (= previous.node node)) previous.derived #()))
        statuses (.get body "statuses" [])
        state (replace (absorb-boot state name boot)
                :workers (| state.workers {name info})
                :statuses (| state.statuses {name {"at" now "endpoint" (.get body "endpoint")
                                                  "jobs" (lfor s statuses (dfor #(k v) (.items s)
                                                                                :if (not-in k #("result" "task")) k v))}})))
  (replace state :tasks (promote-prepared (renew-detached (absorb-task-reports state name statuses now boot) name boot now)
                                         info)))


(defn #^ ClusterState absorb-superseded-heartbeat [#^ ClusterState state #^ str name #^ str boot #^ list statuses #^ int now]
  "退いた世代の process がまだ走らせている切り離した task を最後まで見届けるため: その世代に置いた task の終わりの報告と
   lease の延長だけを写す。worker の名乗り(生存・label・容量・版・世代)・job の状態の報告・drain は今の世代の物なので触らない。"
  (replace state :tasks (renew-detached (absorb-task-reports state name statuses now boot) name boot now)))


;; --- 状態を失った coordinator が、走っている切り離した task を止めさせない(2026-09-27) --------------------------
;; worker は heartbeat の返事に載らない task の子 process を止め(worker_policy.plan-job — 宣言から消えた job)、その結果の file と
;; Program の cache を消す(handlers.CoordinatorLink.accept-tasks・accept-programs)。置き場を失った coordinator は task の行を持たないので、最初の返事で
;; 生きている worker の走っている切り離した task を全部止めさせ、呼び手の key も引けなくなっていた。worker は切り離した task の
;; 状態の報告に、置かれた時の返事の行(Program の置き場のキー program・key と lease と保持の長さを含む)を写して添える(欄 task)。coordinator は
;; 行を持たない task/<id> を worker が走らせていると報告し、その写しを添えていれば、同じ行を引き取り(担い手 = その worker・
;; 世代 = その heartbeat の世代)、同じ heartbeat の返事に載せる(worker の宣言の spec が変わらない = 止めない)。
;; 引き取らない: 行を持つ task(終わった行を含む — 取り消し・lost は今までどおり止める)・写しの無い報告(旧い worker)・
;; 走っても終わってもいない報告・同じ key を別の行が使っている時・欠けた写し。終わった報告(finished・code-failed)も引き取り、
;; 同じ heartbeat の終わりの報告でその終わりへ写す(worker は返事に無い task の結果の file を消すので、引き取らなければ結果を
;; 失い、呼び手は完走した仕事を送り直す)。
(setv ADOPTABLE-PHASES #{"preparing" "probing" "starting" "running" "finished" "code-failed"})


(defn #^ (| int None) task-number [#^ str prefix #^ str id]
  "task の id(<頭><番号>)がこの coordinator の頭 prefix の物なら、その番号(次に振る番号を越えさせるため)。他の頭の id は None。"
  (setv rest (cut id (len prefix) None))
  (if (and (.startswith id prefix) (.isdigit rest)) (int rest) None))


(defn #^ str task-id [#^ ClusterState state]
  "次に振る task の id(頭 + 番号)。"
  (.format "{}{}" state.task-prefix state.next-task))


(defn #^ str fresh-task-prefix [#^ int now]
  "置き場の無いところから起きた coordinator の task の id の頭(起動の時刻の 16 進 — 起動ごとに違う)。前の coordinator が振った id
   (t<番号> か別の起動の頭)と重ならない。"
  (.format "t{:x}-" now))


(defn #^ (| TaskRecord None) adopted-task [#^ ClusterState state #^ str worker #^ (| str None) boot #^ dict status #^ int now]
  "純粋: 状態の報告 1 行 → 引き取る切り離した task の行(引き取らない時は None)。"
  (setv row-name (.get status "name" "") echo (.get status "task"))
  (when (or (not (.startswith row-name "task/")) (not (isinstance echo dict))
            (not-in (.get status "phase") ADOPTABLE-PHASES))
    (return None))
  (setv id (cut row-name 5 None) key (.get echo "key") lease-ms (.get echo "leaseMs")
        revision (.get echo "revision") needs (.get echo "needs" []) program (.get echo "program")
        environ (.get echo "environ" {}))
  ;; 写しの program(置き場のキー)が無い・形の違う報告(blob を運んでいた旧い worker)は引き取らない。引き取った行は同じ sha を
  ;; 参照する(担い手は cache の file を持っているので、状態を失った置き場に Program が無くても走り続ける)。
  (when (or (in id state.tasks) (!= (.get echo "id") id) (not (.get echo "detached"))
            (not (isinstance key str)) (not (isinstance lease-ms int))
            (not (isinstance revision str)) (not (isinstance needs list))
            (not (and (isinstance program str) (PROGRAM-SHA.fullmatch program)))
            (not (isinstance environ dict)) (not (text-map? (.get echo "versions" {})))
            (any (gfor t (.values state.tasks) (= t.key key))))
    (return None))
  (TaskRecord id (.get echo "name" "") program revision
              (component-versions-of (.get echo "versions" {})) (capabilities-of needs "引き取る task の needs") lease-ms (+ now lease-ms) now
              :phase "assigned" :worker worker :started-ms now :detached True :key key :boot boot
              :retain-ms (int-field echo "retainMs" 0) :runtime-env (.get echo "runtimeEnv") :environ (environ-pairs environ)
              :detail (.format "coordinator の置き場に行が無く、担い手 {} が走らせていた task を引き取った" worker)))


(defn #^ ClusterState adopt-running-detached [#^ ClusterState state #^ str worker #^ (| str None) boot #^ list statuses #^ int now]
  "純粋: heartbeat の状態の報告のうち、行を持たない走っている切り離した task を引き取った状態(引き取る物が無ければ同じ object)。
   次に振る task の番号は引き取った id より後へ進める(同じ id を別の task に振らない)。"
  (setv adopted {})
  (for [status statuses]
    (setv task (adopted-task (replace state :tasks (| state.tasks adopted)) worker boot status now))
    (when (is-not task None) (setv (get adopted task.id) task)))
  (if adopted
      (replace state :tasks (| state.tasks adopted)
               :next-task (max [state.next-task
                                #* (gfor id adopted :setv n (task-number state.task-prefix id) :if (is-not n None) (+ n 1))]))
      state))


(defn #^ dict promote-prepared [#^ dict tasks #^ WorkerInfo worker]
  "純粋: worker が env を準備済みと名乗った拍に、その worker の preparing の task を assigned へ進める。"
  (dfor #(id t) (.items tasks)
        id (if (and (= t.phase "preparing") (= t.worker worker.name) (env-ready-on t.runtime-env worker))
               (replace t :phase "assigned")
               t)))


(defn #^ list warms-for [#^ ClusterState state #^ str worker #^ int now]
  "worker に配る温める表の行(期限の内・能力(専用の能力を含む)と宣言の道具が合う行)。heartbeat の返事の warm。"
  (setv info (.get state.workers worker))
  (if (is info None)
      []
      (lfor w (sorted (.values state.warms) :key (fn [w] w.key))
            :if (and (> w.until-ms now) (placeable w.needs info)
                     (tools-cover w.runtime-env info))
            {"key" w.key "runtimeEnv" w.runtime-env})))


(defn #^ dict heartbeat-reply [#^ ClusterState state #^ str name #^ ClusterTiming timing #^ (| dict None) [ready-instances None] #^ int [now 0]
                               #^ (| str None) [boot None] #^ (| list None) [statuses None]]
  "heartbeat を送った process に、動かす job・task・温める表・時間の設定・drain の印を返すため。boot = 送った process の世代・
   statuses = その heartbeat の状態の報告。退いた世代(superseded-boot)への返事は superseded-reply。"
  (when (and (is-not boot None) (superseded-boot state name boot))
    (return (superseded-reply state name boot (or statuses []) timing ready-instances)))
  {"jobs" (lfor s (jobs-for state name ready-instances) (spec-json s))
   "tasks" (tasks-for state name boot)
   ;; 温める表のうち、この worker に合う行(2026-09-26 — worker は job の準備より低い優先度で準備する)。
   "warm" (warms-for state name now)
   "timing" (asdict timing)
   ;; この worker が drain 中か(2026-09-25): worker は返事ごとに Pod の中の ready の file へ写し、readinessProbe は sh でそれを読む
   ;; (hy を起こす probe は込んだ node で 10 秒の timeout を越え、両方の Pod が同時に NotReady → DaemonSet が 2 台を同時に消した)。
   "draining" (in name state.drains)
   ;; 受け入れる本文の形の版(2026-09-26 — cluster_model.ACCEPTED-FORMATS)。
   "formats" (list ACCEPTED-FORMATS)
   ;; この返事を作った時の coordinator の版(#1933): worker は次の変化を GET /watch?after=<この版> で待つ。欄の無い返事は、待つ口の
   ;; 無い旧い coordinator の物。
   "revision" state.revision})


(defn #^ frozenset running-names [#^ list statuses]
  "退いた世代の報告のうち、いま running の job の名(入れ替えで退いた process の行は元の名で数える)。退いた世代に動かし続けさせて
   よい job を選ぶため。"
  (frozenset (gfor row statuses
                   :if (= (.get row "phase") "running")
                   (or (.get row "retiredFrom") (.get row "name") ""))))


(defn #^ dict superseded-reply [#^ ClusterState state #^ str name #^ str boot #^ list statuses #^ ClusterTiming timing
                                #^ (| dict None) [ready-instances None]]
  "退いた世代の process への heartbeat の返事(2026-09-27)。退く process に新しい仕事を起こさせず、動いている物は安全に畳ませるため:
   jobs = 名の置き先の入れ替え(handoff)の job のうち、その世代が running と報告している物だけ(lease を持ったまま Pod の停止まで
   動かし、新しい世代の process が lease を取る。recreate の job は載せない = 止めて lease を返す。退いた後に置かれた job は、その
   世代が動かしていないので載らない)。tasks = task.boot がその世代と等しい切り離した task だけ(最後まで走らせる。RemoteJob の
   task は世代を記録しないので載せない)。温める表は載せない。draining = 真(ready の file を draining にする)・superseded = 真。"
  (setv running (running-names statuses))
  {"jobs" (lfor s (jobs-for state name ready-instances) :if (and s.handoff (in s.name running)) (spec-json s))
   "tasks" (lfor row (tasks-for state name boot)
                 :setv task (get state.tasks (get row "id"))
                 :if (and task.detached (= task.boot boot))
                 row)
   "warm" []
   "timing" (asdict timing)
   "draining" True
   "superseded" True
   "formats" (list ACCEPTED-FORMATS)
   "revision" state.revision})


(defn #^ dict state-view [#^ ClusterState state #^ int now #^ ClusterTiming timing]
  {"now" now
   "jobs" (lfor j state.jobs (| (job-to-json j)
                                {"resourceVersion" (.get (.get state.meta (+ "Service/" j.spec.name) {}) "resourceVersion")}))
   ;; live = heartbeat が lease の内・draining = 期限の内の drain(担い手の名簿の読み ReadRunners の正本 — 2026-09-26)。
   "workers" (dfor #(n w) (.items state.workers)
                   n {"provides" (list w.provides) "exclusive" (list w.exclusive) "derived" (list w.derived) "node" w.node "capacity" w.capacity "silentMs" (- now w.last-seen-ms)
                      "versions" (dict w.versions) "live" (alive now w timing.lease-ms)
                      "draining" (in n (draining-workers state now))})
   "placements" (dfor #(k v) (.items state.placements) k (asdict v))
   "unplaced" (unplaced-jobs now state timing)
   ;; 沈黙した worker の最後の報告は「いま動いている」の証拠にならない。古さを付けて返す。
   "statuses" (dfor #(n st) (.items state.statuses) n (| st {"stale" (> (- now (get st "at")) timing.lease-ms)}))
   "tasks" (lfor t (sorted (.values state.tasks) :key (fn [t] t.id)) (task-summary t))
   "boardKeys" (len state.board)
   "surges" (dfor #(k v) (.items state.surges) k (asdict v))
   "events" (list (cut state.events -50 None))
   "revision" state.revision})


(defn #^ (| str None) runtime-env-refusal [#^ dict body]
  "本文の runtimeEnv(在れば)が宣言として読めなければ理由の文(送り手の誤り — 400)。"
  (setv value (.get body "runtimeEnv"))
  (cond
    (is value None) None
    (not (isinstance value dict)) (.format "runtimeEnv は JSON の object: {!r}" (type value))
    True (try (do (run (runtime-env-of-json value)) None)
              (except [error RuntimeEnvInvalid] (.format "runtimeEnv が誤っている: {}" error)))))


(defn #^ tuple submit-task [#^ ClusterState state #^ dict body #^ int now #^ (| str None) [owner None]]
  "POST /tasks: 呼び手の問い合わせに寿命を縛られた task の行を作る。本文は置き場に置いた Program の sha を運ぶ(task-body-refusal)。"
  (setv refusal (or (format-refusal body) (runtime-env-refusal body) (task-body-refusal state body)))
  (when refusal (return #(state 400 {"error" refusal})))
  ;; 数に読めない leaseSeconds は送り手の誤り(float() の ValueError / TypeError を受け口へ漏らさない — #1024)。
  (setv lease-value (.get body "leaseSeconds" 15.0))
  (try
    (setv lease-seconds (float lease-value))
    (except [[ValueError TypeError]]
      (return #(state 400 {"error" (.format "leaseSeconds は 0 より大きく {} 以下の数: {!r}" TASK-MAX-LEASE-SECONDS lease-value)}))))
  (setv open-count (len (lfor t (.values state.tasks) :if (or (= t.phase "queued") (in t.phase PLACED-PHASES)) t)))
  (when (not (< 0 lease-seconds (+ TASK-MAX-LEASE-SECONDS 1)))
    (return #(state 400 {"error" (.format "leaseSeconds は 0 より大きく {} 以下: {}" TASK-MAX-LEASE-SECONDS lease-seconds)})))
  (when (>= open-count TASK-MAX-OPEN)
    (return #(state 429 {"error" (.format "終わっていない task が上限 {} 本に達している" TASK-MAX-OPEN) "open" open-count})))
  (setv id (task-id state)
        lease-ms (int (* 1000 lease-seconds))
        task (TaskRecord id (.get body "name" "") (get body "program") (get body "revision")
                         (program-versions state (get body "program"))
                         (request-needs body "task の needs")
                         lease-ms (+ now lease-ms) now
                         :runtime-env (.get body "runtimeEnv")
                         :environ (environ-pairs (.get body "environ" {}))))
  #((replace state :tasks (| state.tasks {id task}) :next-task (+ state.next-task 1)) 200 {"task" id}))


(defn #^ tuple poll-task [#^ ClusterState state #^ str id #^ int now]
  "呼び手の問い合わせ。lease を延ばし、いまの様子を返す。"
  (setv task (.get state.tasks id))
  (when (is task None)
    (return #(state 200 {"phase" "missing"})))
  (setv task (replace task :lease-until-ms (+ now task.lease-ms)))
  #((replace state :tasks (| state.tasks {id task})) 200
    {"phase" task.phase "worker" task.worker "detail" task.detail "result" task.result
     "failureKind" task.failure-kind "retryable" task.retryable}))


(defn #^ tuple lease-write [#^ ClusterState state #^ str name #^ dict body #^ int now]
  "POST /leases/<名>: lease の操作 1 つを coordinator の時計で当てる(lease_rules.lease-op)。行が変われば盤へ書く
   (版を 1 進める・盤の書きと同じく永続化してから返事をする)。返り値 #(次の状態 status 答え)。"
  ;; 本文の欄の欠け・型の誤りは送り手の誤り(BodyInvalid・400)— KeyError や int() の例外を受け口へ漏らさない(#1024)。
  (setv op (required-field body "op") token (required-field body "token"))
  (when (not (isinstance op str)) (raise (BodyInvalid (.format "op は文字列: {!r}" op))))
  (when (not (isinstance token str)) (raise (BodyInvalid (.format "token は文字列: {!r}" token))))
  (setv key (semaphore-key name) current (.get state.board key)
        #(row answer) (lease-op current op token (int-field body "permits" 1) (int-field body "ttlMs" 0) now))
  (if (or (is row current) (is row None))
      #(state 200 answer)
      (do (setv version (.get state.board-versions key (if (is current None) 0 1)))
          #((replace state :board (| state.board {key row})
                           :board-versions (| state.board-versions {key (+ version 1)})
                           :board-expiry (dfor #(k v) (.items state.board-expiry) :if (!= k key) k v)
                           :board-sizes (| state.board-sizes {key (value-size row)}))
            200 answer))))


(defn #^ int value-size [#^ (| dict list str int float bool None) value]
  "盤の値の大きさ(JSON の utf-8 の byte 数)。容量の上限の判断と計器に使う。"
  (len (.encode (json.dumps value :ensure-ascii False :separators #("," ":")) "utf-8")))


(defn #^ dict board-usage [#^ ClusterState state]
  {"rows" (len state.board) "bytes" (sum (.values state.board-sizes)) "expiring" (len state.board-expiry)
   "maxRows" BOARD-MAX-ROWS "maxBytes" BOARD-MAX-BYTES "maxValueBytes" BOARD-MAX-VALUE-BYTES})


(defn #^ (| str None) board-capacity-refusal [#^ ClusterState state #^ str key #^ int size]
  "純粋: key へ size byte の値を書くと上限を越えるなら理由の文。越えないなら None。小さくする書きは(合計が上限の上でも)通す。"
  (setv old (.get state.board-sizes key 0)
        total (sum (.values state.board-sizes)))
  (cond
    (> size BOARD-MAX-VALUE-BYTES) (.format "値が {} byte で、1 行の上限 {} byte を越える" size BOARD-MAX-VALUE-BYTES)
    (and (not-in key state.board) (>= (len state.board) BOARD-MAX-ROWS))
      (.format "盤の行が上限 {} 行に達している(期限つきの行 {} 行)" BOARD-MAX-ROWS (len state.board-expiry))
    (and (> size old) (> (+ (- total old) size) BOARD-MAX-BYTES))
      (.format "盤の値の合計が {} byte になり、上限 {} byte を越える" (+ (- total old) size) BOARD-MAX-BYTES)
    True None))


(defn #^ tuple board-write [#^ ClusterState state #^ str key #^ dict body #^ int [now 0]]
  "盤の行 1 つの compare-and-set。expect = 値で比べる(従来)・expectVersion = 行の版で比べる(0 = 行が無い時だけ)。
   両方あれば両方を満たす時だけ書く。value が null で delete が真なら行を消す。返事に行の新しい版を載せる。
   ttlSeconds(2026-09-25)= 行の期限。期限を過ぎた行は調停が消す(sweep-board)。付けない書きは期限を外す(ずっと残す)。
   上限(board-capacity-refusal)を越える書きは 507 で断る。"
  (setv ttl (.get body "ttlSeconds"))
  (setv ttl-refusal (run (board-ttl-refusal ttl)))
  (when (is-not ttl-refusal None)
    (return #(state 400 {"ok" False "error" ttl-refusal})))
  (setv present (in key state.board)
        version (.get state.board-versions key (if present 1 0))
        ok (and (board-allows (.get state.board key) present (in "expect" body) (.get body "expect"))
                (or (not-in "expectVersion" body) (= (get body "expectVersion") version))))
  (cond
    (not ok) #(state 409 {"ok" False "current" (.get state.board key) "resourceVersion" version})
    ;; lease の行への直の書き(旧い版の process)は、coordinator の時計でまだ切れていない担い手を追い出せない(2026-09-25)。
    ;; 409 = 旧い版は compare-and-set の競合として読み直す。
    (and (.startswith key SEMAPHORE-PREFIX) (not (.get body "delete"))
         (is-not (semaphore-write-refusal (.get state.board key) (required-field body "value") now) None))
      #(state 409 {"ok" False "current" (.get state.board key) "resourceVersion" version
                   "error" (semaphore-write-refusal (.get state.board key) (required-field body "value") now)})
    (.get body "delete")
      #((replace state :board (dfor #(k v) (.items state.board) :if (!= k key) k v)
                       :board-versions (dfor #(k v) (.items state.board-versions) :if (!= k key) k v)
                       :board-expiry (dfor #(k v) (.items state.board-expiry) :if (!= k key) k v)
                       :board-sizes (dfor #(k v) (.items state.board-sizes) :if (!= k key) k v))
        200 {"ok" True "resourceVersion" None})
    True
      (do (setv size (value-size (required-field body "value"))
                refusal (board-capacity-refusal state key size))
          (if (is-not refusal None)
              #(state 507 {"ok" False "error" refusal "usage" (board-usage state)})
              #((replace state :board (| state.board {key (required-field body "value")})
                               :board-versions (| state.board-versions {key (+ version 1)})
                               :board-expiry (if (is ttl None)
                                                 (dfor #(k v) (.items state.board-expiry) :if (!= k key) k v)
                                                 (| state.board-expiry {key (+ now (int (* 1000 ttl)))}))
                               :board-sizes (| state.board-sizes {key size}))
                200 {"ok" True "resourceVersion" (+ version 1)})))))


;; --- node の label から導く能力(ADR-DOE-CLUSTER-001 R4b・改訂 1 の I)------------------------------------------

;; node の label を読み直す間隔(label は滅多に変わらない — 置き先を選ぶ時に古い観測を使っても、次の読みで直る)。
(setv NODE-LABELS-TTL-MS 60000)


(deff nodes-to-read [#^ ClusterState state #^ int now]  ; defk にできない: coordinator の調停(Program)が呼ぶ純粋な判断
  {:pre [(: state ClusterState) (: now int)] :post [(: % list)] :tags {:context "doeff-cluster" :role "judgment"}}
  "label を読み直す node の名(整列)— node を名乗る worker の node のうち、観測が無いか古い物。能力の導出の材料を揃えるため。"
  (sorted (sfor w (.values state.workers)
                :if (and w.node (> (- now (.get (.get state.nodes w.node {}) "at" (- now NODE-LABELS-TTL-MS 1)))
                                   NODE-LABELS-TTL-MS))
                w.node)))


(deff derived-capabilities [#^ dict labels #^ tuple table]  ; defk にできない: coordinator の調停(Program)が呼ぶ純粋な判断
  {:pre [(: labels dict) (: table tuple)] :post [(: % tuple)] :tags {:context "doeff-cluster" :role "judgment"}}
  "node の label → その node の worker に足す能力(名の順)。table = ClusterNaming の node-capabilities #(#(鍵 値 能力) …)。"
  (tuple (sorted (sfor #(key value capability) table :if (= (.get labels key) value) capability))))


(deff with-derived-capabilities [#^ ClusterState state #^ tuple table]  ; defk にできない: coordinator の調停(Program)が呼ぶ純粋な判断
  {:pre [(: state ClusterState) (: table tuple)] :post [(: % ClusterState)] :tags {:context "doeff-cluster" :role "judgment"}}
  "node の label の観測から、各 worker の derived(導いた能力)を作り直す。label を読めなかった node(error)の worker は前の値を保つ
   (届かない間に会社の機体の能力を外したり足したりしない — 次に読めた時に直る)。node を名乗らない worker は空。"
  (setv workers {})
  (for [#(name w) (.items state.workers)]
    (setv seen (.get state.nodes w.node))
    (setv derived
      (cond
        (not w.node) #()
        (or (is seen None) (in "error" seen)) w.derived
        True (derived-capabilities (get seen "labels") table)))
    ;; 能力の計算と検査は毎回行い、値が等しい時だけ既存の object を返す。
    (setv (get workers name) (if (= derived w.derived) w (replace w :derived derived))))
  (if (= workers state.workers) state (replace state :workers workers)))
