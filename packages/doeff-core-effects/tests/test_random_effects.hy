;;; Random tests. The contract (the real os-random-handler and the fake seeded-random-handler pass the same deftest) and fake-only properties.
;;; The interpreter assembly is in random_contract_handlers.hy.
;;;
;;;   contract  The answer of RandomBytes is bytes of length count, and two consecutive calls give different values (the real one can coincide with probability 2^-128, which is negligible)
;;;   fake      Same seed and same call order give the same values across runs; a different seed gives different values (so simulated identities are the same every time)
(require doeff-hy.macros [deftest defk <-])
(import doeff [run with_handlers])
(import doeff_core_effects [state])
(import doeff_core_effects [RandomBytes])
(import doeff_core_effects [seeded-random-handler])


(deftest test-a-random-bytes-answer-the-asked-length-and-differ-per-call
  {:interpreters ["os-random" "seeded-random"]}
  (<- first bytes (RandomBytes 16))
  (<- second bytes (RandomBytes 16))
  (<- empty bytes (RandomBytes 0))
  (<- long bytes (RandomBytes 100))
  (assert (= (len first) 16) first)
  (assert (!= first second) (.format "two consecutive calls gave the same value {!r}" first))
  (assert (= empty b"") empty)
  (assert (= (len long) 100) long))


(defk first-draw-of [seed]
  {:pre [(: seed int)] :post [(: % bytes)] :tags {:context "random-test" :role "foundation"}}
  "To run a new run with the fake handler of seed `seed` and obtain the first answer (to look at reproduction across runs)."
  (run (with_handlers [(state) (seeded-random-handler seed)] (RandomBytes 4))))


(deftest test-the-seeded-random-repeats-per-seed-and-order
  (<- same-a bytes (first-draw-of 7))
  (<- same-b bytes (first-draw-of 7))
  (<- other bytes (first-draw-of 8))
  (assert (= same-a same-b) "the first answer for the same seed differs per run")
  (assert (!= same-a other) "the first answer for a different seed is the same"))
