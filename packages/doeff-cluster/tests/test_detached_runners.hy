;; 切り離した task の担い手の名簿と置き先の契約の検(ReadRunners — detached_model.hy)と、担い手ごとの死・drain・戻り・coordinator の
;; 途絶(sim-cluster の検の effect KillWorker・DrainWorker・StopWorker・StartWorker・StopCoordinator — local.hy)。呼び手は置き先の表を
;; coordinator の名簿から読む(2026-09-26)。
;;
;; 同じ筋書きを 2 つの組で回し、同じ答えになることを確かめる:
;;   sim         … 手元の runner sim-cluster(本物の coordinator の調停ループと本物の run-worker — 担い手 a〔能力 x-tool〕と b〔能力 y-tool〕)・
;;                 仮想の時計。筋書きは検の側の呼び手として sim の送り手の口で話す
;;   coordinator … 本物の coordinator の判断(api_policy.respond / tick — test_detached.hy の MemoryCoordinator)と、同じ名乗りの
;;                 担い手 2 つ(本物の coordinator への口)・本物の detached-cluster・仮想の時計
;; coordinator の途絶は sim の組だけで確かめる(本物の送り手の送り直しは実時間の monotonic で数えるので、仮想の時計の coordinator の組
;; では途絶が明けない。sim の宿は同じ期限と間を仮想の時計で数える)。
;; (2026-09-28 まで sim の組の代わりに同じ VM の模擬 detached-local の組だった — 呼び手の外側の handler を継ぐので消した。)
(require doeff-hy.macros [deftest defk deff defhandler <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(require doeff-hy.record [defrecord])
(import collections.abc [Callable])
(import dataclasses [dataclass replace])  ; dataclass は defrecord の展開が名指す
(import pathlib [Path])
(import httpx)
(import pytest)
(import doeff [run with_handlers Program])
(import doeff_core_effects.scheduler [Spawn Cancel Task])
(import doeff_time [Delay SimClock sim-time-handler])
(import os)
(import doeff_cluster.foundation.process_versions [process-versions])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.shared.intent.detached_model [AwaitDetached ReadRunners ReadServices ServiceFact ServicesUnreachable
                                      DetachedSucceeded DetachedLost DetachedUnrunnable DetachedPending DetachedUnreachable
                                      RunnerFact RunnersUnreachable])
(import doeff_cluster.shared.protocol.detached [detached-cluster service-facts-of-view])
(import tests.transport_http [transport-http route-cell detached-sender TEST-ROUTE])
(import doeff_cluster.sim.local [sim-cluster SimWorker KillWorker DrainWorker StopWorker StartWorker StopCoordinator ProcessesOf
                             ReadCoordinator])
(import doeff_cluster.shared.entry.service_build [system-of])
(import doeff_cluster.shared.core.detached_rules [submit-detached-task])
(import doeff_cluster.coordinator.core.coordinator_invariants [StoppedGeneration TaskPlacementSeen stopped-generation-gets-no-new-task])
(import tests.detached_rig [slow-add RigWorker MemoryCoordinator worker-tick worker-loop])

(val RUNNERS #((RunnerFact :name "a" :provides #("x-tool") :exclusive #() :live True :draining False)
                (RunnerFact :name "b" :provides #("y-tool") :exclusive #() :live True :draining False)))
(val SIM-RUNNERS (tuple (gfor f RUNNERS (SimWorker :name f.name :provides (frozenset f.provides) :exclusive (frozenset f.exclusive) :task-reserve 0))))
(val NO-JOBS (system-of "runner-scenarios" #()))
(val ON-X (frozenset ["x-tool"]))
(val ON-Y (frozenset ["y-tool"]))
(val ON-Z (frozenset ["z-tool"]))
(val SLOW 3.0)
(val LEASE 5.0)
(val POLL 0.5)
;; coordinator の名簿の lease(ClusterTiming.lease-ms = 10 秒)より長く待てば、死んだ担い手は名簿で live でなくなる。
(val AFTER-LEASE 15.0)
;; 2 つの組の coordinator の時間の設定: 能力の合う担い手が live でない間に待っている task を待たせる上限だけを 180 秒に縮める
;; (本番の既定は 5 時間 — 期限を過ぎる筋書きを短い仮想の時間で回すため)。ほかは本番の既定。
(val TIMING (ClusterTiming :silent-worker-wait-ms 180000))
(val WAIT-LIMIT-SECONDS (// TIMING.silent-worker-wait-ms 1000))
;; 切り離した task を積んだ時の lease(本番の定期の task と同じ 60 秒)と、担い手が名簿で live でない長さ(lease より長く、上限より短い)。
(val QUEUED-LEASE 60.0)
(val AWAY 90.0)

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
    (setv worker (RigWorker "http://coordinator" (/ self.tmp-path (.format "tasks-{}-{}" name self.boots)) (run (process-versions os.environ))
                            :transport self.transport :name name :provides provides :exclusive exclusive)
          (get self.workers name) worker)
    worker))


(defk start-worker [worker]
  {:pre [(: worker RigWorker)] :post [(: % bool)]}
  ;; 1 拍名乗らせてから heartbeat のループを走らせる(送った時に置ける worker が在るように)。
  (<- (worker-tick worker))
  (<- loop Task (Spawn (worker-loop worker POLL) :daemon True))
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
        (do (<- (start-worker (.fresh runners name old.link.state.provides old.link.state.exclusive)))
            (resume True))
        (resume False))))


(deff coordinator-runners-rig [tmp-path]  ; defk にできない: pytest の params が渡す組を開く関数で、deftest が Program の外で呼ぶ
  {:pre [(: tmp-path Path)] :post [(: % RunnersRig)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "coordinator の組を開くため(担い手 a・b の RigWorker と、本物の detached-cluster・operator の口)。"
  (let [clock (SimClock)
        coordinator (MemoryCoordinator clock TIMING)
        transport (httpx.MockTransport coordinator.handle)
        runners (CoordinatorRunners tmp-path transport)
        operator (httpx.Client :transport transport :base-url "http://coordinator" :headers {"x-actor" "operator"})]
    (for [fact RUNNERS] (.fresh runners fact.name fact.provides fact.exclusive))
    (RunnersRig "coordinator" [(sim-time-handler :clock clock) (transport-http transport) (rig-runners runners operator)
                               (detached-cluster (route-cell) TEST-ROUTE (detached-sender "r") :poll-seconds POLL)]
                runners.workers)))


;; params には組の名を渡し、開く関数は型の付いた表 RIG-OPENERS から引く — params の値は検査器から型が見えない(object)ので、
;; 関数そのものを渡すと呼ぶ所が型の赤になる(#1731)。検は頭で名が str であることを確かめる。
(val RIG-OPENERS
     {"sim" sim-runners-rig "coordinator" coordinator-runners-rig})
(val RIGS [(pytest.param "sim" :id "sim") (pytest.param "coordinator" :id "coordinator")])


(defk open-named [rig-name tmp-path]
  {:pre [(: rig-name str) (: tmp-path Path)] :post [(: % RunnersRig)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "名で表から組を開く関数を引き、組を開くため。"
  ((get RIG-OPENERS rig-name) tmp-path))


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
      (<- (sim-cluster NO-JOBS scenario :workers SIM-RUNNERS :timing TIMING))
      (<- (with-handlers rig.handlers (rig-body rig scenario))))
  True)


(defk by-name [facts]
  {:pre [(: facts tuple)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "名簿の事実(RunnerFact)を担い手の名で引ける表にするため。"
  (dfor f facts f.name f))


;; --- 筋書き ---------------------------------------------------------------------------------------------------

(defk roster-and-placement []
  {:pre [] :post [(: % bool)]}
  ;; 名簿は 2 つとも生きていて drain でない。能力 y-tool を要る task は b に置かれ、走っている間の待ちは b を名指す。
  (<- roster tuple (ReadRunners))
  (val facts (! (by-name roster)))
  (assert (= (sorted facts) ["a" "b"]) roster)
  (assert (all (gfor f (.values facts) (and f.live (not f.draining)))) roster)
  (assert (= #((. (get facts "a") provides) (. (get facts "a") exclusive)) #(#("x-tool") #())) roster)
  (<- (submit-detached-task (slow-add SLOW 1) :key "k-on-y" :needs ON-Y :lease-seconds LEASE))
  (<- (Delay (* SLOW 0.3)))
  (<- early (AwaitDetached "k-on-y" :timeout-seconds 0.0))
  (assert (and (isinstance early DetachedPending) (= early.phase "assigned") (= early.runner "b")) early)
  (<- done (AwaitDetached "k-on-y"))
  (assert (= done (DetachedSucceeded 101)) done)
  True)

(deftest test-the-roster-names-live-runners-and-a-task-goes-to-the-runner-with-its-capability [rig-name tmp-path]
  {:params {"rig_name" RIGS}}
  (assert (isinstance rig-name str) rig-name)
  (<- rig RunnersRig (open-named rig-name tmp-path))
  (<- ok (run-on rig (roster-and-placement)))
  (assert ok))


(defk one-runner-dies []
  {:pre [] :post [(: % bool)]}
  ;; a だけが死ぬ: a の task は消え(走らせ直さない)、b の task は終わる。lease の後の名簿で a は生きていない。
  ;; 死なせる前に 2 秒待つ(sim の worker が task を起こすのはコードの準備の拍の後)。
  (<- (submit-detached-task (slow-add (* SLOW 4) 2) :key "k-x" :needs ON-X :lease-seconds LEASE))
  (<- (submit-detached-task (slow-add (* SLOW 2) 3) :key "k-y" :needs ON-Y :lease-seconds LEASE))
  (<- (Delay 2.0))
  (<- lost int (KillWorker "a"))
  (assert (= lost 1) lost)
  (<- on-x (AwaitDetached "k-x"))
  (assert (isinstance on-x DetachedLost) on-x)
  (<- on-y (AwaitDetached "k-y"))
  (assert (= on-y (DetachedSucceeded 103)) on-y)
  (<- (Delay AFTER-LEASE))
  (<- roster tuple (ReadRunners))
  (val facts (! (by-name roster)))
  (assert (not (. (get facts "a") live)) roster)
  (assert (. (get facts "b") live) roster)
  True)

(deftest test-a-named-runner-death-loses-only-its-tasks [rig-name tmp-path]
  {:params {"rig_name" RIGS}}
  (assert (isinstance rig-name str) rig-name)
  (<- rig RunnersRig (open-named rig-name tmp-path))
  (<- ok (run-on rig (one-runner-dies)))
  (assert ok))


(defk drain-then-return []
  {:pre [] :post [(: % bool)]}
  ;; a を drain: 名簿で draining・x-tool を要る task は置かれずに待つ(失敗にしない)。a が戻る(止めて新しい世代で起こす)と置かれて
  ;; 終わる。
  (<- asked dict (DrainWorker "a"))
  (assert (= (get asked "status") 200) asked)
  (<- (Delay POLL))
  (<- roster tuple (ReadRunners))
  (assert (. (get (! (by-name roster)) "a") draining) roster)
  (<- (submit-detached-task (slow-add SLOW 4) :key "k-drain" :needs ON-X :lease-seconds LEASE))
  (<- (Delay (* 4 POLL)))
  (<- waiting (AwaitDetached "k-drain" :timeout-seconds 0.0))
  (assert (and (isinstance waiting DetachedPending) (= waiting.phase "queued")) waiting)
  (<- (StopWorker "a"))
  (<- started bool (StartWorker "a"))
  (assert started)
  (<- done (AwaitDetached "k-drain"))
  (assert (= done (DetachedSucceeded 104)) done)
  True)

(deftest test-a-drained-runner-gets-no-new-task-until-it-returns [rig-name tmp-path]
  {:params {"rig_name" RIGS}}
  (assert (isinstance rig-name str) rig-name)
  (<- rig RunnersRig (open-named rig-name tmp-path))
  (<- ok (run-on rig (drain-then-return)))
  (assert ok))


(defk no-runner-with-the-capability []
  {:pre [] :post [(: % bool)]}
  (<- (submit-detached-task (slow-add 0.0 5) :key "k-z" :needs ON-Z :lease-seconds LEASE))
  (<- outcome (AwaitDetached "k-z"))
  (assert (isinstance outcome DetachedUnrunnable) outcome)
  True)

(deftest test-a-task-no-runner-can-take-is-unrunnable [rig-name tmp-path]
  {:params {"rig_name" RIGS}}
  (assert (isinstance rig-name str) rig-name)
  (<- rig RunnersRig (open-named rig-name tmp-path))
  (<- ok (run-on rig (no-runner-with-the-capability)))
  (assert ok))


;; --- 唯一の担い手の入れ替えの間の待ち(#2753)---------------------------------------------------------------------------
;; 2026-10-02 唯一の能力の合う担い手の入れ替え(古い Pod の drain → 抜ける → 新しい Pod の名乗り)の間に、待ち行列の切り離した task が
;; 積んだ時の lease(60 秒)を過ぎた拍に「版と能力が合う worker が無い」で落ちた。待ちの上限は task の lease ではなく coordinator の明示の
;; 期限(ClusterTiming.silent-worker-wait-ms — この file の組では TIMING の 180 秒)。

(defk away-longer-than-the-lease-then-return []
  {:pre [] :post [(: % bool)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: a を drain してから x-tool を要る task を積み、a を止めて task の lease(60 秒)より長く(90 秒)戻さない。task は待ちのまま
   (失敗にしない)で、a が新しい世代で名乗り直すと置かれて終わる(直す前の版では lease を過ぎた拍に DetachedUnrunnable)。"
  (<- asked dict (DrainWorker "a"))
  (assert (= (get asked "status") 200) asked)
  (<- (Delay POLL))
  (<- (submit-detached-task (slow-add SLOW 7) :key "k-away" :needs ON-X :lease-seconds QUEUED-LEASE))
  (<- (StopWorker "a"))
  (<- (Delay AWAY))
  (<- waiting (AwaitDetached "k-away" :timeout-seconds 0.0))
  (assert (and (isinstance waiting DetachedPending) (= waiting.phase "queued")) waiting)
  (<- started bool (StartWorker "a"))
  (assert started)
  (<- done (AwaitDetached "k-away"))
  (assert (= done (DetachedSucceeded 107)) done)
  True)

(deftest test-a-queued-task-waits-past-its-lease-while-the-only-runner-is-replaced [rig-name tmp-path]
  {:params {"rig_name" RIGS}}
  (assert (isinstance rig-name str) rig-name)
  (<- rig RunnersRig (open-named rig-name tmp-path))
  (<- ok (run-on rig (away-longer-than-the-lease-then-return)))
  (assert ok))


(defk away-past-the-wait-limit []
  {:pre [] :post [(: % bool)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: a を drain して止めてから(本番の入れ替えと同じ順 — drain の無い止めの直後は、名簿の生存の窓の内に積んだ task が止めた担い手に
   置かれる)x-tool を要る task を積み、待ちの期限(180 秒)より長く戻さない。task は、能力の合う担い手の名と live でない長さと期限を名指して
   DetachedUnrunnable で終わり、その後に a が戻っても走らない(終わった task は変わらない)。"
  (<- asked dict (DrainWorker "a"))
  (assert (= (get asked "status") 200) asked)
  (<- (Delay POLL))
  (<- (StopWorker "a"))
  (<- (submit-detached-task (slow-add SLOW 8) :key "k-gone" :needs ON-X :lease-seconds QUEUED-LEASE))
  (<- outcome (AwaitDetached "k-gone"))
  (assert (isinstance outcome DetachedUnrunnable) outcome)
  (assert (in (.format "の worker a が {} 秒 live でない(待ちの期限 {} 秒を過ぎた)" (+ WAIT-LIMIT-SECONDS 1) WAIT-LIMIT-SECONDS)
              outcome.detail)
          outcome.detail)
  (<- started bool (StartWorker "a"))
  (assert started)
  (<- (Delay AFTER-LEASE))
  (<- still (AwaitDetached "k-gone" :timeout-seconds 0.0))
  (assert (= still outcome) still)
  True)

(deftest test-a-queued-task-fails-by-name-when-the-only-runner-stays-away-past-the-limit [rig-name tmp-path]
  {:params {"rig_name" RIGS}}
  (assert (isinstance rig-name str) rig-name)
  (<- rig RunnersRig (open-named rig-name tmp-path))
  (<- ok (run-on rig (away-past-the-wait-limit)))
  (assert ok))


;; --- sim だけ: coordinator の途絶 ---------------------------------------------------------------------------

(defk outage-hides-the-roster []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: a に 20 秒の task を出し、走り出した後で coordinator を 120 秒止める。止まっている間は名簿も終わりも読めず、送りも届かない
   (送り手は本番と同じ期限まで送り直してから「届かない」と答える)。作り直した後に、止まっている間に終わった結果が読める。答え = 結果を
   読んだ後の task の process の列。"
  (<- (submit-detached-task (slow-add 20.0 6) :key "k-cut" :needs ON-X :lease-seconds LEASE))
  (<- (Delay 3.0))
  (<- (StopCoordinator 120.0))
  (<- (Delay 1.5))
  (<- roster (ReadRunners))
  (assert (isinstance roster RunnersUnreachable) roster)
  ;; 途絶の間は終わりを読めない(死んだとみなさない — 期限を決めた待ちは「届かない」)・送りも届かない。
  (<- during (AwaitDetached "k-cut" :timeout-seconds (* SLOW 1.5)))
  (assert (isinstance during DetachedUnreachable) during)
  (<- refused (submit-detached-task (slow-add 0.0 1) :key "k-during" :needs ON-X :lease-seconds LEASE))
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


;; --- sim だけ: drain を頼まない止め(sigterm)の後に積んだ task(#2819)------------------------------------------------
;; 2026-10-02 使い手の模擬の筋書きが 204ef43c7(#2692)の後に赤: sigterm で止まる途中の担い手が子の終わりを報告した直後に、
;; 呼び手がその担い手の能力を要る次の task を積み、coordinator がそれを止まる途中の担い手に置いた。置かれた task は
;; 始まらず、同じ名の新しい世代にも渡らない(切り離した task は世代に結ぶ)ので、lease を過ぎて lost になった。coordinator が止まりを
;; 知るのは明示の drain の頼み(本番の preStop)だけで、それを通らない止め(機体の終了・手の kill・sim の StopWorker)では穴が開く。
;; 本物の coordinator の組の担い手(detached_rig の RigWorker)は本物の worker の拍ではなく止まりを名乗らないので、sim の組だけで回す。

(val STOPPED-KEY "k-after-stop")
;; 条 C3 の失敗ケースの担い手: a だけが止まり始めを heartbeat で名乗らない(sim の SimWorker の silent-stop — 直す前の worker と同じ)。
(val SILENT-STOP-RUNNERS (tuple (gfor w SIM-RUNNERS (if (= w.name "a") (replace w :silent-stop True) w))))


(defk boot-of [name]
  {:pre [(: name str)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "program"}}
  "worker の今の世代を coordinator の worker の画面(GET /workers/<名>)から読むため(条 C3 の記録)。"
  (<- view dict (ReadCoordinator (+ "/workers/" name)))
  (get view "boot"))


(defrecord StopRecords
  "条 C3 の記録(筋書きの前半の答え): stopped = 止めた世代の列・placed = 止めた後・戻す前に読めた task の置き先の列(置かれていない
   queued の task は載せない)。"
  (#^ (get tuple #(StoppedGeneration ...)) stopped)
  (#^ (get tuple #(TaskPlacementSeen ...)) placed))


(defk placements-seen [key]
  {:pre [(: key str)] :post [(: % (get tuple #(TaskPlacementSeen ...)))] :tags {:context "doeff-cluster-test" :role "program"}}
  "coordinator の状態の画面から読める task key の置き先を、置かれた worker の今の世代つきで記録にするため(条 C3 の記録 — 置かれて
   いない queued の task は空)。"
  (<- state dict (ReadCoordinator "/state"))
  (val row (next (gfor t (get state "tasks") :if (= (.get t "key") key) t) None))
  (when (or (is row None) (is (.get row "worker") None) (= (get row "phase") "queued"))
    (return #()))
  (<- boot str (boot-of (get row "worker")))
  #((TaskPlacementSeen :key key :worker (get row "worker") :boot boot)))


(defk stop-without-drain-and-submit []
  {:pre [] :post [(: % StopRecords)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書きの前半: a を drain を頼まずに止め(sim の StopWorker = sigterm — 宿が止まりを立て、worker が抜けるまで待つ)、その直後に x-tool を
   要る task を積み、置かれ方を読む。止まった a は名簿の生存の窓(lease 10 秒)の内に居る。答え = 条 C3 の記録。"
  (<- stopped-boot str (boot-of "a"))
  (<- (StopWorker "a"))
  (<- (submit-detached-task (slow-add SLOW 10) :key STOPPED-KEY :needs ON-X :lease-seconds QUEUED-LEASE))
  (<- (Delay (* 4 POLL)))
  (<- placed tuple (placements-seen STOPPED-KEY))
  (StopRecords :stopped #((StoppedGeneration :worker "a" :boot stopped-boot)) :placed placed))


(defk stop-without-drain-then-return []
  {:pre [] :post [(: % bool)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 前半(stop-without-drain-and-submit)の task は止まり始めを名乗った世代に置かれず待ち(条 C3 は緑)、a が新しい世代で名乗り
   直すとそこで走って終わる(直す前は止まった世代に置かれ、新しい世代に渡らず lease の後に DetachedLost)。"
  (<- seen StopRecords (stop-without-drain-and-submit))
  (<- breaches tuple (stopped-generation-gets-no-new-task seen.stopped seen.placed))
  (assert (= breaches #()) breaches)
  (<- waiting (AwaitDetached STOPPED-KEY :timeout-seconds 0.0))
  (assert (and (isinstance waiting DetachedPending) (= waiting.phase "queued")) waiting)
  (<- started bool (StartWorker "a"))
  (assert started)
  (<- done (AwaitDetached STOPPED-KEY))
  (assert (= done (DetachedSucceeded 110)) done)
  True)

(deftest test-a-runner-stopped-without-a-drain-gets-no-new-task-until-its-next-generation
  (<- ok bool (sim-cluster NO-JOBS (stop-without-drain-then-return) :workers SIM-RUNNERS :timing TIMING))
  (assert ok))

(deftest test-a-counterexample-worker-that-does-not-announce-its-stop-breaks-c3
  ;; 失敗ケース: a が止まり始めを heartbeat で名乗らない(silent-stop)と、同じ前半で task が止まった a の世代に置かれ、条 C3 が task を名指す。
  (<- seen StopRecords (sim-cluster NO-JOBS (stop-without-drain-and-submit) :workers SILENT-STOP-RUNNERS :timing TIMING))
  (<- breaches tuple (stopped-generation-gets-no-new-task seen.stopped seen.placed))
  (assert (= breaches #(STOPPED-KEY)) seen))


;; 名乗りの前に置いた task(#2976 の I-3 の赤 R5): 積みと止まり始めの名乗りが同じ刻に coordinator へ届くと、どちらを先に
;; 受けるかは決まっていない(本番の到着の順も・#2850 の列の名の順も)。積みを先に受けると task は止まる途中の a の世代に置かれ、
;; 名乗りの後もそこに残って lease まで止まっていた。a は止まり始めた後に新しい task を始めないので、名乗りの heartbeat の状態の報告に
;; 無い task は、名乗りの報告を写す時に置き直しの待ちへ戻す(cluster_policy.absorb-task-reports)。

(val RACED-KEY "k-placed-before-stop")


(defk submit-then-stop-without-drain []
  {:pre [] :post [(: % StopRecords)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書きの前半(競り合い): x-tool を要る task を積んで a の今の世代に置かせ、a がそれを始める前に drain を頼まずに止める(sim の
   StopWorker = sigterm — 宿が止まりを立て、worker が抜けるまで待つ)。止めの前に a の世代に置かれていたこと(競り合いの形)を確かめてから
   止め、止めた後・戻す前の置き先を読む。答え = 条 C3 の記録。"
  (<- stopped-boot str (boot-of "a"))
  (<- (submit-detached-task (slow-add SLOW 10) :key RACED-KEY :needs ON-X :lease-seconds QUEUED-LEASE))
  (<- before tuple (placements-seen RACED-KEY))
  (assert (= before #((TaskPlacementSeen :key RACED-KEY :worker "a" :boot stopped-boot))) before)
  (<- (StopWorker "a"))
  (<- (Delay (* 4 POLL)))
  (<- placed tuple (placements-seen RACED-KEY))
  (StopRecords :stopped #((StoppedGeneration :worker "a" :boot stopped-boot)) :placed placed))


(defk submit-then-stop-then-return []
  {:pre [] :post [(: % bool)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 止める前に a の世代に置かれた task は、a が止まり始めを名乗った後その世代に残らず待ちへ戻り(条 C3 は緑)、a が新しい世代で
   名乗り直すとそこで走って終わる(直す前は止まった世代に残り、新しい世代に渡らず lease の後に DetachedLost)。"
  (<- seen StopRecords (submit-then-stop-without-drain))
  (<- breaches tuple (stopped-generation-gets-no-new-task seen.stopped seen.placed))
  (assert (= breaches #()) seen)
  (<- waiting (AwaitDetached RACED-KEY :timeout-seconds 0.0))
  (assert (and (isinstance waiting DetachedPending) (= waiting.phase "queued")) waiting)
  (<- started bool (StartWorker "a"))
  (assert started)
  (<- done (AwaitDetached RACED-KEY))
  (assert (= done (DetachedSucceeded 110)) done)
  True)

(deftest test-a-task-placed-just-before-its-runner-stops-goes-back-to-waiting
  (<- ok bool (sim-cluster NO-JOBS (submit-then-stop-then-return) :workers SIM-RUNNERS :timing TIMING))
  (assert ok))


;; --- 本物の client: coordinator に届かない送りと待ちは値で答える -------------------------------------------------------------

(deff cut-off [request]  ; defk にできない: httpx の MockTransport が呼ぶ callback
  {:pre [(: request httpx.Request)] :post [(: % httpx.Response)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "coordinator に届かない transport(接続が断られる)として、要求ごとに接続の失敗を上げるため。"
  (raise (httpx.ConnectError "connection refused" :request request)))

(deftest test-the-real-client-answers-unreachable-as-a-value
  ;; 送り直しの期限(deadline-seconds)を過ぎた通信の失敗は、送りも期限を決めた待ちも DetachedUnreachable(sim の宿の途絶と同じ値)。
  (defk scenario []
    {:pre [] :post [(: % bool)]}
    (<- sent (submit-detached-task (slow-add 0.0 1) :key "k-cut-real" :needs ON-X))
    (assert (isinstance sent DetachedUnreachable) sent)
    (<- awaited (AwaitDetached "k-cut-real" :timeout-seconds 1.0))
    (assert (isinstance awaited DetachedUnreachable) awaited)
    True)
  (<- ok bool (with-handlers [(sim-time-handler :clock (SimClock)) (transport-http (httpx.MockTransport cut-off))
                                   (detached-cluster (route-cell) TEST-ROUTE (detached-sender "r" :deadline-seconds 0.2) :poll-seconds POLL)]
                                  (scenario)))
  (assert ok))


;; --- Service の一覧(ReadServices — #3479): 置き先の担い手が報告した落ちた事実 -----------------------------------------------

(deftest test-the-service-view-carries-reported-failures-and-leaves-unreported-ones-as-none
  ;; 担い手の行(status.process)の failures・lastExitCode・lastExitAtMs を写す。行が無い(置き先が無い)・欄を載せない担い手の行・宣言の行に
  ;; 台数が無い(受け付けない宣言)時は None — 0 と黙って倒さない(倒すと、読み手が「落ちていない」と読む)。名の順に並べる。
  (val items [{"name" "w" "spec" {"replicas" 1} "status" {"failures" 5 "process" {"name" "w" "failures" 5 "lastExitCode" 1 "lastExitAtMs" 990}}}
              {"name" "u" "spec" {"replicas" 0} "status" {"process" None}}
              {"name" "v" "spec" {"replicas" 1} "status" {"process" {"name" "v" "attempts" 1}}}
              {"name" "x" "spec" {"revision" "r9"} "status" {"refused" "旧い形の行"}}])
  (assert (= (service-facts-of-view items)
             #((ServiceFact :name "u" :replicas 0 :failures None :last-exit-code None :last-exit-at-ms None)
               (ServiceFact :name "v" :replicas 1 :failures None :last-exit-code None :last-exit-at-ms None)
               (ServiceFact :name "w" :replicas 1 :failures 5 :last-exit-code 1 :last-exit-at-ms 990)
               (ServiceFact :name "x" :replicas None :failures None :last-exit-code None :last-exit-at-ms None)))))


(defk services-listed []
  {:pre [] :post [(: % bool)]}
  ;; Service を宣言していない系では、どちらの組でも一覧は空(名簿の読みと同じ送り手の口で答える)。
  (<- services (ReadServices))
  (assert (= services #()) services)
  True)

(deftest test-both-rigs-answer-the-service-list-through-the-same-port [rig-name tmp-path]
  {:params {"rig_name" RIGS}}
  (assert (isinstance rig-name str) rig-name)
  (<- rig RunnersRig (open-named rig-name tmp-path))
  (<- ok (run-on rig (services-listed)))
  (assert ok))


(deftest test-the-real-client-answers-an-unreachable-service-list-as-a-value
  ;; coordinator に届かない一覧の読みは ServicesUnreachable(落ちているかは分からない — 呼び手は直ったとみなさない)。
  (defk scenario []
    {:pre [] :post [(: % bool)]}
    (<- services (ReadServices))
    (assert (isinstance services ServicesUnreachable) services)
    True)
  (<- ok bool (with-handlers [(sim-time-handler :clock (SimClock)) (transport-http (httpx.MockTransport cut-off))
                                   (detached-cluster (route-cell) TEST-ROUTE (detached-sender "r" :deadline-seconds 0.2) :poll-seconds POLL)]
                                  (scenario)))
  (assert ok))
