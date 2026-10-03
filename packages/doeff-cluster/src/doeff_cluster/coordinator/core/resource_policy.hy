;;; coordinator の資源(Service・Worker・Task・Rollout)の純粋な判断。I/O はしない。
;;;
;;; - 版と出来事の記録: stamp が「前の状態と後の状態」の差から、変わった資源ごとに resourceVersion を進め(spec が変われば
;;;   generation も)、送り手・時刻・前後の版を出来事の記録へ足す。変化を起こした経路(API・heartbeat・調停・Rollout)を問わず
;;;   ここ 1 か所で付くので、版の付け忘れ・記録の漏れが起きない。
;;; - 書きの口: Service と Rollout は資源 1 つずつの compare-and-set(PUT は resourceVersion 必須・古ければ 409)。
;;;   一覧の丸ごとの上書きはしない。宣言を消せるのは所有者か、明示の force つきの delete だけ。
;;; - readiness: Service が Ready か(service-readiness)。process の生存(worker の報告の running)と、宣言が readiness を
;;;   持てば ReportReady の直近の報告の両方で決める。
;;; - 版の判定: Service の指定の版が実際に仕事をしているか(version-state — 5 値・status.version)。running-process・入れ替えの見張り・
;;;   停止の述語(service-stopped — Rollout の相手の観測 api_policy.target-view と共有)を呼んで組み立てる。
(require doeff-hy.macros [defk val <-])
(val MODULE-TAGS {:context "coordinator" :role "judgment"})
(import dataclasses [replace])
(import json)
(import typing [NoReturn])
(import doeff_hy.table [Table TableWrite])
(import doeff_cluster.shared.intent.protocol [ClusterTiming BodyInvalid])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterJob ClusterState ErrorReply RowConflict Placement HandoffPhase UnplacedKind NotReadyKind VersionState VersionVerdict LiveProcess ResourceMeta AuditEvent EventsView ServiceBody ServiceObserved WorkerObserved TaskObserved RolloutObserved ObservedDeployment ResourceView ResourceList LegacyJobRow RolloutRow RolloutStatus RolloutTarget RefusedJob WorkerInfo TaskRecord
                                                       ReportOrigin ReadinessReport MetricsReport WorkerReport])
(import doeff_cluster.coordinator.core.cluster_rules [int-field])
(import doeff_cluster.shared.core.job_rules [spec-hash] doeff_cluster.shared.intent.job_model [JobPhase])
(import doeff_cluster.coordinator.core.cluster_policy [job-to-json alive liveness-deadline still-live-somewhere service-rows unplaced-kind unplaced-text resource-version-of keep-mark-of])
(import doeff_cluster.coordinator.core.rollout_policy [validate-rollout-spec rollout-spec-to-json rollout-status-to-json rollout-targets target-key TERMINAL-PHASES])
(import doeff [run])
(import doeff_cluster.coordinator.intent.request_bodies [ReadinessBody MetricsBody ResourceBody StatusRow])
(import doeff_cluster.shared.core.readiness_rules [handoff-timeout-ms readiness-refusal])
(import doeff_cluster.shared.core.readiness_report [reported-readiness])
(import doeff_cluster.shared.intent.readiness_model [ReadinessClaim])

(setv LEGACY-OWNER "legacy:jobs")        ; 旧い PUT /jobs の頃からの宣言の所有者(誰でも 1 度だけ引き取れる)
(setv COORDINATOR "coordinator")          ; 調停(割り当て・task の置き先)の送り手
(setv MIGRATION "migration")              ; 旧い形の状態の file に版を振った送り手
(setv AUDIT-PER-KIND 300)                 ; 出来事の記録の上限(kind ごと)
(setv KINDS #("Service" "Worker" "Task" "Rollout"))


(defclass Refused [Exception]
  "要求を断る(HTTP の status と本文 ErrorReply を持つ — JSON は coordinator/protocol/replies が綴る・#2614)。"
  (defn #^ None __init__ [self #^ int status #^ ErrorReply body]
    (.__init__ (super) body.message)
    (setv self.status status self.body body)))


(defn #^ NoReturn refuse [#^ int status #^ str message #^ (| int None) [current None]]
  "status と理由の文で要求を断る。current = いまの版(版の食い違いの時だけ — 答えの本文に載る)。"
  (raise (Refused status (ErrorReply :message message :current current))))


(defn #^ str key-of [#^ str kind #^ str name] (+ kind "/" name))


(defn #^ tuple split-key [#^ str key]
  (setv #(kind name) (.split key "/" 1))
  #(kind name))


;; actor は送られてきた値そのもの(header・本文の欄 — 文字列とは限らない)を受けて確かめる。
(defn #^ (| str None) valid-actor [#^ object actor]
  (if (and (isinstance actor str) (< 0 (len (.strip actor)) 200)) (.strip actor) None))


(defn #^ str require-actor [#^ object actor]
  (or (valid-actor actor)
      (refuse 400 "送り手が無い。書きには header X-Actor(依頼の主体の id・作業係の名・worker の名)が要る")))


;; --- readiness -------------------------------------------------------------------------------

(defn #^ (| StatusRow None) job-status-row [#^ ClusterState state #^ str worker #^ str name]
  (setv st (.get state.statuses worker))
  (when (is st None) (return None))
  (for [row st.jobs]
    (when (= row.name name) (return row)))
  None)


;; process の世代(2026-09-24 の実弾で改めた): readiness と計器の報告は、送った process の世代(worker が起こした時に振った名
;; instance・試行の番号 attempt・起こした spec の指紋 specHash・割り当ての世代 placement)を持つ。数えるのは「今の宣言の spec で、
;; 担い手の worker が running と報告している process」が出した報告だけ。以前は同じ担い手・同じ版の直近の報告なら数えたので、
;; 設定だけを変えた時(版は同じ)に、止めた前の process の Ready が window に残り、新しい process が 1 拍も終えないうちに Rollout が
;; 本番を止めた(Rollout 2026-09-25 05:11:12)。spec(設定を含む)が変われば指紋が変わり、以前の報告は数えない。
(setv REPORTS-KEPT 4)            ; Service ごとに残す報告(process の世代ごとに最新 1 つ・直近の 4 世代)


;; 判定の期限(#3064): readiness の判定が now と比べる期限の値の定義点。判定(running-process・service-readiness)はこの値と now を
;; 比べ、静かな区間の次の期限(readiness-due)も同じ値を読む(#1383 の決めの条件 (1) — 模擬だけの見積もりを持たない)。

(defn #^ int carrier-stale-from [#^ WorkerReport st #^ ClusterTiming timing]  ; defk にできない: coordinator の純粋な判断(running-process・handoff_policy.carrier-rows)が呼ぶ
  "担い手の worker の報告 st を古いと数える最初の刻(報告の刻 + lease-ms + 1 ms)。"
  (+ st.at timing.lease-ms 1))


(defn #^ int warm-until [#^ ClusterState state #^ ClusterTiming timing]  ; defk にできない: coordinator の純粋な判断(running-process)が呼ぶ
  "coordinator の起動の直後(担い手の報告が揃っていない間)を終える刻(起動の刻 + lease-ms)。"
  (+ state.started-ms timing.lease-ms))


(defn #^ int unreported-until [#^ ClusterState state #^ int window-ms]  ; defk にできない: coordinator の純粋な判断(service-readiness)が呼ぶ
  "今の process の報告がまだ無い時に Unknown と言う間を終える刻(起動の刻 + window)。"
  (+ state.started-ms window-ms))


(defn #^ int report-expired-from [#^ ReadinessReport report #^ int window-ms]  ; defk にできない: coordinator の純粋な判断(service-readiness)が呼ぶ
  "準備の報告 report を window の外と数える最初の刻(受けた刻 + window + 1 ms)。"
  (+ report.origin.at window-ms 1))


(defn #^ dict running-process [#^ ClusterState state #^ str name #^ int now #^ ClusterTiming timing
                               #^ (| Placement None) [placement None]]
  "Service を今動かしている process。ok = 担い手の worker が今の宣言の spec(版と設定の指紋・割り当ての世代)で running と
   報告している。ok でなければ state(NotReady | Unknown)と reason と、理由の種類 kind(NotReadyKind — 版の判定 version-state が
   文を読まずに分けるため)を持つ。kind が NOT-RUNNING なら担い手の行 row(行に載っていなければ None)も持つ。
   ok・state・reason の意味は readiness・入れ替えの合図(api_policy.ready-instance)・drain・計器が読むので変えない。
   placement = 見る置き先(既定 = いまの置き先。drain で並べた置き先(surge)の process を見る時はそれを渡す — drain_policy)。"
  (setv job (next (gfor j state.jobs :if (= j.spec.name name) j) None))
  ;; extra = 答えに添える欄(担い手の行 :row)。
  (defn #^ dict no [#^ str s #^ NotReadyKind kind #^ str reason #^ (| dict None) #** extra] (| {"ok" False "state" s "kind" kind "reason" reason "job" job} extra))
  (when (is job None) (return (no "NotReady" NotReadyKind.NO-DECLARATION "宣言が無い")))
  (when (= job.replicas 0) (return (no "NotReady" NotReadyKind.NO-REPLICAS "replicas 0(置かない)")))
  (setv a (if (is placement None) (.get state.placements name) placement))
  (when (is a None)
    (setv unplaced (unplaced-kind now state job timing))
    (return (no "NotReady" (unplaced-not-ready unplaced) (+ "置き先が無い: " (unplaced-text unplaced job)))))
  (setv warming (< now (warm-until state timing))
        st (.get state.statuses a.worker))
  ;; 担い手の報告が古い: 移し替えの期限(reassign-after-ms)の内なら「分からない」(Unknown — 途絶の間。Rollout は失敗と数えない)。
  ;; 期限を過ぎた担い手からは job を他へ移すので NotReady(2026-09-25: 以前は heartbeat が 10 秒途絶えただけで NotReady と言い、
  ;; 書き手が書き先へ書けているのに Rollout が戻しに入りえた)。
  ;; 途絶しても動かし続けてよい印をこの担い手に渡してある job(#2804 — ClusterState.keep-marks)は、期限を過ぎても Unknown: 担い手は
  ;; fence でも止めず、coordinator も他へ移さないので、process は動き続けている見込み(監視が止まりと読まない)。印の無い job は担い手が
  ;; fence で止めているので、期限の後は NotReady のまま(他に置ける worker が無ければ置き先は保ち、担い手が戻ると起こし直す)。
  ;; 印の在る job も、担い手は途絶が長い方の柵(ClusterTiming.keep-fence-ms)を越えたら止めるので、その後は NotReady。
  (when (or (is st None) (>= now (carrier-stale-from st timing)))
    (setv carrier (.get state.workers a.worker)
          silent (and carrier (alive now carrier timing.reassign-after-ms))
          mark (run (keep-mark-of state.keep-marks name))
          kept (and (is-not mark None) (= mark.worker a.worker) (is-not carrier None)
                    (alive now carrier timing.keep-fence-ms)))
    (return (no (if (or warming silent kept) "Unknown" "NotReady") NotReadyKind.CARRIER-SILENT
                (if kept
                    (.format "担い手 {} の報告が無い・古い — 途絶しても動かし続けてよい印を渡してあるので、process は動き続けている見込み(担い手が戻るか Worker が消されるまで他へ移さない)"
                             a.worker)
                    (.format "担い手 {} の報告が無い・古い" a.worker)))))
  (setv row (job-status-row state a.worker name))
  (when (or (is row None) (!= row.phase "running"))
    (return (no "NotReady" NotReadyKind.NOT-RUNNING
                (.format "担い手 {} の上で {}" a.worker (if row row.phase "まだ起動していない"))
                :row row)))
  ;; 担い手の行の detail(入れ替えの途中・新の入口を読み込めない理由 — worker_policy.statuses)を添える: Service の status.readyReason
  ;; から「なぜ新が起きないか」が読める(2026-09-25)。
  (setv note (if row.detail (+ ":" row.detail) ""))
  (when (!= row.running-revision job.spec.revision)
    (return (no "NotReady" NotReadyKind.REVISION-MISMATCH
                (.format "版が違う(動いている版 {}・宣言 {}){}" row.running-revision job.spec.revision note))))
  (setv want (spec-hash job.spec))
  (when (not row.instance)
    (return (no "NotReady" NotReadyKind.NO-INSTANCE
                (.format "担い手 {} が process の世代を報告しない(世代を知らない古い worker)" a.worker))))
  (when (!= row.spec-hash want)
    (return (no "NotReady" NotReadyKind.SPEC-MISMATCH
                (.format "動いている process は前の宣言(設定か版)で起こした物(指紋 {}・宣言 {})— 起こし直しを待っている{}"
                         row.spec-hash want note))))
  (when (and (is-not row.placement None) (!= row.placement a.generation))
    (return (no "NotReady" NotReadyKind.PLACEMENT-MISMATCH
                (.format "動いている process は前の割り当ての世代 {} で起こした物(いま {})" row.placement a.generation))))
  {"ok" True "job" job "worker" a.worker "row" row "instance" row.instance "attempt" (str row.attempts)
   "specHash" want "placement" row.placement})


(defn #^ bool report-matches [#^ ReportOrigin origin #^ dict proc]
  "報告(の送り手の世代 origin)が、今動いている process(running-process の ok の答え)の物か。worker・世代の名・試行の番号・spec の指紋・
   割り当ての世代が全部一致する。"
  (and (get proc "ok")
       (= origin.worker (get proc "worker"))
       (= origin.instance (get proc "instance"))
       ;; 試行の番号は送られた型のまま残す(文字列か整数)— 比べる時だけ running-process の綴り(文字列)に揃える。
       (= (str origin.attempt) (get proc "attempt"))
       (= origin.spec-hash (get proc "specHash"))
       (= origin.placement (get proc "placement"))))


(defn #^ (| ReadinessReport MetricsReport None) current-report [#^ (| tuple None) reports #^ dict proc]
  "報告の列(古い順 — 観測の表 readiness か metrics の Service の行)のうち、今動いている process の最新の物。"
  (next (gfor r (reversed (or reports #())) :if (report-matches r.origin proc) r) None))


(defn #^ tuple keep-report [#^ (| tuple None) reports #^ (| ReadinessReport MetricsReport) report]
  "世代ごとに最新 1 つ・直近の REPORTS-KEPT 世代だけ残す(古い順)。担い手の heartbeat より先に新しい process の報告が届いても、
   heartbeat が追いついた時にその報告を数えられるよう、1 つに畳まない。"
  (setv others (lfor r (or reports #()) :if (!= r.origin.instance report.origin.instance) r))
  (tuple (cut (+ others [report]) (- REPORTS-KEPT) None)))


(defk report-origin [body now]
  {:pre [(: body (| ReadinessBody MetricsBody)) (: now int)] :post [(: % ReportOrigin)] :tags {:context "coordinator" :role "judgment"}}
  "報告の本文(受け口が JSON から道の型に解いた値 — #2445)の送り手の process の世代に、受けた時刻を添えるため(準備の報告と計器の報告で
   同じ — 数えるのは今の process の報告だけ・report-matches)。"
  (ReportOrigin :worker body.worker :pid body.pid :revision body.revision :instance body.instance :attempt body.attempt
                :spec-hash body.spec-hash :placement body.placement :at now))


(defk readiness-report [body now]
  {:pre [(: body ReadinessBody) (: now int)] :post [(: % ReadinessReport)] :tags {:context "coordinator" :role "judgment"}}
  "準備の報告の本文を、観測の表 readiness に置く記録にするため。ready・reason・role は fake(readiness-claims)と同じ関数
   (reported-readiness)で揃える — role = active(仕事をしている)か standby(lease を他が持つ間の待機)。旧い報告は active。"
  (<- origin ReportOrigin (report-origin body now))
  (<- claim ReadinessClaim (reported-readiness body.ready body.reason body.role))
  (ReadinessReport :origin origin :ready claim.ready :reason claim.reason :role claim.role))


(defn #^ dict service-readiness [#^ ClusterState state #^ str name #^ int now #^ ClusterTiming timing
                                 #^ (| Placement None) [placement None]]
  "Service が Ready か。state = Ready | NotReady | Unknown(coordinator が起動した直後で報告が揃っていない)。
   placement = 見る置き先(既定 = いまの置き先 — running-process)。"
  (defn #^ dict verdict [#^ str s #^ str reason] {"state" s "reason" reason})
  (setv proc (running-process state name now timing placement))
  (when (not (get proc "ok")) (return (verdict (get proc "state") (get proc "reason"))))
  (setv job (get proc "job"))
  (when (is job.readiness None)
    (return (verdict "Ready" "process が動いている(readiness の宣言なし)")))
  (setv window-ms (int (* 1000 (get job.readiness "windowSeconds")))
        report (current-report (.row state.observations.readiness name) proc))
  (when (is report None)
    (return (verdict (if (< now (unreported-until state window-ms)) "Unknown" "NotReady")
                     (.format "今動いている process(世代 {})からの準備できたの報告がまだ無い" (get proc "instance")))))
  (setv age (- now report.origin.at))
  (setv role report.role)
  (cond
    (>= now (report-expired-from report window-ms))
      (verdict "NotReady" (.format "最後の報告から {} 秒(window {} 秒)" (// age 1000) (// window-ms 1000)))
    (not report.ready) (| (verdict "NotReady" (+ "報告: " report.reason)) {"role" role})
    True (| (verdict "Ready" report.reason) {"role" role})))


;; --- 版の判定(2026-09-29・#1013)---------------------------------------------------------------
;; Service の指定の版(spec.revision)が実際に仕事をしているかを 5 値(cluster_model.VersionState)で答える。running-process の ok は
;; 入れ替え(handoff)の途中で「新しい版が仕事をしている」を意味しない(新が Ready になるまで旧い版が退避名 <名>#retired-<世代> で
;; 仕事を続け、諦めた後も動き続ける)ので、running-process の意味は変えずに、ここで入れ替えの見張りと退いた process を重ねる。

(setv PROCESS-PHASES (frozenset (gfor p #(JobPhase.RUNNING JobPhase.STOPPING JobPhase.STOP-UNCONFIRMED) p.value)))
;; 子 process が生きている phase(worker_policy.phase-of — process を持つ行だけがこの 3 つになる)。


(defn #^ bool service-stopped [#^ ClusterState state #^ str name #^ int now #^ ClusterTiming timing]
  ;; defk にできない: coordinator の純粋な判断(Program の外 — api_policy.target-view と version-state)が呼ぶ
  "Service name が止まっているか: 宣言が無いか replicas 0、かつ置き先が無く、どこにも生きていない(still-live-somewhere)。
   Rollout の相手の観測(api_policy.target-view の stopped)と版の判定(version-state の Stopped)が同じ条件を読むための定義点。"
  (setv job (next (gfor j state.jobs :if (= j.spec.name name) j) None))
  (and (or (is job None) (= job.replicas 0)) (not-in name state.placements)
       (not (still-live-somewhere now state name timing))))


;; --- 静かな区間の次の期限(#3064)--------------------------------------------------------------
;; 模擬の時計の下の coordinator は、試して静かだった歩の後、次の期限より前の歩を本番の判断で試さずに作る(idle_policy.quiet-stretch)。
;; 下の 2 つは、状態がこのままで readiness の判定と止まりの判定が答えを変え得る最初の刻を、判定が比べに使う期限の値(上の
;; carrier-stale-from ほかと cluster_policy.liveness-deadline)から返す。返すのは答えを変え得る刻の下限(早めに試すのは安全・遅らせない)。

(defk readiness-due [state name now timing [placement None]]
  {:pre [(: state ClusterState) (: name str) (: now int) (: timing ClusterTiming) (: placement (| Placement None))]
   :post [(: % (| int None))] :tags {:context "coordinator" :role "judgment"}}
  "Service name の readiness の判定(service-readiness・running-process)が、状態がこのままで答え(Ready | NotReady | Unknown)を変え得る
   最初の刻を知るため — 入れ替えの見張り(api_policy.placement-due)・Rollout の相手の観測(idle_policy.rollout-due)・drain の並べ
   (cluster_policy.sweep-due)が呼ぶ。担い手の報告が新しい間は、報告が古くなる刻と準備の報告の window の期限。古い間は、起動の直後の
   猶予の終わりと、担い手の沈黙の窓 2 つ(移し替え・途絶の柵)。宣言・置き先が無い・replicas 0 の判定は時刻で変わらない(None)。
   placement = 見る置き先(既定 = いまの置き先 — running-process と同じ)。"
  (val job (next (gfor j state.jobs :if (= j.spec.name name) j) None))
  (val a (if (is placement None) (.get state.placements name) placement))
  (val st (if (is a None) None (.get state.statuses a.worker)))
  (val carrier (if (is a None) None (.get state.workers a.worker)))
  (val proc (running-process state name now timing placement))
  (val watched (and (get proc "ok") (is-not job.readiness None)))
  (val window-ms (if watched (int (* 1000 (get job.readiness "windowSeconds"))) 0))
  (val report (if watched (current-report (.row state.observations.readiness name) proc) None))
  (val dues
    (cond
      (or (is job None) (= job.replicas 0) (is a None)) #()
      (and (is-not st None) (< now (carrier-stale-from st timing)))
        (+ #((carrier-stale-from st timing))
           (cond (not watched) #()
                 (is report None) #((unreported-until state window-ms))
                 True #((report-expired-from report window-ms))))
      True
        (+ #((warm-until state timing))
           (if (is carrier None)
               #()
               #((+ (liveness-deadline carrier timing.reassign-after-ms) 1) (+ (liveness-deadline carrier timing.keep-fence-ms) 1))))))
  (min (gfor due dues :if (> due now) due) :default None))


(defk service-stopped-due [state name now timing]
  {:pre [(: state ClusterState) (: name str) (: now int) (: timing ClusterTiming)] :post [(: % (| int None))]
   :tags {:context "coordinator" :role "judgment"}}
  "service-stopped が、状態がこのままで答えを変え得る最初の刻を知るため(Rollout の Service の相手の stopped — idle_policy.rollout-due)。
   宣言が無いか replicas 0 で置き先も無い Service の行を載せた worker が、沈黙で母集団(cluster_policy.service-rows の lease-ms の窓)から
   外れる刻の最小。それ以外は時刻で変わらない(None)。"
  (val job (next (gfor j state.jobs :if (= j.spec.name name) j) None))
  (if (and (or (is job None) (= job.replicas 0)) (not-in name state.placements))
      (min (gfor #(wname st) (.items state.statuses)
                 :setv w (.get state.workers wname)
                 :if (and (is-not w None) (any (gfor row st.jobs (or (= row.name name) (= row.retired-from name)))))
                 :setv due (+ (liveness-deadline w timing.lease-ms) 1)
                 :if (> due now)
                 due)
           :default None)
      None))


(defn #^ tuple live-processes [#^ ClusterState state #^ str name #^ int now #^ ClusterTiming timing]
  ;; defk にできない: coordinator の純粋な判断(Program の外 — observed-of と version-state)が呼ぶ
  "Service name の process が生きている行の版と、入れ替えで退いた旧い process か(status.version.running — 事実の列で、判定ではない)。
   母集団は still-live-somewhere と同じ(cluster_policy.service-rows)で、phase は PROCESS-PHASES。drain で並べた置き先の process も入る。"
  (tuple (gfor row (service-rows now state name timing)
               :if (in row.phase PROCESS-PHASES)
               ;; 版は process を持つ行なら worker が必ず載せる(worker_policy.statuses)。載せない行は None のまま運ぶ(黙って埋めない)。
               (LiveProcess :revision row.running-revision :retired (= row.retired-from name)))))


(defn #^ (| JobPhase None) phase-named [#^ (| str None) phase]
  ;; defk にできない: coordinator の純粋な判断(Program の外 — version-state)が呼ぶ
  "worker の行の phase の綴り → JobPhase。coordinator の知らない綴り(coordinator より新しい worker の phase)は None —
   呼び手は既定の状態へ倒さず「分からない」と答える。"
  (next (gfor p JobPhase :if (= p.value phase) p) None))


(defn #^ NotReadyKind unplaced-not-ready [#^ UnplacedKind kind]
  ;; defk にできない: coordinator の純粋な判断(Program の外 — running-process)が呼ぶ
  "置き先が無い理由の種類 → running-process の NotReady の種類(unplaced-jobs の 3 種をそのまま種類にする)。"
  (match kind
    UnplacedKind.WAITING-PREVIOUS-HOLDER NotReadyKind.WAITING-PREVIOUS-HOLDER
    UnplacedKind.NO-ELIGIBLE-WORKER NotReadyKind.NO-ELIGIBLE-WORKER
    UnplacedKind.NO-ROOM NotReadyKind.NO-ROOM))


(defn #^ VersionState phase-version [#^ JobPhase phase #^ bool retryable]
  ;; defk にできない: coordinator の純粋な判断(Program の外 — version-state)が呼ぶ
  "担い手の行の phase(running-process が running でないと答えた行)→ 版の判定の状態。retryable = ENV-FAILED の失敗を worker が
   再試行するか(行の retryable)。phase を足したら写し先をここに足す — 漏れると何も返さず、網羅の検査(test_version_state)が赤になる。"
  (match phase
    JobPhase.PREPARING VersionState.UPDATING
    JobPhase.PROBING VersionState.UPDATING
    JobPhase.STARTING VersionState.UPDATING
    JobPhase.STOPPING VersionState.UPDATING
    ;; 担い手がまだ宣言を受け取っていない。
    JobPhase.STOPPED VersionState.UPDATING
    ;; 実行環境の準備の失敗: 一時の失敗は撃ち直しを待つ途中・再試行しない失敗は待っても進まない。
    JobPhase.ENV-FAILED (if retryable VersionState.UPDATING VersionState.BLOCKED)
    JobPhase.CODE-FAILED VersionState.BLOCKED
    JobPhase.PROBE-FAILED VersionState.BLOCKED
    ;; 落ちて起こし直している(1 回落ちただけでも Blocked — 落ちた回数は行の detail の文にしか無い・設計 v3 の戻せる決定)。
    JobPhase.BACKOFF VersionState.BLOCKED
    JobPhase.STOP-UNCONFIRMED VersionState.BLOCKED
    ;; service の process が終わった — 想定の外。
    JobPhase.FINISHED VersionState.BLOCKED
    ;; 入れ替えを諦めた(worker の側から見た同じ事実 — 見張りの Abandoned)。
    JobPhase.HANDOFF-ABANDONED VersionState.BLOCKED
    ;; running-process は phase が running の行を「running でない」と答えないので、ここへは届かない。届いたら答えの食い違いなので
    ;; 既定の状態へ倒さず「分からない」と答える。
    JobPhase.RUNNING VersionState.UNKNOWN))


(defn #^ VersionState not-ready-version [#^ NotReadyKind kind #^ (| JobPhase None) phase #^ bool retryable]
  ;; defk にできない: coordinator の純粋な判断(Program の外 — version-state)が呼ぶ
  "running-process が ok でない理由の種類 → 版の判定の状態(Unknown の答えは version-state が先に Unknown にする)。phase・retryable は
   種類が NOT-RUNNING の時だけ読む: 担い手の行の phase(None = 行にまだ載っていない = 宣言を受け取っていない)と ENV-FAILED を
   再試行するか。種類を足したら写し先をここに足す(漏れると網羅の検査が赤になる)。"
  (match kind
    ;; 宣言を消した直後・replicas 0 で止めている途中(停止の述語が止まっていないと答えた = process がまだ生きている)。
    NotReadyKind.NO-DECLARATION VersionState.UPDATING
    NotReadyKind.NO-REPLICAS VersionState.UPDATING
    ;; drain や入れ替えの正常な途中。
    NotReadyKind.WAITING-PREVIOUS-HOLDER VersionState.UPDATING
    NotReadyKind.NO-ELIGIBLE-WORKER VersionState.BLOCKED
    NotReadyKind.NO-ROOM VersionState.BLOCKED
    ;; 担い手の報告が途絶えて移し替えの期限を過ぎた(他に置ける worker が在れば次の調停で他へ移す・無ければ担い手が戻ると起こし直す
    ;; — #2804。途絶しても動かし続けてよい印を渡した job は Unknown なのでここへ来ない)。
    NotReadyKind.CARRIER-SILENT VersionState.UPDATING
    NotReadyKind.NOT-RUNNING (if (is phase None) VersionState.UPDATING (phase-version phase retryable))
    NotReadyKind.REVISION-MISMATCH VersionState.UPDATING
    ;; process の世代を報告しない古い worker — worker を上げるまで進まない。
    NotReadyKind.NO-INSTANCE VersionState.BLOCKED
    NotReadyKind.SPEC-MISMATCH VersionState.UPDATING
    NotReadyKind.PLACEMENT-MISMATCH VersionState.UPDATING))


(defn #^ VersionVerdict not-ready-verdict [#^ dict proc]
  ;; defk にできない: coordinator の純粋な判断(Program の外 — version-state)が呼ぶ
  "running-process の NotReady の答え → 版の判定(理由は running-process の文)。担い手の行が coordinator の知らない phase を
   持つ時は Unknown(既定の状態へ倒さない)。"
  ;; row は種類が NOT-RUNNING の答えだけが持つ(担い手の行 — 行に載っていなければ None)。
  (setv row (.get proc "row") reason (get proc "reason")
        phase (if (is row None) None (phase-named row.phase)))
  (if (and (is-not row None) (is phase None))
      (VersionVerdict :state VersionState.UNKNOWN :reason (+ "担い手が coordinator の知らない phase を報告した: " reason))
      (VersionVerdict :state (not-ready-version (get proc "kind") phase (and (is-not row None) (is row.retryable True)))
                      :reason reason)))


(defn #^ VersionVerdict version-state [#^ ClusterState state #^ str name #^ int now #^ ClusterTiming timing]
  ;; defk にできない: coordinator の純粋な判断(Program の外 — snapshot と observed-of)が呼ぶ
  "Service name の指定の版が実際に仕事をしているか(status.version)。上から順に判定する:
   受け付けていない宣言 → Blocked・止まっている(service-stopped)→ Stopped・担い手の報告が途絶えている(running-process の
   Unknown)→ Unknown・入れ替えを諦めた → Blocked・running-process が ok でない → 理由の種類と phase で Updating / Blocked・
   ok だが入れ替えの途中(見張りが新の Ready を待っている・退いた旧い process が生きている)→ Updating・それ以外 → Current
   (健康 readiness は含まない — 表示は status.ready が運ぶ)。"
  (setv refused (.get state.refused name))
  (when (is-not refused None)
    (return (VersionVerdict :state VersionState.BLOCKED :reason (+ "宣言を受け付けていない: " refused.reason))))
  (when (service-stopped state name now timing)
    (return (VersionVerdict :state VersionState.STOPPED :reason "止めている")))
  (setv proc (running-process state name now timing)
        watch (.get state.handoffs name)
        handoff (if (is watch None) None watch.phase)
        retired (.join "・" (gfor p (live-processes state name now timing) :if p.retired (str p.revision))))
  (cond
    (and (not (get proc "ok")) (= (get proc "state") "Unknown"))
      (VersionVerdict :state VersionState.UNKNOWN :reason (get proc "reason"))
    (= handoff HandoffPhase.ABANDONED)
      (VersionVerdict :state VersionState.BLOCKED
                      :reason (if retired (.format "入れ替えを諦めた(旧い版 {} が動き続けている)" retired) "入れ替えを諦めた"))
    (not (get proc "ok")) (not-ready-verdict proc)
    (= handoff HandoffPhase.WAITING)
      (VersionVerdict :state VersionState.UPDATING
                      :reason (.format "入れ替えの途中(新しい版 {} は準備中{})" (. (get proc "job") spec revision)
                                       (if retired (.format "・旧い版 {} が仕事をしている" retired) "")))
    retired
      (VersionVerdict :state VersionState.UPDATING :reason (.format "入れ替えの途中(旧い版 {} がまだ動いている)" retired))
    True (VersionVerdict :state VersionState.CURRENT :reason "")))


(defn #^ ClusterState record-readiness [#^ ClusterState state #^ str name #^ ReadinessBody body #^ int now]
  "POST /resources/Service/<名>/readiness: Service name の準備の報告を観測の表 readiness の名の行へ足すため(世代ごとに最新 1 つ —
   keep-report)。表は保存しないが Service の status.ready の材料なので、版の比べ(dirty-keys)が読む。"
  (when (not (any (gfor j state.jobs (= j.spec.name name))))
    (refuse 404 (+ "無い Service: " name)))
  (setv report (run (readiness-report body now))
        seen state.observations)
  (replace state :observations
           (replace seen :readiness (.with-writes seen.readiness
                                                  #((TableWrite name (keep-report (.row seen.readiness name) report)))))))


;; --- 資源の写し(版と記録の比べる単位) ------------------------------------------------------------

(defn #^ dict service-spec [#^ ClusterJob job]
  (dfor #(k v) (.items (job-to-json job)) :if (!= k "name") k v))


(defn #^ dict service-row [#^ ClusterState state #^ ClusterJob job #^ int now #^ ClusterTiming timing]
  "宣言した Service 1 つの {spec status}(snapshot の行)。"
  (setv a (.get state.placements job.spec.name))
  {"spec" (service-spec job)
   "status" (| {"worker" (if a a.worker None) "placement" (if a a.generation None)
                "ready" (get (service-readiness state job.spec.name now timing) "state")
                ;; 版の判定の状態(2026-09-29 — ready と同じく、変わった時に出来事と resourceVersion を進める)。理由と動いている
                ;; 版の列は変わりやすい観測なので入れない(observed-of が組む)。
                "version" {"state" (. (version-state state job.spec.name now timing) state value)}}
               ;; drain で並べた置き先(2026-09-25)。在る間だけ載せる(無い Service の status の形・版は以前と同じ)。
               (if (in job.spec.name state.surges)
                   {"surge" (. (get state.surges job.spec.name) worker)}
                   {})
               ;; 入れ替えの期限の見張り(2026-09-26 — handoff_policy)。Ready を待つ間と諦めた間だけ載せる: 段・起点・期限、
               ;; 諦めたなら時刻と理由(期限と最後の NotReady の理由)と新の世代の最後の ReportReady(偽)の reason。
               (if (in job.spec.name state.handoffs)
                   {"handoff" (.status-json (get state.handoffs job.spec.name) (handoff-timeout-ms job.readiness))}
                   {}))})


(defn #^ dict refused-row [#^ ClusterState state #^ RefusedJob r #^ int now #^ ClusterTiming timing]
  "受け付けない Service の行(改訂 1 の C): spec は元の行のまま・status に理由。版の判定は DELETE で消すまで Blocked。"
  {"spec" (dfor #(k v) (.items r.row) :if (!= k "name") k v)
   "status" {"refused" r.reason "version" {"state" (. (version-state state r.name now timing) state value)}}})


(defn #^ dict worker-row [#^ ClusterState state #^ WorkerInfo w]
  "worker 1 つの {spec status}。"
  {"spec" {"provides" (list w.provides) "exclusive" (list w.exclusive) "node" w.node "capacity" w.capacity "versions" (dict w.versions)}
   ;; 生きているか(#1934 — heartbeat が lease の内)。生死の切り替わりの拍で版が進み、出来事の記録に 1 行残る — 名簿を写す呼び手が
   ;; 版の変化の待ち(GET /watch・AwaitRunnersChange)で worker の死と戻りに即座に起きるため。最後の連絡の時刻そのものは変わりやすい
   ;; 観測なので入れない(生きている間の heartbeat では版は進まない)。
   ;; drain(2026-09-25)の始まりと頼み手(誰が・いつ空けさせたかを出来事の記録に残す)。期限は頼み直すたびに延びるので入れない。
   "status" (| {"live" (not-in w.name state.silent)}
               (if (in w.name state.drains)
                   {"drain" {"sinceMs" (. (get state.drains w.name) since-ms) "actor" (. (get state.drains w.name) actor)}}
                   {}))})


(defn #^ dict task-row [#^ TaskRecord t]
  "task 1 つの {spec status}。"
  {"spec" (| {"name" t.name "revision" t.revision "needs" (list t.needs)}
             (if t.detached {"key" t.key} {}))
   "status" {"phase" t.phase "worker" t.worker "detail" t.detail}})


(defn #^ dict rollout-row [#^ RolloutRow r]
  "Rollout 1 つの {spec status}。"
  {"spec" (rollout-spec-to-json r.spec) "status" (rollout-status-to-json r.status)})


(defn #^ dict snapshot [#^ ClusterState state #^ int now #^ ClusterTiming timing]
  "資源ごとの {spec status}。比べて版を進める単位。変わりやすい観測(生存の時刻・lease の期限)は入れない。同じ名の宣言と受け付けない
   行が両方在れば受け付けない行(後に書く)が勝つ — row-of と同じ順。"
  (| (dfor job state.jobs (key-of "Service" job.spec.name) (service-row state job now timing))
     (dfor r (.values state.refused) (key-of "Service" r.name) (refused-row state r now timing))
     (dfor w (.values state.workers) (key-of "Worker" w.name) (worker-row state w))
     (dfor t (.values state.tasks) (key-of "Task" t.id) (task-row t))
     (dfor #(name r) (.items state.rollouts) (key-of "Rollout" name) (rollout-row r))))


(defn #^ (| dict None) row-of [#^ ClusterState state #^ dict jobs #^ str key #^ int now #^ ClusterTiming timing]
  "鍵 1 つの snapshot の行(資源が無ければ None)。jobs = 宣言の名 → ClusterJob(呼び手が 1 度だけ作る)。"
  (setv #(kind name) (split-key key))
  (match kind
    "Service" (cond (in name state.refused) (refused-row state (get state.refused name) now timing)
                    (in name jobs) (service-row state (get jobs name) now timing)
                    True None)
    "Worker" (if (in name state.workers) (worker-row state (get state.workers name)) None)
    "Task" (if (in name state.tasks) (task-row (get state.tasks name)) None)
    "Rollout" (if (in name state.rollouts) (rollout-row (get state.rollouts name)) None)
    _ (raise (ValueError (+ "snapshot の鍵の種類を知らない: " key)))))


(defn #^ frozenset moved-names [#^ (| dict Table) before #^ (| dict Table) after]
  "2 つの写像(か書き換えない表 Table — 観測の表 readiness・#2756)で、値が同じ物(is)でない鍵 — 足した・消した・置き換えた鍵。状態は
   置き換えで進む(replace・with-writes)ので、触らない値は同じ物のまま。"
  ;; 写像そのものが同じ物なら、どの鍵も同じ物(写像をその場で書き換えない)— 鍵を並べずに空を返す(#2716)。
  (match #(before after)
    #(a b) :if (is a b) (frozenset)
    ;; 表は写像ではない(鍵は keys・行は row で引く)。with-writes は書いた鍵の行だけを新しい物にし、他の行は同じ物のまま運ぶ。
    #((Table) (Table)) (frozenset (gfor k (| (frozenset (.keys before)) (frozenset (.keys after)))
                                        :if (is-not (.row before k) (.row after k)) k))
    #((dict) (dict)) (frozenset (gfor k (| (set before) (set after)) :if (is-not (.get before k) (.get after k)) k))
    _ (raise (TypeError (.format "moved-names は同じ種類の 2 つ(写像どうしか表どうし)を比べる: {} と {}"
                                 (. (type before) __name__) (. (type after) __name__))))))


(defn #^ frozenset status-row-names [#^ ClusterState state #^ str worker]
  "worker の最新の報告に載る job の名(退いた process の元の名を含む — service-rows の母集団に入る名)。"
  (setv st (.get state.statuses worker))
  (if (is st None)
      (frozenset)
      (frozenset (+ (lfor row st.jobs row.name) (lfor row st.jobs :if row.retired-from row.retired-from)))))


(defn #^ frozenset dirty-keys [#^ ClusterState before #^ ClusterState after #^ dict jobs-before #^ dict jobs-after]
  "before → after で行が変わりうる資源の鍵(snapshot の行の材料が変わった資源の上集合)と、版の記録の無い資源の鍵(adopt — 行が
   同じでも版を振る)。stamp はこの鍵の行だけを組んで比べる。行は同じ now で組むので、材料の値が同じ物のままの資源の行は前後で等しい
   (時刻だけで変わる観測は snapshot に入れない・生死は note-liveness が silent に写して材料にする)。材料:
   - Service: 宣言・置き先・並べた置き先・入れ替えの見張り・準備の報告(観測の表 readiness)・受け付けない行・途絶しても動かし続けてよい
     印の約束(名ごと)/ 置き先か並べた置き先の
     worker、または報告に名が載る worker の報告と生存(service-rows・running-process)/ 置き先の無い Service は全 worker の生存と
     能力(unplaced-kind)/ drain の集合と起動の時刻(全 Service — まれ)。
   - Worker: 記録・沈黙の集合の出入り・drain。Task・Rollout: 自分の行。"
  (setv names-moved (| (moved-names jobs-before jobs-after) (moved-names before.placements after.placements)
                       (moved-names before.surges after.surges) (moved-names before.handoffs after.handoffs)
                       (moved-names before.observations.readiness after.observations.readiness)
                       (moved-names before.refused after.refused)
                       ;; 途絶しても動かし続けてよい印の約束(#2804 — 担い手の沈黙の後の ready の材料)。
                       (frozenset (gfor m (+ before.keep-marks after.keep-marks)
                                  :if (or (not-in m before.keep-marks) (not-in m after.keep-marks))
                                  m.job)))
        workers-moved (| (moved-names before.workers after.workers) (moved-names before.statuses after.statuses))
        all-services (| (frozenset jobs-before) (frozenset jobs-after) (frozenset before.refused) (frozenset after.refused))
        global-moved (or (!= before.started-ms after.started-ms) (is-not before.drains after.drains))
        carried (frozenset (gfor state #(before after)
                                 placed #((.items state.placements) (.items state.surges))
                                 #(name p) placed
                                 :if (in p.worker workers-moved)
                                 name))
        reported (frozenset (gfor state #(before after) w workers-moved name (status-row-names state w) name))
        unplaced (if workers-moved
                     (frozenset (gfor name all-services :if (or (not-in name before.placements) (not-in name after.placements)) name))
                     (frozenset))
        services (if global-moved all-services (| names-moved carried reported unplaced))
        silent-moved (^ (frozenset before.silent) (frozenset after.silent))
        workers (| (moved-names before.workers after.workers) (moved-names before.drains after.drains) silent-moved)
        moved (| (frozenset (gfor n services (key-of "Service" n)))
                 (frozenset (gfor n workers (key-of "Worker" n)))
                 (frozenset (gfor n (moved-names before.tasks after.tasks) (key-of "Task" n)))
                 (frozenset (gfor n (moved-names before.rollouts after.rollouts) (key-of "Rollout" n))))
        present (| (frozenset (gfor n all-services :if (or (in n jobs-after) (in n after.refused)) (key-of "Service" n)))
                   (frozenset (gfor n after.workers (key-of "Worker" n)))
                   (frozenset (gfor n after.tasks (key-of "Task" n)))
                   (frozenset (gfor n after.rollouts (key-of "Rollout" n))))
        unversioned (frozenset (gfor k present :if (not-in k after.meta) k)))
  (| moved unversioned))


;; v は記録の欄の JSON の値(どの形にもなる)— 長い値だけを切った文字列に置き換え、それ以外はそのまま返す。
(defn #^ object short-value [#^ object v]
  (setv text (json.dumps v :ensure-ascii False :sort-keys True :default str))
  (if (> (len text) 160) (+ (cut text 0 157) "…") v))


(defn #^ dict changed-fields [#^ (| dict None) old #^ (| dict None) new]
  "何が変わったか(spec / status の一段目の欄ごとに前と後。長い値は切る)。"
  (setv out {})
  (for [part #("spec" "status")]
    (setv a (if old (get old part) {}) b (if new (get new part) {}))
    (for [k (sorted (| (set a) (set b)))]
      (when (!= (.get a k) (.get b k))
        (setv (get out (+ part "." k)) [(short-value (.get a k)) (short-value (.get b k))]))))
  out)


(defn #^ tuple trim-audit [#^ list audit]  ; audit = AuditEvent の列
  "kind ごとに直近 AUDIT-PER-KIND 件だけ残す(順は保つ)。"
  (setv counts {} keep [])
  (for [event (reversed audit)]
    (setv kind event.kind n (.get counts kind 0))
    (when (< n AUDIT-PER-KIND)
      (setv (get counts kind) (+ n 1))
      (.append keep event)))
  (tuple (reversed keep)))


(defn #^ ClusterState stamp [#^ ClusterState before #^ ClusterState after #^ str actor #^ int now #^ ClusterTiming timing]
  "before → after の差を資源ごとに版へ写す。変わった資源の resourceVersion を coordinator 全体の番号で進め、spec が変われば
   generation も進め、出来事を 1 件記録する。版の欄の無い資源(旧い形の file から読んだ物)は adopt として版を振る。
   作成か否かは before の行の有無で決める(版の記録の有無ではない): before に行が無い資源は、版の記録が残っていても create とし、
   記録を generation 1 から始める。読み直しは読めない旧い形の行(labels だけの worker)を捨て、その版の記録 meta/<種類>/<名> を
   置き場に残す — 以前は同じ名の資源を書くたびに TypeError になり、新しい形の worker が名乗れなかった(2026-09-29・#1005)。"
  (when (is before after) (return after))
  ;; 比べるのは行が変わりうる資源だけ(dirty-keys — 全体の snapshot を前後で組まない・#2615)。行の形・出来事の順(鍵の順)は前と同じ。
  (setv jobs-before (dfor j before.jobs j.spec.name j) jobs-after (dfor j after.jobs j.spec.name j)
        keys (dirty-keys before after jobs-before jobs-after)
        b (dfor k keys :setv row (row-of before jobs-before k now timing) :if (is-not row None) k row)
        a (dfor k keys :setv row (row-of after jobs-after k now timing) :if (is-not row None) k row)
        meta (dict after.meta) audit (list after.audit) rev after.revision seq after.audit-seq)
  (for [key (sorted keys)]
    (setv old (.get b key) new (.get a key))
    (when (and (= old new) (or (is new None) (in key meta))) (continue))
    (+= rev 1)
    (+= seq 1)
    (setv #(kind name) (split-key key) current (.get meta key))
    (cond
      (is new None)
        (do (.pop meta key None)
            (setv verb "delete" from (if current current.resource-version None) to None
                  generation (if current current.generation None)))
      (or (is old None) (is current None))
        (do (setv verb (if (is old None) "create" "adopt") from None to rev generation 1)
            (setv (get meta key) (ResourceMeta :resource-version rev :generation 1 :created-by actor :created-ms now
                                               :updated-by actor :updated-ms now)))
      True
        (do (setv spec-changed (!= (get old "spec") (get new "spec"))
                  verb (if spec-changed "update" "status") from current.resource-version to rev
                  generation (+ current.generation (if spec-changed 1 0)))
            (setv (get meta key) (replace current :resource-version rev :generation generation :updated-by actor :updated-ms now))))
    (.append audit (AuditEvent :seq seq :at now :actor actor :verb verb :kind kind :name name
                               :from-version from :to-version to :generation generation
                               :changes (changed-fields old new))))
  (replace after :meta meta :revision rev :audit (trim-audit audit) :audit-seq seq))


(defn #^ ClusterState adopt-legacy [#^ ClusterState state #^ int now #^ ClusterTiming timing]
  "旧い形の状態の file(版の欄が無い)を読んだ直後に 1 度: 所有者の無い宣言を LEGACY-OWNER にし、全資源に版を振る(送り手 = migration)。"
  (setv owned (replace state :jobs (tuple (gfor j state.jobs (if (is j.owner None) (replace j :owner LEGACY-OWNER) j)))))
  (stamp (replace state :meta state.meta) owned MIGRATION now timing))


;; --- 見せる形 --------------------------------------------------------------------------------

(defn #^ (| ServiceObserved WorkerObserved TaskObserved RolloutObserved None) observed-of
  [#^ ClusterState state #^ str kind #^ str name #^ int now #^ ClusterTiming timing]
  "資源の種類ごとの変わりやすい観測(比べる単位 snapshot に入れない物 — 資源の画面に足す)。"
  (cond
    (= kind "Service")
      (do (setv a (.get state.placements name) reports (.row state.observations.readiness name))
          (ServiceObserved :ready-reason (get (service-readiness state name now timing) "reason")
                           :last-readiness (if reports (get reports -1) None)
                           :process (if a (job-status-row state a.worker name) None)
                           ;; 版の判定(state は snapshot と同じ値)と、その理由・動いている版の列(2026-09-29)。
                           :version (version-state state name now timing)
                           :running (live-processes state name now timing)))
    (= kind "Worker")
      (do (setv w (get state.workers name))
          (WorkerObserved :silent-ms (- now w.last-seen-ms) :alive (alive now w timing.lease-ms)))
    (= kind "Task") (TaskObserved :task (get state.tasks name))
    (= kind "Rollout")
      (RolloutObserved :observed (tuple (gfor t (rollout-targets (. (get state.rollouts name) spec))
                                              :if (= t.kind "Deployment")
                                              (ObservedDeployment :key (target-key t)
                                                                  :seen (.row state.observations.deployments
                                                                              (+ t.namespace "/" t.name))))))
    True None))


(defn #^ ResourceView resource-view [#^ ClusterState state #^ str key #^ dict snap #^ int now #^ ClusterTiming timing]
  "資源 1 つの画面(JSON は coordinator/protocol/replies が綴る — #2595)。"
  (setv #(kind name) (split-key key) row (get snap key))
  (ResourceView :kind kind :name name :meta (.get state.meta key) :spec (get row "spec") :status (get row "status")
                :observed (observed-of state kind name now timing)))


(defn #^ ResourceList list-resources [#^ ClusterState state #^ str kind #^ int now #^ ClusterTiming timing]
  (when (not-in kind KINDS) (refuse 404 (+ "知らない kind: " kind)))
  (setv snap (snapshot state now timing))
  (ResourceList :kind kind :revision state.revision
                :items (tuple (gfor key (sorted snap) :if (.startswith key (+ kind "/")) (resource-view state key snap now timing)))))


(defn #^ ResourceView get-resource [#^ ClusterState state #^ str kind #^ str name #^ int now #^ ClusterTiming timing]
  (when (not-in kind KINDS) (refuse 404 (+ "知らない kind: " kind)))
  (setv snap (snapshot state now timing) key (key-of kind name))
  (when (not-in key snap) (refuse 404 (+ "無い資源: " key)))
  (resource-view state key snap now timing))


(defn #^ EventsView events-view [#^ ClusterState state #^ dict query]
  "GET /events: 問いの kind / name / since に合う出来事(古い順・limit 件まで — 既定 200・上限 2000)。JSON は coordinator/protocol/replies が綴る。"
  (setv kind (.get query "kind") name (.get query "name") since (int-field query "since" 0)
        limit (min (int-field query "limit" 200) 2000))
  (setv rows (tuple (gfor e state.audit
                          :if (and (> e.seq since) (or (not kind) (= e.kind kind)) (or (not name) (= e.name name)))
                          e)))
  (EventsView :revision state.revision :seq state.audit-seq :events (cut rows (- limit) None)))


;; --- 書きの口(Service と Rollout)--------------------------------------------------------------

(defn #^ None check-version [#^ ClusterState state #^ str key #^ (| int None) version #^ bool [required True]]
  (setv current (resource-version-of state key))
  (cond
    (and required (is version None))
      (refuse 400 "resourceVersion が要る(読んだ時の版を付けて書く — 古い版の書きは 409 で断る)" :current current)
    (and (is-not version None) (!= version current))
      (refuse 409 (.format "版が古い: 送られた {}・いまの {}(読み直してから書く)" version current) :current current)))


(defn #^ tuple active-rollouts-touching [#^ ClusterState state #^ list target-keys #^ (| str None) [skip None]]
  (tuple (gfor #(name r) (.items state.rollouts)
               :if (and (!= name skip) (not-in r.status.phase TERMINAL-PHASES)
                        (& (set target-keys) (sfor t (rollout-targets r.spec) (target-key t))))
               name)))


(defn #^ ClusterState create-resource [#^ ClusterState state #^ str kind #^ (| ResourceBody ServiceBody) body #^ str actor #^ int now]
  ;; 包みの形(name は文字列・spec は object)と Service の宣言の行は、本文を解く所(coordinator/protocol/request_bodies — #2445・#2448)が読んだ。
  (setv name body.name)
  (when (not (and name (not-in "/" name))) (refuse 400 (.format "名前が正しくない: {!r}" name)))
  (cond
    (isinstance body ServiceBody)
      (do (when (any (gfor j state.jobs (= j.spec.name name))) (refuse 409 (+ "もう在る Service: " name)))
          (setv job (replace body.job :owner (or (valid-actor body.owner) actor)))
          ;; 受け付けない行(RefusedJob)と同じ名なら、新しい形の宣言で置き換える(改訂 1 の C)。
          (replace state :jobs (+ state.jobs #(job)) :refused (dfor #(k v) (.items state.refused) :if (!= k name) k v)))
    (= kind "Rollout")
      (do (when (in name state.rollouts) (refuse 409 (+ "もう在る Rollout: " name)))
          (setv raw (dict (or body.spec {}))
                spec (validate-rollout-spec (| raw {"owner" (or (valid-actor (.get raw "owner")) actor)})))
          (for [t (rollout-targets spec)]
            (when (and (= t.kind "Service") (not (any (gfor j state.jobs (= j.spec.name t.name)))))
              (refuse 400 (+ "Rollout の相手の Service が無い(先に作る): " t.name))))
          (setv busy (active-rollouts-touching state (lfor t (rollout-targets spec) (target-key t))))
          (when busy (refuse 409 (+ "同じ相手を扱う Rollout が進行中: " (.join ", " busy))))
          (replace state :rollouts (| state.rollouts {name (RolloutRow :spec spec :status (RolloutStatus :created-ms now))})))
    True (refuse 405 (+ "この kind は API から作れない: " kind))))


(defn #^ None check-owner-change [#^ (| str None) current-owner #^ (| str None) new-owner #^ str actor]
  (when (and new-owner (!= new-owner current-owner) (!= actor current-owner) (!= current-owner LEGACY-OWNER))
    (refuse 403 (.format "所有者を変えられるのは所有者({})だけ" current-owner))))


(defn #^ ClusterState update-resource [#^ ClusterState state #^ str kind #^ str name #^ (| ResourceBody ServiceBody) body #^ str actor]
  (setv key (key-of kind name))
  (cond
    (isinstance body ServiceBody)
      (do (setv current (next (gfor j state.jobs :if (= j.spec.name name) j) None))
          ;; 受け付けない行(RefusedJob)は、新しい形の宣言の PUT で受け付けた job に置き換える(改訂 1 の C)。
          (when (and (is current None) (in name state.refused))
            (check-version state key body.resource-version)
            (setv job (replace body.job :owner (or (valid-actor body.owner)
                                                   (.get (. (get state.refused name) row) "owner") actor)))
            (return (replace state :jobs (+ state.jobs #(job))
                                   :refused (dfor #(k v) (.items state.refused) :if (!= k name) k v))))
          (when (is current None) (refuse 404 (+ "無い Service: " name)))
          (check-version state key body.resource-version)
          (check-owner-change current.owner body.owner actor)
          (setv job (replace body.job :owner (or (valid-actor body.owner) current.owner)))
          (replace state :jobs (tuple (gfor j state.jobs (if (= j.spec.name name) job j)))))
    (= kind "Rollout")
      (do (setv current (.get state.rollouts name) spec (dict (or body.spec {})))
          (when (is current None) (refuse 404 (+ "無い Rollout: " name)))
          (check-version state key body.resource-version)
          (setv old current.spec old-json (rollout-spec-to-json old))
          ;; spec は作った後に変えない。変えてよいのは中止(abort)だけ(旧を先に戻してから新を止める)。
          ;; 送られた spec は作る時と同じ形に揃えてから比べる({"abort": true} だけを送ってもよい)。
          (setv incoming (if (or (in "from" spec) (in "to" spec))
                             (rollout-spec-to-json (validate-rollout-spec (| spec {"owner" (.get spec "owner" old.owner)})))
                             (| old-json spec)))
          (when (!= (dfor #(k v) (.items incoming) :if (!= k "abort") k v)
                    (dfor #(k v) (.items old-json) :if (!= k "abort") k v))
            (refuse 409 "Rollout の spec は変えられない(変えてよいのは abort だけ。別の向きは新しい Rollout で表す)"))
          (replace state :rollouts (| state.rollouts {name (replace current :spec (replace old :abort (bool (.get spec "abort"))))})))
    True (refuse 405 (+ "この kind は API から書けない: " kind))))


(defn #^ ClusterState delete-resource [#^ ClusterState state #^ str kind #^ str name #^ dict query #^ str actor
                                      #^ int now #^ ClusterTiming timing]
  (setv key (key-of kind name) force (in (.get query "force" "") #("true" "1"))
        version (if (in "resourceVersion" query) (int-field query "resourceVersion" None) None))
  (check-version state key version :required False)
  (cond
    (= kind "Service")
      (do (setv current (next (gfor j state.jobs :if (= j.spec.name name) j) None))
          (when (and (is current None) (in name state.refused))
            (return (replace state :refused (dfor #(k v) (.items state.refused) :if (!= k name) k v))))
          (when (is current None) (refuse 404 (+ "無い Service: " name)))
          (when (and (!= actor current.owner) (not force))
            (refuse 403 (.format "宣言を消せるのは所有者({})か、明示の force つきの delete だけ" current.owner)))
          (setv busy (active-rollouts-touching state [(target-key (RolloutTarget :kind "Service" :name name))]))
          (when (and busy (not force)) (refuse 409 (+ "この Service を扱う Rollout が進行中: " (.join ", " busy))))
          ;; 消した Service の準備と計器の報告も観測の表から外す(同じ名で作り直した Service に前の process の報告を数えない)。
          (setv seen state.observations
                dropped #((TableWrite name None)))
          (replace state :jobs (tuple (gfor j state.jobs :if (!= j.spec.name name) j))
                         :observations (replace seen :readiness (.with-writes seen.readiness dropped)
                                                     :metrics (.with-writes seen.metrics dropped))))
    (= kind "Rollout")
      (do (setv current (.get state.rollouts name))
          (when (is current None) (refuse 404 (+ "無い Rollout: " name)))
          (setv owner current.spec.owner)
          (when (and (!= actor owner) (not force))
            (refuse 403 (.format "Rollout を消せるのは所有者({})か、明示の force つきの delete だけ" owner)))
          (when (and (not-in current.status.phase TERMINAL-PHASES) (not force))
            (refuse 409 "進行中の Rollout は消せない(中止は abort を書く。旧を先に戻してから新を止める)"))
          (replace state :rollouts (dfor #(k v) (.items state.rollouts) :if (!= k name) k v)))
    (= kind "Worker")
      (do (setv w (.get state.workers name))
          (when (is w None) (refuse 404 (+ "無い Worker: " name)))
          (when (alive now w timing.reassign-after-ms)
            (refuse 409 "生きている worker は忘れられない(移し替えの期限まで沈黙した worker だけ)"))
          (replace state :workers (dfor #(k v) (.items state.workers) :if (!= k name) k v)
                         :statuses (dfor #(k v) (.items state.statuses) :if (!= k name) k v)))
    (= kind "Task")
      (do (when (not-in name state.tasks) (refuse 404 (+ "無い Task: " name)))
          (replace state :tasks (dfor #(k v) (.items state.tasks) :if (!= k name) k v)))
    True (refuse 404 (+ "知らない kind: " kind))))


;; --- 旧い PUT /jobs(移行の間だけ)-------------------------------------------------------------

(defn #^ tuple legacy-put-jobs [#^ ClusterState state #^ (get tuple #(LegacyJobRow ...)) rows #^ str actor]
  "旧い口 PUT /jobs を資源ごとの compare-and-set に写す(2026-09-24 決定)。
   - 行に resourceVersion があれば、その版の時だけその Service を書き換える。
   - 行に resourceVersion が無ければ、無い Service を作るだけ(在って中身が違えば競合)。中身が同じなら何もしない。
   - 一覧に無い Service は消さない(消すのは DELETE だけ)。返事の untouched に並べる。
   - 1 行でも競合すれば何も書かない(全部か無しか)。返事 409 に行ごとの理由。"
  ;; 行の形の誤り(名の無い行・読めない宣言)は本文を解く所(coordinator/protocol/request_bodies.legacy-jobs-of — #2448)が 400 で断った。
  (setv current (dfor j state.jobs j.spec.name j) jobs (list state.jobs) conflicts [] results {})
  (for [row rows]
    (setv name row.name have (.get current name) version row.version)
    (setv owner (if have have.owner (or (valid-actor row.owner) actor)))
    ;; 行に無い replicas と readiness は今の宣言の値(無ければ既定)で埋め、埋めた形を宣言と同じ規則で検め直す。
    (setv job (replace row.job :owner owner
                       :replicas (if row.replicas-given row.job.replicas (if have have.replicas 1))
                       :readiness (if row.readiness-given row.job.readiness (if have have.readiness None))))
    (setv readiness-problem (readiness-refusal job.readiness job.update))
    (when (is-not readiness-problem None)
      (raise (BodyInvalid readiness-problem)))
    (cond
      (is have None)
        (if (is-not version None)
            (.append conflicts (RowConflict :name name :message "版が付いているが、その Service は無い(消された)"))
            (do (.append jobs job) (setv (get results name) "created")))
      (= job have) (setv (get results name) "unchanged")
      (is version None)
        (.append conflicts (RowConflict :name name :message "resourceVersion の無い行で既存の宣言は変えられない(GET /state の行の版を付ける)"
                                         :current (resource-version-of state (key-of "Service" name))))
      (!= version (resource-version-of state (key-of "Service" name)))
        (.append conflicts (RowConflict :name name :message "版が古い"
                                         :current (resource-version-of state (key-of "Service" name))))
      (and (is-not row.owner None) (!= row.owner have.owner) (!= actor have.owner) (!= have.owner LEGACY-OWNER))
        (.append conflicts (RowConflict :name name :message (.format "所有者を変えられるのは所有者({})だけ" have.owner)))
      True
        (do (setv jobs (lfor j jobs (if (= j.spec.name name) job j)))
            (setv (get results name) "updated"))))
  (setv untouched (lfor n (sorted current) :if (not-in n (sfor r rows r.name)) n))
  (if conflicts
      #(state 409 (ErrorReply :message "競合した行がある(何も書いていない)" :conflicts (tuple conflicts)))
      #((replace state :jobs (tuple jobs)) 200 {"jobs" (len rows) "results" results "untouched" untouched
                                                "note" "一覧に無い Service は消していない(消すのは DELETE /resources/Service/<名>)"})))
