(require doeff-hy.macros [defk <-])

;;; Stop-signal effects and handlers (doeff_core_effects.stop_signal_*):
;;;   * scripted: StopRequested is None until RaiseStop, then the first reason (a second RaiseStop does not overwrite it)
;;;   * os: StopRequested is None until SIGTERM reaches the process, then "signal 15"; the first signal is kept
;;;   * counterexample: without a state handler outside, the session value has nowhere to live and the run fails loudly

(import os)
(import signal)
(import pytest)
(import doeff [run with_handlers])
(import doeff_vm [UnhandledEffect])
(import doeff_core_effects.handlers [state])
(import doeff_core_effects.stop_signal_effects [RaiseStop StopRequested])
(import doeff_core_effects.stop_signal_handlers [os-signal-stop-handler scripted-stop-handler])


(defk scripted-flow []
  {:pre [] :post [(: % tuple)]}
  (<- before (| str None) (StopRequested))
  (<- (RaiseStop "scenario: stop"))
  (<- (RaiseStop "scenario: later"))
  (<- after (| str None) (StopRequested))
  #(before after))


(defn test-scripted-stop-keeps-the-first-reason []
  (assert (= (run (with_handlers [(state) scripted-stop-handler] (scripted-flow)))
             #(None "scenario: stop"))
          "scripted: before RaiseStop must be None, after it the first reason"))


(defk os-flow []
  {:pre [] :post [(: % tuple)]}
  (<- before (| str None) (StopRequested))
  (os.kill (os.getpid) signal.SIGTERM)
  (os.kill (os.getpid) signal.SIGINT)
  (<- after (| str None) (StopRequested))
  #(before after))


(defn test-os-signal-stop-reads-the-first-signal []
  (setv saved (dfor s #(signal.SIGINT signal.SIGTERM) s (signal.getsignal s)))
  (try
    (assert (= (run (with_handlers [(state) os-signal-stop-handler] (os-flow)))
               #(None (+ "signal " (str (int signal.SIGTERM)))))
            "os: before the signal must be None, after SIGTERM then SIGINT the first one (SIGTERM)")
    (finally
      (for [[s h] (.items saved)] (signal.signal s h)))))


(defn test-counterexample-without-state-fails-loudly []
  (with [(pytest.raises UnhandledEffect :match "scripted-stop-handler/raised")]
    (run (with_handlers [scripted-stop-handler] (scripted-flow)))))


(defk raise-under-os []
  {:pre [] :post [(: % None)]}
  (<- (RaiseStop "scenario"))
  None)


(defn test-counterexample-raise-stop-is-not-answered-by-the-os-handler []
  ;; RaiseStop は scripted の口だけ — 本物の signal の handler は答えない(本番で筋書きの止めを起こせない)。
  (with [(pytest.raises UnhandledEffect :match "RaiseStop")]
    (run (with_handlers [(state) os-signal-stop-handler] (raise-under-os)))))
