;; 落ち着いた worker の拍の間の休み(WorkerRest・worker_policy.settled・local.hy の宿の AwaitHostChange — #2264)。
;;
;; - 判断(settled): 撃つ action が無く、どの job も時刻で答えの変わらない相にいる時だけ落ち着いている(反例: 再起動待ち・準備中・
;;   走っている検め・撃つ action のある拍は落ち着いていない)。
;; - 模擬の世界: 落ち着いた worker は heartbeat の期限まで眠り、拍の数が減る(反例: settled を常に偽にすると拍ごとに戻る)。
;; - 眠っている間に宿の真実が変わる(process が落ちる)と、その場で起きる(反例: 呼び鈴を鳴らさない世界では heartbeat の期限まで遅れる)。
;; 宣言の変化に拍 1 つの内に気づく・生存の窓の中で heartbeat が届く・fence は test_heartbeat_decouple.hy が同じ世界で確かめる。
(require doeff-hy.macros [deftest defk <- val var])
(import collections [Counter])
(import doeff_time [Delay])
(import doeff_cluster.clock [now-epoch-ms])
(import doeff_cluster.worker :as worker)
(import doeff_cluster.local :as local)
(import doeff_cluster.local [sim-cluster SimWorker Crash ProcessesOf])
(import doeff_cluster.worker_model [WorldView CodeView CodeState ProbeView ProbeState JobStatus JobPhase StartJob JobSpec])
(import doeff_cluster.worker_policy [settled])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.sim_programs [beacons NET])

(val SETTLE-SECONDS 12.0)
(val PAIR #((SimWorker :name "w1" :provides NET) (SimWorker :name "w2" :provides NET)))
(val READY (WorldView #((CodeView "rev1" CodeState.READY "/c/rev1")) #()))


(defk status-in [phase]
  {:pre [(: phase JobPhase)] :post [(: % JobStatus)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "判断の検の状態の行 1 つを作るため(相だけが違う)。"
  (JobStatus "a" phase "rev1" "rev1" 1 1))


;; --- 判断 ----------------------------------------------------------------------------------------------------

(deftest test-only-a-tick-without-actions-and-with-settled-phases-is-quiet
  (<- running JobStatus (status-in JobPhase.RUNNING))
  (<- backoff JobStatus (status-in JobPhase.BACKOFF))
  (<- preparing JobStatus (status-in JobPhase.PREPARING))
  (<- quiet bool (settled #() #(running) READY))
  (assert quiet)
  ;; 反例: 再起動待ち・準備中の相は時刻で答えが変わる。
  (<- waiting bool (settled #() #(running backoff) READY))
  (assert (not waiting))
  (<- unready bool (settled #() #(preparing) READY))
  (assert (not unready))
  ;; 反例: 撃つ action のある拍・揃っていない木・走っている検め。
  (<- acting bool (settled #((StartJob (JobSpec "a" "jobs.a" #() "rev1") 1 "/c/rev1")) #(running) READY))
  (assert (not acting))
  (<- failed-tree bool (settled #() #(running) (WorldView #((CodeView "rev2" CodeState.FAILED None)) #())))
  (assert (not failed-tree))
  (<- probing bool (settled #() #(running) (WorldView (. READY codes) #() :probes #((ProbeView "h" ProbeState.RUNNING)))))
  (assert (not probing)))


;; --- 模擬の世界: 静かな間は拍が減る ------------------------------------------------------------------------------

(defk quiet-for [seconds]
  {:pre [(: seconds float)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 落ち着くまで待ってから seconds 秒何もしない。"
  (<- (Delay SETTLE-SECONDS))
  (<- (Delay seconds))
  0)


(defk never-settled [actions report world]
  {:pre [(: actions tuple) (: report tuple) (: world WorldView)] :post [(: % bool)]
   :tags {:context "doeff-cluster-test" :role "judgment"}}
  "反例の世界の判断: どの拍も落ち着いていないと答える(拍ごとに休む今までの形)。"
  False)


(defk counted-ticks [ticks]
  {:pre [(: ticks Counter)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "beacons の 2 台を 12 + 30 秒走らせ、worker の拍の数を返すため。"
  (.clear ticks)
  (<- (sim-cluster (beacons sim-foundation) (quiet-for 30.0) :workers PAIR))
  (.total ticks))


(deftest test-a-settled-worker-rests-until-the-heartbeat-is-due [monkeypatch]
  ;; 拍ごとに休む形なら 2 台 × 42 秒 / 0.5 秒 ≈ 170 拍。落ち着いた worker は heartbeat の間隔(生存の窓の 1/4 = 2.5 秒)まで眠るので
  ;; その半分を大きく下回る。反例 — settled を常に偽にすると拍ごとに戻る。
  (val ticks (Counter))
  (val original worker.worker-tick)
  (.setattr monkeypatch worker "worker_tick" (fn [state policy stopping] (.update ticks ["tick"]) (original state policy stopping)))
  (<- resting int (counted-ticks ticks))
  (.setattr monkeypatch worker "settled" never-settled)
  (<- every-tick int (counted-ticks ticks))
  (assert (> every-tick 150) #(resting every-tick))
  (assert (< resting (// every-tick 2)) #(resting every-tick)))


;; --- 眠っている間に process が落ちるとその場で起きる ------------------------------------------------------------

(defk crash-restart-ms []
  {:pre [] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 落ち着いた後、heartbeat の直後(眠りの始め)に beacon の process を落とし、次の process が起きるまでの ms を返す。"
  (<- (Delay SETTLE-SECONDS))
  (<- (Delay 0.6))
  (<- crashed-at int (now-epoch-ms))
  (<- (Crash "beacon"))
  (<- (Delay 10.0))
  (<- processes tuple (ProcessesOf "beacon"))
  (val later (lfor p processes :if (>= p.started-ms crashed-at) p))
  (- (. (get later 0) started-ms) crashed-at))


(defk silent-bells [bells]
  {:pre [(: bells tuple)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "反例の世界: 宿の真実が変わっても休みの呼び鈴を鳴らさない(眠りは heartbeat の期限まで続く)。"
  None)


(deftest test-a-resting-worker-wakes-when-a-process-ends [monkeypatch]
  ;; 再起動は落ちた刻 + backoff(2 秒)+ 拍 1 つの内。反例 — 呼び鈴を鳴らさない世界では、heartbeat の期限まで落ちたことに
  ;; 気づかないので遅れる。
  (<- woken int (sim-cluster (beacons sim-foundation) (crash-restart-ms) :workers PAIR))
  (.setattr monkeypatch local "ring_rest_bells" silent-bells)
  (<- deaf int (sim-cluster (beacons sim-foundation) (crash-restart-ms) :workers PAIR))
  (assert (<= woken 2600) #(woken deaf))
  (assert (> deaf (+ woken 500)) #(woken deaf)))
