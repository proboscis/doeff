;;; 本物の coordinator と worker の上で Rollout の順序を検める(#1386)。
(require doeff-hy.macros [deftest defk deff <- val var])
(import pytest)
(import doeff_time [Delay])
(import doeff_cluster.coordinator.core.api_policy :as api-policy)
(import doeff_cluster.local [sim-cluster SimWorker DeclareRollout KubeCalls SettleDeployment
                             ReadCoordinator ProcessesOf StartWorker StopCoordinator])
(import doeff_cluster.remote_model [RemoteJobFailed])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.sim_programs [beacons])

(val DEP "prod/old-beacon")
(val DEPLOYMENTS {DEP {"specReplicas" 1 "replicas" 1 "readyReplicas" 1}})
(val FORWARD {"from" {"kind" "Deployment" "namespace" "prod" "name" "old-beacon"}
              "to" {"kind" "Service" "name" "beacon"}
              "readyTimeoutSeconds" 90 "stopTimeoutSeconds" 30 "observeSeconds" 1})
(val WORKERS #((SimWorker :name "w1" :provides (frozenset #{"cluster-net"}) :starts-down True)))


(defk rollout-scenario []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "新の worker を後から起こし、旧の停止要求と Pod の停止を分けて確かめる。"
  (<- created dict (DeclareRollout "forward" FORWARD))
  (assert (= (get created "spec" "to" "name") "beacon"))
  (<- (Delay 3.0))
  (<- early tuple (KubeCalls))
  (assert (not early) "新が動く前に旧の Deployment を止めた")
  ;; worker がまだ居ない間に再起動し、Rollout と k8s の初期状態が残ることを確かめる。
  (<- (StopCoordinator 0.5))
  (<- (Delay 2.0))
  (<- (StartWorker "w1"))
  (var calls #())
  (var turns 0)
  (while (and (not calls) (< turns 30))
    (<- (Delay 1.0))
    (:= calls (! (KubeCalls)))
    (:= turns (+ turns 1)))
  (assert (= calls #({"op" "scale" "key" DEP "replicas" 0 "dryRun" False})) calls)
  (<- processes tuple (ProcessesOf "beacon"))
  (assert (any (gfor p processes (is p.exit-code None))) "旧の停止時に新の process が居ない")
  (<- pending dict (ReadCoordinator "/resources/Rollout/forward"))
  (assert (= (get pending "status" "phase") "StoppingOld") pending)
  (val stopped-at (get pending "status" "lastAction" "at"))
  (assert (any (gfor p processes (and (<= p.started-ms stopped-at)
                                      (or (is p.ended-ms None) (> p.ended-ms stopped-at)))))
          "旧の scale が実行された時刻に新の process が居ない")
  (<- (SettleDeployment "prod" "old-beacon"))
  (<- (Delay 3.0))
  (<- complete dict (ReadCoordinator "/resources/Rollout/forward"))
  (assert (= (get complete "status" "phase") "Complete") complete)
  (<- after tuple (KubeCalls))
  (assert (= after calls))
  ;; 読みの値を変更しても、世界の履歴や初期値を変更できない。
  (.clear (get after 0))
  (<- isolated tuple (KubeCalls))
  (assert (= isolated calls))
  isolated)


(defk reverse-scenario []
  {:pre [] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "逆向きも Pod の準備を待つ。ready を明示すれば全台準備済みにはならない。"
  (<- (rollout-scenario))
  (<- (DeclareRollout "reverse" (| FORWARD {"from" (get FORWARD "to") "to" (get FORWARD "from")})))
  (<- (Delay 3.0))
  (<- (SettleDeployment "prod" "old-beacon" :ready 0))
  (<- (Delay 3.0))
  (<- pending dict (ReadCoordinator "/resources/Rollout/reverse"))
  (assert (= (get pending "status" "phase") "WaitingNewReady") pending)
  (<- processes tuple (ProcessesOf "beacon"))
  (assert (any (gfor p processes (is p.exit-code None))))
  (<- (SettleDeployment "prod" "old-beacon"))
  (<- (Delay 8.0))
  (<- complete dict (ReadCoordinator "/resources/Rollout/reverse"))
  (assert (= (get complete "status" "phase") "Complete") complete)
  (<- calls tuple (KubeCalls))
  (assert (= calls #({"op" "scale" "key" DEP "replicas" 0 "dryRun" False}
                     {"op" "scale" "key" DEP "replicas" 1 "dryRun" False})))
  None)


(deftest test-rollout-waits-for-the-service-before-stopping-the-deployment
  (<- calls tuple (sim-cluster (beacons sim-foundation) (rollout-scenario)
                              :workers WORKERS :deployments DEPLOYMENTS))
  (assert (= (len calls) 1))
  (assert (= (get DEPLOYMENTS DEP "specReplicas") 1)))


(deftest test-reverse-rollout-waits-for-the-explicit-pod-readiness
  (<- (sim-cluster (beacons sim-foundation) (reverse-scenario)
                  :workers WORKERS :deployments DEPLOYMENTS)))


(deff stop-old-before-ready [spec status from-view to-view now]  ; defk にできない: api_policy の純粋な判断の callback を置き換える反例
  {:pre [(: spec dict) (: status dict) (: from-view dict) (: to-view dict) (: now int)]
   :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "反例: 新の状態にかかわらず、旧を先に 0 台にする誤った順序。"
  #(status [{"op" "scale" "target" (get spec "from") "replicas" 0}]))


(deftest test-the-same-scenario-rejects-a-rollout-that-stops-old-first [monkeypatch]
  (.setattr monkeypatch api-policy "rollout_step" stop-old-before-ready)
  (with [(pytest.raises AssertionError :match "新が動く前に旧の Deployment を止めた")]
    (<- (sim-cluster (beacons sim-foundation) (rollout-scenario)
                    :workers WORKERS :deployments DEPLOYMENTS))))


(defk rejected-rollouts []
  {:pre [] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "宣言は本番と同じ検証・重複検査を受ける。"
  (with [(pytest.raises RemoteJobFailed)]
    (<- (DeclareRollout "bad" {})))
  (<- (DeclareRollout "forward" FORWARD))
  (with [(pytest.raises RemoteJobFailed)]
    (<- (DeclareRollout "forward" FORWARD)))
  None)


(deftest test-rollout-declarations-go-through-coordinator-validation
  (<- (sim-cluster (beacons sim-foundation) (rejected-rollouts)
                  :workers WORKERS :deployments DEPLOYMENTS)))
