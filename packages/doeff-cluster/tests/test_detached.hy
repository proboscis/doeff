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
(import collections.abc [Callable])
(import json)
(import time)
(import urllib.parse [urlsplit parse-qsl])
(import pathlib [Path])
(import httpx)
(import pytest)
(import doeff [with_handlers Program])
(import doeff_core_effects.effects [Ask])
(import doeff_core_effects.handlers [reader await-handler])
(import doeff_core_effects.scheduler [Spawn Cancel TaskCancelledError])
(import doeff_time [Delay SimClock sim-time-handler async-time-handler])
(import tests.clock_fixtures [clock-ms])
(import doeff_cluster.cluster_model [ClusterState ClusterTiming Request Requirement ComponentVersion])
(import doeff_cluster.detached_policy [Reply submit-detached])
(import doeff_cluster.api_policy [respond tick])
(import doeff_cluster.handlers [CoordinatorLink])
(import doeff_cluster.job_entry [RunContext env-handlers])
(import doeff_cluster.worker_model [DesiredJobs JobStatus JobPhase])
(import doeff_cluster.remote_model [TaskSucceeded decode-program encode-outcome failed-from current-versions])
(import doeff_cluster.detached_model [SubmitDetached AwaitDetached CancelDetached ReleaseDetached SimulateRunnerLoss
                                      DetachedSubmitted DetachedSucceeded DetachedFailed DetachedLost DetachedCancelled
                                      DetachedVersionMismatch DetachedUnknown DetachedPending DetachedRefused])
(import doeff_cluster.detached [detached-local DetachedLocalStore detached-cluster DetachedClient])
(import tests.detached_rig [ENV slow-add RigWorker MemoryCoordinator worker-tick worker-loop])

(setv OTHER-VERSIONS {"python" "0.0.0" "doeff" "0"})


(defk boom []
  {:pre [] :post [(: % int)]}
  (<- base int (Ask "base"))
  (raise (ValueError (.format "業務の失敗 base={}" base))))


(defhandler rig-runner-loss [#^ RigWorker worker]
  ;; 担い手の死: heartbeat が止まり、走っていた task も消える(coordinator は lease の後に lost とする)。
  (SimulateRunnerLoss []
    (setv worker.dead True
          running (lfor n worker.handles :if (not-in n worker.done) n))
    (for [handle (.values worker.handles)] (<- (Cancel handle)))
    (when worker.loop (<- (Cancel worker.loop)))
    (resume (len running))))


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


(defn #^ Rig open-fake-rig [#^ Path tmp-path request [runner-versions None]]
  (fake-rig runner-versions))


(defn #^ Rig open-coordinator-rig [#^ Path tmp-path request [runner-versions None]]
  (coordinator-rig tmp-path runner-versions))


(defn #^ Rig open-served-rig [#^ Path tmp-path request [runner-versions None]]
  ;; 本物の coordinator の process(conftest の served_coordinator・session で共有)は served の組の検が
  ;; 走る時にだけ起こす(fake と coordinator の組だけを走らせる時は起動の数秒を払わない)。
  (served-rig (.getfixturevalue request "served_coordinator") tmp-path runner-versions))


;; 筋書き 1 つを 3 つの組で回す: 各 deftest は `:params {"open_rig" RIGS}` で組ごとの検に展開される
;; (検の名 = `<筋書きの検>[fake]` / `[coordinator]` / `[served]`)。組を開く関数は (tmp-path request [runner-versions]) を受ける。
(setv RIGS [(pytest.param open-fake-rig :id "fake")
            (pytest.param open-coordinator-rig :id "coordinator")
            (pytest.param open-served-rig :id "served")])


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



(defk run-scenario [rig scenario]
  {:pre [(: rig Rig) (: scenario Callable)] :post [(: % bool)]}
  "筋書き(Rig を受けて Program を返す関数)を組の上で回す。"
  (<- ok (run-on rig (scenario rig)))
  ok)


;; --- 筋書き ---------------------------------------------------------------------------------------------------

(defk submit-and-await [rig]
  {:pre [(: rig Rig)] :post [(: % bool)]}
  (<- submitted DetachedSubmitted (SubmitDetached (slow-add rig.slow 1) :env ENV :key "k-basic" :lease-seconds rig.lease))
  (assert (= submitted (DetachedSubmitted "k-basic" True)))
  (<- outcome (AwaitDetached "k-basic"))
  (assert (= outcome (DetachedSucceeded 101)) outcome)
  True)

(deftest test-submit-and-await-returns-the-value [open-rig tmp-path request]
  {:params {"open_rig" RIGS}}
  (<- ok (run-scenario (open-rig tmp-path request) submit-and-await))
  (assert ok))


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

(deftest test-resubmitting-the-same-key-runs-once [open-rig tmp-path request]
  {:params {"open_rig" RIGS}}
  (<- ok (run-scenario (open-rig tmp-path request) resubmit-same-key))
  (assert ok))


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

(deftest test-the-runner-extends-the-lease-of-a-long-task [open-rig tmp-path request]
  {:params {"open_rig" RIGS}}
  (<- ok (run-scenario (open-rig tmp-path request) task-longer-than-the-lease))
  (assert ok))


(defk caller-vanishes-and-reconnects [rig]
  {:pre [(: rig Rig)] :post [(: % bool)]}
  ;; 呼び手(送って待つ task)を取り消す = 呼び手が消えた。task は続き、別の呼び手が同じ key で結果を受け取る。
  (<- caller (Spawn (submit-then-wait "k-vanish" rig.slow rig.lease)))
  (<- (Delay (* rig.slow 0.3)))
  (<- (Cancel caller))
  (<- early (AwaitDetached "k-vanish" :timeout-seconds 0.0))
  (assert (= #(early.key early.phase) #("k-vanish" "assigned")) early)   ; runner は組ごとの担い手の名
  (<- (Delay rig.slow))
  (<- outcome (AwaitDetached "k-vanish"))
  (assert (= outcome (DetachedSucceeded 103)) outcome)
  True)

(deftest test-task-survives-the-caller-and-a-new-caller-reconnects [open-rig tmp-path request]
  {:params {"open_rig" RIGS}}
  (<- ok (run-scenario (open-rig tmp-path request) caller-vanishes-and-reconnects))
  (assert ok))


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

(deftest test-result-is-kept-after-the-runner-dies [open-rig tmp-path request]
  {:params {"open_rig" RIGS}}
  (<- ok (run-scenario (open-rig tmp-path request) result-outlives-the-runner))
  (assert ok))


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

(deftest test-runner-death-loses-the-task-without-rerunning [open-rig tmp-path request]
  {:params {"open_rig" RIGS}}
  (<- ok (run-scenario (open-rig tmp-path request) runner-dies-mid-run))
  (assert ok))


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

(deftest test-cancel-stops-an-open-task-and-keeps-a-finished-result [open-rig tmp-path request]
  {:params {"open_rig" RIGS}}
  (<- ok (run-scenario (open-rig tmp-path request) cancel-open-and-finished))
  (assert ok))


(defk program-raises [rig]
  {:pre [(: rig Rig)] :post [(: % bool)]}
  (<- (SubmitDetached (boom) :env ENV :key "k-boom" :lease-seconds rig.lease))
  (<- outcome (AwaitDetached "k-boom"))
  (assert (isinstance outcome DetachedFailed) outcome)
  (assert (= outcome.kind "ValueError"))
  (assert (= outcome.message "業務の失敗 base=100"))
  (assert (isinstance outcome.error ValueError))
  True)

(deftest test-program-exception-is-a-failed-outcome [open-rig tmp-path request]
  {:params {"open_rig" RIGS}}
  (<- ok (run-scenario (open-rig tmp-path request) program-raises))
  (assert ok))


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

(deftest test-unknown-key-and-release [open-rig tmp-path request]
  {:params {"open_rig" RIGS}}
  (<- ok (run-scenario (open-rig tmp-path request) unknown-and-release))
  (assert ok))


(defk await-times-out [rig]
  {:pre [(: rig Rig)] :post [(: % bool)]}
  (<- (SubmitDetached (slow-add rig.slow 10) :env ENV :key "k-timeout" :lease-seconds rig.lease))
  (<- pending (AwaitDetached "k-timeout" :timeout-seconds (* rig.slow 0.2)))
  (assert (isinstance pending DetachedPending) pending)
  (<- outcome (AwaitDetached "k-timeout"))
  (assert (= outcome (DetachedSucceeded 110)) outcome)
  True)

(deftest test-await-with-a-timeout-returns-pending [open-rig tmp-path request]
  {:params {"open_rig" RIGS}}
  (<- ok (run-scenario (open-rig tmp-path request) await-times-out))
  (assert ok))


(defk versions-differ [rig]
  {:pre [(: rig Rig)] :post [(: % bool)]}
  (<- submitted DetachedSubmitted (SubmitDetached (slow-add 0.0 1) :env ENV :key "k-version" :lease-seconds rig.lease))
  (assert submitted.created)
  (<- outcome (AwaitDetached "k-version"))
  (assert (isinstance outcome DetachedVersionMismatch) outcome)
  (assert (in "python" outcome.detail) outcome.detail)
  True)

(deftest test-version-mismatch-is-a-typed-outcome [open-rig tmp-path request]
  {:params {"open_rig" RIGS}}
  (<- ok (run-scenario (open-rig tmp-path request OTHER-VERSIONS) versions-differ))
  (assert ok))


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

(deftest test-same-key-for-other-work-is-refused [open-rig tmp-path request]
  {:params {"open_rig" RIGS}}
  (<- ok (run-scenario (open-rig tmp-path request) same-key-other-work))
  (assert ok))


(defk same-key-other-requires [rig]
  {:pre [(: rig Rig)] :post [(: % bool)]}
  ;; 同じ key で実行先の条件(Requirement)だけが違う送り直しも別の仕事 — 409。
  (<- (SubmitDetached (slow-add (* rig.slow 10) 1) :env ENV :key "k-requires" :requires #((Requirement "role" "a"))
                      :lease-seconds rig.lease))
  (setv refused None)
  (try
    (<- (SubmitDetached (slow-add 0.0 1) :env ENV :key "k-requires" :requires #((Requirement "role" "b"))
                        :lease-seconds rig.lease))
    (except [error DetachedRefused] (setv refused error)))
  (assert (and refused (= refused.status 409)) refused)
  (<- (CancelDetached "k-requires"))
  True)

(deftest test-same-key-for-other-requires-is-refused [open-rig tmp-path request]
  {:params {"open_rig" RIGS}}
  (<- ok (run-scenario (open-rig tmp-path request) same-key-other-requires))
  (assert ok))


(deftest test-submit-detached-requires-is-a-tuple-of-requirement
  ;; 対の生の tuple は受けない(欄の名 label・value を型が持つ)。
  (setv raised None)
  (try
    (SubmitDetached (slow-add 0.0 1) :env ENV :key "k" :requires #(#("kind" "k3s")))
    (except [error TypeError] (setv raised error)))
  (assert (is-not raised None))
  (assert (= (. (SubmitDetached (slow-add 0.0 1) :env ENV :key "k" :requires #((Requirement "kind" "k3s"))) requires)
             #((Requirement :label "kind" :value "k3s")))))


;; --- coordinator の判断(純粋な関数)と worker の途絶 -------------------------------------------------------------

(import doeff_cluster.durable_kv [full-kv state-from-kv])
(import doeff_cluster.cluster_policy [state-to-json state-from-json])
(import doeff_cluster.coordinator [load-state])
(import doeff_cluster.wal_store [WalStore])
(import doeff_cluster.worker_model [JobSpec])
(import doeff_cluster.worker_policy [kept-when-cut-off])
(import doeff_cluster.handlers [task-spec])

(setv T (ClusterTiming) V {"python" "3.14.0" "doeff" "1"})

(defn call [state method path now [body None]]
  (respond state (Request method path {} body :actor "test") now T))

(defn beat [state name now [boot "b1"] [statuses None] [boot-at None] [labels None]]
  (call state "POST" "/heartbeat" now (| {"name" name "labels" (or labels {}) "capacity" 10 "versions" V "boot" boot
                                          "statuses" (or statuses [])}
                                         (if (is boot-at None) {} {"bootAt" boot-at}))))

(defn put-detached [state key now [lease 10.0] [retain 100.0]]
  (call state "PUT" (+ "/detached/" key) now {"env" "m:e" "blob" "B" "versions" V "revision" "r" "needs" []
                                              "leaseSeconds" lease "retainSeconds" retain}))

;; 読みの時刻は coordinator が起きてからの猶予(lease-ms)の後(猶予の内の知らない key は warming — detached_policy.detached-read)。
(defn phase-of [state key] (get (get (call state "GET" (+ "/detached/" key) (+ state.started-ms T.lease-ms)) 2) "phase"))


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


(deftest test-a-restarted-worker-process-does-not-rerun-its-detached-tasks-and-they-are-lost-by-the-lease
  ;; 2026-09-27までは新しい世代の heartbeat が来た拍に lost にしていた。旧い世代がまだ動いている(Pod の
  ;; preStop の drain の間)こともあるので、旧い世代の task は旧い世代の heartbeat だけが延ばし、沈黙したら lease 切れで lost。
  (setv #(s _ _) (beat (ClusterState) "w" 0))
  (setv #(s _ reply) (put-detached s "job-3" 0 :lease 10.0))
  (setv id (get reply "task"))
  ;; 同じ名の worker が別の process の世代で名乗る(結果の報告を持っていても、前の世代の物としては受け取らない・走らせ直さない)
  (setv #(s _ body) (beat s "w" 1000 :boot "b2" :statuses [{"name" (+ "task/" id) "phase" "finished" "result" "R"}]))
  (assert (= (get body "tasks") []))
  (assert (= (. (get s.tasks id) phase) "assigned"))
  ;; 新しい世代の heartbeat は旧い世代の task の lease を延ばさない。旧い世代が戻らなければ lease 切れ(10 秒)で lost。
  (setv #(s _ _) (beat s "w" 9000 :boot "b2"))
  (setv s (tick s 10001 T))
  (assert (= (. (get s.tasks id) phase) "lost"))
  (assert (in "lease" (. (get s.tasks id) detail))))


;; --- 同じ名の 2 つの世代(2026-09-27 04:44 JST の実測) ------------------------------------
;; worker の Pod を消すと、旧 Pod は preStop の drain(約 40 秒)の間も worker の process を動かし、新 Pod の worker は同じ名で名乗る。
;; 2 つの process が交互に heartbeat を送る。coordinator は初めて見た世代を新しい世代とし、退いた世代の heartbeat を断る。

(defn alternate [s name now boots [statuses None]]
  "boots の順に 50 ms おきに heartbeat を送る。返り値 #(状態 最後の時刻 世代 → 最後の返事)。"
  (setv replies {})
  (for [boot boots]
    (+= now 50)
    (setv #(s _ body) (beat s name now :boot boot :statuses (.get (or statuses {}) boot [])))
    (setv (get replies boot) body))
  #(s now replies))


(deftest test-an-old-generation-heartbeat-does-not-lose-a-task-placed-on-the-new-generation
  (setv #(s _ _) (beat (ClusterState) "w" 0 :boot "old"))
  ;; 新 Pod の worker が 11 秒後に同じ名で名乗る。以後の task は新しい世代に置く。
  (setv #(s _ _) (beat s "w" 11000 :boot "new"))
  (setv #(s _ reply) (put-detached s "job-25" 11010))
  (setv id (get reply "task"))
  (assert (= (. (get s.tasks id) boot) "new"))
  ;; 旧 Pod の drain の間、2 つの世代が交互に heartbeat を送る(実測は 2 秒ごと・ここは 50 ms ごと)。
  (setv #(s now replies) (alternate s "w" 11010 ["old" "new" "old" "new" "old"]))
  (setv task (get s.tasks id))
  (assert (= #(task.phase task.boot) #("assigned" "new")) task.detail)
  ;; 新しい世代の返事にだけ載る(旧い世代は走らせない)。coordinator の見る世代は新しい世代のまま。
  (assert (= (lfor t (get replies "new" "tasks") (get t "id")) [id]))
  (assert (= (get replies "old" "tasks") []))
  (assert (get replies "old" "superseded"))
  (assert (= (. (get s.workers "w") boot) "new"))
  ;; 旧い世代の heartbeat は新しい世代の task の lease を延ばさず、新しい世代の heartbeat が延ばす。
  (setv #(s _ _) (beat s "w" (+ now 50) :boot "new"))
  (assert (= (. (get s.tasks id) lease-until-ms) (+ now 50 (. (get s.tasks id) lease-ms)))))


(deftest test-a-task-on-the-old-generation-keeps-its-lease-and-result-while-the-old-generation-lives
  (setv #(s _ _) (beat (ClusterState) "w" 0 :boot "old"))
  (setv #(s _ reply) (put-detached s "job-24" 0 :lease 10.0))
  (setv done (get reply "task"))
  (setv #(s _ reply) (put-detached s "job-23" 0 :lease 10.0))
  (setv silent (get reply "task"))
  (setv #(s _ _) (beat s "w" 6000 :boot "new"))
  ;; 新しい世代が来ても、旧い世代に置いた task は lost にしない(旧い世代は drain の間まだ走らせている)。
  (assert (= (. (get s.tasks done) phase) "assigned"))
  ;; 旧い世代の heartbeat は自分の世代の task の lease を延ばし、その task だけを返事に載せる。
  (setv #(s _ body) (beat s "w" 9000 :boot "old"))
  (assert (= (sorted (lfor t (get body "tasks") (get t "id"))) (sorted [done silent])))
  ;; 旧い世代の終わりの報告は受ける(結果を捨てない)。
  (setv #(s _ _) (beat s "w" 12000 :boot "old"
                       :statuses [{"name" (+ "task/" done) "phase" "finished" "result" "R" "detail" ""}]))
  (assert (= #((. (get s.tasks done) phase) (. (get s.tasks done) result)) #("finished" "R")))
  ;; 旧い世代が消えた(heartbeat が止まった)= lease 切れで lost。新しい世代の heartbeat は延ばさない。
  (setv #(s _ _) (beat s "w" 20000 :boot "new"))
  (setv s (tick s 22001 T))
  (assert (= (. (get s.tasks silent) phase) "lost"))
  (assert (in "lease" (. (get s.tasks silent) detail)))
  ;; 新しい世代の生存はそのまま(旧い世代の沈黙は worker の沈黙ではない)。
  (assert (= (. (get s.workers "w") boot) "new")))


(deftest test-the-generation-order-survives-a-coordinator-restart
  (setv #(s _ _) (beat (ClusterState) "w" 0 :boot "old"))
  (setv #(s _ _) (beat s "w" 1000 :boot "new"))
  (setv again (state-from-kv (full-kv s) 2000))
  (assert (= #((. (get again.workers "w") boot) (. (get again.workers "w") retired)) #("new" #("old"))))
  ;; 読み直した後も、旧い世代の heartbeat は新しい世代を押しのけない。
  (setv #(again _ body) (beat again "w" 2100 :boot "old"))
  (assert (get body "superseded"))
  (assert (= (. (get again.workers "w") boot) "new")))


;; --- 起動時刻で決める世代の新旧(2026-09-27 — #757)------------------------------------------------
;; 初めて見た順だけでは、置き場を失った coordinator に新しい世代が先に届くと、後から来た古い世代が今の世代になり、古い世代が
;; 止んだ後も新しい世代の heartbeat を断り続けて名が沈黙した。worker は heartbeat に process の起動時刻 bootAt を載せる。

(deftest test-an-empty-coordinator-that-hears-the-new-generation-first-keeps-the-new-generation
  (setv #(s _ _) (beat (ClusterState) "w" 0 :boot "new" :boot-at 2000))
  (setv #(s _ old-reply) (beat s "w" 50 :boot "old" :boot-at 1000))
  ;; 古い世代の heartbeat は superseded の答え・今の世代は新しい世代のまま。
  (assert (get old-reply "superseded") old-reply)
  (assert (= (. (get s.workers "w") boot) "new") (get s.workers "w"))
  (assert (in "old" (. (get s.workers "w") retired)))
  ;; 古い世代の preStop の drain は今の世代に付かない。
  (setv #(s _ view) (call s "POST" "/workers/w/drain" 100 {"boot" "old"}))
  (assert (not-in "w" s.drains) s.drains)
  (assert (get view "drain" "superseded") view)
  ;; 古い世代が止み、新しい世代だけが 5 秒ごとに heartbeat を送る → 60 秒後も新しい世代が生きていて ready。
  (for [t (range 5000 65000 5000)]
    (setv #(s _ reply) (beat s "w" t :boot "new" :boot-at 2000))
    (assert (not (.get reply "superseded" False)) #(t reply)))
  (setv s (tick s 65000 T))
  (setv #(_ _ view) (call s "GET" "/workers/w" 65000))
  (assert (= #((get view "alive") (get view "ready") (get view "boot")) #(True True "new")) view))


(deftest test-workers-that-do-not-name-a-boot-time-keep-the-first-seen-order
  ;; 起動時刻を名乗らない旧い worker は今までどおり(初めて見た順: 見ていない世代が新しい)。
  (setv #(s _ _) (beat (ClusterState) "w" 0 :boot "a"))
  (setv #(s _ reply) (beat s "w" 50 :boot "b"))
  (assert (not (.get reply "superseded" False)))
  (assert (= #((. (get s.workers "w") boot) (. (get s.workers "w") retired)) #("b" #("a"))))
  (setv #(s _ reply) (beat s "w" 100 :boot "a"))
  (assert (get reply "superseded"))
  ;; 片方だけが起動時刻を名乗る時も初めて見た順。
  (setv #(s _ _) (beat s "v" 0 :boot "a" :boot-at 2000))
  (setv #(s _ _) (beat s "v" 50 :boot "b"))
  (assert (= (. (get s.workers "v") boot) "b")))


(deftest test-a-newer-boot-time-takes-the-name-back-from-the-retired-list
  ;; 旧い coordinator が初めて見た順で退かせた世代でも、両方の起動時刻を知れば起動時刻の大きい方が今の世代。
  (setv #(s _ _) (beat (ClusterState) "w" 0 :boot "new"))
  (setv #(s _ _) (beat s "w" 50 :boot "old" :boot-at 1000))
  (assert (= #((. (get s.workers "w") boot) (. (get s.workers "w") retired)) #("old" #("new"))))
  (setv #(s _ reply) (beat s "w" 100 :boot "new" :boot-at 2000))
  (assert (not (.get reply "superseded" False)) reply)
  (assert (= #((. (get s.workers "w") boot) (. (get s.workers "w") retired)) #("new" #("old")))))


(deftest test-the-boot-time-survives-the-state-file-and-the-durable-kv
  (setv #(s _ _) (beat (ClusterState) "w" 0 :boot "old" :boot-at 1000))
  (setv #(s _ _) (beat s "w" 100 :boot "new" :boot-at 2000))
  (for [again [(state-from-kv (full-kv s) 200) (state-from-json (json.loads (json.dumps (state-to-json s))) 200)]]
    (setv w (get again.workers "w"))
    (assert (= #(w.boot w.retired w.boot-at) #("new" #("old") 2000)) w)
    ;; 読み直した後も、一度も見ていない古い世代は起動時刻で古いと分かる(名乗りとして受けない)。
    (setv #(again _ reply) (beat again "w" 300 :boot "older" :boot-at 500))
    (assert (get reply "superseded") reply)
    (assert (= (. (get again.workers "w") boot) "new")))
  ;; 起動時刻の欄の無い旧い形の置き場は、起動時刻を知らない(初めて見た順へ落とす)。
  (setv kv (full-kv s))
  (del (get kv "worker/w" "bootAt"))
  (assert (is (. (get (. (state-from-kv kv 200) workers) "w") boot-at) None)))


;; --- 置き場を失った coordinator と走っている切り離した task(2026-09-27 — #757)----------------------------
;; worker は返事に載らない task の子 process を止め、blob と結果の file を消す。置き場を失った coordinator は task の行を持たないので、
;; 以前は最初の返事で生きている worker の走っている切り離した task を全部止めさせた。worker は状態の報告に置かれた時の行を写し、
;; coordinator はそれを引き取る。

(deftest test-an-empty-coordinator-adopts-the-running-detached-task-a-worker-reports [tmp-path]
  (setv #(s _ _) (beat (ClusterState) "w" 0 :boot-at 1000))
  (setv #(s _ reply) (put-detached s "job-amnesia" 0 :lease 10.0 :retain 100.0))
  (setv id (get reply "task"))
  (setv #(s _ body) (beat s "w" 100 :boot-at 1000))
  (setv link (CoordinatorLink "http://127.0.0.1:9" "w" #() 10 60000 :task-dir (str (/ tmp-path "tasks"))))
  (setv #(before) (.accept-tasks link (get body "tasks")))
  (setv rows (.report link #((JobStatus (+ "task/" id) JobPhase.RUNNING "r" "r" 42 1))))
  ;; 置き場を失った coordinator が起きる: 走っている task を同じ行で引き取り、同じ heartbeat の返事に載せる。
  (setv #(fresh _ body) (beat (ClusterState) "w" 5000 :boot-at 1000 :statuses rows))
  (assert (= (lfor t (get body "tasks") (get t "id")) [id]) body)
  (setv #(after) (.accept-tasks link (get body "tasks")))
  (assert (= after before) "引き取った行の宣言の spec が変わった(worker は子 process を止める)")
  (assert (.exists (/ tmp-path "tasks" (+ id ".blob"))) "走っている task の blob が消えた")
  (assert (= (phase-of fresh "job-amnesia") "assigned"))
  ;; 終わりの報告は呼び手の key で読める。
  (setv #(fresh _ _) (beat fresh "w" 6000 :boot-at 1000
                           :statuses [{"name" (+ "task/" id) "phase" "finished" "result" "R" "detail" ""}]))
  (setv #(_ _ view) (call fresh "GET" "/detached/job-amnesia" 6000))
  (assert (= #((get view "phase") (get view "result")) #("finished" "R")) view)
  ;; 次に振る id は引き取った id と重ならない。
  (setv #(fresh _ other) (put-detached fresh "job-next" 6100))
  (assert (!= (get other "task") id))
  ;; 行を持つ task(取り消した)は引き取らない — 取り消し・lost は今までどおり止める。
  (setv #(s _ _) (call s "POST" "/detached/job-amnesia/cancel" 200))
  (setv #(s _ body) (beat s "w" 300 :boot-at 1000 :statuses rows))
  (assert (= (get body "tasks") []) body)
  ;; 写しの無い報告(旧い worker)は引き取らない。
  (setv #(_ _ body) (beat (ClusterState) "w" 5000 :statuses [{"name" (+ "task/" id) "phase" "running"}]))
  (assert (= (get body "tasks") []) body))


;; 直すべき所(構成レビュー 2026-09-27): 起きた直後の読み・requires・終わった報告・id の振り直し・欠けた写し。

(defn #^ tuple placed-echo [#^ Path tmp-path #^ dict [requires None]]
  "もとの coordinator が task を置き、worker が受けて状態の報告に写しを添えるまで。返り値 #(もとの状態 id 報告を作る link 元の spec)。"
  (setv labels (or requires {}))
  (setv #(s _ _) (beat (ClusterState) "w" 0 :boot-at 1000 :labels labels))
  (setv #(s _ reply) (call s "PUT" "/detached/job-e" 0 {"env" "m:e" "blob" "B" "versions" V "revision" "r" "requires" labels
                                                        "leaseSeconds" 10.0 "retainSeconds" 100.0}))
  (setv id (get reply "task"))
  (setv #(s _ body) (beat s "w" 100 :boot-at 1000 :labels labels))
  (setv link (CoordinatorLink "http://127.0.0.1:9" "w" #() 10 60000 :task-dir (str (/ tmp-path "tasks"))))
  (setv #(spec) (.accept-tasks link (get body "tasks")))
  #(s id link spec))


(deftest test-a-just-started-coordinator-does-not-call-a-key-unknown-before-the-workers-report [tmp-path]
  (setv #(_ id link _) (placed-echo tmp-path))
  (setv rows (.report link #((JobStatus (+ "task/" id) JobPhase.RUNNING "r" "r" 42 1))))
  (setv fresh (ClusterState :started-ms 5000))
  ;; worker の最初の heartbeat より先に呼び手の読みが届く: 知らないと言わない(503・warming)。
  (setv #(fresh status view) (call fresh "GET" "/detached/job-e" 5000))
  (assert (= #(status (get view "phase")) #(503 "warming")) #(status view))
  (setv #(fresh _ _) (beat fresh "w" 5100 :boot-at 1000 :statuses rows))
  (setv #(fresh status view) (call fresh "GET" "/detached/job-e" 5200))
  (assert (= #(status (get view "phase") (get view "task")) #(200 "assigned" id)) view)
  ;; 猶予(lease-ms)を過ぎた後の本当に知らない key は unknown。
  (setv #(_ status view) (call fresh "GET" "/detached/never" (+ 5000 T.lease-ms)))
  (assert (= #(status (get view "phase")) #(200 "unknown")) view))


(deftest test-an-adopted-task-keeps-its-requirements [tmp-path]
  (setv #(s id link spec) (placed-echo tmp-path {"kind" "k3s"}))
  (setv rows (.report link #((JobStatus (+ "task/" id) JobPhase.RUNNING "r" "r" 42 1))))
  (setv #(fresh _ _) (beat (ClusterState) "w" 5000 :boot-at 1000 :statuses rows :labels {"kind" "k3s"}))
  (assert (= (. (get fresh.tasks id) requires) (. (get s.tasks id) requires) #((Requirement "kind" "k3s"))))
  (assert (= (. (get fresh.tasks id) requires) (. (get s.tasks id) requires))))


(deftest test-an-empty-coordinator-adopts-a-finished-task-with-its-result [tmp-path]
  ;; worker は返事に無い task の結果の file を消す — 終わった報告も引き取らないと結果を失い、呼び手は完走した仕事を送り直す。
  (setv #(_ id link _) (placed-echo tmp-path))
  (setv #(row) (.report link #((JobStatus (+ "task/" id) JobPhase.FINISHED "r" "r" None 1))))
  (setv #(fresh _ _) (beat (ClusterState) "w" 5000 :boot-at 1000 :statuses [(| row {"result" "R" "detail" ""})]))
  (setv #(_ _ view) (call fresh "GET" "/detached/job-e" 5000))
  (assert (= #((get view "phase") (get view "result")) #("finished" "R")) view)
  ;; code-failed も同じ(終わりの理由が呼び手に届く)。
  (setv #(fresh _ _) (beat (ClusterState) "w" 5000 :boot-at 1000
                           :statuses [(| row {"phase" "code-failed" "detail" "boom"})]))
  (assert (= (. (get fresh.tasks id) phase) "code-failed")))


(deftest test-a-coordinator-that-starts-without-a-store-does-not-reuse-task-ids [tmp-path]
  ;; 置き場の無いところから起きた coordinator が t1 から振り直すと、worker に残る前の t1 の blob で新しい t1 が走った
  ;; (accept-tasks は blob が在れば書き直さない)。起動ごとに違う頭を振る。
  (setv #(_ id link _) (placed-echo tmp-path))
  (setv fresh (load-state (str (/ tmp-path "state.json")) (WalStore (str (/ tmp-path "wal"))) 123456))
  (setv #(fresh _ _) (beat fresh "other" 123500))
  (setv #(fresh _ reply) (call fresh "PUT" "/detached/job-new" (+ 123500 T.lease-ms)
                               {"env" "m:e" "blob" "NEW" "versions" V "revision" "r" "needs" [] "leaseSeconds" 10.0}))
  (assert (!= (get reply "task") id) #(reply id))
  (assert (= fresh.task-prefix "t1e240-") fresh.task-prefix)
  ;; 頭は保存と読み直しで戻る(state JSON と durable kv)。
  (assert (= (. (state-from-kv (full-kv fresh) 0) task-prefix) "t1e240-"))
  (assert (= (. (state-from-json (json.loads (json.dumps (state-to-json fresh))) 0) task-prefix) "t1e240-"))
  ;; 以前からの置き場(頭の欄が無い)は今までどおり t<番号>。
  (assert (= (. (state-from-kv (full-kv (ClusterState)) 0) task-prefix) "t")))


(deftest test-an-echo-without-env-or-revision-is-not-adopted-and-the-heartbeat-is-answered [tmp-path]
  (setv #(_ id link _) (placed-echo tmp-path))
  (setv #(row) (.report link #((JobStatus (+ "task/" id) JobPhase.RUNNING "r" "r" 42 1))))
  (setv broken (| row {"task" (dfor #(k v) (.items (get row "task")) :if (not-in k #("env" "revision")) k v)}))
  (setv #(fresh status body) (beat (ClusterState) "w" 5000 :statuses [broken]))
  (assert (= #(status (get body "tasks")) #(200 [])) #(status body))
  (assert (not-in id fresh.tasks)))


(defk amnesia-scenario [coordinator]
  {:pre [(: coordinator MemoryCoordinator)] :post [(: % bool)]}
  (<- (SubmitDetached (slow-add 3.0 1) :env ENV :key "k-amnesia" :lease-seconds 5.0))
  (<- (Delay 1.0))
  ;; coordinator が置き場を失って起き直す(task の行も worker の名乗りも無い)。呼び手はすぐ読む — worker の最初の heartbeat より
  ;; 先に届く読みも「知らない」と答えない(送り直させて並走させない)。
  (setv coordinator.state (ClusterState :started-ms (clock-ms coordinator.clock)))
  (<- outcome (AwaitDetached "k-amnesia"))
  (assert (= outcome (DetachedSucceeded 101)) outcome)
  True)


(deftest test-an-amnesic-coordinator-does-not-stop-the-running-detached-task [tmp-path]
  ;; 本物の CoordinatorLink と本物の coordinator の判断で: 置き場を失った coordinator が起きても、担い手の worker は走っている
  ;; 切り離した task を止めず、呼び手は同じ key で結果を受け取る。
  (setv clock (SimClock)
        coordinator (MemoryCoordinator clock)
        transport (httpx.MockTransport coordinator.handle)
        worker (RigWorker "http://coordinator" (/ tmp-path "tasks") (current-versions) :transport transport)
        client (DetachedClient "http://coordinator" "r" :transport transport)
        rig (Rig "coordinator" [(sim-time-handler :clock clock) (rig-runner-loss worker) (detached-cluster client :poll-seconds 0.5)]
                 worker 3.0 5.0 0.5))
  (<- ok (run-on rig (amnesia-scenario coordinator)))
  (assert ok))


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
  (setv #(s _ body) (call s "POST" "/tasks" 0 {"env" "m:e" "blob" "B" "versions" V "revision" "r" "needs" []
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
  (setv link (CoordinatorLink "http://127.0.0.1:9" "w" #() 1 60000))
  (setv link.last-tasks #(detached remote) link.fence-ms 0 link.last-ok (- (time.monotonic) 1))
  (assert (= (.poll link) (DesiredJobs #(detached)))))


(deftest test-detached-task-keeps-typed-requirements-and-versions-through-the-saved-state
  ;; 口の答えは Reply(状態・status・本文)。task の行は条件を Requirement・版を ComponentVersion で持ち、保存と読み直しの後も同じ型。
  (setv #(s _ _) (beat (ClusterState) "w" 0))
  (setv reply (submit-detached s "job-typed" {"env" "m:e" "blob" "B" "versions" V "revision" "r" "requires" {"role" "x" "kind" "k3s"}}
                               100))
  (assert (isinstance reply Reply))
  (assert (= #(reply.status (get reply.body "created")) #(200 True)))
  (setv task (get reply.state.tasks (get reply.body "task")))
  (assert (= task.requires #((Requirement "kind" "k3s") (Requirement "role" "x"))))
  (assert (all (gfor item task.requires (isinstance item Requirement))))
  (assert (= task.versions #((ComponentVersion "doeff" "1") (ComponentVersion "python" "3.14.0"))))
  (assert (all (gfor item task.versions (isinstance item ComponentVersion))))
  (setv again (get (. (state-from-kv (full-kv reply.state) 0) tasks) task.id))
  (assert (= again task))
  (assert (all (gfor item (+ again.requires again.versions) (isinstance item #(Requirement ComponentVersion))))))


(deftest test-submit-refusals
  (setv #(s _ _) (beat (ClusterState) "w" 0))
  (setv #(_ status _) (put-detached s "job-9" 0 :lease 0.0))
  (assert (= status 400))
  (setv #(_ status _) (put-detached s "job-9" 0 :retain (* 31 24 3600.0)))
  (assert (= status 400))
  (setv #(s _ _) (put-detached s "job-9" 0))
  (setv #(_ status body) (call s "PUT" "/detached/job-9" 0 {"env" "other:env" "blob" "B" "versions" V "revision" "r"}))
  (assert (= status 409) body))
