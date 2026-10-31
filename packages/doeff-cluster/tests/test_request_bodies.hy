;; coordinator の受け口の要求の本文を道ごとの型に解く 1 点(coordinator/protocol/request_bodies — #2445): 型にした道(lease の操作・
;; task の結果・drain の頼み)の本文は、欠けた欄・型の違う値・JSON の object でない本文を、判断に入る前に 400 と欄の理由で断る。
;; 知らない欄は読み捨てる(送り手の版が新しい欄を足しても断らない)。まだ型にしていない道は JSON の object のまま判断へ渡る。
(require doeff-hy.macros [deftest <- val])
(import doeff_cluster [ClusterTiming])
(import doeff_cluster [ClusterState])
(import doeff_cluster._coordinator.intent.request_bodies [LeaseBody TaskResultBody DrainBody BoardWrite BodyMalformed])
(import doeff_cluster [responded]
        doeff_cluster._coordinator.protocol.request_bodies [body-of])
(import doeff_cluster [http-request])
(import doeff_cluster._worker.protocol.heartbeat [heartbeat-body])
(import doeff_cluster._coordinator.core.cluster_policy [job-to-json])
(import doeff_cluster._coordinator.protocol.state_json [read-service-rows])
(import tests.program_rows [SAMPLE-RUN])

(val T (ClusterTiming))


(deftest test-typed-routes-read-their-bodies-into-their-types
  (<- lease (body-of (! (http-request "POST" "/leases/app-writer" {} {"op" "claim" "token" "a/1/" "ttlMs" 500 "later" True}))))
  (assert (= lease (LeaseBody :op "claim" :token "a/1/" :permits 1 :ttl-ms 500)) lease)
  (<- result (body-of (! (http-request "POST" "/tasks/t1/result" {} {"worker" "w" "result" "R" "instance" "w-p1"}))))
  (assert (= result (TaskResultBody :worker "w" :result "R" :instance "w-p1" :format 1)) result)
  (<- drain (body-of (! (http-request "POST" "/workers/zeus/drain" {} None))))
  (assert (= drain (DrainBody)) drain)
  ;; まだ型にしていない道は JSON の object のまま。
  (<- untyped (body-of (! (http-request "GET" "/state" {} {"x" 1}))))
  (assert (= untyped {"x" 1}) untyped))


(deftest test-malformed-bodies-are-refused-with-the-field-before-the-decision
  (<- missing (body-of (! (http-request "POST" "/leases/app-writer" {} {"op" "claim"}))))
  (assert (isinstance missing BodyMalformed) missing)
  (assert (in "token" missing.reason) missing)
  (<- wrong (body-of (! (http-request "POST" "/workers/zeus/drain" {} {"boot" 7}))))
  (assert (in "boot" wrong.reason) wrong)
  (<- listed (body-of (! (http-request "POST" "/tasks/t1/result" {} ["w" "R"]))))
  (assert (in "JSON の object" listed.reason) listed)
  ;; 判断に入る前に 400 で断り、状態を変えない。
  (val state (ClusterState))
  (val answer (responded state (! (http-request "POST" "/tasks/t1/result" {} {"worker" "w" "result" 5})) 1000 T))
  (assert (is (get answer 0) state) answer)
  (assert (= (get answer 1) 400) answer)
  (assert (in "result" (get answer 2 "error")) answer))


(deftest test-service-reports-read-their-sender-and-payload-into-types
  ;; readiness と計器の報告(送り手の process の世代と中身)— 欠けた worker・型の違う ready・数でない計器は判断の前に断る。
  (<- ready (body-of (! (http-request "POST" "/resources/Service/web/readiness" {}
                                   {"worker" "w" "revision" "r" "ready" True "pid" 7 "specHash" "h" "attempt" "1"}))))
  (assert (= #(ready.worker ready.ready ready.pid ready.spec-hash ready.attempt ready.role) #("w" True 7 "h" "1" None)) ready)
  (<- metered (body-of (! (http-request "POST" "/resources/Service/web/metrics" {}
                                     {"worker" "w" "revision" "r" "metrics" {"counters" {"done" 3} "durations" {"turn" {"sum" 1.5 "count" 2}}}}))))
  (assert (= (. metered metrics counters) {"done" 3}) metered)
  (assert (= (. (get (. metered metrics durations) "turn") count) 2) metered)
  (<- unnamed (body-of (! (http-request "POST" "/resources/Service/web/readiness" {} {"revision" "r" "ready" True}))))
  (assert (in "worker" unnamed.reason) unnamed)
  (<- worded (body-of (! (http-request "POST" "/resources/Service/web/readiness" {} {"worker" "w" "revision" "r" "ready" "yes"}))))
  (assert (in "ready" worded.reason) worded)
  (<- textual (body-of (! (http-request "POST" "/resources/Service/web/metrics" {} {"worker" "w" "revision" "r" "metrics" {"gauges" {"g" "1"}}}))))
  (assert (in "gauges" textual.reason) textual))



(deftest test-a-board-write-tells-a-missing-expect-from-a-null-expect
  ;; expect の欄が無ければ比べない・null なら「行が無い時だけ書く」— 本文の型の既定値では分けられないので、解く所が欄の在否を印にする。
  (<- free (body-of (! (http-request "PUT" "/board/k" {} {"value" {"n" 1}}))))
  (assert (isinstance free BoardWrite) free)
  (assert (= #(free.value-given free.expect-given free.body.value) #(True False {"n" 1})) free)
  (<- fresh (body-of (! (http-request "PUT" "/board/k" {} {"value" 2 "expect" None}))))
  (assert (= #(fresh.expect-given fresh.body.expect) #(True None)) fresh)
  (<- dropping (body-of (! (http-request "PUT" "/board/k" {} {"delete" True}))))
  (assert (= #(dropping.value-given dropping.body.delete) #(False True)) dropping)
  (<- wordy (body-of (! (http-request "PUT" "/board/k" {} {"value" 1 "ttlSeconds" "60"}))))
  (assert (in "ttlSeconds" wordy.reason) wordy)
  ;; 判断へ: null の expect は行が在れば 409・行が無ければ書く。値の無い書きは 400。
  (val state (ClusterState))
  (val first (responded state (! (http-request "PUT" "/board/k" {} {"value" 1 "expect" None})) 1000 T))
  (assert (= (get first 1) 200) first)
  (val again (responded (get first 0) (! (http-request "PUT" "/board/k" {} {"value" 2 "expect" None})) 1000 T))
  (assert (= (get again 1) 409) again)
  (val empty (responded state (! (http-request "PUT" "/board/k" {} {"ttlSeconds" 5})) 1000 T))
  (assert (= (get empty 1) 400) empty))


(deftest test-a-heartbeat-body-is-read-into-its-type-and-refused-before-the-decision
  ;; heartbeat の本文の形の検め(前は判断の中の手書きの検め)は解く所で: 空の名・欠けた失敗の行の kind・文字列の容量は 400。旧い labels は判断が断る。
  (<- beat (body-of (! (http-request "POST" "/heartbeat" {} {"name" "w" "provides" ["net"] "taskReserve" 2 "envs" {"ready" ["k1"]}
                                                             "statuses" [{"name" "a"}]}))))
  (assert (= #(beat.name beat.provides beat.envs.ready beat.background-room beat.task-reserve (len beat.statuses)) #("w" #("net") #("k1") 0 2 1)) beat)
  (<- nameless (body-of (! (http-request "POST" "/heartbeat" {} {"name" "" "provides" ["net"] "taskReserve" 0}))))
  (assert (isinstance nameless BodyMalformed) nameless)
  (<- kindless (body-of (! (http-request "POST" "/heartbeat" {} {"name" "w" "taskReserve" 0 "envs" {"failed" [{"key" "k"}]}}))))
  (assert (in "kind" kindless.reason) kindless)
  (<- wordy (body-of (! (http-request "POST" "/heartbeat" {} {"name" "w" "backgroundRoom" "10" "taskReserve" 0}))))
  (assert (in "backgroundRoom" wordy.reason) wordy)
  (val old (responded (ClusterState) (! (http-request "POST" "/heartbeat" {} {"name" "w" "taskReserve" 0 "labels" {"kind" "mac"}})) 1000 T))
  (assert (= (get old 1) 400) old)
  (assert (in "labels" (get old 2 "error")) old))


(deftest test-a-worker-overlapping-a-running-elder-says-so-and-the-coordinator-reads-it
  ;; 前の世代がまだ走っている間に起きた worker(起動の script の --overlaps-elder — AL-D2e)の heartbeat の本文は overlapsElder を載せ、
  ;; coordinator の本文の型はそれを真と読む。印の無い worker の本文は前と同じ形(欄を書かない)で、偽と読む。
  (<- overlapping (heartbeat-body :name "w" :provides #("net") :exclusive #() :node "n" :background-room 3 :task-reserve 0 :versions {}
                                  :statuses [] :endpoint "http://c" :boot "b2" :boot-at 2 :tools {} :kept #() :overlaps-elder True))
  (assert (= (get overlapping "overlapsElder") True) overlapping)
  (<- read (body-of (! (http-request "POST" "/heartbeat" {} overlapping))))
  (assert (= read.overlaps-elder True) read)
  (<- plain (heartbeat-body :name "w" :provides #("net") :exclusive #() :node "n" :background-room 3 :task-reserve 0 :versions {}
                            :statuses [] :endpoint "http://c" :boot "b2" :boot-at 2 :tools {} :kept #()))
  (assert (not-in "overlapsElder" plain) plain)
  (<- plain-read (body-of (! (http-request "POST" "/heartbeat" {} plain))))
  (assert (= plain-read.overlaps-elder False) plain-read))


(deftest test-a-heartbeat-without-its-task-reserve-or-outside-its-range-is-refused-before-the-decision
  ;; task のために空けておく数 taskReserve は heartbeat の必ずの欄(既定の値は無い)。欄の無い本文・負の数・数でない値は解く所で 400(判断に
  ;; 入らず、状態を変えない)— 欄を知らない worker を黙って予約 0 と読まない。backgroundRoom の負の数も 400。
  (<- missing (body-of (! (http-request "POST" "/heartbeat" {} {"name" "w" "provides" ["net"] "backgroundRoom" 3}))))
  (assert (isinstance missing BodyMalformed) missing)
  (assert (in "taskReserve" missing.reason) missing)
  (<- negative (body-of (! (http-request "POST" "/heartbeat" {} {"name" "w" "provides" ["net"] "backgroundRoom" 3 "taskReserve" -1}))))
  (assert (isinstance negative BodyMalformed) negative)
  (<- below (body-of (! (http-request "POST" "/heartbeat" {} {"name" "w" "provides" ["net"] "backgroundRoom" -1 "taskReserve" 0}))))
  (assert (isinstance below BodyMalformed) below)
  (<- worded (body-of (! (http-request "POST" "/heartbeat" {} {"name" "w" "provides" ["net"] "backgroundRoom" 3 "taskReserve" "1"}))))
  (assert (in "taskReserve" worded.reason) worded)
  ;; 予約は背景の枠より大きくてよい(手書きの capacity は消した — 上限は無い)。
  (<- whole (body-of (! (http-request "POST" "/heartbeat" {} {"name" "w" "provides" ["net"] "backgroundRoom" 3 "taskReserve" 4}))))
  (assert (= #(whole.task-reserve whole.background-room) #(4 3)) whole)
  (val state (ClusterState))
  (val answer (responded state (! (http-request "POST" "/heartbeat" {} {"name" "w" "provides" ["net"] "backgroundRoom" 3})) 1000 T))
  (assert (is (get answer 0) state) answer)
  (assert (= (get answer 1) 400) answer)
  (assert (in "taskReserve" (get answer 2 "error")) answer))


(deftest test-an-old-worker-heartbeat-with-capacity-is-read-with-no-background-room
  ;; 配備の間だけの許し(coordinator を先・worker を後に入れ替える): 旧い worker の本文は capacity を送り
  ;; backgroundRoom を送らない。capacity は読み捨て(数でも数でない値でも本文を断らない)、背景の枠は 0 と読む。新しい worker の本文は
  ;; capacity を送らない。
  (<- old (body-of (! (http-request "POST" "/heartbeat" {} {"name" "w" "provides" ["net"] "capacity" 41 "taskReserve" 6}))))
  (assert (= #(old.task-reserve old.background-room (hasattr old "capacity")) #(6 0 False)) old)
  (<- odd (body-of (! (http-request "POST" "/heartbeat" {} {"name" "w" "provides" ["net"] "capacity" "x" "taskReserve" 6}))))
  (assert (= odd.background-room 0) odd)
  (<- sent (heartbeat-body :name "w" :provides #("net") :exclusive #() :node "n" :background-room 4 :task-reserve 6 :versions {}
                           :statuses [] :endpoint "http://c" :boot "b2" :boot-at 2 :tools {} :kept #()))
  (assert (= #((get sent "backgroundRoom") (in "capacity" sent)) #(4 False)) sent))


(deftest test-a-resource-declaration-envelope-is-read-into-its-type
  ;; 資源の宣言の包み(name・spec・resourceVersion)— object でない spec・数の名・文字列の版は判断の前に 400。Service の spec は宣言の型
  ;; (ServiceBody の job)に読む — 名は POST なら本文の name・PUT なら path の名。読めない宣言も判断の前に 400(#2448)。
  (val spec {"revision" "r" "run" SAMPLE-RUN "peakMiB" 200 "settleMiB" 100 "needs" ["net"]})
  (<- made (body-of (! (http-request "POST" "/resources/Service" {} {"name" "web" "spec" spec}))))
  (assert (= #(made.name made.job.spec.name made.job.spec.revision made.resource-version) #("web" "web" "r" None)) made)
  (<- edited (body-of (! (http-request "PUT" "/resources/Service/web" {} {"spec" spec "resourceVersion" 3}))))
  (assert (= #(edited.name edited.resource-version) #("web" 3)) edited)
  (<- unreadable (body-of (! (http-request "POST" "/resources/Service" {} {"name" "web" "spec" {"revision" "r" "replicas" 2}}))))
  (assert (isinstance unreadable BodyMalformed) unreadable)
  (<- rollout (body-of (! (http-request "POST" "/resources/Rollout" {} {"name" "move" "spec" {"from" {}}}))))
  (assert (= #(rollout.name rollout.spec) #("move" {"from" {}})) rollout)
  (<- listed (body-of (! (http-request "POST" "/resources/Service" {} {"name" "web" "spec" ["r"]}))))
  (assert (in "spec" listed.reason) listed)
  (<- numbered (body-of (! (http-request "POST" "/resources/Service" {} {"name" 7}))))
  (assert (in "name" numbered.reason) numbered)
  (<- worded (body-of (! (http-request "PUT" "/resources/Service/web" {} {"resourceVersion" "3"}))))
  (assert (in "resourceVersion" worded.reason) worded))


(deftest test-task-and-warm-bodies-are-read-into-their-types
  ;; task と切り離した task は同じ本文の型・温める表は別の型。旧い形の欄(blob)は判断が理由つきで断る。
  (<- task (body-of (! (http-request "POST" "/tasks" {} {"program" "p" "revision" "r" "needs" ["net"] "leaseSeconds" 5}))))
  (<- detached (body-of (! (http-request "PUT" "/detached/k" {} {"program" "p" "revision" "r" "needs" ["net"]}))))
  (assert (= #(task.program task.lease-seconds detached.lease-seconds) #("p" 5 None)) #(task detached))
  (<- warm (body-of (! (http-request "POST" "/warm" {} {"runtimeEnv" {} "ttlSeconds" 60 "needs" ["net"] "holder" "h"}))))
  (assert (= #(warm.ttl-seconds warm.holder) #(60 "h")) warm)
  (<- named (body-of (! (http-request "POST" "/warm" {} {"holder" 7}))))
  (assert (in "holder" named.reason) named)
  (val old (responded (ClusterState) (! (http-request "POST" "/tasks" {} {"blob" "x" "revision" "r" "needs" ["net"]})) 1000 T))
  (assert (= (get old 1) 400) old)
  (assert (in "blob" (get old 2 "error")) old))


(deftest test-the-old-jobs-body-is-read-into-its-type
  ;; 旧い PUT /jobs の本文(jobs の行の列と送り手)— 列でない jobs は判断の前に 400。
  ;; 行は宣言の型(LegacyJobRow)に読み、replicas と readiness が行に在ったかを残す。名の無い行・読めない行も判断の前に 400(#2448)。
  (<- jobs (body-of (! (http-request "PUT" "/jobs" {} {"jobs" [{"name" "a" "revision" "r" "run" SAMPLE-RUN "peakMiB" 200 "settleMiB" 100 "needs" ["net"] "resourceVersion" 4}]
                                                    "actor" "me"}))))
  (val row (get jobs.rows 0))
  (assert (= #((len jobs.rows) jobs.actor row.name row.version row.replicas-given row.readiness-given) #(1 "me" "a" 4 False False)) jobs)
  (<- nameless (body-of (! (http-request "PUT" "/jobs" {} {"jobs" [{"revision" "r"}]}))))
  (assert (in "name" nameless.reason) nameless)
  (<- single (body-of (! (http-request "PUT" "/jobs" {} {"jobs" {"name" "a"}}))))
  (assert (in "jobs" single.reason) single))


(deftest test-a-declared-service-without-its-memory-is-refused-but-a-stored-row-without-it-loads
  ;; 送り手の書く宣言(Service の資源の POST・PUT と旧い PUT /jobs)は memory の欄 peakMiB・settleMiB を必ず持つ — 欠けた・型の違う・
  ;; 山を越える宣言は判断の前に 400 で、job と欄を名指して断る(既定の値で埋めない)。保存の読み直しは
  ;; 旧い coordinator が欄の無いまま保存した行を断らず、memory を None で読む(断ると RefusedJob になり job が止まる)。
  (val bare {"revision" "r" "run" SAMPLE-RUN "needs" ["net"]})
  (<- made (body-of (! (http-request "POST" "/resources/Service" {} {"name" "web" "spec" bare}))))
  (assert (isinstance made BodyMalformed) made)
  (assert (in "Service web" made.reason) made)
  (assert (in "peakMiB・settleMiB は 0 以上の整数" made.reason) made)
  (val answer (responded (ClusterState) (! (http-request "PUT" "/resources/Service/web" {} {"spec" (| bare {"peakMiB" 200})})) 1000 T))
  (assert (= (get answer 1) 400) answer)
  (assert (in "settleMiB" (get answer 2 "error")) answer)
  (<- over (body-of (! (http-request "POST" "/resources/Service" {} {"name" "web" "spec" (| bare {"peakMiB" 100 "settleMiB" 200})}))))
  (assert (in "settleMiB 200 が peakMiB 100 を越える" over.reason) over)
  (<- blank (body-of (! (http-request "POST" "/resources/Service" {} {"name" "web" "spec" (| bare {"peakMiB" 200 "settleMiB" 100 "singleHost" ""})}))))
  (assert (in "singleHost" blank.reason) blank)
  (<- legacy (body-of (! (http-request "PUT" "/jobs" {} {"jobs" [(| bare {"name" "a"})] "actor" "me"}))))
  (assert (isinstance legacy BodyMalformed) legacy)
  (assert (in "Service a" legacy.reason) legacy)
  (<- full (body-of (! (http-request "POST" "/resources/Service" {} {"name" "web" "spec" (| bare {"peakMiB" 900 "settleMiB" 400
                                                                                                    "singleHost" "移す途中"})}))))
  (assert (= #(full.job.peak-mib full.job.settle-mib full.job.single-host) #(900 400 "移す途中")) full)
  (assert (= (get (job-to-json full.job) "peakMiB") 900) full)
  (val stored (read-service-rows [(| bare {"name" "old"})]))
  (val jobs (get stored 0))
  (assert (= (len (.rows (get stored 1))) 0) stored)
  (assert (= #((. (get jobs 0) peak-mib) (. (get jobs 0) settle-mib) (. (get jobs 0) single-host)) #(None None None)) jobs)
  (assert (not-in "peakMiB" (job-to-json (get jobs 0))) jobs))
