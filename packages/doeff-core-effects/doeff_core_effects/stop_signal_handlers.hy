;;; Handlers for the stop-signal effects (stop_signal_effects.hy), named by mechanism.
;;;
;;;   os-signal-stop-handler   the process's SIGINT / SIGTERM. The signal box is installed once per session, the first time
;;;                            StopRequested is asked (so installation happens on the thread that runs the program — run
;;;                            it on the main thread; Python only delivers signals there).
;;;   scripted-stop-handler    no I/O: RaiseStop sets the reason, StopRequested reads it. For tests and emulated worlds.
;;;
;;; Both also answer AwaitStop (park until the stop — agora-redesign #2205): the OS handler parks each wait on an external
;;; promise the signal receiver completes; the scripted handler parks every wait on one promise RaiseStop completes. A
;;; scheduler handler must be outside them (AwaitStop creates and waits on promises).
;;;
;;; Both keep their state in the session, so a state handler (doeff_core_effects.handlers.state) must be outside them.

(require doeff-hy.macros [defhandler <-])

(import signal)
(import types)

(import doeff_core_effects.stop_signal_effects [AwaitStop RaiseStop StopRequested])
(import doeff_core_effects.scheduler [CompletePromise CreateExternalPromise CreatePromise ExternalPromise Wait])


;; Signals that mean "stop" for a service.
(setv #^ (get tuple #(signal.Signals ...)) STOP-SIGNALS #(signal.SIGINT signal.SIGTERM))


(defclass StopBox []
  "The one mutable cell a signal handler can write to (Python signal handlers cannot resume a program).
   Keeps the first reason only. waiters = the external promises of the AwaitStop waits still parked: the first
   signal completes them (ExternalPromise.complete is safe from the signal receiver), so the waits wake at once."

  (#^ (| str None) reason)
  (#^ (get tuple #((get ExternalPromise str) ...)) waiters)

  (defn __init__ [self]
    (setv self.reason None
          self.waiters #()))

  (defn #^ None park [self #^ (get ExternalPromise str) waiter]
    "Keep waiter until the stop (or until its wait is cancelled — forget)."
    (setv self.waiters (+ self.waiters #(waiter)))
    None)

  (defn #^ None forget [self #^ (get ExternalPromise str) waiter]
    "Drop a waiter whose wait was cancelled (a loop raced AwaitStop against its own wait and the other side won)."
    (setv self.waiters (tuple (gfor w self.waiters :if (is-not w waiter) w)))
    None)

  (defn #^ None receive [self #^ int number #^ (| types.FrameType None) frame]
    (when (is self.reason None)
      (setv self.reason (+ "signal " (str number)))
      (for [waiter self.waiters]
        (.complete waiter self.reason))
      (setv self.waiters #()))
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
    (resume box.reason))
  (AwaitStop []
    (when (is-not box.reason None)
      (return (resume box.reason)))
    (<- waiter (CreateExternalPromise))
    (.park box waiter)
    (.on-cancel waiter (fn [] (.forget box waiter)))
    (<- reason str (Wait waiter.future))
    (resume reason)))


(defhandler scripted-stop-handler
  "Answer StopRequested from a reason raised in the program by RaiseStop (no I/O). AwaitStop parks on one promise
   that RaiseStop completes."
  (session var raised None)
  (session var stopped None)
  (RaiseStop [reason]
    (when (is raised None)
      (:= raised reason)
      (when (is-not stopped None)
        (<- (CompletePromise stopped reason))))
    (resume None))
  (StopRequested []
    (resume raised))
  (AwaitStop []
    (when (is-not raised None)
      (return (resume raised)))
    (when (is stopped None)
      (<- made (CreatePromise))
      (:= stopped made))
    (<- reason str (Wait stopped.future))
    (resume reason)))
