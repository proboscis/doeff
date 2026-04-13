;;; Koka-style pattern-matching effect handlers for doeff.
;;;
;;; Usage:
;;;   (require doeff-hy.handle [handle defhandler])
;;;   (import doeff [do :as _doeff-do WithHandler Resume Transfer Pass])
;;;
;;; Macros:
;;;   (handle body (Effect [fields] body...) ...)             — inline handler
;;;   (defhandler name [params?] (Effect [fields] body...) ...) — named handler
;;;
;;; Handler clause operations:
;;;   (resume value)      — Resume k with value, handler stays installed
;;;   (transfer value)    — Resume k with value, handler removed
;;;   (pass)              — Forward effect+continuation to outer handler
;;;   (<- result effect)  — Delegate: re-perform matched effect to outer handler
;;;
;;; Clause guards:
;;;   (EffectType [fields] :when pred body...)  — auto-pass if pred is false
;;;
;;; Compile-time checks:
;;;   - Every clause body must reach resume/transfer/pass/raise on ALL branches
;;;   - Missing terminal in any if/cond branch → SyntaxError at macro expansion
;;;
;;; S-expr preservation:
;;;   - defhandler stores __doeff_body__ for introspection (like defk)

(import hy.models [Expression Symbol List Keyword])


;; ---------------------------------------------------------------------------
;; Termination analysis — every code path must hit resume/transfer/pass/raise
;; ---------------------------------------------------------------------------

(defn _sym-name [form]
  "Get symbol name as string, or None."
  (if (isinstance form Symbol) (str form) None))

(defn _head-name [form]
  "Get head symbol name of an Expression, or None."
  (when (and (isinstance form Expression) (> (len form) 0))
    (_sym-name (get form 0))))

(defn _terminates [form]
  "Check if a single form always reaches resume/transfer/pass/raise."
  (setv head (_head-name form))
  (cond
    (is head None) False

    ;; Direct terminals
    (in head ["resume" "transfer" "pass" "raise"]) True

    ;; (if test then else) — both branches must terminate
    (= head "if")
      (and (>= (len form) 4)
           (_terminates (get form 2))
           (_terminates (get form 3)))

    ;; (cond test1 body1 test2 body2 ...) — all bodies must terminate
    (= head "cond")
      (let [pairs (cut form 1 None)
            bodies (cut pairs 1 None 2)]
        (and (> (len bodies) 0)
             (all (gfor b bodies (_terminates b)))))

    ;; (do form1 form2 ...) — sequence terminates if any form terminates
    (= head "do")
      (_seq-terminates (cut form 1 None))

    ;; (let [...] body...) — check body forms
    (= head "let")
      (and (>= (len form) 3)
           (_seq-terminates (cut form 2 None)))

    ;; (try body (except ...)) — terminates if body terminates
    (= head "try")
      (_seq-terminates (cut form 1 None))

    ;; (when test body...) — only one branch, doesn't guarantee termination
    (= head "when") False

    ;; Anything else: check if it recursively contains a terminal
    True (any (gfor f (cut form 1 None) (_terminates f)))))

(defn _seq-terminates [forms]
  "Check if a sequence of forms terminates (any single form terminates)."
  (any (gfor f forms (_terminates f))))

(defn _check-clause-terminates [etype-str clause-body]
  "Raise SyntaxError if clause body doesn't terminate on all branches."
  (when (not (_seq-terminates clause-body))
    (raise (SyntaxError
      (+ "handle clause for " etype-str
         ": missing resume/transfer/pass on some branch")))))


;; ---------------------------------------------------------------------------
;; Rewriting: resume/transfer/pass → yield expressions
;; ---------------------------------------------------------------------------

(defn _rewrite-ops [form]
  "Recursively rewrite resume/transfer/pass in handler clause body.
   (resume expr)   → (yield (Resume k expr))
   (transfer expr) → (yield (Transfer k expr))
   (pass)          → (yield (Pass effect k))"
  (cond
    (not (isinstance form Expression)) form
    (= (len form) 0) form
    True
      (let [head (get form 0)
            hname (_sym-name head)]
        (cond
          (and (= hname "resume") (= (len form) 2))
            `(yield (Resume k ~(_rewrite-ops (get form 1))))

          (and (= hname "transfer") (= (len form) 2))
            `(yield (Transfer k ~(_rewrite-ops (get form 1))))

          (and (= hname "pass") (= (len form) 1))
            '(yield (Pass effect k))

          True
            (Expression (lfor f form (_rewrite-ops f)))))))


;; ---------------------------------------------------------------------------
;; Clause parsing & handler construction
;; ---------------------------------------------------------------------------

(defn _parse-guard [body-forms]
  "Extract :when guard from body forms if present.
   Returns #(guard-expr remaining-body) or #(None body-forms)."
  (if (and (>= (len body-forms) 2)
           (isinstance (get body-forms 0) Keyword)
           (= (str (get body-forms 0)) ":when"))
      #((get body-forms 1) (list (cut body-forms 2 None)))
      #(None (list body-forms))))

(defn _expand-handler-binds [forms]
  "Expand <- and ! in handler clause body.
   (<- name expr) → (setv name (yield expr))  — delegate to outer handler
   ! in arguments → expanded to <- bindings first"
  (import doeff-hy.macros [_is-bind _bind-parts _expand-bangs])

  (setv expanded [])
  (for [form forms]
    (if (_is-bind form)
        (let [#(nm expr) (_bind-parts form)
              #(inner-bindings rewritten) (_expand-bangs expr)]
          ;; Expand inner bangs first
          (for [b inner-bindings]
            (let [#(bname bexpr) (_bind-parts b)]
              (if (is bname None)
                  (.append expanded `(yield ~bexpr))
                  (.append expanded `(setv ~bname (yield ~bexpr))))))
          ;; Then the main bind
          (if (is nm None)
              (.append expanded `(yield ~rewritten))
              (.append expanded `(setv ~nm (yield ~rewritten)))))
        ;; Not a bind — expand bangs in the form
        (let [#(inner-bindings rewritten) (_expand-bangs form)]
          (for [b inner-bindings]
            (let [#(bname bexpr) (_bind-parts b)]
              (if (is bname None)
                  (.append expanded `(yield ~bexpr))
                  (.append expanded `(setv ~bname (yield ~bexpr))))))
          (.append expanded rewritten))))
  expanded)


(defn _build-clause [clause]
  "Parse one handler clause: (EffectType [fields] [:when guard] body...).
   Validates termination. Returns #(effect-type cond-body)."
  (assert (isinstance clause Expression)
          "handle clause must be an expression")
  (assert (>= (len clause) 3)
          "handle clause needs (EffectType [fields] body...)")

  (setv etype (get clause 0))
  (setv fields (get clause 1))
  (setv raw-body (list (cut clause 2 None)))

  (assert (isinstance fields List)
          "handle clause fields must be [field1 ...]")

  ;; Extract :when guard
  (setv #(guard cbody) (_parse-guard raw-body))

  ;; Termination check BEFORE rewriting (on original body)
  (_check-clause-terminates (str etype) cbody)

  ;; Expand <- and ! bindings → (setv name (yield expr))
  (setv cbody (_expand-handler-binds cbody))

  ;; Field bindings: (setv field (. effect field))
  (setv bindings
    (lfor f fields
      `(setv ~f (. effect ~(Symbol (str f))))))

  ;; Rewrite resume/transfer/pass
  (setv rewritten (lfor form cbody (_rewrite-ops form)))

  ;; Build body: bindings first, then guard, then logic
  (setv full-body
    (if (is guard None)
        `(do ~@bindings ~@rewritten)
        `(do ~@bindings
             (if (not ~guard)
                 (yield (Pass effect k))
                 (do ~@rewritten)))))

  #(etype full-body))


(defn _build-handler-expr [clauses]
  "Build handler expression from clauses. Returns _doeff-do wrapped fn."
  (setv cond-forms [])

  (for [clause clauses]
    (setv #(etype body) (_build-clause clause))
    (.append cond-forms `(isinstance effect ~etype))
    (.append cond-forms body))

  ;; Default: pass unmatched
  (.append cond-forms 'True)
  (.append cond-forms '(yield (Pass effect k)))

  `(_doeff-do (fn [effect k] (cond ~@cond-forms))))


;; ---------------------------------------------------------------------------
;; Public macros
;; ---------------------------------------------------------------------------

(defmacro handle [body #* clauses]
  "Inline pattern-matching effect handler.

   (handle body
     (EffectType [field1 field2]
       (resume (compute field1 field2)))
     (OtherEffect [x]
       :when (pred x)
       (resume (+ x 1))))

   Wraps body with WithHandler. Unmatched effects auto-Pass.
   Compile-time error if any clause branch lacks resume/transfer/pass."
  `(WithHandler ~(_build-handler-expr clauses) ~body))


(defmacro defhandler [name #* rest]
  "Named handler with optional parameters. Preserves s-expr body.

   ;; No params — plain handler value
   (defhandler my-handler
     (Effect [field] (resume (compute field))))

   ;; With params — handler factory function
   (defhandler my-handler [config timeout]
     (Effect [field] (resume (process field config))))

   ;; With guard
   (defhandler filtered-handler [cost]
     (Effect [field recompute-cost]
       :when (matches-cost recompute-cost cost)
       (resume (compute field))))

   The handler's __doeff_body__ stores the original s-expr clauses."

  (setv params None)
  (setv clauses rest)

  ;; First item after name: List = params, Expression = first clause
  (when (and (> (len rest) 0) (isinstance (get rest 0) List))
    (setv params (get rest 0))
    (setv clauses (cut rest 1 None)))

  (setv handler-expr (_build-handler-expr clauses))

  ;; Preserve s-expr body as quoted list of clauses
  (setv quoted-body `(quote ~(list clauses)))

  (if (is params None)
      `(do
         (setv ~name ~handler-expr)
         (setv (. ~name __doeff_body__) ~quoted-body)
         (setv (. ~name __doeff_name__) ~(str name)))
      `(do
         (defn ~name [~@params] ~handler-expr)
         (setv (. ~name __doeff_body__) ~quoted-body)
         (setv (. ~name __doeff_name__) ~(str name)))))
