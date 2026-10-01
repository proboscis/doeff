;;; Stop-signal effects: has the process been asked to stop (SIGINT / SIGTERM)?
;;;
;;; A long-running loop asks StopRequested between steps and winds down when it answers a reason. A loop that sleeps until
;;; something else happens races AwaitStop against its own wait instead of waking up to ask.
;;; The handlers live in stop_signal_handlers.hy: os-signal-stop-handler (the process's real signals)
;;; and scripted-stop-handler (no I/O — a scenario raises the stop with RaiseStop).
;;; Added for agora-redesign #802 (bundle 5) so services stop keeping their own signal boxes.

(import dataclasses [dataclass])
(import doeff_vm [EffectBase])


(defclass [(dataclass :frozen True)] StopRequested [EffectBase]
  "Ask whether a stop has been requested. Answer = the reason (str, e.g. \"signal 15\") or None (keep running).
   The first reason is kept: later signals do not overwrite it.")


(defclass [(dataclass :frozen True)] AwaitStop [EffectBase]
  "Wait until a stop has been requested. Answer = the reason (str) — at once if one was already requested.
   It never answers None: a loop that sleeps until something else happens (a table changes, a deadline) spawns
   this, races it against its own wait, and cancels the loser — so it notices a stop without waking up every few
   seconds to ask StopRequested (agora-redesign #2205).")


(defclass [(dataclass :frozen True)] RaiseStop [EffectBase]
  "Request a stop from inside the program (answered by scripted-stop-handler; the OS handler does not answer it).
   Answer = None. The first reason is kept."
  (#^ str reason))
