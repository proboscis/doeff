;;; Interpreter for the randomness contract tests (composition root) -- runs the Program of the same contract, swapping only the randomness handler (
;;; decision in #1107).
;;;
;;;   os-random      real: os-random-handler (os.urandom)
;;;   seeded-random  fake: seeded-random-handler (values determined by the seed and call order -- it keeps a call count, so state is needed on the outside)
;;;
;;; Used by conftest.py's doeff_interpreter (the :interpreters name of deftest -> INTERPRETERS).
(require doeff-hy.macros [defk <- val])
(import functools [partial])
(import doeff [Program with_handlers])
(import doeff_core_effects [state])
(import doeff_core_effects [os-random-handler])
(import doeff_core_effects [seeded-random-handler])

(val OS-RANDOM "os-random")
(val SEEDED-RANDOM "seeded-random")

;; Seed for the fake (the contract only looks at length and per-call differences).
(val SEED 7)


(defk under-random [name program]
  {:pre [(: name str) (in name #{OS-RANDOM SEEDED-RANDOM}) (: program Program)]
   :post [(: % "Answer of the contract's Program (type varies per Program)")]
   :tags {:context "random-test" :role "foundation"}}
  "To run program under the randomness handler named name."
  (val handlers (if (= name OS-RANDOM) [os-random-handler] [(state) (seeded-random-handler SEED)]))
  (<- answer (with_handlers handlers program))
  answer)


(val INTERPRETERS {OS-RANDOM (partial under-random OS-RANDOM)
                   SEEDED-RANDOM (partial under-random SEEDED-RANDOM)})
