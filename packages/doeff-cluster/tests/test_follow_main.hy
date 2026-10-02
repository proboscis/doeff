;; Service の入れ替え(handoff・2026-09-24): 宣言し直して版が変わると、worker が新を旧と並べて起こし、新が Ready と数えられてから
;; 旧を止める。書き手(lease を持つ process)の居ない拍は旧が止まってから新が lease を取るまでの 1 拍以内で、process が 1 つも
;; 居ない拍は無い。
;;
;; 仮想の時計の上の小さな世界で、coordinator の本物の判断(api_policy.respond・coordinator.rollout-tick)と worker の本物の判断
;; (worker_policy.plan / records-after / statuses)をつなぐ。process の中身(lease を持つ書き手)だけを模す:
;;   - 起きて JOB-START 後から毎拍 ReportReady(真)を送る。lease を持てば role active、持てなければ role standby(待機の拍)。
;;   - lease は 1 つ。持ち主が終わると、worker の ReleaseLeases で返る(期限を待たない)。空いた lease は次の拍に待機の process が取る。
;;   - TERM を受けた process は次の拍で終わる(本番の process と同じく SIGTERM で即座に終わる)。
;;   - 入口の検め(Program の job の service は起こす前に検める)は通る。
;;
;; 以前この file は、image の版を追う係(本番の Deployment の image の LABEL を読んで Service の土台の commit を進める)の筋書きだった。
;; 係は 2026-09-28 に消した(Program の job は宣言した commit でだけ解く — ADR-DOE-CLUSTER-001・計画 2.2 の E)ので、版を変えるのは
;; 宣言し直しだけ。image の版を追う欄(baseFrom・base・overlay)を持つ宣言を断る検は test_old_declarations.hy。
(require doeff-hy.macros [deftest defk <- val])
(import dataclasses [replace])
(import doeff [with_handlers])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterNaming ClusterState])
(import doeff_cluster.shared.protocol.inbox [http-request])
(import doeff_cluster.coordinator.core.api_policy [ready-instances])
(import doeff_cluster.coordinator.protocol.request_bodies [responded])
(import doeff_cluster.coordinator.core.program [rollout-tick])
(import doeff_cluster.coordinator.protocol.kube [KubeMemory kube-memory])
(import doeff_cluster.worker.protocol.heartbeat [status-rows-json])
(import doeff_cluster.coordinator.core.metrics_policy [metrics-text])
(import doeff_cluster.coordinator.core.cluster_policy [still-live-somewhere])
(import doeff_cluster.worker.intent.worker_model [CodeView CodeState ProcessView WorldView WorkerPolicy JobRecord
                        PrepareCode StartJob SignalJob ReapJob RetireJob ReleaseLeases StopStage ProbeEntry ProbeView ProbeState
                        ForgetProbes Action] doeff_cluster.shared.intent.job_model [JobSpec] doeff_cluster.shared.core.job_rules [spec-hash])
(import tests.program_rows [SAMPLE-RUN])
(import doeff_cluster.worker.core.policy [plan records-after statuses])

(setv T (ClusterTiming))
(val N (ClusterNaming))
(setv V {"python" "3.14.0"})
(val WRAP1 "w1")
(val WRAP2 "w2")
(setv JOB-START 2000)
(setv SERVICE {"revision" WRAP1 "needs" ["net"] "run" SAMPLE-RUN "replicas" 1 "readiness" {"windowSeconds" 10}
               "update" "handoff"})


(defclass Sim []
  (defn #^ None __init__ [self]
    (setv self.now 2000000
          self.kube (KubeMemory {})
          self.policy (WorkerPolicy :stop-grace-ms 10000)
          self.records {} self.processes [] self.codes {} self.pids 100
          self.probes {}           ; spec の指紋 → 入口の検めの観測(模擬では通す)
          self.lease None          ; lease を持つ process の世代の名
          self.desired #()
          self.log [])            ; 拍ごと #(時刻 active の世代 走っている process の数 Ready)
    (setv self.state (ClusterState :started-ms (- self.now 60000)))
    (self.call "POST" "/resources/Service" {"name" "writer-a" "spec" SERVICE})
    None)

  (defn #^ dict call [self #^ str method #^ str path #^ (| dict None) [body None] #^ (| str None) [actor "c-test"]]
    (setv #(state status reply) (responded self.state (http-request method path {} body :actor actor) self.now T))
    (assert (< status 300) #(method path status reply))
    (setv self.state state)
    reply)

  (defn #^ dict redeclare [self #^ str revision]
    "宣言し直す(読んだ resourceVersion を付けて版だけを変える — declare の PUT と同じ形)。"
    (setv current (self.call "GET" "/resources/Service/writer-a"))
    (self.call "PUT" "/resources/Service/writer-a"
               {"spec" (| SERVICE {"revision" revision}) "resourceVersion" (get current "resourceVersion")}))

  (defn #^ WorldView world [self]
    (WorldView (tuple (gfor #(k ready-at) (.items self.codes) :if (<= ready-at self.now)
                            (CodeView k CodeState.READY (+ "/c/" k))))
               (tuple self.processes)
               (tuple (.values self.probes))))

  (defn #^ (| int None) apply [self #^ Action action]
    (cond
      (isinstance action PrepareCode) (.setdefault self.codes action.revision (+ self.now 1000))
      (isinstance action ProbeEntry)
        (setv (get self.probes (spec-hash action.spec)) (ProbeView (spec-hash action.spec) ProbeState.PASSED))
      (isinstance action ForgetProbes)
        (setv self.probes (dfor #(k v) (.items self.probes) :if (in k action.keep) k v))
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

  (defn #^ None processes-tick [self]
    ;; 空いた lease は待機の process が取る(取りに行く係が 0.5 秒ごとに読み直す)。
    (setv live (lfor p self.processes :if (is p.exit-code None) p))
    (when (and (is self.lease None) live)
      (setv self.lease (. (max live :key (fn [p] p.started-ms)) instance)))
    (for [p live]
      (when (>= self.now (+ p.started-ms JOB-START))
        (self.call "POST" "/resources/Service/writer-a/readiness"
                   {"worker" "zeus" "pid" p.pid "revision" p.spec.revision "instance" p.instance "attempt" (str p.attempt)
                    "specHash" (spec-hash p.spec) "placement" p.spec.placement "ready" True "reason" "拍を終えた"
                    "role" (if (= self.lease p.instance) "active" "standby")} :actor None)))))


(defk worker-tick [sim]
  {:pre [(: sim Sim)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "worker の本物の判断で模擬の世界を 1 拍進め、状態を本物の綴り(status-rows-json)の heartbeat で送り、返事の job を宣言にするため。"
  (val world (sim.world))
  (val actions (plan sim.now sim.desired world sim.records sim.policy))
  (for [a actions] (sim.apply a))
  (setv sim.records (records-after sim.now sim.records actions sim.policy))
  (<- rows tuple (status-rows-json (tuple (statuses sim.now sim.desired (sim.world) sim.records sim.policy))))
  (val reply (sim.call "POST" "/heartbeat" {"name" "zeus" "provides" ["net"] "capacity" 10 "versions" V "statuses" (list rows)}
                       :actor None))
  (setv sim.desired (tuple (gfor j (get reply "jobs")
                                 (JobSpec (get j "name") (get j "entry") (tuple (get j "args")) (get j "revision")
                                          :placement (.get j "placement")
                                          :handoff (bool (.get j "handoff")) :ready-instance (.get j "readyInstance")))))
  None)


(defk step [sim]
  {:pre [(: sim Sim)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "模擬の世界を 1 秒進めるため(worker の拍 → 子 process の報告 → coordinator の Rollout の拍)。"
  (+= sim.now 1000)
  (<- (worker-tick sim))
  (sim.processes-tick)
  (<- ticked ClusterState (with_handlers [(kube-memory sim.kube)] (rollout-tick sim.state T N sim.now)))
  (setv sim.state ticked)
  (val live (lfor p sim.processes :if (is p.exit-code None) p))
  (.append sim.log #(sim.now sim.lease (len live)
                     (get (sim.call "GET" "/resources/Service/writer-a") "status" "ready")))
  None)


(deftest test-a-redeclared-service-hands-off-without-a-gap
  (val sim (Sim))
  ;; 1. 最初の版(WRAP1)の process が起きて lease を取る。
  (for [_ (range 15)] (<- (step sim)))
  (val first-instance sim.lease)
  (assert first-instance sim.log)
  ;; 2. 宣言し直して版を WRAP2 へ。出来事の記録に送り手と前後の版。
  (sim.redeclare WRAP2)
  (val events (get (sim.call "GET" "/events") "events"))
  (assert (any (gfor e events (and (= (get e "actor") "c-test") (= (.get (get e "changes") "spec.revision") [WRAP1 WRAP2]))))
          events)
  (val changed-at sim.now)
  (var old-stopped None)
  (var new-first-ready None)
  (for [_ (range 40)]
    (<- (step sim))
    (val old (next (gfor p sim.processes :if (= p.instance first-instance) p) None))
    (when (and (is old-stopped None) (or (is old None) (is-not old.exit-code None))) (:= old-stopped sim.now))
    (val new (next (gfor p sim.processes :if (and (!= p.instance first-instance) (= p.spec.revision WRAP2)) p) None))
    (when (and new (is new-first-ready None) (>= sim.now (+ new.started-ms JOB-START))) (:= new-first-ready sim.now)))
  ;; 旧を止めたのは、新が最初に Ready を報告した後。
  (assert (and old-stopped new-first-ready (< new-first-ready old-stopped)) #(new-first-ready old-stopped sim.log))
  ;; 書き手(lease を持つ process)の居ない拍は、旧が止まってから新が lease を取るまでの 1 拍以内。process が 1 つも居ない拍は 0。
  (val after (lfor row sim.log :if (>= (get row 0) changed-at) row))
  (assert (<= (len (lfor row after :if (is (get row 1) None) row)) 1) after)
  (assert (= (len (lfor row after :if (= (get row 2) 0) row)) 0) after)
  ;; 最後は新の process 1 つだけが動いて lease を持ち、Service は Ready。
  (val live (lfor p sim.processes :if (is p.exit-code None) p))
  (assert (= (len live) 1) live)
  (assert (= (. (get live 0) spec revision) WRAP2))
  (assert (= sim.lease (. (get live 0) instance)))
  (assert (= (get (get sim.log -1) 3) "Ready") sim.log)
  ;; 計器: 仕事をしている Ready だけが ready_replicas。待機は standby。
  (val text (metrics-text sim.state sim.now T))
  (assert (in "doeff_worker_service_ready_replicas{service=\"writer-a\"} 1.0" text) text)
  (assert (in "doeff_worker_service_standby{service=\"writer-a\"} 0.0" text) text))


(deftest test-a-standby-only-service-is-ready-but-has-no-ready-replica
  ;; lease を他が持ち続ける(書き手が居ない)Service は、Rollout と入れ替えの意味では Ready だが、書き手の計器では ready_replicas 0。
  (setv sim (Sim))
  (for [_ (range 15)] (<- (step sim)))
  (setv sim.lease "someone-else")
  (for [_ (range 3)] (<- (step sim)))
  (setv text (metrics-text sim.state sim.now T))
  (assert (in "doeff_worker_service_ready{service=\"writer-a\"} 1.0" text) text)
  (assert (in "doeff_worker_service_ready_replicas{service=\"writer-a\"} 0.0" text) text)
  (assert (in "doeff_worker_service_standby{service=\"writer-a\"} 1.0" text) text)
  (assert (in "doeff_worker_service_spec_replicas{service=\"writer-a\"} 1.0" text) text))


(deftest test-ready-instance-is-sent-only-once-the-new-process-reports
  (setv sim (Sim))
  (for [_ (range 3)] (<- (step sim)))
  ;; process は起きたが最初の報告(JOB-START)の前 → Ready の世代の名はまだ無い。
  (setv early (ready-instances sim.state "zeus" sim.now T))
  (for [_ (range 10)] (<- (step sim)))
  (setv later (ready-instances sim.state "zeus" sim.now T))
  (assert (is (get early "writer-a") None) early)
  (assert (= (get later "writer-a") sim.lease) later))


(deftest test-a-retired-process-still-counts-as-live
  ;; 入れ替えで退いた process(行の名は <名>#retired-<世代>)が居る間、その job はまだ動いていると数える(他の worker へ置かない)。
  (val empty (ClusterState :workers {} :statuses {}))
  (import doeff_cluster.coordinator.intent.cluster_model [WorkerInfo WorkerReport])
  (import doeff_cluster.coordinator.intent.request_bodies [StatusRow])
  (val state (replace empty :workers {"zeus" (WorkerInfo "zeus" #("net") 10 1000)}
                            :statuses {"zeus" (WorkerReport :at 1000 :endpoint None
                                                            :jobs #((StatusRow :name "a#retired-1-x" :phase "running" :retired-from "a")))}))
  (assert (still-live-somewhere 1000 state "a" T))
  (assert (not (still-live-somewhere 1000 state "b" T))))


(deftest test-heartbeat-age-per-worker-is-exposed
  ;; coordinator 自身の alert(DoeffWorkerHeartbeatStale)の材料: worker ごとの最後の heartbeat の古さ(label は worker の名だけ —
  ;; 能力の名乗りは計器の label に写さない)。忘れた worker は出ない。
  (import doeff_cluster.coordinator.intent.cluster_model [WorkerInfo])
  (val state (replace (ClusterState)
                      :workers {"zeus" (WorkerInfo "zeus" #("cluster-net" "net") 10 1000)
                                "proboscis-mbp" (WorkerInfo "proboscis-mbp" #("agent-cli") 10 61000 :exclusive #("agent-cli"))}))
  (val text (metrics-text state 91000 T))
  (assert (in "doeff_worker_worker_heartbeat_age_seconds{worker=\"zeus\"} 90.0" text) text)
  (assert (in "doeff_worker_worker_heartbeat_age_seconds{worker=\"proboscis-mbp\"} 30.0" text) text)
  (val forgotten (replace state :workers {"zeus" (get state.workers "zeus")}))
  (assert (not-in "proboscis-mbp" (metrics-text forgotten 91000 T))))
