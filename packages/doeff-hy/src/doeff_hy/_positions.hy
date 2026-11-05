;;; Attach the position of the enclosing user expression to expressions synthesized by macros (so a traceback points at the line that failed).
;;;
;;; What was happening (measured 2026-09-23): when the body of `defk` / `defservice` failed, the traceback
;;; pointed at the start of the body (the `defk` line) instead of the line that failed.
;;;
;;; Mechanism: Hy fills models in a macro result that have no position with the position of
;;; the macro call (`replace_hy_obj(obj, tree)` in `hy.macros.macroexpand` = recursive `replace`).
;;; doeff-hy macros rebuild body expressions (`_expand-bangs` reassembles every Expression in
;;; the body with `(hy.models.Expression ...)`; `<-` synthesizes `(setv x (yield e))`;
;;; `defhandler` rewrites `resume` into `(yield (Resume k e))`). The rebuilt expressions have no
;;; position, so Hy puts the macro call's position (= the head of `defk`) there, and the Python
;;; code object's line table takes that position. Leaves (Symbols, numbers) keep the user's position,
;;; so in the AST the statement `c = x // 0` has the right line, but only the expression `x // 0` carries the range of `defk`.
;;;
;;; The fix (one point): apply `locate-synthesized` before returning the macro output. A Sequence
;;; with no position gets the range of its positioned children (smallest start to largest end).
;;; Children are filled first, so nested synthesis (`(do (setv x (yield e)) (assert ...))`) also gets
;;; the user expression's range from the inside out. Synthesis that contains no user expression at all (a call to `_guard-performed`,
;;; for example) stays without a position, and Hy fills it with the macro call's position as before.
;;; Then, from the top, children of a positioned expression that lack a position (synthesized leaves = the function name of `_guard-statement-value`,
;;; for example) get the parent's position. Models that already have a position (user expressions made by the reader)
;;; are left untouched.

(import hy.models [Object Sequence])

(defn #^ bool _positioned? [#^ Object model]
  (hasattr model "_start_line"))

(defn #^ None _copy-position [#^ Object target #^ Object origin]
  (setv (. target start-line) (. origin start-line)
        (. target start-column) (. origin start-column)
        (. target end-line) (. origin end-line)
        (. target end-column) (. origin end-column)))

(defn #^ None _inherit-down [#^ Sequence tree]
  "Give the parent's position to children of a positioned expression that lack one (synthesized leaves, and synthesis containing no user expression).
   So that the function name in `(_guard-statement-value form ...)` and the `k` in `(yield (Resume k v))` carry
   the position of the user expression that wraps them, not the position of the macro call."
  (for [child tree]
    ;; A bare Python value that a macro splices in with `~(str name)` is not yet a model (Hy
    ;; makes it one later), so it cannot take attributes. Attach only to models.
    (when (and (isinstance child Object) (not (_positioned? child)))
      (_copy-position child tree))
    (when (isinstance child Sequence)
      (_inherit-down child))))

(defn #^ Object locate-synthesized [#^ Object tree]
  "Attach positions to a macro output and return it (rewritten in place).

   1. Bottom-up: give a Sequence with no position the range of its positioned children (`_locate-up`).
   2. Top-down: give the parent's position to children of a positioned expression that still lack one (`_inherit-down`).
   When the outermost expression has no position (it contains no user expression), do nothing — Hy fills it with the macro
   call's position."
  (_locate-up tree)
  (cond
    (not (isinstance tree Sequence)) None
    (_positioned? tree) (_inherit-down tree)
    True (for [child tree]
           (when (and (isinstance child Sequence) (_positioned? child))
             (_inherit-down child))))
  tree)

(defn #^ Object _locate-up [#^ Object tree]
  "Give a Sequence with no position the range of its positioned children, and return tree (rewritten in place).

   Synthesis can also sit inside a positioned user expression (children of an expression rebuilt by `_expand-bangs`),
   so always descend into children regardless of position, and attach a position to the node itself only when it has none."
  (when (isinstance tree Sequence)
    (for [child tree]
      (_locate-up child))
    (when (not (_positioned? tree))
      (setv located (lfor child tree :if (_positioned? child) child))
      (when located
        (setv first (min located :key (fn [m] #((. m start-line) (. m start-column))))
              last (max located :key (fn [m] #((. m end-line) (. m end-column)))))
        (setv (. tree start-line) (. first start-line)
              (. tree start-column) (. first start-column)
              (. tree end-line) (. last end-line)
              (. tree end-column) (. last end-column)))))
  tree)
