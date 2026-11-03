;;; Executable ADR (decision): expected "absence" and "failure" become 2 effects separated by meaning — Absent (the Maybe side) and Raise (the Result side).
;;; The producing side performs Absent / Raise from the start (the same plain style as Koka), and the x in (<- x (risky-call)) is only the success value T. Only where
;;; the answer should be received as a Maybe / Result value is it folded by a boundary handler (maybe, result, on-raise, absent-as). Handlers that answer effects answer with values,
;;; and the conversion to absence and failure is done by <- on the caller's side following the defeffect declaration. The effects a function performs are inferred, not written by hand.
;;;
;;; The points at issue are in the :problem facts (the proposal was adopted at point 9). Status: decided. Implementation proceeds in stages 1 to 5 (R16); this version implements stages 1 and 2:
;;;   stage 1 = the Absent / Raise effects, the boundary handlers, defhandler's ending clause (finish), and the check of how clauses end (R15)
;;;   stage 2 = defeffect's answer declaration (:absent / :failure / :value) and the <- / ! conversion (including :absent and the shape that opens Result / Option values)
;;; Stage 3 (attaching declarations to doeff-records' effects) and stage 5 (rewriting agora's callers) are switched per effect in the same change
;;; — the procedure is R14.
;;;
;;; Revision history (2026-09-27, while writing): the initial proposal declared the 'look only at success' range as a lexical scope (macro success-only).
;;; In response to point 7 (is success-only unnecessary?), the f143ee92 seat decided to remove success-only (a reversible decision — R3).
;;; success-only's aim, that the meaning of the same line does not change with the caller, is met by the producing side performing Absent / Raise from the start.
;;; 2026-09-28: from proposal to decision. Added the decision, the stage plan (R16), the assignment of the 4 kinds of failure (R17), and how handler clauses end (R15 —
;;; from the concern about forgetting to resume), and recorded the reversible decisions made in implementing stages 1 and 2 in the last interpretation of :context.
;;;
;;; Placement: the effect vocabulary (Absent, Raise) and the boundary handlers belong in doeff-core-effects, the same place as Try, so this is the
;;; third ADR-DOE-CORE-EFFECTS. The <- conversion, defeffect's declaration, and defhandler's ending clause are doeff-hy's job; effect inference is doeff-effect-analyzer's; rules are
;;; doeff-linter's; they are listed in :scope.
;;;
;;; Rollback: reverting the stage 2 commit returns <- to the previous (yield e), and defeffect's :absent / :failure / :value become unknown keys.
;;; Reverting the stage 1 commit removes Absent / Raise, the boundary handlers, and finish, and defhandler's clause check returns to its pre-proposal shape.
;;; No effect has a declaration yet (before stage 3), so neither revert changes the behavior of existing callers.
;;;
;;; ---------------------------------------------------------------------------
;;; Writing examples (the answer to point 5 — the shape after stage 3. revise-input is an agora-controllers function)
;;; ---------------------------------------------------------------------------
;;;
;;; Before — controllers/protocol/conversation_input.hy:73-91 on agora-controllers mainline eb2abb27 (as is, with its strings translated):
;;;
;;;   (defk revise-input [revise]
;;;     {:pre [(: revise InputRevise)] :post [(: % (| WriteLanded WriteConflict WriteUnreachable))]
;;;      :effects [ReadRow PutRow ReadPlacedInput] :tags {:context "conversation-input" :role "protocol"}}
;;;     "To copy the revision write: …"
;;;     (<- body (| TypedRow Missing Unreachable) (read-typed PLACED-INPUT-TYPE #(revise.id)))
;;;     (match body
;;;       (Unreachable) (return (WriteUnreachable :detail body.detail))
;;;       (Missing) (return (WriteConflict :detail (+ revise.id " has no body row")))
;;;       _ None)
;;;     (<- placed (| PlacedInput None) (ReadPlacedInput :agent body.value.agent :ref revise.id))
;;;     (when (or (!= body.version revise.version) (is placed None) (!= placed.state LedgerState.PENDING))
;;;       (return (WriteConflict :detail (+ revise.id " changed after it was read"))))
;;;     (<- written (| BodyRecorded BodyUnreachable BodyConflict) (revise-placed-input revise.id revise.text revise.edit-id))
;;;     (match written
;;;       (BodyRecorded) (WriteLanded)
;;;       (BodyConflict) (WriteConflict :detail written.detail)
;;;       (BodyUnreachable) (WriteUnreachable :detail written.detail)))
;;;
;;; After — the shape implemented in stages 1 and 2 (write only the success path, and fold failures into the business answer type with a single on-raise):
;;;
;;;   (defk revise-input [revise]
;;;     {:pre [(: revise InputRevise)] :post [(: % (| WriteLanded WriteConflict WriteUnreachable))]
;;;      :tags {:context "conversation-input" :role "protocol"}}
;;;     "To copy the revision write: …"
;;;     (<- answer
;;;         (on-raise
;;;           (do!
;;;             (<- body (read-typed PLACED-INPUT-TYPE #(revise.id))
;;;                 :absent (Conflict (+ revise.id " has no body row")))
;;;             (<- placed (ReadPlacedInput :agent body.value.agent :ref revise.id)
;;;                 :absent (Conflict (+ revise.id " changed after it was read")))
;;;             (unless (and (= body.version revise.version) (= placed.state LedgerState.PENDING))
;;;               (<- (Raise (Conflict (+ revise.id " changed after it was read")))))
;;;             (<- (revise-placed-input revise.id revise.text revise.edit-id))
;;;             (WriteLanded))
;;;           (Conflict d)        (WriteConflict :detail d)
;;;           (BodyConflict d)    (WriteConflict :detail d)
;;;           (Unreachable d)     (WriteUnreachable :detail d)
;;;           (BodyUnreachable d) (WriteUnreachable :detail d)))
;;;     answer)
;;;
;;;   - The type seen from outside is the same as Before: revise-input : InputRevise → Program[WriteLanded | WriteConflict | WriteUnreachable] ! {ReadRow, PutRow,
;;;     ReadPlacedInput} (Raise does not remain because on-raise folds all of it). The hand-written :effects is replaced by inference (R13) and disappears.
;;;   - revise-placed-input reports losing on version and being unreachable as BodyConflict and BodyUnreachable, so on-raise catches those 2 as well. If it did not,
;;;     Raise[BodyConflict | BodyUnreachable] would leak outward and the type seen from outside would change (inference shows this).
;;;   - Conflict is a failure type for this example (it carries a description text d), distinct from doeff-records' Conflict (current).
;;;   - The :absent receiver is placed inside on-raise (on the bind line), so the Raise it performs reaches on-raise (it does not hit R4's pitfall).
;;;   - The body of on-raise is a single Program expression (wrap multiple lines in do!). maybe and result are values of handlers without settings (ADR-DOE-API-002 R1), and an effect
;;;     with a declaration may be passed directly, as in (maybe (ReadRow …)) (it is opened and then folded).
;;;
;;; An example that writes no boundary in the business core — notify-landed (a name not on the mainline. The close real counterparts are post-notice and notify-outcome in
;;; controllers/core/land_notice.hy:53-70, which currently map PostMessage's answers Accepted / Rejected / Unavailable to counts with a match every time):
;;;
;;;   (defk notify-landed [outcome recipient]
;;;     {:pre [(: outcome LandOutcome) (: recipient str)] :post [(: % Accepted)]
;;;      :tags {:context "land-notice" :role "program"}}
;;;     "To post one landing outcome to one recipient. Refusals and unreachability go outward as Raise, and how to handle them is left to the composition handlers."
;;;     (<- request (notice-request outcome recipient))
;;;     (<- (PostMessage request)))
;;;
;;;   - Inferred type: notify-landed : LandOutcome × str → Program[Accepted] ! {Raise[Rejected | Unavailable], PostMessage}
;;;     (when PostMessage's declaration makes Accepted success and Rejected and Unavailable failures).
;;;   - The caller iterates item by item with Traverse, and per-item failure handling is decided by Traverse's handler (chosen at composition). Production = skip Rejected
;;;     and add it to the counts, for Unavailable end that cycle and reread from the same position. Simulation and tests = stop at the first Raise and the test looks at the reason.
;;;     Skipping one item needs a per-item scope, and Traverse's handler holds it (currently it catches Fail per item; catching Raise is added
;;;     at implementation time).

(require doeff-adr.macros [defadr rule law])
(import doeff-adr.macros [fact interpretation counterexample])
(require doeff-hy.macros [deftest defk deff defeffect <- val var do! on-raise absent-as])
(require doeff-hy.handle [defhandler])
(require doeff-hy.record [defrecord])
(import contextlib [suppress])
(import dataclasses [dataclass fields])
(import hy)
(import pytest)
(import typing [Never get-args])
(import pathlib [Path])
(import doeff [run with-handlers Ok Err Some Nothing Pass Try EffectBase DoExpr K])
(import doeff [handler :as make-handler])
(import doeff [UnhandledEffect])
(import doeff-core-effects [try-handler])
(import doeff-core-effects [Absent Raise]
        doeff-core-effects._effects [Resumption resumption-of])
(import doeff-core-effects [Raise :as ProbeAliasedRaise])  ; R15's counterexample: an alias that cannot be recognized as Raise by name
(import doeff-core-effects [maybe result RaiseCase]
        doeff-core-effects._outcomes [open-bind Outcomes])
(import doeff-records [ReadRowAnswer Row Missing Unreachable])
(import doeff-traverse [Fail])
(import doeff-traverse [normalize-to-none])
(import doeff-hy.handle [_check-clause-terminates])
(import doeff-hy._declarations [EFFECT-KEYS])
(import doeff-hy [ClauseEndingError])


(val REPO-ROOT (. (Path __file__) parent parent parent))


;; ---------------------------------------------------------------------------
;; Live probes — demonstrations of shapes that can and cannot be written with the current VM and macros (the facts R1, R2, R4, R5, R13 stand on).
;; ---------------------------------------------------------------------------

(defeffect ProbeAbsent
  "A sample of expected absence (the shape at the time of the proposal — an ordinary effect. R1's Absent is doeff_core_effects._effects.Absent)."
  {:fields [(: why str)]
   :answer Never
   :tags {:context "doeff-core-effects-adr" :role "intent"}})

(defeffect ProbeRead
  "A sample read that a handler answers with a value (R4 — to show that performing an effect inside a handler clause jumps over the caller's scope)."
  {:answer str
   :tags {:context "doeff-core-effects-adr" :role "intent"}})

(defk probe-ending [value]
  {:pre [(: value (| str (type Nothing)))] :post [(: % (| str (type Nothing)))]
   :tags {:context "doeff-core-effects-adr" :role "program"}}
  "A Program returning the value with which a receiver ends the scope without resuming — so the receiver's @do function ends the scope with that value."
  value)

;; Cannot be a defk: defk reads the arguments (effect k) as a handler and refuses. A handler is only a @do function marked with @handler (ADR-DOE-CORE-003), so
;; a @do function sample shows that the VM accepts a receiver that does not resume (with defhandler it can be written as (finish value) — R15).
(defn [make-handler] probe-maybe-handler [#^ EffectBase effect #^ K k]
  "A sample of R2's maybe receiver: on ProbeAbsent it does not resume and ends the scope with Nothing (the continuation is discarded). Others are passed outward."
  (if (isinstance effect ProbeAbsent)
      (return (yield (probe-ending Nothing)))
      (yield (Pass effect k))))

;; Cannot be a defk: same as probe-maybe-handler (a sample showing that the VM accepts a receiver that does not resume).
(defn [make-handler] probe-outer-handler [#^ EffectBase effect #^ K k]
  "A sample receiver placed outside the foundation handler: on ProbeAbsent it ends the scope with a marker text — to tell which receiver caught it."
  (if (isinstance effect ProbeAbsent)
      (return (yield (probe-ending "the outer one caught it")))
      (yield (Pass effect k))))

(defhandler probe-read-handler
  "A sample of R4's pitfall: a handler answering ProbeRead performs ProbeAbsent inside its clause (a shape that must not be used)."
  (ProbeRead []
    (<- (ProbeAbsent "performed inside a handler clause"))
    (resume "never reached")))

(defk probe-reads-then-continues [seen]
  {:pre [(: seen list)] :post [(: % int)]
   :tags {:context "doeff-core-effects-adr" :role "program"}}
  "Performs ProbeAbsent and then appends a marker to seen — if the receiver discards the continuation, no marker remains."
  (<- (ProbeAbsent "no row"))
  (.append seen "continued")
  1)

(defk probe-reads-a-row []
  {:pre [] :post [(: % str)]
   :tags {:context "doeff-core-effects-adr" :role "program"}}
  "A body that reads one ProbeRead and returns it (the caller in R4's sample)."
  (<- row (ProbeRead))
  row)

(defk probe-declared-effects []
  {:pre [] :post [(: % int)]
   :effects [ProbeAbsent]
   :tags {:context "doeff-core-effects-adr" :role "program"}}
  "A sample with :effects written by hand in the head dict (R13 — if written, it stays in __doeff_effects__)."
  (<- (ProbeAbsent "declared"))
  1)

(defk probe-boom []
  {:pre [] :post [(: % int)]
   :tags {:context "doeff-core-effects-adr" :role "program"}}
  "A Program that raises a Python exception (R11 — a sample of an exception as an implementation error)."
  (raise (ValueError "boom")))

(defk probe-one []
  {:pre [] :post [(: % int)]
   :tags {:context "doeff-core-effects-adr" :role "program"}}
  "A Program that succeeds (a sample Try folds into Ok)."
  1)

(defk probe-try [program]
  {:pre [(: program DoExpr)] :post [(: % (| Ok Err))]
   :tags {:context "doeff-core-effects-adr" :role "program"}}
  "Wraps program in Try and returns the answer with exceptions folded into a Result value."
  (<- answer (Try program))
  answer)

(defk probe-fails-then-continues []
  {:pre [] :post [(: % tuple)]
   :tags {:context "doeff-core-effects-adr" :role "program"}}
  "Performs doeff-traverse's Fail and continues with the resumed value."
  (<- substitute (Fail (ValueError "broken input")))
  #("continued" substitute))


;; ---------------------------------------------------------------------------
;; Stage 1 and 2 probes — the implemented Absent / Raise, boundary handlers, <- conversion, and how handler clauses end
;; ---------------------------------------------------------------------------

(defrecord ProbeFound
  #^ str value)

(defrecord ProbeGone
  #^ str key)

(defrecord ProbeDown
  #^ str detail)

(defrecord ProbeConflict
  #^ str detail)

(defeffect ProbeDeclaredRead
  "A read whose answer is declared as split into success (ProbeFound), absence (ProbeGone), and failure (ProbeDown) (R5)."
  {:fields [(: key str)]
   :answer (| ProbeFound ProbeGone ProbeDown)
   :absent [ProbeGone]
   :failure [ProbeDown]
   :tags {:context "doeff-core-effects-adr" :role "intent"}})

(defeffect ProbePlainRead
  "A read without a declaration (the answer type is the same union) — <- binds the answer as is."
  {:fields [(: key str)]
   :answer (| ProbeFound ProbeGone ProbeDown)
   :tags {:context "doeff-core-effects-adr" :role "intent"}})

(val PROBE-TABLE {"a" (ProbeFound :value "A") "down" (ProbeDown :detail "network is down")})

(defhandler probe-rows
  "A foundation handler that answers reads from PROBE-TABLE with values (absence and failure are also answer values — R4)."
  {:tags {:context "doeff-core-effects-adr" :role "foundation"}}
  (ProbeDeclaredRead [key] (resume (.get PROBE-TABLE key (ProbeGone :key key))))
  (ProbePlainRead [key] (resume (.get PROBE-TABLE key (ProbeGone :key key)))))

(defk probe-read-value [key]
  {:pre [(: key str)] :post [(: % str)]
   :tags {:context "doeff-core-effects-adr" :role "program"}}
  "Binds one read with a declaration — x is only the success type (absence and failure escape as Absent / Raise)."
  (<- found (ProbeDeclaredRead key))
  found.value)

(defk probe-read-plain [key]
  {:pre [(: key str)] :post [(: % (| ProbeFound ProbeGone ProbeDown))]
   :tags {:context "doeff-core-effects-adr" :role "program"}}
  "Binds one read without a declaration — returns the answer as is."
  (<- answer (ProbePlainRead key))
  answer)

(defk probe-read-or-conflict [key]
  {:pre [(: key str)] :post [(: % str)]
   :tags {:context "doeff-core-effects-adr" :role "program"}}
  "(e) Throws an absence performed within this bind (including one inside a deeper defk) as a ProbeConflict failure (<-'s :absent)."
  (<- value (probe-read-value key) :absent (ProbeConflict :detail (+ key " has no row")))
  value)

(defk probe-absent-then-note [seen]
  {:pre [(: seen list)] :post [(: % int)]
   :tags {:context "doeff-core-effects-adr" :role "program"}}
  "Performs Absent and then appends a marker to seen — if the boundary handler discards the continuation, no marker remains."
  (<- (Absent "no row"))
  (.append seen "continued")
  1)

(defk probe-raise-then-note [seen]
  {:pre [(: seen list)] :post [(: % int)]
   :tags {:context "doeff-core-effects-adr" :role "program"}}
  "Performs Raise and then appends a marker to seen."
  (<- (Raise (ProbeDown :detail "unreachable")))
  (.append seen "continued")
  1)

(defk probe-raises-python []
  {:pre [] :post [(: % str)]
   :tags {:context "doeff-core-effects-adr" :role "program"}}
  "(d) Wraps a body that raises a Python exception in on-raise — the exception is not caught."
  (<- answer (on-raise (probe-boom) (ProbeConflict :detail d) d))
  (str answer))

(defhandler probe-raising-read-handler
  "(b) A sample of R4's pitfall (a shape that must not be used): a handler answering ProbeRead performs Raise inside its clause."
  {:tags {:context "doeff-core-effects-adr" :role "foundation"}}
  (ProbeRead []
    (<- (Raise (ProbeConflict :detail "performed inside a handler clause")))
    (resume "never reached")))

(defk probe-direct-default [key]
  {:pre [(: key str)] :post [(: % str)]
   :tags {:context "doeff-core-effects-adr" :role "program"}}
  "An absence from <- in absent-as's literal text resumes with the default, and the continuation runs (R8)."
  (<- value (absent-as (ProbeFound :value "default") (do! (<- found (ProbeDeclaredRead key)) (+ "continued: " found.value))))
  value)

(defk probe-deep-default [key]
  {:pre [(: key str)] :post [(: % str)]
   :tags {:context "doeff-core-effects-adr" :role "program"}}
  "An absence deep inside a called defk does not resume; the scope ends with the default (R8)."
  (<- value (absent-as "default" (do! (<- got (probe-read-value key)) (+ "continued: " got))))
  value)

(defk probe-written-absent-default []
  {:pre [] :post [(: % str)]
   :tags {:context "doeff-core-effects-adr" :role "program"}}
  "A directly written (<- (Absent …)) does not resume; the scope ends with the default (R8)."
  (<- value (absent-as "default" (do! (<- (Absent "written absence")) "continued")))
  value)

(defhandler probe-deadline [limit]
  "A sample clause that deliberately cuts off an ordinary effect (R15 — with a :finish-reason reason)."
  {:tags {:context "doeff-core-effects-adr" :role "foundation"}}
  ;; Why it stays an argument: to vary the cut-off threshold per check (reading it with Ask would add to the check's composition)
  (ProbeRead []
    :finish-reason "stop the whole run when the threshold is exceeded (a timeout example)"
    (if (> limit 0) (resume "read ok") (finish "cut off"))))

(defhandler probe-aliased-raise-resumer
  "R15's counterexample (a shape that must not be used): a clause that resumes a Raise imported under an alias — it cannot be recognized by name, so it is refused when first installed on a body."
  {:tags {:context "doeff-core-effects-adr" :role "foundation"}}
  (ProbeAliasedRaise [reason] (resume 0)))

(defhandler probe-swallowing-handler
  "R15's counterexample (a shape that must not be used): a resume inside a with that suppresses exceptions — it passes at expansion, but exits without resuming, so it is made an error at run time."
  {:tags {:context "doeff-core-effects-adr" :role "foundation"}}
  (ProbeRead [] (with [(suppress ValueError)] (resume (int "not a number")))))

(defk probe-expansion-refusal [source]
  {:pre [(: source str)] :post [(: % str)]
   :tags {:context "doeff-core-effects-adr" :role "judgment"}}
  "The error text when source is expanded (an empty string if it passes) — so a check can look at macro rules that refuse at expansion."
  (try
    (hy.eval (hy.read-many source))
    ""
    (except [e hy.errors.HyMacroExpansionError]
      (str e))))


(defadr ADR-DOE-CORE-EFFECTS-003
  :title "Expected 'absence' and 'failure' become 2 effects separated by meaning — Absent (the Maybe side, no content, only a description text) and Raise (the Result side, carries a reason). The producing side performs Absent / Raise from the start, and the x in (<- x (risky-call)) is only the success value T. Only where the answer should be received as a Maybe / Result value is it folded by a boundary handler (maybe, result, on-raise, absent-as), and the nesting order determines the combined type. Handlers that answer effects answer with values, and the conversion to absence and failure is done by <- on the caller's side following the defeffect declaration. The effects a function performs are not written by hand but inferred by doeff-effect-analyzer, and the function's type is shown as 'return value + set of remaining effects'"
  :status "accepted"
  :scope ["docs/adr/defadr_doeff_core_effects_003_absent_and_raise.hy"
          "packages/doeff-core-effects/doeff_core_effects/_effects.py"
          "packages/doeff-core-effects/doeff_core_effects/_outcomes.py"
          "packages/doeff-core-effects/doeff_core_effects/_handlers.py"
          "packages/doeff-core-effects/tests/test_outcomes.hy"
          "packages/doeff-core-effects/tests/test_outcome_binds.hy"
          "packages/doeff-hy/src/doeff_hy/macros.hy"
          "packages/doeff-hy/src/doeff_hy/_declarations.hy"
          "packages/doeff-hy/src/doeff_hy/handle.hy"
          "packages/doeff-hy/src/doeff_hy/_outcome_forms.hy"
          "packages/doeff-hy/src/doeff_hy/_clause_endings.hy"
          "packages/doeff-hy/src/doeff_hy/_binding_forms.py"
          "packages/doeff-effect-analyzer"
          "packages/doeff-records"
          "packages/doeff-traverse"
          "packages/doeff-linter"]
  :problem
    [(fact
       "Points 1 to 3: (1) In code that mainly looks only at the success case, whether the caller wants to receive a Result / Maybe value or only the inner value changes with context. Is a way of writing needed that says, as a scope, 'within this scope look only at success'? (2) Optional and Result are usually combined. Should a Fail effect or a Nothing effect be performed — what in algebraic effects corresponds to Fail in a Maybe context? (3) Should it be a shape that performs Absent or Fail and decides how to receive it with a scoped handler?")
     (fact
       "Point 4 (origin of R13): the current defk does not show that it performs Fail or Absent; its return type is just an Option or a Result. Whether a function performs Absent or Fail must be readable. One option is to give defk an :effects field, but its content changes depending on what is called inside.")
     (fact
       "Points 5 to 6 (origin of the writing examples and the types): (5) What does code look like with Fail and Absent? (6) This shape resembles try-catch but can be swapped. If Fail / Absent can be handled in one's own style even with mixed writing, is success-only's return type Result[T], or T that performs Absent / Fail?")
     (fact
       "Points 7 to 8 (origin of the decision to remove success-only and of R9): (7) If success-only looks only at the success case, is success-only unnecessary in the first place, so that writing (<- x (dangerous_call)) (<- y (maybe_1)) suffices? (8) Is delimiting a scope wanted only when something should be done in the failure case?")
     (fact
       "Point 9 (origin of making this ADR a decision): it was decided to proceed with this proposal and to fix doeff-record to perform these effects too. Summary of the discussion: of the 4 recurring kinds of failure (Unreachable, Missing, Refused, Conflict), Unreachable and Refused become Raise, Missing becomes the Absent effect, and Conflict stays a value. Effects are one more channel of data that stacks up. In the code body the 4 kinds become effects rather than return value types, and become data at the boundary with the outside world (handler answers, records) and at the 'want to do something' boundaries (maybe, result, on-raise). HTTP status codes are also mapped to the 4 kinds of failure effects in one place in the translation layer.")
     (fact
       "Point 10 (origin of R15): in the case where a handler ends, care is needed not to forget resume by mistake — the coordinator made it a condition: an ending clause can be written only with an explicit operation (finish); if not every path of a clause ends in one of resume, finish, or transfer it is an error at expansion; check against each effect's resumption treatment; cutting off an ordinary effect only with a reason annotation; do not silently stop at run time"
       :evidence "the coordinator's additional request (evening of 2026-09-27)")
     (fact
       "doeff.result has Result and Maybe values: Ok / Err are re-exported from Rust's doeff_vm (Ok has value, Err has error and captured_traceback). Some is an immutable value with value, Nothing is a single value that evaluates as false, and Maybe = Some | Nothing. They are also exported under the same names from doeff's top level. Ok / Err cannot be compared by value (Ok(1) == Ok(1) is false — confirmed 2026-09-28)."
       :evidence "doeff/result.py:19 (Ok / Err), :22-54 (Some), :57-85 (Nothing — __bool__ at :78-79 is False), :94 (Maybe), doeff/__init__.py:52-56")
     (fact
       "Try(program) is an effect that folds a Python exception raised inside program into Err and success into Ok, caught by try_handler. try_handler reattaches the inner handlers, runs program, and returns Err(e) on except Exception — it is the entry point that maps exceptions to values, and it does not look at failures inside answers returned as values (Unreachable, etc.)."
       :evidence "packages/doeff-core-effects/doeff_core_effects/_effects.py (Try), packages/doeff-core-effects/doeff_core_effects/_handlers.py (_try_handler — except Exception → Err), doeff/__init__.py:68")
     (fact
       "doeff-records' effects follow the rule of returning failures as values, listed in the README table column 'failure answers (returned as values)'. Only composition errors and implementation errors are raised as exceptions. ReadRow's answer is Row | Missing | Unreachable — success (Row), expected absence (Missing = no row), and failure (Unreachable = cannot reach the store) are listed in one union, and which is success and which is failure is not written in the type. read-typed in the per-table type layer also returns Missing and Unreachable to the caller as is."
       :evidence "packages/doeff-records/README.md:11-19, :39, packages/doeff-records/src/doeff_records/_values.hy:279-280 (Missing), :371-373 (Unreachable), :387 (ReadRowAnswer), effects.hy:39-45 (ReadRow), typed.hy:114-118 (read-typed)")
     (fact
       "Callers that want to write only the success path split absence and failure by hand every time they receive an answer: on agora-controllers mainline eb2abb27, isinstance checks for Missing / Unreachable appear in 43 files on 74 lines. In revise-input (controllers/protocol/conversation_input.hy:73-91), of its 19 lines, 2 matches and 1 when are branches on absence and failure, and :effects [ReadRow PutRow ReadPlacedInput] is written by hand (:75)."
       :evidence "git grep -P 'isinstance \\S+ (#?\\()?[^)]*(Missing|Unreachable)' origin/main -- '*.hy' in agora-controllers (2026-09-27), git show origin/main:controllers/protocol/conversation_input.hy")
     (fact
       "The answer of revise-placed-input, which revise-input calls, is BodyRecorded | BodyUnreachable | BodyConflict (each carries a detail text), and the answer of ReadPlacedInput is a PlacedInput or None (no row). land_notice's post-notice maps PostMessage's answers Accepted / Rejected / Unavailable to counts with match. No function named notify-landed exists on the mainline or in history (it is the name in the writing example)."
       :evidence "agora-controllers origin/main eb2abb27: controllers/messaging/placed_input.hy:84-101, :167-168, controllers/scheduling/input_port.hy:130-131, controllers/core/land_notice.hy:53-70, git log --all -S 'notify-landed' (0 hits)")
     (fact
       "doeff already has an effect named Fail (doeff-traverse). Its meaning is 'a notice of failure', and a handler can resume the continuation with a substitute value — normalize_to_none resumes Fail with None, and fail_handler rethrows it as an exception. try_call turns a Python function's exception into Fail. Traverse leaves per-item failure handling to the handler, and sequential turns an unhandled Fail inside Traverse into the failure of that one item."
       :evidence "packages/doeff-traverse/doeff_traverse/_effects.py:20-38 (Fail), :40-51 (Traverse — Handler decides … error strategy per item), handlers.py:18-27 (sequential), :502-517 (fail_handler), :520-533 (normalize_to_none), helpers.py:9-22 (try_call)")
     (fact
       "At the time of the proposal (2026-09-27), the keys accepted by defeffect's head dict were :fields, :answer, :tags, :pre, and the answer only remained in __doeff_answer__ as the single type of :answer (a union is fine) — there was no place to declare which types of the union are success, absence, or failure (stage 2 added :absent / :failure / :value — R5). doeff-records' effects are written not with defeffect but with plain defclass."
       :evidence "packages/doeff-hy/src/doeff_hy/_declarations.hy (EFFECT-KEYS, defeffect-form), packages/doeff-records/src/doeff_records/_effects.hy:39")
     (fact
       "An effect performed inside a handler clause goes to handlers outside that handler, skipping receivers placed between the body and that handler — confirmed by this ADR's tests effect-performed-in-a-handler-skips-the-callers-scope (a probe from the time of the proposal) and raise-in-a-handler-clause-skips-the-bodys-result (with the implemented Raise and result)."
       :evidence "this ADR's probe-read-handler, probe-raising-read-handler and tests")
     (fact
       "At the time of the proposal (2026-09-27), a defhandler clause was refused with a SyntaxError at expansion unless every branch reached one of resume / transfer / reperform / raise, and a clause that discards the continuation and ends with a value could not be written (stage 1 added (finish value) — R15). defk reads an argument list of the shape (effect k) as a handler and refuses it. The VM allows a handler that returns a value without resuming and ends the scope with that value (the continuation is discarded). Even when a handler exits returning None without resuming, the VM silently ends the scope with None (confirmed 2026-09-28 — the reason for R15's run-time check)."
       :evidence "packages/doeff-hy/src/doeff_hy/handle.hy (_terminates, _check-clause-terminates), packages/doeff-hy/src/doeff_hy/macros.hy (_reject-handler-signature), this ADR's tests handler-without-resume-ends-the-scope and defhandler-clause-must-end-explicitly, run's answer (None) when a @do handler returns None, via uv run python")
     (fact
       "The head dicts of defk, deff, defp, and defhandler already accept :effects (a list of names of effect types that the definition performs, optional) and keep it in __doeff_effects__ (None if not written). No tool reads this declaration and compares it to inference (only doeff-hy's tests read it)."
       :evidence "packages/doeff-hy/src/doeff_hy/_declarations.hy:1-13, :26 (CONTRACT-KEYS), :59-70 (effects-form), git grep '__doeff_effects__' (hits only in declarations.hy and packages/doeff-hy/tests/test_declarations.py)")
     (fact
       "doeff-effect-analyzer's Python entry point collects the effects a Program's function performs, following callees transitively. It reads Hy after macro expansion, reads the effects a handler catches from defhandler clauses (isinstance branches), and reports what it cannot follow as unresolved rather than silently dropping it. Programs passed as arguments (Spawn children, etc.) are reported separately as carried, and the caller decides whether to fold them. The module cache is in-process only. Rust's Hy reading (extract_effect_from_bind in hy_analyzer.rs) reads the <- shape only with 2 to 4 elements and does not know a trailing :absent <failure> (fixed in stage 4)."
       :evidence "packages/doeff-effect-analyzer/README.md:7-58, python/doeff_effect_analyzer/program_effects.py:159-180 (_MODULE_CACHE), :590 (analyze_program), src/hy_analyzer.rs:418-438")
     (fact
       "ADR-DOE-CLUSTER-001 decides that the job runner adds no handlers at all (R2), and that the recording and replay handlers are placed between the translating handlers inside the Program and the foundation handlers, recording the answers at the boundary (R5, R5b)."
       :evidence "docs/adr/defadr_doeff_cluster_001_job_accepts_a_program.hy:102, :108-109")
     (fact
       "The code-quality policy requires not silently converting unknown values to empty strings, success, or default states."
       :evidence "~/repos/code-quality/docs/policy.md:24-25")
     (fact
       "Measurements at the time of implementing stages 1 and 2 (2026-09-28): the current VM makes yielding Ok / Err / Some / Nothing an error (expected DoExpr or EffectBase) — the shape where <- opens values only affects paths that used to be errors. The open-bind call added to <-'s expansion (including the import inside the expansion) costs about 0.2µs per bind. All 115 doeff files with handlers pass the new clause check (R15), and on the agora-controllers mainline 1 place is refused (the AwaitRequest clause in controllers/protocol/webapp_reception.hy — a match listing all cases by type has no final _) (add a one-line `_ (raise …)` when upgrading doeff)."
       :evidence "the VM error when firing yield Ok(1) via uv run python, timeit (2026-09-28), a scan expanding all of doeff's .hy and all of agora-controllers origin/main's .hy with hy_compile only (2026-09-28)")]
  :context
    [(interpretation
       "In the literature, a failure without content belongs to the Maybe side, and a failure with a reason to the Result (Either) side. Haskell's MonadFail fail (takes a description text but discards it in Maybe and becomes Nothing) and Alternative's empty are the former; Plotkin and Pretnar's exception raise, Koka's throw, and Haskell's throwError are the latter. The answer to the question 'what corresponds to Fail in a Maybe context' (point 2) is, in the literature, that fail / empty are exactly the Maybe side, and the Result side is called raise / throw. To avoid confusion this ADR calls the Maybe side Absent and the Result side Raise, and uses the name Fail for neither (doeff-traverse's Fail already has a different, resumable meaning).")
     (interpretation
       "With a single effect (only Nothing, or only Fail), the receiver cannot tell absence from failure, and cannot build a Maybe inside a Result versus a Result inside a Maybe. Splitting into 2 effects means the combined type is determined only by the nesting order of the receiving handlers — what monad transformer stacking order (MaybeT (Either e) versus ExceptT e Maybe) says can be said with handler order.")
     (interpretation
       "Whether an answer is received as a value or as its content changes with the caller's context (point 1). The initial proposal expressed this with a declaration of a 'look only at success' scope (success-only), but then the type of the same line differs inside and outside the scope. If the producing side performs Absent / Raise from the start, (<- x (risky-call)) has exactly one meaning, 'T, or escape', whoever calls it, and the line's type does not change with the caller. Only callers that want a value fold it on the spot with a boundary handler (points 7 and 8). The same as Koka's style.")
     (interpretation
       "A handler that answers an effect is placed outside the caller's body (at the composition root). Between the body and the handler are the caller's maybe / result. If Absent / Raise is performed inside a handler clause, it goes outside the handler and skips the caller's maybe / result (confirmed by the fact and the test). So the conversion to absence and failure is done not in the handler but in the caller's body — at the <- that receives the answer. Handlers keep answering with values as before, and the recorder records values too.")
     (interpretation
       "Comparison with try-catch (the answer to point 6, that this resembles try-catch but can be swapped):\nAspect | try-catch (exceptions) | Absent / Raise (this proposal)\nWhere it flies from | any line (including exceptions deep in callees) | only lines that perform Absent / Raise, and <- that receives an effect declared to return failures\nWhat flies | not written (Java's checked exceptions are hand-written and go stale) | appears in the inferred effect set (R13)\nAbsence vs failure | not distinguished (null or exception) | split into Absent and Raise, with combinations chosen by nesting order\nDefaults | return a value in catch and end the scope | absent-as can resume with a default\nSwapping the policy | catch is fixed where written | swapped by composition handlers (different for production and simulation)\nReplay | exceptions are not left in records | handlers answer with values and the recorder records them, so replay goes down the same path\nForgetting to receive | found only when run | caught at writing time via the remaining effects of the job entry\nCatch-all shape | catch (Exception) can be written | on-raise takes only type patterns (catch-all shapes are refused at expansion)\nLook | try … catch | on-raise … patterns — almost the same shape, so readers can read it without learning a new way of thinking (an advantage)")
     (interpretation
       "Writing examples (the file's header comment): the After of revise-input writes only the success path and folds absence and failure into the business answer type with one on-raise. The type seen from outside (return value and remaining effects) is the same as Before, and the hand-written :effects is replaced by inference and disappears. The notify-landed example is the shape that writes no boundary in the business core and leaves per-item failure handling to composition handlers.")
     (interpretation
       "The default receivers catch Absent / Raise and discard the continuation, so after catching neither retrying nor resuming with a default is possible (there is no continuation). Retrying is done while the answer is still a failure value — inside a foundation handler that answers with values. A default can be written in a shape unique to effects (a handler that caught Absent resumes the continuation with a default), but this touches 'do not silently turn unknown values into defaults', so handlers that supply defaults are limited to ones the writer made explicit. doeff-traverse's normalize_to_none (silently resumes Fail with None) is an actual example of the shape that happens when it is not explicit.")
     (interpretation
       "The recording and replay handlers (ADR-DOE-CLUSTER-001 R5) record the real answers of read effects (including failure values like Unreachable) below the boundary. <-'s conversion is a deterministic computation inside the body, so replay produces the same Absent / Raise from the same answer.")
     (interpretation
       "Whether a function performs Absent or Raise is determined by the functions it calls and the handlers placed along the way (point 4). Hand-written :effects silently go stale when callees change. Inference that follows callees transitively (doeff-effect-analyzer) does not go stale. A function's type is shown as the pair of its return value (content only — not wrapped in Maybe / Result) and the inferred set of remaining effects. This is the same idea as the computation type A ! Δ in papers such as Eff (Koka writes the effect row before the return type, like exn int).")
     (interpretation
       "What the analyzer lacks for R13 is 3 things: (1) subtracting from the set the effects caught by handlers placed inside a Program (with-handlers, maybe, result, on-raise, <-'s :absent receiver) — currently it only collects a Program's effects, and subtracting handlers happens only on the env coverage side. (2) folding Programs passed as arguments (carried) on the caller's side according to the meaning of where they are passed — currently the caller folds them explicitly with effect_types_with. (3) a form in which results are cached for the linter and the editor to read — treated like Jev's cache, without importing the module on every save. The current cache is only the in-process module cache.")
     (interpretation
       "Effects are one more channel of data that stacks up (point 9). Inside the body the 4 kinds of failure flow outward as effects and turn back into values at boundaries — answer values at the boundary with the outside world (handler answers, records), and values too at the boundaries where the caller 'wants to do something' (maybe, result, on-raise, absent-as). So the shape of handlers and records does not change (they answer with values); what changes is only how answers are received in the body (opening by <-) and the declarations (defeffect's :absent / :failure / :value). The reason doeff-records' handlers do not change is the same: performing Raise in a handler clause skips the caller's scope (R4).")
     (interpretation
       "Reversible decisions made in implementing stages 1 and 2 (2026-09-28, decided as recommended by the owner of this implementation — rollback after each item):\n(1) maybe and result are functions that take a Program (doeff_core_effects._outcomes), and on-raise and absent-as are macros that take one Program expression — multiple lines are wrapped in do! (the same shape as handle and Try. The ADR's (maybe (<- (ReadRow …))) is written (maybe (ReadRow …))). Rollback: add a macro that takes body forms in sequence.\n(2) The name of a handler's ending clause is (finish value), and the reason for cutting off an ordinary effect is the clause key :finish-reason \"reason\" (the Hy reader discards comments, so it was made a key the macro can read). Rollback: replace the name and the key.\n(3) A Python exception cannot be a Raise reason (TypeError when constructing Raise). When <- opens Err(Python exception), it raises the exception as is instead of making it Raise (so an implementation error folded by Try does not turn into a business failure — R11). Rollback: delete the check in effects.py and one branch of _open_value in outcomes.py.\n(4) The failure in <-'s :absent <failure> is a value expression evaluated only on absence (effects cannot be used — refused at expansion). Rollback: go back to the shape that passes a value to open-bind.\n(5) The expansion of <- and ! calls doeff_core_effects._outcomes.open-bind per bind (the import is inside the expansion — the name resolves wherever <- is written; about 0.2µs per call). For effects without a declaration and for Programs it returns exactly what it received, so what is yielded, the bound answer, and exceptions are the same as before. The literal text of the expansion changes (the shapes in tests/test_typed_effect_bind.py were fixed). Rollback: revert the stage 2 commit.\n(6) How an effect is resumed is the effect type attribute __doeff_resumption__ (Resumption). Matching declarations against clauses happens at expansion for what is identifiable by name, and for the rest when the handler is first installed on a body (doing it at definition time would not be able to look up effect types defined after the handler). Rollback: delete the call to clause_endings.check-clause-endings-once.\n(7) The clause-ending check was made stricter: cond's last must be True, match's last must be _ without a guard (a single-name capture), try's except bodies must also end, and resume inside nested functions and comprehensions does not count. Rollback: revert the branches of _terminates in handle.hy.\n(8) The content of stage 4 (stage 4 of this ADR's R16) was interpreted as the tools needed before switching stages 3 and 5 together (the doeff-linter rule that finds stale branches, R13's inference, the Rust analyzer reading :absent) — because the coordinator's request had no definition of stage 4. Rollback: replace the text of stage 4 in R16.")
     (interpretation
       "The stale-branch rule of stage 4 (R14 (4) — 2026-10-23, reversible decisions made by the owner of this implementation; rollback after each item):\n(1) The rule's number and title are DOEFF175 Stale Branch On A Declared Absent Or Failure, its severity is critical, and it is not lowered by the known-issue list (registry) — it is the gate of every switch, so that change is not landed until hits reach 0 (lowered_by_registry is set to false). Rollback: fix the catalog row in rule.rs.\n(2) It applies only to answers bound directly from an effect head ((<- x (E …)), (<- x T (E …)), (<- x (E …) :absent F), (val x (! (E …))), (match (! (E …)) …)). Answers via a defk ((<- x (read-typed …))) are not followed — the return-type branches of the defk in between are fixed in the change that switches that defk's own effect. Rollback: add a branch that follows the defk's inference when the head of the bound expression is a defk (DOEFF127's inference world can be used).\n(3) Types are resolved down to the module via imports and modules and then compared (the same as DOEFF122 — even if a type of the same name exists in another module, only the declared one). Reading the declarations reuses DOEFF122's failure_types_in, split per effect.\n(4) The population is that of the definition rules (definitions' paths and test_paths — callers in checks also go stale on a switch). The ADR's and doeff-core-effects' probe checks are outside doeff's population, and even widening the population to all Hy files gives 0 hits (measured 2026-10-23). Rollback: split the population into a dedicated section.\n(5) defhandler clauses are skipped per definition (they are the side that catches effects, not the side that binds answers). Names are remembered reading front to back, and names that are rebound or hidden by arguments, for / with / let / except / comprehension / match patterns are dropped (erring toward missing). Rollback: replace this with a reading that follows the flow.")]
  :decision
    [(rule R1 "Absence and failure become 2 effects separated by meaning. Absent (expected absence, the Maybe side) has no content and carries only a description text for records and investigation. Raise(e) (failure, the Result side) carries a reason e (a Python exception cannot be the reason — R11). No inheritance relation is created between the 2 (they do not catch each other via isinstance). Among the boundary handlers, maybe, result, on-raise, and the composition policy handlers do not return an answer and discard the continuation. Only R8's absent-as may resume the continuation. They correspond to fail / empty (the Maybe side) and raise / throw (the Result side) in the literature. The failure side is not called Fail — in the literature that is the name of the Maybe side, and it would also be confused with doeff-traverse's Fail (a resumable notice of failure). They live in doeff-core-effects, the same place as Try (Absent and Raise in doeff_core_effects._effects, the boundary handlers in doeff_core_effects._outcomes).")
     (rule R2 "How Absent and Raise are received is decided by the boundary handlers of the outer scope. (maybe body) maps Absent to Nothing and success to Some. (result body) maps Raise(e) to Err(e) and success to Ok. The combined type is chosen by the nesting order: (result (maybe …)) is a Maybe inside a Result (Ok(Some v), Ok(Nothing), Err(e)), and (maybe (result …)) is a Result inside a Maybe (Some(Ok v), Some(Err e), Nothing). Composition policy handlers (skip one item and record it, stop everything) may also catch Raise — what is skipped is the one scope that caught it, and the continuation does not come back. An Absent / Raise with no receiver stops as an unhandled effect like any other effect (it is not silently made Nothing or None). The runner and env sets place no boundary handlers by default (ADR-DOE-CLUSTER-001 R2).")
     (rule R3 "By default the producing side performs Absent / Raise from the start (the plain style, as in Koka). A defk body directly performs (<- (Raise e)) / (<- (Absent description)) — the body runs in the caller's dynamic scope, so it reaches the caller's maybe / result. The x in (<- x (risky-call)) is only the success value T, and whoever calls it, there is exactly one meaning, 'T, or escape' — the meaning of the same line does not change with the caller. No syntax that declares a 'look only at success' scope (success-only) is provided (2026-09-27, a reversible decision made as recommended by the f143ee92 seat. Rollback: add a scope-declaring macro and change R3 back to text of that shape).")
     (rule R4 "A handler that answers an effect (a foundation handler answering ReadRow, a translating handler) must not perform Absent / Raise inside its clauses. An effect performed in a handler clause goes to handlers outside that handler, skipping the scope of the caller's body (maybe / result):\n  composition root: [outer receivers … foundation handler (answers ReadRow)]   ← outer\n    caller's body: (result (maybe (<- row (ReadRow …))))              ← inner\n  ReadRow goes outward from the body, passes straight through maybe and result, and reaches the foundation handler. If the foundation handler performs Absent there, it goes outside the foundation handler and does not come back to the inner maybe and result.\nSo handlers keep answering with values as before (the recorder also records values, and replay does not change), and <- on the caller's side, following the defeffect declaration (R5), turns an absence answer into Absent and a failure answer into Raise(answer), performed in the body's scope. This is an important pitfall, so it becomes a doeff-linter rule (the same rule as R9's second place that must be closed).")
     (rule R5 "What counts as success, absence, failure, or value is decided by each effect's defeffect declaration, not guessed from type names. The shape is {:answer (| Row Missing Unreachable Conflict) :absent [Missing] :failure [Unreachable] :value [Conflict]} — :absent / :failure / :value are all optional and are lists of members of the :answer union (None may be written). Members listed in none of them are success. A member not in :answer or overlapping across 2 keys is an expansion-time error (when :answer is not a union form, Outcomes.declare refuses at definition time). Writing even one attaches __doeff_outcomes__ (doeff_core_effects._outcomes.Outcomes) to the effect type. <- follows the declaration: for success and value it binds the content, for absence it performs Absent, for failure it performs Raise(answer). The answer of an effect without a declaration is not converted and is bound as is (what is yielded is also the same as before). Answers handled normally in the business are not declared as failures — e.g. PutRow's Conflict (losing on version) is an answer handled normally in CAS retries, so it is declared :value. Where absence should be received as a value, wrap that line in maybe ((maybe (ReadRow …)) is a Maybe value). doeff-records' effects (currently defclass) move to defeffect and get this declaration (how to move them is R14).")
     (rule R6 "Split into 2 roles (2026-09-27, a reversible decision made as recommended by the f143ee92 seat). Opening (value → effect): only <- and ! (! goes through the same opening as <-) — the conversion following the declaration (R5), :absent <failure>, and the shape that opens a Result / Option value when passed one. Folding (effect → value): only the boundary handlers maybe, result, on-raise, absent-as. :absent <failure> is a trailing argument of <-; as a narrow receiver wrapping that one line's bind, it turns any Absent performed inside (directly or deep inside a called defk) into Raise(<failure>) (putting the meaning in the same place as the bind line; the receiver is inside the caller's body, so the Raise it performs reaches the caller's boundary). <failure> is a value expression evaluated only on absence, and cannot use effects. A received Result / Option value can be opened by passing it to <- again (Ok, Some → content; Err(e) → Raise(e); Nothing → Absent. If the content of Err is a Python exception, it is raised as the exception — R11) — values and effects can be converted back and forth, and no separate syntax for opening is created. Types (Option is doeff's Maybe = Some | Nothing):\n  (<- x (dangerous-call))      x : T\n  dangerous-call               : Program[T] ! {Raise[E], …}\n  (maybe body)                 : Program[Option[T]] ! {Raise[E], …}\n  (result body)                : Program[Result[T]] ! {Absent, …}\n  (result (maybe body))        : Program[Result[Option[T]]] ! {…}")
     (rule R7 "The boundary on-raise has the shape (on-raise body pattern [:if guard] target …) — it lists only failure-type patterns ((Type field …) or (| (A …) (B …))) and the business answers they map to. Catch-all shapes (_, a single-name capture, object, Exception, BaseException) are refused at expansion, and a case naming an exception type is refused at construction (RaiseCase). A Raise matching no pattern is not caught and passes outward. Python exceptions (implementation errors) are not caught by on-raise (it looks only at Raise). Guards and targets are value expressions (cannot use effects) (2026-09-27, a reversible decision made as recommended by the f143ee92 seat).")
     (rule R8 "(absent-as default body) folds an Absent performed inside into the default (a shape unique to effects — no need to receive a Maybe as a value and fold it into a default). This touches 'do not silently turn unknown values into defaults' (the code-quality policy), so handlers that supply defaults are limited to explicit ones like (absent-as …) written in place, and runner and env sets and foundation handlers do not place resumption with a default. The resumed value fits the type only when Absent was performed in place of a value by <- in absent-as's literal text — if the body is (do! …), only absences performed by <- and ! written directly in that literal text are resumed with the default (that bind receives the default and continues). Returning the default for an Absent performed deep inside a called defk would make the deep line receive a value of a different type. Resuming a (<- (Absent …)) written directly in code would run a continuation that does not assume absence. In these 2 cases it does not resume, and ends absent-as's scope with the default. Binds inside nested do! / fnk / functions count as 'deep'.")
     (rule R9 "Boundary handlers (maybe, result, on-raise, absent-as) are written only where something should be done about absence or failure. Elsewhere only the success path is written, and Absent / Raise escape outward (the same as writing try-catch only where you want to catch). However, there are 2 places that must always be closed structurally. (1) The job entry — the runner adds no handlers (ADR-DOE-CLUSTER-001 R2), so it is closed with the service body's policy handlers (skip and record, stop, etc.). Check: the inferred remaining effects of the job's Program are empty. (2) Places that answer by running a Program inside a handler clause (translating handlers) — a Raise escaping from inside a handler goes outside the handler and skips the caller's scope (R4), so close it into an answer value (the business effect's answer type) before returning. Check: the inferred remaining effects of a Program run in a handler clause contain no Absent / Raise — a candidate new doeff-linter rule (number undecided) (2026-09-27, a reversible decision made as recommended by the f143ee92 seat).")
     (rule R10 "Retrying cannot be done after catching Raise (the continuation has been discarded). Do it while the answer is still a failure value, inside a foundation handler that answers with values (for Unreachable, reissue).")
     (rule R11 "Python exceptions are treated as implementation errors, as is (the same as the doeff-records rule). Expected absence and failure are not represented by exceptions — a Python exception cannot be a Raise reason. Try (folding exceptions into Result) stays as is and is not mixed with Absent / Raise — when <- opens Try's Err(exception), it raises the exception as is rather than making it Raise.")
     (rule R12 "Relation to recording and replay: handlers answer with values, and the recorder records the real answers (including failure values) below the boundary (ADR-DOE-CLUSTER-001 R5). <-'s conversion is a deterministic computation inside the body, so replay produces the same Absent / Raise from the same answer. Absent / Raise are not made general effects that reach the recorder (what is recorded is the answers of read effects).")
     (rule R13 "Which effects a function performs (including Absent and Raise) is not written by hand but inferred by doeff-effect-analyzer (following callees transitively, reading Hy after macro expansion, reading the effects caught from defhandler clauses, and reporting what it cannot follow as unresolved). A function's type is shown as the pair of its return value (content only, not wrapped in Maybe / Result) and the inferred set of remaining effects. Notation example: settle-landing : str → Program[Row] ! {Absent, Raise[Unreachable | Refused], ReadRow, PutRow} (the same idea as the computation type A ! Δ in papers such as Eff). Wrapping in a handler removes the effects that handler catches from the set. :effects (an optional key already in defk's head dict) is not made mandatory. It is written only at public boundaries as an upper bound, and the linter checks inferred ⊆ declared (the declaration does not go stale). Uses: show the remaining effects on editor hover / check at writing time that the remaining effects of the job entry Program are empty (the first of R9) / report a layer violation if general effects (communication, recording) enter the remaining effects of a core definition. What the analyzer lacks is the :context interpretation.")
     (rule R14 "Migration (the procedure for switching stages 3 and 5): doeff-records' effects currently follow the rule of returning failures as values (README 'failure answers (returned as values)'). When a declaration is switched, existing callers of (match body (Missing) …) no longer see Missing (a branch that is never taken remains). So, in the following order, per effect (one effect per change — never switch all effects at once), switch doeff-records' declaration and agora's callers in the same change:\n(1) Pick one effect E to switch (starting with those with few callers — ReadPlacedInput, PostMessage, etc. ReadRow last).\n(2) Move doeff-records' E from defclass to defeffect, and declare :absent / :failure / :value per R17's assignment. The handlers answering E are not changed (they answer with values — performing Raise in a handler clause skips the caller's scope: R4). The record shape does not change either, so existing records can be replayed as is.\n(3) In the same change, rewrite agora's callers of E: callers that split absence and failure with (match …) / isinstance keep only the success path; where something should be done on absence, use <-'s :absent <failure> or absent-as, and where something should be done on failure, use on-raise. Callers that carry around or count Missing as an ordinary value are wrapped in (maybe …) and take the Maybe value (Nothing instead of Missing). Callers of answers declared :value (Conflict) are not changed. Close Absent / Raise at the job entry and in translating handlers that run Programs in handler clauses (R9).\n(4) Stale branches (branches that match / isinstance on the types declared as E's absence or failure as values) are found by a doeff-linter rule (stage 4's tool). The change is not landed until hits reach 0 — law declarations-switch-one-effect-at-a-time.\n(5) Type-determined rewrites (deleting stale branches, wrapping in maybe, moving to :absent) are delegated to Sonnet 5 — hand it the per-effect procedure (this R14) and the list of linter hits and proceed mechanically. Where a judgment is needed about which failure an absence becomes or where to close it, the maintainer of that caller decides.\n(6) HTTP answer status codes are mapped to the 4 kinds in one place in the translation layer (the handler that maps HTTP answers to business answers) (R17), and callers are not shown status codes.")
     (rule R15 "How handler clauses end (the concern about forgetting to resume, point 10): a clause ends with one of resume (if not in tail position, the continuation's answer returns to the clause), transfer, finish (discards the continuation and makes that value the answer of the scope where the handler was placed), reperform, or raise. Forgetting is an error as before — if not every path of the clause (each branch of if, cond, match, try; cond's last is True, match's last is _ without a guard, and try's except bodies too) ends, it is a SyntaxError at expansion. resume inside when, unless, and, or, loops, nested functions, and comprehensions does not count as an ending. Each effect declares how it is resumed (the effect type attribute __doeff_resumption__ = doeff_core_effects._effects.Resumption: Raise = NEVER (no one resumes), Absent = ABSENT_AS_ONLY (only absent-as resumes), ordinary effects that declare nothing = REQUIRED). Clauses contradicting the declaration (clauses that resume / transfer Raise / Absent, clauses that cut off an ordinary effect with finish) are violations — those identifiable by name (clause head is Raise / Absent, a finish clause has no reason) are a SyntaxError at expansion, and those that are not (a Raise imported under an alias, a type declaring __doeff_resumption__) are a ClauseEndingError when the handler is first installed on a body. In particular, resuming Raise 'continues as if it succeeded although it failed', so it is refused both by declaration (NEVER) and at expansion, and even if a plain handler function resumes a Raise / Absent opened by <-, the opening side makes it a RuntimeError. A clause that deliberately cuts off an ordinary effect (stopping the whole run on timeout, etc.) is allowed only when the clause writes :finish-reason \"reason\" (the same treatment as a deff with a reason comment). At run time: if a clause exits without resume or finish (a resume inside a with that suppresses exceptions, or other paths invisible at expansion), the defhandler wrapper makes it a RuntimeError — because the VM silently ends the scope with the value of a handler that does not resume. Plain Python handler functions (@do functions, deff handlers) have no such wrapper, and the VM accepts 'return a value without resuming' as a legitimate ending, so the run-time check does not apply (why it cannot be closed: the VM has no marker to tell finish from a forgotten resume — telling them apart would require adding an explicit ending operation to the VM and rewriting all existing Python handlers). How plain handler functions end is a candidate doeff-linter rule (number undecided).")
     (rule R16 "Stage plan (2026-09-27, point 9):\nStage 1 (no behavior change, additions only): the Absent / Raise effects, the boundary handlers maybe, result, on-raise, absent-as (a deep absence ends the scope with the default), defhandler's ending clause (finish) and the check of how clauses end (R15).\nStage 2 (no behavior change — no effect has a declaration yet): defeffect's answer declaration (R5), the <- and ! conversion, :absent, the shape that opens Result / Option values (R6), and resumption of <- in absent-as's literal text (R8). For <- on an effect without a declaration, both what is yielded and the bound answer are the same as before.\nStage 3: move doeff-records' effects to defeffect and attach declarations (handlers unchanged).\nStage 4: tools needed before switching stages 3 and 5 — the doeff-linter rule that finds stale branches (R14 (4)), the rules for catch-all on-raise and for Absent / Raise in handler clauses (R4, R9), inference (R13), and the Rust analyzer reading <-'s :absent.\nStage 5: rewriting agora's callers. Stages 3 and 5 are switched together — per effect, in the same change (R14).")
     (rule R17 "Assignment of the 4 recurring kinds of failure (point 9): Unreachable (cannot reach the store or the peer) and Refused (the peer refused) are failures (:failure — <- performs Raise), Missing (no row) is absence (:absent — <- performs Absent), and Conflict (losing on version) is a value (:value — an answer handled normally in CAS retries). In the code body the 4 kinds are effects; at the boundary with the outside world (handler answers, records) and at the boundaries where the caller wants to do something (maybe, result, on-raise, absent-as) they are data. HTTP status codes are mapped to these 4 kinds in one place in the translation layer (e.g. 404 → Missing, 409 and 412 → Conflict, 401 and 403 → Refused, 5xx and transport failures → Unreachable — the mapping table is held by the translation layer).\nAddendum (2026-09-28 — a 5th kind of failure): Malformed (JSON from outside does not have the shape of the type contract — carries which type, which field, and why; doeff_hy._wire) is a failure (:failure — <- performs Raise). A version mismatch with the peer can happen in operation, so it is treated as a business failure, not an implementation-error exception. Its only source is the defwire decoders parse / parse-json (ADR-DOE-HY-007 R8). Until the stage 3 switch (R14), parse returns Malformed as a value (answer type = (| T Malformed)) and callers split on the value. At the switch, parse gets a :failure declaration and moves to Raise(Malformed) (one change per effect — the same procedure as the other 4 kinds). A reversible decision made as recommended after option A (defwire) was chosen. Rollback: delete this addendum text and keep parse's answer as the Malformed value.\nAddendum (2026-10-23, the first switch of stages 3 and 5 = the ReadStreamEnd seat — StreamEmpty, an answer outside the 4 kinds): StreamEmpty (the append log has no events at all) is a value (:value — <- binds the content). It is an answer carrying the fact that the log has no events at all and is not mixed with sequence number 0 (the doeff-records README), so it is not made an absence skipped via Absent; callers handle it separately from StreamEnd. ReadStreamEnd's declaration = {:answer (| StreamEnd StreamEmpty Unreachable) :failure [Unreachable] :value [StreamEmpty]} (success = StreamEnd, absence = none). The answering handler keeps answering with values (R4). Decided by = the owner of this card (as the card recommended — a reversible decision). Rollback: delete this addendum text and move StreamEmpty from :value to :absent (callers that split StreamEmpty as a value — the (isinstance end StreamEnd) branches of event_source's stream-starts and publish-appends, the comparisons with StreamEmpty in law law-stream-end-is-the-last-sequence and others — would no longer be reached because <- performs Absent; rewrite them to (maybe …) or <-'s :absent). To revert the declaration entirely, turn the defeffect back into a defclass, and turn the on-raise in event_source's reachable and ride-out-stall back into the original (<- answered ask).\nAddendum (2026-10-24, the second switch of stages 3 and 5 = the WatchEvents seat — EventsQuiet, an answer outside the 4 kinds): EventsQuiet (the head of the append log did not advance until the timeout) is a value (:value — <- binds the content). It is an ordinary answer that nothing advanced until the deadline, and waiting callers (the signal source's publish-appends and others) handle it separately from EventsMoved every time, so it is neither made a failure skipped via Raise nor an absence skipped via Absent (it is not an error even if after is ahead of the head). WatchEvents' declaration = {:answer (| EventsMoved EventsQuiet Unreachable) :failure [Unreachable] :value [EventsQuiet]} (success = EventsMoved, absence = none). The answering handlers (memory, pg, http_client, http_server) keep answering with values (R4). Decided by = the owner of this card (as the card recommended — a reversible decision). Rollback: delete this addendum text and move EventsQuiet from :value to :absent (callers that split EventsQuiet as a value — the (isinstance moved EventsMoved) branch of event_source's publish-appends, the checks' comparisons of WatchEvents' answer with (EventsQuiet), and others — would no longer be reached because <- performs Absent; rewrite them to (maybe …) or <-'s :absent). To revert the declaration entirely, turn the defeffect back into a defclass (the on-raise in event_source's reachable and ride-out-stall passes the answer through unchanged when there is no declaration performing Raise, so it may stay).\nAddendum (2026-10-24, the ReadEventByKey seat of stages 3 and 5 — EventAbsent and EventRetired, answers outside the 4 kinds): EventAbsent (nothing was appended to the log under that key) is absence (:absent — <- performs Absent; the same meaning as ReadRow's Missing), and EventRetired (carries the sequence number of an event that was appended but removed by the retention deadline) is a value (:value — <- binds the content; it is an ordinary answer callers handle separately from Event, and since it carries the fact of removal it is not made an absence skipped via Absent). ReadEventByKey's declaration = {:answer (| Event EventAbsent EventRetired Unreachable) :absent [EventAbsent] :failure [Unreachable] :value [EventRetired]} (success = Event). The answering handler keeps answering with values (R4). Decided by = the owner of this card (as the card recommended — a reversible decision). Rollback: delete this addendum text and change the assignment. If EventAbsent moves from :absent to :value, turn callers that handled absence with <-'s :absent, maybe, or absent-as back into isinstance branches that split EventAbsent as a value. If EventRetired moves from :value to :absent, callers that split EventRetired as a value (isinstance branches, law law-keys-are-forgotten-after-their-memory, and others) would no longer be reached because <- performs Absent; rewrite them to (maybe …) or <-'s :absent. To revert the declaration entirely, turn the defeffect back into a defclass and turn the relay (Perform) back into the original (<- answer effect).\nAddendum (2026-10-24, the WatchChanges seat of stages 3 and 5 — Reset, an answer outside the 4 kinds): Reset (the cursor's epoch differs from the store's version, or the position is before pruned changes) is a value (:value — <- binds the content). It is an ordinary answer that the cursor's generation differs from the store's version, and callers reread from the start using Reset's (epoch, floor) and continue, so it is neither made a failure skipped via Raise nor an absence skipped via Absent. WatchChanges' declaration = {:answer (| Changes Reset Unreachable) :failure [Unreachable] :value [Reset]} (success = Changes, absence = none). The answering handlers (memory, pg, http_client, http_server) keep answering with values (R4). Decided by = the owner of this card (as the card recommended — a reversible decision). Rollback: delete this addendum text and move Reset from :value to :absent (callers that split Reset as a value — the (Reset …) arm of event_source's publish-changes, the comparisons with Reset in the checks' laws law-epoch-change-resets and law-maintenance-prunes-and-sweeps, and others — would no longer be reached because <- performs Absent; rewrite them to (maybe …) or <-'s :absent). If Reset moves to :failure, rewrite the same callers to on-raise. To revert the declaration entirely, turn the defeffect back into a defclass and turn the checks' on-raise (test_memory_faults, test_memory_outage) and the relay Perform (test_laws_catch_broken_handlers) back into the original <- (the on-raise in event_source's reachable and ride-out-stall passes the answer through unchanged when there is no declaration performing Raise, so it may stay).\nAddendum (2026-10-24, the ReadEvents seat of stages 3 and 5 — NotIndexed, an answer outside the 4 kinds): NotIndexed (an idempotency-key head read key-head was requested on a log that does not declare key-lookup) is a failure (:failure — <- performs Raise). It is an error where the caller misread the log's declaration; rereading gives the same answer and there is no continuation, so it is made a failure rather than a value or absence (aligned with the assignment of ListRows' NotIndexed). ReadEvents' declaration = {:answer (| Events Unreachable NotIndexed) :failure [Unreachable NotIndexed]} (success = Events, no absence and no value). The answering handlers (memory, pg, http_client, http_server) keep answering with values (R4). Decided by = the owner of this card (as the card recommended — a reversible decision). Rollback: delete this addendum text and move NotIndexed from :failure to :value (callers that split NotIndexed as a value — the checks' comparisons with the head-read answer and others — would no longer get Raise from <-, and go back to the previous shape of receiving NotIndexed as a value. Law 7b in laws.hy fires via as-writer and does not go through <-, so reverting the declaration does not change it). To revert the declaration entirely, turn the defeffect back into a defclass (callers' on-raise passes the answer through unchanged when there is no declaration performing Raise, so it may stay).\nAddendum (2026-10-25, the PutRows seat of stages 3 and 5 — RowsConflict and RowsRefused, answers outside the 4 kinds): RowsConflict (a row's expectation in the batch does not match the current row — the batch wrote no rows at all) is a value (:value — <- binds the content). Like PutRow's Conflict (losing on version), it is an answer handled normally in CAS retries where the caller rereads and refires, so it is neither made a failure skipped via Raise nor an absence skipped via Absent. RowsRefused (a row in the batch is not allowed by the declaration, or row 0 of a different batch with the same idempotency key — the batch wrote no rows at all) is a failure (:failure — <- performs Raise). Aligned with the assignment of PutRow's Refused. PutRows' declaration = {:answer (| WrittenRows RowsConflict RowsRefused Unreachable) :failure [RowsRefused Unreachable] :value [RowsConflict]} (success = WrittenRows, no absence; Unreachable is a failure per R17's assignment). The answering handlers (memory, pg, http_client, http_server) keep answering with values (R4). Decided by = the owner of this card (as the card recommended — a reversible decision). Rollback: delete this addendum text and change the assignment. If RowsConflict moves from :value to :failure, rewrite callers that split RowsConflict as a value (isinstance branches, match arms) to on-raise. If RowsConflict moves to :absent, the same callers would no longer be reached because <- performs Absent; rewrite them to (maybe …) or <-'s :absent. If RowsRefused moves from :failure to :value, <- no longer performs Raise and the previous shape of receiving RowsRefused as a value returns (callers' on-raise passes the answer through unchanged when there is no declaration performing Raise, so it may stay). To revert the declaration entirely, turn the defeffect back into a defclass and turn the checks' on-raise (test_memory_outage) and the relay Perform (test_memory_faults, test_laws_catch_broken_handlers) back into the original <-.\nAddendum (2026-10-31, the ListRows seat of stages 3 and 5 — Reset and NotIndexed, answers outside the 4 kinds): Reset (the generation of the passed cursor's position differs from the store's version, or the position is before pruned changes) is a value (:value — <- binds the content). It is an ordinary answer that the position's generation differs from the store's version, and callers reread from the head of the listing and continue, so it is neither made a failure skipped via Raise nor an absence skipped via Absent (the same assignment as WatchChanges' Reset). NotIndexed (filtered on a column with no index) is a failure (:failure — <- performs Raise). It is an error where the caller misread the filter column; rereading gives the same answer and there is no continuation, so it is made a failure rather than a value or absence (the same assignment as ReadEvents' NotIndexed; the NotIndexed that the PostgreSQL store returns until index preparation finishes is the same failure, and callers that refire catch it with on-raise). Unreachable is a failure per the assignment above. ListRows' declaration = {:answer (| Page Reset Unreachable NotIndexed) :failure [Unreachable NotIndexed] :value [Reset]} (success = Page, absence = none). The per-table row-type layer list-typed goes through this declaration, returns TypedPage | Reset, and lets Unreachable and NotIndexed escape as Raise. The answering handlers (memory, pg, http_client, http_server) keep answering with values (R4). Decided by = the owner of this card (as the card recommended — a reversible decision). Rollback: delete this addendum text and change the assignment. If Reset moves from :value to :absent, callers that split Reset as a value (isinstance branches, match arms; none inside doeff, but callers that page through for consumers) would no longer be reached because <- performs Absent; rewrite them to (maybe …) or <-'s :absent. If Reset moves to :failure, rewrite the same callers to on-raise. If NotIndexed moves from :failure to :value, <- no longer performs Raise and the previous shape of receiving NotIndexed as a value returns (callers' on-raise passes the answer through unchanged when there is no declaration performing Raise, so it may stay). To revert the declaration entirely, turn the defeffect back into a defclass, turn list-typed's :post back into the original type, and turn the checks' on-raise (test_memory_faults, test_memory_outage) and the relay Perform (test_laws_catch_broken_handlers) back into the original <- (the on-raise in event_source's reachable passes the answer through unchanged when there is no declaration performing Raise, so it may stay).\nAddendum (2026-11-02, the PutRow seat of stages 3 and 5 — the assignment of the 4 kinds stays as decided above): the 4 kinds of PutRow's answer all fall within the assignment above. Written is success; Conflict (losing on version, where the expectation does not match the current row) is a value (:value — <- binds the content; it is an answer handled normally in CAS retries where the caller rereads and refires, so it is neither made a failure skipped via Raise nor an absence skipped via Absent); Refused (a write the declaration does not allow, or a different body with the same idempotency key) and Unreachable are failures (:failure — <- performs Raise). There is no absence. PutRow's declaration = {:answer (| Written Conflict Refused Unreachable) :failure [Refused Unreachable] :value [Conflict]} (PutRows' RowsConflict and RowsRefused follow the same assignment). The per-table row-type layer put-typed goes through this declaration, returns TypedWritten | TypedConflict, and lets Refused and Unreachable escape as Raise. The answering handlers (memory, pg, http_client, http_server) keep answering with values (R4). Decided by = the owner of this switch (per the assignment above — a reversible decision). Rollback: delete this addendum text and change the assignment. If Conflict moves from :value to :failure, rewrite callers that split Conflict as a value (isinstance branches, match arms; including put-typed's TypedConflict branch) to on-raise. If Conflict moves to :absent, the same callers would no longer be reached because <- performs Absent; rewrite them to (maybe …) or <-'s :absent. If Refused moves from :failure to :value, <- no longer performs Raise and the previous shape of receiving Refused as a value returns (callers' on-raise passes the answer through unchanged when there is no declaration performing Raise, so it may stay). To revert the declaration entirely, turn the defeffect back into a defclass, turn put-typed's :post back into the original type (TypedWritten | TypedConflict | Refused | Unreachable) and branch (the final True answer), and turn the checks' on-raise (test_memory_outage, test_typed_rows, test_client_internal_is_unreachable) and the relay Perform (test_memory_faults, test_memory_write_notes, test_laws_catch_broken_handlers) back into the original <-.")]
  :laws
    [(law absent-and-raise-are-distinct
       :statement "Absent has no content (only a description text) ∧ Raise has a reason ∧ Absent and Raise have no inheritance relation ∧ for_all boundary handler h ∈ {maybe, result, on-raise, composition policy handlers}: h catches Absent / Raise and does not resume the continuation"
       :counterexamples
         [(counterexample "both absence and failure are performed as a single Fail — the receiver cannot tell absence from failure, and a Maybe inside a Result cannot be built")
          (counterexample "Raise is made a subtype of Absent — (maybe …) crushes even failures into Nothing and the reason is lost")
          (counterexample "the failure side is named Fail — the same name as doeff-traverse's Fail (resumable with None), so whether the continuation is discarded cannot be read from the name")]
       :enforced-by ["test-adr-doe-core-effects-003-absent-and-raise-are-distinct"]
       :wiring "wired (2026-09-28) — the check confirms the fields, the inheritance, the resumption declarations, and that maybe / result do not resume the continuation. Composition policy handlers (Traverse's per-item receivers) come after stage 4")
     (law fold-handlers-choose-the-shape
       :statement "for_all scope s: the shape of the value leaving s is determined only by the nesting order of the boundary handlers around s — (result (maybe b)) : Result[Maybe[T]] ∧ (maybe (result b)) : Maybe[Result[T]] ∧ an Absent / Raise with no receiver stops as an unhandled effect ∧ the runner and env sets place no boundary handlers"
       :counterexamples
         [(counterexample "the runner or env set places maybe by default — reading the Program does not tell which shape comes back (the same harm as ADR-DOE-CLUSTER-001 R2)")
          (counterexample "a Raise with no receiver is silently turned into None at the top level — a failure turns into a success value")]
       :enforced-by ["test-adr-doe-core-effects-003-nesting-order-chooses-the-shape"]
       :wiring "wired (2026-09-28) — the check confirms the 6 nesting-order cases and that an Absent / Raise with no receiver stops with UnhandledEffect. That the runner places no boundary handlers is checked by the ledger check of ADR-DOE-CLUSTER-001")
     (law a-bind-has-one-meaning
       :statement "for_all line l of a bind (<- x e): the type of x is the success type T determined only by e's declaration, and does not change with handlers placed on the side that calls l — absence and failure do not enter x but escape as Absent / Raise ∧ :absent <failure> maps only absences inside that bind to Raise(<failure>)"
       :counterexamples
         [(counterexample "the answer type is switched by the caller's handler — Row enters x for one caller and Row | Missing | Unreachable for another, breaking the (. row value) inside")
          (counterexample "a syntax that declares a 'look only at success' scope (success-only) is added — the type of the same line differs inside and outside the scope, and two ways of writing coexist")
          (counterexample "the :absent receiver is placed outside the caller's scope (at the composition root) — absences from other binds are mapped to the same failure too, and which line was absent is lost")]
       :enforced-by ["test-adr-doe-core-effects-003-a-bind-has-one-meaning"
                     "test-adr-doe-core-effects-003-absent-option-maps-one-bind"]
       :wiring "wired (2026-09-28) — the check confirms that the same bind line binds the success type under any of maybe, result, or on-raise, and the :absent mapping")
     (law handlers-answer-with-values
       :statement "for_all handler h that answers effect E: h's clauses do not perform Absent / Raise and answer with values of E's answer ∧ the conversion to absence and failure is done by <- in the caller's body"
       :counterexamples
         [(counterexample "a foundation handler answering ReadRow performs (<- (Absent no row)) when the row is missing — Absent goes outside the foundation handler, skips the caller's maybe and becomes unhandled (or is caught by an unrelated outer receiver)")
          (counterexample "a translating handler runs a business Program inside its clause and returns without closing that Program's Raise — it likewise skips the caller's result")
          (counterexample "the recorder is made to record Raise — the answer value is not left in the record, and replay cannot go through the same conversion")]
       :enforced-by ["test-adr-doe-core-effects-003-raise-in-a-handler-clause-skips-the-bodys-result"
                     "test-adr-doe-core-effects-003-declared-bind-opens-the-answer"]
       :wiring "partly wired (2026-09-28) — the check confirms that a Raise performed in a handler clause skips the body's result (demonstrating the pitfall) and that <- opens the answer in the caller's body. A doeff-linter rule checking that handler clauses do not perform Absent / Raise is a candidate with no number yet (stage 4)")
     (law outcomes-are-declared-on-the-effect
       :statement "for_all effect type E, answer type t ∈ answer(E): whether t is success, absence, failure, or value can be read from E's declaration (defeffect) ∧ <-'s conversion follows only that declaration (does not read type names) ∧ <- on an E without a declaration changes neither what it yields nor the answer it binds"
       :counterexamples
         [(counterexample "a type is guessed to be absence if its name contains Missing or NotFound — renaming changes the meaning, and new types with failure-like names are missed")
          (counterexample "PutRow's Conflict is uniformly decided to be a failure — even answers handled normally in CAS retries become Raise, and callers turn them back into values with on-raise every time")
          (counterexample "<- also turns Missing in the answer of an effect without a declaration into Absent — existing callers' behavior silently changes before stage 3")]
       :enforced-by ["test-adr-doe-core-effects-003-declared-bind-opens-the-answer"
                     "test-adr-doe-core-effects-003-undeclared-bind-is-unchanged"]
       :wiring "wired (2026-09-28) — the check confirms the declaration (Outcomes), opening according to the declaration, and that <- on an effect without a declaration yields the same object and binds the same answer")
     (law on-raise-has-no-catch-all
       :statement "for_all on-raise o: every pattern of o is a failure-type pattern (no type-agnostic shape, no _, no Exception) ∧ o does not catch Python exceptions ∧ a Raise matching no pattern passes outside o"
       :counterexamples
         [(counterexample "a catch-all on-raise (pattern _ turning everything into WriteUnreachable) — even when new failure types are added they are silently crushed into the same answer, and they do not appear in the inferred effect set either")
          (counterexample "on-raise also catches Python exceptions — implementation errors turn into business failure answers")]
       :enforced-by ["test-adr-doe-core-effects-003-on-raise-refuses-python-exceptions-and-catch-all"]
       :wiring "wired (2026-09-28) — the check confirms that catch-all patterns are refused at expansion, cases naming an exception type are refused at construction, and Python exceptions pass straight through on-raise")
     (law defaults-are-explicit-handlers
       :statement "for_all handler h that resumes Absent with a default: h is an explicit one written in place (absent-as) ∧ h resumes only an Absent performed in place of a value by <- in absent-as's literal text ∧ runner and env sets and foundation handlers do not resume Absent"
       :counterexamples
         [(counterexample "a foundation handler resumes with an empty string when it catches Absent — absence turns into a value without the caller knowing (the same shape as doeff-traverse's normalize_to_none)")
          (counterexample "(absent-as 0 …) resumes with 0 an Absent performed by (<- row (ReadRow …)) deep inside a called defk — the deep line receives 0 instead of a Row")
          (counterexample "(absent-as 0 …) resumes with 0 a (<- (Absent description)) written directly in code — a continuation that does not assume absence runs")]
       :enforced-by ["test-adr-doe-core-effects-003-absent-as-resumes-only-direct-binds"]
       :wiring "partly wired (2026-09-28) — the check confirms that only <- in the literal text is resumed and that deep absences and directly written Absent end the scope. That only absent-as can resume Absent is guarded by R15's clause check and the RuntimeError on the opening side. A linter rule finding non-explicit default resumption is undecided")
     (law handler-clauses-end-explicitly
       :statement "for_all clause c of defhandler / handle: every path of c ends in one of resume, transfer, finish, reperform, raise (at expansion) ∧ the way c ends does not contradict the Resumption of c's effect (does not resume Raise or Absent; a finish on an ordinary effect has a reason — at expansion, or when the handler is first installed on a body) ∧ if c exits at run time without resume or finish, it is an error (does not silently end the scope)"
       :counterexamples
         [(counterexample "a clause that forgot resume (Tick [now] now) — it returns a value and exits, and the VM silently ends the scope with now")
          (counterexample "a clause that resumes on only one branch (Tick [now] (if (> now 1) (resume 1) None)) — on one path the scope silently ends with None")
          (counterexample "a clause that resumes Raise (Raise [reason] (resume 0)) — it continues as if it succeeded although it failed")
          (counterexample "a cut-off without a reason (Tick [now] (finish None)) — cannot be told apart from a forgotten resume")
          (counterexample "a clause whose match listing the cases by type has no final _ — it silently exits on a value missing from the list")]
       :enforced-by ["test-adr-doe-core-effects-003-defhandler-clause-must-end-explicitly"
                     "test-adr-doe-core-effects-003-clause-endings-checked-at-install-and-run"]
       :wiring "partly wired (2026-09-28) — defhandler and handle clauses are checked at 3 points: expansion, first install, and run time. How plain Python handler functions (@do functions, deff handlers) end is a candidate doeff-linter rule with no number yet (R15)")
     (law scopes-appear-only-where-failure-is-handled
       :statement "for_all placement b of a boundary handler: b does something about absence or failure ∧ for_all job Program j: inferred remaining effects(j) = ∅ (closed by policy handlers) ∧ for_all Program p run in a handler clause: Absent ∉ remaining(p) ∧ Raise ∉ remaining(p)"
       :counterexamples
         [(counterexample "every call is wrapped in result — Result-value branches return to code that is only the success path, the same as writing try-catch on every line")
          (counterexample "a Raise from a Program run in a handler clause leaks — it skips the caller's scope and becomes unhandled")
          (counterexample "not closed at the job entry — the runner adds no handlers, so the Raise stops the job as an unhandled effect")]
       :enforced-by []
       :wiring "not wired (2026-09-28) — the analyzer does not subtract handlers inside a Program, and the doeff-linter rule is also a candidate with no number yet (stage 4)")
     (law retry-happens-before-raise
       :statement "for_all retry r: r is done while the answer is still a failure value, inside a foundation handler that answers with values — a handler that caught Raise does not retry (there is no continuation)"
       :counterexamples
         [(counterexample "the receiver of (result …) sees Err and runs the same Program again — the effects (writes) in the first half of the Program are repeated too")]
       :enforced-by []
       :wiring "not wired (2026-09-28) — there is nothing to check mechanically")
     (law effects-are-inferred-not-hand-written
       :statement "for_all definition f: the set of effects f performs is obtained by inference (doeff-effect-analyzer) ∧ if f has :effects, the linter checks inferred(f) ⊆ declared(f) ∧ an f without :effects is not a violation"
       :counterexamples
         [(counterexample ":effects is required on every defk — it silently goes stale every time a callee changes, and makes the writer do the inference")
          (counterexample "only the declared :effects is trusted without comparing to inference — even when a callee starts performing Raise the declaration does not change, and the job-entry check becomes a false green")]
       :enforced-by []
       :wiring "not wired (2026-09-28) — the analyzer does not subtract handlers inside a Program, and there is no cache of results for tools to read. The linter rule for inferred ⊆ declared is undecided (stage 4)")
     (law declarations-switch-one-effect-at-a-time
       :statement "for_all change that switches the declaration of effect E: callers that match E's absence / failure answer types as values = 0 (doeff-linter finds them) ∧ the effects switched in one change are only those whose callers have been rewritten in the same change"
       :counterexamples
         [(counterexample "ReadRow's declaration is switched but a (match body (Missing) …) branch remains — a branch that is never taken remains, and readers wrongly believe absence is handled there")
          (counterexample "the declarations of doeff-records' 7 effects are switched at once — omissions in the rewrite spread to every caller at once")
          (counterexample "the switch is done by making doeff-records' handler clauses perform Absent on Missing — it skips the caller's maybe (R4)")]
       :enforced-by ["packages/doeff-linter/tests/stale_outcome_branch.rs::only_the_marked_lines_fire"
                     "packages/doeff-linter/tests/stale_outcome_branch.rs::a_hit_is_critical_and_keyed_by_the_definition_the_name_and_the_type"
                     "packages/doeff-linter/tests/stale_outcome_branch.rs::the_single_file_run_reports_the_same_lines"
                     "packages/doeff-linter/tests/stale_outcome_branch.rs::the_declaration_decides_not_the_name_of_the_type"
                     "packages/doeff-linter/tests/stale_outcome_branch.rs::the_registry_does_not_lower_a_hit"
                     "packages/doeff-linter/tests/stale_outcome_branch.rs::the_production_code_of_this_repo_has_no_stale_branches"]
       :wiring "wired (2026-10-23) — the doeff-linter rule that finds stale branches is DOEFF175 (critical, not lowered by the known-issue registry). It hits places where a type that defeffect declared with :absent / :failure is split as a value with a match arm or isinstance (including type tuples and cond branches) against a variable bound to that effect's answer ((<- x (E …)), (<- x T (E …)), (val x (! (E …)))) or a directly written (! (E …)). It does not hit :value types, success types, effects without a declaration, or defhandler clauses (matches the 17 lines marked as firing in packages/doeff-linter/tests/fixtures/stale_outcome_branch/). It is applied by the rust processing stage of the daily full verification (cargo test via make test-rust — tests/stale_outcome_branch.rs judges this repo's production code with the root settings and names hits by file and line), and locally by make lint-doeff and the editor's single-file run. The scope is the population of the definition rules ([tool.doeff-linter.definitions] in the root pyproject.toml — for doeff the 3 dirs of the main body, for agora-controllers controllers, services, and scripts). Repos without a definitions section (merge-queue, agent-commons) are not judged (rules' wired is false), so add the section before the change that switches an effect in such a repo. Answers routed through a defk ((<- x (read-typed …))) are not followed — they are fixed as branches on that defk's return type in the change that switches the defk's own effect")]
  :enforcement
    [(deftest test-adr-doe-core-effects-003-result-and-maybe-values
       ;; Fact: doeff.result's Ok / Err / Some / Nothing are usable in their current form (the values R2's boundary handlers return).
       (val boom (ValueError "boom"))
       (assert (= (. (Ok 1) value) 1))
       (assert (is (. (Err boom) error) boom))
       (assert (= (. (Some 2) value) 2))
       (assert (not Nothing) "Nothing is a single value that evaluates as false")
       ;; Maybe = the type union Some[T] | Nothing (subscripted, so it cannot be passed to isinstance) — check each member by name.
       (assert (isinstance (Some 2) Some))
       (assert (isinstance Nothing (type Nothing))))
     (deftest test-adr-doe-core-effects-003-try-folds-exceptions-into-result
       ;; Fact: Try folds a Python exception inside a Program into Err and success into Ok (R11 — the entry point kept as is).
       (val failed (run (try-handler (probe-try (probe-boom)))))
       (assert (isinstance failed Err) failed)
       (assert (isinstance (. failed error) ValueError) failed)
       (val succeeded (run (try-handler (probe-try (probe-one)))))
       (assert (isinstance succeeded Ok) succeeded)
       (assert (= (. succeeded value) 1)))
     (deftest test-adr-doe-core-effects-003-records-answers-mix-success-absence-failure
       ;; Fact: ReadRow's answer lists success, absence, and failure in one union (why R5's declaration is needed — before stage 3, so there is no declaration yet).
       ;; The three phrases are the Japanese text of packages/doeff-records/README.md, kept as \u escapes so the check still reads that file:
       ;; "failure answers", "returned as values", "only composition errors are raised as exceptions".
       (assert (= (get-args ReadRowAnswer) #(Row Missing Unreachable)))
       (val readme (.read-text (/ REPO-ROOT "packages/doeff-records/README.md") :encoding "utf-8"))
       (assert (in "\u5931\u6557\u306e\u7b54\u3048" readme))
       (assert (in "\u5024\u3067\u8fd4\u3059" readme))
       (assert (in "\u4f8b\u5916\u3067\u4e0a\u304c\u308b\u306e\u306f\u7d44\u307f\u7acb\u3066\u306e\u8aa4\u308a" readme)))
     (deftest test-adr-doe-core-effects-003-handler-without-resume-ends-the-scope
       ;; Fact: the VM allows a handler that returns a value without resuming, and ends the scope with that value (the continuation is discarded)
       ;; — R1's Absent and R2's maybe shape can be written on the current VM (this is also why R15's run-time check is needed).
       (val seen [])
       (assert (is (run (with-handlers [probe-maybe-handler] (probe-reads-then-continues seen))) Nothing))
       (assert (= seen []) "the receiver does not resume, so nothing after Absent runs"))
     (deftest test-adr-doe-core-effects-003-effect-performed-in-a-handler-skips-the-callers-scope
       ;; Fact (R4's pitfall): an effect performed inside a handler clause goes outside that handler. The maybe receiver placed on the body side
       ;; (between the body and the foundation handler) is skipped, and a receiver outside the foundation handler catches it.
       (val answer (run (with-handlers [probe-outer-handler probe-read-handler]
                          (with-handlers [probe-maybe-handler] (probe-reads-a-row)))))
       (assert (= answer "the outer one caught it") answer))
     (deftest test-adr-doe-core-effects-003-defhandler-clause-must-end-explicitly
       ;; R15 (at expansion): a clause that only returns a value, a clause that resumes on only one branch, a clause that resumes Raise, a cut-off without a reason, and a match without
       ;; a final _ are refused. (finish value) and a cut-off with a reason pass.
       ;; "Raise \u306e\u7bc0\u306f" / "Absent \u306e\u7bc0\u306f" ("the Raise clause" / "the Absent clause") are kept as \u escapes because
       ;; packages/doeff-hy/src/doeff_hy/_clause_endings.hy emits that Japanese text.
       (var refused False)
       (try
         (_check-clause-terminates "ProbeAbsent" [(hy.models.Symbol "Nothing")])
         (except [SyntaxError]
           (:= refused True)))
       (assert refused "a clause that only returns a value is refused")
       (_check-clause-terminates "ProbeAbsent" [(hy.read "(resume None)")])
       (_check-clause-terminates "ProbeAbsent" [(hy.read "(finish None)")])
       (val head "(require doeff-hy.macros [defhandler]) (import doeff_core_effects._effects [Raise Absent]) (defhandler probe-h ")
       (assert (in "missing resume" (run (probe-expansion-refusal (+ head "(ProbeRead [] \"value\"))")))) "forgotten resume")
       (assert (in "missing resume" (run (probe-expansion-refusal (+ head "(ProbeRead [] (if True (resume 1) None)))")))) "only one branch")
       (assert (in "missing resume" (run (probe-expansion-refusal (+ head "(ProbeRead [] (when True (resume 1))))")))) "when only")
       (assert (in "missing resume" (run (probe-expansion-refusal (+ head "(ProbeRead [] (match 1 1 (resume 1) 2 (resume 2))))")))) "match without a final _")
       (assert (in "Raise \u306e\u7bc0\u306f" (run (probe-expansion-refusal (+ head "(Raise [reason] (resume 0)))")))) "resuming Raise")
       (assert (in "Absent \u306e\u7bc0\u306f" (run (probe-expansion-refusal (+ head "(Absent [why] (transfer 0)))")))) "resuming Absent")
       (assert (in ":finish-reason" (run (probe-expansion-refusal (+ head "(ProbeRead [] (finish None)))")))) "cut-off without a reason")
       (assert (= "" (run (probe-expansion-refusal (+ head "(Raise [reason] (finish reason)) (Absent [why] (finish None)))")))))
       (assert (= "" (run (probe-expansion-refusal (+ head "(ProbeRead [] :finish-reason \"timed out\" (finish None)))"))))))
     (deftest test-adr-doe-core-effects-003-clause-endings-checked-at-install-and-run
       ;; R15 (at install and at run): resuming a Raise imported under an alias is refused when first installed on a body, and a clause that exits without
       ;; resume or finish on a path invisible at expansion is made an error at run time. A cut-off with a reason ends the handler's scope with a value.
       ;; The match text "\u3069\u308c\u306b\u3082\u5c4a\u304b\u305a\u306b\u629c\u3051\u305f" ("exited without reaching any of them") is kept as \u escapes because
       ;; packages/doeff-hy/src/doeff_hy/_clause_endings.hy emits that Japanese text.
       (with [(pytest.raises ClauseEndingError :match "Resumption.NEVER")]
         (probe-aliased-raise-resumer (probe-one)))
       (with [(pytest.raises RuntimeError :match "\u3069\u308c\u306b\u3082\u5c4a\u304b\u305a\u306b\u629c\u3051\u305f")]
         (run (probe-swallowing-handler (probe-reads-a-row))))
       (assert (= (run ((probe-deadline 1) (probe-reads-a-row))) "read ok"))
       (assert (= (run ((probe-deadline 0) (probe-reads-a-row))) "cut off") "a finish with a reason ends the scope with a value"))
     (deftest test-adr-doe-core-effects-003-traverse-fail-resumes-with-a-substitute
       ;; Fact: doeff-traverse's Fail is a resumable notice of failure, and normalize-to-none resumes the continuation with None
       ;; — the reason R1 does not call the failure side Fail, and also the reason R8 limits resuming with a default to explicit handlers.
       (assert (= (run (normalize-to-none (probe-fails-then-continues))) #("continued" None))))
     (deftest test-adr-doe-core-effects-003-declarations-carry-one-answer-and-optional-effects
       ;; Fact: defeffect holds the :answer type, and the split of the answer (:absent / :failure / :value) is optional — if not written,
       ;; there is no __doeff_outcomes__ (R5). defk's :effects is optional; if written it stays in __doeff_effects__, otherwise None (R13).
       (assert (in ":answer" EFFECT-KEYS))
       (assert (all (gfor key [":absent" ":failure" ":value"] (in key EFFECT-KEYS))))
       (assert (is (. ProbeAbsent __doeff_answer__) Never))
       (assert (is (getattr ProbeAbsent "__doeff_outcomes__" None) None))
       (assert (= (. probe-declared-effects __doeff_effects__) #(ProbeAbsent)))
       (assert (is (. probe-reads-then-continues __doeff_effects__) None)))
     (deftest test-adr-doe-core-effects-003-absent-and-raise-are-distinct
       ;; R1: Absent has only a description text, Raise has a reason, neither inherits from the other, a Python exception cannot be a Raise reason,
       ;; and boundary handlers do not resume the continuation.
       (assert (= (lfor f (fields Absent) f.name) ["why"]))
       (assert (= (lfor f (fields Raise) f.name) ["reason"]))
       (assert (not (or (issubclass Raise Absent) (issubclass Absent Raise))))
       (assert (= #((resumption-of Raise) (resumption-of Absent) (resumption-of ProbeRead))
                  #(Resumption.NEVER Resumption.ABSENT-AS-ONLY Resumption.REQUIRED)))
       (with [(pytest.raises TypeError)]
         (Raise (ValueError "an exception is not a reason")))
       (val seen [])
       (assert (is (run (maybe (probe-absent-then-note seen))) Nothing))
       (assert (= (repr (run (result (probe-raise-then-note seen)))) "Err(ProbeDown(detail='unreachable'))"))
       (assert (= seen []) "maybe and result do not resume the continuation"))
     (deftest test-adr-doe-core-effects-003-nesting-order-chooses-the-shape
       ;; (a) R2: the shape differs between (result (maybe b)) and (maybe (result b)) (Ok / Err cannot be compared by value, so compare by repr).
       (val shapes
         (lfor b [probe-one (fn [] (probe-absent-then-note [])) (fn [] (probe-raise-then-note []))]
               #((repr (run (result (maybe (b))))) (repr (run (maybe (result (b))))))))
       (assert (= shapes [#("Ok(Some(1))" "Some(Ok(1))")
                          #("Ok(Nothing)" "Nothing")
                          #("Err(ProbeDown(detail='unreachable'))" "Some(Err(ProbeDown(detail='unreachable')))")])
               shapes)
       (with [(pytest.raises UnhandledEffect)]
         (run (probe-absent-then-note [])))
       (with [(pytest.raises UnhandledEffect)]
         (run (probe-raise-then-note []))))
     (deftest test-adr-doe-core-effects-003-raise-in-a-handler-clause-skips-the-bodys-result
       ;; (b) R4: a Raise performed inside a handler clause skips the result wrapping the body and reaches the result outside the handler
       ;; (if the inner result had caught it, it would be Ok(Err …)).
       (val answer (run (result (probe-raising-read-handler (result (probe-reads-a-row))))))
       (assert (= (repr answer) "Err(ProbeConflict(detail='performed inside a handler clause'))") answer))
     (deftest test-adr-doe-core-effects-003-declared-bind-opens-the-answer
       ;; R5, R6: <- on an effect with a declaration binds success, and turns absence into Absent and failure into Raise(answer), performed in the caller's scope.
       (assert (isinstance ProbeDeclaredRead.__doeff_outcomes__ Outcomes))
       (assert (= (run (probe-rows (maybe (probe-read-value "a")))) (Some "A")))
       (assert (is (run (probe-rows (maybe (probe-read-value "zz")))) Nothing))
       (assert (= (repr (run (probe-rows (result (probe-read-value "down"))))) "Err(ProbeDown(detail='network is down'))"))
       (assert (is (run (probe-rows (maybe (ProbeDeclaredRead "zz")))) Nothing) "passing an effect with a declaration directly to maybe also opens it"))
     (deftest test-adr-doe-core-effects-003-undeclared-bind-is-unchanged
       ;; (c) R5: <- on an effect without a declaration yields the same thing and binds the same answer as before (absence and failure values are bound as is, nothing escapes).
       (val effect (ProbePlainRead "zz"))
       (assert (is (open-bind effect) effect) "an effect without a declaration yields the same object")
       (val program (probe-read-plain "zz"))
       (assert (is (open-bind program) program) "a Program also yields the same object")
       (assert (= (run (probe-rows (probe-read-plain "zz"))) (ProbeGone :key "zz")) "binds the absence value as is")
       (assert (= (run (probe-rows (probe-read-plain "down"))) (ProbeDown :detail "network is down")) "binds the failure value as is"))
     (deftest test-adr-doe-core-effects-003-a-bind-has-one-meaning
       ;; R3: the same bind line (the <- in probe-read-value) binds the success type under any of the caller's maybe, result, or on-raise.
       (val under-maybe (run (probe-rows (maybe (probe-read-value "a")))))
       (val under-result (run (probe-rows (result (probe-read-value "a")))))
       (val under-on-raise (run (probe-rows (on-raise (probe-read-value "a") (ProbeDown :detail d) d))))
       (assert (= #(under-maybe (repr under-result) under-on-raise) #((Some "A") "Ok('A')" "A"))
               #(under-maybe under-result under-on-raise)))
     (deftest test-adr-doe-core-effects-003-absent-option-maps-one-bind
       ;; (e) R6: <-'s :absent maps an absence inside that bind (including one deep inside a called defk) to Raise(failure). Failures pass through as is.
       (assert (= (repr (run (probe-rows (result (probe-read-or-conflict "zz")))))
                  "Err(ProbeConflict(detail='zz has no row'))"))
       (assert (= (repr (run (probe-rows (result (probe-read-or-conflict "down")))))
                  "Err(ProbeDown(detail='network is down'))"))
       (assert (= (repr (run (probe-rows (result (probe-read-or-conflict "a"))))) "Ok('A')")))
     (deftest test-adr-doe-core-effects-003-on-raise-refuses-python-exceptions-and-catch-all
       ;; (d) R7: on-raise does not catch Python exceptions, catch-all patterns are refused at expansion, and a case naming an exception type is refused at construction.
       ;; "\u53d7\u3051\u3089\u308c\u306a\u3044" ("cannot be caught") is kept as \u escapes because
       ;; packages/doeff-hy/src/doeff_hy/_outcome_forms.hy emits that Japanese text.
       (with [(pytest.raises ValueError :match "boom")]
         (run (probe-raises-python)))
       (for [pattern ["_" "reason" "(Exception)" "(object)"]]
         (assert (in "\u53d7\u3051\u3089\u308c\u306a\u3044" (run (probe-expansion-refusal (+ "(require doeff-hy.macros [on-raise]) (on-raise body " pattern " 0)"))))
                 pattern))
       (with [(pytest.raises TypeError)]
         (RaiseCase #(ValueError) (fn [r] (Some r))))
       (assert (= (run (on-raise (probe-raise-then-note []) (ProbeDown :detail d) d)) "unreachable") "a Raise of a matching type is mapped"))
     (deftest test-adr-doe-core-effects-003-absent-as-resumes-only-direct-binds
       ;; R8: absent-as resumes with the default only for absences from <- in its literal text, and ends the scope with the default for deep absences and directly written Absent.
       (val direct (run (probe-rows (probe-direct-default "zz"))))
       (val deep (run (probe-rows (probe-deep-default "zz"))))
       (val written (run (probe-written-absent-default)))
       (val present (run (probe-rows (probe-direct-default "a"))))
       (assert (= #(direct deep written present) #("continued: default" "default" "default" "continued: A"))
               #(direct deep written present)))]
  :plans ["the switching procedure for stage 3 and stage 5 = R14 of this ADR (switch per effect, in the same change)"])
