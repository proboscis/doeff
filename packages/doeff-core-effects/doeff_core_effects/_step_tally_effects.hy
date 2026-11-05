;;; Step tally effects - while a named window is open, count the steps (from entering a task until returning to the scheduler's effect) the scheduler (both the Python and the Rust version) ran for tasks:
;;; the number of steps, wall ns, CPU ns, and the one longest step. A foundation vocabulary with no business terms.
;;;
;;;   OpenStepTally   Opens window key. Opening an already open key again raises ValueError. Answer = None.
;;;   CloseStepTally  Closes window key and answers the tally (StepTally) accumulated while it was open. None if the key is not open.
;;;
;;; What is counted: of the steps on the thread of the scheduler that opened the window, those whose start and end both fall while the window was open (a step that was in progress when the window opened or closed
;;; is not counted). If windows overlap, the same step is added to every window.
;;;
;;; Per-task tally (U6 of #3855): so that a caller can count only the tasks in charge of an interval, answer the rows
;;; (TaskTally) per task that advanced steps during the window. There is one row per (run, tid) (run = the scheduler's run number - tid is numbered per run, so they are kept as a pair).
;;; The parent is derived from the Spawn events seen during the window - the parent of a task born before the window opened is None. The sum over descendants is added by the caller by joining on the parent.
;;; Counting only steps that started during the window is the same as the step tally. It does not filter by thread (run numbers are unique per process, so runs of another thread
;;; also become separate rows).
;;;
;;;   OpenTaskTally   Opens window key. Opening an already open key again raises ValueError. Answer = None.
;;;   ReadTaskTally   Answers the table so far for window key, leaving the window open. None if the key is not open.
;;;   CloseTaskTally  Closes window key and answers the table accumulated while it was open. None if the key is not open.
;;;
;;; Responder: step-tally-handler (scheduler_step_tally.hy - installs a sink on the scheduler's measurement port set_scheduler_trace and counts.
;;; The step-tally window and the task-tally window share the same sink).
(require doeff-hy.macros [defeffect val])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])


(defrecord SiteTally
  "The sum of steps for one task spawn site within one window: site = the site (None for a task with no site, such as the root task), steps = number of steps, wall-ns /
   cpu-ns = the sum of the steps' wall time and thread CPU time. To read which tasks in the window consumed steps more broadly than the single longest step."
  {:tags {:context "step-tally" :role "type"}
   :check [(>= steps 1) (>= wall-ns 0) (>= cpu-ns 0)]}
  (#^ (| str None) site)
  (#^ int steps)
  (#^ int wall-ns)
  (#^ int cpu-ns))


(defrecord StepTally
  "The tally of one window: steps = number of steps, wall-ns / cpu-ns = the sum of the steps' wall time and thread CPU time, longest-* = the one step with the longest wall time
   (wall, CPU, the task's spawn site, the type name of the effect that ended the step - 0 and None if there were no steps), ready = the number of entries that could run when the last step ended,
   sites = the sums of steps added up per task spawn site (in descending CPU order, ties by descending steps - the sum of the rows equals steps, wall-ns and cpu-ns.
   Included only in the answer given when the window is closed)."
  {:tags {:context "step-tally" :role "type"}
   :check [(>= steps 0) (>= wall-ns 0) (>= cpu-ns 0) (>= ready 0)]}
  (#^ int steps)
  (#^ int wall-ns)
  (#^ int cpu-ns)
  (#^ int longest-wall-ns)
  (#^ int longest-cpu-ns)
  (#^ (| str None) longest-site)
  (#^ (| str None) longest-effect)
  (#^ int ready)
  (setv #^ (get tuple #(SiteTally ...)) sites #()))


;; A tally that has counted no steps.
(val EMPTY-STEP-TALLY (StepTally :steps 0 :wall-ns 0 :cpu-ns 0 :longest-wall-ns 0 :longest-cpu-ns 0
                                 :longest-site None :longest-effect None :ready 0))


(defeffect OpenStepTally
  "Opens window key (head note)."
  {:fields [(: key str)]
   :answer None
   :tags {:context "step-tally" :role "foundation"}})


(defeffect CloseStepTally
  "Closes window key and answers the tally accumulated while it was open. None if the key is not open (head note)."
  {:fields [(: key str)]
   :answer (| StepTally None)
   :tags {:context "step-tally" :role "foundation"}})


(defrecord TaskTally
  "One row of the task-tally window (head note): run = the scheduler's run number, tid = the task's number within that run (None for the root task),
   parent = the tid of the Spawn parent seen during the window (None for the root, or a task born before the window opened), steps = number of steps, wall-ns / cpu-ns = the sum of the steps'
   wall time and thread CPU time, vm-steps / handler-calls = the sum of the steps' doeff-vm step counts and handler calls (they do not fluctuate with machine load)."
  {:tags {:context "step-tally" :role "type"}
   :check [(>= steps 0) (>= wall-ns 0) (>= cpu-ns 0) (>= vm-steps 0) (>= handler-calls 0)]}
  (#^ int run)
  (#^ (| int None) tid)
  (#^ (| int None) parent)
  (#^ int steps)
  (#^ int wall-ns)
  (#^ int cpu-ns)
  (#^ int vm-steps)
  (#^ int handler-calls))


(defeffect OpenTaskTally
  "Opens the task-tally window key (head note)."
  {:fields [(: key str)]
   :answer None
   :tags {:context "step-tally" :role "foundation"}})


(defeffect ReadTaskTally
  "Answers the table so far for the task-tally window key in (run, tid) order, leaving the window open. None if it is not open (head note)."
  {:fields [(: key str)]
   :answer (| (get tuple #(TaskTally ...)) None)
   :tags {:context "step-tally" :role "foundation"}})


(defeffect CloseTaskTally
  "Closes the task-tally window key and answers the table accumulated while it was open in (run, tid) order. None if it is not open (head note)."
  {:fields [(: key str)]
   :answer (| (get tuple #(TaskTally ...)) None)
   :tags {:context "step-tally" :role "foundation"}})

