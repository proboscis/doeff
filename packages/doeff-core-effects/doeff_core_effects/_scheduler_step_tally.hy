;;; Step-tally handler step-tally-handler -- answers OpenStepTally and CloseStepTally with the task-leave events emitted by the scheduler's measurement port
;;; (set_scheduler_trace -- both the Python and the Rust versions emit the same events).
;;;
;;; Purpose: so a caller (e.g. the agora CLI host) can count, via effects and without touching the measurement port itself, the number of steps the scheduler ran during a span (while carrying one turn), their wall and CPU time, and
;;; the longest step. Only the scheduler's port side reads the clock (step_ns and step_cpu_ns of task-leave).
;;;
;;;   storage  One process-wide "key -> window" table and one process-wide "thread -> lane" table (handlers of other runs and other threads use the same tables -- precedent = the
;;;           storage in process_meter.hy). A lane holds the running sums of one scheduler thread (steps, wall, CPU, and the same per task spawn site), the steps that can still be
;;;           the longest step of an open window, and that thread's open windows in opening order. A window holds the name of the scheduler thread that opened it, the opening time,
;;;           and the lane's sums at the opening time; it answers the difference, which is the sum of those steps of that thread that started after the opening time.
;;;   sink    Installed on the measurement port when the first window opens and removed when the last window closes -- while no window exists the port has no sink and the scheduler does not
;;;           produce events. Opening a window while another sink is installed is refused with RuntimeError (another meter's sink is never silently replaced).
;;;   cost    Only while a window is open, the sink is called once per scheduler event. A task-leave takes the lock and advances only its thread's lane -- the cost of one step does not
;;;           grow with the number of open windows (until 2026-11-07 every task-leave rebuilt every open window: with 40 windows open on the agora CLI host, a step cost
;;;           ~760 us of CPU instead of ~420 us, and the extra time made the turns wait longer, which kept more windows open). Opening copies the lane's per-site sums and closing
;;;           lays out the per-site differences, both once per window. The one step that was in progress when a window opened (it started before the opening time) is added to that
;;;           window's opening sums when it leaves, so the difference does not count it.
;;;   longest  The lane keeps the steps that no later step outlasts (wall time strictly longer, compared with the steps that started at or before it), in order of start, so the
;;;           first kept step that started after a window opened is that window's longest step (on a tie the earlier step is kept). Pushing a step drops the steps it outlasts
;;;           (amortized O(1)); closing drops the steps that started before every window still open on the lane.
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
(import doeff_core_effects._step_tally_effects [CloseStepTally OpenStepTally SiteTally StepTally
                                               CloseTaskTally OpenTaskTally ReadTaskTally TaskTally])


(defrecord StepWindow
  "One open window: thread = name of the scheduler thread that opened it, opened-ns = opening time (perf_counter_ns), base-steps / base-wall-ns /
   base-cpu-ns = the thread's lane sums at the opening time, base-sites = the lane's per-site sums (steps, wall ns, CPU ns) at the opening time (a copy
   inside the handler only). The step in progress at the opening time is added to the base when it leaves (header comment)."
  {:tags {:context "step-tally" :role "foundation"}}
  (#^ str thread)
  (#^ int opened-ns)
  (#^ int base-steps)
  (#^ int base-wall-ns)
  (#^ int base-cpu-ns)
  (#^ (get dict #((| str None) tuple)) base-sites))


(defclass Lane []
  "The running sums of one scheduler thread (header comment): steps / wall-ns / cpu-ns = sums of the steps that left while a window of this thread was open,
   sites = task spawn site -> (steps, wall ns, CPU ns), peaks = (started ns, wall ns, CPU ns, site, effect) of the steps that can still be a window's longest step,
   in order of start with wall time not increasing, ready = the ready count at the last step, opened = (opening time, key) of this thread's open windows in opening
   order. Mutable on purpose: the sink advances it in place under LOCK once per step, so that a step costs the same whatever the number of open windows."
  (setv __slots__ #("steps" "wall_ns" "cpu_ns" "sites" "peaks" "ready" "opened"))
  (defn #^ None __init__ [self]  ; cannot be a defk: the constructor the sink and opened call as a plain function
    (setv self.steps 0
          self.wall-ns 0
          self.cpu-ns 0
          self.sites {}
          self.peaks []
          self.ready 0
          self.opened #())))


(defrecord TaskWindow
  "One open task tally window: opened-ns = opening time (perf_counter_ns), rows = (run, tid) -> rows so far.
   rows is a table inside the handler only (one row is replaced per event -- so that a per-window copy is not rebuilt every time. Answered outward as a tuple)."
  {:tags {:context "step-tally" :role "foundation"}}
  (#^ int opened-ns)
  (#^ (get dict #(tuple TaskTally)) rows))


;; One per process: key -> open window. Writes happen inside LOCK and replace the window.
(setv #^ (get dict #(str StepWindow)) WINDOWS {})
;; One per process: scheduler thread name -> lane. Writes happen inside LOCK and advance the lane in place.
(setv #^ (get dict #(str Lane)) LANES {})
;; One per process: key -> open task tally window. Writes happen inside LOCK and replace the rows of the window.
(setv #^ (get dict #(str TaskWindow)) TASK-WINDOWS {})
(setv #^ threading.Lock LOCK (threading.Lock))


(deff empty-task-row [run tid parent]  ; cannot be a defk: used by note-spawned and note-task-step of the sink (step-sink) called from outside the VM
  {:pre [(: run int) (: tid (| int None)) (: parent (| int None))] :post [(: % TaskTally)]
   :tags {:context "step-tally" :role "foundation"}}
  "A row for a task whose steps have not been counted yet."
  (TaskTally :run run :tid tid :parent parent :steps 0 :wall-ns 0 :cpu-ns 0 :vm-steps 0 :handler-calls 0))


(deff note-spawned [event]  ; cannot be a defk: called while holding LOCK, inside the sink (step-sink) that the scheduler's measurement port calls from outside the VM
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


(deff note-task-step [event]  ; cannot be a defk: called while holding LOCK, inside the sink (step-sink) that the scheduler's measurement port calls from outside the VM
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


(deff task-table [window]  ; cannot be a defk: task-read and task-closed copy the table while holding LOCK -- LOCK must not be carried across an effect
  {:pre [(: window TaskWindow)] :post [(: % tuple)] :tags {:context "step-tally" :role "foundation"}}
  "To answer a window's table as a tuple in (run, tid) order (the root task, with tid None, comes first)."
  (tuple (sorted (.values window.rows) :key (fn [row] #(row.run (if (is row.tid None) -1 row.tid))))))


(deff sink-claimed []  ; cannot be a defk: opened and task-opened call it while holding LOCK before adding a window -- LOCK must not be carried across an effect
  {:pre [] :post [(: % None)] :tags {:context "step-tally" :role "foundation"}}
  "When opening the first window (neither a step window nor a task window is open), to install the sink on the measurement port. Refuses if another sink is installed."
  (when (and (not WINDOWS) (not TASK-WINDOWS))
    (setv current (scheduler-trace-sink))
    (when (and (is-not current None) (is-not current step-sink))
      (raise (RuntimeError (.format "Another sink is installed on the scheduler's measurement port: {!r} -- cannot open a step tally window" current))))
    (set-scheduler-trace step-sink))
  None)


(deff sink-released []  ; cannot be a defk: closed and task-closed call it while holding LOCK after removing a window -- LOCK must not be carried across an effect
  {:pre [] :post [(: % None)] :tags {:context "step-tally" :role "foundation"}}
  "When the last window is closed, to remove the sink from the measurement port."
  (when (and (not WINDOWS) (not TASK-WINDOWS) (is (scheduler-trace-sink) step-sink))
    (set-scheduler-trace None))
  None)


(deff step-sink [event]  ; cannot be a defk: a sink the scheduler's measurement port calls as a plain function per event from outside the VM
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
    (setv lane (.get LANES (get event "thread")))
    (when (is-not lane None)
      (with [LOCK]
        (lane-stepped lane (- (get event "ns") step-ns) step-ns (get event "step_cpu_ns") (.get event "site") (.get event "effect")
                      (.get event "ready" 0)))))
  None)


(deff lane-stepped [lane started-ns step-ns step-cpu-ns site effect ready]  ; cannot be a defk: called while holding LOCK, inside the sink (step-sink) that the scheduler's measurement port calls from outside the VM
  {:pre [(: lane Lane) (: started-ns int) (: step-ns int) (: step-cpu-ns int) (: site (| str None)) (: effect (| str None)) (: ready int)]
   :post [(: % None)] :tags {:context "step-tally" :role "foundation"}}
  "To advance one thread's lane with one task-leave step (header comment): add the step to the sums and to its site's sums, keep it as a possible longest step,
   and add it to the opening sums of the windows that opened while it was in progress (opened after it started -- the newest windows, at the end of lane.opened).
   A lane with no open window is left as it is (a window copies the sums when it opens)."
  (when (not lane.opened)
    (return None))
  (setv lane.steps (+ lane.steps 1)
        lane.wall-ns (+ lane.wall-ns step-ns)
        lane.cpu-ns (+ lane.cpu-ns step-cpu-ns)
        lane.ready ready)
  (setv summed (.get lane.sites site))
  (setv (get lane.sites site) (if (is summed None)
                                  #(1 step-ns step-cpu-ns)
                                  #((+ (get summed 0) 1) (+ (get summed 1) step-ns) (+ (get summed 2) step-cpu-ns))))
  (peak-kept lane.peaks #(started-ns step-ns step-cpu-ns site effect))
  (when (< started-ns (get lane.opened -1 0))
    (for [#(opened-ns key) (reversed lane.opened)]
      (when (<= opened-ns started-ns)
        (break))
      (setv window (get WINDOWS key))
      (setv based (.get window.base-sites site))
      (setv (get window.base-sites site) (if (is based None)
                                             #(1 step-ns step-cpu-ns)
                                             #((+ (get based 0) 1) (+ (get based 1) step-ns) (+ (get based 2) step-cpu-ns))))
      (setv (get WINDOWS key) (dataclasses.replace window :base-steps (+ window.base-steps 1) :base-wall-ns (+ window.base-wall-ns step-ns)
                                                   :base-cpu-ns (+ window.base-cpu-ns step-cpu-ns)))))
  None)


(deff peak-kept [peaks peak]  ; cannot be a defk: called while holding LOCK, inside the sink (step-sink) that the scheduler's measurement port calls from outside the VM
  {:pre [(: peaks list) (: peak tuple)] :post [(: % None)] :tags {:context "step-tally" :role "foundation"}}
  "To keep one step peak = (started ns, wall ns, CPU ns, site, effect) in peaks, the possible longest steps in order of start with wall time not increasing (header comment).
   A kept step that started after peak and lasts strictly longer outlasts it in every window that holds it, so peak is not kept (an equal one does not -- on a tie
   the earlier step is the longest); the kept steps that started at or before peak and are strictly shorter are dropped. A step reaches the sink in order of start
   except a step of an outer run on the same thread, so the place is found from the end."
  (setv #(started wall) #((get peak 0) (get peak 1)))
  (setv place (len peaks))
  (while (and (> place 0) (> (get peaks (- place 1) 0) started))
    (setv place (- place 1)))
  (when (and (< place (len peaks)) (> (get peaks place 1) wall))
    (return None))
  (while (and (> place 0) (< (get peaks (- place 1) 1) wall))
    (setv place (- place 1))
    (del (get peaks place)))
  (.insert peaks place peak)
  None)


(defk tally-of-window [window lane]
  {:pre [(: window StepWindow) (: lane Lane)] :post [(: % StepTally)] :tags {:context "step-tally" :role "foundation"}}
  "Pure: the tally of the closed window -- the lane's sums less the window's opening sums, the first kept peak that started at or after the opening time as the
   longest step, the lane's last ready count when a step was counted, and the per-site differences as a tuple in descending CPU order (ties broken by more steps first)."
  (val steps (- lane.steps window.base-steps))
  (val longest (next (gfor peak lane.peaks :if (>= (get peak 0) window.opened-ns) peak) None))
  (val sites (lfor #(site summed) (.items lane.sites)
                   :setv based (.get window.base-sites site #(0 0 0))
                   :if (> (get summed 0) (get based 0))
                   (SiteTally :site site :steps (- (get summed 0) (get based 0)) :wall-ns (- (get summed 1) (get based 1))
                              :cpu-ns (- (get summed 2) (get based 2)))))
  (StepTally :steps steps
             :wall-ns (- lane.wall-ns window.base-wall-ns)
             :cpu-ns (- lane.cpu-ns window.base-cpu-ns)
             :longest-wall-ns (if (is longest None) 0 (get longest 1))
             :longest-cpu-ns (if (is longest None) 0 (get longest 2))
             :longest-site (if (is longest None) None (get longest 3))
             :longest-effect (if (is longest None) None (get longest 4))
             :ready (if (> steps 0) lane.ready 0)
             :sites (tuple (sorted sites :key (fn [row] #((- row.cpu-ns) (- row.steps)))))))


(defk opened [key thread opened-ns]
  {:pre [(: key str) (: thread str) (: opened-ns int)] :post [(: % None)] :tags {:context "step-tally" :role "foundation"}}
  "To open window key: refuse an open key, and if it is the first window, install the sink on the measurement port (refuse if another sink is installed).
   The window keeps a copy of its thread's lane sums at the opening time (header comment)."
  (with [LOCK]
    (when (in key WINDOWS)
      (raise (ValueError (.format "Step tally window {!r} is open (close it before opening again)" key))))
    (sink-claimed)
    (val lane (.setdefault LANES thread (Lane)))
    (setv lane.opened (+ lane.opened #(#(opened-ns key))))
    (setv (get WINDOWS key) (StepWindow :thread thread :opened-ns opened-ns :base-steps lane.steps :base-wall-ns lane.wall-ns
                                        :base-cpu-ns lane.cpu-ns :base-sites (dict lane.sites))))
  None)


(defk closed [key]
  {:pre [(: key str)] :post [(: % (| StepTally None))] :tags {:context "step-tally" :role "foundation"}}
  "To close window key and return the tally (None if it is not open). The lane forgets the window and the peaks no open window of the lane can hold.
   If it is the last window, remove the sink from the measurement port."
  (with [LOCK]
    (val window (.pop WINDOWS key None))
    (val lane (if (is window None) None (get LANES window.thread)))
    (val tallied (if (is window None) None (! (tally-of-window window lane))))
    (when (is-not lane None)
      (setv lane.opened (tuple (gfor one lane.opened :if (!= (get one 1) key) one)))
      (setv lane.peaks (if lane.opened (lfor peak lane.peaks :if (>= (get peak 0) (get lane.opened 0 0)) peak) [])))
    (sink-released))
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
