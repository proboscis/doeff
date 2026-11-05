;;; scripted-freeze-handler, a fake responder for heap freezing (ADR-DOE-CORE-EFFECTS-004) -- answers CollectAndFreeze with a decided
;;; number without touching the GC. So that mocks and tests verify only the boundary that asks for freezing, and do not change the process's GC state.
(require doeff-hy.macros [defhandler val])
(val MODULE-TAGS {:context "freeze" :role "foundation"})
(import doeff_core_effects._heap_effects [CollectAndFreeze])


(defhandler scripted-freeze-handler [#^ int count]
  "Answer CollectAndFreeze with count (does not touch the GC)."
  ;; Why it stays an argument: the number answered is a scripted value decided per test, not a setting read with Ask.
  (CollectAndFreeze []
    (resume count)))
