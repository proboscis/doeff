;;; Fixture for DOEFF175 (stale branches on an effect whose declaration was switched — ADR-DOE-CORE-EFFECTS-003 R14 (4)) — fires on branches (match arms, isinstance) that split, as values, types an effect declared via defeffect as absent / failure,
;;; on the variable bound to that effect's answer; and does not fire on types declared :value, success types, effects with no declaration,
;;; defhandler clauses, same-named types from another module, rebound names, or answers that went through a defk.
;;; A trailing "; fires" at the end of a line marks a line that should fire: tests/stale_outcome_branch.rs compares the set of marked lines with the set of linter hit lines
;;; (removing the rule, or narrowing or widening the form, each makes the sets differ and goes red). The declarations are in effects.hy (app.effects) in the same dir. This file is never run (read-only fixture).
(require doeff-hy.macros [defk deftest defhandler <- val var])
(import app.effects [ReadRow PostRow PlainRead Row Missing Unreachable Conflict Accepted Refused Gone])
(import app.effects :as effects)
(import app.other :as other)
(import doeff-core-effects [maybe])


;; ---------------------------------------------------------------------------
;; Fires: a declared absent / failure type is split as a value on the variable bound to the answer

(defk match-an-absent-arm [key]
  {:pre [(: key str)] :post [(: % (| str None))] :tags {:context "sample" :role "program"}}
  "ReadRow declared Missing as absent — found is only a successful Row, so this arm is never taken."
  (<- found (ReadRow key))
  (match found
    (Missing) (return None)  ; fires
    (Row) found.value))


(defk match-a-failure-arm [key]
  {:pre [(: key str)] :post [(: % (| str None))] :tags {:context "sample" :role "program"}}
  "Unreachable is ReadRow's failure declaration (even a pattern that takes fields hits on the head of the type)."
  (<- found (ReadRow key))
  (match found
    (Unreachable :detail why) (return why)  ; fires
    _ found.value))


(defk match-an-alternation [key]
  {:pre [(: key str)] :post [(: % (| str None))] :tags {:context "sample" :role "program"}}
  "Each alternative of `(| (A) (B))` is counted separately (2 hits on the same line)."
  (<- found (ReadRow key))
  (match found
    (| (Missing) (Unreachable)) (return None)  ; fires
    (Row) found.value))


(defk isinstance-an-absent [key]
  {:pre [(: key str)] :post [(: % (| str None))] :tags {:context "sample" :role "program"}}
  "Splits on the absent type with isinstance."
  (<- found (ReadRow key))
  (when (isinstance found Missing)  ; fires
    (return None))
  found.value)


(defk isinstance-a-tuple [key]
  {:pre [(: key str)] :post [(: % (| str None))] :tags {:context "sample" :role "program"}}
  "Each type in a tuple is counted separately (2 hits on the same line)."
  (<- found (ReadRow key))
  (if (isinstance found #(Missing Unreachable))  ; fires
      None
      found.value))


(defk isinstance-a-union [key]
  {:pre [(: key str)] :post [(: % (| str None))] :tags {:context "sample" :role "program"}}
  "Each type in a (| A B) type list is also counted separately (Row is a success type, so it is not counted)."
  (<- found (ReadRow key))
  (if (isinstance found (| Row Unreachable))  ; fires
      found
      None))


(defk cond-branch [key]
  {:pre [(: key str)] :post [(: % (| str None))] :tags {:context "sample" :role "program"}}
  "isinstance in a cond branch."
  (<- found (ReadRow key))
  (cond
    (isinstance found Unreachable) None  ; fires
    True found.value))


(defk typed-bind [key]
  {:pre [(: key str)] :post [(: % (| str None))] :tags {:context "sample" :role "program"}}
  "A binding with a type annotation, `(<- x T (E …))`, also binds an effect's answer."
  (<- found (| Row Missing Unreachable) (ReadRow key))
  (match found
    (Unreachable) None  ; fires
    (Row) found.value))


(defk bang-bound [key]
  {:pre [(: key str)] :post [(: % (| str None))] :tags {:context "sample" :role "program"}}
  "A name bound with (val x (! (E …)))."
  (val found (! (ReadRow key)))
  (match found
    (Missing) None  ; fires
    (Row) found.value))


(defk bang-subject [key]
  {:pre [(: key str)] :post [(: % (| str None))] :tags {:context "sample" :role "program"}}
  "A form that splits (! (E …)) directly without binding it (the subject of match / isinstance)."
  (match (! (ReadRow key))
    (Missing) None  ; fires
    (Row) "row")
  (when (isinstance (! (ReadRow key)) Unreachable)  ; fires
    (return None))
  key)


(defk absent-suffix-bind [key]
  {:pre [(: key str)] :post [(: % (| str None))] :tags {:context "sample" :role "program"}}
  "An x bound with (<- x (E …) :absent F) is also only a success value (absence becomes F's failure)."
  (<- found (ReadRow key) :absent (Conflict :detail "no such row"))
  (match found
    (Missing) None  ; fires
    (Row) found.value))


(defk module-alias [key]
  {:pre [(: key str)] :post [(: % (| str None))] :tags {:context "sample" :role "program"}}
  "An import alias (an effect and type prefixed with the module name) is also resolved to the module before comparing."
  (<- found (effects.ReadRow key))
  (match found
    (effects.Missing) None  ; fires
    (effects.Row) found.value))


(defk a-second-effect [id]
  {:pre [(: id str)] :post [(: % (| str None))] :tags {:context "sample" :role "program"}}
  "PostRow declared Refused as failure and Gone as absent (declarations are per effect)."
  (<- ack (PostRow id))
  (match ack
    (Refused) None  ; fires
    (Gone) None  ; fires
    (Accepted) ack.id))


(defk nested-in-a-branch [key ready]
  {:pre [(: key str)] :post [(: % (| str None))] :tags {:context "sample" :role "program"}}
  "Both bindings and branches are read inside when and if as well."
  (when ready
    (<- found (ReadRow key))
    (match found
      (Missing) (return None)  ; fires
      (Row) (return found.value)))
  None)


(deftest reads-a-row-in-a-test
  "Test callers also keep stale branches when an effect is switched."
  (<- found (ReadRow "k"))
  (assert (not (isinstance found Missing)))  ; fires
  (assert (= found.value "v")))


;; ---------------------------------------------------------------------------
;; Does not fire

(defk value-arm [key]
  {:pre [(: key str)] :post [(: % (| str None))] :tags {:context "sample" :role "program"}}
  "A branch on a type declared :value (Conflict) passes, since it is a value that can be bound as the answer."
  (<- found (ReadRow key))
  (match found
    (Conflict :detail why) why
    (Row) found.value))


(defk success-arm [key]
  {:pre [(: key str)] :post [(: % (| str None))] :tags {:context "sample" :role "program"}}
  "A branch on the success type (Row) passes."
  (<- found (ReadRow key))
  (when (isinstance found Row)
    (return found.value))
  (if (isinstance found #(Row Conflict)) found None))


(defk plain-effect [key]
  {:pre [(: key str)] :post [(: % (| str None))] :tags {:context "sample" :role "program"}}
  "The answer of an effect with no declaration is bound as is, without conversion, so both Missing and Unreachable arrive as values."
  (<- found (PlainRead key))
  (when (isinstance found Unreachable)
    (return None))
  (match found
    (Missing) None
    (Row) found.value))


(defhandler row-handler
  "A defhandler clause is the side that receives the effect (the side that makes the answer) — neither a match inside a clause nor a branch on a name bound inside a clause is read."
  {:tags {:context "sample" :role "foundation"}}
  (ReadRow [key]
    (match (lookup-row key)
      (Missing) (resume (Missing :key key))
      (Unreachable) (resume (Unreachable :detail "down"))
      row (resume row)))
  (PostRow [id]
    (<- inner (ReadRow id))
    (match inner
      (Missing) (resume (Gone :id id))
      (Row) (resume (Accepted :id id)))))


(defk another-modules-type [key]
  {:pre [(: key str)] :post [(: % (| str None))] :tags {:context "sample" :role "program"}}
  "A same-named type from another module (app.other.Missing) is not the declared type."
  (<- found (ReadRow key))
  (when (isinstance found other.Missing)
    (return None))
  (match found
    (other.Missing) None
    (Row) found.value))


(defk a-type-this-effect-did-not-declare [id]
  {:pre [(: id str)] :post [(: % (| str None))] :tags {:context "sample" :role "program"}}
  "Missing is a type ReadRow declared, and PostRow did not declare it (declarations are per effect)."
  (<- ack (PostRow id))
  (match ack
    (Missing) None
    (Accepted) ack.id))


(defk a-rebound-name [key]
  {:pre [(: key str)] :post [(: % (| str None))] :tags {:context "sample" :role "program"}}
  "A rebound name is no longer the effect's answer."
  (<- found (ReadRow key))
  (setv found (Missing :key key))
  (match found
    (Missing) None
    (Row) found.value))


(defk a-shadowing-parameter [key]
  {:pre [(: key str)] :post [(: % int)] :tags {:context "sample" :role "program"}}
  "Inside a scope where a function parameter binds the same name, it is not the effect's answer."
  (<- found (ReadRow key))
  (val score (fn [found] (match found (Missing) 0 _ 1)))
  (score found.value))


(defk wrapped-in-maybe [key]
  {:pre [(: key str)] :post [(: % (| str None))] :tags {:context "sample" :role "program"}}
  "An answer wrapped in maybe is a Maybe value, not the effect's answer (the head is not an effect)."
  (<- wrapped (maybe (ReadRow key)))
  (match wrapped
    (Missing) None
    _ wrapped))


(defk a-nested-pattern [key]
  {:pre [(: key str)] :post [(: % (| str None))] :tags {:context "sample" :role "program"}}
  "A type inside a pattern's field is not the answer's type (the shape where Row's field value is Missing)."
  (<- found (ReadRow key))
  (match found
    (Row :value (Missing)) None
    (Row) found.value))


(defk through-a-defk [key]
  {:pre [(: key str)] :post [(: % (| str None))] :tags {:context "sample" :role "program"}}
  "An answer that went through a defk is not followed (the head of the bound expression is not an effect)."
  (<- found (fetch-row key))
  (match found
    (Missing) None
    (Row) found.value))


(defk quoted-form [key]
  {:pre [(: key str)] :post [(: % (| str None))] :tags {:context "sample" :role "program"}}
  "A quoted form is not read."
  (<- found (ReadRow key))
  '(match found (Missing) None)
  found.value)
