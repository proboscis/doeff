;;; Latest-value effects - one run places the latest value, and another run reads it without waiting for the writer (ADR-DOE-CORE-EFFECTS-004).
;;; Foundation vocabulary with no business terms.
;;;
;;;   PublishLatest  Places value. The key is exactly the value's type (type(value)) - replaces the previous value of the same type. Answer = None.
;;;   ReadLatest     Reads the latest value of type kind. None if nothing has been placed yet. A value of a child type cannot be read by the parent type (the key is the exact type).
;;;   AwaitLatest    Waits until the latest value of type kind becomes something other than seen (compared with is), and answers that value. If the current value already differs from seen, answers without waiting.
;;;                  So that a value placed by another run is read when it is placed, instead of being re-checked at fixed intervals (the probe's
;;;                  other run waits for the cross-section placed by the writer's run to change and answers). What waits is a bell (CreateExternalPromise, a promise that can be completed from outside the scheduler), which the placing side's
;;;                  PublishLatest rings. It has no upper limit - a caller that waits up to a limit races this effect's task against a clock task and
;;;                  Cancels the loser (a cancelled wait removes its bell). The wait does not stop the virtual clock (the park form of a promise outside the scheduler).
;;;                  Used under scheduled.
;;;
;;; The value placed should be immutable (e.g. a frozen record) - readers receive the reference as is, so rewriting its contents after placing is visible to readers.
;;;
;;; Answerers: process-latest-handler (process_latest.hy - the real one. One store per process shared by name, so different runs and threads read the same value) and
;;; memory-latest-handler (memory_latest.hy - state within a single run only). The two pass the same contract tests (tests/test_latest_contract.hy).
(require doeff-hy.macros [defeffect])


(defeffect PublishLatest
  "Place value as the latest value of its type (see the header comment)."
  {:fields [(: value object)]
   :answer None
   :tags {:context "latest" :role "foundation"}})


(defeffect ReadLatest
  "Read the latest value of type kind (None if there is none yet - see the header comment)."
  {:fields [(: kind type)]
   :answer (| object None)
   :tags {:context "latest" :role "foundation"}})


(defeffect AwaitLatest
  "Wait until the latest value of type kind becomes something other than seen (compared with is), and answer that value (see the header comment)."
  {:fields [(: kind type) (: seen object)]
   :answer (| object None)
   :tags {:context "latest" :role "foundation"}})
