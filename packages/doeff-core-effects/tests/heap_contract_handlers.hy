;;; Interpreter (composition root) for the heap freeze contract tests -- runs the Program of the same contract, swapping only the freeze handler (
;;; decided in #1107).
;;;
;;;   gc-freeze        real: gc-freeze-handler (collects, then freezes -- changes the process's GC state. Undone after the run)
;;;   scripted-freeze  fake: scripted-freeze-handler (does not touch GC, answers a fixed number)
;;;
;;; The real one changes the process's GC state, so after the run it returns the frozen objects with gc.unfreeze (so other tests' collection is not changed).
;;; Used by doeff_interpreter in conftest.py (name in deftest's :interpreters -> INTERPRETERS).
(require doeff-hy.macros [defk <- val])
(import functools [partial])
(import gc)
(import doeff [Program with_handlers])
(import doeff_core_effects [gc-freeze-handler])
(import doeff_core_effects [scripted-freeze-handler])

(val GC-FREEZE "gc-freeze")
(val SCRIPTED-FREEZE "scripted-freeze")

;; The number the fake answers (the contract only looks at "an int, 0 or more").
(val SCRIPTED-COUNT 3)


(defk under-freeze [name program]
  {:pre [(: name str) (in name #{GC-FREEZE SCRIPTED-FREEZE}) (: program Program)]
   :post [(: % "answer of the contract's Program (type depends on the Program)")]
   :tags {:context "heap-test" :role "foundation"}}
  "Run program under the freeze handler named name, and for the real one return the objects frozen during the run afterward."
  (val handler (if (= name GC-FREEZE) gc-freeze-handler (scripted-freeze-handler SCRIPTED-COUNT)))
  (try
    (<- answer (with_handlers [handler] program))
    answer
    (finally
      (gc.unfreeze))))


(val INTERPRETERS {GC-FREEZE (partial under-freeze GC-FREEZE)
                   SCRIPTED-FREEZE (partial under-freeze SCRIPTED-FREEZE)})
