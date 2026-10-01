;; coordinator の受け口の要求の本文を道ごとの型に解く 1 点(coordinator/protocol/request_bodies — #2445): 型にした道(lease の操作・
;; task の結果・drain の頼み)の本文は、欠けた欄・型の違う値・JSON の object でない本文を、判断に入る前に 400 と欄の理由で断る。
;; 知らない欄は読み捨てる(送り手の版が新しい欄を足しても断らない)。まだ型にしていない道は JSON の object のまま判断へ渡る。
(require doeff-hy.macros [deftest <- val])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState])
(import doeff_cluster.coordinator.intent.request_bodies [LeaseBody TaskResultBody DrainBody BoardWrite BodyMalformed])
(import doeff_cluster.coordinator.protocol.request_bodies [body-of responded])
(import doeff_cluster.shared.protocol.inbox [http-request])

(val T (ClusterTiming))


(deftest test-typed-routes-read-their-bodies-into-their-types
  (<- lease (body-of (http-request "POST" "/leases/app-writer" {} {"op" "claim" "token" "a/1/" "ttlMs" 500 "later" True})))
  (assert (= lease (LeaseBody :op "claim" :token "a/1/" :permits 1 :ttl-ms 500)) lease)
  (<- result (body-of (http-request "POST" "/tasks/t1/result" {} {"worker" "w" "result" "R" "instance" "w-p1"})))
  (assert (= result (TaskResultBody :worker "w" :result "R" :instance "w-p1" :format 1)) result)
  (<- drain (body-of (http-request "POST" "/workers/zeus/drain" {} None)))
  (assert (= drain (DrainBody)) drain)
  ;; まだ型にしていない道は JSON の object のまま。
  (<- untyped (body-of (http-request "GET" "/state" {} {"x" 1})))
  (assert (= untyped {"x" 1}) untyped))


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


(deftest test-service-reports-read-their-sender-and-payload-into-types
  ;; readiness と計器の報告(送り手の process の世代と中身)— 欠けた worker・型の違う ready・数でない計器は判断の前に断る。
  (<- ready (body-of (http-request "POST" "/resources/Service/web/readiness" {}
                                   {"worker" "w" "revision" "r" "ready" True "pid" 7 "specHash" "h" "attempt" "1"})))
  (assert (= #(ready.worker ready.ready ready.pid ready.spec-hash ready.attempt ready.role) #("w" True 7 "h" "1" None)) ready)
  (<- metered (body-of (http-request "POST" "/resources/Service/web/metrics" {}
                                     {"worker" "w" "revision" "r" "metrics" {"counters" {"done" 3} "durations" {"turn" {"sum" 1.5 "count" 2}}}})))
  (assert (= (. metered metrics counters) {"done" 3}) metered)
  (assert (= (. (get (. metered metrics durations) "turn") count) 2) metered)
  (<- unnamed (body-of (http-request "POST" "/resources/Service/web/readiness" {} {"revision" "r" "ready" True})))
  (assert (in "worker" unnamed.reason) unnamed)
  (<- worded (body-of (http-request "POST" "/resources/Service/web/readiness" {} {"worker" "w" "revision" "r" "ready" "yes"})))
  (assert (in "ready" worded.reason) worded)
  (<- textual (body-of (http-request "POST" "/resources/Service/web/metrics" {} {"worker" "w" "revision" "r" "metrics" {"gauges" {"g" "1"}}})))
  (assert (in "gauges" textual.reason) textual))



(deftest test-a-board-write-tells-a-missing-expect-from-a-null-expect
  ;; expect の欄が無ければ比べない・null なら「行が無い時だけ書く」— 本文の型の既定値では分けられないので、解く所が欄の在否を印にする。
  (<- free (body-of (http-request "PUT" "/board/k" {} {"value" {"n" 1}})))
  (assert (isinstance free BoardWrite) free)
  (assert (= #(free.value-given free.expect-given free.body.value) #(True False {"n" 1})) free)
  (<- fresh (body-of (http-request "PUT" "/board/k" {} {"value" 2 "expect" None})))
  (assert (= #(fresh.expect-given fresh.body.expect) #(True None)) fresh)
  (<- dropping (body-of (http-request "PUT" "/board/k" {} {"delete" True})))
  (assert (= #(dropping.value-given dropping.body.delete) #(False True)) dropping)
  (<- wordy (body-of (http-request "PUT" "/board/k" {} {"value" 1 "ttlSeconds" "60"})))
  (assert (in "ttlSeconds" wordy.reason) wordy)
  ;; 判断へ: null の expect は行が在れば 409・行が無ければ書く。値の無い書きは 400。
  (val state (ClusterState))
  (val first (responded state (http-request "PUT" "/board/k" {} {"value" 1 "expect" None}) 1000 T))
  (assert (= (get first 1) 200) first)
  (val again (responded (get first 0) (http-request "PUT" "/board/k" {} {"value" 2 "expect" None}) 1000 T))
  (assert (= (get again 1) 409) again)
  (val empty (responded state (http-request "PUT" "/board/k" {} {"ttlSeconds" 5}) 1000 T))
  (assert (= (get empty 1) 400) empty))


(deftest test-a-heartbeat-body-is-read-into-its-type-and-refused-before-the-decision
  ;; heartbeat の本文の形の検め(前は判断の中の手書きの検め)は解く所で: 空の名・欠けた失敗の行の kind・文字列の容量は 400。旧い labels は判断が断る。
  (<- beat (body-of (http-request "POST" "/heartbeat" {} {"name" "w" "provides" ["net"] "envs" {"ready" ["k1"]} "statuses" [{"name" "a"}]})))
  (assert (= #(beat.name beat.provides beat.envs.ready beat.capacity (len beat.statuses)) #("w" #("net") #("k1") 10 1)) beat)
  (<- nameless (body-of (http-request "POST" "/heartbeat" {} {"name" "" "provides" ["net"]})))
  (assert (isinstance nameless BodyMalformed) nameless)
  (<- kindless (body-of (http-request "POST" "/heartbeat" {} {"name" "w" "envs" {"failed" [{"key" "k"}]}})))
  (assert (in "kind" kindless.reason) kindless)
  (<- wordy (body-of (http-request "POST" "/heartbeat" {} {"name" "w" "capacity" "10"})))
  (assert (in "capacity" wordy.reason) wordy)
  (val old (responded (ClusterState) (http-request "POST" "/heartbeat" {} {"name" "w" "labels" {"kind" "mac"}}) 1000 T))
  (assert (= (get old 1) 400) old)
  (assert (in "labels" (get old 2 "error")) old))


(deftest test-a-resource-declaration-envelope-is-read-into-its-type
  ;; 資源の宣言の包み(name・spec・resourceVersion)— object でない spec・数の名・文字列の版は判断の前に 400。spec の中身は判断が読む。
  (<- made (body-of (http-request "POST" "/resources/Service" {} {"name" "web" "spec" {"revision" "r"}})))
  (assert (= #(made.name made.spec made.resource-version) #("web" {"revision" "r"} None)) made)
  (<- edited (body-of (http-request "PUT" "/resources/Service/web" {} {"spec" {} "resourceVersion" 3})))
  (assert (= edited.resource-version 3) edited)
  (<- listed (body-of (http-request "POST" "/resources/Service" {} {"name" "web" "spec" ["r"]})))
  (assert (in "spec" listed.reason) listed)
  (<- numbered (body-of (http-request "POST" "/resources/Service" {} {"name" 7})))
  (assert (in "name" numbered.reason) numbered)
  (<- worded (body-of (http-request "PUT" "/resources/Service/web" {} {"resourceVersion" "3"})))
  (assert (in "resourceVersion" worded.reason) worded))


(deftest test-task-and-warm-bodies-are-read-into-their-types
  ;; task と切り離した task は同じ本文の型・温める表は別の型。旧い形の欄(blob)は判断が理由つきで断る。
  (<- task (body-of (http-request "POST" "/tasks" {} {"program" "p" "revision" "r" "needs" ["net"] "leaseSeconds" 5})))
  (<- detached (body-of (http-request "PUT" "/detached/k" {} {"program" "p" "revision" "r" "needs" ["net"]})))
  (assert (= #(task.program task.lease-seconds detached.lease-seconds) #("p" 5 None)) #(task detached))
  (<- warm (body-of (http-request "POST" "/warm" {} {"runtimeEnv" {} "ttlSeconds" 60 "needs" ["net"] "holder" "h"})))
  (assert (= #(warm.ttl-seconds warm.holder) #(60 "h")) warm)
  (<- named (body-of (http-request "POST" "/warm" {} {"holder" 7})))
  (assert (in "holder" named.reason) named)
  (val old (responded (ClusterState) (http-request "POST" "/tasks" {} {"blob" "x" "revision" "r" "needs" ["net"]}) 1000 T))
  (assert (= (get old 1) 400) old)
  (assert (in "blob" (get old 2 "error")) old))


(deftest test-the-old-jobs-body-is-read-into-its-type
  ;; 旧い PUT /jobs の本文(jobs の行の列と送り手)— 列でない jobs は判断の前に 400。
  (<- jobs (body-of (http-request "PUT" "/jobs" {} {"jobs" [{"name" "a"}] "actor" "me"})))
  (assert (= #((len jobs.jobs) jobs.actor) #(1 "me")) jobs)
  (<- single (body-of (http-request "PUT" "/jobs" {} {"jobs" {"name" "a"}})))
  (assert (in "jobs" single.reason) single))
