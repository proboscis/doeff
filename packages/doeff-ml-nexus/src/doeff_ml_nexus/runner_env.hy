;;; Runner process-env adapter.
;;;
;;; DOEFF_INPUT/DOEFF_OUTPUT remain the remote launcher contract, but this
;;; module translates them into doeff Program env before p_run executes.

(require doeff_hy.macros [defk <-])

(import os)
(import doeff [do :as _doeff-do])
(import doeff [run Program])


(setv RUNNER-INPUT-PATH-KEY "doeff_ml_nexus.runner.input_path")
(setv RUNNER-OUTPUT-PATH-KEY "doeff_ml_nexus.runner.output_path")
(setv DEFAULT-RUNNER-INPUT-PATH "/tmp/doeff-exchange/program.pkl")
(setv DEFAULT-RUNNER-OUTPUT-PATH "/tmp/doeff-exchange/result.pkl")


(defk default-runner-env []
  "Return explicit default Program env for runner file exchange paths."
  {:pre []
   :post [(: % dict)]}
  {RUNNER-INPUT-PATH-KEY DEFAULT-RUNNER-INPUT-PATH
   RUNNER-OUTPUT-PATH-KEY DEFAULT-RUNNER-OUTPUT-PATH})


(defk runner-env-from-process []
  "Translate launcher OS environment into doeff Program env at the boundary."
  {:pre []
   :post [(: % dict)]}
  (<- defaults dict (default-runner-env))
  (| defaults
     {RUNNER-INPUT-PATH-KEY (os.environ.get "DOEFF_INPUT" DEFAULT-RUNNER-INPUT-PATH)
      RUNNER-OUTPUT-PATH-KEY (os.environ.get "DOEFF_OUTPUT" DEFAULT-RUNNER-OUTPUT-PATH)}))


(defk resolve-runner-env [env]
  "Merge interpreter env over launcher-derived runner env."
  {:pre [(: env #(dict Program (type None)))]
   :post [(: % dict)]}
  (<- base dict (runner-env-from-process))
  (if (is env None)
      base
      (| base (if (isinstance env dict) env (run env)))))
