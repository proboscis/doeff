(require doeff-hy.macros [deftest val])

(import dataclasses [replace])
(import doeff_cluster.shared.intent.job_model [JobSpec])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterJob WorkerInfo WorkerReport Placement ClusterState])
(import doeff_cluster.coordinator.intent.request_bodies [StatusRow])
(import doeff_cluster.coordinator.core.cluster_policy [place-jobs jobs-for])
(import tests.program_rows [SAMPLE-TASK-PROGRAM])

(setv T (ClusterTiming :lease-ms 10000 :fence-ms 10000 :reassign-after-ms 30000))

(defn #^ ClusterJob job [#^ str name #^ tuple [needs #()] #^ (| str None) [pin None]]
  (ClusterJob (JobSpec name "m" #() "rev") :needs needs :pin pin))
(defn #^ WorkerInfo worker [#^ str name #^ int seen #^ int [capacity 10] #^ str #* provides] (WorkerInfo name (tuple (sorted provides)) capacity seen))

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

(import doeff_cluster.coordinator.intent.cluster_model [TaskRecord ComponentVersion])
(import doeff_cluster.coordinator.core.cluster_policy [place-tasks unplaced-jobs])

(setv AGENT "agent-cli")
(defn #^ WorkerInfo mac [#^ str name #^ int [seen 0]] (replace (worker name seen 10 AGENT "desk") :exclusive #(AGENT)))
(defn #^ WorkerInfo pod [#^ str name #^ int [seen 0]] (worker name seen 10 "cluster-net"))

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
                            :statuses {"mac" (WorkerReport :at 0 :endpoint None :jobs #((StatusRow :name "placer" :phase "running")))}))
  (assert (= (place-jobs 1000 state T) {}))
  (assert (= (get (unplaced-jobs 1000 (replace state :placements {}) T) "placer") "前の担い手が止め終えるのを待っている"))
  (setv stopped (replace state :placements {} :statuses {"mac" (WorkerReport :at 1500 :endpoint None :jobs #())}))
  (assert (= (. (get (place-jobs 2000 stopped T) "placer") worker) "atlas")))

(defn #^ TaskRecord task [#^ str id #^ tuple needs]
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


;; --- 能力の合う worker の一時の沈黙では task を失敗にしない(#2440)----------------------------------------------------------
;; 2026-10-01 22:54 に coordinator を入れ替えた直後の最初の判定で、worker 5 台が live でないとされ、待っていた task が「能力の合う
;; worker が無い」で即 失敗した。worker の Recreate の入れ替えの間(約 70 秒)も同じ。待っても晴れない理由(能力と版の合う worker が
;; 登録されていない)だけで失敗にし、登録された worker がいま黙っているだけなら task の lease の間は待つ。

(defn #^ ClusterState silent-verify-state [#^ int seen #^ bool detached]
  ;; 能力 verify を持つ唯一の worker(最後の連絡 = seen)と、それを要る待っている task 1 本(lease の期限 20000)の状態。
  (setv v (replace (worker "verify-1" seen 1 "verify") :versions #((ComponentVersion "python" "3")) :exclusive #("verify")))
  (ClusterState #() {"verify-1" v} {} {"t1" (replace (task "t1" #("verify")) :detached detached)}))

(deftest test-a-queued-task-waits-while-its-only-capable-worker-is-silent
  ;; 失敗ケース(直す前の形では failed): 唯一の能力の合う worker の最後の連絡が生存の窓(lease-ms 10 秒)より古い — 入れ替えの間の沈黙。
  (for [detached [False True]]
    (setv waiting (get (place-tasks 15000 (silent-verify-state 0 detached) {} T) "t1"))
    (assert (= waiting.phase "queued") #(detached waiting.phase waiting.detail))
    (assert (in "verify-1" waiting.detail) waiting.detail)
    (assert (in "いま連絡していない" waiting.detail) waiting.detail)
    ;; worker が連絡し直すと(最後の連絡が新しい)置かれる。
    (setv placed (get (place-tasks 15000 (silent-verify-state 14000 detached) {} T) "t1"))
    (assert (= #(placed.phase placed.worker) #("assigned" "verify-1")) #(detached placed.phase))))

(deftest test-a-queued-task-fails-when-no-registered-worker-can-ever-run-it
  ;; 待っても晴れない理由は今どおり失敗: 能力の合う worker が 1 台も登録されていない(上の test-task-follows-the-same-dedicated-rule と
  ;; 同じ)・登録された worker の版が違う・合う worker が待ちの期限(ClusterTiming.silent-worker-wait-ms)より長く live でない。
  (setv other (replace (worker "atlas" 0 10 "cluster-net") :versions #((ComponentVersion "python" "3"))))
  (setv none (get (place-tasks 15000 (ClusterState #() {"atlas" other} {} {"t1" (task "t1" #("verify"))}) {} T) "t1"))
  (assert (= none.phase "failed") none.phase)
  (setv old (replace (worker "verify-1" 0 1 "verify") :versions #((ComponentVersion "python" "2")) :exclusive #("verify")))
  (setv mismatch (get (place-tasks 15000 (ClusterState #() {"verify-1" old} {} {"t1" (task "t1" #("verify"))}) {} T) "t1"))
  (assert (= mismatch.phase "failed") mismatch.phase)
  (setv expired (get (place-tasks (+ T.silent-worker-wait-ms 1) (silent-verify-state 0 True) {} T) "t1"))
  (assert (= expired.phase "failed") expired.phase))


;; --- 待ちの上限は task の lease ではなく明示の期限(#2753)-------------------------------------------------------------------
;; 2026-10-02 唯一の能力の合う worker の入れ替え(古い Pod の drain → 新しい Pod の名乗り)の間に、待ち行列の切り離した task 2 本が
;; 「版と能力が合う worker が無い」で failed になった。切り離した task の lease は積んだ時の 60 秒しかなく、#2440 の待ちはその lease までだった。
;; 待つ長さは、能力と版の合う登録された worker の最後の連絡から ClusterTiming.silent-worker-wait-ms まで(lease とは別)。

(deftest test-a-queued-detached-task-waits-past-its-lease-while-the-capable-worker-is-away
  ;; 失敗ケース(直す前の版では「版と能力(専用の能力を含む)が合う worker が無い」で failed): task の lease の期限(20000)を過ぎ、唯一の
  ;; 能力の合う worker が 120 秒 live でない(drain に入ってから 60 秒より長い)。
  (val waiting (get (place-tasks 120000 (silent-verify-state 0 True) {} T) "t1"))
  (assert (= waiting.phase "queued") #(waiting.phase waiting.detail))
  (assert (in "verify-1 がいま連絡していない" waiting.detail) waiting.detail)
  (assert (in (.format "待ちの期限 = 最後の連絡から {} 秒" (// T.silent-worker-wait-ms 1000)) waiting.detail) waiting.detail)
  ;; 待っている間の記録は拍ごとに変わらない(detail に経った秒を書かない — 変われば調停が拍ごとに保存し、版を進める)。
  (assert (= (get (place-tasks 121000 (silent-verify-state 0 True) {} T) "t1") waiting))
  ;; worker が名乗り直すと置かれる(lease の期限を過ぎていても)。
  (val placed (get (place-tasks 120000 (silent-verify-state 119000 True) {} T) "t1"))
  (assert (= #(placed.phase placed.worker) #("assigned" "verify-1")) #(placed.phase placed.detail)))

(deftest test-a-queued-task-fails-by-name-when-the-capable-worker-stays-away-past-the-deadline
  ;; 期限ちょうどまでは待ち、過ぎた拍に、待った長さと期限を名指して失敗にする(切り離した task も、呼び手が問い合わせを続ける
  ;; — lease が延びている — 切り離していない task も)。
  (val limit T.silent-worker-wait-ms)
  (val named (.format "verify-1 が {} 秒 live でない(待ちの期限 {} 秒を過ぎた)" (+ (// limit 1000) 1) (// limit 1000)))
  (for [detached [False True]]
    (val silent (silent-verify-state 0 detached))
    (val state (replace silent :tasks (dfor #(k t) (.items silent.tasks) k (replace t :lease-until-ms (* 2 limit)))))
    (assert (= (. (get (place-tasks limit state {} T) "t1") phase) "queued") detached)
    (val failed (get (place-tasks (+ limit 1000) state {} T) "t1"))
    (assert (= #(failed.phase failed.finished-ms) #("failed" (+ limit 1000))) #(detached failed.phase))
    (assert (in named failed.detail) failed.detail)))

(deftest test-the-wait-counts-from-the-most-recent-capable-worker
  ;; 能力の合う worker が 2 台登録されている時は、最後の連絡が新しい方から数える(古い方が期限より長く黙っていても、新しい方が期限の内なら待つ)。
  (val limit T.silent-worker-wait-ms)
  (val old (replace (worker "verify-old" 0 1 "verify") :versions #((ComponentVersion "python" "3")) :exclusive #("verify")))
  ;; verify-new も生存の窓(10 秒)の外(61 秒 live でない)— 置けないが、期限の内なので待つ。
  (val new (replace (worker "verify-new" (- limit 60000) 1 "verify") :versions #((ComponentVersion "python" "3")) :exclusive #("verify")))
  (val state (ClusterState #() {"verify-old" old "verify-new" new} {} {"t1" (replace (task "t1" #("verify")) :detached True)}))
  (val waiting (get (place-tasks (+ limit 1000) state {} T) "t1"))
  (assert (= waiting.phase "queued") #(waiting.phase waiting.detail))
  (assert (in "verify-new・verify-old がいま連絡していない" waiting.detail) waiting.detail))


;; --- 能力の名乗りの形(ADR-DOE-CLUSTER-001 R4b)-----------------------------------------------------

(import doeff_cluster.coordinator.core.cluster_policy [placeable request-needs] doeff_cluster.coordinator.protocol.state_json [worker-capabilities-of])
(import doeff_cluster.shared.core.capabilities [capabilities-of])

(deftest test-placeable-is-needs-subset-of-provides-and-respects-exclusive
  (setv gpu (replace (worker "g" 0 10 "gpu" "cluster-net") :exclusive #("gpu")))
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
(import doeff_cluster.coordinator.intent.cluster_model [ClusterNaming])
(import tests.program_rows [heartbeat-of])
(import doeff_cluster.coordinator.core.cluster_policy [register-heartbeat with-derived-capabilities NODE-LABELS-TTL-MS])
(import doeff_cluster.coordinator.core.program [rollout-tick])
(import doeff_cluster.coordinator.protocol.kube [KubeMemory kube-memory])

(import doeff_hy.table [Table TableWrite table-of])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterObservations NodeLabelsSeen NodeLabelsUnreadable])

(setv COMPANY "company-machine")
(setv COMPANY-LABEL {"doeff.dev/company-machine" "true"})

(defk labels-table [labels]
  {:pre [(: labels (get dict #(str str)))] :post [(: % (get Table str))] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "検の node の label(鍵 → 値)を、観測の記録 NodeLabelsSeen が持つ表へ写すため。"
  (table-of (tuple (gfor #(key value) (.items labels) (TableWrite key value)))))

(defk node-observations [rows]
  {:pre [(: rows (get tuple #((get tuple #(str (| NodeLabelsSeen NodeLabelsUnreadable))) ...)))] :post [(: % ClusterObservations)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "node の名と観測(NodeLabelsSeen・NodeLabelsUnreadable)の組の列から、ClusterState.observations の値を作るため。"
  (ClusterObservations :nodes (table-of (tuple (gfor #(node seen) rows (TableWrite node seen))))))

(defk named [name node]
  {:pre [(: name str) (: node str)] :post [(: % dict)]}
  "company-machine を自分で名乗る worker の heartbeat の本文(node = 置かれた k8s の node)。"
  {"name" name "provides" ["net" COMPANY] "capacity" 10 "versions" {} "boot" "b1" "node" node "statuses" []})

(defk beat-as [state name node now]
  {:pre [(: state ClusterState) (: name str) (: node str) (: now int)] :post [(: % ClusterState)]}
  "worker name が node の上から company-machine を名乗る heartbeat を 1 つ受けた後の状態。"
  (<- body dict (named name node))
  (register-heartbeat state (heartbeat-of body) now))

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
  (setv secret (ClusterState #((job "secret" :needs #(COMPANY "net"))) s.workers))
  (assert (= (place-jobs 1000 secret T) {}))
  ;; derivable に無い能力は、今までどおり名乗りのまま受ける。
  (<- plain ClusterState (beat-as (ClusterState) "w" "" 1000))
  (assert (= (. (get plain.workers "w") provides) #(COMPANY "net"))))

(deftest test-only-the-worker-on-a-labelled-node-derives-company-machine
  (setv kube (KubeMemory {} :nodes {"node-company" COMPANY-LABEL "node-home" {"kubernetes.io/hostname" "home"}}))
  (<- start ClusterState (company-state 1000))
  (<- ticked ClusterState (tick-with start kube 1000))
  (assert (= (. (get ticked.workers "at-work") derived) #(COMPANY)))
  (assert (= (. (get ticked.workers "at-home") derived) #()))
  ;; company-machine を要る job は会社の node の worker にだけ置く。一般の job はどちらにも置ける。
  (setv secret (replace ticked :jobs #((job "secret" :needs #(COMPANY "net")) (job "other" :needs #(COMPANY)))))
  (setv placed (place-jobs 1000 secret T))
  (assert (= (sorted placed) ["other" "secret"]) placed)
  (assert (= (sfor p (.values placed) p.worker) #{"at-work"}) placed)
  ;; 同じ node の間の heartbeat は導いた能力を引き継ぐ(次の読みまで外さない)。
  (<- again ClusterState (beat-as ticked "at-work" "node-company" 2000))
  (assert (= (. (get again.workers "at-work") derived) #(COMPANY)))
  ;; 別の node へ移った名乗りは引き継がない(移った先の label を読むまで置かない)。
  (<- moved ClusterState (beat-as ticked "at-work" "node-home" 2000))
  (assert (= (. (get moved.workers "at-work") derived) #())))

(deftest test-derived-capabilities-are-kept-while-node-labels-cannot-be-read
  (setv kube (KubeMemory {} :nodes {"node-company" COMPANY-LABEL "node-home" {}}))
  (<- start ClusterState (company-state 1000))
  (<- ticked ClusterState (tick-with start kube 1000))
  ;; k8s の API が途絶えた間(label を読み直す間隔を過ぎても)、前の derived を保つ — 届かない間に足しも外しもしない。
  (setv kube.down True)
  (setv later (+ 1000 NODE-LABELS-TTL-MS 1))
  (<- work-beaten ClusterState (beat-as ticked "at-work" "node-company" later))
  (<- beaten ClusterState (beat-as work-beaten "at-home" "node-home" later))
  (<- cut-off ClusterState (tick-with beaten kube later))
  (assert (isinstance (.row cut-off.observations.nodes "node-company") NodeLabelsUnreadable) cut-off.observations)
  (assert (= (. (get cut-off.workers "at-work") derived) #(COMPANY)))
  (assert (= (. (get cut-off.workers "at-home") derived) #()))
  (setv secret (replace cut-off :jobs #((job "secret" :needs #(COMPANY "net")))))
  (assert (= (. (get (place-jobs later secret T) "secret") worker) "at-work"))
  ;; 読めるようになり label が外れていれば、次の読みで外す。
  (setv kube.down False)
  (setv (get kube.nodes "node-company") {})
  (<- back ClusterState (tick-with cut-off kube (+ later NODE-LABELS-TTL-MS 1)))
  (assert (= (. (get back.workers "at-work") derived) #())))

(deftest test-with-derived-capabilities-reads-the-naming-table
  ;; 表(ClusterNaming の node-capabilities)の label と値が合う行の能力だけを足す。node を名乗らない worker は空。
  (val table #(#("doeff.dev/company-machine" "true" COMPANY) #("example.org/gpu" "a100" "gpu")))
  (<- n1 (get Table str) (labels-table {"doeff.dev/company-machine" "true" "example.org/gpu" "a100"}))
  (<- n2 (get Table str) (labels-table {"doeff.dev/company-machine" "false"}))
  (<- seen ClusterObservations (node-observations #(#("n1" (NodeLabelsSeen :labels n1 :at 0))
                                                    #("n2" (NodeLabelsSeen :labels n2 :at 0)))))
  (val s (replace (ClusterState :workers {"a" (replace (worker "a" 0 10 "net") :node "n1")
                                         "b" (replace (worker "b" 0 10 "net") :node "n2")
                                         "c" (replace (worker "c" 0 10 "net") :derived #("stale"))})
                  :observations seen))
  (val out (with-derived-capabilities s table))
  (assert (= (. (get out.workers "a") derived) #(COMPANY "gpu")))
  (assert (= (. (get out.workers "b") derived) #()))
  (assert (= (. (get out.workers "c") derived) #())))


;; --- 旧い形の保存の行の読み直し(2026-09-27 より前の coordinator が書いた行)--------------------------------------------------
;; coordinator を落とさずに読む: 旧い worker の行(labels)と温める表の行(requires)は捨て(次の heartbeat・頼み直しで作り直す)、
;; まだ終わっていない旧い task の行は failed(理由つき)にし、終わった行はそのまま読む。

(import doeff_cluster.coordinator.intent.cluster_model [WarmEntry])
(import doeff_cluster.coordinator.protocol.state_json [state-to-json state-from-json])
(import doeff_cluster.coordinator.protocol.durable_kv [full-kv state-from-kv])

(setv SAVED (ClusterState :workers {"old" (worker "old" 0 10 "net") "new" (worker "new" 0 10 "net")}
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
  (setv open (get state.tasks "t1"))
  (assert (= open.phase "failed") open)
  (assert (in "旧い形の task" open.detail) open.detail)
  (assert (= open.needs #()) open)
  (assert (= (. (get state.tasks "t2") phase) "finished"))
  True)

(deftest test-old-saved-rows-in-the-state-file-are-read-without-crashing
  (setv data (state-to-json SAVED))
  (assert (get data "warms") "state file に温める表の行が在る(旧い形へ書き換える対象)")
  (setv workers [])
  (for [w (get data "workers")]
    (if (= (get w "name") "old")
        (do (<- row dict (to-old-worker w))
            (.append workers row))
        (.append workers w)))
  (setv tasks [])
  (for [t (get data "tasks")]
    (<- task-row dict (to-old-needs t))
    (.append tasks task-row))
  (setv warms {})
  (for [#(k v) (.items (get data "warms"))]
    (<- warm-row dict (to-old-needs v))
    (setv (get warms k) warm-row))
  (<- ok bool (check-old-read (state-from-json (| data {"workers" workers "tasks" tasks "warms" warms}) 5000)))
  (assert ok))

(deftest test-old-saved-rows-in-the-durable-kv-are-read-without-crashing
  (setv kv (full-kv SAVED))
  (assert (in "warm/k1" kv) (sorted kv))
  (setv old (dict kv))
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
  (<- known (get Table str) (labels-table COMPANY-LABEL))
  (<- seen ClusterObservations (node-observations #(#("known" (NodeLabelsSeen :labels known :at 0))
                                                    #("error" (NodeLabelsUnreadable :error "unavailable" :at 0)))))
  (val state (ClusterState :workers
    {"observed" (replace (worker "observed" 0) :node "known" :derived capabilities)
     "plain" (worker "plain" 0)
     "missing" (replace (worker "missing" 0) :node "missing" :derived capabilities)
     "error" (replace (worker "error" 0) :node "error" :derived capabilities)}
    :observations seen))
  (val result (with-derived-capabilities state table))
  (assert (= result state))
  (for [#(name original) (.items state.workers)]
    (assert (is (get result.workers name) original)))
  (assert (is result state)))

(deftest test-derived-capabilities-copy-only-changed-workers
  (val table #(#("doeff.dev/company-machine" "true" COMPANY)))
  (<- known (get Table str) (labels-table COMPANY-LABEL))
  (<- seen ClusterObservations (node-observations #(#("known" (NodeLabelsSeen :labels known :at 0)))))
  (val state (ClusterState :workers
    {"changed" (replace (worker "changed" 0) :node "known")
     "same" (worker "same" 0)
     "stale" (replace (worker "stale" 0) :derived #(COMPANY))}
    :observations seen))
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
  (<- known (get Table str) (labels-table COMPANY-LABEL))
  (<- seen ClusterObservations (node-observations #(#("known" (NodeLabelsSeen :labels known :at 0)))))
  (val state (ClusterState :workers {"w" (replace (worker "w" 0) :node "known" :derived #(COMPANY))} :observations seen))
  (assert (= (. (get (. (with-derived-capabilities state table) workers) "w") derived) #(COMPANY)))
  ;; node の観測値を書き換えた次の呼び出しでも、同じ worker の前回の結果を流用しない(観測は凍った値 — 書き換えは新しい表と記録)。
  (<- emptied (get Table str) (labels-table {}))
  (val relabelled (replace state :observations
                           (replace seen :nodes (.with-writes seen.nodes #((TableWrite "known" (NodeLabelsSeen :labels emptied :at 0)))))))
  (assert (= (. (get (. (with-derived-capabilities relabelled table) workers) "w") derived) #()))
  ;; 観測の label が表でなければ、判断の契約(derived-capabilities の :pre)が断る。
  (val broken (replace state :observations
                       (replace seen :nodes (.with-writes seen.nodes #((TableWrite "known" (NodeLabelsSeen :labels None :at 0)))))))
  (with [(pytest.raises AssertionError)]
    (with-derived-capabilities broken table)))


;; --- 観測の書きは版も保存も動かさない(#2728 J1)-----------------------------------------------------------------------------
;; k8s の観測(Deployment・node の label)は ClusterState.observations の表に在り、版の比べ(resource_policy.stamp の dirty-keys)と
;; 保存の差分(durable_kv.durable-delta)の外。観測を書き直すだけの拍は、資源の版・出来事の記録・保存の行を 1 つも動かさない。

(import doeff_hy.json_value [OpaqueJson])
(import doeff_cluster.coordinator.intent.cluster_model [DeploymentReading DeploymentSeen DeploymentUnreadable])
(import doeff_cluster.coordinator.core.resource_policy [stamp])
(import doeff_cluster.coordinator.protocol.durable_kv [durable-delta])

(deftest test-the-observation-write-moves-neither-versions-nor-the-store
  (val kube (KubeMemory {} :nodes {"node-company" COMPANY-LABEL "node-home" {}}))
  (<- start ClusterState (company-state 1000))
  (<- first ClusterState (tick-with start kube 1000))
  ;; 2 拍目は label を読み直す間隔の後: rollout-tick が node の観測を書き直すが、導く能力は同じ。
  (val later (+ 1000 NODE-LABELS-TTL-MS 1))
  (<- second ClusterState (tick-with first kube later))
  (val reread (.row second.observations.nodes "node-company"))
  (val first-read (.row first.observations.nodes "node-company"))
  (assert (and (isinstance reread NodeLabelsSeen) (= reread.at later)) reread)
  (assert (and (isinstance first-read NodeLabelsSeen) (= first-read.at 1000)) first-read)
  (assert (= #(second.revision second.audit-seq second.audit) #(first.revision first.audit-seq first.audit)))
  (<- reread-delta dict (durable-delta first second))
  (assert (= reread-delta {}) reread-delta)
  ;; Deployment の観測(読めた・読めなかった)を書いても同じ — 版を付ける stamp も保存の差分も動かない。検の状態は heartbeat の判断を
  ;; 直に呼んで作ったので資源に版の記録が無い(stamp は版の記録の無い資源に版を振る)— 先に版を振り揃えてから比べる。
  (val settled (stamp (ClusterState) second "c-test" later T))
  (val reading (DeploymentReading :spec-replicas 1 :replicas 1 :ready-replicas 1 :available-replicas 1 :updated-replicas 1
                                  :generation 2 :observed-generation 2 :annotations (OpaqueJson.of {})))
  (val seen settled.observations)
  (val observed (replace settled :observations
                         (replace seen :deployments (.with-writes seen.deployments
                                                                  #((TableWrite "prod/a" (DeploymentSeen :reading reading :at later))
                                                                    (TableWrite "prod/b" (DeploymentUnreadable :error "x" :at later)))))))
  (assert (= (.size observed.observations.deployments) 2))
  (val stamped (stamp settled observed "rollout-controller" later T))
  (assert (= #(stamped.revision stamped.audit-seq stamped.meta) #(settled.revision settled.audit-seq settled.meta)))
  (<- observed-delta dict (durable-delta settled stamped))
  (assert (= observed-delta {}) observed-delta)
  ;; 反例の対照: 保存する欄(worker の記録)を書けば、同じ比べが版と差分を出す(比べが何も見ていないのではない)。
  (val moved (replace observed :workers (| observed.workers {"at-home" (replace (get observed.workers "at-home") :capacity 3)})))
  (assert (> (. (stamp settled moved "c-test" later T) revision) settled.revision))
  (<- moved-delta dict (durable-delta settled moved))
  (assert (in "worker/at-home" moved-delta) moved-delta))
