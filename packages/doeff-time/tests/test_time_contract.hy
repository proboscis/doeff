;;; Contract tests for doeff-time -- the real clocks (async-time-handler, sync-time-handler) and the virtual clock
;;; (sim-time-handler), which answer the same effects, go through the same deftest. The interpreters are assembled in time_contract_clocks.hy.
;;;
;;; Time spans are judged by the clock traits (ClockTraits): the virtual clock is exact; real clocks may wake up early by up to early seconds and late by up to late seconds.
;;; SetTime is an effect of the virtual clock only, so it is outside the contract (test_sim_time.py). SpendCpu (use CPU seconds without yielding the turn) is part of the contract of all 3 clocks. That the real clocks read the wall clock is in test_get_time.py.
(require doeff-hy.macros [defk deftest <- val])
(import datetime [datetime timedelta])
(import dataclasses [dataclass])
(import doeff_core_effects [Cancel CompletePromise CreatePromise Promise Spawn Task Wait])
(import doeff_time [Delay GetMonotonic GetTime RaceOutcome RaceWithin ScheduleAt SpendCpu WaitUntil])
(import time_contract_clocks [ClockTraits ClockUnderTest OuterProbe])

;; Tolerance for "exact" on the virtual clock -- GetMonotonic is the POSIX seconds (float) of the virtual time, and the float step at 2024-scale seconds is finer than 1e-6.
(val EXACT-TOLERANCE 1e-6)
(val SHORT 0.05)


(defclass ScheduledFailure [Exception])


(defk spent-matches [clock wanted spent]
  {:pre [(: clock ClockTraits) (: wanted float) (: spent float)] :post [(: % bool)]
   :tags {:context "doeff-time-test" :role "judgment"}}
  "Whether taking spent seconds for a wait fits, under the clock traits, as a wait of wanted seconds."
  (if clock.exact
      (<= (abs (- spent wanted)) EXACT-TOLERANCE)
      (<= (- wanted clock.early) spent (+ wanted clock.late))))


(defk probed-answer []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-time-test" :role "program"}}
  "Program run via ScheduleAt: has the handler outside the clock handler (OuterProbe) answer it, and returns its arrival number."
  (<- arrival int (OuterProbe))
  #("ran" arrival))


(defk failing-later []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-time-test" :role "program"}}
  "Program run via ScheduleAt that fails (checks whether the failure reaches Wait)."
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
  (assert (is-not t0.tzinfo None) "GetTime returns a timezone-aware datetime")
  (assert (<= t0 t1 t2) (.format "GetTime went backwards: {} {} {}" t0 t1 t2))
  (assert (<= m0 m1 m2) (.format "GetMonotonic went backwards: {} {} {}" m0 m1 m2)))


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
  (assert by-monotonic (.format "{}: difference of GetMonotonic after Delay {}: {}" clock.name SHORT (- m1 m0)))
  (assert by-time (.format "{}: difference of GetTime after Delay {}: {}" clock.name SHORT (- t1 t0))))


(deftest test-wait-until-reaches-its-target
  {:interpreters ["async" "sync" "sim"]}
  (<- clock (ClockUnderTest))
  (<- t0 datetime (GetTime))
  (val target (+ t0 (timedelta :seconds SHORT)))
  (<- (WaitUntil target))
  (<- t1 datetime (GetTime))
  (<- reached bool (spent-matches clock SHORT (.total-seconds (- t1 t0))))
  (assert reached (.format "{}: time after WaitUntil {}: {}" clock.name target t1)))


(deftest test-wait-until-in-the-past-returns-at-once
  {:interpreters ["async" "sync" "sim"]}
  (<- clock (ClockUnderTest))
  (<- t0 datetime (GetTime))
  (<- m0 float (GetMonotonic))
  (<- (WaitUntil (- t0 (timedelta :seconds 10))))
  (<- t1 datetime (GetTime))
  (<- m1 float (GetMonotonic))
  (<- at-once bool (spent-matches clock 0.0 (- m1 m0)))
  (assert at-once (.format "{}: WaitUntil in the past took {} seconds" clock.name (- m1 m0)))
  (assert (<= t0 t1) "the clock went back on WaitUntil in the past"))


(deftest test-other-effects-pass-through
  {:interpreters ["async" "sync" "sim"]}
  (<- arrival int (OuterProbe))
  (assert (= arrival 1) "the clock handler passes effects that are not its own to the outer handler"))


(deftest test-schedule-at-runs-after-its-time-and-wait-answers
  {:interpreters ["async" "sync" "sim"]}
  (<- clock (ClockUnderTest))
  (<- t0 datetime (GetTime))
  (<- task (ScheduleAt (+ t0 (timedelta :seconds SHORT)) (probed-answer)))
  (assert (isinstance task Task) "ScheduleAt returns the Task it spawned")
  (<- answer (Wait task))
  (<- t1 datetime (GetTime))
  (assert (= answer #("ran" 1)) (.format "{}: answer of Wait {} (the Program answer arrives as is, and the outer handler is visible)" clock.name answer))
  (<- after bool (spent-matches clock SHORT (.total-seconds (- t1 t0))))
  (assert after (.format "{}: Task at {} seconds after the time finished in {}" clock.name SHORT (- t1 t0))))


(deftest test-schedule-at-runs-in-time-order
  {:interpreters ["async" "sync" "sim"]}
  (<- t0 datetime (GetTime))
  (<- later (ScheduleAt (+ t0 (timedelta :seconds 0.06)) (probed-answer)))
  (<- sooner (ScheduleAt (+ t0 (timedelta :seconds 0.02)) (probed-answer)))
  (<- later-answer (Wait later))
  (<- sooner-answer (Wait sooner))
  (assert (= #(sooner-answer later-answer) #(#("ran" 1) #("ran" 2)))
          (.format "runs in time order, not registration order: earlier time {}, later time {}" sooner-answer later-answer)))


(deftest test-schedule-at-in-the-past-runs-at-once
  {:interpreters ["async" "sync" "sim"]}
  (<- clock (ClockUnderTest))
  (<- t0 datetime (GetTime))
  (<- m0 float (GetMonotonic))
  (<- task (ScheduleAt (- t0 (timedelta :seconds 10)) (probed-answer)))
  (<- answer (Wait task))
  (<- m1 float (GetMonotonic))
  (<- at-once bool (spent-matches clock 0.0 (- m1 m0)))
  (assert (= answer #("ran" 1)) (.format "answer of a Task at a past time {}" answer))
  (assert at-once (.format "{}: Task at a past time took {} seconds" clock.name (- m1 m0))))


(deftest test-schedule-at-failure-reaches-wait
  {:interpreters ["async" "sync" "sim"]}
  (<- t0 datetime (GetTime))
  (<- task (ScheduleAt (+ t0 (timedelta :seconds 0.01)) (failing-later)))
  (var caught None)
  (try
    (<- (Wait task))
    (except [failure ScheduledFailure]
      (:= caught (str failure))))
  (assert (= caught "scheduled program failed") "a failure of a ScheduleAt Program is raised at Wait"))


(defk completing-later [promise seconds]
  {:pre [(: promise Promise) (: seconds float)] :post [(: % None)] :tags {:context "doeff-time-test" :role "program"}}
  "Program that fulfills a promise with None after seconds seconds (test for RaceWithin -- checks a None value is not mistaken for the deadline)."
  (<- (Delay seconds))
  (<- (CompletePromise promise None))
  None)


(deftest test-race-within-answers-the-first-future-even-a-none
  {:interpreters ["async" "sync" "sim"]}
  (<- clock (ClockUnderTest))
  (<- m0 float (GetMonotonic))
  (<- never Promise (CreatePromise))
  (<- soon Promise (CreatePromise))
  (<- completer Task (Spawn (completing-later soon SHORT)))
  (<- outcome RaceOutcome (RaceWithin #(never.future soon.future) (* 10 SHORT)))
  (<- (Wait completer))
  (<- m1 float (GetMonotonic))
  (assert (= outcome (RaceOutcome :value None :timed-out False))
          (.format "{}: if the Future fulfilled with None comes first, timed-out is false (answer {})" clock.name outcome))
  (<- in-time bool (spent-matches clock SHORT (- m1 m0)))
  (assert in-time (.format "{}: waiting for a Future fulfilled in {} seconds took {} seconds" clock.name SHORT (- m1 m0))))


(deftest test-race-within-times-out-at-its-deadline
  {:interpreters ["async" "sync" "sim"]}
  (<- clock (ClockUnderTest))
  (<- m0 float (GetMonotonic))
  (<- never Promise (CreatePromise))
  (<- also-never Promise (CreatePromise))
  (<- outcome RaceOutcome (RaceWithin #(never.future also-never.future) SHORT))
  (<- m1 float (GetMonotonic))
  (assert (= outcome (RaceOutcome :value None :timed-out True)) (.format "{}: deadline comes first (answer {})" clock.name outcome))
  (<- at-deadline bool (spent-matches clock SHORT (- m1 m0)))
  (assert at-deadline (.format "{}: waiting with a {} second deadline took {} seconds" clock.name SHORT (- m1 m0))))


;; Subject to DOEFF119 but not made a defrecord: a mutable container written by the ticking task (ticking) and read by the test.
(defclass [(dataclass)] TickCount []
  "Number of times the ticking task ran."
  (setv #^ int ticks 0))


(defk ticking [count every]
  {:pre [(: count TickCount) (: every float)] :post [(: % None)] :tags {:context "doeff-time-test" :role "program"}}
  "Program that keeps incrementing count every every seconds (to see whether other tasks of the same scheduler run -- the test stops it with Cancel)."
  (while True
    (setv count.ticks (+ count.ticks 1))
    (<- (Delay every)))
  None)


(val CPU-SPAN 0.15)


(deftest test-spend-cpu-advances-by-its-span
  {:interpreters ["async" "sync" "sim"]}
  (<- clock (ClockUnderTest))
  (<- t0 datetime (GetTime))
  (<- m0 float (GetMonotonic))
  (<- (SpendCpu CPU-SPAN))
  (<- t1 datetime (GetTime))
  (<- m1 float (GetMonotonic))
  (<- by-monotonic bool (spent-matches clock CPU-SPAN (- m1 m0)))
  (<- by-time bool (spent-matches clock CPU-SPAN (.total-seconds (- t1 t0))))
  (assert by-monotonic (.format "{}: difference of GetMonotonic after SpendCpu {}: {}" clock.name CPU-SPAN (- m1 m0)))
  (assert by-time (.format "{}: difference of GetTime after SpendCpu {}: {}" clock.name CPU-SPAN (- t1 t0))))


;; The sync clock is excluded: Delay is also a sleep that blocks the thread, so no other task runs during any wait (there is no place for the ticking task to run).
(deftest test-spend-cpu-runs-no-other-task-while-the-clock-moves
  {:interpreters ["async" "sim"]}
  (<- clock (ClockUnderTest))
  (val count (TickCount))
  (<- ticker Task (Spawn (ticking count 0.01) :daemon True))
  (<- (Delay 0.035))
  (val before count.ticks)
  (<- m0 float (GetMonotonic))
  (<- (SpendCpu CPU-SPAN))
  (<- m1 float (GetMonotonic))
  (val during (- count.ticks before))
  (<- (Delay 0.02))
  (val after (- count.ticks before))
  (<- (Cancel ticker))
  (assert (>= before 1) (.format "{}: the ticking task did not run before using CPU ({} times)" clock.name before))
  (assert (= during 0) (.format "{}: while using CPU for {} seconds, another task of the same scheduler ran {} times" clock.name CPU-SPAN during))
  (<- spent bool (spent-matches clock CPU-SPAN (- m1 m0)))
  (assert spent (.format "{}: using CPU for {} seconds advanced the clock by {} seconds" clock.name CPU-SPAN (- m1 m0)))
  (assert (>= after 1) (.format "{}: no other task ran even after using CPU ({} times)" clock.name after)))
