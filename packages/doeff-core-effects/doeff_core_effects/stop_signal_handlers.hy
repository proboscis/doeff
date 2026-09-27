;;; Handlers for the stop-signal effects (stop_signal_effects.hy), named by mechanism.
;;;
;;;   os-signal-stop-handler   the process's SIGINT / SIGTERM. The signal box is installed once per session, the first time
;;;                            StopRequested is asked (so installation happens on the thread that runs the program — run
;;;                            it on the main thread; Python only delivers signals there).
;;;   scripted-stop-handler    no I/O: RaiseStop sets the reason, StopRequested reads it. For tests and emulated worlds.
;;;
;;; Both keep their state in the session, so a state handler (doeff_core_effects.handlers.state) must be outside them.

(require doeff-hy.macros [defhandler])

(import signal)
(import types)

(import doeff_core_effects.stop_signal_effects [RaiseStop StopRequested])


;; Signals that mean "stop" for a service.
(setv STOP-SIGNALS #(signal.SIGINT signal.SIGTERM))


(defclass StopBox []
  "The one mutable cell a signal handler can write to (Python signal handlers cannot resume a program).
   Keeps the first reason only."

  (defn __init__ [self]
    (setv self.reason None))

  (defn #^ None receive [self #^ int number #^ (| types.FrameType None) frame]
    (when (is self.reason None)
      (setv self.reason (+ "signal " (str number))))
    None))


(defn #^ StopBox install-stop-box []
  "Install SIGINT / SIGTERM receivers that record the first signal into a fresh StopBox.
   The previous receivers are not restored (a service keeps them for its whole life; a test restores them itself)."
  (setv box (StopBox))
  (for [signum STOP-SIGNALS]
    (signal.signal signum box.receive))
  box)


(defhandler os-signal-stop-handler
  "Answer StopRequested from the process's real SIGINT / SIGTERM."
  (session val box (install-stop-box))
  (StopRequested []
    (resume box.reason)))


(defhandler scripted-stop-handler
  "Answer StopRequested from a reason raised in the program by RaiseStop (no I/O)."
  (session var raised None)
  (RaiseStop [reason]
    (when (is raised None)
      (:= raised reason))
    (resume None))
  (StopRequested []
    (resume raised)))
