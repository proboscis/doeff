;;; The real answerer for heap freezing, gc-freeze-handler (ADR-DOE-CORE-EFFECTS-004) -- answers CollectAndFreeze with the process GC.
;;; This module is the only one that touches gc (the fake scripted_freeze.hy does not).
(require doeff-hy.macros [defhandler val])
(val MODULE-TAGS {:context "freeze" :role "foundation"})
(import gc)
(import doeff_core_effects._heap_effects [CollectAndFreeze])


(defhandler gc-freeze-handler
  "Answer CollectAndFreeze by collecting (gc.collect), then freezing (gc.freeze), and answering with the number of frozen objects (gc.get_freeze_count)."
  (CollectAndFreeze []
    (gc.collect)
    (gc.freeze)
    (resume (gc.get-freeze-count))))
