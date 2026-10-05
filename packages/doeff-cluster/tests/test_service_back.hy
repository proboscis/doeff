;; 依る Service の短い停止を越える戻りの待ち service-back / service-back-within(shared/core/service_back.hy — #3490)。
;;
;; 模擬の cluster(見本の系 beacons・worker 2 台)の上で、本物の答え手(sim の宿の AwaitRunnersChange・AwaitServiceReady)に答えさせる。
;; - Ready の Service には、待たずに戻る(最初の問いは前の版を持たない)。
;; - 続けて問うと、coordinator が古い Ready を見ている間は読み直さず、版の変化を上限 SERVICE-BACK-CHANGE-SECONDS まで待ってから読む —
;;   撃ち直しが回り続けない。上限で返ったら待ち直さず Ready を読む(版が動かない止まりでも戻りを拾う)。
;; - 戻らない Service は、上限 seconds ちょうどで None(見張りは止める)。
;; - 落ちた Service は、別の worker へ置き直されて Ready に戻った拍で戻る。
(require doeff-hy.macros [deftest defk <- val var])
(import gc)
(import pytest)
(import sys)
(import doeff_time [Delay])
(import doeff_core_effects.scheduler [Spawn Wait Cancel Discard])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.shared.core.service_back [SERVICE-BACK-CHANGE-SECONDS service-back service-back-within])
(import doeff_cluster.shared.intent.detached_model [ServiceReady])
(import doeff_cluster.sim.local [sim-cluster SimWorker ReadCoordinator KillWorker])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.sim_programs [beacons NET])

(val WORKERS #((SimWorker :name "w1" :provides NET :task-reserve 0) (SimWorker :name "w2" :provides NET :task-reserve 0)))


(defk ready-word [name]
  {:pre [(: name str)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "program"}}
  "模擬の coordinator の Service name の status.ready の語を読むため(待ちの答えと突き合わせる筋書きの確かめ)。"
  (<- view dict (ReadCoordinator (+ "/resources/Service/" name)))
  (get (get view "status") "ready"))


(defk timed-back [name]
  {:pre [(: name str)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "service-back を 1 度撃ち、#(答え 待った ms) を返すため。"
  (<- started int (now-epoch-ms))
  (<- answer ServiceReady (service-back name))
  (<- at int (now-epoch-ms))
  #(answer (- at started)))


(defk asked-twice []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 落ち着いた後(beacon が Ready)に service-back を続けて 2 度撃つ(呼び手の撃ち直しがまだ届かなかった時の 2 度目の問い)。"
  (<- (Delay 12.0))
  (<- first tuple (timed-back "beacon"))
  (<- second tuple (timed-back "beacon"))
  #(first second))


(deftest test-a-ready-service-comes-back-at-once-and-a-stale-ready-is-not-read-again-before-the-version-moves
  ;; 失敗ケース: 版を待たずに Ready を読む待ち(AwaitServiceReady だけ)は、2 度目も待たずに戻る — 古い Ready の間に撃ち直しが回り続ける。
  (<- seen tuple (sim-cluster (beacons sim-foundation) (asked-twice) :workers WORKERS))
  (val first-answer (get seen 0 0))
  (val first-ms (get seen 0 1))
  (val second-answer (get seen 1 0))
  (val second-ms (get seen 1 1))
  (assert (= first-answer.name "beacon") seen)
  (assert (= first-ms 0) "Ready の Service への最初の問いは待たずに戻る")
  (assert (> second-ms 0) "2 度目の問いは版の変化か上限を待ってから Ready を読む")
  (assert (<= second-ms (int (* 1000 (+ SERVICE-BACK-CHANGE-SECONDS 1.0)))) "上限で返ったら待ち直さない")
  (assert (>= second-answer.revision first-answer.revision) seen))


(defk never-back []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 宣言の無い Service missing の戻りを 25 秒まで待つ(404 = Ready にならない)。答え = #(答え 待った ms)。"
  (<- (Delay 12.0))
  (<- started int (now-epoch-ms))
  (<- answer (| ServiceReady None) (service-back-within "missing" 25.0))
  (<- at int (now-epoch-ms))
  #(answer (- at started)))


(deftest test-a-service-that-never-comes-back-ends-at-the-bound
  ;; 失敗ケース: 期限の無い待ちは戻らない Service で終わらない(筋書きが返らない)・上限より前に None を返す待ちは待った秒が足りない。
  (<- seen tuple (sim-cluster (beacons sim-foundation) (never-back) :workers WORKERS))
  (val answer (get seen 0))
  (val waited (get seen 1))
  (assert (is answer None) seen)
  (assert (= waited 25000) seen))


(defk down-then-back []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: beacon の担い手の worker を落として NotReady になってから、戻りを 120 秒まで待つ。beacon は別の worker へ置き直されて Ready に
   戻る。答え = #(待ち始めた時の語 答え 待った ms 返った時の語)。"
  (<- (Delay 12.0))
  (<- view dict (ReadCoordinator "/state"))
  (val holder (get (get (get view "placements") "beacon") "worker"))
  (<- (KillWorker holder))
  (<- (Delay 8.0))
  (<- down str (ready-word "beacon"))
  (<- started int (now-epoch-ms))
  (<- answer (| ServiceReady None) (service-back-within "beacon" 120.0))
  (<- at int (now-epoch-ms))
  (<- back str (ready-word "beacon"))
  #(down answer (- at started) back))


(deftest test-a-down-service-is-waited-for-until-it-is-ready-again
  ;; 失敗ケース: NotReady の間に戻る待ちは、待った ms が 0 で、返った時の語が NotReady。
  (<- seen tuple (sim-cluster (beacons sim-foundation) (down-then-back) :workers WORKERS))
  (val down (get seen 0))
  (val answer (get seen 1))
  (val waited (get seen 2))
  (val back (get seen 3))
  (assert (= down "NotReady") seen)
  (assert (isinstance answer ServiceReady) seen)
  (assert (> waited 0) seen)
  (assert (< waited 120000) seen)
  (assert (= back "Ready") seen))


;; --- 待っている task を止める(#3557)— 取り消しは片付けまで走り、殺された task は「generator ignored GeneratorExit」を名乗らない ---------

(defk stopped-while-waiting [kill]
  {:pre [(: kill bool)] :post [(: % (| str None))] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 宣言の無い Service missing の戻りを待つ task を立て、待ちに入った後に kill なら Discard・さもなくば Cancel して、その task の
   Wait で上がった例外の型の名を返す。"
  (<- (Delay 12.0))
  (<- waiter (Spawn (service-back-within "missing" 25.0)))
  (<- (Delay 5.0))
  (if kill (<- (Discard waiter)) (<- (Cancel waiter)))
  (var ended None)
  (try
    (<- (Wait waiter))
    (except [error Exception]
      (:= ended (. (type error) __name__))))
  ended)


(defk unraisable-while [kill monkeypatch]
  {:pre [(: kill bool) (: monkeypatch pytest.MonkeyPatch)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "止める筋書きを模擬の cluster で回し、#(Wait の例外の型の名 CPython が名乗った unraisable の文の列)を返す(走行の終わりに GC を回して
   捨てた generator の閉じを拾う)。"
  (setv seen [])
  (monkeypatch.setattr sys "unraisablehook" (fn [unraisable] (.append seen (.format "{} — {}" unraisable.exc-value unraisable.err-msg))))
  (<- ended (| str None) (sim-cluster (beacons sim-foundation) (stopped-while-waiting kill) :workers WORKERS))
  (gc.collect)
  #(ended (tuple seen)))


(deftest test-a-cancelled-wait-ends-cancelled-without-an-unraisable [monkeypatch]
  ;; 取り消し(Cancel)は TaskCancelledError を投げ込み、見張りを止める片付けまで走る — CPython の名乗りは 0。
  (<- seen tuple (unraisable-while False monkeypatch))
  (assert (= seen #("TaskCancelledError" #())) seen))


(deftest test-a-discarded-wait-closes-without-an-unraisable [monkeypatch]
  ;; 失敗ケース: 見張りを止める効果を finally に置くと、捨てた(Discard)待ちの generator を CPython が閉じる時に finally の中で yield し、
  ;; 「generator ignored GeneratorExit」を unraisable として名乗る(#3557 の前の形)。
  (<- seen tuple (unraisable-while True monkeypatch))
  (assert (= seen #("TaskCancelledError" #())) seen))
