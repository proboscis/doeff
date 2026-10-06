;; 期限の関数の境(#3865): 要求の無い間、coordinator は期限の関数が返す「次に判断の答えが変わる刻」まで待つ。期限の関数が 1 ms でも
;; 早い刻を返すと、起きた時に判断は変わらず、関数は now より後の刻だけを見るので本当の変化の刻を飛ばす。遅い刻を返すと判断が遅れる。
;; ここでは期限の関数ごとに、返す刻 D の 1 ms 前では判断が答えを変えず、D で変える事を縛る(D は now より後)。
;; - resource_policy.readiness-due と readiness の判定 service-readiness(running-process を含む)— 枝ごと。
;; - resource_policy.service-stopped-due と止まりの判定 service-stopped。
;; - api_policy.deployment-reread-due と読み直す Deployment の選び deployments-to-observe。
;; - cluster_policy.node-reread-due と読み直す node の選び nodes-to-read。
;; readiness-due は「答えを変え得る刻の下限」を返す(早めに試すのは安全)。D で答えが変わらない枝は、その事と判定が実際に変わる刻を
;; そのテストの註に書く(D − 1 で変わらない事だけを縛る)。
(require doeff-hy.macros [deftest defk <- val])
(import dataclasses [replace])
(import doeff_hy.table [TableWrite table-of])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState ClusterObservations KeepMark WorkerInfo RolloutRow RolloutSpec
                                                       RolloutStatus RolloutTarget DeploymentUnreadable NodeLabelsSeen])
(import doeff_cluster.coordinator.core.resource_policy [readiness-due service-readiness carrier-stale-from report-expired-from
                                                        unreported-until warm-until service-stopped service-stopped-due current-report
                                                        running-process])
(import doeff_cluster.coordinator.core.cluster_policy [liveness-deadline nodes-to-read node-reread-due])
(import doeff_cluster.coordinator.core.api_policy [deployments-to-observe deployment-reread-due])
(import tests.test_handoff_deadline [Sim HANDOFF steps])

;; 担い手の報告が古くならない時計(効く期限を準備の報告の window の 1 つにする)。
(val LONG-LEASE (ClusterTiming :lease-ms 1000000000))
;; 担い手の報告が準備の報告の window(10 秒)より早く古くなる時計。
(val SHORT-LEASE (ClusterTiming :lease-ms 5000))
(val T (ClusterTiming))
(val NAME "writer-a")


(defk ready-world []
  {:pre [] :post [(: % Sim)] :tags {:context "doeff-cluster-test" :role "program"}}
  "worker zeus の上で Service writer-a(readiness の window 10 秒)が Ready の世界を作るため。最後の拍で担い手の heartbeat と
   準備の報告(真)を同じ刻 sim.now に受けている。"
  (val sim (Sim HANDOFF))
  (<- (steps sim 12))
  (assert (= (get (service-readiness sim.state NAME sim.now T) "state") "Ready") sim.log)
  (val st (get sim.state.statuses "zeus"))
  (assert (= st.at sim.now) #(st.at sim.now))
  sim)


(defk readiness-boundary [state now timing]
  {:pre [(: state ClusterState) (: now int) (: timing ClusterTiming)] :post [(: % tuple)]
   :tags {:context "doeff-cluster-test" :role "program"}}
  "readiness-due の答え D(now より後であることを確かめる)と、readiness の判定を now・D − 1・D で当てた答えを返すため。"
  (<- due (| int None) (readiness-due state NAME now timing))
  (assert (is-not due None) "期限が無い")
  (assert (> due now) #(due now))
  #(due (service-readiness state NAME now timing) (service-readiness state NAME (- due 1) timing)
        (service-readiness state NAME due timing)))


;; --- readiness-due: 担い手の報告が新しい間 ----------------------------------------------------------------------------

(deftest test-readiness-due-carrier-stale-from-is-the-judgments-boundary
  ;; 枝 carrier-stale-from: 担い手の報告が窓(lease 5 秒)を過ぎて古くなる刻。準備の報告の window の期限(10 秒後)より早い。
  ;; D − 1 は Ready のまま、D で担い手の沈黙(移し替えの窓の内)として Unknown。
  (<- sim Sim (ready-world))
  (val state sim.state)
  (<- seen tuple (readiness-boundary state sim.now SHORT-LEASE))
  (val due (get seen 0))
  (val now (get seen 1))
  (val before (get seen 2))
  (val at (get seen 3))
  (assert (= due (carrier-stale-from (get state.statuses "zeus") SHORT-LEASE)) seen)
  (assert (= before now) seen)
  (assert (= (get before "state") "Ready") seen)
  (assert (= (get at "state") "Unknown") seen))


(deftest test-readiness-due-report-expired-from-is-the-judgments-boundary
  ;; 枝 report-expired-from: 担い手の報告が古くならない間(LONG-LEASE)、準備の報告(真)が window(10 秒)の外になる刻。
  ;; D − 1 は Ready のまま、D で NotReady。
  ;; 註(試していない・コードの読み): 今の process の最新の報告が偽(ready False)の時は、D の前も後も state は NotReady で、
  ;; 変わるのは reason(「報告: …」→「最後の報告から … 秒」)と role の有無だけ。
  (<- sim Sim (ready-world))
  (val state sim.state)
  (val report (current-report (.row state.observations.readiness NAME) (running-process state NAME sim.now LONG-LEASE)))
  (<- seen tuple (readiness-boundary state sim.now LONG-LEASE))
  (val due (get seen 0))
  (val now (get seen 1))
  (val before (get seen 2))
  (val at (get seen 3))
  (assert (= due (report-expired-from report 10000)) seen)
  (assert (= before now) seen)
  (assert (= (get before "state") "Ready") seen)
  (assert (= (get at "state") "NotReady") seen))


(deftest test-readiness-due-unreported-until-is-the-judgments-boundary
  ;; 枝 unreported-until: 担い手の報告は新しいが、今の process の準備の報告がまだ無い(coordinator が 1 秒前に起きた — 報告の表が空)。
  ;; 起動の刻 + window の 1 ms 前は Unknown のまま、その刻に NotReady。
  (<- sim Sim (ready-world))
  (val state (replace sim.state :started-ms (- sim.now 1000)
                      :observations (replace sim.state.observations :readiness (. (ClusterObservations) readiness))))
  (<- seen tuple (readiness-boundary state sim.now LONG-LEASE))
  (val due (get seen 0))
  (val now (get seen 1))
  (val before (get seen 2))
  (val at (get seen 3))
  (assert (= due (unreported-until state 10000) (+ sim.now 9000)) seen)
  (assert (= before now) seen)
  (assert (= (get before "state") "Unknown") seen)
  (assert (= (get at "state") "NotReady") seen))


;; --- readiness-due: 担い手の報告が古い間 ------------------------------------------------------------------------------

(deftest test-readiness-due-warm-until-is-the-judgments-boundary
  ;; 枝 warm-until: 担い手が移し替えの窓(60 秒)を越えて黙っている間に、coordinator の起動の直後の猶予が終わる刻。
  ;; D − 1 は猶予の内なので Unknown、D で NotReady。
  (<- sim Sim (ready-world))
  (val now (+ sim.now 70000))
  (val state (replace sim.state :started-ms (- now 5000)))
  (<- seen tuple (readiness-boundary state now T))
  (val due (get seen 0))
  (val before (get seen 2))
  (val at (get seen 3))
  (assert (= due (warm-until state T) (+ now 5000)) seen)
  (assert (= before (service-readiness state NAME now T)) seen)
  (assert (= (get before "state") "Unknown") seen)
  (assert (= (get at "state") "NotReady") seen))


(deftest test-readiness-due-warm-until-while-the-carrier-is-in-the-reassign-window
  ;; 枝 warm-until(担い手がまだ移し替えの窓の内): D − 1 で変わらない事だけを縛る。
  ;; 註: この枝は D で答えを変えない(下限として早い)。猶予が終わっても担い手が移し替えの窓の内(alive)なので Unknown のまま。
  ;; 判定が実際に変わるのは移し替えの窓の終わり liveness-deadline(zeus, reassign-after-ms) + 1 = 最後の連絡 + 60001 ms(NotReady)。
  ;; その刻は readiness-due が次の答えとして返す(下の断言)ので、飛ばしはしない。
  (<- sim Sim (ready-world))
  (val now (+ sim.now 20000))
  (val state (replace sim.state :started-ms (- now 5000)))
  (<- seen tuple (readiness-boundary state now T))
  (val due (get seen 0))
  (val before (get seen 2))
  (val at (get seen 3))
  (assert (= due (warm-until state T) (+ now 5000)) seen)
  (assert (= before (service-readiness state NAME now T)) seen)
  (assert (= (get before "state") "Unknown") seen)
  (<- next-due (| int None) (readiness-due state NAME due T))
  (assert (= next-due (+ (liveness-deadline (get state.workers "zeus") T.reassign-after-ms) 1)) #(next-due seen)))


(deftest test-readiness-due-reassign-window-is-the-judgments-boundary
  ;; 枝 担い手の沈黙の窓 1(移し替え reassign-after-ms): 起動の直後の猶予は終わっていて、担い手が lease を過ぎて黙っている。
  ;; D(最後の連絡 + 60 秒 + 1 ms)の 1 ms 前は Unknown、D で NotReady(担い手から他へ移す)。
  (<- sim Sim (ready-world))
  (val now (+ sim.now 30000))
  (val state sim.state)
  (<- seen tuple (readiness-boundary state now T))
  (val due (get seen 0))
  (val before (get seen 2))
  (val at (get seen 3))
  (assert (= due (+ (liveness-deadline (get state.workers "zeus") T.reassign-after-ms) 1)) seen)
  (assert (= before (service-readiness state NAME now T)) seen)
  (assert (= (get before "state") "Unknown") seen)
  (assert (= (get at "state") "NotReady") seen))


(deftest test-readiness-due-keep-fence-window-is-the-judgments-boundary
  ;; 枝 担い手の沈黙の窓 2(途絶の柵 keep-fence-ms): 途絶しても動かし続けてよい印を zeus に渡してある。移し替えの窓は過ぎている。
  ;; D(最後の連絡 + 240 秒 + 1 ms)の 1 ms 前は Unknown(印があるので動いている見込み)、D で NotReady。
  (<- sim Sim (ready-world))
  (val now (+ sim.now 100000))
  (val state (replace sim.state :keep-marks #((KeepMark :job NAME :worker "zeus" :boot None :since-ms sim.now))))
  (<- seen tuple (readiness-boundary state now T))
  (val due (get seen 0))
  (val before (get seen 2))
  (val at (get seen 3))
  (assert (= due (+ (liveness-deadline (get state.workers "zeus") T.keep-fence-ms) 1)) seen)
  (assert (= before (service-readiness state NAME now T)) seen)
  (assert (= (get before "state") "Unknown") seen)
  (assert (= (get at "state") "NotReady") seen))


(deftest test-readiness-due-keep-fence-window-without-a-mark
  ;; 枝 担い手の沈黙の窓 2(印が無い): D − 1 で変わらない事だけを縛る。
  ;; 註: この枝は D で答えを変えない(下限として早い)。印が無ければ移し替えの窓を過ぎた時点で NotReady になっていて、
  ;; 状態がこのままなら判定は以後変わらない(実際に変わる刻は無い — D は答えを変えない起きる刻になる)。
  (<- sim Sim (ready-world))
  (val now (+ sim.now 100000))
  (val state sim.state)
  (<- seen tuple (readiness-boundary state now T))
  (val due (get seen 0))
  (val before (get seen 2))
  (val at (get seen 3))
  (assert (= due (+ (liveness-deadline (get state.workers "zeus") T.keep-fence-ms) 1)) seen)
  (assert (= before (service-readiness state NAME now T)) seen)
  (assert (= (get before "state") "NotReady") seen))


;; --- service-stopped-due ------------------------------------------------------------------------------------------------

(deftest test-service-stopped-due-is-the-judgments-boundary
  ;; 宣言と置き先を外した Service の行(running)を zeus がまだ報告している。zeus が lease の窓から外れる刻 D の 1 ms 前は
  ;; まだどこかで動いている(止まっていない)、D で止まっている。
  (<- sim Sim (ready-world))
  (val state (replace sim.state :jobs (tuple (gfor j sim.state.jobs :if (!= j.spec.name NAME) j)) :placements {}))
  (assert (not (service-stopped state NAME sim.now T)))
  (<- due (| int None) (service-stopped-due state NAME sim.now T))
  (assert (= due (+ (liveness-deadline (get state.workers "zeus") T.lease-ms) 1)) due)
  (assert (> due sim.now) #(due sim.now))
  (assert (not (service-stopped state NAME (- due 1) T)) due)
  (assert (service-stopped state NAME due T) due))


;; --- deployment-reread-due ----------------------------------------------------------------------------------------------

(val ROLLOUT-STOPPED-OLD 900000)

(defk complete-rollout [name target]
  {:pre [(: name str) (: target RolloutTarget)] :post [(: % RolloutRow)] :tags {:context "doeff-cluster-test" :role "program"}}
  "Service writer-a から Deployment target へ移し終えた(Complete の)Rollout の行を作るため(target が台数の持ち主になる)。"
  (RolloutRow :spec (RolloutSpec :from-target (RolloutTarget :kind "Service" :name NAME) :to-target target :owner None
                                 :ready-timeout-seconds 60 :stop-timeout-seconds 90 :observe-seconds 120 :fail-after-seconds 15
                                 :rollback-timeout-seconds 60 :mark-deployment False :abort False)
              :status (RolloutStatus :phase "Complete" :stopped-old-ms ROLLOUT-STOPPED-OLD :completed-ms ROLLOUT-STOPPED-OLD)))


(deftest test-deployment-reread-due-is-the-judgments-boundary
  ;; Complete の Rollout 2 つが台数を持つ Deployment ns/app-a(1000000 に読んだ)と ns/app-b(1005000 に読んだ)。D = 早い方の読み直しの
  ;; 刻 1010001 の 1 ms 前は何も読まず、D で ns/app-a を読み始める。
  (<- row-a RolloutRow (complete-rollout "to-a" (RolloutTarget :kind "Deployment" :name "app-a" :namespace "ns" :replicas 1)))
  (<- row-b RolloutRow (complete-rollout "to-b" (RolloutTarget :kind "Deployment" :name "app-b" :namespace "ns" :replicas 1)))
  (val observations (ClusterObservations
                      :deployments (table-of #((TableWrite "ns/app-a" (DeploymentUnreadable :error "読めなかった" :at 1000000))
                                               (TableWrite "ns/app-b" (DeploymentUnreadable :error "読めなかった" :at 1005000))))))
  (val state (ClusterState :rollouts {"to-a" row-a "to-b" row-b} :observations observations))
  (val now 1003000)
  (assert (= (deployments-to-observe state now) []))
  (<- due (| int None) (deployment-reread-due state now))
  (assert (= due 1010001) due)
  (assert (> due now) #(due now))
  (assert (= (deployments-to-observe state (- due 1)) []) due)
  (assert (= (deployments-to-observe state due) ["ns/app-a"]) due))


;; --- node-reread-due ----------------------------------------------------------------------------------------------------

(deftest test-node-reread-due-is-the-judgments-boundary
  ;; node n1(1000000 に読んだ)と n2(1020000 に読んだ)の上の worker。D = 早い方の読み直しの刻 1060001 の 1 ms 前は何も読まず、
  ;; D で n1 を読み始める。
  (val workers {"w1" (WorkerInfo :name "w1" :provides #("cpu") :capacity 1 :last-seen-ms 1000000 :task-reserve 0 :node "n1")
                "w2" (WorkerInfo :name "w2" :provides #("cpu") :capacity 1 :last-seen-ms 1000000 :task-reserve 0 :node "n2")})
  (val observations (ClusterObservations
                      :nodes (table-of #((TableWrite "n1" (NodeLabelsSeen :labels (table-of #()) :at 1000000))
                                         (TableWrite "n2" (NodeLabelsSeen :labels (table-of #()) :at 1020000))))))
  (val state (ClusterState :workers workers :observations observations))
  (val now 1030000)
  (val now-read (nodes-to-read state now))
  (assert (= now-read []) now-read)
  (<- due (| int None) (node-reread-due state now))
  (assert (= due 1060001) due)
  (assert (> due now) #(due now))
  (val before (nodes-to-read state (- due 1)))
  (val at (nodes-to-read state due))
  (assert (= before []) before)
  (assert (= at ["n1"]) at))
