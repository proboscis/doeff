;; coordinator は要求の無い間、次の期限か要求か停止の合図まで受付を 1 本で待つ(#3865)。worker の代役(模擬の時計の下の宿)は静かな拍を
;; 一度に眠り、預けた heartbeat は受付の列がその刻に普通の要求として渡す(#2790・#3865 の案 4)。
;;
;; - 同値: 同じ筋書きを、期限だけで起きる走り(既定 — 宿は静かな拍を眠る)と、余計に起こす走り(宿は 1 拍ずつ打ち、coordinator を
;;   1 秒ごとに外の出来事で起こす — 前の 1 秒の拍と同じ起き方)で回すと、耐久の状態の変わり目の列(判断とその刻)・置き場の最後の
;;   状態・筋書きの答えが一致する。比べる時は生存の印(counter の aliveMs・worker の lastSeenMs)を外す — 印は歩の刻に書くので、
;;   起きる回数で違ってよい(眠る間は印を書かない・#3865 の決定)。
;; - 期限だけで起きる走りの coordinator の歩は、余計に起こす走りより少ない(1 秒の格子で起きない)。
;; - 期限の関数(liveness-due・sweep-due・task-due)は、判断が比べに使う期限の値と同じ刻を返す。
(require doeff-hy.macros [deftest defk <- val var])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass replace])
(import doeff [Program])
(import doeff_cluster.shared.intent.service_model [System])
(import doeff_time [Delay])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_core_effects.scheduler [Spawn Cancel Task])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState WorkerInfo])
(import doeff_cluster.coordinator.core.cluster_policy [liveness-due note-liveness forget-silent-workers alive])
(import doeff_cluster.shared.intent.due_model [DueAt DueNow DueNever])
(import doeff_cluster.coordinator.core.cluster_policy [sweep-board sweep-drains sweep-warms sweep-due])
(import doeff_cluster.coordinator.core.program_policy [sweep-programs PROGRAM-GRACE-MS])
(import doeff_cluster.coordinator.intent.cluster_model [BoardRow ProgramRow Drain WarmEntry])
(import doeff_cluster.coordinator.entry.handler_sets [MemoryWalStore])
(import doeff_cluster.coordinator.protocol.request_queue [RequestQueue nudge-takers])
(import doeff_cluster.sim.local [sim-cluster ProcessesOf SharedRows StopCoordinator CoordinatorRuns KillWorker ReadCoordinator ClientLink SimLink
                             SimWorker Redeclare CoordinatorStep CoordinatorSteps])
(import doeff_cluster.worker.intent.worker_model [WorkerPolicy])
(import doeff_cluster.shared.intent.detached_model [AwaitDetached DetachedAwaited DetachedSucceeded])
(import doeff_cluster.shared.core.detached_rules [submit-detached-task])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.sim_programs [quitters quitters-v2 beacons slow-task sim-task-foundation NET])
(import tests.test_local_rollout [reverse-scenario DEPLOYMENTS WORKERS :as ROLLOUT-WORKERS])


;; --- 同値: 期限だけで起きる走りと、余計に起こす走りで、判断の刻が同じ ------------------------------------------------

(val TWO-WORKERS #((SimWorker :name "w1" :provides (frozenset ["cluster-net"]) :task-reserve 0)
                   (SimWorker :name "w2" :provides (frozenset ["cluster-net"]) :task-reserve 0)))

;; 仮想の長い時間を回す使い手の検と同じ worker の設定(拍 10 秒・起こし直しは待たせる)— 要求の無い時間が長い。
;; 宿の刻み(sim-cluster の :tick-seconds・秒)。worker の代役は起きた刻から 10 秒ごとに拍を打つ。
(val QUIET-TICK-SECONDS 10.0)
(val QUIET-POLICY (WorkerPolicy :restart-backoff-ms 1000000000 :restart-backoff-max-ms 1000000000))

(val EXTRA-WAKE-SECONDS 1.0)   ; 余計に起こす走りの、coordinator を外の出来事で起こす間隔(前の 1 秒の拍)


(defrecord Trace
  "1 回の走りの読み: deltas = coordinator の置き場への書きの列(書いた順)・steps = 置き場へ書いた歩の記録(CoordinatorStep の tuple —
   刻と書きの列)・final = 走りが終わった後の置き場の鍵の表・answer = 筋書きの答え・takes = coordinator の歩の数・deposits = worker の宿が
   heartbeat を預けた回数(起こされた宿は預け直すので、宿の起きた回数の物差し)・heard-wakes = 列が積んだ heartbeat の返事が最後の返事と
   違って宿を起こした回数。"
  (#^ list deltas)
  (#^ (get tuple #(CoordinatorStep ...)) steps)
  ;; 置き場の鍵の表をそのまま持つ(置き場の口 load の答えの形 — 2 つの走りの最後の状態を丸ごと比べるため)。
  (#^ dict final)
  (#^ (| tuple None) answer)
  (#^ int takes)
  (#^ int deposits)
  (#^ int heard-wakes))


(defk wake-every [queue seconds]
  {:pre [(: queue RequestQueue) (: seconds float)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "余計に起こす走りの部品: seconds 秒ごとに、待っている coordinator を外の出来事で起こすため(要求は積まない — 前の 1 秒の拍と同じ
   起き方を外から作る。比べの基準のための検だけの起こし手で、筋書きが終わる時に取り消す)。"
  (while True
    (<- (Delay seconds))
    (<- (nudge-takers queue)))
  None)


(defk ended-with-takes [scenario extra]
  {:pre [(: scenario Program) (: extra bool)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書きを回し(extra なら coordinator を EXTRA-WAKE-SECONDS ごとに起こしながら)、その答えと、終わった時の coordinator の歩の数と、
   置き場へ書いた歩の記録と、宿の預けの回数と HEARD で起こした回数を返すため。"
  (<- link SimLink (ClientLink))
  (var waker None)
  (when extra
    (<- spawned Task (Spawn (wake-every link.queue EXTRA-WAKE-SECONDS)))
    (:= waker spawned))
  (<- answer (| tuple None) scenario)
  (when (is-not waker None)
    (<- (Cancel waker)))
  (<- steps tuple (CoordinatorSteps))
  #(answer link.queue.takes steps link.queue.deposits link.queue.heard-wakes))


(defk trace-of [system scenario every * [workers None] [policy None] [deployments None] [tick-seconds 0.5]]
  {:pre [(: system System) (: scenario Program) (: every bool) (: workers (| tuple None)) (: policy (| WorkerPolicy None))
         (: deployments (| dict None)) (: tick-seconds float)]
   :post [(: % Trace)] :tags {:context "doeff-cluster-test" :role "program"}}
  "同じ系と筋書きを、every なら余計に起こす走り(宿は 1 拍ずつ打ち・coordinator を 1 秒ごとに起こす)、偽なら期限だけで起きる走り
   (宿は静かな拍を眠る)で回し、置き場の書きの列と筋書きの答えと歩の数を返すため。"
  (val made [])
  (<- seen tuple (sim-cluster system (ended-with-takes scenario every) :workers workers :policy policy :deployments deployments :tick-seconds tick-seconds
                              :skip-idle (not every)
                              :store (fn [] (let [store (MemoryWalStore)] (.append made store) store))))
  (Trace :deltas (. (get made 0) deltas) :steps (get seen 2) :final (.load (get made 0)) :answer (get seen 0) :takes (get seen 1)
         :deposits (get seen 3) :heard-wakes (get seen 4)))


(defk without-alive-marks [kv]
  {:pre [(: kv dict)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "置き場の鍵の表 kv から生存の印(counter の aliveMs・worker/<名> の lastSeenMs — 歩の刻に書く印)を外すため(起きる回数で違ってよい欄)。"
  (dfor #(key value) (.items kv)
        key (cond
              (and (= key "counter") (isinstance value dict)) (dfor #(k v) (.items value) :if (!= k "aliveMs") k v)
              (and (.startswith key "worker/") (isinstance value dict)) (dfor #(k v) (.items value) :if (!= k "lastSeenMs") k v)
              True value)))


(defk state-changes [steps]
  {:pre [(: steps tuple)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "置き場へ書いた歩の記録 steps の書きを順に重ねた鍵の表から、生存の印を外した耐久の状態が前と変わった歩だけを #(刻 状態) の列にする
   ため — 判断とその刻を持つ(印だけの書きは数えない — 起きる回数で違ってよい)。"
  (var kv {})
  (var last {})
  (var changes #())
  (for [step steps]
    (for [writes step.writes]
      (for [w writes]
        (if (is w.value None)
            (:= kv (dfor #(k v) (.items kv) :if (!= k w.key) k v))
            (:= kv (| kv {w.key w.value})))))
    (<- seen dict (without-alive-marks kv))
    (when (!= seen last)
      (:= changes (+ changes #(#(step.at seen))))
      (:= last seen)))
  changes)


(defk step-apart [every skipped]
  {:pre [(: every tuple) (: skipped tuple)] :post [(: % list)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "余計に起こす走りと期限だけで起きる走りの、置き場へ書いた歩の記録から、耐久の状態の変わり目の列(刻と状態)の食い違いを並べるため
   (空 = 同じ判断を同じ刻に下した)。"
  (<- kept tuple (state-changes every))
  (<- seen tuple (state-changes skipped))
  (val first-diff (next (gfor #(i #(a b)) (enumerate (zip kept seen)) :if (!= a b) i) None))
  (+ (if (is first-diff None)
         []
         [(.format "耐久の状態の変わり目の {} 件目(刻 {} と {})が食い違う" (+ first-diff 1) (get (get kept first-diff) 0)
                   (get (get seen first-diff) 0))])
     (if (= (len kept) (len seen)) [] [(.format "変わり目の数 {} と {}" (len kept) (len seen))])))


(defrecord MarkWrite
  "検の部品の書き 1 つ(Persist の writes の要素と同じ欄)。"
  (#^ str key)
  (#^ (| dict None) value))


(defk mark-only [key value]
  {:pre [(: key str) (: value dict)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "検の部品: 鍵 key に値 value を書く書き 1 つの列を作るため(Persist の writes と同じ形 — 欄 key と value)。"
  #(#((MarkWrite :key key :value value))))


(deftest test-the-step-criterion-names-a-change-at-another-instant-and-ignores-alive-marks
  ;; 歩の記録の基準の失敗ケース: 同じ盤の書きが 1 秒ずれた走り・同じ刻に違う値を書いた走りを名指す。生存の印だけの書き(counter の
  ;; aliveMs)が片方に多いのは食い違いにしない。どの走りも起動の歩で counter を書く(本物の置き場と同じ)。
  (val started (CoordinatorStep :at 0 :writes (! (mark-only "counter" {"nextTask" 1 "aliveMs" 0}))))
  (val reference #(started
                   (CoordinatorStep :at 5000 :writes (! (mark-only "board/k" {"value" 1})))
                   (CoordinatorStep :at 9000 :writes (! (mark-only "board/k" {"value" 2})))))
  (<- late list (step-apart reference #(started
                                        (CoordinatorStep :at 6000 :writes (! (mark-only "board/k" {"value" 1})))
                                        (CoordinatorStep :at 9000 :writes (! (mark-only "board/k" {"value" 2}))))))
  (assert late late)
  (<- other list (step-apart reference #(started
                                         (CoordinatorStep :at 5000 :writes (! (mark-only "board/k" {"value" 3})))
                                         (CoordinatorStep :at 9000 :writes (! (mark-only "board/k" {"value" 2}))))))
  (assert other other)
  (<- marks list (step-apart reference #(started
                                         (CoordinatorStep :at 1000 :writes (! (mark-only "counter" {"nextTask" 1 "aliveMs" 1000})))
                                         (CoordinatorStep :at 5000 :writes (! (mark-only "board/k" {"value" 1})))
                                         (CoordinatorStep :at 7000 :writes (! (mark-only "counter" {"nextTask" 1 "aliveMs" 7000})))
                                         (CoordinatorStep :at 9000 :writes (! (mark-only "board/k" {"value" 2}))))))
  (assert (= marks []) marks))


(defk same-decisions [every skipped]
  {:pre [(: every Trace) (: skipped Trace)] :post [(: % list)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "余計に起こす走りと期限だけで起きる走りの食い違い(耐久の状態の変わり目の列・生存の印を外した置き場の最後の状態・筋書きの答え)を
   並べるため(空 = 同じ判断を同じ刻に下し、同じ状態で終わった)。"
  (<- every-final dict (without-alive-marks every.final))
  (<- skipped-final dict (without-alive-marks skipped.final))
  (+ (! (step-apart every.steps skipped.steps))
     (if (= every-final skipped-final) [] ["置き場の最後の状態が食い違う"])
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


(deftest test-waking-only-at-deadlines-keeps-every-decision-at-the-same-instant
  ;; worker の死(沈黙 → 移し替え)と coordinator の止まり(止めの合図 → 作り直し)を含む筋書き: 余計に起こす走りと、期限だけで起きる
  ;; 走りで、耐久の状態の変わり目の列(判断とその刻 — worker の沈黙を判じた刻を含む)・置き場の最後の状態・coordinator の一生・
  ;; process の列・盤の行が一致する。期限だけで起きる走りは歩の数が少ない。
  (<- every Trace (trace-of (quitters sim-foundation) (kill-then-stop) True :workers TWO-WORKERS :policy QUIET-POLICY :tick-seconds QUIET-TICK-SECONDS))
  (<- skipped Trace (trace-of (quitters sim-foundation) (kill-then-stop) False :workers TWO-WORKERS :policy QUIET-POLICY :tick-seconds QUIET-TICK-SECONDS))
  (assert (> (len every.deltas) 10) (len every.deltas))
  (assert (is-not every.answer None) "走りは答えを返している")
  (assert (= (len (get every.answer 0)) 2) every.answer)
  (<- breaches list (same-decisions every skipped))
  (assert (= breaches []) breaches)
  (assert (< skipped.takes every.takes) #(skipped.takes every.takes)))


(defk stop-at-an-instant [gaps]
  {:pre [(: gaps tuple)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 8.3 秒で coordinator を要求で起こし、gaps の秒を順に眠って(余計に起こす走りの起こしの timer より後に眠りを登録して)止めを
   入れ、20 秒後に一生を読むため。"
  (<- (Delay 8.3))
  (<- (ReadCoordinator "/state"))
  (for [gap gaps]
    (<- (Delay gap)))
  (<- (StopCoordinator 5.0))
  (<- (Delay 20.0))
  (<- runs tuple (CoordinatorRuns))
  runs)


(deftest test-an-event-at-a-wake-instant-is-seen-the-same-either-way
  ;; 余計に起こす走りの起こしの刻ちょうどの出来事(止めの注入)は、どちらの走りでも同じ刻に通る(模擬の列は要求の無いまま返る時に
  ;; 0 秒の Delay をはさむ)— 眠りの割り方(timer の登録の順)によらず、判断の刻と一生が一致する(2026-09-30 のレビューの再現)。
  (for [gaps [#(1.0) #(0.0001 0.9999) #(0.5 0.5) #(0.7)]]
    (<- every Trace (trace-of (quitters sim-foundation) (stop-at-an-instant gaps) True :workers TWO-WORKERS :policy QUIET-POLICY :tick-seconds QUIET-TICK-SECONDS))
    (<- skipped Trace (trace-of (quitters sim-foundation) (stop-at-an-instant gaps) False :workers TWO-WORKERS :policy QUIET-POLICY :tick-seconds QUIET-TICK-SECONDS))
    (<- breaches list (same-decisions every skipped))
    (assert (= breaches []) #(gaps breaches))))


(deftest test-rollouts-read-and-act-at-the-same-instants-either-way
  ;; Rollout(k8s の読み・止めの action・段の期限)を含む筋書き: 余計に起こす走りと期限だけで起きる走りで、判断の刻と置き場の最後の
  ;; 状態が一致する(Rollout の歩の期限 — wake_policy.rollout-due)。
  (<- every Trace (trace-of (beacons sim-foundation) (reverse-scenario) True :workers ROLLOUT-WORKERS :deployments DEPLOYMENTS))
  (<- skipped Trace (trace-of (beacons sim-foundation) (reverse-scenario) False :workers ROLLOUT-WORKERS :deployments DEPLOYMENTS))
  (<- breaches list (same-decisions every skipped))
  (assert (= breaches []) breaches))


(val QUIET-SECONDS 60.0)    ; 出来事の間の静かな区間の長さ(拍 10 秒の worker 2 台に、heartbeat が 6 回ずつ届く長さ)


(defk quiet-stretches []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 静かな区間をはさんで、切り離した task(lease 60 秒・40 秒眠る)・宣言し直し・worker w1 の死・coordinator の 5 秒の止まりを
   順に入れ、task の答え・一生・process・盤の行を読むため。"
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


(deftest test-a-quiet-system-keeps-every-decision-at-the-same-instant
  ;; 余計に起こす走りと、期限だけで起きる走りで、判断とその刻・task の答え・coordinator の一生・process の列・盤の行が一致する。task は
  ;; lease の内に終わる(眠る宿の預けた heartbeat が、本番と同じ刻に届いて lease を延ばす)。
  (<- every Trace (trace-of (quitters sim-foundation) (quiet-stretches) True :workers TWO-WORKERS :policy QUIET-POLICY :tick-seconds QUIET-TICK-SECONDS))
  (<- skipped Trace (trace-of (quitters sim-foundation) (quiet-stretches) False :workers TWO-WORKERS :policy QUIET-POLICY :tick-seconds QUIET-TICK-SECONDS))
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


(deftest test-a-quiet-system-steps-only-for-requests-and-deadlines
  ;; worker 2 台・拍 10 秒・何も起きない仮想の 5 分: 期限だけで起きる走りの coordinator の歩は、余計に起こす走り(1 秒ごとの起こしだけで
  ;; 300 歩)より 300 歩以上少ない — 歩は届いた要求(heartbeat と名指しの待ち)と期限の分だけ。反例: 1 秒の格子で起きる作りは差が 0。
  (<- skipped Trace (trace-of (quitters sim-foundation) (quiet-for-minutes 5) False :workers TWO-WORKERS :policy QUIET-POLICY :tick-seconds QUIET-TICK-SECONDS))
  (<- every Trace (trace-of (quitters sim-foundation) (quiet-for-minutes 5) True :workers TWO-WORKERS :policy QUIET-POLICY :tick-seconds QUIET-TICK-SECONDS))
  (assert (>= (- every.takes skipped.takes) 300) #(skipped.takes every.takes)))


;; --- worker の生死の期限(#3061) -----------------------------------------------------------------------------------
;; liveness-due は、worker の生死の判断が比べる期限の値(cluster_policy.liveness-deadline = 最後の連絡 + 窓)から、答えが変わる最初の刻を返す。
;; - 窓ごとに、本番の判断の答えは liveness-due の返す刻ちょうどで変わり、その 1 ms 前では変わらない。反例 — 判断と liveness-due が別の
;;   比べ方を持つ形(alive を < にする・forget を >= にする・窓を 1 つ数え落とす)は、答えの変わる刻とずれて赤。

(deftest test-liveness-due-is-the-deadline-the-judgments-compare
  (val timing (ClusterTiming))
  (val seen 1000000)
  (val worker (WorkerInfo :name "w" :provides #("cpu") :capacity 1 :last-seen-ms seen :task-reserve 0))
  (val start (ClusterState :workers {"w" worker}))
  ;; lease-ms — note-liveness が生きていないと数え始める刻。
  (<- lease-answer (| DueAt DueNow DueNever) (liveness-due start seen timing))
  (assert (= lease-answer (DueAt :at (+ seen timing.lease-ms 1))) lease-answer)
  (val lease-due lease-answer.at)
  (assert (is (note-liveness start (- lease-due 1) timing) start))
  (val silent (note-liveness start lease-due timing))
  (assert (= silent.silent (frozenset ["w"])) silent.silent)
  ;; reassign-after-ms・keep-fence-ms — held-placements と資源の status が比べる alive の窓。
  (<- reassign-answer (| DueAt DueNow DueNever) (liveness-due silent lease-due timing))
  (assert (= reassign-answer (DueAt :at (+ seen timing.reassign-after-ms 1))) reassign-answer)
  (val reassign-due reassign-answer.at)
  (assert (and (alive (- reassign-due 1) worker timing.reassign-after-ms) (not (alive reassign-due worker timing.reassign-after-ms))))
  (<- fence-answer (| DueAt DueNow DueNever) (liveness-due silent reassign-due timing))
  (assert (= fence-answer (DueAt :at (+ seen timing.keep-fence-ms 1))) fence-answer)
  (val fence-due fence-answer.at)
  (assert (and (alive (- fence-due 1) worker timing.keep-fence-ms) (not (alive fence-due worker timing.keep-fence-ms))))
  ;; worker-forget-ms — forget-silent-workers が忘れる刻。
  (<- forget-answer (| DueAt DueNow DueNever) (liveness-due silent fence-due timing))
  (assert (= forget-answer (DueAt :at (+ seen timing.worker-forget-ms 1))) forget-answer)
  (val forget-due forget-answer.at)
  (<- kept ClusterState (forget-silent-workers silent (- forget-due 1) timing))
  (assert (is kept silent))
  (<- forgotten ClusterState (forget-silent-workers silent forget-due timing))
  (assert (= forgotten.workers {}) forgotten.workers)
  ;; 忘れた後: 生きていないと数える名が残っていれば note-liveness が今の刻で外す(今すぐ)・外した後は期限が無い(無し)。
  (<- left (| DueAt DueNow DueNever) (liveness-due forgotten forget-due timing))
  (assert (= left (DueNow)) left)
  (<- after (| DueAt DueNow DueNever) (liveness-due (note-liveness forgotten forget-due timing) forget-due timing))
  (assert (= after (DueNever)) after))


;; --- 掃除の期限(#3063) ---------------------------------------------------------------------------------------------------
;; sweep-due は、掃除の判断(盤の行・drain・温める表・詰めた Program)が比べに使う期限と同じ値から次の刻を返す。
;; - 判断ごとに: 返す刻 D の 1 ms 前では判断が状態を変えず、D で変える。反例 — sweep-due か判断の片方だけ期限の値を変えると赤。

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
    (<- due (| DueAt DueNow DueNever) (sweep-due state 0 (ClusterTiming)))
    (<- before ClusterState (swept-at state (- due-at 1)))
    (<- at ClusterState (swept-at state due-at))
    (assert (= due (DueAt :at due-at)) due)
    (assert (is before state) "期限の 1 ms 前に判断が状態を変えた")
    (assert (is-not at state) "期限の刻に判断が状態を変えなかった"))
  ;; 期限の無い盤の行だけの状態は、時刻では何も変わらない。
  (<- none (| DueAt DueNow DueNever) (sweep-due (ClusterState :board {"k" (BoardRow :value 1 :version 1 :expires-ms None :size 1)}) 0 (ClusterTiming)))
  (assert (= none (DueNever)) none))


;; --- task の期限(#3062) -----------------------------------------------------------------------------------------------------------
;; task の判断(cluster_policy.place-tasks)の次の刻(task-due)は、判断が比べる期限と同じ関数(task-lapse-at・wait-lapse-at)から求める。
;; - task-due の刻は place-tasks の答えが変わる最初の刻そのもの(1 ms 前の place-tasks は task を変えず、その刻の place-tasks は変える):
;;   lease の内に終わって保持の期限が来る切り離した task・lease の切れる置いた切り離した task・呼び手が問い合わせを止めた task・能力の
;;   合う worker の沈黙を待つ task。反例 — 判断か task-due の片方だけ期限をずらすと赤(task-due を行の有無の形に戻しても赤)。

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
    (<- answer (| DueAt DueNow DueNever) (task-due quiet start timing))
    (assert (= answer (DueAt :at expected)) #(name answer expected))
    (val due answer.at)
    (<- before dict (place-tasks (- due 1) quiet {} timing))
    (<- at dict (place-tasks due quiet {} timing))
    (assert (= before settled) #(name "期限の 1 ms 前に変わった" before))
    (assert (!= at settled) #(name "期限の刻に変わらない" at))))


;; --- 要求の刻と重なった、眠る宿の heartbeat(#2850 の続き・#3865)------------------------------------------------------------
;; 眠っている worker の宿が預けた heartbeat の刻に、別の要求がちょうど届くと、前は宿を起こして本物の heartbeat を送らせた(起きた宿は先の
;; 拍を試し直して預け直す — 使い手の模擬の検の 1 本で歩数 1,203,544 → 1,492,965)。今は宿を起こさず、列がその刻に heartbeat を要求と
;; して積み、返事が最後の返事と同じなら宿は眠ったまま写す。違う時だけ宿を起こし、宿はその刻のまま、列が受けた返事で動く(送らない —
;; 同じ刻の heartbeat を 2 度受けさせない)。
;; - worker 1 台(拍 10 秒 — heartbeat は拍の刻 x0 秒)に、読みの要求を 10 秒ごとに 20 回、拍の刻ちょうど(x0 秒)と 0.1 秒後(x0.1 秒)に
;;   送る。どちらも余計に起こす走りと判断の変わり目・置き場の最後の状態・筋書きの答えが一致し、重なった走りの宿の預けの回数は、ずらした
;;   走りより多くない。反例 — 重なった刻に宿を起こす形は、重なるたびに宿が預け直して赤。
;; - 拍の刻ちょうどに、その worker へ置く task を投げる: 返事が変わるので宿を起こし(HEARD)、余計に起こす走りと同じ刻に task を走らせる。
;;   今の worker は名指しの待ち(/watch)で coordinator の変化を待つので、同じ歩で待ちも答え、宿はその刻に起きる。HEARD は待ちに頼らない
;;   明示の道として残し、ここではその道を通った上で判断が同じことを見る。起きた宿が送らずに列の返事を読む(take-heard)ので、同じ刻の
;;   heartbeat は 2 度届かない。
(val ONE-WORKER #((SimWorker :name "w1" :provides (frozenset ["cluster-net"]) :task-reserve 0)))


(defk wait-until-offset [offset]
  {:pre [(: offset float)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書きの部品: 起動の刻を含む 10 秒の区切りの頭から数えた秒 offset の刻まで眠るため(worker の拍の刻 x0 秒に揃えて要求を送る)。"
  (<- started int (now-epoch-ms))
  (val origin (- started (% started 10000)))
  (<- now int (now-epoch-ms))
  (<- (Delay (/ (- (+ origin (int (* 1000 offset))) now) 1000.0)))
  None)


(defk reads-on [offsets]
  {:pre [(: offsets tuple)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 10 秒の区切りの頭から数えた秒 offsets の刻ちょうどに読みの要求を 1 件ずつ送り、終わりの一生を読むため。"
  (<- started int (now-epoch-ms))
  (val origin (- started (% started 10000)))
  (for [offset offsets]
    (<- now int (now-epoch-ms))
    (<- (Delay (/ (- (+ origin (int (* 1000 offset))) now) 1000.0)))
    (<- (ReadCoordinator "/state")))
  (<- runs tuple (CoordinatorRuns))
  runs)


(deftest test-a-request-on-a-resting-workers-beat-takes-the-beat-without-waking-the-worker
  (var deposits {})
  (for [first [20.0 20.1]]
    (val scenario (fn [] (reads-on (tuple (gfor k (range 20) (+ first (* 10.0 k)))))))
    (<- every Trace (trace-of (quitters sim-foundation) (scenario) True :workers ONE-WORKER :policy QUIET-POLICY :tick-seconds QUIET-TICK-SECONDS))
    (<- skipped Trace (trace-of (quitters sim-foundation) (scenario) False :workers ONE-WORKER :policy QUIET-POLICY :tick-seconds QUIET-TICK-SECONDS))
    (assert (is-not every.answer None) "走りは答えを返している")
    (<- breaches list (same-decisions every skipped))
    (assert (= breaches []) #(first breaches))
    (:= deposits (| deposits {first skipped.deposits})))
  (assert (<= (get deposits 20.0) (get deposits 20.1)) deposits))


(defk task-on-a-beat [offset]
  {:pre [(: offset float)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 10 秒の区切りの頭から数えた秒 offset の刻ちょうど(worker の拍の刻)に、worker へ置く切り離した task(10 秒眠る)を投げ、
   答えを待って 30 秒の静かな区間をおき、task の答えと一生を読むため。"
  (<- (wait-until-offset offset))
  (<- (submit-detached-task (slow-task sim-task-foundation 10.0) :key "on-beat" :needs NET :name "on-beat" :lease-seconds 60.0))
  (<- answer DetachedAwaited (AwaitDetached "on-beat" :timeout-seconds 120.0))
  (<- (Delay 30.0))
  (<- runs tuple (CoordinatorRuns))
  #(answer runs))


(deftest test-a-task-placed-on-a-resting-workers-beat-runs-at-the-same-tick
  (<- every Trace (trace-of (quitters sim-foundation) (task-on-a-beat 60.0) True :workers ONE-WORKER :policy QUIET-POLICY :tick-seconds QUIET-TICK-SECONDS))
  (<- skipped Trace (trace-of (quitters sim-foundation) (task-on-a-beat 60.0) False :workers ONE-WORKER :policy QUIET-POLICY :tick-seconds QUIET-TICK-SECONDS))
  (assert (isinstance (get every.answer 0) DetachedSucceeded) every.answer)
  (<- breaches list (same-decisions every skipped))
  (assert (= breaches []) breaches)
  ;; 返事が変わった拍を受けて、宿をその刻に起こした(この道を通った)。
  (assert (> skipped.heard-wakes 0) skipped.heard-wakes))
