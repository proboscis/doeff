;; 切り離した task の担い手の名簿と置き先の契約の検(ReadRunners — detached_model.hy)と、担い手ごとの死・drain・戻り・coordinator の
;; 途絶(sim-cluster の検の effect KillWorker・DrainWorker・StopWorker・StartWorker・StopCoordinator — local.hy)。呼び手は置き先の表を
;; coordinator の名簿から読む(2026-09-26)。
;;
;; 同じ筋書きを 2 つの組で回し、同じ答えになることを確かめる:
;;   sim         … 手元の runner sim-cluster(本物の coordinator の調停ループと本物の run-worker — 担い手 a〔能力 x-tool〕と b〔能力 y-tool〕)・
;;                 仮想の時計。筋書きは検の側の呼び手として sim の送り手の口で話す
;;   coordinator … 本物の coordinator の判断(api_policy.respond / tick — test_detached.hy の MemoryCoordinator)と、同じ名乗りの
;;                 担い手 2 つ(本物の CoordinatorLink)・本物の DetachedClient・仮想の時計
;; coordinator の途絶は sim の組だけで確かめる(本物の送り手の送り直しは実時間の monotonic で数えるので、仮想の時計の coordinator の組
;; では途絶が明けない。sim の宿は同じ期限と間を仮想の時計で数える)。
;; (2026-09-28 まで sim の組の代わりに同じ VM の模擬 detached-local の組だった — 呼び手の外側の handler を継ぐので消した。)
(require doeff-hy.macros [deftest defk deff defhandler <- val var])
(import collections.abc [Callable])
(import pathlib [Path])
(import typing [NoReturn])
(import httpx)
(import pytest)
(import doeff [with_handlers Program])
(import doeff_core_effects.scheduler [Spawn Cancel])
(import doeff_time [Delay SimClock sim-time-handler])
(import doeff_cluster.remote_model [current-versions])
(import doeff_cluster.detached_model [SubmitDetached AwaitDetached ReadRunners
                                      DetachedSucceeded DetachedLost DetachedUnrunnable DetachedPending DetachedUnreachable
                                      RunnerFact RunnersUnreachable])
(import doeff_cluster.detached [detached-cluster DetachedClient])
(import doeff_cluster.local [sim-cluster SimWorker KillWorker DrainWorker StopWorker StartWorker StopCoordinator ProcessesOf
                             ReadCoordinator])
(import doeff_cluster.service_model [system-of])
(import tests.detached_rig [slow-add RigWorker MemoryCoordinator worker-tick worker-loop])

(val RUNNERS #((RunnerFact :name "a" :provides #("x-tool") :exclusive #() :live True :draining False)
                (RunnerFact :name "b" :provides #("y-tool") :exclusive #() :live True :draining False)))
(val SIM-RUNNERS (tuple (gfor f RUNNERS (SimWorker :name f.name :provides (frozenset f.provides) :exclusive (frozenset f.exclusive)))))
(val NO-JOBS (system-of "runner-scenarios" #()))
(val ON-X (frozenset ["x-tool"]))
(val ON-Y (frozenset ["y-tool"]))
(val ON-Z (frozenset ["z-tool"]))
(val SLOW 3.0)
(val LEASE 5.0)
(val POLL 0.5)
;; coordinator の名簿の lease(ClusterTiming.lease-ms = 10 秒)より長く待てば、死んだ担い手は名簿で live でなくなる。
(val AFTER-LEASE 15.0)

;; --- 組 -----------------------------------------------------------------------------------------------------

(defclass RunnersRig []
  "筋書きを回す組。handlers = 被せる handler の組(外側が先 — sim は使わない)・workers = 担い手の名 → RigWorker(sim は空)。"
  (defn #^ None __init__ [self #^ str kind #^ list handlers #^ dict workers]
    (setv self.kind kind self.handlers handlers self.workers workers)))


(deff sim-runners-rig [tmp-path]  ; defk にできない: pytest の params が渡す組を開く関数で、deftest が Program の外で呼ぶ
  {:pre [(: tmp-path Path)] :post [(: % RunnersRig)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "sim の組を開くため(担い手 a・b は sim-cluster の worker — 筋書きを回す時に SIM-RUNNERS で並べる)。"
  (RunnersRig "sim" [] {}))


(defclass CoordinatorRunners []
  "coordinator の組の担い手の置き場: 名 → RigWorker と、作り直す時の材料(task の dir・transport)。"
  (defn #^ None __init__ [self #^ Path tmp-path #^ httpx.BaseTransport transport]
    (setv self.tmp-path tmp-path self.transport transport self.workers {} self.boots 0))

  (defn #^ RigWorker fresh [self #^ str name #^ tuple provides #^ tuple exclusive]
    "名乗る担い手を新しく作る(作り直しは新しい世代 — coordinator の drain は新しい世代の heartbeat で解ける)。"
    (+= self.boots 1)
    (setv worker (RigWorker "http://coordinator" (/ self.tmp-path (.format "tasks-{}-{}" name self.boots)) (current-versions)
                            :transport self.transport :name name :provides provides :exclusive exclusive)
          (get self.workers name) worker)
    worker))


(defk start-worker [worker]
  {:pre [(: worker RigWorker)] :post [(: % bool)]}
  ;; 1 拍名乗らせてから heartbeat のループを走らせる(送った時に置ける worker が在るように)。
  (<- (worker-tick worker))
  (<- loop (Spawn (worker-loop worker POLL) :daemon True))
  (setv worker.loop loop)
  True)


(defk stop-worker [worker]
  {:pre [(: worker RigWorker)] :post [(: % int)]}
  ;; 担い手の死: heartbeat が止まり、走っていた task も消える。答え = 消えた(終わっていなかった)task の数。
  (val running (lfor n worker.handles :if (not-in n worker.done) n))
  (setv worker.dead True)
  (for [handle (.values worker.handles)] (<- (Cancel handle)))
  (when worker.loop (<- (Cancel worker.loop)))
  (len running))


(defhandler rig-runners [#^ CoordinatorRunners runners #^ httpx.Client operator]
  ;; sim の組の検の effect(KillWorker・DrainWorker・StopWorker・StartWorker)に、coordinator の組の担い手で答える。
  (KillWorker [name]
    (<- lost int (stop-worker (get runners.workers name)))
    (resume lost))
  (DrainWorker [name ttl-seconds]
    ;; 本番の drain と同じ口(POST /workers/<名>/drain)。答え = 本番の CoordinatorCall と同じ形。
    (val response (.post operator (.format "/workers/{}/drain" name) :json {"ttlSeconds" ttl-seconds}))
    (resume {"status" response.status-code "body" (.json response)}))
  (StopWorker [name]
    ;; 止め = 前の process が抜ける(同じ名で 2 つの世代が交互に名乗ると、coordinator は作り直しと読んで置いた task を消す)。
    (val old (get runners.workers name))
    (when (not old.dead) (<- (stop-worker old)))
    (resume None))
  (StartWorker [name]
    ;; 戻り = 作り直した process(新しい世代)。
    (val old (get runners.workers name))
    (if old.dead
        (do (<- (start-worker (.fresh runners name old.link.provides old.link.exclusive)))
            (resume True))
        (resume False))))


(deff coordinator-runners-rig [tmp-path]  ; defk にできない: pytest の params が渡す組を開く関数で、deftest が Program の外で呼ぶ
  {:pre [(: tmp-path Path)] :post [(: % RunnersRig)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "coordinator の組を開くため(担い手 a・b の RigWorker と、本物の DetachedClient・operator の口)。"
  (let [clock (SimClock)
        coordinator (MemoryCoordinator clock)
        transport (httpx.MockTransport coordinator.handle)
        runners (CoordinatorRunners tmp-path transport)
        operator (httpx.Client :transport transport :base-url "http://coordinator" :headers {"x-actor" "operator"})]
    (for [fact RUNNERS] (.fresh runners fact.name fact.provides fact.exclusive))
    (RunnersRig "coordinator" [(sim-time-handler :clock clock) (rig-runners runners operator)
                               (detached-cluster (DetachedClient "http://coordinator" "r" :transport transport) :poll-seconds POLL)]
                runners.workers)))


(val RIGS [(pytest.param sim-runners-rig :id "sim") (pytest.param coordinator-runners-rig :id "coordinator")])


(defk rig-body [rig scenario]
  {:pre [(: rig RunnersRig) (: scenario Program)] :post [(: % bool)] :tags {:context "doeff-cluster-test" :role "program"}}
  "coordinator の組の担い手を名乗らせてから筋書きを回し、終わったら生きている担い手を止めるため。"
  (for [worker (list (.values rig.workers))] (<- (start-worker worker)))
  (<- scenario)
  (for [worker (list (.values rig.workers))]
    (when (not worker.dead) (<- (stop-worker worker))))
  True)


(defk run-on [rig scenario]
  {:pre [(: rig RunnersRig) (: scenario Program)] :post [(: % bool)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書きを組の上で回すため(sim の組は sim-cluster の中で・担い手 a・b は sim の worker)。"
  (if (= rig.kind "sim")
      (<- (sim-cluster NO-JOBS scenario :workers SIM-RUNNERS))
      (<- (with-handlers rig.handlers (rig-body rig scenario))))
  True)


(defn #^ (get dict #(str RunnerFact)) by-name [#^ tuple facts]
  (dfor f facts f.name f))


;; --- 筋書き ---------------------------------------------------------------------------------------------------

(defk roster-and-placement []
  {:pre [] :post [(: % bool)]}
  ;; 名簿は 2 つとも生きていて drain でない。能力 y-tool を要る task は b に置かれ、走っている間の待ちは b を名指す。
  (<- roster (ReadRunners))
  (val facts (by-name roster))
  (assert (= (sorted facts) ["a" "b"]) roster)
  (assert (all (gfor f (.values facts) (and f.live (not f.draining)))) roster)
  (assert (= #((. (get facts "a") provides) (. (get facts "a") exclusive)) #(#("x-tool") #())) roster)
  (<- (SubmitDetached (slow-add SLOW 1) :key "k-on-y" :needs ON-Y :lease-seconds LEASE))
  (<- (Delay (* SLOW 0.3)))
  (<- early (AwaitDetached "k-on-y" :timeout-seconds 0.0))
  (assert (and (isinstance early DetachedPending) (= early.phase "assigned") (= early.runner "b")) early)
  (<- done (AwaitDetached "k-on-y"))
  (assert (= done (DetachedSucceeded 101)) done)
  True)

(deftest test-the-roster-names-live-runners-and-a-task-goes-to-the-runner-with-its-capability [open-rig tmp-path]
  {:params {"open_rig" RIGS}}
  (<- ok (run-on (open-rig tmp-path) (roster-and-placement)))
  (assert ok))


(defk one-runner-dies []
  {:pre [] :post [(: % bool)]}
  ;; a だけが死ぬ: a の task は消え(走らせ直さない)、b の task は終わる。lease の後の名簿で a は生きていない。
  ;; 死なせる前に 2 秒待つ(sim の worker が task を起こすのはコードの準備の拍の後)。
  (<- (SubmitDetached (slow-add (* SLOW 4) 2) :key "k-x" :needs ON-X :lease-seconds LEASE))
  (<- (SubmitDetached (slow-add (* SLOW 2) 3) :key "k-y" :needs ON-Y :lease-seconds LEASE))
  (<- (Delay 2.0))
  (<- lost int (KillWorker "a"))
  (assert (= lost 1) lost)
  (<- on-x (AwaitDetached "k-x"))
  (assert (isinstance on-x DetachedLost) on-x)
  (<- on-y (AwaitDetached "k-y"))
  (assert (= on-y (DetachedSucceeded 103)) on-y)
  (<- (Delay AFTER-LEASE))
  (<- roster (ReadRunners))
  (val facts (by-name roster))
  (assert (not (. (get facts "a") live)) roster)
  (assert (. (get facts "b") live) roster)
  True)

(deftest test-a-named-runner-death-loses-only-its-tasks [open-rig tmp-path]
  {:params {"open_rig" RIGS}}
  (<- ok (run-on (open-rig tmp-path) (one-runner-dies)))
  (assert ok))


(defk drain-then-return []
  {:pre [] :post [(: % bool)]}
  ;; a を drain: 名簿で draining・x-tool を要る task は置かれずに待つ(失敗にしない)。a が戻る(止めて新しい世代で起こす)と置かれて
  ;; 終わる。
  (<- asked dict (DrainWorker "a"))
  (assert (= (get asked "status") 200) asked)
  (<- (Delay POLL))
  (<- roster (ReadRunners))
  (assert (. (get (by-name roster) "a") draining) roster)
  (<- (SubmitDetached (slow-add SLOW 4) :key "k-drain" :needs ON-X :lease-seconds LEASE))
  (<- (Delay (* 4 POLL)))
  (<- waiting (AwaitDetached "k-drain" :timeout-seconds 0.0))
  (assert (and (isinstance waiting DetachedPending) (= waiting.phase "queued")) waiting)
  (<- (StopWorker "a"))
  (<- started bool (StartWorker "a"))
  (assert started)
  (<- done (AwaitDetached "k-drain"))
  (assert (= done (DetachedSucceeded 104)) done)
  True)

(deftest test-a-drained-runner-gets-no-new-task-until-it-returns [open-rig tmp-path]
  {:params {"open_rig" RIGS}}
  (<- ok (run-on (open-rig tmp-path) (drain-then-return)))
  (assert ok))


(defk no-runner-with-the-capability []
  {:pre [] :post [(: % bool)]}
  (<- (SubmitDetached (slow-add 0.0 5) :key "k-z" :needs ON-Z :lease-seconds LEASE))
  (<- outcome (AwaitDetached "k-z"))
  (assert (isinstance outcome DetachedUnrunnable) outcome)
  True)

(deftest test-a-task-no-runner-can-take-is-unrunnable [open-rig tmp-path]
  {:params {"open_rig" RIGS}}
  (<- ok (run-on (open-rig tmp-path) (no-runner-with-the-capability)))
  (assert ok))


;; --- sim だけ: coordinator の途絶 ---------------------------------------------------------------------------

(defk outage-hides-the-roster []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: a に 20 秒の task を出し、走り出した後で coordinator を 120 秒止める。止まっている間は名簿も終わりも読めず、送りも届かない
   (送り手は本番と同じ期限まで送り直してから「届かない」と答える)。作り直した後に、止まっている間に終わった結果が読める。答え = 結果を
   読んだ後の task の process の列。"
  (<- (SubmitDetached (slow-add 20.0 6) :key "k-cut" :needs ON-X :lease-seconds LEASE))
  (<- (Delay 3.0))
  (<- (StopCoordinator 120.0))
  (<- (Delay 1.5))
  (<- roster (ReadRunners))
  (assert (isinstance roster RunnersUnreachable) roster)
  ;; 途絶の間は終わりを読めない(死んだとみなさない — 期限を決めた待ちは「届かない」)・送りも届かない。
  (<- during (AwaitDetached "k-cut" :timeout-seconds (* SLOW 1.5)))
  (assert (isinstance during DetachedUnreachable) during)
  (<- refused (SubmitDetached (slow-add 0.0 1) :key "k-during" :needs ON-X :lease-seconds LEASE))
  (assert (isinstance refused DetachedUnreachable) refused)
  (<- after (AwaitDetached "k-cut"))
  (assert (= after (DetachedSucceeded 106)) after)
  (<- back (ReadRunners))
  (assert (isinstance back tuple) back)
  (<- state dict (ReadCoordinator "/state"))
  (val ids (lfor t (get state "tasks") :if (= (.get t "key") "k-cut") (get t "id")))
  (assert (= (len ids) 1) ids)
  (<- processes tuple (ProcessesOf (+ "task/" (get ids 0))))
  processes)

(deftest test-a-coordinator-outage-hides-the-roster-but-keeps-tasks-running
  ;; 担い手は coordinator の途絶で切り離した task を止めない(fence を越えても kept-when-cut-off が残す)。task は 1 度だけ a で走り、
  ;; 結果を書いて終わる(走らせ直さない)。
  (<- processes tuple (sim-cluster NO-JOBS (outage-hides-the-roster) :workers SIM-RUNNERS))
  (assert (= (len processes) 1) processes)
  (val only (get processes 0))
  (assert (= #(only.worker only.exit-code) #("a" 0)) processes))


;; --- 本物の client: coordinator に届かない送りと待ちは値で答える -------------------------------------------------------------

(defn #^ NoReturn cut-off [#^ httpx.Request request]
  "coordinator に届かない transport(接続が断られる)。"
  (raise (httpx.ConnectError "connection refused" :request request)))

(deftest test-the-real-client-answers-unreachable-as-a-value
  ;; 送り直しの期限(deadline-seconds)を過ぎた通信の失敗は、送りも期限を決めた待ちも DetachedUnreachable(sim の宿の途絶と同じ値)。
  (val client (DetachedClient "http://coordinator" "r" :transport (httpx.MockTransport cut-off) :deadline-seconds 0.2))
  (defk scenario []
    {:pre [] :post [(: % bool)]}
    (<- sent (SubmitDetached (slow-add 0.0 1) :key "k-cut-real" :needs ON-X))
    (assert (isinstance sent DetachedUnreachable) sent)
    (<- awaited (AwaitDetached "k-cut-real" :timeout-seconds 1.0))
    (assert (isinstance awaited DetachedUnreachable) awaited)
    True)
  (<- ok bool (with-handlers [(sim-time-handler :clock (SimClock)) (detached-cluster client :poll-seconds POLL)] (scenario)))
  (assert ok))
