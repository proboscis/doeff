;;; Koka-style pattern-matching effect handlers for doeff.
;;;
;;; Usage:
;;;   (require doeff-hy.handle [handle defhandler])
;;;   ;; No extra imports needed — macros inject their own runtime deps.
;;;
;;; Macros:
;;;   (handle body (Effect [fields] body...) ...)             — inline handler
;;;   (defhandler name [params?] ["docstring"] (Effect [fields] body...) ...)
;;;                                                            — named handler
;;;   (with-handler [handler ...] body)                       — handler stack syntax
;;;
;;; Handler clause operations (terminal — handler gives up control):
;;;   (resume value)        — Resume k with value, handler stays installed
;;;   (transfer value)      — Resume k with value, handler removed (tail-call)
;;;   (reperform effect)    — Forward effect+k to outer handler (OCaml 5 reperform)
;;;   (pass)                — DEPRECATED: use (reperform effect)
;;;
;;; Handler clause operations (non-terminal — handler keeps control):
;;;   (<- result effect)    — Delegate effect to outer handler, bind result, continue
;;;                           The handler pauses, outer handler resolves, handler resumes.
;;;
;;; Key distinction:
;;;   (reperform effect)    → terminal: "I can't handle this, pass it up" → handler done
;;;   (<- x (SomeEff ...))  → non-terminal: "ask outer, get result back, keep going"
;;;
;;; Clause guards:
;;;   (EffectType [fields] :when pred body...)  — auto-reperform if pred is false
;;;
;;; Compile-time checks:
;;;   - Every clause body must reach resume/transfer/reperform/raise on ALL branches
;;;   - Missing terminal in any if/cond branch → SyntaxError at macro expansion
;;;
;;; S-expr preservation:
;;;   - defhandler stores __doeff_body__ for introspection (like defk)

(import hy.models [Expression Symbol List Keyword Sequence String])


;; ---------------------------------------------------------------------------
;; Lazy clause support — per-session effectful lazy init via Get/Put + Some
;; ---------------------------------------------------------------------------

(defn _is-lazy-clause [form]
  "Check if form is (lazy name body...) or (lazy-val name body...) or (lazy-var name body...)."
  (and (isinstance form Expression)
       (>= (len form) 3)
       (isinstance (get form 0) Symbol)
       (in (str (get form 0)) #{"lazy" "lazy-val" "lazy-var"})))

(defn _lazy-mutability [form]
  "Return :var for lazy-var, :val for lazy/lazy-val."
  (if (= (str (get form 0)) "lazy-var") :var :val))

(defn _parse-lazy [form]
  "Parse (lazy[-val|-var] name body...) → #(name-sym body-forms mutability)."
  (assert (_is-lazy-clause form))
  #((get form 1) (list (cut form 2 None)) (_lazy-mutability form)))

(defn _extract-lazy-clauses [clauses]
  "Split clauses into (lazy-defs, effect-clauses).
   lazy-defs: list of #(name-sym body-forms mutability).
   effect-clauses: remaining clauses."
  (setv lazys []
        effects [])
  (for [c clauses]
    (if (_is-lazy-clause c)
        (.append lazys (_parse-lazy c))
        (.append effects c)))
  #(lazys effects))

(defn _is-set-bang [form]
  "Check if form is (set! name expr)."
  (and (isinstance form Expression)
       (= (len form) 3)
       (isinstance (get form 0) Symbol)
       (= (str (get form 0)) "set!")))

(defn _check-set-bang-violations [lazy-defs clause-forms]
  "Raise SyntaxError if set! is used on a lazy-val name."
  (setv val-names
    (set (gfor #(lname _ mut) lazy-defs :if (= mut :val) (str lname))))
  (defn walk [form]
    (when (isinstance form Expression)
      (when (_is-set-bang form)
        (setv target (str (get form 1)))
        (when (in target val-names)
          (raise (SyntaxError
            (.format "set! on lazy-val '{name}': lazy-val is immutable. Use lazy-var instead."
                     :name target)))))
      (for [child form]
        (walk child))))
  (for [f clause-forms]
    (walk f)))

(setv _lazy-counter 0)

(defn _fresh-lazy-tmp [lazy-name suffix]
  "Generate a unique temp var for lazy init."
  (global _lazy-counter)
  (setv _lazy-counter (+ _lazy-counter 1))
  (Symbol (+ "_lazy_" (str lazy-name) "_" suffix "_" (str _lazy-counter))))

(defn _references-symbol [form sym-name]
  "Check if form's AST contains a Symbol with the given name.
   Traverses any hy.models.Sequence (Expression, List, Tuple, Set, Dict,
   FString, FComponent) so references inside tuple/set/dict literals are
   detected — required for lazy init injection when the lazy name appears
   only inside a tuple constructor like ``#(lazy-name other)``."
  (cond
    (isinstance form Symbol) (= (str form) sym-name)
    (isinstance form Sequence)
      (any (gfor f form (_references-symbol f sym-name)))
    True False))

(defn _build-lazy-init-forms [handler-name lazy-name lazy-body]
  "Build the yield-based lazy init code for one lazy def.
   Returns list of Hy forms (already in yield IR — no <- needed).

   Generated pattern:
     (setv _cached (yield (Get key)))
     (if (isinstance _cached Some)
         (setv name (. _cached value))
         (do ...init-body...
             (setv _val last-form)
             (yield (Put key (Some _val)))
             (setv name _val)))"
  (import doeff-hy.macros [_expand-bangs _is-bind _bind-parts])

  ;; Expand <- and ! in lazy body
  (setv expanded-body
    (_expand-handler-binds lazy-body
                           (+ "lazy " (str handler-name) "/" (str lazy-name))))

  ;; Separate init steps from value expression
  (setv init-forms (list (cut expanded-body 0 -1)))
  (setv value-expr (get expanded-body -1))

  ;; Generate temp vars
  (setv cached-var (_fresh-lazy-tmp lazy-name "cached"))
  (setv val-var (_fresh-lazy-tmp lazy-name "val"))

  ;; Build state key: __name__ + "/handler-name/lazy-name"
  (setv key-suffix (+ "/" (str handler-name) "/" (str lazy-name)))
  (setv key-expr `(+ __name__ ~key-suffix))

  ;; Key variable for set! macro to reference
  (setv key-var (Symbol (+ "_lazy_" (str lazy-name) "_key")))

  ;; Build the init forms
  (setv else-body
    (+ init-forms
       [`(setv ~val-var ~value-expr)
        `(yield (Put ~key-var (Some ~val-var)))
        `(setv ~(Symbol (str lazy-name)) ~val-var)]))

  ;; Build full lazy init sequence
  [`(setv ~key-var ~key-expr)
   `(setv ~cached-var (yield (Get ~key-var)))
   `(if (isinstance ~cached-var Some)
        (setv ~(Symbol (str lazy-name)) (. ~cached-var value))
        (do ~@else-body))])


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
    (in head ["resume" "transfer" "pass" "reperform" "raise"]) True

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
;; TCO: tail-position (resume expr) → (transfer expr)
;; ---------------------------------------------------------------------------

(defn _tail-resume-to-transfer [form]
  "Replace tail-position (resume expr) with (transfer expr).
   Recurses into if/cond/do/let but NOT into try (need frame for except)."
  (cond
    (not (isinstance form Expression)) form
    (= (len form) 0) form
    True
      (let [hname (_sym-name (get form 0))]
        (cond
          ;; (resume expr) → (transfer expr)
          (and (= hname "resume") (= (len form) 2))
            (Expression [(Symbol "transfer") (get form 1)])

          ;; (if test then else) → optimize both branches
          (and (= hname "if") (>= (len form) 4))
            (Expression [(get form 0) (get form 1)
                         (_tail-resume-to-transfer (get form 2))
                         (_tail-resume-to-transfer (get form 3))])

          ;; (cond p1 b1 p2 b2 ...) → optimize body (odd-index) forms
          (= hname "cond")
            (let [items (list (cut form 1 None))
                  result [(get form 0)]]
              (for [#(i item) (enumerate items)]
                (.append result
                  (if (% i 2)
                      (_tail-resume-to-transfer item)
                      item)))
              (Expression result))

          ;; (do ...) → optimize last form
          (and (= hname "do") (> (len form) 1))
            (Expression
              (+ (list (cut form 0 -1))
                 [(_tail-resume-to-transfer (get form -1))]))

          ;; (let [...] body...) → optimize last body form
          (and (= hname "let") (>= (len form) 3))
            (Expression
              (+ (list (cut form 0 -1))
                 [(_tail-resume-to-transfer (get form -1))]))

          ;; (try ...) → do NOT optimize (need frame for except)
          (= hname "try") form

          ;; Anything else → don't touch
          True form))))

(defn _tco-seq [forms]
  "Apply tail-resume TCO to the last form in a sequence."
  (if (= (len forms) 0)
      forms
      (+ (list (cut forms 0 -1))
         [(_tail-resume-to-transfer (get forms -1))])))


;; ---------------------------------------------------------------------------
;; Rewriting: resume/transfer/pass → yield expressions
;; ---------------------------------------------------------------------------

(defn _rewrite-ops [form]
  "Recursively rewrite resume/transfer/pass/reperform in handler clause body.
   (resume expr)      → (yield (Resume k expr))
   (transfer expr)    → (yield (Transfer k expr))
   (reperform effect) → (yield (Pass effect k))     — OCaml 5 aligned
   (pass)             → (yield (Pass effect k))      — deprecated, use reperform"
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

          ;; (reperform expr) — OCaml 5 aligned forwarding
          (and (= hname "reperform") (= (len form) 2))
            `(yield (Pass ~(_rewrite-ops (get form 1)) k))

          ;; (pass) — deprecated, use (reperform effect) instead
          (and (= hname "pass") (= (len form) 1))
            (do
              (import warnings)
              (warnings.warn
                "(pass) is deprecated in defhandler — use (reperform effect) instead"
                DeprecationWarning)
              '(yield (Pass effect k)))

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

(defn _expand-handler-binds [forms [owner "handler clause"]]
  "Expand <- and ! in handler clause body.
   (<- name expr) → (setv name (yield expr))  — delegate to outer handler
   (! expr) → (yield expr) in place [ADR-DOE-HY-003]"
  (import doeff-hy.macros [_is-bind _bind-parts _expand-bangs])

  (setv expanded [])
  (for [form forms]
    (setv rewritten (_expand-bangs form owner))
    (if (_is-bind rewritten)
        (let [#(nm expr) (_bind-parts rewritten)]
          (if (is nm None)
              (.append expanded `(yield ~expr))
              (.append expanded `(setv ~nm (yield ~expr)))))
        (.append expanded rewritten)))
  expanded)


(defn _build-clause [clause [lazy-defs None] [handler-name None]]
  "Parse one handler clause: (EffectType [fields] [:when guard] body...).
   Validates termination. Returns #(effect-type cond-body).
   If lazy-defs is provided, inject lazy init for referenced lazy names."
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

  ;; TCO: tail-position (resume expr) → (transfer expr)
  ;; Must run BEFORE _expand-handler-binds and _rewrite-ops so it sees
  ;; the original (resume ...) forms, not the rewritten (yield (Resume ...)).
  (setv cbody (_tco-seq cbody))

  ;; Expand <- and ! bindings → (setv name (yield expr))
  (setv cbody
    (_expand-handler-binds cbody
                           (if (is handler-name None)
                               (+ "handler clause " (str etype))
                               (+ "defhandler " (str handler-name)
                                  " clause " (str etype)))))

  ;; Check set! violations on lazy-val names
  (when lazy-defs
    (_check-set-bang-violations lazy-defs raw-body))

  ;; Inject lazy init for referenced lazy names (after bind expansion,
  ;; before rewrite-ops — lazy init forms are already in yield IR)
  (setv lazy-prefix [])
  (when (and lazy-defs handler-name)
    (for [#(lname lbody _mut) lazy-defs]
      ;; Symbol scan: only inject if clause body references this lazy name
      (when (any (gfor form raw-body (_references-symbol form (str lname))))
        (.extend lazy-prefix
          (_build-lazy-init-forms handler-name lname lbody)))))

  ;; Field bindings: (setv field (. effect field))
  (setv bindings
    (lfor f fields
      `(setv ~f (. effect ~(Symbol (str f))))))

  ;; Rewrite resume/transfer/pass (lazy-prefix is already in yield IR,
  ;; but _rewrite-ops only touches resume/transfer/pass — safe to pass through)
  (setv rewritten (+ lazy-prefix (lfor form cbody (_rewrite-ops form))))

  ;; Build body: bindings first, then guard, then logic
  (setv full-body
    (if (is guard None)
        `(do ~@bindings ~@rewritten)
        `(do ~@bindings
             (if (not ~guard)
                 (yield (Pass effect k))
                 (do ~@rewritten)))))

  #(etype full-body))


(defn _build-handler-expr [clauses [lazy-defs None] [handler-name None]]
  "Build handler expression from clauses. Returns _doeff-do wrapped fn.
   If lazy-defs is provided, lazy init is injected into clauses that reference them."
  (setv cond-forms [])

  (for [clause clauses]
    (setv #(etype body) (_build-clause clause
                                       :lazy-defs lazy-defs
                                       :handler-name handler-name))
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

   Wraps body with the Rust handler node. Unmatched effects auto-Pass.
   Compile-time error if any clause branch lacks resume/transfer/pass."
  (setv h-expr (_build-handler-expr clauses))
  `(do
     (import doeff.do [do :as _doeff-do])
     (import doeff [Resume Transfer Pass])
     (import doeff_vm [WithHandler])
     (WithHandler ~h-expr ~body)))


(defmacro with-handler [handlers body]
  "Apply a non-empty handler stack to a Program body.

   (with-handler [outer inner] body)

   expands to:

     (outer (inner body))

   The list order is scope order: leftmost is outermost, rightmost is
   innermost. Handler values are Program -> Program functions such as the
   values produced by defhandler."
  (when (not (isinstance handlers List))
    (raise (SyntaxError
             "with-handler requires a handler vector: (with-handler [h1 h2] body)")))
  (when (= (len handlers) 0)
    (raise (SyntaxError
             "with-handler requires a non-empty handler vector: (with-handler [h] body)")))
  (setv wrapped body)
  (for [h (reversed (list handlers))]
    (setv wrapped `(~h ~wrapped)))
  wrapped)


(defmacro defhandler [name #* rest]
  "Named handler with optional parameters. Preserves s-expr body.

   ;; No params — plain handler value
   (defhandler my-handler
     (Effect [field] (resume (compute field))))

   ;; With docstring
   (defhandler my-handler
     \"Handle MyEffect values.\"
     (Effect [field] (resume (compute field))))

   ;; With params — handler factory function
   (defhandler my-handler [config timeout]
     (Effect [field] (resume (process field config))))

   ;; With guard
   (defhandler filtered-handler [cost]
     (Effect [field recompute-cost]
       :when (matches-cost recompute-cost cost)
       (resume (compute field))))

   ;; With lazy init (per-session via Get/Put + Some/Nothing)
   (defhandler my-handler
     (lazy client
       (<- secret (Ask \"api_key\"))
       (Client :password secret))
     (Effect [field] (resume (.fetch client field))))

   ;; Terminal operations:
   ;;   (resume value)      — resume k, handler stays installed
   ;;   (transfer value)    — resume k, handler removed (tail-call optimized)
   ;;   (reperform effect)  — forward effect+k to outer handler (handler done)
   ;;
   ;; Non-terminal operation:
   ;;   (<- result effect)  — delegate to outer handler, get result back, continue

   The handler's __doeff_body__ stores the original s-expr clauses."

  (setv params None)
  (setv docstring None)
  (setv clauses rest)

  ;; Optional docstring may appear immediately after name for no-arg handlers
  ;; or after params for parameterized handlers.
  (when (and (> (len clauses) 0) (isinstance (get clauses 0) String))
    (setv docstring (get clauses 0))
    (setv clauses (cut clauses 1 None)))

  ;; First item after name: List = params, Expression = first clause
  (when (and (> (len clauses) 0) (isinstance (get clauses 0) List))
    (setv params (get clauses 0))
    (setv clauses (cut clauses 1 None)))

  (when (and (> (len clauses) 0) (isinstance (get clauses 0) String))
    (setv docstring (get clauses 0))
    (setv clauses (cut clauses 1 None)))

  ;; Separate lazy defs from effect clauses
  (setv #(lazy-defs effect-clauses) (_extract-lazy-clauses clauses))

  (setv handler-expr
    (if lazy-defs
        (_build-handler-expr effect-clauses
                             :lazy-defs lazy-defs
                             :handler-name name)
        (_build-handler-expr effect-clauses)))

  ;; Preserve s-expr body as quoted list of all clauses (including lazy)
  (setv quoted-body `(quote ~(list clauses)))

  ;; Extra imports needed when lazy is used
  (setv lazy-imports
    (if lazy-defs
        `(do (import doeff [Some])
             (import doeff_core_effects.effects [Get Put]))
        `(do)))

  ;; defhandler produces a Program -> Program function instead of exposing a
  ;; raw handler dispatcher. The inner dispatcher is stored for introspection
  ;; and installed with the Rust VM WithHandler node.
  (if (is params None)
      `(do
         (import doeff.do [do :as _doeff-do])
         (import doeff [Resume Transfer Pass])
         (import doeff_vm [WithHandler])
         ~lazy-imports
         (setv ~name
           ((fn []
              (setv __doeff-handler-data__ ~handler-expr)
              (defn __doeff-handler-fn__ [__doeff-body__]
                (WithHandler __doeff-handler-data__ __doeff-body__))
              (setv (. __doeff-handler-fn__ __doc__) ~docstring)
              (setv (. __doeff-handler-fn__ _doeff_is_handler_fn) True)
              (setv (. __doeff-handler-fn__ __doeff_handler_data__)
                    __doeff-handler-data__)
              __doeff-handler-fn__)))
         (setv (. ~name __doeff_body__) ~quoted-body)
         (setv (. ~name __doeff_name__) ~(str name)))
      `(do
         (import doeff.do [do :as _doeff-do])
         (import doeff [Resume Transfer Pass])
         (import doeff_vm [WithHandler])
         ~lazy-imports
         (defn ~name [~@params]
           (setv __doeff-handler-data__ ~handler-expr)
           (defn __doeff-handler-fn__ [__doeff-body__]
             (WithHandler __doeff-handler-data__ __doeff-body__))
           (setv (. __doeff-handler-fn__ __doc__) ~docstring)
           (setv (. __doeff-handler-fn__ _doeff_is_handler_fn) True)
           (setv (. __doeff-handler-fn__ __doeff_handler_data__)
                 __doeff-handler-data__)
           (setv (. __doeff-handler-fn__ __doeff_name__) ~(str name))
           __doeff-handler-fn__)
         (setv (. ~name __doc__) ~docstring)
         (setv (. ~name __doeff_body__) ~quoted-body)
         (setv (. ~name __doeff_name__) ~(str name)))))
