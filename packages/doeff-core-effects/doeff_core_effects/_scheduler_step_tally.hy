;;; Step-tally handler step-tally-handler -- answers OpenStepTally and CloseStepTally with the task-leave events emitted by the scheduler's measurement port
;;; (set_scheduler_trace -- both the Python and the Rust versions emit the same events).
;;;
;;; Purpose: so a caller (e.g. the agora CLI host) can count, via effects and without touching the measurement port itself, the number of steps the scheduler ran during a span (while carrying one turn), their wall and CPU time, and
;;; the longest step. Only the scheduler's port side reads the clock (step_ns and step_cpu_ns of task-leave).
;;;
;;;   storage  One process-wide "key -> window" table (handlers of other runs and other threads use the same table -- precedent = the storage in process_meter.hy).
;;;           A window holds the name of the scheduler thread that opened it and the opening time, and adds up only those steps of that thread that started after the opening time.
;;;   sink    Installed on the measurement port when the first window opens and removed when the last window closes -- while no window exists the port has no sink and the scheduler does not
;;;           produce events. Opening a window while another sink is installed is refused with RuntimeError (another meter's sink is never silently replaced).
;;;   cost    Only while a window is open, the sink is called once per scheduler event, and each task-leave takes the lock and replaces the window.
;;;           The per-spawn-site sums of tasks (SiteTally) replace one row of the per-window table on every step, and are laid out only once, when the window is closed.
;;;
;;; If a run ends without closing the window, the window and the sink stay in the process (opening the same key next time is refused). Either scheduler version will do
;;; (the default Rust version emits the same events -- "measurement port" in docs/25-rust-scheduler.md).
;;;
;;; Task tally windows (OpenTaskTally, ReadTaskTally, CloseTaskTally): the same sink advances, from spawned and task-leave events,
;;; the per-window table of (run, tid) -> row (TaskTally). Step windows and task windows share the sink, and as long as even one window of either kind is open
;;; the sink stays installed (the decision to install or remove it is in one place, sink-claimed and sink-released).
(require doeff-hy.macros [defhandler defk deff <- val])
(val MODULE-TAGS {:context "step-tally" :role "foundation"})
(require doeff-hy.record [defrecord])
(import dataclasses)
(import dataclasses [dataclass])
(import threading)
(import time)
(import doeff_core_effects._scheduler [scheduler-trace-sink set-scheduler-trace])
(import doeff_core_effects._step_tally_effects [CloseStepTally EMPTY-STEP-TALLY OpenStepTally SiteTally StepTally
                                               CloseTaskTally OpenTaskTally ReadTaskTally TaskTally])


(defrecord StepWindow
  "One open window: thread = name of the scheduler thread that opened it, opened-ns = opening time (perf_counter_ns), tally = tally so far,
   sites = task spawn site -> sum so far (a table inside the handler only -- replaces one row per step and, when the window is closed,
   is answered as a tuple in descending CPU order. Same shape as rows of TaskWindow)."
  {:tags {:context "step-tally" :role "foundation"}}
  (#^ str thread)
  (#^ int opened-ns)
  (#^ StepTally tally)
  (#^ (get dict #((| str None) SiteTally)) sites))


(defrecord TaskWindow
  "One open task tally window: opened-ns = opening time (perf_counter_ns), rows = (run, tid) -> rows so far.
   rows is a table inside the handler only (one row is replaced per event -- so that a per-window copy is not rebuilt every time. Answered outward as a tuple)."
  {:tags {:context "step-tally" :role "foundation"}}
  (#^ int opened-ns)
  (#^ (get dict #(tuple TaskTally)) rows))


;; One per process: key -> open window. Writes happen inside LOCK and replace the window.
(setv #^ (get dict #(str StepWindow)) WINDOWS {})
;; One per process: key -> open task tally window. Writes happen inside LOCK and replace the rows of the window.
(setv #^ (get dict #(str TaskWindow)) TASK-WINDOWS {})
(setv #^ threading.Lock LOCK (threading.Lock))


(deff empty-task-row [run tid parent]  ; defk にできない: used by note-spawned and note-task-step of the sink (step-sink) called from outside the VM
  {:pre [(: run int) (: tid (| int None)) (: parent (| int None))] :post [(: % TaskTally)]
   :tags {:context "step-tally" :role "foundation"}}
  "A row for a task whose steps have not been counted yet."
  (TaskTally :run run :tid tid :parent parent :steps 0 :wall-ns 0 :cpu-ns 0 :vm-steps 0 :handler-calls 0))


(deff note-spawned [event]  ; defk にできない: called while holding LOCK, inside the sink (step-sink) that the scheduler's measurement port calls from outside the VM
  {:pre [(: event dict)] :post [(: % None)] :tags {:context "step-tally" :role "foundation"}}
  "To write the parent of one spawned event into the tables of all open task windows, as a child born after the opening time."
  (setv run (get event "run"))
  (setv tid (get event "tid"))
  (setv parent (get event "parent"))
  (for [window (.values TASK-WINDOWS)]
    (when (>= (get event "ns") window.opened-ns)
      (setv row (.get window.rows #(run tid) (empty-task-row run tid None)))
      (setv (get window.rows #(run tid)) (dataclasses.replace row :parent parent))))
  None)


(deff note-task-step [event]  ; defk にできない: called while holding LOCK, inside the sink (step-sink) that the scheduler's measurement port calls from outside the VM
  {:pre [(: event dict)] :post [(: % None)] :tags {:context "step-tally" :role "foundation"}}
  "To add the step of one task-leave event to that task's row in all task windows whose opening time is before the step's start."
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


(deff task-table [window]  ; defk にできない: task-read and task-closed copy the table while holding LOCK -- LOCK must not be carried across an effect
  {:pre [(: window TaskWindow)] :post [(: % tuple)] :tags {:context "step-tally" :role "foundation"}}
  "To answer a window's table as a tuple in (run, tid) order (the root task, with tid None, comes first)."
  (tuple (sorted (.values window.rows) :key (fn [row] #(row.run (if (is row.tid None) -1 row.tid))))))


(deff sink-claimed []  ; defk にできない: opened and task-opened call it while holding LOCK before adding a window -- LOCK must not be carried across an effect
  {:pre [] :post [(: % None)] :tags {:context "step-tally" :role "foundation"}}
  "When opening the first window (neither a step window nor a task window is open), to install the sink on the measurement port. Refuses if another sink is installed."
  (when (and (not WINDOWS) (not TASK-WINDOWS))
    (setv current (scheduler-trace-sink))
    (when (and (is-not current None) (is-not current step-sink))
      (raise (RuntimeError (.format "Another sink is installed on the scheduler's measurement port: {!r} -- cannot open a step tally window" current))))
    (set-scheduler-trace step-sink))
  None)


(deff sink-released []  ; defk にできない: closed and task-closed call it while holding LOCK after removing a window -- LOCK must not be carried across an effect
  {:pre [] :post [(: % None)] :tags {:context "step-tally" :role "foundation"}}
  "When the last window is closed, to remove the sink from the measurement port."
  (when (and (not WINDOWS) (not TASK-WINDOWS) (is (scheduler-trace-sink) step-sink))
    (set-scheduler-trace None))
  None)


(deff step-sink [event]  ; defk にできない: a sink the scheduler's measurement port calls as a plain function per event from outside the VM
  {:pre [(: event dict)] :post [(: % None)] :tags {:context "step-tally" :role "foundation"}}
  "To advance the open windows with one event. Step window = add the task-leave step to every window containing that step (only windows of the same thread whose step started after the window's opening time
   -- the longest step is compared by wall time, and on a tie the earlier step is kept). Task window = write the parent of spawned and the step of task-leave into the
   (run, tid) row (note-spawned, note-task-step)."
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
  "Pure: to put, on the tally of the closed window, the per-site sums as a tuple in descending CPU order (ties broken by more steps first)."
  (dataclasses.replace window.tally
                       :sites (tuple (sorted (.values window.sites) :key (fn [row] #((- row.cpu-ns) (- row.steps)))))))


(defk opened [key thread opened-ns]
  {:pre [(: key str) (: thread str) (: opened-ns int)] :post [(: % None)] :tags {:context "step-tally" :role "foundation"}}
  "To open window key: refuse an open key, and if it is the first window, install the sink on the measurement port (refuse if another sink is installed)."
  (with [LOCK]
    (when (in key WINDOWS)
      (raise (ValueError (.format "Step tally window {!r} is open (close it before opening again)" key))))
    (sink-claimed)
    (setv (get WINDOWS key) (StepWindow :thread thread :opened-ns opened-ns :tally EMPTY-STEP-TALLY :sites {})))
  None)


(defk closed [key]
  {:pre [(: key str)] :post [(: % (| StepTally None))] :tags {:context "step-tally" :role "foundation"}}
  "To close window key and return the tally (None if it is not open). If it is the last window, remove the sink from the measurement port."
  (with [LOCK]
    (val window (.pop WINDOWS key None))
    (sink-released))
  (when (is window None)
    (return None))
  (<- tallied StepTally (tally-with-sites window))
  tallied)


(defk task-opened [key opened-ns]
  {:pre [(: key str) (: opened-ns int)] :post [(: % None)] :tags {:context "step-tally" :role "foundation"}}
  "To open task tally window key: refuse an open key, and if it is the first window, install the sink on the measurement port (refuse if another sink is installed)."
  (with [LOCK]
    (when (in key TASK-WINDOWS)
      (raise (ValueError (.format "Task tally window {!r} is open (close it before opening again)" key))))
    (sink-claimed)
    (setv (get TASK-WINDOWS key) (TaskWindow :opened-ns opened-ns :rows {})))
  None)


(defk task-read [key]
  {:pre [(: key str)] :post [(: % (| tuple None))] :tags {:context "step-tally" :role "foundation"}}
  "To return the table so far of task tally window key while leaving the window open (None if it is not open)."
  (with [LOCK]
    (val window (.get TASK-WINDOWS key None))
    (val table (if (is window None) None (task-table window))))
  table)


(defk task-closed [key]
  {:pre [(: key str)] :post [(: % (| tuple None))] :tags {:context "step-tally" :role "foundation"}}
  "To close task tally window key and return the table (None if it is not open). If it is the last window, remove the sink from the measurement port."
  (with [LOCK]
    (val window (.pop TASK-WINDOWS key None))
    (sink-released)
    (val table (if (is window None) None (task-table window))))
  table)


(defhandler step-tally-handler
  "Answers OpenStepTally, CloseStepTally, OpenTaskTally, ReadTaskTally and CloseTaskTally with one process-wide window table and the scheduler's measurement port
   (see the header comment). Place it inside the scheduler (both the Python and the Rust versions)."
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
