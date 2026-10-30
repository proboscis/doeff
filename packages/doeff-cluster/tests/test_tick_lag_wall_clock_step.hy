;; 拍の遅れの計りが、壁の時計が周の途中で動いても遅い区間を名指す事の検(#3825)。
;;
;; 実例の形: 拍の区間の長さを壁の時計(GetTime)の差で計ると、NTP などで時刻が戻った時に区間が負になり、実際に 6 秒かかった拍が
;; 遅れの行を出さない・いちばん長い区間と別の区間を名指す・進んだ時に速い拍を遅いと名指す。区間の計りは単調な時計(GetMonotonic)で
;; 読み、判断の now だけ壁の時計のまま。
;; 台本: 仮想の時計の下で、宿の代役が ReadDesired の待ちの最中に壁の時計だけを動かす(effect StepWallClock — 手前の handler が GetTime の
;; 答えをずらし、GetMonotonic はそのまま仮想の時計が答える。SetTime では壁も単調も一緒に動いてしまう)。動かす契機を時刻の読みの回数や
;; 経過秒にせず、待ちの最中に置く — 拍が壁の時計を何回読んでも、どの順で読んでも同じ台本で回る。
(require doeff-hy.macros [deftest defk defhandler defeffect <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import datetime [datetime timedelta])
(import doeff [run with-handlers])
(import doeff_core_effects [SlogEffect])
(import doeff_core_effects [state])
(import doeff_core_effects [scheduled])
(import doeff_time [Delay GetTimeEffect SimClock sim-time-handler])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster [WorldView WorkerPolicy ReadDesired ObserveWorld EnvReport PublishStatus]
        doeff_cluster._worker.intent.worker_model [WorkerState DesiredJobs])
(import doeff_cluster._worker.core.program [worker-tick TICK-LAG-LOG])

(val POLICY (WorkerPolicy :stop-grace-ms 1000 :kill-grace-ms 500))


(defrecord StepWorld
  "宿の代役の台本: slows = 待たせる effect の名と秒の組の列・wall-back-seconds = ReadDesired の待ちの最中に壁の時計を戻す秒(負 = 進める・
   0 = 動かさない)。"
  (#^ tuple slows)
  (setv #^ float wall-back-seconds 0.0))


(defrecord LagLine
  "拍の遅れの行 1 つの欄。"
  (#^ int elapsed-ms)
  (#^ str slowest)
  (#^ int slowest-ms))


(defrecord TickSeen
  "拍 1 つの見え方: lags = 拍の遅れの行・start = 拍の頭の壁の時刻(epoch ms)・began = 拍が起きる物の組の基準に返した拍の頭の壁の時刻
   (epoch ms)。"
  (#^ tuple lags)
  (#^ int start)
  (#^ int began))


(defeffect StepWallClock
  "壁の時計だけを seconds 戻す(負 = 進める)— 検だけの操作。GetMonotonic は動かさない。"
  {:fields [(: seconds float)] :answer None :tags {:context "doeff-cluster-test" :role "intent"}})


(defeffect NotedLags
  "ここまでの拍の遅れの行を読む — 検だけの問い。"
  {:fields [] :answer tuple :tags {:context "doeff-cluster-test" :role "intent"}})


(defhandler wall-clock-stepped
  "外の世界の時計と log の手前に立つ代役: StepWallClock の分だけ GetTime の答えをずらす(壁の時計だけ動く — GetMonotonic は通す)。
   拍の遅れの行は覚え、他の行は受け流す。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  (session var back-seconds 0.0)
  (session var lags #())
  (StepWallClock [seconds]
    (:= back-seconds (+ back-seconds seconds))
    (resume None))
  (GetTimeEffect []
    (<- now datetime effect)
    (resume (- now (timedelta :seconds back-seconds))))
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
  "本番の宿のうち coordinator への口・観測の代役: 宣言は空・観測は版も子 process も無い。world.slows の名の effect だけ待たせ、ReadDesired は
   待った後に壁の時計を world.wall-back-seconds 動かす(待ちの最中に時刻が動いた形)。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  ;; 引数に残す理由: 検ごとに待たせる effect と壁の時計の動きの台本を変える(Ask で区別できない)。
  (EnvReport []
    (<- (slowed world "EnvReport"))
    (resume None))
  (ReadDesired [env-report stopping]
    (<- (slowed world "ReadDesired"))
    (<- (StepWallClock world.wall-back-seconds))
    (resume (DesiredJobs #())))
  (ObserveWorld []
    (<- (slowed world "ObserveWorld"))
    (resume (WorldView #() #())))
  (PublishStatus [statuses note]
    (<- (slowed world "PublishStatus"))
    (resume None)))


(defk one-tick []
  {:pre [] :post [(: % TickSeen)] :tags {:context "doeff-cluster-test" :role "program"}}
  "本物の拍を 1 回回し、拍の頭の壁の時刻・拍が返した基準・拍の遅れの行を返すため。"
  (<- start int (now-epoch-ms))
  (<- ticked tuple (worker-tick (WorkerState) POLICY False))
  (<- lags tuple (NotedLags))
  (TickSeen :lags lags :start start :began (get ticked 5)))


(defk seen-on [world]
  {:pre [(: world StepWorld)] :post [(: % TickSeen)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "拍 1 回を宿の代役と仮想の時計の下で回すため。"
  (run (scheduled (with-handlers [(state) (sim-time-handler :clock (SimClock)) wall-clock-stepped (step-world world)]
                    (one-tick)))))


(deftest test-a-slow-tick-is-named-though-the-wall-clock-stepped-back-beyond-it
  ;; ReadDesired が 6 秒かかる間に壁の時計が 10 秒戻る: 拍は実際に 6 秒かかったので、遅れの行は ReadDesired を 6 秒以上と名指す。
  ;; 壁の時計の差で計ると、拍の経過が -4 秒になり行が出ない。
  (<- seen TickSeen (seen-on (StepWorld :slows #(#("ReadDesired" 6.0)) :wall-back-seconds 10.0)))
  (assert (= (len seen.lags) 1) seen)
  (val lag (get seen.lags 0))
  (assert (= lag.slowest "ReadDesired") lag)
  (assert (>= lag.slowest-ms 6000) lag)
  (assert (>= lag.elapsed-ms 6000) lag))


(deftest test-the-longest-span-is-named-though-the-wall-clock-shortened-it
  ;; ReadDesired が 6 秒・PublishStatus が 4 秒かかる間に壁の時計が 4 秒戻る: いちばん長いのは ReadDesired(6 秒)。壁の時計の差で計ると
  ;; ReadDesired が 2 秒に縮み、4 秒の ActionsToPublishStatus を名指す。
  (<- seen TickSeen (seen-on (StepWorld :slows #(#("ReadDesired" 6.0) #("PublishStatus" 4.0)) :wall-back-seconds 4.0)))
  (assert (= (len seen.lags) 1) seen)
  (val lag (get seen.lags 0))
  (assert (= lag.slowest "ReadDesired") lag)
  (assert (>= lag.slowest-ms 6000) lag))


(deftest test-a-quick-tick-is-quiet-though-the-wall-clock-stepped-forward
  ;; 反対向き: 何も待たない拍の最中に壁の時計が 10 秒進む。拍は一瞬なので遅れの行は出ない。壁の時計の差で計ると ReadDesired が 10 秒に
  ;; 伸び、速い拍を遅いと名指す。
  (<- seen TickSeen (seen-on (StepWorld :slows #() :wall-back-seconds -10.0)))
  (assert (= seen.lags #()) seen))


(deftest test-a-tick-without-a-step-is-named-as-before
  ;; 対照: 壁の時計が動かない拍は、今も同じ名指し(この検は直す前も後も緑)。
  (<- seen TickSeen (seen-on (StepWorld :slows #(#("ReadDesired" 6.0)))))
  (assert (= (len seen.lags) 1) seen)
  (val lag (get seen.lags 0))
  (assert (= lag.slowest "ReadDesired") lag)
  (assert (>= lag.slowest-ms 6000) lag))


(deftest test-the-wake-base-is-the-wall-time-of-the-tick-head-when-the-clock-does-not-move
  ;; 拍が起きる物の組の基準に返す拍の頭の壁の時刻は、読まずに判断の now から単調な差を引いて導く。壁の時計が動かない拍では、拍の頭に読んだ
  ;; 壁の時刻と同じ(6 秒待つ拍でも)。
  (<- seen TickSeen (seen-on (StepWorld :slows #(#("ReadDesired" 6.0) #("PublishStatus" 4.0)))))
  (assert (= seen.began seen.start) seen))


(deftest test-the-wake-base-follows-the-wall-clock-frame-of-the-decision-now
  ;; 壁の時計が 10 秒戻った拍: 基準は判断の now と同じ壁の枠で、now から実際の経過(6 秒)だけ前 = 拍の頭に読んだ壁の時刻より 10 秒前。
  ;; 次の拍の判断の now も戻った後の枠で読むので、基準と now の差は実際の経過のまま(壁の枠が混ざらない)。
  (<- seen TickSeen (seen-on (StepWorld :slows #(#("ReadDesired" 6.0)) :wall-back-seconds 10.0)))
  (assert (= seen.began (- seen.start 10000)) seen))
