;; 進みの印を子の書いた量に結ぶ(run-watched)の、600 秒を越える時間の検 — 模擬の時計と、子の経過を台本で答える handler の上で。
;;
;; 本物の子では 600 秒を待てないので、子(git archive・tar)を台本の子に置き換える: 台本の子は simulated の秒で走り続け、書いた量(file の大きさ)を
;; 経過の秒から答える。確かめる性質:
;;   1 書き続ける子は、1 つの段が停滞の窓(PrepareLimits.stall-seconds = 600)を越えて続いても、どの時点でも「進まない」と判じられない
;;   2 量が増えない子は印を 1 度も触られず、窓の 600 秒で止める時になる(時間だけで触る heartbeat は無い)
(require doeff-hy.macros [deftest defk defhandler <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import doeff [with-handlers])
(import doeff_core_effects.handlers [state])
(import doeff_core_effects.file_effects [PathKind PathStat StatPath WriteText ReadText RemoveTree])
(import doeff_core_effects.process_effects [StartProcess PollProcess ProcessStarted ProcessRunning ProcessExited])
(import doeff_time [SimClock sim-time-handler GetMonotonic])
(import doeff_cluster.worker.core.env_upkeep [PrepareLimits prepare-overdue])
(import doeff_cluster.worker.protocol.env_translation [run-watched CommandResult])

(val CHILD-SECONDS 1500.0)
(val POLL-SECONDS 5.0)
(val PROGRESS "/state/mark.progress")
(val OUT "/state/child.out")


(defclass ChildLog []
  "台本の子の記録: started = 子を起こした秒・touches = 進みの印を触った秒(触った順)。"
  (defn #^ None __init__ [self]
    (setv #^ float self.started 0.0)
    (setv #^ (get tuple #(float ...)) self.touches #())
    None))


(defhandler scripted-child [#^ ChildLog log #^ bool writes]
  ;; 引数に残す理由: 検ごとに別の記録と、子が書き続けるかを持つ。子は CHILD-SECONDS 走り、writes の間だけ書いた量(file の大きさ)が経過に比例して増える。
  (StartProcess [argv stdout-path stderr-path]
    (<- now (GetMonotonic))
    (setv log.started now)
    (resume (ProcessStarted :pid 1)))
  (PollProcess [pid]
    (<- now (GetMonotonic))
    (resume (if (< (- now log.started) CHILD-SECONDS) (ProcessRunning :pid pid) (ProcessExited :pid pid :exit-code 0))))
  (StatPath [path]
    (<- now (GetMonotonic))
    (resume (PathStat :kind PathKind.FILE :real-path path :size (if writes (int (* 1000 (- now log.started))) 0) :modified now)))
  (WriteText [path text mode replace sync]
    (<- now (GetMonotonic))
    (when (= path PROGRESS)
      (setv log.touches (+ log.touches #(now))))
    (resume None))
  (ReadText [path]
    (resume ""))
  (RemoveTree [path]
    (resume None)))


(defk watch-child [log writes]
  {:pre [(: log ChildLog) (: writes bool)] :post [(: % float)] :tags {:context "doeff-cluster-test" :role "program"}}
  "台本の子を run-watched で待ち、終わった秒(模擬の時計)を返すため。"
  (<- result CommandResult (run-watched #("tar" "-xvf" "a.tar") None OUT "/state/child.err" #(OUT) PROGRESS POLL-SECONDS))
  (assert (= result.code 0) result)
  (<- ended float (GetMonotonic))
  ended)


(defk run-child [log writes]
  {:pre [(: log ChildLog) (: writes bool)] :post [(: % float)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "模擬の時計と台本の子の下で watch-child を走らせ、終わった秒を返すため。"
  (<- ended float ((state) ((sim-time-handler :clock (SimClock))
                            (with-handlers [(scripted-child log writes)] (watch-child log writes)))))
  ended)


(deftest test-a-child-that-keeps-writing-is-never-judged-stalled-past-the-window []
  (val log (ChildLog))
  (<- ended float (run-child log True))
  (val limits (PrepareLimits))
  (assert (> (- ended log.started) limits.stall-seconds) "1 つの段が停滞の窓を越えて続く筋でなければ検にならない")
  ;; 起こした秒を最初の印の時刻とし、印の時刻の列に対して、子が走る間の全ての読みの秒で「進まない」と判じられない。
  (val marks (+ #(log.started) log.touches))
  (for [#(a b) (zip marks (+ (cut marks 1 None) #(ended)))]
    (<- overdue bool (prepare-overdue a (- b 0.001) limits))
    (assert (not overdue) (.format "{} 秒から {} 秒まで印が進まない" a b)))
  None)


(deftest test-a-child-that-writes-nothing-is-stopped-at-the-window-and-never-touched []
  (val log (ChildLog))
  (<- ended float (run-child log False))
  (val limits (PrepareLimits))
  (assert (= log.touches #()) "量が増えない子は、時間が経っても印を触られない")
  (<- at-window bool (prepare-overdue log.started (+ log.started limits.stall-seconds) limits))
  (<- past-window bool (prepare-overdue log.started (+ log.started limits.stall-seconds 1.0) limits))
  (assert (not at-window))
  (assert past-window "窓の 600 秒を越えたら止める時になる")
  (assert (> (- ended log.started) limits.stall-seconds))
  None)
