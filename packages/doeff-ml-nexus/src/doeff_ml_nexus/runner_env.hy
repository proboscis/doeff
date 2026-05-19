;;; Runner process-env adapter.
;;;
;;; DOEFF_INPUT/DOEFF_OUTPUT remain the remote launcher contract, but this
;;; module translates them into doeff Program env before p_run executes.

(import os)
(import doeff [run])


(setv RUNNER-INPUT-PATH-KEY "doeff_ml_nexus.runner.input_path")
(setv RUNNER-OUTPUT-PATH-KEY "doeff_ml_nexus.runner.output_path")
(setv DEFAULT-RUNNER-INPUT-PATH "/tmp/doeff-exchange/program.pkl")
(setv DEFAULT-RUNNER-OUTPUT-PATH "/tmp/doeff-exchange/result.pkl")


(defn default-runner-env []
  "Return explicit default Program env for runner file exchange paths."
  {RUNNER-INPUT-PATH-KEY DEFAULT-RUNNER-INPUT-PATH
   RUNNER-OUTPUT-PATH-KEY DEFAULT-RUNNER-OUTPUT-PATH})


(defn runner-env-from-process []
  "Translate launcher OS environment into doeff Program env at the boundary."
  (| (default-runner-env)
     {RUNNER-INPUT-PATH-KEY (os.environ.get "DOEFF_INPUT" DEFAULT-RUNNER-INPUT-PATH)
      RUNNER-OUTPUT-PATH-KEY (os.environ.get "DOEFF_OUTPUT" DEFAULT-RUNNER-OUTPUT-PATH)}))


(defn resolve-runner-env [env]
  "Merge interpreter env over launcher-derived runner env."
  (setv base (runner-env-from-process))
  (if (is env None)
      base
      (| base (if (isinstance env dict) env (run env)))))
