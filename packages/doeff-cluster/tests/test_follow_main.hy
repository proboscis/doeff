;; 本番の業務コードの版への追随(2026-09-24): coordinator が本番の Deployment の image の版を読んで Service の base を進め、
;; worker が Service の入れ替え(handoff)で新を旧と並べて起こし、新が Ready と数えられてから旧を止める。
;;
;; 仮想の時計の上の小さな世界で、coordinator の本物の判断(api_policy.respond・coordinator.rollout-tick)と worker の本物の判断
;; (worker_policy.plan / records-after / statuses)をつなぐ。process の中身(lease を持つ書き手)だけを模す:
;;   - 起きて JOB-START 後から毎拍 ReportReady(真)を送る。lease を持てば role active、持てなければ role standby(待機の拍)。
;;   - lease は 1 つ。持ち主が終わると、worker の ReleaseLeases で返る(期限を待たない)。空いた lease は次の拍に待機の process が取る。
;;   - TERM を受けた process は次の拍で終わる(本番の process と同じく SIGTERM で即座に終わる)。
(require doeff-hy.macros [deftest])
(import dataclasses [replace])
(import doeff [run with_handlers])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_cluster.cluster_model [ClusterTiming ClusterNaming ClusterState Request])
(import doeff_cluster.api_policy [respond ready-instances])
(import doeff_cluster.coordinator [rollout-tick])
(import doeff_cluster.kube_handlers [KubeMemory kube-memory])
(import doeff_cluster.image_handlers [image-memory split-image])
(import doeff_cluster.handlers [status-row])
(import doeff_cluster.metrics_policy [metrics-text])
(import doeff_cluster.cluster_policy [still-live-somewhere])
(import doeff_cluster.base_follow_policy [follow-bases image-entry BASE-FOLLOW-ACTOR])
(import doeff_cluster.worker_model [JobSpec CodeView CodeState ProcessView WorldView WorkerPolicy JobRecord
                        PrepareCode StartJob SignalJob ReapJob RetireJob ReleaseLeases StopStage code-key spec-hash])
(import doeff_cluster.worker_policy [plan records-after statuses])

(setv T (ClusterTiming))
;; 外の系と取り交わす名(配備する側が決める)。版の LABEL と、一緒に写す LABEL 1 つ。
(setv N (ClusterNaming :revision-label "org.example.app-revision" :version-labels #(#("runtime" "org.example.runtime-revision"))))
(setv REVISION-LABEL N.revision-label)
(setv V {"python" "3.14.0"})
(setv DEP "prod/app-writer")
(setv SHA1 (* "1" 40) SHA2 (* "2" 40) WRAP "w0")
(setv IMG1 "zeus:5000/app:20260924-1111111" IMG2 "zeus:5000/app:20260925-2222222")
(setv JOB-START 2000)
(setv SERVICE {"revision" WRAP "requires" {} "entry" "m" "args" [] "replicas" 1 "readiness" {"windowSeconds" 10}
               "update" "handoff"
               "baseFrom" {"kind" "Deployment" "namespace" "prod" "name" "app-writer" "container" "app-writer"}})


(defn deployment [image]
  {"specReplicas" 0 "replicas" 0 "readyReplicas" 0 "availableReplicas" 0 "updatedReplicas" 0
   "generation" 1 "observedGeneration" 1 "annotations" {} "images" {"app-writer" image}})


(defclass Sim []
  (defn __init__ [self]
    (setv self.now 2000000
          self.kube (KubeMemory {DEP (deployment IMG1)})
          self.images {IMG1 {REVISION-LABEL SHA1} IMG2 {REVISION-LABEL SHA2}}
          self.policy (WorkerPolicy :stop-grace-ms 10000)
          self.records {} self.processes [] self.codes {} self.pids 100
          self.lease None          ; lease を持つ process の世代の名
          self.desired #()
          self.log [])            ; 拍ごと #(時刻 active の世代 走っている process の数 Ready)
    (setv self.state (ClusterState :started-ms (- self.now 60000)))
    (self.call "POST" "/resources/Service" {"name" "writer-a" "spec" SERVICE})
    None)

  (defn call [self method path [body None] [actor "c-test"]]
    (setv #(state status reply) (respond self.state (Request method path {} body :actor actor) self.now T))
    (assert (< status 300) #(method path status reply))
    (setv self.state state)
    reply)

  (defn world [self]
    (WorldView (tuple (gfor #(k ready-at) (.items self.codes) :if (<= ready-at self.now)
                            (CodeView k CodeState.READY (+ "/c/" k))))
               (tuple self.processes)))

  (defn apply [self action]
    (cond
      (isinstance action PrepareCode) (.setdefault self.codes action.revision (+ self.now 1000))
      (isinstance action StartJob)
        (do (+= self.pids 1)
            (.append self.processes (ProcessView action.spec.name action.spec action.attempt self.pids self.now
                                                 :instance (.format "{}-sim{}" action.attempt self.now))))
      (isinstance action RetireJob)
        (setv self.processes (lfor p self.processes (if (= p.pid action.pid) (replace p :name action.new-name :retired-from action.name) p)))
      (isinstance action SignalJob)
        ;; SIGTERM で即座に終わる(本番の書き手と同じ)。
        (setv self.processes (lfor p self.processes (if (= p.pid action.pid) (replace p :exit-code -15) p)))
      (isinstance action ReapJob) (setv self.processes (lfor p self.processes :if (!= p.pid action.pid) p))
      (isinstance action ReleaseLeases) (when (= self.lease action.instance) (setv self.lease None))))

  (defn worker-tick [self]
    (setv world (self.world) actions (plan self.now self.desired world self.records self.policy))
    (for [a actions] (self.apply a))
    (setv self.records (records-after self.now self.records actions self.policy))
    (setv rows (lfor s (statuses self.now self.desired (self.world) self.records self.policy) (status-row s)))
    (setv reply (self.call "POST" "/heartbeat" {"name" "zeus" "labels" {} "capacity" 10 "versions" V "statuses" rows} :actor None))
    (setv self.desired (tuple (gfor j (get reply "jobs")
                                    (JobSpec (get j "name") (get j "entry") (tuple (get j "args")) (get j "revision")
                                             :placement (.get j "placement") :base (.get j "base")
                                             :handoff (bool (.get j "handoff")) :ready-instance (.get j "readyInstance"))))))

  (defn processes-tick [self]
    ;; 空いた lease は待機の process が取る(取りに行く係が 0.5 秒ごとに読み直す)。
    (setv live (lfor p self.processes :if (is p.exit-code None) p))
    (when (and (is self.lease None) live)
      (setv self.lease (. (max live :key (fn [p] p.started-ms)) instance)))
    (for [p live]
      (when (>= self.now (+ p.started-ms JOB-START))
        (self.call "POST" "/resources/Service/writer-a/readiness"
                   {"worker" "zeus" "pid" p.pid "revision" p.spec.revision "instance" p.instance "attempt" (str p.attempt)
                    "specHash" (spec-hash p.spec) "placement" p.spec.placement "ready" True "reason" "拍を終えた"
                    "role" (if (= self.lease p.instance) "active" "standby")} :actor None))))

  (defn step [self]
    (+= self.now 1000)
    (self.worker-tick)
    (self.processes-tick)
    (setv self.state (run (scheduled (with_handlers [(kube-memory self.kube) (image-memory self.images)]
                                       (rollout-tick self.state T N self.now)))))
    (setv live (lfor p self.processes :if (is p.exit-code None) p))
    (.append self.log #(self.now self.lease (len live)
                        (get (self.call "GET" "/resources/Service/writer-a") "status" "ready")))))


(defn job-of [sim] (next (gfor j sim.state.jobs :if (= j.spec.name "writer-a") j)))


(deftest test-service-follows-the-production-image-and-hands-off-without-a-gap
  (setv sim (Sim))
  ;; 1. 最初の観測で base が本番の版(SHA1)へ進み、その木で process が起きて lease を取る。
  (for [_ (range 15)] (sim.step))
  (assert (= (. (job-of sim) spec base) SHA1))
  (setv first-instance sim.lease)
  (assert first-instance sim.log)
  (setv events (get (sim.call "GET" "/events") "events"))
  (assert (any (gfor e events (and (= (get e "actor") BASE-FOLLOW-ACTOR) (= (get e "changes" "spec.base") [None SHA1])))) events)
  (assert (= (get (sim.call "GET" "/resources/Service/writer-a") "status" "base" "revision") SHA1))
  ;; 2. 本番の配備の流れが image を上げる(Deployment の template の image だけが変わる・台数 0 のまま)。
  (setv (get sim.kube.deployments DEP "images") {"app-writer" IMG2})
  (setv changed-at sim.now old-stopped None new-first-ready None)
  (for [_ (range 40)]
    (sim.step)
    (setv old (next (gfor p sim.processes :if (= p.instance first-instance) p) None))
    (when (and (is old-stopped None) (or (is old None) (is-not old.exit-code None))) (setv old-stopped sim.now))
    (setv new (next (gfor p sim.processes :if (and (!= p.instance first-instance) (= p.spec.base SHA2)) p) None))
    (when (and new (is new-first-ready None) (>= sim.now (+ new.started-ms JOB-START))) (setv new-first-ready sim.now)))
  ;; base は 10 秒以内(読みの間)に SHA2 へ。出来事の記録に前後の値。
  (assert (= (. (job-of sim) spec base) SHA2))
  (setv events (get (sim.call "GET" "/events") "events"))
  (assert (any (gfor e events (and (= (get e "actor") BASE-FOLLOW-ACTOR) (= (get e "changes" "spec.base") [SHA1 SHA2])))) events)
  ;; 旧を止めたのは、新が最初に Ready を報告した後。
  (assert (and old-stopped new-first-ready (< new-first-ready old-stopped)) #(new-first-ready old-stopped sim.log))
  ;; 書き手(lease を持つ process)の居ない拍は、旧が止まってから新が lease を取るまでの 1 拍以内。process が 1 つも居ない拍は 0。
  (setv after (lfor row sim.log :if (>= (get row 0) changed-at) row))
  (assert (<= (len (lfor row after :if (is (get row 1) None) row)) 1) after)
  (assert (= (len (lfor row after :if (= (get row 2) 0) row)) 0) after)
  ;; 最後は新の process 1 つだけが動いて lease を持ち、Service は Ready。
  (setv live (lfor p sim.processes :if (is p.exit-code None) p))
  (assert (= (len live) 1) live)
  (assert (= (. (get live 0) spec base) SHA2))
  (assert (= sim.lease (. (get live 0) instance)))
  (assert (= (get (get sim.log -1) 3) "Ready") sim.log)
  ;; 計器: 仕事をしている Ready だけが ready_replicas。待機は standby。
  (setv text (metrics-text sim.state sim.now T))
  (assert (in "doeff_worker_service_ready_replicas{service=\"writer-a\"} 1.0" text) text)
  (assert (in "doeff_worker_service_standby{service=\"writer-a\"} 0.0" text) text))


(deftest test-a-standby-only-service-is-ready-but-has-no-ready-replica
  ;; lease を他が持ち続ける(書き手が居ない)Service は、Rollout と入れ替えの意味では Ready だが、書き手の計器では ready_replicas 0。
  (setv sim (Sim))
  (for [_ (range 15)] (sim.step))
  (setv sim.lease "someone-else")
  (for [_ (range 3)] (sim.step))
  (setv text (metrics-text sim.state sim.now T))
  (assert (in "doeff_worker_service_ready{service=\"writer-a\"} 1.0" text) text)
  (assert (in "doeff_worker_service_ready_replicas{service=\"writer-a\"} 0.0" text) text)
  (assert (in "doeff_worker_service_standby{service=\"writer-a\"} 1.0" text) text)
  (assert (in "doeff_worker_service_spec_replicas{service=\"writer-a\"} 1.0" text) text))


(deftest test-ready-instance-is-sent-only-once-the-new-process-reports
  (setv sim (Sim))
  (for [_ (range 3)] (sim.step))
  ;; process は起きたが最初の報告(JOB-START)の前 → Ready の世代の名はまだ無い。
  (setv early (ready-instances sim.state "zeus" sim.now T))
  (for [_ (range 10)] (sim.step))
  (setv later (ready-instances sim.state "zeus" sim.now T))
  (assert (is (get early "writer-a") None) early)
  (assert (= (get later "writer-a") sim.lease) later))


(deftest test-follow-does-not-move-the-base-without-a-readable-label
  (setv sim (Sim))
  (setv (get sim.images IMG1) {REVISION-LABEL "unknown"})
  (for [_ (range 12)] (sim.step))
  (assert (is (. (job-of sim) spec base) None))
  (assert (in "40 桁" (get (sim.call "GET" "/resources/Service/writer-a") "status" "base" "reason"))))


(deftest test-redeclaring-without-base-keeps-the-followed-base
  (setv sim (Sim))
  (for [_ (range 12)] (sim.step))
  (setv current (sim.call "GET" "/resources/Service/writer-a"))
  ;; 宣言し直し(declare.hy の --apply と同じ: base を書かない spec)でも、追随の係が進めた base は保たれる。
  (sim.call "PUT" "/resources/Service/writer-a" {"spec" (| SERVICE {"revision" "w1"}) "resourceVersion" (get current "resourceVersion")})
  (assert (= (. (job-of sim) spec base) SHA1))
  ;; 版の組(2026-09-25): overlay の無い宣言の定義の版は base と同じ commit(宣言の revision は base を観測する前だけ使う)。
  (assert (= (. (job-of sim) spec revision) SHA1))
  (assert (= (code-key (. (job-of sim) spec)) SHA1)))


(deftest test-overlay-is-the-only-way-to-run-another-definition-revision
  (setv sim (Sim) overlay (* "a" 40))
  (for [_ (range 12)] (sim.step))
  (setv current (sim.call "GET" "/resources/Service/writer-a"))
  (sim.call "PUT" "/resources/Service/writer-a" {"spec" (| SERVICE {"overlay" overlay}) "resourceVersion" (get current "resourceVersion")})
  (setv job (job-of sim))
  (assert (= #(job.spec.base job.spec.revision job.overlay) #(SHA1 overlay overlay)))
  (assert (= (code-key job.spec) (+ SHA1 "~" overlay)))
  ;; 保存の形に overlay が残り、読み戻しても同じ。
  (setv stored (get (sim.call "GET" "/resources/Service/writer-a") "spec"))
  (assert (= (get stored "overlay") overlay) stored))


(defn #^ None test-overlay-must-be-a-full-commit-on-a-followed-service []
  (import doeff_cluster.cluster_policy [job-from-json])
  (import pytest)
  (with [(pytest.raises ValueError)]
    (job-from-json (| SERVICE {"name" "x" "overlay" "abc1234"})))
  (with [(pytest.raises ValueError)]
    (job-from-json {"name" "x" "revision" "r" "entry" "m" "overlay" (* "a" 40)})))


(defn test-image-entry-requires-a-full-commit []
  (assert (= (get (image-entry {REVISION-LABEL SHA1 "org.example.runtime-revision" "d"} 5 N) "revision") SHA1))
  (assert (= (get (image-entry {REVISION-LABEL SHA1 "org.example.runtime-revision" "d"} 5 N) "runtime") "d"))
  (assert (in "error" (image-entry {REVISION-LABEL "abc1234"} 5 N)))
  (assert (= (split-image IMG1) #("zeus:5000" "app" "20260924-1111111"))))


(defn test-a-retired-process-still-counts-as-live []
  ;; 入れ替えで退いた process(行の名は <名>#retired-<世代>)が居る間、その job はまだ動いていると数える(他の worker へ置かない)。
  (setv state (ClusterState :workers {} :statuses {}))
  (import doeff_cluster.cluster_model [WorkerInfo])
  (setv state (replace state :workers {"zeus" (WorkerInfo "zeus" #() 10 1000)}
                             :statuses {"zeus" {"at" 1000 "jobs" [{"name" "a#retired-1-x" "phase" "running" "retiredFrom" "a"}]}}))
  (assert (still-live-somewhere 1000 state "a" T))
  (assert (not (still-live-somewhere 1000 state "b" T))))


(defn test-heartbeat-age-per-worker-is-exposed []
  ;; coordinator 自身の alert(DoeffWorkerHeartbeatStale)の材料: worker ごとの最後の heartbeat の古さと kind。忘れた worker は出ない。
  (import doeff_cluster.cluster_model [WorkerInfo])
  (setv state (replace (ClusterState)
                       :workers {"zeus" (WorkerInfo "zeus" #(#("kind" "k3s") #("host" "zeus")) 10 1000)
                                 "proboscis-mbp" (WorkerInfo "proboscis-mbp" #(#("kind" "mac")) 10 61000)}))
  (setv text (metrics-text state 91000 T))
  (assert (in "doeff_worker_worker_heartbeat_age_seconds{kind=\"k3s\",worker=\"zeus\"} 90.0" text) text)
  (assert (in "doeff_worker_worker_heartbeat_age_seconds{kind=\"mac\",worker=\"proboscis-mbp\"} 30.0" text) text)
  (setv forgotten (replace state :workers {"zeus" (get state.workers "zeus")}))
  (assert (not-in "proboscis-mbp" (metrics-text forgotten 91000 T))))
