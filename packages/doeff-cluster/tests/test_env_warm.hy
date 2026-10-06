;; 実行環境の先読み(WarmRuntimeEnv)・温まった worker を優先する置き先・掃除と disk の状態の検 — 手元の runner sim-cluster と純粋な判断
;; (2026-09-26・2026-09-28 に同じ VM の模擬 detached-local の筋書きを sim-cluster へ移した)。
;;
;; 筋書き(設計 worker-runtime-env.md 節 5):
;;   8 先読み: 送る前に WarmRuntimeEnv → 送る: 準備の時間が「送ってから Program が走り出すまで」に入らない(仮想の時計で 2 秒以内)
;;     反例「先読みをせずに送る」: 仮想の時計で 2 秒を超え、計器(冷たい起動の数・phase preparing)がそれを示す
;;   9 固定された root がある時に roots の合計が上限を越える: 固定された root と project ごとの新しい 2 つ(今の版と戻し先の版)は残り、
;;     固定されていない古い root が消える(掃除の選びの純粋な関数・#3732)
;; coordinator: 温める表(POST /warm・GET /warm/<キー>)・heartbeat の返事で label の合う worker にだけ配る・準備済みの worker を
;;   優先して置く・準備済みが無ければ phase preparing と計器 doeff_worker_env_cold_start_total・envCapacity=exhausted の worker を避ける。
;; worker: 温める env を job より後に準備する(PrepareEnv :warm True)・固定の集合を掃除の係へ渡す(SweepEnvs)。
;; 準備の期限: 先読みも job の準備も、停滞(進みの印が動かない長さ)だけで止める(合計の時間では止めない — #3515)。
;; bytecode: 焼きの並列数は cgroup の CPU の上限・焼く範囲は入口の module の import の閉包。
(require doeff-hy.macros [deftest defk deff do! <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass replace])
(import json)
(import pathlib [Path])
(import doeff [with-handlers])
(import doeff_time [Delay])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.shared.intent.runtime_env_model [RuntimeEnv])
(import doeff_cluster.shared.core.runtime_env_rules [runtime-env->json runtime-env-of-json env-key])
(import doeff_cluster.shared.core.native_wheel [current-platform])
(import doeff_cluster.shared.intent.detached_model [AwaitDetached DetachedSucceeded])
(import httpx)
(import doeff_cluster.shared.protocol.detached [warm-cluster])
(import doeff_time [SimClock sim-time-handler])
(import tests.transport_http [transport-http route-cell TEST-ROUTE])
(import doeff_cluster.sim.local [sim-cluster SimWorker SimLink ClientLink coordinator-answers ReadCoordinator ProcessesOf PreparationsOf
                             FailRoute])
(import doeff_cluster.shared.entry.service_build [system-of])
(import doeff_cluster.shared.intent.warm_model [ReadWarmState WarmState WarmUnreachable WarmAnswer])
(import doeff_cluster.shared.core.warm_rules [warm-key warm-state-of-json warm-runtime-env])
(import doeff_cluster.worker.core.env_upkeep [RootInfo RootsTally PrepareLimits SWEEP-EVERY-MS sweep-choice sweep-due sweep-wanted
                                              recent-per-project prepare-overdue env-capacity])
(import doeff_cluster.shared.intent.protocol [ClusterTiming Request PlainText])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState TaskRecord WorkerInfo ComponentVersion])
(import doeff_cluster.shared.protocol.inbox [http-request])
(import doeff_cluster.coordinator.core.cluster_policy [place-tasks register-heartbeat heartbeat-reply load-of tasks-for])
(import doeff_cluster.coordinator.protocol.request_bodies [responded])
(import doeff_cluster.coordinator.core.metrics_policy [metrics-text])
(import doeff_cluster.worker.intent.worker_model [CodeView CodeState WorldView WorkerPolicy PrepareEnv StartJob SweepEnvs WarmEnv StartWarmChild WarmChildView WarmChildMark
                                    EnvDisk NotYetRead DeclarationRead] doeff_cluster.shared.intent.job_model [JobSpec] doeff_cluster.worker.core.worker_rules [code-key])
(import doeff_cluster.worker.core.policy [plan pinned-env-keys sweep-actions])
(import doeff_cluster.worker.protocol.declared [task-spec])
(import doeff_cluster.worker.core.bake_plan [cpu-limit-of])
(import doeff_cluster.shared.core.detached_rules [submit-detached-task])
(import tests.env_fixtures [LOCK env-of])
(import tests.detached_rig [slow-add])
(import tests.program_rows [SAMPLE-TASK-PROGRAM program-placed heartbeat-of])

;; --- 筋書き 8 と反例(手元の runner sim-cluster)-------------------------------------------------
;; sim の worker は実行環境の root の準備に PREPARE-SECONDS かかる(sim の宿の SimWorker の prepare-seconds)。温める表の行は本物の
;; coordinator が worker の heartbeat の返事に配り、本物の run-worker が先読み(PrepareEnv :warm)を撃ち、task の置き先(温まった worker・
;; 冷たい起動の計器)は本物の coordinator が決める。送る task の実行環境の宣言は送り手の口(ClientLink を置き換えた SimLink の
;; runtime-env — 本番の DetachedSender の runtime-env)が運ぶ。

(val PREPARE-SECONDS 5.0)
(val WARM-WORKERS #((SimWorker :name "w1" :provides (frozenset ["local"]) :prepare-seconds PREPARE-SECONDS :task-reserve 0)))
(val NO-JOBS (system-of "warm-scenarios" #()))
(val COLD-STARTS-METRIC "doeff_worker_env_cold_start_total")


(defrecord Measured
  "送った task の読み: waited = 送ってから task の process が起きるまでの仮想の秒・cold-starts = coordinator の冷たい起動の計器・
   preparations = worker が起こした実行環境の root の準備(SimPreparation の列)・code-preparations = worker が起こしたコードの木の準備。"
  (#^ float waited)
  (#^ float cold-starts)
  (#^ tuple preparations)
  (#^ tuple code-preparations))


(defk metric-value [text name]
  {:pre [(: text str) (: name str)] :post [(: % float)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "Prometheus の text から label の無い計器 name の値を読むため(無ければ 0)。"
  (var value 0.0)
  (for [line (.splitlines text)]
    (when (.startswith line (+ name " "))
      (:= value (float (get (.split line) -1)))))
  value)


(defk send-and-measure [env key n]
  {:pre [(: env (| RuntimeEnv None)) (: key str) (: n int)] :post [(: % Measured)] :tags {:context "doeff-cluster-test" :role "program"}}
  "env の送り手(None = 実行環境の宣言を持たない送り手 — 送り手の版のコードだけ)として 1 本送って答えを待ち、「送ってから task の
   process が起きるまで」の仮想の秒・冷たい起動の計器・準備の列を読むため。"
  (<- link SimLink (ClientLink))
  (<- sent int (now-epoch-ms))
  (<- outcome (with-handlers [(coordinator-answers (replace link :runtime-env env))]
                (do! (<- (submit-detached-task (slow-add 0.0 n) :needs (frozenset ["local"]) :key key))
                     (<- awaited (AwaitDetached key))
                     awaited)))
  (assert (= outcome (DetachedSucceeded (+ 100 n))) outcome)
  (<- state dict (ReadCoordinator "/state"))
  (val ids (lfor t (get state "tasks") :if (= (.get t "key") key) (get t "id")))
  (<- processes tuple (ProcessesOf (+ "task/" (get ids 0))))
  (<- metrics PlainText (ReadCoordinator "/metrics"))
  (<- cold float (metric-value metrics.text COLD-STARTS-METRIC))
  (<- preparations tuple (PreparationsOf "w1"))
  (Measured :waited (/ (- (. (get processes 0) started-ms) sent) 1000.0) :cold-starts cold
            :preparations (tuple (gfor p preparations :if p.env p))
            :code-preparations (tuple (gfor p preparations :if (not p.env) p))))


(defk scenario-8 []
  {:pre [] :post [(: % Measured)] :tags {:context "doeff-cluster-test" :role "program"}}
  "送る前に温め、準備済みになってから送る。"
  (<- env RuntimeEnv (env-of "app-1" "lib-1" LOCK))
  (<- first WarmState (warm-runtime-env env (frozenset ["local"]) 600.0 "tests"))
  (<- expected str (warm-key env #("local")))
  (assert (= first.key expected) first)
  (var current first)
  (while (not current.ready)
    (<- (Delay 1.0))
    (<- again WarmState (ReadWarmState first.key))
    (:= current again))
  (assert (= current.ready #("w1")) current)
  (<- measured Measured (send-and-measure env "job-8" 8))
  measured)

(deftest test-scenario-8-warming-keeps-preparation-out-of-the-wait
  ;; 温めた後の送り: 待ちは 2 秒以内(worker の拍と置き先の拍だけ)・冷たい起動 0・準備は温めた 1 回だけ(送った task は準備しない)。
  (<- seen Measured (sim-cluster NO-JOBS (scenario-8) :workers WARM-WORKERS))
  (assert (<= seen.waited 2.0) (.format "温めた後の待ちは 2 秒以内: {}" seen.waited))
  (assert (= seen.cold-starts 0.0) seen.cold-starts)
  (assert (= (lfor p seen.preparations p.warm) [True]) seen.preparations))

(defk counterexample-8 []
  {:pre [] :post [(: % Measured)] :tags {:context "doeff-cluster-test" :role "program"}}
  "反例: 温めずに送る。"
  (<- env RuntimeEnv (env-of "app-1" "lib-1" LOCK))
  (<- measured Measured (send-and-measure env "job-cold" 9))
  measured)

(deftest test-counterexample-sending-without-warming-waits-for-preparation
  ;; 温めない送り: 待ちに準備(PREPARE-SECONDS)が入り 2 秒を超え、coordinator の冷たい起動の計器が 1 つ増え、準備は task の準備(先読み
  ;; でない)1 回。
  (<- seen Measured (sim-cluster NO-JOBS (counterexample-8) :workers WARM-WORKERS))
  (assert (> seen.waited 2.0) (.format "温めない送りは準備を待つ: {}" seen.waited))
  (assert (= seen.cold-starts 1.0) seen.cold-starts)
  (assert (= (lfor p seen.preparations p.warm) [False]) seen.preparations))


;; --- 準備の秒はコードの木と実行環境の root で別(#2879) ----------------------------------------------
;; 本番の worker はコードの木の準備(PrepareCode)と実行環境の root の準備(PrepareEnv)を別の操作として行い、それぞれに別の時間が
;; かかる。sim の worker もコードの木は prepare-seconds・実行環境の root は env-prepare-seconds の後に揃う。新しい版(実行環境の宣言を
;; 持つ送り手)の root の準備だけが遅い筋書きで、旧い版(宣言を持たない送り手)の task はその準備を待たずに起きる — 1 つの秒を両方に
;; 掛けると、旧い版の起動に新しい版の準備の秒の空きが出る(#2840 の置く係の入れ替えの筋書きの起点 150 秒・10 秒)。

(val SPLIT-ENV-SECONDS 30.0)
(val SPLIT-WORKERS #((SimWorker :name "w1" :provides (frozenset ["local"]) :prepare-seconds 0.0
                                :env-prepare-seconds SPLIT-ENV-SECONDS :task-reserve 0)))


(defrecord SplitMeasured
  "旧い版と新しい版を続けて送った読み: old = 実行環境の宣言を持たない送り手の task・new = 宣言を持つ送り手の task。"
  (#^ Measured old)
  (#^ Measured new))


(defk split-scenario []
  {:pre [] :post [(: % SplitMeasured)] :tags {:context "doeff-cluster-test" :role "program"}}
  "旧い版の送り手(実行環境の宣言なし)で 1 本、続けて新しい版の送り手(宣言あり)で 1 本送り、それぞれの待ちと準備を読む。"
  (<- old Measured (send-and-measure None "old-1" 1))
  (<- env RuntimeEnv (env-of "app-1" "lib-1" LOCK))
  (<- new Measured (send-and-measure env "new-1" 2))
  (SplitMeasured :old old :new new))


(deftest test-an-old-version-task-does-not-wait-for-the-new-version-env-root-preparation
  (<- seen SplitMeasured (sim-cluster NO-JOBS (split-scenario) :workers SPLIT-WORKERS))
  ;; 旧い版: コードの木の準備は prepare-seconds(0 秒)で揃い、実行環境の root は準備せず、待ちは worker の拍と置き先の拍だけ(2 秒以内)。
  (assert (<= seen.old.waited 2.0) (.format "旧い版は実行環境の root の準備を待たない: {}" seen.old.waited))
  (assert (= seen.old.preparations #()) seen.old.preparations)
  (assert (and seen.old.code-preparations (all (gfor p seen.old.code-preparations (= p.ready-ms p.started-ms))))
          seen.old.code-preparations)
  ;; 新しい版: 実行環境の root の準備は env-prepare-seconds(30 秒)かかり、待ちはそれを含む。
  (assert (= (lfor p seen.new.preparations (- p.ready-ms p.started-ms)) [(int (* 1000 SPLIT-ENV-SECONDS))]) seen.new.preparations)
  (assert (>= seen.new.waited SPLIT-ENV-SECONDS) (.format "新しい版は実行環境の root の準備を待つ: {}" seen.new.waited)))


;; --- 筋書き 9(掃除の選び — 純粋な関数) --------------------------------------------------------

(deftest test-scenario-9-sweep-keeps-pinned-recent-two-and-foreign-roots
  ;; project p の root 4 つ(最後に使った時刻 a < b < c < d — d が今の版・c が 1 つ前に動いていた版 = 戻し先)と、別の project q の root
  ;; 1 つ(最も古く使った)と、worker が作っていない dir x。a は固定(走っている job か温める表)。合計は worker が作った root だけ(500)。
  (val roots #((RootInfo :key "a" :project "p" :made-ms 10 :last-used-ms 10 :bytes 100 :owned True)
               (RootInfo :key "b" :project "p" :made-ms 20 :last-used-ms 20 :bytes 100 :owned True)
               (RootInfo :key "c" :project "p" :made-ms 30 :last-used-ms 30 :bytes 100 :owned True)
               (RootInfo :key "d" :project "p" :made-ms 40 :last-used-ms 40 :bytes 100 :owned True)
               (RootInfo :key "q" :project "q" :made-ms 1 :last-used-ms 1 :bytes 100 :owned True)
               (RootInfo :key "x" :project "" :made-ms 1 :last-used-ms 1 :bytes 100 :owned False)))
  (<- recent frozenset (recent-per-project roots))
  (assert (= recent (frozenset #("c" "d" "q"))) "project ごとに最後に使った新しい 2 つ(別の project は 1 つしか無ければその 1 つ)")
  (<- nothing tuple (sweep-choice roots (frozenset #("a")) 500))
  (assert (= nothing #()) "roots の合計が上限の内なら消さない")
  (<- one tuple (sweep-choice roots (frozenset #("a")) 450))
  (assert (= one #("b")) "固定されていない最も古い root から、合計が上限の内へ戻るまで")
  (<- all tuple (sweep-choice roots (frozenset #("a")) 0))
  (assert (= all #("b")) "固定(a)・project ごとの新しい 2 つ(d と戻し先の c)・別の project の root(q)・worker の作っていない dir(x)は消さない"))


(deftest test-the-previous-version-root-survives-even-when-a-newer-root-was-made-later
  ;; 版を替えた job の 1 つ前の版の root(old — 戻し先)は、後から完成した先読みの root(next — まだ使っていない)があっても、最後に使った
  ;; 時刻で新しい 2 つに入る限り残る。反例 = 直す前の形(project ごとに完成の時刻の最新 1 つだけ守る)は old を消す — 下の断言が赤。
  (val roots #((RootInfo :key "older" :project "p" :made-ms 10 :last-used-ms 100 :bytes 100 :owned True)
               (RootInfo :key "old" :project "p" :made-ms 20 :last-used-ms 200 :bytes 100 :owned True)
               (RootInfo :key "now" :project "p" :made-ms 30 :last-used-ms 300 :bytes 100 :owned True)))
  (<- chosen tuple (sweep-choice roots (frozenset #("now")) 0))
  (assert (= chosen #("older")) "戻し先の old は残り、それより古い older だけを消す"))


(deftest test-sweep-due-measures-only-when-the-roots-changed-or-stay-over-the-cap
  ;; 数え(root ごとに木を歩く)は、まだ数えていない時・完成した root の集合が変わった時・上限を越えたままで固定が変わったか間隔が経った
  ;; 時だけ。上限の内で集合が変わらなければ、固定が変わっても数えない(共有の disk の空きでは数えない — #3732)。
  (val under (RootsTally :ready (frozenset #("env-a")) :bytes 100))
  (val over (RootsTally :ready (frozenset #("env-a")) :bytes 300))
  (val same (frozenset #("env-a")))
  (assert (! (sweep-due None same 200 False 0 0)) "まだ数えていなければ数える")
  (assert (not (! (sweep-due under same 200 True 1000 0))) "上限の内で集合が変わらなければ、固定が変わっても数えない")
  (assert (! (sweep-due under (frozenset #("env-a" "env-b")) 200 False 1000 0)) "新しく完成した root が在れば数え直す")
  (assert (! (sweep-due under (frozenset) 200 False 1000 0)) "root が消えれば数え直す")
  (assert (not (! (sweep-due over same 200 False 1000 0))) "上限を越えたままでも、固定が変わらず間隔の内なら数えない")
  (assert (! (sweep-due over same 200 True 1000 0)) "上限を越えたまま固定が変われば数え直す")
  (assert (! (sweep-due over same 200 False SWEEP-EVERY-MS 0)) "上限を越えたまま間隔が経てば数え直す")
  ;; 掃除の係が拍を求めるか(heartbeat の観測の sweep-wanted)。
  (assert (! (sweep-wanted False None same 200)) "まだ数えていなければ求める")
  (assert (not (! (sweep-wanted False under same 200))) "上限の内で集合が変わらず、走っていなければ求めない")
  (assert (! (sweep-wanted True under same 200)) "走っている掃除は拍ごとに答えを読んで進むので求める")
  (assert (! (sweep-wanted False over same 200)) "上限を越えていれば求める")
  (assert (! (sweep-wanted False under (frozenset #("env-b")) 200)) "完成した root の集合が変われば求める"))


;; --- 準備の期限と disk の状態(純粋) ------------------------------------------------------------

(deftest test-prepare-deadline-is-the-stall-for-every-preparation
  ;; 起こした時刻は 0 秒・引数は (最後の進み 今)。先読みも job の準備(冷たい・温い)も、最後の進みから 600 秒進まない時だけ止める。
  (val limits (PrepareLimits :stall-seconds 600.0))
  (<- long-but-moving bool (prepare-overdue 7000.0 7100.0 limits))
  (assert (not long-but-moving) "長くても進んでいれば止めない")
  ;; 実例(#3515): 温い job の準備が起こしてから 301 秒 — 最後の進み(bytecode の repo の木)から 11 秒なので止めない。
  (<- past-warm bool (prepare-overdue 290.0 301.0 limits))
  (assert (not past-warm) "起こしてから 300 秒を越えても、進んでいれば止めない")
  (<- past-cold bool (prepare-overdue 1700.0 1801.0 limits))
  (assert (not past-cold) "起こしてから 1800 秒を越えても、進んでいれば止めない")
  ;; 止めなさすぎにしない: 進みの印が 600 秒を越えて動かなければ止める(600 秒ちょうどはまだ止めない)。
  (<- edge bool (prepare-overdue 100.0 700.0 limits))
  (assert (not edge) "600 秒ちょうどは止めない")
  (<- stalled bool (prepare-overdue 100.0 701.0 limits))
  (assert stalled "600 秒を越えて進まなければ止める"))


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


(defk worker-of [name provides seen [ready #()] [capacity "ok"] [load-capacity 2]]
  {:pre [(: name str) (: provides tuple) (: seen int) (: ready tuple) (: capacity str) (: load-capacity int)] :post [(: % WorkerInfo)]
   :tags {:context "doeff-cluster-test" :role "entry"}}
  "名乗った worker の事実(準備済みの root の鍵 ready・root の置き場の余地 capacity)を、盤の状態に置く形で組むため。"
  (WorkerInfo name provides load-capacity seen #() None #() :platform "linux-x86_64" :env-ready (frozenset ready)
              :env-capacity capacity :task-reserve 0))


(defk env-task [id declared [needs #("net")]]
  {:pre [(: id str) (: declared dict) (: needs tuple)] :post [(: % TaskRecord)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "実行環境の宣言 declared を持つ task の行を、盤の状態に置く形で組むため。"
  (TaskRecord id "" SAMPLE-TASK-PROGRAM "" #() needs 60000 60000 0 :runtime-env declared))


(deftest test-warm-table-is-written-read-and-handed-to-matching-workers
  (<- declared dict (declared-of "app-1"))
  (<- key-linux str (key-on declared "linux-x86_64"))
  (val body {"runtimeEnv" declared "needs" ["agent-cli"] "ttlSeconds" 600 "holder" "svc-a"})
  (val written (responded (ClusterState) (! (http-request "POST" "/warm" {} body :actor "svc-a")) 1000 TIMING))
  (val after (get written 0))
  (assert (= (get written 1) 200) written)
  (val warmed (warm-state-of-json (get written 2)))
  (assert (= warmed.until-ms 601000) warmed)
  (assert (= #(warmed.ready warmed.preparing) #(#() #())) warmed)
  ;; 能力の合う worker の heartbeat の返事にだけ載る(能力の足りない worker・専用の能力を持つ worker には載らない)
  (val hb {"name" "w1" "provides" ["agent-cli"] "capacity" 2 "taskReserve" 0 "versions" {} "boot" "b1" "platform" "linux-x86_64"
           "envs" {"ready" [] "preparing" [key-linux] "failed" []} "envCapacity" "ok"})
  (val s1 (! (register-heartbeat after (! (heartbeat-of hb)) 2000)))
  (val reply-1 (! (heartbeat-reply s1 "w1" TIMING :now 2000)))
  (assert (= (lfor e reply-1.warm e.runtime-env) [declared]))
  (val s2 (! (register-heartbeat s1 (! (heartbeat-of (| hb {"name" "w2" "provides" ["net"]}))) 2000)))
  (assert (= (. (! (heartbeat-reply s2 "w2" TIMING :now 2000)) warm) #()) "能力の足りない worker には配らない")
  (val with-gpu (! (register-heartbeat s2 (! (heartbeat-of (| hb {"name" "w3" "provides" ["agent-cli" "gpu"] "exclusive" ["gpu"]}))) 2000)))
  (assert (= (. (! (heartbeat-reply with-gpu "w3" TIMING :now 2000)) warm) #())
          "専用の能力(gpu)を持つ worker には、その能力を要らない行を配らない")
  (assert (= (. (! (heartbeat-reply s2 "w1" TIMING :now 700000)) warm) #()) "期限を過ぎた行は配らない")
  ;; 読む: w1 は準備中 → 準備済みを名乗った後は ready
  (val read-1 (get (responded s2 (! (http-request "GET" (+ "/warm/" warmed.key) {} None)) 2000 TIMING) 2))
  (assert (= (. (warm-state-of-json read-1) preparing) #("w1")) read-1)
  (val s3 (! (register-heartbeat s2 (! (heartbeat-of (| hb {"envs" {"ready" [key-linux] "preparing" [] "failed" []}}))) 3000)))
  (val read-2 (get (responded s3 (! (http-request "GET" (+ "/warm/" warmed.key) {} None)) 3000 TIMING) 2))
  (assert (= (. (warm-state-of-json read-2) ready) #("w1")) read-2)
  (val missing (responded s3 (! (http-request "GET" "/warm/000000000000000000000000" {} None)) 3000 TIMING))
  (assert (= (get missing 1) 404) missing))



(deftest test-a-warm-row-says-when-a-worker-built-it-without-measuring-memory
  ;; worker が heartbeat の envs.memoryUnmeasured に root のキーを名乗ると(先の組みの前に memory を読めなかった — #3748)、その行の姿は
  ;; memory-unmeasured = 真で、通信の本文(memoryUnmeasured)を往復しても残る。名乗らない worker だけの行は偽。
  (<- declared dict (declared-of "app-1"))
  (<- key-linux str (key-on declared "linux-x86_64"))
  (val body {"runtimeEnv" declared "needs" ["agent-cli"] "ttlSeconds" 600 "holder" "svc-a"})
  (val written (responded (ClusterState) (! (http-request "POST" "/warm" {} body :actor "svc-a")) 1000 TIMING))
  (val warmed (warm-state-of-json (get written 2)))
  (val hb {"name" "w1" "provides" ["agent-cli"] "capacity" 2 "taskReserve" 0 "versions" {} "boot" "b1" "platform" "linux-x86_64"
           "envs" {"ready" [] "preparing" [key-linux] "failed" [] "memoryUnmeasured" []} "envCapacity" "ok"})
  (val measured (! (register-heartbeat (get written 0) (! (heartbeat-of hb)) 2000)))
  (val read-1 (warm-state-of-json (get (responded measured (! (http-request "GET" (+ "/warm/" warmed.key) {} None)) 2000 TIMING) 2)))
  (assert (= #(read-1.preparing read-1.memory-unmeasured) #(#("w1") False)) read-1)
  (val unmeasured (! (register-heartbeat measured (! (heartbeat-of (| hb {"envs" {"ready" [] "preparing" [key-linux] "failed" []
                                                                              "memoryUnmeasured" [key-linux]}}))) 3000)))
  (val read-2 (warm-state-of-json (get (responded unmeasured (! (http-request "GET" (+ "/warm/" warmed.key) {} None)) 3000 TIMING) 2)))
  (assert (= #(read-2.preparing read-2.memory-unmeasured) #(#("w1") True)) read-2))


;; --- 本物の client(warm-cluster): coordinator に届かない頼みは値で答える(2026-09-28)---------------------------

(deff answers-503 [request]  ; defk にできない: httpx の MockTransport が同期で呼ぶ外の callback
  {:pre [(: request httpx.Request)] :post [(: % httpx.Response)]}
  "coordinator の /warm の代役: どの要求にも 503 を返す(作り直しの最中の coordinator の 5xx)。"
  (httpx.Response 503 :json {"error" "coordinator が作り直しの最中"}))

(defk warm-and-read [env]
  {:pre [(: env RuntimeEnv)] :post [(: % tuple)]}
  "温める頼みと行の読みを 1 回ずつ出し、2 つの答えを返す。"
  (<- written WarmAnswer (warm-runtime-env env (frozenset ["local"]) 600.0 "tests"))
  (<- key str (warm-key env #("local")))
  (<- read WarmAnswer (ReadWarmState key))
  #(written read))

(defk warm-through [transport]
  {:pre [(: transport httpx.MockTransport)] :post [(: % tuple)]}
  "本物の warm-cluster(送り直しの期限を短くした — 仮想の時計の秒)の下で、温める頼みと読みを 1 回ずつ出す。"
  (<- env RuntimeEnv (env-of "app-1" "lib-1" LOCK))
  (<- answers tuple (with-handlers [(sim-time-handler :clock (SimClock)) (transport-http transport) (warm-cluster (route-cell) (replace TEST-ROUTE :resend-deadline-seconds 0.2))]
                      (warm-and-read env)))
  answers)


(deftest test-the-real-warm-client-answers-a-server-error-as-unreachable
  ;; 反例 1: coordinator の /warm が 503 を返す → 温める頼みも読みも例外でなく WarmUnreachable(理由に返った状態が残る)。
  (<- answers tuple (warm-through (httpx.MockTransport answers-503)))
  (for [answer answers]
    (assert (isinstance answer WarmUnreachable) answer)
    (assert (in "503" answer.detail) answer)))


;; 接続が断られる時(送り直しの期限を過ぎた通信の失敗)の答え・sim の宿の同じ答え・届く時の頼みと読みの姿は、本物と sim が同じ Program を
;; 通る契約テスト test_warm_contract.hy が持つ。


;; --- sim-cluster の故障の口 FailRoute: coordinator の /warm の 5xx ------------------------------------------------

(val FAULT-SECONDS 30.0)

(defk warm-under-a-failed-route []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: POST /warm に FAULT-SECONDS 秒 503 で答える故障を入れて温めを頼み、行を読み、故障が明けてから頼み直す。答え =
   #(故障の間の頼みの答え 行の読み 明けた後の頼みの答え)。"
  (<- env RuntimeEnv (env-of "app-1" "lib-1" LOCK))
  (<- (FailRoute "POST" "/warm" 503 FAULT-SECONDS))
  (<- failed WarmAnswer (warm-runtime-env env (frozenset ["local"]) 600.0 "tests"))
  (<- key str (warm-key env #("local")))
  (<- absent WarmAnswer (ReadWarmState key))
  (<- (Delay (+ FAULT-SECONDS 1.0)))
  (<- again WarmAnswer (warm-runtime-env env (frozenset ["local"]) 600.0 "tests"))
  #(failed absent again))

(deftest test-a-failed-warm-route-answers-unreachable-and-writes-no-row
  ;; 故障の間の頼みは本番の warm-cluster と同じ WarmUnreachable(理由に返った状態)で、要求は調停ループに届かない(同じキーの行の読み —
  ;; 故障を入れていない口 — は空の姿)。故障が明けた後の頼みは本物の coordinator が書いた行の姿。
  (<- seen tuple (sim-cluster NO-JOBS (warm-under-a-failed-route) :workers WARM-WORKERS))
  (val failed (get seen 0))
  (val absent (get seen 1))
  (val again (get seen 2))
  (assert (isinstance failed WarmUnreachable) failed)
  (assert (in "503" failed.detail) failed)
  (assert (= #(absent.ready absent.preparing absent.until-ms) #(#() #() 0)) absent)
  (assert (isinstance again WarmState) again)
  (assert (= again.key absent.key) #(again absent))
  (assert (> again.until-ms 0) again))


(deftest test-placement-prefers-a-warm-worker-and-marks-a-cold-start
  (<- declared dict (declared-of "app-1"))
  (<- key str (key-on declared "linux-x86_64"))
  (val task (! (env-task "t1" declared)))
  ;; 準備済みの w2 を、名前順で先の w1(空き同じ)より優先する
  (val warm-state (ClusterState :workers {"w1" (! (worker-of "w1" #("net") 0)) "w2" (! (worker-of "w2" #("net") 0 :ready #(key)))}
                                :tasks {"t1" task}))
  (val placed (get (! (place-tasks 10 warm-state {} TIMING)) "t1"))
  (assert (= #(placed.phase placed.worker) #("assigned" "w2")) placed)
  ;; 準備済みが無ければ置くが phase は preparing(assigned と分ける)で、冷たい起動を数える
  (val cold-state (ClusterState :workers {"w1" (! (worker-of "w1" #("net") 0))} :tasks {"t1" task}))
  (val cold (get (! (place-tasks 10 cold-state {} TIMING)) "t1"))
  (assert (= #(cold.phase cold.worker) #("preparing" "w1")) cold)
  (val after (replace cold-state :tasks {"t1" cold}))
  (assert (= (. (get (load-of after {}) "w1") tasks) 1) "preparing の task も担い手の数に入る")
  (assert (= (lfor t (tasks-for after "w1") t.id) ["t1"]) "preparing の task も worker へ送る")
  ;; worker が準備済みを名乗った拍に assigned へ進む
  (val hb {"name" "w1" "provides" ["net"] "capacity" 2 "taskReserve" 0 "versions" {} "boot" None "platform" "linux-x86_64"
           "envs" {"ready" [key] "preparing" [] "failed" []} "envCapacity" "ok"})
  (val promoted (! (register-heartbeat after (! (heartbeat-of hb)) 20)))
  (assert (= (. (get promoted.tasks "t1") phase) "assigned") (get promoted.tasks "t1")))


(deftest test-cold-starts-are-counted-in-the-metrics
  (<- declared dict (declared-of "app-1"))
  (<- placed tuple (program-placed (ClusterState :workers {"w1" (! (worker-of "w1" #("net") 0))} :tasks {"t1" (! (env-task "t1" declared))}
                                                 :next-task 2)
                                   :now 10))
  (val submitted (responded (get placed 0) (! (http-request "POST" "/tasks" {}
                                                  {"program" (get placed 1) "revision" "" "versions" {} "needs" ["net"] "leaseSeconds" 60
                                                   "runtimeEnv" declared}))
                          10 TIMING))
  (assert (in "doeff_worker_env_cold_start_total 2" (metrics-text (get submitted 0) 10 TIMING))
          "冷たい起動(準備済みの worker が無い置き先)を数える"))


(deftest test-an-exhausted-worker-gets-no-task-whose-env-it-has-not-prepared
  (<- declared dict (declared-of "app-1"))
  (<- key str (key-on declared "linux-x86_64"))
  (val task (! (env-task "t1" declared)))
  (val two (ClusterState :workers {"w1" (! (worker-of "w1" #("net") 0 :capacity "exhausted")) "w2" (! (worker-of "w2" #("net") 0))}
                         :tasks {"t1" task}))
  (assert (= (. (get (! (place-tasks 10 two {} TIMING)) "t1") worker) "w2") "空きの尽きた worker を避ける")
  (val only (ClusterState :workers {"w1" (! (worker-of "w1" #("net") 0 :capacity "exhausted"))} :tasks {"t1" task}))
  (assert (= (. (get (! (place-tasks 10 only {} TIMING)) "t1") phase) "queued") "置ける先が無ければ待つ")
  (val ready (ClusterState :workers {"w1" (! (worker-of "w1" #("net") 0 :capacity "exhausted" :ready #(key)))} :tasks {"t1" task}))
  (assert (= (. (get (! (place-tasks 10 ready {} TIMING)) "t1") worker) "w1") "準備済みの env の task は置いてよい"))


;; --- worker の判断 -----------------------------------------------------------------------------

(defk warm-env-of [label]
  {:pre [(: label str)] :post [(: % WarmEnv)]}
  "温める env 1 つ(worker の root のキーと宣言の JSON の文字列)。"
  (<- declared dict (declared-of label))
  (<- key str (key-on declared (current-platform)))
  (WarmEnv :key (+ "env-" key) :runtime-env (json.dumps declared :sort-keys True :ensure-ascii False)))


(deftest test-the-worker-warms-after-its-jobs-and-retries-a-failed-warm-later
  (<- job-declared dict (declared-of "app-1"))
  (<- spec (task-spec {"id" "t1" "revision" "" "versions" {} "program" SAMPLE-TASK-PROGRAM "runtimeEnv" job-declared}
                       (Path "/tmp/tasks")))
  (<- warm WarmEnv (warm-env-of "app-2"))
  (val policy (WorkerPolicy))
  (assert (is-not spec.runtime-env None) spec)
  (val actions (! (plan 0 #(spec) (WorldView #() #()) {} policy :warm #(warm))))
  (assert (= actions #((PrepareEnv (code-key spec) spec.runtime-env) (PrepareEnv warm.key warm.runtime-env :warm True)))
          "job の準備が先・温める準備が後")
  ;; 準備済みの root には待ちの子を起こし(#3646)、待ちの子も準備済みなら何もしない。
  (val ready (WorldView #((CodeView warm.key CodeState.READY :path "/r")) #()))
  (<- started tuple (plan 0 #() ready {} policy :warm #(warm)))
  (assert (= (tuple (gfor a started #((type a) a.key))) #(#(StartWarmChild warm.key))) "準備済みの root には待ちの子を起こす")
  (val warmed (WorldView #((CodeView warm.key CodeState.READY :path "/r")) #()
                         :warm-children #((WarmChildView :key warm.key :pid 1 :started-ms 0 :mark (WarmChildMark :threads 1 :vm-live #(0 0 0))))))
  (assert (= (! (plan 0 #() warmed {} policy :warm #(warm))) #()) "root も待ちの子も準備済みなら何もしない")
  (val failed (WorldView #((CodeView warm.key CodeState.FAILED :detail "d" :failed-ms 0)) #()))
  (assert (= (! (plan 10 #() failed {} policy :warm #(warm))) #()) "失敗の直後は撃ち直さない")
  (assert (= (! (plan policy.code-retry-ms #() failed {} policy :warm #(warm)))
             #((PrepareEnv warm.key warm.runtime-env :warm True)))))


(deftest test-the-worker-pins-running-desired-warm-and-preparing-roots-for-the-sweep
  (<- job-declared dict (declared-of "app-1"))
  (<- spec (task-spec {"id" "t1" "revision" "" "versions" {} "program" SAMPLE-TASK-PROGRAM "runtimeEnv" job-declared}
                       (Path "/tmp/tasks")))
  (<- warm WarmEnv (warm-env-of "app-2"))
  (val world (WorldView #((CodeView "env-preparing" CodeState.PREPARING)) #() #()
                        :env-disk (EnvDisk :free 10 :sweep-wanted True :pinned (frozenset))))
  (val pinned (! (pinned-env-keys #(spec) world #(warm))))
  (assert (= pinned (frozenset #((code-key spec) warm.key "env-preparing"))) pinned)
  (val declaration (DeclarationRead :jobs #(spec) :warm #(warm)))
  (assert (= (! (sweep-actions declaration world)) #((SweepEnvs pinned))) "掃除の係が拍を求めていれば固定の集合を渡して掃除する")
  (val roomy (replace world :env-disk (EnvDisk :free 10 :sweep-wanted False :pinned pinned)))
  (assert (= (! (sweep-actions declaration roomy)) #())
          "掃除の係が拍を求めず、固定の集合が変わらなければ、共有の disk の空きが少なくても掃除の係を呼ばない(#3732)")
  ;; 宣言をまだ一度も読めていない間(起き直した直後 — #3731)は、掃除の係が拍を求めていても掃除の係を呼ばない。
  (assert (= (! (sweep-actions (NotYetRead) world)) #()) "宣言を読む前は掃除しない")
  (<- planned tuple (plan 0 #(spec) world {} (WorkerPolicy) :warm #(warm)))
  (assert (not (any (gfor a planned (isinstance a SweepEnvs))))
          "掃除は plan でなく最後に読めた宣言で判じる sweep-actions が出す"))


;; --- bytecode の焼き(#664 の実測から) -------------------------------------------------------------

(deftest test-compile-parallelism-follows-the-cgroup-cpu-limit
  (assert (= (! (cpu-limit-of "400000 100000\n" 16)) 4) "pod の上限 4 CPU は node の 16 より優先")
  (assert (= (! (cpu-limit-of "150000 100000\n" 16)) 2) "端数は切り上げる")
  (assert (= (! (cpu-limit-of "max 100000\n" 16)) 16) "上限の無い cgroup は使える CPU の数")
  (assert (= (! (cpu-limit-of None 3)) 3) "cgroup の file が無ければ使える CPU の数"))


(deftest test-bytecode-entries-travel-in-the-declaration-but-not-in-the-key
  (<- env RuntimeEnv (env-of "app-1" "lib-1" LOCK))
  (val narrowed (replace env :bytecode-entries #("app.main")))
  (<- declared dict (runtime-env->json narrowed))
  (<- back RuntimeEnv (runtime-env-of-json declared))
  (assert (= back.bytecode-entries #("app.main")) back)
  (<- key-a str (env-key env "linux-x86_64"))
  (<- key-b str (env-key narrowed "linux-x86_64"))
  (assert (= key-a key-b) "焼く範囲は root の file を変えないのでキーに入れない"))
