;;; 歩の積算の答え手 step-tally-handler(agora-redesign #3855)— OpenStepTally・CloseStepTally に、Python の scheduler の測りの口
;;; (set_scheduler_trace)が出す task-leave の出来事で答える。
;;;
;;; 何のためか: 呼び手(agora の CLI ホストなど)が、ある区間(1 つのターンを運ぶ間)に scheduler が走らせた歩の数・壁と CPU の時間と
;;; 最長の歩を、測りの口に自分で触らずに効果で数えるため。時計を読むのは scheduler の口の側だけ(task-leave の step_ns・step_cpu_ns)。
;;;
;;;   置き場  process に 1 つの「key → 窓」の表(別の run・別の thread の答え手も同じ表を使う — 先例 = process_meter.hy の置き場)。
;;;           窓は開けた scheduler の thread の名と開けた時刻を持ち、その thread の歩のうち、始まりが開けた時刻より後の物だけを足す。
;;;   sink    最初の窓を開けた時に測りの口へ据え、最後の窓を閉じた時に外す — 窓が 1 つも無い間は口に sink が無く、scheduler は出来事を
;;;           作らない。別の sink が据えてある時に窓を開けると RuntimeError で断る(他の測りの sink を黙って差し替えない)。
;;;   費用    窓が開いている間だけ、scheduler の出来事ごとに sink が 1 回呼ばれ、task-leave ごとに lock を取って窓を差し替える。
;;;
;;; 窓を閉じずに run が終わると、その窓と sink は process に残る(次に同じ key を開けると断る)。Rust の scheduler は出来事を出さないので、
;;; 窓を開けている間に Rust の scheduler を組むと scheduled が断る。
(require doeff-hy.macros [defhandler defk deff <- val])
(val MODULE-TAGS {:context "step-tally" :role "foundation"})
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import threading)
(import time)
(import doeff_core_effects.scheduler [scheduler-trace-sink set-scheduler-trace])
(import doeff_core_effects.step_tally_effects [CloseStepTally EMPTY-STEP-TALLY OpenStepTally StepTally])


(defrecord StepWindow
  "開いている窓 1 つ: thread = 開けた scheduler の thread の名・opened-ns = 開けた時刻(perf_counter_ns)・tally = ここまでの積算。"
  {:tags {:context "step-tally" :role "foundation"}}
  (#^ str thread)
  (#^ int opened-ns)
  (#^ StepTally tally))


;; process に 1 つ: key → 開いている窓。書きは LOCK の中で、窓を差し替える。
(setv #^ (get dict #(str StepWindow)) WINDOWS {})
(setv #^ threading.Lock LOCK (threading.Lock))


(deff step-sink [event]  ; defk にできない: scheduler の測りの口が VM の外から出来事ごとに素の関数として呼ぶ sink
  {:pre [(: event dict)] :post [(: % None)] :tags {:context "step-tally" :role "foundation"}}
  "task-leave の出来事 1 つを、その歩を含む開いている窓 全部に足すため(歩の始まりが窓を開けた時刻より後で、同じ thread の窓だけ)。
   最長の歩は壁の時間で比べ、同じ長さなら先の歩を残す。"
  (setv step-ns (.get event "step_ns"))
  (when (and WINDOWS (= (get event "event") "task-leave") (is-not step-ns None))
    (setv thread (get event "thread"))
    (setv started-ns (- (get event "ns") step-ns))
    (setv step-cpu-ns (get event "step_cpu_ns"))
    (setv site (.get event "site"))
    (setv effect (.get event "effect"))
    (setv ready (.get event "ready" 0))
    (with [LOCK]
      (for [#(key window) (tuple (.items WINDOWS))]
        (when (and (= window.thread thread) (>= started-ns window.opened-ns))
          (setv tally window.tally)
          (setv longer (> step-ns tally.longest-wall-ns))
          (setv (get WINDOWS key)
                (StepWindow :thread thread :opened-ns window.opened-ns
                            :tally (StepTally :steps (+ tally.steps 1)
                                              :wall-ns (+ tally.wall-ns step-ns)
                                              :cpu-ns (+ tally.cpu-ns step-cpu-ns)
                                              :longest-wall-ns (if longer step-ns tally.longest-wall-ns)
                                              :longest-cpu-ns (if longer step-cpu-ns tally.longest-cpu-ns)
                                              :longest-site (if longer site tally.longest-site)
                                              :longest-effect (if longer effect tally.longest-effect)
                                              :ready ready)))))))
  None)


(defk opened [key thread opened-ns]
  {:pre [(: key str) (: thread str) (: opened-ns int)] :post [(: % None)] :tags {:context "step-tally" :role "foundation"}}
  "窓 key を開くため: 開いている key は断り、最初の窓なら測りの口へ sink を据える(別の sink が据えてあれば断る)。"
  (with [LOCK]
    (when (in key WINDOWS)
      (raise (ValueError (.format "歩の積算の窓 {!r} は開いている(閉じてから開け直す)" key))))
    (when (not WINDOWS)
      (val current (scheduler-trace-sink))
      (when (and (is-not current None) (is-not current step-sink))
        (raise (RuntimeError (.format "scheduler の測りの口に別の sink が据えてある: {!r} — 歩の積算の窓を開けられない" current))))
      (set-scheduler-trace step-sink))
    (setv (get WINDOWS key) (StepWindow :thread thread :opened-ns opened-ns :tally EMPTY-STEP-TALLY)))
  None)


(defk closed [key]
  {:pre [(: key str)] :post [(: % (| StepTally None))] :tags {:context "step-tally" :role "foundation"}}
  "窓 key を閉じて積算を返すため(開いていなければ None)。最後の窓なら測りの口から sink を外す。"
  (with [LOCK]
    (val window (.pop WINDOWS key None))
    (when (and (not WINDOWS) (is (scheduler-trace-sink) step-sink))
      (set-scheduler-trace None)))
  (if (is window None) None window.tally))


(defhandler step-tally-handler
  "OpenStepTally・CloseStepTally に、process に 1 つの窓の表と scheduler の測りの口で答える(頭の註)。Python の scheduler の内側に置く。"
  (OpenStepTally [key]
    (<- (opened key (. (threading.current-thread) name) (time.perf-counter-ns)))
    (resume None))
  (CloseStepTally [key]
    (<- tally (closed key))
    (resume tally)))
