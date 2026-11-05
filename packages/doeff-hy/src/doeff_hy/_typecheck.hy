;;; TypeCheck effect — switch runtime type checking by handler (fence 3).
;;;
;;; Origin = agora-redesign stage 7 lane 7c (fence of decision 1.4: "TypeCheck effect
;;; (static always on, runtime switched by handler)", `docs/plans/decisions-merge-2026-09-12.md`).
;;;
;;; Two layers of type checking:
;;;   - Static is **always on**: project Hy source into a Python AST and run it through
;;;     pyright (the shared quality check `~/dotfiles/agent/quality/hy_typecheck.py`).
;;;     There is no switch — a public surface without types goes red by that alone.
;;;   - Runtime is **switched**: a check that looks at the value itself is issued as a request (TypeCheck),
;;;     and a handler decides whether to fulfill it. The default is the house that does not fulfill (off),
;;;     and tests install the house that does (on).
;;;
;;; Why the default is off: runtime checks pay twice for what static analysis already knows. On a surface where static is always
;;; in force they are redundant and touch hot-path values every time. Only boundaries (values from outside,
;;; rows read from the ledger) are turned on in tests and verified; production runs on the static guarantee.
;;;
;;; Known shape: algebraic effects. The request is a value, the handler is the house of execution, and the
;;; composition root chooses which house to apply.


(import dataclasses [dataclass])
(import doeff [EffectBase])


(defclass TypeCheckError [TypeError]
  "A runtime type check failed. A value at a boundary invisible to static analysis differs from its contract.")


(defclass [(dataclass :frozen True :kw-only True)] TypeCheck [EffectBase]
  "A request to verify that a value has the expected type.

   owner    = name of the function or boundary that requested the check (a hint for whoever reads the red)
   target   = name of the value (argument name; `%` = return value)
   expected = expected type (a type or tuple of types acceptable to isinstance)
   value    = the value to inspect
   The house that fulfills it returns the value as is (off), or sends TypeCheckError if it is violated (on)."
  #^ str owner
  #^ str target
  #^ object expected
  #^ object value)


(defn #^ TypeCheck type-check [#^ str owner #^ str target #^ object expected #^ object value]
  "Build the request (not yet fulfilled). The caller binds it with `<-`.

   `expected` is a type (or tuple of types) acceptable to isinstance, and `value` may be
   any value — making this a defk with a contract would make the :pre type object, which would
   break fence 2 (the ban on `\"value\"`, Any, and object) ourselves. So the request is
   built by a plain function, and the contract lives in the request field types (the TypeCheck declaration)."
  (TypeCheck :owner owner :target target :expected expected :value value))


(defn #^ (| str None) type-check-verdict [#^ str owner #^ str target #^ object expected #^ object value]
  "Pure judgment: the red message if violated, None if it passes.

   Both the runtime house (on) and the static check read this single point — the definition
   of the wording is not buried inside a handler."
  (when (not (or (isinstance expected type) (isinstance expected tuple)))
    (raise (TypeError (+ owner ": expected type is not a type: " (repr expected)))))
  (if (isinstance value expected)
      None
      (+ owner ": " target " has the wrong type: expected " (str expected)
         " / actual " (. (type value) __name__))))
