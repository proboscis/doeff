;; worker の作業の disk の空きの予告。
;; 2026-10-10 13:39 JST に zeus の worker の空きが新しい版の準備の下限 WORKER_ENV_MIN_FREE_GIB(25 GiB)を切り、zeus へ置く job の新しい版の
;; 準備が全部止まった。heartbeat の envCapacity は exhausted / ok の 2 語だけで、下限を切ってから分かった(1 時間に約 5 GB 減っていた)。
;;   (1) env-capacity は下限 + NEAR-MARGIN-BYTES(20 GB)を切った空きに near を名乗る(下限を切れば今までどおり exhausted・下限 0 は ok)
;;   (2) coordinator の Worker の資源の status は envCapacity が ok でない間だけ載せる(読み手が GET /resources/Worker で読む —
;;       ok の worker の status の形と版は以前と同じ)
;; 失敗ケース = near を名乗らない(下限を切るまで ok)・status に載せない(読み手が予告を読めない)。
(require doeff-hy.macros [deftest <- val])
(import doeff_cluster.worker.core.env_upkeep [env-capacity])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState WorkerInfo])
(import doeff_cluster.coordinator.core.resource_policy [worker-row])

;; 下限(本番の既定 25 GiB)と、下限の上の空き(byte)。
(val MIN-FREE (* 25 1024 1024 1024))
(val GB (** 10 9))


(deftest test-env-capacity-names-near-within-twenty-gb-above-the-floor
  (<- below str (env-capacity (- MIN-FREE 1) MIN-FREE))
  (<- near str (env-capacity (+ MIN-FREE (* 10 GB)) MIN-FREE))
  (<- edge str (env-capacity (+ MIN-FREE (* 20 GB)) MIN-FREE))
  (<- ok str (env-capacity (+ MIN-FREE (* 30 GB)) MIN-FREE))
  (<- unset str (env-capacity 0 0))
  (assert (= #(below near edge ok unset) #("exhausted" "near" "ok" "ok" "ok"))))


(deftest test-the-worker-status-carries-env-capacity-only-when-not-ok
  (val near (WorkerInfo "w1" #("net") 2 0 #() None #() :platform "linux-x86_64" :env-capacity "near" :task-reserve 0))
  (val ok (WorkerInfo "w2" #("net") 2 0 #() None #() :platform "linux-x86_64" :env-capacity "ok" :task-reserve 0))
  (val state (ClusterState :workers {"w1" near "w2" ok}))
  (assert (= (.get (get (worker-row state near 0) "status") "envCapacity") "near"))
  (assert (not-in "envCapacity" (get (worker-row state ok 0) "status"))))
