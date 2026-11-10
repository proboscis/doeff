;; 枠の file の行を N+1 の判断の worker にする読み room-file-workers。
;; 能力は worker 自身の名乗り(起動の引数の能力 host-<名>・雛形の能力・足す能力に、行の組の印を足す)と同じで、memory の予算は行の値。
;; task の空きは機体の測った空きで、枠の file に無い(照らしの worker は測った空きを持たない)。失敗ケース = worker が名乗らない行
;; (memoryMiB の無い行・範囲の外)を既定の値で通す・足す能力を行の無い worker に当てて黙って捨てる・heartbeat の名乗りと違う能力を作る。
(require doeff-hy.macros [deftest <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import json)
(import pytest)
(import doeff_cluster [WorkerInfo])
(import doeff_cluster [ExtraProvides FailoverWorker])
(import doeff_cluster [room-file-workers])
(import doeff_cluster [TEMPLATE-PROVIDES])
(import doeff_cluster._worker.protocol.heartbeat [heartbeat-body])

;; node-h の行は旧い版の file の形(手書きの task の枠 capacity・taskReserve・backgroundRoom を持つ)— 読まずに通す。
(val ROOMS {"node-h" {"capacity" 40 "taskReserve" 6 "backgroundRoom" 4 "warmServices" True "memoryMiB" 60000}
            "node-e" {"pools" ["pool-node-e-node-f"] "memoryMiB" 1792}
            "node-a" {"pools" ["pool-b" "pool-a"] "memoryMiB" 8960}})


(deftest test-every-row-becomes-a-worker-with-the-provides-it-announces
  (<- workers (get tuple #(FailoverWorker ...)) (room-file-workers (json.dumps ROOMS) #((ExtraProvides :name "node-h" :provides #("agent" "boundary-x")))))
  (assert (= workers
             #((FailoverWorker :info (WorkerInfo :name "node-a" :provides #("host-node-a" "host-systemd-readable" "pool-a" "pool-b")
                                                 :last-seen-ms 0 :exclusive #() :memory-mib 8960)
                               :memory-mib 8960)
               (FailoverWorker :info (WorkerInfo :name "node-e" :provides #("host-node-e" "host-systemd-readable" "pool-node-e-node-f")
                                                 :last-seen-ms 0 :exclusive #() :memory-mib 1792)
                               :memory-mib 1792)
               (FailoverWorker :info (WorkerInfo :name "node-h" :provides #("agent" "boundary-x" "host-systemd-readable" "host-node-h")
                                                 :last-seen-ms 0 :exclusive #() :memory-mib 60000)
                               :memory-mib 60000)))
         workers))


(deftest test-the-provides-are-what-the-heartbeat-announces
  ;; 照らしの能力と heartbeat の名乗りが同じ和(起動の引数の能力 + 行の組の印)— 片方だけ変わってずれない。
  (<- workers (get tuple #(FailoverWorker ...)) (room-file-workers (json.dumps ROOMS) #()))
  (val node-a (get workers 0))
  (<- body dict (heartbeat-body :name "node-a" :provides (tuple (sorted #("host-node-a" #* TEMPLATE-PROVIDES))) :exclusive #() :node "node-a"
                                :versions {} :statuses [] :endpoint "" :boot "b" :boot-at 0 :tools {} :kept #()
                                :pools #("pool-a" "pool-b")))
  (assert (= (get body "provides") (list node-a.info.provides)) #(body node-a)))


(deftest test-a-row-the-worker-would-not-announce-stops-the-reading
  ;; worker が malformed と読む行は、既定の値で埋めずに名指して止まる。
  (for [#(rows word) [[{"node-b" {"capacity" 3 "taskReserve" 0}} "worker node-b の行の memoryMiB が 0 以上の整数でない: None"]
                      [{"node-b" {"memoryMiB" 3840 "pools" ["resident-roomy"]}} "worker node-b の pools の"]
                      [{"node-b" {"memoryMiB" -1}} "worker node-b の行の memoryMiB が 0 以上の整数でない: -1"]]]
    (with [error (pytest.raises ValueError)]
      (! (room-file-workers (json.dumps rows) #())))
    (assert (in word (str error.value)) #(word (str error.value)))))


(deftest test-a-file-without-rows-and-extras-without-a-row-stop-the-reading
  (for [#(text extras word) [["{" #() "JSON の object でない"]
                             ["[]" #() "JSON の object でない"]
                             ["{}" #() "行が 1 つも無い"]
                             [(json.dumps ROOMS) #((ExtraProvides :name "node-b" :provides #("agent"))) "node-b の行が枠の file に無い"]]]
    (with [error (pytest.raises ValueError)]
      (! (room-file-workers text extras)))
    (assert (in word (str error.value)) #(word (str error.value)))))
