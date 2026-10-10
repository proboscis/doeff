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
;;;           task の spawn の場所ごとの和(SiteTally)は、窓ごとの表の 1 行を歩ごとに差し替え、閉じた時に 1 度だけ並べる。
;;;
;;; 窓を閉じずに run が終わると、その窓と sink は process に残る(次に同じ key を開けると断る)。Rust の scheduler は出来事を出さないので、
;;; 窓を開けている間に Rust の scheduler を組むと scheduled が断る。
;;;
;;; task の積算の窓(OpenTaskTally・ReadTaskTally・CloseTaskTally — agora-redesign #4188): 同じ sink が spawned と task-leave の出来事で、
;;; 窓ごとの (run, tid) → 行(TaskTally)の表を進める。歩の窓と task の窓は sink を分け合い、どちらかの窓が 1 つでも開いている間は
;;; sink を据えたまま(据える・外すの判じは sink-claimed・sink-released の 1 か所)。
(require doeff-hy.macros [defhandler defk deff <- val])
(val MODULE-TAGS {:context "step-tally" :role "foundation"})
(require doeff-hy.record [defrecord])
(import dataclasses)
(import dataclasses [dataclass])
(import threading)
(import time)
(import doeff_core_effects.scheduler [scheduler-trace-sink set-scheduler-trace])
(import doeff_core_effects.step_tally_effects [CloseStepTally EMPTY-STEP-TALLY OpenStepTally SiteTally StepTally
                                               CloseTaskTally OpenTaskTally ReadTaskTally TaskTally])


(defrecord StepWindow
  "開いている窓 1 つ: thread = 開けた scheduler の thread の名・opened-ns = 開けた時刻(perf_counter_ns)・tally = ここまでの積算・
   sites = task の spawn の場所 → ここまでの和(答え手の中だけの表 — 歩ごとに 1 行を差し替え、窓を閉じた時に CPU の多い順の tuple で
   答える。TaskWindow の rows と同じ形)。"
  {:tags {:context "step-tally" :role "foundation"}}
  (#^ str thread)
  (#^ int opened-ns)
  (#^ StepTally tally)
  (#^ (get dict #((| str None) SiteTally)) sites))


(defrecord TaskWindow
  "開いている task の積算の窓 1 つ: opened-ns = 開けた時刻(perf_counter_ns)・rows = (run, tid) → ここまでの行。
   rows は答え手の中だけの表(出来事ごとに 1 行を差し替える — 窓ごとの写しを毎回作り直さないため。外へは tuple で答える)。"
  {:tags {:context "step-tally" :role "foundation"}}
  (#^ int opened-ns)
  (#^ (get dict #(tuple TaskTally)) rows))


;; process に 1 つ: key → 開いている窓。書きは LOCK の中で、窓を差し替える。
(setv #^ (get dict #(str StepWindow)) WINDOWS {})
;; process に 1 つ: key → 開いている task の積算の窓。書きは LOCK の中で、窓の rows の行を差し替える。
(setv #^ (get dict #(str TaskWindow)) TASK-WINDOWS {})
(setv #^ threading.Lock LOCK (threading.Lock))


(deff empty-task-row [run tid parent]  ; defk にできない: VM の外から呼ばれる sink(step-sink)の note-spawned・note-task-step が使う
  {:pre [(: run int) (: tid (| int None)) (: parent (| int None))] :post [(: % TaskTally)]
   :tags {:context "step-tally" :role "foundation"}}
  "まだ歩を数えていない task の行。"
  (TaskTally :run run :tid tid :parent parent :steps 0 :wall-ns 0 :cpu-ns 0 :vm-steps 0 :handler-calls 0))


(deff note-spawned [event]  ; defk にできない: scheduler の測りの口が VM の外から呼ぶ sink(step-sink)の中で、LOCK を持って呼ぶ
  {:pre [(: event dict)] :post [(: % None)] :tags {:context "step-tally" :role "foundation"}}
  "spawned の出来事 1 つの親を、開けた時刻より後に生まれた子として、開いている task の窓 全部の表に書くため。"
  (setv run (get event "run"))
  (setv tid (get event "tid"))
  (setv parent (get event "parent"))
  (for [window (.values TASK-WINDOWS)]
    (when (>= (get event "ns") window.opened-ns)
      (setv row (.get window.rows #(run tid) (empty-task-row run tid None)))
      (setv (get window.rows #(run tid)) (dataclasses.replace row :parent parent))))
  None)


(deff note-task-step [event]  ; defk にできない: scheduler の測りの口が VM の外から呼ぶ sink(step-sink)の中で、LOCK を持って呼ぶ
  {:pre [(: event dict)] :post [(: % None)] :tags {:context "step-tally" :role "foundation"}}
  "task-leave の出来事 1 つの歩を、歩の始まりが開けた時刻より後の task の窓 全部で、その task の行に足すため。"
  (setv run (get event "run"))
  (setv tid (get event "tid"))
  (setv started-ns (- (get event "ns") (get event "step_ns")))
  (for [window (.values TASK-WINDOWS)]
    (when (>= started-ns window.opened-ns)
      (setv row (.get window.rows #(run tid) (empty-task-row run tid None)))
      (setv (get window.rows #(run tid))
            (dataclasses.replace row
                                 :steps (+ row.steps 1)
                                 :wall-ns (+ row.wall-ns (get event "step_ns"))
                                 :cpu-ns (+ row.cpu-ns (get event "step_cpu_ns"))
                                 :vm-steps (+ row.vm-steps (get event "step_vm_steps"))
                                 :handler-calls (+ row.handler-calls (get event "step_handler_calls"))))))
  None)


(deff task-table [window]  ; defk にできない: task-read・task-closed が LOCK を持ったまま表を写す — 効果を出す間に LOCK を持ち越さない
  {:pre [(: window TaskWindow)] :post [(: % tuple)] :tags {:context "step-tally" :role "foundation"}}
  "窓の表を (run, tid) の順の tuple で答えるため(根の task の tid None は先頭)。"
  (tuple (sorted (.values window.rows) :key (fn [row] #(row.run (if (is row.tid None) -1 row.tid))))))


(deff sink-claimed []  ; defk にできない: opened・task-opened が LOCK を持ったまま窓を足す前に呼ぶ — 効果を出す間に LOCK を持ち越さない
  {:pre [] :post [(: % None)] :tags {:context "step-tally" :role "foundation"}}
  "最初の窓(歩の窓・task の窓のどちらも開いていない)を開ける時に、測りの口へ sink を据えるため。別の sink が据えてあれば断る。"
  (when (and (not WINDOWS) (not TASK-WINDOWS))
    (setv current (scheduler-trace-sink))
    (when (and (is-not current None) (is-not current step-sink))
      (raise (RuntimeError (.format "scheduler の測りの口に別の sink が据えてある: {!r} — 歩の積算の窓を開けられない" current))))
    (set-scheduler-trace step-sink))
  None)


(deff sink-released []  ; defk にできない: closed・task-closed が LOCK を持ったまま窓を外した後に呼ぶ — 効果を出す間に LOCK を持ち越さない
  {:pre [] :post [(: % None)] :tags {:context "step-tally" :role "foundation"}}
  "最後の窓を閉じた時に、測りの口から sink を外すため。"
  (when (and (not WINDOWS) (not TASK-WINDOWS) (is (scheduler-trace-sink) step-sink))
    (set-scheduler-trace None))
  None)


(deff step-sink [event]  ; defk にできない: scheduler の測りの口が VM の外から出来事ごとに素の関数として呼ぶ sink
  {:pre [(: event dict)] :post [(: % None)] :tags {:context "step-tally" :role "foundation"}}
  "出来事 1 つで、開いている窓を進めるため。歩の窓 = task-leave の歩を、その歩を含む窓 全部に足す(歩の始まりが窓を開けた時刻より後で、
   同じ thread の窓だけ — 最長の歩は壁の時間で比べ、同じ長さなら先の歩を残す)。task の窓 = spawned の親と task-leave の歩を
   (run, tid) の行に書く(note-spawned・note-task-step)。"
  (setv step-ns (.get event "step_ns"))
  (setv kind (get event "event"))
  (when TASK-WINDOWS
    (with [LOCK]
      (match kind
        "spawned" (note-spawned event)
        "task-leave" (when (is-not step-ns None) (note-task-step event)))))
  (when (and WINDOWS (= kind "task-leave") (is-not step-ns None))
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
          (setv summed (.get window.sites site))
          (setv (get window.sites site)
                (if (is summed None)
                    (SiteTally :site site :steps 1 :wall-ns step-ns :cpu-ns step-cpu-ns)
                    (SiteTally :site site :steps (+ summed.steps 1) :wall-ns (+ summed.wall-ns step-ns)
                               :cpu-ns (+ summed.cpu-ns step-cpu-ns))))
          (setv (get WINDOWS key)
                (StepWindow :thread thread :opened-ns window.opened-ns :sites window.sites
                            :tally (StepTally :steps (+ tally.steps 1)
                                              :wall-ns (+ tally.wall-ns step-ns)
                                              :cpu-ns (+ tally.cpu-ns step-cpu-ns)
                                              :longest-wall-ns (if longer step-ns tally.longest-wall-ns)
                                              :longest-cpu-ns (if longer step-cpu-ns tally.longest-cpu-ns)
                                              :longest-site (if longer site tally.longest-site)
                                              :longest-effect (if longer effect tally.longest-effect)
                                              :ready ready)))))))
  None)


(defk tally-with-sites [window]
  {:pre [(: window StepWindow)] :post [(: % StepTally)] :tags {:context "step-tally" :role "foundation"}}
  "純粋: 閉じた窓 window の積算に、場所ごとの和を CPU の多い順(同じ CPU なら歩の多い順)の tuple で載せるため。"
  (dataclasses.replace window.tally
                       :sites (tuple (sorted (.values window.sites) :key (fn [row] #((- row.cpu-ns) (- row.steps)))))))


(defk opened [key thread opened-ns]
  {:pre [(: key str) (: thread str) (: opened-ns int)] :post [(: % None)] :tags {:context "step-tally" :role "foundation"}}
  "窓 key を開くため: 開いている key は断り、最初の窓なら測りの口へ sink を据える(別の sink が据えてあれば断る)。"
  (with [LOCK]
    (when (in key WINDOWS)
      (raise (ValueError (.format "歩の積算の窓 {!r} は開いている(閉じてから開け直す)" key))))
    (sink-claimed)
    (setv (get WINDOWS key) (StepWindow :thread thread :opened-ns opened-ns :tally EMPTY-STEP-TALLY :sites {})))
  None)


(defk closed [key]
  {:pre [(: key str)] :post [(: % (| StepTally None))] :tags {:context "step-tally" :role "foundation"}}
  "窓 key を閉じて積算を返すため(開いていなければ None)。最後の窓なら測りの口から sink を外す。"
  (with [LOCK]
    (val window (.pop WINDOWS key None))
    (sink-released))
  (when (is window None)
    (return None))
  (<- tallied StepTally (tally-with-sites window))
  tallied)


(defk task-opened [key opened-ns]
  {:pre [(: key str) (: opened-ns int)] :post [(: % None)] :tags {:context "step-tally" :role "foundation"}}
  "task の積算の窓 key を開くため: 開いている key は断り、最初の窓なら測りの口へ sink を据える(別の sink が据えてあれば断る)。"
  (with [LOCK]
    (when (in key TASK-WINDOWS)
      (raise (ValueError (.format "task の積算の窓 {!r} は開いている(閉じてから開け直す)" key))))
    (sink-claimed)
    (setv (get TASK-WINDOWS key) (TaskWindow :opened-ns opened-ns :rows {})))
  None)


(defk task-read [key]
  {:pre [(: key str)] :post [(: % (| tuple None))] :tags {:context "step-tally" :role "foundation"}}
  "task の積算の窓 key のここまでの表を、窓を開けたまま返すため(開いていなければ None)。"
  (with [LOCK]
    (val window (.get TASK-WINDOWS key None))
    (val table (if (is window None) None (task-table window))))
  table)


(defk task-closed [key]
  {:pre [(: key str)] :post [(: % (| tuple None))] :tags {:context "step-tally" :role "foundation"}}
  "task の積算の窓 key を閉じて表を返すため(開いていなければ None)。最後の窓なら測りの口から sink を外す。"
  (with [LOCK]
    (val window (.pop TASK-WINDOWS key None))
    (sink-released)
    (val table (if (is window None) None (task-table window))))
  table)


(defhandler step-tally-handler
  "OpenStepTally・CloseStepTally・OpenTaskTally・ReadTaskTally・CloseTaskTally に、process に 1 つの窓の表と scheduler の測りの口で
   答える(頭の註)。Python の scheduler の内側に置く。"
  (OpenStepTally [key]
    (<- (opened key (. (threading.current-thread) name) (time.perf-counter-ns)))
    (resume None))
  (CloseStepTally [key]
    (<- tally (closed key))
    (resume tally))
  (OpenTaskTally [key]
    (<- (task-opened key (time.perf-counter-ns)))
    (resume None))
  (ReadTaskTally [key]
    (<- table (task-read key))
    (resume table))
  (CloseTaskTally [key]
    (<- table (task-closed key))
    (resume table)))
