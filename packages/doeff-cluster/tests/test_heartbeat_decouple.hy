;; heartbeat を worker の拍から切り離す(beat_policy・worker/protocol/coordinator_link・local.hy の宿 — #1933)。模擬の世界(sim-cluster)の検。
;;
;; - desired の変化(宣言し直し)は名指しの待ちで受け、worker は拍 1 つの内に起きる(反例: 待ちが届かない worker は間隔まで気づかない)。
;; - 生存の窓(lease-ms 10 秒)の中で heartbeat が届き、worker は生きていると数えられ続ける(反例: 間隔を窓より長くすると死んだと数える)。
;; - 切り離した task の短い lease は切れない(間隔は task の lease の 1/3 以下 — 反例: 間隔を lease より長くすると lost)。
;; - 待つ口の無い coordinator(/watch が 404)には今までどおり拍ごとに送る。
;; - fence の判断は今までどおり最後に届いた heartbeat から数える(網が切れて fence を越えた拍で job を止める)。
(require doeff-hy.macros [deftest defk <- val var])
(import collections [Counter])
(import doeff_time [Delay])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.sim.local :as local)
(import doeff_cluster.sim.local [sim-cluster SimWorker ReadCoordinator ProcessesOf Redeclare CutWorker FailRoute HostTruthOf HostTruth
                             WatchFailuresOf])
(import doeff_cluster.worker.core.beat_policy [WatchReading])
(import doeff_cluster.shared.intent.detached_model [AwaitDetached DetachedSucceeded DetachedLost])
(import doeff_cluster.shared.core.detached_rules [submit-detached-task])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.sim_programs [beacons beacons-v2 pulses slow-task sim-task-foundation NET])

(val SETTLE-SECONDS 12.0)
(val TIMING (ClusterTiming))


;; heartbeat の間隔 → worker 2 台(None = 本番の判断・数 = 反例の世界の壊れた間隔 ms)。
(val PAIRS (dfor every-ms [None 5000 8000 12000]
                 every-ms #((SimWorker :name "w1" :provides NET :beat-every-ms every-ms)
                            (SimWorker :name "w2" :provides NET :beat-every-ms every-ms))))


;; --- desired の変化に遅れず起きる ------------------------------------------------------------------------

(defk just-heard [name]
  {:pre [(: name str)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "worker name の heartbeat が coordinator に届いた直後(届いてから 100 ms 以内)まで待つため。答え = 届いてからの ms。"
  (<- first dict (ReadCoordinator (+ "/workers/" name)))
  (var silent (get first "silentMs"))
  (while (> silent 100)
    (<- (Delay 0.05))
    (<- again dict (ReadCoordinator (+ "/workers/" name)))
    (:= silent (get again "silentMs")))
  silent)


(defk redeclare-latency [mode]
  {:pre [(: mode str)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 落ち着いた後に beacon を版 2 に宣言し直し、旧い版の process が止まるまでの ms を返す(worker が変化に気づいた刻の物差し)。
   mode = tick(待つ口の無い coordinator — 最初から /watch が 404・拍ごとの heartbeat)・watch(待ちで受ける)・blind(落ち着いた後に
   待ちの口を 503 で塞ぐ — 待ちが届かない)。blind は、beacon を持つ worker の heartbeat が届いた直後に宣言し直す: 次に気づけるのは
   heartbeat の間隔の後で、遅れが heartbeat の位相の偶然に依らない(以前は宣言し直しが次の heartbeat の約 1 秒前に当たり、反例の余白が
   0 だった — #2719 で気づいた拍のうちに起こすようになると 500 ms 足りずに赤)。"
  (when (= mode "tick")
    (<- (FailRoute "GET" "/watch" 404 1000.0)))
  (<- (Delay SETTLE-SECONDS))
  (when (= mode "blind")
    (<- (FailRoute "GET" "/watch" 503 100.0))
    (<- (Delay 12.0))
    (<- running tuple (ProcessesOf "beacon"))
    (<- (just-heard (. (get running -1) worker))))
  (<- asked int (now-epoch-ms))
  (<- (Redeclare (beacons-v2 sim-foundation)))
  (<- (Delay 10.0))
  (<- processes tuple (ProcessesOf "beacon"))
  (val old (lfor p processes :if (and (< p.started-ms asked) (is-not p.ended-ms None) (>= p.ended-ms asked)) p))
  (- (. (get old 0) ended-ms) asked))


(deftest test-a-desired-change-wakes-the-worker-within-a-tick
  ;; 宣言し直しは待ちが受け、拍ごとに heartbeat を送る今までの形と拍 1 つ(0.5 秒)の差の内で旧い版を止める。反例 — 待ちが塞がれた
  ;; worker は heartbeat の間隔(8 秒)が来るまで気づかない(heartbeat の直後に宣言し直すので、遅れは間隔からその拍の分を引いた以上)。
  (<- ticking int (sim-cluster (beacons sim-foundation) (redeclare-latency "tick") :workers (get PAIRS None)))
  (<- watching int (sim-cluster (beacons sim-foundation) (redeclare-latency "watch") :workers (get PAIRS None)))
  (<- blind int (sim-cluster (beacons sim-foundation) (redeclare-latency "blind") :workers (get PAIRS 8000)))
  (assert (<= watching (+ ticking 500)) #(ticking watching blind))
  (assert (>= blind (+ ticking 1000)) #(ticking watching blind))
  (assert (>= blind (- 8000 1000)) #(ticking watching blind)))


(defk broken-watch-latency []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: redeclare-latency の watch と同じ宣言し直しの後、2 台の待ちの task が止まった記録を読む。答え = #(ms 記録の tuple)。"
  (<- latency int (redeclare-latency "watch"))
  (<- first tuple (WatchFailuresOf "w1"))
  (<- second tuple (WatchFailuresOf "w2"))
  #(latency (+ first second)))


(defk breaking-note [name boot after reading]
  {:pre [(: name str) (: boot str) (: after int) (: reading WatchReading)] :post [(: % bool)]
   :tags {:context "doeff-cluster-test" :role "program"}}
  "壊れた待ちの世界の note-watch: 2 回目からの待ちの答えで思わぬ例外を投げる(口を確かめた後に待ちの task が壊れる)。"
  (.update BROKEN-CALLS [name])
  (when (> (get BROKEN-CALLS name) 1)
    (raise (RuntimeError "壊れた待ち")))
  (<- more bool (ORIGINAL-NOTE-WATCH name boot after reading))
  more)

(val BROKEN-CALLS (Counter))
(val ORIGINAL-NOTE-WATCH local.note-watch)


(deftest test-a-watch-task-that-dies-is-recorded-and-falls-back-to-every-tick [monkeypatch]
  ;; 名指しの待ちの task が(口を確かめた後に)思わぬ例外で止まると、理由が世界の記録に 1 行ずつ残り(本番の「待ちの thread が
  ;; 止まった」)、その世代は拍ごとの heartbeat に戻って、宣言し直しに拍ごとの形と同じ刻で気づく(黙って間隔まで遅れない)。
  (<- ticking int (sim-cluster (beacons sim-foundation) (redeclare-latency "tick") :workers (get PAIRS None)))
  (.clear BROKEN-CALLS)
  (.setattr monkeypatch local "note_watch" breaking-note)
  (<- seen tuple (sim-cluster (beacons sim-foundation) (broken-watch-latency) :workers (get PAIRS None)))
  (val failures (get seen 1))
  (assert (= (len failures) 2) failures)
  (assert (all (gfor f failures (in "RuntimeError: 壊れた待ち" f.reason))) failures)
  (assert (<= (get seen 0) (+ ticking 500)) #(ticking seen)))


;; --- 生存の窓の中で heartbeat が届く ------------------------------------------------------------------------

(defk liveness-samples []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 落ち着いた後 60 秒の間 0.5 秒ごとに coordinator の名簿を読み、#(生きていないと数えた回数 最長の沈黙 ms) を返す。"
  (<- (Delay SETTLE-SECONDS))
  (var dead 0)
  (var longest 0)
  (for [_ (range 120)]
    (<- view dict (ReadCoordinator "/state"))
    (for [row (.values (get view "workers"))]
      (when (not (get row "live")) (:= dead (+ dead 1)))
      (:= longest (max longest (get row "silentMs"))))
    (<- (Delay 0.5)))
  #(dead longest))


(deftest test-heartbeats-arrive-inside-the-liveness-window
  ;; 間隔は窓の 1/4(2.5 秒)— 沈黙は間隔と拍 1 つの内・生きていないと数えられることは無い。反例 — 間隔を窓(10 秒)より長くすると
  ;; coordinator は worker を死んだと数える。
  (<- seen tuple (sim-cluster (beacons sim-foundation) (liveness-samples) :workers (get PAIRS None)))
  (assert (= (get seen 0) 0) seen)
  (assert (<= (get seen 1) (+ (// TIMING.lease-ms 4) 500)) seen)
  (<- broken tuple (sim-cluster (beacons sim-foundation) (liveness-samples) :workers (get PAIRS 12000)))
  (assert (> (get broken 0) 0) broken))


;; --- 切り離した task の lease が切れない --------------------------------------------------------------------

(defk short-lease-task []
  {:pre [] :post [(: % (| DetachedSucceeded DetachedLost))] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: lease 3 秒の切り離した task(20 秒眠る)を送り、終わりを待つ。"
  (<- (Delay SETTLE-SECONDS))
  (<- (submit-detached-task (slow-task sim-task-foundation 20.0) :key "short-lease" :needs NET :name "slow" :lease-seconds 3.0))
  (<- answer (AwaitDetached "short-lease" :timeout-seconds 60.0))
  answer)


(deftest test-a-short-detached-lease-is-extended-by-the-heartbeats
  ;; 間隔は自分の切り離した task の lease の 1/3(1 秒)以下になり、task は最後まで走る。反例 — 間隔 5 秒の worker では lease(3 秒)が
  ;; 切れて lost。
  (<- answer (| DetachedSucceeded DetachedLost) (sim-cluster (beacons sim-foundation) (short-lease-task) :workers (get PAIRS None)))
  (assert (isinstance answer DetachedSucceeded) answer)
  (<- broken (| DetachedSucceeded DetachedLost) (sim-cluster (beacons sim-foundation) (short-lease-task) :workers (get PAIRS 5000)))
  (assert (isinstance broken DetachedLost) broken))


;; --- 待つ口の無い coordinator では拍ごと ---------------------------------------------------------------------

(defk quiet-for [seconds without-watch]
  {:pre [(: seconds float) (: without-watch bool)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: (without-watch なら最初から /watch を 404 にして)seconds 秒待つ。"
  (when without-watch
    (<- (FailRoute "GET" "/watch" 404 1000.0)))
  (<- (Delay seconds))
  0)


(deftest test-a-coordinator-without-the-watch-gets-a-heartbeat-every-tick [monkeypatch]
  ;; /watch が 404 の coordinator には拍(0.5 秒)ごとに送る(30 秒・2 台で 100 回を越える)。待てる coordinator には間隔ごと(同じ
  ;; 30 秒で 40 回ほど)。
  (val beats (Counter))
  (val original local.heartbeat)
  (.setattr monkeypatch local "heartbeat" (fn [worker boot] (.update beats [worker.name]) (original worker boot)))
  (<- (sim-cluster (beacons sim-foundation) (quiet-for 30.0 True) :workers (get PAIRS None)))
  (val without (.total beats))
  (.clear beats)
  (<- (sim-cluster (beacons sim-foundation) (quiet-for 30.0 False) :workers (get PAIRS None)))
  (val with-watch (.total beats))
  (assert (> without 100) #(without with-watch))
  (assert (< with-watch 50) #(without with-watch)))


;; --- fence は最後に届いた heartbeat から数える ---------------------------------------------------------------

(defk cut-holder []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: pulse を持つ worker の網を 40 秒切り、切った時の最後に届いた heartbeat の刻と、その worker の pulse の process の
   終わりの刻を返す。"
  (<- (Delay SETTLE-SECONDS))
  (<- view dict (ReadCoordinator "/state"))
  (val holder (get (get (get view "placements") "pulse") "worker"))
  (<- truth HostTruth (HostTruthOf holder))
  (<- cut-at int (now-epoch-ms))
  (<- (CutWorker holder 40.0))
  (<- (Delay 30.0))
  (<- processes tuple (ProcessesOf "pulse"))
  (val mine (lfor p processes :if (and (= p.worker holder) (< p.started-ms cut-at)
                                       (or (is p.ended-ms None) (>= p.ended-ms cut-at))) p))
  #(truth.last-ok-ms (. (get mine 0) ended-ms)))


(deftest test-the-fence-still-counts-from-the-last-delivered-heartbeat
  ;; 網の切れた worker は、最後に届いた heartbeat から fence(20 秒)を越えた最初の拍で job を止める(拍の間隔 0.5 秒の内)。
  (<- seen tuple (sim-cluster (pulses sim-foundation) (cut-holder) :workers (get PAIRS None)))
  (val last-ok (get seen 0))
  (val ended (get seen 1))
  (assert (is-not ended None) seen)
  (assert (< TIMING.fence-ms (- ended last-ok) (+ TIMING.fence-ms 600)) #((- ended last-ok) seen)))
