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
(require doeff-hy.record [defenum defrecord])
(import dataclasses [dataclass field asdict])
(import enum [StrEnum])
(import typing [NamedTuple])
(import doeff [EffectBase])
(import .worker_model [JobSpec])


;; --- 実行先の条件と版(task・切り離した task・worker が共に使う) -------------------------------------

(defclass Requirement [NamedTuple]
  "実行先の条件 1 つ: worker の label の名と、その label に要る値(例 = (Requirement \"kind\" \"k3s\"))。"
  (#^ str label)
  (#^ str value))


(defclass ComponentVersion [NamedTuple]
  "版 1 つ: 部品の名(python・cloudpickle・doeff)とその版の綴り。task を送れる worker を選ぶのに、送り手と worker の組を比べる。"
  (#^ str component)
  (#^ str version))


(defn #^ (get tuple #(Requirement ...)) requirements-of [#^ dict labels]
  "JSON の object(label の名 → 値)→ 名の順の Requirement の tuple(比べる時に順が揃う)。JSON から読む境界で使う。"
  (tuple (sorted (gfor #(label value) (.items labels) (Requirement label value)))))


(defn #^ (get tuple #(ComponentVersion ...)) component-versions-of [#^ dict versions]
  "JSON の object(部品の名 → 版)→ 名の順の ComponentVersion の tuple。JSON から読む境界で使う。"
  (tuple (sorted (gfor #(component version) (.items versions) (ComponentVersion component version)))))


(defclass [(dataclass :frozen True)] ClusterJob []
  (#^ JobSpec spec)
  (setv #^ tuple requires #())        ; worker の label に要る組(例 = #(#("kind" "k3s")))
  (setv #^ (| str None) pin None)     ; この worker にだけ置く
  (setv #^ (| dict None) run None)    ; 宣言の元の形(service の関数の参照・env・設定)。表示と保存のため
  ;; --- Service の資源としての欄(2026-09-24) ---
  (setv #^ int replicas 1)            ; 0 = 宣言は残すが置かない(Rollout が旧を止める・新を起こす口)。1 = 置いて動かし続ける
  (setv #^ (| dict None) readiness None) ; {"windowSeconds": n} = ReportReady の「準備できた」が直近 n 秒以内にある時だけ Ready
  (setv #^ (| str None) owner None)   ; 宣言の所有者(依頼の主体の id・作業係の名)。消せるのは所有者か明示の force の delete だけ
  ;; --- 版の追随と入れ替え(2026-09-24) ---
  ;; 入れ替えの形: "recreate"(旧を止めてから新 — 既定)か "handoff"(新が Ready と数えられてから旧を止める — worker_model.JobSpec)。
  (setv #^ str update "recreate")
  ;; 土台の commit(spec.base)をどこから追うか: {"kind" "Deployment" "namespace" "name" "container"?}。在れば coordinator が
  ;; その Deployment の pod template の image(配備の流れが apply で決めた版)の LABEL(ClusterNaming の revision-label)を読み、
  ;; spec.base をその commit へ進める(送り手 base-follow)。spec.base はこの係だけが書く欄(Rollout にとっての replicas と同じ)。
  (setv #^ (| dict None) base-from None)
  ;; 定義の版の明示の上書き(2026-09-25・40 桁の commit)。baseFrom を持つ Service の版の組(業務コード・定義(worker の重ねる dir)・
  ;; 実行環境)は Deployment の image の commit 1 つが正本で、spec.revision は spec.base と同じ commit を追う(重ねない木)。
  ;; overlay が在る時だけ spec.revision = overlay(「base の木 + overlay の commit の重ねる dir」— 以前の重ねる形)。
  (setv #^ (| str None) overlay None))


(defclass [(dataclass :frozen True)] WorkerInfo []
  (#^ str name)
  (#^ tuple labels)
  (#^ int capacity)
  (#^ int last-seen-ms)
  (setv #^ (get tuple #(ComponentVersion ...)) versions #())        ; worker の Python / cloudpickle / doeff の版(task を送れる相手を選ぶ)
  ;; worker の process の世代(起動のたびに新しく振る・heartbeat の boot)。drain は頼まれた時の世代に付き、別の世代の heartbeat
  ;; (Pod を作り直した後の worker)が来たら解ける(2026-09-25)。保存しない(読み直しの後は次の heartbeat で埋まる)。旧い worker は None。
  (setv #^ (| str None) boot None)
  ;; worker が名乗る道具(外部の CLI・OS の library — 名と版・2026-09-26)。実行環境の宣言の tools と照らして置き先を選ぶ。
  (setv #^ (get tuple #(ComponentVersion ...)) tools #()))


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
  "クラスタが外の系(k8s の Deployment・image の registry)と取り交わす名。どれも配備する側(composition root の引数)が決める。
   owner-annotation = Rollout が台数を持つ Deployment に付ける annotation の鍵。
   owner-scope      = その値の頭に付ける、このクラスタの名(「<scope>/Rollout/<名> replicas=<n>」)。
   revision-label   = 土台の版の追随(base_follow_policy)が読む image の LABEL(40 桁の commit)。
   version-labels   = 版と一緒に写しておく LABEL の組 #(#(鍵 LABEL) …)。Service の status.base に鍵の名で並ぶ(比べない・表示だけ)。"
  (setv #^ str owner-annotation "doeff-cluster/replicas-owned-by")
  (setv #^ str owner-scope "doeff-cluster")
  (setv #^ str revision-label "org.opencontainers.image.revision")
  (setv #^ tuple version-labels #())

  (defn __post-init__ [self]
    (when (in "revision" (gfor pair self.version-labels (get pair 0)))
      (raise (ValueError "version-labels の鍵に revision は使えない(版そのものの鍵)")))))


(defn #^ ClusterNaming naming-from-json [#^ str text]
  "coordinator の引数(JSON)→ ClusterNaming。欄は ownerAnnotation・ownerScope・revisionLabel・versionLabels({鍵: LABEL})。
   書かなかった欄は既定のまま。"
  (import json)
  (setv data (json.loads text))
  (when (not (isinstance data dict))
    (raise (ValueError "naming は JSON の object")))
  (setv known #{"ownerAnnotation" "ownerScope" "revisionLabel" "versionLabels"})
  (setv unknown (sorted (gfor k data :if (not-in k known) k)))
  (when unknown
    (raise (ValueError (+ "naming の知らない欄: " (.join ", " unknown)))))
  (setv base (ClusterNaming))
  (ClusterNaming :owner-annotation (.get data "ownerAnnotation" base.owner-annotation)
                 :owner-scope (.get data "ownerScope" base.owner-scope)
                 :revision-label (.get data "revisionLabel" base.revision-label)
                 :version-labels (tuple (gfor #(k v) (.items (.get data "versionLabels" {})) #(k v)))))


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
  "task 1 本。phase = queued | assigned | finished | code-failed | failed(切り離した task は + version-mismatch | lost | cancelled)。
   result = worker が返した結果の blob(TaskSucceeded / TaskFailed の cloudpickle)。finished で None なら結果なし。"
  (#^ str id)
  (#^ str name)
  (#^ str env)
  (#^ str blob)
  (#^ str revision)
  (#^ (get tuple #(ComponentVersion ...)) versions)   ; 送り手の版(名の順)
  (#^ (get tuple #(Requirement ...)) requires)        ; 実行先の条件(label の名の順)
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
  ;; lease は担い手の worker の heartbeat が延ばし、切れたら(worker の死)phase = lost。boot = 置いた時の worker の process の世代
  ;; (違う世代の heartbeat が来たら lost — 走らせ直さない)。retain-ms = 終わった後に結果を持っておく長さ。
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
  (setv #^ bool retryable False))


(defn #^ dict task-record-to-json [#^ TaskRecord task]
  "TaskRecord → 保存の JSON の形(版と条件は名 → 値の object)。保存の 2 つの形(state file と durable の KV)はここだけを使う。"
  (| (asdict task) {"versions" (dict task.versions) "requires" (dict task.requires)}))


(defn #^ TaskRecord task-record-from-json [#^ dict data]
  "保存の JSON の形 → TaskRecord(task-record-to-json の逆)。"
  (TaskRecord #** (| data {"versions" (component-versions-of (get data "versions"))
                           "requires" (requirements-of (get data "requires"))
                           "avoid" (tuple (.get data "avoid" []))})))


(defclass [(dataclass :frozen True)] ClusterState []
  (setv #^ tuple jobs #())
  (setv #^ dict workers (field :default-factory dict))
  (setv #^ dict placements (field :default-factory dict)) ; job の名 → Placement
  (setv #^ dict tasks (field :default-factory dict))
  (setv #^ int next-task 1)
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
  ;; image(「registry/名:tag」)→ LABEL から読んだ版 {"revision" <version-labels の鍵>… "at"} か {"error" "at"}(版の追随の cache)
  (setv #^ dict images (field :default-factory dict))
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
  ;; 入れ替え(handoff)の期限の見張り(2026-09-26): Service の名 → HandoffWatch。新の世代が動き出してから期限の間 Ready にならなければ
  ;; 諦めを記録し、heartbeat の返事の job に載せる(worker は新を止めて旧を残す — handoff_policy)。保存する(durable_kv)。
  (setv #^ dict handoffs (field :default-factory dict)))


;; --- HTTP の要求と返事 ----------------------------------------------------------

(defclass [(dataclass :frozen True :eq False)] Request []
  "受けた HTTP 要求 1 件。slot は返事を待つ handler の側の物(判断は見ない)。
   actor = 送り手(header X-Actor)。無ければ None(資源の書きは断る・盤と task は送り元の番地で記録する)。"
  (#^ str method)
  (#^ str path)
  (#^ dict query)
  (#^ object body)
  (setv #^ object slot None)
  (setv #^ (| str None) actor None)
  (setv #^ str peer ""))


(defclass [(dataclass :frozen True)] PlainText []
  "JSON でない返事の本文(GET /metrics の Prometheus の text)。HTTP の handler は content-type をそのまま付けて text を返す。"
  (#^ str text)
  (setv #^ str content-type "text/plain; version=0.0.4; charset=utf-8"))


;; --- effect ----------------------------------------------------------------------

(defclass [(dataclass :frozen True)] NextRequests [EffectBase]
  "結果は Request の list。最初の 1 件を timeout-seconds まで待ち(来なければ空 = 期限の経過で割り当てを動かす拍)、
   その時点で並んでいる要求を limit 件まで一緒に取る(group commit の 1 まとまり)。"
  (#^ float timeout-seconds)
  (setv #^ int limit 256))


(defclass [(dataclass :frozen True)] Reply [EffectBase]
  (#^ Request request)
  (#^ int status)
  (#^ object body))


(defclass [(dataclass :frozen True)] Persist [EffectBase]
  "1 まとまりの変化(キー → 新しい値・消えたキーは None — durable_kv.hy)を耐久の場所へ書き、fsync が終わってから戻る。
   返事(Reply)はこの後にだけ出す: 返事を済ませた書きは coordinator が落ちても消えない。書けなければ例外(返事をせずに落ちる)。"
  (#^ dict delta))


(defclass [(dataclass :frozen True)] CoordinatorStopRequested [EffectBase]
  "結果は bool。")
