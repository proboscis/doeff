;; coordinator の受け口の要求の本文を道ごとの型に解く 1 点(coordinator/protocol/request_bodies — #2445): 型にした道(lease の操作・
;; task の結果・drain の頼み)の本文は、欠けた欄・型の違う値・JSON の object でない本文を、判断に入る前に 400 と欄の理由で断る。
;; 知らない欄は読み捨てる(送り手の版が新しい欄を足しても断らない)。まだ型にしていない道は JSON の object のまま判断へ渡る。
(require doeff-hy.macros [deftest <- val])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState])
(import doeff_cluster.coordinator.intent.request_bodies [LeaseBody TaskResultBody DrainBody BodyMalformed])
(import doeff_cluster.coordinator.protocol.request_bodies [body-of responded])
(import doeff_cluster.foundation.coordinator_inbox [http-request])

(val T (ClusterTiming))


(deftest test-typed-routes-read-their-bodies-into-their-types
  (<- lease (body-of (http-request "POST" "/leases/app-writer" {} {"op" "claim" "token" "a/1/" "ttlMs" 500 "later" True})))
  (assert (= lease (LeaseBody :op "claim" :token "a/1/" :permits 1 :ttl-ms 500)) lease)
  (<- result (body-of (http-request "POST" "/tasks/t1/result" {} {"worker" "w" "result" "R" "instance" "w-p1"})))
  (assert (= result (TaskResultBody :worker "w" :result "R" :instance "w-p1" :format 1)) result)
  (<- drain (body-of (http-request "POST" "/workers/zeus/drain" {} None)))
  (assert (= drain (DrainBody)) drain)
  ;; まだ型にしていない道は JSON の object のまま。
  (<- board (body-of (http-request "PUT" "/board/k" {} {"value" 1})))
  (assert (= board {"value" 1}) board))


(deftest test-malformed-bodies-are-refused-with-the-field-before-the-decision
  (<- missing (body-of (http-request "POST" "/leases/app-writer" {} {"op" "claim"})))
  (assert (isinstance missing BodyMalformed) missing)
  (assert (in "token" missing.reason) missing)
  (<- wrong (body-of (http-request "POST" "/workers/zeus/drain" {} {"boot" 7})))
  (assert (in "boot" wrong.reason) wrong)
  (<- listed (body-of (http-request "POST" "/tasks/t1/result" {} ["w" "R"])))
  (assert (in "JSON の object" listed.reason) listed)
  ;; 判断に入る前に 400 で断り、状態を変えない。
  (val state (ClusterState))
  (val answer (responded state (http-request "POST" "/tasks/t1/result" {} {"worker" "w" "result" 5}) 1000 T))
  (assert (is (get answer 0) state) answer)
  (assert (= (get answer 1) 400) answer)
  (assert (in "result" (get answer 2 "error")) answer))
