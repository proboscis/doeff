;; worker の拍の間の眠りを宣言の変化で起こす(#2692)。
;;
;; worker の調整ループ(worker/core/program の run-worker)は拍ごとに tick-seconds(0.5 秒)を眠る。前の形は眠りが無条件で、名指しの待ちが
;; 「変わった」と答えても次の拍の境まで起きず、task の開始が変化の刻の位相で 0〜0.5 秒遅れた。今は宣言の読みが呼び鈴(DesiredJobs.changed)を
;; 添え、眠りは呼び鈴と tick-seconds を競わせる(呼び鈴で起きた時は拍の終わりから wake-gap-seconds まで眠り足す)。
;;
;; - 眠りの途中で宣言が変われば、拍の境を待たずに起きる(模擬の世界 sim-cluster で、置いた task の開始までの仮想の ms を測る)。
;;   反例: 呼び鈴を無視して tick-seconds を眠る形では、変化の位相しだいで 300 ms 以上待つ。
;; - 呼び鈴が鳴らない(変化が無い・起こしを取りこぼした)時も、拍は tick-seconds ごとに打たれる(止まらない・早まらない)。
;; - 起こしが途切れなく続いても、拍は 1 秒に 1 / wake-gap-seconds 回まで(空回りしない)。反例: 間を空けない形では同じ仮想の刻で拍が
;;   回り続ける(この検は上限の拍の数で打ち切って赤)。
;; - 1 回の眠りの間に変化が何度来ても、鳴る呼び鈴は 1 つ(本番の coordinator への口 — 鳴るまで拍をまたいで同じ呼び鈴を渡す)。
(require doeff-hy.macros [deftest defk defhandler <- val var])
(import doeff_time [Delay SimClock sim-time-handler])
(import doeff_core_effects.scheduler [CreatePromise CompletePromise Promise])
(import tests.clock_fixtures [clock-ms])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.shared.core.detached_rules [submit-detached-task])
(import doeff_cluster.shared.intent.detached_model [AwaitDetached])
(import doeff_cluster.sim.local [sim-cluster SimWorker ProcessesOf ReadCoordinator])
(import doeff_cluster.worker.intent.worker_model [WorkerPolicy WorldView DesiredJobs ReadDesired ObserveWorld WorkerStopRequested
                                                  PublishStatus EnvReport])
(import doeff_cluster.worker.core.program [run-worker])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.sim_programs [beacons slow-task sim-task-foundation NET])

(val POLICY (WorkerPolicy))
(val READ-LIMIT 200)        ; 空回りの反例を打ち切る拍の数(同じ仮想の刻で回り続ける形を赤にする)
(val SETTLE-SECONDS 12.0)


;; --- 拍の打ち方(偽の宿 — 仮想の時計の上で本物の run-worker を回す)---------------------------------------------

(defclass TickLog []
  "偽の宿の観測: reads = 宣言の読み(拍)の仮想の時刻(epoch ms)の列。"
  (defn #^ None __init__ [self #^ SimClock clock]
    (setv self.clock clock self.reads #())))


(defhandler bell-host [#^ TickLog log #^ Promise bell #^ int stop-ms]
  ;; 引数に残す理由: 検ごとに別の時計・呼び鈴・止める刻で並べる(Ask で区別できない)。
  ;; 宣言は空のまま、毎拍同じ呼び鈴(bell)を添えて答える。止める刻 stop-ms か読みの上限で止まれと答える。
  (ReadDesired [env-report]
    (setv log.reads (+ log.reads #((clock-ms log.clock))))
    (resume (DesiredJobs #() :changed bell.future)))
  (WorkerStopRequested []
    (resume (or (>= (clock-ms log.clock) stop-ms) (>= (len log.reads) READ-LIMIT))))
  (EnvReport [] (resume None))
  (ObserveWorld [] (resume (WorldView #() #())))
  (PublishStatus [statuses note] (resume None)))


(defk ticks-with-bell [rung stop-ms]
  {:pre [(: rung bool) (: stop-ms int)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "本物の run-worker を偽の宿の上で stop-ms まで回し、拍の仮想の時刻の列を返すため。rung = 呼び鈴を始めから鳴らしておく(起こしが
   途切れなく続く世界)・偽 = 呼び鈴が鳴らない世界。"
  (<- bell Promise (CreatePromise))
  (when rung
    (<- (CompletePromise bell True)))
  (val log (TickLog (SimClock)))
  (<- ((sim-time-handler :clock log.clock) ((bell-host log bell stop-ms) (run-worker POLICY))))
  log.reads)


(deftest test-a-silent-bell-still-ticks-every-tick-seconds
  ;; 呼び鈴が鳴らない(変化が無い・起こしを取りこぼした)時は、拍は tick-seconds(500 ms)ごと — 止まらず、早まりもしない。
  (<- reads tuple (ticks-with-bell False 3000))
  (val gaps (lfor #(a b) (zip reads (cut reads 1 None)) (- b a)))
  (assert (>= (len gaps) 5) reads)
  (assert (all (gfor g gaps (= g 500))) gaps))


(deftest test-repeated-wakes-do-not-busy-loop
  ;; 起こしが途切れなく続いても(呼び鈴が鳴りっぱなし)、拍は wake-gap-seconds(100 ms)ごと — 1 秒に 10 回まで。起こしは効いている
  ;; (拍は tick-seconds ごとの 2 回より多い)。反例: 間を空けない形は同じ刻で READ-LIMIT まで回る・呼び鈴を無視する形は 1 秒に 2 回。
  (<- reads tuple (ticks-with-bell True 1000))
  (val in-first-second (lfor r reads :if (< r 1000) r))
  (assert (< (len reads) READ-LIMIT) (len reads))
  (assert (<= (len in-first-second) 10) reads)
  (assert (> (len in-first-second) 2) reads)
  (assert (all (gfor #(a b) (zip reads (cut reads 1 None)) (>= (- b a) 100))) reads))


;; --- 眠りの途中の変化で起きる(模擬の世界)-------------------------------------------------------------------

(defk task-id-named [name]
  {:pre [(: name str)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "coordinator の状態から、name で出した task の id を読むため。"
  (<- state dict (ReadCoordinator "/state"))
  (val ids (lfor t (get state "tasks") :if (= (get t "name") name) (get t "id")))
  (assert (= (len ids) 1) (get state "tasks"))
  (get ids 0))


(defk start-latency [name pause]
  {:pre [(: name str) (: pause float)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "pause 秒眠った後(拍の位相をずらす)に切り離した task を 1 つ置き、置いてから task の process が起きるまでの仮想の ms を返すため。"
  (<- (Delay pause))
  (<- asked int (now-epoch-ms))
  (<- (submit-detached-task (slow-task sim-task-foundation 0.2) :key name :needs NET :name name))
  (<- (AwaitDetached name :timeout-seconds 30.0))
  (<- id str (task-id-named name))
  (<- runs tuple (ProcessesOf (+ "task/" id)))
  (- (. (get runs 0) started-ms) asked))


(val PAUSES #(0.37 0.61 0.83 1.13 0.29 0.71))

(defk start-latencies []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 落ち着いた後、木を用意させる task を 1 つ走らせ(最初の task の準備の拍はこの検の外 — 別の直し)、拍の位相をずらしながら
   task を 1 つずつ置き、それぞれの開始までの ms を返す。"
  (<- (Delay SETTLE-SECONDS))
  (<- (start-latency "warm" 0.0))
  (var latencies #())
  (for [#(i pause) (enumerate PAUSES)]
    (<- ms int (start-latency (+ "probe-" (str i)) pause))
    (:= latencies (+ latencies #(ms))))
  latencies)


(deftest test-a-change-mid-sleep-starts-the-task-before-the-next-tick
  ;; 置いた task は拍の境(tick-seconds = 500 ms)を待たずに起きる: どの位相で置いても開始まで wake-gap-seconds(100 ms)+ 送りの数 ms の内。
  ;; 反例: 眠りが無条件の形では、置いた刻の位相しだいで 300 ms 以上待つ(最大は拍 1 つ近く)。
  (<- latencies tuple (sim-cluster (beacons sim-foundation) (start-latencies)
                                   :workers #((SimWorker :name "w1" :provides NET))))
  (assert (= (len latencies) (len PAUSES)) latencies)
  (assert (all (gfor ms latencies (< ms 150))) latencies))
