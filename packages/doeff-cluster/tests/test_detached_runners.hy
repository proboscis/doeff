;; 切り離した task の担い手の名簿と置き先の契約の検(ReadRunners・SimulateRunnerLoss の名指し・SimulateRunnerDrain・SimulateRunnerReturn・
;; SimulateCoordinatorOutage — detached_model.hy)。呼び手は置き先の表を coordinator の名簿から読み、模擬は担い手ごとの死・drain・
;; 戻り・coordinator の途絶を起こす(2026-09-26)。
;;
;; 同じ筋書きを 2 つの組で回し、fake が本物の coordinator の判断と同じ答えを返すことを確かめる:
;;   fake        … detached-local(担い手 a〔role=x〕と b〔role=y〕)・仮想の時計
;;   coordinator … 本物の coordinator の判断(api_policy.respond / tick — test_detached.hy の MemoryCoordinator)と、同じ名乗りの
;;                 担い手 2 つ(本物の CoordinatorLink)・本物の DetachedClient・仮想の時計
;; coordinator の途絶は fake の組だけで確かめる(本物の送り手の送り直しは実時間の monotonic で数えるので、仮想の時計の組では
;; 途絶が明けない)。
(require doeff-hy.macros [deftest defk defhandler <- val var])
(import collections.abc [Callable])
(import pathlib [Path])
(import httpx)
(import pytest)
(import doeff [with_handlers Program])
(import doeff_core_effects.handlers [reader])
(import doeff_core_effects.scheduler [Spawn Cancel])
(import doeff_time [Delay SimClock sim-time-handler])
(import doeff_cluster.cluster_model [Requirement])
(import doeff_cluster.remote_model [current-versions])
(import doeff_cluster.detached_model [SubmitDetached AwaitDetached ReadRunners SimulateRunnerLoss SimulateRunnerDrain
                                      SimulateRunnerReturn SimulateCoordinatorOutage
                                      DetachedSucceeded DetachedLost DetachedUnrunnable DetachedPending DetachedUnreachable
                                      RunnerFact RunnersUnreachable])
(import doeff_cluster.detached [detached-local DetachedLocalStore detached-cluster DetachedClient DetachedEvent])
(import tests.detached_rig [ENV slow-add RigWorker MemoryCoordinator worker-tick worker-loop])

(val RUNNERS #((RunnerFact :name "a" :labels #(#("role" "x")) :live True :draining False)
                (RunnerFact :name "b" :labels #(#("role" "y")) :live True :draining False)))
(val ON-X #((Requirement "role" "x")))
(val ON-Y #((Requirement "role" "y")))
(val ON-Z #((Requirement "role" "z")))
(val SLOW 3.0)
(val LEASE 5.0)
(val POLL 0.5)
;; coordinator の名簿の lease(ClusterTiming.lease-ms = 10 秒)より長く待てば、死んだ担い手は名簿で live でなくなる。
(val AFTER-LEASE 15.0)


;; --- 組 -----------------------------------------------------------------------------------------------------

(defclass RunnersRig []
  "筋書きを回す組。handlers = 被せる handler の組(外側が先)・workers = 担い手の名 → RigWorker(fake は空)。"
  (defn __init__ [self #^ str kind #^ list handlers #^ dict workers]
    (setv self.kind kind self.handlers handlers self.workers workers)))


(defn #^ RunnersRig fake-runners-rig [#^ Path tmp-path]
  (setv store (DetachedLocalStore :runners RUNNERS))
  (RunnersRig "fake" [(sim-time-handler :clock (SimClock)) (reader {"base" 100}) (detached-local store :poll-seconds POLL)] {}))


(defclass CoordinatorRunners []
  "coordinator の組の担い手の置き場: 名 → RigWorker と、作り直す時の材料(task の dir・transport)。"
  (defn __init__ [self #^ Path tmp-path transport]
    (setv self.tmp-path tmp-path self.transport transport self.workers {} self.boots 0))

  (defn #^ RigWorker fresh [self #^ str name #^ dict labels]
    "名乗る担い手を新しく作る(作り直しは新しい世代 — coordinator の drain は新しい世代の heartbeat で解ける)。"
    (+= self.boots 1)
    (setv worker (RigWorker "http://coordinator" (/ self.tmp-path (.format "tasks-{}-{}" name self.boots)) (current-versions)
                            :transport self.transport :name name :labels labels)
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
  (SimulateRunnerLoss [runner]
    (<- lost int (stop-worker (get runners.workers runner)))
    (resume lost))
  (SimulateRunnerDrain [runner]
    ;; 本番の drain と同じ口(POST /workers/<名>/drain)。答え = その担い手でまだ走っている task の数。
    (.raise-for-status (.post operator (.format "/workers/{}/drain" runner) :json {}))
    (val worker (get runners.workers runner))
    (resume (len (lfor n worker.handles :if (not-in n worker.done) n))))
  (SimulateRunnerReturn [runner]
    ;; 戻り = 作り直した process(前の process は止まっている — 同じ名で 2 つの世代が交互に名乗ると、coordinator は作り直しと読んで
    ;; 置いた task を消す。drain の後の戻りでも、前の process は抜けてから新しい process が名乗る)。
    (val old (get runners.workers runner))
    (when (not old.dead) (<- (stop-worker old)))
    (<- (start-worker (.fresh runners runner (dict old.link.labels))))
    (resume None)))


(defn #^ RunnersRig coordinator-runners-rig [#^ Path tmp-path]
  (setv clock (SimClock)
        coordinator (MemoryCoordinator clock)
        transport (httpx.MockTransport coordinator.handle)
        runners (CoordinatorRunners tmp-path transport)
        operator (httpx.Client :transport transport :base-url "http://coordinator" :headers {"x-actor" "operator"}))
  (for [fact RUNNERS] (.fresh runners fact.name (dict fact.labels)))
  (RunnersRig "coordinator" [(sim-time-handler :clock clock) (reader {"worker" "child" "base" 100}) (rig-runners runners operator)
                             (detached-cluster (DetachedClient "http://coordinator" "r" :transport transport) :poll-seconds POLL)]
              runners.workers))


(val RIGS [(pytest.param fake-runners-rig :id "fake") (pytest.param coordinator-runners-rig :id "coordinator")])


(defk run-on [rig scenario]
  {:pre [(: rig RunnersRig) (: scenario Program)] :post [(: % bool)]}
  ;; 担い手を名乗らせてから筋書きを回し、終わったら生きている担い手を止める。
  (defk body []
    {:pre [] :post [(: % bool)]}
    (for [worker (list (.values rig.workers))] (<- (start-worker worker)))
    (<- scenario)
    (for [worker (list (.values rig.workers)) :if (not worker.dead)] (<- (stop-worker worker)))
    True)
  (<- ok bool (with-handlers rig.handlers (body)))
  ok)


(defn #^ (get dict #(str RunnerFact)) by-name [#^ tuple facts]
  (dfor f facts f.name f))


;; --- 筋書き ---------------------------------------------------------------------------------------------------

(defk roster-and-placement []
  {:pre [] :post [(: % bool)]}
  ;; 名簿は 2 つとも生きていて drain でない。role=y を求める task は b に置かれ、走っている間の待ちは b を名指す。
  (<- roster (ReadRunners))
  (val facts (by-name roster))
  (assert (= (sorted facts) ["a" "b"]) roster)
  (assert (all (gfor f (.values facts) (and f.live (not f.draining)))) roster)
  (assert (= (. (get facts "a") labels) #(#("role" "x"))) roster)
  (<- (SubmitDetached (slow-add SLOW 1) :env ENV :key "k-on-y" :requires ON-Y :lease-seconds LEASE))
  (<- (Delay (* SLOW 0.3)))
  (<- early (AwaitDetached "k-on-y" :timeout-seconds 0.0))
  (assert (and (isinstance early DetachedPending) (= early.phase "assigned") (= early.runner "b")) early)
  (<- done (AwaitDetached "k-on-y"))
  (assert (= done (DetachedSucceeded 101)) done)
  True)

(deftest test-the-roster-names-live-runners-and-a-task-goes-to-the-runner-with-its-label [open-rig tmp-path]
  {:params {"open_rig" RIGS}}
  (<- ok (run-on (open-rig tmp-path) (roster-and-placement)))
  (assert ok))


(defk one-runner-dies []
  {:pre [] :post [(: % bool)]}
  ;; a だけが死ぬ: a の task は消え(走らせ直さない)、b の task は終わる。lease の後の名簿で a は生きていない。
  (<- (SubmitDetached (slow-add (* SLOW 4) 2) :env ENV :key "k-x" :requires ON-X :lease-seconds LEASE))
  (<- (SubmitDetached (slow-add (* SLOW 2) 3) :env ENV :key "k-y" :requires ON-Y :lease-seconds LEASE))
  (<- (Delay (* SLOW 0.3)))
  (<- lost int (SimulateRunnerLoss "a"))
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
  ;; a を drain: 名簿で draining・role=x の task は置かれずに待つ(失敗にしない)。a が戻ると置かれて終わる。
  (<- still int (SimulateRunnerDrain "a"))
  (assert (= still 0) still)
  (<- (Delay POLL))
  (<- roster (ReadRunners))
  (assert (. (get (by-name roster) "a") draining) roster)
  (<- (SubmitDetached (slow-add SLOW 4) :env ENV :key "k-drain" :requires ON-X :lease-seconds LEASE))
  (<- (Delay (* 4 POLL)))
  (<- waiting (AwaitDetached "k-drain" :timeout-seconds 0.0))
  (assert (and (isinstance waiting DetachedPending) (= waiting.phase "queued")) waiting)
  (<- (SimulateRunnerReturn "a"))
  (<- done (AwaitDetached "k-drain"))
  (assert (= done (DetachedSucceeded 104)) done)
  True)

(deftest test-a-drained-runner-gets-no-new-task-until-it-returns [open-rig tmp-path]
  {:params {"open_rig" RIGS}}
  (<- ok (run-on (open-rig tmp-path) (drain-then-return)))
  (assert ok))


(defk no-runner-with-the-label []
  {:pre [] :post [(: % bool)]}
  (<- (SubmitDetached (slow-add 0.0 5) :env ENV :key "k-z" :requires ON-Z :lease-seconds LEASE))
  (<- outcome (AwaitDetached "k-z"))
  (assert (isinstance outcome DetachedUnrunnable) outcome)
  True)

(deftest test-a-task-no-runner-can-take-is-unrunnable [open-rig tmp-path]
  {:params {"open_rig" RIGS}}
  (<- ok (run-on (open-rig tmp-path) (no-runner-with-the-label)))
  (assert ok))


;; --- fake だけ: coordinator の途絶と真実の記録 ------------------------------------------------------------------

(deftest test-fake-outage-hides-the-roster-but-keeps-tasks-running [tmp-path]
  (val store (DetachedLocalStore :runners RUNNERS))
  (val rig (RunnersRig "fake" [(sim-time-handler :clock (SimClock)) (reader {"base" 100}) (detached-local store :poll-seconds POLL)] {}))
  (defk scenario []
    {:pre [] :post [(: % bool)]}
    (<- (SubmitDetached (slow-add SLOW 6) :env ENV :key "k-cut" :requires ON-X :lease-seconds LEASE))
    (<- (SimulateCoordinatorOutage (* SLOW 2)))
    (<- roster (ReadRunners))
    (assert (isinstance roster RunnersUnreachable) roster)
    ;; 途絶の間は終わりを読めない(死んだとみなさない — 期限を決めた待ちは「届かない」)・送りも届かない。明けた後に、途絶の間に
    ;; 終わった結果が読める。
    (<- during (AwaitDetached "k-cut" :timeout-seconds (* SLOW 1.5)))
    (assert (isinstance during DetachedUnreachable) during)
    (<- refused (SubmitDetached (slow-add 0.0 1) :env ENV :key "k-during" :requires ON-X :lease-seconds LEASE))
    (assert (isinstance refused DetachedUnreachable) refused)
    (<- after (AwaitDetached "k-cut"))
    (assert (= after (DetachedSucceeded 106)) after)
    (<- back (ReadRunners))
    (assert (isinstance back tuple) back)
    True)
  (<- ok (run-on rig (scenario)))
  (assert ok)
  (assert (= (lfor e store.events #(e.key e.op e.runner))
             [#("k-cut" "submitted" "") #("k-cut" "started" "a") #("k-cut" "succeeded" "a")])
          store.events))


;; --- 本物の client: coordinator に届かない送りと待ちは値で答える -------------------------------------------------------------

(defn cut-off [request]
  "coordinator に届かない transport(接続が断られる)。"
  (raise (httpx.ConnectError "connection refused" :request request)))

(deftest test-the-real-client-answers-unreachable-as-a-value
  ;; 送り直しの期限(deadline-seconds)を過ぎた通信の失敗は、送りも期限を決めた待ちも DetachedUnreachable(fake の途絶と同じ値)。
  (val client (DetachedClient "http://coordinator" "r" :transport (httpx.MockTransport cut-off) :deadline-seconds 0.2))
  (defk scenario []
    {:pre [] :post [(: % bool)]}
    (<- sent (SubmitDetached (slow-add 0.0 1) :env ENV :key "k-cut-real"))
    (assert (isinstance sent DetachedUnreachable) sent)
    (<- awaited (AwaitDetached "k-cut-real" :timeout-seconds 1.0))
    (assert (isinstance awaited DetachedUnreachable) awaited)
    True)
  (<- ok bool (with-handlers [(sim-time-handler :clock (SimClock)) (detached-cluster client :poll-seconds POLL)] (scenario)))
  (assert ok))
