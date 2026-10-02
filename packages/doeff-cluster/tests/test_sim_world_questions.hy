;; 調停ループの一番内側(sim の observe-requests)が 1 歩ごとに sim の世界(sim-world)へ出す問いは、歩の数の定数倍に収まる(#2668)。
;;
;; 世界へ出る効果は 1 つずつ答え手の段を通るので、1 歩の問いの数がそのまま模擬の検の費用になる(test_emulated_daily_verify の重い 1 本で
;; 世界の答え手を約 160 万回通っていた)。取った要求を篩う問い(網の切れ・口の故障・時計)は 1 歩に 1 度にまとめ、何も取らなかった歩は
;; 覚え(HoldRequests)を変えないので世界へ聞かない。
;;
;; - 静かな歩(要求の無い歩)N 回で、世界への問いはちょうど N 個(RouteFaultsNow が 1 歩に 1 つ)。
;;   反例: 網の切れ(CutPeers)・口の故障(FailedRoutes)を別々に聞き、空の覚えも書く形では 1 歩に 3 つ(この検は赤)。
;; - worker の宿(sim-host)の読んで直して書く 1 組(状態の報告 PublishStatus)は、世界への問い 1 つ(ChangeHostTruth)。
;;   反例: 宿の真実を読み(HostTruthOf)、直して置き直す(PutHostTruth)形では 1 組に 2 つ(この検は赤)。
(require doeff-hy.macros [deftest defk defhandler defeffect <- val var])
(import doeff [with-handlers])
(import doeff_vm [EffectBase])
(import doeff_time [sim-time-handler])
(import doeff_core_effects.handlers [state :as session-store])
(import doeff_cluster.shared.intent.protocol [NextRequests])
(import doeff_cluster.coordinator.protocol.request_queue [RequestQueue queued-requests])
(import doeff_cluster.worker.intent.worker_model [PublishStatus])
(import doeff_cluster.sim.local [SimPlan SimWorker HostTruth HostTruthOf sim-plan sim-world sim-host observe-requests])
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
  (<- asked tuple ((sim-time-handler :clock (clock-at 0))
                   (with-handlers [(session-store) (sim-world plan) world-question-counter
                                   (queued-requests (RequestQueue :skip-idle False)) observe-requests]
                     (quiet-steps n))))
  asked)


(deftest test-a-quiet-step-asks-the-world-once
  (<- asked tuple (questions-in-quiet-steps 5))
  ;; 1 歩に 1 つ(網の切れ・口の故障・刻をまとめた問い)— 何も取らなかった歩は覚えを書かない。
  (assert (= asked (* #("RouteFaultsNow") 5)) asked))


(val HOST (SimWorker :name "w1" :provides #{"net"}))


(defk publish-once []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "worker w1 の今の世代の宿(本物の sim-host)で状態の報告を 1 度出し、世界への問いの列を読むため(先頭の 1 つは世代を知るための読み)。"
  (<- truth HostTruth (HostTruthOf HOST.name))
  (<- (with-handlers [(sim-host HOST truth.boot)] (PublishStatus :statuses #())))
  (<- asked tuple (WorldQuestions))
  asked)


(deftest test-a-host-read-change-write-asks-the-world-once
  (<- plan SimPlan (sim-plan (beacons sim-foundation) #(HOST) None "sim" 0 None None None None))
  (<- asked tuple ((sim-time-handler :clock (clock-at 0))
                   (with-handlers [(session-store) (sim-world plan) world-question-counter] (publish-once))))
  ;; 世代を知る読み 1 つの後、報告の 1 組は ChangeHostTruth 1 つ(読みと書きを別々に聞かない)。
  (assert (= asked #("HostTruthOf" "ChangeHostTruth")) asked))
