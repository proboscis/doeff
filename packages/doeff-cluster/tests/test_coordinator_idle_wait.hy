;; 本番の coordinator も、要求の無い間は次の期限か要求か停止まで 1 本で待つ(#3865)。
;;
;; - 本番の組(production-handlers)は、静かな状態の IdleNextRequests で、受付の箱を 1 秒で打ち切らずに待つ(期限が無ければ期限なし)。
;; - 受付の箱の probe は、待つと定めた刻までの待ちを止まりと数えない(静かな間に /readyz・/livez が落ちて container が作り直されない)。
;;   歩の中に閾値より長く居る時は、今までどおり止まりと数える。
(require doeff-hy.macros [deftest defk <- val var])
(import queue)
(import threading)
(import doeff [with-handlers])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState ClusterNaming IdleProbe IdleNextRequests IdleTaken])
(import doeff_cluster.coordinator.entry.handler_sets [production-handlers MemoryWalStore])
(import doeff_cluster.coordinator.protocol.kube [KubeReadBatches kube-unavailable])
(import doeff_cluster.foundation.coordinator_inbox [RequestInbox RawRequest ReplySlot StopState])
(import doeff_time [Delay])
(import doeff_cluster.sim.local [wall-sim-cluster ClientLink SimLink SimWorker])
(import doeff_cluster.worker.intent.worker_model [WorkerPolicy])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.sim_programs [quitters])


(val QUIET-PROBE (IdleProbe (ClusterState) (ClusterTiming) (ClusterNaming)))


(defclass ScriptedInbox [RequestInbox]
  "本番の受付の箱の取り出しだけを記録に替えた箱: 調停ループが渡した待ちの秒を並べ、待たずに要求を 1 件返す。"
  (defn #^ None __init__ [self]
    (.__init__ (super) 0)
    (setv self.timeouts [])
    None)

  (defn #^ list take [self #^ (| float None) timeout #^ int limit]
    (.append self.timeouts timeout)
    [(RawRequest "GET" "/state" {} None (ReplySlot) None "test")]))


(defk take-quiet [probe]
  {:pre [(: probe IdleProbe)] :post [(: % (| list IdleTaken))] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書きの調停ループ: 要求の無い新しい状態の材料を添えて、coordinator の歩と同じ IdleNextRequests を 1 回出すため。"
  (<- taken (| list IdleTaken) (IdleNextRequests 1.0 :idle probe))
  taken)


(deftest test-the-production-coordinator-waits-without-a-tick-while-nothing-is-due
  ;; 新しい状態には期限が 1 つも無い(worker も task も Rollout も無い)。本番の組は受付の箱を期限なしで待つ。
  ;; 直す前は、材料 idle を読まずに 1 秒で打ち切る(#1383 の決定で本番だけ 1 秒を残していた)。
  (val inbox (ScriptedInbox))
  (val handlers (production-handlers inbox (MemoryWalStore) (StopState)
                                     (kube-unavailable "テストの coordinator は k8s を持たない" (KubeReadBatches))))
  (<- (with-handlers handlers (take-quiet QUIET-PROBE)))
  (assert (= inbox.timeouts [None]) inbox.timeouts))


(defclass EnteredQueue [queue.Queue]
  "受付の列: 取り手が待ちに入った事を Event で知らせる(probe を、取り手が待っている最中に読むため)。"
  (defn #^ None __init__ [self]
    (.__init__ (super))
    (setv self.entered (threading.Event))
    None)

  (defn #^ object get [self [block True] [timeout None]]
    (.set self.entered)
    (.get (super) block timeout)))


(deftest test-the-probe-does-not-count-a-planned-wait-as-a-stall
  ;; 取り手が 300 秒の待ちに入った後、45 秒目の /readyz と 130 秒目の /livez は 200(待つと定めた刻の前なので生きている)。
  ;; 直す前は「最後に取りに来てから」の秒で判じるので、45 秒目の /readyz が 503(閾値 30 秒)・130 秒目の /livez が 503(閾値 120 秒)。
  (val now [1000.0])
  (val inbox (RequestInbox 0 :clock (fn [] (get now 0))))
  (setv inbox.queue (EnteredQueue))
  (val taker (threading.Thread :target (fn [] (.take inbox 300.0 1)) :daemon True))
  (.start taker)
  (assert (.wait inbox.queue.entered 5.0))
  (setv (get now 0) 1045.0)
  (val ready (get (.probe inbox "/readyz") 0))
  (setv (get now 0) 1130.0)
  (val live (get (.probe inbox "/livez") 0))
  (.put inbox.queue (RawRequest "GET" "/state" {} None (ReplySlot) None "test"))
  (.join taker 5.0)
  (assert (= #(ready live) #(200 200)) #(ready live)))


(deftest test-the-probe-still-reports-a-loop-stuck-inside-a-step
  ;; 守り: 取り手が要求を取って歩に入った後、31 秒戻らなければ /readyz は 503(閾値 30 秒)・121 秒で /livez も 503。
  (val now [1000.0])
  (val inbox (RequestInbox 0 :clock (fn [] (get now 0))))
  (.put inbox.queue (RawRequest "GET" "/state" {} None (ReplySlot) None "test"))
  (.take inbox 300.0 1)
  (setv (get now 0) 1031.0)
  (val ready (get (.probe inbox "/readyz") 0))
  (setv (get now 0) 1121.0)
  (val live (get (.probe inbox "/livez") 0))
  (assert (= #(ready live) #(503 503)) #(ready live)))


;; --- 模擬の環境(壁の時計)— 要求の無い間の起きの数 ----------------------------------------------------------------

(val QUIET-SECONDS 3.0)
(val RESTING-WORKERS #((SimWorker :name "w1" :provides (frozenset ["cluster-net"]) :task-reserve 0)))
(val RESTING-POLICY (WorkerPolicy :tick-seconds 10.0 :restart-backoff-ms 1000000000 :restart-backoff-max-ms 1000000000))


(defk takes-while-quiet [seconds]
  {:pre [(: seconds float)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 起動の後 1 秒置き、要求の来ない seconds 秒の間に coordinator が受け口から取った回数(歩の数)を読むため。"
  (<- (Delay 1.0))
  (<- link SimLink (ClientLink))
  (val before link.queue.takes)
  (<- (Delay seconds))
  (- link.queue.takes before))


(deftest test-a-quiet-coordinator-does-not-wake-on-the-wall-clock
  ;; worker 1 台・拍 10 秒(この 3 秒の間に heartbeat は来ない)・要求の無い 3 秒: coordinator は起きない(歩 0)。
  ;; 直す前は 1 秒ごとに起きる(3 秒で 3 歩 前後)。
  (<- taken int (wall-sim-cluster (quitters sim-foundation) (takes-while-quiet QUIET-SECONDS)
                                  :workers RESTING-WORKERS :policy RESTING-POLICY))
  (assert (= taken 0) taken))
