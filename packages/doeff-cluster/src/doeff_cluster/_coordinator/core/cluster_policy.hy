;;; coordinator の純粋な判断。宣言された job・task・worker の生存・今の割り当て・時刻から、次の割り当てを導く。
;;; HTTP の要求 1 件への返事も、状態と要求と時刻から (次の状態 status 本文) を返す純粋な関数にする。I/O はしない。
;;; 割り当ては安定させる: 担い手が移し替えの期限内に生きていれば動かさない。
(require doeff-hy.macros [defk deff val var])
(val MODULE-TAGS {:context "coordinator" :role "judgment"})
(import collections.abc [Mapping])
(import dataclasses [replace])
(import functools)
(import hashlib)
(import itertools [groupby])
(import operator [attrgetter])
(import json)
(import math [ceil])
(import re)
(import typing [TypeGuard])

(import doeff_cluster._shared.intent.job_model [JobSpec LEGACY-ENTRY-PLACE])
(import doeff_cluster._shared.core.job_rules [content-refusal memory-refusal single-host-refusal idle-budget-read idle-budget-json])
(import doeff_cluster._shared.intent.protocol [ClusterTiming Request BodyInvalid])
(import doeff_cluster._shared.core.capabilities [capabilities-of environ-pairs])
(import doeff_cluster._shared.core.supplied_spelling [supplied-spellings])
(import doeff_cluster._shared.core.detached_rules [task-priority-refusal task-limits-refusal task-limits-of task-memory-refusal])
(import doeff_cluster._coordinator.intent.cluster_model [ClusterJob ErrorReply TaskAccepted TaskProgress TaskMissing TaskResultTaken BoardTally BoardUsage BoardWritten BoardConflict BoardRefused WorkerInfo TaskOffer WarmOffer HeartbeatReply ServiceView WorkerView StatusView StateView GroupLimitView GroupHeld BoardRow WorkerReport GenerationOrder Placement ClusterState TaskRecord EnvFailed HandoffPhase UnplacedKind UnplacedView TaskUnplacedKind WorkerLoad YieldCandidate YieldPlan PlacementClass ACCEPTED-FORMATS PLACED-PHASES TaskClaim])
(import doeff_cluster._coordinator.intent.cluster_model [NodeLabelsSeen KeepMark KnownExit SupersededRun HandoverBlocker GenerationRow])
(import doeff_cluster._shared.intent.detached_model [TaskLimit GroupLimit])
(import doeff_cluster._shared.intent.due_model [DueAt DueNow DueNever])
(import doeff_cluster._shared.core.due_policy [due-of-instants])
(import doeff_cluster._coordinator.intent.worker_notices [WorkerBack WorkerGone])
(import doeff_hy._table [Table TableWrite TableDraft draft-of table-of])
(import doeff_cluster._coordinator.intent.task_table [task-index held-row?])
(import doeff_cluster._coordinator.core.cluster_rules [component-versions-of format-version-refusal])
(import doeff_cluster._coordinator.intent.request_bodies [LeaseBody TaskResultBody BoardWrite HeartbeatBody EnvsReport StatusRow TaskBody MeasuredLoad])
(import doeff_cluster._coordinator.core.cluster_rules [required-field int-field])
(import doeff_cluster._shared.intent.semaphore_model [SEMAPHORE-PREFIX])
(import doeff_cluster._shared.core.lease_rules [lease-op semaphore-write-refusal semaphore-key drop-holders lease-holder holder-tokens-prefix live-instances])
(import doeff_cluster._shared.intent.readiness_model [RETIREMENT-HOLD])
(import doeff_cluster._shared.core.board_rules [board-allows board-ttl-refusal])
(import doeff [run])
(import doeff_cluster._shared.intent.runtime_env_model [RuntimeEnvInvalid])
(import doeff_cluster._shared.core.runtime_env_rules [runtime-env-of-json env-key dependency-key child-environ-refusal])
(import doeff_cluster._shared.core.readiness_rules [readiness-refusal retired-lifetime-ms retired-limit-of])
(import doeff_cluster._coordinator.core.program_policy [PROGRAM-GRACE-MS program-refs])
(import doeff_cluster._coordinator.core.measured_room [measured-room reserve-of unseen-since need-of reserved-mib-of memory-room-mib memory-line-mib
                                                       drained-room-mib keeps-room? urgent-priority? PEAK-STEP-MIB])
(import doeff_cluster._coordinator.core.task_claims [shape-peaks-of task-need-of claim-mib-of offered-mib-of])

;; 子の入口の名。子は `hy -m <名>` として起きるので、worker が子の起きる doeff で解ける置き場の名へ替えて検め・起こす
;; (worker/core/launch の child-entry-place と JobSpec.entry-at — 実行環境の job は root ごとの置き場で、_worker の無い古い版の root では
;; 区画を隠す前の名・木の job は worker 自身の置き場)。送る綴りは指紋の材料の
;; 綴り(置き場 LEGACY-ENTRY-PLACE)と同じにする: 置き場を読まない古い worker は返事の名を綴りのまま指紋に入れるので、綴りが違うと
;; coordinator を先に上げた拍に全部の job を起こし直し、その後も指紋が宣言と合わずに NotReady のまま残る(2026-11-02 の本番 —
;; tests/test_older_worker_reads_the_wire_entry.hy)。
(setv #^ str JOB-ENTRY (+ LEGACY-ENTRY-PLACE ".job_entry"))
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


;; --- 宣言の読み書き(JSON ⇄ 型) ----------------------------------------------------

(defn #^ (get TypeGuard (get dict #(str object))) json-object? [#^ object value]
  "JSON の値が object(dict)か — 真の枝で値を dict[str, object] として読める型の門。JSON の object の鍵は文字列なので、鍵は検めない。
   宣言の行(Mapping[str, object])の欄の値は object で入るので、dict を受ける部品へ渡す前にこの門で絞る(実行の検めは isinstance 1 つ)。"
  (isinstance value dict))


(defn #^ (get TypeGuard (get list object)) json-array? [#^ object value]
  "JSON の値が array(list)か — 真の枝で値を list[object] として読める型の門。"
  (isinstance value list))


(defn #^ (| str None) declared-runtime-env [#^ (get Mapping #(str object)) item]
  "宣言 1 行の runtimeEnv(在れば)の正規化した JSON の文字列(JobSpec.runtime-env — 比べる欄)。無ければ None。"
  (setv value (.get item "runtimeEnv"))
  (if (is value None) None (json.dumps value :sort-keys True :ensure-ascii False)))

(defn #^ JobSpec spec-of-declaration [#^ (get Mapping #(str object)) item]
  "宣言 1 行 → worker が起動する形(job_entry の service 入口と詰めた Program の置き場のキー)。宣言の job は Program の job だけ —
   run の無い行(生の entry と args を worker に直に起こさせる形)は理由つきで断る(ADR-DOE-CLUSTER-001 R1・R7 — 移行の期間は置かない)。
   runtimeEnv を持つ宣言は、worker が env の root を準備してその venv で起こす(版は worker が env のキーへ置き換える)。
   行は JSON の object の読み口 Mapping[str, object](行の型の TypedDict も渡せる)— 欄の値は検めを通った所で型を絞って取り出す。"
  (setv run (.get item "run") revision (required-field item "revision") runtime (declared-runtime-env item) name (.get item "name"))
  ;; 名前と revision の空は送り手の誤り(400)— JobSpec の不変条件(name・entry・revision が要る)の素の ValueError まで運ばない(#2796:
  ;; 本文の読みの中の ValueError が調停ループの外まで抜け、coordinator の process ごと落ちた)。
  (cond
    (not (and (isinstance name str) name)) (raise (BodyInvalid (.format "Service の名前は空でない文字列: {!r}" name)))
    (is run None) (raise (BodyInvalid (raw-entry-refusal item)))
    (not (json-object? run)) (raise (BodyInvalid (.format "run は JSON の object: {!r}" run)))
    (not (and (isinstance revision str) revision)) (raise (BodyInvalid (.format "revision は空でない文字列: {!r}" revision)))
    (= (.get run "kind") "service")
      (do (setv refusal (program-row-refusal item run))
          (when refusal (raise (BodyInvalid refusal)))
          ;; 欄の取り出し: program-row-refusal が run.program を 64 桁の sha の文字列・run.versions と environ を文字列の鍵と値の
          ;; object と確かめた後なので、ここは型を絞るだけ(欄が無ければ空の組)。readiness は job-from-json が readiness-refusal で確かめる。
          (setv handoff (= (.get item "update") "handoff") readiness (.get item "readiness")
                program (.get run "program") versions (.get run "versions") environ (.get item "environ"))
          (setv held (if (json-object? readiness) readiness None))
          (JobSpec name JOB-ENTRY #("service" "--identity" (identity-hash run))
                   revision
                   :handoff handoff :runtime-env runtime
                   ;; 退いた process の寿命の上限(#4072 の D-2 — 宣言の readiness の retiredSeconds・handoff の job だけ)。
                   :retired-ms (if handoff (retired-lifetime-ms held) None)
                   ;; 退いた process を同時に残す数の上限 R(#4072 の D-3 の改め — 宣言の readiness の retiredLimit・handoff の job だけ)。
                   :retired-limit (if handoff (retired-limit-of held) None)
                   :program (if (isinstance program str) program None)
                   ;; 子の入口が比べる送り手の版 = 宣言の行の run.versions(宣言の道具が Program と一緒に置いた版)。worker は Program の
                   ;; cache の file をこの版ごとに分けるので、同じ sha の Program を別の版で宣言し直すと取り直す。
                   :versions (if (json-object? versions) (environ-pairs versions) #())
                   :environ (if (json-object? environ) (environ-pairs environ) #())
                   ;; 中身の鍵(在れば指紋と比べが版と commit の代わりにこれを読む・program-row-refusal が形を確かめた)。
                   :content (.get item "content")))
    True (raise (BodyInvalid (+ "知らない run.kind: " (repr (.get run "kind")))))))


;; --- Program の job の行(ADR-DOE-CLUSTER-001・改訂 1 の A・E・F・G) -------------------------------------

(setv PROGRAM-SHA (re.compile r"[0-9a-f]{64}"))
;; 旧い宣言の run の欄(関数の参照 + 設定 + handler の組の import path — 2026-09-27 より前の形)。
(setv OLD-RUN-KEYS #("factory" "env" "config"))
;; image の版を追う係(base-follow — 2026-09-28 に消した)の行の欄: 追う Deployment・追った commit・定義だけを別の commit で重ねる版。
(val IMAGE-FOLLOW-KEYS #("baseFrom" "base" "overlay"))


(deff raw-entry-refusal [#^ (get Mapping #(str object)) item]  ; cannot be a defk: 宣言の読み(coordinator の純粋な判断)が呼ぶ
  {:pre [(: item Mapping)] :post [(: % str)] :tags {:context "coordinator" :role "judgment"}}
  "run の無い宣言の行(worker に module と引数を直に起こさせる生の entry の job)を断る理由の文 — 書きの口の 400・保存の読み直しの
   RefusedJob の理由に同じ文を出すため。宣言の job は Program の値 1 つだけ(ADR-DOE-CLUSTER-001 R1・R7)。"
  (.format "生の entry の job(entry {!r}・args {!r}・run が無い)は受け付けない — job は Program の値 1 つで宣言する(defsystem と declare・ADR-DOE-CLUSTER-001 R1)"
           (.get item "entry") (.get item "args")))


(deff identity-hash [#^ (get Mapping #(str object)) run]  ; cannot be a defk: 宣言の読み(coordinator の純粋な判断)が呼ぶ
  {:pre [(: run Mapping)] :post [(: % str) (= (len %) 16)] :tags {:context "coordinator" :role "judgment"}}
  "Program の job の同一性の指紋 = identity(関数の参照と引数の正規 JSON)と versions の sha256 の頭 16 桁。job の引数に載り、
   spec-hash(revision・environ と一緒)の材料になる。詰めた Program の中身(program の sha)は入れない — 揺れるため(改訂 1 の A)。"
  (cut (.hexdigest (hashlib.sha256 (.encode (json.dumps {"identity" (get run "identity") "versions" (.get run "versions" {})}
                                                        :sort-keys True :ensure-ascii False :separators #("," ":"))
                                            "utf-8")))
       0 16))


(deff program-row-refusal [#^ (get Mapping #(str object)) item #^ (get dict #(str object)) run]  ; cannot be a defk: 宣言の読み(coordinator の純粋な判断)が呼ぶ
  {:pre [(: item Mapping) (: run dict)] :post [(: % (| str None))] :tags {:context "coordinator" :role "judgment"}}
  "Program の job の宣言の行 item(run = 行の run — 呼び手が JSON の object と確かめた物)が受けられない理由(受けられれば None)。
   旧い形(run.factory・run.env・run.config・requires)・
   image の版を追う欄(baseFrom・base・overlay — Program を詰めた commit と別の commit で解くことになる — 改訂 1 の E)・置き場のキーの形・identity の欠け・
   versions の形・environ の名(child-environ-refusal の検め・実行環境の env-vars との重なり — 改訂 1 の G)を検める。"
  (setv old (lfor k OLD-RUN-KEYS :if (in k run) k)
        environ (.get item "environ" {})
        runtime-env (.get item "runtimeEnv")
        env-vars (if (json-object? runtime-env) (.get runtime-env "envVars") None)
        declared (if (json-array? env-vars) (lfor v env-vars :if (json-object? v) (get v "name")) [])
        program (.get run "program")
        identity (.get run "identity")
        versions (.get run "versions" {}))
  (cond
    old (.format "旧い宣言の形(run の {})は受け付けない — Program の値 1 つで宣言し直す(ADR-DOE-CLUSTER-001 R1・R3b)"
                 (.join "・" old))
    (is-not (.get item "requires") None) "旧い宣言の形(requires)は受け付けない — 要る能力 needs で宣言し直す(R4b)"
    (any (gfor k IMAGE-FOLLOW-KEYS (is-not (.get item k) None)))
      (.format "Program の job は {} を持たない(image の版を追う形 — 詰めた commit と別の commit で解くことになる)"
               (.join "・" (gfor k IMAGE-FOLLOW-KEYS :if (is-not (.get item k) None) k)))
    (not (and (isinstance program str) (PROGRAM-SHA.fullmatch program)))
      (.format "run.program は詰めた Program の置き場のキー(64 桁の sha256): {!r}" program)
    (not (and (json-object? identity) (isinstance (.get identity "function") str)))
      "run.identity(呼んだ関数の参照と引数)が無い"
    (not (text-map? versions)) (.format "run.versions は部品の名 → 版の文字列の object: {!r}" versions)
    (not (json-object? environ)) (.format "environ は文字列の鍵と値の object: {!r}" environ)
    (is-not (content-refusal (.get item "content")) None) (content-refusal (.get item "content"))
    True (environ-refusal environ declared)))


(deff environ-refusal [#^ dict environ #^ list declared]  ; cannot be a defk: 宣言の読み(coordinator の純粋な判断)が呼ぶ
  {:pre [(: environ dict) (: declared list)] :post [(: % (| str None))] :tags {:context "coordinator" :role "judgment"}}
  "宣言の行・task の本文の environ が受けられない理由。名と値は実行環境の env-vars と同じ検め(runtime_env_rules.child-environ-refusal —
   EnvVar の名の形・worker の予約・秘密の中身の名)で、env-vars と同じ名は断る(子の環境変数の足し口を 1 つにする — 改訂 1 の G)。"
  (setv problem (child-environ-refusal environ))
  (when problem (return problem))
  (setv clash (sorted (gfor k environ :if (in k declared) k)))
  (if clash
      (.format "environ の {} は実行環境の env-vars と同じ名 — どちらか 1 つで宣言する" clash)
      None))


(deff task-environ-refusal [#^ (| dict list tuple str int float bool None) environ #^ (| dict list tuple str int float bool None) runtime]  ; cannot be a defk: HTTP の本文を読む境界(Program の外)が呼ぶ純粋な判断
  {:pre [(: environ (| dict list tuple str int float bool None)) (: runtime (| dict list tuple str int float bool None))] :post [(: % (| str None))] :tags {:context "coordinator" :role "judgment"}}
  "task(POST /tasks・PUT /detached)の本文の environ(子の環境変数 — 無ければ空)が受けられない理由。規則は service の宣言の行と同じ
   environ-refusal 1 つ(2026-09-28)。runtimeEnv の形の誤りは runtime-env-refusal が断るので、ここでは object の時だけ
   env-vars の名と比べる。"
  (setv environ (if (is environ None) {} environ))
  (if (not (isinstance environ dict))
      (.format "environ は環境変数の名 → 文字列の object: {!r}" environ)
      (environ-refusal environ (if (isinstance runtime dict)
                                   (lfor v (.get runtime "envVars" []) :if (isinstance v dict) (.get v "name"))
                                   []))))


(defn #^ (| str None) optional-text-field [#^ object value #^ str field]
  "宣言の行の欄(文字列か null)の値 → 文字列か None。他の型は送り手の誤り(BodyInvalid — 400)。pin・owner のように、宣言の型が
   文字列か None の欄を ClusterJob へ渡す前に検める。"
  (cond
    (is value None) None
    (isinstance value str) value
    True (raise (BodyInvalid (.format "{} は文字列か null: {!r}" field value)))))


(defn #^ ClusterJob declared-job-from-json [#^ (get Mapping #(str object)) item]
  "送り手が書く宣言の行(Service の資源の POST・PUT と旧い PUT /jobs)を、memory の欄 peakMiB・settleMiB を必ず求めて読むため
   (欄の無い宣言は断り、既定の値で埋めない)。保存の読み直しは job-from-json を直に呼び、欄の無い行も受ける。"
  ;; 行の形の誤り(旧い宣言の形など)は先に job-from-json が今までどおりの理由で断る。欄の無さはその後に言う。
  (setv job (job-from-json item) problem (memory-refusal (.get item "peakMiB") (.get item "settleMiB")))
  (when (is-not problem None)
    (raise (BodyInvalid (.format "Service {} の宣言: {}" (.get item "name") problem))))
  job)


(defn #^ ClusterJob job-from-json [#^ (get Mapping #(str object)) item]
  "宣言の行(送られた行と保存の行)→ ClusterJob。memory の欄は在れば検め、無ければ None で読む — 旧い coordinator は memory を持たずに
   job を保存したので、新しい coordinator が置き場を読み直す時に欄の無い行を断らない(断ると RefusedJob になり job が止まる)。
   欄を必ず求めるのは送り手の書く口(declared-job-from-json)だけ。"
  (setv peak (.get item "peakMiB") settle (.get item "settleMiB") single-host (.get item "singleHost")
        idle-budget (if (is (.get item "idleBudget") None) None (idle-budget-read (.get item "idleBudget"))))
  (setv memory-problem (or (if (and (is peak None) (is settle None)) None (memory-refusal peak settle))
                           (single-host-refusal single-host)
                           (if (isinstance idle-budget str) idle-budget None)))
  (when (is-not memory-problem None)
    (raise (BodyInvalid (.format "Service {} の宣言: {}" (.get item "name") memory-problem))))
  (setv replicas (.get item "replicas" 1) readiness (.get item "readiness"))
  (when (not-in replicas #(0 1))
    (raise (BodyInvalid (.format "replicas は 0 か 1(Service は 1 つだけ動かす): {!r}" replicas))))
  (setv update (.get item "update" "recreate"))
  (when (not-in update #("recreate" "handoff"))
    (raise (BodyInvalid (.format "update は recreate か handoff: {!r}" update))))
  ;; readiness の形(windowSeconds・入れ替えの期限 handoffTimeoutSeconds)は宣言の側と同じ規則(readiness_rules.readiness-refusal)。
  (setv readiness-problem (readiness-refusal readiness update))
  (when (is-not readiness-problem None)
    (raise (BodyInvalid readiness-problem)))
  (setv env-refusal (runtime-env-refusal item))
  (when (is-not env-refusal None)
    (raise (BodyInvalid env-refusal)))
  (setv spec (spec-of-declaration item) run (.get item "run"))
  (ClusterJob spec
              (request-needs item "Service の needs")
              (optional-text-field (.get item "pin") "pin")
              ;; 受け付けた job は run を持つ(spec-of-declaration が object と確かめた)・readiness は None か object(readiness-refusal)。
              (if (json-object? run) run None)
              replicas
              (if (json-object? readiness) readiness None)
              (optional-text-field (.get item "owner") "owner")
              update
              ;; memory-refusal が整数と確かめた後なので、ここは型を絞るだけ。
              :peak-mib (if (isinstance peak int) peak None)
              :settle-mib (if (isinstance settle int) settle None)
              :single-host (if (isinstance single-host str) single-host None)
              ;; 読めない予算(理由の文)は上で断った後なので、ここは型を絞るだけ。
              :idle-budget (if (isinstance idle-budget str) None idle-budget)))


(defn #^ dict job-to-json [#^ ClusterJob job]
  (setv base {"name" job.spec.name "revision" job.spec.revision
              "needs" (list job.needs) "pin" job.pin
              "replicas" job.replicas "readiness" job.readiness "owner" job.owner})
  ;; 入れ替えの欄は、使う宣言にだけ書く(使わない宣言の spec の形・版は以前と同じ)。
  (setv extra (| (if (= job.update "recreate") {} {"update" job.update})
                 (if (is job.spec.runtime-env None) {} {"runtimeEnv" (json.loads job.spec.runtime-env)})
                 (if job.spec.environ {"environ" (dict job.spec.environ)} {})
                 ;; 中身の鍵は送られた宣言にだけ書く(鍵の無い宣言の spec の形は以前と同じ)。
                 (if (is job.spec.content None) {} {"content" job.spec.content})
                 ;; memory は知っている時だけ書く(旧い coordinator の保存から読んだ job は次の宣言し直しまで知らない)・訳は在る時だけ。
                 (if (is job.peak-mib None) {} {"peakMiB" job.peak-mib "settleMiB" job.settle-mib})
                 (if (is job.single-host None) {} {"singleHost" job.single-host})
                 ;; 暇な時の予算は宣言した job にだけ書く(書かない宣言の spec の形・版は以前と同じ)。
                 (if (is job.idle-budget None) {} {"idleBudget" (idle-budget-json job.idle-budget)})))
  ;; 受け付けた job は Program の job だけ(run を持つ — spec-of-declaration)。行は run を運ぶ(entry と args は worker の内部の形)。
  (| base extra {"run" job.run}))


(defn #^ (| int None) boot-at-of [#^ dict body]
  "heartbeat の本文・保存の形の bootAt(process の起動時刻・epoch ms)。整数でない値(欄の無い旧い worker を含む)は知らない = None。"
  (setv value (.get body "bootAt"))
  (if (and (isinstance value int) (not (isinstance value bool))) value None))


(defn #^ (| int None) resource-version-of [#^ ClusterState state #^ str key]
  "資源 key(<種類>/<名>)の今の版(版の記録が無ければ None)。"
  (setv meta (.row state.meta key))
  (if (is meta None) None meta.resource-version))


(defn #^ dict status-row-to-json [#^ StatusRow row]
  "worker の状態の報告の行 → 見せる JSON の形(GET /state の statuses と Service の status.process — worker/protocol/heartbeat の
   status-row と同じ欄。準備の失敗と入口の検めの欄は在る時だけ・結果と task の写しは持ち続けないので載せない)。lastExitAtMs は
   最後に終わったと知れた刻(worker-report・known-exits-after)— 機体が死んで世代が入れ替わった時は、新しい世代の起動の刻を上限として
   数える(実の終わりはそれ以前)。世代が重なる時(退いた世代の process がまだ走る)は、退いた世代が報告した終わりの刻も大きい方の候補に
   入れる。coordinator を作り直しても戻らない(worker/<名> の保存の行の knownExits から運ぶ)。注記: Service が別の worker へ置き直されると
   Service の status.process は新しい担い手の行で、前の担い手の刻は出ない・沈黙が 7 日続いた worker を忘れると(forget-silent-workers)
   その worker の刻も消える・刻はその worker の node の時計(generation-order と同じ前提)。"
  (| {"name" row.name "phase" row.phase "desiredRevision" row.desired-revision "runningRevision" row.running-revision
      "pid" row.pid "attempts" row.attempts "detail" row.detail
      "failures" row.failures "lastExitCode" row.last-exit-code "lastExitAtMs" row.last-exit-at-ms
      "instance" row.instance "specHash" row.spec-hash
      "placement" row.placement "retiredFrom" row.retired-from}
     (if (is row.failure-kind None) {} {"failureKind" row.failure-kind "retryable" row.retryable})
     (if (is row.probe None) {} {"probe" {"state" row.probe.state "elapsedSeconds" row.probe.elapsed-seconds
                                          "attempts" row.probe.attempts "lastFailure" row.probe.last-failure}})))


;; --- 割り当て ------------------------------------------------------------------------

(deff liveness-deadline [#^ WorkerInfo worker #^ int window-ms]  ; cannot be a defk: 生死の比べ alive(Program の外の純粋な判断)が呼ぶ
  {:pre [(: worker WorkerInfo) (: window-ms int)] :post [(: % int)] :tags {:context "coordinator" :role "judgment"}}
  "worker を窓 window-ms の内で生きていると数える最後の刻(epoch ms)を求めるため。生死の判断(alive・forget-silent-workers)と、その
   答えが変わる最初の刻を求める liveness-due が同じこの値で比べる(#3061 — 片方だけ比べ方を変えると、模擬の静かな区間が判断の
   変わる歩を試さずに飛ぶ)。"
  (+ worker.last-seen-ms window-ms))


(defn #^ bool alive [#^ int now #^ WorkerInfo worker #^ int window-ms]
  "worker が刻 now に窓 window-ms の内で生きているか(最後の連絡から window-ms 以内か)を判じるため。"
  (<= now (liveness-deadline worker window-ms)))


(deff silent-names [#^ ClusterState state #^ int now #^ ClusterTiming timing]  ; cannot be a defk: note-liveness(Program の外の純粋な判断)が呼ぶ
  {:pre [(: state ClusterState) (: now int) (: timing ClusterTiming)] :post [(: % frozenset)] :tags {:context "coordinator" :role "judgment"}}
  "刻 now に生きていないと数える worker の名を求めるため(note-liveness と liveness-due が同じ求め方を使う)。"
  (frozenset (gfor w (.rows state.workers) :if (not (alive now w timing.lease-ms)) w.name)))


(deff note-liveness [#^ ClusterState state #^ int now #^ ClusterTiming timing]  ; cannot be a defk: 調停の純粋な判断(api_policy.settle — Program の外)が呼ぶ
  {:pre [(: state ClusterState) (: now int) (: timing ClusterTiming)] :post [(: % ClusterState)] :tags {:context "coordinator" :role "judgment"}}
  "生きていないと数える worker の名(ClusterState.silent)を今の時刻で求め直すため(#1934)。変わらなければ同じ値を返す(版を進めない)。
   変われば新しい値 — Worker の資源の status の live が変わり、stamp が版を進めて出来事を 1 行残す(死んだ拍と戻った拍だけ)。"
  (let [silent (silent-names state now timing)]
    (if (= silent state.silent) state (replace state :silent silent))))


;; --- worker の生死の出来事(#3864)------------------------------------------------------------------------------------------
;; coordinator が worker の生死を process の外へ出す出来事を、沈黙の集合(note-liveness が求める ClusterState.silent)の 1 か所から
;; 求める。出すのは program.coordinator-step(保存の後)。時間で見回る所は無い — 沈黙の集合が変わる歩でだけ出来事が在る。

;; WorkerGone の期限は生死の判断と同じ liveness-deadline・WorkerBack の刻は最後の heartbeat。

(defk liveness-moves [before after timing]
  {:pre [(: before ClusterState) (: after ClusterState) (: timing ClusterTiming)] :post [(: % tuple)]
   :tags {:context "coordinator" :role "judgment"}}
  "coordinator の 1 歩(before → after)で worker の生死が変わった所を、出す出来事の列にするため(名の順)。沈黙の集合に入った名 =
   WorkerGone・出た名のうち after の名簿に残る名 = WorkerBack(長い沈黙で名簿から消えた worker — forget-silent-workers — は戻った事に
   しない)。変わらなければ空。"
  (val gone (sorted (- after.silent before.silent)))
  (val back (sorted (gfor name (- before.silent after.silent) :if (is-not (.row after.workers name) None) name)))
  (+ (tuple (gfor name gone :setv w (.row after.workers name)
                  (WorkerGone :worker name :boot w.boot :deadline-ms (liveness-deadline w timing.lease-ms))))
     (tuple (gfor name back :setv w (.row after.workers name)
                  (WorkerBack :worker name :boot w.boot :seen-ms w.last-seen-ms)))))


(defk liveness-now [state timing]
  {:pre [(: state ClusterState) (: timing ClusterTiming)] :post [(: % tuple)] :tags {:context "coordinator" :role "judgment"}}
  "coordinator が起きた時に、名簿の全部の worker の今の生死を 1 度出すため(名の順 — 沈黙の worker は WorkerGone・ほかは WorkerBack)。
   沈黙の集合は保存の形に無いので、起き直しの後の最初の歩の状態から求める。受け手の追いつきにも成る。"
  (tuple (gfor name (sorted (.keys state.workers)) :setv w (.row state.workers name)
               (if (in name state.silent)
                   (WorkerGone :worker name :boot w.boot :deadline-ms (liveness-deadline w timing.lease-ms))
                   (WorkerBack :worker name :boot w.boot :seen-ms w.last-seen-ms)))))


(deff placeable [#^ (get tuple #(str ...)) needs #^ WorkerInfo worker]  ; cannot be a defk: coordinator と模擬の置き先の選び(Program の外の純粋な判断)が呼ぶ
  {:pre [(: needs tuple) (: worker WorkerInfo)] :post [(: % bool)] :tags {:context "coordinator" :role "judgment"}}
  "needs の job / task をこの worker に置けるか — 置き場所の規則の定義点はここ 1 つ(ADR-DOE-CLUSTER-001 R4b):
   needs ⊆ provides ∪ derived(node の label から coordinator が導いた能力)、かつ worker が専用の能力(exclusive)を持てば、そのどれかを needs に持つ(一般の仕事を専用の担い手に置かない)。"
  (and (<= (set needs) (| (set worker.provides) (set worker.derived)))
       (or (not worker.exclusive) (bool (& (set needs) (set worker.exclusive))))))


(deff named-capabilities [#^ (| tuple list None) provides #^ (| tuple list None) exclusive #^ bool old-labels #^ str what]  ; cannot be a defk: heartbeat の本文と保存の JSON を読む境界(Program の外)が呼ぶ
  {:pre [(: provides (| tuple list None)) (: exclusive (| tuple list None)) (: old-labels bool) (: what str)] :post [(: % tuple) (= (len %) 2)] :tags {:context "coordinator" :role "judgment"}}
  "worker の能力の名乗り(provides・exclusive の名の列 — None = 欄が無い)→ #(provides exclusive)。exclusive は provides の一部でなければ
   ならない。旧い形(labels だけで provides の無い名乗り — old-labels)は BodyInvalid(ADR-DOE-CLUSTER-001 R4b)。heartbeat の本文の型
   (#2445)と保存の行(worker-capabilities-of)が同じ規則で読む。"
  (when (and old-labels (is provides None))
    (raise (BodyInvalid (.format "{}: 旧い形の labels は受け付けない — worker は --provides と --exclusive で能力を名乗る" what))))
  (setv provides (capabilities-of (list (or provides [])) (+ what " の provides")))
  (setv exclusive (capabilities-of (list (or exclusive [])) (+ what " の exclusive")))
  (when (not (<= (set exclusive) (set provides)))
    (raise (BodyInvalid (.format "{}: exclusive {} は provides {} の一部で名乗る" what (list exclusive) (list provides)))))
  #(provides exclusive))


(deff request-needs [#^ (get Mapping #(str object)) body #^ str what]  ; cannot be a defk: HTTP の本文・宣言の JSON を読む境界(Program の外)が呼ぶ
  {:pre [(: body Mapping) (: what str)] :post [(: % tuple)] :tags {:context "coordinator" :role "judgment"}}
  "送られた宣言・task・温める頼みの本文の needs → 名の順の tuple。旧い形の requires を持つ本文・空の needs は BodyInvalid(理由つき・ValueError の子)—
   旧い宣言は受け付けない・要る能力は必ず書く(改訂 1 の I)。"
  (needs-named (.get body "needs") (.get body "requires") what))


(deff needs-named [#^ object needs #^ object requires #^ str what]  ; cannot be a defk: HTTP の本文・宣言の JSON を読む境界(Program の外)が呼ぶ
  {:pre [(: needs "JSON の値 — 能力の名の列か、欄が無い None") (: requires "JSON の値 — 旧い形の欄の値か、欄が無い None") (: what str)] :post [(: % tuple)] :tags {:context "coordinator" :role "judgment"}}
  "要る能力の名乗り(needs と旧い形の requires の値 — None = 欄が無い)→ 名の順の tuple。宣言の行(request-needs)と本文の型(#2445)が
   同じ規則で読む: requires は BodyInvalid・空の needs は BodyInvalid。"
  (when (is-not requires None)
    (raise (BodyInvalid (.format "旧い形の requires {!r} は受け付けない — 要る能力の名の列 needs で書き直す(ADR-DOE-CLUSTER-001 R4b)"
                                requires))))
  (setv needs (capabilities-of (if (is needs None) [] needs) what))
  (when (not needs)
    (raise (BodyInvalid (.format "{} が空 — 要る能力の名を 1 つ以上書く(どこにでも置ける仕事は無い・ADR-DOE-CLUSTER-001 R4b・改訂 1 の I)" what))))
  needs)


(deff task-body-refusal [#^ ClusterState state #^ TaskBody body]  ; cannot be a defk: HTTP の本文を読む境界(Program の外)が呼ぶ純粋な判断
  {:pre [(: state ClusterState) (: body TaskBody)] :post [(: % (| str None))] :tags {:context "coordinator" :role "judgment"}}
  "task(POST /tasks・PUT /detached)の本文が受けられない理由 — 旧い形の env(handler の組の import path)・旧い形の blob(詰めた
   Program を本文に載せる形)と versions(版の写し)・置き場のキー program の形と置き場に在るか・子の環境変数 environ(service の :environ と同じ規則)・needs の欠け。task も Program の値 1 つで、handler は Program の
   中で並べ(ADR-DOE-CLUSTER-001 R1・R2・改訂 1 の J の 11)、詰めた Program は service の宣言と同じく先に /programs/<sha> に置いて
   本文は sha だけを運ぶ(R3b — service と task で運び方を分けない)。"
  (let [program body.program]
    (cond
      (is-not body.env None)
        (.format "旧い形の env {!r}(handler の組の import path)は受け付けない — handler は task の Program の中の with-handlers で並べる"
                 body.env)
      (is-not body.blob None)
        "旧い形の blob(詰めた Program を本文に載せる形)は受け付けない — 先に PUT /programs/<sha> で置き、本文は program に sha を書く"
      ;; 版は Program と一緒に置いた版 1 つ(program-versions)。本文の写しは置いた版と食い違いうるので受けない(黙って捨てない)。
      (is-not body.versions None)
        "本文の versions は受け付けない — task の版は PUT /programs/<sha> で Program と一緒に置いた版を使う"
      (not (and (isinstance program str) (PROGRAM-SHA.fullmatch program)))
        (.format "program は詰めた Program の置き場のキー(64 桁の sha256): {!r}" program)
      (is (.row state.programs program) None)
        (.format "program {} は置き場に無い — 先に PUT /programs/{} で置く" program program)
      ;; 版(送り手の commit)は行の必須の欄(TaskRecord.revision)— 欠けを行を作る所の KeyError に任せない(#1024)。
      (not (isinstance body.revision str))
        (.format "revision(送り手の commit の文字列)が無い: {!r}" body.revision)
      True (or (task-environ-refusal body.environ body.runtime-env) (needs-refusal body.needs body.requires)))))


(deff program-versions [#^ ClusterState state #^ str sha]  ; cannot be a defk: HTTP の本文を読む境界(Program の外)が呼ぶ純粋な判断
  {:pre [(: state ClusterState) (: sha str)] :post [(: % tuple)] :tags {:context "coordinator" :role "judgment"}}
  "置き場に置いた Program の送り手の版(名の順の tuple)— task の版は詰めた Program と一緒に置いた版 1 つから取る(本文に版の写しを
   運ばせない・置く worker の版と比べる — can-run-task)。呼ぶ前に task-body-refusal が置き場に在ることを確かめる。"
  (setv row (.row state.programs sha))
  (when (is row None)
    (raise (KeyError sha)))
  (component-versions-of row.versions))


(deff needs-refusal [#^ (| dict list tuple str int float bool None) needs #^ (| dict list tuple str int float bool None) requires]  ; cannot be a defk: HTTP の本文を読む境界(Program の外)が呼ぶ純粋な判断
  {:pre [(: needs (| dict list tuple str int float bool None)) (: requires (| dict list tuple str int float bool None))] :post [(: % (| str None))] :tags {:context "coordinator" :role "judgment"}}
  "本文の needs(と旧い形の requires)が受けられない理由(受けられれば None)— 400 の理由の文を 1 か所で作るため。"
  (try
    (needs-named needs requires "needs")
    None
    (except [error ValueError]
      (str error))))


(deff effective-needs [#^ ClusterState state #^ ClusterJob job]  ; cannot be a defk: 置き先の判断(eligible)と表示の綴り(unplaced-text の呼び手 — Program の外の純粋な判断)が呼ぶ
  {:pre [(: state ClusterState) (: job ClusterJob)] :post [(: % tuple)] :tags {:context "coordinator" :role "judgment"}}
  "Service に効く needs を、置き先の判断の 1 か所で決めるため: needs の一時の上書き(資源 NeedsOverride)が在れば
   上書きの needs、無ければ宣言の needs(ClusterJob.needs — 上書きは宣言を書き換えない)。上書きの行は、期限の来た拍と上書きの needs を
   満たす生きた worker が 0 になった拍に、置き方の前に外れる(resource_policy.lift-needs-overrides)ので、置き方の拍に残る行は満たせる行。
   置き先の判断(eligible — 置く・続ける・drain の移し先・置けない訳)と模擬が同じこの関数を読む。"
  (setv override (.row state.needs-overrides job.spec.name))
  (if (is override None) job.needs override.needs))


(defn #^ bool eligible [#^ ClusterState state #^ ClusterJob job #^ WorkerInfo worker]
  "Service をこの worker に置けるか(pin と、効く needs — effective-needs)。"
  (and (or (is job.pin None) (= job.pin worker.name))
       (placeable (effective-needs state job) worker)))


;; probing(2026-09-27)= starting の手前の入口の検めの間(その worker で起こしかけている — 他へ置かない)。
;; elder-runs(AL-D2f)= 同じ名の前の世代が動かしている間、新しい世代が準備と検めを揃えて待っている(その worker で起こしかけている)。
(setv LIVE-PHASES #{"preparing" "probing" "starting" "elder-runs" "backoff" "running" "stopping" "stop-unconfirmed"})


(defn #^ tuple service-rows [#^ int now #^ ClusterState state #^ str name #^ ClusterTiming timing]
  "job name の process の行の母集団(worker の名の順): lease の内に報告した全部の worker の最新の報告のうち、名が一致する行と、
   入れ替えで退いた process の行(行の名は <名>#retired-<世代>・retiredFrom = 名)。同じ名の新しい世代が名乗った後も job を動かして
   いる退いた世代の行(elder-rows)も入る — 受け渡しの間、job を動かしている process は退いた世代の物。「まだどこかで動いているか」
   (still-live-somewhere)と「どの版が動いているか」(resource_policy.live-processes)が同じ母集団を読むための定義点。"
  (tuple (gfor #(wname st) (sorted (.items state.observations.statuses))
               :setv w (.row state.workers wname)
               :if (and (is-not w None) (alive now w timing.lease-ms))
               row (+ st.jobs (tuple (gfor e (elder-rows now state wname timing) e.row)))
               :if (or (= row.name name) (= row.retired-from name))
               row)))


(defn #^ tuple elder-rows [#^ int now #^ ClusterState state #^ str worker #^ ClusterTiming timing]
  ;; cannot be a defk: coordinator の純粋な判断(service-rows の内包表記と resource_policy.carrying-row — defn)が呼ぶ
  "純粋: 名 worker の退いた世代のうち、組(SupersededRun)の最後の報告が lease の内の世代の、組の job の行(GenerationRow の列・世代の順)。
   世代の受け渡しの間、job を今動かしている process の行を Service の観測(service-rows・resource_policy.carrying-row)に出すため。
   黙った世代の行は数えない(handover-runs と同じ窓 — 黙った世代の process は止まったと読む)。"
  (setv info (.row state.workers worker) current (if (is info None) None info.boot))
  (tuple (gfor r state.superseded-runs
               :if (and (= r.worker worker) (!= r.boot current) (< (- now r.seen-ms) timing.lease-ms))
               :setv report (.row state.observations.elder-statuses (elder-key r.worker r.boot))
               :if (is-not report None)
               row report.jobs
               (GenerationRow :boot r.boot :row row))))


(defn #^ bool still-live-somewhere [#^ int now #^ ClusterState state #^ str name #^ ClusterTiming timing]
  "生きている worker の最新の報告に、その job がまだ動いている形で載っているか。載っている間は他へ置かない
   (動いている担い手から移す時、元の担い手が止め終えるまで新しい担い手を起動しない = 同じ job を 2 つ動かさない)。
   入れ替えで退いた process も、その job がまだ動いていると数える(母集団は service-rows)。"
  (any (gfor row (service-rows now state name timing) (in row.phase LIVE-PHASES))))


;; --- drain(2026-09-25) -----------------------------------------------------------------
;; worker の Pod を入れ替える前に、その上の書き手を別の worker へ移す。drain 中の worker には新しい置き先(job・task)を割り当てない。
;; 入れ替え(handoff)の Service の並べた置き先(surge)と付け替えは drain_policy.advance-drains(readiness を読む)。ここは置き先の
;; 判断が drain を知る部分だけ。

(defn #^ frozenset draining-workers [#^ ClusterState state #^ int now]
  "期限の内の drain を持つ worker の名。"
  (frozenset (gfor #(name d) (.items state.drains) :if (> d.until-ms now) name)))


(defn #^ bool relocating [#^ int now #^ ClusterState state #^ ClusterJob job #^ Placement placed #^ ClusterTiming timing]
  "入れ替え(handoff)の Service job の置き先 placed の担い手が、移し替えの期限の内で生きているのに、宣言の条件(pin と効く needs —
   eligible)を満たさなくなったか(宣言し直しで置き先が替わった)。その job は drain の job と同じく、条件を満たす別の worker へ並べて
   置き、並べた先が Ready になってから付け替える(drain_policy.advance-drains)— 担い手を先に外すと、新しい process が Ready になる
   まで Ready の書き手が居ない(2026-11-02: 起き上がりに数分の同期が要る service で、置き先の替わる宣言し直しのたびに 6〜9 分)。
   並べている間、担い手には版を据え置く印を返す(jobs-for — 担い手の上に新しい版を起こさない)。"
  (setv w (.row state.workers placed.worker))
  (and job.spec.handoff (> job.replicas 0) (is-not w None) (alive now w timing.reassign-after-ms) (not (eligible state job w))))


(defn #^ bool can-take [#^ int now #^ ClusterState state #^ ClusterJob job #^ WorkerInfo w #^ dict load #^ ClusterTiming timing
                        #^ (| frozenset None) [draining None]]
  "job(と並べた置き先 surge)を新しく置ける worker か(生きている・条件を満たす・drain 中でない・memory の残りに job の settle が入る —
   memory-misfit が None)。job は task の空き(task-room-of)を使わない。load = load-of の答え(worker の名 → WorkerLoad)。"
  (and (alive now w timing.lease-ms) (eligible state job w)
       (not-in w.name (if (is draining None) (draining-workers state now) draining))
       (is (run (memory-misfit w load job)) None)))


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
  (setv worker (.row state.workers name))
  (and (is-not boot None) (is-not worker None) (in boot worker.retired)))


(defn #^ bool other-generation-boot [#^ ClusterState state #^ str name #^ (| str None) boot]
  "純粋: drain の頼みの boot が、name の今の世代でない(退いた世代か、一度も見ていない世代)か。今の世代でない頼みに今の世代の
   drain を付けないため。boot の無い頼み・今の世代を知らない worker(読み直しの直後の旧い形の置き場など)は今の世代の頼みとみなす。"
  (setv worker (.row state.workers name))
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
  "世代に付いた drain(世代つきの頼み — Pod の preStop・止まり始めを告げる heartbeat)を、頼まれた時の世代と違う世代(Pod を作り直した後の
   worker)の heartbeat で解く。世代を持たない drain(世代を付けない頼み — 版上げの Program・手の頼み)は worker の名前に付き、どの世代の
   heartbeat でも解かない(頼み手の DELETE か期限まで保つ・#4177)。退いた世代の heartbeat はここへ来ない
   (register-heartbeat が先に分ける — 旧い世代の heartbeat が新しい世代の drain を解かず、旧い世代の drain を付け直さない)。
   解く拍に、drain の掛かった世代に置いた切り離した task へ予定した退きの印を付ける(retiring-marked — その世代の lease が後で切れても、
   走らせ直しの上限に数えない・2026-11-05)。"
  (setv d (.row state.drains name))
  (cond
    (or (is d None) (is boot None) (is d.boot None)) state
    (!= d.boot boot)
      (replace state :drains (.with-writes state.drains (tuple [(TableWrite name None)]))
                     :tasks (retiring-marked state.tasks name d.boot
                                             (.format "worker {} の世代 {} に drain(頼み手 {})が掛かったまま世代 {} に替わった"
                                                      name d.boot (or d.actor "名乗らない") boot)))
    True state))


;; --- 前の世代の process が持っていた名前付きの lease(#3770) ----------------------------------------------------------
;; worker が突然止まる(drain しない・終わった process の lease を返さない)と、子の process は終わっているのに、その process が名乗った
;; 担い手(<job>/<process の世代の名>/…)は盤の lease の行に期限まで残り、同じ名で起き直した worker の新しい process は期限まで lease を
;; 取れなかった。新しい世代(generation-order が NEWER)の名乗りは前の世代の process が終わった印(known-exits-after が前の世代の
;; process の終わりを新しい世代の起動の刻までと数えるのと同じ前提)なので、その時に前の世代の最新の報告で process の世代の名を持つ行の
;; 担い手を外す。担い手の綴りと外し方は worker が終わった process の lease を返すのと同じ定義(lease_rules.lease-holder・
;; holder-tokens-prefix・drop-holders)。退いた世代の heartbeat(OLDER)はここへ来ない — 外した担い手を戻さず、その process の延長は
;; lost と答える(lease_rules.renew)。前の世代が最後の報告の後に起こした process の担い手は報告に無いので、期限まで残る。
;; 例外(AL-D2e): 前の世代がまだ走っている間に起きた世代(heartbeat の overlaps-elder — 同じ機体に 2 つの世代を並べる RollingUpdate で、
;; 起動の script が前の世代の dir の錠を持たれていると見た)の名乗りは前の世代の終わりの印でないので、担い手を外さない。
;;
;; 退いた世代の止まり(2026-11-05 の node-h): overlaps-elder は「自分の外のどれかの世代の錠が持たれている」で立つので、さらに古い世代が
;; 退いている間は、直前の世代が突然止まっていても立つ。名乗りで外さなかった直前の世代の担い手は、その後の名乗りの前の世代の報告にも
;; 載らず、期限の切れたまま行に残る — 行から担い手が外れるのを待つ process(GET /watch?lease=&vacate=)には永久の持ち主になった。
;; そこで外す時を名乗りの 1 回の出来事だけに結ばず、「その世代は止まった」という判じにも結ぶ: 退いた世代から最後に受けた報告
;; (ClusterObservations.retired-statuses — 重なりの名乗りで前の世代の最新の報告を置き、退いた世代の heartbeat ごとに置き直す)の刻から
;; lease の窓(ClusterTiming.lease-ms — 今の世代の生死の窓と同じ)を越えて黙った世代を止まったと判じ、その報告の process の担い手を
;; 外す(sweep-stopped-generations — 調停の拍ごと)。報告を続ける退いた世代は止まったと判じないので、担い手を外さず延長も通す(AL-D2e)。

(defk generation-holder-prefixes [report]
  {:pre [(: report (| WorkerReport None))] :post [(: % tuple)] :tags {:context "coordinator" :role "judgment"}}
  "worker の最新の報告 report の行のうち process の世代の名を持つ物から、その process が名乗った担い手の token の頭を求めるため。
   担い手の job の名は子が名乗った名(入れ替えで退いた process は元の名 retired-from — worker の ReleaseLeases と同じ)。"
  (match report
    None #()
    _ (tuple (gfor row report.jobs
                   :if row.instance
                   (holder-tokens-prefix (lease-holder (or row.retired-from row.name) row.instance))))))


(defk holders-dropped [row prefixes]
  {:pre [(: row BoardRow) (: prefixes tuple)] :post [(: % (| dict None))] :tags {:context "coordinator" :role "judgment"}}
  "盤の lease の行 row の値から、token が prefixes のどれかで始まる担い手を外した後の値を求めるため。外す物が無ければ None。"
  (var current row.value)
  (var changed False)
  (for [prefix prefixes]
    (val updated (drop-holders current prefix))
    (match updated
      None None
      _ (do (:= current updated) (:= changed True))))
  (if changed current None))


(defk board-without-generation-holders [board report]
  {:pre [(: board (get Table BoardRow)) (: report (| WorkerReport None))] :post [(: % tuple)] :tags {:context "coordinator" :role "judgment"}}
  "worker の前の世代の最新の報告 report の process が持っていた担い手を、盤 board の lease の行から外す書き(TableWrite の列)を求めるため(頭の註)。
   変わった行は版を 1 進める(POST /leases の書きと同じ)。外す物が無ければ空の列。"
  (! (board-without-holders board (! (generation-holder-prefixes report)))))


(defk board-without-holders [board prefixes]
  {:pre [(: board (get Table BoardRow)) (: prefixes tuple)] :post [(: % tuple)] :tags {:context "coordinator" :role "judgment"}}
  "token が prefixes(担い手の token の頭の列)のどれかで始まる担い手を、盤 board の lease の行から外す書き(TableWrite の列)を求めるため。
   変わった行は版を 1 進める(POST /leases の書きと同じ)。外す物が無ければ空の列(頭の列が空なら盤を読まない)。"
  (when (not prefixes)
    (return #()))
  (var freed #())
  (for [#(key row) (.items board)]
    (when (.startswith key SEMAPHORE-PREFIX)
      (val value (! (holders-dropped row prefixes)))
      (match value
        None None
        _ (:= freed (+ freed #((TableWrite key (replace row :value value :version (+ row.version 1) :size (value-size value)))))))))
  freed)


(deff retired-silence-deadline [#^ WorkerReport report #^ int window-ms]  ; cannot be a defk: 掃除の期限 sweep-due の内包表記の中から呼ぶ純粋な比べ
  {:pre [(: report WorkerReport) (: window-ms int)] :post [(: % int)] :tags {:context "coordinator" :role "judgment"}}
  "退いた世代を窓 window-ms の内で生きていると数える最後の刻(その世代から最後に受けた報告 report の刻 + 窓・epoch ms)を求めるため。
   止まりの判じ(sweep-stopped-generations)と、その答えが変わる最初の刻を求める sweep-due が同じこの値で比べる(今の世代の生死の
   alive・liveness-deadline と同じ比べ方 — 刻がこれを越えたら止まった)。"
  (+ report.at window-ms))


(defk sweep-stopped-generations [state now timing]
  {:pre [(: state ClusterState) (: now int) (: timing ClusterTiming)] :post [(: % ClusterState)] :tags {:context "coordinator" :role "judgment"}}
  "純粋: 最後に受けた報告から lease の窓(timing.lease-ms)を越えて黙った退いた世代を止まったと判じ(#3770 の節の頭の註)、その報告の
   process が持っていた担い手を盤の lease の行から外し、その世代の報告(ClusterObservations.retired-statuses の行)を忘れた状態を
   求めるため。止まった世代が無ければ同じ object。"
  (val retired state.observations.retired-statuses)
  (val stopped (tuple (gfor #(key report) (.items retired) :if (> now (retired-silence-deadline report timing.lease-ms)) key)))
  (when (not stopped)
    (return state))
  (var prefixes #())
  (for [key stopped]
    (:= prefixes (+ prefixes (! (generation-holder-prefixes (.row retired key))))))
  (val written (board-written state (! (board-without-holders state.board prefixes))))
  (replace written :observations (replace written.observations
                                          :retired-statuses (.with-writes retired (tuple (gfor key stopped (TableWrite key None)))))))


(defk task-peaks-noted [tasks worker measured]
  {:pre [(: tasks (get Table TaskRecord)) (: worker str) (: measured (| MeasuredLoad None))] :post [(: % (get Table TaskRecord))]
   :tags {:context "coordinator" :role "judgment"}}
  "worker の heartbeat の測りが名乗る task ごとの今の使い(measured.task-memory-MiB)で、その worker に置いた task の測った山(peak-mib)を
   書き直すため(measured_room の頭の註の測った山で数える要り): 山が無い行は今の使い、在る行は今の使いが山 + PEAK-STEP-MIB 以上の時だけ。
   測りの無い・task の使いを名乗らない heartbeat は表のまま。"
  (when (or (is measured None) (is measured.task-memory-MiB None))
    (return tasks))
  (setv writes [])
  (for [#(id use) (.items measured.task-memory-MiB)]
    (setv t (.row tasks id))
    (when (and (is-not t None) (= t.worker worker) (in t.phase PLACED-PHASES)
               (or (is t.peak-mib None) (>= use (+ t.peak-mib PEAK-STEP-MIB))))
      (.append writes (TableWrite id (replace t :peak-mib use)))))
  (if writes (.with-writes tasks (tuple writes)) tasks))


(defn #^ dict load-of [#^ ClusterState state #^ (get Table Placement) placements]
  "worker ごとの担っている数(worker の名 → WorkerLoad): jobs = 常駐の job の置き先(placements)と並べた置き先(surge)・tasks = 置かれた
   task(PLACED-PHASES)・settle-mib = 置き先と surge の job の settle の和(memory を知らない旧い保存の行の job は 0 — 宣言し直しで知る)・
   unseen = 置かれた task のうち、worker の測った空きにまだ出ていない物(measured を載せた同じ heartbeat の状態の報告に、動いている行
   task/<id> が無いか、置いた刻 started-ms が measured_room.unseen-since の刻より後 — 走り出したばかりの task の使いは測りに出きって
   いない)・reserved-mib = 置かれた task の memory の予約の和(task ごとに measured_room.reserved-mib-of — 要り〔置いた時に固定した申告 — task_claims.claim-mib-of〕
   − worker の測りが名乗る task の今の使い〔名乗られない task は 0 — 要りの全部〕)・unmeasured-mib = 置かれた task の
   うち worker の測りが今の使いを名乗らない task の要りの和(測った空きにまだ出ていない分 — WorkerLoad の註)。
   置ける空きは memory-room-of・memory-misfit・task-room-of・task-memory-room-of がこの値から求める(数え方を 1 か所にする)。"
  (setv jobs (dfor name (.keys state.workers) name 0) tasks (dfor name (.keys state.workers) name 0)
        unseen (dfor name (.keys state.workers) name 0)
        reserved (dfor name (.keys state.workers) name 0)
        unmeasured (dfor name (.keys state.workers) name 0)
        ;; 測りの名乗る task ごとの今の使い(worker ごと — 名乗らない worker は空)。
        uses (dfor #(name w) (.items state.workers)
                   name (if (or (is w.measured None) (is w.measured.task-memory-MiB None)) {} w.measured.task-memory-MiB))
        settles (dfor name (.keys state.workers) name 0)
        settle-of (dfor job state.jobs job.spec.name (or job.settle-mib 0))
        since (dfor #(name w) (.items state.workers) name (run (unseen-since w.measured)))
        ;; 形ごとの測った山(申告の行の無い置いた task の要りを数える — task_claims.claim-mib-of)。
        peaks (run (shape-peaks-of state.tasks))
        ;; measured と同じ heartbeat の状態の報告で動いていた task の id(worker ごと)。
        live (dfor name (.keys state.workers)
                   :setv report (.row state.observations.statuses name)
                   name (if (is report None)
                            (frozenset)
                            (frozenset (gfor row report.jobs :if (and (.startswith row.name "task/") (in row.phase LIVE-PHASES))
                                             (cut row.name 5 None))))))
  (for [a (+ (list (.rows placements)) (list (.rows state.surges)))]
    (when (in a.worker jobs)
      (+= (get jobs a.worker) 1)
      (+= (get settles a.worker) (.get settle-of a.job 0))))
  (for [t (scan-rows state.tasks)]
    (when (and (in t.phase PLACED-PHASES) (in t.worker tasks))
      (+= (get tasks t.worker) 1)
      (setv need (run (claim-mib-of t state.claims (.row state.workers t.worker) peaks))
            use (.get (get uses t.worker) t.id))
      (+= (get reserved t.worker) (run (reserved-mib-of need use)))
      (when (is use None)
        (+= (get unmeasured t.worker) need))
      (setv cut-off (get since t.worker))
      (when (and (is-not cut-off None)
                 (not (and (in t.id (get live t.worker)) (is-not t.started-ms None) (<= t.started-ms cut-off))))
        (+= (get unseen t.worker) 1))))
  (dfor name (.keys state.workers) name (WorkerLoad :jobs (get jobs name) :tasks (get tasks name) :settle-mib (get settles name)
                                                    :unseen (get unseen name) :reserved-mib (get reserved name)
                                                    :unmeasured-mib (get unmeasured name))))


(defk memory-room-of [worker load]
  {:pre [(: worker WorkerInfo) (: load (get dict #(str WorkerLoad)))] :post [(: % (| int None))] :tags {:context "coordinator" :role "judgment"}}
  "worker の常駐の job の memory の残り(MiB)を知るため: memory の予算(memory-mib)− 置いた job と surge の settle の和。
   memory を名乗らない worker(memory-mib None — 枠の file の外の機体の worker。Mac・node-h-native の worker は file を
   読まない)は memory で縛らない(None)。load = load-of の答え(worker の名 → WorkerLoad)。"
  (val used (get load worker.name))
  (if (is worker.memory-mib None) None (- worker.memory-mib used.settle-mib)))


(defk memory-misfit [worker load job]
  {:pre [(: worker WorkerInfo) (: load (get dict #(str WorkerLoad))) (: job ClusterJob)] :post [(: % (| str None))]
   :tags {:context "coordinator" :role "judgment"}}
  "job(と並べた置き先 surge)を worker に新しく置けない memory の訳を、job を名指す文にするため(置けるなら None)。
   job の側の枠は「置いた job の settle の和 + 新しい job の settle ≤ memory の予算」だけで、数では縛らない。山(peak-mib)は使わない — 山は
   宣言の側の N+1 の照らし(failover_policy の規則 3)が見る。置く時に新しい job の山で照らすと、最後に置く大きい job が落ち着いた値の
   和では入るのに断られる(本番の node-h では和が 67150 MiB になり、予算 63488 MiB を越えた)。memory を知らない job(settle-mib None — 欄の無い旧い保存の行)は新しく置かず、宣言し直しを待つ。
   memory を名乗らない worker(memory-room-of が None)は memory で縛らない。置き先の判断 place-jobs・movable?・drain の move-target は
   どれも can-take を通してこの判断を読む。
   worker が版の準備を待つ間の取り置きを名乗る間(測りの prepare-wait-MiB — worker/core/eviction_rules の頭の註の準備を待つ間の取り置き)は、
   予算に残りが在っても新しく置かない: 予算は静的な値なので、取り置きを予算から引いても node-e の 11-09(予算 1792 − 取り置き 529 MiB)の
   形では常駐の service 2 つ(約 370 MiB)が入り、空いた分を準備より先に取った。置いた job は退かない(準備が走り出すと名乗りが消える)。"
  (<- room (| int None) (memory-room-of worker load))
  (val used (. (get load worker.name) settle-mib))
  (val waiting (if (is worker.measured None) None worker.measured.prepare-wait-MiB))
  (cond
    (is job.settle-mib None)
      (.format "job {} は memory を知らない(settleMiB の無い旧い保存の行)— 宣言し直しを待つ" job.spec.name)
    (is-not waiting None)
      (.format "job {} を worker {} に置かない — worker は版の準備が空きを待つ間の取り置き {} MiB を名乗っている(準備が走り出すまで新しく置かない)"
               job.spec.name worker.name waiting)
    (is room None) None
    (> job.settle-mib room)
      (.format "job {} を worker {} に置くと、置いた job の settle の和 {} MiB + job の settle {} MiB = {} MiB が memory の予算 {} MiB を越える"
               job.spec.name worker.name used job.settle-mib (+ used job.settle-mib) worker.memory-mib)
    True None))


(defk task-room-of [worker load * [urgent False] [lend False]]
  {:pre [(: worker WorkerInfo) (: load dict) (: urgent bool) (: lend bool)] :post [(: % int)] :tags {:context "coordinator" :role "judgment"}}
  "worker に task をあと幾つ置けるかを知るため: 機体の測った空き(worker.measured)から数えた数(measured_room.measured-room — 置いた
   task と、そのうち測りにまだ出ていない task を引く。memory の空き〔task-memory-room-of〕が 0 以下なら 0)。常駐の job の今の使いは
   測った値に入っているので、その分を残して数える。以前は手書きの予約と背景の枠(枠の file の taskReserve・backgroundRoom)から置いた
   task を引いた。load = load-of の答え(worker の名 → WorkerLoad)。task の置き先(place-tasks)と GET /state の taskRoom はこの値を読む。
   urgent = 急ぎの優先の task の数えか(measured_room の頭の註の急ぎの task の空き — GET /state は平常の数え)・lend = 借りの数えか
   (measured_room の頭の註の線の内の貸し)。"
  (val used (get load worker.name))
  (! (measured-room worker.measured used.tasks used.unseen used.reserved-mib :urgent urgent :lend lend)))


(defk task-memory-room-of [worker load * [urgent False] [lend False]]
  {:pre [(: worker WorkerInfo) (: load dict) (: urgent bool) (: lend bool)] :post [(: % int)] :tags {:context "coordinator" :role "judgment"}}
  "worker に新しく置ける task の memory の要りの最大(MiB)を知るため: 測った memory の空き − 床 − 置いた task の予約の和
   (measured_room.memory-room-mib — 2026-11-02)。task の置き先(place-tasks)は要りがこの値以下の worker にだけ置き、GET /state の
   taskMemoryRoomMiB はこの値を出す。load = load-of の答え。urgent = 急ぎの優先の task の数えか(measured_room の頭の註の急ぎの
   task の空き — GET /state は平常の数え)・lend = 借りの数えか(measured_room の頭の註の線の内の貸し)。"
  (! (memory-room-mib worker.measured (. (get load worker.name) reserved-mib) :urgent urgent :lend lend)))


(defk memory-need-refusal [state needs memory-mib]
  {:pre [(: state ClusterState) (: needs tuple) (: memory-mib (| int None))] :post [(: % (| str None))]
   :tags {:context "coordinator" :role "judgment"}}
  "積む時に、待っても置けない大きさの task を断る理由を知るため(None = 受ける — 2026-11-02): memory の要り memory-mib が、能力 needs の
   合うどの worker の線(measured_room.memory-line-mib — 名乗った memory の総量〔kubelet の下の worker は追い出しの線の大きさ〕− 床)
   よりも大きい。測りを名乗る合う worker が 1 台も無い間(coordinator の起動の直後・測りの前の版の worker だけ)と、要りを名乗らない
   task は判じない(待たせる)。今だけ空きの足りない task は断らずに queued で待つ(置き先の task-misfit が TASK-NO-MEMORY を名乗る)。"
  (when (is memory-mib None)
    (return None))
  (var widest None)
  (var names #())
  (for [w (.rows state.workers) :if (and (placeable needs w) (is-not w.measured None))]
    (<- line (| int None) (memory-line-mib w.measured))
    (:= names #(#* names w.name))
    (when (or (is widest None) (> line widest))
      (:= widest line)))
  (if (and (is-not widest None) (> memory-mib widest))
      (.format "memoryMiB {} は、能力 {} の合うどの worker({})の memory の線 − 床の最大 {} MiB よりも大きい — 置ける worker が無い"
               memory-mib (list needs) (.join "・" (sorted names)) widest)
      None))


(defn #^ tuple active-jobs [#^ ClusterState state]
  "置く対象の宣言(replicas 0 の Service は宣言に残るが置かない — 担い手は次の heartbeat で止める)。"
  (tuple (gfor job state.jobs :if (> job.replicas 0) job)))


;; --- 移せる先の無い job と、途絶しても動かし続けてよい印(#2804) ---------------------------------------------------------
;; 本番の Service はどれも置ける worker が 1 台しかなく、担い手の途絶(処理の止まり・網の途絶)で置き先を外しても他へ移らず、止めて
;; 起こし直す損だけが残った(2026-10-02 13:26・13:53)。そこで:
;;   - 他に置ける worker が無い job は、担い手が移し替えの期限(reassign-after-ms)を過ぎて沈黙しても置き先を外さない(place-jobs の 1)。
;;   - その job には返事で「途絶しても動かし続けてよい」印を付け(keep-marked・heartbeat-reply)、印を渡した事実を約束(ClusterState.keep-marks)
;;     として持つ(remember-keep-marks)。worker は印の在る job を fence でも止めない(worker_policy.kept-when-cut-off?)。
;;   - 約束の在る job は担い手から動かさない(place-jobs の 1・2)— 約束の在る間は、2 か所で走らない保証を時間の競争(fence < 移し替え)
;;     ではなく「移さない」で持つ。約束が外れるのは 3 つの時: 担い手の今の世代の heartbeat が印を持たないと知らせた時(released-keep-marks
;;     — 印の無い返事が届いた後なので、その後は fence が効く)・Worker が消された時・担い手の沈黙が約束の期限(ClusterTiming.kept-reassign-after-ms・
;;     280 秒)を越えた時(この 2 つは sweep-keep-marks)。3 つ目は担い手の名が替わる切り替え(旧い名の worker は二度と知らせて来ない —
;;     2026-10-09 22:03 の本番の止まり)のため。期限の後の保証は時間の柵に戻る: 担い手は印の在る job も途絶が長い方の柵(keep-fence-ms)を
;;     越えると自分で止め、約束の期限はその止め切りの最悪より後(条 C4 と同じ形・shared/core/timing_rules)。
;;   - 他に置ける worker が在る job は今までどおり: 印を付けず、担い手が期限を過ぎて沈黙したら外して移す(worker は fence で先に止まる)。
;; 同じ名の worker の新しい世代(Pod の作り直し)の知らせ(印を持たない)では約束を外す(同じ名の置き先を新しい世代へ引き継ぐ今までの
;; 前提と同じ)。届かない node の上の旧い世代は、印の在る job も長い方の柵(ClusterTiming.keep-fence-ms・240 秒)で止めるので、k8s が
;; 作り直した新しい世代(早くても約 350 秒後 — 数の前提は ClusterTiming.keep-fence-ms の註)とは重ならない。
;; node に縛られた担い手(heartbeat の pinnedToNode — 2026-11-10): 同じ名の新しい世代は同じ node にしか置けず、node が届かない間は
;; 起きないので、担い手は印の在る job をさらに長い柵(ClusterTiming.pinned-keep-fence-ms・1 時間)まで動かし続ける — coordinator と
;; API server が同じ機体に在り、その機体が止まった間も job を止めない。約束にその名乗りを残し(KeepMark.pinned)、約束の期限をその柵から
;; 求める(kept-window-ms — 担い手の止め切りより後・条 C4 と同じ形)。


(defk movable? [now state job holder load timing draining]
  {:pre [(: now int) (: state ClusterState) (: job ClusterJob) (: holder str) (: load dict) (: timing ClusterTiming) (: draining frozenset)]
   :post [(: % bool)] :tags {:context "coordinator" :role "judgment"}}
  "job を担い手 holder 以外の worker へ今すぐ移せるか(生きていて・条件を満たし・drain 中でなく・空きの在る worker が他に在るか — can-take)を
   決めるため。移せない job は担い手が沈黙しても置き先を外さず、途絶しても動かし続けてよい印を付ける。load = worker ごとの担っている数。"
  (any (gfor w (.rows state.workers)
             (and (!= w.name holder) (can-take now state job w load timing draining)))))


(defk keep-marked [now state worker timing]
  {:pre [(: now int) (: state ClusterState) (: worker str) (: timing ClusterTiming)] :post [(: % frozenset)]
   :tags {:context "coordinator" :role "judgment"}}
  "worker に置いた job のうち、返事で「途絶しても動かし続けてよい」印を付ける job の名を決めるため: 置き先(並べた置き先 surge は除く)が
   この worker で、入れ替えでなく(入れ替えの書き手は印が無くても途絶で止めない)、この worker が条件を満たし、他の worker へ今すぐ
   移せない job。負荷は他に置ける見込みの worker が在る時だけ求める(本番の Service はどれも置ける worker が 1 台なので求めない)。"
  (val info (.row state.workers worker))
  (val draining (draining-workers state now))
  (val mine (if (is info None)
                #()
                (tuple (gfor job (active-jobs state)
                             :setv placed (.row state.placements job.spec.name)
                             :if (and (is-not placed None) (= placed.worker worker) (not job.spec.handoff) (eligible state job info))
                             job))))
  (val crowded (any (gfor job mine w (.rows state.workers)
                          (and (!= w.name worker) (alive now w timing.lease-ms) (eligible state job w) (not-in w.name draining)))))
  (var marked #())
  (if (not crowded)
      ;; 他に置ける見込みの worker が 1 台も無い — どれも移せない(負荷を求めない)。
      (:= marked (tuple (gfor job mine job.spec.name)))
      (do (val load (load-of state state.placements))
          (for [job mine]
            (<- free bool (movable? now state job worker load timing draining))
            (when (not free)
              (:= marked #(#* marked job.spec.name))))))
  (frozenset marked))


(defk keep-mark-of [marks job]
  {:pre [(: marks tuple) (: job str)] :post [(: % (| KeepMark None))] :tags {:context "coordinator" :role "judgment"}}
  "job の途絶しても動かし続けてよい印の約束(無ければ None)を、約束の列(ClusterState.keep-marks — job ごとに 1 つ)から引くため。"
  (next (gfor mark marks :if (= mark.job job) mark) None))


(defk held-placements [now state timing]
  {:pre [(: now int) (: state ClusterState) (: timing ClusterTiming)] :post [(: % dict)] :tags {:context "coordinator" :role "judgment"}}
  "置き先の判断(place-jobs)の 1 段目 — 続けてよい割り当て(job の名 → Placement)を決めるため: 宣言に在り・replicas 1・担い手が知られて
   いて、次のどれか。
   - 途絶しても動かし続けてよい印の約束(keep-marks)をこの担い手と持つ(条件・生存・drain を問わない — 約束の在る間は、担い手の上で
     process が動いているかもしれない。約束は、担い手が印を持たないと知らせた時に heartbeat の受け口が外し、Worker が消された時と
     担い手の沈黙が約束の期限 kept-reassign-after-ms を越えた時に調停の前の sweep-keep-marks が外す)。
   - 担い手が条件を満たし、drain 中の担い手の上の入れ替えでない job で他へ移せる物でなく(移せるなら止めて移す — 置ける先が無ければ
     残して空白を作らない・入れ替えの job は drain_policy が並べてから付け替える)、担い手が移し替えの期限の内か、他へ移せない
     (外しても移らず、止めて起こし直す損だけが残る — #2804)。
   - 入れ替えの job で、担い手が条件を満たさなくなった(relocating)が、並べた置き先が在るか他へ移せる — 並べた先が Ready になるまで
     旧い担い手に残す(drain_policy が付け替える)。移せる先が無ければ今までどおり外す。
   いまの負荷は「他へ移せるか」の判断だけが読む — drain 中の worker も期限を過ぎて沈黙した担い手も、条件を満たさなくなった入れ替えの
   担い手も無ければ求めない(#2655 — 調停の 1 周ごとの費用)。"
  (val draining (draining-workers state now))
  (val stale (any (gfor a (.rows state.placements)
                        :setv w (.row state.workers a.worker)
                        (and (is-not w None) (not (alive now w timing.reassign-after-ms))))))
  (val moving (frozenset (gfor job (active-jobs state)
                               :setv a (.row state.placements job.spec.name)
                               :if (and (is-not a None) (relocating now state job a timing))
                               job.spec.name)))
  (val load (if (or draining stale moving) (load-of state state.placements) {}))
  (var kept {})
  (for [job (active-jobs state)]
    (val current (.row state.placements job.spec.name))
    (val worker (if (is current None) None (.row state.workers current.worker)))
    (when (is-not worker None)
      (<- mark (| KeepMark None) (keep-mark-of state.keep-marks job.spec.name))
      (val promised (and (is-not mark None) (= mark.worker current.worker)))
      (val fit (eligible state job worker))
      (val leaving (and (in current.worker draining) (not job.spec.handoff)))
      (val awake (alive now worker timing.reassign-after-ms))
      (var free False)
      (when (and (not promised) fit (or leaving (not awake)))
        (<- movable bool (movable? now state job current.worker load timing draining))
        (:= free movable))
      ;; 条件を満たさなくなった入れ替えの担い手: 並べた置き先が在れば残す(並べた先は load で数えるので movable? に聞かない)・
      ;; 無ければ他へ移せる時だけ残す(drain_policy が次に並べる)。
      (var handing-over False)
      (when (and (not promised) (in job.spec.name moving))
        (if (is-not (.row state.surges job.spec.name) None)
            (:= handing-over True)
            (do (<- movable bool (movable? now state job current.worker load timing draining))
                (:= handing-over movable))))
      (when (or promised handing-over (and fit (not (and leaving free)) (or awake (not free))))
        (:= kept (| kept {job.spec.name current})))))
  kept)


(defk released-keep-marks [marks worker held]
  {:pre [(: marks tuple) (: worker str) (: held (| tuple None))] :post [(: % tuple)] :tags {:context "coordinator" :role "judgment"}}
  "worker の今の世代の heartbeat が知らせた「今持っている印」(held — None = 欄の無い古い worker = 印を持たない)から、その worker への約束の
   うち印を持たなくなった job の約束を外すため。worker は最後に届いた返事の印を知らせるので、外す約束の job は印の無い返事が届いた後 —
   その後の途絶は fence が止める(移し替えより先)。外す物が無ければ同じ列を返す。"
  (val holding (frozenset (or held #())))
  (val kept (tuple (gfor mark marks :if (or (!= mark.worker worker) (in mark.job holding)) mark)))
  (if (= (len kept) (len marks)) marks kept))


(defk remember-keep-marks [state worker boot held reply now [pinned False]]
  {:pre [(: state ClusterState) (: worker str) (: boot (| str None)) (: held (| tuple None)) (: reply HeartbeatReply) (: now int)
         (: pinned bool)]
   :post [(: % ClusterState)] :tags {:context "coordinator" :role "judgment"}}
  "heartbeat の返事で印を付けた job を、印を渡した担い手との約束として状態に残すため(返事を送る前に状態と一緒に保存される)。印を知る
   worker(本文に keptWhenCutOff の欄が在る — held が None でない)だけ — 古い worker は印を読まずに fence で止めるので約束しない。
   pinned = その heartbeat で担い手が node に縛られていると名乗った(約束の印に残し、約束の期限を長い柵から求める — kept-window-ms)。
   名乗りが替わった約束(止まり始めた世代は名乗らない)は書き直す — 担い手は名乗りを下ろした時から短い柵で止めるので、短い期限へ
   移るのは担い手より後。同じ担い手への約束は初めて渡した時刻を保つ。約束の列は job の名の順・job ごとに 1 つ。変わらなければ同じ状態を返す。"
  (val marked (frozenset (gfor spec reply.jobs :if spec.keep-when-cut-off spec.name)))
  (val fresh (tuple (gfor name (sorted marked)
                          :setv previous (next (gfor m state.keep-marks :if (= m.job name) m) None)
                          :if (not (and (is-not previous None) (= previous.worker worker) (= previous.boot boot) (= previous.pinned pinned)))
                          (KeepMark :job name :worker worker :boot boot :pinned pinned
                                    :since-ms (if (and (is-not previous None) (= previous.worker worker)) previous.since-ms now)))))
  (val renewed (frozenset (gfor f fresh f.job)))
  (if (or (is held None) (not fresh))
      state
      (replace state :keep-marks (tuple (sorted (+ (tuple (gfor m state.keep-marks :if (not-in m.job renewed) m)) fresh)
                                                :key (fn [m] m.job))))))


(deff kept-window-ms [#^ ClusterTiming timing #^ bool pinned]  ; cannot be a defk: 約束の外し(sweep-keep-marks)と期限の刻(liveness-due)が Program の外の純粋な判断から呼ぶ
  {:pre [(: timing ClusterTiming) (: pinned bool)] :post [(: % int)] :tags {:context "coordinator" :role "judgment"}}
  "印の約束を担い手の沈黙から外すまでの窓を、約束の外しとその刻の求めで同じ値にするため。node に縛られた担い手(pinned)は印の在る job を
   長い柵(pinned-keep-fence-ms)まで動かし続けるので、kept-reassign-after-ms と keep-fence-ms の差(止め切りの最悪の残り + 余白 = 40 秒 —
   条 C4 と同じ形)をその柵に足す。縛られていない担い手は kept-reassign-after-ms。"
  (if pinned
      (+ timing.pinned-keep-fence-ms (- timing.kept-reassign-after-ms timing.keep-fence-ms))
      timing.kept-reassign-after-ms))


(defk sweep-keep-marks [state now timing]
  {:pre [(: state ClusterState) (: now int) (: timing ClusterTiming)] :post [(: % ClusterState)] :tags {:context "coordinator" :role "judgment"}}
  "外してよい約束を外すため(外した約束の job は、置き先の判断が他へ移せる): 消された・忘れた Worker への約束(Worker の削除は
   「その worker はもう動いていない」という明示の宣言)と、担い手の沈黙が約束の期限(ClusterTiming.kept-reassign-after-ms)を越えた約束
   (担い手は印の在る job も途絶が長い方の柵 keep-fence-ms を越えると自分で止め、期限はその止め切りの最悪より後 — 担い手の名が替わる
   切り替えで、旧い名の worker が二度と知らせて来ない時も外れる。node に縛られた担い手は長い柵 pinned-keep-fence-ms から求めた期限 —
   kept-window-ms)。生死の比べは alive で、期限の刻は liveness-due が同じ値で求める。外す物が無ければ同じ状態を返す。"
  (val held (tuple (gfor mark state.keep-marks
                         :setv holder (.row state.workers mark.worker)
                         :if (and (is-not holder None) (alive now holder (kept-window-ms timing mark.pinned)))
                         mark)))
  (if (= (len held) (len state.keep-marks))
      state
      (replace state :keep-marks held)))


(defk jobs-placed [now state timing]
  {:pre [(: now int) (: state ClusterState) (: timing ClusterTiming)] :post [(: % (get Table Placement))]
   :tags {:context "coordinator" :role "judgment"}}
  "純粋: place-jobs の書きを state.placements へ当てた表(書きが空なら state.placements そのもの)— 全件の表で置き先を読む読み手(test)のため。"
  (.with-writes state.placements (! (place-jobs now state timing))))


(defk place-jobs [now state timing]
  {:pre [(: now int) (: state ClusterState) (: timing ClusterTiming)] :post [(: % tuple)] :tags {:context "coordinator" :role "judgment"}}
  "宣言された job の次の割り当て(job の名 → Placement)を決めるため: 続けてよい割り当てを残し、担い手の無い job を空きの多い生きた
   worker へ置き、宣言から消えた job の割り当てを落とす。答えは state.placements へ当てる書きの列(TableWrite の tuple — 変わった
   job だけ・何も変わらなければ空)。全件の写像は組んで返さない。"
  (setv jobs (active-jobs state)
        draining (draining-workers state now))
  ;; 1. 続けてよい割り当てを残す(held-placements — 担い手が移し替えの期限内か、他へ移せないか、途絶しても動かし続けてよい印の約束を
  ;;    持つ。drain 中の担い手の上の入れ替えでない job は、他へ移せる時だけ外す)。
  (<- kept dict (held-placements now state timing))
  ;; 2. 担い手の無い job を、生きている worker のうち空きの多い順へ置く(同点は名前順)。
  ;;    どこかの生きた worker がまだその job を動かしている間は置かない(条件が変わって生きた担い手から外した job は、
  ;;    元の担い手が止め終えたと報告してから置く)。drain 中の worker には置かない。
  ;;    途絶しても動かし続けてよい印の約束の在る job は、約束の担い手にだけ置く(担い手が印を持たないと知らせるまで、担い手の上で
  ;;    古い宣言の process が動いているかもしれない — 宣言から消えて置き先を外した後に宣言し直した job など・#2804)。
  ;;    drain で並べた置き先(surge)を持つ job は、その置き先へ付け替える(そこで動いている process をそのまま使う — 旧い担い手が
  ;;    沈黙して外れた時)。
  ;; 置いた後の負荷は担い手の無い job を置く時だけ読む — 全部の job が割り当てを保っていれば求めない(#2655)。置けるかは memory の残りに
  ;; job の settle が入るか(can-take — memory-misfit。task の空きは使わない)・並びは担っている数(job と task の和)の少ない順。
  ;; 全部の job が割り当てを保っていれば load は読まれない(None)。
  (setv load (if (all (gfor job jobs (in job.spec.name kept))) None (load-of state (table-of (tuple (gfor #(n a) (.items kept) (TableWrite n a)))))))
  ;; 保った割り当ては state.placements の行そのもの(書かない)。新しく置く物だけ fresh に貯める。
  (setv taken (set kept) fresh [])
  (for [job (sorted jobs :key (fn [j] j.spec.name))]
    (when (in job.spec.name taken) (continue))
    (setv surge (.row state.surges job.spec.name)
          surge-worker (if surge (.row state.workers surge.worker) None))
    (when (and (is-not surge-worker None) (alive now surge-worker timing.lease-ms) (eligible state job surge-worker))
      (.append fresh surge)
      (.add taken job.spec.name)
      (continue))
    (when (still-live-somewhere now state job.spec.name timing) (continue))
    (setv mark (! (keep-mark-of state.keep-marks job.spec.name))
          candidates (sorted
      (lfor w (.rows state.workers)
            :if (and (can-take now state job w load timing draining)
                     (or (is mark None) (= w.name mark.worker)))
            w)
      :key (fn [w] #((+ (. (get load w.name) jobs) (. (get load w.name) tasks)) w.name))))
    (when candidates
      (setv chosen (get candidates 0)
            previous (.row state.placements job.spec.name)
            used (get load chosen.name)
            (get load chosen.name) (replace used :jobs (+ used.jobs 1) :settle-mib (+ used.settle-mib job.settle-mib)))
      (.append fresh (Placement job.spec.name chosen.name (if previous (+ previous.generation 1) 1) now))
      (.add taken job.spec.name)))
  ;; 書く = 新しく置いた行(今の行と値も同じなら書かない)と、保たれなかった・宣言から消えた job の行を消す書き(None)。
  ;; 宣言から消えた job の割り当ては残さない(担い手は次の heartbeat で止める)。
  (tuple (sorted (+ (tuple (gfor p fresh :setv old (.row state.placements p.job) :if (not (and (is-not old None) (= old p))) (TableWrite p.job p)))
                    (tuple (gfor name (.keys state.placements) :if (not-in name taken) (TableWrite name None))))
                 :key (fn [w] w.key))))


(defn #^ tuple jobs-for [#^ ClusterState state #^ str worker #^ (| dict None) [ready-instances None]]
  "worker に割り当てた job の spec。割り当ての世代(placement)を載せる — worker は起こす process へ渡し、process は readiness と
   計器の報告に載せる(比べない欄なので、世代だけが変わっても worker は process を起こし直さない)。
   ready-instances = Service の名 → Ready と数えている process の世代の名(入れ替えの job に載せる・resource_policy.ready-instances が求める)。
   drain で並べた置き先(surge)がこの worker に在る job も載せる(新しい process を起こし、standby で lease を待つ)。
   入れ替えを諦めた job(state.handoffs の ABANDONED — handoff_policy)には諦めの印を載せる(worker は新を止めて旧を残す)。
   条件を満たさなくなった担い手に残した入れ替えの job(relocating — 並べた先が Ready になるまで)には版を据え置く印を載せる(worker は
   動いている旧い版をそのまま動かし、新しい版を準備も起動もしない — 新しい版は並べた先で起きる)。"
  (setv info (.row state.workers worker))
  (tuple (gfor job state.jobs
               :setv placed (.row state.placements job.spec.name)
               :setv surge (.row state.surges job.spec.name)
               :setv a (cond (and placed (= placed.worker worker)) placed
                             (and surge (= surge.worker worker) (> job.replicas 0)) surge
                             True None)
               :if a
               :setv watch (.row state.handoffs job.spec.name)
               (replace job.spec :placement a.generation :peak-mib job.peak-mib
                        :ready-instance (.get (or ready-instances {}) job.spec.name)
                        :handoff-abandoned (and (is-not watch None) (= watch.phase HandoffPhase.ABANDONED))
                        :hold-version (and job.spec.handoff (is a placed) (is-not info None) (not (eligible state job info)))))))


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


(defn [(functools.lru-cache :maxsize 4096)] _dependency-key [#^ str declared-text #^ str platform]
  ;; 同じ宣言と platform の依存のキーを拍ごとに計算し直さないための cache(_root-key と同じ形)。
  (run (dependency-key (run (runtime-env-of-json (json.loads declared-text))) platform)))


(defn #^ str dependency-key-on [#^ dict declared #^ WorkerInfo worker]
  "宣言の依存の部分(venv)を worker の上で呼ぶキー — 温める表の行が worker の上で依存まで揃ったかを、worker が heartbeat で名乗る
   準備済みの root の依存のキー(env-ready-deps)と照らすため。"
  (_dependency-key (json.dumps declared :sort-keys True :ensure-ascii False) worker.platform))


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
  (setv seen (lfor w (sorted (.rows state.workers) :key (fn [w] w.name))
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
    ;; 途絶しても動かし続けてよい印の約束の在る job は、約束の担い手が印を持たないと知らせるまで他へ置かない(#2804)。
    (or (still-live-somewhere now state job.spec.name timing) (is-not (run (keep-mark-of state.keep-marks job.spec.name)) None))
      UnplacedKind.WAITING-PREVIOUS-HOLDER
    (not (any (gfor w (.rows state.workers) (and (alive now w timing.lease-ms) (eligible state job w)
                                                    (not-in w.name (draining-workers state now))))))
      UnplacedKind.NO-ELIGIBLE-WORKER
    True UnplacedKind.NO-ROOM))


(defk memory-misfits [now state job timing]
  {:pre [(: now int) (: state ClusterState) (: job ClusterJob) (: timing ClusterTiming)] :post [(: % (get tuple #(str ...)))]
   :tags {:context "coordinator" :role "judgment"}}
  "担い手の無い job を、生きていて条件を満たし drain 中でない worker のそれぞれに memory で置けない訳(memory-misfit の文 — worker の名の
   順)— 置けない訳(unplaced-text の NO-ROOM・UnplacedView.memory)が、どの worker の予算をどれだけ越えるかを名指すため。"
  (val draining (draining-workers state now))
  (val load (load-of state state.placements))
  (var texts #())
  (for [w (sorted (.rows state.workers) :key (fn [w] w.name))]
    (when (and (alive now w timing.lease-ms) (eligible state job w) (not-in w.name draining))
      (<- text (| str None) (memory-misfit w load job))
      (when (is-not text None) (:= texts #(#* texts text)))))
  texts)


(defn #^ str unplaced-text [#^ UnplacedKind kind #^ ClusterJob job #^ tuple needs #^ tuple memory]
  "置き先が無い理由の種類 → 人が読む文(状態の表示 GET /state の unplaced と Service の readyReason の綴り)。needs = 効く needs
   (effective-needs — 上書きの間は上書きの needs)・memory = worker ごとの memory で置けない訳(memory-misfits — NO-ROOM の文に足す)。"
  (match kind
    UnplacedKind.WAITING-PREVIOUS-HOLDER "前の担い手が止め終えるのを待っている"
    UnplacedKind.NO-ELIGIBLE-WORKER
      (.format "置ける worker が無い(要る能力 {}・固定 {}。専用の能力を持つ worker には、そのどれかを要る job だけを置く。drain 中の worker には置かない)"
               (list needs) job.pin)
    UnplacedKind.NO-ROOM (if memory (.format "置ける worker に空きが無い({})" (.join "・" memory)) "置ける worker に空きが無い")))


(defk unplaced-memory [now state job kind timing]
  {:pre [(: now int) (: state ClusterState) (: job ClusterJob) (: kind UnplacedKind) (: timing ClusterTiming)] :post [(: % (get tuple #(str ...)))]
   :tags {:context "coordinator" :role "judgment"}}
  "置き先の無い job の memory で置けない訳(NO-ROOM の時だけ memory-misfits — 他の種類では空)— unplaced-text と UnplacedView が同じ訳を読むため。"
  (var texts #())
  (when (= kind UnplacedKind.NO-ROOM)
    (<- found (get tuple #(str ...)) (memory-misfits now state job timing))
    (:= texts found))
  texts)


(defn #^ dict unplaced-jobs [#^ int now #^ ClusterState state #^ ClusterTiming timing]
  "担い手の無い job と、その理由(状態の表示用)。"
  (dfor job (active-jobs state)
        :if (is (.row state.placements job.spec.name) None)
        :setv kind (unplaced-kind now state job timing)
        job.spec.name
        (unplaced-text kind job (effective-needs state job) (run (unplaced-memory now state job kind timing)))))


(defk note-unplaced [state now]
  {:pre [(: state ClusterState) (: now int)] :post [(: % ClusterState)] :tags {:context "coordinator" :role "judgment"}}
  "置き先が無いと最初に見た拍の刻(ClusterState.unplaced-since)を、この拍の置き先で求め直すため。置き先の無い
   Service(unplaced-jobs と同じ母集団)は前の刻を保ち、初めて見た Service には now を置く。置かれた・宣言から消えた Service の行は外す。
   変わらなければ同じ値を返す。"
  (val names (sorted (gfor job (active-jobs state) :if (is (.row state.placements job.spec.name) None) job.spec.name)))
  (val since (table-of (tuple (gfor name names
                                    (TableWrite name (let [seen (.row state.unplaced-since name)] (if (is seen None) now seen)))))))
  (if (= since state.unplaced-since) state (replace state :unplaced-since since)))


(defn #^ tuple missing-needs [#^ int now #^ ClusterState state #^ tuple needs #^ ClusterTiming timing]
  "needs のうち、生きていて drain 中でない worker のどれも名乗らない能力(名の順)— 置き先の無い Service の「足りない能力」の読み。
   能力は名乗り(provides)と node の label から導いた能力(derived)の和で数える(placeable と同じ)。"
  (setv draining (draining-workers state now))
  (setv offered (set (gfor w (.rows state.workers) :if (and (alive now w timing.lease-ms) (not-in w.name draining))
                          c (+ (tuple w.provides) (tuple w.derived)) c)))
  (tuple (sorted (- (set needs) offered))))


(defn #^ UnplacedView unplaced-view [#^ int now #^ ClusterState state #^ ClusterJob job #^ ClusterTiming timing]
  "置き先の無い Service 1 つの読み(UnplacedView)— 外の見張りが理由の文を読み解かずに、種類・足りない能力・いつからを読むため。
  GET /state の unplacedDetail(unplaced-views)と Service の資源の status.unplaced(resource_policy.observed-of)が
   同じこの関数を読む。刻は ClusterState.unplaced-since(まだ調停の拍が見ていなければ now)。"
  (setv needs (tuple (effective-needs state job))
        since (.row state.unplaced-since job.spec.name)
        kind (unplaced-kind now state job timing))
  (UnplacedView :service job.spec.name :kind kind :needs needs
                :missing (missing-needs now state needs timing) :pin job.pin
                :since-ms (if (is since None) now since)
                :memory (run (unplaced-memory now state job kind timing))))


(defn #^ tuple unplaced-views [#^ int now #^ ClusterState state #^ ClusterTiming timing]
  "置き先の無い Service の読みの列(Service の名の順 — UnplacedView)— GET /state の unplacedDetail を綴るため。母集団は unplaced-jobs と同じ。"
  (tuple (gfor job (sorted (active-jobs state) :key (fn [j] j.spec.name))
               :if (is (.row state.placements job.spec.name) None)
               (unplaced-view now state job timing))))


(defk task-unplaced-text [kind]
  {:pre [(: kind TaskUnplacedKind)] :post [(: % str)] :tags {:context "coordinator" :role "judgment"}}
  "待っている task を置けない理由の種類 → task の detail に書く文を決めるため(place-tasks が書く・GET /state の task の detail に出る)。
   文は種類ごとに固定 — 待つ間に調停が拍ごとに書き換えて保存の版を進めない。"
  (match kind
    TaskUnplacedKind.TASK-NO-ROOM
      "能力と版の合う worker は在るが、どの機体の測った空き(CPU・memory・PSI)にも task を置く余りが無い — 空くまで待つ"
    TaskUnplacedKind.TASK-NO-MEMORY
      "能力と版の合う worker は在るが、どの機体の memory の空き(測った空き − 置いた task のまだ伸びる分)も task の memory の要りに足りない — 空くまで待つ"
    TaskUnplacedKind.TASK-LIMITED
      "task の名乗った組(limits)の同時数が系の宣言の上限に達している — 同じ組の task が終わるまで待つ"
    TaskUnplacedKind.TASK-RESERVED
      "負の優先の task(背景の仕事)は worker の予約のうち優先 0 以上の task に残す半分を使わない — 予約を越える空きができるまで待つ"
    TaskUnplacedKind.TASK-BACKGROUND-RESERVED
      "優先 0 以上の task は、背景の仕事が待っている間は worker の予約のうち背景の仕事に残す半分を使わない — 空きができるまで待つ"
    TaskUnplacedKind.TASK-UNDECLARED
      "task の名乗った組(limits)が系の宣言(資源 GroupLimit)に無い — 組の上限が宣言されるまで待つ"
    TaskUnplacedKind.TASK-UNMEASURED
      "能力と版の合う worker は在るが、どれも機体の測った空き(heartbeat の measured)を名乗っていない — 測った空きを名乗るまで待つ"))


(defk limit-slot [limit declared worker]
  {:pre [(: limit TaskLimit) (: declared GroupLimit) (: worker (| str None))] :post [(: % tuple)] :tags {:context "coordinator" :role "judgment"}}
  "task の名乗った組 1 つを数える升の鍵を知るため(置いている数を升ごとに数え、置く前に比べる): #(組 区分 worker)。区分は宣言が区分ごとに
   数える組(per-part)の時だけ task の part・worker は宣言の範囲が worker の時だけ置いた worker(cluster の範囲は None)。範囲と区分の
   読みは task の名乗りでなく宣言 declared が決める(以前は task の名乗った範囲で数えた)。"
  #(limit.group (if declared.per-part limit.part "") (if (= declared.scope "worker") worker None)))


(defk placed-claims [tasks]
  {:pre [(: tasks (get Table TaskRecord))] :post [(: % tuple)] :tags {:context "coordinator" :role "judgment"}}
  "置いている(PLACED-PHASES の)task の組の名乗りと置いた worker の組 #(limits worker) を、task 1 本につき 1 つ並べるため(組の上限の
   数えが task の行から読むのはここだけ — 送り手の他の欄を数えに入れさせない。設計のレビューの指摘 2-a・引数と答えは
   test_group_limits_owner が固定する)。"
  (tuple (gfor t (scan-rows tasks) :if (in t.phase PLACED-PHASES) #(t.limits t.worker))))


(defk limit-holdings [claims groups]
  {:pre [(: claims tuple) (: groups Table)] :post [(: % dict)] :tags {:context "coordinator" :role "judgment"}}
  "置いている task の名乗り claims(placed-claims の答え — #(limits worker) の tuple)から組の升ごとの数(limit-slot → 数)を知るため —
   place-tasks はこの数から始め、置くたびに足す(hold-limits)。groups = 組の名で引く、系が宣言した GroupLimit の表(宣言に無い組の
   名乗りは数えない)。"
  (var held {})
  (for [#(limits worker) claims]
    (<- more dict (hold-limits held limits worker groups))
    (:= held more))
  held)


(defk hold-limits [held limits worker groups]
  {:pre [(: held dict) (: limits tuple) (: worker (| str None)) (: groups Table)] :post [(: % dict)]
   :tags {:context "coordinator" :role "judgment"}}
  "task 1 本(名乗った組 limits — TaskLimit の tuple)を worker に置いた後の組の升ごとの数を知るため(held を変えずに新しい dict を返す —
   宣言に無い組の名乗りは数えない)。1 本は升ごとに 1 と数える。task の行を受けないのは、送り手の名乗る他の欄(重さなど)を数えに入れさせない
   ため(値の持ち主は系の宣言だけ。引数は test_group_limits_owner が固定する)。"
  (var after (dict held))
  (for [l limits :setv declared (.row groups l.group) :if (is-not declared None)]
    (<- slot tuple (limit-slot l declared worker))
    (:= after (| after {slot (+ (.get after slot 0) 1)})))
  after)


(defk foreground-reserve-of [worker]
  {:pre [(: worker WorkerInfo)] :post [(: % int)] :tags {:context "coordinator" :role "judgment"}}
  "worker の予約(measured_room.reserve-of — 機体の CPU の数から導く。以前は手書きの task-reserve)のうち、負の優先の task(背景の仕事)に
   使わせず優先 0 以上の task に残す数を知るため: 予約の半分(切り上げ)。
   背景の仕事は残りの半分(切り捨て)までは予約を使える(2026-10-14)。以前は予約を 1 つも使わせず、背景の仕事の
   置き場は job の側の残り(capacity − task-reserve − 常駐の job)だけだった — 常駐の job は capacity − task-reserve まで置ける(C17)ので、
   job が増えて job の側を埋めると背景の仕事は 1 本も置かれず、本番の node-h(capacity 33・task-reserve 6・常駐の job 27)で会話の
   long-command 8 本が 76〜230 分 queued のまま待った。予約の中に背景の仕事の分を持てば、job の数によらず背景の仕事が走り、
   優先 0 以上の task(配備の組み立て・日次の検)には予約の半分が必ず残る(10-11 の、背景の仕事が枠を埋めて組み立てが待った件の守り)。"
  (<- reserve int (reserve-of worker.measured))
  (- reserve (// reserve 2)))


(defk background-reserve-of [worker]
  {:pre [(: worker WorkerInfo)] :post [(: % int)] :tags {:context "coordinator" :role "judgment"}}
  "worker の予約(measured_room.reserve-of)のうち、待っている負の優先の task(背景の仕事)が在る間は優先 0 以上の task に使わせず背景の
   仕事に残す数を知るため:
   予約の残りの半分(切り捨て — foreground-reserve-of と足して予約の全部)。以前は優先 0 以上の側の半分だけを守り、背景の仕事の側の半分は
   守らなかった — 本番の node-h(capacity 40・task-reserve 6・常駐の job 33 → task の空き 7)で優先 0 の組 4 つ(日次の検・着地の後の検 2 組・
   着地の宣言し直し)が 4 本を持つと、残る空き 3 は予約の半分 3 を越えず、会話の long-command は 1 本も置かれなかった(2026-10-29 17:09〜
   21:11 JST に最大 27 本・3〜4 時間 queued)。背景の仕事は置いている数がこの数に満たない間は
   予約の残りを使ってよく、優先 0 以上の task は、その worker に置けうる待っている背景の仕事が在る間は、この数に満たない分を残す。"
  (<- reserve int (reserve-of worker.measured))
  (// reserve 2))


(defk task-misfit [limits priority worker room held groups bg-held bg-wanted need memory-room]
  {:pre [(: limits tuple) (: priority int) (: worker WorkerInfo) (: room int) (: held dict) (: groups Table) (: bg-held int)
         (: bg-wanted int) (: need int) (: memory-room int)]
   :post [(: % (| TaskUnplacedKind None))]
   :tags {:context "coordinator" :role "judgment"}}
  "能力と版の合う worker 1 台に、待っている task(名乗った組 limits・優先 priority — task の行は受けない)を今置けない理由を知るため
   (None = 置ける)。room = その worker の測った空き(task-room-of)・held = 置いている task の組の升ごとの数(limit-holdings)・
   groups = 系が宣言した GroupLimit の表・bg-held = その worker に置いている負の優先の task の数・bg-wanted = その worker に今置けうる
   待っている負の優先の task の数・need = task の memory の要り(MiB — measured_room.need-of)・memory-room = その worker の memory の
   空き(task-memory-room-of)。名乗った組の
   どれかが宣言に無い → TASK-UNDECLARED・worker が測った空きを名乗らない(measured が無いか CPU の使いをまだ数えられない)→
   TASK-UNMEASURED・空きが無い → TASK-NO-ROOM・要りが memory の空きを越える → TASK-NO-MEMORY(2026-11-02)・
   名乗った組のどれかの升が宣言の値に達している → TASK-LIMITED・
   負の優先の task で、空きが予約のうち優先 0 以上の task に残す数(foreground-reserve-of)以下で、置いている背景の仕事が背景の仕事に残す数
   (background-reserve-of)に達している → TASK-RESERVED(2026-10-11・残す数は 2026-10-14 に予約の全部から半分へ・背景の仕事の側の半分は
   2026-10-29)・優先 0 以上の task で、空きが背景の仕事に残す数のうち置いている数に満たない分(待っている背景の仕事の数まで)以下 →
   TASK-BACKGROUND-RESERVED。比べる値は宣言の値で、task の名乗りに値は無い。"
  (<- by-limits (| TaskUnplacedKind None) (limits-misfit limits worker.name held groups))
  (<- kept int (foreground-reserve-of worker))
  (<- bg-floor int (background-reserve-of worker))
  (val bg-kept (max 0 (min (- bg-floor bg-held) bg-wanted)))
  (cond
    (= by-limits TaskUnplacedKind.TASK-UNDECLARED) by-limits
    (or (is worker.measured None) (is worker.measured.cpu-busy None)) TaskUnplacedKind.TASK-UNMEASURED
    (<= room 0) TaskUnplacedKind.TASK-NO-ROOM
    (> need memory-room) TaskUnplacedKind.TASK-NO-MEMORY
    by-limits by-limits
    (and (< priority 0) (<= room kept) (>= bg-held bg-floor)) TaskUnplacedKind.TASK-RESERVED
    (and (>= priority 0) (<= room bg-kept)) TaskUnplacedKind.TASK-BACKGROUND-RESERVED
    True None))


(defk starved-keeper [task workers needs rooms kept held groups]
  {:pre [(: task TaskRecord) (: workers list) (: needs dict) (: rooms dict) (: kept dict) (: held dict) (: groups Table)]
   :post [(: % (| WorkerInfo None))] :tags {:context "coordinator" :role "judgment"}}
  "長く待った task のために空きを取っておく worker を選ぶため(measured_room の頭の註の長く待った task の取っておき — None = 取っておかない):
   workers = task を置ける worker(生きて drain 中でも disk 尽きでもない)・needs = worker の名 → task の要り(task-need-of)・rooms = worker の
   名 → memory の空き(task-memory-room-of — この拍に置いた task と先の取っておきを引いた値)・kept = worker の名 → この拍に先の task へ
   取っておいた MiB・held と groups = 名乗った組の升の数と宣言(limits-misfit)。選ぶのは、組の上限に達しておらず、要りが memory の空きを
   越え(memory の空きだけで置けない)、今の task が全部終われば入る見込み(drained-room-mib − 先の取っておき)以内の worker のうち、memory の
   空きの最も大きい物(同じなら名の順)。"
  (var best None)
  (for [w (sorted workers :key (fn [w] w.name))]
    (val need (get needs w.name))
    (val room (get rooms w.name))
    (<- by-limits (| TaskUnplacedKind None) (limits-misfit task.limits w.name held groups))
    (<- drained int (drained-room-mib w.measured))
    (when (and (is by-limits None) (> need room) (<= need (- drained (.get kept w.name 0)))
               (or (is best None) (> room (get rooms best.name))))
      (:= best w)))
  best)


(defk yield-candidates [task worker tasks peaks claims taken]
  {:pre [(: task TaskRecord) (: worker WorkerInfo) (: tasks tuple) (: peaks dict) (: claims (get Table TaskClaim)) (: taken frozenset)]
   :post [(: % tuple)] :tags {:context "coordinator" :role "judgment"}}
  "急ぎの task に場所を譲らせうる、worker に置いた task を、止める順に知るため(measured_room の頭の註の場所の譲り): tasks = task の
   行・peaks と claims = 形ごとの測った山(shape-peaks-of)と置いた task の申告の表(要りの数え task_claims.claim-mib-of)・taken = この拍に
   既に止めると決めた task の id。候補は、
   その worker に置かれ(PLACED-PHASES)、場所を譲る名乗り(preemptible — 走らせ直しの名乗りと組)を持ち、task より優先の低い切り離した
   task。答え = YieldCandidate の tuple — 戻る MiB = 止めると戻る memory(測りの名乗る今の使い + まだ予約している伸びる分 = load-of と
   同じ数えの max(要り, 今の使い))。順は優先の低い順、同じ優先は戻る MiB の大きい順(止める本数を少なくする)、同じなら id の順。"
  (val uses (if (or (is worker.measured None) (is worker.measured.task-memory-MiB None)) {} worker.measured.task-memory-MiB))
  (var rows #())
  (for [t tasks :if (and t.detached t.preemptible t.rerun (in t.phase PLACED-PHASES) (= t.worker worker.name)
                         (< t.priority task.priority) (not-in t.id taken))]
    (val use (.get uses t.id))
    (<- need int (claim-mib-of t claims worker peaks))
    (<- reserved int (reserved-mib-of need use))
    (:= rows #(#* rows (YieldCandidate :task t :reserved-mib reserved :freed-mib (+ reserved (or use 0)) :measured (is-not use None)))))
  (tuple (sorted rows :key (fn [c] #(c.task.priority (- c.freed-mib) c.task.id)))))


(defk yield-victims [need room candidates]
  {:pre [(: need int) (: room int) (: candidates tuple)] :post [(: % (| tuple None))] :tags {:context "coordinator" :role "judgment"}}
  "急ぎの task の要り need を、memory の空き room の worker に入れるために止める task を知るため(measured_room の頭の註の場所の譲り):
   candidates = yield-candidates の答え(止める順)。頭から、戻る MiB の和を空きに足して要りに届くまで取った YieldCandidate の tuple。
   止めなくても入る(要り ≤ 空き)・全部止めても届かない時は None(譲らせない — 届かない止めで仕事を失わせない)。"
  (when (<= need room)
    (return None))
  (var picked #())
  (var freed 0)
  (for [c candidates]
    (when (>= (+ room freed) need)
      (break))
    (:= picked #(#* picked c))
    (:= freed (+ freed c.freed-mib)))
  (if (>= (+ room freed) need) picked None))


(defk yield-plan [task workers needs rooms counts tasks peaks claims taken held groups bg-held bg-wanted]
  {:pre [(: task TaskRecord) (: workers list) (: needs dict) (: rooms dict) (: counts dict) (: tasks tuple) (: peaks dict)
         (: claims (get Table TaskClaim)) (: taken frozenset)
         (: held dict) (: groups Table) (: bg-held dict) (: bg-wanted dict)]
   :post [(: % (| YieldPlan None))] :tags {:context "coordinator" :role "judgment"}}
  "急ぎの task が memory の空きに入らない拍に、どの worker でどの task に場所を譲らせて置くかを知るため(measured_room の頭の註の場所の
   譲り — None = 譲らせない): workers = 置き先の候補(門の内の task は置ける worker 全部・門の外の task は組の上限に達していない worker)・
   needs / rooms = worker の名 → task の要りと、その task を数える memory の空き(門の内は task-memory-room-of・門の外は測りにまだ出て
   いない要りだけを引いた空き)・counts = worker の名 → 測った数の空き(task-room-of — 門の外の task は読まない)・tasks・peaks・claims・taken =
   yield-candidates の引数・held・groups・bg-held・bg-wanted = task-misfit の引数(worker の名 → 数)。worker ごとに止める task を
   yield-victims で選び、門の内の task は止めた後の数と memory の空きで task-misfit を問い直す(memory と数の外の訳 — 組の上限・予約・
   測りの無さ — で置けない worker では譲らせない)。選ぶのは止める本数の少ない worker、同じなら戻る MiB の和の小さい物、同じなら名の順。"
  (var best None)
  (var best-key None)
  (for [w (sorted workers :key (fn [w] w.name))]
    (val need (get needs w.name))
    (val room (get rooms w.name))
    (<- candidates tuple (yield-candidates task w tasks peaks claims taken))
    (<- victims (| tuple None) (yield-victims need room candidates))
    (when (is-not victims None)
      (val freed (sum (gfor v victims v.freed-mib)))
      (val background (len (lfor v victims :if (< v.task.priority 0) v)))
      ;; 門の外の task は組の上限だけで判じる(workers は組の上限に達していない worker — 問い直さない)。
      (var misfit None)
      (when (not task.ungated)
        (<- found (| TaskUnplacedKind None)
            (task-misfit task.limits task.priority w (+ (get counts w.name) (len victims)) held groups
                         (- (get bg-held w.name) background) (get bg-wanted w.name) need (+ room freed)))
        (:= misfit found))
      (val key #((len victims) freed w.name))
      (when (and (is misfit None) (or (is best-key None) (< key best-key)))
        (:= best (YieldPlan :worker w :victims victims))
        (:= best-key key))))
  best)


(defk preempted-detached [task now by worker]
  {:pre [(: task TaskRecord) (: now int) (: by TaskRecord) (: worker str)] :post [(: % TaskRecord)]
   :tags {:context "coordinator" :role "judgment"}}
  "急ぎの task by に場所を譲るため止める切り離した task の次の行(measured_room の頭の註の場所の譲り — 走らせ直しの道の次の試行と
   同じ形・id が替わり、呼び手は前の行を消す)。担い手は消えていないので、消失の上限に数えない(losses を足さない)。detail と rerun-note に
   止めた訳を、preempted-by に譲った相手の id を残す。"
  (val note (.format "優先 {} の task {} に場所を譲るため worker {} で止めた(優先 {}・場所を譲る名乗り)— 試行 {} を最初から待つ(上限に数えた消失 {}/{} 回)"
                     by.priority by.id worker task.priority (+ task.attempt 1) task.losses RERUN-LIMIT))
  (replace task :id (rerun-id task) :attempt (+ task.attempt 1) :phase "queued" :worker None :boot None :started-ms None
           :placed-memory-available-mib None :placed-measured-at-ms None
           :submitted-ms now :lease-until-ms (+ now task.lease-ms) :reported False :queued-noticed-ms None
           :avoid #() :env-attempts 0 :failure-kind "" :retryable False :retiring ""
           :rerun-note note :detail note :preempted-by by.id :borrowed False :reclaimed False))


(defk reclaim-victims [worker rows]
  {:pre [(: worker WorkerInfo) (: rows tuple)] :post [(: % tuple)] :tags {:context "coordinator" :role "judgment"}}
  "上に載る系が memory の不足で仕事を待たせている worker で、止めて待ちの行へ戻す借りの task を知るため(measured_room の頭の註の線の内の
   貸し): rows = 今の task の行。worker が取り返す量(measured の reclaim-MiB)を名乗る時だけ、その worker に置いた(PLACED-PHASES)
   借りの task(borrowed — 場所を譲る名乗りと走らせ直しの名乗りを持つ切り離した task)を、優先の低い順・測りの名乗る今の使いの大きい順
   (同じなら id の順)に、今の使いの和が取り返す量に届くまで並べる。測りに出ていて行が置いた task でない使い(止めた後で worker がまだ
   止めていない task)は既に戻る memory として先に数える(拍ごとに止め足さない)。名乗らない・届いている時は空。"
  (val measured worker.measured)
  (when (or (is measured None) (is measured.reclaim-MiB None))
    (return #()))
  (val uses (or measured.task-memory-MiB {}))
  (val placed (frozenset (gfor t rows :if (and (in t.phase PLACED-PHASES) (= t.worker worker.name)) t.id)))
  (val freeing (sum (gfor #(id use) (.items uses) :if (not-in id placed) use)))
  (val ordered (sorted (gfor t rows :if (and t.detached t.borrowed t.preemptible t.rerun (in t.phase PLACED-PHASES)
                                             (= t.worker worker.name))
                             t)
                       :key (fn [t] #(t.priority (- (.get uses t.id 0)) t.id))))
  (var picked #())
  (var freed freeing)
  (for [t ordered]
    (when (>= freed measured.reclaim-MiB)
      (break))
    (:= picked #(#* picked t))
    (:= freed (+ freed (.get uses t.id 0))))
  picked)


(defk reclaimed-detached [task now worker]
  {:pre [(: task TaskRecord) (: now int) (: worker str)] :post [(: % TaskRecord)] :tags {:context "coordinator" :role "judgment"}}
  "上に載る系が memory を待つので取り返す借りの task の次の行(measured_room の頭の註の線の内の貸し — 場所の譲りの preempted-detached と
   同じ形・id が替わり、呼び手は前の行を消す)。担い手は消えていないので、消失の上限に数えない(losses を足さない)。detail と rerun-note に
   取り返した訳を、reclaimed に印を残す。"
  (val note (.format "上に載る系が memory の不足で仕事を待たせているので、worker {} の線の内に借りて置いた試行を止めた(優先 {}・場所を譲る名乗り)— 試行 {} を最初から待つ(上限に数えた消失 {}/{} 回)"
                     worker task.priority (+ task.attempt 1) task.losses RERUN-LIMIT))
  (replace task :id (rerun-id task) :attempt (+ task.attempt 1) :phase "queued" :worker None :boot None :started-ms None
           :placed-memory-available-mib None :placed-measured-at-ms None
           :submitted-ms now :lease-until-ms (+ now task.lease-ms) :reported False :queued-noticed-ms None
           :avoid #() :env-attempts 0 :failure-kind "" :retryable False :retiring ""
           :rerun-note note :detail note :preempted-by "" :borrowed False :reclaimed True))


(defk kept-text [text worker]
  {:pre [(: text str) (: worker str)] :post [(: % str)] :tags {:context "coordinator" :role "judgment"}}
  "取っておきを掛けて待つ task の detail の文を決めるため: 待つ訳の文 text に、取っておく worker を名指す句を足す(worker が替わらない
   間は同じ文 — 調停が拍ごとに書き換えて保存の版を進めない)。"
  (.format "{} — 長く待ったので worker {} の memory の空きをこの task に取っておく(後ろの task はその空きを使わない)" text worker))


(defk limits-misfit [limits worker held groups]
  {:pre [(: limits tuple) (: worker str) (: held dict) (: groups Table)] :post [(: % (| TaskUnplacedKind None))]
   :tags {:context "coordinator" :role "judgment"}}
  "task の名乗った組 limits(TaskLimit の tuple)が、worker に置くのを止めるかを知るため(None = 止めない): どれかが宣言に無い →
   TASK-UNDECLARED・どれかの升の置いている数(held)が宣言の値に達している → TASK-LIMITED。比べるのは宣言の値と置いている本数だけで、
   task の行を受けない(送り手の名乗る他の欄を数えに入れさせない — 盲検 B。引数は test_group_limits_owner が固定する)。"
  (var undeclared False)
  (var full False)
  (for [l limits]
    (val declared (.row groups l.group))
    (if (is declared None)
        (:= undeclared True)
        (do (<- slot tuple (limit-slot l declared worker))
            (when (>= (.get held slot 0) declared.limit) (:= full True)))))
  (cond
    undeclared TaskUnplacedKind.TASK-UNDECLARED
    full TaskUnplacedKind.TASK-LIMITED
    True None))


(defk misfit-reason [kinds]
  {:pre [(: kinds list)] :post [(: % TaskUnplacedKind)] :tags {:context "coordinator" :role "judgment"}}
  "置ける worker のどれにも置けない task の待ちの理由を 1 つに決めるため(kinds = worker ごとの task-misfit の答え・空でない)。
   宣言に無い組 → 上限 → 予約 → memory の空き → 数の空き → 測りの無さの順に、待てば晴れる理由の具体な物を名乗る(測った空きの無い worker と測りを名乗らない
   worker が並ぶ時は空きの無さを名乗る — 測りを名乗る worker が在る)。"
  (cond
    (in TaskUnplacedKind.TASK-UNDECLARED kinds) TaskUnplacedKind.TASK-UNDECLARED
    (in TaskUnplacedKind.TASK-LIMITED kinds) TaskUnplacedKind.TASK-LIMITED
    (in TaskUnplacedKind.TASK-RESERVED kinds) TaskUnplacedKind.TASK-RESERVED
    (in TaskUnplacedKind.TASK-BACKGROUND-RESERVED kinds) TaskUnplacedKind.TASK-BACKGROUND-RESERVED
    (in TaskUnplacedKind.TASK-NO-MEMORY kinds) TaskUnplacedKind.TASK-NO-MEMORY
    (in TaskUnplacedKind.TASK-NO-ROOM kinds) TaskUnplacedKind.TASK-NO-ROOM
    (in TaskUnplacedKind.TASK-UNMEASURED kinds) TaskUnplacedKind.TASK-UNMEASURED
    True TaskUnplacedKind.TASK-NO-ROOM))


(defk limits-undeclared-refusal [groups value]
  {:pre [(: groups Table) (: value (| list None))] :post [(: % (| str None))] :tags {:context "coordinator" :role "judgment"}}
  "受け口が、task の本文の欄 limits(形を task-limits-refusal で検めた値)を系の宣言と照らして断る理由を知るため(None = 受ける):
   宣言に無い組・区分ごとに数える組(per-part)で区分を名乗らない・区分ごとに数えない組で区分を名乗る。groups = 系が宣言した
   GroupLimit の表(送り手は値を送らず、coordinator は宣言の値で数える)。"
  (next (gfor l (task-limits-of value)
              :setv declared (.row groups l.group)
              :setv problem (cond
                              (is declared None)
                                (.format "limits の組 {} は系の宣言に無い — 系の :task-limits に組の上限を書いて宣言してから送る(宣言の在る組: {})"
                                         l.group (sorted (.keys groups)))
                              (and declared.per-part (not l.part))
                                (.format "limits の組 {} は区分ごとに数える組(perPart)— part を名乗る" l.group)
                              (and (not declared.per-part) l.part)
                                (.format "limits の組 {} は区分ごとに数えない組 — part {!r} を外す(区分で上限を分けない)" l.group l.part)
                              True None)
              :if problem
              problem)
        None))


(defk group-limit-views [state]
  {:pre [(: state ClusterState)] :post [(: % tuple)] :tags {:context "coordinator" :role "judgment"}}
  "GET /state の組の画面の列(組の名の順 — GroupLimitView)を作るため: 系が宣言した値と、置いている数の升(置き方 place-tasks と同じ数え
   limit-holdings)と、その組を名乗って待っている task の数。運用者が、値が宣言のとおりに効いているかを 1 か所で読む。"
  (<- claims tuple (placed-claims state.tasks))
  (<- held dict (limit-holdings claims state.group-limits))
  (val queued (lfor t (scan-rows state.tasks) :if (= t.phase "queued") t))
  (tuple (gfor #(name declared) (sorted (.items state.group-limits) :key (fn [item] (get item 0)))
               (GroupLimitView :limit declared
                               :resource-version (resource-version-of state (+ "GroupLimit/" name))
                               :held (tuple (gfor #(slot count) (sorted (.items held) :key (fn [item] #((or (get (get item 0) 2) "") (get (get item 0) 1))))
                                                  :if (= (get slot 0) name)
                                                  (GroupHeld :worker (get slot 2) :part (get slot 1) :count count)))
                               :queued (len (lfor t queued :if (any (gfor l t.limits (= l.group name))) t))))))


(setv DETACHED-TERMINAL #("finished" "code-failed" "env-failed" "failed" "version-mismatch" "lost" "cancelled"))
;; 実行環境の準備の一時の失敗を、別の worker へ置き直す回数の上限(起動前なので同じ task を 2 度実行しない)。
(setv ENV-RETRIES 2)


(defn #^ TaskRecord end-detached [#^ TaskRecord task #^ str phase #^ int now #^ str detail #^ (| str None) [result None]]
  "純粋: 切り離した task を終わりの phase にする(終わりの phase は二度と変わらない)。置き場のキー program は持ったまま — 行が在る間
   (結果の保持の間)は置き場の Program を参照し、行が消えたら program_policy.sweep-programs が猶予の後に消す(掃除の規則を 1 つにする)。"
  (replace task :phase phase :finished-ms now :detail detail :result result))


;; 走らせ直し(2026-11-04): 送り手が名乗った切り離した task は、担い手が消えた時に lost で終わらず、同じ key の次の試行として待ちの行へ戻る。
;; 名乗らない task は今までどおり lost。担い手が消えた道はどれも gone-detached を通る — lease 切れ(settle-detached)・止まり始めた worker が走って
;; いた子を止めた(absorb-task-reports-into)・止まり始めた worker の報告で子が結果なしで終わった(absorb-detached-report)。
;; 次の試行は行の id を替える(<最初の試行の id>-r<試行の番号> — rerun-id): worker の job の名は task/<id> なので、前の試行の process の報告と結果の
;; 届けは行が無いので受けられず(absorb-task-reports-into・absorb-task-result)、返事から外れた job として worker に止められる(沈黙から戻った旧い
;; process が同じ命令を 2 本目として走り続けない — 同じ worker の同じ世代に次の試行を置いた時も、旧い job を止めて新しい job を起こす)。
;; 走らせ直しは RERUN-LIMIT 回まで: task 自身が担い手を落としている時(memory を使い尽くすなど)に機体を落とし続けない。
;; 上限に数えるのは、予定した退きの外で担い手が消えた試行だけ(TaskRecord.losses — 2026-11-05)。予定した退き = worker の
;; 側の操作で世代が退いた(版の入れ替え・Pod の作り直し・drain): 止まり始めた世代の報告で消えた試行(absorb-task-reports-into・
;; absorb-detached-report)と、task の世代に drain が掛かっている間か、drain が掛かったまま世代が退いた後に lease が切れた試行(retiring-of)。
;; 退きは人と配備の操作の数だけ起きるので、数えなくても走らせ直しは止まる。drain の掛かっていない世代の lease 切れ(沈黙 — memory が詰まって
;; heartbeat が止まった形)と、止まり始めでない世代の子の消失は今までどおり数える。
;; 次の試行は、積み直した刻より後に連絡のあった worker にだけ置く(heard-since-rerun): 生死の窓の中でまだ生きていると数える消えた worker へ置いて、
;; また lease 切れで回数を使わない。
(setv RERUN-LIMIT 3)
;; 次の試行の行の id の区切り(task の頭と番号 — task-id・fresh-task-prefix — はこの綴りを持たない)。
(setv RERUN-ID-MARK "-r")


(defn #^ str rerun-id [#^ TaskRecord task]
  "純粋: task の次の試行の行の id(節の頭の註)。"
  (.format "{}{}{}" (get (.partition task.id RERUN-ID-MARK) 0) RERUN-ID-MARK (+ task.attempt 1)))


(defn #^ TaskRecord gone-detached [#^ TaskRecord task #^ int now #^ str why #^ (| str None) [retiring None]]
  "純粋: 担い手が消えた切り離した task の次の行(節の頭の註)。why = 消えた訳の 1 句。retiring = 予定した退きの訳(退いた世代と drain の
   頼み手 — None = 予定した退きの外で消えた)。予定した退きの試行は上限に数えない(losses を足さない)。走らせ直しを名乗り上限の内なら、次の
   試行の待ちの行(key・Program・置き方の名乗りは同じ。id が替わる — 呼び手は put-gone で前の行を消す。待ちの期限と知らせは積み直した刻から
   数える。前の試行の worker の失敗の覚えと退きの印は持ち越さない)。detail には退きか消失かと、上限に数えた消失の数を書く(読み手が「上限に
   近い」と「入れ替えに当たった」を見分ける)。ほかは lost。"
  (setv losses (if (is retiring None) (+ task.losses 1) task.losses)
        note (if (is retiring None)
                 (.format "{} — 試行 {} を待つ(上限に数えた消失 {}/{} 回)" why (+ task.attempt 1) losses RERUN-LIMIT)
                 (.format "{} — 予定した退き({})なので上限に数えない — 試行 {} を待つ(上限に数えた消失 {}/{} 回)"
                          why retiring (+ task.attempt 1) losses RERUN-LIMIT)))
  (cond
    (not task.rerun)
      (end-detached task "lost" now (.format "{} — 走らせ直さない" why))
    (> losses RERUN-LIMIT)
      (end-detached task "lost" now (.format "{} — 担い手が消えた試行が走らせ直しの上限 {} 回に達した" why RERUN-LIMIT))
    True
      (replace task :id (rerun-id task) :attempt (+ task.attempt 1) :phase "queued" :worker None :boot None :started-ms None
               :placed-memory-available-mib None :placed-measured-at-ms None
               :submitted-ms now :lease-until-ms (+ now task.lease-ms) :reported False :queued-noticed-ms None
               :avoid #() :env-attempts 0 :failure-kind "" :retryable False :losses losses :retiring ""
               :rerun-note note :detail note :preempted-by "" :borrowed False :reclaimed False)))


(defn #^ str waiting-detail [#^ TaskRecord task #^ str text]
  "純粋: 待っている task の detail(待つ訳の文 text)。走らせ直した試行は、前の試行が退きか消失か(rerun-note)を頭に残す — 積み直した拍に
   置き先が無くても、読み手が「入れ替えに当たった」と「上限に近い」を見分けられるように。"
  (if task.rerun-note (.format "{} — {}" task.rerun-note text) text))


(defn #^ (| str None) retiring-of [#^ TaskRecord task #^ ClusterState state #^ int now]
  "純粋: 置いた切り離した task の担い手の世代が予定どおり退いているか(lease 切れを上限に数えるかを分けるため — 節の頭の註)。退いた印
   (task.retiring — drain が掛かったまま世代が退いた)か、今その世代に掛かっている期限の内の drain(世代つきなら task の世代の物・
   世代を付けない drain は名前に付く)があれば訳の 1 句。どちらも無ければ None(沈黙 — 上限に数える)。"
  (setv d (if (is task.worker None) None (.row state.drains task.worker)))
  (cond
    task.retiring task.retiring
    (and (is-not d None) (> d.until-ms now) (or (is d.boot None) (is task.boot None) (= d.boot task.boot)))
      (.format "worker {} の世代 {} に drain(頼み手 {})が掛かっている" task.worker task.boot (or d.actor "名乗らない"))
    True None))


(defn #^ (get Table TaskRecord) retiring-marked [#^ (get Table TaskRecord) tasks #^ str worker #^ (| str None) boot #^ str why]
  "純粋: worker の世代 boot に置いた開いた切り離した task に、予定した退きの印 why を付けた表(付ける行が無ければ同じ object)。世代つきの
   drain が新しい世代の heartbeat で解ける拍(absorb-boot)と、退いた世代の Pod が drain を頼んだ拍(drain_policy.request-drain)に呼ぶ —
   前の世代の lease が切れる刻には drain の行が無いので、印で退きと沈黙を分ける(retiring-of)。"
  (setv writes (tuple (gfor t (scan-rows tasks)
                            :if (and t.detached (in t.phase PLACED-PHASES) (= t.worker worker) (same-boot t boot) (not t.retiring))
                            (TableWrite t.id (replace t :retiring why)))))
  (if writes (.with-writes tasks writes) tasks))


(defn #^ bool heard-since-rerun [#^ TaskRecord task #^ WorkerInfo worker]
  "純粋: task を worker に置いてよいか(節の頭の註)— 最初の試行は問わない。次の試行は、積み直した刻(submitted-ms)より後に worker の連絡があった時だけ。"
  (or (= task.attempt 1) (>= worker.last-seen-ms task.submitted-ms)))


(defk settle-detached [task now lapsed [retiring None]]
  {:pre [(: task TaskRecord) (: now int) (: lapsed bool) (: retiring (| str None))] :post [(: % (| TaskRecord None))]
   :tags {:context "coordinator" :role "judgment"}}
  "純粋: 切り離した task 1 本の期限の判断。lapsed = 自分の期限(task-lapse-at の刻)が来た。retiring = 担い手の世代が予定どおり退いている訳
   (retiring-of — None = 退きの印が無い)。保持の期限を過ぎた終わりの行は None(消す)。置いた task の lease(担い手の worker の heartbeat が
   延ばす)が切れた = 担い手が消えた(gone-detached — 名乗らない task は lost・名乗った task は id の替わった次の試行の行。退きの印のある
   lease 切れは走らせ直しの上限に数えない)。呼び手の問い合わせは lease に触らない(呼び手が消えても task は続く)。待ちの期限を名乗った
   queued の task の期限が来た = 置かれないまま期限を越えた(queue-expired-detached — cancelled で終える)。"
  (cond
    (in task.phase DETACHED-TERMINAL)
      (if lapsed None task)
    (and (in task.phase PLACED-PHASES) lapsed)
      (gone-detached task now (.format "担い手の worker {} の lease が切れた(worker の死とみなす)" task.worker) retiring)
    (and (= task.phase "queued") lapsed (is-not task.queue-limit-ms None))
      (! (queue-expired-detached task now))
    True task))


(defk span-text [ms]
  {:pre [(: ms int)] :post [(: % str)] :tags {:context "coordinator" :role "judgment"}}
  "待った長さを、送り手の会話や運用者が読む「N 時間 M 分」の形にするため(1 時間に満たなければ分だけ)。"
  (val minutes (// ms 60000))
  (if (>= minutes 60)
      (.format "{} 時間 {} 分" (// minutes 60) (% minutes 60))
      (.format "{} 分" minutes)))


(defk queue-expired-detached [task now]
  {:pre [(: task TaskRecord) (: now int)] :post [(: % TaskRecord)] :tags {:context "coordinator" :role "judgment"}}
  "純粋: 待ちの期限(queue-limit-ms — SubmitDetached の queue-limit-seconds の註)を越えて queued のままの切り離した task を cancelled で終える
   ため。送り手が置かれない task を何十時間も黙って待たないよう、detail に期限・待った長さ・最後の置かれない理由(待つ間の detail)を書く
   — 終わりなので知らせ先へ出口の終わりの行が届き、送り手は頼み直すか別の道を選べる(2026-11-12)。置いた task には効かない。"
  (<- limit str (span-text task.queue-limit-ms))
  (<- waited str (span-text (- now task.submitted-ms)))
  (end-detached task "cancelled" now
                (.format "待ちの期限 {} を越えて queued のまま置かれなかったので取り消した(待った長さ {})— 最後の置かれない理由: {}"
                         limit waited (or task.detail "理由の記録なし"))))


(defn #^ str unplaceable-phase [#^ TaskRecord task #^ ClusterState state #^ int now #^ ClusterTiming timing]
  "置ける worker が無い切り離した task の終わりの phase。能力の合う生きた worker はいるのに版だけが違う = version-mismatch。"
  (if (any (gfor w (.rows state.workers)
                 (and (alive now w timing.lease-ms) (placeable task.needs w))))
      "version-mismatch"
      "failed"))


;; task の判断(place-tasks)が比べる期限の値の関数。静かな区間の次の刻(task-due — #3062)も同じ関数から読む。

(defk task-lapse-at [task now]
  {:pre [(: task TaskRecord) (: now int)] :post [(: % (| int None))] :tags {:context "coordinator" :role "judgment"}}
  "task 1 本が自分の期限の欄で変わる最初の刻を知るため(place-tasks はこの刻から変える・task-due は同じ値を次に試す刻にする)。
   切り離していない task = lease の期限の次の ms(呼び手が問い合わせを止めた — 行を落とす)。切り離した task の終わりの行 = 保持の期限の
   次の ms(行を消す — 終わった刻を持たない行は now から数える)。置いた切り離した task = lease の期限の次の ms(担い手の worker の死 —
   lost)。待ちの期限を名乗った queued の切り離した task = 積んだ刻 + 期限の次の ms(置かれないまま期限を越えた — cancelled)。ほかの
   待っている切り離した task は自分の期限では変わらない(None — 担い手の沈黙の待ちの期限は wait-lapse-at)。"
  (match task
    (TaskRecord :detached False) (+ task.lease-until-ms 1)
    (TaskRecord :phase phase) :if (in phase DETACHED-TERMINAL) (+ (or task.finished-ms now) task.retain-ms 1)
    (TaskRecord :phase phase) :if (in phase PLACED-PHASES) (+ task.lease-until-ms 1)
    (TaskRecord :phase "queued" :queue-limit-ms limit) :if (is-not limit None) (+ task.submitted-ms limit 1)
    _ None))


(defn #^ bool held-task? [#^ TaskRecord task]
  "切り離した終わりの行(結果を保持しているだけ)か。切り離していない終わった行は含まない(lease を延ばされ走査の対象のまま)。
   task の表の索引と同じ判じ(task_table.held-row?)。"
  (held-row? task))


(defn #^ list scan-rows [#^ (get Table TaskRecord) tasks]
  "走査の対象の行(held-task? でない行 — 結果を保持しているだけの切り離した終わりを除く)を id の順で。数え・並べの読みが保持側の行を回さないため
   (task-split の走査の対象と同じ行 — 期限の最小は要らない読みの口)。task の表の索引から読む(保持の行を回らない — task_table)。"
  (list (.values (. (task-index tasks) live))))


(defk task-split [tasks now]
  {:pre [(: tasks (get Table TaskRecord)) (: now int)] :post [(: % tuple)] :tags {:context "coordinator" :role "judgment"}}
  "1 走査で tasks を、走査の対象の行(held-task? でない行 — id の順を保つ dict)と、保持側の最小期限(task-lapse-at と同じ式 —
   終わった刻 finished-ms を持たない行は除く。無ければ None)に分けるため。#(走査の対象 最小期限) を返す。どちらも task の表の索引が書きの
   たびに持ち続ける値(task_table — 保持の行を回らない)。走査の対象の dict は読み手が書き換えない。"
  (val index (task-index tasks))
  #(index.live index.earliest))


(defk wait-lapse-at [capable timing]
  {:pre [(: capable list) (: timing ClusterTiming)] :post [(: % (| int None))] :tags {:context "coordinator" :role "judgment"}}
  "置ける worker の無い待っている task を、能力と版の合う worker capable がいま連絡していない間に待たせる期限が過ぎる最初の刻を知るため
   (place-tasks はこの刻から待ちを終える・task-due は同じ値を次に試す刻にする): capable のうち最後の連絡が最も新しい物から
   ClusterTiming.silent-worker-wait-ms の次の ms。capable が空なら None(待たずに終える)。"
  (if capable (+ (max (gfor w capable w.last-seen-ms)) timing.silent-worker-wait-ms 1) None))


(defn #^ tuple task-writes [#^ (get Table TaskRecord) original #^ dict rows]
  "純粋: rows(task の id → 書き直した行か None = 消す)のうち、original の行と違う行(object が違い値も違う行)だけの書きの列。
   値が等しいだけで object の違う行・元から無い行を消す書きは含めない(何も変わらなければ空の列 — place-tasks の答えが前の表のまま)。"
  (tuple (gfor #(id new) (.items rows)
               :setv old (.row original id)
               :if (not (or (is new old) (and (is-not new None) (is-not old None) (= new old))))
               (TableWrite id new))))


;; 置き方の判断が読まない欄を空にした行の覚え(task の行の object の id → #(行 行の側の PlacementClass) — 2026-11-12)。待っている
;; task の行は、変わらない拍には同じ object が状態の tasks に残る(書き直すと新しい object になる)ので、object ごとに 1 度だけ空にする
;; (欄の多い行の写しが、置けない判断を借りた後の place-tasks の費用の大半だった)。行そのものを一緒に持つので、持っている間に id が別の
;; object へ使い回されない。上限 PLACEMENT-ROW-MEMO-MAX を越えたら全部を捨てて覚え直す(handoff_policy の _FINGERPRINTS と同じ形)。
(setv _PLACEMENT-ROWS {})
(setv PLACEMENT-ROW-MEMO-MAX 8192)


(defn #^ PlacementClass placement-row [#^ TaskRecord task]  ; cannot be a defk: 覚えの dict を引く(Program の外の純粋な判断 placement-class の材料)
  "task の行から、置き方の判断(able・task-misfit・starved-keeper・線の内の貸し・置き先の順)が読まない欄(id・名・Program・期限・detail・
   環境変数・知らせ先など)を空にした行と、実行環境の宣言の JSON の文字列を持つ PlacementClass(unheard と keeps は空の値 — 呼び手の
   placement-class が拍の値で埋める)を得るため。読む欄を空にすると違う task が同じ判断を借りるので、迷う欄は残す(残すと判断を借りる
   task が減るだけで答えは変わらない)。同じ行の object には覚えた値を返す。"
  (setv kept (.get _PLACEMENT-ROWS (id task)))
  (when (and (is-not kept None) (is (get kept 0) task))
    (return (get kept 1)))
  (setv row (replace task :id "" :name "" :program None :revision "" :lease-ms 0 :lease-until-ms 0 :submitted-ms 0
                          :detail "" :key None :retain-ms 0 :environ #() :notify "" :queued-noticed-ms None
                          :rerun-note "" :runtime-env None)
        env (if (is task.runtime-env None) None (json.dumps task.runtime-env :sort-keys True :ensure-ascii False)))
  (when (>= (len _PLACEMENT-ROWS) PLACEMENT-ROW-MEMO-MAX)
    (.clear _PLACEMENT-ROWS))
  (setv shape (PlacementClass :row row :env env :unheard (frozenset) :keeps False))
  (setv (get _PLACEMENT-ROWS (id task)) #(task shape))
  shape)


(defk placement-class [task workers now]
  {:pre [(: task TaskRecord) (: workers tuple) (: now int)] :post [(: % PlacementClass)] :tags {:context "coordinator" :role "judgment"}}
  "待っている task の置き方を決める task の側の入力を 1 つの値にするため(place-tasks が、置けないと判じた task と同じ値の後ろの task を、
   置き先の数えが変わるまで数え直さない — 2026-11-12・PlacementClass の註)。行の側は placement-row(読まない欄を空にした行)。
   workers = 状態の全部の worker の行(次の試行の task の unheard を数える)。"
  (<- keeps bool (keeps-room? task.priority (- now task.submitted-ms)))
  (replace (placement-row task)
           :unheard (frozenset (gfor w workers :if (not (heard-since-rerun task w)) w.name))
           :keeps keeps))


(defk tasks-placed [now state placements timing]
  {:pre [(: now int) (: state ClusterState) (: placements (get Table Placement)) (: timing ClusterTiming)] :post [(: % (get Table TaskRecord))]
   :tags {:context "coordinator" :role "judgment"}}
  "純粋: place-tasks の書きを state.tasks へ当てた表(書きが空なら state.tasks そのもの)— 全件の表で置き先を読む読み手(test)のため。"
  (.with-writes state.tasks (! (place-tasks now state placements timing))))


(defk place-tasks [now state placements timing]
  {:pre [(: now int) (: state ClusterState) (: placements (get Table Placement)) (: timing ClusterTiming)] :post [(: % tuple)]
   :tags {:context "coordinator" :role "judgment"}}
  "task の期限切れを落とし、担い手が沈黙した task を失敗にし、待っている task を置く。切り離した task は settle-detached の規則。
   答えは state.tasks へ当てる書きの列(TableWrite の tuple — 変わった行だけ・何も変わらなければ空)。全件の表は組まない。"
  ;; 結果を保持しているだけの行(切り離した終わり)は走査しない — 保持側の最小期限が来た拍だけ 1 周して期限切れを落とす。
  (<- split tuple (task-split state.tasks now))
  (setv #(live earliest) split)
  ;; writes = task の id → 書き直した行か None(消す)。変わらない行は書かない(最後に task-writes が元の行と比べて絞る)。
  (setv writes {})
  ;; queue = 走査の後も待っている task(置く判断の対象)。
  (setv queue {})
  (for [#(id task) (.items live)]
    ;; 自分の期限(静かな区間の次の刻 task-due と同じ関数 task-lapse-at の刻)が来たか。
    (<- lapse (| int None) (task-lapse-at task now))
    (val lapsed (and (is-not lapse None) (>= now lapse)))
    (cond
      task.detached
        (do (<- kept (| TaskRecord None) (settle-detached task now lapsed
                                                          ;; 退きの印は lease の切れた置いた行だけで読む(走査の毎拍に drain の行を引かない)。
                                                          (if (and lapsed (in task.phase PLACED-PHASES)) (retiring-of task state now) None)))
            ;; 走らせ直しは行の id が替わる(前の行を消して次の試行の行を書く — 節 gone-detached の頭の註)。
            (when (and (is-not kept None) (!= kept.id id))
              (setv (get writes kept.id) kept))
            (setv (get writes id) (if (and (is-not kept None) (!= kept.id id)) None kept)))
      ;; 呼び手が問い合わせを止めた(止まった)= task も要らない。担い手は次の heartbeat で子 process を止める。
      lapsed (setv (get writes id) None)
      (and (in task.phase PLACED-PHASES)
           (or (is (.row state.workers task.worker) None)
               (not (alive now (.row state.workers task.worker) timing.reassign-after-ms))))
        (setv (get writes id) (replace task :phase "failed" :finished-ms now
                                       :detail (.format "担い手の worker {} が沈黙した(task は走らせ直さない)" task.worker)))))
  ;; 線の内の貸しの取り返し(measured_room の頭の註): 上に載る系が memory を待つ worker の借りの task を止めて次の試行の待ちの行へ戻す。
  ;; 待ちの行は下で queue に入り、この拍に置き直せる(その worker は待つ間は貸しを名乗らない)。
  (val after-scan (tuple (gfor #(id task) (.items live) :setv row (if (in id writes) (get writes id) task) :if (is-not row None) row)))
  (for [w (sorted (.rows state.workers) :key (fn [w] w.name)) :if (alive now w timing.lease-ms)]
    (<- victims tuple (reclaim-victims w after-scan))
    (for [v victims]
      (<- next-row TaskRecord (reclaimed-detached v now w.name))
      (setv (get writes v.id) None
            (get writes next-row.id) next-row)))
  ;; 走査の後の行の読み(書いた行が先・無ければ元の行)。
  (for [#(id task) (.items live)]
    (setv now-row (if (in id writes) (get writes id) task))
    (when (and (is-not now-row None) (= now-row.phase "queued"))
      (setv (get queue id) now-row)))
  ;; この拍に積み直した次の試行の行(id が新しいので live に無い)も、この拍で置く。
  (for [#(id row) (.items writes)]
    (when (and (is-not row None) (not-in id live) (= row.phase "queued"))
      (setv (get queue id) row)))
  ;; 保持側の行は書かず元の object のまま残す。期限(finished-ms + retain-ms + 1 — task-lapse-at と同じ式)が来た行は落とす(期限の来た拍だけ
  ;; 全行を読む)。finished-ms の無い行は毎拍 now から数えるので落ちない。
  (when (and (is-not earliest None) (>= now earliest))
    (for [#(id lapse) (.items (. (task-index state.tasks) held)) :if (>= now lapse)]
      (setv (get writes id) None)))
  ;; 待っている task が無ければ、置く判断(負荷・drain 中の worker・状態の写し)を組まない — 調停の 1 周ごとに状態全体を写す費用が
  ;; 模擬の拍の大半だった(#2655 の profile: replace 135,053 回・2.3 秒)。
  (when (not queue)
    (return (task-writes state.tasks writes)))
  (setv placed (replace state :tasks (.with-writes state.tasks (task-writes state.tasks writes))))
  (setv load (load-of placed placements)
        draining (draining-workers state now))
  ;; 置いている task の上限の升ごとの数(置くたびに足す — 同じ拍で置いた分も数える)。
  (var held (! (limit-holdings (! (placed-claims placed.tasks)) state.group-limits)))
  ;; 形ごとの測った山(task の要りを名乗りと測った山の大きい方で数える — measured_room の頭の註)。
  (<- peaks dict (shape-peaks-of placed.tasks))
  ;; 背景の仕事の側の予約(background-reserve-of)の守りに要る worker ごとの数: bg-held = 置いている負の優先の task(置くたびに足す)・
  ;; bg-wanted = 今その worker に置けうる(生きて drain 中でなく disk が在り、名乗った組の上限に達しておらず、門の内の task は要りが
  ;; memory の空きに入る)待っている負の優先の task。
  ;; bg-wanted は拍の初めに 1 度数える — 置く順で優先 0 以上の task が先なので、背景の仕事を置き始める前に読む数は変わらない。
  ;; 要りが memory の空きを越える task(TASK-NO-MEMORY)を数えると、置けない背景の仕事が予約の背景の仕事の分を守り続け、背景の仕事も
  ;; 優先 0 以上の task もその分を使わない(2026-11-12 — 要り 2560 MiB の背景の仕事 160 本がどの worker の空きにも入らず待ち、優先 0 の
  ;; task 94 本が背景の仕事の分の文で 3 時間超待った)。
  (setv bg-held (dfor name (.keys state.workers) name 0)
        bg-wanted (dfor name (.keys state.workers) name 0)
        ;; 長く待った task へこの拍に取っておいた memory(worker の名 → MiB — measured_room の頭の註の取っておき)。
        kept (dfor name (.keys state.workers) name 0))
  ;; この拍に急ぎの task へ場所を譲らせると決めた task の id(measured_room の頭の註の場所の譲り — 同じ task を 2 度止めない)。
  (var taken (frozenset))
  ;; 譲らせる候補を探す task の行(拍の初めに置かれていた task — この拍に置いた task は優先が低いので急ぎの task の後に置かれる)。
  (val placed-rows (tuple (scan-rows placed.tasks)))
  (for [t (scan-rows placed.tasks) :if (and (< t.priority 0) (in t.phase PLACED-PHASES) (in t.worker bg-held))]
    (+= (get bg-held t.worker) 1))
  ;; worker ごとの memory の空き(背景の仕事は急ぎでない — 平常の数え)は、背景の仕事が待っている拍だけ 1 度数える。
  (var bg-memory-rooms {})
  ;; 置き方の task の側の入力(placement-class)は待っている task ごとに 1 度だけ求め、下の 2 つの使い回しの鍵にする。
  (val worker-rows (tuple (.rows state.workers)))
  (val classes {})
  (for [#(id task) (.items queue)]
    (<- queued-class PlacementClass (placement-class task worker-rows now))
    (setv (get classes id) queued-class))
  ;; 同じ入力の背景の仕事は同じ worker で数える(この数えの間は組の升と負荷が変わらない)— 入力 → 数える worker の名。
  (val bg-counted {})
  (for [#(id task) (.items queue) :if (and (= task.phase "queued") (< task.priority 0))]
    (val bg-class (get classes id))
    (when (not-in bg-class bg-counted)
      (var counted #())
      (for [w worker-rows :if (and (alive now w timing.lease-ms) (heard-since-rerun task w) (can-run-task task w) (not-in w.name draining)
                                   (env-room-on task w))]
        (<- bg-limited (| TaskUnplacedKind None) (limits-misfit task.limits w.name held state.group-limits))
        (when (and (is bg-limited None) (not task.ungated) (not-in w.name bg-memory-rooms))
          (<- bg-memory-room int (task-memory-room-of w load))
          (:= bg-memory-rooms (| bg-memory-rooms {w.name bg-memory-room})))
        (when (and (is bg-limited None)
                   (or task.ungated (<= (! (task-need-of task w peaks)) (get bg-memory-rooms w.name))))
          (:= counted #(#* counted w.name))))
      (setv (get bg-counted bg-class) counted))
    (for [name (get bg-counted bg-class)]
      (+= (get bg-wanted name) 1)))
  ;; 置けないと判じた入力の答え(入力 → #(数えの代 待ちの文 — None = 文を書かずに待つ))。数えの代(epoch)は、この拍に task を置いた・
  ;; 取っておいた・場所を譲らせた時に進む(負荷・組の升・背景の仕事の数・取っておき・止めた task の数えが変わる)。同じ入力の後ろの
  ;; task は、代が同じ間は worker ごとの要り・空き・置けない理由を数え直さずに同じ文で待つ(2026-11-12 — 置けない task が数百本溜まると、
  ;; 要求ごとの調停が待っている task の数 × worker の数の数えで主 thread を埋めた)。急ぎの優先の task は場所の譲りの候補(taken)を読む
  ;; ので借りない。
  (val unplaced-verdicts {})
  (var epoch 0)
  ;; 置く順 = 優先の大きい順、同じ優先の中は id の順(2026-10-11 — 以前は id の順だけで、先に積んだ背景の仕事が空いた枠を先に取った)。
  (for [id (sorted queue :key (fn [i] #((- (. (get queue i) priority)) i)))]
    (setv task (get queue id))
    ;; 急ぎの優先の task は、worker が上に載る系の残す線を外して名乗る空きで数える(measured_room の頭の註の急ぎの task の空き)。
    (<- urgent bool (urgent-priority? task.priority))
    (val task-class (get classes id))
    ;; 同じ入力の task が、この拍の今の数えで置けないと判じられた: 同じ文で待つ(上の unplaced-verdicts の註)。
    (when (and (not urgent) (in task-class unplaced-verdicts))
      (val known-reason (get unplaced-verdicts task-class))
      (when (is-not known-reason None)
        (val known-text (waiting-detail task known-reason))
        (when (!= task.detail known-text)
          (setv (get writes id) (replace task :detail known-text))))
      (continue))
    (when (= task.phase "queued")
      ;; drain 中の worker には新しい task を置かない(置ける先が他に無ければ、drain が解けるまで待つ — 失敗にはしない)。
      (setv able (lfor w (.rows state.workers) :if (and (alive now w timing.lease-ms) (heard-since-rerun task w) (can-run-task task w)) w)
            ;; drain 中と、disk の尽きた worker(準備済みでない env の task)は避ける — 置ける先が他に無ければ待つ。
            able-now (lfor w able :if (and (not-in w.name draining) (env-room-on task w)) w))
      ;; 測った空きの門の外の task(ungated)は、名乗った組の上限に達していない worker ならどれにも置ける(下の misfit — 空きを判じない)。
      ;; task を置けるのは測った空き(task-room-of — 機体の CPU・PSI から数え、置いた task と測りにまだ出ていない task を引いた数)が在り、
      ;; task の memory の要りが memory の空き(task-memory-room-of — 測った空き − 床 − 置いた task のまだ伸びる分)以下で、名乗った上限に
      ;; 達しておらず、負の優先なら予約を越える空きの在る worker だけ(task-misfit)。置けない worker の理由は待ちの文に使う。roomy = 置ける
      ;; worker と測った空きの組。
      ;; 要りは worker ごと(測った山をその worker の線で切る — task-need-of)。
      (var needs {})
      (var rooms {})
      ;; worker の名 → 測った数の空きと、門の外の task の memory の空き(場所の譲りが読む — yield-plan)。
      (var counts {})
      (var bares {})
      ;; 要りの足りなさ(worker の名 → MiB — 置き先の順の頭)。門の外の task は、測った空きから測りにまだ出ていない task の要りだけを
      ;; 引いた空き(伸びの見込みを引かない — measured_room の頭の註の置き先の順)で数える。門の内の task は数えない(0 — roomy は要りが入る worker だけ)。
      (var shorts {})
      (var roomy #())
      (var misfits [])
      (for [w able-now]
        (<- need int (task-need-of task w peaks))
        (setv (get needs w.name) need)
        (<- room int (task-room-of w load :urgent urgent))
        (<- memory-room int (task-memory-room-of w load :urgent urgent))
        (setv (get rooms w.name) memory-room
              (get counts w.name) room)
        (when task.ungated
          (<- bare-room int (memory-room-mib w.measured (. (get load w.name) unmeasured-mib) :urgent urgent))
          (setv (get bares w.name) bare-room
                (get shorts w.name) (max 0 (- need bare-room))))
        ;; 測った空きの門の外の task は、空きと予約を判じず、名乗った組(宣言に無い組・組の上限)だけで判じる(measured_room の頭の註の
        ;; 測った空きの門の外の task)。
        (<- misfit (| TaskUnplacedKind None) (if task.ungated
                                                 (limits-misfit task.limits w.name held state.group-limits)
                                                 (task-misfit task.limits task.priority w room held state.group-limits
                                                              (get bg-held w.name) (get bg-wanted w.name) need memory-room)))
        (if (is misfit None)
            (:= roomy #(#* roomy #(w room)))
            (:= misfits [#* misfits misfit])))
      ;; 置き先の順の頭は要りの足りなさ(shorts — 入る worker は 0)の小さい順(measured_room の頭の註の測った空きの門の外の task の置き先の
      ;; 順)。門の内の task はどれも 0 で順は変わらない。門の外の task は、要りの入る worker が在ればそこへ、どこにも入らなければ足りなさの
      ;; 最も小さい worker へ置く(待たない)。前は空きを判じず次の順だけで選び、測った空きの数が同じ 0 の 2 台から名の順で memory の少ない
      ;; 機体を選び、置かれた task が jobs の層の追い出しで止まり、置き直しで 1 回ごとに 5〜15 分遅れた(2026-11-11)。
      ;; 実行環境の task は、その env を準備済みの worker を優先する(空きの多さより先 — 準備を task の待ちに入れない・2026-09-26)。
      ;; 次は測った空きの多い順(以前は担っている数の少ない順 — 空いた機体があるのに混んだ機体へ置いた)、同じ空きは名の順。
      (var free (lfor #(w _) (sorted roomy :key (fn [pair] #((.get shorts (. (get pair 0) name) 0)
                                                              (not (env-ready-on task.runtime-env (get pair 0)))
                                                              (- (get pair 1)) (. (get pair 0) name))))
                       w))
      ;; 線の内の貸し(measured_room の頭の註): 場所を譲る名乗りの門の内の task が線の上の空きで置けない時、worker が線の内を貸す空きで
      ;; 数え直し、入る worker に借りとして置く(急ぎの優先の task は急ぎの task の空きで数えるので借りにしない)。
      (var lent False)
      (when (and (not free) (not urgent) (not task.ungated) task.detached task.preemptible task.rerun)
        (var lend-roomy #())
        ;; 取り返す量を名乗る worker(上に載る系が待っている)には貸さない(前の版の worker が両方を名乗っても)。
        (for [w able-now :if (and (is-not w.measured None) (is-not w.measured.lend-memory-available-MiB None)
                                  (is w.measured.reclaim-MiB None))]
          (<- lend-room int (task-room-of w load :lend True))
          (<- lend-memory-room int (task-memory-room-of w load :lend True))
          (<- lend-misfit (| TaskUnplacedKind None) (task-misfit task.limits task.priority w lend-room held state.group-limits
                                                                 (get bg-held w.name) (get bg-wanted w.name) (get needs w.name)
                                                                 lend-memory-room))
          (when (is lend-misfit None)
            (:= lend-roomy #(#* lend-roomy #(w lend-room)))))
        (when lend-roomy
          (:= free (lfor #(w _) (sorted lend-roomy :key (fn [pair] #((not (env-ready-on task.runtime-env (get pair 0)))
                                                                     (- (get pair 1)) (. (get pair 0) name))))
                         w))
          (:= lent True)))
      ;; 場所の譲り(measured_room の頭の註): 急ぎの優先の task が memory の空きに入らない(門の内は置ける worker が無い・門の外は選んだ
      ;; worker でも要りが空きを越える)時、同じ worker の場所を譲る名乗りの優先の低い task を止めて次の試行の待ちの行へ戻し、この拍で
      ;; 急ぎの task をその worker に置く。止めた task の process は、返事から外れた job として worker が止める(走らせ直しの道と同じ)。
      ;; 譲らせる先が無ければ今までどおり(門の内は待つか取っておく・門の外は足りなさの最も小さい worker へ置く)。
      (var yielded None)
      (when (and urgent (or (not free) (and task.ungated (> (get shorts (. (get free 0) name)) 0))))
        (<- plan (| YieldPlan None) (yield-plan task (if task.ungated free able-now) needs (if task.ungated bares rooms) counts
                                                placed-rows peaks placed.claims taken held state.group-limits bg-held bg-wanted))
        (when (is-not plan None)
          (:= yielded plan)
          (val yield-worker plan.worker)
          (for [v plan.victims]
            (<- next-row TaskRecord (preempted-detached v.task now task yield-worker.name))
            (setv (get writes v.task.id) None
                  (get writes next-row.id) next-row)
            (:= taken (| taken (frozenset #(v.task.id))))
            (when (< v.task.priority 0)
              (-= (get bg-held yield-worker.name) 1)))
          ;; 止めた task の分を置いた task の数え(load-of)から外す: 数と予約と測りにまだ出ていない要り。今の使いは worker が止めて次の
          ;; 測りに出るまで測った空きに残る(この拍の後ろの task は、戻る前の空きで数える)。
          (val yield-used (get load yield-worker.name))
          (setv (get load yield-worker.name) (replace yield-used :tasks (max 0 (- yield-used.tasks (len plan.victims)))
                                           :reserved-mib (max 0 (- yield-used.reserved-mib (sum (gfor v plan.victims v.reserved-mib))))
                                           :unmeasured-mib (max 0 (- yield-used.unmeasured-mib (sum (gfor v plan.victims :if (not v.measured)
                                                                                                  v.reserved-mib))))))
          (:= free [yield-worker])))
      ;; 置けずに STARVE-WAIT-MS を越えて待った task と急ぎの優先(KEEP-NOW-PRIORITY 以上)の task は(keeps-room?)、memory の空きだけで
      ;; 置けない worker のうち、今の task が全部終われば入る見込みの在る 1 台に要りを取っておく(measured_room の頭の註の取っておき)。
      ;; 後ろの順の task は、その worker の memory の空きから要りを引いて数える。測った空きの門の外の task は空きを待たないので取っておかない
      ;; (置けないのは組の上限だけ — 空きを溜めても置けない)。
      (var keeper None)
      (when (and (not free) (not task.ungated) (! (keeps-room? task.priority (- now task.submitted-ms))))
        (<- chosen-keeper (| WorkerInfo None) (starved-keeper task able-now needs rooms kept held state.group-limits))
        (:= keeper chosen-keeper)
        (when (is-not keeper None)
          (val kept-need (get needs keeper.name))
          (val kept-load (get load keeper.name))
          (setv (get load keeper.name) (replace kept-load :reserved-mib (+ kept-load.reserved-mib kept-need)))
          (+= (get kept keeper.name) kept-need)
          ;; 取っておきで worker の memory の空きが減った — 置けないと判じた答えは、後ろの task に貸さない。
          (.clear unplaced-verdicts)))
      (cond
        free (do (setv chosen (get free 0)
                       ;; 準備済みの worker が無い置き先 = 冷たい起動(worker が準備してから走る)。phase を preparing にして assigned と分ける。
                       phase (if (and (is-not task.runtime-env None) (not (env-ready-on task.runtime-env chosen)))
                                 "preparing" "assigned")
                       used (get load chosen.name)
                       ;; 置いた task は今の刻 now に置かれ、測った刻より後なので測りにまだ出ていない(load-of の unseen と同じ数え)。
                       ;; 今の使いも名乗られていないので、要りの全部を予約する(load-of の reserved-mib と同じ数え)。
                       (get load chosen.name) (replace used :tasks (+ used.tasks 1) :unseen (+ used.unseen 1)
                                                       :reserved-mib (+ used.reserved-mib (get needs chosen.name))
                                                       :unmeasured-mib (+ used.unmeasured-mib (get needs chosen.name))))
                 (when (< task.priority 0)
                   (+= (get bg-held chosen.name) 1))
                 (<- after-hold dict (hold-limits held task.limits chosen.name state.group-limits))
                 (:= held after-hold)
                 ;; 負荷と組の升が変わった(場所を譲らせた分も)— 置けないと判じた答えは、後ろの task に貸さない。
                 (.clear unplaced-verdicts)
                 ;; 置いた拍に数えた worker の測った空きと、その測りの刻を行に写す(task が終わった後も置いた刻の空きを読むため —
                 ;; worker の measured は保存しない拍ごとの観測)。測りの無い worker(門の外の task だけが置ける)は写さない。
                 (setv seen chosen.measured
                       seen-available (if (is seen None) None seen.memory-available-MiB)
                       seen-at (if (is seen None) None seen.at-ms))
                 ;; 切り離した task は置いた worker の process の世代を覚え、lease を置いた時から数える。場所を譲らせて置いた task は、
                 ;; 止めた task を detail に名指す。
                 (setv (get writes id) (if task.detached
                                          (replace task :phase phase :worker chosen.name :started-ms now
                                                   :placed-memory-available-mib seen-available :placed-measured-at-ms seen-at
                                                   :boot chosen.boot :lease-until-ms (+ now task.lease-ms) :borrowed lent
                                                   :detail (cond
                                                             lent (.format "線の上の memory の空きに入らないので、worker {} の線の内に借りとして置いた(上に載る系が memory を待つ間は止めて待ちの行へ戻す)"
                                                                           chosen.name)
                                                             (is yielded None)
                                                               task.detail
                                                             True
                                                               (.format "memory の空きに入らないので、worker {} の優先の低い task {} に場所を譲らせて置いた"
                                                                        chosen.name (.join "・" (gfor v yielded.victims v.task.id)))))
                                          (replace task :phase phase :worker chosen.name :started-ms now
                                                   :placed-memory-available-mib seen-available :placed-measured-at-ms seen-at))))
        ;; 置ける worker(drain 中でも disk 尽きでもない)は在るが、どれにも今置けない(測った空きが 0・測りが無い・上限・予約): 失敗にせず待つ。
        ;; 理由は閉じた語(misfit-reason)の固定の文(task-unplaced-text)。既に同じ文なら書き換えない(調停が拍ごとに保存して版を進めない)。
        ;; drain 中・disk 尽きで置ける先が 1 つも無い(able-now が空)時は、今までどおり理由を書かずに待つ(空きの話ではない)。
        able-now
          (let [reason (! (task-unplaced-text (! (misfit-reason misfits))))
                text (waiting-detail task (if (is keeper None) reason (! (kept-text reason keeper.name))))]
            ;; 取っておかずに待つ答えは、同じ入力の後ろの task に貸す(取っておいた時は上で答えを捨てた)。
            (when (and (not urgent) (is keeper None))
              (setv (get unplaced-verdicts task-class) reason))
            (when (!= task.detail text)
              (setv (get writes id) (replace task :detail text))))
        ;; 準備の一時の失敗の後に、置き直せる別の worker が無い: 最後の失敗で終える。
        (and (not able) task.failure-kind)
          (setv (get writes id) (end-env-failed task now task.detail))
        ;; 能力の合う生きた worker はいるが、宣言の道具を名乗る worker が無い。
        (and (not able) (is-not task.runtime-env None)
             (any (gfor w (.rows state.workers)
                        (and (alive now w timing.lease-ms) (placeable task.needs w)))))
          (setv (get writes id)
                (end-env-failed (replace task :failure-kind "tool-missing" :retryable False) now
                                (.format "宣言の道具 {} を名乗る worker が無い"
                                         (lfor t (.get task.runtime-env "tools" []) (.format "{}{}" (get t "name")
                                                                                             (if (.get t "version") (+ "=" (get t "version")) ""))))))
        ;; 能力と版の合う worker は登録されているが、いま連絡していない(coordinator を起こし直した直後・worker の Recreate の入れ替えの間・
        ;; 能力を持つ worker が 1 台だけの時の一瞬の沈黙)なら、待っても晴れうるので失敗にせず待つ(#2440)。待ちの上限は task の lease では
        ;; なく明示の期限 ClusterTiming.silent-worker-wait-ms で、合う worker のうち最後の連絡が新しい物から数える(#2753 — 切り離した task の
        ;; lease は積んだ時の 60 秒で、入れ替えの drain の間に過ぎて落ちた)。待つ間の detail は拍ごとに変えない(経った秒を書くと、調停が
        ;; 拍ごとに保存して版を進める)。期限を過ぎたら、待った長さと期限を名指して終える。登録された worker のどれも能力と版が合わない時は
        ;; 待っても晴れないので、版の違う所を名指して終える。ended = 終える理由(None = 待つ)。
        (not able)
          (let [capable (lfor w (.rows state.workers) :if (can-run-task task w) w)
                silence (if capable (- now (max (gfor w capable w.last-seen-ms))) None)
                ;; 待ちの期限(task-due と同じ関数 wait-lapse-at の刻)。
                waited (! (wait-lapse-at capable timing))
                names (.join "・" (sorted (gfor w capable w.name)))
                limit-s (// timing.silent-worker-wait-ms 1000)
                ended (cond
                        (is silence None) (versions-note task state now timing)
                        (>= now waited)
                          (.format "要る能力 {} の worker {} が {} 秒 live でない(待ちの期限 {} 秒を過ぎた)"
                                   (list task.needs) names (ceil (/ silence 1000)) limit-s)
                        True None)]
            (setv (get writes id)
                  (cond
                    (is ended None)
                      (replace task :detail (waiting-detail task (.format "要る能力 {} の worker {} がいま連絡していない — 連絡が戻るまで待つ(待ちの期限 = 最後の連絡から {} 秒)"
                                                                          (list task.needs) names limit-s)))
                    task.detached (end-detached task (unplaceable-phase task state now timing) now ended)
                    True (replace task :phase "failed" :finished-ms now :detail ended))))
        ;; 置ける worker が drain 中か disk 尽きだけ: 文を書かずに待つ答えを、同じ入力の後ろの task に貸す。
        (not urgent)
          (setv (get unplaced-verdicts task-class) None))))
  (task-writes state.tasks writes))


(defn #^ bool same-boot [#^ TaskRecord task #^ (| str None) boot]
  "切り離した task の担い手の process の世代が、置いた時と同じか(どちらかを知らなければ同じとみなす)。"
  (or (not task.detached) (is task.boot None) (is boot None) (= task.boot boot)))


(defn #^ TaskRecord end-env-failed [#^ TaskRecord task #^ int now #^ str detail]
  "純粋: 実行環境を準備できなかった task を終える(切り離した task は終わりの phase)。"
  (if task.detached
      (replace (end-detached task "env-failed" now detail) :failure-kind task.failure-kind :retryable task.retryable)
      (replace task :phase "env-failed" :finished-ms now :detail detail)))


(defn #^ TaskRecord absorb-env-failure [#^ TaskRecord task #^ str worker #^ StatusRow status #^ int now]
  "純粋: worker の「実行環境を準備できない」の報告 → 一時の失敗で置き直しの回数が残れば、その worker を避けて待ちへ戻す。
   それ以外は終える。どちらも子 process を起こす前(Program は走っていない)。"
  (setv kind (or status.failure-kind "") retryable (is status.retryable True)
        detail (.format "worker {} で実行環境を準備できない({}): {}" worker kind status.detail)
        failed (replace task :failure-kind kind :retryable retryable :detail detail))
  (if (and retryable (< task.env-attempts ENV-RETRIES))
      (replace failed :phase "queued" :worker None :boot None :started-ms None
               :placed-memory-available-mib None :placed-measured-at-ms None
               :avoid (+ task.avoid #(worker)) :env-attempts (+ task.env-attempts 1))
      (end-env-failed failed now detail)))


(defn #^ tuple tasks-for [#^ ClusterState state #^ str worker #^ (| str None) [boot None]]
  "heartbeat の返事で worker の process へ走らせる task を渡すため。切り離した task は、置いた時と同じ process の世代にだけ送る
   (作り直した worker の process で走らせ直さない)。boot = 返事を受ける process の世代(渡さなければ coordinator の見る今の世代)。"
  (setv known (.row state.workers worker)
        boot (cond (is-not boot None) boot (is known None) None True known.boot))
  ;; 詰めた Program は置き場のキー(sha)だけを運ぶ — worker が /programs/<sha> から取る(service の job と同じ・改訂 1 の F)。
  ;; 切り離した task は、状態を失った coordinator が引き取れるだけの欄を持つ(worker が状態の報告に写す — adopt-running-detached・
  ;; 2026-09-27)。子の環境変数は service の job の行の environ と同じ欄(切り離した task は写しにも残る)。JSON は protocol/replies。
  ;; memory-mib は置いた時に固定した申告(task_claims.offered-mib-of — worker が task の使いを比べる量)。
  (tuple (gfor task (sorted (scan-rows state.tasks) :key (fn [t] t.id))
               :if (and (in task.phase PLACED-PHASES) (= task.worker worker) (same-boot task boot))
               (TaskOffer :id task.id :name task.name :revision task.revision :versions task.versions :program task.program
                          :detached task.detached :key task.key :lease-ms task.lease-ms :retain-ms task.retain-ms :needs task.needs
                          :runtime-env task.runtime-env :environ task.environ :memory-mib (run (offered-mib-of task state.claims))
                          :rerun task.rerun :attempt task.attempt :losses task.losses :notify task.notify
                          :priority task.priority :preemptible task.preemptible))))


(defn #^ TaskRecord task-finished [#^ TaskRecord task #^ int now #^ str detail #^ (| str None) result]
  "純粋: 子 process が結果を持って終わった task の記録(切り離した task は終わりの phase — end-detached)。heartbeat の報告と子 process の
   直の届け(absorb-task-result)が同じ形で終える。result = 詰めた結果(None = 結果なし — 切り離した task では呼ばない)。"
  (if task.detached
      (end-detached task "finished" now detail result)
      (replace task :phase "finished" :finished-ms now :result result :detail detail)))


(defn #^ TaskRecord absorb-detached-report [#^ TaskRecord task #^ StatusRow status #^ int now #^ bool [stopping False]]
  "切り離した task の終わりの報告 → 終わりの phase。結果を書かずに終わった子 process は lost(結果が無い = 消失)。stopping = この報告は止まり
   始めた世代の物(機体の停止は worker と子へ同時に止めの合図を送るので、worker が止める前に子が結果なしで終わった報告が来る — 担い手が消えた
   道の 1 つ。gone-detached)。止まり始めでない世代の子の消失(memory の見張りや OS に止められた子)は走らせ直さない — task 自身が訳かもしれない。"
  (setv phase status.phase detail status.detail)
  (cond
    (and (= phase "finished") (is-not status.result None))
      (task-finished task now detail status.result)
    (and (= phase "finished") stopping)
      (gone-detached task now (.format "止まり始めた担い手の子 process が結果を書かずに終わった({})" detail)
                     (.format "worker {} の世代 {} が止まり始めを名乗った" task.worker task.boot))
    (= phase "finished")
      (end-detached task "lost" now (.format "子 process が結果を書かずに終わった({})" detail))
    (= phase "code-failed") (end-detached task "code-failed" now detail)
    True task))


(defn #^ None put-row [#^ (get TableDraft TaskRecord) draft #^ TaskRecord task]
  "下書きの task の行を task にする。下書きの今の行と同じ object か値の等しい行なら書かない(値が等しいだけで object の違う行は元の object のまま
   — 書きの列が変わった行だけになる・Q3)。"
  (setv old (.row draft task.id))
  (when (not (or (is task old) (= task old)))
    (.put draft task.id task)))


(defn #^ None put-gone [#^ (get TableDraft TaskRecord) draft #^ TaskRecord before #^ TaskRecord after]
  "下書きの task の行 before を after にする。走らせ直しで id が替わった時(gone-detached)は前の行を消す。"
  (when (!= after.id before.id)
    (.remove draft before.id))
  (put-row draft after))


(defn #^ None absorb-task-reports-into [#^ (get TableDraft TaskRecord) draft #^ str worker #^ tuple statuses #^ int now #^ (| str None) boot
                                        #^ bool stopping]
  "純粋: worker の状態の報告のうち、task の終わりを task の記録へ写す書きを下書き draft に貯める(写す行が無ければ何も書かない)。
   切り離した task は置いた時と同じ process の世代の報告だけ。
   stopping = この報告は今の世代が止まり始めを名乗った heartbeat の物(#2819 — 呼び手は今の世代か新しい世代の heartbeat だけを
   渡す)。その時は、その世代に置いて報告に task/<id> の行が無い task(その世代で一度も走っていない)を置き直しの待ち(queued)へ戻す
   (#2976 の I-3 の赤 R5): 止まり始めた worker は新しい task を始めない(worker/core/program の worker-tick は止まりの拍で宣言を
   空として扱う)ので、残すと lease まで止まる。積みと名乗りが同じ刻に届いた時に受ける順は決まっていない(本番の到着の順)ので、
   名乗りを先に受けた時だけ塞ぐ drain(drain_policy.absorb-stopping)では足りない。避ける worker(avoid)には足さない — 同じ名の
   次の世代には置いてよい。"
  (for [status statuses]
    (setv name status.name)
    (when (.startswith name "task/")
      (setv task (.row draft (cut name 5 None)))
      (when (and task (in task.phase PLACED-PHASES) (= task.worker worker) (same-boot task boot))
        (setv phase status.phase)
        ;; 置いた世代が行を報告した = 子 process を起こした(止まりの報告で行が消えても走らせ直さない — 下の stopping の枝)。
        (when (not task.reported)
          (setv task (replace task :reported True))
          (.put draft task.id task))
        (cond
          (= phase "env-failed") (put-row draft (absorb-env-failure task worker status now))
          task.detached (put-gone draft task (absorb-detached-report task status now stopping))
          (= phase "finished")
            (put-row draft (task-finished task now status.detail status.result))
          (= phase "code-failed")
            (put-row draft (replace task :phase "code-failed" :finished-ms now :detail status.detail))))))
  (when stopping
    (setv running (frozenset (gfor status statuses :if (.startswith status.name "task/") (cut status.name 5 None))))
    (for [id (sorted (.keys draft))]
      (setv task (.row draft id))
      (when (and (in task.phase PLACED-PHASES) (= task.worker worker) (same-boot task boot) (not-in id running))
        (put-gone draft task
                  (if (and task.detached task.reported)
                      ;; 行を報告した後に消えた = 止まり始めた worker が走っていた子 process を止めた。走らせ直しを名乗らない task は lost
                      ;; (置き直すと同じ名の次の世代でもう 1 度走った — 2026-10-10 17:56 の作り直しの後)。
                      (gone-detached task now (.format "担い手の worker {} が止まり始め、走っていた子 process を止めた" worker)
                                     (.format "worker {} の世代 {} が止まり始めを名乗った" worker task.boot))
                      (replace task :phase "queued" :worker None :boot None :started-ms None
                               :placed-memory-available-mib None :placed-measured-at-ms None
                               :detail (.format "担い手の worker {} が始める前に止まり始めた — 置き直しを待つ" worker))))))))


(defn #^ None renew-detached-into [#^ (get TableDraft TaskRecord) draft #^ str worker #^ (| str None) boot #^ int now]
  "純粋: 担い手の heartbeat: その worker に置いた切り離した task の lease を延ばす書きを下書き draft に貯める(lease は worker が延ばす)。
   延ばすのは置いた時と同じ process の世代の heartbeat だけ。別の世代の heartbeat では何もしない — 置いた世代の process が消えていれば
   lease 切れ(settle-detached — lost か、名乗った task は次の試行)。以前は別の世代の heartbeat が来た拍に lost にしていたが、同じ名の 2 つの世代が
   並んで動く間(旧い Pod の preStop の drain)は、旧い世代の heartbeat が新しい世代に置いた task を失わせた。"
  (for [t (.rows draft)]
    (when (and t.detached (in t.phase PLACED-PHASES) (= t.worker worker) (same-boot t boot))
      (put-row draft (replace t :lease-until-ms (+ now t.lease-ms))))))


(defn #^ None promote-prepared-into [#^ (get TableDraft TaskRecord) draft #^ WorkerInfo worker]
  "純粋: worker が env を準備済みと名乗った拍に、その worker の preparing の task を assigned へ進める書きを下書き draft に貯める。"
  (for [t (.rows draft)]
    (when (and (= t.phase "preparing") (= t.worker worker.name) (env-ready-on t.runtime-env worker))
      (put-row draft (replace t :phase "assigned")))))


(defn #^ (get Table TaskRecord) heard-tasks [#^ ClusterState state #^ str worker #^ tuple statuses #^ int now #^ (| str None) boot
                                             #^ bool stopping #^ (| WorkerInfo None) prepared]
  "純粋: worker の heartbeat 1 本が tasks に与える変化(報告の写し・lease の延長・準備済みの進め — prepared が WorkerInfo の時だけ)を、
   1 つの下書きに貯めて 1 度だけ freeze した表。変わる行が無ければ state.tasks を同じ object で返し、変わった行だけ新しい object
   (他の行は元の object — Q3)。下書きはこの関数の中だけで作って freeze する(別の task が状態を置き換える `<-` をまたがない)。"
  (setv draft (draft-of state.tasks))
  (absorb-task-reports-into draft worker statuses now boot stopping)
  (renew-detached-into draft worker boot now)
  (when (is-not prepared None)
    (promote-prepared-into draft prepared))
  (.freeze draft))


(defn #^ (get Table TaskRecord) absorb-task-reports [#^ ClusterState state #^ str worker #^ tuple statuses #^ int now #^ (| str None) [boot None]
                                   #^ bool [stopping False]]
  "worker の状態の報告のうち、task の終わりを task の記録へ写した表(absorb-task-reports-into の 1 本だけの freeze)。
   写す行が無ければ state.tasks を同じ object で返し、写した行だけ新しい object にする(他の行は元の object — Q3)。"
  (setv draft (draft-of state.tasks))
  (absorb-task-reports-into draft worker statuses now boot stopping)
  (.freeze draft))


(defk absorb-task-result [state id body now]
  {:pre [(: state ClusterState) (: id str) (: body TaskResultBody) (: now int)] :post [(: % tuple)]
   :tags {:context "coordinator" :role "judgment"}}
  "POST /tasks/<id>/result: task の子 process が終わる前に直に届けた結果を task の記録へ写す(#1387 — 結果の運び手を worker の
   heartbeat だけにすると、子の exit 0 から次の heartbeat までに worker が死んだ時に結果が届かず、起き直した worker が同じ task を
   もう 1 度走らせた)。本文 = {worker instance result format}(shared/protocol/task_result の task-result-request)。返り値 #(次の状態 status 答え)。
   - 置いた worker からの、まだ終わっていない task の結果 → 結果を持って終える(task-finished)。200。
   - 終わった task(heartbeat が先に運んだ・同じ結果の 2 度目の届け)→ 状態を変えない。200(冪等 — heartbeat の報告も終わった task には
     何もしない: absorb-task-reports)。
   - 別の worker に置いた task → 409(古い送り手)。知らない task(呼び手が落とした・lease 切れ)→ 404。
   切り離した task も置いた worker の名だけで比べる: 子は worker の process の世代を知らず、切り離した task は置いた世代の process にしか
   渡らない(tasks-for)ので、同じ名の worker の子が届ける結果はその task を走らせた process の物。"
  ;; 欄の欠けと型の誤りは本文を解く所(coordinator/protocol/request_bodies)が 400 で断る。ここで見るのは形の版だけ。
  (setv refusal (format-version-refusal body.format))
  (when refusal (return #(state 400 (ErrorReply :message refusal))))
  (setv worker body.worker result body.result)
  (setv task (.row state.tasks id))
  (cond
    (is task None)
      #(state 404 (ErrorReply :message (.format "task {} を知らない(呼び手が落とした・lease が切れた)" id)))
    (not-in task.phase PLACED-PHASES)
      #(state 200 (TaskResultTaken :accepted False :phase task.phase))
    (!= task.worker worker)
      #(state 409 (ErrorReply :message (.format "task {} は worker {} に置いてある(送り手 {})" id task.worker worker)))
    True
      #((replace state :tasks (.with-writes state.tasks #((TableWrite id (task-finished task now (.format "子 process {} が終わる前に届けた"
                                                                                                         body.instance) result)))))
        200 (TaskResultTaken :accepted True :phase "finished"))))


(defn #^ (get Table TaskRecord) renew-detached [#^ (get Table TaskRecord) tasks #^ str worker #^ (| str None) boot #^ int now]
  "担い手の heartbeat: その worker に置いた切り離した task の lease を延ばした表(renew-detached-into の 1 本だけの freeze)。
   延ばす行が無ければ同じ object(担わない worker の heartbeat — Q3)。"
  (setv draft (draft-of tasks))
  (renew-detached-into draft worker boot now)
  (.freeze draft))


;; --- 1 拍の調停 ------------------------------------------------------------------------

(defk sweep-board [state now]
  {:pre [(: state ClusterState) (: now int)] :post [(: % ClusterState)] :tags {:context "coordinator" :role "judgment"}}
  "純粋: 期限を過ぎた盤の行を消した状態。期限つきの行が無い・期限の下限(BoardTally.floor)がまだ来ていなければ、盤の全行を読まずに
   同じ object を返す。下限が来たら全行を読み、期限の来た行を消して、下限を残りのいちばん早い期限に求め直す(消す行が無くても —
   次の拍から、求め直した下限が来るまで全行を読まない)。"
  (setv tally (board-tally state))
  (when (or (= tally.expiring 0) (and (is-not tally.floor None) (> tally.floor now)))
    (return state))
  (setv gone (tuple (gfor #(k row) (.items state.board) :if (and (is-not row.expires-ms None) (<= row.expires-ms now)) k))
        swept (if gone (board-written state (tuple (gfor k gone (TableWrite k None)))) state)
        kept (board-tally swept))
  (replace swept :board-tally (replace kept :floor (expiry-floor-of swept.board))))


(defk forget-silent-workers [state now timing]
  {:pre [(: state ClusterState) (: now int) (: timing ClusterTiming)] :post [(: % ClusterState)] :tags {:context "coordinator" :role "judgment"}}
  "純粋: 設定の忘れる期限(ClusterTiming.worker-forget-ms)より長く沈黙し、置き先も task も持たない worker を忘れた状態(忘れる物が無ければ同じ object)。"
  (setv busy (| (sfor a (+ (list (.rows state.placements)) (list (.rows state.surges))) a.worker)
                ;; 終わって結果を持っているだけの切り離した task は worker を引き留めない。
                (sfor t (scan-rows state.tasks) :if t.worker t.worker))
        gone (lfor #(n w) (.items state.workers) :if (and (> now (liveness-deadline w timing.worker-forget-ms)) (not-in n busy)) n))
  (if (not gone)
      state
      (replace state :workers (.with-writes state.workers (tuple (gfor n gone (TableWrite n None))))
                     :observations (replace state.observations
                                            :statuses (.with-writes state.observations.statuses (tuple (gfor n gone (TableWrite n None)))))
                     :drains (.with-writes state.drains (tuple (gfor n gone (TableWrite n None)))))))


(defk sweep-drains [state now]
  {:pre [(: state ClusterState) (: now int)] :post [(: % ClusterState)] :tags {:context "coordinator" :role "judgment"}}
  "純粋: 期限を過ぎた drain を消した状態(消す物が無ければ同じ object)。頼み手(worker の preStop)は期限の内に頼み直し続ける。"
  (if (all (gfor d (.rows state.drains) (> d.until-ms now)))
      state
      (replace state :drains (.with-writes state.drains (tuple (gfor #(n d) (.items state.drains) :if (<= d.until-ms now) (TableWrite n None)))))))


(defk sweep-warms [state now]
  {:pre [(: state ClusterState) (: now int)] :post [(: % ClusterState)] :tags {:context "coordinator" :role "judgment"}}
  "純粋: 期限を過ぎた温める表の行を消した状態(消す物が無ければ同じ object)。"
  (setv expired (tuple (gfor #(k w) (.items state.warms) :if (<= w.until-ms now) (TableWrite k None))))
  (if (= (len expired) 0)
      state
      (replace state :warms (.with-writes state.warms expired))))


(defk cold-starts-written [tasks writes]
  {:pre [(: tasks (get Table TaskRecord)) (: writes tuple)] :post [(: % int)] :tags {:context "coordinator" :role "judgment"}}
  "待ちから preparing に置かれた task の数を、書きの列 writes(place-tasks の答え)だけから数える(全行を読まない — cold-starts と同じ数)。"
  (len (lfor w writes
             :if (and (is-not w.value None) (= w.value.phase "preparing")
                      (is-not (.row tasks w.key) None) (= (. (.row tasks w.key) phase) "queued"))
             w)))


(defk cold-starts [before after]
  {:pre [(: before (get Table TaskRecord)) (: after (get Table TaskRecord))] :post [(: % int)] :tags {:context "coordinator" :role "judgment"}}
  "待ちから preparing に置かれた task の数(準備済みの worker が無いまま置いた = 冷たい起動)。"
  (len (lfor t (scan-rows after)
             :if (and (= t.phase "preparing") (is-not (.row before t.id) None) (= (. (.row before t.id) phase) "queued"))
             t)))


;; --- 判断の期限(#3060・#3865) ---------------------------------------------------------------------------------------------
;; coordinator が次に起きる刻(wake_policy.next-wake が集める)を知るための関数。
;; どれも期限の答え(intent/due_model — #3865)を返す: DueAt = 状態がこのまま変わらない間に、その判断の答えが変わり得る最初の刻
;; (epoch ms・now より後)・DueNow = まだ落ち着いていない(今判断すれば状態が変わる)・DueNever = 状態がこのままならその判断は何もしない。
;; 刻は判断が比べに使う期限の値から求める(liveness-due = #3061・task-due = #3062・sweep-due = #3063・置いた task と待っている task = #3865)。

(defk liveness-due [state now timing]
  {:pre [(: state ClusterState) (: now int) (: timing ClusterTiming)] :post [(: % (| DueAt DueNow DueNever))] :tags {:context "coordinator" :role "judgment"}}
  "worker の生死の判断(forget-silent-workers・note-liveness・置き先の生死の判定・約束の外し)が、状態がこのままで答えを変え得る最初の刻を
   知るため。どの判断も、最後の連絡 + 窓(liveness-deadline)を今の刻が越えた時に答えを変える — 窓は lease-ms(note-liveness・place-jobs・
   drain・見え方)・reassign-after-ms(held-placements・資源の status)・keep-fence-ms(資源の status の印の柵)・kept-reassign-after-ms
   (sweep-keep-marks — 要求の無い間も、その刻に起きて約束を外す。縛られた担い手への約束は kept-window-ms と、資源の status の長い柵
   pinned-keep-fence-ms — 縛られた担い手の居ない worker にも足す。早く起きるのは構わない)・worker-forget-ms(forget-silent-workers)。答え = worker ごと・窓ごとの
   「期限 + 1 ms」のうち now より後の最小(どれも過ぎていれば DueNever)。生きていないと数える名(silent)が今の刻の求め直しと違えば、
   note-liveness が今の刻で答えを変えるので DueNow。"
  (val windows #(timing.lease-ms timing.reassign-after-ms timing.keep-fence-ms timing.kept-reassign-after-ms
                 timing.pinned-keep-fence-ms (kept-window-ms timing True) timing.worker-forget-ms))
  (val ats (tuple (gfor w (.rows state.workers) window windows
                        :setv due (+ (liveness-deadline w window) 1)
                        :if (> due now)
                        due)))
  (cond
    (!= (silent-names state now timing) state.silent) (DueNow)
    ats (DueAt :at (min ats))
    True (DueNever)))


(defk task-due [state now timing]
  {:pre [(: state ClusterState) (: now int) (: timing ClusterTiming)] :post [(: % (| DueAt DueNow DueNever))] :tags {:context "coordinator" :role "judgment"}}
  "task の判断(place-tasks の lease の切れ・置き直し・切り離した task)が、状態がこのままで答えを変え得る最初の刻を知るため(#3062)。
   刻は place-tasks が比べる期限と同じ関数から求める: 各 task の自分の期限(task-lapse-at)と、置ける worker の無い待っている task の
   待ちの期限(wait-lapse-at)と、置いた切り離していない task の担い手の生死の窓(reassign-after-ms)が切れる刻と、置ける生きた worker の
   在る待っている task のその worker の生死の窓(lease-ms)が切れる刻の最小(#3865)。どれかが now 以前なら DueNow(place-tasks が今の刻で
   変える)・どの task も期限を持たなければ DueNever。"
  ;; 結果を保持しているだけの行(切り離した終わり)は走査せず、保持側の最小期限 1 値だけを期限の列に入れる。
  ;; 終わった刻を持たない保持の行は毎拍 now から数えて動くので入れない(place-tasks も落とさない)。
  (<- split tuple (task-split state.tasks now))
  (setv #(scanned earliest) split)
  (setv dues (if (is earliest None) [] [earliest]))
  ;; 能力と版の合う worker は、置き方の判断が読む欄の同じ待っている task で同じ(placement-row の行 → worker の列)— 待っている task の数 ×
  ;; worker の数の比べを、入力の数 × worker の数にする(2026-11-12 — place-tasks の判断を借りる直しと同じ時)。
  (val capables {})
  (for [task (.values scanned)]
    (<- lapse (| int None) (task-lapse-at task now))
    (val capable (if (= task.phase "queued")
                     (do (val shape (placement-row task))
                         (when (not-in shape capables)
                           (setv (get capables shape) (lfor w (.rows state.workers) :if (can-run-task task w) w)))
                         (get capables shape))
                     []))
    (val live (lfor w capable :if (alive now w timing.lease-ms) w))
    (<- waited (| int None) (wait-lapse-at capable timing))
    (val tried (match task
                 ;; 置いた切り離していない task は、担い手が reassign-after-ms の窓の外に出た刻に失敗にする(place-tasks と同じ比べ)。
                 ;; 担い手が状態に居なければ、place-tasks が今の刻で失敗にする。
                 (TaskRecord :detached False :phase phase) :if (in phase PLACED-PHASES)
                   (if (is-not (.row state.workers task.worker) None)
                       (+ (liveness-deadline (.row state.workers task.worker) timing.reassign-after-ms) 1)
                       now)
                 ;; 待っている task: 置ける生きた worker が在れば、その置き先の候補が変わるのは worker が lease-ms の窓の外に出る刻
                 ;; (空き・drain の終わりは要求と sweep-due が受ける)。置ける worker が 1 台も無ければ place-tasks が今の刻で終える。
                 ;; 生きた worker が無ければ待ちの期限。
                 (TaskRecord :phase "queued")
                   (cond
                     live (min (gfor w live (+ (liveness-deadline w timing.lease-ms) 1)))
                     (is waited None) now
                     True waited)
                 _ None))
    (.extend dues (gfor due [lapse tried] :if (is-not due None) due)))
  (<- answer (| DueAt DueNow DueNever) (due-of-instants now (tuple dues)))
  answer)


(defk sweep-due [state now timing]
  {:pre [(: state ClusterState) (: now int) (: timing ClusterTiming)] :post [(: % (| DueAt DueNow DueNever))] :tags {:context "coordinator" :role "judgment"}}
  "掃除の判断(盤の行・止まった退いた世代の担い手・drain・温める表・詰めた Program)が、状態がこのままで答えを変え得る最初の刻を知るため
   (#3063)。値は各判断が比べに使う期限と同じ: sweep-board = 行の expires-ms・sweep-stopped-generations = 退いた世代の沈黙の窓
   (retired-silence-deadline)を越える刻・sweep-drains(と advance-drains の drain 中の判定)= until-ms・sweep-warms = until-ms・
   program_policy.sweep-programs = 参照の無い Program の put-ms + PROGRAM-GRACE-MS を過ぎた刻。途絶しても動かし続けてよい印の約束を
   外す sweep-keep-marks の刻は担い手の沈黙の期限(kept-reassign-after-ms)なので liveness-due が求める・冷えた起動の数は数えるだけなので、
   刻を持たない。"
  (val used (if (> (.size state.programs) 0) (program-refs state) (frozenset)))
  ;; 盤は期限の下限だけを入れる(全行を読まない — 下限が来ると sweep-board が全行を読んで求め直す。早く起きるのは構わない)。
  (val tally (board-tally state))
  (val deadlines (+ (cond (= tally.expiring 0) [] (is tally.floor None) [now] True [tally.floor])
                    (lfor r (.rows state.observations.retired-statuses) (+ (retired-silence-deadline r timing.lease-ms) 1))
                    (lfor d (.rows state.drains) d.until-ms)
                    (lfor w (.rows state.warms) w.until-ms)
                    (lfor #(sha row) (.items state.programs) :if (not-in sha used) (+ row.put-ms PROGRAM-GRACE-MS 1))))
  ;; 掃いた後の状態なら期限はどれも now より後。掃く前の状態(期限が now 以前)は DueNow(sweep が今の刻で変える)。
  (<- answer (| DueAt DueNow DueNever) (due-of-instants now (tuple deadlines)))
  answer)


(defk reconcile [now given timing]
  {:pre [(: now int) (: given ClusterState) (: timing ClusterTiming)] :post [(: % ClusterState)] :tags {:context "coordinator" :role "judgment"}}
  "1 拍の調停: 期限を過ぎた物(盤の行・lease の窓を越えて黙った退いた世代の process の担い手・drain・温める表の行・沈黙した worker・
   消えた Worker と沈黙が約束の期限を越えた担い手への約束)を掃いてから、job と task の置き先を決め直し、置き先の変化を出来事の列に
   足した状態を求めるため。何も変わらなければ掃いた後の状態そのものを返す。"
  ;; 消された・忘れた Worker と、沈黙が約束の期限(kept-reassign-after-ms)を越えた担い手への途絶しても動かし続けてよい印の約束は、
  ;; 置き先の判断の前に外す(#2804 — その job を他へ置ける)。
  (<- swept ClusterState (sweep-stopped-generations (! (sweep-board given now)) now timing))
  (<- state ClusterState
      (sweep-keep-marks (! (forget-silent-workers (! (sweep-warms (! (sweep-drains swept now)) now)) now timing))
                        now timing))
  ;; 変わらない割り当てと task は元の object のまま引き継ぎ、何も変わらなければ状態そのものを返す(2026-09-29・#1356):
  ;; 版を付ける stamp は同じ object なら資源の写し(snapshot)を作らずに返す。以前は毎拍作り直した dict を返したので、変化の無い
  ;; 1 秒ごとの拍でも写しを 2 つ作って比べていた(模擬の仮想 1700 秒で約 2,000 回)。
  (setv before state.placements
        placed (! (place-jobs now state timing))
        after (.with-writes before placed)
        task-changes (! (place-tasks now state after timing))
        tasks (.with-writes state.tasks task-changes))
  (when (and (is after before) (is tasks state.tasks))
    (return state))
  (setv events (list state.events))
  (for [write placed]
    (setv name write.key old (.row before name) new write.value)
    (.append events {"at" now "job" name
                     "from" (if old old.worker None) "to" (if new new.worker None)
                     "generation" (if new new.generation None)}))
  (replace state :placements after :tasks tasks :events (tuple (cut events (- MAX-EVENTS) None))
                 :env-cold-starts (+ state.env-cold-starts (! (cold-starts-written state.tasks task-changes)))))


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
      (!= before.keep-marks after.keep-marks)
      (!= (set (.keys before.workers)) (set (.keys after.workers)))
      (any (gfor #(n w) (.items after.workers)
                 :setv b (.row before.workers n)
                 (or (is b None) (!= #(b.provides b.exclusive b.derived b.node b.versions b.boot b.retired b.boot-at b.known-exits)
                                     #(w.provides w.exclusive w.derived w.node w.versions w.boot w.retired w.boot-at w.known-exits)))))))


(defn #^ list board-changes [#^ ClusterState before #^ ClusterState after]
  "書き直しが要る盤の行の鍵(書かれた・消えた・中身の変わった行 — 鍵の昇順)。盤の表の changed-keys が名乗る行だけ: 基を分け合う
   前後の盤(1 行を書いた歩)は差分と消した鍵の行だけを見るので、盤の全行を回らない(行の数に比例せず、書きの数に比例する — 全行を
   回っていた時は 2,057 行の盤の 1 行の書きで 2.3 ms)。変わらない行は同じ object のまま引き継がれるので同一性を先に見て、違う
   object だけ値で比べる(盤全体を値で比べない — 大きな shadow の盤は 2.3 MB)。作り直したが中身の同じ行は名乗らない
   (durable_kv.durable-delta の「前と後を丸ごと耐久の形にして比べた答え」と同じ)。"
  (list (.changed-keys before.board after.board)))


;; --- HTTP の要求への返事(判断の部品。要求の振り分けは api_policy) -----------------------------------

(deff text-map? [#^ object value]  ; cannot be a defk: HTTP の本文を読む境界(Program の外)が呼ぶ純粋な判断
  {:pre [(: value "JSON の値")] :post [(: % bool)] :tags {:context "coordinator" :role "judgment"}}
  "JSON の値が「名 → 文字列」の object か — heartbeat の versions・tools(component-versions-of が名の順に並べる)を写す前に確かめるため。"
  (and (isinstance value dict) (all (gfor #(k v) (.items value) (and (isinstance k str) (isinstance v str))))))


;; --- 最後に終わったと知れた刻(lastExitAtMs — #3672) ------------------------------------------------------------------
;; 使い手は担い手の行の lastExitAtMs が進んだ事で、担い手の上の process が終わったと知る。worker の機体が丸ごと死んで新しい世代
;; (heartbeat の bootAt が新しい)で戻ると、前の世代の process は終わっているのに、新しい世代はその終わりを知らない(記憶ごと消えた)。
;; 以前は heartbeat ごとに行を丸ごと置き換えたので、新しい世代の行(lastExitAtMs 無し)が前の値を消し、刻は進まなかった。
;; 新しい世代の最初の heartbeat は行を載せない(worker は返事の宣言を受けてから行を作る — 本番の LinkState.statuses も sim の宿の
;; 真実も初めは空)ので、数えた刻は宣言の在る job の間は行の外(WorkerInfo.known-exits)に持ち、次に載る同じ名の行へ運ぶ。
;; 状態の報告(ClusterObservations.statuses)は保存しないが、known-exits は worker/<名> の行に保存する: 機体が丸ごと死ぬと同じ機体の coordinator も
;; 一緒に作り直されうるので、作り直しの後に最初に来る新しい世代の heartbeat でも、前の世代で process を持っていた job を数え、進めた刻を
;; 戻さない(以前は報告ごと失い、刻は進まず・None へ戻った)。
;; 世代が重なる時(旧い Pod が preStop の drain の間も process を動かし、新旧の世代が交互に heartbeat を送る — 上の 2026-09-27 の註)は、
;; 退いた世代の process の実の終わりが新しい世代の起動の刻より後になる。退いた世代(OLDER)の報告の行の終わりの刻も大きい方の候補に入れ、
;; 実の終わりが届けばその刻を載せる(起動の刻を上限と言えるのは機体が死んで世代が入れ替わった時だけ)。

(defk known-exits-after [previous rows order boot-at declared]
  {:pre [(: previous (| WorkerInfo None)) (: rows tuple) (: order GenerationOrder) (: boot-at (| int None)) (: declared frozenset)]
   :post [(: % tuple)] :tags {:context "coordinator" :role "judgment"}}
  "worker の heartbeat 1 つ(世代の比べ order・起動の刻 boot-at・状態の報告の行 rows)を受けた後の、宣言の在る job(declared)ごとの最後に
   終わったと知れた刻と、今の世代の報告で process を持つか(WorkerInfo.known-exits の新しい値)を求めるため。前の値は previous の
   known-exits。刻は名ごとに候補の大きい方 = 前に知っていた刻・この報告の行の刻(退いた世代の行も — 世代が重なる時の実の終わり)・
   新しい世代(NEWER)なら前の世代で process を持っていた job の新しい世代の起動の刻(実の終わりはそれ以前)。process を持っていなかった
   job(起動の前)と、起動の刻を名乗らない世代では進めない(推測で刻を作らない)。process を持つかは、今の世代か新しい世代の報告なら
   行の pid、退いた世代の報告なら前のまま(退いた世代の process は今の世代の物でない)。宣言の無い名(task・消した Service)は持たない。"
  (val prior (if (is previous None) #() previous.known-exits))
  (val holding (if (= order GenerationOrder.OLDER)
                   (lfor e prior :if e.has-process (KnownExit :job e.job :at-ms None :has-process True))
                   (lfor row rows :if (is-not row.pid None) (KnownExit :job row.name :at-ms None :has-process True))))
  (val ended (if (and (= order GenerationOrder.NEWER) (is-not boot-at None))
                 (lfor e prior :if e.has-process (KnownExit :job e.job :at-ms boot-at :has-process False))
                 []))
  (val noted (sorted (gfor e (+ (lfor e prior :if (is-not e.at-ms None) (KnownExit :job e.job :at-ms e.at-ms :has-process False))
                                (lfor row rows :if (is-not row.last-exit-at-ms None)
                                      (KnownExit :job row.name :at-ms row.last-exit-at-ms :has-process False))
                                ended
                                holding)
                           :if (in e.job declared)
                           e)
                     :key (attrgetter "job")))
  (tuple (gfor #(job group) (groupby noted :key (attrgetter "job"))
               :setv members (tuple group)
               :setv times (lfor e members :if (is-not e.at-ms None) e.at-ms)
               (KnownExit :job job :at-ms (if times (max times) None) :has-process (any (gfor e members e.has-process))))))


(defk rows-with-known-exits [rows known]
  {:pre [(: rows tuple) (: known tuple)] :post [(: % tuple)] :tags {:context "coordinator" :role "judgment"}}
  "状態の報告の行 rows の last-exit-at-ms を、行の刻と知れた刻(known — known-exits-after)の大きい方にするため(宣言の無い名の行は
   行の刻のまま)。刻が変わらない行は同じ値のまま運ぶ(heartbeat ごとに全部の行を作り直さない — #3774)。"
  (tuple (gfor row rows
               :setv times (+ (if (is row.last-exit-at-ms None) [] [row.last-exit-at-ms])
                              (lfor e known :if (and (= e.job row.name) (is-not e.at-ms None)) e.at-ms))
               :setv at (if times (max times) None)
               (if (= at row.last-exit-at-ms) row (replace row :last-exit-at-ms at)))))


(defk worker-report [rows now endpoint known [pod-ip ""]]
  {:pre [(: rows tuple) (: now int) (: endpoint (| str None)) (: known tuple) (: pod-ip str)]
   :post [(: % WorkerReport)] :tags {:context "coordinator" :role "judgment"}}
  "今の世代か新しい世代の heartbeat の状態の報告の行 rows から、worker の最新の報告(ClusterObservations.statuses の行)を作るため。行は結果の
   欄 result と task の写しを外し(持ち続けるのは process の姿だけ)、last-exit-at-ms を最後に終わったと知れた刻にする(known =
   この heartbeat の後の WorkerInfo.known-exits — 同じ worker・同じ job の名では前に知っていた刻から戻さない)。result も task も
   持たない行(service の job の行)は同じ値のまま運ぶ(#3774)。pod-ip = その世代の Pod の IP(heartbeat の podIp)。"
  (WorkerReport :at now :endpoint endpoint :pod-ip pod-ip
                :jobs (! (rows-with-known-exits (tuple (gfor row rows
                                                             (if (and (is row.result None) (is row.task None))
                                                                 row
                                                                 (replace row :result None :task None))))
                                                known))))


(defk register-heartbeat [given body now]
  {:pre [(: given ClusterState) (: body HeartbeatBody) (: now int)] :post [(: % ClusterState)] :tags {:context "coordinator" :role "judgment"}}
  "heartbeat の中身(worker の能力・容量・版と、各 job / task の状態)を状態へ写す。割り当ての調停はしない(呼び手が別の送り手
   = coordinator として調停する)。古い世代の heartbeat(generation-order が OLDER)は名乗りとして受けず、その世代を退いた世代の
   列に載せ、その世代に置いた task の終わりの報告と lease の延長だけを写す(absorb-superseded-heartbeat)。
   知らない切り離した task をその process が走らせていれば、先に引き取る(adopt-running-detached — 状態を失った coordinator)。
   job の行は worker-report が持ち続ける形にする(最後に終わったと知れた刻を前の報告から戻さず、新しい世代の heartbeat では前の世代の
   process の終わりを新しい世代の起動の刻までと数える — #3672)。知れた刻と今の世代で process を持つ job は WorkerInfo.known-exits に
   持つ(known-exits-after — 保存する)。退いた世代の heartbeat は、その行の終わりの刻だけを知れた刻の候補に入れる(世代が重なる時)。
   本文の形の誤りは本文を解く所(coordinator/protocol/request_bodies — #2445)が 400 で断る。"
  (setv name body.name boot body.boot boot-at body.boot-at statuses body.statuses
        previous (.row given.workers name)
        order (generation-order previous boot boot-at)
        adopted (adopt-running-detached given name boot statuses now)
        declared (frozenset (gfor j adopted.jobs j.spec.name))
        known (! (known-exits-after previous statuses order boot-at declared)))
  (when (= order GenerationOrder.OLDER)
    ;; OLDER は今の世代と boot の両方が在る時だけ(generation-order の最初の枝が、どちらかの無い時を CURRENT にする)。
    (when (or (is previous None) (is boot None))
      (raise (RuntimeError (.format "世代の比べが OLDER なのに今の世代か boot が無い: {}" name))))
    ;; 退いた世代の行の終わりの刻で知れた刻が進んだ時だけ、今の世代の報告の行へも写す(進まなければ報告は同じ object のまま)。
    (setv current-report (.row adopted.observations.statuses name))
    (return (absorb-superseded-heartbeat
              (replace adopted
                       :workers (.with-writes adopted.workers #((TableWrite name (replace previous :retired (retired-with previous.retired boot) :known-exits known))))
                       :observations (if (or (is current-report None) (= known previous.known-exits))
                                         adopted.observations
                                         (replace adopted.observations
                                                  :statuses (.with-writes adopted.observations.statuses
                                                                          #((TableWrite name (replace current-report
                                                                                                      :jobs (! (rows-with-known-exits current-report.jobs known)))))))))
              name boot statuses now
              ;; 退いた世代の報告も今の世代の報告と同じ形で持つ(受け渡しの間、job を今動かしている process の行 — elder-statuses-after)。
              (! (worker-report statuses now body.endpoint known body.pod-ip)))))
  (setv envs (or body.envs (EnvsReport))
        caps (named-capabilities body.provides body.exclusive (is-not body.labels None) (.format "worker {} の名乗り" name))
        node body.node
        info (WorkerInfo name (tuple (gfor c (get caps 0) :if (not-in c adopted.derivable) c))
                         now
                         (component-versions-of (or body.versions {}))
                         boot
                         (component-versions-of (or body.tools {}))
                         :platform body.platform
                         :env-ready (frozenset envs.ready)
                         :env-preparing (frozenset envs.preparing)
                         :env-failed (tuple (gfor f envs.failed (EnvFailed f.key f.kind f.detail f.retryable)))
                         :env-memory-unmeasured (frozenset envs.memory-unmeasured)
                         :env-ready-deps (frozenset envs.ready-deps)
                         :env-capacity body.env-capacity
                         :account-token body.account-token
                         :room-file body.room-file
                         :doeff-commit body.doeff-commit
                         :memory-mib body.memory-MiB
                         :measured body.measured
                         :child-environ (if (is body.child-environ None) None (tuple (sorted body.child-environ)))
                         :prepare body.prepare
                         :retired (retired-after previous boot)
                         ;; 同じ世代が起動時刻を名乗らなくなっても(版を戻した worker)、知っている起動時刻は捨てない。
                         :boot-at (if (and (is boot-at None) (= order GenerationOrder.CURRENT) (is-not previous None)
                                           (= previous.boot boot))
                                      previous.boot-at
                                      boot-at)
                         :exclusive (get caps 1)
                         ;; node の label から導いた能力は、同じ node の間だけ前の観測を引き継ぐ(次の調停で読み直す)。
                         :node node
                         :derived (if (and (is-not previous None) (= previous.node node)) previous.derived #())
                         ;; 生存の印(保存の lastSeenMs)は印の拍(api_policy.mark-alive)だけが進める — heartbeat は前の印を運ぶ(運ばないと
                         ;; heartbeat ごとに worker/<名> の行から lastSeenMs が消え、書きが印の拍ごとでなくなる・#2903)。世代を問わない
                         ;; (#2903 の前の ClusterState.seen-marks も名ごとで、heartbeat が触らなかった)。
                         :seen-mark (if (is previous None) None previous.seen-mark)
                         ;; 最後に終わったと知れた刻と今の世代で process を持つ job(#3672 — 保存の行の knownExits)。
                         :known-exits known)
        ;; job の行の最後に終わったと知れた刻は、known-exits-after が前の知れた刻と世代の比べから数えた known(worker-report — #3672)。
        report (! (worker-report statuses now body.endpoint known body.pod-ip))
        ;; 新しい世代の名乗りで、前の世代の process が持っていた名前付きの lease を外す(#3770 — 前の世代の最新の報告は、この
        ;; heartbeat で置き換える前の adopted.observations.statuses の行)。前の世代がまだ走っている間に起きた世代(overlaps-elder — RollingUpdate・
        ;; AL-D2e)は外さない: 前の世代の process は走り続け、延長を通し、終わる時に返すか期限で戻す。前の世代が実は止まっていれば、
        ;; 黙った窓の後に sweep-stopped-generations が外す(その材料に、前の世代の最新の報告を退いた世代の報告として置く — 刻はその世代から
        ;; 最後に聞いた刻のまま)。
        previous-report (.row adopted.observations.statuses name)
        freed (if (and (= order GenerationOrder.NEWER) (not body.overlaps-elder))
                  (! (board-without-generation-holders adopted.board previous-report))
                  #())
        elder (and (= order GenerationOrder.NEWER) body.overlaps-elder (is-not previous None) (is-not previous.boot None)
                   (is-not previous-report None))
        board-state (board-written adopted freed)
        ;; absorb-boot は drain だけを変えるので、観測は adopted のまま書いてよい。
        registered (replace (absorb-boot adopted name boot)
                     :workers (.with-writes adopted.workers #((TableWrite name info)))
                     :observations (replace adopted.observations
                                            :statuses (.with-writes adopted.observations.statuses #((TableWrite name report)))
                                            :retired-statuses (if elder
                                                                  (.with-writes adopted.observations.retired-statuses
                                                                                #((TableWrite (elder-key name previous.boot) previous-report)))
                                                                  adopted.observations.retired-statuses))
                     :board board-state.board :board-tally board-state.board-tally)
        ;; 前の世代がまだ走っている間に起きた世代の名乗り(AL-D2f): 前の世代の最新の報告の running の常駐の job を、前の世代が動かして
        ;; いる組(SupersededRun)の種にする — 前の世代の次の heartbeat を待たずに、今の世代への最初の返事から印 elder-runs を付ける。
        seeded (if elder
                   (elder-runs-written registered name previous.boot previous-report.jobs now
                                       ;; 前の世代の最新の報告を、組の job の行として持ち続ける(刻はこの heartbeat の後の知れた刻)。
                                       (replace previous-report :jobs (! (rows-with-known-exits previous-report.jobs known)))
                                       :overlapped True)
                   registered))
  ;; 今の世代(か新しい世代)の知らせた「今持っている印」で、印を持たなくなった job の約束を外す(#2804 — 退いた世代の heartbeat は
  ;; 上で抜けるので約束に触らない)。止まり始めの名乗り(body.stopping・#2819)は、その世代に置いて始まっていない task を
  ;; 置き直しの待ちへ戻す(absorb-task-reports・#2976 の I-3 の赤 R5)— 退いた世代の heartbeat はここへ来ないので戻さない。
  (replace seeded :tasks (! (task-peaks-noted (heard-tasks seeded name statuses now boot body.stopping info) name body.measured))
                      :keep-marks (! (released-keep-marks seeded.keep-marks name body.kept-when-cut-off))))


(defn #^ ClusterState absorb-superseded-heartbeat [#^ ClusterState state #^ str name #^ str boot #^ tuple statuses #^ int now
                                                  #^ WorkerReport report]
  "退いた世代の process がまだ走らせている切り離した task を最後まで見届けるため: その世代に置いた task の終わりの報告と
   lease の延長だけを写す。worker の名乗り(生存・label・容量・版・世代)・job の状態の報告・drain は今の世代の物なので触らない。
   その世代が走らせている入れ替えの job の組(SupersededRun — 旧い Pod の preStop が待つ・AL-D2)と、組の job の行(report — この
   heartbeat の報告を worker-report の形にした物)も書き直す(elder-runs-written)。report は止まりの判じの材料(退いた世代から最後に
   受けた報告 — ClusterObservations.retired-statuses)にも置き直す(sweep-stopped-generations — 報告を続ける間は止まったと判じない)。"
  (setv heard (replace state :tasks (heard-tasks state name statuses now boot False None)
                             :observations (replace state.observations
                                                    :retired-statuses (.with-writes state.observations.retired-statuses
                                                                                    #((TableWrite (elder-key name boot) report))))))
  (elder-runs-written heard name boot statuses now report))


(defn #^ ClusterState elder-runs-written [#^ ClusterState state #^ str name #^ str boot #^ tuple statuses #^ int now
                                          #^ WorkerReport report #^ (| bool None) [overlapped None]]
  ;; cannot be a defk: coordinator の純粋な判断(absorb-superseded-heartbeat — defn)が呼ぶ
  "純粋: 退いた世代 boot の報告(statuses と、それを worker-report の形にした report)の後の、退いた世代の組の列(superseded-runs-after)と
   その組の job の行(ClusterObservations.elder-statuses — elder-statuses-after)を一緒に書くため。組と行を同じ歩で書くので、行は組が
   job を持つ間だけ残る。"
  (setv runs (superseded-runs-after state name boot statuses now :overlapped overlapped))
  (replace state :superseded-runs runs
                 :observations (replace state.observations
                                        :elder-statuses (elder-statuses-after state.observations.elder-statuses runs name boot report))))


(defn #^ str elder-key [#^ str name #^ str boot]
  ;; cannot be a defk: coordinator の純粋な判断(elder-rows の内包表記・elder-statuses-after — defn)が呼ぶ
  "退いた世代の報告の表(ClusterObservations.elder-statuses)の鍵 — 保存の superseded/<名>/<世代> と同じ組。"
  (+ name "/" boot))


(defn #^ Table elder-statuses-after [#^ Table elders #^ tuple runs #^ str name #^ str boot #^ WorkerReport report]
  ;; cannot be a defk: coordinator の純粋な判断(elder-runs-written — defn)が呼ぶ
  "純粋: 組の列 runs を書いた後の、退いた世代の報告の表 elders(世代の受け渡しの間、job を今動かしている process の行を Service の
   観測に出すため — resource_policy.carrying-row と service-rows)。名 name の世代 boot の行を、report のうち組が持つ job の行(と、
   その job から入れ替えで退いた process の行)だけにして置き直し、組の無い鍵の行を外す(覚える数は組の数と同じ)。同じ値の行は
   書き直さない(版の比べの材料が heartbeat ごとに動かないように)。"
  (setv run (next (gfor r runs :if (and (= r.worker name) (= r.boot boot)) r) None)
        key (elder-key name boot)
        live (frozenset (gfor r runs (elder-key r.worker r.boot)))
        kept (if (is run None)
                 None
                 (replace report :jobs (tuple (gfor row report.jobs :if (in (or row.retired-from row.name) run.jobs) row))))
        prior (.row elders key)
        ;; 報告の刻 at は読まない(行の新しさは組の seen-ms で数える)ので、行が同じなら前の行のまま。
        writes (+ (if (or (is kept None) (and (is-not prior None) (= prior.jobs kept.jobs) (= prior.endpoint kept.endpoint)))
                      #()
                      #((TableWrite key kept)))
                  (tuple (gfor k (.keys elders) :if (not-in k live) (TableWrite k None)))))
  (if writes (.with-writes elders writes) elders))


(defn #^ tuple superseded-runs-after [#^ ClusterState state #^ str name #^ str boot #^ tuple statuses #^ int now
                                      #^ (| bool None) [overlapped None]]
  ;; cannot be a defk: coordinator の純粋な判断(absorb-superseded-heartbeat — defn)が呼ぶ
  "純粋: 退いた世代 boot の報告 statuses の後の、退いた世代の走らせている常駐の job の組の列(AL-D2)。この世代の行を、報告の
   running の job の名で置き直す(空なら外す)。数える job は handoff の job、組が overlapped(新しい世代が重なりを名乗った — 名乗りの
   時に overlapped = 真で種を置く・それ以外の報告は前の組の印を引き継ぐ)なら recreate の job も(task は除く)— recreate の job は
   新しい世代が起こせると報告するまで退いた世代に残す(AL-D2f — heartbeat-reply の印 elder-runs と superseded-reply)。recreate の job は
   止めの途中(stopping・stop-unconfirmed — 前の世代の process がまだ刈られていない)の行も数える(holding-names)。名 name の行の
   うち、もう退いた世代の列(WorkerInfo.retired)に無い世代の行も外す(覚えの上限 RETIRED-BOOTS-KEPT と同じ長さで捨てる)。"
  (setv info (.row state.workers name)
        kept (if (is info None) #() info.retired)
        prior (next (gfor r state.superseded-runs :if (and (= r.worker name) (= r.boot boot)) r) None)
        overlap (if (is overlapped None) (and (is-not prior None) prior.overlapped) overlapped)
        ;; 止まりの始まりの印(AL-D2k)は前の組から引き継ぐ(drain_policy.request-drain が付ける)。
        leaving (and (is-not prior None) prior.leaving)
        ;; 組を初めて書いた刻(受け渡しの待ちの始まり)も引き継ぐ。欄の前に保存した組は、次の報告の刻から数える。
        since (if (and (is-not prior None) (is-not prior.since-ms None)) prior.since-ms now)
        counted (frozenset (gfor j state.jobs :if (if overlap (not j.spec.once) j.spec.handoff) j.spec.name))
        ;; 止めの途中の recreate の job も前の世代が持っている(11-07 06:42 JST の node-c — running の行だけを数えると、止めの合図の直後に
        ;; 新しい世代の印 elder-runs が外れ、前の世代の process が持ったままの port の bind で新しい process が落ちた)。
        recreated (frozenset (gfor j state.jobs :if (and overlap (not j.spec.once) (not j.spec.handoff)) j.spec.name))
        jobs (tuple (sorted (| (& (running-names statuses) counted) (& (holding-names statuses) recreated))))
        others (tuple (gfor r state.superseded-runs
                            :if (not (and (= r.worker name) (or (= r.boot boot) (not-in r.boot kept))))
                            r)))
  (tuple (sorted (+ others (if jobs #((SupersededRun :worker name :boot boot :jobs jobs :seen-ms now :overlapped overlap :leaving leaving
                                                           :since-ms since))
                           #()))
                 :key (fn [r] #(r.worker r.boot)))))


;; --- 状態を失った coordinator が、走っている切り離した task を止めさせない(2026-09-27) --------------------------
;; worker は heartbeat の返事に載らない task の子 process を止め(worker_policy.plan-job — 宣言から消えた job)、その結果の file と
;; Program の cache を消す(coordinator への口の accepted-tasks・fetched-programs)。置き場を失った coordinator は task の行を持たないので、最初の返事で
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


(defn #^ (| TaskRecord None) adopted-task [#^ ClusterState state #^ str worker #^ (| str None) boot #^ StatusRow status #^ int now]
  "純粋: 状態の報告 1 行 → 引き取る切り離した task の行(引き取らない時は None)。"
  (setv row-name status.name echo status.task)
  (when (or (not (.startswith row-name "task/")) (is echo None)
            (not-in status.phase ADOPTABLE-PHASES))
    (return None))
  (setv id (cut row-name 5 None) key (.get echo "key") lease-ms (.get echo "leaseMs")
        revision (.get echo "revision") needs (.get echo "needs" []) program (.get echo "program")
        environ (.get echo "environ" {})
        ;; memory の要り(2026-11-02 — 引き取った後も置き先が予約を続ける。形の違う値は名乗らないと読む)。
        memory (.get echo "memoryMiB"))
  ;; 写しの program(置き場のキー)が無い・形の違う報告(blob を運んでいた旧い worker)は引き取らない。引き取った行は同じ sha を
  ;; 参照する(担い手は cache の file を持っているので、状態を失った置き場に Program が無くても走り続ける)。
  (when (or (is-not (.row state.tasks id) None) (!= (.get echo "id") id) (not (.get echo "detached"))
            (not (isinstance key str)) (not (isinstance lease-ms int))
            (not (isinstance revision str)) (not (isinstance needs list))
            (not (and (isinstance program str) (PROGRAM-SHA.fullmatch program)))
            (not (isinstance environ dict)) (not (text-map? (.get echo "versions" {})))
            (any (gfor t (.rows state.tasks) (= t.key key))))
    (return None))
  (TaskRecord id (.get echo "name" "") program revision
              (component-versions-of (.get echo "versions" {})) (capabilities-of needs "引き取る task の needs") lease-ms (+ now lease-ms) now
              :phase "assigned" :worker worker :started-ms now :detached True :key key :boot boot
              :retain-ms (int-field echo "retainMs" 0) :runtime-env (.get echo "runtimeEnv") :environ (environ-pairs environ)
              :memory-mib (if (is (task-memory-refusal memory) None) memory None)
              :rerun (is (.get echo "rerun") True) :attempt (int-field echo "attempt" 1)
              ;; 上限に数えた消失の数を運ばない写し(2026-11-05 の前の worker)は、前の試行を全部数えた。
              :losses (int-field echo "losses" (- (int-field echo "attempt" 1) 1))
              :notify (if (isinstance (.get echo "notify") str) (.get echo "notify") "")
              ;; 置き方の優先と場所を譲る名乗り(2026-11-11 — 運ばない写しは優先 0・名乗らない。名乗りは走らせ直しの名乗りと組でだけ読む)。
              :priority (int-field echo "priority" 0)
              :preemptible (and (is (.get echo "preemptible") True) (is (.get echo "rerun") True))
              :detail (.format "coordinator の置き場に行が無く、担い手 {} が走らせていた task を引き取った" worker)))


(defn #^ ClusterState adopt-running-detached [#^ ClusterState state #^ str worker #^ (| str None) boot #^ tuple statuses #^ int now]
  "純粋: heartbeat の状態の報告のうち、行を持たない走っている切り離した task を引き取った状態(引き取る物が無ければ同じ object)。
   次に振る task の番号は引き取った id より後へ進める(同じ id を別の task に振らない)。"
  (setv adopted {})
  (for [status statuses]
    ;; まだ何も引き取っていない間は state そのものを渡す(行ごとに状態を作り直さない — #3774)。
    (setv task (adopted-task (if adopted (replace state :tasks (adopted-over state.tasks adopted)) state) worker boot status now))
    (when (is-not task None) (setv (get adopted task.id) task)))
  (if adopted
      (replace state :tasks (adopted-over state.tasks adopted)
               :next-task (max [state.next-task
                                #* (gfor id adopted :setv n (task-number state.task-prefix id) :if (is-not n None) (+ n 1))]))
      state))


(defn #^ (get Table TaskRecord) adopted-over [#^ (get Table TaskRecord) tasks #^ dict adopted]
  "純粋: 引き取った行(task の id → 行)を tasks へ重ねた表。"
  (.with-writes tasks (tuple (gfor #(id t) (.items adopted) (TableWrite id t)))))


(defn #^ (get Table TaskRecord) promote-prepared [#^ (get Table TaskRecord) tasks #^ WorkerInfo worker]
  "純粋: worker が env を準備済みと名乗った拍に、その worker の preparing の task を assigned へ進めた表(promote-prepared-into の 1 本だけの
   freeze)。進める行が無ければ同じ object(Q3)。"
  (setv draft (draft-of tasks))
  (promote-prepared-into draft worker)
  (.freeze draft))


(defn #^ tuple warms-for [#^ ClusterState state #^ str worker #^ int now]
  "worker に配る温める表の行(期限の内・能力(専用の能力を含む)と宣言の道具が合う行)。heartbeat の返事の warm。
   drain 中の worker(draining-workers — 送り手が読む warm_policy.warm-view が数えない worker と同じ判断)には配らない: 空ける worker に
   新しい版の実行環境の準備を始めさせない(#3669 — 2026-10-05 に drain 中の旧い worker が準備を始めて捨てた)。"
  (setv info (.row state.workers worker))
  (if (or (is info None) (in worker (draining-workers state now)))
      #()
      (tuple (gfor w (sorted (.rows state.warms) :key (fn [w] w.key))
                   :if (and (> w.until-ms now) (placeable w.needs info)
                            (tools-cover w.runtime-env info))
                   (WarmOffer :key w.key :runtime-env w.runtime-env :urgent (> w.urgent-until-ms now))))))


(defk supplied-for [state worker]
  {:pre [(: state ClusterState) (: worker str)] :post [(: % (| (get tuple #(str ...)) None))] :tags {:context "coordinator" :role "judgment"}}
  "worker に置かれうる job(置く対象の宣言のうち、pin と効く needs が合う物 — eligible。生死・drain・空きは見ない: 今は置けなくても、
   ほかの worker が止まれば移って来る)の宣言の :environ の、ConfigMap・Secret の参照の綴りを、重ねずに綴りの順に並べるため(heartbeat の
   返事の supplied — worker は API server が答える間に値の写しを持ち、止まった間に移って来た job が起動できる・ADR-DOE-CLUSTER-004)。
   名乗っていない worker には None(渡さない — 空の列を渡すと、その worker は写しを全部消す)。"
  (setv info (.row state.workers worker))
  (when (is info None)
    (return None))
  (var found (frozenset))
  (for [job (active-jobs state)]
    (when (eligible state job info)
      (<- spelled tuple (supplied-spellings job.spec.environ))
      (:= found (| found (frozenset spelled)))))
  (tuple (sorted found)))


(defk heartbeat-reply [state name timing [ready-instances None] [now 0] [boot None] [statuses None]]
  {:pre [(: state ClusterState) (: name str) (: timing ClusterTiming) (: ready-instances (| dict None)) (: now int) (: boot (| str None))
         (: statuses (| tuple None))]
   :post [(: % HeartbeatReply)] :tags {:context "coordinator" :role "judgment"}}
  "heartbeat を送った process に、動かす job・task・温める表・時間の設定・drain の印を返すため。boot = 送った process の世代・
   statuses = その heartbeat の状態の報告。退いた世代(superseded-boot)への返事は superseded-reply。"
  (when (and (is-not boot None) (superseded-boot state name boot))
    (return (superseded-reply state name boot statuses timing ready-instances)))
  ;; warm = 温める表のうち、この worker に合う行(2026-09-26 — worker は job の準備より低い優先度で準備する)。drain 中の worker には
  ;; 配らない(warms-for — #3669)。
  ;; draining = この worker が drain 中か(2026-09-25): worker は返事ごとに Pod の中の ready の file へ写し、readinessProbe は sh でそれを読む
  ;; (hy を起こす probe は込んだ node で 10 秒の timeout を越え、両方の Pod が同時に NotReady → DaemonSet が 2 台を同時に消した)。
  ;; formats = 受け入れる本文の形の版(2026-09-26 — cluster_model.ACCEPTED-FORMATS)。
  ;; supplied = この worker に置かれうる job の参照の綴り(supplied-for — ADR-DOE-CLUSTER-004)。
  ;; revision = この返事を作った時の coordinator の版(#1933): worker は次の変化を GET /watch?after=<この版> で待つ。
  ;; keepWhenCutOff = 他へ移せない job に付ける「途絶しても動かし続けてよい」印(#2804 — keep-marked)。印を渡した事実は呼び手が
  ;; remember-keep-marks で約束として状態に残す。
  (<- marked frozenset (keep-marked now state name timing))
  ;; elder = 同じ名の前の世代がまだ動かしている常駐の job(AL-D2f)。その recreate の job には印 elder-runs を付ける — worker は準備と
  ;; 入口の検めだけをして起こさず、揃ったら状態の行を elder-runs にする(superseded-reply が前の世代から外す)。
  ;; 黙った前の世代を数える窓は移し替えの期限(reassign-after-ms — 条 C4 timing-outlasts-the-self-stop): 黙った世代は途絶の柵で job を
  ;; 自分で止め始めるが、刈り終えるまでは process が port を持つ。生死の窓(lease-ms)で外すと、両方の世代が coordinator に届かない
  ;; 途絶の後に、新しい世代が前の世代の刈りより先に起こす(11-07 06:42 JST の node-c)。
  (val elder (if (and (is-not boot None) (> now 0)) (elder-held state name boot now timing.reassign-after-ms) (frozenset)))
  ;; 前の世代の job をまだ受け取れない間は、worker が Pod の ready の file を書かない(AL-D2k — handover-pending)。
  (val pending (and (is-not boot None) (> now 0) (handover-pending state name boot now timing ready-instances)))
  ;; 退きの保ち(readiness_model の RETIREMENT-HOLD): 入れ替えの job ごとに、期限の内の保ちの持ち手の世代の名を
  ;; 載せる(worker は列に在る退いた process を寿命の上限で止めない)。退いた世代への返事(superseded-reply)には載せない — その世代の
  ;; 退いた process は Pod の止まりの drain の上限(寿命の上限 + 5 分)の内で終わる。
  (val hold-row (.row state.board (semaphore-key RETIREMENT-HOLD)))
  (<- supplied (| tuple None) (supplied-for state name))
  (var held-specs #())
  (for [spec (jobs-for state name ready-instances)]
    (<- held tuple (live-instances (if (and spec.handoff (is-not hold-row None)) hold-row.value None) now spec.name))
    (:= held-specs (+ held-specs #((if held (replace spec :retired-held held) spec)))))
  (HeartbeatReply :jobs (tuple (gfor spec held-specs
                                     :setv kept (if (in spec.name marked) (replace spec :keep-when-cut-off True) spec)
                                     (if (and (in spec.name elder) (not spec.handoff) (not spec.once))
                                         (replace kept :elder-runs True)
                                         kept)))
                  :tasks (tasks-for state name boot) :warm (warms-for state name now)
                  :timing timing :draining (is-not (.row state.drains name) None) :superseded False :formats (tuple ACCEPTED-FORMATS)
                  :revision state.revision :handover-pending pending :supplied supplied))


;; 受け渡しで「今の世代が受け取れる」と数える recreate の job の phase(AL-D2k): 準備と検めが揃って待っている(elder-runs)か、もう
;; 動いているか、準備できないと確定した・機体の memory の見張りに続けて止められて起こし直さない(待っても揃わない — 入れ替えを止めない)。
(val HANDOVER-SETTLED-PHASES #{"elder-runs" "running" "code-failed" "probe-failed" "env-failed" "handoff-abandoned" "memory-killed"})


(defn #^ bool handover-pending [#^ ClusterState state #^ str name #^ str boot #^ int now #^ ClusterTiming timing
                                #^ (| dict None) ready-instances]
  ;; cannot be a defk: coordinator の純粋な判断(heartbeat-reply の値・drain_policy.worker-view — defn)が呼ぶ
  "純粋: 名 name の今の世代 boot が、同じ名の前の世代がまだ動かしている常駐の job をまだ受け取れないか(AL-D2k)。止めている job の
   判断は handover-blockers(この関数はその有無だけを読む — 止まりの名指しと同じ判断)。真の間、worker は Pod の ready の file を
   書かず、Deployment は前の世代の Pod の削除を
   始めない — Service は worker の Pod を振り先にし、Terminating の Pod を振り先から外すので、前の世代で動く job へ届かなくなる前に、
   今の世代が全部を受け取れる姿にしておく。"
  (bool (handover-blockers state name boot now timing ready-instances)))


;; 受け渡しの止まりの知らせの上限: 待ちの始まり(SupersededRun.since-ms)からこの長さを越えても止めている
;; job(handover-blockers)が残れば、Worker の status.handover の stalled と GET /metrics の doeff_worker_handover_stalled{worker} を
;; 1 にする。根拠: 今の世代が job を受け取れるまでに要るのは、Pod の起動と最初の heartbeat・job ごとの実行環境の準備(世代をまたいで
;; 同じ node の root を共有するので、ふつうは準備済み — 進まない準備は 600 秒で止めてやり直す・worker の PrepareLimits.stall-seconds)・
;; handoff の job の Ready(新しい process が動き出してから Ready までの上限の既定 300 秒 — readiness の handoffTimeoutSeconds)。
;; 止めてやり直す準備 1 回と Ready の待ちの和 900 秒に、Pod の起動と readiness の窓の 300 秒を足した 20 分。2026-10-22 の node-h は
;; 新しい世代の handoff の job が落ち続け、15:45:30 の始まりから 16:08:39 に人が手で外すまで 23 分止まった(この上限なら 16:05:30 に
;; 知らせる)。知らせるだけで諦めない: 待ちを外すと Deployment が前の世代の Pod を消し、まだ動いている前の世代の process まで止まる
;; (k8s の Deployment も progressDeadlineSeconds で Progressing=False を付けるだけで、旧い ReplicaSet を動かし続ける)。
(setv HANDOVER-STALL-MS 1200000)


(defn #^ tuple handover-blockers [#^ ClusterState state #^ str name #^ str boot #^ int now #^ ClusterTiming timing
                                  #^ (| dict None) ready-instances]
  ;; cannot be a defk: coordinator の純粋な判断(handover-pending・資源の行 resource_policy.handover-of — defn)が呼ぶ
  "純粋: 名 name の今の世代 boot の受け渡しを止めている job(HandoverBlocker の列・job の名の順)。handover-pending
   は空でない時に真 — 止める判断の定義点はここ 1 つ。数えるのは、重なりを名乗った前の世代(overlapped)のうち、止まりがまだ始まって
   いない(leaving でない)・最後の報告が lease の内の組の job だけ。recreate の job = 今の世代の状態の行が HANDOVER-SETTLED-PHASES に
   無い。handoff の job = Ready と数えている process(ready-instance)が今の世代の報告の行に無い(入れ替えを諦めた handoff-abandoned は
   数えない)。"
  (setv waiting (handover-waiting state name boot now timing))
  (when (not waiting) (return #()))
  ;; 状態の報告(statuses)は今の世代の物だけを持つ — 名乗りの世代(WorkerInfo.boot)が boot の時だけ読む。
  (setv info (.row state.workers name)
        current (.row state.observations.statuses name)
        rows (if (and (is-not current None) (is-not info None) (= info.boot boot)) current.jobs #())
        own (frozenset (gfor row rows :if row.instance row.instance)))
  ;; job の結末 = 同じ名の行のうち最後の物(無ければ None)。
  (tuple (sorted (gfor spec (jobs-for state name ready-instances)
                       :if (in spec.name waiting)
                       :setv row (next (gfor row (reversed rows) :if (= row.name spec.name) row) None)
                       :setv phase (if (is row None) None row.phase)
                       :if (if spec.handoff
                               (not (or (in spec.ready-instance own) (= phase "handoff-abandoned")))
                               (not-in phase HANDOVER-SETTLED-PHASES))
                       (HandoverBlocker :job spec.name :update (if spec.handoff "handoff" "recreate") :phase phase
                                        :failures (if (is row None) None row.failures)
                                        :last-exit-code (if (is row None) None row.last-exit-code)
                                        :detail (if (is row None) "" row.detail)))
                 :key (fn [b] b.job))))


(defn #^ frozenset handover-waiting [#^ ClusterState state #^ str name #^ str boot #^ int now #^ ClusterTiming timing]
  "純粋: 名 name の今の世代 boot がまだ受け取っていない、前の世代の常駐の job の名(重なりを名乗った・leaving でない・最後の報告が
   lease の内の組 — handover-blockers の母集団)。"
  (frozenset (gfor r (handover-runs state name boot now timing) job r.jobs job)))


(defn #^ tuple handover-runs [#^ ClusterState state #^ str name #^ str boot #^ int now #^ ClusterTiming timing]
  "純粋: 名 name の今の世代 boot が受け渡しを待っている前の世代の組(SupersededRun — 重なりを名乗った・leaving でない・最後の報告が
   lease の内)。"
  (tuple (gfor r state.superseded-runs
               :if (and r.overlapped (not r.leaving) (= r.worker name) (!= r.boot boot)
                        (< (- now r.seen-ms) timing.lease-ms))
               r)))


(defn #^ (| int None) handover-since [#^ ClusterState state #^ str name #^ str boot #^ int now #^ ClusterTiming timing]
  "純粋: 名 name の今の世代 boot の受け渡しの待ちの始まりの刻(待っている組の since-ms の最小)。待っている組が
   無いか、始まりを持たない(欄の前に保存した)組だけなら None。"
  (min (gfor r (handover-runs state name boot now timing) :if (is-not r.since-ms None) r.since-ms) :default None))


(defn #^ frozenset elder-held [#^ ClusterState state #^ str name #^ str boot #^ int now #^ int window-ms]
  ;; cannot be a defk: coordinator の純粋な判断(heartbeat-reply の内包表記の前の値)が呼ぶ
  "純粋: 名 name の worker の、送り手の世代 boot と違う前の世代がまだ動かしている常駐の job の名(AL-D2f — その世代の最後の報告が
   window-ms 以内の、新しい世代が重なりを名乗った(overlapped)SupersededRun の行だけ。window-ms を越えて黙った世代の行は数えない —
   前の世代が止まれば今の世代が起こす)。"
  (frozenset (gfor r state.superseded-runs
                   :if (and r.overlapped (= r.worker name) (!= r.boot boot) (< (- now r.seen-ms) window-ms))
                   job r.jobs
                   job)))


(defn #^ frozenset running-names [#^ tuple statuses]
  "退いた世代の報告のうち、いま running の job の名(入れ替えで退いた process の行は元の名で数える)。退いた世代に動かし続けさせて
   よい job を選ぶため。"
  (frozenset (gfor row statuses
                   :if (= row.phase "running")
                   (or row.retired-from row.name))))


;; 退いた世代の process がまだ刈られていない job の phase: 動いているか、止めの合図を送った後で刈りの前(止めを確かめられない間も含む)。
(val HOLDING-PHASES #{"running" "stopping" "stop-unconfirmed"})


(defn #^ frozenset holding-names [#^ tuple statuses]
  ;; cannot be a defk: coordinator の純粋な判断(superseded-runs-after の setv の中 — defn)が呼ぶ
  "退いた世代の報告のうち、その process がまだ在る(HOLDING-PHASES — 刈りの前の)job の名(入れ替えで退いた process の行は元の名で
   数える)。前の世代が持っている port や置き場を、新しい世代が起こす process と取り合わせないため(heartbeat-reply の印 elder-runs)。"
  (frozenset (gfor row statuses
                   :if (in row.phase HOLDING-PHASES)
                   (or row.retired-from row.name))))


(defn #^ HeartbeatReply superseded-reply [#^ ClusterState state #^ str name #^ str boot #^ (| tuple None) statuses #^ ClusterTiming timing
                                #^ (| dict None) [ready-instances None]]
  "退いた世代の process への heartbeat の返事(2026-09-27)。退く process に新しい仕事を起こさせず、動いている物は安全に畳ませるため:
   jobs = 名の置き先の入れ替え(handoff)の job のうち、その世代が running と報告している物だけ(lease を持ったまま Pod の停止まで
   動かし、新しい世代の process が lease を取る。recreate の job は載せない = 止めて lease を返す。退いた後に置かれた job は、その
   世代が動かしていないので載らない)。tasks = task.boot がその世代と等しい切り離した task だけ(最後まで走らせる。RemoteJob の
   task は世代を記録しないので載せない)。温める表は載せない。draining = 真(ready の file を draining にする)・superseded = 真。
   世代をまたぐ入れ替え(AL-D2): 載せる handoff の job のうち、Ready と数えている process の世代(ready-instance)がこの世代の報告の
   行に無い物 = 新しい世代の process が Ready の物には印 superseded-by を載せる(worker は今の process を止めずに退かせ、起こし直さない —
   走っている仕事を終えて自分で終わる)。"
  ;; statuses = None は名指しの待ちの見え方(watch_policy.worker-mark — 報告を持たない): その世代の記録(SupersededRun)の jobs を
  ;; 走っている job と数える(数えなければ見え方が変わらず、前の世代を外す返事の変化で待ちが起きない — AL-D2f)。
  (setv recorded (frozenset (gfor r state.superseded-runs :if (and (= r.worker name) (= r.boot boot)) job r.jobs job))
        running (if (is statuses None) recorded (running-names statuses))
        own (frozenset (gfor row (or statuses #()) :if row.instance row.instance))
        ;; 今の世代が起こせると報告した recreate の job(状態の行の phase が elder-runs — AL-D2f)。それまでは前の世代に残す。
        current (.row state.observations.statuses name)
        ;; 重なりの組は、この世代の Pod の止まりが始まる(leaving)まで何も渡さない(AL-D2k — Service の振り先は止まりが始まるまで
        ;; この世代の Pod なので、先に渡すと渡した job へ届かない)。始まったら、起こせると報告した job を一度に渡す。
        run (next (gfor r state.superseded-runs :if (and (= r.worker name) (= r.boot boot)) r) None)
        staying (and (is-not run None) run.overlapped (not run.leaving))
        taken (if staying
                  (frozenset)
                  (frozenset (gfor row (if (is current None) #() current.jobs) :if (= row.phase "elder-runs") row.name)))
        ;; 新しい世代が重なりを名乗った組(overlapped)の recreate の job だけを残す(名乗らない新しい世代 = 今どおり止める)。
        held (frozenset (gfor r state.superseded-runs :if (and r.overlapped (= r.worker name) (= r.boot boot)) job r.jobs job)))
  (HeartbeatReply :jobs (tuple (gfor s (jobs-for state name ready-instances)
                                     :if (or (and s.handoff (in s.name running))
                                             (and (not s.handoff) (not s.once) (in s.name held) (in s.name running) (not-in s.name taken)))
                                     (cond
                                       staying s
                                       (and (is-not s.ready-instance None) (not-in s.ready-instance own))
                                         (replace s :superseded-by s.ready-instance)
                                       ;; 止まりの始まった重なりの組(AL-D2n): 今の世代の process が Ready と数えられていない拍(負荷の
                                       ;; 間など)でも印を付ける — 印が無いと、退かせた process が終わった後に、この世代の worker が
                                       ;; 新しい試行を起こし、今の世代の process から lease を取る(10-11 21:29 JST の node-h)。値は今の
                                       ;; 世代(worker は値を読まず、在るかだけを読む)。
                                       (and s.handoff (is-not run None) run.leaving)
                                         (replace s :superseded-by (+ "generation:" (or (getattr (.row state.workers name) "boot" None) "")))
                                       True s)))
                  :tasks (tuple (gfor offer (tasks-for state name boot)
                                      :setv task (.row state.tasks offer.id)
                                      :if (and task.detached (= task.boot boot))
                                      offer))
                  :warm #() :timing timing :draining True :superseded True :formats (tuple ACCEPTED-FORMATS)
                  :revision state.revision))


(defk state-view [state now timing]
  {:pre [(: state ClusterState) (: now int) (: timing ClusterTiming)] :post [(: % StateView)] :tags {:context "coordinator" :role "judgment"}}
  "GET /state の状態の画面(JSON は coordinator/protocol/replies が綴る — #2595)。worker ごとの taskRoom と taskMemoryRoomMiB は置き先の
   判断と同じ数え(load-of と task-room-of・task-memory-room-of — 機体の測った空きと置いた task の memory の要り)から読む。"
  (setv draining (draining-workers state now)
        load (load-of state state.placements))
  (var workers #())
  (for [#(n w) (.items state.workers)]
    (<- room int (task-room-of w load))
    (<- memory-room int (task-memory-room-of w load))
    (:= workers #(#* workers (WorkerView :info w :silent-ms (- now w.last-seen-ms) :live (alive now w timing.lease-ms)
                                           :draining (in n draining) :task-room room :task-memory-room-mib memory-room))))
  (StateView :now now
             :services (tuple (gfor j state.jobs (ServiceView :job j :resource-version (resource-version-of state (+ "Service/" j.spec.name)))))
             :workers workers
             :placements state.placements
             :unplaced (unplaced-jobs now state timing)
             :statuses (dfor #(n st) (.items state.observations.statuses) n (StatusView :report st :stale (> (- now st.at) timing.lease-ms)))
             :tasks (tuple (sorted (.rows state.tasks) :key (fn [t] t.id)))
             :board-keys (.size state.board)
             :surges state.surges
             :events (tuple (cut state.events -50 None))
             :revision state.revision
             ;; 途絶しても動かし続けてよい印の約束(#2883 — 読みだけ。列は job の名の順のまま)。
             :keep-marks state.keep-marks
             ;; この coordinator の process が走っている doeff の版(#3772 — 読めなければ None)。
             :coordinator-commit state.running-commit
             ;; 組ごとの宣言の値と置いている数。
             :group-limits (! (group-limit-views state))
             ;; 置き先の一時の上書き(Service の名の順)。
             :needs-overrides (tuple (sorted (.rows state.needs-overrides) :key (fn [o] o.service)))
             ;; 置いた task の申告(task の id の順 — ADR-DOE-CLUSTER-005 R4)。
             :claims (tuple (sorted (.rows state.claims) :key (fn [c] c.task)))
             ;; 置き先の無い Service の種類・足りない能力・いつから(unplaced と同じ Service)。
             :unplaced-detail (unplaced-views now state timing)))


(defn #^ (| str None) runtime-env-refusal [#^ (get Mapping #(str object)) body]
  "宣言の行の runtimeEnv(在れば)が宣言として読めなければ理由の文(送り手の誤り — 400)。"
  (runtime-env-value-refusal (.get body "runtimeEnv")))


(defn #^ (| str None) runtime-env-value-refusal [#^ object value]
  "runtimeEnv の値(None = 無い)が宣言として読めなければ理由の文 — 宣言の行と本文の型(#2445)が同じ規則で読む。"
  (cond
    (is value None) None
    (not (isinstance value dict)) (.format "runtimeEnv は JSON の object: {!r}" (type value))
    True (try (do (run (runtime-env-of-json value)) None)
              (except [error RuntimeEnvInvalid] (.format "runtimeEnv が誤っている: {}" error)))))


(defk submit-task [state body now [owner None]]
  {:pre [(: state ClusterState) (: body TaskBody) (: now int) (: owner (| str None))] :post [(: % tuple)]
   :tags {:context "coordinator" :role "judgment"}}
  "POST /tasks: 呼び手の問い合わせに寿命を縛られた task の行を作る。本文は置き場に置いた Program の sha を運ぶ(task-body-refusal)。"
  (setv refusal (or (format-version-refusal body.format) (runtime-env-value-refusal body.runtime-env) (task-body-refusal state body)
                    (task-priority-refusal body.priority) (task-limits-refusal body.limits)
                    ;; 測った空きの門の外は切り離した task だけの名乗り(2026-11-11 — 呼び手の問い合わせに縛られた task は空きの門の内で置く)。
                    (if (is body.ungated None) None "ungated は切り離した task(PUT /detached)だけが名乗る")
                    ;; 場所を譲る名乗りも切り離した task だけ(2026-11-11 — 止めた task は走らせ直しの道で列へ戻る)。
                    (if (is body.preemptible None) None "preemptible は切り離した task(PUT /detached)だけが名乗る")
                    ;; 待ちの期限も切り離した task だけ(2026-11-12 — 縛られた task は呼び手が問い合わせを止めれば消える)。
                    (if (is body.queue-limit-seconds None) None "queueLimitSeconds は切り離した task(PUT /detached)だけが名乗る")
                    (! (limits-undeclared-refusal state.group-limits body.limits))))
  (when refusal (return #(state 400 (ErrorReply :message refusal))))
  ;; 数に読めない leaseSeconds は送り手の誤り(float() の ValueError / TypeError を受け口へ漏らさない — #1024)。
  (setv lease-value (if (is body.lease-seconds None) 15.0 body.lease-seconds))
  (try
    (setv lease-seconds (float lease-value))
    (except [[ValueError TypeError]]
      (return #(state 400 (ErrorReply :message (.format "leaseSeconds は 0 より大きく {} 以下の数: {!r}" TASK-MAX-LEASE-SECONDS lease-value))))))
  (setv open-count (len (lfor t (scan-rows state.tasks) :if (or (= t.phase "queued") (in t.phase PLACED-PHASES)) t)))
  (when (not (< 0 lease-seconds (+ TASK-MAX-LEASE-SECONDS 1)))
    (return #(state 400 (ErrorReply :message (.format "leaseSeconds は 0 より大きく {} 以下: {}" TASK-MAX-LEASE-SECONDS lease-seconds)))))
  (when (>= open-count TASK-MAX-OPEN)
    (return #(state 429 (ErrorReply :message (.format "終わっていない task が上限 {} 本に達している" TASK-MAX-OPEN) :open open-count))))
  (setv id (task-id state)
        lease-ms (int (* 1000 lease-seconds))
        task (TaskRecord id body.name body.program body.revision
                         (program-versions state body.program)
                         (needs-named body.needs body.requires "task の needs")
                         lease-ms (+ now lease-ms) now
                         :runtime-env body.runtime-env
                         :environ (environ-pairs (or body.environ {}))
                         :priority (or body.priority 0) :limits (task-limits-of body.limits)))
  #((replace state :tasks (.with-writes state.tasks #((TableWrite id task))) :next-task (+ state.next-task 1)) 200 (TaskAccepted :id id)))


(defk poll-task [state id now]
  {:pre [(: state ClusterState) (: id str) (: now int)] :post [(: % tuple)] :tags {:context "coordinator" :role "judgment"}}
  "呼び手の問い合わせ。lease を延ばし、いまの様子を返す。"
  (setv found (.row state.tasks id))
  (when (is found None)
    (return #(state 200 (TaskMissing :id id))))
  (setv task (replace found :lease-until-ms (+ now found.lease-ms)))
  #((replace state :tasks (.with-writes state.tasks #((TableWrite id task)))) 200
    (TaskProgress :phase task.phase :worker task.worker :detail task.detail :result task.result
                  :failure-kind task.failure-kind :retryable task.retryable)))


(defk lease-write [state name body now]
  {:pre [(: state ClusterState) (: name str) (: body LeaseBody) (: now int)] :post [(: % tuple)] :tags {:context "coordinator" :role "judgment"}}
  "POST /leases/<名>: lease の操作 1 つを coordinator の時計で当てる(lease_rules.lease-op)。行が変われば盤へ書く
   (版を 1 進める・盤の書きと同じく永続化してから返事をする)。返り値 #(次の状態 status 答え)。"
  ;; 本文の欄の欠け・型の誤りは本文を解く所(coordinator/protocol/request_bodies)が 400 で断る(#1024・#2445)。
  (setv key (semaphore-key name) entry (.row state.board key) current (if (is entry None) None entry.value)
        #(row verdict) (lease-op current body.op body.token body.permits body.ttl-ms now)
        ;; 返事の本文は LeaseAnswer の値(JSON の wire の形は coordinator/protocol/replies が綴る — #2614)。
        answer verdict)
  (if (or (is row current) (is row None))
      #(state 200 answer)
      (do (setv version (if (is entry None) 0 entry.version))
          #((board-written state #((TableWrite key (BoardRow :value row :version (+ version 1) :expires-ms None :size (value-size row)))))
            200 answer))))


(defn #^ int value-size [#^ (| dict list str int float bool None) value]
  "盤の値の大きさ(JSON の utf-8 の byte 数)。容量の上限の判断と計器に使う。"
  (len (.encode (json.dumps value :ensure-ascii False :separators #("," ":")) "utf-8")))


(defn #^ (| int None) expiry-floor-of [#^ (get Table BoardRow) board]
  "盤の全行のいちばん早い期限(期限つきの行が無ければ None)— BoardTally.floor を求め直す所(board-tally-of と sweep-board)。"
  (min (gfor row (.rows board) :if (is-not row.expires-ms None) row.expires-ms) :default None))


(defn #^ BoardTally board-tally-of [#^ (get Table BoardRow) board]
  "盤の全行から集計を求める(起動の読み直しと、集計をまだ持たない状態の最初の読みだけ — 書きごとには board-written が足し引きする)。"
  (BoardTally :rows (.size board) :bytes (sum (gfor row (.rows board) row.size))
              :expiring (len (lfor row (.rows board) :if (is-not row.expires-ms None) row))
              :floor (expiry-floor-of board)))


(defn #^ BoardTally board-tally [#^ ClusterState state]
  "状態の盤の集計(持っていなければ全行から求める)。"
  (if (is state.board-tally None) (board-tally-of state.board) state.board-tally))


(defn #^ ClusterState board-written [#^ ClusterState state #^ tuple writes]
  "盤へ書き(鍵ごとに 1 つの TableWrite・value None は消す)を当てた状態。集計は書く行・消す行の分だけ足し引きする(全行を読まない)。
   期限の下限(BoardTally.floor)は書く行の期限との小さい方を取り、消しでは動かさない(期限つきの行が 0 になれば None)。
   書きが全部今の行そのものなら状態そのもの。"
  (setv board (.with-writes state.board writes))
  (when (is board state.board)
    (return state))
  (setv tally (board-tally state) rows tally.rows size tally.bytes expiring tally.expiring floor tally.floor)
  (for [write writes]
    (setv old (.row state.board write.key) new write.value)
    (when (is-not old None)
      (-= rows 1)
      (-= size old.size)
      (when (is-not old.expires-ms None) (-= expiring 1)))
    (when (is-not new None)
      (+= rows 1)
      (+= size new.size)
      (when (is-not new.expires-ms None)
        (+= expiring 1)
        (setv floor (if (is floor None) new.expires-ms (min floor new.expires-ms))))))
  (replace state :board board :board-tally (BoardTally :rows rows :bytes size :expiring expiring :floor (if (= expiring 0) None floor))))


(defn #^ BoardUsage board-usage [#^ ClusterState state]
  "盤の使い方と上限 — 容量の判断・計器・容量で断った答えが同じ数を読むため。"
  (setv tally (board-tally state))
  (BoardUsage :rows tally.rows :bytes tally.bytes :expiring tally.expiring
              :max-rows BOARD-MAX-ROWS :max-bytes BOARD-MAX-BYTES :max-value-bytes BOARD-MAX-VALUE-BYTES))


(defn #^ (| str None) board-capacity-refusal [#^ ClusterState state #^ str key #^ int size]
  "純粋: key へ size byte の値を書くと上限を越えるなら理由の文。越えないなら None。小さくする書きは(合計が上限の上でも)通す。"
  (setv entry (.row state.board key)
        old (if (is entry None) 0 entry.size)
        total (. (board-tally state) bytes))
  (cond
    (> size BOARD-MAX-VALUE-BYTES) (.format "値が {} byte で、1 行の上限 {} byte を越える" size BOARD-MAX-VALUE-BYTES)
    (and (is entry None) (>= (.size state.board) BOARD-MAX-ROWS))
      (.format "盤の行が上限 {} 行に達している(期限つきの行 {} 行)" BOARD-MAX-ROWS (. (board-usage state) expiring))
    (and (> size old) (> (+ (- total old) size) BOARD-MAX-BYTES))
      (.format "盤の値の合計が {} byte になり、上限 {} byte を越える" (+ (- total old) size) BOARD-MAX-BYTES)
    True None))


(defn #^ object written-value [#^ BoardWrite write]
  "盤の書きの値(欄が無ければ送り手の誤り — 400)。null も値として書く。"
  (when (not write.value-given)
    (raise (BodyInvalid "value の欄が無い(消すなら delete: true)")))
  write.body.value)


(defk board-write [state key write [now 0]]
  {:pre [(: state ClusterState) (: key str) (: write BoardWrite) (: now int)] :post [(: % tuple)] :tags {:context "coordinator" :role "judgment"}}
  "盤の行 1 つの compare-and-set。expect = 値で比べる(従来)・expectVersion = 行の版で比べる(0 = 行が無い時だけ)。
   両方あれば両方を満たす時だけ書く。value が null で delete が真なら行を消す。返事に行の新しい版を載せる。
   ttlSeconds(2026-09-25)= 行の期限。期限を過ぎた行は調停が消す(sweep-board)。付けない書きは期限を外す(ずっと残す)。
   上限(board-capacity-refusal)を越える書きは 507 で断る。"
  (setv body write.body ttl write.body.ttl-seconds)
  (<- ttl-refusal (board-ttl-refusal ttl))
  (when (is-not ttl-refusal None)
    (return #(state 400 (BoardRefused :reason ttl-refusal))))
  (setv entry (.row state.board key)
        present (is-not entry None)
        current (if present entry.value None)
        version (if present entry.version 0)
        ok (and (board-allows current present write.expect-given body.expect)
                (or (is body.expect-version None) (= body.expect-version version))))
  (cond
    (not ok) #(state 409 (BoardConflict :current current :version version))
    ;; lease の行への直の書き(旧い版の process)は、coordinator の時計でまだ切れていない担い手を追い出せない(2026-09-25)。
    ;; 409 = 旧い版は compare-and-set の競合として読み直す。
    (and (.startswith key SEMAPHORE-PREFIX) (not body.delete)
         (is-not (semaphore-write-refusal current (written-value write) now) None))
      #(state 409 (BoardConflict :current current :version version
                                 :reason (semaphore-write-refusal current (written-value write) now)))
    body.delete
      #((board-written state #((TableWrite key None)))
        200 (BoardWritten :version None))
    True
      (do (setv size (value-size (written-value write))
                refusal (board-capacity-refusal state key size))
          (if (is-not refusal None)
              #(state 507 (BoardRefused :reason refusal :usage (board-usage state)))
              #((board-written state #((TableWrite key (BoardRow :value (written-value write) :version (+ version 1)
                                                                 :expires-ms (if (is ttl None) None (+ now (int (* 1000 ttl))))
                                                                 :size size))))
                200 (BoardWritten :version (+ version 1)))))))


;; --- node の label から導く能力(ADR-DOE-CLUSTER-001 R4b・改訂 1 の I)------------------------------------------

(defk nodes-to-follow [state]
  {:pre [(: state ClusterState)] :post [(: % tuple)] :tags {:context "coordinator" :role "judgment"}}
  "見張る node の名(整列)— heartbeat で node を申告した worker の node 全部。能力の導出の材料(node の label)を、時間で読みに行かず
   変化の出来事で受けるため(#4070 — 時刻を読まない。worker の居なくなった node は外れ、見張りが止まる)。"
  (tuple (sorted (sfor w (.rows state.workers) :if w.node w.node))))


(deff derived-capabilities [#^ (get Table str) labels #^ tuple table]  ; cannot be a defk: coordinator の調停(Program)が呼ぶ純粋な判断
  {:pre [(: labels (get Table str)) (: table tuple)] :post [(: % tuple)] :tags {:context "coordinator" :role "judgment"}}
  "node の label → その node の worker に足す能力(名の順)。table = ClusterNaming の node-capabilities #(#(鍵 値 能力) …)。"
  (tuple (sorted (sfor #(key value capability) table :if (= (.row labels key) value) capability))))


(deff with-derived-capabilities [#^ ClusterState state #^ tuple table]  ; cannot be a defk: coordinator の調停(Program)が呼ぶ純粋な判断
  {:pre [(: state ClusterState) (: table tuple)] :post [(: % ClusterState)] :tags {:context "coordinator" :role "judgment"}}
  "node の label の観測から、各 worker の derived(導いた能力)を作り直す。label を読めなかった node(error)の worker は前の値を保つ
   (届かない間に会社の機体の能力を外したり足したりしない — 次に読めた時に直る)。node を名乗らない worker は空。"
  (setv written [])
  (for [#(name w) (.items state.workers)]
    (setv derived
      (if (not w.node)
          #()
          (match (.row state.observations.nodes w.node)
            (NodeLabelsSeen :labels labels) (derived-capabilities labels table)
            ;; まだ読んでいない・読めなかった node の worker は前の値を保つ。
            _ w.derived)))
    ;; 能力の計算と検査は毎回行い、値が違う行だけ書く(全員が同じなら表は同じ object のまま)。
    (when (!= derived w.derived)
      (.append written (TableWrite name (replace w :derived derived)))))
  (setv workers (.with-writes state.workers (tuple written)))
  (if (is workers state.workers) state (replace state :workers workers)))
