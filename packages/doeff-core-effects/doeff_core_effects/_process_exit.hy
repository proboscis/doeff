;;; Answerer for the effects that wait for a child process to end (AwaitProcessExit in process_effects.hy, AwaitWarmChildExit in warm_effects.hy)
;;; process-exit-handler.
;;;
;;; How it waits: open an fd that becomes readable when the process ends, hang it on add_reader of the shared asyncio loop of await-handler, and answer when it becomes readable --
;;; no periodic polling. Only how to open the fd differs per platform (EXIT-FD-OPENERS -- Linux = pidfd-of [pidfd_open] in pidfd_exit.hy, macOS = kqueue-fd-of in kqueue_exit.hy
;;; [EVFILT_PROC / NOTE_EXIT of kqueue]); both have the same shape pid -> fd | None (None for a pid that does not exist; the caller closes the fd).
;;; A platform with no known way to open is named with ExitWaitUnavailable (it does not silently fall back to periodic polling). Both ways can open on a process that is not a child,
;;; so a child split off from the waiting child (not a child of the worker) can be waited on in the same shape.
;;;
;;;   AwaitProcessExit    Waits only for children this process started (STARTED-CHILDREN in os_process.hy) -- a pid not in the table is ProcessNotChild.
;;;                       The wait does not reap (the exit code comes with a later PollProcess). After opening the fd, it confirms the child has not yet been reaped
;;;                       (if not reaped, the pid at open time is still this child -- it was not reused).
;;;   AwaitWarmChildExit  Waits on the pair of pid and start-ticks. After opening the fd, it confirms a process with the same start-ticks is still running
;;;                       (same-child-running in os_warm_process.hy). If different (ended, reused, or absent), it answers on the spot.
;;;
;;; Resources: opening, checking, waiting on, and closing the fd all happen inside the awaited coroutine (the loop thread). Even if the Await is discarded before reaching the answerer,
;;; neither an opened fd nor a never-awaited coroutine remains, and cancelling the waiting task makes the coroutine's finally remove the read wait and close the fd.
;;;
;;; Stall watch: the wait is an external wait whose source of the end notice (the fd that becomes readable at the end) is registered, so the Await's source-alive is given
;;; a read of "has the awaited party not yet ended" (AwaitProcessExit = child-running, AwaitWarmChildExit = same-child-running).
;;; The scheduler does not count a wait as stalled while the party is alive, and counts as stalled a wait whose party has ended but whose notice has not arrived (clause S1 --
;;; packages/doeff-core-effects/architecture.hy). The read runs on the scheduler thread, once per period of the stall watch.
;;;
;;; Needs await-handler and scheduled on the outside.
(require doeff-hy.macros [defhandler defk <- val])
(val MODULE-TAGS {:context "process" :role "foundation"})
(import asyncio)
(import os)
(import platform)
(import collections.abc [Callable Generator])
(import doeff [run])
(import doeff_core_effects._effects [Await])
(import doeff_core_effects._process_effects [AwaitProcessExit ProcessEnded ProcessNotChild])
(import doeff_core_effects._warm_effects [AwaitWarmChildExit])
(import doeff_core_effects._os_process [STARTED-CHILDREN])
(import doeff_core_effects._os_warm_process [proc-stat-of same-child-running])
(import doeff_core_effects._process_stat [ProcStat ENDED-STATES])
(import doeff_core_effects._pidfd_exit [pidfd-of])
(import doeff_core_effects._kqueue_exit [kqueue-fd-of])

;; How to open the fd that becomes readable at the end, per platform (platform.system name) (header comment).
(val EXIT-FD-OPENERS {"Linux" pidfd-of "Darwin" kqueue-fd-of})


(defclass ExitWaitUnavailable [RuntimeError]
  "This platform has no known way to open an fd readable at process end (a platform not in EXIT-FD-OPENERS).")


(defk exit-fd-opener-for [system]
  {:pre [(: system str)] :post [(: % Callable)] :tags {:context "process" :role "foundation"}}
  "To choose, from the platform name (platform.system), how to open the fd readable at the end on that platform. An unknown platform gives an ExitWaitUnavailable carrying its name."
  (val opener (.get EXIT-FD-OPENERS system))
  (when (is opener None)
    (raise (ExitWaitUnavailable (.format "platform with no known way to open an fd to wait for process end: {} (known platforms = {})"
                                         system (.join ", " (sorted EXIT-FD-OPENERS))))))
  opener)


(defk exit-fd-of [pid]
  {:pre [(: pid int)] :post [(: % (| int None))] :tags {:context "process" :role "foundation"}}
  "To open, with this platform's way, an fd readable when the process pid ends. None for a pid that does not exist (the caller closes it)."
  (<- opener Callable (exit-fd-opener-for (platform.system)))
  (<- fd (| int None) (opener pid))
  fd)


(defk child-unreaped [pid]
  {:pre [(: pid int)] :post [(: % bool)] :tags {:context "process" :role "foundation"}}
  "To check whether pid is a child this process started and has not yet reaped (the pid of an unreaped child is never reused)."
  (val found (.find STARTED-CHILDREN pid))
  (and (is-not found None) (is (. (get found 0) returncode) None)))


(defk child-running [pid]
  {:pre [(: pid int)] :post [(: % bool)] :tags {:context "process" :role "foundation"}}
  "To check whether pid is a child this process started and has not yet reaped, and has not ended yet (neither zombie nor being reaped -- outside ENDED-STATES) --
   a read of whether the source of the end notice is alive (stall watch in the header comment). The pid of an unreaped child is never reused, so
   start-ticks is not compared."
  (<- unreaped bool (child-unreaped pid))
  (if unreaped
      (do (<- seen (| ProcStat None) (proc-stat-of pid))
          (and (is-not seen None) (not-in seen.state ENDED-STATES)))
      False))


(defn :async #^ None ended [#^ int pid #^ (get Callable #([] bool)) still-ours]
  "A coroutine that waits until the process pid ends (runs on the loop thread). Opens the fd readable at the end; if still-ours (after opening, is it still the awaited
   process?) is false it returns on the spot, if true it waits until the fd is readable and returns. In every way of ending (including cancellation) it removes the read wait and closes the fd."
  (setv fd (run (exit-fd-of pid)))
  (when (is fd None)
    (return None))
  (try
    (when (still-ours)
      (setv loop (asyncio.get-running-loop) done (.create-future loop))
      (.add-reader loop fd (fn [] (when (not (.done done)) (.set-result done None))))
      (try (await done)
           (finally (.remove-reader loop fd))))
    (finally (os.close fd)))
  None)


(defclass ExitEnd []
  "An awaitable that waits for the process pid to end (handed to Await). The coroutine is created only when awaited -- even if the Await is discarded before reaching the answerer,
   neither an opened fd nor a never-awaited coroutine remains (header comment)."
  (defn #^ None __init__ [self #^ int pid #^ (get Callable #([] bool)) still-ours]
    (setv self.pid pid self.still-ours still-ours))

  (defn #^ (get Generator #(object None None)) __await__ [self]
    (.__await__ (ended self.pid self.still-ours))))


(defhandler process-exit-handler
  "Answers the effects that wait for a child process to end (AwaitProcessExit, AwaitWarmChildExit) by waiting on an fd readable at the end (per-platform way of opening)."
  ;; Wait for the end of the child process with an fd readable at the end (per-platform way of opening) (header comment).
  (AwaitProcessExit [pid]
    (if (is (.find STARTED-CHILDREN pid) None)
        (resume (ProcessNotChild :pid pid))
        (do (<- (Await (ExitEnd pid (fn [] (run (child-unreaped pid))))
                       :source-alive (fn [] (run (child-running pid)))))
            (resume (ProcessEnded :pid pid)))))
  (AwaitWarmChildExit [pid start-ticks]
    (val running (fn [] (run (same-child-running pid start-ticks))))
    (<- (Await (ExitEnd pid running) :source-alive running))
    (resume (ProcessEnded :pid pid))))
