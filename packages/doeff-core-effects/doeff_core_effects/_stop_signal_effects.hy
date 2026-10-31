;;; Stop-signal effects: has the process been asked to stop (SIGINT / SIGTERM)?
;;;
;;; A long-running loop asks StopRequested between steps and winds down when it answers a reason. A loop that sleeps until
;;; something else happens races AwaitStop against its own wait instead of waking up to ask.
;;; The handlers live in stop_signal_handlers.hy: os-signal-stop-handler (the process's real signals)
;;; and scripted-stop-handler (no I/O — a scenario raises the stop with RaiseStop).
;;; Added so services stop keeping their own signal boxes.

(import dataclasses [dataclass])
(import doeff [EffectBase])


(defclass [(dataclass :frozen True)] StopRequested [EffectBase]
  "Ask whether a stop has been requested. Answer = the reason (str, e.g. \"signal 15\") or None (keep running).
   The first reason is kept: later signals do not overwrite it.")


(defclass [(dataclass :frozen True)] AwaitStop [EffectBase]
  "Wait until a stop has been requested. Answer = the reason (str) — at once if one was already requested.
   It never answers None: a loop that sleeps until something else happens (a table changes, a deadline) spawns
   this, races it against its own wait, and cancels the loser — so it notices a stop without waking up every few
   seconds to ask StopRequested.")


(defclass [(dataclass :frozen True)] StopBell [EffectBase]
  "Get the bell a stop rings. Answer = a scheduler Future that completes when a stop is requested — the same Future for the whole
   session, already completed when a stop already came. Its value is not the reason (ask StopRequested). A loop that waits for the
   first of several things (a deadline, other bells) puts this Future in its race as it is: unlike AwaitStop, nothing is spawned
   per wait and nothing is left to cancel when another thing wins (doeff-cluster's worker tick pause, which waits between every
   heartbeat).")


(defclass [(dataclass :frozen True)] RaiseStop [EffectBase]
  "Request a stop from inside the program (answered by scripted-stop-handler; the OS handler does not answer it).
   Answer = None. The first reason is kept."
  (#^ str reason))
