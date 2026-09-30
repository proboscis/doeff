(require doeff-hy.macros [deftest])

(import dataclasses [replace])
(import doeff_cluster.worker_model [JobSpec])
(import doeff_cluster.cluster_model [ClusterJob WorkerInfo Placement ClusterTiming ClusterState])
(import doeff_cluster.cluster_policy [place-jobs jobs-for])
(import tests.program_rows [SAMPLE-TASK-PROGRAM])

(setv T (ClusterTiming :lease-ms 10000 :fence-ms 10000 :reassign-after-ms 30000))

(defn job [name #** kw] (ClusterJob (JobSpec name "m" #() "rev") #** kw))
(defn worker [name seen [capacity 10] #* provides] (WorkerInfo name (tuple (sorted provides)) capacity seen))

(deftest test-spreads-and-respects-capabilities-and-pins
  (setv state (ClusterState
    #((job "a") (job "b") (job "k" :needs #("cluster-net")) (job "p" :pin "mac"))
    {"mac" (worker "mac" 0) "new" (worker "new" 0) "pod" (worker "pod" 0 10 "cluster-net")}))
  (setv result (place-jobs 1000 state T))
  (assert (= (. (get result "k") worker) "pod"))
  (assert (= (. (get result "p") worker) "mac"))
  ;; a と b は空きの多い順に散る(p が mac・k が pod に載った後なので new が先)
  (assert (= (sorted (lfor n ["a" "b"] (. (get result n) worker))) ["mac" "new"])))

(deftest test-placement-is-stable-while-worker-is-alive
  (setv state (ClusterState #((job "a")) {"mac" (worker "mac" 0) "new" (worker "new" 0)}
                            {"a" (Placement "a" "new" 3 0)}))
  (assert (= (get (place-jobs 5000 state T) "a") (Placement "a" "new" 3 0))))

(deftest test-silent-worker-keeps-job-until-reassign-deadline
  ;; new の最後の heartbeat は 0。fence(T の 10 秒)で new は自分で止めている。移すのは 30 秒後から。
  (setv state (ClusterState #((job "a")) {"mac" (worker "mac" 40000) "new" (worker "new" 0)}
                            {"a" (Placement "a" "new" 1 0)}))
  (assert (= (. (get (place-jobs 30000 state T) "a") worker) "new"))
  (setv moved (get (place-jobs 30001 state T) "a"))
  (assert (= #(moved.worker moved.generation) #("mac" 2))))

(deftest test-no_candidate-leaves-job-unassigned-and-removed-job-is-dropped
  (setv state (ClusterState #((job "k" :needs #("cluster-net"))) {"mac" (worker "mac" 0)}
                            {"gone" (Placement "gone" "mac" 1 0)}))
  (assert (= (place-jobs 1000 state T) {})))

(deftest test-capacity-and-jobs-for
  (setv state (ClusterState #((job "a") (job "b") (job "c")) {"mac" (worker "mac" 0 2)}))
  (setv result (place-jobs 0 state T))
  (assert (= (sorted result) ["a" "b"]))
  (assert (= (lfor s (jobs-for (replace state :placements result) "mac") s.name) ["a" "b"])))

(deftest test-timing-rejects-reassign-before-fence
  (import pytest)
  (with [(pytest.raises ValueError)] (ClusterTiming :fence-ms 30000 :reassign-after-ms 30000)))



;; --- 能力と専用の能力(exclusive — 以前の dedicated の印・k8s の taint に当たる) ---------------------------------

(import doeff_cluster.cluster_model [TaskRecord ComponentVersion])
(import doeff_cluster.cluster_policy [place-tasks unplaced-jobs])

(setv AGENT "agent-cli")
(defn mac [name [seen 0]] (replace (worker name seen 10 AGENT "desk") :exclusive #(AGENT)))
(defn pod [name [seen 0]] (worker name seen 10 "cluster-net"))

(deftest test-general-job-is-not-placed-on-a-dedicated-worker
  ;; Mac の方が空いていても、専用の印を求めない job は k3s へ
  (setv state (ClusterState #((job "a") (job "b") (job "c")) {"mac" (mac "mac") "atlas" (pod "atlas")}))
  (assert (= (sfor n ["a" "b" "c"] (. (get (place-jobs 1000 state T) n) worker)) #{"atlas"})))

(deftest test-agent-job-goes-to-the-dedicated-worker
  (setv state (ClusterState #((job "runner" :needs #(AGENT))) {"mac" (mac "mac") "atlas" (pod "atlas")}))
  (assert (= (. (get (place-jobs 1000 state T) "runner") worker) "mac")))

(deftest test-pin-does-not-override-the-dedicated-mark
  (setv state (ClusterState #((job "p" :pin "mac")) {"mac" (mac "mac")}))
  (assert (= (place-jobs 1000 state T) {}))
  (assert (in "置ける worker が無い" (get (unplaced-jobs 1000 state T) "p"))))

(deftest test-job-without-a-place-is-reported-unplaced
  ;; agent の job で Mac が居ない・一般の job で k3s が居ない
  (setv state (ClusterState #((job "runner" :needs #(AGENT)) (job "placer")) {"atlas" (pod "atlas")}))
  (setv s2 (ClusterState #((job "placer")) {"mac" (mac "mac")}))
  (assert (not-in "runner" (place-jobs 1000 state T)))
  (assert (in "置ける worker が無い" (get (unplaced-jobs 1000 state T) "runner")))
  (assert (in "置ける worker が無い" (get (unplaced-jobs 1000 s2 T) "placer"))))

(deftest test-job-leaving-a-worker-that-became-dedicated-waits-until-it-stopped-there
  ;; mac が専用の印を付けて戻った。mac に載っていた一般の job は外れ、mac がまだ動かしていると報告している間は置かず、
  ;; 止め終えた報告の後で k3s へ置く(同じ job を 2 つ動かさない)。
  (setv state (ClusterState #((job "placer")) {"mac" (mac "mac") "atlas" (pod "atlas")}
                            {"placer" (Placement "placer" "mac" 2 0)}
                            :statuses {"mac" {"at" 0 "jobs" [{"name" "placer" "phase" "running"}]}}))
  (assert (= (place-jobs 1000 state T) {}))
  (assert (= (get (unplaced-jobs 1000 (replace state :placements {}) T) "placer") "前の担い手が止め終えるのを待っている"))
  (setv stopped (replace state :placements {} :statuses {"mac" {"at" 1500 "jobs" []}}))
  (assert (= (. (get (place-jobs 2000 stopped T) "placer") worker) "atlas")))

(defn task [id needs]
  (TaskRecord id "digest" SAMPLE-TASK-PROGRAM "rev" #((ComponentVersion "python" "3")) needs 15000 20000 0))

(deftest test-task-follows-the-same-dedicated-rule
  (setv workers {"mac" (replace (mac "mac") :versions #((ComponentVersion "python" "3")))
                 "atlas" (replace (pod "atlas") :versions #((ComponentVersion "python" "3")))})
  (setv state (ClusterState #() workers {} {"t1" (task "t1" #()) "t2" (task "t2" #(AGENT))}))
  (setv placed (place-tasks 1000 state {} T))
  (assert (= #((. (get placed "t1") worker) (. (get placed "t2") worker)) #("atlas" "mac")))
  ;; agent の task で Mac が居なければ、送らずに失敗(理由に要る能力)
  (setv only-pod (ClusterState #() {"atlas" (get workers "atlas")} {} {"t3" (task "t3" #(AGENT))}))
  (setv failed (get (place-tasks 1000 only-pod {} T) "t3"))
  (assert (= failed.phase "failed"))
  (assert (in "agent-cli" failed.detail)))


;; --- 能力の名乗りの形(ADR-DOE-CLUSTER-001 R4b)-----------------------------------------------------

(import doeff_cluster.cluster_policy [placeable worker-capabilities-of request-needs])
(import doeff_cluster.cluster_model [capabilities-of])

(deftest test-placeable-is-needs-subset-of-provides-and-respects-exclusive
  (val gpu (replace (worker "g" 0 10 "gpu" "cluster-net") :exclusive #("gpu")))
  (assert (placeable #("gpu") gpu))
  (assert (placeable #("cluster-net" "gpu") gpu))
  ;; 専用の能力を要らない一般の仕事は、提供されていても置かない
  (assert (not (placeable #("cluster-net") gpu)))
  (assert (not (placeable #() gpu)))
  ;; 提供の外の能力を要る仕事は置かない
  (assert (not (placeable #("claude-cli") (worker "p" 0 10 "cluster-net"))))
  (assert (placeable #() (worker "p" 0 10 "cluster-net"))))

(deftest test-old-label-forms-are-refused-with-a-reason
  (import pytest)
  (with [e (pytest.raises ValueError)] (capabilities-of {"kind" "k3s"} "needs"))
  (assert (in "旧い requires / labels の形は受け付けない" (str e.value)))
  (with [e (pytest.raises ValueError)] (capabilities-of ["kind=k3s"] "needs"))
  (assert (in "label の形" (str e.value)))
  (with [e (pytest.raises ValueError)] (request-needs {"requires" {"role" "agent"}} "needs"))
  (assert (in "旧い形の requires" (str e.value)))
  (with [e (pytest.raises ValueError)] (worker-capabilities-of {"labels" {"kind" "mac"}} "worker"))
  (assert (in "--provides" (str e.value)))
  (with [e (pytest.raises ValueError)] (worker-capabilities-of {"provides" ["a"] "exclusive" ["b"]} "worker"))
  (assert (in "provides" (str e.value)))
  (assert (= (worker-capabilities-of {"provides" ["b" "a" "a"] "exclusive" ["a"]} "worker") #(#("a" "b") #("a")))))


;; --- node の label から導く能力(company-machine — ADR-DOE-CLUSTER-001 R4b・改訂 1 の I)-----------------------------------
;; 会社の機体の境界を worker の自己申告に任せない: heartbeat で company-machine を名乗っても provides に入らず、coordinator が
;; worker の置かれた node の label(doeff.dev/company-machine=true)を読んで derived に足した worker にだけ、それを要る job を置く。

(require doeff-hy.macros [defk <- val var])
(import doeff [with_handlers])
(import doeff_cluster.cluster_model [ClusterNaming])
(import doeff_cluster.cluster_policy [register-heartbeat with-derived-capabilities NODE-LABELS-TTL-MS])
(import doeff_cluster.coordinator [rollout-tick])
(import doeff_cluster.kube_handlers [KubeMemory kube-memory])

(setv COMPANY "company-machine")
(setv COMPANY-LABEL {"doeff.dev/company-machine" "true"})

(defk named [name node]
  {:pre [(: name str) (: node str)] :post [(: % dict)]}
  "company-machine を自分で名乗る worker の heartbeat の本文(node = 置かれた k8s の node)。"
  {"name" name "provides" ["net" COMPANY] "capacity" 10 "versions" {} "boot" "b1" "node" node "statuses" []})

(defk beat-as [state name node now]
  {:pre [(: state ClusterState) (: name str) (: node str) (: now int)] :post [(: % ClusterState)]}
  "worker name が node の上から company-machine を名乗る heartbeat を 1 つ受けた後の状態。"
  (<- body dict (named name node))
  (register-heartbeat state body now))

(defk company-state [now]
  {:pre [(: now int)] :post [(: % ClusterState)]}
  "derivable に company-machine を持つ coordinator の状態へ、会社の node の worker と人の node の worker が名乗った後。"
  (<- at-work ClusterState (beat-as (ClusterState :derivable (frozenset [COMPANY])) "at-work" "node-company" now))
  (<- both ClusterState (beat-as at-work "at-home" "node-home" now))
  both)

(defk tick-with [state kube now]
  {:pre [(: state ClusterState) (: kube KubeMemory) (: now int)] :post [(: % ClusterState)]}
  "coordinator の調停の 1 拍(rollout-tick — node の label の読みと能力の導出を含む)をテストの k8s の上で回す。"
  (<- after ClusterState (with_handlers [(kube-memory kube)] (rollout-tick state T (ClusterNaming) now)))
  after)

(deftest test-a-self-declared-company-machine-is-not-a-provided-capability
  (<- s ClusterState (company-state 1000))
  (assert (= (. (get s.workers "at-work") provides) #("net")))
  (assert (= (. (get s.workers "at-home") provides) #("net")))
  (assert (= (. (get s.workers "at-work") node) "node-company"))
  ;; label を読む前は、どちらにも company-machine を要る job を置かない。
  (val secret (ClusterState #((job "secret" :needs #(COMPANY "net"))) s.workers))
  (assert (= (place-jobs 1000 secret T) {}))
  ;; derivable に無い能力は、今までどおり名乗りのまま受ける。
  (<- plain ClusterState (beat-as (ClusterState) "w" "" 1000))
  (assert (= (. (get plain.workers "w") provides) #(COMPANY "net"))))

(deftest test-only-the-worker-on-a-labelled-node-derives-company-machine
  (val kube (KubeMemory {} :nodes {"node-company" COMPANY-LABEL "node-home" {"kubernetes.io/hostname" "home"}}))
  (<- start ClusterState (company-state 1000))
  (<- ticked ClusterState (tick-with start kube 1000))
  (assert (= (. (get ticked.workers "at-work") derived) #(COMPANY)))
  (assert (= (. (get ticked.workers "at-home") derived) #()))
  ;; company-machine を要る job は会社の node の worker にだけ置く。一般の job はどちらにも置ける。
  (val secret (replace ticked :jobs #((job "secret" :needs #(COMPANY "net")) (job "other" :needs #(COMPANY)))))
  (val placed (place-jobs 1000 secret T))
  (assert (= (sorted placed) ["other" "secret"]) placed)
  (assert (= (sfor p (.values placed) p.worker) #{"at-work"}) placed)
  ;; 同じ node の間の heartbeat は導いた能力を引き継ぐ(次の読みまで外さない)。
  (<- again ClusterState (beat-as ticked "at-work" "node-company" 2000))
  (assert (= (. (get again.workers "at-work") derived) #(COMPANY)))
  ;; 別の node へ移った名乗りは引き継がない(移った先の label を読むまで置かない)。
  (<- moved ClusterState (beat-as ticked "at-work" "node-home" 2000))
  (assert (= (. (get moved.workers "at-work") derived) #())))

(deftest test-derived-capabilities-are-kept-while-node-labels-cannot-be-read
  (val kube (KubeMemory {} :nodes {"node-company" COMPANY-LABEL "node-home" {}}))
  (<- start ClusterState (company-state 1000))
  (<- ticked ClusterState (tick-with start kube 1000))
  ;; k8s の API が途絶えた間(label を読み直す間隔を過ぎても)、前の derived を保つ — 届かない間に足しも外しもしない。
  (setv kube.down True)
  (val later (+ 1000 NODE-LABELS-TTL-MS 1))
  (<- work-beaten ClusterState (beat-as ticked "at-work" "node-company" later))
  (<- beaten ClusterState (beat-as work-beaten "at-home" "node-home" later))
  (<- cut-off ClusterState (tick-with beaten kube later))
  (assert (in "error" (get cut-off.nodes "node-company")) cut-off.nodes)
  (assert (= (. (get cut-off.workers "at-work") derived) #(COMPANY)))
  (assert (= (. (get cut-off.workers "at-home") derived) #()))
  (val secret (replace cut-off :jobs #((job "secret" :needs #(COMPANY "net")))))
  (assert (= (. (get (place-jobs later secret T) "secret") worker) "at-work"))
  ;; 読めるようになり label が外れていれば、次の読みで外す。
  (setv kube.down False)
  (setv (get kube.nodes "node-company") {})
  (<- back ClusterState (tick-with cut-off kube (+ later NODE-LABELS-TTL-MS 1)))
  (assert (= (. (get back.workers "at-work") derived) #())))

(deftest test-with-derived-capabilities-reads-the-naming-table
  ;; 表(ClusterNaming の node-capabilities)の label と値が合う行の能力だけを足す。node を名乗らない worker は空。
  (val table #(#("doeff.dev/company-machine" "true" COMPANY) #("example.org/gpu" "a100" "gpu")))
  (val s (replace (ClusterState :workers {"a" (replace (worker "a" 0 10 "net") :node "n1")
                                          "b" (replace (worker "b" 0 10 "net") :node "n2")
                                          "c" (replace (worker "c" 0 10 "net") :derived #("stale"))})
                  :nodes {"n1" {"labels" {"doeff.dev/company-machine" "true" "example.org/gpu" "a100"} "at" 0}
                          "n2" {"labels" {"doeff.dev/company-machine" "false"} "at" 0}}))
  (val out (with-derived-capabilities s table))
  (assert (= (. (get out.workers "a") derived) #(COMPANY "gpu")))
  (assert (= (. (get out.workers "b") derived) #()))
  (assert (= (. (get out.workers "c") derived) #())))


;; --- 旧い形の保存の行の読み直し(2026-09-27 より前の coordinator が書いた行)--------------------------------------------------
;; coordinator を落とさずに読む: 旧い worker の行(labels)と温める表の行(requires)は捨て(次の heartbeat・頼み直しで作り直す)、
;; まだ終わっていない旧い task の行は failed(理由つき)にし、終わった行はそのまま読む。

(import doeff_cluster.cluster_model [WarmEntry])
(import doeff_cluster.cluster_policy [state-to-json state-from-json])
(import doeff_cluster.durable_kv [full-kv state-from-kv])

(val SAVED (ClusterState :workers {"old" (worker "old" 0 10 "net") "new" (worker "new" 0 10 "net")}
                         :tasks {"t1" (task "t1" #("net")) "t2" (replace (task "t2" #("net")) :phase "finished")}
                         :warms {"k1" (WarmEntry "k1" {"repos" []} #("net") 999999 "svc-a")}
                         :next-task 3))

(defk to-old-worker [row]
  {:pre [(: row dict)] :post [(: % dict)]}
  "新しい形の worker の行 → 旧い形(labels だけ)。"
  (| (dfor #(k v) (.items row) :if (not-in k #("provides" "exclusive" "node")) k v) {"labels" {"kind" "k3s"}}))

(defk to-old-needs [row]
  {:pre [(: row dict)] :post [(: % dict)]}
  "新しい形の task・温める表の行 → 旧い形(needs の代わりに requires の object)。"
  (| (dfor #(k v) (.items row) :if (!= k "needs") k v) {"requires" {"kind" "k3s"}}))

(defk check-old-read [state]
  {:pre [(: state ClusterState)] :post [(: % bool)]}
  "旧い行を読んだ状態: 旧い worker と温める表の行は捨て、終わっていない task は failed(理由つき)、終わった task はそのまま。"
  (assert (= (sorted state.workers) ["new"]) state.workers)
  (assert (= state.warms {}) state.warms)
  (val open (get state.tasks "t1"))
  (assert (= open.phase "failed") open)
  (assert (in "旧い形の task" open.detail) open.detail)
  (assert (= open.needs #()) open)
  (assert (= (. (get state.tasks "t2") phase) "finished"))
  True)

(deftest test-old-saved-rows-in-the-state-file-are-read-without-crashing
  (val data (state-to-json SAVED))
  (assert (get data "warms") "state file に温める表の行が在る(旧い形へ書き換える対象)")
  (val workers [])
  (for [w (get data "workers")]
    (if (= (get w "name") "old")
        (do (<- row dict (to-old-worker w))
            (.append workers row))
        (.append workers w)))
  (val tasks [])
  (for [t (get data "tasks")]
    (<- task-row dict (to-old-needs t))
    (.append tasks task-row))
  (val warms {})
  (for [#(k v) (.items (get data "warms"))]
    (<- warm-row dict (to-old-needs v))
    (setv (get warms k) warm-row))
  (<- ok bool (check-old-read (state-from-json (| data {"workers" workers "tasks" tasks "warms" warms}) 5000)))
  (assert ok))

(deftest test-old-saved-rows-in-the-durable-kv-are-read-without-crashing
  (val kv (full-kv SAVED))
  (assert (in "warm/k1" kv) (sorted kv))
  (val old (dict kv))
  (<- worker-row dict (to-old-worker (get kv "worker/old")))
  (setv (get old "worker/old") worker-row)
  (for [k (lfor k kv :if (or (.startswith k "task/") (.startswith k "warm/")) k)]
    (<- row dict (to-old-needs (get kv k)))
    (setv (get old k) row))
  (<- ok bool (check-old-read (state-from-kv old 5000)))
  (assert ok))


(deftest test-derived-capabilities-reuse-equal-values
  ;; 計算で出来る tuple と別の object でも、値が等しければ既存の worker と状態を返す。
  (val capabilities (tuple [COMPANY]))
  (val table #(#("doeff.dev/company-machine" "true" COMPANY)))
  (val state (ClusterState :workers
    {"observed" (replace (worker "observed" 0) :node "known" :derived capabilities)
     "plain" (worker "plain" 0)
     "missing" (replace (worker "missing" 0) :node "missing" :derived capabilities)
     "error" (replace (worker "error" 0) :node "error" :derived capabilities)}
    :nodes {"known" {"labels" COMPANY-LABEL "at" 0}
            "error" {"error" "unavailable" "at" 0}}))
  (val result (with-derived-capabilities state table))
  (assert (= result state))
  (for [#(name original) (.items state.workers)]
    (assert (is (get result.workers name) original)))
  (assert (is result state)))

(deftest test-derived-capabilities-copy-only-changed-workers
  (val table #(#("doeff.dev/company-machine" "true" COMPANY)))
  (val state (ClusterState :workers
    {"changed" (replace (worker "changed" 0) :node "known")
     "same" (worker "same" 0)
     "stale" (replace (worker "stale" 0) :derived #(COMPANY))}
    :nodes {"known" {"labels" COMPANY-LABEL "at" 0}}))
  (val result (with-derived-capabilities state table))
  (assert (is-not result state))
  (assert (is (get result.workers "same") (get state.workers "same")))
  (assert (is-not (get result.workers "changed") (get state.workers "changed")))
  (assert (= (. (get result.workers "changed") derived) #(COMPANY)))
  (assert (= (. (get result.workers "stale") derived) #()))
  (assert (= (. (get state.workers "changed") derived) #()))
  (assert (= (. (get state.workers "stale") derived) #(COMPANY)))
  ;; 同じ state でも表が変われば計算し直して、能力を外す。
  (val removed (with-derived-capabilities result #()))
  (assert (= (. (get removed.workers "changed") derived) #()))
  (assert (is (get removed.workers "same") (get result.workers "same"))))

(deftest test-derived-capabilities-still-read-and-check-labels
  (import pytest)
  (val table #(#("doeff.dev/company-machine" "true" COMPANY)))
  (val state (ClusterState :workers
    {"w" (replace (worker "w" 0) :node "known" :derived #(COMPANY))}
    :nodes {"known" {"labels" (dict COMPANY-LABEL) "at" 0}}))
  (with-derived-capabilities state table)
  ;; node の観測値を更新した次の呼び出しでも、同じ worker の前回の結果を流用しない。
  (setv (get state.nodes "known" "labels") {})
  (assert (= (. (get (. (with-derived-capabilities state table) workers) "w") derived) #()))
  (setv (get state.nodes "known" "labels") None)
  (with [(pytest.raises AssertionError)]
    (with-derived-capabilities state table)))
