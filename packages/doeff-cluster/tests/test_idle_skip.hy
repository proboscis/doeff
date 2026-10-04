;; 模擬の時計の下だけ、coordinator は要求の無い間の静かな区間を一度に眠る(idle_policy.quiet-stretch・coordinator_handler_sets の
;; RequestQueue の skip-idle・2026-09-30 の決定・#2790)。本番の拍の間隔 1 秒と判断の刻は変えない。
;;
;; - 模擬の時計では、要求が無ければ最初の静かでない歩まで一気に進む(1 秒ごとに起きない)。眠った間の歩(生存の印・吸った待ちの
;;   期限の引き直し)は、起きた時に調停ループへ渡して同じ順・同じ値で保存する。
;; - 本番の受付(http-requests)は材料 idle を読まず、拍の間隔は 1 秒のまま。skip-idle でない模擬の列も 1 秒ごと。
;; - 同じ筋書きを 1 秒ごとの拍と飛ばす拍で回すと、置き場への書きの列・coordinator の一生・process の列が一致する(判断の刻が同じ)。
;; - 系全体の静かな区間(worker の拍が状態を変えず、heartbeat が静かな早道に入り、coordinator の歩が生存の印のほか何も変えない間)も、
;;   切り離した task の lease・宣言し直し・worker の死・coordinator の止まりを挟んで、1 拍ずつ進めた走りと同じ判断を同じ刻に下す
;;   (#2781 — 下の「系全体の静かな区間」の節)。
(require doeff-hy.macros [deftest defk defhandler defeffect <- val var])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import doeff [with-handlers Program])
(import doeff_cluster.shared.intent.service_model [System])
(import doeff_time [Delay sim-time-handler])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_core_effects.scheduler [CreatePromise Promise Spawn])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState ClusterNaming IdleProbe IdleNextRequests IdleTaken SaveState QuietStep
                                                       QuietStretch Watcher WatchRefusal WorkerInfo])
(import doeff_cluster.coordinator.core.cluster_policy [liveness-due note-liveness forget-silent-workers alive WORKER-FORGET-MS])
(import doeff_cluster.coordinator.core.program [coordinator-step])
(import doeff_cluster.coordinator.core.idle_policy [quiet-stretch quiet-step quiet-due])
(import doeff_cluster.coordinator.core.idle_policy :as idle-policy)
(import doeff_cluster.coordinator.core.cluster_policy [sweep-board sweep-drains sweep-warms sweep-due])
(import doeff_cluster.coordinator.core.program_policy [sweep-programs PROGRAM-GRACE-MS])
(import doeff_cluster.coordinator.intent.cluster_model [BoardRow ProgramRow Drain WarmEntry])
(import dataclasses [replace])
(import doeff_cluster.coordinator.core.watch_policy [watch-of])
(import doeff_core_effects.handlers [state :as session-store])
(import doeff_cluster.coordinator.entry.handler_sets [MemoryWalStore])
(import doeff_cluster.coordinator.protocol.request_queue [RequestQueue queued-requests enqueue-request])
(import doeff_cluster.foundation.coordinator_inbox [RequestInbox] doeff_cluster.shared.protocol.inbox [http-requests http-request])
(import doeff_cluster.sim.local [sim-cluster ProcessesOf SharedRows StopCoordinator CoordinatorRuns KillWorker ReadCoordinator ClientLink SimLink
                             SimWorker Redeclare HostTruthOf HostTruth CoordinatorStep CoordinatorSteps handed-quiet-steps])
(import doeff_cluster.coordinator.protocol.durable_kv [full-kv])
(import doeff_cluster.worker.intent.worker_model [WorkerPolicy])
(import doeff_cluster.shared.intent.detached_model [AwaitDetached DetachedAwaited DetachedSucceeded])
(import doeff_cluster.shared.core.detached_rules [submit-detached-task])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.sim_programs [quitters quitters-v2 beacons slow-task sim-task-foundation NET])
(import tests.test_local_rollout [reverse-scenario DEPLOYMENTS WORKERS :as ROLLOUT-WORKERS])
(import tests.clock_fixtures [clock-at])


(val FRESH-PROBE (IdleProbe (ClusterState) (ClusterTiming) (ClusterNaming)))


(defk wake-times [probe times]
  {:pre [(: probe IdleProbe) (: times int)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書きの取り手: coordinator の拍と同じ NextRequests 1.0(材料 idle 付き)を times 回出し、各回の起きた刻を読むため。"
  (var seen #())
  (for [_ (range times)]
    (<- (IdleNextRequests 1.0 :idle probe))
    (<- woke int (now-epoch-ms))
    (:= seen (+ seen #(woke))))
  seen)


(defk queue-wakes [skip-idle probe times]
  {:pre [(: skip-idle bool) (: probe IdleProbe) (: times int)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "模擬の列(skip-idle の真偽)を仮想の時計(起点 0)で回し、取り手の起きた刻の列を返すため。"
  (<- woke tuple ((sim-time-handler :clock (! (clock-at 0)))
                   (with-handlers [(queued-requests (RequestQueue :skip-idle skip-idle))] (wake-times probe times))))
  woke)


(defk send-at [queue seconds]
  {:pre [(: queue RequestQueue) (: seconds float)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書きの送り手: seconds 秒眠ってから、列に読みの要求を 1 件積むため(返事は待たない)。"
  (<- (Delay seconds))
  (<- slot Promise (CreatePromise))
  (<- (enqueue-request queue (! (http-request "GET" "/state" {} None :slot slot))))
  None)


(defk take-once [queue probe seconds]
  {:pre [(: queue RequestQueue) (: probe IdleProbe) (: seconds float)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書きの取り手: seconds 秒後に要求を積む送り手を走らせ、coordinator の歩と同じ NextRequests 1.0(材料 idle 付き)を 1 回出して、
   起きた刻と答えを読むため。"
  (<- (Spawn (send-at queue seconds)))
  (<- taken (| list IdleTaken) (IdleNextRequests 1.0 :idle probe))
  (<- woke int (now-epoch-ms))
  #(woke taken))


(defk taken-at [skip-idle probe seconds]
  {:pre [(: skip-idle bool) (: probe IdleProbe) (: seconds float)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "模擬の列(skip-idle の真偽)を仮想の時計(起点 0)で回し、seconds 秒後に要求が来る時の取り手の起きた刻と答えを返すため。"
  (val queue (RequestQueue :skip-idle skip-idle))
  (<- seen tuple ((sim-time-handler :clock (! (clock-at 0))) (with-handlers [(queued-requests queue)] (take-once queue probe seconds))))
  seen)


(deftest test-the-simulated-coordinator-sleeps-through-quiet-steps-until-a-request
  ;; 要求の無い新しい状態: 生存の印(ALIVE-MARK-MS 5 秒)の歩は静かな歩(置き場への書きはまとめて後で)なので起きない。skip-idle の列は
  ;; 12.3 秒目の要求で起き、眠った間の 1 秒ごとの歩 12 個(1〜12 秒目)を添えて返す。印の歩は 5 秒目と 10 秒目。
  (<- seen tuple (taken-at True FRESH-PROBE 12.3))
  (val woke (get seen 0))
  (val taken (get seen 1))
  (assert (= woke 12300) seen)
  (assert (isinstance taken IdleTaken) taken)
  (assert (= (lfor step taken.steps step.at) (lfor k (range 1 13) (* 1000 k))) taken.steps)
  (assert (= (lfor step taken.steps step.state.alive-ms) [0 0 0 0 5000 5000 5000 5000 5000 10000 10000 10000]) taken.steps)
  (assert (= (len taken.batch) 1) taken)
  ;; 反例 — skip-idle でない列(本番と同じ間隔): 1 秒目に起き、歩を添えない。
  (<- ticked tuple (queue-wakes False FRESH-PROBE 1))
  (assert (= ticked #(1000)) ticked))


(defclass RecordingInbox [RequestInbox]
  "本番の受付の箱の取り出し(HTTP の thread の列を待つ 1 点)だけを記録に替えた箱: 調停ループが渡した待ちの秒を並べ、待たずに空で返る。"
  (defn #^ None __init__ [self]
    (.__init__ (super) 0)
    (setv self.timeouts [])
    None)

  (defn #^ list take [self #^ float timeout #^ int limit]
    (.append self.timeouts timeout)
    []))


(deftest test-the-production-inbox-keeps-the-one-second-tick
  ;; 本番の受付(http-requests)は材料 idle を読まない: 調停ループが NextRequests 1.0 に何も変えない長い状態を添えても、箱を待つ
  ;; 秒は 1 秒のまま。
  (val inbox (RecordingInbox))
  (<- ((sim-time-handler :clock (! (clock-at 0))) (with-handlers [(http-requests inbox)] (wake-times FRESH-PROBE 2))))
  (assert (= inbox.timeouts [1.0 1.0]) inbox.timeouts))


;; --- 同値: 1 秒ごとの拍と飛ばす拍で、判断の刻が同じ ----------------------------------------------------------------

(val TWO-WORKERS #((SimWorker :name "w1" :provides (frozenset ["cluster-net"]) :task-reserve 0)
                   (SimWorker :name "w2" :provides (frozenset ["cluster-net"]) :task-reserve 0)))

;; 仮想の長い時間を回す使い手の検と同じ worker の設定(拍 10 秒・起こし直しは待たせる)— 要求の無い拍が多い。
(val QUIET-POLICY (WorkerPolicy :tick-seconds 10.0 :restart-backoff-ms 1000000000 :restart-backoff-max-ms 1000000000))


(defrecord Trace
  "1 回の走りの読み: deltas = coordinator の置き場への書きの列(書いた順 — 判断の結果と刻を含む)・steps = coordinator の歩ごとの記録
   (CoordinatorStep の tuple — 刻とその歩の後の状態・#2670 の根 B — 筋書きが読む刻までに coordinator が起きて記録した歩)・final =
   走りが終わった後の置き場の鍵の表(耐久になった全部のキー — 記録の後の静かな区間の最後の状態も含む)・answer = 筋書きの答え・takes =
   coordinator の拍の数。"
  (#^ list deltas)
  (#^ (get tuple #(CoordinatorStep ...)) steps)
  ;; 置き場の鍵の表をそのまま持つ(置き場の口 load の答えの形 — 2 つの走りの最後の状態を丸ごと比べるため)。
  (#^ dict final)
  (#^ (| tuple None) answer)
  (#^ int takes))


(defk ended-with-takes [scenario]
  {:pre [(: scenario Program)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書きを回し、その答えと、終わった時の coordinator の拍の数と、coordinator の歩ごとの記録を返すため。"
  (<- answer (| tuple None) scenario)
  (<- link SimLink (ClientLink))
  (<- steps tuple (CoordinatorSteps))
  #(answer link.queue.takes steps))


(defk trace-of [system scenario skip-idle * [workers None] [policy None] [deployments None]]
  {:pre [(: system System) (: scenario Program) (: skip-idle bool) (: workers (| tuple None)) (: policy (| WorkerPolicy None))
         (: deployments (| dict None))]
   :post [(: % Trace)] :tags {:context "doeff-cluster-test" :role "program"}}
  "同じ系と筋書きを skip-idle の真偽で回し、置き場の書きの列と筋書きの答えと拍の数を返すため。"
  (val made [])
  (<- seen tuple (sim-cluster system (ended-with-takes scenario) :workers workers :policy policy :deployments deployments
                              :skip-idle skip-idle
                              :store (fn [] (let [store (MemoryWalStore)] (.append made store) store))))
  (Trace :deltas (. (get made 0) deltas) :steps (get seen 2) :final (.load (get made 0)) :answer (get seen 0) :takes (get seen 1)))


(defk state-changes [steps]
  {:pre [(: steps tuple)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "coordinator の歩ごとの記録 steps から、耐久の状態(置き場の鍵の形 full-kv)が前の歩と変わった歩だけを #(刻 耐久の状態) の列にする
   ため — 判断とその刻と最後の状態を、置き場への書きの列と同じだけ持つ(何も変えない歩は数えない — 1 拍ずつの走りと飛ばす走りで、
   保存の無い歩の数は違ってよい)。"
  (var last None)
  (var changes #())
  (for [step steps]
    (<- kv dict (full-kv step.state))
    (when (!= kv last)
      (:= changes (+ changes #(#(step.at kv))))
      (:= last kv)))
  changes)


(defk step-apart [every skipped]
  {:pre [(: every tuple) (: skipped tuple)] :post [(: % list)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "1 秒ごとの拍と飛ばす拍の coordinator の歩ごとの記録から、耐久の状態の変わり目の列(刻と状態)の食い違いを並べるため(空 = 同じ判断を
   同じ刻に下した — 置き場への書きの列に依らない基準・#2670 の根 B)。比べるのは両方の走りが記録し終えた刻まで: 飛ばす走りは眠っている
   静かな区間の歩を起きた時に記録するので、筋書きが読む刻に眠っていれば、その区間の歩はまだ無い(その先は最後の状態 final で比べる)。"
  (val until (min (if every (. (get every -1) at) 0) (if skipped (. (get skipped -1) at) 0)))
  (<- kept tuple (state-changes (tuple (gfor step every :if (<= step.at until) step))))
  (<- seen tuple (state-changes (tuple (gfor step skipped :if (<= step.at until) step))))
  (val first-diff (next (gfor #(i #(a b)) (enumerate (zip kept seen)) :if (!= a b) i) None))
  (+ (if (is first-diff None)
         []
         [(.format "耐久の状態の変わり目の {} 件目(刻 {} と {})が食い違う" (+ first-diff 1) (get (get kept first-diff) 0)
                   (get (get seen first-diff) 0))])
     (if (= (len kept) (len seen)) [] [(.format "変わり目の数 {} と {}" (len kept) (len seen))])))


(deftest test-the-step-criterion-names-a-change-at-another-tick-or-with-another-state
  ;; 歩ごとの記録の基準(置き場への書きの列に依らない — #2670 の根 B)の失敗ケース: 生存の印の変わり目が 1 拍ずれた走り・同じ刻に
  ;; 違う状態になった走りを名指す。何も変えない歩が片方に多いだけ(保存の無い歩の数の違い)は食い違いにしない。
  (val start (ClusterState))
  (val marked (replace start :alive-ms 5000))
  (val reference #((CoordinatorStep :at 0 :state start) (CoordinatorStep :at 5000 :state marked) (CoordinatorStep :at 9000 :state marked)))
  (<- late list (step-apart reference
                            #((CoordinatorStep :at 0 :state start) (CoordinatorStep :at 6000 :state marked)
                              (CoordinatorStep :at 9000 :state marked))))
  (assert late late)
  (<- other list (step-apart reference
                             #((CoordinatorStep :at 0 :state start) (CoordinatorStep :at 5000 :state (replace start :alive-ms 4000))
                               (CoordinatorStep :at 9000 :state marked))))
  (assert other other)
  (<- idle list (step-apart reference
                            #((CoordinatorStep :at 0 :state start) (CoordinatorStep :at 0 :state start) (CoordinatorStep :at 5000 :state marked)
                              (CoordinatorStep :at 7000 :state marked) (CoordinatorStep :at 9000 :state marked))))
  (assert (= idle []) idle))


(deftest test-a-quiet-stretch-is-handed-as-one-step-unless-a-crash-waits
  ;; 調停ループへ渡す静かな区間の歩(#2670 の根 B): 落ちの注入が待っていない間は最後の歩 1 つにまとめ(刻・状態・待ちは最後の歩・生存の
  ;; 印は区間のどれかが書いていれば立てる — 保存するかの判断と落ちの数えが読む)、待っている間(ends-at-marks)は歩ごとのまま。反例 —
  ;; 待っている間もまとめる作り方・印を最後の歩の物だけにする作り方は赤。
  (val start (ClusterState))
  (val marked (replace start :alive-ms 5000))
  (val steps #((QuietStep :at 1000 :state start :watchers #() :marked False)
               (QuietStep :at 5000 :state marked :watchers #() :marked True)
               (QuietStep :at 6000 :state marked :watchers #() :marked False)))
  (val calm (RequestQueue :skip-idle True))
  (<- folded tuple (handed-quiet-steps calm steps))
  (assert (= (lfor step folded #(step.at step.state.alive-ms step.marked)) [#(6000 5000 True)]) folded)
  (val waiting (RequestQueue :skip-idle True))
  (setv waiting.ends-at-marks True)
  (<- kept tuple (handed-quiet-steps waiting steps))
  (assert (= kept steps) kept))


(defk same-decisions [every skipped]
  {:pre [(: every Trace) (: skipped Trace)] :post [(: % list)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "1 秒ごとの拍と飛ばす拍の走りの食い違い(coordinator の耐久の状態の変わり目の列・置き場の最後の状態・筋書きの答え)を並べるため
   (空 = 同じ判断を同じ刻に下し、同じ状態で終わった)。置き場への書きの列は比べない: 飛ばす走りは、落ちの注入が待っていない静かな区間を
   1 回の保存にまとめる(#2670 の根 B — 決め cisco-c8 2026-10-03 17:1x・#2790 で残した書きの列の基準を替えた)。"
  (+ (! (step-apart every.steps skipped.steps))
     (if (= every.final skipped.final) [] ["置き場の最後の状態が食い違う"])
     (if (= every.answer skipped.answer) [] [(.format "筋書きの答え {!r} と {!r}" every.answer skipped.answer)])))


(defk kill-then-stop []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 8 秒待って worker w1 を node ごと死なせ(沈黙・lease・移し替えの期限)、60 秒待って coordinator を 5 秒止め(止めの合図と
   作り直し)、20 秒待って一生・process・盤の行を読むため。"
  (<- (Delay 8.0))
  (<- (KillWorker "w1"))
  (<- (Delay 60.0))
  (<- (StopCoordinator 5.0))
  (<- (Delay 20.0))
  (<- runs tuple (CoordinatorRuns))
  (<- processes tuple (ProcessesOf "quitter"))
  (<- rows dict (SharedRows "quit/"))
  #(runs processes rows))


(deftest test-skipping-idle-ticks-keeps-every-decision-at-the-same-tick
  ;; worker の死(沈黙 → 移し替え)と coordinator の止まり(止めの合図 → 作り直し)を含む筋書き: 1 秒ごとの拍と、何も変えない拍を
  ;; 飛ばす拍で、耐久の状態の変わり目の列(判断とその刻 — worker の沈黙を判じた刻を含む)・置き場の最後の状態・coordinator の一生・
  ;; process の列・盤の行が一致する。飛ばす側は拍の数が少なく、静かな区間を 1 回の保存にまとめるので置き場への書きも少ない(#2670 の根 B)。
  (<- every Trace (trace-of (quitters sim-foundation) (kill-then-stop) False :workers TWO-WORKERS :policy QUIET-POLICY))
  (<- skipped Trace (trace-of (quitters sim-foundation) (kill-then-stop) True :workers TWO-WORKERS :policy QUIET-POLICY))
  (assert (> (len every.deltas) 10) (len every.deltas))
  (assert (is-not every.answer None) "走りは答えを返している")
  (assert (= (len (get every.answer 0)) 2) every.answer)
  (<- breaches list (same-decisions every skipped))
  (assert (= breaches []) breaches)
  (assert (< (* 2 skipped.takes) every.takes) #(skipped.takes every.takes))
  (assert (< (len skipped.deltas) (len every.deltas)) #((len skipped.deltas) (len every.deltas))))


(defk stop-on-a-tick-boundary [gaps]
  {:pre [(: gaps tuple)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 8.3 秒で coordinator を要求で起こし、gaps の秒を順に眠って(1 秒ごとの拍の timer より後に眠りを登録して)本番の次の拍の刻
   ちょうどに止めを入れ、20 秒後に一生を読むため。"
  (<- (Delay 8.3))
  (<- (ReadCoordinator "/state"))
  (for [gap gaps]
    (<- (Delay gap)))
  (<- (StopCoordinator 5.0))
  (<- (Delay 20.0))
  (<- runs tuple (CoordinatorRuns))
  runs)


(deftest test-an-event-on-a-tick-boundary-is-seen-before-the-tick-either-way
  ;; 拍の刻ちょうどの出来事(止めの注入)は、1 秒ごとの拍でも飛ばす拍でも、その刻の拍より先に通る(模擬の列は要求の無いまま起きた時
  ;; 0 秒の Delay をはさむ)— 眠りの割り方(timer の登録の順)によらず、置き場の書きの列と一生が一致する(2026-09-30 のレビューの再現)。
  (for [gaps [#(1.0) #(0.0001 0.9999) #(0.5 0.5) #(0.7)]]
    (<- every Trace (trace-of (quitters sim-foundation) (stop-on-a-tick-boundary gaps) False :workers TWO-WORKERS :policy QUIET-POLICY))
    (<- skipped Trace (trace-of (quitters sim-foundation) (stop-on-a-tick-boundary gaps) True :workers TWO-WORKERS :policy QUIET-POLICY))
    (<- breaches list (same-decisions every skipped))
    (assert (= breaches []) #(gaps breaches))))


(deftest test-rollouts-read-and-act-at-the-same-ticks-either-way
  ;; Rollout(k8s の読み・止めの action・段の期限)を含む筋書き: 1 秒ごとの拍と飛ばす拍で、置き場の書きの列と k8s への呼びが一致する
  ;; (Rollout の拍が読む・出す拍は飛ばさない — idle_policy.rollout-quiet の None の枝)。
  (<- every Trace (trace-of (beacons sim-foundation) (reverse-scenario) False :workers ROLLOUT-WORKERS :deployments DEPLOYMENTS))
  (<- skipped Trace (trace-of (beacons sim-foundation) (reverse-scenario) True :workers ROLLOUT-WORKERS :deployments DEPLOYMENTS))
  (<- breaches list (same-decisions every skipped))
  (assert (= breaches []) breaches))


;; --- 系全体の静かな区間(#2781): worker の拍も一度に進める ------------------------------------------------------------
;; 模擬の時計の下(skip-idle)では、worker の拍が状態を変えず・heartbeat が静かな早道に入り・coordinator の歩が生存の印のほか何も
;; 変えない区間を、本番の判断の関数で試して一度に進める(飛ばした拍の生存の印は、本番で拍が届いたのと同じ刻で積む)。1 拍ずつ進めた
;; 走り(skip-idle 偽 — coordinator の 1 秒ごとの拍と、worker の 10 秒ごとの拍と heartbeat)と、判断とその刻が同じ。

(val QUIET-SECONDS 60.0)    ; 出来事の間の静かな区間の長さ(拍 10 秒の worker 2 台に、heartbeat が 6 回ずつ届く長さ)


(defk quiet-stretches []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 静かな区間をはさんで、切り離した task(lease 60 秒・40 秒眠る)・宣言し直し・worker w1 の死・coordinator の 5 秒の止まりを
   順に入れ、task の答え・一生・process・盤の行を読むため(飛ばしてよい拍と、状態を変える拍の両方を含む)。"
  (<- (Delay 30.0))
  (<- (submit-detached-task (slow-task sim-task-foundation 40.0) :key "stretch" :needs NET :name "slow" :lease-seconds 60.0))
  (<- answer DetachedAwaited (AwaitDetached "stretch" :timeout-seconds 120.0))
  (<- (Delay QUIET-SECONDS))
  (<- (Redeclare (quitters-v2 sim-foundation)))
  (<- (Delay QUIET-SECONDS))
  (<- (KillWorker "w1"))
  (<- (Delay QUIET-SECONDS))
  (<- (StopCoordinator 5.0))
  (<- (Delay QUIET-SECONDS))
  (<- runs tuple (CoordinatorRuns))
  (<- processes tuple (ProcessesOf "quitter"))
  (<- rows dict (SharedRows "quit/"))
  #(answer runs processes rows))


(deftest test-a-quiet-system-keeps-every-decision-at-the-same-tick
  ;; 1 拍ずつ進めた走りと、静かな区間を一度に進める走りで、置き場への書きの列(判断とその刻)・task の答え・coordinator の一生・
  ;; process の列・盤の行が一致する。task は lease の内に終わる(飛ばした拍の生存の印と lease の延長が、本番と同じ刻で積まれる)。
  (<- every Trace (trace-of (quitters sim-foundation) (quiet-stretches) False :workers TWO-WORKERS :policy QUIET-POLICY))
  (<- skipped Trace (trace-of (quitters sim-foundation) (quiet-stretches) True :workers TWO-WORKERS :policy QUIET-POLICY))
  (assert (is-not every.answer None) "走りは答えを返している")
  (assert (isinstance (get every.answer 0) DetachedSucceeded) every.answer)
  (assert (= (len (get every.answer 1)) 2) every.answer)
  (<- breaches list (same-decisions every skipped))
  (assert (= breaches []) breaches))


(defk quiet-for-minutes [minutes]
  {:pre [(: minutes int)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 何も起きない仮想の minutes 分を待ち、終わりの一生を読むため。"
  (<- (Delay (* 60.0 minutes)))
  (<- runs tuple (CoordinatorRuns))
  runs)


(deftest test-a-quiet-stretch-costs-few-coordinator-steps
  ;; worker 2 台・拍 10 秒・何も起きない仮想の 20 分: 一度に進める走りの coordinator の歩は、起動の分(1 分の走りの歩)を除いて
  ;; 20 回より少ない(仮想の 1 分に 1 回より少ない)。反例 — worker の拍ごとの heartbeat と待ちの返事と、5 秒ごとの生存の印で歩くと、
  ;; 静かな 19 分で約 570 回(2026-10-02 の実測 — 仮想の 1 時間あたり約 3.3 秒の根・#2769)。判断の刻が 1 拍ずつの走りと同じことは
  ;; test-a-quiet-system-keeps-every-decision-at-the-same-tick が見る(ここで 1 拍ずつの 20 分を回すと、それだけで約 10 秒かかる)。
  (<- skipped Trace (trace-of (quitters sim-foundation) (quiet-for-minutes 20) True :workers TWO-WORKERS :policy QUIET-POLICY))
  (<- started Trace (trace-of (quitters sim-foundation) (quiet-for-minutes 1) True :workers TWO-WORKERS :policy QUIET-POLICY))
  (assert (< (- skipped.takes started.takes) 20) #(skipped.takes started.takes)))


;; --- 届いた仮の拍の覚えの一生(#2769) ------------------------------------------------------------------------------
;; 列は、調停ループへ渡した(届いた)仮の拍を、眠っている宿が写すまで覚える(request_queue の consumed)。宿は預けの拍を刻の順に写し、
;; 写し終えた刻より後の拍だけを問うので、覚えるのは「届いたが宿がまだ写していない拍」だけ。反例 — 既に写した拍(区間の試しで写した
;; 拍)や起きた宿が残りとしてまとめて写した拍を覚えると、外す者が無く走りの長さに比例して伸びる(2026-10-02 の実測: worker 2 台・
;; 拍 10 秒の静かな系で、仮想の 5 分 17 個・10 分 44 個・20 分 100 個。宿の拍ごとの問いと外しがその長さを舐め、使い手の模擬の検の
;; 時間が窓の長さのほぼ 2 乗になった)。

(defrecord HostHeard
  "宿 1 つの読み: name = worker の名・beats = 宿の真実の届いた heartbeat の数・last-ok-ms = 最後に届いた刻・consumed = 列が覚えている
   その宿の届いた仮の拍(ProvisionalBeat の tuple)・reach = 宿の真実の rest-reach(起きた時に書く次の眠りの拍の上限 — 今の眠りの長さは
   これ以下)。"
  (#^ str name)
  (#^ int beats)
  (#^ int last-ok-ms)
  (#^ tuple consumed)
  (#^ int reach))


(defrecord HeardLedger
  "列の届いた拍の覚えの読み: hosts = 宿ごとの読み(HostHeard の tuple — TWO-WORKERS の順)・remembered = 列が覚えている拍の数(どの宿の
   拍も含む)・queue = 列そのもの(走りが終わって宿が全部起きた後の覚えを読む)。"
  (#^ tuple hosts)
  (#^ int remembered)
  (#^ RequestQueue queue))


(defk heard-ledger []
  {:pre [] :post [(: % HeardLedger)] :tags {:context "doeff-cluster-test" :role "program"}}
  "列が覚えている届いた仮の拍を宿ごとに、その宿の真実の届いた heartbeat の数・最後に届いた刻と並べて読むため。"
  (<- link SimLink (ClientLink))
  (val consumed (tuple link.queue.consumed))
  (var hosts #())
  (for [worker TWO-WORKERS]
    (<- truth HostTruth (HostTruthOf worker.name))
    (:= hosts (+ hosts #((HostHeard :name worker.name :beats truth.beats :last-ok-ms truth.last-ok-ms
                                    :consumed (tuple (gfor beat consumed :if (= beat.name worker.name) beat))
                                    :reach truth.rest-reach)))))
  (HeardLedger :hosts hosts :remembered (len consumed) :queue link.queue))


(defk quiet-then-read [minutes]
  {:pre [(: minutes int)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 何も起きない仮想の minutes 分を待ち(宿は静かな拍を眠っている)、列の覚えと宿の真実を読むため(答え = その読み 1 つの tuple
   — 走りの答えの形 ended-with-takes に合わせる)。"
  (<- (Delay (* 60.0 minutes)))
  (<- read HeardLedger (heard-ledger))
  #(read))


(deftest test-the-queue-remembers-only-heard-beats-the-host-has-not-written
  ;; 静かな 5 分の後、眠っている宿の覚えの拍はどれも、その宿がまだ写していない刻(最後に届いた刻より後)。走りが終わって(止めの合図が
  ;; 眠っている宿を全部起こす)宿が残りを写した後は、覚えが空。宿が写した heartbeat と置き場の書きの列は 1 拍ずつの走りと同じ。
  (<- every Trace (trace-of (quitters sim-foundation) (quiet-then-read 5) False :workers TWO-WORKERS :policy QUIET-POLICY))
  (<- skipped Trace (trace-of (quitters sim-foundation) (quiet-then-read 5) True :workers TWO-WORKERS :policy QUIET-POLICY))
  (val resting (get skipped.answer 0))
  (val reference-hosts (. (get every.answer 0) hosts))
  (val written (lfor host resting.hosts
                     :if (any (gfor beat host.consumed (<= beat.at host.last-ok-ms)))
                     #(host.name host.last-ok-ms (tuple (gfor beat host.consumed beat.at)))))
  (assert (= written []) written)
  (assert (= resting.remembered (sum (gfor host resting.hosts (len host.consumed)))) resting)
  (assert (= (len resting.queue.consumed) 0) (tuple (gfor beat resting.queue.consumed #(beat.name beat.at))))
  ;; 写した heartbeat は、1 拍ずつの走りが届けた heartbeat の列の頭と同じ数・同じ刻(拍 10 秒の 5 分 — 1 拍ずつの走りは 31 回)。
  ;; 眠っている宿は、通った拍を起きた時にまとめて写すので(#3066)、読む刻には今の眠りの分まで遅れていることがある — 遅れは宿の
  ;; rest-reach(今の眠りの長さの上限)以下。
  (val tick-ms (int (* 1000 QUIET-POLICY.tick-seconds)))
  (val apart (lfor #(host reference) (zip resting.hosts reference-hosts)
                   :if (or (> (- reference.beats host.beats) host.reach) (> host.last-ok-ms reference.last-ok-ms)
                           (!= (- reference.beats host.beats) (// (- reference.last-ok-ms host.last-ok-ms) tick-ms)))
                   #(host reference)))
  (assert (= apart []) apart)
  (<- breaches list (same-decisions every (Trace :deltas skipped.deltas :steps skipped.steps :final skipped.final :answer every.answer :takes skipped.takes)))
  (assert (= breaches []) breaches))


;; --- 保存する物が無い静かな歩は保存を出さない(#2670 の根 B) ------------------------------------------------------------
;; 起きた調停ループは、眠った区間の歩のうち生存の印か仮の拍を持つ歩だけを保存する(印も拍も無い歩は Rollout の拍の刻しか違わず、
;; 差分が空)。書きの列は同じ — 上の同値の検(1 拍ずつの走りと置き場の書きの列が一致)が見る。
;; - 新しい状態の模擬の列が 12.3 秒目の要求で起きる時(眠った歩 12 個・生存の印は 5 秒目と 10 秒目)、調停ループは印の 2 歩と本物の歩の
;;   3 回だけ保存する。反例 — 歩ごとに保存する形では 13 回。印の歩(差分の在る歩)を保存しない形では、本物の歩の 1 回だけ(この検は赤)。

(defeffect SavesSeen
  "保存を覚える handler(saves-recorded)が受けた SaveState の後の状態の列(受けた順)。"
  {:answer tuple :tags {:context "doeff-cluster-test" :role "intent"}})


(defhandler saves-recorded
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  ;; 置き場の代役: 受けた保存(SaveState)の後の状態を順に覚える(置き場には書かない)。
  (session var saved #())
  (SavesSeen []
    (resume saved))
  (SaveState [before after]
    (:= saved (+ saved #(after)))
    (resume None)))


(defk woken-step-saves [queue]
  {:pre [(: queue RequestQueue)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "12.3 秒目に要求を積む送り手を走らせ、新しい状態から調停ループの 1 歩(coordinator-step — 模擬の列 queue が眠った区間の歩を添えて
   起こす)を回し、その歩が出した保存の後の状態の列を読むため。"
  (<- (Spawn (send-at queue 12.3)))
  (<- (coordinator-step (ClusterState) (ClusterTiming) (ClusterNaming) #()))
  (<- seen tuple (SavesSeen))
  seen)


(deftest test-a-woken-coordinator-saves-only-the-quiet-steps-that-change-something
  (val queue (RequestQueue :skip-idle True))
  (<- saved tuple ((sim-time-handler :clock (! (clock-at 0)))
                   (with-handlers [(session-store) saves-recorded (queued-requests queue)] (woken-step-saves queue))))
  ;; 生存の印の歩(5 秒目・10 秒目)を保存し、印も拍も無い 10 歩は保存しない。最後は本物の歩(12.3 秒目 — Rollout の拍は 12 秒目の
  ;; まま)の保存。
  (assert (= (lfor state saved #(state.alive-ms state.rollout-tick-ms)) [#(5000 5000) #(10000 10000) #(10000 12000)])
          (lfor state saved #(state.alive-ms state.rollout-tick-ms))))


;; --- 次の期限より前の歩は試さずに作る(#3060) ---------------------------------------------------------------------------
;; 静かな区間は、次の期限(idle_policy.quiet-due — 判断ごとの「答えが変わり得る最初の刻」の最小)より前の歩を本番の判断で試さずに作る
;; (held-step)。行の無い新しい状態では、要求の無い拍も Rollout の拍も何も変えないので、期限は待ちの期限と仮の拍だけになる。
;; - 試さずに作った歩は、1 歩ずつ本番の判断(quiet-step)で試した歩と同じ値(刻・状態・待ち・生存の印)。反例 — 生存の印を進めない作り方は赤。
;; - 待ちの期限(7.5 秒)の後の最初の歩(8 秒目)は試し、待ちに「変わっていない」と答える歩で区間が切れる。反例 — 待ちの期限を見ない
;;   作り方は切れずに先へ進む(赤)。
;; - 次に試す刻(quiet-due)は、行の無い状態では None・待ちが在れば待ちの期限・要求の無い拍の判断の行が在れば次の拍。

(defk tried-steps [start horizon]
  {:pre [(: start QuietStep) (: horizon int)] :post [(: % QuietStretch)] :tags {:context "doeff-cluster-test" :role "program"}}
  "start の歩の後から horizon まで、1 秒ごとに本番の判断(quiet-step)で試した歩の列と最初の静かでない歩の刻を返すため(比べの基準)。"
  (var last start)
  (var steps #())
  (var end None)
  (var at (+ start.at 1000))
  (while (and (is end None) (<= at horizon))
    (<- stepped (| QuietStep None) (quiet-step last at #() (ClusterTiming) (ClusterNaming)))
    (if (is stepped None)
        (:= end at)
        (do (:= steps (+ steps #(stepped)))
            (:= last stepped)
            (:= at (+ at 1000)))))
  (QuietStretch :steps steps :end-at end))


(defk fresh-stretch [held horizon]
  {:pre [(: held tuple) (: horizon int)] :post [(: % QuietStretch)] :tags {:context "doeff-cluster-test" :role "program"}}
  "行の無い新しい状態の静かな区間を、待ち held を持たせて刻 0 から horizon まで本番の進め方(quiet-stretch)で進めるため。"
  (val state (ClusterState))
  (<- stretch QuietStretch (quiet-stretch (IdleProbe state (ClusterTiming) (ClusterNaming) :watchers held)
                                          (QuietStep :at 0 :state state :watchers held :marked False) horizon))
  stretch)


(deftest test-held-steps-are-the-steps-a-trial-would-make
  (<- reader (| Watcher WatchRefusal None) (watch-of (! (http-request "GET" "/watch" {"after" "0" "timeoutSeconds" "7.5"} None)) 0))
  (for [#(held ending) [#(#() None) #(#(reader) 8000)]]
    (<- skipped QuietStretch (fresh-stretch held 30000))
    (<- tried QuietStretch (tried-steps (QuietStep :at 0 :state (ClusterState) :watchers held :marked False) 30000))
    (assert (= skipped.end-at tried.end-at ending) #(skipped.end-at tried.end-at))
    (assert (= (len skipped.steps) (len tried.steps)) #((len skipped.steps) (len tried.steps)))
    (val apart (lfor #(a b) (zip skipped.steps tried.steps) :if (!= a b) #(a.at a.state.alive-ms b.state.alive-ms)))
    (assert (= apart []) apart)))


(deftest test-the-next-trial-is-the-earliest-due
  ;; 行の無い新しい状態で待ちも仮の拍も無ければ、試す歩は無い(None — 区間の終わりまで試さずに進む)。待ちが在れば待ちの期限、要求の無い
  ;; 拍の判断の行(ここでは呼び手の問い合わせの止まった task — lease の期限 30 秒)が在れば、その判断の期限(task-due — #3062)。反例 — いつも
  ;; 次の拍を返す形は 3 つとも赤・待ちを見ない形は 2 つ目が赤。
  (val state (ClusterState))
  (val probe (IdleProbe state (ClusterTiming) (ClusterNaming)))
  (<- reader (| Watcher WatchRefusal None) (watch-of (! (http-request "GET" "/watch" {"after" "0" "timeoutSeconds" "7.5"} None)) 0))
  (assert (isinstance reader Watcher) reader)
  (<- idle (| int None) (quiet-due (QuietStep :at 0 :state state :watchers #() :marked False) probe #()))
  (<- waiting (| int None) (quiet-due (QuietStep :at 0 :state state :watchers #(reader) :marked False) probe #()))
  (<- row TaskRecord (task-row "t1" "finished" 30000))
  (val busy-state (replace state :tasks {"t1" row}))
  (<- busy (| int None) (quiet-due (QuietStep :at 0 :state busy-state :watchers #() :marked False) probe #()))
  (assert (= #(idle waiting busy) #(None 7500 30001)) #(idle waiting busy)))


(defk never-due [last probe pending]
  {:pre [(: last QuietStep) (: probe IdleProbe) (: pending tuple)] :post [(: % (| int None))] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "どの判断にも期限が無いと答える quiet-due の代わり(区間の起点の直後の歩を試すかを、期限の関数の中身から切り離して見るため)。"
  None)


(deftest test-the-step-after-the-start-is-tried-whatever-the-dues-say [monkeypatch]
  ;; 区間の起点は、本物の歩が時刻と関係なく状態を変えた直後で、次の拍の判断の不動点とは限らない(#3060)。期限の関数が「何も来ない」と
  ;; 答えても、起点の直後の歩は本番の判断で試す。ここでは起点の直後の拍に待ちの期限(0.5 秒)が来ているので、試せばその歩で区間が
  ;; 切れる(1000)。反例 — 起点の前に期限を見て、それより前の歩を試さずに作る形は、区間を切らずに horizon まで進む(None)。
  (.setattr monkeypatch idle-policy "quiet_due" never-due)
  (<- reader (| Watcher WatchRefusal None) (watch-of (! (http-request "GET" "/watch" {"after" "0" "timeoutSeconds" "0.5"} None)) 0))
  (assert (isinstance reader Watcher) reader)
  (<- stretch QuietStretch (fresh-stretch #(reader) 30000))
  (assert (= stretch.end-at 1000) stretch.end-at))


;; --- worker の生死の期限(#3061) -----------------------------------------------------------------------------------
;; liveness-due は、worker の生死の判断が比べる期限の値(cluster_policy.liveness-deadline = 最後の連絡 + 窓)から、答えが変わる最初の刻を返す。
;; - 窓ごとに、本番の判断の答えは liveness-due の返す刻ちょうどで変わり、その 1 ms 前では変わらない。反例 — 判断と liveness-due が別の
;;   比べ方を持つ形(alive を < にする・forget を >= にする・窓を 1 つ数え落とす)は、答えの変わる刻とずれて赤。
;; - worker の居る静かな区間は、いちばん早い期限の後の最初の歩で試され、1 歩ずつ試した走りと同じ歩で切れる。反例 — 期限より後の刻を
;;   返す形(遅い worker の期限・後の窓の期限)は、生死の変わる歩を試さずに飛び、切れる刻がずれて赤。

(deftest test-liveness-due-is-the-deadline-the-judgments-compare
  (val timing (ClusterTiming))
  (val seen 1000000)
  (val worker (WorkerInfo :name "w" :provides #("cpu") :capacity 1 :last-seen-ms seen :task-reserve 0))
  (val start (ClusterState :workers {"w" worker}))
  ;; lease-ms — note-liveness が生きていないと数え始める刻。
  (<- lease-due (| int None) (liveness-due start seen timing))
  (assert (= lease-due (+ seen timing.lease-ms 1)) lease-due)
  (assert (is (note-liveness start (- lease-due 1) timing) start))
  (val silent (note-liveness start lease-due timing))
  (assert (= silent.silent (frozenset ["w"])) silent.silent)
  ;; reassign-after-ms・keep-fence-ms — held-placements と資源の status が比べる alive の窓。
  (<- reassign-due (| int None) (liveness-due silent lease-due timing))
  (assert (= reassign-due (+ seen timing.reassign-after-ms 1)) reassign-due)
  (assert (and (alive (- reassign-due 1) worker timing.reassign-after-ms) (not (alive reassign-due worker timing.reassign-after-ms))))
  (<- fence-due (| int None) (liveness-due silent reassign-due timing))
  (assert (= fence-due (+ seen timing.keep-fence-ms 1)) fence-due)
  (assert (and (alive (- fence-due 1) worker timing.keep-fence-ms) (not (alive fence-due worker timing.keep-fence-ms))))
  ;; WORKER-FORGET-MS — forget-silent-workers が忘れる刻。
  (<- forget-due (| int None) (liveness-due silent fence-due timing))
  (assert (= forget-due (+ seen WORKER-FORGET-MS 1)) forget-due)
  (<- kept ClusterState (forget-silent-workers silent (- forget-due 1)))
  (assert (is kept silent))
  (<- forgotten ClusterState (forget-silent-workers silent forget-due))
  (assert (= forgotten.workers {}) forgotten.workers)
  ;; 忘れた後: 生きていないと数える名が残っていれば note-liveness が次の歩で外す(次の刻)・外した後は期限が無い(None)。
  (<- left (| int None) (liveness-due forgotten forget-due timing))
  (assert (= left (+ forget-due 1)) left)
  (<- after (| int None) (liveness-due (note-liveness forgotten forget-due timing) forget-due timing))
  (assert (is after None) after))


(deftest test-a-quiet-stretch-with-workers-is-tried-at-the-first-liveness-deadline
  ;; 2 台のうち早く黙った w2(最後の連絡 0)の lease の期限 10000 を越える最初の歩 11000 で note-liveness が答えを変え、区間が切れる
  ;; (w1 の期限 15000 より前)。
  (val state (ClusterState :workers {"w1" (WorkerInfo :name "w1" :provides #("cpu") :capacity 1 :last-seen-ms 5000 :task-reserve 0)
                                     "w2" (WorkerInfo :name "w2" :provides #("cpu") :capacity 1 :last-seen-ms 0 :task-reserve 0)}))
  (val start (QuietStep :at 0 :state state :watchers #() :marked False))
  (<- skipped QuietStretch (quiet-stretch (IdleProbe state (ClusterTiming) (ClusterNaming)) start 30000))
  (<- tried QuietStretch (tried-steps start 30000))
  (assert (= skipped.end-at tried.end-at 11000) #(skipped.end-at tried.end-at))
  (assert (= (len skipped.steps) (len tried.steps)) #((len skipped.steps) (len tried.steps)))
  (val apart (lfor #(a b) (zip skipped.steps tried.steps) :if (!= a b) #(a.at b.at)))
  (assert (= apart []) apart))


;; --- 掃除の期限(#3063) ---------------------------------------------------------------------------------------------------
;; sweep-due は、掃除の判断(盤の行・drain・温める表・詰めた Program)が比べに使う期限と同じ値から次の刻を返す。
;; - 判断ごとに: 返す刻 D の 1 ms 前では判断が状態を変えず、D で変える。反例 — sweep-due か判断の片方だけ期限の値を変えると赤。
;; - 期限の 12.3 秒目の盤の行を持つ静かな区間は、試さずに進めても 1 秒ごとに試した歩と同じ(行は 13 秒目の歩で消える — 飛び越さない)。
;;   反例 — 次の拍(行の有無)ではなく期限を遅く返す形は、行が消える歩を飛び越して赤。

(defk swept-at [state now]
  {:pre [(: state ClusterState) (: now int)] :post [(: % ClusterState)] :tags {:context "doeff-cluster-test" :role "program"}}
  "掃除の 4 つの判断(sweep-board・sweep-drains・sweep-warms・sweep-programs)を now で当てた状態を求めるため(どれも変えなければ同じ object)。"
  (<- board ClusterState (sweep-board state now))
  (<- drains ClusterState (sweep-drains board now))
  (<- warms ClusterState (sweep-warms drains now))
  (sweep-programs warms now))


(deftest test-the-sweep-due-is-the-deadline-each-sweep-compares
  (val due-at 12345)
  (val states [(ClusterState :board {"k" (BoardRow :value 1 :version 1 :expires-ms due-at :size 1)})
               (ClusterState :drains {"w" (Drain :worker "w" :since-ms 0 :until-ms due-at)})
               (ClusterState :warms {"e" (WarmEntry :key "e" :runtime-env {} :needs #() :until-ms due-at :holder "w")})
               (ClusterState :programs {"p" (ProgramRow :blob "" :versions {} :put-ms (- due-at PROGRAM-GRACE-MS 1))})])
  (for [state states]
    (<- due (| int None) (sweep-due state 0 (ClusterTiming)))
    (<- before ClusterState (swept-at state (- due-at 1)))
    (<- at ClusterState (swept-at state due-at))
    (assert (= due due-at) due)
    (assert (is before state) "期限の 1 ms 前に判断が状態を変えた")
    (assert (is-not at state) "期限の刻に判断が状態を変えなかった"))
  ;; 期限の無い盤の行だけの状態は、時刻では何も変わらない。
  (<- none (| int None) (sweep-due (ClusterState :board {"k" (BoardRow :value 1 :version 1 :expires-ms None :size 1)}) 0 (ClusterTiming)))
  (assert (is none None) none))


(deftest test-a-board-row-expiring-in-a-quiet-stretch-is-swept-on-the-same-step
  (val state (ClusterState :board {"k" (BoardRow :value 1 :version 1 :expires-ms 12300 :size 1)}))
  (val start (QuietStep :at 0 :state state :watchers #() :marked False))
  (<- skipped QuietStretch (quiet-stretch (IdleProbe state (ClusterTiming) (ClusterNaming)) start 30000))
  (<- tried QuietStretch (tried-steps start 30000))
  (assert (= skipped.end-at tried.end-at) #(skipped.end-at tried.end-at))
  (val apart (lfor #(a b) (zip skipped.steps tried.steps) :if (!= a b) #(a.at (len a.state.board) (len b.state.board))))
  (assert (= (len skipped.steps) (len tried.steps)) #((len skipped.steps) (len tried.steps)))
  (assert (= apart []) apart)
  ;; 1 秒ごとに試した歩で、行は 12 秒目の歩まで在り、行を消す 13 秒目の歩(静かでない歩)で区間が切れる(この検が期限の刻を通る事の確かめ)。
  (assert (= #(tried.end-at (len (. (get tried.steps -1) state board))) #(13000 1))
          #(tried.end-at (lfor s tried.steps #(s.at (len s.state.board))))))


;; --- task の期限(#3062) -----------------------------------------------------------------------------------------------------------
;; task の判断(cluster_policy.place-tasks)の次の刻(task-due)は、判断が比べる期限と同じ関数(task-lapse-at・wait-lapse-at)から求める。
;; - task-due の刻は place-tasks の答えが変わる最初の刻そのもの(1 ms 前の place-tasks は task を変えず、その刻の place-tasks は変える):
;;   lease の内に終わって保持の期限が来る切り離した task・lease の切れる置いた切り離した task・呼び手が問い合わせを止めた task・能力の
;;   合う worker の沈黙を待つ task。反例 — 判断か task-due の片方だけ期限をずらすと赤(task-due を行の有無の形に戻しても赤)。
;; - 静かな区間は task の期限より前の歩を試さずに作り、期限の後の最初の拍の書きで切れる(1 秒ごとに本番の判断で試した歩の列・切れる刻と
;;   同じ)。期限の早い task の書きを遅い task の期限まで飛び越さない。反例 — task-due が期限より遅い刻を返すと、切れる刻が遅れて赤。

(import doeff_cluster.coordinator.intent.cluster_model [TaskRecord WorkerInfo ComponentVersion])
(import doeff_cluster.coordinator.core.cluster_policy [place-tasks task-due])


(defk task-row [id phase lease-until * [detached False] [worker None] [finished None] [needs #()]]
  {:pre [(: id str) (: phase str) (: lease-until int) (: detached bool) (: worker (| str None)) (: finished (| int None)) (: needs tuple)]
   :post [(: % TaskRecord)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "期限の筋書きの task の行(lease 15 秒・終わった後の保持 7 秒・python 3)を作るため。"
  (TaskRecord id "digest" None "rev" #((ComponentVersion "python" "3")) needs 15000 lease-until 0
              :phase phase :worker worker :finished-ms finished :detached detached :retain-ms 7000))


(defk lapse-scenarios []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "task の期限の筋書き #(名 状態 起点の刻 期限の刻) を並べるため: lease の内(3 秒目)に終わった切り離した task の保持の期限・lease の切れる
   置いた切り離した task(担い手は居ない)・呼び手が問い合わせを止めた task・その 2 本(早い方の期限)・唯一の能力の合う worker(最後の
   連絡 0)の沈黙を待つ切り離した task(起点 15 秒目 — 生存の窓の外)。"
  (<- retained TaskRecord (task-row "done" "finished" 15000 :detached True :worker "w1" :finished 3000))
  (<- lost TaskRecord (task-row "lost" "assigned" 20000 :detached True :worker "w1"))
  (<- dropped TaskRecord (task-row "call" "finished" 12000 :worker "w1" :finished 3000))
  (<- waiting TaskRecord (task-row "wait" "queued" 20000 :detached True :needs #("verify")))
  (val verify (replace (WorkerInfo "verify-1" #("verify") 1 0 :task-reserve 0) :versions #((ComponentVersion "python" "3")) :exclusive #("verify")))
  #(#("lease の内に終わった task の保持" (ClusterState :tasks {"done" retained}) 5000 10001)
    #("lease の切れる置いた task" (ClusterState :tasks {"lost" lost}) 5000 20001)
    #("呼び手が問い合わせを止めた task" (ClusterState :tasks {"call" dropped}) 5000 12001)
    #("保持の期限と lease の期限の 2 本" (ClusterState :tasks {"done" retained "lost" lost}) 5000 10001)
    #("能力の合う worker の沈黙を待つ task" (ClusterState :workers {"verify-1" verify} :tasks {"wait" waiting}) 15000
      (+ (. (ClusterTiming) silent-worker-wait-ms) 1))))


(deftest test-the-task-due-is-the-tick-the-task-judgment-changes-at
  (val timing (ClusterTiming))
  (<- scenarios tuple (lapse-scenarios))
  (for [#(name state start expected) scenarios]
    ;; 起点の拍の後の状態(待つ task は待ちの理由を書いた後)から数える — 静かな区間の歩の前提。
    (<- settled dict (place-tasks start state {} timing))
    (val quiet (replace state :tasks settled))
    (<- due (| int None) (task-due quiet start timing))
    (assert (= due expected) #(name due expected))
    (<- before dict (place-tasks (- due 1) quiet {} timing))
    (<- at dict (place-tasks due quiet {} timing))
    (assert (= before settled) #(name "期限の 1 ms 前に変わった" before))
    (assert (!= at settled) #(name "期限の刻に変わらない" at))))


(deftest test-a-quiet-stretch-stops-at-the-first-task-deadline-like-a-trial-every-tick
  ;; 行が task だけの筋書き(起点 5 秒目)を 30 秒目まで: 区間は task の期限の後の最初の拍で切れる — 保持の期限(10001)は 11 秒目・lease の
  ;; 期限(20001)は 21 秒目・呼び手の止まった task(12001)は 13 秒目・その 2 本は早い方の 11 秒目。次に試す刻(quiet-due)は最も早い task の期限。
  (<- scenarios tuple (lapse-scenarios))
  (for [#(name state start due) scenarios]
    (when (not state.workers)
      (val start-step (QuietStep :at start :state state :watchers #() :marked False))
      (val probe (IdleProbe state (ClusterTiming) (ClusterNaming)))
      (<- first (| int None) (quiet-due start-step probe #()))
      (<- skipped QuietStretch (quiet-stretch probe start-step 30000))
      (<- tried QuietStretch (tried-steps start-step 30000))
      (val ending (* 1000 (// (+ due 999) 1000)))
      (assert (= first due) #(name first due))
      (assert (= skipped.end-at tried.end-at ending) #(name skipped.end-at tried.end-at ending))
      (assert (= (len skipped.steps) (len tried.steps)) #(name (len skipped.steps) (len tried.steps)))
      (val apart (lfor #(a b) (zip skipped.steps tried.steps) :if (!= a b) #(a.at a.state.alive-ms b.state.alive-ms)))
      (assert (= apart []) #(name apart)))))
