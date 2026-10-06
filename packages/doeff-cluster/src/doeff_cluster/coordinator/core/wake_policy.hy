;;; coordinator が次に起きる刻と、落ち着くまでの約束の純粋な判断(#3865)。
;;;
;;; 調停ループ(core/program.hy の run-coordinator)は、要求が無い間、次に起きる刻(next-wake)まで受付を 1 本で待つ。
;;; 要求を受けずに状態を変えた歩の後は待たずにもう 1 歩進め(after-step)、それ以外は次に起きる刻まで待つ。今すぐが上限を越えて続けば、
;;; 変わり続けた欄を名指して落ちる(count-unsettled)。判断は期限ちょうどの刻に出る(1 秒の格子に丸めない)。
(require doeff-hy.macros [defk <- val var])
(val MODULE-TAGS {:context "coordinator" :role "judgment"})
(import dataclasses [fields])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState ClusterNaming])
(import doeff_cluster.shared.intent.due_model [DueAt DueNow DueNever])
(import doeff_cluster.coordinator.intent.due_model [CoordinatorUnsettled])
(import doeff_cluster.shared.core.due_policy [earliest-due])
(import doeff_cluster.coordinator.core.api_policy [tick-due deployments-to-observe deployment-reread-due ROLLOUT-TICK-MS])
(import doeff_cluster.coordinator.core.cluster_policy [nodes-to-read node-reread-due])
(import doeff_cluster.coordinator.core.resource_policy [readiness-due service-stopped-due])
(import doeff_cluster.coordinator.core.rollout_policy [rollout-targets rollout-phase-due TERMINAL-PHASES])
(import doeff_cluster.coordinator.core.watch_policy [all-waiting-unchanged lease-full-at])

;; 今すぐ(DueNow)が続いてよい歩の数。1 歩ごとに状態の欄の 1 つが落ち着く(生死の求め直し → task → 掃除 → 置き先)ので、正しい判断なら
;; 数歩で落ち着く。越えたら回り続けていると見て落ちる。
(val UNSETTLED-STEP-LIMIT 100)


(defk rollout-due [state now timing naming]
  {:pre [(: state ClusterState) (: now int) (: timing ClusterTiming) (: naming ClusterNaming)] :post [(: % (| DueAt DueNow DueNever))]
   :tags {:context "coordinator" :role "judgment"}}
  "Rollout の歩(coordinator-step の rollout-tick)が、状態がこのままで Kubernetes を読む・段を進める・action を出し得る最初の刻を知るため
   (#3064)。期限は、台数の持ち主の Deployment と node の印の読み直しの刻(deployment-reread-due・node-reread-due)と、終わっていない
   Rollout ごとの段の期限(rollout-phase-due)と、その Service の相手の観測が変わる刻(readiness の判定 readiness-due・止まりの判定
   service-stopped-due)。どれも判断が比べに使う期限の値から求める(#1383 の決めの条件 (1))。Rollout の歩は、前の Rollout の歩
   (rollout-tick-ms)から ROLLOUT-TICK-MS 経った歩でだけ回るので、期限は前の Rollout の歩の刻より後(まだ判じていない物)から求め、答えは
   その最小と、Rollout の歩を回せる最初の刻(rollout-tick-ms + ROLLOUT-TICK-MS)の遅い方(期限の刻の歩が Rollout の歩を回せない時も期限を
   落とさない — #3865)。DueNever = 時刻では変わらない。今読む物が在る間は、Kubernetes を読む周期(次の Rollout の歩の刻)の刻を返す:
   これは期限ではなく周期で見に行く形で、Kubernetes の watch に替える件(#3868)で消す(#3865)。"
  (val since state.rollout-tick-ms)
  (<- deployments (| int None) (deployment-reread-due state since))
  (<- nodes (| int None) (node-reread-due state since))
  (var dues (tuple (gfor due [deployments nodes] :if (is-not due None) due)))
  (for [#(_ r) (sorted (.items state.rollouts))]
    (when (not-in r.status.phase TERMINAL-PHASES)
      (<- phase (| int None) (rollout-phase-due r.spec r.status since))
      (:= dues (+ dues (if (is-not phase None) #(phase) #())))
      (for [target (rollout-targets r.spec)]
        (when (= target.kind "Service")
          (<- ready (| int None) (readiness-due state target.name since timing))
          (<- stopped (| int None) (service-stopped-due state target.name since timing))
          (:= dues (+ dues (tuple (gfor due [ready stopped] :if (is-not due None) due))))))))
  (val gate (+ since ROLLOUT-TICK-MS))
  (cond
    (or (deployments-to-observe state now) (nodes-to-read state now)) (DueAt :at (max (+ now 1) gate))
    dues (DueAt :at (max (min dues) gate))
    True (DueNever)))


(defk watchers-due [watchers state now]
  {:pre [(: watchers tuple) (: state ClusterState) (: now int)] :post [(: % (| DueAt DueNow DueNever))]
   :tags {:context "coordinator" :role "judgment"}}
  "返事を待たせている待ち(GET /watch の Watcher)が答えを変える最初の刻を知るため: どの待ちも落ち着いていれば(版が after のまま・名指した
   待ちは見え方を覚え済み — watch_policy.all-waiting-unchanged)いちばん早い期限 deadline-ms ちょうど(watch-deadline は now >= deadline-ms で
   「変わっていない」と答える)、落ち着いていない待ちが在れば今すぐ(settle-watch が今の刻で答える・見え方を覚える)、待ちが無ければ無し。"
  ;; lease の待ちは、その lease に最初に空きが出る刻(担い手の期限切れ)にも起きる(#3865 の後の単位)。
  (<- unchanged bool (all-waiting-unchanged watchers state now))
  (var ats #())
  (for [watcher watchers]
    (:= ats (+ ats #(watcher.deadline-ms)))
    (when (is-not watcher.lease None)
      (<- full (| int None) (lease-full-at watcher state now))
      (when (is-not full None)
        (:= ats (+ ats #(full))))))
  (cond
    (not watchers) (DueNever)
    (not unchanged) (DueNow)
    True (DueAt :at (min ats))))


(defk next-wake [state now timing naming watchers]
  {:pre [(: state ClusterState) (: now int) (: timing ClusterTiming) (: naming ClusterNaming) (: watchers tuple)]
   :post [(: % (| DueAt DueNow DueNever))] :tags {:context "coordinator" :role "judgment"}}
  "何も変えない歩の後に、調停ループが次に起きる刻を知るため: 要求の無い歩の期限(api_policy.tick-due)・Rollout の期限(rollout-due —
   Rollout の進行中は Kubernetes を読む周期を含む・#3868)・待ちの期限(watchers-due)のいちばん早い答え。"
  (<- ticking (| DueAt DueNow DueNever) (tick-due state now timing))
  (<- rolling (| DueAt DueNow DueNever) (rollout-due state now timing naming))
  (<- waiting (| DueAt DueNow DueNever) (watchers-due watchers state now))
  (<- answer (| DueAt DueNow DueNever) (earliest-due #(ticking rolling waiting)))
  answer)


(defk after-step [due changed]
  {:pre [(: due (| DueAt DueNow DueNever)) (: changed bool)] :post [(: % (| DueAt DueNow DueNever))]
   :tags {:context "coordinator" :role "judgment"}}
  "歩の後にどこまで待つかを決めるため: 要求を受けずに状態を変えた歩(changed — 時刻で変わった判断の歩)の後は今すぐもう 1 歩(その歩の
   答えが次の判断の答えを変えうる)。それ以外は、その時に求めた次に起きる刻 due まで — 要求を受けた歩の後も、要求で変わった状態が
   落ち着いていなければ期限の関数が今すぐを返す(R3)ので、必ずもう 1 歩は回さない(#3865 の直し A — 要求ごとの空の歩を消す)。"
  (if changed (DueNow) due))


(defk wait-seconds [due now]
  {:pre [(: due (| DueAt DueNow DueNever)) (: now int)] :post [(: % (| float None))] :tags {:context "coordinator" :role "judgment"}}
  "次に起きる刻 due を、受付を待つ秒(NextRequests の timeout)にするため: 刻までの秒(過ぎていれば 0)・今すぐは 0・無しは期限なし(None
   — 要求か停止の合図か外の出来事でだけ起きる)。"
  (match due
    (DueAt :at at) (/ (max 0 (- at now)) 1000.0)
    (DueNow) 0.0
    (DueNever) None))


(defk count-unsettled [streak due before after]
  {:pre [(: streak int) (: due (| DueAt DueNow DueNever)) (: before ClusterState) (: after ClusterState)] :post [(: % int)]
   :tags {:context "coordinator" :role "judgment"}}
  "今すぐ(DueNow)が続いた歩の数を数え、回り続ける調停ループを名指して止めるため: 今すぐなら 1 足し、それ以外は 0 に戻す。足した数が
   UNSETTLED-STEP-LIMIT を越えたら、歩の前後の状態 before・after で違う欄の名を書いて CoordinatorUnsettled で落ちる。"
  (val counted (match due (DueNow) (+ streak 1) (DueAt) 0 (DueNever) 0))
  (when (> counted UNSETTLED-STEP-LIMIT)
    (raise (CoordinatorUnsettled
             (.format "調停ループが {} 歩 落ち着かない(今すぐが続いた)— 変わり続けた欄: {}" counted
                      (or (lfor f (fields before) :if (!= (getattr before f.name) (getattr after f.name)) f.name) "無し")))))
  counted)
