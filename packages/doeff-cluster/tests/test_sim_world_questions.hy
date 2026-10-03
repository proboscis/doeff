;; 調停ループの一番内側(sim の observe-requests)が 1 歩ごとに sim の世界(sim-world)へ出す問いは、歩の数の定数倍に収まる(#2668)。
;;
;; 世界へ出る効果は 1 つずつ答え手の段を通るので、1 歩の問いの数がそのまま模擬の検の費用になる(test_emulated_daily_verify の重い 1 本で
;; 世界の答え手を約 160 万回通っていた)。取った要求を篩う問い(網の切れ・口の故障・時計)と、報告の記録・覚え・区間の歩の書きの数は
;; 1 歩に 1 度にまとめ(AdmitBatch・#3054 の C-6)、何も取らなかった歩は報告も覚えも書かない。
;;
;; - 静かな歩(要求の無い歩)N 回で、世界への問いはちょうど N 個(AdmitBatch が 1 歩に 1 つ)。
;;   反例: 網の切れ(CutPeers)・口の故障(FailedRoutes)を別々に聞き、空の覚えも書く形では 1 歩に 3 つ(この検は赤)。
;; - worker の宿(sim-host)の読んで直して書く 1 組(状態の報告 PublishStatus)は、世界への問い 1 つ(ChangeHostTruth)。
;;   反例: 宿の真実を読み(HostTruthOf)、直して置き直す(PutHostTruth)形では 1 組に 2 つ(この検は赤)。
(require doeff-hy.macros [deftest defk defhandler defeffect <- val var])
(import doeff [with-handlers Program])
(import doeff_vm [EffectBase])
(import doeff_time [sim-time-handler])
(import doeff_core_effects.handlers [state :as session-store])
(import doeff_core_effects.scheduler [CreatePromise Promise])
(import doeff_cluster.shared.intent.protocol [NextRequests Request])
(import doeff_cluster.coordinator.protocol.request_queue [RequestQueue queued-requests enqueue-request])
(import doeff_cluster.coordinator.protocol.store [Persist])
(import doeff_cluster.worker.intent.worker_model [PublishStatus WorkerStopRequested ReadDesired])
(import doeff_cluster.sim.local [SimPlan SimParts SimWorker HostTruth HostTruthOf PartsOf sim-plan sim-world sim-host
                                 observe-requests])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.sim_programs [beacons])
(import tests.clock_fixtures [clock-at])


(val SIM-LOCAL "doeff_cluster.sim.local")


(defeffect WorldQuestions
  "数える handler(world-question-counter)が数えた、世界への問いの型の名の列(問うた順)。"
  {:answer tuple :tags {:context "doeff-cluster-test" :role "intent"}})


(defhandler world-question-counter
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  ;; 内側(調停ループの一番内側の observe-requests)が sim の世界へ出した問い(sim.local の効果)の型の名を順に覚え、問いはそのまま外側の
  ;; 世界へ出し直す(答えは世界の答えのまま — 数えるだけ)。
  (session var asked #())
  (WorldQuestions []
    (resume asked))
  (EffectBase []
    :when (= (. (type effect) __module__) SIM-LOCAL)
    (:= asked (+ asked #((. (type effect) __name__))))
    (<- answer effect)
    (resume answer)))


(defk quiet-steps [n]
  {:pre [(: n int)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "調停ループの拍と同じ NextRequests を n 回出し(列は空 — 静かな歩)、世界への問いの列を読むため。"
  (for [_ (range n)]
    (<- (NextRequests 1.0 10)))
  (<- asked tuple (WorldQuestions))
  asked)


(defk questions-in-quiet-steps [n]
  {:pre [(: n int)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "本物の列(1 秒ごとの拍)・本物の observe-requests・本物の sim の世界の間に数える handler を置き、静かな歩 n 回の問いの列を返すため。"
  (<- plan SimPlan (sim-plan (beacons sim-foundation) None None "sim" 0 None None None None))
  (<- asked tuple ((sim-time-handler :clock (! (clock-at 0)))
                   (with-handlers [(session-store) (sim-world plan) world-question-counter
                                   (queued-requests (RequestQueue :skip-idle False)) observe-requests]
                     (quiet-steps n))))
  asked)


(deftest test-a-quiet-step-asks-the-world-once
  (<- asked tuple (questions-in-quiet-steps 5))
  ;; 1 歩に 1 つ(網の切れ・口の故障・刻をまとめた問い)— 何も取らなかった歩は覚えを書かない。
  (assert (= asked (* #("AdmitBatch") 5)) asked))


(val HOST (SimWorker :name "w1" :provides #{"net"}))


(defk publish-once [plan]
  {:pre [(: plan SimPlan)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "worker w1 の今の世代の宿(本物の sim-host)で状態の報告を 1 度出し、その間の世界への問いの列を読むため(宿を組む読みは数えない —
   under-host)。"
  (<- asked tuple (under-host plan (PublishStatus :statuses #())))
  asked)


(deftest test-a-host-read-change-write-asks-the-world-once
  (<- plan SimPlan (sim-plan (beacons sim-foundation) #(HOST) None "sim" 0 None None None None))
  (<- asked tuple ((sim-time-handler :clock (! (clock-at 0)))
                   (with-handlers [(session-store) (sim-world plan) world-question-counter] (publish-once plan))))
  ;; 報告の 1 組は ChangeHostTruth 1 つ(読みと書きを別々に聞かない)。
  (assert (= asked #("ChangeHostTruth")) asked))


;; 1 つの要求・1 つの拍・1 回の書きが世界へ出す問いを 1 つにまとめる(#3054 の C-6 — placer の筋書きで sim の宿が VM の歩の 23%・
;; 世界の状態の読み書き Get 4,682 / Put 1,260)。
;; - 要求を取った歩: 篩い(網の切れ・口の故障・刻)と、service の報告の記録と、返事の前に落ちた時の覚えを、問い 1 つ(AdmitBatch)。
;;   反例: RouteFaultsNow・NoteReports・HoldRequests を別々に聞く形では 1 歩に 2〜3 つ(この検は赤)。
;; - 書き(Persist)1 回: 落ちの注入の判断(区間の歩の書きの数え・止めの注入)を問い 1 つ(PersistCrashDue)、書き終えた知らせ(終わった
;;   task と準備の待ち手を起こす)を問い 1 つ(NoteCoordinatorWrite)。反例: TakeReplayedWrite・PauseDue・PartsOf を別々に聞く形。
;; - worker の拍の止めの問い(WorkerStopRequested): 世代の確かめと全 worker の止まれを問い 1 つ(StopRequestOf)。
;;   反例: HostTruthOf と WorkersStopping を別々に聞く形。
;; - heartbeat を送る拍: 筋(PlanOf)と部品(PartsOf)は宿の世代ごとに 1 度 — 拍ごとに世界へ聞かない。

(defk asked-during [program]
  {:pre [(: program (| Program EffectBase))] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "program を走らせる間に世界へ出た問いの列を読むため(それより前の問いを除く)。"
  (<- before tuple (WorldQuestions))
  (<- program)
  (<- after tuple (WorldQuestions))
  (tuple (cut after (len before) None)))


(defhandler persist-written
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  ;; observe-requests の外側の置き場の代役: Persist を書き終えたとだけ答える(書きの中身は見ない)。
  (Persist [writes]
    (resume None)))


(defk one-request-step []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "列に要求を 1 つ積み、調停ループの拍と同じ NextRequests を 1 回出して、その歩の世界への問いの列を読むため。"
  (val queue (RequestQueue :skip-idle False))
  (<- slot Promise (CreatePromise))
  (<- (enqueue-request queue (Request :method "GET" :path "/state" :query {} :body None :parts #("state") :slot slot
                                     :peer HOST.name)))
  (<- asked tuple (asked-during (with-handlers [(queued-requests queue) observe-requests] (NextRequests 1.0 10))))
  asked)


(deftest test-a-step-that-takes-a-request-asks-the-world-once
  (<- plan SimPlan (sim-plan (beacons sim-foundation) #(HOST) None "sim" 0 None None None None))
  (<- asked tuple ((sim-time-handler :clock (! (clock-at 0)))
                   (with-handlers [(session-store) (sim-world plan) world-question-counter] (one-request-step))))
  (assert (= asked #("AdmitBatch")) asked))


(deftest test-a-persist-asks-the-world-once-before-and-once-after
  (<- plan SimPlan (sim-plan (beacons sim-foundation) #(HOST) None "sim" 0 None None None None))
  (<- asked tuple ((sim-time-handler :clock (! (clock-at 0)))
                   (with-handlers [(session-store) (sim-world plan) world-question-counter persist-written observe-requests]
                     (asked-during (Persist :writes #())))))
  (assert (= asked #("PersistCrashDue" "NoteCoordinatorWrite")) asked))


(defk under-host [plan program]
  {:pre [(: plan SimPlan) (: program (| Program EffectBase))] :post [(: % tuple)]
   :tags {:context "doeff-cluster-test" :role "program"}}
  "worker w1 の今の世代の宿(本物の sim-host — 世代の始めに筋と部品を 1 度受ける run-sim-worker と同じ組み立て・筋は世界に渡した物)の中で
   program を走らせ、その間の世界への問いの列を読むため(宿を組む読みは数えない)。"
  (<- truth HostTruth (HostTruthOf HOST.name))
  (<- parts SimParts (PartsOf))
  (<- asked tuple (asked-during (with-handlers [(sim-host HOST truth.boot plan parts)] program)))
  asked)


(deftest test-a-worker-stop-question-asks-the-world-once
  (<- plan SimPlan (sim-plan (beacons sim-foundation) #(HOST) None "sim" 0 None None None None))
  (<- asked tuple ((sim-time-handler :clock (! (clock-at 0)))
                   (with-handlers [(session-store) (sim-world plan) world-question-counter] (under-host plan (WorkerStopRequested)))))
  (assert (= asked #("StopRequestOf")) asked))


(deftest test-a-heartbeat-beat-does-not-ask-the-world-for-the-plan-or-the-parts
  ;; 宿の最初の拍は heartbeat を送る(まだ fresh でない)。coordinator は立っていない(列が上がっていない)ので送りは接続の失敗で返る。
  (<- plan SimPlan (sim-plan (beacons sim-foundation) #(HOST) None "sim" 0 None None None None))
  (<- asked tuple ((sim-time-handler :clock (! (clock-at 0)))
                   (with-handlers [(session-store) (sim-world plan) world-question-counter] (under-host plan (ReadDesired :stopping False)))))
  (assert (not (& (set asked) #{"PlanOf" "PartsOf"})) asked))
