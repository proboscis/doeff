;;; How defhandler / handle clauses end (ADR-DOE-CORE-EFFECTS-003 R15): the ending clause (finish value), and checks that stop a forgotten
;;; resume at three points in time.
;;;
;;;   Expansion time    Every path of a clause ends in one of the clause operations (resume, transfer, finish, reperform, raise; names and properties are in the clause_ops.hy table)
;;;                     (_terminates in handle.hy).
;;;                     If the effect name the clause refers to is Raise / Absent, resume and transfer are refused. A clause that cuts off another effect with finish
;;;                     needs a reason (:finish-reason "...").
;;;   Definition time   When the expression that builds the handler is evaluated, the resumption handling that the clause's effect type declares (__doeff_resumption__ --
;;;                     doeff_core_effects._effects.Resumption) is checked against how the clause ends (this also catches a Raise imported under an alias).
;;;   Run time          If a clause exits without either resume or finish, RuntimeError (the scope is not silently ended with None).
;;;
;;; handle.hy imports this module inside a function at expansion time (handle.hy is required by macros.hy, so helpers that use deff
;;; are placed outside handle.hy). The expanded handler imports this module for the definition-time check.

(require doeff-hy.macros [deff])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import collections.abc [Callable])
(import types [UnionType])
(import typing [Union get-args get-origin])
(import hy)
(import hy.models [Expression Symbol Keyword String Sequence Object Tuple])
(import doeff_core_effects._effects [Resumption resumption-of])
;; The operations that end a clause and how they end it (resume the continuation, cut it off, ...) are looked up from the clause_ops.hy table; the list of names is not copied here
;; (when a copy was not updated, the Raise check for a new operation silently slipped through). A non-tail resume returns to the clause,
;; so the run-time check looks at it.
(import doeff-hy._clause-ops [Ending clause-op ops-ending])


;; Effects identifiable by name (ones that can be refused at expansion time) and their resumption handling.
(setv NAMED-RESUMPTION {"Raise" Resumption.NEVER "Absent" Resumption.ABSENT-AS-ONLY})


(defrecord MatchArm
  #^ Object pattern
  #^ (| Object None) guard
  #^ Object body)


(defrecord ClauseOptions
  #^ (| Object None) guard
  #^ (| String None) finish-reason
  #^ list body)


(defclass ClauseEndingError [TypeError]
  "The way a clause ends contradicts the effect's resumption declaration (raised at definition time).")


(deff parse-clause-options [forms where]  ; cannot be a defk: a function called at macro expansion time
  {:pre [(: forms list) (: where str)] :post [(: % ClauseOptions)]
   :tags {:context "doeff-hy-handler" :role "foundation"}}
  "Separates the leading keys of a clause body, `:when guard` and `:finish-reason \"reason\"` (in either order). An unknown key, or a reason that is not a
   non-empty string literal, is a SyntaxError."
  (let [items (list forms)
        found {}]
    (while (and (>= (len items) 2) (isinstance (get items 0) Keyword))
      (let [key (str (.pop items 0))
            value (.pop items 0)]
        (when (not-in key #{":when" ":finish-reason"})
          (raise (SyntaxError (+ where ": clause key " key " is not accepted; the accepted keys are :when and :finish-reason"))))
        (when (in key found)
          (raise (SyntaxError (+ where ": clause key " key " appears twice"))))
        (when (and (= key ":finish-reason") (not (and (isinstance value String) (.strip (str value)))))
          (raise (SyntaxError (+ where ": :finish-reason must be a string giving the reason for cutting off (a non-empty literal): " (.lstrip (hy.repr value) "'")))))
        (setv (get found key) value)))
    (ClauseOptions :guard (.get found ":when") :finish-reason (.get found ":finish-reason") :body items)))


(deff irrefutable-pattern? [pattern]  ; cannot be a defk: a function called at macro expansion time
  {:pre [(: pattern Object)] :post [(: % bool)]
   :tags {:context "doeff-hy-handler" :role "foundation"}}
  "Checks whether the last arm of a match is a catch-all (`_` or a single-name capture); without one, a path remains where a non-matching value slips through."
  (and (isinstance pattern Symbol) (not-in "." (str pattern))))


(deff match-terminates? [form]  ; cannot be a defk: a function called at macro expansion time
  {:pre [(: form Expression)] :post [(: % bool)]
   :tags {:context "doeff-hy-handler" :role "foundation"}}
  "Checks whether a (match subject pattern [:if guard] body ...) clause ends on every path: the body of every arm ends, and the last arm is a catch-all
   with no guard (so there is no path where a non-matching value slips through and silently ends the scope; R15)."
  (import doeff-hy.handle [_terminates])
  (let [arms (match-arms form)]
    (and (is-not arms None)
         (bool arms)
         (is (. (get arms -1) guard) None)
         (irrefutable-pattern? (. (get arms -1) pattern))
         (all (gfor arm arms (_terminates arm.body))))))


(deff match-arms [form]  ; cannot be a defk: a function called at macro expansion time
  {:pre [(: form Expression)] :post [(: % (| list None))]
   :tags {:context "doeff-hy-handler" :role "foundation"}}
  "Splits (match subject pattern [:if guard] body ...) into a list of arms (None if any arm lacks a body). It also keeps each arm's guard form;
   the clause-ending check (match-terminates?) and the rewrite that turns a tail resume into transfer (_match-cases-to-transfer in handle.hy)
   use the same split (there are not two ways of splitting)."
  (let [items (list (cut form 2 None))
        arms []]
    (while items
      (let [pattern (.pop items 0)
            guarded (and (bool items) (isinstance (get items 0) Keyword) (= (str (get items 0)) ":if"))
            guard (when guarded
                    (.pop items 0)
                    (when items (.pop items 0)))]
        (if items
            (.append arms (MatchArm :pattern pattern :guard guard :body (.pop items 0)))
            (return None))))
    arms))


(deff try-terminates? [form]  ; cannot be a defk: a function called at macro expansion time
  {:pre [(: form Expression)] :post [(: % bool)]
   :tags {:context "doeff-hy-handler" :role "foundation"}}
  "Checks whether a (try body ... (except [...] ...) ...) clause ends on every path: the body ends and each except body also ends
   (a path that caught an exception also reaches one of resume, finish, raise)."
  (import doeff-hy.handle [_head-name _seq-terminates])
  (let [parts (list (cut form 1 None))
        body (lfor f parts :if (not-in (_head-name f) #{"except" "except*" "else" "finally"}) f)
        handlers (lfor f parts :if (in (_head-name f) #{"except" "except*"}) f)]
    (and (_seq-terminates body)
         (all (gfor h handlers (_seq-terminates (list (cut h 2 None))))))))


(deff ops-in [node]  ; cannot be a defk: a function called at macro expansion time
  {:pre [(: node Object)] :post [(: % frozenset)]
   :tags {:context "doeff-hy-handler" :role "foundation"}}
  "The set of names of ending operations used in the form (not counting those inside quote), for checking against the effect's resumption declaration."
  (let [head (when (and (isinstance node Expression) (> (len node) 0) (isinstance (get node 0) Symbol))
               (str (get node 0)))]
    (match node
      (Expression) :if (in head #{"quote" "quasiquote"}) (frozenset)
      (Expression) :if (is-not (clause-op node) None) (| (frozenset [head]) #* (gfor child node (ops-in child)))
      (Sequence) (| (frozenset) #* (gfor child node (ops-in child)))
      _ (frozenset))))


(deff clause-ops [forms]  ; cannot be a defk: a function called at macro expansion time
  {:pre [(: forms list)] :post [(: % frozenset)]
   :tags {:context "doeff-hy-handler" :role "foundation"}}
  "The set of names of ending operations used by the clause body."
  (| (frozenset) #* (gfor form forms (ops-in form))))


(deff effect-name [etype]  ; cannot be a defk: a function called at macro expansion time
  {:pre [(: etype Object)] :post [(: % (| str None))]
   :tags {:context "doeff-hy-handler" :role "foundation"}}
  "The name of the effect type at the head of the clause (the last segment if dotted), to identify effects recognizable by name (Raise / Absent)."
  (when (isinstance etype Symbol)
    (get (.split (str etype) ".") -1)))


(deff check-clause-by-name [etype ops options where]  ; cannot be a defk: a function called at macro expansion time
  {:pre [(: etype Object) (: ops frozenset) (: options ClauseOptions) (: where str)] :post [(: % None)]
   :tags {:context "doeff-hy-handler" :role "foundation"}}
  "Turns violations detectable at expansion time into SyntaxError: resume / transfer in a Raise clause (it would continue as if a failure had succeeded),
   resume / transfer in an Absent clause (only absent-as may resume), a clause that cuts off another effect with finish without a reason,
   and :finish-reason on a clause with no finish. Which operations resume and which cut off is decided by the ending (Ending) in the clause_ops.hy table."
  (let [named (.get NAMED-RESUMPTION (effect-name etype))
        resumes (ops-ending ops Ending.RESUMES)
        finishes (ops-ending ops Ending.FINISHES)]
    (when (and (= named Resumption.NEVER) resumes)
      (raise (SyntaxError (+ where ": A Raise clause cannot " (.join "/" (sorted resumes)) ": nobody resumes a Raise"
                             " (it would continue as if a failure had succeeded). End the scope with (finish value) or pass it outward with (reperform effect)"
                             " [ADR-DOE-CORE-EFFECTS-003 R15]"))))
    (when (and (= named Resumption.ABSENT-AS-ONLY) resumes)
      (raise (SyntaxError (+ where ": An Absent clause cannot " (.join "/" (sorted resumes)) ": the only thing that may resume an Absent is"
                             " absent-as. End the scope with (finish value) or pass it outward with (reperform effect)"
                             " [ADR-DOE-CORE-EFFECTS-003 R8, R15]"))))
    (when (and (is named None) finishes (is options.finish-reason None))
      (raise (SyntaxError (+ where ": to cut off an ordinary effect's clause with (finish ...), write :finish-reason \"reason\" in the clause"
                             " (e.g. stopping the whole process on timeout; this distinguishes it from a forgotten resume) [ADR-DOE-CORE-EFFECTS-003 R15]"))))
    (when (and (is-not options.finish-reason None) (not finishes))
      (raise (SyntaxError (+ where ": :finish-reason is given but the clause has no (finish ...)"))))
    None))


(deff ending-spec-form [etype ops options]  ; cannot be a defk: a function called at macro expansion time
  {:pre [(: etype Object) (: ops frozenset) (: options ClauseOptions)] :post [(: % Tuple)]
   :tags {:context "doeff-hy-handler" :role "foundation"}}
  "The form describing one clause, passed to the definition-time check: #(effect type, tuple of names of operations used, whether a reason is present)."
  `#(~etype #(~@(lfor op (sorted ops) (String op))) ~(is-not options.finish-reason None)))


(deff check-clause-endings [handler clauses]  ; cannot be a defk: a check the expanded handler calls at definition time (performs no effect)
  {:pre [(: handler str) (: clauses list)] :post [(: % None)]
   :tags {:context "doeff-hy-handler" :role "foundation"}}
  "Checks how each clause of the handler ends against the resumption handling (Resumption) declared by the clause's effect type. Cases not identifiable by name
   (a Raise imported under an alias, a type that declares __doeff_resumption__) are stopped here. A violation is a ClauseEndingError."
  (for [#(etype ops has-reason) clauses]
    (let [types (match etype
                  (type) [etype]
                  (tuple) (list etype)
                  (UnionType) (list (get-args etype))
                  _ :if (is (get-origin etype) Union) (list (get-args etype))
                  _ [])
          resumes (ops-ending ops Ending.RESUMES)]
      (for [effect-type types]
        (let [declared (resumption-of effect-type)
              where (+ handler " clause " effect-type.__name__)]
          (match declared
            Resumption.NEVER :if resumes
              (raise (ClauseEndingError (+ where ": " effect-type.__name__ " is an effect that is never resumed (Resumption.NEVER), but the clause does "
                                           (.join "/" (sorted resumes)) " [ADR-DOE-CORE-EFFECTS-003 R15]")))
            Resumption.ABSENT-AS-ONLY :if resumes
              (raise (ClauseEndingError (+ where ": " effect-type.__name__ " may be resumed only by absent-as, but the clause does "
                                           (.join "/" (sorted resumes)) " [ADR-DOE-CORE-EFFECTS-003 R8, R15]")))
            Resumption.REQUIRED :if (and (ops-ending ops Ending.FINISHES) (not has-reason))
              (raise (ClauseEndingError (+ where ": an effect that is resumed is cut off with (finish ...) but there is no :finish-reason"
                                           " [ADR-DOE-CORE-EFFECTS-003 R15]")))
            _ None)))))
  None)


;; defhandler definitions whose clause-ending check is done (the code object of the specs function that builds the clause descriptions; one per definition). The check is
;; determined by the clause's effect types alone and does not depend on the arguments of a handler that takes arguments. When the done-mark was placed on the handler's body (data), a handler with arguments had a new body every time it was built, so
;; handlers built per request (such as wait-stamps in the record service) redid the check every time (about 10% of the CPU of
;; the scheduler thread for one request).
(setv CHECKED-DEFINITIONS (set))


(deff check-clause-endings-once [data handler specs]  ; cannot be a defk: a check called when the expanded handler wraps a body (performs no effect)
  {:pre [(: data Callable) (: handler str) (: specs Callable)] :post [(: % None)]
   :tags {:context "doeff-hy-handler" :role "foundation"}}
  "Runs the clause-ending check of a defhandler once per definition, when the handler first wraps a body (the module has been fully loaded by then,
   so effect types defined after the handler can also be resolved). The done-mark is kept in the definition marker (CHECKED-DEFINITIONS, the code object of specs).
   data is accepted so as not to change the shape of the expansion's call."
  (setv definition specs.__code__)
  (when (not-in definition CHECKED-DEFINITIONS)
    (check-clause-endings handler (specs))
    (.add CHECKED-DEFINITIONS definition))
  None)


(deff fell-through [handler effect-label]  ; cannot be a defk: called by the expanded handler's clause at run time (only builds an exception)
  {:pre [(: handler str) (: effect-label str)] :post [(: % RuntimeError)]
   :tags {:context "doeff-hy-handler" :role "foundation"}}
  "The error for when a clause exits without either resume or finish (the scope is not silently ended with None; R15)."
  (RuntimeError (+ handler " clause " effect-label " exited without reaching any of resume / transfer / finish / reperform. "
                   "The scope is not ended silently. Resume on every path, or end with (finish value) [ADR-DOE-CORE-EFFECTS-003 R15]")))
