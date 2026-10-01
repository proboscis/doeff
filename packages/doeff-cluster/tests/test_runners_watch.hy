;; 名簿の変化の待ち AwaitRunnersChange(detached_model・本番の DetachedClient.runners-change・sim の宿 — #1934)。
;;
;; - 版が after から変わった刻ちょうどに RunnersChange(changed 真・次の after)で返る。変わらなければ上限の後の拍で changed 偽。
;; - 待つ口の無い coordinator(/watch が 404)には RunnersWatchMissing(呼び手が周回に戻る)。
;; - 本番の client は同じ読み(runners-change-of)で答える: 200 → RunnersChange・404 → RunnersWatchMissing・届かない → RunnersUnreachable。
(require doeff-hy.macros [deftest defk <- val var])
(import httpx)
(import doeff_core_effects.scheduler [Spawn Task Wait])
(import doeff_time [Delay])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.detached [DetachedClient])
(import doeff_cluster.shared.intent.detached_model [AwaitRunnersChange RunnersChange RunnersWatchMissing RunnersUnreachable])
(import doeff_cluster.sim.local [sim-cluster SimWorker ReadCoordinator DrainWorker FailRoute KillWorker StartWorker])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.sim_programs [beacons NET])

(val WORKERS #((SimWorker :name "w1" :provides NET) (SimWorker :name "w2" :provides NET)))
(val TIMING (ClusterTiming))


(defk timed-change [after seconds]
  {:pre [(: after int) (: seconds float)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書きの送り手として AwaitRunnersChange を 1 回待ち、#(答え 返った刻) を返すため。"
  (<- answer (AwaitRunnersChange after :timeout-seconds seconds))
  (<- at int (now-epoch-ms))
  #(answer at))


(defk drain-during-wait []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 落ち着いた後の版 R から 1.5 秒の待ち(何も変えない — 変わらない)を待ち、次に R の変化を 8 秒まで待たせて 2 秒後に drain を
   頼む。答え = #(R drain の刻 変化の待ちの #(答え 刻) 変わらない待ちの #(答え 刻) 変わらない待ちを待ち始めた刻)。"
  (<- (Delay 12.0))
  (<- view dict (ReadCoordinator "/state"))
  (val revision (get view "revision"))
  (<- started int (now-epoch-ms))
  (<- quiet tuple (timed-change revision 1.5))
  (<- waiter Task (Spawn (timed-change revision 8.0)))
  (<- (Delay 2.0))
  (<- drained-at int (now-epoch-ms))
  (<- (DrainWorker "w1"))
  (<- first tuple (Wait waiter))
  #(revision drained-at first quiet started))


(deftest test-a-runners-change-wakes-at-the-change-and-times-out-unchanged
  (<- seen tuple (sim-cluster (beacons sim-foundation) (drain-during-wait) :workers WORKERS))
  (val revision (get seen 0))
  (val first (get seen 2 0))
  (val second (get seen 3 0))
  (assert (= first (RunnersChange :revision first.revision :changed True)) first)
  (assert (> first.revision revision) #(first revision))
  (assert (= (get seen 2 1) (get seen 1)) seen)
  (assert (isinstance second RunnersChange) second)
  (assert (not second.changed) second)
  (assert (<= 1500 (- (get seen 3 1) (get seen 4)) 2500) seen))


(defk wait-without-route []
  {:pre [] :post [(: % (| RunnersChange RunnersWatchMissing RunnersUnreachable))] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: /watch を 404 にしてから待つ(待つ口の無い旧い coordinator)。"
  (<- (FailRoute "GET" "/watch" 404 100.0))
  (<- answer (AwaitRunnersChange 0 :timeout-seconds 1.0))
  answer)


(deftest test-a-coordinator-without-the-watch-answers-watch-missing
  (<- answer (| RunnersChange RunnersWatchMissing RunnersUnreachable) (sim-cluster (beacons sim-foundation) (wait-without-route) :workers WORKERS))
  (assert (isinstance answer RunnersWatchMissing) answer))


(deftest test-the-production-client-reads-the-watch-the-same-way
  (val changed (DetachedClient "http://coord" "r" :transport (httpx.MockTransport (fn [request] (httpx.Response 200 :json {"revision" 9 "changed" True})))))
  (assert (= (.runners-change changed 3 1.0) (RunnersChange :revision 9 :changed True)))
  (val missing (DetachedClient "http://coord" "r" :transport (httpx.MockTransport (fn [request] (httpx.Response 404 :json {"error" "知らない"})))))
  (assert (isinstance (.runners-change missing 3 1.0) RunnersWatchMissing))
  (val cut (DetachedClient "http://coord" "r" :transport (httpx.MockTransport (fn [request] (raise (httpx.ConnectError "切れた" :request request))))))
  (assert (isinstance (.runners-change cut 3 1.0) RunnersUnreachable)))


;; --- worker の生死は版に入る(#1934 の決め直し — Worker の status の live)-------------------------------------------

(defk worker-events []
  {:pre [] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "出来事の記録のうち Worker の行の数を読むため(生きている間の heartbeat で増えないことを見る)。"
  (<- view dict (ReadCoordinator "/events"))
  (len (lfor e (get view "events") :if (= (get e "kind") "Worker") e)))


(defk live-of [name]
  {:pre [(: name str)] :post [(: % bool)] :tags {:context "doeff-cluster-test" :role "program"}}
  "coordinator の名簿で worker name が生きているかを読むため。"
  (<- view dict (ReadCoordinator "/state"))
  (bool (get (get (get view "workers") name) "live")))


(defk first-change [after]
  {:pre [(: after int)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "版が after から変わるまで待ちを重ね(1 回 10 秒まで・6 回 = 60 秒で諦める)、#(変わった答え か None 刻) を返すため。"
  (var seen after)
  (var answer None)
  (var tries 0)
  (while (and (is answer None) (< tries 6))
    (<- got RunnersChange (AwaitRunnersChange seen :timeout-seconds 10.0))
    (:= tries (+ tries 1))
    (if got.changed (:= answer got) (:= seen got.revision)))
  (<- at int (now-epoch-ms))
  #(answer at))


(defk death-and-return []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 落ち着いた後、beacon を持たない worker の生きている間の 20 秒(待ちは変わらない・Worker の記録は増えない)を見て、その
   worker を殺し、死んだと数えられた拍の変化で待ちが起きることと、起こし直した拍の変化で起きることを読む。"
  (<- (Delay 12.0))
  (<- view dict (ReadCoordinator "/state"))
  (val holder (get (get (get view "placements") "beacon") "worker"))
  (val other (if (= holder "w1") "w2" "w1"))
  (<- events-before int (worker-events))
  (<- quiet-a RunnersChange (AwaitRunnersChange (get view "revision") :timeout-seconds 10.0))
  (<- quiet-b RunnersChange (AwaitRunnersChange quiet-a.revision :timeout-seconds 10.0))
  (<- events-after int (worker-events))
  (<- killed-at int (now-epoch-ms))
  (<- (KillWorker other))
  (<- before-death dict (ReadCoordinator "/state"))
  (<- death tuple (first-change (get before-death "revision")))
  (<- dead-live bool (live-of other))
  (<- dead-view dict (ReadCoordinator "/state"))
  (<- waiter Task (Spawn (first-change (get dead-view "revision"))))
  (<- (Delay 3.0))
  (<- started-at int (now-epoch-ms))
  (<- (StartWorker other))
  (<- back tuple (Wait waiter))
  (<- back-live bool (live-of other))
  #(quiet-a quiet-b events-before events-after killed-at death dead-live started-at back back-live))


(deftest test-a-worker-death-and-return-advance-the-version-but-live-heartbeats-do-not
  (<- seen tuple (sim-cluster (beacons sim-foundation) (death-and-return) :workers WORKERS))
  (val quiet-a (get seen 0))
  (val quiet-b (get seen 1))
  ;; 生きている間の heartbeat では版は進まず、Worker の記録の行も増えない(記録の行が増えすぎない)。
  (assert (not quiet-a.changed) quiet-a)
  (assert (not quiet-b.changed) quiet-b)
  (assert (= (get seen 2) (get seen 3)) seen)
  ;; 死んだ拍: 最後の heartbeat から lease(10 秒)の後の拍で版が進み、待ちが起きる — その時にはもう死んだと数えられている。
  (val killed-at (get seen 4))
  (val death-at (get seen 5 1))
  (assert (get (get seen 5) 0) seen)
  (assert (not (get seen 6)) "起きた時には死んだと数えられている")
  (assert (<= (- TIMING.lease-ms 3000) (- death-at killed-at) (+ TIMING.lease-ms 1000)) #((- death-at killed-at) seen))
  ;; 戻った拍: 起こし直した worker の最初の heartbeat の拍で版が進み、待ちが起きる(上限まで待たない)。
  (val started-at (get seen 7))
  (val back-at (get seen 8 1))
  (assert (get seen 9) "起きた時には生きていると数えられている")
  (assert (<= 0 (- back-at started-at) 1000) #((- back-at started-at) seen)))
