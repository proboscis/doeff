;;; Tests for heap freezing. The contract (the real gc-freeze-handler and the fake scripted-freeze-handler pass the same deftest), and
;;; properties specific to the real one and to the fake. The interpreter assembly is in heap_contract_handlers.hy.
;;;
;;;   contract  the answer of CollectAndFreeze is an int >= 0
;;;   real      freezes something (the test process has live objects -- the answer is greater than 0 and frozen objects exist after the call. Objects
;;;            can be released after being frozen, so gc.get_freeze_count read later may be smaller than the answer)
;;;   fake      does not touch the GC (the frozen count does not change) and answers a fixed number
(require doeff-hy.macros [deftest <- val])
(import gc)
(import doeff [run with_handlers])
(import doeff_core_effects [CollectAndFreeze])
(import doeff_core_effects [gc-freeze-handler])
(import doeff_core_effects [scripted-freeze-handler])


(deftest test-a-freeze-answers-a-count
  {:interpreters ["gc-freeze" "scripted-freeze"]}
  (<- frozen int (CollectAndFreeze))
  (assert (>= frozen 0) (.format "frozen count is {!r}" frozen)))


(deftest test-the-real-freeze-freezes-live-objects
  (try
    (val frozen (run (with_handlers [gc-freeze-handler] (CollectAndFreeze))))
    (assert (> frozen 0) (.format "frozen count is {!r} although live objects exist" frozen))
    (assert (> (gc.get-freeze-count) 0) "no frozen objects after CollectAndFreeze")
    (finally
      (gc.unfreeze))))


(deftest test-the-scripted-freeze-leaves-the-collector-alone
  (val before (gc.get-freeze-count))
  (val frozen (run (with_handlers [(scripted-freeze-handler 3)] (CollectAndFreeze))))
  (assert (= frozen 3) (.format "fake answered {!r} (fixed count is 3)" frozen))
  (assert (= (gc.get-freeze-count) before) "fake changed the GC freeze state"))
