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
(import doeff_cluster.shared.intent.protocol [NextRequests Request Reply CoordinatorStopRequested])
(import doeff_cluster.coordinator.protocol.request_queue [RequestQueue queued-requests enqueue-request])
(import doeff_cluster.coordinator.protocol.store [Persist])
(import doeff_cluster.worker.intent.worker_model [PublishStatus WorkerStopRequested ReadDesired])
(import doeff_cluster.sim.local [SimPlan SimParts SimWorker HostTruth HostTruthOf PartsOf sim-plan sim-world sim-host StopCoordinator CrashCoordinator TakeHeldRequests
                                 observe-requests StepBook])
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
  (val queue (RequestQueue :skip-idle False))
  (<- asked tuple ((sim-time-handler :clock (! (clock-at 0)))
                   (with-handlers [(session-store) (sim-world plan) world-question-counter
                                   (queued-requests queue) (observe-requests (StepBook) queue)]
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
;; - 書き(Persist)1 回: 落ちの注入が待っている時だけ、落ちの判断を問い 1 つ(PersistCrashDue — 書きより前)。待っていなければ
;;   問わない(#3132 — 生存の印の書き 1 回 46 歩のうち 20 歩がこの問いだった)。区間の歩の書きの数え(replayed)は帳面 StepBook が
;;   持ち、注入が待っていても落とさない。反例: 書きごとに必ず問う形(この検は赤)。書き終えた知らせ(終わった task と準備の待ち手を
;;   起こす)は歩の終わりの問いへ。
;; - 歩の終わり(次の歩の頭の止まりの判定 CoordinatorStopRequested): この歩の返事の手放し・書きの知らせ・止まりの判定を問い 1 つ
;;   (StepEnded — #2670 の根 A)。要求を 1 つ取った 1 歩は、取り・書き・歩の終わりの 3 つ。反例: 返事ごとの ReleaseRequest・書きごとの
;;   NoteCoordinatorWrite・歩の頭の PauseDue を別々に聞く前の形は 1 歩に 5 つ(この検は赤)。止まった後の返事(止まる調停ループの待ちへの
;;   返事 — 次の歩の頭が無い)はすぐ手放す(ReleaseRequest)。
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
  (<- asked tuple (asked-during (with-handlers [(queued-requests queue) (observe-requests (StepBook) queue)] (NextRequests 1.0 10))))
  asked)


(deftest test-a-step-that-takes-a-request-asks-the-world-once
  (<- plan SimPlan (sim-plan (beacons sim-foundation) #(HOST) None "sim" 0 None None None None))
  (<- asked tuple ((sim-time-handler :clock (! (clock-at 0)))
                   (with-handlers [(session-store) (sim-world plan) world-question-counter] (one-request-step))))
  (assert (= asked #("AdmitBatch")) asked))


(defhandler never-stopped
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  ;; observe-requests の外側の止めの合図の代役(本番の stop-flag): 止まりは頼まれていない、とだけ答える。
  (CoordinatorStopRequested []
    (resume False)))


(defk persisted-and-ended []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "書きを 1 回出し、次の歩の頭の止まりの判定を出して、それぞれの間の世界への問いの列を読むため。"
  (<- persisted tuple (asked-during (Persist :writes #())))
  (<- ended tuple (asked-during (CoordinatorStopRequested)))
  #(persisted ended))


(defk persist-then-step-end []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "sim の世界の列(落ちの注入の印を世界が立てる列)で observe-requests を組み、persisted-and-ended を回すため。"
  (<- parts SimParts (PartsOf))
  (<- asked tuple (with-handlers [never-stopped persist-written (observe-requests (StepBook) parts.queue)] (persisted-and-ended)))
  asked)


(deftest test-a-persist-asks-the-world-nothing-without-a-queued-crash-and-the-step-end-notes-it
  (<- plan SimPlan (sim-plan (beacons sim-foundation) #(HOST) None "sim" 0 None None None None))
  (<- asked tuple ((sim-time-handler :clock (! (clock-at 0)))
                   (with-handlers [(session-store) (sim-world plan) world-question-counter] (persist-then-step-end))))
  ;; 落ちの注入が待っていなければ、書きの前に問わない(#3132)・書き終えた知らせは歩の終わりの問い 1 つに入る。
  (assert (= asked #(#() #("StepEnded"))) asked))


(defk crashed-by [book queue]
  {:pre [(: book StepBook) (: queue RequestQueue)] :post [(: % bool)] :tags {:context "doeff-cluster-test" :role "program"}}
  "observe-requests(帳面 book・列 queue)の内側で書きを 1 回出し、落ちの注入で落ちた(OSError — 返事をせずに落ちる)かを読むため。"
  (try
    (<- (with-handlers [persist-written (observe-requests book queue)] (Persist :writes #())))
    False
    (except [OSError]
      True)))


(defk persist-with-a-queued-crash [replayed]
  {:pre [(: replayed int)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "落ちの注入(CrashCoordinator)の後に、区間の歩の書きの数え replayed を持つ帳面で書きを 2 回出し、それぞれの #(世界への問いの列 落ちたか)
   を読むため。"
  (<- (CrashCoordinator 5.0))
  (<- parts SimParts (PartsOf))
  (val book (StepBook))
  (setv book.replayed replayed)
  (<- first-before tuple (WorldQuestions))
  (<- first-crashed bool (crashed-by book parts.queue))
  (<- first-after tuple (WorldQuestions))
  (<- second-crashed bool (crashed-by book parts.queue))
  (<- second-after tuple (WorldQuestions))
  #(#((tuple (cut first-after (len first-before) None)) first-crashed)
    #((tuple (cut second-after (len first-after) None)) second-crashed)))


(defk crash-persists [replayed]
  {:pre [(: replayed int)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "本物の sim の世界の上で persist-with-a-queued-crash を回すため。"
  (<- plan SimPlan (sim-plan (beacons sim-foundation) #(HOST) None "sim" 0 None None None None))
  (<- answer tuple ((sim-time-handler :clock (! (clock-at 0)))
                    (with-handlers [(session-store) (sim-world plan) world-question-counter] (persist-with-a-queued-crash replayed))))
  answer)


(deftest test-a-queued-crash-is-asked-and-taken-on-the-next-persist
  ;; 注入が待っていれば、次の書きの前に問い 1 つで落ちる。落ちた後は注入が残っていないので、次の書きは問わない。
  (<- answer tuple (crash-persists 0))
  (assert (= answer #(#(#("PersistCrashDue") True) #(#() False))) answer))


(deftest test-a-replayed-idle-write-is-not-crashed-even-with-a-queued-crash
  ;; 眠った静かな区間の歩の書き(1 拍ずつの走りでは注入より前の刻の書き)は、注入が待っていても落とさず、世界へも問わない。落ちるのは
  ;; その後の最初の書きで、落ちる前に溜めた区間の歩の書きの知らせを出す(書き終えた書きの待ち手は、落ちても起こす)。
  (<- answer tuple (crash-persists 1))
  (assert (= answer #(#(#() False) #(#("PersistCrashDue" "NoteCoordinatorWrite") True))) answer))


(defk step-of-one []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "調停ループの 1 歩と同じ順に効果を出すため: 要求を取り、書き、取った最初の要求へ返事をし(残りは返事を待たせる待ちと同じ)、次の歩の頭の
   止まりの判定を出す。答え = #(止まるか 返事を待たせた要求の tuple)。"
  (<- taken list (NextRequests 1.0 10))
  (<- (Persist :writes #()))
  (<- (Reply (get taken 0) 200 {}))
  (<- stopping bool (CoordinatorStopRequested))
  #(stopping (tuple (cut taken 1 None))))


(defk state-request [slot]
  {:pre [(: slot Promise)] :post [(: % Request)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "worker w1 からの状態の読みの要求 1 つを作るため。"
  (Request :method "GET" :path "/state" :query {} :body None :parts #("state") :slot slot :peer HOST.name))


(defk whole-step [stop-first]
  {:pre [(: stop-first bool)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "列に要求を 2 つ積み、1 歩を回した間の世界への問いの列と止まりの答えと、止まった後に待たせた要求へ返事をした間の問いの列と、その後に
   世界が覚えている要求の数を読むため。stop-first = 歩の前に止めを注入する(歩の終わりの判定が止まりと答え、その後の返事 — 止まる
   調停ループの待ちへの返事 release-watchers と同じ — はすぐ手放す)。偽なら待たせた要求は返事をせずに残す。"
  (val queue (RequestQueue :skip-idle False))
  ;; 歩と止まった後の返事は同じ帳面(coordinator の一生 1 つ)を使う。
  (val book (StepBook))
  (<- first-slot Promise (CreatePromise))
  (<- second-slot Promise (CreatePromise))
  (<- first Request (state-request first-slot))
  (<- second Request (state-request second-slot))
  (<- (enqueue-request queue first))
  (<- (enqueue-request queue second))
  (when stop-first
    (<- (StopCoordinator 5.0)))
  (<- answer tuple (asked-during-with-answer (with-handlers [never-stopped (queued-requests queue) persist-written (observe-requests book queue)]
                                                (step-of-one))))
  (var late #())
  (when stop-first
    (for [waiting (get (get answer 1) 1)]
      (<- asked tuple (asked-during (with-handlers [never-stopped (queued-requests queue) persist-written (observe-requests book queue)]
                                      (Reply waiting 200 {}))))
      (:= late (+ late asked))))
  (<- held tuple (TakeHeldRequests))
  #(#((get answer 0) (get (get answer 1) 0)) late (len held)))


(defk asked-during-with-answer [program]
  {:pre [(: program (| Program EffectBase))] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "program を走らせる間に世界へ出た問いの列と、program の答えを読むため。"
  (<- before tuple (WorldQuestions))
  (<- answer program)
  (<- after tuple (WorldQuestions))
  #((tuple (cut after (len before) None)) answer))


(deftest test-a-step-that-takes-persists-and-replies-asks-the-world-twice
  (<- plan SimPlan (sim-plan (beacons sim-foundation) #(HOST) None "sim" 0 None None None None))
  (<- seen tuple ((sim-time-handler :clock (! (clock-at 0)))
                  (with-handlers [(session-store) (sim-world plan) world-question-counter] (whole-step False))))
  (val answer (get seen 0))
  (val late (get seen 1))
  (val held (get seen 2))
  ;; 取りと歩の終わりの 2 つ(落ちの注入が待っていないので、書きの前には問わない — #3132)。返事を済ませた要求は歩の終わりに覚えから
  ;; 外れ、返事を待たせた要求 1 つだけが残る(止まった時に接続の失敗を返す相手)。
  (assert (= answer #(#("AdmitBatch" "StepEnded") False)) answer)
  (assert (= held 1) held))


(deftest test-a-reply-after-the-stop-is-released-at-once
  ;; 止めを注入した歩: 歩の終わりの判定が止まりと答える。その後の返事(止まる調停ループの待ちへの返事と同じ — 次の歩の頭が無い)は
  ;; すぐ手放す(ReleaseRequest)— 溜めると、止まった後に接続の失敗を返す相手に残る。
  (<- plan SimPlan (sim-plan (beacons sim-foundation) #(HOST) None "sim" 0 None None None None))
  (<- seen tuple ((sim-time-handler :clock (! (clock-at 0)))
                  (with-handlers [(session-store) (sim-world plan) world-question-counter] (whole-step True))))
  (val answer (get seen 0))
  (val late (get seen 1))
  (val held (get seen 2))
  (assert (= answer #(#("AdmitBatch" "StepEnded") True)) answer)
  (assert (= late #("ReleaseRequest")) late)
  (assert (= held 0) held))


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
