;; 実行環境の先読み(WarmRuntimeEnv)・温まった worker を優先する置き先・掃除と disk の状態の検 — 速い模擬と純粋な判断(2026-09-26)。
;;
;; 筋書き(設計 worker-runtime-env.md 節 5):
;;   8 先読み: 送る前に WarmRuntimeEnv → 送る: 準備の時間が「送ってから Program が走り出すまで」に入らない(仮想の時計で 2 秒以内)
;;     反例「先読みをせずに送る」: 仮想の時計で 2 秒を超え、計器(冷たい起動の数・phase preparing)がそれを示す
;;   9 固定された root がある時に空きが下限を切る: 固定された root は残り、固定されていない古い root が消える(掃除の選びの純粋な関数)
;; coordinator: 温める表(POST /warm・GET /warm/<キー>)・heartbeat の返事で label の合う worker にだけ配る・準備済みの worker を
;;   優先して置く・準備済みが無ければ phase preparing と計器 doeff_worker_env_cold_start_total・envCapacity=exhausted の worker を避ける。
;; worker: 温める env を job より後に準備する(PrepareEnv :warm True)・固定の集合を掃除の係へ渡す(SweepEnvs)。
;; 準備の期限: 先読みは停滞(処理ステージが進まない)だけ・job の準備は冷たい / 温いで別の期限。
;; bytecode: 焼きの並列数は cgroup の CPU の上限・焼く範囲は入口の module の import の閉包。
(require doeff-hy.macros [deftest defk <- val var])
(import dataclasses [replace])
(import json)
(import doeff [Program run])
(import doeff_core_effects.handlers [state reader])
(import doeff_core_effects.effects [Ask])
(import doeff_time [SimClock sim-time-handler GetMonotonic Delay])
(import doeff_cluster.runtime_env_model [RuntimeEnv runtime-env->json runtime-env-of-json env-key current-platform])
(import doeff_cluster.env_fake [fake-env FakeEnvWorld FakeEnvLog ReadFakeEnvLog])
(import doeff_cluster.detached_model [SubmitDetached AwaitDetached DetachedSucceeded])
(import doeff_cluster.detached [detached-local DetachedLocalStore])
(import doeff_cluster.warm_model [WarmRuntimeEnv ReadWarmState WarmState warm-key warm-state-of-json])
(import doeff_cluster.env_upkeep [RootInfo PrepareLimits sweep-choice prepare-overdue env-capacity])
(import doeff_cluster.cluster_model [ClusterState ClusterTiming TaskRecord WorkerInfo ComponentVersion Requirement Request])
(import doeff_cluster.cluster_policy [place-tasks register-heartbeat heartbeat-reply load-of tasks-for])
(import doeff_cluster.api_policy [respond])
(import doeff_cluster.metrics_policy [metrics-text])
(import doeff_cluster.worker_model [JobSpec CodeView CodeState WorldView WorkerPolicy PrepareEnv StartJob SweepEnvs WarmEnv
                                    EnvDisk code-key])
(import doeff_cluster.worker_policy [plan pinned-env-keys])
(import doeff_cluster.handlers [task-spec])
(import doeff_cluster.code_prepare [cpu-limit-of import-closure])
(import tests.env_fixtures [LOCK env-of base-world])

;; Program が走り出した仮想の時刻(送ってからの待ちを測る)。
(val STARTS [])


(defk timed-add [n]
  {:pre [(: n int)] :post [(: % int)]}
  "送る Program: 走り出した仮想の時刻を残し、実行先の base に n を足して返す。"
  (<- now float (GetMonotonic))
  (.append STARTS now)
  (<- base int (Ask "base"))
  (+ base n))


(defk run-sim [world program]
  {:pre [(: world FakeEnvWorld) (: program Program)] :post [(: % bool)]}
  "筋書きを速い模擬の組(状態・仮想の時計・fake-env・実行先の reader)の下で走らせる。"
  (<- ok bool ((state) ((sim-time-handler :clock (SimClock)) ((fake-env world) ((reader {"base" 100}) program)))))
  ok)


(defk send-and-measure [store key n]
  {:pre [(: store DetachedLocalStore) (: key str) (: n int)] :post [(: % float)]}
  "1 本送って答えを待ち、「送ってから Program が走り出すまで」の仮想の秒を返す。"
  (<- sent float (GetMonotonic))
  (<- ((detached-local store) (SubmitDetached (timed-add n) :env "tests.fixtures.envs:plain_env" :key key)))
  (<- outcome ((detached-local store) (AwaitDetached key)))
  (assert (= outcome (DetachedSucceeded (+ 100 n))) outcome)
  (- (get STARTS -1) sent))


;; --- 筋書き 8 と反例(速い模擬) --------------------------------------------------------------

(defk scenario-8 []
  {:pre [] :post [(: % bool)]}
  "送る前に温め、準備済みになってから送る → 待ちは 2 秒以内・冷たい起動 0・phase preparing を通らない。"
  (<- env RuntimeEnv (env-of "app-1" "lib-1" LOCK))
  (val store (DetachedLocalStore :runtime-env env))
  (<- first WarmState ((detached-local store) (WarmRuntimeEnv env #() 600.0 "tests")))
  (<- expected str (warm-key env #()))
  (assert (= first.key expected) first)
  (var current first)
  (while (not current.ready)
    (<- (Delay 1.0))
    (<- again WarmState ((detached-local store) (ReadWarmState first.key)))
    (:= current again))
  (assert (= current.ready #("local")) current)
  (<- waited float (send-and-measure store "job-8" 8))
  (assert (<= waited 2.0) (.format "温めた後の待ちは 2 秒以内: {}" waited))
  (assert (= store.cold-starts 0) store.cold-starts)
  (assert (not-in "preparing" (. (get store.records "job-8") phases)) (. (get store.records "job-8") phases))
  (<- log FakeEnvLog (ReadFakeEnvLog))
  (assert (= log.syncs 1) "温めた準備 1 回だけ(送った task は準備しない)")
  True)


(deftest test-scenario-8-warming-keeps-preparation-out-of-the-wait
  (.clear STARTS)
  (<- world FakeEnvWorld (base-world))
  (<- ok bool (run-sim world (scenario-8)))
  (assert ok))


(defk counterexample-8 []
  {:pre [] :post [(: % bool)]}
  "反例: 温めずに送る → 待ちが 2 秒を超え、冷たい起動 1・phase preparing を通ったことが記録に残る。"
  (<- env RuntimeEnv (env-of "app-1" "lib-1" LOCK))
  (val store (DetachedLocalStore :runtime-env env))
  (<- waited float (send-and-measure store "job-cold" 9))
  (assert (> waited 2.0) (.format "温めない送りは準備を待つ: {}" waited))
  (assert (= store.cold-starts 1) store.cold-starts)
  (assert (in "preparing" (. (get store.records "job-cold") phases)) (. (get store.records "job-cold") phases))
  True)


(deftest test-counterexample-sending-without-warming-waits-for-preparation
  (.clear STARTS)
  (<- world FakeEnvWorld (base-world))
  (<- ok bool (run-sim world (counterexample-8)))
  (assert ok))


;; --- 筋書き 9(掃除の選び — 純粋な関数) --------------------------------------------------------

(deftest test-scenario-9-sweep-keeps-pinned-latest-and-foreign-roots
  ;; project p の root 4 つ(a が最古・d が最新)と、worker が作っていない dir x(さらに古い)。a は固定(走っている job か温める表)。
  (val roots #((RootInfo :key "a" :project "p" :made-ms 10 :last-used-ms 10 :bytes 100 :owned True)
               (RootInfo :key "b" :project "p" :made-ms 20 :last-used-ms 20 :bytes 100 :owned True)
               (RootInfo :key "c" :project "p" :made-ms 30 :last-used-ms 30 :bytes 100 :owned True)
               (RootInfo :key "d" :project "p" :made-ms 40 :last-used-ms 5 :bytes 100 :owned True)
               (RootInfo :key "x" :project "" :made-ms 1 :last-used-ms 1 :bytes 100 :owned False)))
  (<- nothing tuple (sweep-choice roots (frozenset #("a")) 500 400))
  (assert (= nothing #()) "空きが下限の上なら消さない")
  (<- one tuple (sweep-choice roots (frozenset #("a")) 350 400))
  (assert (= one #("b")) "固定されていない最も古い root から、下限を越えるまで")
  (<- all tuple (sweep-choice roots (frozenset #("a")) 0 10000))
  (assert (= all #("b" "c")) "固定(a)・project ごとの最新(d — 使った時刻は古くても)・worker の作っていない dir(x)は消さない"))


;; --- 準備の期限と disk の状態(純粋) ------------------------------------------------------------

(deftest test-prepare-deadlines-split-warm-stall-and-cold-or-warm-jobs
  (val limits (PrepareLimits :cold-seconds 1800.0 :warm-seconds 300.0 :stall-seconds 600.0))
  ;; 先読み: 期限を掛けない — 処理ステージが 10 分進まない時だけ
  (<- long-but-moving bool (prepare-overdue True False 0.0 7000.0 7100.0 limits))
  (assert (not long-but-moving) "先読みは長くても進んでいれば止めない")
  (<- stalled bool (prepare-overdue True False 0.0 100.0 701.0 limits))
  (assert stalled "先読みは 10 分進まなければ止める")
  ;; job の準備: 冷たい(root も wheel も無い)は 30 分・温いは 5 分
  (<- cold-ok bool (prepare-overdue False True 0.0 1000.0 1700.0 limits))
  (<- cold-over bool (prepare-overdue False True 0.0 1700.0 1801.0 limits))
  (<- warm-over bool (prepare-overdue False False 0.0 290.0 301.0 limits))
  (assert (and (not cold-ok) cold-over warm-over)))


(deftest test-env-capacity-is-exhausted-below-the-preparation-floor
  (<- low str (env-capacity 10 100))
  (<- ok str (env-capacity 100 100))
  (<- unset str (env-capacity 0 0))
  (assert (= #(low ok unset) #("exhausted" "ok" "ok"))))


;; --- coordinator の判断 -----------------------------------------------------------------------

(val TIMING (ClusterTiming))


(defk declared-of [label]
  {:pre [(: label str)] :post [(: % dict)]}
  "commit の名 → 宣言の JSON。"
  (<- env RuntimeEnv (env-of label "lib-1" LOCK))
  (<- declared dict (runtime-env->json env))
  declared)


(defk key-on [declared platform]
  {:pre [(: declared dict) (: platform str)] :post [(: % str)]}
  "宣言の JSON と worker の platform → worker の root のキー。"
  (<- env RuntimeEnv (runtime-env-of-json declared))
  (<- key str (env-key env platform))
  key)


(defn #^ WorkerInfo worker-of [#^ str name #^ tuple labels #^ int seen [ready #()] [capacity "ok"] [load-capacity 2]]
  (WorkerInfo name labels load-capacity seen #() None #() :platform "linux-x86_64" :env-ready (frozenset ready)
              :env-capacity capacity))


(defn #^ TaskRecord env-task [#^ str id #^ dict declared [requires #()]]
  (TaskRecord id "" "tests.fixtures.envs:plain_env" "blob" "" #() requires 60000 60000 0 :runtime-env declared))


(deftest test-warm-table-is-written-read-and-handed-to-matching-workers
  (<- declared dict (declared-of "app-1"))
  (<- key-linux str (key-on declared "linux-x86_64"))
  (val body {"runtimeEnv" declared "requires" {"role" "agent"} "ttlSeconds" 600 "holder" "svc-a"})
  (val written (respond (ClusterState) (Request "POST" "/warm" {} body :actor "svc-a") 1000 TIMING))
  (val after (get written 0))
  (assert (= (get written 1) 200) written)
  (val warmed (warm-state-of-json (get written 2)))
  (assert (= warmed.until-ms 601000) warmed)
  (assert (= #(warmed.ready warmed.preparing) #(#() #())) warmed)
  ;; label の合う worker の heartbeat の返事にだけ載る(合わない worker・専用の印の worker には載らない)
  (val hb {"name" "w1" "labels" {"role" "agent"} "capacity" 2 "versions" {} "boot" "b1" "platform" "linux-x86_64"
           "envs" {"ready" [] "preparing" [key-linux] "failed" []} "envCapacity" "ok"})
  (val s1 (register-heartbeat after hb 2000))
  (assert (= (lfor e (get (heartbeat-reply s1 "w1" TIMING :now 2000) "warm") (get e "runtimeEnv")) [declared]))
  (val s2 (register-heartbeat s1 (| hb {"name" "w2" "labels" {"role" "other"}}) 2000))
  (assert (= (get (heartbeat-reply s2 "w2" TIMING :now 2000) "warm") []) "label の合わない worker には配らない")
  (assert (= (get (heartbeat-reply s2 "w1" TIMING :now 700000) "warm") []) "期限を過ぎた行は配らない")
  ;; 読む: w1 は準備中 → 準備済みを名乗った後は ready
  (val read-1 (get (respond s2 (Request "GET" (+ "/warm/" warmed.key) {} None) 2000 TIMING) 2))
  (assert (= (. (warm-state-of-json read-1) preparing) #("w1")) read-1)
  (val s3 (register-heartbeat s2 (| hb {"envs" {"ready" [key-linux] "preparing" [] "failed" []}}) 3000))
  (val read-2 (get (respond s3 (Request "GET" (+ "/warm/" warmed.key) {} None) 3000 TIMING) 2))
  (assert (= (. (warm-state-of-json read-2) ready) #("w1")) read-2)
  (val missing (respond s3 (Request "GET" "/warm/000000000000000000000000" {} None) 3000 TIMING))
  (assert (= (get missing 1) 404) missing))


(deftest test-placement-prefers-a-warm-worker-and-marks-a-cold-start
  (<- declared dict (declared-of "app-1"))
  (<- key str (key-on declared "linux-x86_64"))
  (val task (env-task "t1" declared))
  ;; 準備済みの w2 を、名前順で先の w1(空き同じ)より優先する
  (val warm-state (ClusterState :workers {"w1" (worker-of "w1" #() 0) "w2" (worker-of "w2" #() 0 :ready #(key))}
                                :tasks {"t1" task}))
  (val placed (get (place-tasks 10 warm-state {} TIMING) "t1"))
  (assert (= #(placed.phase placed.worker) #("assigned" "w2")) placed)
  ;; 準備済みが無ければ置くが phase は preparing(assigned と分ける)で、冷たい起動を数える
  (val cold-state (ClusterState :workers {"w1" (worker-of "w1" #() 0)} :tasks {"t1" task}))
  (val cold (get (place-tasks 10 cold-state {} TIMING) "t1"))
  (assert (= #(cold.phase cold.worker) #("preparing" "w1")) cold)
  (val after (replace cold-state :tasks {"t1" cold}))
  (assert (= (get (load-of after {}) "w1") 1) "preparing の task も担い手の数に入る")
  (assert (= (lfor t (tasks-for after "w1") (get t "id")) ["t1"]) "preparing の task も worker へ送る")
  ;; worker が準備済みを名乗った拍に assigned へ進む
  (val hb {"name" "w1" "labels" {} "capacity" 2 "versions" {} "boot" None "platform" "linux-x86_64"
           "envs" {"ready" [key] "preparing" [] "failed" []} "envCapacity" "ok"})
  (val promoted (register-heartbeat after hb 20))
  (assert (= (. (get promoted.tasks "t1") phase) "assigned") (get promoted.tasks "t1")))


(deftest test-cold-starts-are-counted-in-the-metrics
  (<- declared dict (declared-of "app-1"))
  (val coordinator-state (ClusterState :workers {"w1" (worker-of "w1" #() 0)} :tasks {"t1" (env-task "t1" declared)} :next-task 2))
  (val submitted (respond coordinator-state (Request "POST" "/tasks" {}
                                                     {"env" "e" "blob" "b" "revision" "" "versions" {} "leaseSeconds" 60
                                                      "runtimeEnv" declared})
                          10 TIMING))
  (assert (in "doeff_worker_env_cold_start_total 2" (metrics-text (get submitted 0) 10 TIMING))
          "冷たい起動(準備済みの worker が無い置き先)を数える"))


(deftest test-an-exhausted-worker-gets-no-task-whose-env-it-has-not-prepared
  (<- declared dict (declared-of "app-1"))
  (<- key str (key-on declared "linux-x86_64"))
  (val task (env-task "t1" declared))
  (val two (ClusterState :workers {"w1" (worker-of "w1" #() 0 :capacity "exhausted") "w2" (worker-of "w2" #() 0)}
                         :tasks {"t1" task}))
  (assert (= (. (get (place-tasks 10 two {} TIMING) "t1") worker) "w2") "空きの尽きた worker を避ける")
  (val only (ClusterState :workers {"w1" (worker-of "w1" #() 0 :capacity "exhausted")} :tasks {"t1" task}))
  (assert (= (. (get (place-tasks 10 only {} TIMING) "t1") phase) "queued") "置ける先が無ければ待つ")
  (val ready (ClusterState :workers {"w1" (worker-of "w1" #() 0 :capacity "exhausted" :ready #(key))} :tasks {"t1" task}))
  (assert (= (. (get (place-tasks 10 ready {} TIMING) "t1") worker) "w1") "準備済みの env の task は置いてよい"))


;; --- worker の判断 -----------------------------------------------------------------------------

(defk warm-env-of [label]
  {:pre [(: label str)] :post [(: % WarmEnv)]}
  "温める env 1 つ(worker の root のキーと宣言の JSON の文字列)。"
  (<- declared dict (declared-of label))
  (<- key str (key-on declared (current-platform)))
  (WarmEnv :key (+ "env-" key) :runtime-env (json.dumps declared :sort-keys True :ensure-ascii False)))


(deftest test-the-worker-warms-after-its-jobs-and-retries-a-failed-warm-later
  (<- job-declared dict (declared-of "app-1"))
  (val spec (task-spec {"id" "t1" "env" "e" "revision" "" "versions" {} "blob" "b" "runtimeEnv" job-declared}
                       (. (__import__ "pathlib") (Path "/tmp/tasks"))))
  (<- warm WarmEnv (warm-env-of "app-2"))
  (val policy (WorkerPolicy))
  (val actions (plan 0 #(spec) (WorldView #() #()) {} policy :warm #(warm)))
  (assert (= actions #((PrepareEnv (code-key spec) spec.runtime-env) (PrepareEnv warm.key warm.runtime-env :warm True)))
          "job の準備が先・温める準備が後")
  (val ready (WorldView #((CodeView warm.key CodeState.READY :path "/r")) #()))
  (assert (= (plan 0 #() ready {} policy :warm #(warm)) #()) "準備済みなら何もしない")
  (val failed (WorldView #((CodeView warm.key CodeState.FAILED :detail "d" :failed-ms 0)) #()))
  (assert (= (plan 10 #() failed {} policy :warm #(warm)) #()) "失敗の直後は撃ち直さない")
  (assert (= (plan policy.code-retry-ms #() failed {} policy :warm #(warm))
             #((PrepareEnv warm.key warm.runtime-env :warm True)))))


(deftest test-the-worker-pins-running-desired-warm-and-preparing-roots-for-the-sweep
  (<- job-declared dict (declared-of "app-1"))
  (val spec (task-spec {"id" "t1" "env" "e" "revision" "" "versions" {} "blob" "b" "runtimeEnv" job-declared}
                       (. (__import__ "pathlib") (Path "/tmp/tasks"))))
  (<- warm WarmEnv (warm-env-of "app-2"))
  (val world (WorldView #((CodeView "env-preparing" CodeState.PREPARING)) #() #()
                        :env-disk (EnvDisk :free 10 :floor 100 :pinned (frozenset))))
  (val pinned (pinned-env-keys #(spec) world #(warm)))
  (assert (= pinned (frozenset #((code-key spec) warm.key "env-preparing"))) pinned)
  (val policy (WorkerPolicy))
  (val actions (plan 0 #(spec) world {} policy :warm #(warm)))
  (assert (in (SweepEnvs pinned) actions) "空きが下限を切れば固定の集合を渡して掃除する")
  (val roomy (replace world :env-disk (EnvDisk :free 1000 :floor 100 :pinned pinned)))
  (assert (not (any (gfor a (plan 0 #(spec) roomy {} policy :warm #(warm)) (isinstance a SweepEnvs))))
          "空きが足り、固定の集合が変わらなければ掃除の係を呼ばない"))


;; --- bytecode の焼き(#664 の実測から) -------------------------------------------------------------

(deftest test-compile-parallelism-follows-the-cgroup-cpu-limit
  (assert (= (cpu-limit-of "400000 100000\n" 16) 4) "pod の上限 4 CPU は node の 16 より優先")
  (assert (= (cpu-limit-of "150000 100000\n" 16) 2) "端数は切り上げる")
  (assert (= (cpu-limit-of "max 100000\n" 16) 16) "上限の無い cgroup は使える CPU の数")
  (assert (= (cpu-limit-of None 3) 3) "cgroup の file が無ければ使える CPU の数"))


(deftest test-the-compile-scope-is-the-import-closure-of-the-entries [tmp-path]
  (for [#(rel text) [#("pkg/__init__.py" "")
                     #("pkg/entry.hy" "(import pkg.used [f])\n(require pkg.macros [m])\n(import json os)\n")
                     #("pkg/used.hy" "(import .deep [g])\n(defn f [] 1)\n")
                     #("pkg/deep.py" "import pkg.leaf\n")
                     #("pkg/leaf.py" "X = 1\n")
                     #("pkg/macros.hy" "(defmacro m [] 1)\n")
                     #("pkg/unused.hy" "(import pkg.leaf)\n")]]
    (setv path (/ tmp-path rel))
    (.mkdir path.parent :parents True :exist-ok True)
    (.write-text path text))
  (val sources (sorted (gfor p (.rglob tmp-path "*") :if (.is-file p) (str (.relative-to p tmp-path)))))
  (val closure (import-closure (str tmp-path) sources #("pkg.entry") #(".")))
  (assert (= closure (frozenset #("pkg/__init__.py" "pkg/entry.hy" "pkg/used.hy" "pkg/deep.py" "pkg/leaf.py" "pkg/macros.hy")))
          closure))


(deftest test-bytecode-entries-travel-in-the-declaration-but-not-in-the-key
  (<- env RuntimeEnv (env-of "app-1" "lib-1" LOCK))
  (val narrowed (replace env :bytecode-entries #("app.main")))
  (<- declared dict (runtime-env->json narrowed))
  (<- back RuntimeEnv (runtime-env-of-json declared))
  (assert (= back.bytecode-entries #("app.main")) back)
  (<- key-a str (env-key env "linux-x86_64"))
  (<- key-b str (env-key narrowed "linux-x86_64"))
  (assert (= key-a key-b) "焼く範囲は root の file を変えないのでキーに入れない"))
