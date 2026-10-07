;;; Rollout の進行中に、coordinator が時間で k8s の Deployment を読みに行かず、Deployment の変化の出来事で処理ステージを進める事の
;;; 失敗ケース(#3868 — 利用者 2026-10-06 "so anything that require polling, are to be fixed. polling is a last resort")。
;;;
;;; 本物の coordinator と worker を模擬の cluster(仮想の時計・偽の k8s KubeMemory)で回す。逆向きの Rollout(Service → Deployment)は
;;; 処理ステージ WaitingNewReady で新の Deployment の準備を待つ:
;;;
;;;   (a) 準備を待つ静かな 20 秒の間、coordinator が k8s の Deployment を読みに行く数は 0(変化の出来事が来ない — 直す前は 1 秒ごとに
;;;       読みに行くので約 20 回で赤)。
;;;   (b) Deployment の Pod が揃った刻(仮想の時計の 1 秒の格子から外した刻)に、Rollout は StoppingOld へ進む(直す前は次の 1 秒ごとの
;;;       読みの刻まで進まないので、履歴の刻がずれて赤)。
(require doeff-hy.macros [deftest defk <- val])
(import doeff_events [MemoryBroker])
(import doeff_time [Delay])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.sim.local [sim-cluster DeclareRollout KubeReads SettleDeployment ReadCoordinator])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.sim_programs [beacons])
(import tests.test_local_rollout [rollout-scenario DEPLOYMENTS FORWARD WORKERS])

;; 準備を待つ静かな区間の長さ(秒)と、Pod が揃う刻を 1 秒の格子から外す端数(秒)。
(val QUIET-SECONDS 20.0)
(val OFF-GRID-SECONDS 0.37)


(defk waiting-for-the-deployment []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "前向きの Rollout を終えた後に逆向きの Rollout を宣言し、新の Deployment の準備を待つ間の静かな区間に k8s を読みに行った数と、Pod が
   揃った刻と、その後の Rollout の status を読むため。答え = #(静かな区間の読みの数 揃えた刻 Rollout の JSON)。"
  (<- (rollout-scenario))
  (<- (DeclareRollout "reverse" (| FORWARD {"from" (get FORWARD "to") "to" (get FORWARD "from")})))
  (<- (Delay 3.0))
  (<- (SettleDeployment "prod" "old-beacon" :ready 0))
  (<- (Delay 3.0))
  (<- pending dict (ReadCoordinator "/resources/Rollout/reverse"))
  (assert (= (get pending "status" "phase") "WaitingNewReady") pending)
  (<- before tuple (KubeReads))
  (<- (Delay QUIET-SECONDS))
  (<- after tuple (KubeReads))
  (<- (Delay OFF-GRID-SECONDS))
  (<- settled-at int (now-epoch-ms))
  (<- (SettleDeployment "prod" "old-beacon"))
  (<- (Delay 5.0))
  (<- moved dict (ReadCoordinator "/resources/Rollout/reverse"))
  #((- (len after) (len before)) settled-at moved))


(defk run-waiting []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書きを模擬の cluster で回すため。"
  (<- seen tuple (sim-cluster :notice-broker (MemoryBroker) (beacons sim-foundation) (waiting-for-the-deployment)
                              :workers WORKERS :deployments DEPLOYMENTS))
  seen)


(deftest test-a-waiting-rollout-does-not-read-the-deployment-on-a-timer
  (<- seen tuple (run-waiting))
  (val quiet-reads (get seen 0))
  (assert (= quiet-reads 0) quiet-reads))


(deftest test-a-rollout-moves-at-the-instant-the-deployment-changes
  (<- seen tuple (run-waiting))
  (val settled-at (get seen 1))
  (val moved (get seen 2))
  (val entered (lfor h (get moved "status" "history") :if (= (get h "phase") "StoppingOld") (get h "at")))
  ;; 逆向きの Rollout の StoppingOld は 1 度だけ(前向きの Rollout は別の行)。
  (assert (= entered [settled-at]) #(entered settled-at)))
