;;; doeff-time の契約テスト — 同じ effect に答える本物の時計(async-time-handler・sync-time-handler)と仮想の時計
;;; (sim-time-handler)が、同じ deftest を通る(agora-redesign #1159)。解釈器の組み立ては time_contract_clocks.hy。
;;;
;;; 時間の幅は時計の性質(ClockTraits)で見る: 仮想の時計はちょうど・本物の時計は早起きを early 秒・遅れを late 秒まで許す。
;;; SetTime は仮想の時計だけの effect なので契約の外(test_sim_time.py)。本物の時計が壁時計を読むことは test_get_time.py。
(require doeff-hy.macros [defk deftest <- val])
(import datetime [datetime timedelta])
(import doeff_core_effects.scheduler [Task Wait])
(import doeff_time [Delay GetMonotonic GetTime ScheduleAt WaitUntil])
(import time_contract_clocks [ClockTraits ClockUnderTest OuterProbe])

;; 仮想の時計の「ちょうど」の許し — GetMonotonic は仮想の時刻の POSIX の秒(float)で、2024 年の秒数の刻みは 1e-6 より細かい。
(val EXACT-TOLERANCE 1e-6)
(val SHORT 0.05)


(defclass ScheduledFailure [Exception])


(defk spent-matches [clock wanted spent]
  {:pre [(: clock ClockTraits) (: wanted float) (: spent float)] :post [(: % bool)]
   :tags {:context "doeff-time-test" :role "judgment"}}
  "待ちに spent 秒かかったことが、wanted 秒の待ちとして時計の性質に合うか。"
  (if clock.exact
      (<= (abs (- spent wanted)) EXACT-TOLERANCE)
      (<= (- wanted clock.early) spent (+ wanted clock.late))))


(defk probed-answer []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-time-test" :role "program"}}
  "ScheduleAt で走らせる Program: 時計の handler の外側の handler(OuterProbe)に答えてもらい、その到着の番号を返す。"
  (<- arrival int (OuterProbe))
  #("ran" arrival))


(defk failing-later []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-time-test" :role "program"}}
  "ScheduleAt で走らせて失敗する Program(失敗が Wait に届くかの検)。"
  (<- (OuterProbe))
  (raise (ScheduledFailure "scheduled program failed")))


(deftest test-readings-never-go-back
  {:interpreters ["async" "sync" "sim"]}
  (<- t0 datetime (GetTime))
  (<- m0 float (GetMonotonic))
  (<- (Delay 0.02))
  (<- t1 datetime (GetTime))
  (<- m1 float (GetMonotonic))
  (<- (Delay 0.0))
  (<- t2 datetime (GetTime))
  (<- m2 float (GetMonotonic))
  (assert (is-not t0.tzinfo None) "GetTime は時差つきの datetime を返す")
  (assert (<= t0 t1 t2) (.format "GetTime が後退した: {} {} {}" t0 t1 t2))
  (assert (<= m0 m1 m2) (.format "GetMonotonic が後退した: {} {} {}" m0 m1 m2)))


(deftest test-delay-advances-by-its-span
  {:interpreters ["async" "sync" "sim"]}
  (<- clock (ClockUnderTest))
  (<- t0 datetime (GetTime))
  (<- m0 float (GetMonotonic))
  (<- (Delay SHORT))
  (<- t1 datetime (GetTime))
  (<- m1 float (GetMonotonic))
  (<- by-monotonic bool (spent-matches clock SHORT (- m1 m0)))
  (<- by-time bool (spent-matches clock SHORT (.total-seconds (- t1 t0))))
  (assert by-monotonic (.format "{}: Delay {} の後の GetMonotonic の差 {}" clock.name SHORT (- m1 m0)))
  (assert by-time (.format "{}: Delay {} の後の GetTime の差 {}" clock.name SHORT (- t1 t0))))


(deftest test-wait-until-reaches-its-target
  {:interpreters ["async" "sync" "sim"]}
  (<- clock (ClockUnderTest))
  (<- t0 datetime (GetTime))
  (val target (+ t0 (timedelta :seconds SHORT)))
  (<- (WaitUntil target))
  (<- t1 datetime (GetTime))
  (<- reached bool (spent-matches clock SHORT (.total-seconds (- t1 t0))))
  (assert reached (.format "{}: WaitUntil {} の後の時刻 {}" clock.name target t1)))


(deftest test-wait-until-in-the-past-returns-at-once
  {:interpreters ["async" "sync" "sim"]}
  (<- clock (ClockUnderTest))
  (<- t0 datetime (GetTime))
  (<- m0 float (GetMonotonic))
  (<- (WaitUntil (- t0 (timedelta :seconds 10))))
  (<- t1 datetime (GetTime))
  (<- m1 float (GetMonotonic))
  (<- at-once bool (spent-matches clock 0.0 (- m1 m0)))
  (assert at-once (.format "{}: 過去の WaitUntil に {} 秒かかった" clock.name (- m1 m0)))
  (assert (<= t0 t1) "過去の WaitUntil で時計が戻った"))


(deftest test-other-effects-pass-through
  {:interpreters ["async" "sync" "sim"]}
  (<- arrival int (OuterProbe))
  (assert (= arrival 1) "時計の handler は自分の effect でない物を外側の handler へ渡す"))


(deftest test-schedule-at-runs-after-its-time-and-wait-answers
  {:interpreters ["async" "sync" "sim"]}
  (<- clock (ClockUnderTest))
  (<- t0 datetime (GetTime))
  (<- task (ScheduleAt (+ t0 (timedelta :seconds SHORT)) (probed-answer)))
  (assert (isinstance task Task) "ScheduleAt は Spawn した Task を返す")
  (<- answer (Wait task))
  (<- t1 datetime (GetTime))
  (assert (= answer #("ran" 1)) (.format "{}: Wait の答え {}(Program の答えがそのまま届き、外側の handler が見える)" clock.name answer))
  (<- after bool (spent-matches clock SHORT (.total-seconds (- t1 t0))))
  (assert after (.format "{}: 刻の {} 秒後の Task が {} で終わった" clock.name SHORT (- t1 t0))))


(deftest test-schedule-at-runs-in-time-order
  {:interpreters ["async" "sync" "sim"]}
  (<- t0 datetime (GetTime))
  (<- later (ScheduleAt (+ t0 (timedelta :seconds 0.06)) (probed-answer)))
  (<- sooner (ScheduleAt (+ t0 (timedelta :seconds 0.02)) (probed-answer)))
  (<- later-answer (Wait later))
  (<- sooner-answer (Wait sooner))
  (assert (= #(sooner-answer later-answer) #(#("ran" 1) #("ran" 2)))
          (.format "登録の順ではなく刻の順に走る: 早い刻 {}・遅い刻 {}" sooner-answer later-answer)))


(deftest test-schedule-at-in-the-past-runs-at-once
  {:interpreters ["async" "sync" "sim"]}
  (<- clock (ClockUnderTest))
  (<- t0 datetime (GetTime))
  (<- m0 float (GetMonotonic))
  (<- task (ScheduleAt (- t0 (timedelta :seconds 10)) (probed-answer)))
  (<- answer (Wait task))
  (<- m1 float (GetMonotonic))
  (<- at-once bool (spent-matches clock 0.0 (- m1 m0)))
  (assert (= answer #("ran" 1)) (.format "過去の刻の Task の答え {}" answer))
  (assert at-once (.format "{}: 過去の刻の Task に {} 秒かかった" clock.name (- m1 m0))))


(deftest test-schedule-at-failure-reaches-wait
  {:interpreters ["async" "sync" "sim"]}
  (<- t0 datetime (GetTime))
  (<- task (ScheduleAt (+ t0 (timedelta :seconds 0.01)) (failing-later)))
  (var caught None)
  (try
    (<- (Wait task))
    (except [failure ScheduledFailure]
      (:= caught (str failure))))
  (assert (= caught "scheduled program failed") "ScheduleAt の Program の失敗は Wait で上がる"))
