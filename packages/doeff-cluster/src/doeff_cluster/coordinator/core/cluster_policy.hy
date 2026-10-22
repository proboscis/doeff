;;; coordinator の純粋な判断。宣言された job・task・worker の生存・今の割り当て・時刻から、次の割り当てを導く。
;;; HTTP の要求 1 件への返事も、状態と要求と時刻から (次の状態 status 本文) を返す純粋な関数にする。I/O はしない。
;;; 割り当ては安定させる: 担い手が移し替えの期限内に生きていれば動かさない。
(require doeff-hy.macros [defk deff val var])
(val MODULE-TAGS {:context "coordinator" :role "judgment"})
(import dataclasses [replace])
(import functools)
(import hashlib)
(import itertools [groupby])
(import operator [attrgetter])
(import json)
(import math [ceil])
(import re)

(import doeff_cluster.shared.intent.job_model [JobSpec])
(import doeff_cluster.shared.intent.protocol [ClusterTiming Request BodyInvalid])
(import doeff_cluster.shared.core.capabilities [capabilities-of environ-pairs])
(import doeff_cluster.shared.core.detached_rules [task-priority-refusal task-limits-refusal task-limits-of])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterJob ErrorReply TaskAccepted TaskProgress TaskMissing TaskResultTaken BoardTally BoardUsage BoardWritten BoardConflict BoardRefused WorkerInfo TaskOffer WarmOffer HeartbeatReply ServiceView WorkerView StatusView StateView GroupLimitView GroupHeld BoardRow WorkerReport GenerationOrder Placement ClusterState TaskRecord EnvFailed HandoffPhase UnplacedKind TaskUnplacedKind WorkerLoad ACCEPTED-FORMATS PLACED-PHASES])
(import doeff_cluster.coordinator.intent.cluster_model [NodeLabelsSeen KeepMark KnownExit SupersededRun HandoverBlocker])
(import doeff_cluster.shared.intent.detached_model [TaskLimit GroupLimit])
(import doeff_cluster.shared.intent.due_model [DueAt DueNow DueNever])
(import doeff_cluster.shared.core.due_policy [due-of-instants])
(import doeff_cluster.coordinator.intent.worker_notices [WorkerBack WorkerGone])
(import doeff_hy.table [Table TableWrite TableDraft draft-of table-of])
(import doeff_cluster.coordinator.core.cluster_rules [component-versions-of format-version-refusal])
(import doeff_cluster.coordinator.intent.request_bodies [LeaseBody TaskResultBody BoardWrite HeartbeatBody EnvsReport StatusRow TaskBody])
(import doeff_cluster.coordinator.core.cluster_rules [required-field int-field])
(import doeff_cluster.shared.intent.semaphore_model [SEMAPHORE-PREFIX])
(import doeff_cluster.shared.core.lease_rules [lease-op semaphore-write-refusal semaphore-key drop-holders lease-holder holder-tokens-prefix live-instances])
(import doeff_cluster.shared.intent.readiness_model [RETIREMENT-HOLD])
(import doeff_cluster.shared.core.board_rules [board-allows board-ttl-refusal])
(import doeff [run])
(import doeff_cluster.shared.intent.runtime_env_model [RuntimeEnvInvalid])
(import doeff_cluster.shared.core.runtime_env_rules [runtime-env-of-json env-key child-environ-refusal])
(import doeff_cluster.shared.core.readiness_rules [readiness-refusal retired-lifetime-ms retired-limit-of])
(import doeff_cluster.coordinator.core.program_policy [PROGRAM-GRACE-MS program-refs])

(setv JOB-ENTRY "doeff_cluster.worker.entry.job_entry")
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

(defn #^ (| str None) declared-runtime-env [#^ dict item]
  "宣言 1 行の runtimeEnv(在れば)の正規化した JSON の文字列(JobSpec.runtime-env — 比べる欄)。無ければ None。"
  (setv value (.get item "runtimeEnv"))
  (if (is value None) None (json.dumps value :sort-keys True :ensure-ascii False)))

(defn #^ JobSpec spec-of-declaration [#^ (get dict #(str object)) item]
  "宣言 1 行 → worker が起動する形(job_entry の service 入口と詰めた Program の置き場のキー)。宣言の job は Program の job だけ —
   run の無い行(生の entry と args を worker に直に起こさせる形)は理由つきで断る(ADR-DOE-CLUSTER-001 R1・R7 — 移行の期間は置かない)。
   runtimeEnv を持つ宣言は、worker が env の root を準備してその venv で起こす(版は worker が env のキーへ置き換える)。"
  (setv run (.get item "run") revision (required-field item "revision") runtime (declared-runtime-env item) name (.get item "name"))
  ;; 名前と revision の空は送り手の誤り(400)— JobSpec の不変条件(name・entry・revision が要る)の素の ValueError まで運ばない(#2796:
  ;; 本文の読みの中の ValueError が調停ループの外まで抜け、coordinator の process ごと落ちた)。
  (cond
    (not (and (isinstance name str) name)) (raise (BodyInvalid (.format "Service の名前は空でない文字列: {!r}" name)))
    (is run None) (raise (BodyInvalid (raw-entry-refusal item)))
    (not (isinstance run dict)) (raise (BodyInvalid (.format "run は JSON の object: {!r}" run)))
    (not (and (isinstance revision str) revision)) (raise (BodyInvalid (.format "revision は空でない文字列: {!r}" revision)))
    (= (.get run "kind") "service")
      (do (setv refusal (program-row-refusal item))
          (when refusal (raise (BodyInvalid refusal)))
          (setv handoff (= (.get item "update") "handoff"))
          (JobSpec name JOB-ENTRY #("service" "--identity" (identity-hash run))
                   revision
                   :handoff handoff :runtime-env runtime
                   ;; 退いた process の寿命の上限(#4072 の D-2 — 宣言の readiness の retiredSeconds・handoff の job だけ)。
                   :retired-ms (if handoff (retired-lifetime-ms (.get item "readiness")) None)
                   ;; 退いた process を同時に残す数の上限 R(#4072 の D-3 の改め — 宣言の readiness の retiredLimit・handoff の job だけ)。
                   :retired-limit (if handoff (retired-limit-of (.get item "readiness")) None)
                   :program (get run "program")
                   ;; 子の入口が比べる送り手の版 = 宣言の行の run.versions(宣言の道具が Program と一緒に置いた版)。worker は Program の
                   ;; cache の file をこの版ごとに分けるので、同じ sha の Program を別の版で宣言し直すと取り直す(card ki-172e63fed4c7)。
                   :versions (environ-pairs (.get run "versions" {}))
                   :environ (environ-pairs (.get item "environ" {}))))
    True (raise (BodyInvalid (+ "知らない run.kind: " (repr (.get run "kind")))))))


;; --- Program の job の行(ADR-DOE-CLUSTER-001・改訂 1 の A・E・F・G) -------------------------------------

(setv PROGRAM-SHA (re.compile r"[0-9a-f]{64}"))
;; 旧い宣言の run の欄(関数の参照 + 設定 + handler の組の import path — 2026-09-27 より前の形)。
(setv OLD-RUN-KEYS #("factory" "env" "config"))
;; image の版を追う係(base-follow — 2026-09-28 に消した)の行の欄: 追う Deployment・追った commit・定義だけを別の commit で重ねる版。
(val IMAGE-FOLLOW-KEYS #("baseFrom" "base" "overlay"))


(deff raw-entry-refusal [#^ dict item]  ; defk にできない: 宣言の読み(coordinator の純粋な判断)が呼ぶ
  {:pre [(: item dict)] :post [(: % str)] :tags {:context "coordinator" :role "judgment"}}
  "run の無い宣言の行(worker に module と引数を直に起こさせる生の entry の job)を断る理由の文 — 書きの口の 400・保存の読み直しの
   RefusedJob の理由に同じ文を出すため。宣言の job は Program の値 1 つだけ(ADR-DOE-CLUSTER-001 R1・R7)。"
  (.format "生の entry の job(entry {!r}・args {!r}・run が無い)は受け付けない — job は Program の値 1 つで宣言する(defsystem と declare・ADR-DOE-CLUSTER-001 R1)"
           (.get item "entry") (.get item "args")))


(deff identity-hash [#^ dict run]  ; defk にできない: 宣言の読み(coordinator の純粋な判断)が呼ぶ
  {:pre [(: run dict)] :post [(: % str) (= (len %) 16)] :tags {:context "coordinator" :role "judgment"}}
  "Program の job の同一性の指紋 = identity(関数の参照と引数の正規 JSON)と versions の sha256 の頭 16 桁。job の引数に載り、
   spec-hash(revision・environ と一緒)の材料になる。詰めた Program の中身(program の sha)は入れない — 揺れるため(改訂 1 の A)。"
  (cut (.hexdigest (hashlib.sha256 (.encode (json.dumps {"identity" (get run "identity") "versions" (.get run "versions" {})}
                                                        :sort-keys True :ensure-ascii False :separators #("," ":"))
                                            "utf-8")))
       0 16))


(deff program-row-refusal [#^ dict item]  ; defk にできない: 宣言の読み(coordinator の純粋な判断)が呼ぶ
  {:pre [(: item dict)] :post [(: % (| str None))] :tags {:context "coordinator" :role "judgment"}}
  "Program の job の宣言の行が受けられない理由(受けられれば None)。旧い形(run.factory・run.env・run.config・requires)・
   image の版を追う欄(baseFrom・base・overlay — Program を詰めた commit と別の commit で解くことになる — 改訂 1 の E)・置き場のキーの形・identity の欠け・
   versions の形・environ の名(child-environ-refusal の検め・実行環境の env-vars との重なり — 改訂 1 の G)を検める。"
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
    (not (text-map? (.get run "versions" {}))) (.format "run.versions は部品の名 → 版の文字列の object: {!r}" (.get run "versions"))
    (not (isinstance environ dict)) (.format "environ は文字列の鍵と値の object: {!r}" environ)
    True (environ-refusal environ declared)))


(deff environ-refusal [#^ dict environ #^ list declared]  ; defk にできない: 宣言の読み(coordinator の純粋な判断)が呼ぶ
  {:pre [(: environ dict) (: declared list)] :post [(: % (| str None))] :tags {:context "coordinator" :role "judgment"}}
  "宣言の行・task の本文の environ が受けられない理由。名と値は実行環境の env-vars と同じ検め(runtime_env_rules.child-environ-refusal —
   EnvVar の名の形・worker の予約・秘密の中身の名)で、env-vars と同じ名は断る(子の環境変数の足し口を 1 つにする — 改訂 1 の G)。"
  (setv problem (child-environ-refusal environ))
  (when problem (return problem))
  (setv clash (sorted (gfor k environ :if (in k declared) k)))
  (if clash
      (.format "environ の {} は実行環境の env-vars と同じ名 — どちらか 1 つで宣言する" clash)
      None))


(deff task-environ-refusal [#^ (| dict list tuple str int float bool None) environ #^ (| dict list tuple str int float bool None) runtime]  ; defk にできない: HTTP の本文を読む境界(Program の外)が呼ぶ純粋な判断
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


(defn #^ ClusterJob job-from-json [#^ dict item]
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

(deff liveness-deadline [#^ WorkerInfo worker #^ int window-ms]  ; defk にできない: 生死の比べ alive(Program の外の純粋な判断)が呼ぶ
  {:pre [(: worker WorkerInfo) (: window-ms int)] :post [(: % int)] :tags {:context "coordinator" :role "judgment"}}
  "worker を窓 window-ms の内で生きていると数える最後の刻(epoch ms)を求めるため。生死の判断(alive・forget-silent-workers)と、その
   答えが変わる最初の刻を求める liveness-due が同じこの値で比べる(#3061 — 片方だけ比べ方を変えると、模擬の静かな区間が判断の
   変わる歩を試さずに飛ぶ)。"
  (+ worker.last-seen-ms window-ms))


(defn #^ bool alive [#^ int now #^ WorkerInfo worker #^ int window-ms]
  "worker が刻 now に窓 window-ms の内で生きているか(最後の連絡から window-ms 以内か)を判じるため。"
  (<= now (liveness-deadline worker window-ms)))


(deff silent-names [#^ ClusterState state #^ int now #^ ClusterTiming timing]  ; defk にできない: note-liveness(Program の外の純粋な判断)が呼ぶ
  {:pre [(: state ClusterState) (: now int) (: timing ClusterTiming)] :post [(: % frozenset)] :tags {:context "coordinator" :role "judgment"}}
  "刻 now に生きていないと数える worker の名を求めるため(note-liveness と liveness-due が同じ求め方を使う)。"
  (frozenset (gfor w (.rows state.workers) :if (not (alive now w timing.lease-ms)) w.name)))


(deff note-liveness [#^ ClusterState state #^ int now #^ ClusterTiming timing]  ; defk にできない: 調停の純粋な判断(api_policy.settle — Program の外)が呼ぶ
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


(deff placeable [#^ (get tuple #(str ...)) needs #^ WorkerInfo worker]  ; defk にできない: coordinator と模擬の置き先の選び(Program の外の純粋な判断)が呼ぶ
  {:pre [(: needs tuple) (: worker WorkerInfo)] :post [(: % bool)] :tags {:context "coordinator" :role "judgment"}}
  "needs の job / task をこの worker に置けるか — 置き場所の規則の定義点はここ 1 つ(ADR-DOE-CLUSTER-001 R4b):
   needs ⊆ provides ∪ derived(node の label から coordinator が導いた能力)、かつ worker が専用の能力(exclusive)を持てば、そのどれかを needs に持つ(一般の仕事を専用の担い手に置かない)。"
  (and (<= (set needs) (| (set worker.provides) (set worker.derived)))
       (or (not worker.exclusive) (bool (& (set needs) (set worker.exclusive))))))


(deff named-capabilities [#^ (| tuple list None) provides #^ (| tuple list None) exclusive #^ bool old-labels #^ str what]  ; defk にできない: heartbeat の本文と保存の JSON を読む境界(Program の外)が呼ぶ
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


(deff request-needs [#^ dict body #^ str what]  ; defk にできない: HTTP の本文・宣言の JSON を読む境界(Program の外)が呼ぶ
  {:pre [(: body dict) (: what str)] :post [(: % tuple)] :tags {:context "coordinator" :role "judgment"}}
  "送られた宣言・task・温める頼みの本文の needs → 名の順の tuple。旧い形の requires を持つ本文・空の needs は BodyInvalid(理由つき・ValueError の子)—
   旧い宣言は受け付けない(operator 2026-09-27)・要る能力は必ず書く(改訂 1 の I)。"
  (needs-named (.get body "needs") (.get body "requires") what))


(deff needs-named [#^ (| dict list tuple str int float bool None) needs #^ (| dict list tuple str int float bool None) requires #^ str what]  ; defk にできない: HTTP の本文・宣言の JSON を読む境界(Program の外)が呼ぶ
  {:pre [(: needs (| dict list tuple str int float bool None)) (: requires (| dict list tuple str int float bool None)) (: what str)] :post [(: % tuple)] :tags {:context "coordinator" :role "judgment"}}
  "要る能力の名乗り(needs と旧い形の requires の値 — None = 欄が無い)→ 名の順の tuple。宣言の行(request-needs)と本文の型(#2445)が
   同じ規則で読む: requires は BodyInvalid・空の needs は BodyInvalid。"
  (when (is-not requires None)
    (raise (BodyInvalid (.format "旧い形の requires {!r} は受け付けない — 要る能力の名の列 needs で書き直す(ADR-DOE-CLUSTER-001 R4b)"
                                requires))))
  (setv needs (capabilities-of (if (is needs None) [] needs) what))
  (when (not needs)
    (raise (BodyInvalid (.format "{} が空 — 要る能力の名を 1 つ以上書く(どこにでも置ける仕事は無い・ADR-DOE-CLUSTER-001 R4b・改訂 1 の I)" what))))
  needs)


(deff task-body-refusal [#^ ClusterState state #^ TaskBody body]  ; defk にできない: HTTP の本文を読む境界(Program の外)が呼ぶ純粋な判断
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


(deff program-versions [#^ ClusterState state #^ str sha]  ; defk にできない: HTTP の本文を読む境界(Program の外)が呼ぶ純粋な判断
  {:pre [(: state ClusterState) (: sha str)] :post [(: % tuple)] :tags {:context "coordinator" :role "judgment"}}
  "置き場に置いた Program の送り手の版(名の順の tuple)— task の版は詰めた Program と一緒に置いた版 1 つから取る(本文に版の写しを
   運ばせない・置く worker の版と比べる — can-run-task)。呼ぶ前に task-body-refusal が置き場に在ることを確かめる。"
  (setv row (.row state.programs sha))
  (when (is row None)
    (raise (KeyError sha)))
  (component-versions-of row.versions))


(deff needs-refusal [#^ (| dict list tuple str int float bool None) needs #^ (| dict list tuple str int float bool None) requires]  ; defk にできない: HTTP の本文を読む境界(Program の外)が呼ぶ純粋な判断
  {:pre [(: needs (| dict list tuple str int float bool None)) (: requires (| dict list tuple str int float bool None))] :post [(: % (| str None))] :tags {:context "coordinator" :role "judgment"}}
  "本文の needs(と旧い形の requires)が受けられない理由(受けられれば None)— 400 の理由の文を 1 か所で作るため。"
  (try
    (needs-named needs requires "needs")
    None
    (except [error ValueError]
      (str error))))


(defn #^ bool eligible [#^ ClusterJob job #^ WorkerInfo worker]
  "Service をこの worker に置けるか(pin と能力)。"
  (and (or (is job.pin None) (= job.pin worker.name))
       (placeable job.needs worker)))


;; probing(2026-09-27)= starting の手前の入口の検めの間(その worker で起こしかけている — 他へ置かない)。
;; elder-runs(AL-D2f)= 同じ名の前の世代が動かしている間、新しい世代が準備と検めを揃えて待っている(その worker で起こしかけている)。
(setv LIVE-PHASES #{"preparing" "probing" "starting" "elder-runs" "backoff" "running" "stopping" "stop-unconfirmed"})


(defn #^ tuple service-rows [#^ int now #^ ClusterState state #^ str name #^ ClusterTiming timing]
  "job name の process の行の母集団(worker の名の順): lease の内に報告した全部の worker の最新の報告のうち、名が一致する行と、
   入れ替えで退いた process の行(行の名は <名>#retired-<世代>・retiredFrom = 名)。「まだどこかで動いているか」(still-live-somewhere)と
   「どの版が動いているか」(resource_policy.live-processes)が同じ母集団を読むための定義点。"
  (tuple (gfor #(wname st) (sorted (.items state.observations.statuses))
               :setv w (.row state.workers wname)
               :if (and (is-not w None) (alive now w timing.lease-ms))
               row st.jobs
               :if (or (= row.name name) (= row.retired-from name))
               row)))


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


(defn #^ bool can-take [#^ int now #^ ClusterState state #^ ClusterJob job #^ WorkerInfo w #^ dict load #^ ClusterTiming timing
                        #^ (| frozenset None) [draining None]]
  "job(と並べた置き先 surge)を新しく置ける worker か(生きている・条件を満たす・drain 中でない・job の側の空き job-room-of と全体の
   空き task-room-of の両方が在る — task のために空けておく分 task-reserve には置かない)。load = load-of の答え(worker の名 → WorkerLoad)。"
  (and (alive now w timing.lease-ms) (eligible job w)
       (not-in w.name (if (is draining None) (draining-workers state now) draining))
       (> (run (job-room-of w load)) 0)
       (> (run (task-room-of w load)) 0)))


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
   (register-heartbeat が先に分ける — 旧い世代の heartbeat が新しい世代の drain を解かず、旧い世代の drain を付け直さない)。"
  (setv d (.row state.drains name))
  (cond
    (or (is d None) (is boot None) (is d.boot None)) state
    (!= d.boot boot) (replace state :drains (.with-writes state.drains (tuple [(TableWrite name None)])))
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
  (val prefixes (! (generation-holder-prefixes report)))
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


(defn #^ dict load-of [#^ ClusterState state #^ (get Table Placement) placements]
  "worker ごとの担っている数(worker の名 → WorkerLoad): jobs = 常駐の job の置き先(placements)と並べた置き先(surge)・tasks = 置かれた
   task(PLACED-PHASES)。置ける空きは job-room-of・task-room-of がこの値から求める(数え方を 1 か所にする)。"
  (setv jobs (dfor name (.keys state.workers) name 0) tasks (dfor name (.keys state.workers) name 0))
  (for [a (+ (list (.rows placements)) (list (.rows state.surges)))]
    (when (in a.worker jobs) (+= (get jobs a.worker) 1)))
  (for [t (scan-rows state.tasks)]
    (when (and (in t.phase PLACED-PHASES) (in t.worker tasks)) (+= (get tasks t.worker) 1)))
  (dfor name (.keys state.workers) name (WorkerLoad :jobs (get jobs name) :tasks (get tasks name))))


(defk job-room-of [worker load]
  {:pre [(: worker WorkerInfo) (: load (get dict #(str WorkerLoad)))] :post [(: % int)] :tags {:context "coordinator" :role "judgment"}}
  "worker に常駐の job と並べた置き先(surge)をあと幾つ置けるかを知るため: (capacity − task-reserve) − (job の置き先 + surge の数)。
   task のために空けておく分(task-reserve)は job の側から引く — 常駐の job が枠を埋めても task の置き場が残る。load = load-of の答え
   (worker の名 → WorkerLoad)。0 以下なら job の側に空きが無い(置き先の判断 place-jobs・movable?・drain の move-target はどれも can-take を
   通してこの値を読む)。"
  (val used (get load worker.name))
  (- (- worker.capacity worker.task-reserve) used.jobs))


(defk task-room-of [worker load]
  {:pre [(: worker WorkerInfo) (: load dict)] :post [(: % int)] :tags {:context "coordinator" :role "judgment"}}
  "worker に task(と job)をあと幾つ置けるかを知るため: capacity − (job の置き先 + surge の数 + 置かれた task の数)。task は予約
   (task-reserve)を使い切ったら job の残りへはみ出してよいので、capacity 全体から数える。job と surge を置くにも、この値が 0 より大きい事が
   要る(can-take)。load = load-of の答え(worker の名 → WorkerLoad)。task の置き先(place-tasks)と GET /state の taskRoom はこの値を読む。"
  (val used (get load worker.name))
  (- worker.capacity (+ used.jobs used.tasks)))


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
                             :if (and (is-not placed None) (= placed.worker worker) (not job.spec.handoff) (eligible job info))
                             job))))
  (val crowded (any (gfor job mine w (.rows state.workers)
                          (and (!= w.name worker) (alive now w timing.lease-ms) (eligible job w) (not-in w.name draining)))))
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
   いまの負荷は「他へ移せるか」の判断だけが読む — drain 中の worker も期限を過ぎて沈黙した担い手も無ければ求めない(#2655 — 調停の
   1 周ごとの費用)。"
  (val draining (draining-workers state now))
  (val stale (any (gfor a (.rows state.placements)
                        :setv w (.row state.workers a.worker)
                        (and (is-not w None) (not (alive now w timing.reassign-after-ms))))))
  (val load (if (or draining stale) (load-of state state.placements) {}))
  (var kept {})
  (for [job (active-jobs state)]
    (val current (.row state.placements job.spec.name))
    (val worker (if (is current None) None (.row state.workers current.worker)))
    (when (is-not worker None)
      (<- mark (| KeepMark None) (keep-mark-of state.keep-marks job.spec.name))
      (val promised (and (is-not mark None) (= mark.worker current.worker)))
      (val fit (eligible job worker))
      (val leaving (and (in current.worker draining) (not job.spec.handoff)))
      (val awake (alive now worker timing.reassign-after-ms))
      (var free False)
      (when (and (not promised) fit (or leaving (not awake)))
        (<- movable bool (movable? now state job current.worker load timing draining))
        (:= free movable))
      (when (or promised (and fit (not (and leaving free)) (or awake (not free))))
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


(defk remember-keep-marks [state worker boot held reply now]
  {:pre [(: state ClusterState) (: worker str) (: boot (| str None)) (: held (| tuple None)) (: reply HeartbeatReply) (: now int)]
   :post [(: % ClusterState)] :tags {:context "coordinator" :role "judgment"}}
  "heartbeat の返事で印を付けた job を、印を渡した担い手との約束として状態に残すため(返事を送る前に状態と一緒に保存される)。印を知る
   worker(本文に keptWhenCutOff の欄が在る — held が None でない)だけ — 古い worker は印を読まずに fence で止めるので約束しない。
   同じ担い手への約束は初めて渡した時刻を保つ。約束の列は job の名の順・job ごとに 1 つ。変わらなければ同じ状態を返す。"
  (val marked (frozenset (gfor spec reply.jobs :if spec.keep-when-cut-off spec.name)))
  (val fresh (tuple (gfor name (sorted marked)
                          :setv previous (next (gfor m state.keep-marks :if (= m.job name) m) None)
                          :if (not (and (is-not previous None) (= previous.worker worker) (= previous.boot boot)))
                          (KeepMark :job name :worker worker :boot boot
                                    :since-ms (if (and (is-not previous None) (= previous.worker worker)) previous.since-ms now)))))
  (val renewed (frozenset (gfor f fresh f.job)))
  (if (or (is held None) (not fresh))
      state
      (replace state :keep-marks (tuple (sorted (+ (tuple (gfor m state.keep-marks :if (not-in m.job renewed) m)) fresh)
                                                :key (fn [m] m.job))))))


(defk sweep-keep-marks [state now timing]
  {:pre [(: state ClusterState) (: now int) (: timing ClusterTiming)] :post [(: % ClusterState)] :tags {:context "coordinator" :role "judgment"}}
  "外してよい約束を外すため(外した約束の job は、置き先の判断が他へ移せる): 消された・忘れた Worker への約束(Worker の削除は
   「その worker はもう動いていない」という明示の宣言)と、担い手の沈黙が約束の期限(ClusterTiming.kept-reassign-after-ms)を越えた約束
   (担い手は印の在る job も途絶が長い方の柵 keep-fence-ms を越えると自分で止め、期限はその止め切りの最悪より後 — 担い手の名が替わる
   切り替えで、旧い名の worker が二度と知らせて来ない時も外れる)。生死の比べは alive で、期限の刻は liveness-due が同じ値で求める。
   外す物が無ければ同じ状態を返す。"
  (val held (tuple (gfor mark state.keep-marks
                         :setv holder (.row state.workers mark.worker)
                         :if (and (is-not holder None) (alive now holder timing.kept-reassign-after-ms))
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
  ;; 置いた後の負荷は担い手の無い job を置く時だけ読む — 全部の job が割り当てを保っていれば求めない(#2655)。置けるかは job の側の空きと
  ;; 全体の空きの両方(can-take — task のために空けておく分には置かない)・並びは担っている数(job と task の和)の少ない順。
  ;; 全部の job が割り当てを保っていれば load は読まれない(None)。
  (setv load (if (all (gfor job jobs (in job.spec.name kept))) None (load-of state (table-of (tuple (gfor #(n a) (.items kept) (TableWrite n a)))))))
  ;; 保った割り当ては state.placements の行そのもの(書かない)。新しく置く物だけ fresh に貯める。
  (setv taken (set kept) fresh [])
  (for [job (sorted jobs :key (fn [j] j.spec.name))]
    (when (in job.spec.name taken) (continue))
    (setv surge (.row state.surges job.spec.name)
          surge-worker (if surge (.row state.workers surge.worker) None))
    (when (and (is-not surge-worker None) (alive now surge-worker timing.lease-ms) (eligible job surge-worker))
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
            (get load chosen.name) (replace used :jobs (+ used.jobs 1)))
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
   入れ替えを諦めた job(state.handoffs の ABANDONED — handoff_policy)には諦めの印を載せる(worker は新を止めて旧を残す)。"
  (tuple (gfor job state.jobs
               :setv placed (.row state.placements job.spec.name)
               :setv surge (.row state.surges job.spec.name)
               :setv a (cond (and placed (= placed.worker worker)) placed
                             (and surge (= surge.worker worker) (> job.replicas 0)) surge
                             True None)
               :if a
               :setv watch (.row state.handoffs job.spec.name)
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
    (not (any (gfor w (.rows state.workers) (and (alive now w timing.lease-ms) (eligible job w)
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
        :if (is (.row state.placements job.spec.name) None)
        job.spec.name
        (unplaced-text (unplaced-kind now state job timing) job)))


(defk task-unplaced-text [kind]
  {:pre [(: kind TaskUnplacedKind)] :post [(: % str)] :tags {:context "coordinator" :role "judgment"}}
  "待っている task を置けない理由の種類 → task の detail に書く文を決めるため(place-tasks が書く・GET /state の task の detail に出る)。
   文は種類ごとに固定 — 待つ間に調停が拍ごとに書き換えて保存の版を進めない。"
  (match kind
    TaskUnplacedKind.TASK-NO-ROOM
      "能力と版の合う worker は在るが、どれも task を置ける空きが無い(常駐の job と置かれた task で capacity が埋まっている)— 空くまで待つ"
    TaskUnplacedKind.TASK-LIMITED
      "task の名乗った組(limits)の同時数が系の宣言の上限に達している — 同じ組の task が終わるまで待つ"
    TaskUnplacedKind.TASK-RESERVED
      "負の優先の task(背景の仕事)は worker の task-reserve を使わない — 予約を越える空きができるまで待つ"
    TaskUnplacedKind.TASK-UNDECLARED
      "task の名乗った組(limits)が系の宣言(資源 GroupLimit)に無い — 組の上限が宣言されるまで待つ"))


(defk limit-slot [limit declared worker]
  {:pre [(: limit TaskLimit) (: declared GroupLimit) (: worker (| str None))] :post [(: % tuple)] :tags {:context "coordinator" :role "judgment"}}
  "task の名乗った組 1 つを数える升の鍵を知るため(置いている数を升ごとに数え、置く前に比べる): #(組 区分 worker)。区分は宣言が区分ごとに
   数える組(per-part)の時だけ task の part・worker は宣言の範囲が worker の時だけ置いた worker(cluster の範囲は None)。範囲と区分の
   読みは task の名乗りでなく宣言 declared が決める(card ki-b0e8028c8c8d — 以前は task の名乗った範囲で数えた)。"
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
   ため(値の持ち主は系の宣言だけ — 設計検証 lt-9E0W15ZW21H6R3SA26CNW1T4FP の盲検 B。引数は test_group_limits_owner が固定する)。"
  (var after (dict held))
  (for [l limits :setv declared (.row groups l.group) :if (is-not declared None)]
    (<- slot tuple (limit-slot l declared worker))
    (:= after (| after {slot (+ (.get after slot 0) 1)})))
  after)


(defk foreground-reserve-of [worker]
  {:pre [(: worker WorkerInfo)] :post [(: % int)] :tags {:context "coordinator" :role "judgment"}}
  "worker の task-reserve のうち、負の優先の task(背景の仕事)に使わせず優先 0 以上の task に残す数を知るため: 予約の半分(切り上げ)。
   背景の仕事は残りの半分(切り捨て)までは予約を使える(card ki-f6d3da302cf0・2026-10-14)。以前は予約を 1 つも使わせず、背景の仕事の
   置き場は job の側の残り(capacity − task-reserve − 常駐の job)だけだった — 常駐の job は capacity − task-reserve まで置ける(C17)ので、
   job が増えて job の側を埋めると背景の仕事は 1 本も置かれず、本番の zeus(capacity 33・task-reserve 6・常駐の job 27)で会話の
   long-command 8 本が 76〜230 分 queued のまま待った。予約の中に背景の仕事の分を持てば、job の数によらず背景の仕事が走り、
   優先 0 以上の task(配備の組み立て・日次の検)には予約の半分が必ず残る(10-11 の、背景の仕事が枠を埋めて組み立てが待った件の守り)。"
  (- worker.task-reserve (// worker.task-reserve 2)))


(defk task-misfit [limits priority worker room held groups]
  {:pre [(: limits tuple) (: priority int) (: worker WorkerInfo) (: room int) (: held dict) (: groups Table)]
   :post [(: % (| TaskUnplacedKind None))]
   :tags {:context "coordinator" :role "judgment"}}
  "能力と版の合う worker 1 台に、待っている task(名乗った組 limits・優先 priority — task の行は受けない)を今置けない理由を知るため
   (None = 置ける)。room = その worker の task の空き(task-room-of)・held = 置いている task の組の升ごとの数(limit-holdings)・
   groups = 系が宣言した GroupLimit の表。名乗った組の
   どれかが宣言に無い → TASK-UNDECLARED・空きが無い → TASK-NO-ROOM・名乗った組のどれかの升が宣言の値に達している → TASK-LIMITED・
   負の優先の task で、空きが予約のうち優先 0 以上の task に残す数(foreground-reserve-of)以下 → TASK-RESERVED(2026-10-11・残す数は
   2026-10-14 に予約の全部から半分へ)。比べる値は宣言の値で、task の名乗りに値は無い(card ki-b0e8028c8c8d)。"
  (<- by-limits (| TaskUnplacedKind None) (limits-misfit limits worker.name held groups))
  (<- kept int (foreground-reserve-of worker))
  (cond
    (= by-limits TaskUnplacedKind.TASK-UNDECLARED) by-limits
    (<= room 0) TaskUnplacedKind.TASK-NO-ROOM
    by-limits by-limits
    (and (< priority 0) (<= room kept)) TaskUnplacedKind.TASK-RESERVED
    True None))


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
   宣言に無い組 → 上限 → 予約 → 空きの順に、待てば晴れる理由の具体な物を名乗る。"
  (cond
    (in TaskUnplacedKind.TASK-UNDECLARED kinds) TaskUnplacedKind.TASK-UNDECLARED
    (in TaskUnplacedKind.TASK-LIMITED kinds) TaskUnplacedKind.TASK-LIMITED
    (in TaskUnplacedKind.TASK-RESERVED kinds) TaskUnplacedKind.TASK-RESERVED
    True TaskUnplacedKind.TASK-NO-ROOM))


(defk limits-undeclared-refusal [groups value]
  {:pre [(: groups Table) (: value (| list None))] :post [(: % (| str None))] :tags {:context "coordinator" :role "judgment"}}
  "受け口が、task の本文の欄 limits(形を task-limits-refusal で検めた値)を系の宣言と照らして断る理由を知るため(None = 受ける):
   宣言に無い組・区分ごとに数える組(per-part)で区分を名乗らない・区分ごとに数えない組で区分を名乗る。groups = 系が宣言した
   GroupLimit の表(card ki-b0e8028c8c8d — 送り手は値を送らず、coordinator は宣言の値で数える)。"
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
   limit-holdings)と、その組を名乗って待っている task の数。運用者が、値が宣言のとおりに効いているかを 1 か所で読む(card ki-b0e8028c8c8d)。"
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


(defk settle-detached [task now lapsed]
  {:pre [(: task TaskRecord) (: now int) (: lapsed bool)] :post [(: % (| TaskRecord None))] :tags {:context "coordinator" :role "judgment"}}
  "純粋: 切り離した task 1 本の期限の判断。lapsed = 自分の期限(task-lapse-at の刻)が来た。保持の期限を過ぎた終わりの行は None(消す)。
   置いた task の lease(担い手の worker の heartbeat が延ばす)が切れた = worker の死 = lost(走らせ直さない)。
   呼び手の問い合わせは lease に触らない(呼び手が消えても task は続く)。"
  (cond
    (in task.phase DETACHED-TERMINAL)
      (if lapsed None task)
    (and (in task.phase PLACED-PHASES) lapsed)
      (end-detached task "lost" now
                    (.format "担い手の worker {} の lease が切れた(worker の死とみなす — task は走らせ直さない)" task.worker))
    True task))


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
   lost)。待っている切り離した task は自分の期限では変わらない(None — 待ちの期限は wait-lapse-at)。"
  (match task
    (TaskRecord :detached False) (+ task.lease-until-ms 1)
    (TaskRecord :phase phase) :if (in phase DETACHED-TERMINAL) (+ (or task.finished-ms now) task.retain-ms 1)
    (TaskRecord :phase phase) :if (in phase PLACED-PHASES) (+ task.lease-until-ms 1)
    _ None))


(defn #^ bool held-task? [#^ TaskRecord task]
  "切り離した終わりの行(結果を保持しているだけ)か。切り離していない終わった行は含まない(lease を延ばされ走査の対象のまま)。"
  (and task.detached (in task.phase DETACHED-TERMINAL)))


(defn #^ list scan-rows [#^ (get Table TaskRecord) tasks]
  "走査の対象の行(held-task? でない行 — 結果を保持しているだけの切り離した終わりを除く)を id の順で。数え・並べの読みが保持側の行を回さないため
   (task-split の走査の対象と同じ行 — 期限の最小は要らない読みの口)。"
  (lfor t (.rows tasks) :if (not (held-task? t)) t))


(defk task-split [tasks now]
  {:pre [(: tasks (get Table TaskRecord)) (: now int)] :post [(: % tuple)] :tags {:context "coordinator" :role "judgment"}}
  "1 走査で tasks を、走査の対象の行(held-task? でない行 — id の順を保つ dict)と、保持側の最小期限(task-lapse-at と同じ式 —
   終わった刻 finished-ms を持たない行は除く。無ければ None)に分けるため。#(走査の対象 最小期限) を返す。"
  (val live {})
  (var earliest None)
  (for [#(id task) (.items tasks)]
    (if (held-task? task)
        (when (is-not task.finished-ms None)
          (val lapse (+ task.finished-ms task.retain-ms 1))
          (when (or (is earliest None) (< lapse earliest)) (:= earliest lapse)))
        (setv (get live id) task)))
  #(live earliest))


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
        (do (<- kept (| TaskRecord None) (settle-detached task now lapsed))
            (setv (get writes id) kept))
      ;; 呼び手が問い合わせを止めた(止まった)= task も要らない。担い手は次の heartbeat で子 process を止める。
      lapsed (setv (get writes id) None)
      (and (in task.phase PLACED-PHASES)
           (or (is (.row state.workers task.worker) None)
               (not (alive now (.row state.workers task.worker) timing.reassign-after-ms))))
        (setv (get writes id) (replace task :phase "failed" :finished-ms now
                                       :detail (.format "担い手の worker {} が沈黙した(task は走らせ直さない)" task.worker)))))
  ;; 走査の後の行の読み(書いた行が先・無ければ元の行)。
  (for [#(id task) (.items live)]
    (setv now-row (if (in id writes) (get writes id) task))
    (when (and (is-not now-row None) (= now-row.phase "queued"))
      (setv (get queue id) now-row)))
  ;; 保持側の行は書かず元の object のまま残す。期限(finished-ms + retain-ms + 1 — task-lapse-at と同じ式)が来た行は落とす(期限の来た拍だけ
  ;; 全行を読む)。finished-ms の無い行は毎拍 now から数えるので落ちない。
  (when (and (is-not earliest None) (>= now earliest))
    (for [task (.rows state.tasks)]
      (when (and (held-task? task) (is-not task.finished-ms None) (>= now (+ task.finished-ms task.retain-ms 1)))
        (setv (get writes task.id) None))))
  ;; 待っている task が無ければ、置く判断(負荷・drain 中の worker・状態の写し)を組まない — 調停の 1 周ごとに状態全体を写す費用が
  ;; 模擬の拍の大半だった(#2655 の profile: replace 135,053 回・2.3 秒)。
  (when (not queue)
    (return (task-writes state.tasks writes)))
  (setv placed (replace state :tasks (.with-writes state.tasks (task-writes state.tasks writes))))
  (setv load (load-of placed placements)
        draining (draining-workers state now))
  ;; 置いている task の上限の升ごとの数(置くたびに足す — 同じ拍で置いた分も数える)。
  (var held (! (limit-holdings (! (placed-claims placed.tasks)) state.group-limits)))
  ;; 置く順 = 優先の大きい順、同じ優先の中は id の順(2026-10-11 — 以前は id の順だけで、先に積んだ背景の仕事が空いた枠を先に取った)。
  (for [id (sorted queue :key (fn [i] #((- (. (get queue i) priority)) i)))]
    (setv task (get queue id))
    (when (= task.phase "queued")
      ;; drain 中の worker には新しい task を置かない(置ける先が他に無ければ、drain が解けるまで待つ — 失敗にはしない)。
      (setv able (lfor w (.rows state.workers) :if (and (alive now w timing.lease-ms) (can-run-task task w)) w)
            ;; drain 中と、disk の尽きた worker(準備済みでない env の task)は避ける — 置ける先が他に無ければ待つ。
            able-now (lfor w able :if (and (not-in w.name draining) (env-room-on task w)) w))
      ;; task を置けるのは task の空き(task-room-of — capacity 全体から job・surge・置かれた task を引いた数)が在り、名乗った上限に
      ;; 達しておらず、負の優先なら予約を越える空きの在る worker だけ(task-misfit)。置けない worker の理由は待ちの文に使う。
      (var roomy #())
      (var misfits [])
      (for [w able-now]
        (<- room int (task-room-of w load))
        (<- misfit (| TaskUnplacedKind None) (task-misfit task.limits task.priority w room held state.group-limits))
        (if (is misfit None)
            (:= roomy #(#* roomy w))
            (:= misfits [#* misfits misfit])))
      ;; 実行環境の task は、その env を準備済みの worker を優先する(空きの多さより先 — 準備を task の待ちに入れない・2026-09-26)。
      ;; 次は担っている数(job と task の和)の少ない順。
      (setv free (sorted roomy :key (fn [w] #((not (env-ready-on task.runtime-env w))
                                              (+ (. (get load w.name) jobs) (. (get load w.name) tasks)) w.name))))
      (cond
        free (do (setv chosen (get free 0)
                       ;; 準備済みの worker が無い置き先 = 冷たい起動(worker が準備してから走る)。phase を preparing にして assigned と分ける。
                       phase (if (and (is-not task.runtime-env None) (not (env-ready-on task.runtime-env chosen)))
                                 "preparing" "assigned")
                       used (get load chosen.name)
                       (get load chosen.name) (replace used :tasks (+ used.tasks 1)))
                 (<- after-hold dict (hold-limits held task.limits chosen.name state.group-limits))
                 (:= held after-hold)
                 ;; 切り離した task は置いた worker の process の世代を覚え、lease を置いた時から数える。
                 (setv (get writes id) (if task.detached
                                          (replace task :phase phase :worker chosen.name :started-ms now
                                                   :boot chosen.boot :lease-until-ms (+ now task.lease-ms))
                                          (replace task :phase phase :worker chosen.name :started-ms now))))
        ;; 置ける worker(drain 中でも disk 尽きでもない)は在るが、どれにも今置けない(空きが 0・上限・予約): 失敗にせず待つ。
        ;; 理由は閉じた語(misfit-reason)の固定の文(task-unplaced-text)。既に同じ文なら書き換えない(調停が拍ごとに保存して版を進めない)。
        ;; drain 中・disk 尽きで置ける先が 1 つも無い(able-now が空)時は、今までどおり理由を書かずに待つ(空きの話ではない)。
        able-now
          (let [text (! (task-unplaced-text (! (misfit-reason misfits))))]
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
                      (replace task :detail (.format "要る能力 {} の worker {} がいま連絡していない — 連絡が戻るまで待つ(待ちの期限 = 最後の連絡から {} 秒)"
                                                     (list task.needs) names limit-s))
                    task.detached (end-detached task (unplaceable-phase task state now timing) now ended)
                    True (replace task :phase "failed" :finished-ms now :detail ended)))))))
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
  (tuple (gfor task (sorted (scan-rows state.tasks) :key (fn [t] t.id))
               :if (and (in task.phase PLACED-PHASES) (= task.worker worker) (same-boot task boot))
               (TaskOffer :id task.id :name task.name :revision task.revision :versions task.versions :program task.program
                          :detached task.detached :key task.key :lease-ms task.lease-ms :retain-ms task.retain-ms :needs task.needs
                          :runtime-env task.runtime-env :environ task.environ))))


(defn #^ TaskRecord task-finished [#^ TaskRecord task #^ int now #^ str detail #^ (| str None) result]
  "純粋: 子 process が結果を持って終わった task の記録(切り離した task は終わりの phase — end-detached)。heartbeat の報告と子 process の
   直の届け(absorb-task-result)が同じ形で終える。result = 詰めた結果(None = 結果なし — 切り離した task では呼ばない)。"
  (if task.detached
      (end-detached task "finished" now detail result)
      (replace task :phase "finished" :finished-ms now :result result :detail detail)))


(defn #^ TaskRecord absorb-detached-report [#^ TaskRecord task #^ StatusRow status #^ int now]
  "切り離した task の終わりの報告 → 終わりの phase。結果を書かずに終わった子 process は lost(結果が無い = 消失)。"
  (setv phase status.phase detail status.detail)
  (cond
    (and (= phase "finished") (is-not status.result None))
      (task-finished task now detail status.result)
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
          task.detached (put-row draft (absorb-detached-report task status now))
          (= phase "finished")
            (put-row draft (task-finished task now status.detail status.result))
          (= phase "code-failed")
            (put-row draft (replace task :phase "code-failed" :finished-ms now :detail status.detail))))))
  (when stopping
    (setv running (frozenset (gfor status statuses :if (.startswith status.name "task/") (cut status.name 5 None))))
    (for [id (sorted (.keys draft))]
      (setv task (.row draft id))
      (when (and (in task.phase PLACED-PHASES) (= task.worker worker) (same-boot task boot) (not-in id running))
        (put-row draft
                 (if (and task.detached task.reported)
                     ;; 行を報告した後に消えた = 止まり始めた worker が走っていた子 process を止めた。切り離した task は走らせ直さない
                     ;; (置き直すと同じ名の次の世代でもう 1 度走った — 2026-10-10 17:56 の作り直しの後)。
                     (end-detached task "lost" now
                                   (.format "担い手の worker {} が止まり始め、走っていた子 process を止めた — 走らせ直さない" worker))
                     (replace task :phase "queued" :worker None :boot None :started-ms None
                              :detail (.format "担い手の worker {} が始める前に止まり始めた — 置き直しを待つ" worker))))))))


(defn #^ None renew-detached-into [#^ (get TableDraft TaskRecord) draft #^ str worker #^ (| str None) boot #^ int now]
  "純粋: 担い手の heartbeat: その worker に置いた切り離した task の lease を延ばす書きを下書き draft に貯める(lease は worker が延ばす)。
   延ばすのは置いた時と同じ process の世代の heartbeat だけ。別の世代の heartbeat では何もしない — 置いた世代の process が消えていれば
   lease 切れで lost(settle-detached・走らせ直さない)。以前は別の世代の heartbeat が来た拍に lost にしていたが、同じ名の 2 つの世代が
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
  "純粋: 期限を過ぎた盤の行を消した状態(期限つきの行が無ければ同じ object)。"
  (setv gone (tuple (gfor #(k row) (.items state.board) :if (and (is-not row.expires-ms None) (<= row.expires-ms now)) k)))
  (if (not gone)
      state
      (board-written state (tuple (gfor k gone (TableWrite k None))))))


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
   (sweep-keep-marks — 要求の無い間も、その刻に起きて約束を外す)・worker-forget-ms(forget-silent-workers)。答え = worker ごと・窓ごとの
   「期限 + 1 ms」のうち now より後の最小(どれも過ぎていれば DueNever)。生きていないと数える名(silent)が今の刻の求め直しと違えば、
   note-liveness が今の刻で答えを変えるので DueNow。"
  (val windows #(timing.lease-ms timing.reassign-after-ms timing.keep-fence-ms timing.kept-reassign-after-ms timing.worker-forget-ms))
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
  (for [task (.values scanned)]
    (<- lapse (| int None) (task-lapse-at task now))
    (val capable (if (= task.phase "queued") (lfor w (.rows state.workers) :if (can-run-task task w) w) []))
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
  "掃除の判断(盤の行・drain・温める表・詰めた Program)が、状態がこのままで答えを変え得る最初の刻を知るため(#3063)。値は各判断が
   比べに使う期限と同じ: sweep-board = 行の expires-ms・sweep-drains(と advance-drains の drain 中の判定)= until-ms・sweep-warms = until-ms・
   program_policy.sweep-programs = 参照の無い Program の put-ms + PROGRAM-GRACE-MS を過ぎた刻。途絶しても動かし続けてよい印の約束を
   外す sweep-keep-marks の刻は担い手の沈黙の期限(kept-reassign-after-ms)なので liveness-due が求める・冷えた起動の数は数えるだけなので、
   刻を持たない。"
  (val used (if (> (.size state.programs) 0) (program-refs state) (frozenset)))
  (val deadlines (+ (lfor row (.rows state.board) :if (is-not row.expires-ms None) row.expires-ms)
                    (lfor d (.rows state.drains) d.until-ms)
                    (lfor w (.rows state.warms) w.until-ms)
                    (lfor #(sha row) (.items state.programs) :if (not-in sha used) (+ row.put-ms PROGRAM-GRACE-MS 1))))
  ;; 掃いた後の状態なら期限はどれも now より後。掃く前の状態(期限が now 以前)は DueNow(sweep が今の刻で変える)。
  (<- answer (| DueAt DueNow DueNever) (due-of-instants now (tuple deadlines)))
  answer)


(defk reconcile [now given timing]
  {:pre [(: now int) (: given ClusterState) (: timing ClusterTiming)] :post [(: % ClusterState)] :tags {:context "coordinator" :role "judgment"}}
  "1 拍の調停: 期限を過ぎた物(盤の行・drain・温める表の行・沈黙した worker・消えた Worker と沈黙が約束の期限を越えた担い手への約束)を
   掃いてから、job と task の置き先を決め直し、置き先の変化を出来事の列に足した状態を求めるため。何も変わらなければ掃いた後の状態
   そのものを返す。"
  ;; 消された・忘れた Worker と、沈黙が約束の期限(kept-reassign-after-ms)を越えた担い手への途絶しても動かし続けてよい印の約束は、
  ;; 置き先の判断の前に外す(#2804 — その job を他へ置ける)。
  (<- state ClusterState
      (sweep-keep-marks (! (forget-silent-workers (! (sweep-warms (! (sweep-drains (! (sweep-board given now)) now)) now)) now timing))
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
                 (or (is b None) (!= #(b.provides b.exclusive b.derived b.node b.capacity b.task-reserve b.versions b.boot b.retired b.boot-at
                                       b.known-exits)
                                     #(w.provides w.exclusive w.derived w.node w.capacity w.task-reserve w.versions w.boot w.retired w.boot-at
                                       w.known-exits)))))))


(defn #^ list board-changes [#^ ClusterState before #^ ClusterState after]
  "書き直しが要る盤の行の鍵(書かれた・消えた)。変わらない行は同じ object のまま引き継がれるので、同一性で比べる(盤全体を
   値で比べない — 大きな shadow の盤は 2.3 MB)。"
  (if (is before.board after.board)
      []
      (+ (lfor #(k v) (.items after.board) :if (is-not (.row before.board k) v) k)
         (lfor k (.keys before.board) :if (is (.row after.board k) None) k))))


;; --- HTTP の要求への返事(判断の部品。要求の振り分けは api_policy) -----------------------------------

(deff text-map? [value]  ; defk にできない: HTTP の本文を読む境界(Program の外)が呼ぶ純粋な判断
  {:pre [(: value (| dict list str int float bool None))] :post [(: % bool)] :tags {:context "coordinator" :role "judgment"}}
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


(defk worker-report [rows now endpoint known]
  {:pre [(: rows tuple) (: now int) (: endpoint (| str None)) (: known tuple)]
   :post [(: % WorkerReport)] :tags {:context "coordinator" :role "judgment"}}
  "今の世代か新しい世代の heartbeat の状態の報告の行 rows から、worker の最新の報告(ClusterObservations.statuses の行)を作るため。行は結果の
   欄 result と task の写しを外し(持ち続けるのは process の姿だけ)、last-exit-at-ms を最後に終わったと知れた刻にする(known =
   この heartbeat の後の WorkerInfo.known-exits — 同じ worker・同じ job の名では前に知っていた刻から戻さない)。result も task も
   持たない行(service の job の行)は同じ値のまま運ぶ(#3774)。"
  (WorkerReport :at now :endpoint endpoint
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
              name boot statuses now)))
  (setv envs (or body.envs (EnvsReport))
        caps (named-capabilities body.provides body.exclusive (is-not body.labels None) (.format "worker {} の名乗り" name))
        node body.node
        info (WorkerInfo name (tuple (gfor c (get caps 0) :if (not-in c adopted.derivable) c))
                         body.capacity now
                         (component-versions-of (or body.versions {}))
                         boot
                         (component-versions-of (or body.tools {}))
                         ;; task のために空けておく数(本文の必ずの欄 — 0 以上 capacity 以下は本文の型が検める)。
                         :task-reserve body.task-reserve
                         :platform body.platform
                         :env-ready (frozenset envs.ready)
                         :env-preparing (frozenset envs.preparing)
                         :env-failed (tuple (gfor f envs.failed (EnvFailed f.key f.kind f.detail f.retryable)))
                         :env-memory-unmeasured (frozenset envs.memory-unmeasured)
                         :env-capacity body.env-capacity
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
        report (! (worker-report statuses now body.endpoint known))
        ;; 新しい世代の名乗りで、前の世代の process が持っていた名前付きの lease を外す(#3770 — 前の世代の最新の報告は、この
        ;; heartbeat で置き換える前の adopted.observations.statuses の行)。前の世代がまだ走っている間に起きた世代(overlaps-elder — RollingUpdate・
        ;; AL-D2e)は外さない: 前の世代の process は走り続け、延長を通し、終わる時に返すか期限で戻す。
        previous-report (.row adopted.observations.statuses name)
        freed (if (and (= order GenerationOrder.NEWER) (not body.overlaps-elder))
                  (! (board-without-generation-holders adopted.board previous-report))
                  #())
        board-state (board-written adopted freed)
        ;; absorb-boot は drain だけを変えるので、観測は adopted のまま書いてよい。
        registered (replace (absorb-boot adopted name boot)
                     :workers (.with-writes adopted.workers #((TableWrite name info)))
                     :observations (replace adopted.observations
                                            :statuses (.with-writes adopted.observations.statuses #((TableWrite name report))))
                     :board board-state.board :board-tally board-state.board-tally)
        ;; 前の世代がまだ走っている間に起きた世代の名乗り(AL-D2f): 前の世代の最新の報告の running の常駐の job を、前の世代が動かして
        ;; いる組(SupersededRun)の種にする — 前の世代の次の heartbeat を待たずに、今の世代への最初の返事から印 elder-runs を付ける。
        seeded (if (and (= order GenerationOrder.NEWER) body.overlaps-elder (is-not previous None) (is-not previous.boot None)
                        (is-not previous-report None))
                   (replace registered :superseded-runs (superseded-runs-after registered name previous.boot previous-report.jobs now
                                                                               :overlapped True))
                   registered))
  ;; 今の世代(か新しい世代)の知らせた「今持っている印」で、印を持たなくなった job の約束を外す(#2804 — 退いた世代の heartbeat は
  ;; 上で抜けるので約束に触らない)。止まり始めの名乗り(body.stopping・#2819)は、その世代に置いて始まっていない task を
  ;; 置き直しの待ちへ戻す(absorb-task-reports・#2976 の I-3 の赤 R5)— 退いた世代の heartbeat はここへ来ないので戻さない。
  (replace seeded :tasks (heard-tasks seeded name statuses now boot body.stopping info)
                      :keep-marks (! (released-keep-marks seeded.keep-marks name body.kept-when-cut-off))))


(defn #^ ClusterState absorb-superseded-heartbeat [#^ ClusterState state #^ str name #^ str boot #^ tuple statuses #^ int now]
  "退いた世代の process がまだ走らせている切り離した task を最後まで見届けるため: その世代に置いた task の終わりの報告と
   lease の延長だけを写す。worker の名乗り(生存・label・容量・版・世代)・job の状態の報告・drain は今の世代の物なので触らない。
   その世代が走らせている入れ替えの job の組(SupersededRun — 旧い Pod の preStop が待つ・AL-D2)も書き直す。"
  (replace state :tasks (heard-tasks state name statuses now boot False None)
                 :superseded-runs (superseded-runs-after state name boot statuses now)))


(defn #^ tuple superseded-runs-after [#^ ClusterState state #^ str name #^ str boot #^ tuple statuses #^ int now
                                      #^ (| bool None) [overlapped None]]
  ;; defk にできない: coordinator の純粋な判断(absorb-superseded-heartbeat — defn)が呼ぶ
  "純粋: 退いた世代 boot の報告 statuses の後の、退いた世代の走らせている常駐の job の組の列(AL-D2)。この世代の行を、報告の
   running の job の名で置き直す(空なら外す)。数える job は handoff の job、組が overlapped(新しい世代が重なりを名乗った — 名乗りの
   時に overlapped = 真で種を置く・それ以外の報告は前の組の印を引き継ぐ)なら recreate の job も(task は除く)— recreate の job は
   新しい世代が起こせると報告するまで退いた世代に残す(AL-D2f — heartbeat-reply の印 elder-runs と superseded-reply)。名 name の行の
   うち、もう退いた世代の列(WorkerInfo.retired)に無い世代の行も外す(覚えの上限 RETIRED-BOOTS-KEPT と同じ長さで捨てる)。"
  (setv info (.row state.workers name)
        kept (if (is info None) #() info.retired)
        prior (next (gfor r state.superseded-runs :if (and (= r.worker name) (= r.boot boot)) r) None)
        overlap (if (is overlapped None) (and (is-not prior None) prior.overlapped) overlapped)
        ;; 止まりの始まりの印(AL-D2k)は前の組から引き継ぐ(drain_policy.request-drain が付ける)。
        leaving (and (is-not prior None) prior.leaving)
        ;; 組を初めて書いた刻(受け渡しの待ちの始まり — card ki-d8e594fe934d)も引き継ぐ。欄の前に保存した組は、次の報告の刻から数える。
        since (if (and (is-not prior None) (is-not prior.since-ms None)) prior.since-ms now)
        counted (frozenset (gfor j state.jobs :if (if overlap (not j.spec.once) j.spec.handoff) j.spec.name))
        jobs (tuple (sorted (& (running-names statuses) counted)))
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
        environ (.get echo "environ" {}))
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
                   (WarmOffer :key w.key :runtime-env w.runtime-env)))))


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
  ;; revision = この返事を作った時の coordinator の版(#1933): worker は次の変化を GET /watch?after=<この版> で待つ。
  ;; keepWhenCutOff = 他へ移せない job に付ける「途絶しても動かし続けてよい」印(#2804 — keep-marked)。印を渡した事実は呼び手が
  ;; remember-keep-marks で約束として状態に残す。
  (<- marked frozenset (keep-marked now state name timing))
  ;; elder = 同じ名の前の世代がまだ動かしている常駐の job(AL-D2f)。その recreate の job には印 elder-runs を付ける — worker は準備と
  ;; 入口の検めだけをして起こさず、揃ったら状態の行を elder-runs にする(superseded-reply が前の世代から外す)。
  (val elder (if (and (is-not boot None) (> now 0)) (elder-held state name boot now timing.lease-ms) (frozenset)))
  ;; 前の世代の job をまだ受け取れない間は、worker が Pod の ready の file を書かない(AL-D2k — handover-pending)。
  (val pending (and (is-not boot None) (> now 0) (handover-pending state name boot now timing ready-instances)))
  ;; 退きの保ち(card ki-e3531030eb37 — readiness_model の RETIREMENT-HOLD): 入れ替えの job ごとに、期限の内の保ちの持ち手の世代の名を
  ;; 載せる(worker は列に在る退いた process を寿命の上限で止めない)。退いた世代への返事(superseded-reply)には載せない — その世代の
  ;; 退いた process は Pod の止まりの drain の上限(寿命の上限 + 5 分)の内で終わる。
  (val hold-row (.row state.board (semaphore-key RETIREMENT-HOLD)))
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
                  :revision state.revision :handover-pending pending))


;; 受け渡しで「今の世代が受け取れる」と数える recreate の job の phase(AL-D2k): 準備と検めが揃って待っている(elder-runs)か、もう
;; 動いているか、準備できないと確定した・機体の memory の見張りに続けて止められて起こし直さない(待っても揃わない — 入れ替えを止めない)。
(val HANDOVER-SETTLED-PHASES #{"elder-runs" "running" "code-failed" "probe-failed" "env-failed" "handoff-abandoned" "memory-killed"})


(defn #^ bool handover-pending [#^ ClusterState state #^ str name #^ str boot #^ int now #^ ClusterTiming timing
                                #^ (| dict None) ready-instances]
  ;; defk にできない: coordinator の純粋な判断(heartbeat-reply の値・drain_policy.worker-view — defn)が呼ぶ
  "純粋: 名 name の今の世代 boot が、同じ名の前の世代がまだ動かしている常駐の job をまだ受け取れないか(AL-D2k)。止めている job の
   判断は handover-blockers(この関数はその有無だけを読む — 止まりの名指しと同じ判断)。真の間、worker は Pod の ready の file を
   書かず、Deployment は前の世代の Pod の削除を
   始めない — Service は worker の Pod を振り先にし、Terminating の Pod を振り先から外すので、前の世代で動く job へ届かなくなる前に、
   今の世代が全部を受け取れる姿にしておく。"
  (bool (handover-blockers state name boot now timing ready-instances)))


;; 受け渡しの止まりの知らせの上限(card ki-d8e594fe934d): 待ちの始まり(SupersededRun.since-ms)からこの長さを越えても止めている
;; job(handover-blockers)が残れば、Worker の status.handover の stalled と GET /metrics の doeff_worker_handover_stalled{worker} を
;; 1 にする。根拠: 今の世代が job を受け取れるまでに要るのは、Pod の起動と最初の heartbeat・job ごとの実行環境の準備(世代をまたいで
;; 同じ node の root を共有するので、ふつうは準備済み — 進まない準備は 600 秒で止めてやり直す・worker の PrepareLimits.stall-seconds)・
;; handoff の job の Ready(新しい process が動き出してから Ready までの上限の既定 300 秒 — readiness の handoffTimeoutSeconds)。
;; 止めてやり直す準備 1 回と Ready の待ちの和 900 秒に、Pod の起動と readiness の窓の 300 秒を足した 20 分。2026-10-22 の zeus は
;; 新しい世代の handoff の job が落ち続け、15:45:30 の始まりから 16:08:39 に人が手で外すまで 23 分止まった(この上限なら 16:05:30 に
;; 知らせる)。知らせるだけで諦めない: 待ちを外すと Deployment が前の世代の Pod を消し、まだ動いている前の世代の process まで止まる
;; (k8s の Deployment も progressDeadlineSeconds で Progressing=False を付けるだけで、旧い ReplicaSet を動かし続ける)。
(setv HANDOVER-STALL-MS 1200000)


(defn #^ tuple handover-blockers [#^ ClusterState state #^ str name #^ str boot #^ int now #^ ClusterTiming timing
                                  #^ (| dict None) ready-instances]
  ;; defk にできない: coordinator の純粋な判断(handover-pending・資源の行 resource_policy.handover-of — defn)が呼ぶ
  "純粋: 名 name の今の世代 boot の受け渡しを止めている job(HandoverBlocker の列・job の名の順 — card ki-d8e594fe934d)。handover-pending
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
  "純粋: 名 name の今の世代 boot の受け渡しの待ちの始まりの刻(待っている組の since-ms の最小 — card ki-d8e594fe934d)。待っている組が
   無いか、始まりを持たない(欄の前に保存した)組だけなら None。"
  (min (gfor r (handover-runs state name boot now timing) :if (is-not r.since-ms None) r.since-ms) :default None))


(defn #^ frozenset elder-held [#^ ClusterState state #^ str name #^ str boot #^ int now #^ int window-ms]
  ;; defk にできない: coordinator の純粋な判断(heartbeat-reply の内包表記の前の値)が呼ぶ
  "純粋: 名 name の worker の、送り手の世代 boot と違う前の世代がまだ動かしている常駐の job の名(AL-D2f — その世代の最後の報告が
   window-ms 以内の、新しい世代が重なりを名乗った(overlapped)SupersededRun の行だけ。黙った世代の行は数えない — 前の世代が止まれば
   今の世代が起こす)。"
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
                                       ;; 新しい試行を起こし、今の世代の process から lease を取る(10-11 21:29 JST の zeus)。値は今の
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
  "GET /state の状態の画面(JSON は coordinator/protocol/replies が綴る — #2595)。worker ごとの taskRoom は置き先の判断と同じ数え
   (load-of と task-room-of)から読む。"
  (setv draining (draining-workers state now)
        load (load-of state state.placements))
  (var workers #())
  (for [#(n w) (.items state.workers)]
    (<- room int (task-room-of w load))
    (:= workers #(#* workers (WorkerView :info w :silent-ms (- now w.last-seen-ms) :live (alive now w timing.lease-ms)
                                           :draining (in n draining) :task-room room))))
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
             ;; 組ごとの宣言の値と置いている数(card ki-b0e8028c8c8d)。
             :group-limits (! (group-limit-views state))))


(defn #^ (| str None) runtime-env-refusal [#^ dict body]
  "宣言の行の runtimeEnv(在れば)が宣言として読めなければ理由の文(送り手の誤り — 400)。"
  (runtime-env-value-refusal (.get body "runtimeEnv")))


(defn #^ (| str None) runtime-env-value-refusal [#^ (| dict list tuple str int float bool None) value]
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


(defn #^ BoardTally board-tally-of [#^ (get Table BoardRow) board]
  "盤の全行から集計を求める(起動の読み直しと、集計をまだ持たない状態の最初の読みだけ — 書きごとには board-written が足し引きする)。"
  (BoardTally :rows (.size board) :bytes (sum (gfor row (.rows board) row.size))
              :expiring (len (lfor row (.rows board) :if (is-not row.expires-ms None) row))))


(defn #^ BoardTally board-tally [#^ ClusterState state]
  "状態の盤の集計(持っていなければ全行から求める)。"
  (if (is state.board-tally None) (board-tally-of state.board) state.board-tally))


(defn #^ ClusterState board-written [#^ ClusterState state #^ tuple writes]
  "盤へ書き(鍵ごとに 1 つの TableWrite・value None は消す)を当てた状態。集計は書く行・消す行の分だけ足し引きする(全行を読まない)。
   書きが全部今の行そのものなら状態そのもの。"
  (setv board (.with-writes state.board writes))
  (when (is board state.board)
    (return state))
  (setv tally (board-tally state) rows tally.rows size tally.bytes expiring tally.expiring)
  (for [write writes]
    (setv old (.row state.board write.key) new write.value)
    (when (is-not old None)
      (-= rows 1)
      (-= size old.size)
      (when (is-not old.expires-ms None) (-= expiring 1)))
    (when (is-not new None)
      (+= rows 1)
      (+= size new.size)
      (when (is-not new.expires-ms None) (+= expiring 1))))
  (replace state :board board :board-tally (BoardTally :rows rows :bytes size :expiring expiring)))


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


(deff derived-capabilities [#^ (get Table str) labels #^ tuple table]  ; defk にできない: coordinator の調停(Program)が呼ぶ純粋な判断
  {:pre [(: labels (get Table str)) (: table tuple)] :post [(: % tuple)] :tags {:context "coordinator" :role "judgment"}}
  "node の label → その node の worker に足す能力(名の順)。table = ClusterNaming の node-capabilities #(#(鍵 値 能力) …)。"
  (tuple (sorted (sfor #(key value capability) table :if (= (.row labels key) value) capability))))


(deff with-derived-capabilities [#^ ClusterState state #^ tuple table]  ; defk にできない: coordinator の調停(Program)が呼ぶ純粋な判断
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
