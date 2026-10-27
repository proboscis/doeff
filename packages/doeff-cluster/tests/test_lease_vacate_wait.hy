;; 名前付きの lease の行から、名指した担い手の外の token が外れるのを待つ(GET /watch?lease=<名>&vacate=<担い手> — AwaitLeaseVacated)。
;; 空きの待ち(AwaitLeaseFree)は期限の切れていない担い手の数で起きるので、期限の切れた token が行に残る間はすぐ返る。待つ側が「行から
;; token が外れるまで取らない」と決めた時、空きの待ちでは時計で読み直すしかなかった。この待ちは行から token が外れる書きの後にだけ起き、
;; 時刻では起きない。
;;   1 期限の切れた外の token が残る間は起きず、その token が行から外れた刻ちょうどに起きる(期限の刻では起きない)。同じ刻に出した
;;     空きの待ちはすぐ返る(反例 — 空きの待ちで待つと、期限の切れた token の在る間 回り続ける)。
;;   2 行に自分の token しか無ければすぐ起きる(自分の token は外を待たない)。
;;   3 何も外れなければ待ちの期限で偽を返す(判断の検 — 模擬の世界の上限は本番の既定の 350 倍で長い)。
;;   4 答えに vacate の印の無い返事(この問いを知らない旧い coordinator は素の空きの待ちと答える)を断る。
;;   5 落ち着いている判じ(all-waiting-unchanged)は settle-watch と同じ枝で、次に起きる刻(watchers-due)は外の token の期限を数えない。
(require doeff-hy.macros [deftest defk <- val var])
(import dataclasses [replace])
(import doeff_events [MemoryBroker])
(import doeff_core_effects.scheduler [Spawn Wait Task])
(import doeff_time [Delay])
(import doeff_hy.table [TableWrite table-of])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.shared.core.lease_rules [semaphore-key outside-holders])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.shared.intent.due_model [DueAt])
(import doeff_cluster.shared.intent.semaphore_model [ClusterSemaphore LeaseOp LeaseAnswer AwaitLeaseFree AwaitLeaseVacated])
(import doeff_cluster.shared.protocol.board_requests [lease-vacate-request lease-vacate-answer])
(import doeff_cluster.shared.protocol.inbox [http-request])
(import doeff_cluster.coordinator.intent.cluster_model [BoardRow ClusterState Watcher WatchRefusal WatchAnswer WatchStep])
(import doeff_cluster.coordinator.core.watch_policy [watch-of settle-watch all-waiting-unchanged watch-answer-body])
(import doeff_cluster.coordinator.core.wake_policy [watchers-due])
(import doeff_cluster.sim.local [sim-cluster])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.sim_programs [beacons])

(val LOCK (ClusterSemaphore "vacate-lock" 1))
(val ELDER "elder")
(val YOUNG "young")
(val ELDER-TOKEN "elder/1")
;; 外の担い手の期限(ms)— 延ばさずに切れる。外すのはその後 HOLD-SECONDS の刻(期限と外しの刻を離し、どちらで起きたかを見分ける)。
(val SHORT-TTL-MS 1300)
(val HOLD-SECONDS 5.3)
;; 「刻ちょうど」と数える遅れの上限(ms)— 要求の往復の分。
(val SLACK-MS 100)


(defk vacate-waiter [name holder]
  {:pre [(: name str) (: holder str)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "lease name の行から担い手 holder の外の token が外れるのを待ち、答えと起きた刻(epoch ms)を返すため。"
  (<- seen bool (AwaitLeaseVacated name holder))
  (<- at int (now-epoch-ms))
  #(seen at))


(defk vacated-at-the-release []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き 1: 外の担い手が短い期限で lease を取り、延ばさずに置く。期限の後に空けの待ちと空きの待ちを出し、HOLD-SECONDS 後に外の token を
   返す。#(期限の刻 空きの待ちが返った刻 返した刻 空けの待ちの答え 空けの待ちが起きた刻) を返すため。"
  (<- sent int (now-epoch-ms))
  (<- held LeaseAnswer (LeaseOp LOCK.name "claim" ELDER-TOKEN LOCK.permits SHORT-TTL-MS))
  (assert held.ok held)
  (<- (Delay (/ (* 2 SHORT-TTL-MS) 1000.0)))
  (<- waiting Task (Spawn (vacate-waiter LOCK.name YOUNG)))
  (<- _free bool (AwaitLeaseFree LOCK.name))
  (<- free-at int (now-epoch-ms))
  (<- (Delay HOLD-SECONDS))
  (<- released-at int (now-epoch-ms))
  (<- gone LeaseAnswer (LeaseOp LOCK.name "release" ELDER-TOKEN LOCK.permits 0))
  (assert gone.ok gone)
  (<- #(seen woke-at) tuple (Wait waiting))
  #((+ sent SHORT-TTL-MS) free-at released-at seen woke-at))


(deftest test-a-vacate-wait-wakes-when-the-expired-token-leaves-the-row-not-at-its-expiry
  (<- #(expires-at free-at released-at seen woke-at) tuple
      (sim-cluster :notice-broker (MemoryBroker) (beacons sim-foundation) (vacated-at-the-release)))
  ;; 反例: 空きの待ちは期限の切れた token が残っていても、出したその刻に返る(待つ側が読み直しを回し続ける形)。
  (assert (< expires-at free-at (+ expires-at SHORT-TTL-MS SLACK-MS)) #(expires-at free-at))
  (assert seen #(seen woke-at))
  (assert (<= released-at woke-at (+ released-at SLACK-MS)) #(released-at woke-at)))


(defk own-tokens-only []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き 2: 自分の token だけが行に在る lease の空けの待ちを出し、#(出した刻 答え 起きた刻) を返すため。"
  (<- held LeaseAnswer (LeaseOp LOCK.name "claim" (+ YOUNG "/1") LOCK.permits 30000))
  (assert held.ok held)
  (<- sent int (now-epoch-ms))
  (<- #(seen at) tuple (vacate-waiter LOCK.name YOUNG))
  #(sent seen at))


(deftest test-a-vacate-wait-does-not-wait-on-its-own-tokens
  (<- #(sent seen at) tuple (sim-cluster :notice-broker (MemoryBroker) (beacons sim-foundation) (own-tokens-only)))
  (assert seen)
  (assert (<= sent at (+ sent SLACK-MS)) #(sent at)))


;; --- 旧い coordinator の答え ---------------------------------------------------------------------------------------------

(deftest test-a-vacate-answer-without-the-mark-is-refused
  ;; 旧い coordinator は vacate を読まず、期限の切れた token の在る間 {"changed" true} をすぐ返す。真と読むと待つ側は回り続けるので断る。
  (for [answered [{"revision" 3 "changed" True} {"revision" 3 "changed" True "vacate" "other"}]]
    (var refused False)
    (try
      (lease-vacate-answer answered YOUNG)
      (except [ValueError]
        (:= refused True)))
    (assert refused answered))
  (assert (lease-vacate-answer {"revision" 3 "changed" True "vacate" YOUNG} YOUNG))
  (assert (not (lease-vacate-answer {"revision" 3 "changed" False "vacate" YOUNG} YOUNG)))
  (assert (= (lease-vacate-request LOCK.name YOUNG) #("GET" "/watch" {"lease" LOCK.name "vacate" YOUNG} None))))


;; --- 判断(watch_policy・wake_policy) -------------------------------------------------------------------------------------

(defk watcher-of [query]
  {:pre [(: query dict)] :post [(: % (| Watcher WatchRefusal None))] :tags {:context "doeff-cluster-test" :role "program"}}
  "刻 0 に受けた GET /watch(問い query)の待ちか断りを作るため。"
  (<- watch (| Watcher WatchRefusal None) (watch-of (! (http-request "GET" "/watch" query None)) 0 (ClusterTiming)))
  watch)


(defk state-with [holders]
  {:pre [(: holders dict)] :post [(: % ClusterState)] :tags {:context "doeff-cluster-test" :role "program"}}
  "LOCK の盤の行の担い手(token → 期限 ms)が holders の状態を作るため。"
  (val row {"permits" LOCK.permits "holders" holders})
  (ClusterState :board (table-of #((TableWrite (semaphore-key LOCK.name) (BoardRow :value row :version 1 :expires-ms None :size 1))))))


(deftest test-a-vacate-query-is-read-only-with-a-lease
  (<- vacating (| Watcher WatchRefusal None) (watcher-of {"lease" LOCK.name "vacate" YOUNG "timeoutSeconds" "10"}))
  (assert (isinstance vacating Watcher) vacating)
  (assert (= #(vacating.lease vacating.vacate) #(LOCK.name YOUNG)) vacating)
  (<- alone (| Watcher WatchRefusal None) (watcher-of {"after" "0" "vacate" YOUNG}))
  (assert (isinstance alone WatchRefusal) alone)
  (<- empty (| Watcher WatchRefusal None) (watcher-of {"lease" LOCK.name "vacate" ""}))
  (assert (isinstance empty WatchRefusal) empty))


(deftest test-outside-holders-count-expired-tokens-and-skip-own
  (<- outside tuple (outside-holders {"permits" 1 "holders" {"elder/1" 5 "young/2" 5 "younger/1" 5}} YOUNG))
  (assert (= (sorted outside) ["elder/1" "younger/1"]) outside)
  (<- none tuple (outside-holders None YOUNG))
  (assert (= none #()) none))


(deftest test-a-vacate-watcher-settles-only-on-the-row-and-its-deadline
  ;; 落ち着いている判じは settle-watch の枝と同じ: 外の token(期限の切れた物も)が残り期限の前 = 落ち着いている(答えない)・外の token が
  ;; 無い = 起きる・期限 = 偽で返す。空きの待ちと違い、外の token の期限は判じを変えない。
  (<- watcher (| Watcher WatchRefusal None) (watcher-of {"lease" LOCK.name "vacate" YOUNG "timeoutSeconds" "10"}))
  (<- expired ClusterState (state-with {ELDER-TOKEN 1000}))
  (<- live ClusterState (state-with {ELDER-TOKEN 9000}))
  (<- own ClusterState (state-with {"young/1" 1000}))
  (val cases [#(expired 5000 True None) #(live 5000 True None) #(own 5000 False True) #((ClusterState) 5000 False True)
              #(expired 10000 False False)])
  (for [#(state now expected changed) cases]
    (<- unchanged bool (all-waiting-unchanged #(watcher) state now))
    (assert (= unchanged expected) #(now unchanged))
    (<- judged WatchStep (settle-watch watcher state now (ClusterTiming)))
    (if (is changed None)
        (assert (is judged.answer None) judged)
        (assert (= judged.answer.changed changed) judged))))


(deftest test-a-vacate-watcher-wakes-the-coordinator-only-at-its-deadline
  ;; 失敗ケース: 外の token の期限(9000)で調停ループを起こすと、起きても行は変わらず、答えずにまた待つ空の歩になる。
  (<- watcher (| Watcher WatchRefusal None) (watcher-of {"lease" LOCK.name "vacate" YOUNG "timeoutSeconds" "10"}))
  (<- live ClusterState (state-with {ELDER-TOKEN 9000}))
  (<- due (| DueAt object) (watchers-due #(watcher) live 5000))
  (assert (= due (DueAt :at 10000)) due))


(deftest test-a-vacate-answer-body-carries-the-mark
  (<- watcher (| Watcher WatchRefusal None) (watcher-of {"lease" LOCK.name "vacate" YOUNG "timeoutSeconds" "10"}))
  (<- body dict (watch-answer-body watcher (WatchAnswer 4 True)))
  (assert (= body {"revision" 4 "changed" True "vacate" YOUNG}) body)
  (<- plain (| Watcher WatchRefusal None) (watcher-of {"lease" LOCK.name}))
  (<- free dict (watch-answer-body plain (WatchAnswer 4 True)))
  (assert (= free {"revision" 4 "changed" True}) free))
