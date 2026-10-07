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
(require doeff-hy.record [defenum defrecord defwire])
(val MODULE-TAGS {:context "coordinator" :role "intent"})
(import dataclasses [dataclass field KW_ONLY])
(import enum [StrEnum])
(import functools [partial])
(import typing [NamedTuple])
(import doeff [EffectBase])
(import doeff_hy.json_value [OpaqueJson])
(import doeff_hy.table [Table table-of])
(import doeff_cluster.shared.intent.job_model [JobSpec])
(import doeff_cluster.shared.intent.protocol [ClusterTiming Request])
(import doeff_cluster.coordinator.intent.request_bodies [StatusRow DurationRow])


(defclass ComponentVersion [NamedTuple]
  "版 1 つ: 部品の名(python・cloudpickle・doeff)とその版の綴り。task を送れる worker を選ぶのに、送り手と worker の組を比べる。"
  (#^ str component)
  (#^ str version))


(defclass [(dataclass :frozen True)] ClusterJob []
  (#^ JobSpec spec)
  (setv #^ (get tuple #(str ...)) needs #())           ; 要る能力の名(名の順 — capabilities-of)。置く worker は needs ⊆ provides
  (setv #^ (| str None) pin None)     ; この worker にだけ置く
  (setv #^ (| (get dict #(str object)) None) run None)    ; 宣言の run(詰めた Program の置き場のキー・identity・版・describe)。表示と保存のため
  ;; --- Service の資源としての欄(2026-09-24) ---
  (setv #^ int replicas 1)            ; 0 = 宣言は残すが置かない(Rollout が旧を止める・新を起こす口)。1 = 置いて動かし続ける
  (setv #^ (| (get dict #(str object)) None) readiness None) ; {"windowSeconds": n} = ReportReady の「準備できた」が直近 n 秒以内にある時だけ Ready
  (setv #^ (| str None) owner None)   ; 宣言の所有者(依頼の主体の id・作業係の名)。誰が宣言したかの記録で、書き・消しの可否には使わない
  ;; --- 入れ替え(2026-09-24) ---
  ;; 入れ替えの形: "recreate"(新しい版の準備と入口の検めが済んでから旧を止め、止め終えてから新 — 既定)か "handoff"(新が Ready と
  ;; 数えられてから旧を止める — worker_model.JobSpec)。
  ;; 版は宣言の revision ただ 1 つ(Program を詰めた commit — 以前の image の版を追う baseFrom と、定義だけを別の commit で重ねる
  ;; overlay は消した。持つ行は宣言の口と読み直しで断る — cluster_policy.program-row-refusal)。
  (setv #^ str update "recreate"))


(defenum GenerationOrder CURRENT OLDER NEWER)


;; heartbeat の process の世代が、同じ名の今の世代に比べてどれか(cluster_policy.generation-order — 2026-09-27)。
;; CURRENT = 今の世代(初めての名・世代を名乗らない旧い worker を含む)・OLDER = 古い世代(名乗りとして受けない)・
;; NEWER = 新しい世代(今の世代を退かせる)。


(defwire KnownExit
  "worker の上の宣言の在る job 1 つの、最後に終わったと知れた刻と今の世代の process(WorkerInfo.known-exits の 1 行 — 保存する・#3672):
   job = job の名(状態の報告の行の name)・at-ms = 最後に終わったと知れた刻(epoch ms・まだ知らなければ None)・has-process = 今の世代の
   最新の報告でこの job の行が pid を持つ(process が在る)か。作るのは cluster_policy.known-exits-after。機体が死んで世代が入れ替わった
   時は、process を持っていた job を新しい世代の起動の刻までに終わったと数える(実の終わりはそれ以前)。at-ms が None の行は has-process が
   真の行だけ(何も知らない job は列に載せない)。保存の行 worker/<名> の knownExits の 1 行 {job atMs hasProcess} はこの型で解く
   (coordinator/protocol/state_json.worker-generations-from-json)。"
  {:tags {:context "coordinator" :role "type" :reads "json"} :names :camel :unknown :reject}
  (#^ str job)
  (#^ (| int None) at-ms)
  (#^ bool has-process))


(defclass [(dataclass :frozen True)] WorkerInfo []
  (#^ str name)
  (#^ (get tuple #(str ...)) provides)                 ; 提供する能力の名(名の順 — クラスタの設定で名乗る)
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
  ;; platform = root のキーの材料(shared/core/native_wheel の current_platform)・env-ready / env-preparing = 準備済み / 準備中の root のキー・
  ;; env-failed = 準備に失敗した root(EnvFailed の tuple)・env-capacity = "ok" か "exhausted"(準備を始める空きが無い)。
  (setv #^ str platform "")
  (setv #^ (get frozenset str) env-ready (frozenset))
  (setv #^ (get frozenset str) env-preparing (frozenset))
  (setv #^ (get tuple #(EnvFailed ...)) env-failed #())
  (setv #^ str env-capacity "ok")
  ;; 先の組みを memory を測らずに始めた root のキー(heartbeat の envs.memoryUnmeasured — 温める表の行の memory-unmeasured の材料・#3748)。
  ;; 位置で渡す欄の後ろに置かない(retired と boot-at の前 — どちらも名で渡す)。
  (setv #^ (get frozenset str) env-memory-unmeasured (frozenset))
  ;; 退いた世代(boot の欄の説明 — 位置で渡す欄の後ろに置く)。
  (setv #^ (get tuple #(str ...)) retired #())
  ;; 今の世代の process の起動時刻(epoch ms・heartbeat の bootAt — 2026-09-27)。今の世代と来た世代の両方の起動時刻を知る時は、
  ;; 大きい方を新しい世代とする(cluster_policy.generation-order)。状態を失った coordinator に新しい世代が先に届いても、後から来た
  ;; 古い世代に今の世代を明け渡さない。起動時刻を名乗らない旧い worker・旧い形の置き場は None(初めて見た順へ落とす)。保存する。
  (setv #^ (| int None) boot-at None)
  ;; 専用の能力(provides の一部・名の順)。空でなければ、このどれかを needs に持つ job / task だけを置く(以前の dedicated の印)。
  (setv #^ (get tuple #(str ...)) exclusive #())
  ;; worker の置かれた node の名(heartbeat の node — k8s の downward API。k8s の外の機体は空)と、coordinator がその node の label から
  ;; 導いた能力(ClusterNaming の node-capabilities — worker の自己申告ではない)。置き先の判断は provides と derived の和を見る。
  (setv #^ str node "")
  (setv #^ (get tuple #(str ...)) derived #())
  ;; 最後の連絡の時刻(last-seen-ms)を、coordinator の生存の印と同じ拍(api_policy.mark-alive・ALIVE-MARK-MS ごと)で写した値 = 耐久の鍵
  ;; worker/<名> の lastSeenMs(2026-09-25)。last-seen-ms は heartbeat ごとに進むが保存の行には入れない — 書きは印の拍ごと(5 秒に
  ;; 1 回)。起動の時は、この値と alive-ms の差(止まる前の最後の印の時点の沈黙)を今から数え直す(api_policy.resume-after-downtime)。
  ;; まだ印の拍を通っていない worker は None(lastSeenMs を書かない)。以前は ClusterState の写像 seen-marks(worker 名 → 時刻)に
  ;; 持っていた — worker の保存の行の材料をこの記録 1 つに寄せた(#2903)。
  (setv #^ (| int None) seen-mark None)
  ;; task のために空けておく数(heartbeat の taskReserve — 0 以上 capacity 以下・必ず名乗る)。常駐の job と並べた置き先(surge)は
  ;; capacity からこの分を引いた数までしか置かない(cluster_policy.job-room-of)。task は予約を使い切ったら job の残りへはみ出してよい
  ;; (cluster_policy.task-room-of は capacity 全体から数える)。保存する。位置で渡す欄の後ろに置くので、KW_ONLY の印の後の名で渡す
  ;; 必ずの欄(既定の値なし — 型の宣言 .pyi でも必ずの欄として読まれる)。
  (#^ KW_ONLY _)
  (#^ int task-reserve)
  ;; 宣言の在る job ごとの、最後に終わったと知れた刻と今の世代の報告で process を持つか(KnownExit の列・job の名の順 — #3672)。
  ;; heartbeat ごとに cluster_policy.known-exits-after が作り直す。保存する(worker/<名> の knownExits — 状態の報告 statuses は保存しないので、
  ;; coordinator を作り直した後に最初に来る新しい世代の heartbeat も、前の世代で process を持っていた job をこの列から数える)。
  ;; 中身が替わるのは process の起き・終わり・世代の入れ替わりの時だけ(同じ世代の heartbeat をくり返しても保存の行は変わらない)。
  ;; 欄の無い保存の行は空の列として読む。
  (setv #^ (get tuple #(KnownExit ...)) known-exits #()))


(defrecord WorkerLoad
  "worker 1 台の担っている数(cluster_policy.load-of の値): jobs = 常駐の job の置き先と並べた置き先(surge)の数・tasks = 置かれた task
   (PLACED-PHASES)の数。置ける空き(job-room-of・task-room-of)はこの 2 つと worker の capacity・task-reserve から求める。"
  {:tags {:context "coordinator" :role "type"}}
  (#^ int jobs)
  (#^ int tasks))


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
  (#^ (get dict #(str object)) runtime-env)
  (#^ (get tuple #(str ...)) needs)
  (#^ int until-ms)
  (#^ str holder))


(defrecord ProgramRow
  "置き場に置いた詰めた Program 1 つ(PUT /programs/<sha> — 改訂 1 の F): blob = 詰めた Program(base64 の文字列)・versions = 詰めた
   送り手の版(名 → 版)・put-ms = 置いた時刻(参照の無い Program を猶予の後に消す — program_policy.sweep-programs)。#2447 で JSON の dict を
   この型にした。保存の JSON の形 {blob versions putMs} は cluster_policy の program-row-to-json / program-row-from-json。"
  (#^ str blob)
  (#^ (get dict #(str str)) versions)
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
  (setv #^ (| (get dict #(str object)) None) last-action None)
  (setv #^ (| (get dict #(str int)) None) simulated None)
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
  (#^ (get dict #(str object)) changes))


(defrecord EventsView
  "GET /events の答え(resource_policy.events-view — #2595): revision = coordinator 全体の版・seq = 出来事の通し番号の今の値・events = 問いの
   kind / name / since に合う出来事(古い順・limit 件まで)。JSON の形 {revision seq events} は coordinator/protocol/replies が綴る。"
  (#^ int revision)
  (#^ int seq)
  (#^ (get tuple #(AuditEvent ...)) events))


(defrecord ServiceView
  "状態の画面の Service 1 つ: job = 宣言・resource-version = 資源の版(版の記録が無ければ None)。"
  (#^ ClusterJob job)
  (#^ (| int None) resource-version))


(defrecord WorkerView
  "状態の画面の worker 1 つ: info = 名乗り・silent-ms = 最後の連絡からの長さ・live = heartbeat が lease の内か・draining = 期限の内の drain
   か(担い手の名簿の読み ReadRunners の正本 — 2026-09-26)・task-room = いま task を置ける空き(cluster_policy.task-room-of の答え)。"
  (#^ WorkerInfo info)
  (#^ int silent-ms)
  (#^ bool live)
  (#^ bool draining)
  (#^ int task-room))


(defrecord StatusView
  "状態の画面の worker の最後の報告 1 つ: report = 報告・stale = lease より古いか(沈黙した worker の最後の報告は「いま動いている」の
   証拠にならないので古さを付けて見せる)。"
  (#^ WorkerReport report)
  (#^ bool stale))


(defrecord KeepMark
  "途絶しても動かし続けてよい印の約束 1 つ(#2804)= coordinator が worker へ「この job は途絶しても止めなくてよい」と返事で渡した事実。
   印を渡した担い手は coordinator に届かない間も job を動かし続けうるので、coordinator はこの約束が在る間、job を他の worker へ置かない
   (担い手の上の置き先を、沈黙・能力の変化・drain を問わず保つ — cluster_policy.place-jobs)。約束が外れるのは、担い手の今の世代の heartbeat が
   その job の印を持たないと知らせた時(keptWhenCutOff — 印の無い返事が届いた後)か、Worker が消された時だけ。
   job = job の名・worker = 印を渡した担い手の名・boot = 渡した時の担い手の process の世代(表示 — 世代が替わった担い手は新しい世代の
   知らせで約束を外す)・since-ms = 初めて渡した時刻。保存する(durable_kv の keep/<名> — coordinator を作り直しても約束を忘れない)。
   読みの口: GET /state の keepMarks(#2883 — StateView.keep-marks)。"
  {:tags {:context "coordinator" :role "type"}}
  (#^ str job)
  (#^ str worker)
  (#^ (| str None) boot)
  (#^ int since-ms))


(defrecord StateView
  "GET /state の状態の画面(cluster_policy.state-view — #2595): now・services・workers・placements(job の名 → Placement)・unplaced(job の名 →
   置き先が無い理由)・statuses(worker の名 → StatusView)・tasks・board-keys = 盤の行の数・surges(job の名 → Placement)・events = 直近
   50 件の割り当ての移り変わり・revision・keep-marks = 途絶しても動かし続けてよい印の約束の列(job の名の順 — #2883: 版上げの後と障害の時に、
   どの job が約束で担い手に留まっているかを外から確かめるため・読みだけ)・coordinator-commit = 答えた coordinator の process が走っている
   doeff の版(ClusterState の running-commit — 読めなければ None で、JSON の欄 coordinatorCommit を書かない。版上げの Program が「新しい
   版の coordinator が答えた」を、宣言した版でなく答えた process の版で判じるため・#3772)。JSON の形は coordinator/protocol/replies が綴る。"
  (#^ int now)
  (#^ (get tuple #(ServiceView ...)) services)
  (#^ (get tuple #(WorkerView ...)) workers)
  (#^ (get dict #(str Placement)) placements)
  (#^ (get dict #(str str)) unplaced)
  (#^ (get dict #(str StatusView)) statuses)
  (#^ (get tuple #(TaskRecord ...)) tasks)
  (#^ int board-keys)
  (#^ (get dict #(str Placement)) surges)
  (#^ (get tuple #(object ...)) events)
  (#^ int revision)
  (#^ (get tuple #(KeepMark ...)) keep-marks)
  (#^ (| str None) coordinator-commit))


(defenum DrainPhase
  (DRAINING "Draining")
  (DRAINED "Drained")
  (BLOCKED "Blocked"))

;; drain の進みの段。DRAINING = まだ残りが在る・DRAINED = 残りが 0(preStop が終わる)・BLOCKED = 待てば移せる job が在り、どれも移している
;; 途中でない。待っても移せない job(能力の合う別の worker が名簿に無い — DrainProgress.unmovable)は残りに数えない(#3669)。


(defrecord DrainProgress
  "drain の進み 1 つ(drain_policy.drain-view — #2595): worker・boot = drain を頼んだ世代・superseded = 退いた世代の待ちの答えか
   (superseded-worker-view)・since-ms / until-ms / actor = drain の頼みの記録(退いた世代の答えには無い — None)・phase・remaining = まだ
   残っている job と task の名・moving(job の名 → 並べた先の worker)・blocked(job の名 → 今は移せず待っている理由)・unmovable = 待っても
   移せないので待たない job の名(能力の合う別の worker が名簿に 1 台も無い — #3669)・moving-ready(job の名 → 並べた先の準備の理由)。
   対の欄は job の名から引く表なので dict で持つ。JSON の形は coordinator/protocol/replies が綴る。"
  (#^ str worker)
  (#^ (| str None) boot)
  (#^ bool superseded)
  (#^ (| int None) since-ms)
  (#^ (| int None) until-ms)
  (#^ (| str None) actor)
  (#^ DrainPhase phase)
  (#^ (get tuple #(str ...)) remaining)
  (#^ (get dict #(str str)) moving)
  (#^ (get dict #(str str)) blocked)
  (#^ (get tuple #(str ...)) unmovable)
  (#^ (get dict #(str str)) moving-ready))


(defrecord WorkerDrainView
  "GET /workers/<名> と drain の頼みの答え(drain_policy.worker-view・superseded-worker-view — #2595): info = 名乗り・alive = heartbeat が
   lease の内か・silent-ms・superseded = 退いた世代の待ちの答えか・drain = drain の進み(drain が無ければ None)・ready = 生きていて drain
   中でない(新しい Pod の readinessProbe が見る)。JSON の形は coordinator/protocol/replies が綴る。"
  (#^ WorkerInfo info)
  (#^ bool alive)
  (#^ int silent-ms)
  (#^ bool superseded)
  (#^ (| DrainProgress None) drain)
  (#^ bool ready))


(defrecord StateReply
  "GET /state の答え(#2595): view = 状態の画面(StateView)・audit = 直近の出来事
   (30 件)・drains = drain の画面(worker の名 → DrainProgress — drain_policy.drains-view)。JSON の形(view に audit と drains を足した
   object)は coordinator/protocol/replies が綴る。"
  (#^ StateView view)
  (#^ (get tuple #(AuditEvent ...)) audit)
  (#^ (get dict #(str DrainProgress)) drains))


(defrecord WorkerReport
  "worker 1 つの最新の状態の報告(ClusterState.statuses の値 — 鍵 = worker の名・保存しない): at = 受けた時刻(epoch ms)・endpoint =
   worker が名乗った宛先(名乗らない旧い worker は None)・jobs = job の行の列(heartbeat の statuses の行 StatusRow から、結果の
   欄 result と task の写しを外した物 — 持ち続けるのは process の姿だけ)。#2447 で dict をこの型にした。jobs の行の last-exit-at-ms は
   最後に終わったと知れた刻(WorkerInfo.known-exits の刻と行の刻の大きい方 — cluster_policy.worker-report・#3672)。"
  (#^ int at)
  (#^ (| str None) endpoint)
  (#^ (get tuple #(StatusRow ...)) jobs))


(defrecord ServiceBody
  "POST /resources/Service・PUT /resources/Service/<名> の本文を Service の宣言に解いた値(本文を解く所 coordinator/protocol/request_bodies が
   作る — #2448): name = 資源の名(POST は本文の name・PUT は path の名)・job = 宣言の行を読んだ job(owner は本文の owner のまま —
   所有者を決めるのは判断)・owner = 本文の owner(無ければ None・形は判断が valid-actor で検める)・resource-version = 読んだ時の版(PUT)。"
  (#^ (| str None) name)
  (#^ ClusterJob job)
  (#^ object owner)
  (#^ (| int None) resource-version))


(defrecord LegacyJobRow
  "旧い PUT /jobs の行 1 つを Service の宣言に解いた値: name = Service の名・version = 行の resourceVersion(無ければ None)・owner = 行の
   owner(無ければ None)・job = 行を読んだ job(replicas と readiness は行に在る時だけ行の値 — 無ければ判断が今の宣言の値で埋める)・
   replicas-given / readiness-given = 行にその欄が在ったか。"
  (#^ str name)
  (#^ object version)
  (#^ object owner)
  (#^ ClusterJob job)
  (#^ bool replicas-given)
  (#^ bool readiness-given))


(defrecord LegacyJobs
  "旧い PUT /jobs の本文を解いた値: rows = 行の列(LegacyJobRow)・actor = 送り手(header X-Actor が無い時)。"
  (#^ (get tuple #(LegacyJobRow ...)) rows)
  (#^ (| str None) actor))


(defrecord RefusedJob
  "受け付けない Service の行(2026-09-27・改訂 1 の C)。旧い宣言の形の行を読み直した時と、読めない行を、coordinator を落とさずに
   持っておく: name = Service の名・row = 元の行(保存と表示のため JSON のまま)・reason = 理由。置き先・Rollout・drain・計器は
   ClusterState.jobs(受け付けた job)だけを見て、これは見ない。PUT で新しい形に書き直せば jobs へ移る。"
  (#^ str name)
  (#^ (get dict #(str object)) row)
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

  (defn #^ (get dict #(str object)) to-json [self]  ; defk にできない: 保存の形へ写す dataclass の口(coordinator の純粋な関数が呼ぶ)
    "保存の形(durable_kv と state-to-json が使う)。"
    {"declaration" self.declaration "sinceMs" self.since-ms "phase" self.phase.value
     "abandonedMs" self.abandoned-ms "reason" self.reason "lastReport" self.last-report})

  (defn #^ (get dict #(str object)) status-json [self #^ int timeout-ms]  ; defk にできない: 資源の表示の形へ写す dataclass の口(coordinator の純粋な関数が呼ぶ)
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


(defenum TaskUnplacedKind TASK-NO-ROOM)


;; 待っている task を置けない理由(cluster_policy.place-tasks が detail に書く文の種類 — cluster_policy.task-unplaced-text)。
;; TASK-NO-ROOM = 能力と版の合う生きた worker が在り、drain 中でも disk 尽きでもない worker も在るが、どれも task を置ける空き
;; (task-room-of)が 0(常駐の job と置かれた task で capacity が埋まっている)。


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


;; --- 保存しない k8s の観測(#2728 J1)---------------------------------------------------------------------------------------
;;
;; Rollout の調停の拍(core/program.rollout-tick)が k8s から読んだ物。ClusterState.observations(ClusterObservations)の表に置き、
;; 版の比べ(resource_policy.stamp の dirty-keys)と保存の差分(protocol/durable_kv の SOURCE-GROUPS)の外に在る — 観測を書いても
;; 資源の版も保存の行も動かない。k8s の JSON を型へ解くのは答え手(coordinator/protocol/kube)の 1 点で、core は型の値だけを読む。

(defwire DeploymentReading
  "k8s の Deployment を読んだ答え 1 つ(読みの束の答え — protocol/kube が k8s の JSON から解く・Rollout が見る欄だけ): spec-replicas = 宣言の台数・replicas /
   ready-replicas / available-replicas / updated-replicas = status の台数・generation = 宣言の世代・observed-generation = controller が
   見た世代・annotations = metadata.annotations(中を読まない — 資源の画面へそのまま写すだけ)。欄の名と順は GET /resources/Rollout の
   status.observed の行の形と同じ(coordinator/protocol/replies が dump で綴る)ので、既定値を持たせない(dump は既定値の欄を省く)。"
  {:tags {:context "coordinator" :role "type" :reads "json"} :names :camel :unknown :ignore}
  (#^ int spec-replicas)
  (#^ int replicas)
  (#^ int ready-replicas)
  (#^ int available-replicas)
  (#^ int updated-replicas)
  (#^ int generation)
  (#^ int observed-generation)
  (#^ OpaqueJson annotations))


(defrecord DeploymentSeen
  "読めた Deployment の観測(Rollout の相手の Ready / 止まったの判じと、台数の食い違いの材料): reading = k8s の答え・at = 読んだ時刻(epoch ms)。"
  (#^ DeploymentReading reading)
  (#^ int at))


(defrecord DeploymentUnreadable
  "読めなかった Deployment の観測(Rollout はこの相手を Unknown と扱い、台数を変えない): error = 理由・at = 試した時刻(epoch ms)。"
  (#^ str error)
  (#^ int at))


(defrecord NodeLabelsSeen
  "読めた node の label(worker の能力の導出の材料 — cluster_policy.with-derived-capabilities): labels = label の鍵 → 値・
   at = 読んだ時刻(epoch ms)。"
  (#^ (get Table str) labels)
  (#^ int at))


(defrecord NodeLabelsUnreadable
  "読めなかった node の label(その node の worker は前に導いた能力を保つ): error = 理由・at = 試した時刻(epoch ms)。"
  (#^ str error)
  (#^ int at))


;; --- 保存しない Service の process の報告(#2756 J2)-----------------------------------------------------------------------
;;
;; service の process が拍ごとに送る準備の報告(ReportReady)と計器の報告(ReportMetrics)。POST /resources/Service/<名>/readiness・
;; …/metrics の本文の JSON を道の型(coordinator/intent/request_bodies の ReadinessBody・MetricsBody)へ解くのは受け口の 1 点
;; (coordinator/protocol/request_bodies.body-of)で、core の受け取り(resource_policy.record-readiness・metrics_policy.record-metrics)は
;; 型の値に受けた時刻を添えてこの記録にする。ClusterState.observations の readiness・metrics の表に Service の名ごとに置く(process の
;; 世代ごとに最新 1 つ・直近の数世代 — 古い順の tuple)。保存の差分(durable_kv の SOURCE-GROUPS)の外。readiness の表だけは Service の
;; status.ready の材料なので、版の比べ(resource_policy.dirty-keys)が読む(計器の表は読まない — 計器は資源の状態を変えない)。

(defrecord ReportOrigin
  "報告を送った process の世代と受けた時刻(準備の報告と計器の報告で同じ欄 — 数えるのは今の process の報告だけ・resource_policy.
   report-matches): worker = 担い手の worker・pid = process の番号・revision = 版・instance = 世代の名・attempt = 試行の番号(送られた型の
   まま — 資源の画面の status.lastReadiness が JSON へそのまま返す)・spec-hash = 起こした spec の指紋・placement = 割り当ての世代・
   at = coordinator が受けた時刻(epoch ms)。旧い process が欠いた欄は None。"
  (#^ str worker)
  (#^ (| int None) pid)
  (#^ str revision)
  (#^ (| str None) instance)
  (#^ (| str int None) attempt)
  (#^ (| str None) spec-hash)
  (#^ (| int None) placement)
  (#^ int at))


(defrecord ReadinessReport
  "準備の報告(ReportReady)1 つ: origin = 送り手の世代と受けた時刻・ready = 準備できたか・reason = 理由・role = active(仕事をしている)か
   standby(lease を他が持つ間の待機)。ready・reason・role の揃え方は shared/core/readiness_report.reported-readiness(fake と同じ)。"
  (#^ ReportOrigin origin)
  (#^ bool ready)
  (#^ str reason)
  (#^ str role))


(defrecord MetricsReport
  "計器の報告(ReportMetrics)1 つ(名と値の検めを通った物 — metrics_policy.metrics-refusal): origin = 送り手の世代と受けた時刻・
   counters / gauges = 計器の名 → 値・durations = 名 → 合計の秒と回数。報告に無い族は空の表。GET /metrics が名の順に綴る。"
  (#^ ReportOrigin origin)
  (#^ (get Table (| int float)) counters)
  (#^ (get Table (| int float)) gauges)
  (#^ (get Table DurationRow) durations))


(defrecord ClusterObservations
  "coordinator が外から読んだ・受けた、保存しない観測の置き場(ClusterState.observations — 上の 2 つの註)。deployments = 「ns/名」→
   Deployment の最後の観測・nodes = node の名 → label の最後の観測(どちらも読み直す間隔を決める at を持つ)・readiness = Service の名 →
   準備の報告の列・metrics = Service の名 → 計器の報告の列(どちらも process の世代ごとに最新 1 つ・古い順)。"
  (setv #^ (get Table (| DeploymentSeen DeploymentUnreadable)) deployments (field :default-factory (partial table-of #())))
  (setv #^ (get Table (| NodeLabelsSeen NodeLabelsUnreadable)) nodes (field :default-factory (partial table-of #())))
  (setv #^ (get Table (get tuple #(ReadinessReport ...))) readiness (field :default-factory (partial table-of #())))
  (setv #^ (get Table (get tuple #(MetricsReport ...))) metrics (field :default-factory (partial table-of #()))))


(defrecord ObservedDeployment
  "資源の画面の Rollout の相手の Deployment 1 つ: key = 相手の鍵(rollout_policy.target-key)・seen = 最後の観測(まだ読んでいなければ None)。"
  (#^ str key)
  (#^ (| DeploymentSeen DeploymentUnreadable None) seen))


(defrecord ServiceObserved
  "資源の画面の Service の観測(resource_policy.resource-view — #2595): ready-reason = 準備の判定の理由・last-readiness = 最後に受けた準備の
   報告(無ければ None — JSON の形は coordinator/protocol/replies が綴る)・process = 置き先の worker の最後の報告の行・version = 版の判定・
   running = 生きている process の版の列。"
  (#^ str ready-reason)
  (#^ (| ReadinessReport None) last-readiness)
  (#^ (| StatusRow None) process)
  (#^ VersionVerdict version)
  (#^ (get tuple #(LiveProcess ...)) running))


(defrecord WorkerObserved
  "資源の画面の Worker の観測: silent-ms = 最後の連絡からの長さ・alive = heartbeat が lease の内か。"
  (#^ int silent-ms)
  (#^ bool alive))


(defrecord TaskObserved
  "資源の画面の Task の観測: task = task の行(要約の JSON は protocol が綴る)。"
  (#^ TaskRecord task))


(defrecord RolloutObserved
  "資源の画面の Rollout の観測: observed = 相手の Deployment ごとの最後の観測(spec の from → to の順・Deployment の相手だけ)。
   JSON の形(鍵 → 観測の object か null)は coordinator/protocol/replies が綴る。"
  (#^ (get tuple #(ObservedDeployment ...)) observed))


(defrecord ResourceView
  "GET /resources/<種類>/<名> の資源 1 つ(resource_policy.resource-view — #2595): kind・name・meta = 版の記録(無ければ None)・spec と status =
   版を進める比べる単位(resource_policy.snapshot の行 — 差分が出来事の記録の changes に JSON の値のまま残るので、ここも JSON の値で持つ)・
   observed = 種類ごとの変わりやすい観測(比べる単位に入れない物)。JSON の形は coordinator/protocol/replies が綴る。"
  (#^ str kind)
  (#^ str name)
  (#^ (| ResourceMeta None) meta)
  (#^ (get dict #(str object)) spec)
  (#^ (get dict #(str object)) status)
  (#^ (| ServiceObserved WorkerObserved TaskObserved RolloutObserved None) observed))


(defrecord ResourceList
  "GET /resources/<種類> の答え: kind・revision = coordinator 全体の版・items = 資源の画面の列(鍵の順)。"
  (#^ str kind)
  (#^ int revision)
  (#^ (get tuple #(ResourceView ...)) items))


(defrecord RowConflict
  "旧い一括の宣言(PUT /jobs)で書けなかった行 1 つ(resource_policy.legacy-put-jobs — #2614): name・message = 理由・current = いまの版
   (版の食い違いの時だけ)。"
  (#^ str name)
  (#^ str message)
  (setv #^ (| int None) current None))


(defrecord ErrorReply
  "断った要求の答えの本文(#2614): message = 理由の文・current = いまの版(版の食い違いの時だけ)・conflicts = 書けなかった行・open = 終わって
   いない task の本数(上限の時だけ)・fault = coordinator の中の欠陥か(送り手の誤りでないことを名乗る)。JSON の形({error …})は
   coordinator/protocol/replies が綴り、付け足しの欄は在る時だけ書く。"
  (#^ str message)
  (setv #^ (| int None) current None)
  (setv #^ (| (get tuple #(RowConflict ...)) None) conflicts None)
  (setv #^ (| int None) open None)
  (setv #^ bool fault False))


(defrecord BoardUsage
  "盤の使い方と上限(cluster_policy.board-usage — #2614): rows = 行の数・bytes = 値の合計の byte 数・expiring = 期限つきの行の数・
   max-rows / max-bytes / max-value-bytes = 上限。容量の判断・計器・容量で断った答えが読む。"
  (#^ int rows)
  (#^ int bytes)
  (#^ int expiring)
  (#^ int max-rows)
  (#^ int max-bytes)
  (#^ int max-value-bytes))


(defrecord BoardEntryView
  "GET /board の答えの行 1 つ: key・value = 書かれた値(JSON の値のまま)・version = 行の版。"
  (#^ str key)
  (#^ object value)
  (#^ int version))


(defrecord BoardRead
  "GET /board の答え(#2614): entries = 前置きに合う行(鍵の順)・with-versions = 版も見せるか(問いの withVersions)。JSON の形
   (鍵 → 値、または鍵 → {value resourceVersion})は coordinator/protocol/replies が綴る。"
  (#^ (get tuple #(BoardEntryView ...)) entries)
  (#^ bool with-versions))


(defrecord BoardWritten
  "盤の書きが通った答え(cluster_policy.board-write — #2614): version = 行の新しい版(消した時は None)。"
  (#^ (| int None) version))


(defrecord BoardConflict
  "盤の compare-and-set が合わなかった答え(409): current = いまの値・version = いまの版・reason = lease の行を追い出せない理由
   (lease の行への直の書きの時だけ)。"
  (#^ object current)
  (#^ int version)
  (setv #^ (| str None) reason None))


(defrecord BoardRefused
  "盤の書きを断った答え: reason = 理由・usage = 盤の使い方(容量で断った時だけ — 507)。期限の誤りは 400。"
  (#^ str reason)
  (setv #^ (| BoardUsage None) usage None))


(defrecord TaskAccepted
  "POST /tasks の答え(cluster_policy.submit-task — #2614): id = 作った task の id。"
  (#^ str id))


(defrecord TaskProgress
  "GET /tasks/<id> の答え(cluster_policy.poll-task): いまの様子 — phase・worker・detail・result・failure-kind・retryable(TaskRecord の欄)。"
  (#^ str phase)
  (#^ (| str None) worker)
  (#^ str detail)
  (#^ (| str None) result)
  (#^ str failure-kind)
  (#^ bool retryable))


(defrecord TaskMissing
  "GET /tasks/<id> で task を知らない答え(呼び手が落とした・lease が切れた): id = 問われた id。JSON は {phase: missing}。"
  (#^ str id))


(defrecord TaskResultTaken
  "POST /tasks/<id>/result の答え(cluster_policy.absorb-task-result): accepted = この届けで結果を写したか(終わった task への 2 度目の
   届けは False)・phase = task のいまの段。"
  (#^ bool accepted)
  (#^ str phase))


(defrecord TaskDropped
  "DELETE /tasks/<id> の答え: id = 取り下げた task の id(知らない id でも答えは同じ)。"
  (#^ str id))


(defrecord DetachedSubmitted
  "PUT /detached/<key> の答え(detached_policy.submit-detached — #2614): key・id = task の id・created = この頼みで作ったか(同じ key の
   行が在れば False)・phase。"
  (#^ str key)
  (#^ str id)
  (#^ bool created)
  (#^ str phase))


(defrecord DetachedProgress
  "GET /detached/<key> の答え(detached_policy.detached-view): いまの様子 — key・id・phase・detail・result・worker・failure-kind・retryable。"
  (#^ str key)
  (#^ str id)
  (#^ str phase)
  (#^ str detail)
  (#^ (| str None) result)
  (#^ (| str None) worker)
  (#^ str failure-kind)
  (#^ bool retryable))


(defrecord DetachedUnknown
  "GET /detached/<key> で key の行が無い答え(JSON は {key phase: unknown})。"
  (#^ str key))


(defrecord DetachedWarming
  "GET /detached/<key> の 503: coordinator が起きた直後で、行の無い key を知らないと言えない(detached_policy.detached-read)。phase = warming
   の名(detached_model.WARMING-PHASE)・reason = 理由の文。"
  (#^ str key)
  (#^ str phase)
  (#^ str reason))


(defrecord DetachedCancelled
  "POST /detached/<key>/cancel の答え: cancelled = この頼みで取り消したか・phase = いまの段(行が無ければ unknown)。"
  (#^ str key)
  (#^ bool cancelled)
  (#^ str phase))


(defrecord TargetView
  "Rollout の相手 1 つの観測(api_policy.target-view が作り rollout_policy.rollout-step が読む — #2614): ready = Ready | NotReady | Unknown・
   stopped = 止まっているか(観測が無い・古い時は None)・spec-replicas = 宣言の台数(知らなければ None)・reason = 人が読む理由。"
  (#^ str ready)
  (#^ (| bool None) stopped)
  (#^ (| int None) spec-replicas)
  (#^ str reason))


(defrecord ProgramStored
  "PUT /programs/<sha> の答え(program_policy.program-write — #2614): sha = 置いた Program のキー。"
  (#^ str sha))


(defrecord DetachedReleased
  "DELETE /detached/<key> の答え: released = この頼みで行を消したか(行が無ければ False)。"
  (#^ str key)
  (#^ bool released))


(defclass [(dataclass :frozen True)] ClusterNaming []
  "クラスタが外の系(k8s の Deployment・Node)と取り交わす名。どれも配備する側(composition root の引数)が決める。
   owner-annotation = Rollout が台数を持つ Deployment に付ける annotation の鍵。
   owner-scope      = その値の頭に付ける、このクラスタの名(「<scope>/Rollout/<名> replicas=<n>」)。
   node-capabilities = node の label から導く能力 #(#(label の鍵 値 能力の名) …)(ADR-DOE-CLUSTER-001 R4b・改訂 1 の I)。ここに在る能力は
                      worker が自分で名乗っても受けない — coordinator が worker の置かれた node の label を読んで足す(会社の機体の境界を
                      worker の自己申告に任せない)。既定 = company-machine を label doeff.dev/company-machine=true から。"
  (setv #^ str owner-annotation "doeff-cluster/replicas-owned-by")
  (setv #^ str owner-scope "doeff-cluster")
  (setv #^ (get tuple #((get tuple #(str str str)) ...)) node-capabilities #(#("doeff.dev/company-machine" "true" "company-machine"))))


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
  (#^ (get tuple #(str ...)) needs)                    ; 要る能力の名(名の順)
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
  (setv #^ (| (get dict #(str object)) None) runtime-env None)
  (setv #^ int env-attempts 0)
  (setv #^ (get tuple #(str ...)) avoid #())
  (setv #^ str failure-kind "")
  (setv #^ bool retryable False)
  ;; --- 子の環境変数(2026-09-28)---
  ;; environ = 送り手の effect の :environ(名の順の #(名 値) の tuple — service の JobSpec.environ と同じ形)。heartbeat の返事で worker へ
  ;; 運び、worker は service と同じ路(ProcessHost.launch)で子の環境変数に置く。この欄の無い行(2026-09-28 より前)は空で読む。
  (setv #^ (get tuple #((get tuple #(str str)) ...)) environ #()))


;; 担い手の worker に置いた task の phase(担い手の数・送る task・報告の吸い上げ・lease の延長で同じに扱う)。
(setv PLACED-PHASES (frozenset #("assigned" "preparing")))


;; 終わった task の phase(旧い形の保存の行を読む時に、まだ終わっていない行だけを断る — task-record-from-json)。
(setv ENDED-PHASES (frozenset #("finished" "code-failed" "failed" "version-mismatch" "lost" "cancelled" "env-failed")))


(defclass [(dataclass :frozen True)] ClusterState []
  (setv #^ (get tuple #(ClusterJob ...)) jobs #())
  (setv #^ (get dict #(str WorkerInfo)) workers (field :default-factory dict))
  (setv #^ (get dict #(str Placement)) placements (field :default-factory dict)) ; job の名 → Placement
  (setv #^ (get dict #(str TaskRecord)) tasks (field :default-factory dict))
  (setv #^ int next-task 1)
  ;; task の id の頭(2026-09-27 — #757)。id = <頭><番号>。以前からの置き場は "t"(t1, t2 …)。置き場の無いところから起きた
  ;; coordinator は起動ごとに違う頭を振る(cluster_policy.fresh-task-prefix)— 前の coordinator が振った id(worker に blob が
  ;; 残り、子 process が走っているかもしれない)を振り直さない。保存する(counter の taskPrefix)。
  (setv #^ str task-prefix "t")
  (setv #^ (get dict #(str BoardRow)) board (field :default-factory dict))    ; 盤の鍵 → BoardRow(行と一緒に保存)
  (setv #^ (get dict #(str WorkerReport)) statuses (field :default-factory dict))  ; worker 名 → WorkerReport(保存しない)
  (setv #^ (get tuple #(object ...)) events #())                            ; 割り当ての移り変わり(直近 200 件・保存しない)
  ;; --- 資源(2026-09-24) ---
  (setv #^ (get dict #(str ResourceMeta)) meta (field :default-factory dict))      ; "Kind/名" → 資源の版の欄(resourceVersion・generation・作った / 書いた送り手と時刻)
  (setv #^ int revision 0)                              ; coordinator 全体の版の番号(書きのたびに 1 進む)
  (setv #^ (get tuple #(AuditEvent ...)) audit #())                             ; 出来事の記録 AuditEvent の列(kind ごとに件数の上限つき・保存する)
  (setv #^ int audit-seq 0)
  (setv #^ (get dict #(str RolloutRow)) rollouts (field :default-factory dict))  ; Rollout の名 → RolloutRow
  ;; --- 保存しない観測 ---
  ;; Service の process の準備と計器の報告(#2756)、k8s の Deployment と node の label の最後の観測(#2728)は最後の欄 observations
  ;; (ClusterObservations)。
  ;; node の label から導く能力の名(ClusterNaming の node-capabilities の能力 — coordinator の起動で入れる・保存しない)。
  ;; worker の heartbeat の provides にこの名が在っても受けない(自己申告を断る — 改訂 1 の I)。
  (setv #^ (get frozenset str) derivable (frozenset))
  ;; この coordinator の process が走っている doeff の版(起動の時に入口が 1 度、環境変数 WORKER_DOEFF_COMMIT から読む — 入口の
  ;; with-running-commit。読めなければ None)。保存しない(process の世代ごとの事実)。GET /state の coordinatorCommit に載る(#3772)。
  (setv #^ (| str None) running-commit None)
  ;; 受け付けない Service の行(名 → RefusedJob — 改訂 1 の C)。保存する(元の行のまま)— 読み直しても同じ理由で受け付けない。
  (setv #^ (get dict #(str RefusedJob)) refused (field :default-factory dict))
  ;; 詰めた Program の置き場(sha → {"blob" "versions" "putMs"} — program_policy・改訂 1 の F)。保存する。
  (setv #^ (get dict #(str ProgramRow)) programs (field :default-factory dict))
  (setv #^ int started-ms 0)                            ; この coordinator の process が状態を読んだ時刻(観測が揃うまでの猶予)
  ;; coordinator が生きていた最後の時刻(ALIVE-MARK-MS ごとに耐久の鍵 counter へ書く)。起動の時に「止まっていた長さ」を測り、
  ;; 進行中の Rollout の段の起点と task の lease を、その長さだけずらす(api_policy.resume-after-downtime・2026-09-25)。
  ;; 同じ拍(mark-alive)で、各 worker の最後の連絡の時刻を WorkerInfo の欄 seen-mark に写す(耐久の鍵 worker/<名> の lastSeenMs)。
  ;; 以前は最後の連絡の時刻を保存せず、読み直しのたびに全 worker を「いま連絡があった」とみなしていたので、32 時間沈黙した
  ;; worker も coordinator が起き直すたびに生きていると出た(2026-09-25)。
  (setv #^ int alive-ms 0)
  ;; drain(2026-09-25): worker の名 → Drain と、入れ替えの Service を drain 中の worker から移す間の並べた置き先
  ;; (job の名 → Placement・surge)。surge の担い手は process を起こし(standby で待つ)、coordinator がそれを Ready と数えたら
  ;; placements をその置き先へ付け替える(旧い担い手は宣言から外れて止め、lease を返す)。どちらも保存する(durable_kv)。
  (setv #^ (get dict #(str Drain)) drains (field :default-factory dict))
  (setv #^ (get dict #(str Placement)) surges (field :default-factory dict))
  ;; 温める表(2026-09-26): 行のキー → WarmEntry。保存する(durable_kv の warm/<キー>)。
  (setv #^ (get dict #(str WarmEntry)) warms (field :default-factory dict))
  ;; 冷たい起動の数(実行環境の task を、準備済みの worker が 1 つも無いまま置いた回数 — 計器 doeff_worker_env_cold_start_total)。
  ;; 保存しない(counter は process の世代ごとに 0 から数える)。
  (setv #^ int env-cold-starts 0)
  ;; 入れ替え(handoff)の期限の見張り(2026-09-26): Service の名 → HandoffWatch。新の世代が動き出してから期限の間 Ready にならなければ
  ;; 諦めを記録し、heartbeat の返事の job に載せる(worker は新を止めて旧を残す — handoff_policy)。保存する(durable_kv)。
  (setv #^ (get dict #(str HandoffWatch)) handoffs (field :default-factory dict))
  ;; 生きていないと数えた worker の名(heartbeat が lease の外 — #1934)。調停の拍ごとに cluster_policy.note-liveness が時刻から
  ;; 求め直し、変わった拍だけ新しい値にする — Worker の資源の status の live と版は、この欄の変化で進む(時刻そのものを版の比べに
  ;; 入れると、何も変わらない拍の早い戻り(resource_policy.stamp)で切り替わりを取りこぼす)。保存しない(読み直しの後の最初の拍で
  ;; 求め直す)。位置の引数で作る呼び手を崩さないよう最後の欄に置く。
  (setv #^ (get frozenset str) silent (frozenset))
  ;; 外から読んだ・受けた保存しない観測(k8s の Deployment と node の label — #2728 J1・Service の process の準備と計器の報告 — #2756 J2。
  ;; worker の観測も順に移す)。保存の差分(durable_kv の SOURCE-GROUPS)はこの欄を見ない。版の比べ(resource_policy.dirty-keys)が読むのは
  ;; Service の status.ready の材料の readiness の表だけ。位置の引数の呼び手のため最後に置く。
  (setv #^ ClusterObservations observations (field :default-factory ClusterObservations))
  ;; 途絶しても動かし続けてよい印の約束(#2804): KeepMark の列(job の名の順・job ごとに 1 つ — 引くのは cluster_policy.keep-mark-of)。
  ;; 印を渡した担い手から job を他へ移さない約束で、担い手が印を持たないと知らせるか Worker が消されるまで残る(宣言から job が消えても
  ;; 残す — 途絶した担い手が古い宣言のまま動かしているかもしれない)。保存する(durable_kv の keep/<名>)。位置の引数の呼び手のため最後に置く。
  (setv #^ (get tuple #(KeepMark ...)) keep-marks #()))


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
;; 期限は入らない)を after で渡し、版がそれと違うようになるか、timeoutSeconds(ClusterTiming.watch-max-ms まで)が過ぎるまで返事を待つ。
;; 答え = {"revision" 今の版 "changed" 変わったか}。worker を名指せば、版が進んでもその worker の heartbeat の返事(温める表を除く)が
;; 変わらない間は起きない。調停ループ(coordinator.coordinator-step)が待ちの要求を持ち、書きの後(Persist の後)と歩ごとに判じる。
;; 期限の刻ちょうどに返す(coordinator は待ちの期限まで受付を待つ — wake_policy.watchers-due・#3865)。

;; 待ちの上限は worker も問いに載せる取り交わしの値なので ClusterTiming の watch-max-ms(shared/intent/protocol — #3865)。


(defrecord TaskOffer
  "heartbeat の返事で worker の process へ渡す task 1 つ(cluster_policy.tasks-for — 返事に載せる欄だけを TaskRecord から写す。lease の期限の
   ような拍ごとに変わる欄を持たないので、返事の等しさで worker の見え方を比べられる — watch_policy.worker-mark): id・name・revision・
   versions = 送り手の版・program = 詰めた Program の置き場のキー・detached / key / lease-ms / retain-ms / needs = 切り離した task が
   引き取りのために運ぶ欄(切り離していなければ使わない)・runtime-env = 実行環境の宣言(無ければ None)・environ = 子の環境変数。"
  (#^ str id)
  (#^ str name)
  (#^ str revision)
  (#^ (get tuple #(ComponentVersion ...)) versions)
  (#^ (| str None) program)
  (#^ bool detached)
  (#^ (| str None) key)
  (#^ int lease-ms)
  (#^ int retain-ms)
  (#^ (get tuple #(str ...)) needs)
  (#^ (| (get dict #(str object)) None) runtime-env)
  (#^ (get tuple #((get tuple #(str str)) ...)) environ))


(defrecord WarmOffer
  "heartbeat の返事で worker へ配る温める表の行 1 つ(cluster_policy.warms-for): key = 行のキー・runtime-env = 宣言の JSON。"
  (#^ str key)
  (#^ (get dict #(str object)) runtime-env))


(defrecord HeartbeatReply
  "heartbeat の返事(cluster_policy.heartbeat-reply・superseded-reply — #2595): jobs = 動かす job の spec・tasks = 走らせる task・warm =
   温める表の行・timing = 時間の設定・draining = drain 中か・superseded = 退いた世代への返事か・formats = 受け入れる本文の形の版・
   revision = 返事を作った時の coordinator の版。JSON の形は coordinator/protocol/replies が綴る(superseded は真の時だけ書く)。"
  (#^ (get tuple #(JobSpec ...)) jobs)
  (#^ (get tuple #(TaskOffer ...)) tasks)
  (#^ (get tuple #(WarmOffer ...)) warm)
  (#^ ClusterTiming timing)
  (#^ bool draining)
  (#^ bool superseded)
  (#^ (get tuple #(int ...)) formats)
  (#^ int revision))


(defclass [(dataclass :frozen True)] Watcher []
  "GET /watch の待ち 1 件(調停ループが返事まで持つ)。request = 返事を返す相手の要求・after = 送り手が知っている版・deadline-ms =
   変わらなくても返す刻(epoch ms)・worker / boot = 名指した worker とその process の世代(None = coordinator 全体の版だけを見る)・
   mark = 名指した worker の heartbeat の返事の見え方(版 after の時の物 — まだ見ていなければ None)・lease = 空きを待つ名前付きの
   lease の名(None = 版の変化を待つ。名があれば版を見ず、その lease に空きがある時に起きる — 今空いていればすぐ・#3865 の後の単位)。"
  (#^ Request request)
  (#^ int after)
  (#^ int deadline-ms)
  (setv #^ (| str None) worker None)
  (setv #^ (| str None) boot None)
  (setv #^ (| HeartbeatReply None) mark None)
  (setv #^ (| str None) lease None))


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

(defclass [(dataclass :frozen True)] CoordinatorFault [EffectBase]
  "coordinator の中の欠陥(Fault)を log に 1 行出す。結果は None。本番の受け口(coordinator_inbox.http-requests)は stderr へ、
   模擬の受け口(coordinator.protocol.request_queue.queued-requests)は列の faults へ書く。"
  (#^ Fault fault))


(defclass [(dataclass :frozen True)] SaveState [EffectBase]
  "調停の 1 まとまりの前の状態 before から後の状態 after への変化を耐久の場所へ写し、書き終えてから戻る(変化が無ければ何も書かない)。
   返事(Reply)はこの後にだけ出す: 返事を済ませた書きは coordinator が落ちても消えない。書けなければ例外(返事をせずに落ちる)。
   保存の綴り(キー → JSON の値の差分)は答え手の protocol(coordinator/protocol/store の durable-states — durable_kv)が作る。
   #2446 で、core の調停ループが KV の差分を組んで Persist に載せていた形から、型の値の前後を渡す形にした。"
  (#^ ClusterState before)
  (#^ ClusterState after))
