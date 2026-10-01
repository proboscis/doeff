;; 切り離した task(SubmitDetached / AwaitDetached / CancelDetached / ReleaseDetached — detached_model.hy)の契約の検。
;;
;; 同じ筋書き(doeff の Program)を 3 つの組で回す:
;;   sim         … 手元の runner sim-cluster(本物の coordinator の調停ループと本物の run-worker・偽の宿が task の Program を柵の中で
;;                 走らせる — 筋書きは検の側の呼び手として sim の送り手の口で話す)・仮想の時計。担い手の死は KillWorker
;;                 (2026-09-28 まで同じ VM の模擬 detached-local の組だった — 呼び手の外側の handler を継ぐので消した)
;;   coordinator … 本物の coordinator の判断(api_policy.respond / tick)を httpx.MockTransport の後ろに置き、本物の detached-cluster と
;;                 本物の coordinator への口(heartbeat・task の file・結果の報告)で話す。担い手は同じ VM で Program を走らせる・仮想の時計
;;   served      … 本物の coordinator の process(hy -m doeff_cluster.coordinator.entry.main・HTTP・追記の log。conftest の served_coordinator が
;;                 検の間で 1 つを共有する)・同じ担い手・実時間
;; 筋書き: 送って待つ / 同じ key の送り直し / 呼び手が消えても続き再接続 / 結果の後の担い手の死 / 走っている間の担い手の死 /
;;         lease は担い手が延ばす / 取り消し / Program の例外 / 知らない key と解放 / timeout / 版の不一致 / key の衝突。
;; その後に coordinator の判断(純粋な関数)と worker の途絶の検。
(require doeff-hy.macros [deftest defk deff defhandler <- val var])
(import collections.abc [Callable])
(import json)
(import time)
(import urllib.parse [urlsplit parse-qsl])
(import pathlib [Path])
(import httpx)
(import pytest)
(import doeff [with_handlers Program run])
(import doeff_core_effects.effects [Ask])
(import doeff_core_effects.handlers [reader await-handler])
(import doeff_core_effects.scheduler [Spawn Cancel Task TaskCancelledError])
(import doeff_time [Delay SimClock sim-time-handler async-time-handler])
(import tests.clock_fixtures [clock-ms])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState ComponentVersion])
(import doeff_cluster.foundation.coordinator_inbox [http-request])
(import doeff_cluster.coordinator.core.detached_policy [Reply submit-detached])
(import doeff_cluster.coordinator.core.api_policy [respond tick])
(import tests.link_rig [LinkRig])

(import doeff_cluster.worker.intent.worker_model [DesiredJobs JobStatus] doeff_cluster.shared.intent.job_model [JobPhase])
(import doeff_cluster.shared.intent.remote_model [TaskSucceeded decode-program encode-outcome failed-from])
(import doeff_cluster.foundation.process_versions [current-versions])
(import doeff_cluster.shared.intent.detached_model [SubmitDetached AwaitDetached CancelDetached ReleaseDetached
                                      DetachedSubmitted DetachedSucceeded DetachedFailed DetachedLost DetachedCancelled
                                      DetachedVersionMismatch DetachedUnknown DetachedPending DetachedRefused])
(import doeff_cluster.shared.protocol.detached [detached-cluster])
(import doeff_core_effects.http_handlers [http-production-handler])
(import tests.transport_http [transport-http route-cell detached-sender TEST-ROUTE])
(import doeff_cluster.sim.local [sim-cluster SimWorker KillWorker ReadCoordinator])
(import doeff_cluster.shared.intent.service_model [system-of])
(import tests.detached_rig [slow-add RigWorker MemoryCoordinator worker-tick worker-loop RIG-PROVIDES])
(import tests.program_rows [SAMPLE-TASK-PROGRAM program-placed])

(setv OTHER-VERSIONS {"python" "0.0.0" "doeff" "0"})
;; 筋書きの task が要る能力: 3 つの組の担い手(sim の worker・RigWorker の既定)が共に提供する local。
(setv LOCAL (frozenset RIG-PROVIDES))
;; 3 つの組の担い手の名(筋書きの KillWorker が名指す)。
(val RUNNER "w1")
;; sim の組の系: job を持たない(筋書きだけが呼び手として coordinator に話し、task は worker が走らせる)。
(val NO-JOBS (system-of "detached-scenarios" #()))


(defk boom-body []
  {:pre [] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  (<- base int (Ask "base"))
  (raise (ValueError (.format "業務の失敗 base={}" base))))

(defk boom []
  {:pre [] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "例外を投げる送る Program(自分の reader を並べる — 担い手は handler を足さない)。"
  (<- value int (with-handlers [(reader {"base" 100})] (boom-body)))
  value)


(defhandler rig-runner-loss [#^ RigWorker worker]
  ;; 担い手の死(sim の組の KillWorker と同じ effect に、coordinator・served の組の担い手で答える): heartbeat が止まり、走っていた
  ;; task も消える(coordinator は lease の後に lost とする)。答え = 消えた(終わっていなかった)task の数。
  (KillWorker [name]
    (val running (lfor n worker.handles :if (not-in n worker.done) n))
    (setv worker.dead True)
    (for [handle (.values worker.handles)] (<- (Cancel handle)))
    (when worker.loop (<- (Cancel worker.loop)))
    (resume (len running))))


;; --- 組 -----------------------------------------------------------------------------------------------------

(defclass Rig []
  "筋書きを回す組。handlers = 筋書きに被せる handler の組(外側が先 — sim は使わない)・worker = 担い手(sim は None)・
   sim-workers = sim の組の worker(SimWorker の tuple — 他の組は None)・slow / lease / poll = 時間の尺度。"
  (defn #^ None __init__ [self #^ str kind #^ list handlers #^ (| RigWorker None) worker #^ float slow #^ float lease #^ float poll
                  #^ (| Callable None) [runs None] #^ (| Callable None) [close None] #^ (| tuple None) [sim-workers None]]
    (setv self.kind kind self.handlers handlers self.worker worker self.slow slow self.lease lease self.poll poll
          self.runs runs self.close (or close (fn [] None)) self.sim-workers sim-workers)))


(deff sim-rig [runner-versions]  ; defk にできない: pytest の params が渡す組を開く関数(open-sim-rig)が Program の外で呼ぶ
  {:pre [(: runner-versions (| dict None))] :post [(: % Rig)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "sim の組を開くため: 担い手 = 能力 local の sim の worker 1 台(runner-versions = その worker の名乗る版 — None は送り手と同じ)。
   送る Program が自分の handler を並べる(sim の宿は柵の中で走らせ、足りない handler を補わない)。task の始まりは worker の拍(0.5 秒)と
   コードの準備の拍を挟むので、slow は他の組より長く取る(仮想の時計なので走る時間は増えない)。"
  (Rig "sim" [] None 6.0 5.0 1.0
       :sim-workers #((SimWorker :name RUNNER :provides LOCAL :versions runner-versions))))


(defn #^ Rig coordinator-rig [#^ Path tmp-path #^ (| dict None) runner-versions]
  (setv clock (SimClock)
        coordinator (MemoryCoordinator clock)
        transport (httpx.MockTransport coordinator.handle)
        worker (RigWorker "http://coordinator" (/ tmp-path "tasks") (or runner-versions (current-versions)) :transport transport)
        sender (detached-sender "r"))
  (Rig "coordinator" [(sim-time-handler :clock clock) (transport-http transport) (rig-runner-loss worker)
                      (detached-cluster (route-cell) TEST-ROUTE sender :poll-seconds 0.5)]
       worker 3.0 5.0 0.5
       :runs (fn [key] (len (lfor t (.values coordinator.state.tasks) :if (= t.key key) t)))))


(defn #^ Rig served-rig [#^ str url #^ Path tmp-path #^ (| dict None) [runner-versions None]]
  (setv worker (RigWorker url (/ tmp-path "tasks") (or runner-versions (current-versions)))
        sender (detached-sender "r"))
  (defn #^ int runs [#^ str key]
    (len (lfor t (get (.json (httpx.get (+ url "/state"))) "tasks") :if (= (.get t "key") key) t)))
  ;; 実時間: lease は heartbeat の間隔(0.2 秒)の十倍以上に取る(込んだ機体で heartbeat が遅れても消失と取り違えない)。
  (Rig "served" [(await-handler) (async-time-handler) (http-production-handler) (rig-runner-loss worker)
                 (detached-cluster (route-cell url) TEST-ROUTE sender :poll-seconds 0.2)]
       worker 1.0 2.5 0.2 :runs runs))


(defn #^ Rig open-sim-rig [#^ Path tmp-path #^ pytest.FixtureRequest request #^ (| dict None) [runner-versions None]]
  (sim-rig runner-versions))


(defn #^ Rig open-coordinator-rig [#^ Path tmp-path #^ pytest.FixtureRequest request #^ (| dict None) [runner-versions None]]
  (coordinator-rig tmp-path runner-versions))


(defn #^ Rig open-served-rig [#^ Path tmp-path #^ pytest.FixtureRequest request #^ (| dict None) [runner-versions None]]
  ;; 本物の coordinator の process(conftest の served_coordinator・session で共有)は served の組の検が
  ;; 走る時にだけ起こす(sim と coordinator の組だけを走らせる時は起動の数秒を払わない)。
  (served-rig (.getfixturevalue request "served_coordinator") tmp-path runner-versions))


;; 筋書き 1 つを 3 つの組で回す: 各 deftest は `:params {"rig_name" RIGS}` で組ごとの検に展開される
;; (検の名 = `<筋書きの検>[sim]` / `[coordinator]` / `[served]`)。組を開く関数は (tmp-path request [runner-versions]) を受ける。
;; params には組の名を渡し、開く関数は型の付いた表 RIG-OPENERS から引く — params の値は検査器から型が見えない(object)ので、
;; 関数そのものを渡すと呼ぶ所が型の赤になる(#1731)。検は頭で名が str であることを確かめる。
(val RIG-OPENERS
     {"sim" open-sim-rig "coordinator" open-coordinator-rig "served" open-served-rig})
(setv RIGS [(pytest.param "sim" :id "sim")
            (pytest.param "coordinator" :id "coordinator")
            (pytest.param "served" :id "served")])


(defk open-named [rig-name tmp-path request runner-versions]
  {:pre [(: rig-name str) (: tmp-path Path) (: request pytest.FixtureRequest) (: runner-versions (| dict None))] :post [(: % Rig)]
   :tags {:context "doeff-cluster-test" :role "foundation"}}
  "名で表から組を開く関数を引き、組を開くため(runner-versions = None なら組の既定の版)。"
  ((get RIG-OPENERS rig-name) tmp-path request runner-versions))


(defk with-worker [rig scenario]
  {:pre [(: rig Rig) (: scenario Program)] :post [(: % bool)]}
  ;; 担い手を 1 拍名乗らせてから(送った時に置ける worker が在るように)heartbeat のループを走らせ、筋書きの後に止める。
  (setv worker rig.worker)
  (when worker
    (<- (worker-tick worker))
    (<- loop Task (Spawn (worker-loop worker rig.poll) :daemon True))
    (setv worker.loop loop))
  (<- scenario)
  (when (and worker (not worker.dead))
    (setv worker.dead True)
    (assert (is-not worker.loop None) "担い手のループは筋書きの前に走らせた")
    (<- (Cancel worker.loop)))
  True)


(defk run-on [rig scenario]
  {:pre [(: rig Rig) (: scenario Program)] :post [(: % bool)]}
  ;; sim の組は筋書きを sim-cluster の中で回す(担い手は sim の worker)。他の組は handler を被せ、担い手を並べて回す。
  (try
    (if (= rig.kind "sim")
        (<- (sim-cluster NO-JOBS scenario :workers rig.sim-workers))
        (<- (with-handlers rig.handlers (with-worker rig scenario))))
    (finally
      (rig.close)))
  True)


(defk runs-of [rig key]
  {:pre [(: rig Rig) (: key str)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "key の task を coordinator が作った数(冪等の検 — sim の組は coordinator の GET /state を読む)。"
  (cond
    (= rig.kind "sim") (do (<- state dict (ReadCoordinator "/state"))
                           (len (lfor t (get state "tasks") :if (= (.get t "key") key) t)))
    (is rig.runs None) (raise (ValueError (.format "組 {} は作った数を数えられない" rig.kind)))
    True (rig.runs key)))



(defk run-scenario [rig scenario]
  {:pre [(: rig Rig) (: scenario Callable)] :post [(: % bool)]}
  "筋書き(Rig を受けて Program を返す関数)を組の上で回す。"
  (<- ok (run-on rig (scenario rig)))
  ok)


;; --- 筋書き ---------------------------------------------------------------------------------------------------

(defk submit-and-await [rig]
  {:pre [(: rig Rig)] :post [(: % bool)]}
  (<- submitted DetachedSubmitted (SubmitDetached (slow-add rig.slow 1) :needs LOCAL :key "k-basic" :lease-seconds rig.lease))
  (assert (= submitted (DetachedSubmitted "k-basic" True)))
  (<- outcome (AwaitDetached "k-basic"))
  (assert (= outcome (DetachedSucceeded 101)) outcome)
  True)

(deftest test-submit-and-await-returns-the-value [rig-name tmp-path request]
  {:params {"rig_name" RIGS}}
  (assert (isinstance rig-name str) rig-name)
  (<- rig Rig (open-named rig-name tmp-path request None))
  (<- ok (run-scenario rig submit-and-await))
  (assert ok))


(defk resubmit-same-key [rig]
  {:pre [(: rig Rig)] :post [(: % bool)]}
  (<- first DetachedSubmitted (SubmitDetached (slow-add rig.slow 2) :needs LOCAL :key "k-idem" :lease-seconds rig.lease))
  (<- second DetachedSubmitted (SubmitDetached (slow-add rig.slow 2) :needs LOCAL :key "k-idem" :lease-seconds rig.lease))
  (assert (= #(first.created second.created) #(True False)))
  (<- outcome (AwaitDetached "k-idem"))
  (assert (= outcome (DetachedSucceeded 102)) outcome)
  ;; 終わった後の送り直しも同じ行(走らせ直さない)。
  (<- third DetachedSubmitted (SubmitDetached (slow-add rig.slow 2) :needs LOCAL :key "k-idem" :lease-seconds rig.lease))
  (assert (not third.created))
  (<- runs int (runs-of rig "k-idem"))
  (assert (= runs 1) runs)
  True)

(deftest test-resubmitting-the-same-key-runs-once [rig-name tmp-path request]
  {:params {"rig_name" RIGS}}
  (assert (isinstance rig-name str) rig-name)
  (<- rig Rig (open-named rig-name tmp-path request None))
  (<- ok (run-scenario rig resubmit-same-key))
  (assert ok))


(defk submit-then-wait [key seconds lease]
  {:pre [(: key str) (: seconds float) (: lease float)] :post [(: % DetachedSucceeded)]}
  (<- (SubmitDetached (slow-add seconds 3) :needs LOCAL :key key :lease-seconds lease))
  (<- outcome DetachedSucceeded (AwaitDetached key))
  outcome)

(defk task-longer-than-the-lease [rig]
  {:pre [(: rig Rig)] :post [(: % bool)]}
  ;; lease は担い手が延ばす: lease の 2 倍かかる task も、担い手が生きている限り消えない(呼び手の問い合わせは無くてよい)。
  (<- (SubmitDetached (slow-add (* rig.lease 2) 11) :needs LOCAL :key "k-long" :lease-seconds rig.lease))
  (<- outcome (AwaitDetached "k-long"))
  (assert (= outcome (DetachedSucceeded 111)) outcome)
  True)

(deftest test-the-runner-extends-the-lease-of-a-long-task [rig-name tmp-path request]
  {:params {"rig_name" RIGS}}
  (assert (isinstance rig-name str) rig-name)
  (<- rig Rig (open-named rig-name tmp-path request None))
  (<- ok (run-scenario rig task-longer-than-the-lease))
  (assert ok))


(defk caller-vanishes-and-reconnects [rig]
  {:pre [(: rig Rig)] :post [(: % bool)]}
  ;; 呼び手(送って待つ task)を取り消す = 呼び手が消えた。task は続き、別の呼び手が同じ key で結果を受け取る。
  (<- caller Task (Spawn (submit-then-wait "k-vanish" rig.slow rig.lease)))
  (<- (Delay (* rig.slow 0.3)))
  (<- (Cancel caller))
  (<- early DetachedPending (AwaitDetached "k-vanish" :timeout-seconds 0.0))
  (assert (= #(early.key early.phase) #("k-vanish" "assigned")) early)   ; runner は組ごとの担い手の名
  (<- (Delay rig.slow))
  (<- outcome (AwaitDetached "k-vanish"))
  (assert (= outcome (DetachedSucceeded 103)) outcome)
  True)

(deftest test-task-survives-the-caller-and-a-new-caller-reconnects [rig-name tmp-path request]
  {:params {"rig_name" RIGS}}
  (assert (isinstance rig-name str) rig-name)
  (<- rig Rig (open-named rig-name tmp-path request None))
  (<- ok (run-scenario rig caller-vanishes-and-reconnects))
  (assert ok))


(defk result-outlives-the-runner [rig]
  {:pre [(: rig Rig)] :post [(: % bool)]}
  (<- (SubmitDetached (slow-add 0.0 4) :needs LOCAL :key "k-kept" :lease-seconds rig.lease))
  (<- outcome (AwaitDetached "k-kept"))
  (assert (= outcome (DetachedSucceeded 104)) outcome)
  ;; 結果の後に担い手が死んでも、結果は保持する(lease が切れる時間を過ぎても)。
  (<- lost int (KillWorker RUNNER))
  (assert (= lost 0))
  (<- (Delay (* rig.lease 1.5)))
  (<- again (AwaitDetached "k-kept"))
  (assert (= again (DetachedSucceeded 104)) again)
  True)

(deftest test-result-is-kept-after-the-runner-dies [rig-name tmp-path request]
  {:params {"rig_name" RIGS}}
  (assert (isinstance rig-name str) rig-name)
  (<- rig Rig (open-named rig-name tmp-path request None))
  (<- ok (run-scenario rig result-outlives-the-runner))
  (assert ok))


(defk runner-dies-mid-run [rig]
  {:pre [(: rig Rig)] :post [(: % bool)]}
  (<- (SubmitDetached (slow-add (* rig.slow 10) 5) :needs LOCAL :key "k-lost" :lease-seconds rig.lease))
  (<- (Delay (* rig.slow 0.3)))
  (<- lost int (KillWorker RUNNER))
  (assert (= lost 1))
  (<- outcome (AwaitDetached "k-lost"))
  (assert (isinstance outcome DetachedLost) outcome)
  ;; 走らせ直さない(同じ key の送り直しは消えた行を返すだけ)。
  (<- again DetachedSubmitted (SubmitDetached (slow-add 0.0 5) :needs LOCAL :key "k-lost" :lease-seconds rig.lease))
  (assert (not again.created))
  (<- still (AwaitDetached "k-lost"))
  (assert (isinstance still DetachedLost) still)
  True)

(deftest test-runner-death-loses-the-task-without-rerunning [rig-name tmp-path request]
  {:params {"rig_name" RIGS}}
  (assert (isinstance rig-name str) rig-name)
  (<- rig Rig (open-named rig-name tmp-path request None))
  (<- ok (run-scenario rig runner-dies-mid-run))
  (assert ok))


(defk cancel-open-and-finished [rig]
  {:pre [(: rig Rig)] :post [(: % bool)]}
  (<- (SubmitDetached (slow-add (* rig.slow 10) 6) :needs LOCAL :key "k-cancel" :lease-seconds rig.lease))
  (<- (Delay (* rig.slow 0.3)))
  (<- cancelled bool (CancelDetached "k-cancel"))
  (assert cancelled)
  (<- outcome (AwaitDetached "k-cancel"))
  (assert (= outcome (DetachedCancelled)) outcome)
  (<- twice bool (CancelDetached "k-cancel"))
  (assert (not twice))
  ;; 終わった後の取り消しは何もしない(結果は保持)。
  (<- (SubmitDetached (slow-add 0.0 7) :needs LOCAL :key "k-done" :lease-seconds rig.lease))
  (<- done (AwaitDetached "k-done"))
  (assert (= done (DetachedSucceeded 107)) done)
  (<- late bool (CancelDetached "k-done"))
  (assert (not late))
  (<- kept (AwaitDetached "k-done"))
  (assert (= kept (DetachedSucceeded 107)) kept)
  (<- unknown bool (CancelDetached "k-never"))
  (assert (not unknown))
  True)

(deftest test-cancel-stops-an-open-task-and-keeps-a-finished-result [rig-name tmp-path request]
  {:params {"rig_name" RIGS}}
  (assert (isinstance rig-name str) rig-name)
  (<- rig Rig (open-named rig-name tmp-path request None))
  (<- ok (run-scenario rig cancel-open-and-finished))
  (assert ok))


(defk program-raises [rig]
  {:pre [(: rig Rig)] :post [(: % bool)]}
  (<- (SubmitDetached (boom) :needs LOCAL :key "k-boom" :lease-seconds rig.lease))
  (<- outcome (AwaitDetached "k-boom"))
  (assert (isinstance outcome DetachedFailed) outcome)
  (assert (= outcome.kind "ValueError"))
  (assert (= outcome.message "業務の失敗 base=100"))
  (assert (isinstance outcome.error ValueError))
  True)

(deftest test-program-exception-is-a-failed-outcome [rig-name tmp-path request]
  {:params {"rig_name" RIGS}}
  (assert (isinstance rig-name str) rig-name)
  (<- rig Rig (open-named rig-name tmp-path request None))
  (<- ok (run-scenario rig program-raises))
  (assert ok))


(defk unknown-and-release [rig]
  {:pre [(: rig Rig)] :post [(: % bool)]}
  (<- nothing (AwaitDetached "k-nope"))
  (assert (= nothing (DetachedUnknown "k-nope")))
  (<- (SubmitDetached (slow-add 0.0 8) :needs LOCAL :key "k-release" :lease-seconds rig.lease))
  (<- done (AwaitDetached "k-release"))
  (assert (= done (DetachedSucceeded 108)))
  (<- released bool (ReleaseDetached "k-release"))
  (assert released)
  (<- gone (AwaitDetached "k-release"))
  (assert (= gone (DetachedUnknown "k-release")))
  (<- again bool (ReleaseDetached "k-release"))
  (assert (not again))
  ;; 解放した key は送り直せる(新しい task)。
  (<- fresh DetachedSubmitted (SubmitDetached (slow-add 0.0 9) :needs LOCAL :key "k-release" :lease-seconds rig.lease))
  (assert fresh.created)
  (<- rerun (AwaitDetached "k-release"))
  (assert (= rerun (DetachedSucceeded 109)))
  ;; まだ終わっていない task は解放できない(先に取り消す)。
  (<- (SubmitDetached (slow-add (* rig.slow 10) 1) :needs LOCAL :key "k-open" :lease-seconds rig.lease))
  (var refused None)
  (try
    (<- (ReleaseDetached "k-open"))
    (except [error DetachedRefused] (:= refused error)))
  (assert (and refused (= refused.status 409)) refused)
  (<- (CancelDetached "k-open"))
  True)

(deftest test-unknown-key-and-release [rig-name tmp-path request]
  {:params {"rig_name" RIGS}}
  (assert (isinstance rig-name str) rig-name)
  (<- rig Rig (open-named rig-name tmp-path request None))
  (<- ok (run-scenario rig unknown-and-release))
  (assert ok))


(defk await-times-out [rig]
  {:pre [(: rig Rig)] :post [(: % bool)]}
  (<- (SubmitDetached (slow-add rig.slow 10) :needs LOCAL :key "k-timeout" :lease-seconds rig.lease))
  (<- pending (AwaitDetached "k-timeout" :timeout-seconds (* rig.slow 0.2)))
  (assert (isinstance pending DetachedPending) pending)
  (<- outcome (AwaitDetached "k-timeout"))
  (assert (= outcome (DetachedSucceeded 110)) outcome)
  True)

(deftest test-await-with-a-timeout-returns-pending [rig-name tmp-path request]
  {:params {"rig_name" RIGS}}
  (assert (isinstance rig-name str) rig-name)
  (<- rig Rig (open-named rig-name tmp-path request None))
  (<- ok (run-scenario rig await-times-out))
  (assert ok))


(defk versions-differ [rig]
  {:pre [(: rig Rig)] :post [(: % bool)]}
  (<- submitted DetachedSubmitted (SubmitDetached (slow-add 0.0 1) :needs LOCAL :key "k-version" :lease-seconds rig.lease))
  (assert submitted.created)
  (<- outcome (AwaitDetached "k-version"))
  (assert (isinstance outcome DetachedVersionMismatch) outcome)
  (assert (in "python" outcome.detail) outcome.detail)
  True)

(deftest test-version-mismatch-is-a-typed-outcome [rig-name tmp-path request]
  {:params {"rig_name" RIGS}}
  (assert (isinstance rig-name str) rig-name)
  (<- rig Rig (open-named rig-name tmp-path request OTHER-VERSIONS))
  (<- ok (run-scenario rig versions-differ))
  (assert ok))


(defk same-key-other-work [rig]
  {:pre [(: rig Rig)] :post [(: % bool)]}
  (<- (SubmitDetached (slow-add 0.0 1) :needs LOCAL :key "k-conflict" :name "a" :lease-seconds rig.lease))
  (var refused None)
  (try
    (<- (SubmitDetached (slow-add 0.0 1) :needs LOCAL :key "k-conflict" :name "b" :lease-seconds rig.lease))
    (except [error DetachedRefused] (:= refused error)))
  (assert (and refused (= refused.status 409)) refused)
  (<- outcome (AwaitDetached "k-conflict"))
  (assert (= outcome (DetachedSucceeded 101)))
  True)

(deftest test-same-key-for-other-work-is-refused [rig-name tmp-path request]
  {:params {"rig_name" RIGS}}
  (assert (isinstance rig-name str) rig-name)
  (<- rig Rig (open-named rig-name tmp-path request None))
  (<- ok (run-scenario rig same-key-other-work))
  (assert ok))


(defk same-key-other-needs [rig]
  {:pre [(: rig Rig)] :post [(: % bool)]}
  ;; 同じ key で要る能力(needs)だけが違う送り直しも別の仕事 — 409。
  (<- (SubmitDetached (slow-add (* rig.slow 10) 1) :key "k-needs" :needs LOCAL :lease-seconds rig.lease))
  (var refused None)
  (try
    (<- (SubmitDetached (slow-add 0.0 1) :key "k-needs" :needs (| LOCAL #{"gpu"}) :lease-seconds rig.lease))
    (except [error DetachedRefused] (:= refused error)))
  (assert (and refused (= refused.status 409)) refused)
  (<- (CancelDetached "k-needs"))
  True)

(deftest test-same-key-for-other-needs-is-refused [rig-name tmp-path request]
  {:params {"rig_name" RIGS}}
  (assert (isinstance rig-name str) rig-name)
  (<- rig Rig (open-named rig-name tmp-path request None))
  (<- ok (run-scenario rig same-key-other-needs))
  (assert ok))


(defk effect-refusal [make]
  {:pre [(: make Callable)] :post [(: % str)]}
  "effect を作って TypeError の文を返す(作れてしまえば AssertionError)。"
  (try
    (make)
    (except [error TypeError]
      (return (str error))))
  (raise (AssertionError "needs の誤りを作る時に断らなかった")))


(deftest test-the-three-effects-refuse-empty-or-old-needs
  ;; SubmitDetached・RemoteJob・WarmRuntimeEnv は needs を能力の名の空でない frozenset でだけ作れる(ADR-DOE-CLUSTER-001 R4b)。
  ;; 空(書き忘れ)・旧い Requirement の組の tuple・label の形の名は、送る前の作る時点で TypeError。
  (import doeff_cluster.shared.intent.remote_model [RemoteJob])
  (import doeff_cluster.shared.intent.warm_model [WarmRuntimeEnv])
  (import tests.env_fixtures [LOCK env-of])
  (import doeff_cluster.shared.intent.runtime_env_model [RuntimeEnv])
  (<- env RuntimeEnv (env-of "app-1" "lib-1" LOCK))
  (val makers {"SubmitDetached" (fn [needs] (SubmitDetached (slow-add 0.0 1) :key "k" :needs needs))
               "RemoteJob" (fn [needs] (RemoteJob (slow-add 0.0 1) :needs needs))
               "WarmRuntimeEnv" (fn [needs] (WarmRuntimeEnv env needs 60.0 "tests"))})
  (for [#(name make) (.items makers)]
    (<- empty (effect-refusal (fn [] (make (frozenset)))))
    (assert (and (in name empty) (in "空" empty)) empty)
    ;; 旧い形: Requirement の (label value) の組の tuple。
    (<- old (effect-refusal (fn [] (make #(#("kind" "k3s"))))))
    (assert (in "frozenset" old) old)
    (<- label (effect-refusal (fn [] (make (frozenset ["kind=k3s"])))))
    (assert (in "kind=k3s" label) label)
    (assert (= (. (make (frozenset ["cluster-net"])) needs) (frozenset ["cluster-net"]))))
  ;; 書き忘れ(既定の空)も断る。
  (<- missing (effect-refusal (fn [] (SubmitDetached (slow-add 0.0 1) :key "k"))))
  (assert (in "空" missing) missing)
  (<- missing-remote (effect-refusal (fn [] (RemoteJob (slow-add 0.0 1)))))
  (assert (in "空" missing-remote) missing-remote))


;; --- coordinator の判断(純粋な関数)と worker の途絶 -------------------------------------------------------------

(import doeff_cluster.coordinator.core.durable_kv [full-kv state-from-kv])
(import doeff_cluster.coordinator.core.cluster_policy [state-to-json state-from-json])
(import doeff_cluster.coordinator.entry.main [load-state])
(import doeff_cluster.foundation.wal_store [WalStore])
(import doeff_cluster.shared.intent.job_model [JobSpec])
(import doeff_cluster.worker.core.policy [kept-when-cut-off])
(import doeff_cluster.handlers [task-spec])

(setv T (ClusterTiming) V {"python" "3.14.0" "doeff" "1"})

(defn #^ tuple call [#^ ClusterState state #^ str method #^ str path #^ int now #^ (| dict None) [body None]]
  (respond state (http-request method path {} body :actor "test") now T))

(defn #^ tuple beat [#^ ClusterState state #^ str name #^ int now #^ str [boot "b1"] #^ (| list None) [statuses None]
           #^ (| int None) [boot-at None] #^ (| list None) [provides None]]
  (call state "POST" "/heartbeat" now (| {"name" name "provides" (or provides ["net"]) "capacity" 10 "versions" V "boot" boot
                                          "statuses" (or statuses [])}
                                         (if (is boot-at None) {} {"bootAt" boot-at}))))

(defn #^ tuple put-detached [#^ ClusterState state #^ str key #^ int now #^ float [lease 10.0] #^ float [retain 100.0]]
  ;; 詰めた Program を置き場に(版 V と一緒に)置いてから、本文は置き場のキーだけを運ぶ(service の宣言と同じ運び方)。
  (setv #(state sha) (run (program-placed state V :now now)))
  (call state "PUT" (+ "/detached/" key) now {"program" sha "revision" "r" "needs" ["net"]
                                              "leaseSeconds" lease "retainSeconds" retain}))

;; 読みの時刻は coordinator が起きてからの猶予(lease-ms)の後(猶予の内の知らない key は warming — detached_policy.detached-read)。
(defn #^ str phase-of [#^ ClusterState state #^ str key] (get (get (call state "GET" (+ "/detached/" key) (+ state.started-ms T.lease-ms)) 2) "phase"))


(deftest test-detached-task-goes-to-the-worker-with-its-boot-and-a-detached-flag
  (val reply-1 (beat (ClusterState) "w" 0))
  (var s (get reply-1 0))
  (val reply-2 (put-detached s "job-1" 100))
  (:= s (get reply-2 0))
  (val status (get reply-2 1))
  (val reply (get reply-2 2))
  (assert (= #(status (get reply "created")) #(200 True)))
  (setv task (get s.tasks (get reply "task")))
  (assert (= #(task.phase task.worker task.boot) #("assigned" "w" "b1")))
  (val reply-3 (beat s "w" 200))
  (:= s (get reply-3 0))
  (val body (get reply-3 2))
  (setv #(row) (get body "tasks"))
  (assert (get row "detached"))
  ;; worker の側: 途絶で止めない task の宣言になる
  (assert (. (task-spec row (Path "/tmp")) detached)))


(deftest test-caller-reads-do-not-extend-the-lease-but-worker-heartbeats-do
  (val reply-4 (beat (ClusterState) "w" 0))
  (var s (get reply-4 0))
  (val reply-5 (put-detached s "job-2" 0 :lease 10.0))
  (:= s (get reply-5 0))
  (val reply (get reply-5 2))
  (setv id (get reply "task"))
  (val reply-6 (beat s "w" 9000))
  (:= s (get reply-6 0))                     ; 担い手の heartbeat が lease を 19000 まで延ばす
  (val reply-7 (call s "GET" "/detached/job-2" 15000))
  (:= s (get reply-7 0))   ; 呼び手の読みは lease に触らない
  (:= s (tick s 18000 T))
  (assert (= (. (get s.tasks id) phase) "assigned"))
  (:= s (tick s 19001 T))                            ; 担い手が沈黙した = worker の死
  (assert (= (. (get s.tasks id) phase) "lost"))
  (assert (in "lease" (. (get s.tasks id) detail))))


(deftest test-a-restarted-worker-process-does-not-rerun-its-detached-tasks-and-they-are-lost-by-the-lease
  ;; 2026-09-27までは新しい世代の heartbeat が来た拍に lost にしていた。旧い世代がまだ動いている(Pod の
  ;; preStop の drain の間)こともあるので、旧い世代の task は旧い世代の heartbeat だけが延ばし、沈黙したら lease 切れで lost。
  (val reply-8 (beat (ClusterState) "w" 0))
  (var s (get reply-8 0))
  (val reply-9 (put-detached s "job-3" 0 :lease 10.0))
  (:= s (get reply-9 0))
  (val reply (get reply-9 2))
  (setv id (get reply "task"))
  ;; 同じ名の worker が別の process の世代で名乗る(結果の報告を持っていても、前の世代の物としては受け取らない・走らせ直さない)
  (val reply-10 (beat s "w" 1000 :boot "b2" :statuses [{"name" (+ "task/" id) "phase" "finished" "result" "R"}]))
  (:= s (get reply-10 0))
  (val body (get reply-10 2))
  (assert (= (get body "tasks") []))
  (assert (= (. (get s.tasks id) phase) "assigned"))
  ;; 新しい世代の heartbeat は旧い世代の task の lease を延ばさない。旧い世代が戻らなければ lease 切れ(10 秒)で lost。
  (val reply-11 (beat s "w" 9000 :boot "b2"))
  (:= s (get reply-11 0))
  (:= s (tick s 10001 T))
  (assert (= (. (get s.tasks id) phase) "lost"))
  (assert (in "lease" (. (get s.tasks id) detail))))


;; --- 同じ名の 2 つの世代(2026-09-27 04:44 JST の実測) ------------------------------------
;; worker の Pod を消すと、旧 Pod は preStop の drain(約 40 秒)の間も worker の process を動かし、新 Pod の worker は同じ名で名乗る。
;; 2 つの process が交互に heartbeat を送る。coordinator は初めて見た世代を新しい世代とし、退いた世代の heartbeat を断る。

(defn #^ tuple alternate [#^ ClusterState s #^ str name #^ int now #^ list boots #^ (| dict None) [statuses None]]
  "boots の順に 50 ms おきに heartbeat を送る。返り値 #(状態 最後の時刻 世代 → 最後の返事)。"
  (setv replies {})
  (for [boot boots]
    (+= now 50)
    (setv #(s _ body) (beat s name now :boot boot :statuses (.get (or statuses {}) boot [])))
    (setv (get replies boot) body))
  #(s now replies))


(deftest test-an-old-generation-heartbeat-does-not-lose-a-task-placed-on-the-new-generation
  (val reply-12 (beat (ClusterState) "w" 0 :boot "old"))
  (var s (get reply-12 0))
  ;; 新 Pod の worker が 11 秒後に同じ名で名乗る。以後の task は新しい世代に置く。
  (val reply-13 (beat s "w" 11000 :boot "new"))
  (:= s (get reply-13 0))
  (val reply-14 (put-detached s "job-25" 11010))
  (:= s (get reply-14 0))
  (val reply (get reply-14 2))
  (setv id (get reply "task"))
  (assert (= (. (get s.tasks id) boot) "new"))
  ;; 旧 Pod の drain の間、2 つの世代が交互に heartbeat を送る(実測は 2 秒ごと・ここは 50 ms ごと)。
  (val reply-15 (alternate s "w" 11010 ["old" "new" "old" "new" "old"]))
  (:= s (get reply-15 0))
  (val now (get reply-15 1))
  (val replies (get reply-15 2))
  (setv task (get s.tasks id))
  (assert (= #(task.phase task.boot) #("assigned" "new")) task.detail)
  ;; 新しい世代の返事にだけ載る(旧い世代は走らせない)。coordinator の見る世代は新しい世代のまま。
  (assert (= (lfor t (get replies "new" "tasks") (get t "id")) [id]))
  (assert (= (get replies "old" "tasks") []))
  (assert (get replies "old" "superseded"))
  (assert (= (. (get s.workers "w") boot) "new"))
  ;; 旧い世代の heartbeat は新しい世代の task の lease を延ばさず、新しい世代の heartbeat が延ばす。
  (val reply-16 (beat s "w" (+ now 50) :boot "new"))
  (:= s (get reply-16 0))
  (assert (= (. (get s.tasks id) lease-until-ms) (+ now 50 (. (get s.tasks id) lease-ms)))))


(deftest test-a-task-on-the-old-generation-keeps-its-lease-and-result-while-the-old-generation-lives
  (val reply-17 (beat (ClusterState) "w" 0 :boot "old"))
  (var s (get reply-17 0))
  (val reply-18 (put-detached s "job-24" 0 :lease 10.0))
  (:= s (get reply-18 0))
  (var reply (get reply-18 2))
  (setv done (get reply "task"))
  (val reply-19 (put-detached s "job-23" 0 :lease 10.0))
  (:= s (get reply-19 0))
  (:= reply (get reply-19 2))
  (setv silent (get reply "task"))
  (val reply-20 (beat s "w" 6000 :boot "new"))
  (:= s (get reply-20 0))
  ;; 新しい世代が来ても、旧い世代に置いた task は lost にしない(旧い世代は drain の間まだ走らせている)。
  (assert (= (. (get s.tasks done) phase) "assigned"))
  ;; 旧い世代の heartbeat は自分の世代の task の lease を延ばし、その task だけを返事に載せる。
  (val reply-21 (beat s "w" 9000 :boot "old"))
  (:= s (get reply-21 0))
  (val body (get reply-21 2))
  (assert (= (sorted (lfor t (get body "tasks") (get t "id"))) (sorted [done silent])))
  ;; 旧い世代の終わりの報告は受ける(結果を捨てない)。
  (val reply-22 (beat s "w" 12000 :boot "old"
                       :statuses [{"name" (+ "task/" done) "phase" "finished" "result" "R" "detail" ""}]))
  (:= s (get reply-22 0))
  (assert (= #((. (get s.tasks done) phase) (. (get s.tasks done) result)) #("finished" "R")))
  ;; 旧い世代が消えた(heartbeat が止まった)= lease 切れで lost。新しい世代の heartbeat は延ばさない。
  (val reply-23 (beat s "w" 20000 :boot "new"))
  (:= s (get reply-23 0))
  (:= s (tick s 22001 T))
  (assert (= (. (get s.tasks silent) phase) "lost"))
  (assert (in "lease" (. (get s.tasks silent) detail)))
  ;; 新しい世代の生存はそのまま(旧い世代の沈黙は worker の沈黙ではない)。
  (assert (= (. (get s.workers "w") boot) "new")))


(deftest test-the-generation-order-survives-a-coordinator-restart
  (val reply-24 (beat (ClusterState) "w" 0 :boot "old"))
  (var s (get reply-24 0))
  (val reply-25 (beat s "w" 1000 :boot "new"))
  (:= s (get reply-25 0))
  (var again (state-from-kv (full-kv s) 2000))
  (assert (= #((. (get again.workers "w") boot) (. (get again.workers "w") retired)) #("new" #("old"))))
  ;; 読み直した後も、旧い世代の heartbeat は新しい世代を押しのけない。
  (val reply-26 (beat again "w" 2100 :boot "old"))
  (:= again (get reply-26 0))
  (val body (get reply-26 2))
  (assert (get body "superseded"))
  (assert (= (. (get again.workers "w") boot) "new")))


;; --- 起動時刻で決める世代の新旧(2026-09-27 — #757)------------------------------------------------
;; 初めて見た順だけでは、置き場を失った coordinator に新しい世代が先に届くと、後から来た古い世代が今の世代になり、古い世代が
;; 止んだ後も新しい世代の heartbeat を断り続けて名が沈黙した。worker は heartbeat に process の起動時刻 bootAt を載せる。

(deftest test-an-empty-coordinator-that-hears-the-new-generation-first-keeps-the-new-generation
  (val reply-27 (beat (ClusterState) "w" 0 :boot "new" :boot-at 2000))
  (var s (get reply-27 0))
  (val reply-28 (beat s "w" 50 :boot "old" :boot-at 1000))
  (:= s (get reply-28 0))
  (val old-reply (get reply-28 2))
  ;; 古い世代の heartbeat は superseded の答え・今の世代は新しい世代のまま。
  (assert (get old-reply "superseded") old-reply)
  (assert (= (. (get s.workers "w") boot) "new") (get s.workers "w"))
  (assert (in "old" (. (get s.workers "w") retired)))
  ;; 古い世代の preStop の drain は今の世代に付かない。
  (val reply-29 (call s "POST" "/workers/w/drain" 100 {"boot" "old"}))
  (:= s (get reply-29 0))
  (var view (get reply-29 2))
  (assert (not-in "w" s.drains) s.drains)
  (assert (get view "drain" "superseded") view)
  ;; 古い世代が止み、新しい世代だけが 5 秒ごとに heartbeat を送る → 60 秒後も新しい世代が生きていて ready。
  (for [t (range 5000 65000 5000)]
    (val beaten (beat s "w" t :boot "new" :boot-at 2000))
    (:= s (get beaten 0))
    (val reply (get beaten 2))
    (assert (not (.get reply "superseded" False)) #(t reply)))
  (:= s (tick s 65000 T))
  (val reply-30 (call s "GET" "/workers/w" 65000))
  (:= view (get reply-30 2))
  (assert (= #((get view "alive") (get view "ready") (get view "boot")) #(True True "new")) view))


(deftest test-workers-that-do-not-name-a-boot-time-keep-the-first-seen-order
  ;; 起動時刻を名乗らない旧い worker は今までどおり(初めて見た順: 見ていない世代が新しい)。
  (val reply-31 (beat (ClusterState) "w" 0 :boot "a"))
  (var s (get reply-31 0))
  (val reply-32 (beat s "w" 50 :boot "b"))
  (:= s (get reply-32 0))
  (var reply (get reply-32 2))
  (assert (not (.get reply "superseded" False)))
  (assert (= #((. (get s.workers "w") boot) (. (get s.workers "w") retired)) #("b" #("a"))))
  (val reply-33 (beat s "w" 100 :boot "a"))
  (:= s (get reply-33 0))
  (:= reply (get reply-33 2))
  (assert (get reply "superseded"))
  ;; 片方だけが起動時刻を名乗る時も初めて見た順。
  (val reply-34 (beat s "v" 0 :boot "a" :boot-at 2000))
  (:= s (get reply-34 0))
  (val reply-35 (beat s "v" 50 :boot "b"))
  (:= s (get reply-35 0))
  (assert (= (. (get s.workers "v") boot) "b")))


(deftest test-a-newer-boot-time-takes-the-name-back-from-the-retired-list
  ;; 旧い coordinator が初めて見た順で退かせた世代でも、両方の起動時刻を知れば起動時刻の大きい方が今の世代。
  (val reply-36 (beat (ClusterState) "w" 0 :boot "new"))
  (var s (get reply-36 0))
  (val reply-37 (beat s "w" 50 :boot "old" :boot-at 1000))
  (:= s (get reply-37 0))
  (assert (= #((. (get s.workers "w") boot) (. (get s.workers "w") retired)) #("old" #("new"))))
  (val reply-38 (beat s "w" 100 :boot "new" :boot-at 2000))
  (:= s (get reply-38 0))
  (val reply (get reply-38 2))
  (assert (not (.get reply "superseded" False)) reply)
  (assert (= #((. (get s.workers "w") boot) (. (get s.workers "w") retired)) #("new" #("old")))))


(deftest test-the-boot-time-survives-the-state-file-and-the-durable-kv
  (val reply-39 (beat (ClusterState) "w" 0 :boot "old" :boot-at 1000))
  (var s (get reply-39 0))
  (val reply-40 (beat s "w" 100 :boot "new" :boot-at 2000))
  (:= s (get reply-40 0))
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
;; worker は返事に載らない task の子 process を止め、結果の file と Program の cache を消す。置き場を失った coordinator は task の行を持たないので、
;; 以前は最初の返事で生きている worker の走っている切り離した task を全部止めさせた。worker は状態の報告に置かれた時の行を写し、
;; coordinator はそれを引き取る。

(deftest test-an-empty-coordinator-adopts-the-running-detached-task-a-worker-reports [tmp-path]
  (val reply-41 (beat (ClusterState) "w" 0 :boot-at 1000))
  (var s (get reply-41 0))
  (val reply-42 (put-detached s "job-amnesia" 0 :lease 10.0 :retain 100.0))
  (:= s (get reply-42 0))
  (val reply (get reply-42 2))
  (setv id (get reply "task"))
  (val reply-43 (beat s "w" 100 :boot-at 1000))
  (:= s (get reply-43 0))
  (var body (get reply-43 2))
  (setv link (LinkRig "http://127.0.0.1:9" "w" #() 10 60000 :task-dir (str (/ tmp-path "tasks"))))
  (setv #(before) (.accept-tasks link (get body "tasks")))
  (setv rows (.report link #((JobStatus (+ "task/" id) JobPhase.RUNNING "r" "r" 42 1))))
  ;; 置き場を失った coordinator が起きる: 走っている task を同じ行で引き取り、同じ heartbeat の返事に載せる。
  (val reply-44 (beat (ClusterState) "w" 5000 :boot-at 1000 :statuses rows))
  (var fresh (get reply-44 0))
  (:= body (get reply-44 2))
  (assert (= (lfor t (get body "tasks") (get t "id")) [id]) body)
  (setv #(after) (.accept-tasks link (get body "tasks")))
  (assert (= after before) "引き取った行の宣言の spec が変わった(worker は子 process を止める)")
  ;; 引き取った行は同じ置き場のキーを運ぶ(担い手の cache の Program を使い続ける — 状態を失った置き場に Program が無くてもよい)。
  (assert (= (get body "tasks" 0 "program") after.program SAMPLE-TASK-PROGRAM) body)
  (assert (.exists (/ tmp-path "tasks" (+ id ".program"))) "走っている task の Program の印が消えた")
  (assert (= (phase-of fresh "job-amnesia") "assigned"))
  ;; 終わりの報告は呼び手の key で読める。
  (val reply-45 (beat fresh "w" 6000 :boot-at 1000
                           :statuses [{"name" (+ "task/" id) "phase" "finished" "result" "R" "detail" ""}]))
  (:= fresh (get reply-45 0))
  (setv #(_ _ view) (call fresh "GET" "/detached/job-amnesia" 6000))
  (assert (= #((get view "phase") (get view "result")) #("finished" "R")) view)
  ;; 次に振る id は引き取った id と重ならない。
  (val reply-46 (put-detached fresh "job-next" 6100))
  (:= fresh (get reply-46 0))
  (val other (get reply-46 2))
  (assert (!= (get other "task") id))
  ;; 行を持つ task(取り消した)は引き取らない — 取り消し・lost は今までどおり止める。
  (val reply-47 (call s "POST" "/detached/job-amnesia/cancel" 200))
  (:= s (get reply-47 0))
  (val reply-48 (beat s "w" 300 :boot-at 1000 :statuses rows))
  (:= s (get reply-48 0))
  (:= body (get reply-48 2))
  (assert (= (get body "tasks") []) body)
  ;; 写しの無い報告(旧い worker)は引き取らない。
  (val reply-49 (beat (ClusterState) "w" 5000 :statuses [{"name" (+ "task/" id) "phase" "running"}]))
  (:= body (get reply-49 2))
  (assert (= (get body "tasks") []) body))


;; 直すべき所(構成レビュー 2026-09-27): 起きた直後の読み・needs・終わった報告・id の振り直し・欠けた写し。

(defn #^ tuple placed-echo [#^ Path tmp-path #^ (| list None) [needs None]]
  "もとの coordinator が task を置き、worker が受けて状態の報告に写しを添えるまで。返り値 #(もとの状態 id 報告を作る link 元の spec)。"
  (setv caps (or needs ["net"]))
  (setv #(s _ _) (beat (ClusterState) "w" 0 :boot-at 1000 :provides caps))
  (setv #(s sha) (run (program-placed s V)))
  (setv #(s _ reply) (call s "PUT" "/detached/job-e" 0 {"program" sha "revision" "r" "needs" caps
                                                        "leaseSeconds" 10.0 "retainSeconds" 100.0}))
  (setv id (get reply "task"))
  (setv #(s _ body) (beat s "w" 100 :boot-at 1000 :provides caps))
  (setv link (LinkRig "http://127.0.0.1:9" "w" #() 10 60000 :task-dir (str (/ tmp-path "tasks"))))
  (setv #(spec) (.accept-tasks link (get body "tasks")))
  #(s id link spec))


(deftest test-a-just-started-coordinator-does-not-call-a-key-unknown-before-the-workers-report [tmp-path]
  (setv #(_ id link _) (placed-echo tmp-path))
  (setv rows (.report link #((JobStatus (+ "task/" id) JobPhase.RUNNING "r" "r" 42 1))))
  (var fresh (ClusterState :started-ms 5000))
  ;; worker の最初の heartbeat より先に呼び手の読みが届く: 知らないと言わない(503・warming)。
  (val reply-50 (call fresh "GET" "/detached/job-e" 5000))
  (:= fresh (get reply-50 0))
  (var status (get reply-50 1))
  (var view (get reply-50 2))
  (assert (= #(status (get view "phase")) #(503 "warming")) #(status view))
  (val reply-51 (beat fresh "w" 5100 :boot-at 1000 :statuses rows))
  (:= fresh (get reply-51 0))
  (val reply-52 (call fresh "GET" "/detached/job-e" 5200))
  (:= fresh (get reply-52 0))
  (:= status (get reply-52 1))
  (:= view (get reply-52 2))
  (assert (= #(status (get view "phase") (get view "task")) #(200 "assigned" id)) view)
  ;; 猶予(lease-ms)を過ぎた後の本当に知らない key は unknown。
  (val reply-53 (call fresh "GET" "/detached/never" (+ 5000 T.lease-ms)))
  (:= status (get reply-53 1))
  (:= view (get reply-53 2))
  (assert (= #(status (get view "phase")) #(200 "unknown")) view))


(deftest test-an-adopted-task-keeps-its-needs [tmp-path]
  (setv #(s id link spec) (placed-echo tmp-path ["cluster-net"]))
  (setv rows (.report link #((JobStatus (+ "task/" id) JobPhase.RUNNING "r" "r" 42 1))))
  (setv #(fresh _ _) (beat (ClusterState) "w" 5000 :boot-at 1000 :statuses rows :provides ["cluster-net"]))
  (assert (= (. (get fresh.tasks id) needs) (. (get s.tasks id) needs) #("cluster-net"))))


(deftest test-an-empty-coordinator-adopts-a-finished-task-with-its-result [tmp-path]
  ;; worker は返事に無い task の結果の file を消す — 終わった報告も引き取らないと結果を失い、呼び手は完走した仕事を送り直す。
  (setv #(_ id link _) (placed-echo tmp-path))
  (setv #(row) (.report link #((JobStatus (+ "task/" id) JobPhase.FINISHED "r" "r" None 1))))
  (val reply-54 (beat (ClusterState) "w" 5000 :boot-at 1000 :statuses [(| row {"result" "R" "detail" ""})]))
  (var fresh (get reply-54 0))
  (setv #(_ _ view) (call fresh "GET" "/detached/job-e" 5000))
  (assert (= #((get view "phase") (get view "result")) #("finished" "R")) view)
  ;; code-failed も同じ(終わりの理由が呼び手に届く)。
  (val reply-55 (beat (ClusterState) "w" 5000 :boot-at 1000
                           :statuses [(| row {"phase" "code-failed" "detail" "boom"})]))
  (:= fresh (get reply-55 0))
  (assert (= (. (get fresh.tasks id) phase) "code-failed")))


(deftest test-a-coordinator-that-starts-without-a-store-does-not-reuse-task-ids [tmp-path]
  ;; 置き場の無いところから起きた coordinator が t1 から振り直すと、worker に残る前の t1 の blob で新しい t1 が走った
  ;; (以前の accept-tasks は blob の file が在れば書き直さなかった)。起動ごとに違う頭を振る。
  (setv #(_ id link _) (placed-echo tmp-path))
  (var fresh (load-state (str (/ tmp-path "state.json")) (WalStore (str (/ tmp-path "wal"))) 123456))
  (val reply-56 (beat fresh "other" 123500))
  (:= fresh (get reply-56 0))
  (val reply-57 (run (program-placed fresh V "TkVX" (+ 123500 T.lease-ms))))
  (:= fresh (get reply-57 0))
  (val sha (get reply-57 1))
  (val reply-58 (call fresh "PUT" "/detached/job-new" (+ 123500 T.lease-ms)
                               {"program" sha "revision" "r" "needs" ["net"] "leaseSeconds" 10.0}))
  (:= fresh (get reply-58 0))
  (val reply (get reply-58 2))
  (assert (!= (get reply "task") id) #(reply id))
  (assert (= fresh.task-prefix "t1e240-") fresh.task-prefix)
  ;; 頭は保存と読み直しで戻る(state JSON と durable kv)。
  (assert (= (. (state-from-kv (full-kv fresh) 0) task-prefix) "t1e240-"))
  (assert (= (. (state-from-json (json.loads (json.dumps (state-to-json fresh))) 0) task-prefix) "t1e240-"))
  ;; 以前からの置き場(頭の欄が無い)は今までどおり t<番号>。
  (assert (= (. (state-from-kv (full-kv (ClusterState)) 0) task-prefix) "t")))


(deftest test-an-echo-without-revision-is-not-adopted-and-the-heartbeat-is-answered [tmp-path]
  (setv #(_ id link _) (placed-echo tmp-path))
  (setv #(row) (.report link #((JobStatus (+ "task/" id) JobPhase.RUNNING "r" "r" 42 1))))
  (setv broken (| row {"task" (dfor #(k v) (.items (get row "task")) :if (not-in k #("revision")) k v)}))
  (setv #(fresh status body) (beat (ClusterState) "w" 5000 :statuses [broken]))
  (assert (= #(status (get body "tasks")) #(200 [])) #(status body))
  (assert (not-in id fresh.tasks)))


(defk amnesia-scenario [coordinator]
  {:pre [(: coordinator MemoryCoordinator)] :post [(: % bool)]}
  (<- (SubmitDetached (slow-add 3.0 1) :needs LOCAL :key "k-amnesia" :lease-seconds 5.0))
  (<- (Delay 1.0))
  ;; coordinator が置き場を失って起き直す(task の行も worker の名乗りも無い)。呼び手はすぐ読む — worker の最初の heartbeat より
  ;; 先に届く読みも「知らない」と答えない(送り直させて並走させない)。
  (setv coordinator.state (ClusterState :started-ms (clock-ms coordinator.clock)))
  (<- outcome (AwaitDetached "k-amnesia"))
  (assert (= outcome (DetachedSucceeded 101)) outcome)
  True)


(deftest test-an-amnesic-coordinator-does-not-stop-the-running-detached-task [tmp-path]
  ;; 本物の coordinator への口 と本物の coordinator の判断で: 置き場を失った coordinator が起きても、担い手の worker は走っている
  ;; 切り離した task を止めず、呼び手は同じ key で結果を受け取る。
  (setv clock (SimClock)
        coordinator (MemoryCoordinator clock)
        transport (httpx.MockTransport coordinator.handle)
        worker (RigWorker "http://coordinator" (/ tmp-path "tasks") (current-versions) :transport transport)
        sender (detached-sender "r")
        rig (Rig "coordinator" [(sim-time-handler :clock clock) (transport-http transport) (rig-runner-loss worker)
                                (detached-cluster (route-cell) TEST-ROUTE sender :poll-seconds 0.5)]
                 worker 3.0 5.0 0.5))
  (<- ok (run-on rig (amnesia-scenario coordinator)))
  (assert ok))


(deftest test-result-is-kept-after-the-worker-dies-until-release-or-retention
  (val reply-59 (beat (ClusterState) "w" 0))
  (var s (get reply-59 0))
  (val reply-60 (put-detached s "job-4" 0 :lease 5.0 :retain 100.0))
  (:= s (get reply-60 0))
  (val reply (get reply-60 2))
  (setv id (get reply "task"))
  (val reply-61 (beat s "w" 1000 :statuses [{"name" (+ "task/" id) "phase" "finished" "result" "R" "detail" ""}]))
  (:= s (get reply-61 0))
  (assert (= (. (get s.tasks id) phase) "finished"))
  ;; 終わった行も置き場のキーを持つ(結果の保持の間は置き場の Program を参照し続け、行が消えたら掃除される)。
  (assert (= (. (get s.tasks id) program) SAMPLE-TASK-PROGRAM))
  ;; 担い手が死んで lease の時間が過ぎても、結果はそのまま
  (:= s (tick s 60000 T))
  (setv #(_ _ view) (call s "GET" "/detached/job-4" 60000))
  (assert (= #((get view "phase") (get view "result")) #("finished" "R")))
  ;; 保持の期限(終わった時刻 1000 + 100 秒)を過ぎたら消える
  (:= s (tick s 101001 T))
  (assert (= (phase-of s "job-4") "unknown")))


(deftest test-detached-records-survive-a-coordinator-restart
  (val reply-62 (beat (ClusterState) "w" 0))
  (var s (get reply-62 0))
  (val reply-63 (put-detached s "job-5" 0))
  (:= s (get reply-63 0))
  (val reply-64 (put-detached s "job-6" 0))
  (:= s (get reply-64 0))
  (var reply (get reply-64 2))
  (val reply-65 (beat s "w" 1000 :statuses [{"name" (+ "task/" (get reply "task")) "phase" "finished" "result" "R" "detail" ""}]))
  (:= s (get reply-65 0))
  (setv again (state-from-kv (full-kv s) 2000))
  (assert (= again.tasks s.tasks))
  (setv #(_ _ view) (call again "GET" "/detached/job-6" 2000))
  (assert (= (get view "result") "R"))
  ;; 読み直した後の送り直しも同じ行
  (val reply-66 (put-detached again "job-5" 2000))
  (:= reply (get reply-66 2))
  (assert (not (get reply "created"))))


(deftest test-drain-waits-until-the-detached-tasks-on-the-worker-are-done
  (val reply-67 (beat (ClusterState) "w" 0))
  (var s (get reply-67 0))
  (val reply-68 (put-detached s "job-7" 0))
  (:= s (get reply-68 0))
  (val reply (get reply-68 2))
  (setv id (get reply "task"))
  (val reply-69 (call s "POST" "/workers/w/drain" 100 {}))
  (:= s (get reply-69 0))
  (var view (get reply-69 2))
  (assert (= (get view "drain" "remaining") [(+ "task/" id)]))
  (assert (not (get view "drain" "drained")))
  ;; drain 中の worker には新しい task を置かない(置ける先が他に無ければ待つ)
  (val reply-70 (put-detached s "job-8" 200))
  (:= s (get reply-70 0))
  (val other (get reply-70 2))
  (assert (= (. (get s.tasks (get other "task")) phase) "queued"))
  (val reply-71 (beat s "w" 300 :statuses [{"name" (+ "task/" id) "phase" "finished" "result" "R" "detail" ""}]))
  (:= s (get reply-71 0))
  (val reply-72 (call s "GET" "/workers/w" 400))
  (:= s (get reply-72 0))
  (:= view (get reply-72 2))
  (assert (get view "drain" "drained")))


(deftest test-remote-job-tasks-keep-their-caller-bound-lifetime
  ;; RemoteJob の task(/tasks)は今までどおり: 呼び手の問い合わせが lease を延ばし、drain は数えず、途絶で止める。
  (val reply-73 (beat (ClusterState) "w" 0))
  (var s (get reply-73 0))
  (val reply-74 (run (program-placed s V)))
  (:= s (get reply-74 0))
  (val sha (get reply-74 1))
  (val reply-75 (call s "POST" "/tasks" 0 {"program" sha "revision" "r" "needs" ["net"]
                                               "name" "n" "leaseSeconds" 5.0}))
  (:= s (get reply-75 0))
  (var body (get reply-75 2))
  (setv id (get body "task"))
  (val reply-76 (beat s "w" 100))
  (:= s (get reply-76 0))
  (:= body (get reply-76 2))
  (assert (not-in "detached" (get body "tasks" 0)))
  (val reply-77 (call s "POST" "/workers/w/drain" 100 {}))
  (:= s (get reply-77 0))
  (val view (get reply-77 2))
  (assert (get view "drain" "drained"))
  (val reply-78 (beat s "w" 5200))
  (:= s (get reply-78 0))                   ; heartbeat は RemoteJob の lease を延ばさない
  (assert (not-in id s.tasks)))


(deftest test-a-cut-off-worker-keeps-detached-tasks-and-stops-remote-job-tasks
  (setv detached (JobSpec "task/t1" "doeff_cluster.job_entry" #() "r" :once True :detached True)
        remote (JobSpec "task/t2" "doeff_cluster.job_entry" #() "r" :once True)
        writer (JobSpec "svc" "doeff_cluster.job_entry" #() "r" :handoff True)
        plain (JobSpec "plain" "doeff_cluster.job_entry" #() "r"))
  (assert (= (kept-when-cut-off #(detached remote writer plain)) #(detached writer)))
  ;; coordinator への口: 途絶が fence を越えたら、最後に受け取った宣言のうち切り離した task を動かし続ける
  (setv link (LinkRig "http://127.0.0.1:9" "w" #() 1 60000))
  (setv link.state.last-tasks #(detached remote) link.state.fence-ms 0 link.state.last-ok-ms (- (int (* 1000 (time.time))) (int (* 1000 1))))
  (assert (= (.poll link) (DesiredJobs #(detached)))))


(deftest test-detached-task-keeps-typed-needs-and-versions-through-the-saved-state
  ;; 口の答えは Reply(状態・status・本文)。task の行は needs を能力の名の名の順の tuple・版を ComponentVersion で持ち、保存と読み直しの
  ;; 後も同じ型。
  (val reply-79 (beat (ClusterState) "w" 0))
  (var s (get reply-79 0))
  ;; 版は置き場に Program と一緒に置いた版(本文は版の写しを運ばない)。
  (val reply-80 (run (program-placed s V)))
  (:= s (get reply-80 0))
  (val sha (get reply-80 1))
  (setv reply (submit-detached s "job-typed" {"program" sha "revision" "r" "needs" ["x-tool" "cluster-net" "x-tool"]}
                               100))
  (assert (isinstance reply Reply))
  (assert (= #(reply.status (get reply.body "created")) #(200 True)))
  (setv task (get reply.state.tasks (get reply.body "task")))
  (assert (= task.needs #("cluster-net" "x-tool")))
  (assert (all (gfor item task.needs (isinstance item str))))
  (assert (= task.versions #((ComponentVersion "doeff" "1") (ComponentVersion "python" "3.14.0"))))
  (assert (all (gfor item task.versions (isinstance item ComponentVersion))))
  (setv again (get (. (state-from-kv (full-kv reply.state) 0) tasks) task.id))
  (assert (= again task))
  (assert (= again.needs #("cluster-net" "x-tool"))))


(deftest test-submit-refusals
  (val reply-81 (beat (ClusterState) "w" 0))
  (var s (get reply-81 0))
  (val reply-82 (put-detached s "job-9" 0 :lease 0.0))
  (var status (get reply-82 1))
  (assert (= status 400))
  (val reply-83 (put-detached s "job-9" 0 :retain (* 31 24 3600.0)))
  (:= status (get reply-83 1))
  (assert (= status 400))
  (val reply-84 (put-detached s "job-9" 0))
  (:= s (get reply-84 0))
  (val reply-85 (call s "PUT" "/detached/job-9" 0 {"name" "other" "program" SAMPLE-TASK-PROGRAM "revision" "r" "needs" ["net"]}))
  (:= status (get reply-85 1))
  (val body (get reply-85 2))
  (assert (= status 409) body))


(deftest test-bodies-without-needs-or-with-old-requires-are-refused-with-a-reason
  ;; 要る能力を書かない本文(needs が無い・空)と旧い形の requires の本文は、POST /tasks・PUT /detached・POST /warm のどれでも
  ;; 400 で理由を返し、状態を変えない(ADR-DOE-CLUSTER-001 R4b — どこにでも置ける仕事は無い・label の照合は受け付けない)。
  (import tests.env_fixtures [LOCK env-of])
  (import doeff_cluster.shared.intent.runtime_env_model [runtime-env->json RuntimeEnv])
  (<- env RuntimeEnv (env-of "app-1" "lib-1" LOCK))
  (<- declared (runtime-env->json env))
  (val reply-86 (beat (ClusterState) "w" 0))
  (var s (get reply-86 0))
  (val reply-87 (run (program-placed s V)))
  (:= s (get reply-87 0))
  (val sha (get reply-87 1))
  (val task {"program" sha "revision" "r" "leaseSeconds" 10.0})
  (val warm {"runtimeEnv" declared "ttlSeconds" 60 "holder" "svc-a"})
  (val routes [#("POST" "/tasks" task) #("PUT" "/detached/job-n" task) #("POST" "/warm" warm)])
  (for [#(method path base) routes]
    (for [#(extra reason) [#({} "空") #({"needs" []} "空") #({"requires" {"kind" "k3s"}} "旧い形の requires")
                           #({"requires" {"kind" "k3s"} "needs" ["net"]} "旧い形の requires")
                           #({"needs" ["kind=k3s"]} "label の形") #({"needs" {"kind" "k3s"}} "label の object")]]
      (setv #(after status body) (call s method path 10 (| base extra)))
      (assert (= status 400) #(method path extra status body))
      (assert (in reason (get body "error")) #(method path extra body))
      (assert (= after s) #(method path extra)))   ; 状態を変えない(値で比べる — POST /tasks は断っても調停を通る)
    ;; needs を書けば同じ本文が通る(断りは needs の欠けだけによる)。
    (setv #(_ passed-status passed-body) (call s method path 10 (| base {"needs" ["net"]})))
    (assert (= passed-status 200) #(method path passed-status passed-body)))
  ;; 旧い形の env(handler の組の import path — ADR-DOE-CLUSTER-001 改訂 1 の J の 11)は、needs が揃っていても task の口で 400 と理由。
  (for [#(method path) [#("POST" "/tasks") #("PUT" "/detached/job-env")]]
    (val answer (call s method path 10 (| task {"needs" ["net"] "env" "m:e"})))
    (assert (= (get answer 1) 400) #(method path answer))
    (assert (in "旧い形の env" (get answer 2 "error")) #(method path answer))
    (assert (= (get answer 0) s) #(method path))))


(deftest test-the-three-effects-have-no-env-or-requires-field
  ;; 入口 12(改訂 1 の J): task の effect は Program の値 1 つと needs だけを持つ。旧い欄 :env(handler の組の import path)と
  ;; :requires(label の照合)は構成子に無い — 欄の一覧に無いことを直に確かめる(frozen の dataclass は欄の外の名の引数を作る時点で
  ;; TypeError で断る。無い欄を名指して呼ぶ書き方は型検査が呼び出しの誤りとして断るので、欄の一覧を読む)。
  ;; WarmRuntimeEnv は温める実行の環境そのものを欄 env(RuntimeEnv)に持つ — 旧い :env(import path の文字列)とは別の欄なので、
  ;; env は型が RuntimeEnv であることを確かめ、requires だけ無いことを確かめる。
  (import dataclasses [fields])
  (import doeff_cluster.shared.intent.remote_model [RemoteJob])
  (import doeff_cluster.shared.intent.warm_model [WarmRuntimeEnv])
  (import doeff_cluster.shared.intent.runtime_env_model [RuntimeEnv])
  (for [#(effect gone) [#(SubmitDetached ["env" "requires"]) #(RemoteJob ["env" "requires"]) #(WarmRuntimeEnv ["requires"])]]
    (val names (frozenset (gfor f (fields effect) f.name)))
    (assert (in "needs" names) #(effect.__name__ names))
    (for [field gone]
      (assert (not-in field names) #(effect.__name__ field names))))
  (val warm-env (next (gfor f (fields WarmRuntimeEnv) :if (= f.name "env") f.type)))
  (assert (is warm-env RuntimeEnv) warm-env))


(deftest test-a-heartbeat-with-only-labels-is-refused
  ;; 旧い worker の名乗り(labels だけ・provides が無い)は 400 で理由を返し、worker を名簿に載せない。
  (val reply-88 (call (ClusterState) "POST" "/heartbeat" 0
                                   {"name" "old" "labels" {"kind" "k3s"} "capacity" 10 "versions" V "boot" "b1" "statuses" []}))
  (val after (get reply-88 0))
  (var status (get reply-88 1))
  (var body (get reply-88 2))
  (assert (= status 400) body)
  (assert (in "旧い形の labels" (get body "error")) body)
  (assert (not-in "old" after.workers))
  ;; provides の外の exclusive・label の形の名も断る。
  (val reply-89 (call (ClusterState) "POST" "/heartbeat" 0
                               {"name" "w" "provides" ["net"] "exclusive" ["gpu"] "capacity" 10 "versions" V "statuses" []}))
  (:= status (get reply-89 1))
  (:= body (get reply-89 2))
  (assert (= status 400) body)
  (val reply-90 (call (ClusterState) "POST" "/heartbeat" 0
                               {"name" "w" "provides" ["kind=k3s"] "capacity" 10 "versions" V "statuses" []}))
  (:= status (get reply-90 1))
  (:= body (get reply-90 2))
  (assert (= status 400) body))
