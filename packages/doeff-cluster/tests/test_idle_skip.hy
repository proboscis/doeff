;; 模擬の時計の下だけ、coordinator は要求の無い間に「本番の判断で何も変えない拍」を一度に眠る(idle_policy.quiet-ticks・
;; coordinator_handler_sets の RequestQueue の skip-idle・2026-09-30 の決定)。本番の拍の間隔 1 秒と判断の刻は変えない。
;;
;; - 模擬の時計では、要求が無ければ次に状態の変わる拍まで一気に進む(1 秒ごとに起きない)。
;; - 本番の受付(http-requests)は材料 idle を読まず、拍の間隔は 1 秒のまま。skip-idle でない模擬の列も 1 秒ごと。
;; - 同じ筋書きを 1 秒ごとの拍と飛ばす拍で回すと、置き場への書きの列・coordinator の一生・process の列が一致する(判断の刻が同じ)。
(require doeff-hy.macros [deftest defk <- val var])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import doeff [with-handlers Program])
(import doeff_cluster.shared.intent.service_model [System])
(import doeff_time [Delay sim-time-handler])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState ClusterNaming IdleProbe NextRequests])
(import doeff_cluster.coordinator.entry.handler_sets [RequestQueue MemoryWalStore queued-requests])
(import doeff_cluster.foundation.coordinator_inbox [RequestInbox http-requests])
(import doeff_cluster.coordinator.core.idle_policy [quiet-ticks])
(import doeff_cluster.sim.local [sim-cluster ProcessesOf SharedRows StopCoordinator CoordinatorRuns KillWorker ReadCoordinator ClientLink SimLink
                             SimWorker])
(import doeff_cluster.worker_model [WorkerPolicy])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.sim_programs [quitters beacons])
(import tests.test_local_rollout [reverse-scenario DEPLOYMENTS WORKERS :as ROLLOUT-WORKERS])
(import tests.clock_fixtures [clock-at])


(val FRESH-PROBE (IdleProbe (ClusterState) (ClusterTiming) (ClusterNaming)))


(defk wake-times [probe times]
  {:pre [(: probe IdleProbe) (: times int)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書きの取り手: coordinator の拍と同じ NextRequests 1.0(材料 idle 付き)を times 回出し、各回の起きた刻を読むため。"
  (var seen #())
  (for [_ (range times)]
    (<- (NextRequests 1.0 :idle probe))
    (<- woke int (now-epoch-ms))
    (:= seen (+ seen #(woke))))
  seen)


(defk queue-wakes [skip-idle probe times]
  {:pre [(: skip-idle bool) (: probe IdleProbe) (: times int)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "模擬の列(skip-idle の真偽)を仮想の時計(起点 0)で回し、取り手の起きた刻の列を返すため。"
  (<- woke tuple ((sim-time-handler :clock (clock-at 0))
                   (with-handlers [(queued-requests (RequestQueue :skip-idle skip-idle))] (wake-times probe times))))
  woke)


(deftest test-the-simulated-coordinator-sleeps-to-the-next-changing-tick
  ;; 要求の無い新しい状態: 最初に状態を変える拍は、生きていた時刻の印(ALIVE-MARK-MS 5 秒)の 5 秒目。skip-idle の列は 5 秒目に
  ;; 起きる(間の 1 秒ごとの拍を飛ばす)。判断の関数で試した拍の数(quiet-ticks)も 5。
  (<- quiet int (quiet-ticks FRESH-PROBE 0))
  (assert (= quiet 5) quiet)
  (<- skipped tuple (queue-wakes True FRESH-PROBE 1))
  (assert (= skipped #(5000)) skipped)
  ;; 反例 — skip-idle でない列(本番と同じ間隔): 1 秒目に起きる。
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
  (<- ((sim-time-handler :clock (clock-at 0)) (with-handlers [(http-requests inbox)] (wake-times FRESH-PROBE 2))))
  (assert (= inbox.timeouts [1.0 1.0]) inbox.timeouts))


;; --- 同値: 1 秒ごとの拍と飛ばす拍で、判断の刻が同じ ----------------------------------------------------------------

(val TWO-WORKERS #((SimWorker :name "w1" :provides (frozenset ["cluster-net"]))
                   (SimWorker :name "w2" :provides (frozenset ["cluster-net"]))))

;; 仮想の長い時間を回す使い手の検と同じ worker の設定(拍 10 秒・起こし直しは待たせる)— 要求の無い拍が多い。
(val QUIET-POLICY (WorkerPolicy :tick-seconds 10.0 :restart-backoff-ms 1000000000 :restart-backoff-max-ms 1000000000))


(defrecord Trace
  "1 回の走りの読み: deltas = coordinator の置き場への書きの列(書いた順 — 判断の結果と刻を含む)・answer = 筋書きの答え・
   takes = coordinator の拍の数。"
  (#^ list deltas)
  (#^ (| tuple None) answer)
  (#^ int takes))


(defk ended-with-takes [scenario]
  {:pre [(: scenario Program)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書きを回し、その答えと、終わった時の coordinator の拍の数を返すため。"
  (<- answer (| tuple None) scenario)
  (<- link SimLink (ClientLink))
  #(answer link.queue.takes))


(defk trace-of [system scenario skip-idle * [workers None] [policy None] [deployments None]]
  {:pre [(: system System) (: scenario Program) (: skip-idle bool) (: workers (| tuple None)) (: policy (| WorkerPolicy None))
         (: deployments (| dict None))]
   :post [(: % Trace)] :tags {:context "doeff-cluster-test" :role "program"}}
  "同じ系と筋書きを skip-idle の真偽で回し、置き場の書きの列と筋書きの答えと拍の数を返すため。"
  (val made [])
  (<- seen tuple (sim-cluster system (ended-with-takes scenario) :workers workers :policy policy :deployments deployments
                              :skip-idle skip-idle
                              :store (fn [] (let [store (MemoryWalStore)] (.append made store) store))))
  (Trace :deltas (. (get made 0) deltas) :answer (get seen 0) :takes (get seen 1)))


(defk same-decisions [every skipped]
  {:pre [(: every Trace) (: skipped Trace)] :post [(: % list)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "1 秒ごとの拍と飛ばす拍の走りの食い違い(置き場の書きの列・筋書きの答え)を並べるため(空 = 同じ判断を同じ刻に下した)。"
  (val first-diff (next (gfor #(i #(a b)) (enumerate (zip every.deltas skipped.deltas)) :if (!= a b) i) None))
  (+ (if (is first-diff None) [] [(.format "置き場の書きの {} 件目が食い違う" (+ first-diff 1))])
     (if (= (len every.deltas) (len skipped.deltas)) [] [(.format "書きの数 {} と {}" (len every.deltas) (len skipped.deltas))])
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
  ;; 飛ばす拍で、置き場への書きの列(判断とその刻)・coordinator の一生・process の列・盤の行が一致する。飛ばす側は拍の数が少ない。
  (<- every Trace (trace-of (quitters sim-foundation) (kill-then-stop) False :workers TWO-WORKERS :policy QUIET-POLICY))
  (<- skipped Trace (trace-of (quitters sim-foundation) (kill-then-stop) True :workers TWO-WORKERS :policy QUIET-POLICY))
  (assert (> (len every.deltas) 10) (len every.deltas))
  (assert (is-not every.answer None) "走りは答えを返している")
  (assert (= (len (get every.answer 0)) 2) every.answer)
  (<- breaches list (same-decisions every skipped))
  (assert (= breaches []) breaches)
  (assert (< (* 2 skipped.takes) every.takes) #(skipped.takes every.takes)))


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
