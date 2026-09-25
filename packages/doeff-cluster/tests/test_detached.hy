;; 切り離した task(SubmitDetached / AwaitDetached / CancelDetached / ReleaseDetached — detached_model.hy)の契約の検。
;;
;; 同じ筋書き(doeff の Program)を 3 つの組で回す:
;;   fake        … detached-local(同じ VM の scheduler の task)・仮想の時計
;;   coordinator … 本物の coordinator の判断(api_policy.respond / tick)を httpx.MockTransport の後ろに置き、本物の DetachedClient と
;;                 本物の CoordinatorLink(heartbeat・task の file・結果の報告)で話す。担い手は同じ VM で Program を走らせる・仮想の時計
;;   served      … 本物の coordinator の process(hy -m doeff_cluster.coordinator・HTTP・追記の log。conftest の served_coordinator が
;;                 検の間で 1 つを共有する)・同じ担い手・実時間
;; 筋書き: 送って待つ / 同じ key の送り直し / 呼び手が消えても続き再接続 / 結果の後の担い手の死 / 走っている間の担い手の死 /
;;         lease は担い手が延ばす / 取り消し / Program の例外 / 知らない key と解放 / timeout / 版の不一致 / key の衝突。
;; その後に coordinator の判断(純粋な関数)と worker の途絶の検。
(require doeff-hy.macros [deftest defk defhandler <-])
(import json)
(import time)
(import urllib.parse [urlsplit parse-qsl])
(import pathlib [Path])
(import httpx)
(import doeff [with_handlers Program])
(import doeff_core_effects.effects [Ask])
(import doeff_core_effects.handlers [reader await-handler])
(import doeff_core_effects.scheduler [Spawn Cancel TaskCancelledError])
(import doeff_time [Delay SimClock sim-time-handler async-time-handler])
(import tests.clock_fixtures [clock-ms])
(import doeff_cluster.cluster_model [ClusterState ClusterTiming Request])
(import doeff_cluster.api_policy [respond tick])
(import doeff_cluster.handlers [CoordinatorLink])
(import doeff_cluster.job_entry [RunContext env-handlers])
(import doeff_cluster.worker_model [DesiredJobs JobStatus JobPhase])
(import doeff_cluster.remote_model [TaskSucceeded decode-program encode-outcome failed-from current-versions])
(import doeff_cluster.detached_model [SubmitDetached AwaitDetached CancelDetached ReleaseDetached SimulateRunnerLoss
                                      DetachedSubmitted DetachedSucceeded DetachedFailed DetachedLost DetachedCancelled
                                      DetachedVersionMismatch DetachedUnknown DetachedPending DetachedRefused])
(import doeff_cluster.detached [detached-local DetachedLocalStore detached-cluster DetachedClient])

(setv ENV "tests.fixtures.envs:plain_env")                        ; base = 100 の reader
(setv OTHER-VERSIONS {"python" "0.0.0" "doeff" "0"})


;; --- 送る Program -------------------------------------------------------------------------------

(defk slow-add [seconds n]
  {:pre [(: seconds float) (: n int)] :post [(: % int)]}
  (<- (Delay seconds))
  (<- base int (Ask "base"))
  (+ base n))


(defk boom []
  {:pre [] :post [(: % int)]}
  (<- base int (Ask "base"))
  (raise (ValueError (.format "業務の失敗 base={}" base))))


;; --- 担い手(coordinator の組・served の組が共有する)--------------------------------------------------------
;; 本物の CoordinatorLink で heartbeat を送り、割り当てられた task の file(blob)を受け、同じ VM の scheduler の task として
;; env の handler の組の下で走らせ、結果の file を書いて報告する(子 process の入口 job_entry task と同じ手順 — 子 process そのものは
;; test_remote.hy が通す)。

(defclass RigWorker []
  (defn __init__ [self #^ str url #^ Path task-dir #^ dict versions [transport None]]
    (setv self.link (CoordinatorLink url "w1" {} 10 20000 :task-dir (str task-dir) :versions versions :transport transport)
          self.handles {} self.done #{} self.dead False self.loop None)))


(defn #^ dict task-args [#^ tuple args]
  "task の job の引数(\"task\" \"--blob\" P \"--result\" P \"--env\" E \"--versions\" V)→ {欄: 値}。"
  (dfor i (range 1 (len args) 2) (cut (get args i) 2 None) (get args (+ i 1))))


(defk run-rig-task [worker name args revision]
  {:pre [(: worker RigWorker) (: name str) (: args dict) (: revision str)] :post [(: % bool)]}
  (setv program (decode-program (.read-text (Path (get args "blob")) :encoding "ascii"))
        handlers (env-handlers (get args "env") {} (RunContext "" "w1" revision name)))
  (try
    (<- value (with-handlers handlers program))
    (setv outcome (TaskSucceeded value))
    (except [error TaskCancelledError]
      (raise))
    (except [error Exception]
      (setv outcome (failed-from error))))
  (.write-text (Path (get args "result")) (encode-outcome outcome) :encoding "ascii")
  (.add worker.done name)
  True)


(defk worker-tick [worker]
  {:pre [(: worker RigWorker)] :post [(: % bool)]}
  (setv desired (.poll worker.link))
  (when (isinstance desired DesiredJobs)
    (setv specs (dfor s desired.jobs :if s.once s.name s))
    (for [#(name spec) (.items specs)]
      (when (not-in name worker.handles)
        (<- handle (Spawn (run-rig-task worker name (task-args spec.args) spec.revision)))
        (setv (get worker.handles name) handle)))
    ;; 宣言から外れた task(取り消し・lost・結果を受け取り終えた)は止める。
    (for [name (list worker.handles)]
      (when (not-in name specs)
        (<- (Cancel (.pop worker.handles name)))
        (.discard worker.done name))))
  (setv worker.link.statuses
        (.report worker.link (tuple (gfor name worker.handles
                                          (JobStatus name (if (in name worker.done) JobPhase.FINISHED JobPhase.RUNNING)
                                                     "r" "r" None 1)))))
  True)


(defk worker-loop [worker seconds]
  {:pre [(: worker RigWorker) (: seconds float)] :post [(: % bool)]}
  (while (not worker.dead)
    (<- (Delay seconds))
    (<- (worker-tick worker)))
  True)


(defhandler rig-runner-loss [#^ RigWorker worker]
  ;; 担い手の死: heartbeat が止まり、走っていた task も消える(coordinator は lease の後に lost とする)。
  (SimulateRunnerLoss []
    (setv worker.dead True
          running (lfor n worker.handles :if (not-in n worker.done) n))
    (for [handle (.values worker.handles)] (<- (Cancel handle)))
    (when worker.loop (<- (Cancel worker.loop)))
    (resume (len running))))


;; --- coordinator(本物の判断を memory の上で)------------------------------------------------------------

(defclass MemoryCoordinator []
  "本物の api_policy.respond / tick を httpx の MockTransport の後ろに置く。時刻は仮想の時計。要求の前に tick する(本物の調停
   ループは要求の無い拍に tick する)。"
  (defn __init__ [self #^ SimClock clock]
    (setv self.clock clock self.state (ClusterState) self.timing (ClusterTiming)))

  (defn handle [self request]
    (setv now (clock-ms self.clock)
          split (urlsplit (str request.url))
          body (if request.content (json.loads request.content) None))
    (setv self.state (tick self.state now self.timing))
    (setv #(state status reply) (respond self.state (Request request.method split.path (dict (parse-qsl split.query)) body
                                                             :actor (.get request.headers "x-actor"))
                                         now self.timing))
    (setv self.state state)
    (httpx.Response status :json reply)))


;; --- 組 -----------------------------------------------------------------------------------------------------

(defclass Rig []
  "筋書きを回す組。handlers = 筋書きに被せる handler の組(外側が先)・worker = 担い手(fake は None)・slow / lease / poll = 時間の尺度。"
  (defn __init__ [self #^ str kind #^ list handlers worker #^ float slow #^ float lease #^ float poll [runs None] [close None]]
    (setv self.kind kind self.handlers handlers self.worker worker self.slow slow self.lease lease self.poll poll
          self.runs runs self.close (or close (fn [] None)))))


(defn #^ Rig fake-rig [runner-versions]
  (setv store (DetachedLocalStore :runner-versions runner-versions))
  (Rig "fake" [(sim-time-handler :clock (SimClock)) (reader {"worker" "child" "base" 100}) (detached-local store :poll-seconds 0.5)]
       None 3.0 5.0 0.5 :runs (fn [key] store.runs)))


(defn #^ Rig coordinator-rig [#^ Path tmp-path runner-versions]
  (setv clock (SimClock)
        coordinator (MemoryCoordinator clock)
        transport (httpx.MockTransport coordinator.handle)
        worker (RigWorker "http://coordinator" (/ tmp-path "tasks") (or runner-versions (current-versions)) :transport transport)
        client (DetachedClient "http://coordinator" "r" :transport transport))
  (Rig "coordinator" [(sim-time-handler :clock clock) (rig-runner-loss worker) (detached-cluster client :poll-seconds 0.5)]
       worker 3.0 5.0 0.5
       :runs (fn [key] (len (lfor t (.values coordinator.state.tasks) :if (= t.key key) t)))))


(defn #^ Rig served-rig [#^ str url #^ Path tmp-path [runner-versions None]]
  (setv worker (RigWorker url (/ tmp-path "tasks") (or runner-versions (current-versions)))
        client (DetachedClient url "r"))
  (defn runs [key]
    (len (lfor t (get (.json (httpx.get (+ url "/state"))) "tasks") :if (= (.get t "key") key) t)))
  ;; 実時間: lease は heartbeat の間隔(0.2 秒)の十倍以上に取る(込んだ機体で heartbeat が遅れても消失と取り違えない)。
  (Rig "served" [(await-handler) (async-time-handler) (rig-runner-loss worker) (detached-cluster client :poll-seconds 0.2)]
       worker 1.0 2.5 0.2 :runs runs))


(defn #^ Rig make-rig [#^ str kind #^ Path tmp-path [runner-versions None]]
  (if (= kind "fake") (fake-rig runner-versions) (coordinator-rig tmp-path runner-versions)))


(defk with-worker [rig scenario]
  {:pre [(: rig Rig) (: scenario Program)] :post [(: % bool)]}
  ;; 担い手を 1 拍名乗らせてから(送った時に置ける worker が在るように)heartbeat のループを走らせ、筋書きの後に止める。
  (setv worker rig.worker)
  (when worker
    (<- (worker-tick worker))
    (<- loop (Spawn (worker-loop worker rig.poll) :daemon True))
    (setv worker.loop loop))
  (<- scenario)
  (when (and worker (not worker.dead))
    (setv worker.dead True)
    (<- (Cancel worker.loop)))
  True)


(defk run-on [rig scenario]
  {:pre [(: rig Rig) (: scenario Program)] :post [(: % bool)]}
  (try
    (<- (with-handlers rig.handlers (with-worker rig scenario)))
    (finally
      (rig.close)))
  True)




(defmacro on-every-rig [name scenario #* rig-args]
  "筋書き 1 つを 3 つの組(fake・coordinator・served)の deftest にする。"
  (setv test-name (fn [kind] (hy.models.Symbol (+ (str name) "-on-" kind))))
  `(do ~@(lfor kind ["fake" "coordinator"]
               `(deftest ~(test-name kind) [tmp-path]
                  (setv rig (make-rig ~kind tmp-path ~@rig-args))
                  (<- ok (run-on rig (~scenario rig)))
                  (assert ok)))
       (deftest ~(test-name "served") [tmp-path served-coordinator]
         (setv rig (served-rig served-coordinator tmp-path ~@rig-args))
         (<- ok (run-on rig (~scenario rig)))
         (assert ok))))


;; --- 筋書き ---------------------------------------------------------------------------------------------------

(defk submit-and-await [rig]
  {:pre [(: rig Rig)] :post [(: % bool)]}
  (<- submitted DetachedSubmitted (SubmitDetached (slow-add rig.slow 1) :env ENV :key "k-basic" :lease-seconds rig.lease))
  (assert (= submitted (DetachedSubmitted "k-basic" True)))
  (<- outcome (AwaitDetached "k-basic"))
  (assert (= outcome (DetachedSucceeded 101)) outcome)
  True)

(on-every-rig test-submit-and-await-returns-the-value submit-and-await)


(defk resubmit-same-key [rig]
  {:pre [(: rig Rig)] :post [(: % bool)]}
  (<- first DetachedSubmitted (SubmitDetached (slow-add rig.slow 2) :env ENV :key "k-idem" :lease-seconds rig.lease))
  (<- second DetachedSubmitted (SubmitDetached (slow-add rig.slow 2) :env ENV :key "k-idem" :lease-seconds rig.lease))
  (assert (= #(first.created second.created) #(True False)))
  (<- outcome (AwaitDetached "k-idem"))
  (assert (= outcome (DetachedSucceeded 102)) outcome)
  ;; 終わった後の送り直しも同じ行(走らせ直さない)。
  (<- third DetachedSubmitted (SubmitDetached (slow-add rig.slow 2) :env ENV :key "k-idem" :lease-seconds rig.lease))
  (assert (not third.created))
  (assert (= (rig.runs "k-idem") 1))
  True)

(on-every-rig test-resubmitting-the-same-key-runs-once resubmit-same-key)


(defk submit-then-wait [key seconds lease]
  {:pre [(: key str) (: seconds float) (: lease float)] :post [(: % DetachedSucceeded)]}
  (<- (SubmitDetached (slow-add seconds 3) :env ENV :key key :lease-seconds lease))
  (<- outcome (AwaitDetached key))
  outcome)

(defk task-longer-than-the-lease [rig]
  {:pre [(: rig Rig)] :post [(: % bool)]}
  ;; lease は担い手が延ばす: lease の 2 倍かかる task も、担い手が生きている限り消えない(呼び手の問い合わせは無くてよい)。
  (<- (SubmitDetached (slow-add (* rig.lease 2) 11) :env ENV :key "k-long" :lease-seconds rig.lease))
  (<- outcome (AwaitDetached "k-long"))
  (assert (= outcome (DetachedSucceeded 111)) outcome)
  True)

(on-every-rig test-the-runner-extends-the-lease-of-a-long-task task-longer-than-the-lease)


(defk caller-vanishes-and-reconnects [rig]
  {:pre [(: rig Rig)] :post [(: % bool)]}
  ;; 呼び手(送って待つ task)を取り消す = 呼び手が消えた。task は続き、別の呼び手が同じ key で結果を受け取る。
  (<- caller (Spawn (submit-then-wait "k-vanish" rig.slow rig.lease)))
  (<- (Delay (* rig.slow 0.3)))
  (<- (Cancel caller))
  (<- early (AwaitDetached "k-vanish" :timeout-seconds 0.0))
  (assert (= early (DetachedPending "k-vanish" "assigned")) early)
  (<- (Delay rig.slow))
  (<- outcome (AwaitDetached "k-vanish"))
  (assert (= outcome (DetachedSucceeded 103)) outcome)
  True)

(on-every-rig test-task-survives-the-caller-and-a-new-caller-reconnects caller-vanishes-and-reconnects)


(defk result-outlives-the-runner [rig]
  {:pre [(: rig Rig)] :post [(: % bool)]}
  (<- (SubmitDetached (slow-add 0.0 4) :env ENV :key "k-kept" :lease-seconds rig.lease))
  (<- outcome (AwaitDetached "k-kept"))
  (assert (= outcome (DetachedSucceeded 104)) outcome)
  ;; 結果の後に担い手が死んでも、結果は保持する(lease が切れる時間を過ぎても)。
  (<- lost int (SimulateRunnerLoss))
  (assert (= lost 0))
  (<- (Delay (* rig.lease 1.5)))
  (<- again (AwaitDetached "k-kept"))
  (assert (= again (DetachedSucceeded 104)) again)
  True)

(on-every-rig test-result-is-kept-after-the-runner-dies result-outlives-the-runner)


(defk runner-dies-mid-run [rig]
  {:pre [(: rig Rig)] :post [(: % bool)]}
  (<- (SubmitDetached (slow-add (* rig.slow 10) 5) :env ENV :key "k-lost" :lease-seconds rig.lease))
  (<- (Delay (* rig.slow 0.3)))
  (<- lost int (SimulateRunnerLoss))
  (assert (= lost 1))
  (<- outcome (AwaitDetached "k-lost"))
  (assert (isinstance outcome DetachedLost) outcome)
  ;; 走らせ直さない(同じ key の送り直しは消えた行を返すだけ)。
  (<- again DetachedSubmitted (SubmitDetached (slow-add 0.0 5) :env ENV :key "k-lost" :lease-seconds rig.lease))
  (assert (not again.created))
  (<- still (AwaitDetached "k-lost"))
  (assert (isinstance still DetachedLost) still)
  True)

(on-every-rig test-runner-death-loses-the-task-without-rerunning runner-dies-mid-run)


(defk cancel-open-and-finished [rig]
  {:pre [(: rig Rig)] :post [(: % bool)]}
  (<- (SubmitDetached (slow-add (* rig.slow 10) 6) :env ENV :key "k-cancel" :lease-seconds rig.lease))
  (<- (Delay (* rig.slow 0.3)))
  (<- cancelled bool (CancelDetached "k-cancel"))
  (assert cancelled)
  (<- outcome (AwaitDetached "k-cancel"))
  (assert (= outcome (DetachedCancelled)) outcome)
  (<- twice bool (CancelDetached "k-cancel"))
  (assert (not twice))
  ;; 終わった後の取り消しは何もしない(結果は保持)。
  (<- (SubmitDetached (slow-add 0.0 7) :env ENV :key "k-done" :lease-seconds rig.lease))
  (<- done (AwaitDetached "k-done"))
  (assert (= done (DetachedSucceeded 107)) done)
  (<- late bool (CancelDetached "k-done"))
  (assert (not late))
  (<- kept (AwaitDetached "k-done"))
  (assert (= kept (DetachedSucceeded 107)) kept)
  (<- unknown bool (CancelDetached "k-never"))
  (assert (not unknown))
  True)

(on-every-rig test-cancel-stops-an-open-task-and-keeps-a-finished-result cancel-open-and-finished)


(defk program-raises [rig]
  {:pre [(: rig Rig)] :post [(: % bool)]}
  (<- (SubmitDetached (boom) :env ENV :key "k-boom" :lease-seconds rig.lease))
  (<- outcome (AwaitDetached "k-boom"))
  (assert (isinstance outcome DetachedFailed) outcome)
  (assert (= outcome.kind "ValueError"))
  (assert (= outcome.message "業務の失敗 base=100"))
  (assert (isinstance outcome.error ValueError))
  True)

(on-every-rig test-program-exception-is-a-failed-outcome program-raises)


(defk unknown-and-release [rig]
  {:pre [(: rig Rig)] :post [(: % bool)]}
  (<- nothing (AwaitDetached "k-nope"))
  (assert (= nothing (DetachedUnknown "k-nope")))
  (<- (SubmitDetached (slow-add 0.0 8) :env ENV :key "k-release" :lease-seconds rig.lease))
  (<- done (AwaitDetached "k-release"))
  (assert (= done (DetachedSucceeded 108)))
  (<- released bool (ReleaseDetached "k-release"))
  (assert released)
  (<- gone (AwaitDetached "k-release"))
  (assert (= gone (DetachedUnknown "k-release")))
  (<- again bool (ReleaseDetached "k-release"))
  (assert (not again))
  ;; 解放した key は送り直せる(新しい task)。
  (<- fresh DetachedSubmitted (SubmitDetached (slow-add 0.0 9) :env ENV :key "k-release" :lease-seconds rig.lease))
  (assert fresh.created)
  (<- rerun (AwaitDetached "k-release"))
  (assert (= rerun (DetachedSucceeded 109)))
  ;; まだ終わっていない task は解放できない(先に取り消す)。
  (<- (SubmitDetached (slow-add (* rig.slow 10) 1) :env ENV :key "k-open" :lease-seconds rig.lease))
  (setv refused None)
  (try
    (<- (ReleaseDetached "k-open"))
    (except [error DetachedRefused] (setv refused error)))
  (assert (and refused (= refused.status 409)) refused)
  (<- (CancelDetached "k-open"))
  True)

(on-every-rig test-unknown-key-and-release unknown-and-release)


(defk await-times-out [rig]
  {:pre [(: rig Rig)] :post [(: % bool)]}
  (<- (SubmitDetached (slow-add rig.slow 10) :env ENV :key "k-timeout" :lease-seconds rig.lease))
  (<- pending (AwaitDetached "k-timeout" :timeout-seconds (* rig.slow 0.2)))
  (assert (isinstance pending DetachedPending) pending)
  (<- outcome (AwaitDetached "k-timeout"))
  (assert (= outcome (DetachedSucceeded 110)) outcome)
  True)

(on-every-rig test-await-with-a-timeout-returns-pending await-times-out)


(defk versions-differ [rig]
  {:pre [(: rig Rig)] :post [(: % bool)]}
  (<- submitted DetachedSubmitted (SubmitDetached (slow-add 0.0 1) :env ENV :key "k-version" :lease-seconds rig.lease))
  (assert submitted.created)
  (<- outcome (AwaitDetached "k-version"))
  (assert (isinstance outcome DetachedVersionMismatch) outcome)
  (assert (in "python" outcome.detail) outcome.detail)
  True)

(on-every-rig test-version-mismatch-is-a-typed-outcome versions-differ OTHER-VERSIONS)


(defk same-key-other-work [rig]
  {:pre [(: rig Rig)] :post [(: % bool)]}
  (<- (SubmitDetached (slow-add 0.0 1) :env ENV :key "k-conflict" :name "a" :lease-seconds rig.lease))
  (setv refused None)
  (try
    (<- (SubmitDetached (slow-add 0.0 1) :env ENV :key "k-conflict" :name "b" :lease-seconds rig.lease))
    (except [error DetachedRefused] (setv refused error)))
  (assert (and refused (= refused.status 409)) refused)
  (<- outcome (AwaitDetached "k-conflict"))
  (assert (= outcome (DetachedSucceeded 101)))
  True)

(on-every-rig test-same-key-for-other-work-is-refused same-key-other-work)


;; --- coordinator の判断(純粋な関数)と worker の途絶 -------------------------------------------------------------

(import doeff_cluster.durable_kv [full-kv state-from-kv])
(import doeff_cluster.worker_model [JobSpec])
(import doeff_cluster.worker_policy [kept-when-cut-off])
(import doeff_cluster.handlers [task-spec])

(setv T (ClusterTiming) V {"python" "3.14.0" "doeff" "1"})

(defn call [state method path now [body None]]
  (respond state (Request method path {} body :actor "test") now T))

(defn beat [state name now [boot "b1"] [statuses None]]
  (call state "POST" "/heartbeat" now {"name" name "labels" {} "capacity" 10 "versions" V "boot" boot
                                       "statuses" (or statuses [])}))

(defn put-detached [state key now [lease 10.0] [retain 100.0]]
  (call state "PUT" (+ "/detached/" key) now {"env" "m:e" "blob" "B" "versions" V "revision" "r" "requires" {}
                                              "leaseSeconds" lease "retainSeconds" retain}))

(defn phase-of [state key] (get (get (call state "GET" (+ "/detached/" key) 0) 2) "phase"))


(deftest test-detached-task-goes-to-the-worker-with-its-boot-and-a-detached-flag
  (setv #(s _ _) (beat (ClusterState) "w" 0))
  (setv #(s status reply) (put-detached s "job-1" 100))
  (assert (= #(status (get reply "created")) #(200 True)))
  (setv task (get s.tasks (get reply "task")))
  (assert (= #(task.phase task.worker task.boot) #("assigned" "w" "b1")))
  (setv #(s _ body) (beat s "w" 200))
  (setv #(row) (get body "tasks"))
  (assert (get row "detached"))
  ;; worker の側: 途絶で止めない task の宣言になる
  (assert (. (task-spec row (Path "/tmp")) detached)))


(deftest test-caller-reads-do-not-extend-the-lease-but-worker-heartbeats-do
  (setv #(s _ _) (beat (ClusterState) "w" 0))
  (setv #(s _ reply) (put-detached s "job-2" 0 :lease 10.0))
  (setv id (get reply "task"))
  (setv #(s _ _) (beat s "w" 9000))                     ; 担い手の heartbeat が lease を 19000 まで延ばす
  (setv #(s _ _) (call s "GET" "/detached/job-2" 15000))   ; 呼び手の読みは lease に触らない
  (setv s (tick s 18000 T))
  (assert (= (. (get s.tasks id) phase) "assigned"))
  (setv s (tick s 19001 T))                            ; 担い手が沈黙した = worker の死
  (assert (= (. (get s.tasks id) phase) "lost"))
  (assert (in "lease" (. (get s.tasks id) detail))))


(deftest test-a-restarted-worker-process-loses-its-detached-tasks-and-does-not-rerun-them
  (setv #(s _ _) (beat (ClusterState) "w" 0))
  (setv #(s _ reply) (put-detached s "job-3" 0))
  (setv id (get reply "task"))
  ;; 同じ名の worker が別の process の世代で名乗る(結果の報告を持っていても、前の世代の物としては受け取らない)
  (setv #(s _ body) (beat s "w" 1000 :boot "b2" :statuses [{"name" (+ "task/" id) "phase" "finished" "result" "R"}]))
  (assert (= (get body "tasks") []))
  (assert (= (. (get s.tasks id) phase) "lost"))
  (assert (in "作り直された" (. (get s.tasks id) detail))))


(deftest test-result-is-kept-after-the-worker-dies-until-release-or-retention
  (setv #(s _ _) (beat (ClusterState) "w" 0))
  (setv #(s _ reply) (put-detached s "job-4" 0 :lease 5.0 :retain 100.0))
  (setv id (get reply "task"))
  (setv #(s _ _) (beat s "w" 1000 :statuses [{"name" (+ "task/" id) "phase" "finished" "result" "R" "detail" ""}]))
  (assert (= (. (get s.tasks id) phase) "finished"))
  (assert (= (. (get s.tasks id) blob) ""))           ; 終わった行は blob を捨て、結果だけ持つ
  ;; 担い手が死んで lease の時間が過ぎても、結果はそのまま
  (setv s (tick s 60000 T))
  (setv #(_ _ view) (call s "GET" "/detached/job-4" 60000))
  (assert (= #((get view "phase") (get view "result")) #("finished" "R")))
  ;; 保持の期限(終わった時刻 1000 + 100 秒)を過ぎたら消える
  (setv s (tick s 101001 T))
  (assert (= (phase-of s "job-4") "unknown")))


(deftest test-detached-records-survive-a-coordinator-restart
  (setv #(s _ _) (beat (ClusterState) "w" 0))
  (setv #(s _ _) (put-detached s "job-5" 0))
  (setv #(s _ reply) (put-detached s "job-6" 0))
  (setv #(s _ _) (beat s "w" 1000 :statuses [{"name" (+ "task/" (get reply "task")) "phase" "finished" "result" "R" "detail" ""}]))
  (setv again (state-from-kv (full-kv s) 2000))
  (assert (= again.tasks s.tasks))
  (setv #(_ _ view) (call again "GET" "/detached/job-6" 2000))
  (assert (= (get view "result") "R"))
  ;; 読み直した後の送り直しも同じ行
  (setv #(_ _ reply) (put-detached again "job-5" 2000))
  (assert (not (get reply "created"))))


(deftest test-drain-waits-until-the-detached-tasks-on-the-worker-are-done
  (setv #(s _ _) (beat (ClusterState) "w" 0))
  (setv #(s _ reply) (put-detached s "job-7" 0))
  (setv id (get reply "task"))
  (setv #(s _ view) (call s "POST" "/workers/w/drain" 100 {}))
  (assert (= (get view "drain" "remaining") [(+ "task/" id)]))
  (assert (not (get view "drain" "drained")))
  ;; drain 中の worker には新しい task を置かない(置ける先が他に無ければ待つ)
  (setv #(s _ other) (put-detached s "job-8" 200))
  (assert (= (. (get s.tasks (get other "task")) phase) "queued"))
  (setv #(s _ _) (beat s "w" 300 :statuses [{"name" (+ "task/" id) "phase" "finished" "result" "R" "detail" ""}]))
  (setv #(s _ view) (call s "GET" "/workers/w" 400))
  (assert (get view "drain" "drained")))


(deftest test-remote-job-tasks-keep-their-caller-bound-lifetime
  ;; RemoteJob の task(/tasks)は今までどおり: 呼び手の問い合わせが lease を延ばし、drain は数えず、途絶で止める。
  (setv #(s _ _) (beat (ClusterState) "w" 0))
  (setv #(s _ body) (call s "POST" "/tasks" 0 {"env" "m:e" "blob" "B" "versions" V "revision" "r" "requires" {}
                                               "name" "n" "leaseSeconds" 5.0}))
  (setv id (get body "task"))
  (setv #(s _ body) (beat s "w" 100))
  (assert (not-in "detached" (get body "tasks" 0)))
  (setv #(s _ view) (call s "POST" "/workers/w/drain" 100 {}))
  (assert (get view "drain" "drained"))
  (setv #(s _ _) (beat s "w" 5200))                   ; heartbeat は RemoteJob の lease を延ばさない
  (assert (not-in id s.tasks)))


(deftest test-a-cut-off-worker-keeps-detached-tasks-and-stops-remote-job-tasks
  (setv detached (JobSpec "task/t1" "doeff_cluster.job_entry" #() "r" :once True :detached True)
        remote (JobSpec "task/t2" "doeff_cluster.job_entry" #() "r" :once True)
        writer (JobSpec "svc" "doeff_cluster.job_entry" #() "r" :handoff True)
        plain (JobSpec "plain" "doeff_cluster.job_entry" #() "r"))
  (assert (= (kept-when-cut-off #(detached remote writer plain)) #(detached writer)))
  ;; CoordinatorLink: 途絶が fence を越えたら、最後に受け取った宣言のうち切り離した task を動かし続ける
  (setv link (CoordinatorLink "http://127.0.0.1:9" "w" {} 1 60000))
  (setv link.last-tasks #(detached remote) link.fence-ms 0 link.last-ok (- (time.monotonic) 1))
  (assert (= (.poll link) (DesiredJobs #(detached)))))


(deftest test-submit-refusals
  (setv #(s _ _) (beat (ClusterState) "w" 0))
  (setv #(_ status _) (put-detached s "job-9" 0 :lease 0.0))
  (assert (= status 400))
  (setv #(_ status _) (put-detached s "job-9" 0 :retain (* 31 24 3600.0)))
  (assert (= status 400))
  (setv #(s _ _) (put-detached s "job-9" 0))
  (setv #(_ status body) (call s "PUT" "/detached/job-9" 0 {"env" "other:env" "blob" "B" "versions" V "revision" "r"}))
  (assert (= status 409) body))
