;;; Interpreter (composition root) for the meter contract tests - runs the same contract Program with only the meter answerer swapped (decision in #1107).
;;;
;;;   process-meter  real: process-meter-handler (one store per process - shared by name across runs and threads)
;;;   memory-meter   fake: memory-meter-handler (state within a single run only - reads no clock, gc, or threads)
;;;
;;; The real one keeps a per-process store for each name, so the interpreter uses a fresh name on every run (counts from earlier tests do not mix in).
;;; The same bucket table (CONTRACT-SETTINGS) is passed to both.
;;; Used by doeff_interpreter in conftest.py (the :interpreters names of deftest -> INTERPRETERS).
(require doeff-hy.macros [defk <- val])
(import functools [partial])
(import itertools [count])
(import doeff [Program with_handlers])
(import doeff_core_effects [state])
(import doeff_core_effects [MeterBucket MeterSettings])
(import doeff_core_effects [process-meter-handler])
(import doeff_core_effects [memory-meter-handler])

(val PROCESS-METER "process-meter")
(val MEMORY-METER "memory-meter")

;; Bucket table read by the contract tests: at most 1 s, at most 5 s, and inf which counts everything.
(val CONTRACT-SETTINGS (MeterSettings :buckets #((MeterBucket :label "le_1s" :ceiling 1.0)
                                                 (MeterBucket :label "le_5s" :ceiling 5.0))
                                      :inf-label "le_inf"))

;; A counter that changes the real store's name on every run (the process store persists for the whole test process).
(val RUN-NUMBERS (count))


(defk under-meter [name program]
  {:pre [(: name str) (in name #{PROCESS-METER MEMORY-METER}) (: program Program)]
   :post [(: % "answer of the contract Program (type varies per Program)")]
   :tags {:context "meter-test" :role "foundation"}}
  "Run program under the meter answerer named name. The real one uses a fresh store name on every run."
  (val handler (if (= name PROCESS-METER)
                   (process-meter-handler (+ "meter-contract-" (str (next RUN-NUMBERS))) CONTRACT-SETTINGS)
                   (memory-meter-handler CONTRACT-SETTINGS)))
  (<- answer (with_handlers [(state) handler] program))
  answer)


(val INTERPRETERS {PROCESS-METER (partial under-meter PROCESS-METER)
                   MEMORY-METER (partial under-meter MEMORY-METER)})
