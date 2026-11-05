;;; The absence and failure effects and the boundary handlers (ADR-DOE-CORE-EFFECTS-003 stage 1 - Absent / Raise of doeff_core_effects._effects, and
;;; maybe, result, on-raise, absent-as of doeff_core_effects._outcomes).
;;;
;;;   * Absent has only an explanation text; Raise has a reason; neither inherits from the other; a Python exception cannot be placed in the reason of Raise
;;;   * maybe folds Absent into Nothing and result folds Raise into Err, without resuming the continuation (later lines do not run)
;;;   * The nesting order changes Result<Option> versus Option<Result>
;;;   * on-raise maps only a Raise of a matching type, passes a non-matching Raise outward, and does not catch Python exceptions
;;;   * absent-as ends the scope with the default value on a deep absence
;;;   * An Absent / Raise with no receiver stops as an unhandled effect (it is not silently turned into Nothing)
;;;   * maybe and result are applied as handler values with (maybe body) (ADR-DOE-API-002 R1); on-raise and absent-as are factories that return handlers
;;;     (ADR-DOE-API-001 R6)

(require doeff-hy.macros [deftest defk <- val on-raise absent-as])

(import dataclasses [dataclass fields])
(import hy)
(import pytest)
(import doeff [run with-handlers])
(import doeff.result [Nothing Some])
(import doeff [Err Ok UnhandledEffect])
(import doeff_core_effects [Absent Raise Ask]
        doeff_core_effects._effects [Resumption resumption-of])
(import doeff_core_effects [maybe result on-raise :as on-raise-fn absent-as :as absent-as-fn RaiseCase])


(defclass [(dataclass :frozen True)] Conflict []
  "Version loss (a failure reason for the tests)."
  #^ str detail)

(defclass [(dataclass :frozen True)] Unreachable []
  "Cannot reach the store (a failure reason for the tests)."
  #^ str detail)


(defk absent-then-note [seen]
  {:pre [(: seen list)] :post [(: % int)]
   :tags {:context "outcomes-test" :role "program"}}
  "Emit Absent, then add a mark to seen after it - if the receiver discards the continuation, the mark does not remain."
  (<- (Absent "no such row"))
  (.append seen "continued")
  1)

(defk raise-then-note [reason seen]
  {:pre [(: reason (| Conflict Unreachable)) (: seen list)] :post [(: % int)]
   :tags {:context "outcomes-test" :role "program"}}
  "Emit Raise(reason), then add a mark to seen after it."
  (<- (Raise reason))
  (.append seen "continued")
  1)

(defk succeeds []
  {:pre [] :post [(: % int)]
   :tags {:context "outcomes-test" :role "program"}}
  "A body that succeeds."
  7)

(defk raises-python []
  {:pre [] :post [(: % int)]
   :tags {:context "outcomes-test" :role "program"}}
  "A body that raises a Python exception (a sample of an implementation mistake)."
  (raise (ValueError "implementation mistake")))


(deftest test-absent-and-raise-are-distinct
  (assert (= (lfor f (fields Absent) f.name) ["why"]) "Absent has only an explanation text")
  (assert (= (lfor f (fields Raise) f.name) ["reason"]) "Raise has a reason")
  (assert (not (issubclass Raise Absent)))
  (assert (not (issubclass Absent Raise)))
  (assert (is (resumption-of Raise) Resumption.NEVER))
  (assert (is (resumption-of Absent) Resumption.ABSENT-AS-ONLY))
  (assert (is (resumption-of Ask) Resumption.REQUIRED) "an effect with no declaration requires resumption")
  (with [(pytest.raises TypeError :match "Python exception")]
    (Raise (ValueError "x")))
  (with [(pytest.raises TypeError)]
    (Absent "")))


(deftest test-maybe-folds-absent-into-nothing-and-drops-the-rest
  (val seen [])
  (<- folded (maybe (absent-then-note seen)))
  (assert (is folded Nothing) folded)
  (assert (= seen []) "maybe does not resume the continuation")
  (<- kept (maybe (succeeds)))
  (assert (= kept (Some 7)) kept))


(deftest test-result-folds-raise-into-err-and-drops-the-rest
  (val seen [])
  (<- folded (result (raise-then-note (Conflict "lost") seen)))
  (assert (= (repr folded) "Err(Conflict(detail='lost'))") folded)
  (assert (= seen []) "result does not resume the continuation")
  (<- kept (result (succeeds)))
  (assert (= (repr kept) "Ok(7)") kept))


(deftest test-nesting-order-chooses-the-shape
  ;; (result (maybe b)) : Result[Option[T]], (maybe (result b)) : Option[Result[T]] (R2)
  (val lost (Unreachable "unreachable"))
  (<- r-ok (result (maybe (succeeds))))
  (<- r-none (result (maybe (absent-then-note []))))
  (<- r-err (result (maybe (raise-then-note lost []))))
  ;; Ok / Err cannot be compared by value (Rust types), so compare their shapes by repr
  (assert (= (repr #(r-ok r-none r-err)) "(Ok(Some(7)), Ok(Nothing), Err(Unreachable(detail='unreachable')))")
          #(r-ok r-none r-err))
  (<- m-ok (maybe (result (succeeds))))
  (<- m-err (maybe (result (raise-then-note lost []))))
  (<- m-none (maybe (result (absent-then-note []))))
  (assert (= (repr #(m-ok m-err m-none)) "(Some(Ok(7)), Some(Err(Unreachable(detail='unreachable'))), Nothing)")
          #(m-ok m-err m-none)))


(deftest test-unhandled-absent-and-raise-stop-loudly
  ;; An Absent / Raise with no receiver stops as an unhandled effect (the runner does not place the boundary handlers by default)
  (with [(pytest.raises UnhandledEffect)]
    (run (absent-then-note [])))
  (with [(pytest.raises UnhandledEffect)]
    (run (raise-then-note (Conflict "c") []))))


(defk recovers [reason]
  {:pre [(: reason (| Conflict Unreachable))] :post [(: % str)]
   :tags {:context "outcomes-test" :role "program"}}
  "Map only Conflict to a business answer (Unreachable passes outward)."
  (<- answer (on-raise (raise-then-note reason [])
               (Conflict d) (+ "conflict: " d)))
  (str answer))


(deftest test-on-raise-maps-only-matching-reasons
  (<- mapped (recovers (Conflict "version")))
  (assert (= mapped "conflict: version") mapped)
  (<- passed (result (recovers (Unreachable "network"))))
  (assert (= (repr passed) "Err(Unreachable(detail='network'))") "a non-matching Raise passes to the outer result"))


(defk guarded-recovery [reason]
  {:pre [(: reason Conflict)] :post [(: % str)]
   :tags {:context "outcomes-test" :role "program"}}
  "A receiver whose guard misses moves on to the next receiver (a Raise of a non-matching shape passes outward)."
  (<- answer (on-raise (raise-then-note reason [])
               (Conflict d) :if (.startswith d "version") (+ "version: " d)
               (| (Unreachable d) (Conflict d)) (+ "other: " d)))
  answer)


(deftest test-on-raise-guards-and-or-patterns
  (<- first (guarded-recovery (Conflict "version lost")))
  (<- second (guarded-recovery (Conflict "key")))
  (assert (= #(first second) #("version: version lost" "other: key")) #(first second)))


(defclass [(dataclass :frozen True)] AppendRefused []
  "A refusal to add to a stream (has a field name spelled with `-` - a failure reason for the tests)."
  #^ str idempotency_key
  #^ str reason)


(defk keyed-recovery [reason]
  {:pre [(: reason AppendRefused)] :post [(: % str)]
   :tags {:context "outcomes-test" :role "program"}}
  "Write the field names of the receiver's type pattern in Hy spelling (`:idempotency-key`) - the expansion rewrites them to the attribute name idempotency_key."
  (<- answer (on-raise (Raise reason)
               (AppendRefused :idempotency-key key :reason why) (+ key ": " why)))
  answer)


(deftest test-on-raise-mangles-dashed-field-names
  ;; Before the fix, on-raise's match looked up the attribute idempotency-key, did not match, and the Raise escaped outward
  (<- mapped (keyed-recovery (AppendRefused "key-1" "duplicate")))
  (assert (= mapped "key-1: duplicate") mapped))


(defk python-error-through-on-raise []
  {:pre [] :post [(: % str)]
   :tags {:context "outcomes-test" :role "program"}}
  "Wrap a body that raises a Python exception with on-raise - the exception is not caught."
  (<- answer (on-raise (raises-python) (Conflict d) d))
  answer)


(deftest test-on-raise-does-not-catch-python-exceptions
  (with [(pytest.raises ValueError :match "implementation mistake")]
    (run (python-error-through-on-raise)))
  ;; A receiver naming an exception type cannot be made (RaiseCase refuses it - catch-all shapes and Python exceptions are not accepted: R7)
  (for [bad [object Exception ValueError BaseException]]
    (with [(pytest.raises TypeError)]
      (RaiseCase #(bad) (fn [r] (Some r))))))


(defk expansion-refusal [source]
  {:pre [(: source str)] :post [(: % str)]
   :tags {:context "outcomes-test" :role "judgment"}}
  "The error text from expanding source (an empty string if it passes) - to check in tests the rule of the macro that refuses at expansion time."
  (try
    (hy.eval (hy.read-many source))
    (return "")
    (except [e hy.errors.HyMacroExpansionError]
      (return (str e)))))


(deftest test-on-raise-refuses-catch-all-patterns-at-expansion
  (for [pattern ["_" "reason" "(Exception)" "(object)" "(BaseException e)" "(| (Conflict d) _)"]]
    (assert (in "cannot be accepted" (! (expansion-refusal (+ "(require doeff-hy.macros [on-raise]) (on-raise body " pattern " 0)"))))
            pattern))
  (assert (in "value expression" (! (expansion-refusal "(require doeff-hy.macros [on-raise]) (on-raise body (Conflict d) (! (f d)))")))
          "the mapped value is a value expression (effects cannot be used)"))


(defk defaults-when-absent [seen]
  {:pre [(: seen list)] :post [(: % int)]
   :tags {:context "outcomes-test" :role "program"}}
  "absent-as makes a deep defk's absence the end of the scope with the default value."
  (<- n (absent-as 0 (absent-then-note seen)))
  n)


(deftest test-absent-as-ends-the-scope-with-the-default-for-deep-absence
  (val seen [])
  (<- n (defaults-when-absent seen))
  (assert (= n 0) n)
  (assert (= seen []) "a deep absence does not resume the continuation (the default value is not passed to the deep lines)")
  (<- kept ((absent-as-fn 0) (succeeds)))
  (assert (= kept 7) kept))


(deftest test-the-boundaries-are-handlers
  ;; maybe and result, which have no settings, are applied to the program as handler values (ADR-DOE-API-002 R1; the answer of 2026-10-29),
  ;; and on_raise and absent_as, which take settings, are factories that return a handler when called, like core state() (ADR-DOE-API-001 R6).
  ;; All of them carry the mark _doeff_is_handler_fn.
  (val conflict-case (RaiseCase #(Conflict) (fn [r] (Some (+ "conflict: " r.detail)))))
  (val handlers {"maybe" maybe
                 "result" result
                 "on_raise" (on-raise-fn conflict-case)
                 "absent_as" (absent-as-fn 0)})
  (for [[name h] (.items handlers)]
    (assert (is (getattr h "_doeff_is_handler_fn" None) True) name)
    (assert (= h.__name__ name) h.__name__))
  (<- some ((get handlers "maybe") (succeeds)))
  (<- nothing ((get handlers "maybe") (absent-then-note [])))
  (<- ok ((get handlers "result") (succeeds)))
  (<- err ((get handlers "result") (raise-then-note (Conflict "lost") [])))
  (<- mapped ((get handlers "on_raise") (raise-then-note (Conflict "version") [])))
  (<- defaulted ((get handlers "absent_as") (absent-then-note [])))
  (assert (= (repr #(some nothing ok err mapped defaulted))
             "(Some(7), Nothing, Ok(7), Err(Conflict(detail='lost')), 'conflict: version', 0)")
          #(some nothing ok err mapped defaulted))
  ;; They can also be placed in a handler bundle (outer first): a maybe inside a result is Ok(Nothing).
  (<- stacked (with-handlers [result maybe] (absent-then-note [])))
  (assert (= (repr stacked) "Ok(Nothing)") stacked)
  ;; Putting a non-receiver where a receiver goes is refused at the call site.
  (with [(pytest.raises TypeError :match "handler factory")]
    (on-raise-fn conflict-case (succeeds))))


(deftest test-the-former-factory-calls-warn-and-answer-the-handler
  ;; The old call forms maybe() and result() return the handler itself for one version, emitting a DeprecationWarning (ADR-DOE-API-002 R3).
  (with [(pytest.warns DeprecationWarning :match "maybe has no settings")]
    (val called-maybe (maybe)))
  (assert (is called-maybe maybe))
  (with [(pytest.warns DeprecationWarning :match "result has no settings")]
    (val called-result (result)))
  (assert (is called-result result))
  (with [(pytest.warns DeprecationWarning)]
    (<- folded ((maybe) (absent-then-note []))))
  (assert (is folded Nothing) folded))


(deftest test-the-old-forms-are-refused
  ;; The old form that passes a program to a factory is refused with TypeError at the call site - the layer that translated it has been removed.
  ;; absent_as also refuses the expansion shape of the previous macro (the mark placed third).
  (val conflict-case (RaiseCase #(Conflict) (fn [r] (Some (+ "conflict: " r.detail)))))
  (with [(pytest.raises TypeError :match "handler factory")]
    (on-raise-fn (raise-then-note (Conflict "version") []) conflict-case))
  (with [(pytest.raises TypeError :match "positional argument")]
    (absent-as-fn 0 (absent-then-note [])))
  (with [(pytest.raises TypeError :match "positional argument")]
    (absent-as-fn 0 (absent-then-note []) None)))
