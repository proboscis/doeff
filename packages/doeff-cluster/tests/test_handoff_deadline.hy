;; 入れ替え(update = handoff)の期限(2026-09-26 — handoff_policy): 新の世代が期限の間 Ready にならなければ、coordinator が入れ替えを
;; 諦め(新を止めて旧を残す)、Service の status に段と理由を出す。宣言が変われば諦めが解ける。
;;
;; 仮想の時計の上の小さな世界で、coordinator の本物の判断(api_policy.respond)と worker の本物の判断(worker_policy.plan /
;; records-after / statuses)と本物の返事の読み(handlers.declared-job-spec)をつなぐ。process の中身だけを模す:
;;   - 起きて JOB-START 後から毎拍 ReportReady を送る。壊れた版(broken)の process は ReportReady(偽)と理由を送る。
;;   - TERM を受けた process は次の拍で終わる(本番の書き手と同じく SIGTERM で即座に終わる)。
;; 筋書き: (a) 期限の内に Ready → 旧が止まる(今と同じ)・(b) 期限を越えて NotReady → 新が止まり起こし直されず、旧は動き続け、
;; status に段と理由(ReportReady の reason を含む)— coordinator を作り直しても諦めは保たれる・(c) 宣言を変えると諦めが解けて
;; 新しい spec の入れ替えが始まる・(d) recreate の Service は今と同じ。
(require doeff-hy.macros [deftest val var])
(import dataclasses [replace])
(import pytest)
(import doeff_cluster.cluster_model [ClusterTiming ClusterState Request])
(import doeff_cluster.api_policy [respond resume-after-downtime])
(import doeff_cluster.durable_kv [durable-kv state-from-kv])
(import doeff_cluster.cluster_policy [job-from-json])
(import doeff_cluster.handlers [status-row declared-job-spec])
(import doeff_cluster.readiness_model [handoff-timeout-ms HANDOFF-TIMEOUT-SECONDS])
(import doeff_cluster.service_model [service])
(import doeff_cluster.worker_model [CodeView CodeState ProcessView WorldView WorkerPolicy PrepareCode StartJob SignalJob ReapJob
                                    RetireJob spec-hash])
(import doeff_cluster.worker_policy [plan records-after statuses])

(val T (ClusterTiming))
(val V {"python" "3.14.0"})
(val JOB-START 2000)
(val TIMEOUT-SECONDS 30)
(val WARMING "温まっていない env のキー env-k2・準備の失敗 prepare-timeout")
(val HANDOFF {"revision" "r1" "requires" {} "entry" "m" "args" [] "replicas" 1
              "readiness" {"windowSeconds" 10 "handoffTimeoutSeconds" TIMEOUT-SECONDS} "update" "handoff"})
(val RECREATE {"revision" "r1" "requires" {} "entry" "m" "args" [] "replicas" 1 "readiness" {"windowSeconds" 10}})


(defclass Sim []
  "coordinator 1 つと worker 1 台(zeus)と、その上の子 process の模擬の世界(状態を持つ object — 拍ごとに進める)。"
  (defn __init__ [self #^ dict declaration]  ; defk にできない: 模擬の世界の object の組み立て(class の口)
    "declaration = Service writer-a の宣言の spec。"
    (setv self.now 3000000
          self.policy (WorkerPolicy :stop-grace-ms 10000)
          self.records {} self.processes [] self.codes {} self.pids 100
          self.desired #()
          self.broken #{}         ; ReportReady(偽)を送る版
          self.starts []          ; #(時刻 版 世代の名) — 起こした process の記録
          self.replies []         ; heartbeat の返事の writer-a の job の行(拍ごと)
          self.log [])            ; 拍ごと #(時刻 動いている process の数)
    (setv self.state (ClusterState :started-ms (- self.now 60000)))
    (self.call "POST" "/resources/Service" {"name" "writer-a" "spec" declaration})
    None)

  (defn call [self #^ str method #^ str path [body None] #^ (| str None) [actor "c-test"]]  ; defk にできない: 模擬の世界の method(coordinator の口へ要求を送る)
    "coordinator の本物の返事(api_policy.respond)へ要求を 1 件送り、状態を進めて本文を返す。"
    (setv #(state status reply) (respond self.state (Request method path {} body :actor actor) self.now T))
    (assert (< status 300) #(method path status reply))
    (setv self.state state)
    reply)

  (defn world [self]  ; defk にできない: 模擬の世界の method(worker の観測を作る)
    "worker の観測(準備の済んだ木と、子 process)。"
    (WorldView (tuple (gfor #(k ready-at) (.items self.codes) :if (<= ready-at self.now)
                            (CodeView k CodeState.READY (+ "/c/" k))))
               (tuple self.processes)))

  (defn apply [self action]  ; defk にできない: 模擬の世界の method(worker の action を子 process の世界へ当てる)
    "worker の action を模擬の世界へ当てる(木の準備は 1 秒・TERM で即座に終わる)。"
    (cond
      (isinstance action PrepareCode) (.setdefault self.codes action.revision (+ self.now 1000))
      (isinstance action StartJob)
        (do (+= self.pids 1)
            (setv instance (.format "{}-sim{}" action.attempt self.now))
            (.append self.starts #(self.now action.spec.revision instance))
            (.append self.processes (ProcessView action.spec.name action.spec action.attempt self.pids self.now
                                                 :instance instance)))
      (isinstance action RetireJob)
        (setv self.processes (lfor p self.processes (if (= p.pid action.pid) (replace p :name action.new-name :retired-from action.name) p)))
      (isinstance action SignalJob)
        (setv self.processes (lfor p self.processes (if (= p.pid action.pid) (replace p :exit-code -15) p)))
      (isinstance action ReapJob) (setv self.processes (lfor p self.processes :if (!= p.pid action.pid) p))))

  (defn worker-tick [self]  ; defk にできない: 模擬の世界の method(worker の 1 拍)
    "worker の本物の判断で 1 拍進め、状態を heartbeat で送り、返事を本物の読み(declared-job-spec)で宣言にする。"
    (setv world (self.world) actions (plan self.now self.desired world self.records self.policy))
    (for [a actions] (self.apply a))
    (setv self.records (records-after self.now self.records actions self.policy))
    (setv rows (lfor s (statuses self.now self.desired (self.world) self.records self.policy) (status-row s)))
    (setv reply (self.call "POST" "/heartbeat" {"name" "zeus" "labels" {} "capacity" 10 "versions" V "statuses" rows} :actor None))
    (.append self.replies (next (gfor j (get reply "jobs") :if (= (get j "name") "writer-a") j) None))
    (setv self.desired (tuple (gfor j (get reply "jobs") (declared-job-spec j)))))

  (defn processes-tick [self]  ; defk にできない: 模擬の世界の method(子 process の拍)
    "動いている子 process が JOB-START の後から毎拍 ReportReady を送る(壊れた版は偽と理由)。"
    (for [p self.processes]
      (when (and (is p.exit-code None) (>= self.now (+ p.started-ms JOB-START)))
        (setv healthy (not-in p.spec.revision self.broken))
        (self.call "POST" "/resources/Service/writer-a/readiness"
                   {"worker" "zeus" "pid" p.pid "revision" p.spec.revision "instance" p.instance "attempt" (str p.attempt)
                    "specHash" (spec-hash p.spec) "placement" p.spec.placement "ready" healthy
                    "reason" (if healthy "拍を終えた" WARMING) "role" "active"} :actor None))))

  (defn step [self]  ; defk にできない: 模擬の世界の method(1 秒進める)
    "1 秒進める(worker の拍 → 子 process の報告)。"
    (+= self.now 1000)
    (self.worker-tick)
    (self.processes-tick)
    (.append self.log #(self.now (len (lfor p self.processes :if (is p.exit-code None) p)))))

  (defn mark-broken [self #^ str revision]  ; defk にできない: 模擬の世界の method
    "その版の process が ReportReady(偽)を送るようにする(準備できない新の世代を模す)。"
    (setv self.broken (| self.broken #{revision})))

  (defn steps [self #^ int n]  ; defk にできない: 模擬の世界の method
    "n 拍進める。"
    (for [_ (range n)] (self.step)))

  (defn redeclare [self #^ dict declaration]  ; defk にできない: 模擬の世界の method(宣言の書き換え — declare --apply と同じ口)
    "Service の宣言を書き換える(読んだ版を付けた PUT)。"
    (setv current (self.call "GET" "/resources/Service/writer-a"))
    (self.call "PUT" "/resources/Service/writer-a" {"spec" declaration "resourceVersion" (get current "resourceVersion")}))

  (defn status [self]  ; defk にできない: 模擬の世界の method(資源の口の読み)
    "Service writer-a の資源の status。"
    (get (self.call "GET" "/resources/Service/writer-a") "status"))

  (defn live [self #^ str revision]  ; defk にできない: 模擬の世界の method
    "その版で動いている子 process。"
    (lfor p self.processes :if (and (is p.exit-code None) (= p.spec.revision revision)) p))

  (defn restart-coordinator [self]  ; defk にできない: 模擬の世界の method(作り直し)
    "coordinator の作り直し: 耐久の置き場の形から読み直す(worker の報告・readiness は失う)。止まっていた時間は無い。"
    (setv #(state _) (resume-after-downtime (state-from-kv (durable-kv self.state) self.now) self.now))
    (setv self.state state)))


(defn handoff-changes [sim]  ; defk にできない: 検の読みの道具(出来事の記録の欄を拾う)
  "出来事の記録のうち、Service writer-a の status.handoff の移り変わり(前後の段)。記録は長い値を切った文字列にする
   (resource_policy.short-value)ので、その時は段の名を文字列から拾う。"
  (defn phase-of [v]  ; defk にできない: 内包表記の中の読みの道具
    (cond (is v None) None
          (isinstance v dict) (get v "phase")
          True (next (gfor p ["WaitingReady" "Abandoned"] :if (in p v) p))))
  (lfor e sim.state.audit
        :if (and (= (get e "kind") "Service") (in "status.handoff" (get e "changes")))
        (tuple (gfor v (get e "changes" "status.handoff") (phase-of v)))))


(defn gapless [sim]  ; defk にできない: 検の読みの道具
  "書き手の空白が無いか: 最初の process が起きた後のどの拍も、1 つ以上の process が動いている。"
  (import itertools)
  (all (gfor row (itertools.dropwhile (fn [row] (= (get row 1) 0)) sim.log) (> (get row 1) 0))))


(defn started [sim revision]  ; defk にできない: 検の読みの道具
  "その版で起こした process の数。"
  (len (lfor s sim.starts :if (= (get s 1) revision) s)))


(defn abandon-r2 []  ; defk にできない: 検の筋書きの組み立て(模擬の世界を (b) の諦めまで進める)
  "r1 を起こし、Ready にならない r2 へ宣言を変え、期限を越えて諦めるまで進めた世界(筋書き (b) と (c) が使う)。"
  (setv sim (Sim HANDOFF))
  (sim.steps 12)
  (assert (= (get (sim.status) "ready") "Ready") sim.log)
  (sim.mark-broken "r2")
  (sim.redeclare (| HANDOFF {"revision" "r2"}))
  (sim.steps (+ TIMEOUT-SECONDS 15))
  sim)


;; --- 宣言の形 -------------------------------------------------------------------------------

(defn tally-program [interval]  ; defk にできない: service の宣言の参照先(宣言の組み立てだけに使う — 走らせない)
  "宣言の検めの検だけに使う本体。"
  interval)


(deftest test-the-handoff-deadline-is-declared-in-readiness-and-checked-at-both-entrances
  ;; 期限の宣言 = readiness の handoffTimeoutSeconds(正の数・既定 300 — Rollout の readyTimeoutSeconds と同じ)。handoff の Service だけが
  ;; 持つ。service の宣言と coordinator の宣言の読みが同じ規則で断る。
  (assert (= (handoff-timeout-ms None) (* 1000 HANDOFF-TIMEOUT-SECONDS)))
  (assert (= HANDOFF-TIMEOUT-SECONDS 300))
  (assert (= (handoff-timeout-ms {"windowSeconds" 10}) 300000))
  (assert (= (handoff-timeout-ms {"windowSeconds" 10 "handoffTimeoutSeconds" 45}) 45000))
  (val declared (service "w" tally-program :env "m:e" :config {"interval" 1.0} :update "handoff"
                         :readiness {"windowSeconds" 10 "handoffTimeoutSeconds" 45}))
  (assert (= declared.readiness {"windowSeconds" 10 "handoffTimeoutSeconds" 45}))
  (assert (= (. (job-from-json (| {"name" "w"} HANDOFF)) readiness) {"windowSeconds" 10 "handoffTimeoutSeconds" TIMEOUT-SECONDS}))
  (for [bad [{"windowSeconds" 10 "handoffTimeoutSeconds" 0} {"windowSeconds" 10 "handoffTimeoutSeconds" -5}
             {"windowSeconds" 10 "handoffTimeoutSeconds" True} {"windowSeconds" 10 "handoffTimeoutSeconds" "300"}
             {"windowSeconds" 10 "handoffTimeoutSecond" 300}]]
    (with [(pytest.raises ValueError)]
      (service "w" tally-program :env "m:e" :config {"interval" 1.0} :update "handoff" :readiness bad))
    (with [(pytest.raises ValueError)]
      (job-from-json (| {"name" "w"} HANDOFF {"readiness" bad}))))
  ;; recreate の Service は期限を持たない(効かない欄を黙って受けない)。
  (with [(pytest.raises ValueError)]
    (service "w" tally-program :env "m:e" :config {"interval" 1.0} :readiness {"windowSeconds" 10 "handoffTimeoutSeconds" 45}))
  (with [(pytest.raises ValueError)]
    (job-from-json (| {"name" "w"} RECREATE {"readiness" {"windowSeconds" 10 "handoffTimeoutSeconds" 45}}))))


;; --- 筋書き ---------------------------------------------------------------------------------

(deftest test-a-new-generation-ready-within-the-deadline-stops-the-old-as-before
  ;; (a) 期限の内に Ready → 旧が止まる(今と同じ)。見張りは Ready を待つ間だけ status.handoff に出て、Ready で消える。
  (val sim (Sim HANDOFF))
  (sim.steps 12)
  (val first (get (sim.live "r1") 0))
  (sim.redeclare (| HANDOFF {"revision" "r2"}))
  (sim.steps 20)
  (assert (= (sim.live "r1") []) sim.log)
  (assert (= (len (sim.live "r2")) 1) sim.processes)
  (assert (not-in first.pid (lfor p sim.processes p.pid)))
  (assert (= (get (sim.status) "ready") "Ready"))
  (assert (not-in "handoff" (sim.status)) (sim.status))
  (assert (= sim.state.handoffs {}))
  (assert (= (handoff-changes sim) [#(None "WaitingReady") #("WaitingReady" None)]) (handoff-changes sim))
  (assert (not (any (gfor j sim.replies (and j (.get j "handoffAbandoned"))))) "諦めの印は一度も載らない")
  ;; 書き手の空白は無い(どの拍も 1 つ以上の process が動いている)。
  (assert (gapless sim) sim.log))


(deftest test-a-new-generation-not-ready-past-the-deadline-is-stopped-and-the-old-keeps-running
  ;; (b) 期限を越えて NotReady → 新が止まり起こし直されず、旧は動き続け、status に段と理由(ReportReady の reason を含む)。
  (val sim (abandon-r2))
  (val handoff (get (sim.status) "handoff"))
  (assert (= (get handoff "phase") "Abandoned") handoff)
  (assert (= (get handoff "timeoutSeconds") TIMEOUT-SECONDS) handoff)
  (assert (in (.format "{} 秒の間 Ready にならなかった" TIMEOUT-SECONDS) (get handoff "reason")) handoff)
  (assert (in WARMING (get handoff "reason")) handoff)
  (assert (= (get handoff "lastNotReadyReport") WARMING) handoff)
  ;; 期限の起点は新の process が動き出した時(宣言を変えた時ではない — 木の準備の 1 秒を数えない)。
  (val first-r2 (next (gfor s sim.starts :if (= (get s 1) "r2") (get s 0))))
  (assert (>= (get handoff "sinceMs") first-r2) #(handoff first-r2))
  (assert (>= (- (get handoff "abandonedMs") (get handoff "sinceMs")) (* 1000 TIMEOUT-SECONDS)) handoff)
  ;; worker は新を止め、旧(退いた r1)は動き続ける。諦めは heartbeat の返事の job の印で worker へ届く。
  (assert (= (sim.live "r2") []) sim.processes)
  (val old (sim.live "r1"))
  (assert (and (= (len old) 1) (= (. (get old 0) retired-from) "writer-a")) old)
  (assert (.get (get sim.replies -1) "handoffAbandoned") (get sim.replies -1))
  (val row (next (gfor r (get sim.state.statuses "zeus" "jobs") :if (= (get r "name") "writer-a") r)))
  (assert (= (get row "phase") "handoff-abandoned") row)
  ;; 起こし直さない: 時間が経っても、coordinator を作り直しても(諦めは保存される)、r2 は起きない。
  (val starts-r2 (started sim "r2"))
  (sim.steps 20)
  (sim.restart-coordinator)
  (sim.steps 40)
  (assert (= (started sim "r2") starts-r2) sim.starts)
  (assert (= (sim.live "r2") []))
  (assert (= (len (sim.live "r1")) 1))
  (assert (= (get (get (sim.status) "handoff") "phase") "Abandoned"))
  (assert (gapless sim) sim.log))


(deftest test-changing-the-declaration-lifts-the-abandonment-and-hands-off-again
  ;; (c) 宣言を変えると諦めが解けて、新しい spec の入れ替えが始まる(Ready になった後に退いた旧が止まる)。
  (val sim (abandon-r2))
  (assert (= (get (get (sim.status) "handoff") "phase") "Abandoned"))
  (sim.redeclare (| HANDOFF {"revision" "r3"}))
  ;; 書き換えの後の最初の返事から諦めの印は消える。
  (sim.step)
  (assert (not (.get (get sim.replies -1) "handoffAbandoned" False)) (get sim.replies -1))
  (sim.steps 20)
  (assert (= (sim.live "r1") []) sim.processes)
  (assert (= (len (sim.live "r3")) 1) sim.processes)
  (assert (= (get (sim.status) "ready") "Ready"))
  (assert (not-in "handoff" (sim.status)))
  (assert (= (lfor c (handoff-changes sim) (get c 1)) ["WaitingReady" "Abandoned" None "WaitingReady" None])
          (handoff-changes sim))
  (assert (gapless sim) sim.log))


(deftest test-a-recreate-service-is-unchanged
  ;; (d) recreate の Service は期限を持たない: 旧を止めてから新を起こし、新が NotReady のままでも止めない・status に handoff を出さない。
  (val sim (Sim RECREATE))
  (sim.steps 12)
  (sim.mark-broken "r2")
  (sim.redeclare (| RECREATE {"revision" "r2"}))
  (sim.steps (+ TIMEOUT-SECONDS 60))
  (assert (= (sim.live "r1") []))
  (assert (= (len (sim.live "r2")) 1) sim.processes)
  (assert (= (started sim "r2") 1) sim.starts)
  (assert (= (get (sim.status) "ready") "NotReady"))
  (assert (not-in "handoff" (sim.status)))
  (assert (= sim.state.handoffs {}))
  (assert (= (handoff-changes sim) []))
  (assert (not (any (gfor j sim.replies (and j (in "handoffAbandoned" j)))))))
