;;; coordinator の資源(Service・Worker・Task・Rollout・GroupLimit・NeedsOverride)の純粋な判断。I/O はしない。
;;;
;;; - 版と出来事の記録: stamp が「前の状態と後の状態」の差から、変わった資源ごとに resourceVersion を進め(spec が変われば
;;;   generation も)、送り手・時刻・前後の版を出来事の記録へ足す。変化を起こした経路(API・heartbeat・調停・Rollout)を問わず
;;;   ここ 1 か所で付くので、版の付け忘れ・記録の漏れが起きない。
;;; - 書きの口: Service・Rollout・GroupLimit は資源 1 つずつの compare-and-set(PUT は resourceVersion 必須・古ければ 409)。
;;;   GroupLimit(2026-10-12・card ki-b0e8028c8c8d)= 系が宣言した組の同時数の上限 1 つ(名 = 組の名・spec = {scope limit perPart? system?})。
;;;   宣言の道具が書き、置き方はこの値で数える。spec の形は系の構成子と同じ規則(detached_rules.group-limit-refusal)で検める。
;;;   system = 組を宣言した系の名で、別の系からの書き直しは 409 で断る(組を宣言する系は 1 つ)。system は X-Actor と同じ信頼の水準の
;;;   名乗りで(照らす系の名簿は無い)、別の名の系が組を写した時(盲検 A)を止める物 — 同じ名を名乗る 2 つの宣言は区別しない。
;;;   system の無い組は持ち主の無い組で、最初に書き直した系が持つ。消す(DELETE)は持ち主を照らさない(宣言の道具は消さない)。
;;;   一覧の丸ごとの上書きはしない。所有者(owner)は誰が宣言したかの記録で、書き・消しを断る照合には使わない(名乗りの X-Actor は
;;;   確かめないので守りにならない — 2026-10-05 に照合して断る分岐を外した)。force は進行中の Rollout が扱う資源を消す時だけに効く。
;;;   Service の PUT で、版(spec.revision)を変えずに置き先(needs・pin)を変える書きは 409 で断り、資源 NeedsOverride を案内する
;;;   (2026-10-27・card ki-1523bc0aba4f — placement-rewrite-refusal)。宣言の道具は revision に宣言の版を入れるので、宣言は断られない。
;;;   照らすのは版で、送り手の名乗りではない。
;;; - NeedsOverride(2026-10-27・card ki-1523bc0aba4f)= Service 1 つの置き先の一時の上書き(名 = Service の名・spec = {needs reason
;;;   ttlSeconds} — 作る時だけ。版つきの書き直しは無い。延ばす・変えるのは DELETE してから作り直す)。宣言(Service の spec)は書き換えず、
;;;   置き先の判断が効く needs をこの行から決める(cluster_policy.effective-needs)。調停の拍ごとに(lift-needs-overrides — api_policy.settle が
;;;   置き方の前に呼ぶ)、期限の来た上書きと、上書きの needs を満たす生きた worker が 0 になった上書きを外し、同じ拍で宣言の needs で
;;;   置き直す(送り手 = coordinator の delete — 出来事の記録に残る)。DELETE も同じ外し方。
;;; - readiness: Service が Ready か(service-readiness)。process の生存(worker の報告の running)と、宣言が readiness を
;;;   持てば ReportReady の直近の報告の両方で決める。
;;; - 版の判定: Service の指定の版が実際に仕事をしているか(version-state — 5 値・status.version)。running-process・入れ替えの見張り・
;;;   停止の述語(service-stopped — Rollout の相手の観測 api_policy.target-view と共有)を呼んで組み立てる。
(require doeff-hy.macros [defk deff val var <-])
(val MODULE-TAGS {:context "coordinator" :role "judgment"})
(import dataclasses [replace])
(import json)
(import typing [NoReturn])
(import doeff_hy.table [Table TableWrite draft-of])
(import doeff_cluster.shared.intent.protocol [ClusterTiming BodyInvalid])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterJob ClusterState NeedsOverride ErrorReply RowConflict Placement HandoffPhase UnplacedKind NotReadyKind VersionState VersionVerdict LiveProcess ResourceMeta AuditEvent EventsView ServiceBody ServiceObserved WorkerObserved TaskObserved RolloutObserved ObservedDeployment ResourceView ResourceList LegacyJobRow RolloutRow RolloutStatus RolloutTarget RefusedJob WorkerInfo HandoverBlocker TaskRecord
                                                       ReportOrigin ReadinessReport MetricsReport WorkerReport])
(import doeff_cluster.coordinator.core.cluster_rules [int-field])
(import doeff_cluster.shared.core.job_rules [spec-hash] doeff_cluster.shared.intent.job_model [JobPhase])
(import doeff_cluster.coordinator.core.cluster_policy [job-to-json alive effective-needs placeable liveness-deadline still-live-somewhere service-rows unplaced-kind unplaced-text unplaced-view active-jobs resource-version-of keep-mark-of warms-for
                                                       handover-blockers handover-since HANDOVER-STALL-MS])
(import doeff_cluster.coordinator.core.rollout_policy [validate-rollout-spec rollout-spec-to-json rollout-status-to-json rollout-targets target-key TERMINAL-PHASES])
(import doeff [run])
(import doeff_cluster.coordinator.intent.request_bodies [ReadinessBody MetricsBody ResourceBody StatusRow])
(import doeff_cluster.shared.core.readiness_rules [handoff-timeout-ms readiness-refusal])
(import doeff_cluster.shared.core.readiness_report [reported-readiness])
(import doeff_cluster.shared.intent.readiness_model [ReadinessClaim])
(import doeff_cluster.shared.intent.detached_model [GroupLimit])
(import doeff_cluster.shared.core.detached_rules [group-limit-refusal group-limit-of group-limit-spec])
(import doeff_cluster.shared.core.capabilities [capabilities-of])

(setv LEGACY-OWNER "legacy:jobs")        ; 旧い PUT /jobs の頃からの宣言の所有者の記録(本当の持ち主は coordinator には分からない)
(setv COORDINATOR "coordinator")          ; 調停(割り当て・task の置き先)の送り手
(setv MIGRATION "migration")              ; 旧い形の状態の file に版を振った送り手
(setv AUDIT-PER-KIND 300)                 ; 出来事の記録の上限(kind ごと)
(setv KINDS #("Service" "Worker" "Task" "Rollout" "GroupLimit" "NeedsOverride"))


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
  (setv st (.row state.observations.statuses worker))
  (when (is st None) (return None))
  (for [row st.jobs]
    (when (= row.name name) (return row)))
  None)


;; process の世代(2026-09-24 の実弾で改めた): readiness と計器の報告は、送った process の世代(worker が起こした時に振った名
;; instance・試行の番号 attempt・起こした spec の指紋 specHash・割り当ての世代 placement)を持つ。数えるのは「今の宣言の spec で、
;; 担い手の worker が running と報告している process」が出した報告だけ。以前は同じ担い手・同じ版の直近の報告なら数えたので、
;; 設定だけを変えた時(版は同じ)に、止めた前の process の Ready が window に残り、新しい process が 1 拍も終えないうちに Rollout が
;; 本番を止めた(Rollout 2026-09-25 05:11:12)。spec(設定を含む)が変われば指紋が変わり、以前の報告は数えない。
;; 残す報告(AL-D2o・2026-10-11): 置き先の process の報告は、他の送り手がいくつ報告しても必ず残す(keep-report・placed-senders)。
;; 以前は送り手を問わず直近の 4 世代だけを残したので、退いた世代の worker が起こし直した process(同じ worker の名・同じ指紋・同じ
;; 割り当ての世代を名乗る)が 4 つ並んで報告すると、置き先の process の報告が次の報告までに押し出され、Service が Ready と NotReady の
;; 間で揺れた(10-11 12:14〜12:50 UTC — 揺れが入れ替えの合図 supersededBy を外し、退いた世代の起こし直しを毎周期に増やした)。
(setv REPORTS-KEPT 4)            ; Service ごとに残す置き先でない送り手の報告(process の世代ごとに最新 1 つ・直近の 4 世代)


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
   ok・state・reason の意味は readiness・入れ替えの合図(ready-instance)・drain・計器が読むので変えない。
   placement = 見る置き先(既定 = いまの置き先。drain で並べた置き先(surge)の process を見る時はそれを渡す — drain_policy)。"
  (setv job (next (gfor j state.jobs :if (= j.spec.name name) j) None))
  ;; extra = 答えに添える欄(担い手の行 :row)。
  (defn #^ dict no [#^ str s #^ NotReadyKind kind #^ str reason #^ (| dict None) #** extra] (| {"ok" False "state" s "kind" kind "reason" reason "job" job} extra))
  (when (is job None) (return (no "NotReady" NotReadyKind.NO-DECLARATION "宣言が無い")))
  (when (= job.replicas 0) (return (no "NotReady" NotReadyKind.NO-REPLICAS "replicas 0(置かない)")))
  (setv a (if (is placement None) (.row state.placements name) placement))
  (when (is a None)
    (setv unplaced (unplaced-kind now state job timing))
    (return (no "NotReady" (unplaced-not-ready unplaced) (+ "置き先が無い: " (unplaced-text unplaced job (effective-needs state job))))))
  (setv warming (< now (warm-until state timing))
        st (.row state.observations.statuses a.worker))
  ;; 担い手の報告が古い: 移し替えの期限(reassign-after-ms)の内なら「分からない」(Unknown — 途絶の間。Rollout は失敗と数えない)。
  ;; 期限を過ぎた担い手からは job を他へ移すので NotReady(2026-09-25: 以前は heartbeat が 10 秒途絶えただけで NotReady と言い、
  ;; 書き手が書き先へ書けているのに Rollout が戻しに入りえた)。
  ;; 途絶しても動かし続けてよい印をこの担い手に渡してある job(#2804 — ClusterState.keep-marks)は、期限を過ぎても Unknown: 担い手は
  ;; fence でも止めず、coordinator も約束の期限(kept-reassign-after-ms — 長い方の柵より後)までは他へ移さないので、process は動き続けて
  ;; いると考えられる(監視が止まりと判じない)。印の無い job は担い手が
  ;; fence で止めているので、期限の後は NotReady のまま(他に置ける worker が無ければ置き先は保ち、担い手が戻ると起こし直す)。
  ;; 印の在る job も、担い手は途絶が長い方の柵(ClusterTiming.keep-fence-ms)を越えたら止めるので、その後は NotReady。
  (when (or (is st None) (>= now (carrier-stale-from st timing)))
    (setv carrier (.row state.workers a.worker)
          silent (and carrier (alive now carrier timing.reassign-after-ms))
          mark (run (keep-mark-of state.keep-marks name))
          kept (and (is-not mark None) (= mark.worker a.worker) (is-not carrier None)
                    (alive now carrier timing.keep-fence-ms)))
    (return (no (if (or warming silent kept) "Unknown" "NotReady") NotReadyKind.CARRIER-SILENT
                (if kept
                    (.format "担い手 {} の報告が無い・古い — 途絶しても動かし続けてよい印を渡してあるので、process は動き続けていると考えられる(担い手が戻るか、Worker が消されるか、担い手の沈黙が約束の期限を越えるまで他へ移さない)"
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


(defn #^ (| str None) last-refusal [#^ ClusterState state #^ str name #^ str want]  ; defk にできない: coordinator の純粋な判断(handoff_policy.next-watch)が呼ぶ
  "入れ替えを諦めた記録(HandoffWatch.last-report — 表示)に、新の世代が「なぜ準備できないか」を添えるため: 今の宣言の spec(指紋 want)を
   名乗る送り手が送った最後の ReportReady(偽)の reason(業務の側が書く)。無ければ None。前の試行の reason も載せるよう世代の名で
   絞らないので、Ready かどうかの判断には使わない(判断は service-readiness — 置き先の process の報告だけを数える)。表を読むのは
   表の持ち主のこの module だけ(semgrep doeff-cluster-report-tables-are-read-by-their-owner)なので、handoff_policy から移した。"
  (next (gfor report (reversed (or (.row state.observations.readiness name) #()))
              :if (and (= report.origin.spec-hash want) (not report.ready))
              report.reason)
        None))


(defn #^ frozenset placed-senders [#^ ClusterState state #^ str name]
  ;; defk にできない: coordinator の純粋な判断(Program の外 — record-readiness・metrics_policy.record-metrics)が呼ぶ
  "Service name の置き先(いまの置き先と drain で並べた置き先)の担い手が、名 name の行で動かしていると最後に報告した process の
   送り手 #(worker 世代の名) の組。readiness の判定(running-process)が報告を数える相手の候補で、報告の表がこの送り手の報告を
   必ず残す(keep-report)。時刻は問わない(担い手の報告が古い間も、最後に報告された process を置き先の process とみなす)。"
  (frozenset (gfor a #((.row state.placements name) (.row state.surges name))
                   :if (is-not a None)
                   :setv row (job-status-row state a.worker name)
                   :if (and (is-not row None) row.instance)
                   #(a.worker row.instance))))


(defn #^ tuple keep-report [#^ (| tuple None) reports #^ (| ReadinessReport MetricsReport) report #^ frozenset placed]
  "報告 report を足した後の Service の報告の列(古い順・世代の名ごとに最新 1 つ)。送り手が置き先の process(placed — placed-senders の
   #(worker 世代の名))の報告は必ず残し、それ以外の送り手の報告は直近の REPORTS-KEPT 世代だけ残す — 置き先でない送り手(退いた世代の
   worker が起こし直した process など)がいくつ報告しても、置き先の報告を押し出さない(AL-D2o)。置き先でない報告も捨てずに残すのは、
   担い手の heartbeat より先に新しい process の報告が届いても、heartbeat が追いついた時にその報告を数えられるようにするため。"
  (setv rows (+ (lfor r (or reports #()) :if (!= r.origin.instance report.origin.instance) r) [report])
        others (lfor r rows :if (not-in #(r.origin.worker r.origin.instance) placed) r.origin.instance)
        dropped (frozenset (cut others 0 (max 0 (- (len others) REPORTS-KEPT)))))
  (tuple (gfor r rows :if (not-in r.origin.instance dropped) r)))


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
  (and (or (is job None) (= job.replicas 0)) (is (.row state.placements name) None)
       (not (still-live-somewhere now state name timing))))


;; --- 判断の期限(#3064・#3865)--------------------------------------------------------------
;; coordinator は、何も変えない歩の後、次の期限まで受付を待つ(wake_policy.next-wake)。下の 2 つは、状態がこのままで readiness の判定と止まりの判定が答えを変え得る最初の刻を、判定が比べに使う期限の値(上の
;; carrier-stale-from ほかと cluster_policy.liveness-deadline)から返す。返すのは答えを変え得る刻の下限(早めに試すのは安全・遅らせない)。

(defk readiness-due [state name now timing [placement None]]
  {:pre [(: state ClusterState) (: name str) (: now int) (: timing ClusterTiming) (: placement (| Placement None))]
   :post [(: % (| int None))] :tags {:context "coordinator" :role "judgment"}}
  "Service name の readiness の判定(service-readiness・running-process)が、状態がこのままで答え(Ready | NotReady | Unknown)を変え得る
   最初の刻を知るため — 入れ替えの見張り(api_policy.placement-due)・Rollout の相手の観測(wake_policy.rollout-due)・drain の並べ
   (cluster_policy.sweep-due)が呼ぶ。担い手の報告が新しい間は、報告が古くなる刻と準備の報告の window の期限。古い間は、起動の直後の
   猶予の終わりと、担い手の沈黙の窓 2 つ(移し替え・途絶の柵)。宣言・置き先が無い・replicas 0 の判定は時刻で変わらない(None)。
   placement = 見る置き先(既定 = いまの置き先 — running-process と同じ)。"
  (val job (next (gfor j state.jobs :if (= j.spec.name name) j) None))
  (val a (if (is placement None) (.row state.placements name) placement))
  (val st (if (is a None) None (.row state.observations.statuses a.worker)))
  (val carrier (if (is a None) None (.row state.workers a.worker)))
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
  "service-stopped が、状態がこのままで答えを変え得る最初の刻を知るため(Rollout の Service の相手の stopped — wake_policy.rollout-due)。
   宣言が無いか replicas 0 で置き先も無い Service の行を載せた worker が、沈黙で母集団(cluster_policy.service-rows の lease-ms の窓)から
   外れる刻の最小。それ以外は時刻で変わらない(None)。"
  (val job (next (gfor j state.jobs :if (= j.spec.name name) j) None))
  (if (and (or (is job None) (= job.replicas 0)) (is (.row state.placements name) None))
      (min (gfor #(wname st) (.items state.observations.statuses)
                 :setv w (.row state.workers wname)
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
    ;; 同じ名の前の世代が動かしている間、新しい世代は準備と検めを揃えて待つ(AL-D2f)— 世代の入れ替えの正常な途中。
    JobPhase.ELDER-RUNS VersionState.UPDATING
    JobPhase.STOPPING VersionState.UPDATING
    ;; 担い手がまだ宣言を受け取っていない。
    JobPhase.STOPPED VersionState.UPDATING
    ;; 実行環境の準備の失敗: 一時の失敗は撃ち直しを待つ途中・再試行しない失敗は待っても進まない。
    JobPhase.ENV-FAILED (if retryable VersionState.UPDATING VersionState.BLOCKED)
    JobPhase.CODE-FAILED VersionState.BLOCKED
    JobPhase.PROBE-FAILED VersionState.BLOCKED
    ;; 落ちて起こし直している(1 回落ちただけでも Blocked・設計 v3 の戻せる決定)。続けて落ちた回数は Service の status.failures の欄(#3477)。
    JobPhase.BACKOFF VersionState.BLOCKED
    JobPhase.STOP-UNCONFIRMED VersionState.BLOCKED
    ;; service の process が終わった — 想定の外。
    JobPhase.FINISHED VersionState.BLOCKED
    ;; 入れ替えを諦めた(worker の側から見た同じ事実 — 見張りの Abandoned)。
    JobPhase.HANDOFF-ABANDONED VersionState.BLOCKED
    ;; 機体の memory の見張りに続けて止められ、worker が起こし直しを止めた(宣言が変わるまで進まない)。
    JobPhase.MEMORY-KILLED VersionState.BLOCKED
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
  (setv refused (.row state.refused name))
  (when (is-not refused None)
    (return (VersionVerdict :state VersionState.BLOCKED :reason (+ "宣言を受け付けていない: " refused.reason))))
  (when (service-stopped state name now timing)
    (return (VersionVerdict :state VersionState.STOPPED :reason "止めている")))
  (setv proc (running-process state name now timing)
        watch (.row state.handoffs name)
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
  "POST /resources/Service/<名>/readiness: Service name の準備の報告を観測の表 readiness の名の行へ足すため(世代ごとに最新 1 つ・
   送り手が置き先の process の報告は必ず残す — keep-report・placed-senders)。表は保存しないが Service の status.ready の材料なので、
   版の比べ(dirty-keys)が読む。"
  (when (not (any (gfor j state.jobs (= j.spec.name name))))
    (refuse 404 (+ "無い Service: " name)))
  (setv report (run (readiness-report body now))
        seen state.observations)
  (replace state :observations
           (replace seen :readiness (.with-writes seen.readiness
                                                  #((TableWrite name (keep-report (.row seen.readiness name) report
                                                                                  (placed-senders state name))))))))


;; --- 資源の写し(版と記録の比べる単位) ------------------------------------------------------------

(defn #^ dict service-spec [#^ ClusterJob job]
  (dfor #(k v) (.items (job-to-json job)) :if (!= k "name") k v))


(defn #^ dict service-row [#^ ClusterState state #^ ClusterJob job #^ int now #^ ClusterTiming timing]
  "宣言した Service 1 つの {spec status}(snapshot の行)。"
  (setv a (.row state.placements job.spec.name)
        carrier (if a (job-status-row state a.worker job.spec.name) None)
        failures (if (is carrier None) None carrier.failures)
        last-exit (if (is carrier None) None carrier.last-exit-at-ms))
  {"spec" (service-spec job)
   "status" (| {"worker" (if a a.worker None) "placement" (if a a.generation None)
                "ready" (get (service-readiness state job.spec.name now timing) "state")
                ;; 版の判定の状態(2026-09-29 — ready と同じく、変わった時に出来事と resourceVersion を進める)。理由と動いている
                ;; 版の列は変わりやすい観測なので入れない(observed-of が組む)。
                "version" {"state" (. (version-state state job.spec.name now timing) state value)}}
               ;; 続けて落ちた回数(#3477)。置き先の担い手の行が 1 以上を報告している間だけ載せる — 落ちるたびと、安定して 0 に戻った時に
               ;; 出来事と resourceVersion が進み、版の変化の待ち(GET /watch)が起きる。無い Service の status の形・版は以前と同じ。
               (if failures {"failures" failures} {})
               ;; 最後に終わったと知れた刻(#3672)。置き先の担い手の行が刻を持つ間だけ載せる — process が終わるたびと worker の世代の
               ;; 入れ替わり(cluster_policy.worker-report が新しい世代の起動の刻まで進める)に出来事と resourceVersion が進み、版の変化の待ち
               ;; (GET /watch)が刻だけの変化にも起きる。heartbeat ごとには替わらない(同じ世代の拍では刻は前のまま)。無い Service の
               ;; status の形・版は以前と同じ。
               (if (is-not last-exit None) {"lastExitAtMs" last-exit} {})
               ;; drain で並べた置き先(2026-09-25)。在る間だけ載せる(無い Service の status の形・版は以前と同じ)。
               (if (is-not (.row state.surges job.spec.name) None)
                   {"surge" (. (.row state.surges job.spec.name) worker)}
                   {})
               ;; 入れ替えの期限の見張り(2026-09-26 — handoff_policy)。Ready を待つ間と諦めた間だけ載せる: 段・起点・期限、
               ;; 諦めたなら時刻と理由(期限と最後の NotReady の理由)と新の世代の最後の ReportReady(偽)の reason。
               (if (setx handoff-watch (.row state.handoffs job.spec.name))
                   {"handoff" (.status-json handoff-watch (handoff-timeout-ms job.readiness))}
                   {})
               ;; 置き先の一時の上書き(2026-10-27・card ki-1523bc0aba4f — 資源 NeedsOverride)。在る間だけ載せる: 効いている needs・訳・期限・
               ;; 書き手。宣言の spec.needs は書き換えないので、今効く needs はこの欄で読む。置く・外す拍に出来事と resourceVersion が進む。
               (if (setx override (.row state.needs-overrides job.spec.name))
                   {"needsOverride" (needs-override-spec override)}
                   {}))})


(defn #^ dict refused-row [#^ ClusterState state #^ RefusedJob r #^ int now #^ ClusterTiming timing]
  "受け付けない Service の行(改訂 1 の C): spec は元の行のまま・status に理由。版の判定は DELETE で消すまで Blocked。"
  {"spec" (dfor #(k v) (.items r.row) :if (!= k "name") k v)
   "status" {"refused" r.reason "version" {"state" (. (version-state state r.name now timing) state value)}}})


(defn #^ dict ready-instances [#^ ClusterState state #^ str worker #^ int now #^ ClusterTiming timing]
  "worker に割り当てた入れ替え(handoff)の Service の名 → Ready と数えている process の世代の名(Ready でなければ None)。
   worker はこれが新しい process の世代の名になった後に、退いた旧い process を止める。"
  (dfor job state.jobs
        :setv a (.row state.placements job.spec.name)
        :if (and a (= a.worker worker) job.spec.handoff)
        job.spec.name
        (ready-instance state job.spec.name now timing)))


(defn #^ (| str None) ready-instance [#^ ClusterState state #^ str name #^ int now #^ ClusterTiming timing]
  "Service name の走っている process の世代の名(Service が Ready と数えられている時だけ・そうでなければ None)。"
  (setv proc (running-process state name now timing))
  (if (and (get proc "ok") (= (get (service-readiness state name now timing) "state") "Ready"))
      (get proc "instance")
      None))


(deff handover-of [#^ ClusterState state #^ WorkerInfo w #^ int now #^ ClusterTiming timing]  ; defk にできない: coordinator の純粋な判断(Program の外 — worker-row)が呼ぶ
  {:pre [(: state ClusterState) (: w WorkerInfo) (: now int) (: timing ClusterTiming)] :post [(: % (| dict None))]
   :tags {:context "coordinator" :role "judgment"}}
  "worker w の世代をまたぐ recreate の job の受け渡しの姿(AL-D2f): elder = 前の世代がまだ動かしている job・ready = 今の世代が
   起こせると報告した job・leaving = 前の世代の Pod の止まりが始まった(AL-D2k — 受け渡しはここから。印が付いた拍で版を進め、前の世代の
   名指しの待ちを起こす)。前の世代が動かしている job が無ければ None。
   受け渡しを止めている間(card ki-d8e594fe934d — heartbeat の返事の handoverPending と同じ判断 cluster_policy.handover-blockers)は、
   blocking = 止めている job ごとの名・入れ替えの形・今の世代の最後の結末、sinceMs = 待ちの始まりの刻、stalled = 始まりから
   HANDOVER-STALL-MS を越えた(読み手の知らせの材料 — 計器 doeff_worker_handover_stalled と同じ値)を載せる。"
  (setv runs (tuple (gfor r state.superseded-runs :if (and r.overlapped (= r.worker w.name)) r))
        elder (sorted (sfor r runs job r.jobs job)))
  (when (not elder)
    (return None))
  (setv report (.row state.observations.statuses w.name)
        ready (sorted (sfor row (if (is report None) #() report.jobs) :if (= row.phase "elder-runs") row.name))
        blockers (if (is w.boot None)
                     #()
                     (handover-blockers state w.name w.boot now timing (ready-instances state w.name now timing)))
        since (if blockers (handover-since state w.name w.boot now timing) None))
  (| {"elder" elder "ready" ready "leaving" (any (gfor r runs r.leaving))
      "stalled" (and (is-not since None) (>= (- now since) HANDOVER-STALL-MS))}
     (if blockers {"blocking" (lfor b blockers (blocker-json b))} {})
     (if (is-not since None) {"sinceMs" since} {})))


(defn #^ dict blocker-json [#^ HandoverBlocker b]
  "受け渡しを止めている job 1 つの status.handover.blocking の行(結末の欄は値の在る物だけ — detail は長さ BLOCKER-DETAIL-CHARS で切る)。"
  (| {"job" b.job "update" b.update}
     (if (is-not b.phase None) {"phase" b.phase} {})
     (if (is-not b.failures None) {"failures" b.failures} {})
     (if (is-not b.last-exit-code None) {"lastExitCode" b.last-exit-code} {})
     (if b.detail {"detail" (cut b.detail 0 BLOCKER-DETAIL-CHARS)} {})))


;; status.handover.blocking の detail の長さの上限(字)。worker の detail は落ちた訳の 1 行(例外の名と文)で、資源の行の版の比べと
;; 出来事の記録に入る — 長い traceback を行ごとに運ばない。
(setv BLOCKER-DETAIL-CHARS 300)


(defn #^ dict worker-row [#^ ClusterState state #^ WorkerInfo w #^ int now #^ ClusterTiming timing]
  "worker 1 つの {spec status}。now = 温める表の行のうち、この worker に当たる行(期限の内)を選ぶ刻と、受け渡しの止まりを数える刻
   (timing の lease で前の世代の沈黙を数える — handover-of)。"
  {"spec" {"provides" (list w.provides) "exclusive" (list w.exclusive) "node" w.node "capacity" w.capacity "taskReserve" w.task-reserve
           "versions" (dict w.versions)}
   ;; 生きているか(#1934 — heartbeat が lease の内)。生死の切り替わりの拍で版が進み、出来事の記録に 1 行残る — 名簿を写す呼び手が
   ;; 版の変化の待ち(GET /watch・AwaitRunnersChange)で worker の死と戻りに即座に起きるため。最後の連絡の時刻そのものは変わりやすい
   ;; 観測なので入れない(生きている間の heartbeat では版は進まない)。
   ;; drain(2026-09-25)の始まりと頼み手(誰が・いつ空けさせたかを出来事の記録に残す)。期限は頼み直すたびに延びるので入れない。
   "status" (| {"live" (not-in w.name state.silent)}
               (if (is-not (.row state.drains w.name) None)
                   {"drain" {"sinceMs" (. (.row state.drains w.name) since-ms) "actor" (. (.row state.drains w.name) actor)}}
                   {})
               ;; 実行環境の root の組みの進み(#3668 (b)・2026-10-06)— heartbeat が名乗る準備済み・準備中・失敗の root のキー。組みが進んだ拍に
               ;; 版と出来事が進み、組みの完成を待つ送り手(AwaitWarm)が GET /watch で起きる。名乗る root が在る間だけ載せる(無い worker の
               ;; status の形・版は以前と同じ)。失敗の理由の文(detail)は揺れるので載せない — 種類と一時かだけ。
               (if (or w.env-ready w.env-preparing w.env-failed)
                   {"env" {"ready" (sorted w.env-ready) "preparing" (sorted w.env-preparing)
                           "failed" (sorted (lfor f w.env-failed {"key" f.key "kind" f.kind "retryable" f.retryable})
                                            :key (fn [row] #((get row "key") (get row "kind"))))}}
                   {})
               ;; この worker に当たる温める表の行の鍵(#3871 の後 — heartbeat の返事の warm と同じ選び方 cluster_policy.warms-for)。行の
               ;; 書きと消しで版が進み、この worker を名指した GET /watch が起き、worker は次の heartbeat を待たずに準備を始める
               ;; (watch_policy.worker-mark)。期限は頼み直すたびに延びるので入れない — 期限の延長だけでは版も見え方も変わらない。
               ;; 当たる行が在る間だけ載せる(無い worker の status の形・版は以前と同じ)。
               ;; disk の条件(heartbeat の envCapacity — near = 準備の最低に近い・exhausted = 最低を割った)。ok でない間だけ載せる(ok の worker の
               ;; status の形・版は以前と同じ)。切り替わりの拍で版が進み、読み手が GET /resources/Worker で予告を読む。
               (if (!= w.env-capacity "ok") {"envCapacity" w.env-capacity} {})
               ;; ServiceAccount の token の状態(heartbeat の accountToken — stale = kubelet の置き直しが遅れた・expired = 期限が切れた・
               ;; card ki-d31166f84ad0)。envCapacity と同じく ok でない間だけ載せ、切り替わりの拍で版が進む(読み手が 401 の前に知る)。
               (if (!= w.account-token "ok") {"accountToken" w.account-token} {})
               ;; 枠の file の状態(heartbeat の roomFile — unreadable・malformed の間 worker は前に名乗った枠のまま・card ki-6828eda880ce)。
               ;; 同じく ok でない間だけ載せる(読み手が枠の変更が効いていない事を知る)。
               (if (!= w.room-file "ok") {"roomFile" w.room-file} {})
               ;; 世代をまたぐ recreate の job の受け渡し(AL-D2f): 前の世代がまだ動かしている job(重なりを名乗った新しい世代の間の
               ;; SupersededRun)と、今の世代が起こせると報告した job(状態の行の phase が elder-runs)。受け渡しが進むたびに版が進み、
               ;; この worker を名指した 2 つの世代の GET /watch が起きる(前の世代は外す返事を、今の世代は印の外れを次の heartbeat を
               ;; 待たずに受ける — watch_policy.worker-mark)。受け渡しの間だけ載せる(無い worker の status の形・版は以前と同じ)。
               (if (setx handover (handover-of state w now timing)) {"handover" handover} {})
               (if (setx offers (warms-for state w.name now))
                   {"warm" (sorted (gfor o offers o.key))}
                   {}))})


(defn #^ dict task-row [#^ TaskRecord t]
  "task 1 つの {spec status}。"
  {"spec" (| {"name" t.name "revision" t.revision "needs" (list t.needs)}
             (if t.detached {"key" t.key} {}))
   "status" {"phase" t.phase "worker" t.worker "detail" t.detail}})


(defn #^ dict rollout-row [#^ RolloutRow r]
  "Rollout 1 つの {spec status}。"
  {"spec" (rollout-spec-to-json r.spec) "status" (rollout-status-to-json r.status)})


(deff group-limit-row [#^ GroupLimit limit]  ; defk にできない: 版の比べ(snapshot・row-of — Program の外の純粋な判断)が呼ぶ
  {:pre [(: limit GroupLimit)] :post [(: % dict)] :tags {:context "coordinator" :role "judgment"}}
  "組の上限 1 つの資源の行 {spec status} を綴るため(status は空 — 置いている数は拍ごとに動くので版に入れず、GET /state の groupLimits
   に出す)。"
  {"spec" (group-limit-spec limit) "status" {}})


(defn #^ dict snapshot [#^ ClusterState state #^ int now #^ ClusterTiming timing]
  "資源ごとの {spec status}。比べて版を進める単位。変わりやすい観測(生存の時刻・lease の期限)は入れない。同じ名の宣言と受け付けない
   行が両方在れば受け付けない行(後に書く)が勝つ — row-of と同じ順。"
  (| (dfor job state.jobs (key-of "Service" job.spec.name) (service-row state job now timing))
     (dfor r (.rows state.refused) (key-of "Service" r.name) (refused-row state r now timing))
     (dfor w (.rows state.workers) (key-of "Worker" w.name) (worker-row state w now timing))
     (dfor t (.rows state.tasks) (key-of "Task" t.id) (task-row t))
     (dfor #(name r) (.items state.rollouts) (key-of "Rollout" name) (rollout-row r))
     (dfor #(name l) (.items state.group-limits) (key-of "GroupLimit" name) (group-limit-row l))
     (dfor o (.rows state.needs-overrides) (key-of "NeedsOverride" o.service) (needs-override-row o))))


(defn #^ (| dict None) row-of [#^ ClusterState state #^ dict jobs #^ str key #^ int now #^ ClusterTiming timing]
  "鍵 1 つの snapshot の行(資源が無ければ None)。jobs = 宣言の名 → ClusterJob(呼び手が 1 度だけ作る)。"
  (setv #(kind name) (split-key key))
  (match kind
    "Service" (cond (setx refused-job (.row state.refused name)) (refused-row state refused-job now timing)
                    (in name jobs) (service-row state (get jobs name) now timing)
                    True None)
    "Worker" (if (setx worker (.row state.workers name)) (worker-row state worker now timing) None)
    "Task" (if (setx task (.row state.tasks name)) (task-row task) None)
    "Rollout" (if (setx rollout (.row state.rollouts name)) (rollout-row rollout) None)
    "GroupLimit" (if (setx limit (.row state.group-limits name)) (group-limit-row limit) None)
    "NeedsOverride" (if (setx override (.row state.needs-overrides name)) (needs-override-row override) None)
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
  (setv st (.row state.observations.statuses worker))
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
   - Worker: 記録・沈黙の集合の出入り・drain / 温める表の行の出入りと書き換え(全 worker — 当たる行は能力で決まる・まれ)/
     世代をまたぐ受け渡し(退いた世代の組と、組の在る worker の報告 — AL-D2f)。
     Task・Rollout・GroupLimit・NeedsOverride: 自分の行(上書きが Service の needs を書き換えた時は、Service の宣言の変化として上で拾う)。"
  (setv names-moved (| (moved-names jobs-before jobs-after) (moved-names before.placements after.placements)
                       (moved-names before.surges after.surges) (moved-names before.handoffs after.handoffs)
                       (moved-names before.observations.readiness after.observations.readiness)
                       (moved-names before.refused after.refused)
                       ;; 置き先の一時の上書き(Service の status.needsOverride と置ける worker — 名ごと)。
                       (moved-names before.needs-overrides after.needs-overrides)
                       ;; 途絶しても動かし続けてよい印の約束(#2804 — 担い手の沈黙の後の ready の材料)。
                       (frozenset (gfor m (+ before.keep-marks after.keep-marks)
                                  :if (or (not-in m before.keep-marks) (not-in m after.keep-marks))
                                  m.job)))
        workers-moved (| (moved-names before.workers after.workers) (moved-names before.observations.statuses after.observations.statuses))
        all-services (| (frozenset jobs-before) (frozenset jobs-after) (frozenset (.keys before.refused)) (frozenset (.keys after.refused)))
        global-moved (or (!= before.started-ms after.started-ms) (is-not before.drains after.drains))
        carried (frozenset (gfor state #(before after)
                                 placed #((.items state.placements) (.items state.surges))
                                 #(name p) placed
                                 :if (in p.worker workers-moved)
                                 name))
        reported (frozenset (gfor state #(before after) w workers-moved name (status-row-names state w) name))
        unplaced (if workers-moved
                     (frozenset (gfor name all-services :if (or (is (.row before.placements name) None) (is (.row after.placements name) None)) name))
                     (frozenset))
        services (if global-moved all-services (| names-moved carried reported unplaced))
        silent-moved (^ (frozenset before.silent) (frozenset after.silent))
        ;; 世代をまたぐ受け渡し(AL-D2f — worker-row の handover): 退いた世代の組か、今の世代の報告が変わった worker。
        handover-moved (| (frozenset (gfor r (^ (set before.superseded-runs) (set after.superseded-runs)) r.worker))
                          (frozenset (gfor n (moved-names before.observations.statuses after.observations.statuses)
                                           :if (any (gfor r after.superseded-runs (= r.worker n)))
                                           n)))
        workers (| (moved-names before.workers after.workers) (moved-names before.drains after.drains) silent-moved handover-moved
                   (if (moved-names before.warms after.warms) (| (frozenset (.keys before.workers)) (frozenset (.keys after.workers))) (frozenset)))
        moved (| (frozenset (gfor n services (key-of "Service" n)))
                 (frozenset (gfor n workers (key-of "Worker" n)))
                 (frozenset (gfor n (.changed-keys before.tasks after.tasks) (key-of "Task" n)))
                 (frozenset (gfor n (moved-names before.rollouts after.rollouts) (key-of "Rollout" n)))
                 (frozenset (gfor n (moved-names before.group-limits after.group-limits) (key-of "GroupLimit" n)))
                 (frozenset (gfor n (moved-names before.needs-overrides after.needs-overrides) (key-of "NeedsOverride" n))))
        present (| (frozenset (gfor n all-services :if (or (in n jobs-after) (is-not (.row after.refused n) None)) (key-of "Service" n)))
                   (frozenset (gfor n (.keys after.workers) (key-of "Worker" n)))
                   (frozenset (gfor n (.keys after.tasks) (key-of "Task" n)))
                   (frozenset (gfor n (.keys after.rollouts) (key-of "Rollout" n)))
                   (frozenset (gfor n (.keys after.group-limits) (key-of "GroupLimit" n)))
                   (frozenset (gfor n (.keys after.needs-overrides) (key-of "NeedsOverride" n))))
        unversioned (frozenset (gfor k present :if (is (.row after.meta k) None) k)))
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
        ;; 版を進める資源(行が変わった・版の記録が無い adopt・消えたが記録の残る delete)。0 件なら after をそのまま返す
        ;; (meta・audit を作り直さない — 保存の diff が欄を `is` で比べて組を飛ばせる)。
        pending (lfor key (sorted keys)
                      :setv old (.get b key)
                      :setv new (.get a key)
                      :if (not (and (= old new) (or (is new None) (is-not (.row after.meta key) None))))
                      key))
  (when (not pending) (return after))
  (setv meta (draft-of after.meta) audit (list after.audit) rev after.revision seq after.audit-seq)
  (for [key pending]
    (setv old (.get b key) new (.get a key))
    (+= rev 1)
    (+= seq 1)
    (setv #(kind name) (split-key key) current (.row meta key))
    (cond
      (is new None)
        (do (.remove meta key)
            (setv verb "delete" from (if current current.resource-version None) to None
                  generation (if current current.generation None)))
      (or (is old None) (is current None))
        (do (setv verb (if (is old None) "create" "adopt") from None to rev generation 1)
            (.put meta key (ResourceMeta :resource-version rev :generation 1 :created-by actor :created-ms now
                                         :updated-by actor :updated-ms now)))
      True
        (do (setv spec-changed (!= (get old "spec") (get new "spec"))
                  verb (if spec-changed "update" "status") from current.resource-version to rev
                  generation (+ current.generation (if spec-changed 1 0)))
            (.put meta key (replace current :resource-version rev :generation generation :updated-by actor :updated-ms now))))
    (.append audit (AuditEvent :seq seq :at now :actor actor :verb verb :kind kind :name name
                               :from-version from :to-version to :generation generation
                               :changes (changed-fields old new))))
  (replace after :meta (.freeze meta) :revision rev :audit (trim-audit audit) :audit-seq seq))


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
      (do (setv a (.row state.placements name) reports (.row state.observations.readiness name))
          (ServiceObserved :ready-reason (get (service-readiness state name now timing) "reason")
                           :last-readiness (if reports (get reports -1) None)
                           :process (if a (job-status-row state a.worker name) None)
                           ;; 版の判定(state は snapshot と同じ値)と、その理由・動いている版の列(2026-09-29)。
                           :version (version-state state name now timing)
                           :running (live-processes state name now timing)
                           ;; 置き先が無ければ種類・足りない能力・いつから(card ki-53b8677f1374 — 外の見張りが Service の一覧から読む)。
                           :unplaced (let [job (next (gfor j (active-jobs state) :if (= j.spec.name name) j) None)]
                                       (if (and (is a None) (is-not job None)) (unplaced-view now state job timing) None))))
    (= kind "Worker")
      (do (setv w (.row state.workers name))
          (when (is w None) (raise (ValueError (+ "無い Worker を観測しようとした: " name))))
          (WorkerObserved :silent-ms (- now w.last-seen-ms) :alive (alive now w timing.lease-ms)))
    (= kind "Task")
      (do (setv task (.row state.tasks name))
          (when (is task None) (raise (ValueError (+ "無い Task を観測しようとした: " name))))
          (TaskObserved :task task))
    (= kind "Rollout")
      (RolloutObserved :observed (tuple (gfor t (rollout-targets (. (.row state.rollouts name) spec))
                                              :if (= t.kind "Deployment")
                                              (ObservedDeployment :key (target-key t)
                                                                  :seen (.row state.observations.deployments
                                                                              (+ t.namespace "/" t.name))))))
    True None))


(defn #^ ResourceView resource-view [#^ ClusterState state #^ str key #^ dict snap #^ int now #^ ClusterTiming timing]
  "資源 1 つの画面(JSON は coordinator/protocol/replies が綴る — #2595)。"
  (setv #(kind name) (split-key key) row (get snap key))
  (ResourceView :kind kind :name name :meta (.row state.meta key) :spec (get row "spec") :status (get row "status")
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


;; --- 書きの口(Service・Rollout・GroupLimit)---------------------------------------------------

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


(defn #^ GroupLimit checked-group-limit [#^ str name #^ (| dict None) spec]
  "資源 GroupLimit の作り・書き直しの spec を、系の構成子と同じ規則(group-limit-refusal)で検めて GroupLimit にするため(受けられなければ 400)。"
  (setv refusal (group-limit-refusal name spec))
  (when (is-not refusal None) (refuse 400 refusal))
  (group-limit-of name spec))


;; --- Service の needs の一時の上書き(資源 NeedsOverride — 2026-10-27・card ki-1523bc0aba4f)-------------------------------

;; 上書きの期限の上限(秒 — 7 日)。上書きは復旧の間だけの物で、期限の無い上書き(git の宣言の外に残り続ける置き先)を受けない。
(setv NEEDS-OVERRIDE-MAX-SECONDS (* 7 24 3600))
;; 作る時の spec の欄(この外の欄は断る — 書き手と刻は coordinator が決める)。
(setv NEEDS-OVERRIDE-KEYS (frozenset #("needs" "reason" "ttlSeconds")))
;; 訳の長さの上限(字)。訳は出来事の記録と資源の行に載る 1 行。
(setv NEEDS-OVERRIDE-REASON-CHARS 200)


(deff needs-override-spec [#^ NeedsOverride override]  ; defk にできない: 版の比べ(snapshot・row-of)と保存の綴り(state file・durable KV — Program の外)が呼ぶ純粋な綴り
  {:pre [(: override NeedsOverride)] :post [(: % dict)] :tags {:context "coordinator" :role "judgment"}}
  "上書き 1 つの JSON の形(資源の行の spec・保存の値・GET の答えで同じ形 — 読みは state_json.needs-override-from-json)を 1 か所で綴るため。"
  {"needs" (list override.needs) "reason" override.reason "expiresAtMs" override.expires-ms "writer" override.writer
   "sinceMs" override.since-ms})


(deff needs-override-row [#^ NeedsOverride override]  ; defk にできない: 版の比べ(snapshot・row-of — Program の外の純粋な判断)が呼ぶ
  {:pre [(: override NeedsOverride)] :post [(: % dict)] :tags {:context "coordinator" :role "judgment"}}
  "上書き 1 つの資源の行 {spec status} を綴るため(status は空 — 外れたかどうかは行の有無と出来事の記録で表す)。"
  {"spec" (needs-override-spec override) "status" {}})


(defk needs-satisfiable [state needs now timing]
  {:pre [(: state ClusterState) (: needs tuple) (: now int) (: timing ClusterTiming)] :post [(: % bool)]
   :tags {:context "coordinator" :role "judgment"}}
  "needs を満たす(置き方と同じ規則 placeable — provides ∪ derived と専用の能力)生きた worker が在るかを知るため。上書きを作れるか
   (作る時)と、外すか(調停の拍)の両方がこの 1 つで判じる。"
  (any (gfor w (.rows state.workers) (and (alive now w timing.lease-ms) (placeable needs w)))))


(defk needs-overrides-lifted [state overrides]
  {:pre [(: state ClusterState) (: overrides tuple)] :post [(: % ClusterState)] :tags {:context "coordinator" :role "judgment"}}
  "上書き overrides(NeedsOverride の tuple)の行を外した状態を作るため(期限・満たす worker の消え・DELETE の 3 つの外し方が同じこの 1 つを
   通る)。宣言は書き換えていないので、外した拍から置き先の判断は宣言の needs を読む(cluster_policy.effective-needs)。"
  (replace state :needs-overrides (.with-writes state.needs-overrides (tuple (gfor o overrides (TableWrite o.service None))))))


(defk lift-needs-overrides [state now timing]
  {:pre [(: state ClusterState) (: now int) (: timing ClusterTiming)] :post [(: % ClusterState)] :tags {:context "coordinator" :role "judgment"}}
  "調停の拍ごとに(api_policy.settle が置き方の前に呼ぶ)、期限の来た上書きと、上書きの needs を満たす生きた worker が 0 になった上書き
   (worker が作り直されて能力を名乗らなくなった拍)を外し、同じ拍の置き方を宣言の needs で行わせるため。以前の直の PUT の書き換えは
   能力の側だけが戻ると Service を置き先の無いまま残した(2026-10-26 22:45〜23:21 の 13 本)。外した行は出来事の記録に送り手 coordinator の
   delete として残る。外す物が無ければ state をそのまま返す(同じ物 — 版の比べと保存の差分を動かさない)。"
  (var lifted #())
  (for [o (.rows state.needs-overrides)]
    (<- held bool (needs-satisfiable state o.needs now timing))
    (when (or (>= now o.expires-ms) (not held))
      (:= lifted (+ lifted #(o)))))
  (if lifted
      (! (needs-overrides-lifted state lifted))
      state))


(defk needs-override-due [state]
  {:pre [(: state ClusterState)] :post [(: % (| int None))] :tags {:context "coordinator" :role "judgment"}}
  "上書きの外しが、状態がこのままで答えを変え得る最初の刻を知るため(api_policy.tick-due — 静かな区間でも期限の拍に起きる)。期限の最小。
   満たす worker の消えは worker の沈黙(liveness-due)か heartbeat で起きる拍が拾う。上書きが無ければ None。"
  (min (gfor o (.rows state.needs-overrides) o.expires-ms) :default None))


(defk placement-rewrite-refusal [name current job]
  {:pre [(: name str) (: current ClusterJob) (: job ClusterJob)] :post [(: % (| str None))] :tags {:context "coordinator" :role "judgment"}}
  "Service name の宣言 current を job へ書き換える要求が、版(spec.revision)を変えずに置き先(needs か pin)だけを変えるなら断る理由
   (受けられれば None)を知るため。宣言の道具は revision に宣言の版を入れるので、置き先を変える宣言は新しい revision になって通る。
   版を変えない置き先の書き換えは宣言の外の手の書き換え(2026-10-26 に復旧の手が 13 本の needs を書き換え、元の値が残らなかった)で、
   一時の置き先は宣言を書き換えない記録 NeedsOverride で書く。送り手の名乗りは照らさない(2026-10-05 の決め)。"
  (if (or (!= job.spec.revision current.spec.revision) (and (= job.needs current.needs) (= job.pin current.pin)))
      None
      (.format (+ "Service {} の置き先(needs {} → {}・pin {} → {})を、版(revision {})を変えずに書き換える要求は受けない — 宣言を変えるなら"
                  "新しい revision で宣言し直す。一時に置き先を変えるなら、宣言を書き換えない記録 NeedsOverride を作る: POST /resources/NeedsOverride "
                  "{{\"name\": \"{}\", \"spec\": {{\"needs\": [...], \"reason\": \"<訳>\", \"ttlSeconds\": <秒>}}}}"
                  "(期限の拍か、上書きの needs を満たす生きた worker が 0 になった拍に、宣言の needs で置き直す)")
               name (list current.needs) (list job.needs) current.pin job.pin current.spec.revision name)))


(defk create-needs-override [state body actor now timing]
  {:pre [(: state ClusterState) (: body ResourceBody) (: actor str) (: now int) (: timing ClusterTiming)] :post [(: % ClusterState)]
   :tags {:context "coordinator" :role "judgment"}}
  "POST /resources/NeedsOverride を受けるため: Service body.name の置き先を、宣言を書き換えずに spec の needs へ一時に向ける記録(訳・期限・
   書き手)を置く。断る物: spec の形の誤り・空の needs・訳の無い / 長すぎる・期限が 1 秒〜NEEDS-OVERRIDE-MAX-SECONDS の外(400)・Service が
   無い(404)・同じ Service の上書きが在る・上書きの needs を満たす生きた worker が今 0(409 — 作っても次の拍で外れる)。"
  (val name body.name)
  (val spec body.spec)
  (when (not (isinstance spec dict))
    (refuse 400 "NeedsOverride の spec は object {\"needs\": [...], \"reason\": \"<訳>\", \"ttlSeconds\": <秒>}"))
  (val unknown (sorted (- (frozenset spec) NEEDS-OVERRIDE-KEYS)))
  (when unknown
    (refuse 400 (.format "NeedsOverride の spec の知らない欄: {}(書けるのは needs・reason・ttlSeconds — 書き手と刻は coordinator が持つ)"
                         unknown)))
  (val needs (capabilities-of (.get spec "needs") "NeedsOverride の needs"))
  (val reason (.get spec "reason"))
  (val ttl (.get spec "ttlSeconds"))
  (when (not needs) (refuse 400 "NeedsOverride の needs が空 — 上書きの間に要る能力の名を 1 つ以上書く"))
  (when (not (and (isinstance reason str) (< 0 (len (.strip reason)) (+ NEEDS-OVERRIDE-REASON-CHARS 1)) (not-in "\n" reason)))
    (refuse 400 (.format "NeedsOverride の reason({!r})は上書きの訳の 1 行(1〜{} 字)" reason NEEDS-OVERRIDE-REASON-CHARS)))
  (when (not (and (isinstance ttl int) (not (isinstance ttl bool)) (<= 1 ttl NEEDS-OVERRIDE-MAX-SECONDS)))
    (refuse 400 (.format "NeedsOverride の ttlSeconds({!r})は 1〜{} の整数 — 上書きは期限の拍に外れる" ttl NEEDS-OVERRIDE-MAX-SECONDS)))
  (when (not (any (gfor j state.jobs (= j.spec.name name))))
    (refuse 404 (+ "無い Service(NeedsOverride の名は上書きする Service の名): " (str name))))
  (when (is-not (.row state.needs-overrides name) None)
    (refuse 409 (.format "Service {} の NeedsOverride はもう在る(延ばす・変えるなら DELETE /resources/NeedsOverride/{} の後に作り直す)" name name)))
  (<- held bool (needs-satisfiable state needs now timing))
  (when (not held)
    (refuse 409 (.format "needs {} を満たす生きた worker が無い — 上書きしても置き先が無く、次の拍で外れる(先に worker に能力を名乗らせる)"
                         (list needs))))
  (replace state :needs-overrides (.with-writes state.needs-overrides
                                                #((TableWrite name (NeedsOverride :service name :needs needs :reason (.strip reason)
                                                                                  :expires-ms (+ now (* ttl 1000)) :writer actor
                                                                                  :since-ms now))))))


(defn #^ ClusterState create-resource [#^ ClusterState state #^ str kind #^ (| ResourceBody ServiceBody) body #^ str actor #^ int now]
  ;; 包みの形(name は文字列・spec は object)と Service の宣言の行は、本文を解く所(coordinator/protocol/request_bodies — #2445・#2448)が読んだ。
  (setv name body.name)
  (when (not (and name (not-in "/" name))) (refuse 400 (.format "名前が正しくない: {!r}" name)))
  (cond
    (isinstance body ServiceBody)
      (do (when (any (gfor j state.jobs (= j.spec.name name))) (refuse 409 (+ "もう在る Service: " name)))
          (setv job (replace body.job :owner (or (valid-actor body.owner) actor)))
          ;; 受け付けない行(RefusedJob)と同じ名なら、新しい形の宣言で置き換える(改訂 1 の C)。
          (replace state :jobs (+ state.jobs #(job)) :refused (.with-writes state.refused #((TableWrite name None)))))
    (= kind "Rollout")
      (do (when (is-not (.row state.rollouts name) None) (refuse 409 (+ "もう在る Rollout: " name)))
          (setv raw (dict (or body.spec {}))
                spec (validate-rollout-spec (| raw {"owner" (or (valid-actor (.get raw "owner")) actor)})))
          (for [t (rollout-targets spec)]
            (when (and (= t.kind "Service") (not (any (gfor j state.jobs (= j.spec.name t.name)))))
              (refuse 400 (+ "Rollout の相手の Service が無い(先に作る): " t.name))))
          (setv busy (active-rollouts-touching state (lfor t (rollout-targets spec) (target-key t))))
          (when busy (refuse 409 (+ "同じ相手を扱う Rollout が進行中: " (.join ", " busy))))
          (replace state :rollouts (.with-writes state.rollouts #((TableWrite name (RolloutRow :spec spec :status (RolloutStatus :created-ms now)))))))
    (= kind "GroupLimit")
      (do (when (is-not (.row state.group-limits name) None) (refuse 409 (+ "もう在る GroupLimit: " name)))
          (replace state :group-limits (.with-writes state.group-limits #((TableWrite name (checked-group-limit name body.spec))))))
    True (refuse 405 (+ "この kind は API から作れない: " kind))))


(defn #^ ClusterState update-resource [#^ ClusterState state #^ str kind #^ str name #^ (| ResourceBody ServiceBody) body #^ str actor]
  (setv key (key-of kind name))
  (cond
    (isinstance body ServiceBody)
      (do (setv current (next (gfor j state.jobs :if (= j.spec.name name) j) None))
          ;; 受け付けない行(RefusedJob)は、新しい形の宣言の PUT で受け付けた job に置き換える(改訂 1 の C)。
          (when (and (is current None) (is-not (.row state.refused name) None))
            (check-version state key body.resource-version)
            (setv job (replace body.job :owner (or (valid-actor body.owner)
                                                   (.get (. (.row state.refused name) row) "owner") actor)))
            (return (replace state :jobs (+ state.jobs #(job))
                                   :refused (.with-writes state.refused #((TableWrite name None))))))
          (when (is current None) (refuse 404 (+ "無い Service: " name)))
          (check-version state key body.resource-version)
          ;; 版(revision)を変えない置き先(needs・pin)の書き換えは断り、一時の置き先の記録 NeedsOverride を案内する(2026-10-27・
          ;; card ki-1523bc0aba4f — placement-rewrite-refusal)。上書きの間の宣言し直しは宣言だけを書き換え、上書きはそのまま効く。
          (setv refusal (run (placement-rewrite-refusal name current body.job)))
          (when refusal (refuse 409 refusal))
          ;; 所有者は本文の owner の記録(無ければ今の値)。送り手が誰でも書き換える — 名乗りを照合して断らない(2026-10-05)。
          (setv job (replace body.job :owner (or (valid-actor body.owner) current.owner)))
          (replace state :jobs (tuple (gfor j state.jobs (if (= j.spec.name name) job j)))))
    (= kind "Rollout")
      (do (setv current (.row state.rollouts name) spec (dict (or body.spec {})))
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
          (replace state :rollouts (.with-writes state.rollouts #((TableWrite name (replace current :spec (replace old :abort (bool (.get spec "abort")))))))))
    (= kind "GroupLimit")
      (do (setv current (.row state.group-limits name))
          (when (is current None) (refuse 404 (+ "無い GroupLimit: " name)))
          (setv limit (checked-group-limit name body.spec))
          ;; 組を宣言する系は 1 つ(設計検証 lt-9E0W15ZW21H6R3SA26CNW1T4FP の盲検 A — 別の系の宣言が黙って値を書き直し、宣言の順で同時数が
          ;; 変わった)。版の照合は読んでから書くまでの他人の書きしか止めないので、持ち主の系を別に比べる。持ち主の無い組(system の無い
          ;; spec — 74ebb50b6 の宣言の道具が書いた組)は、最初に書き直した系が持つ。
          (when (and current.system (!= limit.system current.system))
            (refuse 409 (.format "GroupLimit {} は系 {} が宣言した組 — 系 {} は書き直せない(組を宣言する系は 1 つ。持ち主を移すなら DELETE してから作る)"
                                 name current.system limit.system)))
          (check-version state key body.resource-version)
          ;; 値と範囲を変えてよい(宣言し直し)。置いている task は止めず、次の置きから新しい値で数える。
          (replace state :group-limits (.with-writes state.group-limits #((TableWrite name limit)))))
    (= kind "NeedsOverride")
      (refuse 405 (.format "NeedsOverride は書き直さない(延ばす・変えるなら DELETE /resources/NeedsOverride/{} の後に作り直す)" name))
    True (refuse 405 (+ "この kind は API から書けない: " kind))))


(defn #^ ClusterState delete-resource [#^ ClusterState state #^ str kind #^ str name #^ dict query
                                      #^ int now #^ ClusterTiming timing]
  (setv key (key-of kind name) force (in (.get query "force" "") #("true" "1"))
        version (if (in "resourceVersion" query) (int-field query "resourceVersion" None) None))
  (check-version state key version :required False)
  (cond
    (= kind "Service")
      (do (setv current (next (gfor j state.jobs :if (= j.spec.name name) j) None))
          (when (and (is current None) (is-not (.row state.refused name) None))
            (return (replace state :refused (.with-writes state.refused #((TableWrite name None))))))
          (when (is current None) (refuse 404 (+ "無い Service: " name)))
          (setv busy (active-rollouts-touching state [(target-key (RolloutTarget :kind "Service" :name name))]))
          (when (and busy (not force)) (refuse 409 (+ "この Service を扱う Rollout が進行中: " (.join ", " busy))))
          ;; 消した Service の準備と計器の報告も観測の表から外す(同じ名で作り直した Service に前の process の報告を数えない)。
          (setv seen state.observations
                dropped #((TableWrite name None)))
          ;; 消した Service の needs の上書きも外す(同じ名で作り直した Service に前の上書きを効かせない)。
          (replace state :jobs (tuple (gfor j state.jobs :if (!= j.spec.name name) j))
                         :observations (replace seen :readiness (.with-writes seen.readiness dropped)
                                                     :metrics (.with-writes seen.metrics dropped))
                         :needs-overrides (.with-writes state.needs-overrides dropped)))
    (= kind "Rollout")
      (do (setv current (.row state.rollouts name))
          (when (is current None) (refuse 404 (+ "無い Rollout: " name)))
          (when (and (not-in current.status.phase TERMINAL-PHASES) (not force))
            (refuse 409 "進行中の Rollout は消せない(中止は abort を書く。旧を先に戻してから新を止める)"))
          (replace state :rollouts (.with-writes state.rollouts #((TableWrite name None)))))
    (= kind "Worker")
      (do (setv w (.row state.workers name))
          (when (is w None) (refuse 404 (+ "無い Worker: " name)))
          (when (alive now w timing.reassign-after-ms)
            (refuse 409 "生きている worker は忘れられない(移し替えの期限まで沈黙した worker だけ)"))
          (replace state :workers (.with-writes state.workers #((TableWrite name None)))
                         :observations (replace state.observations
                                                :statuses (.with-writes state.observations.statuses #((TableWrite name None))))))
    (= kind "Task")
      (do (when (is (.row state.tasks name) None) (refuse 404 (+ "無い Task: " name)))
          (replace state :tasks (.with-writes state.tasks #((TableWrite name None)))))
    ;; 消した組を名乗る task は、置いている物は止めず、待っている物は宣言し直されるまで待つ(TASK-UNDECLARED)。新しい task は受け口が断る。
    (= kind "GroupLimit")
      (do (when (is (.row state.group-limits name) None) (refuse 404 (+ "無い GroupLimit: " name)))
          (replace state :group-limits (.with-writes state.group-limits #((TableWrite name None)))))
    ;; 上書きを外し、Service を元の needs(上書きの間の持ち主の宣言し直しを含む)で置き直す — 期限・能力の消えと同じ外し方。
    (= kind "NeedsOverride")
      (do (setv override (.row state.needs-overrides name))
          (when (is override None) (refuse 404 (+ "無い NeedsOverride: " name)))
          (run (needs-overrides-lifted state #(override))))
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
    ;; 版を変えない置き先の書き換えは断る(資源の口の PUT と同じ placement-rewrite-refusal — 2026-10-27・card ki-1523bc0aba4f)。
    (setv refusal (if have (run (placement-rewrite-refusal name have job)) None))
    (cond
      (is have None)
        (if (is-not version None)
            (.append conflicts (RowConflict :name name :message "版が付いているが、その Service は無い(消された)"))
            (do (.append jobs job) (setv (get results name) "created")))
      (= job have) (setv (get results name) "unchanged")
      refusal (.append conflicts (RowConflict :name name :message refusal))
      (is version None)
        (.append conflicts (RowConflict :name name :message "resourceVersion の無い行で既存の宣言は変えられない(GET /state の行の版を付ける)"
                                         :current (resource-version-of state (key-of "Service" name))))
      (!= version (resource-version-of state (key-of "Service" name)))
        (.append conflicts (RowConflict :name name :message "版が古い"
                                         :current (resource-version-of state (key-of "Service" name))))
      True
        (do (setv jobs (lfor j jobs (if (= j.spec.name name) job j)))
            (setv (get results name) "updated"))))
  (setv untouched (lfor n (sorted current) :if (not-in n (sfor r rows r.name)) n))
  (if conflicts
      #(state 409 (ErrorReply :message "競合した行がある(何も書いていない)" :conflicts (tuple conflicts)))
      #((replace state :jobs (tuple jobs)) 200 {"jobs" (len rows) "results" results "untouched" untouched
                                                "note" "一覧に無い Service は消していない(消すのは DELETE /resources/Service/<名>)"})))
