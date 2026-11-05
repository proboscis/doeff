;;; The two homes that fulfil TypeCheck (fence 3). Default is off; in checks, on.
;;;
;;; Origin = agora-redesign stage 7 lane 7c (decision 1.4 fence "the TypeCheck effect").
;;; The request vocabulary and the wording of the error are held in one place, `doeff_hy._typecheck`, and this module holds
;;; only "fulfil or not" (no judgment is placed in the handler).
;;;
;;; Warning: this module is out of reach of the static projection of the common quality checks (the projection of `defhandler` is
;;; not supported yet -- as of 2026-09-12). So no judgment is placed here at all, and the request vocabulary and the
;;; verdict side (`typecheck.hy`) are kept projectable.

(require doeff-hy.handle [defhandler])
;; Tag at the head of the module (DOEFF112 of doeff-linter) -- val needs the require of doeff-hy.macros, so this module writes it with setv.
(setv MODULE-TAGS {:context "typecheck" :role "foundation"})

(import doeff [run])
(import doeff_hy._typecheck [TypeCheck TypeCheckError type-check-verdict])


(defhandler type-checks-off
  "Default home: receives requests but does not look at values (run-time checks are off).

   Static checks are always in effect, so production runs in this home."

  (TypeCheck [owner target expected value]
    (resume value)))


(defhandler type-checks-on
  "Check home: looks at values as requested and sends a TypeCheckError if violated."

  (TypeCheck [owner target expected value]
    (setv verdict (type-check-verdict owner target expected value))
    (when (is-not verdict None)
      (raise (TypeCheckError verdict)))
    (resume value)))


(defn #^ object run-with-type-checks [#^ object program]
  "Check composition root: runs the program with run-time type checks on."
  (run (type-checks-on program)))


(defn #^ object run-without-type-checks [#^ object program]
  "Production composition root: runs the program with run-time type checks off (default)."
  (run (type-checks-off program)))
