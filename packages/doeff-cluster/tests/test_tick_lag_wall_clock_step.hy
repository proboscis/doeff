;; 拍の遅れの計りが、壁の時計が周の途中で戻っても遅い区間を名指す事の検(#3825)。
;;
;; 実例の形: 拍の区間の長さを壁の時計(GetTime)の差で計ると、NTP などで時刻が戻った時に区間が負になり、実際に 6 秒かかった拍が
;; 遅れの行を出さない・いちばん長い区間と別の区間を名指す。区間の計りは単調な時計(GetMonotonic)で読み、判断の now だけ壁の時計のまま。
;; 台本: 仮想の時計の下で、GetTime の答えだけを「最初の読みから n 秒後の読みから b 秒戻した値」にする(GetMonotonic はそのまま仮想の時計が答える —
;; SetTime では壁も単調も一緒に戻ってしまうので、壁の時計だけ戻す手前の handler で作る)。
(require doeff-hy.macros [deftest defk defhandler defeffect <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import datetime [datetime timedelta])
(import doeff [Program run with-handlers])
(import doeff_core_effects.effects [SlogEffect])
(import doeff_core_effects.handlers [state])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_time [Delay GetTimeEffect SimClock sim-time-handler])
(import doeff_cluster.worker.intent.worker_model [WorldView WorkerPolicy WorkerState DesiredJobs ReadDesired ObserveWorld
                                                 EnvReport PublishStatus])
(import doeff_cluster.worker.core.program [worker-tick TICK-LAG-LOG])

(val POLICY (WorkerPolicy :stop-grace-ms 1000 :kill-grace-ms 500))
;; 拍の頭から何秒後の読みから壁の時計が戻るか。ReadDesired の待ち(6 秒)の最中に戻った形。
(val STEP-AFTER-SECONDS 3.0)


(defrecord StepWorld
  "宿の代役の台本: slows = 待たせる effect の名と秒の組の列。"
  (#^ tuple slows))


(defrecord LagLine
  "拍の遅れの行 1 つの欄。"
  (#^ int elapsed-ms)
  (#^ str slowest)
  (#^ int slowest-ms))


(defeffect NotedLags
  "ここまでの拍の遅れの行を読む — 検だけの問い。"
  {:fields [] :answer tuple :tags {:context "doeff-cluster-test" :role "intent"}})


(defhandler wall-clock-stepped [#^ float after-seconds #^ float back-seconds]
  "外の世界の時計と log の手前に立つ代役: 最初の GetTime の読みから after-seconds 後の読みからは、答えを back-seconds 戻す(壁の時計だけ
   戻る — GetMonotonic は通す。契機を読みの回数でなく仮想の時刻にして、拍が壁の時計を何回読んでも同じ台本で回る)。拍の遅れの行は覚え、
   他の行は受け流す。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  ;; 引数に残す理由: 検ごとに壁の時計が戻る刻と幅を変える(Ask で区別できない)。
  (session var origin None)
  (session var lags #())
  (GetTimeEffect []
    (<- now datetime effect)
    (when (is origin None) (:= origin now))
    (val stepped (>= (- now origin) (timedelta :seconds after-seconds)))
    (resume (if stepped (- now (timedelta :seconds back-seconds)) now)))
  (SlogEffect []
    (val fields effect.kwargs)
    (match effect.msg
      m :if (= m TICK-LAG-LOG) (:= lags (+ lags #((LagLine :elapsed-ms (get fields "elapsed_ms") :slowest (get fields "slowest")
                                                           :slowest-ms (get fields "slowest_ms")))))
      _ None)
    (resume None))
  (NotedLags []
    (resume lags)))


(defk slowed [world name]
  {:pre [(: world StepWorld) (: name str)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "台本が name を待たせる時だけ、仮想の時計でその秒数待つため。"
  (for [#(slow-name seconds) world.slows]
    (when (= slow-name name) (<- (Delay seconds))))
  None)


(defhandler step-world [#^ StepWorld world]
  "本番の宿のうち coordinator への口・観測の代役: 宣言は空・観測は版も子 process も無い。world.slows の名の effect だけ待たせる。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  ;; 引数に残す理由: 検ごとに待たせる effect の台本を変える(Ask で区別できない)。
  (EnvReport []
    (<- (slowed world "EnvReport"))
    (resume None))
  (ReadDesired [env-report stopping]
    (<- (slowed world "ReadDesired"))
    (resume (DesiredJobs #())))
  (ObserveWorld []
    (<- (slowed world "ObserveWorld"))
    (resume (WorldView #() #())))
  (PublishStatus [statuses note]
    (<- (slowed world "PublishStatus"))
    (resume None)))


(defk one-tick []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "本物の拍を 1 回回し、拍の遅れの行を返すため。"
  (<- (worker-tick (WorkerState) POLICY False))
  (<- lags tuple (NotedLags))
  lags)


(defk lags-of [world back-seconds]
  {:pre [(: world StepWorld) (: back-seconds float)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "拍 1 回を、拍の頭から STEP-AFTER-SECONDS 後の読みから壁の時計を back-seconds 戻す仮想の世界で回すため。"
  (run (scheduled (with-handlers [(state) (sim-time-handler :clock (SimClock)) (step-world world)
                                  (wall-clock-stepped STEP-AFTER-SECONDS back-seconds)]
                    (one-tick)))))


(deftest test-a-slow-tick-is-named-though-the-wall-clock-stepped-back-beyond-it
  ;; ReadDesired が 6 秒かかる間に壁の時計が 10 秒戻る: 拍は実際に 6 秒かかったので、遅れの行は ReadDesired を 6 秒以上と名指す。
  ;; 壁の時計の差で計ると、拍の経過が -4 秒になり行が出ない。
  (<- lags tuple (lags-of (StepWorld :slows #(#("ReadDesired" 6.0))) 10.0))
  (assert (= (len lags) 1) lags)
  (val lag (get lags 0))
  (assert (= lag.slowest "ReadDesired") lag)
  (assert (>= lag.slowest-ms 6000) lag)
  (assert (>= lag.elapsed-ms 6000) lag))


(deftest test-the-longest-span-is-named-though-the-wall-clock-shortened-it
  ;; ReadDesired が 6 秒・PublishStatus が 4 秒かかる間に壁の時計が 4 秒戻る: いちばん長いのは ReadDesired(6 秒)。壁の時計の差で計ると
  ;; ReadDesired が 2 秒に縮み、4 秒の ActionsToPublishStatus を名指す。
  (<- lags tuple (lags-of (StepWorld :slows #(#("ReadDesired" 6.0) #("PublishStatus" 4.0))) 4.0))
  (assert (= (len lags) 1) lags)
  (val lag (get lags 0))
  (assert (= lag.slowest "ReadDesired") lag)
  (assert (>= lag.slowest-ms 6000) lag))


(deftest test-a-quick-tick-is-quiet-though-the-wall-clock-stepped-forward
  ;; 反対向き: 何も待たない拍の最中に壁の時計が 10 秒進む。拍は一瞬なので遅れの行は出ない。壁の時計の差で計ると ReadDesired が 10 秒に
  ;; 伸び、速い拍を遅いと名指す。
  (<- lags tuple (lags-of (StepWorld :slows #()) -10.0))
  (assert (= lags #()) lags))


(deftest test-a-tick-without-a-step-is-named-as-before
  ;; 対照: 壁の時計が戻らない拍は、今も同じ名指し(この検は直す前も後も緑)。
  (<- lags tuple (lags-of (StepWorld :slows #(#("ReadDesired" 6.0))) 0.0))
  (assert (= (len lags) 1) lags)
  (val lag (get lags 0))
  (assert (= lag.slowest "ReadDesired") lag)
  (assert (>= lag.slowest-ms 6000) lag))
