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
(require doeff-hy.macros [deff val])
(require doeff-hy.record [defenum defrecord])
(import dataclasses [dataclass field asdict fields])
(import enum [StrEnum])
(import re)
(import typing [NamedTuple])
(import doeff [EffectBase])
(import .worker_model [JobSpec])


;; --- 実行先の能力と版(task・切り離した task・worker が共に使う) -------------------------------------
;;
;; 能力(capability — ADR-DOE-CLUSTER-001 R4b・2026-09-27): job と task は「要る能力の名」の集合(needs)を宣言し、worker は「提供する
;; 能力の名」の集合(provides)をクラスタの設定(起動の引数)で名乗る。coordinator は needs ⊆ provides の worker にだけ置く。
;; 置き場所の名(kind=k3s・role=…・機体の名)は書かない。worker の exclusive(provides の一部)は「この能力のどれかを needs に持つ
;; job / task だけを受ける」の印(以前の label `dedicated=<k>=<v>` の置き換え — 会社の機体・人の機体のように、一般の仕事を置かない担い手)。
;; 能力の名は小文字・数字・`.`・`-` だけ(`k=v` の旧い label の形を名として受けない)。needs と provides は名の順の tuple で持つ。

(val CAPABILITY-PATTERN (re.compile r"[a-z0-9][a-z0-9.-]*"))


(deff capability-refusal [name]  ; defk にできない: 宣言・heartbeat・保存の JSON を読む境界(Program の外)が呼ぶ純粋な判断
  {:pre [(: name str)] :post [(: % (| str None))] :tags {:context "doeff-cluster" :role "judgment"}}
  "能力の名 1 つが名として受けられない理由(受けられれば None)— 旧い label の形(`kind=k3s`)を黙って名にしないため。"
  (cond
    (in "=" name) (.format "能力の名 {!r} は label の形(鍵=値)— 置き場所ではなく要る能力の名を書く(ADR-DOE-CLUSTER-001 R4b)" name)
    (not (CAPABILITY-PATTERN.fullmatch name)) (.format "能力の名 {!r} は小文字・数字・`.`・`-` だけで書く" name)
    True None))


(deff capabilities-of [value #^ str what]  ; defk にできない: 宣言・heartbeat・保存の JSON を読む境界(Program の外)が呼ぶ
  {:pre [(: value (| list tuple set frozenset dict str int float bool None)) (: what str)] :post [(: % tuple)] :tags {:context "doeff-cluster" :role "judgment"}}
  "JSON の能力の名の列(list・tuple・frozenset)→ 名の順の重なりの無い tuple(比べる時に順が揃う)。旧い形(label の object)や
   名として受けられない値は BodyInvalid(送り手の誤り — ValueError の子)(what = 誤りの文の欄の名)。"
  (when (isinstance value dict)
    (raise (BodyInvalid (.format "{} が label の object {!r} — 旧い requires / labels の形は受け付けない。能力の名の列で書く(ADR-DOE-CLUSTER-001 R4b)"
                                what value))))
  (when (not (isinstance value #(list tuple set frozenset)))
    (raise (BodyInvalid (.format "{} は能力の名の列: {!r}" what value))))
  (for [name value]
    (when (not (isinstance name str))
      (raise (BodyInvalid (.format "{}: 能力の名は文字列: {!r}" what name))))
    (setv problem (capability-refusal name))
    (when (is-not problem None)
      (raise (BodyInvalid (.format "{}: {}" what problem)))))
  (tuple (sorted (set value))))


(deff effect-needs-problem [needs]  ; defk にできない: effect の構成子(dataclass の __post_init__)が呼ぶ純粋な判断
  {:pre [(: needs (| frozenset tuple list set dict str None))] :post [(: % (| str None))] :tags {:context "doeff-cluster" :role "judgment"}}
  "effect(RemoteJob・SubmitDetached・WarmRuntimeEnv)の needs が受けられない理由(受けられれば None)— 3 つの構成子が同じ規則で
   断るため: 能力の名の空でない frozenset(旧い Requirement の tuple・label の組・空は断る — 改訂 1 の I)。"
  (cond
    (not (isinstance needs frozenset)) (.format "needs は能力の名の frozenset: {!r}" needs)
    (not needs) "needs が空 — 要る能力の名を 1 つ以上書く"
    True (next (gfor n needs
                     :setv p (if (isinstance n str) (capability-refusal n) (.format "能力の名は文字列: {!r}" n))
                     :if p p)
               None)))


(deff environ-pairs [#^ dict environ]  ; defk にできない: coordinator の本文の読み・worker の返事の読み(Program の外)が呼ぶ純粋な判断
  {:pre [(: environ dict)] :post [(: % tuple)] :tags {:context "doeff-cluster" :role "judgment"}}
  "子の環境変数の dict → 名の順の #(名 値) の tuple(TaskRecord.environ・JobSpec.environ の形)— 行と spec の比べと指紋を
   名の順 1 つにするため。"
  (tuple (gfor k (sorted environ) #(k (get environ k)))))


(defclass ComponentVersion [NamedTuple]
  "版 1 つ: 部品の名(python・cloudpickle・doeff)とその版の綴り。task を送れる worker を選ぶのに、送り手と worker の組を比べる。"
  (#^ str component)
  (#^ str version))


(defn #^ (get tuple #(ComponentVersion ...)) component-versions-of [#^ dict versions]
  "JSON の object(部品の名 → 版)→ 名の順の ComponentVersion の tuple。JSON から読む境界で使う。"
  (tuple (sorted (gfor #(component version) (.items versions) (ComponentVersion component version)))))


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


(defn #^ HandoffWatch handoff-watch-from-json [#^ dict data]  ; defk にできない: 保存の読み(coordinator の起動の純粋な関数)が呼ぶ
  "保存の形 → HandoffWatch(HandoffWatch.to-json の逆)。知らない段は読めない(ValueError — 黙って待ちに戻さない)。"
  (HandoffWatch :declaration (get data "declaration") :since-ms (get data "sinceMs")
                :phase (HandoffPhase (get data "phase"))
                :abandoned-ms (.get data "abandonedMs")
                :reason (.get data "reason" "")
                :last-report (.get data "lastReport")))


(defclass [(dataclass :frozen True)] ClusterTiming []
  (setv #^ int lease-ms 10000)          ; これより新しい heartbeat の worker にだけ新しく割り当てる
  ;; fence は tailnet の実測の途絶(最長 約 13 秒・2026-09-23 newmac)より長く、移し替えは fence より十分長く取る
  ;; (止めた worker と新しい担い手が同時に動かない)。代償は障害時の移し替えが 45 秒になること。
  ;; worker は heartbeat の返事の timing から fence を受け取る(この値が唯一の定義点)。
  ;; worker が連絡の途絶から lease を持たない job と task を止めるまで。書き手(入れ替えを宣言した job)は止めない — 書きは lease の
  ;; 柵だけが守る(worker_policy.kept-when-cut-off・2026-09-25)。
  (setv #^ int fence-ms 20000)
  (setv #^ int reassign-after-ms 45000) ; 連絡の途絶えた worker の job を他へ移すまで

  (defn __post-init__ [self]
    (when (<= self.reassign-after-ms self.fence-ms)
      (raise (ValueError "移し替えは worker の自己停止より後でなければならない")))))


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


;; image の版を追う係(base-follow)が読んでいた naming の欄(image の LABEL の名)。係は消した(Program の job は宣言した commit でだけ
;; 解く — 計画 2.2 の E)ので、書かれていれば黙って捨てず、理由つきで断る(coordinator は起動しない)。
(val RETIRED-NAMING-FIELDS (frozenset #("revisionLabel" "versionLabels")))


(defn #^ ClusterNaming naming-from-json [#^ str text]
  "coordinator の引数(JSON)→ ClusterNaming。欄は ownerAnnotation・ownerScope・nodeCapabilities([{\"label\" \"value\" \"capability\"} …])。
   書かなかった欄は既定のまま。消した欄(RETIRED-NAMING-FIELDS)と知らない欄は断る。"
  (import json)
  (setv data (json.loads text))
  (when (not (isinstance data dict))
    (raise (ValueError "naming は JSON の object")))
  (when (& (set data) RETIRED-NAMING-FIELDS)
    (raise (ValueError (.format "naming の {} は受け付けない — image の版を追う係は消した(Program の job は宣言した commit でだけ解く)"
                                (sorted (& (set data) RETIRED-NAMING-FIELDS))))))
  (setv known #{"ownerAnnotation" "ownerScope" "nodeCapabilities"})
  (setv unknown (sorted (gfor k data :if (not-in k known) k)))
  (when unknown
    (raise (ValueError (+ "naming の知らない欄: " (.join ", " unknown)))))
  (setv base (ClusterNaming))
  (ClusterNaming :owner-annotation (.get data "ownerAnnotation" base.owner-annotation)
                 :owner-scope (.get data "ownerScope" base.owner-scope)
                 :node-capabilities (if (in "nodeCapabilities" data)
                                        (tuple (gfor row (get data "nodeCapabilities")
                                                     #((get row "label") (get row "value") (get row "capability"))))
                                        base.node-capabilities)))


;; HTTP の本文(/tasks・/detached・/heartbeat)の形の版(2026-09-26)。送り手・coordinator・worker は別々の版になり得るので、本文に
;; format を置き、coordinator は受け入れる範囲を heartbeat の返事と /livez で名乗り、範囲の外の送り手を 400 で断る。format の無い
;; 本文(この版より前の送り手)は 1 として受ける。
(setv PROTOCOL-FORMAT 1)
(setv ACCEPTED-FORMATS #(1))


(defn #^ (| str None) format-refusal [#^ dict body]  ; defk にできない: coordinator の純粋な判断(Program の外)が呼ぶ
  "本文の format が受け入れる範囲の外なら理由の文。"
  (setv form (.get body "format" 1))
  (if (in form ACCEPTED-FORMATS)
      None
      (.format "本文の形の版 {!r} を受け入れない(受け入れる版 = {})" form (list ACCEPTED-FORMATS))))


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


(defn #^ dict task-record-to-json [#^ TaskRecord task]
  "TaskRecord → 保存の JSON の形(版は名 → 値の object・needs は名の list)。保存の 2 つの形(state file と durable の KV)はここだけを使う。"
  (| (asdict task) {"versions" (dict task.versions) "needs" (list task.needs) "environ" (dict task.environ)}))


;; 終わった task の phase(旧い形の保存の行を読む時に、まだ終わっていない行だけを断る — task-record-from-json)。
(setv ENDED-PHASES (frozenset #("finished" "code-failed" "failed" "version-mismatch" "lost" "cancelled" "env-failed")))


(defn #^ TaskRecord task-record-from-json [#^ dict data]
  "保存の JSON の形 → TaskRecord(task-record-to-json の逆)。
   旧い形の行は読み直しで coordinator を落とさず、まだ終わっていない行を failed(理由つき)にする — 旧い形は受け付けない
   (operator 2026-09-27)。旧い形 = TaskRecord に無い欄を持つ行(今の TaskRecord の欄の集合 1 つで判じる — 消した欄を 1 つずつ数えると、
   数え漏れた欄 1 つで読み直しが TypeError になり coordinator が起きない。実弾 2026-09-28 の予行: 3880944e の行の env)。
   無い欄は捨てて読む(終わった行は Program 無し = program None で読む)。空の requires は needs 無しと同じ。"
  (setv known (sfor f (fields TaskRecord) f.name)
        extra (sorted (gfor k data :if (not-in k known) k))
        phase (stored-str data "phase" "queued")
        unended (not-in phase ENDED-PHASES)
        reason (old-task-row-reason (.get data "requires") extra)
        ;; failure = まだ終わっていない旧い形の行を failed にする理由(None = そのまま読む)
        failure (if unended reason None))
  ;; 欄ごとに型を確かめて読む(#** で辞書を渡すと、型の違う保存の値が黙って欄に入る — agora-redesign #1662)。
  (TaskRecord :id (stored-str data "id")
              :name (stored-str data "name")
              :program (stored-optional-str data "program")
              :revision (stored-str data "revision")
              :versions (component-versions-of (get data "versions"))
              :needs (capabilities-of (.get data "needs" []) "task の needs")
              :lease-ms (stored-int data "lease_ms")
              :lease-until-ms (stored-int data "lease_until_ms")
              :submitted-ms (stored-int data "submitted_ms")
              :phase (if (is failure None) phase "failed")
              :worker (stored-optional-str data "worker")
              :result (stored-optional-str data "result")
              :detail (if (is failure None) (stored-str data "detail" "") failure)
              :started-ms (stored-optional-int data "started_ms")
              :finished-ms (stored-optional-int data "finished_ms")
              :detached (stored-bool data "detached" False)
              :key (stored-optional-str data "key")
              :boot (stored-optional-str data "boot")
              :retain-ms (stored-int data "retain_ms" 0)
              :runtime-env (stored-optional-dict data "runtime_env")
              :env-attempts (stored-int data "env_attempts" 0)
              :avoid (stored-items data "avoid")
              :failure-kind (stored-str data "failure_kind" "")
              :retryable (stored-bool data "retryable" False)
              ;; 子の環境変数の欄の無い旧い行は空(欄が無いだけで旧い形とは数えない — 足した欄)。
              :environ (environ-pairs (.get data "environ" {}))))


;; 保存の行の欄の読み(task-record-from-json)。型の違う値は、どの欄がどう違うかを名乗る ValueError にする(保存の行の壊れ — 送り手の誤りの
;; BodyInvalid とは別)。無い欄は既定値で読む(既定値が None の欄は必須)。

(deff stored-str [#^ dict data #^ str key #^ (| str None) [default None]]  ; defk にできない: 保存の読み直し(Program の外)が呼ぶ純粋な読み
  {:pre [(: data dict) (: key str) (: default (| str None))] :post [(: % str)] :tags {:context "doeff-cluster" :role "judgment"}}
  "保存の行の文字列の欄を str として読むため(無ければ default・default が None なら必須)。"
  (when (and (not-in key data) (is default None))
    (raise (ValueError (.format "保存の task の行に {} が無い" key))))
  (setv value (.get data key default))
  (when (not (isinstance value str))
    (raise (ValueError (.format "保存の task の行の {} は文字列: {!r}" key value))))
  value)

(deff stored-optional-str [#^ dict data #^ str key]  ; defk にできない: 保存の読み直し(Program の外)が呼ぶ純粋な読み
  {:pre [(: data dict) (: key str)] :post [(: % (| str None))] :tags {:context "doeff-cluster" :role "judgment"}}
  "保存の行の、無くてよい文字列の欄を str か None として読むため。"
  (setv value (.get data key None))
  (when (not (isinstance value #(str (type None))))
    (raise (ValueError (.format "保存の task の行の {} は文字列か null: {!r}" key value))))
  value)

(deff stored-int [#^ dict data #^ str key #^ (| int None) [default None]]  ; defk にできない: 保存の読み直し(Program の外)が呼ぶ純粋な読み
  {:pre [(: data dict) (: key str) (: default (| int None))] :post [(: % int)] :tags {:context "doeff-cluster" :role "judgment"}}
  "保存の行の整数の欄を int として読むため(無ければ default・default が None なら必須。真偽値は整数と数えない)。"
  (when (and (not-in key data) (is default None))
    (raise (ValueError (.format "保存の task の行に {} が無い" key))))
  (setv value (.get data key default))
  (when (or (not (isinstance value int)) (isinstance value bool))
    (raise (ValueError (.format "保存の task の行の {} は整数: {!r}" key value))))
  value)

(deff stored-optional-int [#^ dict data #^ str key]  ; defk にできない: 保存の読み直し(Program の外)が呼ぶ純粋な読み
  {:pre [(: data dict) (: key str)] :post [(: % (| int None))] :tags {:context "doeff-cluster" :role "judgment"}}
  "保存の行の、無くてよい整数の欄を int か None として読むため。"
  (setv value (.get data key None))
  (when (or (isinstance value bool) (not (isinstance value #(int (type None)))))
    (raise (ValueError (.format "保存の task の行の {} は整数か null: {!r}" key value))))
  value)

(deff stored-bool [#^ dict data #^ str key #^ bool default]  ; defk にできない: 保存の読み直し(Program の外)が呼ぶ純粋な読み
  {:pre [(: data dict) (: key str) (: default bool)] :post [(: % bool)] :tags {:context "doeff-cluster" :role "judgment"}}
  "保存の行の真偽値の欄を bool として読むため。"
  (setv value (.get data key default))
  (when (not (isinstance value bool))
    (raise (ValueError (.format "保存の task の行の {} は真偽値: {!r}" key value))))
  value)

(deff stored-optional-dict [#^ dict data #^ str key]  ; defk にできない: 保存の読み直し(Program の外)が呼ぶ純粋な読み
  {:pre [(: data dict) (: key str)] :post [(: % (| dict None))] :tags {:context "doeff-cluster" :role "judgment"}}
  "保存の行の、無くてよい object の欄を dict か None として読むため。"
  (setv value (.get data key None))
  (when (not (isinstance value #(dict (type None))))
    (raise (ValueError (.format "保存の task の行の {} は object か null: {!r}" key value))))
  value)

(deff stored-items [#^ dict data #^ str key]  ; defk にできない: 保存の読み直し(Program の外)が呼ぶ純粋な読み
  {:pre [(: data dict) (: key str)] :post [(: % tuple)] :tags {:context "doeff-cluster" :role "judgment"}}
  "保存の行の配列の欄を tuple として読むため(無ければ空)。JSON を通った行は list、JSON を通らずに渡る行(asdict のまま)は tuple で来る。"
  (setv value (.get data key #()))
  (when (not (isinstance value #(list tuple)))
    (raise (ValueError (.format "保存の task の行の {} は配列: {!r}" key value))))
  (tuple value))


(deff old-task-row-reason [#^ (| dict list None) old #^ list extra]  ; defk にできない: 保存の読み直し(Program の外)が呼ぶ純粋な判断
  {:pre [(: old (| dict list None)) (: extra list)] :post [(: % (| str None))] :tags {:context "doeff-cluster" :role "judgment"}}
  "保存の task の行が旧い形なら、まだ終わっていない行を failed にする理由の文(新しい形なら None)。old = 行の requires の値・
   extra = 今の TaskRecord に無い欄の名(requires・blob・env ほか)。"
  (cond
    old (.format "旧い形の task(requires {})は受け付けない — 新しい形(needs)で送り直す" old)
    (in "blob" extra) "旧い形の task(詰めた Program を行に持つ blob)は受け付けない — Program を /programs に置き、その sha で送り直す"
    (in "env" extra) "旧い形の task(handler の組の import path env)は受け付けない — task の Program が自分の土台で本体を包み、needs で送り直す"
    extra (.format "旧い形の task(今の形に無い欄 {})は受け付けない — 新しい形で送り直す" extra)
    True None))


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
  (setv #^ dict board (field :default-factory dict))
  (setv #^ dict statuses (field :default-factory dict))  ; worker 名 → {"at" ms "jobs" [...]}(保存しない)
  (setv #^ tuple events #())                            ; 割り当ての移り変わり(直近 200 件・保存しない)
  ;; --- 資源(2026-09-24) ---
  (setv #^ dict meta (field :default-factory dict))      ; "Kind/名" → 資源の版の欄(resourceVersion・generation・作った / 書いた送り手と時刻)
  (setv #^ int revision 0)                              ; coordinator 全体の版の番号(書きのたびに 1 進む)
  (setv #^ tuple audit #())                             ; 出来事の記録(kind ごとに件数の上限つき・保存する)
  (setv #^ int audit-seq 0)
  (setv #^ dict rollouts (field :default-factory dict))  ; Rollout の名 → {"spec" … "status" …}
  (setv #^ dict board-versions (field :default-factory dict)) ; 盤の行 → その行の版(行ごとに 1 から増える・行の file と一緒に保存)
  ;; 盤の行 → 期限(epoch ミリ秒)。PUT の ttlSeconds で付き、期限を過ぎた行は調停が消す(2026-09-25・行と一緒に保存)。
  (setv #^ dict board-expiry (field :default-factory dict))
  ;; 盤の行 → 値の JSON の byte 数(保存しない — 読み直しの時に測り直す)。盤の容量の上限の判断と計器に使う。
  (setv #^ dict board-sizes (field :default-factory dict))
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
  (setv #^ dict handoffs (field :default-factory dict)))


;; --- HTTP の要求と返事 ----------------------------------------------------------

(defclass [(dataclass :frozen True :eq False)] Request []
  "受けた HTTP 要求 1 件。slot は返事を待つ handler の側の物(判断は見ない)。
   actor = 送り手(header X-Actor)。無ければ None(資源の書きは断る・盤と task は送り元の番地で記録する)。
   path = 受けたままの path(log と返事の文に使う)・parts = path を / で割り、区切りごとに percent の符号を戻した物。
   符号を戻すのは HTTP の境(coordinator_inbox.http-request)の仕事で、判断(api_policy.respond)は parts だけを読む(#1636)。"
  (#^ str method)
  (#^ str path)
  (#^ dict query)
  (#^ object body)
  (#^ tuple parts)
  (setv #^ object slot None)
  (setv #^ (| str None) actor None)
  (setv #^ str peer ""))


(defclass BodyInvalid [ValueError]
  "送り手の要求の本文の誤り(欠けた欄・受けられない値・旧い形)。受け口(api_policy.respond)はこれと resource_policy.Refused だけを
   400 にし、それ以外の例外は coordinator の中の欠陥(Fault・500 と log の 1 行)にする(#1024 — #1005 では中の
   TypeError が 400 に畳まれ、log にも出ずに原因の特定が遅れた)。ValueError の子なので、同じ検めを保存の行や起動の引数で呼ぶ所の
   except ValueError はそのまま受ける。")


(deff required-field [#^ dict body #^ str key]  ; defk にできない: 受け口の本文の読み(Program の外の純粋な判断)が呼ぶ
  {:pre [(: body dict) (: key str)] :post [(: % (| dict list str int float bool None))] :tags {:context "doeff-cluster" :role "judgment"}}
  "送り手の本文の必須の欄の値 — 欄が無ければ BodyInvalid(送り手の誤り・400)。(get body 欄) の KeyError に頼ると、受け口は
   送り手の欠けと coordinator の中の KeyError を分けられない(#1024)。値は null でもよい(在ることだけを検める)。"
  (when (not-in key body)
    (raise (BodyInvalid (.format "本文に {} が無い" key))))
  (get body key))


(deff int-field [#^ dict fields #^ str key default]  ; defk にできない: 受け口の本文・query の読み(Program の外の純粋な判断)が呼ぶ
  {:pre [(: fields dict) (: key str) (: default (| int None))] :post [(: % int)] :tags {:context "doeff-cluster" :role "judgment"}}
  "送り手の本文・query の整数の欄(無ければ default)を int に読む — 読めない値(数でない文字列・object など)は BodyInvalid
   (送り手の誤り・400)。読み方は int() のまま(小数は切り捨て・数字の文字列は数)。"
  (setv value (.get fields key default))
  (try
    (int value)
    (except [error [ValueError TypeError]]
      (raise (BodyInvalid (.format "{} は整数: {!r}" key value))))))


(defclass [(dataclass :frozen True)] Fault []
  "coordinator の中の欠陥(要求の処理の中で上がった、送り手の誤りでない例外)の閉じた答えの形。受け口は 500 と本文
   {\"error\" …} で返し、coordinator は CoordinatorFault で log に 1 行出す。where = 例外が上がった所(file:行 関数)。"
  (#^ str method)
  (#^ str path)
  (#^ str error-type)
  (#^ str message)
  (#^ str where))


(defclass [(dataclass :frozen True)] PlainText []
  "JSON でない返事の本文(GET /metrics の Prometheus の text)。HTTP の handler は content-type をそのまま付けて text を返す。"
  (#^ str text)
  (setv #^ str content-type "text/plain; version=0.0.4; charset=utf-8"))


;; --- effect ----------------------------------------------------------------------

(defclass [(dataclass :frozen True)] IdleProbe []
  "要求の無い拍を飛ばしてよい長さを、模擬の時計の下の受け口が本番と同じ判断の関数で試すための材料(idle_policy.quiet-ticks —
   2026-09-30)。state = この拍の前の調停の状態・timing / naming = 調停ループの設定。本番の受け口は読まない。"
  (#^ ClusterState state)
  (#^ ClusterTiming timing)
  (#^ ClusterNaming naming))


(defclass [(dataclass :frozen True)] NextRequests [EffectBase]
  "結果は Request の list。最初の 1 件を timeout-seconds まで待ち(来なければ空 = 期限の経過で割り当てを動かす拍)、
   その時点で並んでいる要求を limit 件まで一緒に取る(group commit の 1 まとまり)。idle = 模擬の時計の下の受け口だけが読む材料
   (要求が無ければ、本番の判断で何も変わらない拍の数だけ一度に眠る — 本番の受け口は読まず、拍の間隔は timeout-seconds のまま)。"
  (#^ float timeout-seconds)
  (setv #^ int limit 256)
  (setv #^ (| IdleProbe None) idle None))


(defclass [(dataclass :frozen True)] Reply [EffectBase]
  (#^ Request request)
  (#^ int status)
  (#^ object body))


(defclass [(dataclass :frozen True)] CoordinatorFault [EffectBase]
  "coordinator の中の欠陥(Fault)を log に 1 行出す。結果は None。本番の受け口(coordinator_inbox.http-requests)は stderr へ、
   模擬の受け口(coordinator_handler_sets.queued-requests)は列の faults へ書く。"
  (#^ Fault fault))


(defclass [(dataclass :frozen True)] Persist [EffectBase]
  "1 まとまりの変化(キー → 新しい値・消えたキーは None — durable_kv.hy)を耐久の場所へ書き、fsync が終わってから戻る。
   返事(Reply)はこの後にだけ出す: 返事を済ませた書きは coordinator が落ちても消えない。書けなければ例外(返事をせずに落ちる)。"
  (#^ dict delta))


(defclass [(dataclass :frozen True)] CoordinatorStopRequested [EffectBase]
  "結果は bool。")
