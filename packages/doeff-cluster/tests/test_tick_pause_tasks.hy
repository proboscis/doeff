;; worker の周の間の待ち(tick_pauses の await-wakes)が周ごとに task を起こさない(card ki-637eea9a117c)— 失敗ケース。
;;
;; 前の形は周ごとに「期限まで眠る task」と「止めの合図を待つ task」を 2 つ起こして取り消した(仕事の無い worker 1 台が 2.5 秒ごとに
;; 払う)。今は期限を時計の答え手の RaceWithin が持ち、止めの合図は止めの答え手が session で 1 つ持つ呼び鈴(StopBell)を競わせる。
;;   1 待つ子の無い静かな周を N 回待っても、待ちが起こす task は 0(N に比例しない)。反例: 前の形は 2N。
;;   2 止めの呼び鈴が期限に何度か負けた後(周をまたいで同じ呼び鈴)でも、待ちの最中に来た止めの合図で待ちがすぐ起きる(期限まで待たない)。
(require doeff-hy.macros [deftest defk defhandler <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import doeff_time [Delay SimClock sim-time-handler])
(import doeff_core_effects.handlers [state])
(import doeff_core_effects.scheduler [Spawn Task Wait])
(import doeff_core_effects.stop_signal_effects [RaiseStop])
(import doeff_core_effects.stop_signal_handlers [scripted-stop-handler])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.shared.intent.due_model [DueAt])
(import doeff_cluster.worker.intent.worker_model [AwaitNextTick WakeSet WorkerPolicy])
(import doeff_cluster.worker.protocol.tick_pauses [tick-pauses])

(val POLICY (WorkerPolicy))
(val BEAT-MS 2500)
(val LONG-MS 60000)


(defclass SpawnLog []
  "周の待ちが起こした task の数(Spawn が count-spawns を通った回数)を数える入れ物。"
  (defn #^ None __init__ [self]
    (setv self.spawns 0)
    None))


(defhandler count-spawns [#^ SpawnLog log]
  ;; 引数に残す理由: 数える入れ物は検ごとに別(検の側が読む)。
  (Spawn []
    (setv log.spawns (+ log.spawns 1))
    (reperform effect)))


(defk quiet-waits [n]
  {:pre [(: n int)] :post [(: % list)] :tags {:context "doeff-cluster-test" :role "program"}}
  "呼び鈴も待つ子も無い周の待ち(期限 BEAT-MS 先)を n 回待ち、1 回ずつ待った仮想の ms を返すため。"
  (var waited [])
  (for [_ (range n)]
    (<- began int (now-epoch-ms))
    (<- (AwaitNextTick POLICY None (WakeSet :due (DueAt :at (+ began BEAT-MS)) :bells #() :exits #()) :stopping False))
    (<- ended int (now-epoch-ms))
    (:= waited (+ waited [(- ended began)])))
  waited)


(defk raise-stop-after [seconds]
  {:pre [(: seconds float)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "seconds 秒の後に止めの合図を立てるため(本番の SIGTERM の代役)。"
  (<- (Delay seconds))
  (<- (RaiseStop "signal 15"))
  None)


(defk stop-mid-wait-after-quiet-ticks [quiet]
  {:pre [(: quiet int)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "静かな周を quiet 回(期限で起きる — 止めの呼び鈴は quiet 度負ける)待った後、LONG-MS 先の期限の待ちの 1 秒目に止めを立て、
   #(静かな周の待ちの ms の列 止めの待ちの ms) を返すため。"
  (<- waited list (quiet-waits quiet))
  (<- began int (now-epoch-ms))
  (<- raiser Task (Spawn (raise-stop-after 1.0)))
  (<- (AwaitNextTick POLICY None (WakeSet :due (DueAt :at (+ began LONG-MS)) :bells #() :exits #()) :stopping False))
  (<- ended int (now-epoch-ms))
  (<- (Wait raiser))
  #(waited (- ended began)))


(deftest test-quiet-waits-spawn-no-task-whatever-their-number
  (for [n [5 20]]
    (val log (SpawnLog))
    (<- waited list ((state) ((sim-time-handler :clock (SimClock))
                               (scripted-stop-handler ((count-spawns log) (tick-pauses (quiet-waits n)))))))
    (assert (= waited (* [BEAT-MS] n)) waited)
    (assert (= log.spawns 0) #(n log.spawns))))


(deftest test-a-stop-wakes-the-wait-after-the-stop-bell-lost-to-deadlines
  (<- seen tuple ((state) ((sim-time-handler :clock (SimClock))
                           (scripted-stop-handler (tick-pauses (stop-mid-wait-after-quiet-ticks 3))))))
  (assert (= (get seen 0) [BEAT-MS BEAT-MS BEAT-MS]) seen)
  (assert (= (get seen 1) 1000) seen))
