;; worker の生死の出来事の判断の失敗ケース(#3864): coordinator が前後の状態の沈黙の集合(ClusterState.silent)を比べて出す
;; WorkerGone・WorkerBack(cluster_policy.liveness-moves)と、coordinator が起きた時に出す今の状態(cluster_policy.liveness-now)。
;;   1 期限を越えた最初の歩で WorkerGone が 1 度だけ出る(後の歩で重ねて出ない)。
;;   2 期限の前(期限の刻ちょうどまで)には出ない。
;;   3 heartbeat が戻ると WorkerBack が 1 度出る。
;;   4 長い沈黙で名簿から消えた worker は WorkerBack にならない。
;;   起き直し(D): 今の状態は、沈黙の worker の WorkerGone と生きている worker の WorkerBack。
(require doeff-hy.macros [defk deftest <- val])
(import doeff [run])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.shared.protocol.inbox [http-request])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState])
(import doeff_cluster.coordinator.intent.worker_notices [WorkerBack WorkerGone])
(import doeff_cluster.coordinator.core.api_policy [tick])
(import doeff_cluster.coordinator.core.cluster_policy [register-heartbeat liveness-moves liveness-now WORKER-FORGET-MS])
(import doeff_cluster.coordinator.protocol.request_bodies [body-of responded])

(val T (ClusterTiming))
(val V {"python" "3.14.0" "doeff" "1"})
;; worker w が最後に heartbeat を送った刻と、その生死の期限(最後の heartbeat + lease-ms)。
(val SEEN 1000)
(val DEADLINE (+ SEEN T.lease-ms))


(defk beat-body [boot]
  {:pre [(: boot str)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "process の起動の印 boot を名乗る worker w の heartbeat の本文を組むため。"
  {"name" "w" "boot" boot "provides" ["net"] "capacity" 10 "taskReserve" 0 "versions" V "statuses" []})


(defk named []
  {:pre [] :post [(: % ClusterState)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "worker w(起動の印 b1)が刻 SEEN に名乗っただけの状態(置き先も task も無い)を、検の出発点として組むため。"
  (<- body dict (beat-body "b1"))
  (get (responded (ClusterState) (! (http-request "POST" "/heartbeat" {} body)) SEEN T) 0))


(defk heard-at [state boot now]
  {:pre [(: state ClusterState) (: boot str) (: now int)] :post [(: % ClusterState)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "worker w が刻 now に起動の印 boot で heartbeat を送り、同じ刻で調停した状態を組むため。"
  (<- body dict (beat-body boot))
  (<- heard ClusterState (register-heartbeat state (run (body-of (! (http-request "POST" "/heartbeat" {} body)))) now))
  (<- settled ClusterState (tick heard now T))
  settled)


(deftest test-a-worker-past-its-deadline-is-told-gone-once
  ;; 期限の刻ちょうどまでは生きている(alive は now <= 期限)。越えた最初の歩で WorkerGone が 1 度、その後の歩では出ない。
  (val start (! (named)))
  (val at-deadline (! (tick start DEADLINE T)))
  (val past (! (tick at-deadline (+ DEADLINE 1) T)))
  (val later (! (tick past (+ DEADLINE 5000) T)))
  (assert (= (! (liveness-moves start at-deadline T)) #()) "期限の刻ちょうどでは出さない")
  (assert (= (! (liveness-moves at-deadline past T)) #((WorkerGone :worker "w" :boot "b1" :deadline-ms DEADLINE)))
          "期限を越えた歩で WorkerGone を 1 つ")
  (assert (= (! (liveness-moves past later T)) #()) "沈黙が続く歩では重ねて出さない"))


(deftest test-a-worker-within-its-deadline-is-told-nothing
  (val start (! (named)))
  (val within (! (tick start (- DEADLINE 1) T)))
  (assert (= (! (liveness-moves start within T)) #()) "期限の前には出さない"))


(deftest test-a-silent-worker-that-beats-again-is-told-back-once
  ;; 新しい process(起動の印 b2)で heartbeat が戻ると、その起動の印と最後の heartbeat の刻で WorkerBack が 1 度。
  (val past (! (tick (! (named)) (+ DEADLINE 1) T)))
  (val back-at (+ DEADLINE 9000))
  (val revived (! (heard-at past "b2" back-at)))
  (val again (! (tick revived (+ back-at 500) T)))
  (assert (= (! (liveness-moves past revived T)) #((WorkerBack :worker "w" :boot "b2" :seen-ms back-at)))
          "戻った歩で WorkerBack を 1 つ")
  (assert (= (! (liveness-moves revived again T)) #()) "生きている間は重ねて出さない"))


(deftest test-a-worker-forgotten-after-a-long-silence-is-not-told-back
  ;; 長い沈黙(WORKER-FORGET-MS)で名簿から消えた worker は沈黙の集合からも外れるが、戻ったのではない。
  (val past (! (tick (! (named)) (+ DEADLINE 1) T)))
  (val forgotten (! (tick past (+ SEEN WORKER-FORGET-MS 1) T)))
  (assert (not-in "w" forgotten.workers) "長い沈黙の worker は名簿から消える")
  (assert (= (! (liveness-moves past forgotten T)) #()) "名簿から消えた worker は WorkerBack にならない"))


(deftest test-the-current-liveness-tells-every-worker-once
  ;; coordinator が起きた時に出す今の状態: 沈黙の worker は WorkerGone(期限つき)・生きている worker は WorkerBack。
  (val live (! (tick (! (named)) (+ SEEN 10) T)))
  (val past (! (tick live (+ DEADLINE 1) T)))
  (assert (= (! (liveness-now live T)) #((WorkerBack :worker "w" :boot "b1" :seen-ms SEEN))) "生きている worker は WorkerBack")
  (assert (= (! (liveness-now past T)) #((WorkerGone :worker "w" :boot "b1" :deadline-ms DEADLINE))) "沈黙の worker は WorkerGone"))
