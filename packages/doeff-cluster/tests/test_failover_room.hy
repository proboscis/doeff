;; 枠の file の行を N+1 の判断の worker にする読み room-file-workers(card ki-5996b0cce581)。
;; 能力は worker 自身の名乗り(起動の引数の能力 host-<名>・雛形の能力・足す能力に、行の組の印を足す)と同じで、枠・予約・memory の予算は
;; 行の値。失敗ケース = worker が名乗らない行(taskReserve の無い行・範囲の外)を既定の値で通す・足す能力を行の無い worker に当てて
;; 黙って捨てる・heartbeat の名乗りと違う能力を作る。
(require doeff-hy.macros [deftest <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import json)
(import pytest)
(import doeff_cluster [WorkerInfo])
(import doeff_cluster [ExtraProvides FailoverWorker])
(import doeff_cluster [room-file-workers])
(import doeff_cluster.worker.core.room_rules [TEMPLATE-PROVIDES])
(import doeff_cluster.worker.protocol.heartbeat [heartbeat-body])

(val ROOMS {"zeus" {"capacity" 40 "taskReserve" 6 "backgroundRoom" 4 "warmServices" True "memoryMiB" 60000}
            "k3s-1" {"capacity" 2 "taskReserve" 0 "backgroundRoom" 0 "pools" ["pool-k3s-1-k3s-2"] "memoryMiB" 1792}
            "atlas" {"capacity" 17 "taskReserve" 3 "backgroundRoom" 0 "pools" ["pool-b" "pool-a"] "memoryMiB" 8960}})


(deftest test-every-row-becomes-a-worker-with-the-provides-it-announces
  (<- workers (get tuple #(FailoverWorker ...)) (room-file-workers (json.dumps ROOMS) #((ExtraProvides :name "zeus" :provides #("agent" "boundary-x")))))
  (assert (= workers
             #((FailoverWorker :info (WorkerInfo :name "atlas" :provides #("host-atlas" "host-systemd-readable" "pool-a" "pool-b") :background-room 0
                                                 :last-seen-ms 0 :exclusive #() :task-reserve 3 :memory-mib 8960)
                               :memory-mib 8960)
               (FailoverWorker :info (WorkerInfo :name "k3s-1" :provides #("host-k3s-1" "host-systemd-readable" "pool-k3s-1-k3s-2") :background-room 0
                                                 :last-seen-ms 0 :exclusive #() :task-reserve 0 :memory-mib 1792)
                               :memory-mib 1792)
               (FailoverWorker :info (WorkerInfo :name "zeus" :provides #("agent" "boundary-x" "host-systemd-readable" "host-zeus") :background-room 4
                                                 :last-seen-ms 0 :exclusive #() :task-reserve 6 :memory-mib 60000)
                               :memory-mib 60000)))
         workers))


(deftest test-the-provides-are-what-the-heartbeat-announces
  ;; 照らしの能力と heartbeat の名乗りが同じ和(起動の引数の能力 + 行の組の印)— 片方だけ変わってずれない。
  (<- workers (get tuple #(FailoverWorker ...)) (room-file-workers (json.dumps ROOMS) #()))
  (val atlas (get workers 0))
  (<- body dict (heartbeat-body :name "atlas" :provides (tuple (sorted #("host-atlas" #* TEMPLATE-PROVIDES))) :exclusive #() :node "atlas"
                                :background-room 0 :task-reserve 3 :versions {} :statuses [] :endpoint "" :boot "b" :boot-at 0 :tools {} :kept #()
                                :pools #("pool-a" "pool-b")))
  (assert (= (get body "provides") (list atlas.info.provides)) #(body atlas)))


(deftest test-a-row-the-worker-would-not-announce-stops-the-reading
  ;; worker が malformed と読む行は、既定の値で埋めずに名指して止まる。
  (for [#(rows word) [[{"eos" {"capacity" 3}} "worker eos の行の taskReserve が 0 以上の整数でない"]
                      [{"eos" {"capacity" 3 "taskReserve" 0 "memoryMiB" 3840}} "worker eos の行の backgroundRoom が 0 以上の整数でない"]
                      [{"eos" {"taskReserve" 0 "backgroundRoom" 0 "memoryMiB" 3840 "pools" ["resident-roomy"]}} "worker eos の pools の"]
                      [{"eos" {"taskReserve" 0 "backgroundRoom" 0 "memoryMiB" -1}} "worker eos の行の memoryMiB が 0 以上の整数でない: -1"]]]
    (with [error (pytest.raises ValueError)]
      (! (room-file-workers (json.dumps rows) #())))
    (assert (in word (str error.value)) #(word (str error.value)))))


(deftest test-a-file-without-rows-and-extras-without-a-row-stop-the-reading
  (for [#(text extras word) [["{" #() "JSON の object でない"]
                             ["[]" #() "JSON の object でない"]
                             ["{}" #() "行が 1 つも無い"]
                             [(json.dumps ROOMS) #((ExtraProvides :name "eos" :provides #("agent"))) "eos の行が枠の file に無い"]]]
    (with [error (pytest.raises ValueError)]
      (! (room-file-workers text extras)))
    (assert (in word (str error.value)) #(word (str error.value)))))
