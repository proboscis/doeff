;;; Interpreter (composition root) for the stop-signal contract tests - runs the same contract Program with only the stop-signal handler swapped.
;;;
;;;   os-signal  real: os-signal-stop-handler (a real SIGINT / SIGTERM delivered to the process)
;;;   scripted   fake: scripted-stop-handler (no I/O - raises the signal with RaiseStop)
;;;
;;; The contract Program raises the signal with the SendStop effect. Only the means of raising the signal differs per interpreter:
;;;   os-signal -> sends a real signal to the test process (os.kill(os.getpid(), signum))
;;;   scripted  -> raises, with RaiseStop, the same reason string (signal-reason) that the real box writes
;;; StopHandlerUnderTest answers the handler under test itself, for the counterexample that runs without a state handler outside.
;;; The os-signal assembly remembers the SIGINT / SIGTERM receivers from before the run and restores them afterwards (the real handler does not restore receivers).
;;; Used by doeff_interpreter in conftest.py (the :interpreters names of deftest -> INTERPRETERS).
(require doeff-hy.macros [defk defhandler <- val])
(import dataclasses [dataclass])
(import functools [partial])
(import os)
(import signal)
(import doeff [EffectBase Program with_handlers])
(import doeff_core_effects [state])
(import doeff_core_effects [RaiseStop])
(import doeff_core_effects [os-signal-stop-handler scripted-stop-handler]
        doeff_core_effects._stop_signal_handlers [STOP-SIGNALS])

(val OS-SIGNAL "os-signal")
(val SCRIPTED "scripted")
(val STOP-HANDLERS {OS-SIGNAL os-signal-stop-handler
                    SCRIPTED scripted-stop-handler})


(defclass [(dataclass :frozen True)] SendStop [EffectBase]
  "Effect by which the contract Program raises stop signal signum (answer = None)."
  (#^ int signum))


(defclass [(dataclass :frozen True)] StopHandlerUnderTest [EffectBase]
  "Effect that asks for the stop-signal handler under test itself (for the counterexample that places no state handler outside).")


(defk signal-reason [signum]
  {:pre [(: signum int)] :post [(: % str)] :tags {:context "stop-signal-test" :role "judgment"}}
  "The reason string that the real box (StopBox) writes on receiving signal signum. The scripted side uses this as the RaiseStop reason."
  (+ "signal " (str (int signum))))


(defhandler send-by-os-signal
  "Raise SendStop by sending a real signal to the test process itself (Python calls the receiver at the next bytecode boundary)."
  (SendStop [signum]
    (os.kill (os.getpid) signum)
    (resume None)))


(defhandler send-by-raise-stop
  "Raise SendStop by emitting RaiseStop to the inner scripted-stop-handler."
  (SendStop [signum]
    (<- reason str (signal-reason signum))
    (<- (RaiseStop reason))
    (resume None)))


(val SENDERS {OS-SIGNAL send-by-os-signal
              SCRIPTED send-by-raise-stop})


(defhandler stop-handler-under-test [handler]
  ;; Reason it stays an argument: the handler under test is a value decided on the assembly side per interpreter name, and the contract Program has no name to distinguish it.
  (StopHandlerUnderTest []
    (resume handler)))


(defk under-stop-signal [name program]
  {:pre [(: name str) (in name STOP-HANDLERS) (: program Program)]
   :post [(: % "answer of the contract Program (type varies per Program)")]
   :tags {:context "stop-signal-test" :role "foundation"}}
  "Run program under the stop-signal handler named name. From outside in: state, the answerer of the handler under test, the stop-signal handler,
   and the signal raiser (the stop-signal handler receives the RaiseStop that the scripted raiser emits, so the raiser is innermost).
   Restores the SIGINT / SIGTERM receivers from before the run after it."
  (val handler (get STOP-HANDLERS name))
  (val saved (dfor signum STOP-SIGNALS signum (signal.getsignal signum)))
  (try
    (<- answer (with_handlers [(state) (stop-handler-under-test handler) handler (get SENDERS name)] program))
    answer
    (finally
      (for [#(signum receiver) (.items saved)]
        (signal.signal signum receiver)))))


(val INTERPRETERS {OS-SIGNAL (partial under-stop-signal OS-SIGNAL)
                   SCRIPTED (partial under-stop-signal SCRIPTED)})
