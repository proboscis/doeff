;;; 複数の worker に job と task を割り当てる coordinator の型と effect。
;;;
;;; worker は数秒ごとに coordinator へ生存を知らせ(heartbeat)、自分に割り当てられた job と task を受け取る。
;;; 連絡が途絶えた worker は fence-ms で自分の job を止め、coordinator は reassign-after-ms が過ぎてから
;;; 別の worker へ割り当て直す(reassign-after-ms > fence-ms + 停止の猶予 でなければならない)。
;;;
;;; job(service)= 宣言が持つ常駐の仕事。task = RemoteJob が出した短い仕事。呼び手が問い合わせで lease を延ばし続ける
;;; 間だけ在り、途絶えれば coordinator が落とす(担い手の worker は次の heartbeat でその子 process を止める)。
;;; 盤(board)= service どうしが読み書きする共有の状態の、実験用の代役(本番では業務の系の側の共有の置き場に当たる)。
;;;
;;; 資源(2026-09-24): coordinator の状態は kind ごとの資源 — Service(宣言)・Worker・Task・Rollout — として外へ見せる。
;;; 各資源は resourceVersion(coordinator 全体で単調に増える番号)と generation(spec が変わるたびに増える)を持ち、
;;; 書きは資源 1 つずつの compare-and-set。誰が・いつ・何を・前後の版は出来事の記録(audit)に残る。
;;; 版と記録は「前の状態と後の状態の差」から 1 か所(resource_policy.stamp)で付けるので、どの経路の変化も漏れない。
(require doeff-hy.macros [val])
(require doeff-hy.record [defenum defrecord])
(val MODULE-TAGS {:context "coordinator" :role "intent"})
(import dataclasses [dataclass field])
(import enum [StrEnum])
(import typing [NamedTuple])
(import doeff [EffectBase])
(import doeff_cluster.shared.intent.job_model [JobSpec])
(import doeff_cluster.shared.intent.protocol [ClusterTiming Request NextRequests])
(import doeff_cluster.coordinator.intent.request_bodies [StatusRow])


(defclass ComponentVersion [NamedTuple]
  "版 1 つ: 部品の名(python・cloudpickle・doeff)とその版の綴り。task を送れる worker を選ぶのに、送り手と worker の組を比べる。"
  (#^ str component)
  (#^ str version))


(defclass [(dataclass :frozen True)] ClusterJob []
  (#^ JobSpec spec)
  (setv #^ tuple needs #())           ; 要る能力の名(名の順 — capabilities-of)。置く worker は needs ⊆ provides
  (setv #^ (| str None) pin None)     ; この worker にだけ置く
  (setv #^ (| dict None) run None)    ; 宣言の run(詰めた Program の置き場のキー・identity・版・describe)。表示と保存のため
  ;; --- Service の資源としての欄(2026-09-24) ---
  (setv #^ int replicas 1)            ; 0 = 宣言は残すが置かない(Rollout が旧を止める・新を起こす口)。1 = 置いて動かし続ける
  (setv #^ (| dict None) readiness None) ; {"windowSeconds": n} = ReportReady の「準備できた」が直近 n 秒以内にある時だけ Ready
  (setv #^ (| str None) owner None)   ; 宣言の所有者(依頼の主体の id・作業係の名)。消せるのは所有者か明示の force の delete だけ
  ;; --- 入れ替え(2026-09-24) ---
  ;; 入れ替えの形: "recreate"(旧を止めてから新 — 既定)か "handoff"(新が Ready と数えられてから旧を止める — worker_model.JobSpec)。
  ;; 版は宣言の revision ただ 1 つ(Program を詰めた commit — 以前の image の版を追う baseFrom と、定義だけを別の commit で重ねる
  ;; overlay は消した。持つ行は宣言の口と読み直しで断る — cluster_policy.program-row-refusal)。
  (setv #^ str update "recreate"))


(defenum GenerationOrder CURRENT OLDER NEWER)


;; heartbeat の process の世代が、同じ名の今の世代に比べてどれか(cluster_policy.generation-order — 2026-09-27)。
;; CURRENT = 今の世代(初めての名・世代を名乗らない旧い worker を含む)・OLDER = 古い世代(名乗りとして受けない)・
;; NEWER = 新しい世代(今の世代を退かせる)。


(defclass [(dataclass :frozen True)] WorkerInfo []
  (#^ str name)
  (#^ tuple provides)                 ; 提供する能力の名(名の順 — クラスタの設定で名乗る)
  (#^ int capacity)
  (#^ int last-seen-ms)
  (setv #^ (get tuple #(ComponentVersion ...)) versions #())        ; worker の Python / cloudpickle / doeff の版(task を送れる相手を選ぶ)
  ;; worker の process の世代(起動のたびに新しく振る・heartbeat の boot)。drain は頼まれた時の世代に付き、別の世代の heartbeat
  ;; (Pod を作り直した後の worker)が来たら解ける(2026-09-25)。旧い worker は None。
  ;; 世代の順(2026-09-27): boot の id(uuid)には順が無いので、coordinator が初めて見た順を世代の順とする。
  ;; boot = この名の今の世代・retired = この名の退いた世代(新しい順・cluster_policy.RETIRED-BOOTS-KEPT まで)。退いた世代の
  ;; heartbeat は worker の名乗りとして受けない(cluster_policy.superseded-boot)。どちらも保存する(読み直しの後も順を保つ)。
  (setv #^ (| str None) boot None)
  ;; worker が名乗る道具(外部の CLI・OS の library — 名と版・2026-09-26)。実行環境の宣言の tools と照らして置き先を選ぶ。
  (setv #^ (get tuple #(ComponentVersion ...)) tools #())
  ;; 実行環境の root の名乗り(2026-09-26・heartbeat の platform・envs・envCapacity)。保存しない(次の heartbeat で埋まる)。
  ;; platform = root のキーの材料(runtime_env_model.current-platform)・env-ready / env-preparing = 準備済み / 準備中の root のキー・
  ;; env-failed = 準備に失敗した root(EnvFailed の tuple)・env-capacity = "ok" か "exhausted"(準備を始める空きが無い)。
  (setv #^ str platform "")
  (setv #^ frozenset env-ready (frozenset))
  (setv #^ frozenset env-preparing (frozenset))
  (setv #^ tuple env-failed #())
  (setv #^ str env-capacity "ok")
  ;; 退いた世代(boot の欄の説明 — 位置で渡す欄の後ろに置く)。
  (setv #^ (get tuple #(str ...)) retired #())
  ;; 今の世代の process の起動時刻(epoch ms・heartbeat の bootAt — 2026-09-27)。今の世代と来た世代の両方の起動時刻を知る時は、
  ;; 大きい方を新しい世代とする(cluster_policy.generation-order)。状態を失った coordinator に新しい世代が先に届いても、後から来た
  ;; 古い世代に今の世代を明け渡さない。起動時刻を名乗らない旧い worker・旧い形の置き場は None(初めて見た順へ落とす)。保存する。
  (setv #^ (| int None) boot-at None)
  ;; 専用の能力(provides の一部・名の順)。空でなければ、このどれかを needs に持つ job / task だけを置く(以前の dedicated の印)。
  (setv #^ tuple exclusive #())
  ;; worker の置かれた node の名(heartbeat の node — k8s の downward API。k8s の外の機体は空)と、coordinator がその node の label から
  ;; 導いた能力(ClusterNaming の node-capabilities — worker の自己申告ではない)。置き先の判断は provides と derived の和を見る。
  (setv #^ str node "")
  (setv #^ tuple derived #()))


(defclass [(dataclass :frozen True)] EnvFailed []
  "worker が名乗った root の準備の失敗 1 つ(キー・EnvFailureKind の値の綴り・理由・一時か)。"
  (#^ str key)
  (#^ str kind)
  (#^ str detail)
  (#^ bool retryable))


(defclass [(dataclass :frozen True)] WarmEntry []
  "温める表の行 1 つ(2026-09-26・WarmRuntimeEnv)。key = 宣言(platform を含まない)と needs の組のキー・runtime-env = 宣言の JSON・
   needs = 準備してほしい worker に要る能力・until-ms = 期限(過ぎた行は調停が消す)・holder = 頼んだ主体(記録と表示だけ)。
   能力の合う worker は heartbeat の返事で行を受け取り、job の準備より低い優先度で準備する。行の期限の内は掃除がその root を消さない。"
  (#^ str key)
  (#^ dict runtime-env)
  (#^ tuple needs)
  (#^ int until-ms)
  (#^ str holder))


(defrecord ProgramRow
  "置き場に置いた詰めた Program 1 つ(PUT /programs/<sha> — 改訂 1 の F): blob = 詰めた Program(base64 の文字列)・versions = 詰めた
   送り手の版(名 → 版)・put-ms = 置いた時刻(参照の無い Program を猶予の後に消す — program_policy.sweep-programs)。#2447 で JSON の dict を
   この型にした。保存の JSON の形 {blob versions putMs} は cluster_policy の program-row-to-json / program-row-from-json。"
  (#^ str blob)
  (#^ dict versions)
  (#^ int put-ms))


(defrecord ResourceMeta
  "資源 1 つの版の記録(ClusterState.meta の値 — 鍵 = <種類>/<名>): resource-version = 版(書くたびに cluster の版を振る)・generation =
   spec の世代(spec が変わった時だけ進む)・created-by / created-ms・updated-by / updated-ms = 作った・最後に書いた送り手と時刻。#2447 で
   JSON の dict をこの型にした。保存の JSON の形 {resourceVersion generation createdBy createdMs updatedBy updatedMs} は cluster_policy の
   resource-meta-to-json / resource-meta-from-json。"
  (#^ int resource-version)
  (#^ int generation)
  (#^ str created-by)
  (#^ int created-ms)
  (#^ str updated-by)
  (#^ int updated-ms))


(defrecord BoardRow
  "盤の行 1 つ(ClusterState.board の値 — 鍵 = 盤の鍵): value = 書かれた値(JSON の値 — 業務の系の物なので中を読まない。null も値)・
   version = 行の版(行ごとに 1 から増える)・expires-ms = 期限(epoch ミリ秒 — PUT の ttlSeconds で付き、過ぎた行は調停が消す・
   無ければ None)・size = 値の JSON の byte 数(容量の上限の判断と計器に使う・保存しない — 読み直しの時に測り直す)。
   #2447 で、鍵ごとの 4 つの表(値・版・期限・大きさ)をこの型 1 つにまとめた。耐久の形 {value resourceVersion [expiresMs]} は
   durable_kv.board-entry。"
  (#^ object value)
  (#^ int version)
  (#^ (| int None) expires-ms)
  (#^ int size))


(defrecord RolloutTarget
  "Rollout の相手 1 つ(spec の from / to): kind = Service か Deployment・name・namespace = Deployment の名前空間(Service は None)・
   replicas = Deployment を新として起こす台数(無ければ None)・dry-run = Deployment を書かずに台数を記録だけする(Service は偽)。
   JSON の形は rollout_policy の target-to-json(Service は {kind name}・Deployment は {kind namespace name replicas dryRun})。"
  (#^ str kind)
  (#^ str name)
  (setv #^ (| str None) namespace None)
  (setv #^ (| int None) replicas None)
  (setv #^ bool dry-run False))


(defrecord RolloutSpec
  "Rollout の宣言(rollout_policy.validate-rollout-spec が送り手の本文から揃えた形 — 欠けた秒の欄は既定の値): from-target / to-target =
   旧と新・owner = 所有者・*-seconds = 段ごとの期限(新の Ready・旧の停止・観察・観察中の NotReady・戻し)・mark-deployment = 台数の
   持ち主の annotation を Deployment に付けるか・abort = 中止の指示(作った後に変えてよい唯一の欄)。#2447 で dict をこの型にした。
   JSON の形 {from to owner readyTimeoutSeconds … markDeployment abort} は rollout_policy の rollout-spec-to-json。"
  (#^ RolloutTarget from-target)
  (#^ RolloutTarget to-target)
  (#^ (| str None) owner)
  (#^ (| int float) ready-timeout-seconds)
  (#^ (| int float) stop-timeout-seconds)
  (#^ (| int float) observe-seconds)
  (#^ (| int float) fail-after-seconds)
  (#^ (| int float) rollback-timeout-seconds)
  (#^ bool mark-deployment)
  (#^ bool abort))


(defrecord RolloutHistory
  "Rollout の段の移り 1 つ(status.history の要素): phase = 入った段・at = 時刻・reason = 理由。"
  (#^ str phase)
  (#^ int at)
  (#^ str reason))


(defrecord RolloutStuck
  "戻し(RollingBack)が rollbackTimeoutSeconds を過ぎても終わらない印(status.stuck — 人を呼ぶ): step = 戻しの手順
   (restoreOld / stopNew)・reason = 理由・since-ms = 印を付けた時刻。"
  (#^ str step)
  (#^ str reason)
  (#^ int since-ms))


(defrecord RolloutDrift
  "完了した Rollout が台数を持つ Deployment の、宣言の台数と期待の食い違い(status.drift — 直さずに出すだけ): deployment = 「ns/名」・
   expected = 期待の台数・observed = 観測した宣言の台数・since-ms = 食い違いを最初に見た時刻・note = 説明の文。"
  (#^ str deployment)
  (#^ int expected)
  (#^ int observed)
  (#^ int since-ms)
  (#^ str note))


(defrecord RolloutStatus
  "Rollout の進み具合(RolloutRow.status — rollout_policy.rollout-step が段を進める): phase = 段(Pending から Complete / RolledBack)・
   phase-since-ms = 段に入った時刻・reason = 今の段の理由・history = 段の移り(直近 30 件)・created-ms / started-ms / completed-ms =
   作った・新を起こし始めた・終えた時刻・from-replicas = 戻す時の旧の台数・stopped-old-ms = 旧が止まった時刻・not-ready-since-ms /
   unknown-since-ms = 観察中に新が NotReady / 観測が Unknown になった時刻・rollback-step = 戻しの手順・failure = 戻しに入った理由・
   restored-old-ms = 旧が Ready に戻った時刻・stuck / stuck-cleared-ms = 戻しが終わらない印とそれが解けた時刻・last-action = 直前に
   実行した action と結末(op・target の「Kind:名」・ok・error・at・count ほか — action の種類で欄が違うので JSON の object のまま)・
   simulated = dry-run の相手の記録した台数(「Kind:名」→ 台数)・marked-deployment = 台数の持ち主の annotation を置いた「ns/名」・
   drift / drift-resolved-ms = 台数の食い違いとそれが解けた時刻。無い欄は None。#2447 で dict をこの型にした。JSON の形(在る欄だけの
   {phase phaseSinceMs reason history …})は rollout_policy の rollout-status-to-json / rollout-status-from-json。"
  (setv #^ str phase "Pending")
  (setv #^ (| int None) phase-since-ms None)
  (setv #^ (| str None) reason None)
  (setv #^ (get tuple #(RolloutHistory ...)) history #())
  (setv #^ (| int None) created-ms None)
  (setv #^ (| int None) started-ms None)
  (setv #^ (| int None) from-replicas None)
  (setv #^ (| int None) stopped-old-ms None)
  (setv #^ (| int None) not-ready-since-ms None)
  (setv #^ (| int None) unknown-since-ms None)
  (setv #^ (| int None) completed-ms None)
  (setv #^ (| str None) rollback-step None)
  (setv #^ (| str None) failure None)
  (setv #^ (| int None) restored-old-ms None)
  (setv #^ (| RolloutStuck None) stuck None)
  (setv #^ (| int None) stuck-cleared-ms None)
  (setv #^ (| dict None) last-action None)
  (setv #^ (| dict None) simulated None)
  (setv #^ (| str None) marked-deployment None)
  (setv #^ (| RolloutDrift None) drift None)
  (setv #^ (| int None) drift-resolved-ms None))


(defrecord RolloutRow
  "Rollout 1 つ(ClusterState.rollouts の値 — 鍵 = Rollout の名・保存する): spec = 宣言 RolloutSpec・status = 進み具合 RolloutStatus。
   #2447 で dict をこの型にした。
   保存の JSON の形 {spec status} は cluster_policy の rollout-row-to-json / rollout-row-from-json。"
  (#^ RolloutSpec spec)
  (#^ RolloutStatus status))


(defrecord AuditEvent
  "出来事の記録 1 件(ClusterState.audit の要素 — resource_policy.stamp が資源の版を進めるたびに 1 件): seq = 通し番号・at = 時刻・
   actor = 送り手・verb = create / adopt / update / status / delete・kind / name = 資源・from-version / to-version = 前と後の版
   (作った時は前が None・消した時は後が None)・generation = spec の世代・changes = 変わった欄(\"spec.<欄>\" か \"status.<欄>\" →
   [前 後] — 長い値は切る)。#2447 で JSON の dict をこの型にした。保存と見せる JSON の形 {seq at actor verb kind name fromVersion
   toVersion generation changes} は cluster_policy の audit-event-to-json / audit-event-from-json。"
  (#^ int seq)
  (#^ int at)
  (#^ str actor)
  (#^ str verb)
  (#^ str kind)
  (#^ str name)
  (#^ (| int None) from-version)
  (#^ (| int None) to-version)
  (#^ (| int None) generation)
  (#^ dict changes))


(defrecord WorkerReport
  "worker 1 つの最新の状態の報告(ClusterState.statuses の値 — 鍵 = worker の名・保存しない): at = 受けた時刻(epoch ms)・endpoint =
   worker が名乗った宛先(名乗らない旧い worker は None)・jobs = job の行の列(heartbeat の statuses の行 StatusRow から、結果の
   欄 result と task の写しを外した物 — 持ち続けるのは process の姿だけ)。#2447 で dict をこの型にした。"
  (#^ int at)
  (#^ (| str None) endpoint)
  (#^ (get tuple #(StatusRow ...)) jobs))


(defrecord RefusedJob
  "受け付けない Service の行(2026-09-27・改訂 1 の C)。旧い宣言の形の行を読み直した時と、読めない行を、coordinator を落とさずに
   持っておく: name = Service の名・row = 元の行(保存と表示のため JSON のまま)・reason = 理由。置き先・Rollout・drain・計器は
   ClusterState.jobs(受け付けた job)だけを見て、これは見ない。PUT で新しい形に書き直せば jobs へ移る。"
  (#^ str name)
  (#^ dict row)
  (#^ str reason))


(defclass [(dataclass :frozen True)] Placement []
  "置き先 = job をどの worker に置いたか(配置)。依頼(Request)とは別の物。2026-09-25 に改名(永続化の鍵は durable_kv.hy)。"
  (#^ str job)
  (#^ str worker)
  (#^ int generation)                 ; この job の置き先が変わるたびに増える(heartbeat の返事の job の行の placement)
  (#^ int since-ms))


(defclass [(dataclass :frozen True)] Drain []
  "worker の drain(2026-09-25)= その worker を空けてよいかを問う印。drain 中の worker には新しい置き先を割り当てず、上の入れ替え
   (handoff)の Service は別の worker へ並べて(surge)Ready を待ってから移し、それ以外の Service は止めて移す。
   boot = 頼まれた時の worker の process の世代(別の世代の heartbeat が来たら解ける — Pod を作り直した後の worker は空けない)。
   until-ms = 期限(頼み直すたびに延びる・忘れられた drain が worker を空けたままにしない)。"
  (#^ str worker)
  (#^ int since-ms)
  (#^ int until-ms)
  (setv #^ (| str None) boot None)
  (setv #^ str actor ""))


(defenum HandoffPhase
  (WAITING "WaitingReady")
  (ABANDONED "Abandoned"))


;; 入れ替え(handoff)の見張りの段。WAITING = 新の世代が動き出し、Ready を待っている(旧は退いて動いている)・
;; ABANDONED = 期限の間 Ready にならず、入れ替えを諦めた(worker は新を止めて起こし直さず、旧を動かし続ける)。


(defrecord HandoffWatch
  "入れ替え(update = handoff)の Service 1 つの期限の見張り(2026-09-26 — handoff_policy)。coordinator の状態に Service の名ごとに持ち、
   保存する(durable_kv の handoff/<名>)— 作り直しの後も諦めを保ち、止まっていた時間は期限に数えない(resume-after-downtime)。
   declaration = 見張りを始めた時の宣言の指紋(handoff_policy.declaration-fingerprint)。宣言が変われば見張りを捨てる(諦めも解ける)。
   since-ms = 新の世代(今の宣言の spec の process)を担い手の報告に初めて見た coordinator の時刻(期限の起点)。
   abandoned-ms・reason・last-report = 諦めた時だけ: 時刻・期限と最後の NotReady の理由・新の世代の最後の ReportReady(偽)の reason。"
  (#^ str declaration)
  (#^ int since-ms)
  (setv #^ HandoffPhase phase HandoffPhase.WAITING)
  (setv #^ (| int None) abandoned-ms None)
  (setv #^ str reason "")
  (setv #^ (| str None) last-report None)

  (defn #^ dict to-json [self]  ; defk にできない: 保存の形へ写す dataclass の口(coordinator の純粋な関数が呼ぶ)
    "保存の形(durable_kv と state-to-json が使う)。"
    {"declaration" self.declaration "sinceMs" self.since-ms "phase" self.phase.value
     "abandonedMs" self.abandoned-ms "reason" self.reason "lastReport" self.last-report})

  (defn #^ dict status-json [self #^ int timeout-ms]  ; defk にできない: 資源の表示の形へ写す dataclass の口(coordinator の純粋な関数が呼ぶ)
    "Service の資源の status.handoff に載せる形(段が変わる時だけ変わる — 拍ごとに版と出来事の記録を進めない)。"
    (| {"phase" self.phase.value "sinceMs" self.since-ms "timeoutSeconds" (/ timeout-ms 1000)}
       (if (= self.phase HandoffPhase.ABANDONED)
           {"abandonedMs" self.abandoned-ms "reason" self.reason "lastNotReadyReport" self.last-report}
           {}))))


;; --- 版の判定(2026-09-29・#1013) -------------------------------------------------------------
;; Service ごとに「指定の版(spec.revision)が実際に仕事をしているか」を coordinator が答える(resource_policy.version-state)。
;; 材料の判定(running-process・入れ替えの見張り・停止の述語)の答えは、理由の文ではなく下の閉じた型で運ぶ — version-state は種類を
;; 網羅の match で状態へ写し、文を読んで分けない。

(defenum UnplacedKind WAITING-PREVIOUS-HOLDER NO-ELIGIBLE-WORKER NO-ROOM)


;; 置き先が無い理由(cluster_policy.unplaced-kind)。WAITING-PREVIOUS-HOLDER = 前の担い手が止め終えるのを待っている(drain や
;; 入れ替えの正常な途中)・NO-ELIGIBLE-WORKER = 置ける worker が無い・NO-ROOM = 置ける worker に空きが無い。


(defenum NotReadyKind
  NO-DECLARATION NO-REPLICAS WAITING-PREVIOUS-HOLDER NO-ELIGIBLE-WORKER NO-ROOM CARRIER-SILENT NOT-RUNNING
  REVISION-MISMATCH NO-INSTANCE SPEC-MISMATCH PLACEMENT-MISMATCH)


;; running-process が ok でない理由の種類(答えの dict の "kind")。宣言が無い・replicas 0・置き先が無い 3 種(UnplacedKind と同じ)・
;; 担い手の報告が古い(Unknown の間も同じ種類)・担い手の行の phase が running でない・版の違い・process の世代を報告しない・
;; 設定の指紋の違い・割り当ての世代の違い。


(defenum VersionState
  (CURRENT "Current")
  (UPDATING "Updating")
  (BLOCKED "Blocked")
  (STOPPED "Stopped")
  (UNKNOWN "Unknown"))


;; 版の判定の 5 値(Service の資源の status.version.state の綴り — 外へ見せる約束)。Current = 指定の版の process が仕事をしていて、
;; 退いた旧い process が生きていない(健康 readiness は含まない)・Updating = 指定の版へ移っている途中・Blocked = 待っても指定の版へ
;; 進まない・Stopped = 止めている・Unknown = 担い手の報告が途絶えていて分からない。


(defrecord VersionVerdict
  "版の判定の答え(resource_policy.version-state)。reason = 人が読む理由(Current は空)。"
  (#^ VersionState state)
  (#^ str reason))


(defrecord LiveProcess
  "Service の process が生きている行 1 つの事実(resource_policy.live-processes — 判定ではない)。revision = 動いている版
   (process を持つ行なら worker が必ず載せる。載せない行の None は埋めずにそのまま運ぶ)・retired = 入れ替えで退いた旧い process の
   行か(行の retiredFrom が Service の名)。"
  (#^ (| str None) revision)
  (#^ bool retired))


(defclass [(dataclass :frozen True)] ClusterNaming []
  "クラスタが外の系(k8s の Deployment・Node)と取り交わす名。どれも配備する側(composition root の引数)が決める。
   owner-annotation = Rollout が台数を持つ Deployment に付ける annotation の鍵。
   owner-scope      = その値の頭に付ける、このクラスタの名(「<scope>/Rollout/<名> replicas=<n>」)。
   node-capabilities = node の label から導く能力 #(#(label の鍵 値 能力の名) …)(ADR-DOE-CLUSTER-001 R4b・改訂 1 の I)。ここに在る能力は
                      worker が自分で名乗っても受けない — coordinator が worker の置かれた node の label を読んで足す(会社の機体の境界を
                      worker の自己申告に任せない)。既定 = company-machine を label doeff.dev/company-machine=true から。"
  (setv #^ str owner-annotation "doeff-cluster/replicas-owned-by")
  (setv #^ str owner-scope "doeff-cluster")
  (setv #^ tuple node-capabilities #(#("doeff.dev/company-machine" "true" "company-machine"))))


;; 受け入れる本文の版の範囲(送り手の版 PROTOCOL-FORMAT は shared/intent/protocol.hy)。
(setv ACCEPTED-FORMATS #(1))


(defclass [(dataclass :frozen True)] TaskRecord []
  "task 1 本。phase = queued | preparing | assigned | finished | code-failed | failed(切り離した task は + version-mismatch | lost | cancelled)。
   preparing = 実行環境の task を、その env を準備済みでない worker に置いた(worker が準備してから走る・冷たい起動)。worker が準備済みを
   名乗った拍に assigned へ進む。担い手の数・送る task・報告の吸い上げでは assigned と同じに扱う(PLACED-PHASES)。
   result = worker が返した結果の blob(TaskSucceeded / TaskFailed の cloudpickle)。finished で None なら結果なし。
   program = 詰めた Program の置き場のキー(sha256 — 本体は /programs/<sha>。service の宣言の行と同じ運び方 — ADR-DOE-CLUSTER-001 R3b)。
   行が在る間(終わって結果を保持している間も)置き場の Program を参照し続ける(program_policy.program-refs)。None = 2026-09-27 より前の
   形で読んだ終わった行(Program を持たない)。"
  (#^ str id)
  (#^ str name)
  (#^ (| str None) program)
  (#^ str revision)
  (#^ (get tuple #(ComponentVersion ...)) versions)   ; 送り手の版(名の順)
  (#^ tuple needs)                    ; 要る能力の名(名の順)
  (#^ int lease-ms)
  (#^ int lease-until-ms)
  (#^ int submitted-ms)
  (setv #^ str phase "queued")
  (setv #^ (| str None) worker None)
  (setv #^ (| str None) result None)
  (setv #^ str detail "")
  (setv #^ (| int None) started-ms None)
  (setv #^ (| int None) finished-ms None)
  ;; --- 切り離した task(2026-09-25・SubmitDetached — detached_model.hy)---
  ;; detached = 呼び手の問い合わせと寿命を切り離した task。key = 呼び手の決めた job id(送り直しても同じ行)。
  ;; boot = 置いた時の worker の process の世代。lease は置いた世代の heartbeat だけが延ばし、置いた世代の heartbeat が lease の間
  ;; 止まったら phase = lost(走らせ直さない)。同じ名の別の世代の heartbeat は延ばしも lost にもしない(2026-09-27)。
  ;; retain-ms = 終わった後に結果を持っておく長さ。
  ;; 切り離した task だけが使う phase: version-mismatch | lost | cancelled。
  (setv #^ bool detached False)
  (setv #^ (| str None) key None)
  (setv #^ (| str None) boot None)
  (setv #^ int retain-ms 0)
  ;; --- 実行環境(runtime env・2026-09-26)---
  ;; runtime-env = 宣言の JSON(runtime_env_model の runtime-env->json の形)。在れば worker の版と比べずに置き(版の突き合わせは
  ;; env の root の中の子 process が行う)、worker は env の root を準備してから走らせる。準備の失敗(phase env-failed)は
  ;; failure-kind と retryable を持つ。一時の失敗は、試した worker(avoid)を避けて env-attempts が ENV-RETRIES になるまで置き直す。
  (setv #^ (| dict None) runtime-env None)
  (setv #^ int env-attempts 0)
  (setv #^ tuple avoid #())
  (setv #^ str failure-kind "")
  (setv #^ bool retryable False)
  ;; --- 子の環境変数(2026-09-28)---
  ;; environ = 送り手の effect の :environ(名の順の #(名 値) の tuple — service の JobSpec.environ と同じ形)。heartbeat の返事で worker へ
  ;; 運び、worker は service と同じ路(ProcessHost.launch)で子の環境変数に置く。この欄の無い行(2026-09-28 より前)は空で読む。
  (setv #^ tuple environ #()))


;; 担い手の worker に置いた task の phase(担い手の数・送る task・報告の吸い上げ・lease の延長で同じに扱う)。
(setv PLACED-PHASES (frozenset #("assigned" "preparing")))


;; 終わった task の phase(旧い形の保存の行を読む時に、まだ終わっていない行だけを断る — task-record-from-json)。
(setv ENDED-PHASES (frozenset #("finished" "code-failed" "failed" "version-mismatch" "lost" "cancelled" "env-failed")))


(defclass [(dataclass :frozen True)] ClusterState []
  (setv #^ tuple jobs #())
  (setv #^ dict workers (field :default-factory dict))
  (setv #^ dict placements (field :default-factory dict)) ; job の名 → Placement
  (setv #^ dict tasks (field :default-factory dict))
  (setv #^ int next-task 1)
  ;; task の id の頭(2026-09-27 — #757)。id = <頭><番号>。以前からの置き場は "t"(t1, t2 …)。置き場の無いところから起きた
  ;; coordinator は起動ごとに違う頭を振る(cluster_policy.fresh-task-prefix)— 前の coordinator が振った id(worker に blob が
  ;; 残り、子 process が走っているかもしれない)を振り直さない。保存する(counter の taskPrefix)。
  (setv #^ str task-prefix "t")
  (setv #^ dict board (field :default-factory dict))    ; 盤の鍵 → BoardRow(行と一緒に保存)
  (setv #^ dict statuses (field :default-factory dict))  ; worker 名 → WorkerReport(保存しない)
  (setv #^ tuple events #())                            ; 割り当ての移り変わり(直近 200 件・保存しない)
  ;; --- 資源(2026-09-24) ---
  (setv #^ dict meta (field :default-factory dict))      ; "Kind/名" → 資源の版の欄(resourceVersion・generation・作った / 書いた送り手と時刻)
  (setv #^ int revision 0)                              ; coordinator 全体の版の番号(書きのたびに 1 進む)
  (setv #^ tuple audit #())                             ; 出来事の記録 AuditEvent の列(kind ごとに件数の上限つき・保存する)
  (setv #^ int audit-seq 0)
  (setv #^ dict rollouts (field :default-factory dict))  ; Rollout の名 → RolloutRow
  ;; --- 保存しない観測 ---
  ;; Service の名 → 直近の ReportReady の報告(process の世代ごとに最新 1 つ・古い順の tuple)
  ;; {worker pid revision instance attempt specHash placement ready reason at}
  (setv #^ dict readiness (field :default-factory dict))
  ;; Service の名 → 直近の ReportMetrics の報告(同じ形・ready と reason の代わりに metrics)。GET /metrics が今の process の分だけ出す
  (setv #^ dict metrics (field :default-factory dict))
  (setv #^ dict deployments (field :default-factory dict)) ; "ns/名" → k8s の Deployment の最後の観測
  ;; node の名 → その node の label の最後の観測 {"labels" {…} "at" ms} か {"error" "at"}(能力の導出の cache・保存しない)
  (setv #^ dict nodes (field :default-factory dict))
  ;; node の label から導く能力の名(ClusterNaming の node-capabilities の能力 — coordinator の起動で入れる・保存しない)。
  ;; worker の heartbeat の provides にこの名が在っても受けない(自己申告を断る — 改訂 1 の I)。
  (setv #^ frozenset derivable (frozenset))
  ;; 受け付けない Service の行(名 → RefusedJob — 改訂 1 の C)。保存する(元の行のまま)— 読み直しても同じ理由で受け付けない。
  (setv #^ dict refused (field :default-factory dict))
  ;; 詰めた Program の置き場(sha → {"blob" "versions" "putMs"} — program_policy・改訂 1 の F)。保存する。
  (setv #^ dict programs (field :default-factory dict))
  (setv #^ int started-ms 0)                            ; この coordinator の process が状態を読んだ時刻(観測が揃うまでの猶予)
  (setv #^ int rollout-tick-ms 0)                       ; Rollout を最後に調停した時刻
  ;; coordinator が生きていた最後の時刻(ALIVE-MARK-MS ごとに耐久の鍵 counter へ書く)。起動の時に「止まっていた長さ」を測り、
  ;; 進行中の Rollout の段の起点と task の lease を、その長さだけずらす(api_policy.resume-after-downtime・2026-09-25)。
  (setv #^ int alive-ms 0)
  ;; worker の名 → 最後の連絡の時刻を alive-ms と同じ拍(mark-alive)で写した値(耐久の鍵 worker/<名> の lastSeenMs・2026-09-25)。
  ;; 起動の時は、この値と alive-ms の差(止まる前の最後の印の時点の沈黙)を今から数え直す(api_policy.resume-after-downtime)。
  ;; 以前は最後の連絡の時刻を保存せず、読み直しのたびに全 worker を「いま連絡があった」とみなしていたので、32 時間沈黙した
  ;; worker も coordinator が起き直すたびに生きていると出た(2026-09-25)。heartbeat ごとではなく印の拍ごとに写す = 書きは 5 秒に 1 回。
  (setv #^ dict seen-marks (field :default-factory dict))
  ;; drain(2026-09-25): worker の名 → Drain と、入れ替えの Service を drain 中の worker から移す間の並べた置き先
  ;; (job の名 → Placement・surge)。surge の担い手は process を起こし(standby で待つ)、coordinator がそれを Ready と数えたら
  ;; placements をその置き先へ付け替える(旧い担い手は宣言から外れて止め、lease を返す)。どちらも保存する(durable_kv)。
  (setv #^ dict drains (field :default-factory dict))
  (setv #^ dict surges (field :default-factory dict))
  ;; 温める表(2026-09-26): 行のキー → WarmEntry。保存する(durable_kv の warm/<キー>)。
  (setv #^ dict warms (field :default-factory dict))
  ;; 冷たい起動の数(実行環境の task を、準備済みの worker が 1 つも無いまま置いた回数 — 計器 doeff_worker_env_cold_start_total)。
  ;; 保存しない(counter は process の世代ごとに 0 から数える)。
  (setv #^ int env-cold-starts 0)
  ;; 入れ替え(handoff)の期限の見張り(2026-09-26): Service の名 → HandoffWatch。新の世代が動き出してから期限の間 Ready にならなければ
  ;; 諦めを記録し、heartbeat の返事の job に載せる(worker は新を止めて旧を残す — handoff_policy)。保存する(durable_kv)。
  (setv #^ dict handoffs (field :default-factory dict))
  ;; 生きていないと数えた worker の名(heartbeat が lease の外 — #1934)。調停の拍ごとに cluster_policy.note-liveness が時刻から
  ;; 求め直し、変わった拍だけ新しい値にする — Worker の資源の status の live と版は、この欄の変化で進む(時刻そのものを版の比べに
  ;; 入れると、何も変わらない拍の早い戻り(resource_policy.stamp)で切り替わりを取りこぼす)。保存しない(読み直しの後の最初の拍で
  ;; 求め直す)。位置の引数で作る呼び手を崩さないよう最後の欄に置く。
  (setv #^ frozenset silent (frozenset)))


(defclass [(dataclass :frozen True)] Fault []
  "coordinator の中の欠陥(要求の処理の中で上がった、送り手の誤りでない例外)の閉じた答えの形。受け口は 500 と本文
   {\"error\" …} で返し、coordinator は CoordinatorFault で log に 1 行出す。where = 例外が上がった所(file:行 関数)。"
  (#^ str method)
  (#^ str path)
  (#^ str error-type)
  (#^ str message)
  (#^ str where))


;; --- 版の変化を待つ読み(GET /watch — #1933)---------------------------------------------------
;;
;; 送り手は最後に知った coordinator 全体の版(ClusterState.revision — 資源の spec / status が変わるたびに進む・生存の時刻や lease の
;; 期限は入らない)を after で渡し、版がそれと違うようになるか、timeoutSeconds(WATCH-MAX-SECONDS まで)が過ぎるまで返事を待つ。
;; 答え = {"revision" 今の版 "changed" 変わったか}。worker を名指せば、版が進んでもその worker の heartbeat の返事(温める表を除く)が
;; 変わらない間は起きない。調停ループ(coordinator.coordinator-step)が待ちの要求を持ち、書きの後(Persist の後)と拍ごとに判じる。
;; 期限は拍(TICK-MS)の刻で判じる — 期限の後の最初の拍で返す。

;; 待ちの上限 WATCH-MAX-SECONDS は worker も問いに載せる取り交わしの値なので shared/intent/protocol に在る(#2025)。


(defclass [(dataclass :frozen True)] Watcher []
  "GET /watch の待ち 1 件(調停ループが返事まで持つ)。request = 返事を返す相手の要求・after = 送り手が知っている版・deadline-ms =
   変わらなくても返す刻(epoch ms)・worker / boot = 名指した worker とその process の世代(None = coordinator 全体の版だけを見る)・
   mark = 名指した worker の heartbeat の返事の見え方(版 after の時の物 — まだ見ていなければ None)。"
  (#^ Request request)
  (#^ int after)
  (#^ int deadline-ms)
  (setv #^ (| str None) worker None)
  (setv #^ (| str None) boot None)
  (setv #^ (| dict None) mark None))


(defclass [(dataclass :frozen True)] WatchRefusal []
  "GET /watch の問いの読めない形(after の無い・整数でない・timeoutSeconds が数でない)— 400 で断る理由。"
  (#^ Request request)
  (#^ str reason))


(defclass [(dataclass :frozen True)] WatchAnswer []
  "GET /watch の答え: revision = 返す時の coordinator の版(送り手が次の after に使う)・changed = after から変わったか(偽 = 期限)。
   本文の JSON は {\"revision\" … \"changed\" …}(coordinator-step が返事の境で作る)。"
  (#^ int revision)
  (#^ bool changed))


(defclass [(dataclass :frozen True)] WatchStep []
  "待ち 1 件を今の状態で判じた答え: answer = 返す答え(まだ待つなら None)・watcher = 待ち続ける時の次の形(版を見直した後の物)。"
  (#^ (| WatchAnswer None) answer)
  (#^ Watcher watcher))


;; --- effect ----------------------------------------------------------------------

(defclass [(dataclass :frozen True)] IdleProbe []
  "要求の無い拍を飛ばしてよい長さを、模擬の時計の下の受け口が本番と同じ判断の関数で試すための材料(idle_policy.quiet-ticks —
   2026-09-30)。state = この拍の前の調停の状態・timing / naming = 調停ループの設定・wake-ms = 版の変化を待つ要求(GET /watch)の
   いちばん早い期限(epoch ms — 無ければ None。その刻の後の最初の拍は、待ちに返事をするので飛ばさない)。本番の受け口は読まない。"
  (#^ ClusterState state)
  (#^ ClusterTiming timing)
  (#^ ClusterNaming naming)
  (setv #^ (| int None) wake-ms None))


(defclass [(dataclass :frozen True)] IdleNextRequests [NextRequests]
  "coordinator の調停ループが出す NextRequests(shared の受け口の effect)に、模擬の時計の下の受け口だけが読む材料 idle を足した物
   (要求が無ければ、本番の判断で何も変わらない拍の数だけ一度に眠る — 本番の受け口は NextRequests として受けて idle を読まず、拍の
   間隔は timeout-seconds のまま)。idle は coordinator の状態の全体(ClusterState)を持つので、shared の NextRequests には置かず
   この子 class に置く(record-store は NextRequests だけを読む・#2180)。"
  (setv #^ (| IdleProbe None) idle None))


(defclass [(dataclass :frozen True)] CoordinatorFault [EffectBase]
  "coordinator の中の欠陥(Fault)を log に 1 行出す。結果は None。本番の受け口(coordinator_inbox.http-requests)は stderr へ、
   模擬の受け口(coordinator.protocol.request_queue.queued-requests)は列の faults へ書く。"
  (#^ Fault fault))


(defclass [(dataclass :frozen True)] Persist [EffectBase]
  "1 まとまりの変化(キー → 新しい値・消えたキーは None — durable_kv.hy)を耐久の場所へ書き、fsync が終わってから戻る。
   返事(Reply)はこの後にだけ出す: 返事を済ませた書きは coordinator が落ちても消えない。書けなければ例外(返事をせずに落ちる)。"
  (#^ dict delta))
