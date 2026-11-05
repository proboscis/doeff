;;; The interpreter (composition root) for the latest-value contract tests -- runs the same contract Program, swapping only the latest-value answerer (
;;; decision of #1107).
;;;
;;;   process-latest  real: process-latest-handler (one store per process -- shared by the same name across other runs and other threads)
;;;   memory-latest   fake: memory-latest-handler (state only within one run -- does not read threads)
;;;
;;; The real one has a process store per name, so the interpreter uses a new name on every run (values from earlier checks do not mix in).
;;; Used by conftest.py's doeff_interpreter (the names in deftest's :interpreters -> INTERPRETERS).
(require doeff-hy.macros [defk <- val])
(import functools [partial])
(import itertools [count])
(import doeff [Program with_handlers])
(import doeff_core_effects [state])
(import doeff_core_effects [process-latest-handler])
(import doeff_core_effects [memory-latest-handler])

(val PROCESS-LATEST "process-latest")
(val MEMORY-LATEST "memory-latest")

;; A counter that changes the real store's name on every run (the process store stays for the whole life of the test process).
(val RUN-NUMBERS (count))


(defk under-latest [name program]
  {:pre [(: name str) (in name #{PROCESS-LATEST MEMORY-LATEST}) (: program Program)]
   :post [(: % "answer of the contract Program (type varies per Program)")]
   :tags {:context "latest-test" :role "foundation"}}
  "Run program under the latest-value answerer for name. The real one uses a new store name on every run."
  (val handler (if (= name PROCESS-LATEST)
                   (process-latest-handler (+ "latest-contract-" (str (next RUN-NUMBERS))))
                   memory-latest-handler))
  (<- answer (with_handlers [(state) handler] program))
  answer)


(val INTERPRETERS {PROCESS-LATEST (partial under-latest PROCESS-LATEST)
                   MEMORY-LATEST (partial under-latest MEMORY-LATEST)})
