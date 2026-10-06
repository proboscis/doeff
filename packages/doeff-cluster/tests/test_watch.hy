;; 版の変化を待つ読み GET /watch(watch_policy・coordinator.coordinator-step — #1933)。
;;
;; - 版(state の revision)が after から変わった刻ちょうどに返る(書きの後の同じ拍 — 読み直さない)。
;; - 変わらなければ期限の刻ちょうどに「変わっていない」と返る(coordinator は次の期限まで待つ — #3865)。
;; - worker を名指した待ちは、他の worker の変化(版は進む)では起きず、その worker の返事が変わる変化で起きる。
;; - 止まる coordinator は待ちに「変わっていない」と返してから止まる(送り手を接続の失敗まで待たせない)。
;; - after の無い問いは 400。heartbeat の返事は、次の待ちの after に使う版を運ぶ。
(require doeff-hy.macros [deftest defk <- val var])
(import doeff_core_effects.scheduler [Spawn Task Wait])
(import doeff_time [Delay])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState ClusterNaming Watcher WatchRefusal
                                                       WatchStep])
(import doeff_cluster.coordinator.core.cluster_policy [heartbeat-reply])
(import doeff_cluster.coordinator.protocol.replies [reply-json])
(import doeff_cluster.coordinator.core.watch_policy [watch-of settle-watch all-waiting-unchanged])
(import dataclasses [replace])
(import doeff_cluster.shared.protocol.inbox [http-request])
(import doeff_cluster.sim.local [sim-cluster send-request ClientLink SimLink SimWorker ReadCoordinator DrainWorker StopCoordinator])
(import doeff_cluster.worker.intent.worker_model [WorkerPolicy])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.sim_programs [beacons quitters])

(val TWO-WORKERS #((SimWorker :name "w1" :provides (frozenset ["cluster-net"]) :task-reserve 0)
                   (SimWorker :name "w2" :provides (frozenset ["cluster-net"]) :task-reserve 0)))
;; 系が落ち着くまで(beacon が置かれ、readiness の window 5 秒が埋まる)待つ秒。
(val SETTLE-SECONDS 12.0)
;; heartbeat の間(4 秒)に coordinator が要求の無い拍を持つ worker の設定 — 模擬の列が何も変えない拍を飛ばす場面を作る(既定の 0.5 秒
;; ごとの heartbeat では要求が絶えず、飛ばす拍が無い)。
(val SPARSE-TICK-SECONDS 4.0)
(val SPARSE-POLICY (WorkerPolicy :restart-backoff-ms 1000000000 :restart-backoff-max-ms 1000000000))


(defk watch-once [query]
  {:pre [(: query dict)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書きの送り手の口で GET /watch を 1 回送り、返事 #(status 本文) と返事を受けた刻を返すため。"
  (<- link SimLink (ClientLink))
  (<- answer tuple (send-request link "GET" "/watch" query None))
  (<- at int (now-epoch-ms))
  #(answer at))


(defk settled-view []
  {:pre [] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "program"}}
  "系が落ち着くまで待ち、GET /state の本文(版 revision と置き先 placements)を読むため。"
  (<- (Delay SETTLE-SECONDS))
  (<- view dict (ReadCoordinator "/state"))
  view)


(defk change-wakes-watcher []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 版 R を読んで R の後を 8 秒まで待たせ、2.5 秒後に worker w1 の drain を頼む(Worker の status が変わり版が進む)。
   答え = #(R drain を頼んだ刻 待ちの #(返事 刻))。"
  (<- view dict (settled-view))
  (val revision (get view "revision"))
  (<- waiter Task (Spawn (watch-once {"after" (str revision) "timeoutSeconds" "8"})))
  (<- (Delay 2.5))
  (<- drained-at int (now-epoch-ms))
  (<- (DrainWorker "w1"))
  (<- seen tuple (Wait waiter))
  #(revision drained-at seen))


(deftest test-a-watch-returns-at-the-change-without-rereading
  (<- seen tuple (sim-cluster (beacons sim-foundation) (change-wakes-watcher) :workers TWO-WORKERS))
  (val revision (get seen 0))
  (val drained-at (get seen 1))
  (val answer (get seen 2 0))
  (val at (get seen 2 1))
  (assert (= (get answer 0) 200) answer)
  (assert (get (get answer 1) "changed") answer)
  (assert (> (get (get answer 1) "revision") revision) #(answer revision))
  ;; drain の書きと同じ刻(期限の 8 秒でも、次の拍でもない)。
  (assert (= at drained-at) #(at drained-at)))


(defk quiet-watch [seconds]
  {:pre [(: seconds float)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 版 R を読み、何も変えずに R の後を seconds 秒まで待つ。答え = #(待ち始めた刻 返事 返事の刻)。"
  (<- view dict (settled-view))
  (<- started int (now-epoch-ms))
  (<- seen tuple (watch-once {"after" (str (get view "revision")) "timeoutSeconds" (str seconds)}))
  #(started (get seen 0) (get seen 1)))


(deftest test-an-unchanged-watch-returns-at-its-deadline-either-way
  ;; 変わらない待ちは、期限の刻ちょうどに「変わっていない」と返る(coordinator は待ちの期限まで受付を待つ — 1 秒の格子に丸めない・
  ;; #3865)。worker の代役が静かな拍を眠る走り(既定)でも、1 拍ずつ打つ走りでも同じ刻。反例: 期限を次に起きる刻に入れない作りは、
  ;; 次に状態の変わる刻まで寝過ごす。
  (for [seconds [0.5 1.2 2.5]]
    (<- skipped tuple (sim-cluster (quitters sim-foundation) (quiet-watch seconds) :workers TWO-WORKERS :policy SPARSE-POLICY :tick-seconds SPARSE-TICK-SECONDS))
    (<- every tuple (sim-cluster (quitters sim-foundation) (quiet-watch seconds) :workers TWO-WORKERS :policy SPARSE-POLICY :tick-seconds SPARSE-TICK-SECONDS
                                 :skip-idle False))
    (for [#(started answer at) [skipped every]]
      (assert (= (get answer 0) 200) answer)
      (assert (not (get (get answer 1) "changed")) answer)
      (assert (= (- at started) (round (* 1000 seconds))) #(seconds (- at started))))))


(defk watcher-of [query]
  {:pre [(: query dict)] :post [(: % Watcher)] :tags {:context "doeff-cluster-test" :role "program"}}
  "刻 0 に受けた GET /watch(問い query)の待ちを作るため。"
  (<- watch (| Watcher WatchRefusal None) (watch-of (! (http-request "GET" "/watch" query None)) 0 (ClusterTiming)))
  (match watch
    (Watcher) watch
    _ (raise (ValueError (.format "待ちにならない問い: {}" query)))))


(defk scoped-watches []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: beacon を持つ worker H と持たない worker O を読み、H を名指した待ち(4 秒)の間に O の drain を頼む(版は進むが H の返事は
   変わらない)。次に H を名指した待ちの間に H の drain を頼む(H の返事の draining が変わる)。答え = #(1 つ目の待ち 2 つ目の待ち
   O の drain の刻 H の drain の刻 最初の版)。"
  (<- view dict (settled-view))
  (val holder (get (get (get view "placements") "beacon") "worker"))
  (val other (if (= holder "w1") "w2" "w1"))
  (val revision (get view "revision"))
  (<- first Task (Spawn (watch-once {"after" (str revision) "timeoutSeconds" "4" "worker" holder})))
  (<- (Delay 1.0))
  (<- other-at int (now-epoch-ms))
  (<- (DrainWorker other))
  (<- first-seen tuple (Wait first))
  (val next-revision (get (get (get first-seen 0) 1) "revision"))
  (<- second Task (Spawn (watch-once {"after" (str next-revision) "timeoutSeconds" "8" "worker" holder})))
  (<- (Delay 1.0))
  (<- holder-at int (now-epoch-ms))
  (<- (DrainWorker holder))
  (<- second-seen tuple (Wait second))
  #(first-seen second-seen other-at holder-at revision))


(deftest test-a-worker-scoped-watch-wakes-only-for-its-own-changes
  (<- seen tuple (sim-cluster (beacons sim-foundation) (scoped-watches) :workers TWO-WORKERS))
  (val first (get seen 0 0))
  (val first-at (get seen 0 1))
  (val second (get seen 1 0))
  (val second-at (get seen 1 1))
  (val other-at (get seen 2))
  (val holder-at (get seen 3))
  (val revision (get seen 4))
  ;; 他の worker の drain では起きない: 期限(4 秒)で「変わっていない」— 版は進んでいる(次の after に使える)。
  (assert (= (get first 0) 200) first)
  (assert (not (get (get first 1) "changed")) first)
  (assert (> (get (get first 1) "revision") revision) #(first revision))
  (assert (> first-at other-at) #(first-at other-at))
  ;; 名指した worker の drain では、その刻に起きる。
  (assert (get (get second 1) "changed") second)
  (assert (= second-at holder-at) #(second-at holder-at)))


(defk watch-across-stop []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 10 秒まで待たせ、1 秒後に coordinator を 3 秒止める。答え = #(待ちの #(返事 刻) 止めを頼んだ刻)。"
  (<- view dict (settled-view))
  (<- waiter Task (Spawn (watch-once {"after" (str (get view "revision")) "timeoutSeconds" "10"})))
  (<- (Delay 1.0))
  (<- stop-at int (now-epoch-ms))
  (<- (StopCoordinator 3.0))
  (<- seen tuple (Wait waiter))
  #(seen stop-at))


(deftest test-a-stopping-coordinator-answers-its-watchers
  ;; 止まる coordinator は待ちに「変わっていない」と返す(反例 — 返さずに止まれば、送り手は接続の失敗 #(None …) を受ける)。
  (<- seen tuple (sim-cluster (beacons sim-foundation) (watch-across-stop) :workers TWO-WORKERS))
  (val answer (get seen 0 0))
  (val at (get seen 0 1))
  (val stop-at (get seen 1))
  (assert (= (get answer 0) 200) answer)
  (assert (not (get (get answer 1) "changed")) answer)
  ;; 止まるのは次の拍(1 秒以内)。期限の 10 秒までは待たせない。
  (assert (<= 0 (- at stop-at) 1000) #(at stop-at)))


(defk refused-watches []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: after の無い問いと、数でない timeoutSeconds の問いを送る。"
  (<- missing tuple (watch-once {}))
  (<- bad tuple (watch-once {"after" "0" "timeoutSeconds" "soon"}))
  #((get missing 0) (get bad 0)))


(deftest test-an-unreadable-watch-query-is-refused
  (<- seen tuple (sim-cluster (beacons sim-foundation) (refused-watches) :workers TWO-WORKERS))
  (for [answer seen]
    (assert (= (get answer 0) 400) answer)))


(deftest test-the-heartbeat-reply-carries-the-revision-to-watch-after
  (val reply (! (heartbeat-reply (ClusterState :revision 7) "w1" (ClusterTiming))))
  (assert (= reply.revision 7) reply)
  (assert (= (get (! (reply-json reply)) "revision") 7) reply))


;; --- 待ちを判じずに持ち越す条件(#2670 の根 B) ------------------------------------------------------------------------
;; 静かな区間の歩は、どの待ちにも settle-watch が答えない時(watch_policy.all-waiting-unchanged)、待ちを 1 件ずつ判じずに持ち越す。
;; この条件が真なら settle-watch は答えず、待ちを同じ物のまま返す。版が動いた待ち・見え方を覚える前の名指しの待ち・期限の来た待ちは
;; 条件が偽で、settle-watch が判じる。反例 — 版を見ない条件では、版の動いた待ちを判じずに持ち越す(この検は赤)。

(deftest test-watchers-carried-without-judging-are-exactly-those-settle-watch-leaves
  (val state (ClusterState))
  (val moved (replace state :revision 1))
  (<- reader Watcher (watcher-of {"after" "0" "timeoutSeconds" "10"}))
  (<- named Watcher (watcher-of {"after" "0" "timeoutSeconds" "10" "worker" "w1" "boot" "b1"}))
  (<- first WatchStep (settle-watch named state 0 (ClusterTiming)))
  (val marked first.watcher)
  (assert (is-not marked.mark None) marked)
  (val cases [#(reader state 5000 True) #(marked state 5000 True) #(reader moved 5000 False) #(marked moved 5000 False)
              #(named state 5000 False) #(reader state 10000 False) #(marked state 10000 False)])
  (for [#(watcher seen now expected) cases]
    (<- unchanged bool (all-waiting-unchanged #(watcher) seen now))
    (assert (= unchanged expected) #(watcher.worker (is-not watcher.mark None) seen.revision now))
    (when unchanged
      (<- judged WatchStep (settle-watch watcher seen now (ClusterTiming)))
      (assert (is judged.answer None) judged)
      (assert (is judged.watcher watcher) judged))))
