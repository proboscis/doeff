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
;;;   (finish value)        — Drop k; the handled scope answers value (ADR-DOE-CORE-EFFECTS-003 R15).
;;;                           Raise / Absent clauses finish freely; an ordinary effect's clause
;;;                           needs :finish-reason "…" (e.g. a deadline that stops the whole run)
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
;;; Checks (ADR-DOE-CORE-EFFECTS-003 R15 — a forgotten resume never ends a scope silently):
;;;   - Every clause body must reach resume/transfer/finish/reperform/raise on ALL branches
;;;     (each if / cond / match / try branch; cond ends with True, match with a bare _) →
;;;     SyntaxError at macro expansion
;;;   - A clause's ending must match its effect's declared resumption (Resumption): no resume of
;;;     Raise / Absent, no finish of an ordinary effect without :finish-reason — by name at
;;;     expansion, by the effect type when the handler first wraps a body (ClauseEndingError)
;;;   - A clause that leaves without resume / finish at run time → RuntimeError
;;;
;;; S-expr preservation:
;;;   - defhandler stores __doeff_body__ for introspection (like defk)

(import hy.models [Expression Symbol List Keyword Sequence String])
;; Bundle expansion helpers under `_` names — this module is the boundary of macros that Hy's require reads by path, and the only names it exposes are macros
;; (ADR-DOE-API-001 R14). If bundled under plain names, the inventory would count them as public names of doeff_hy.handle.
(import doeff-hy._positions [locate-synthesized :as _locate-synthesized])
(import doeff-hy._declarations [DECLARATION-KEYS :as _DECLARATION-KEYS
                               refuse-unknown-keys :as _refuse-unknown-keys
                               declaration-setters :as _declaration-setters])
(import hy.models [Dict])
(import doeff-hy._static-view [static-view-enabled :as _static-view-enabled report-findings :as _report-findings])
(import doeff-hy._clause-ops [clause-op :as _clause-op node-names :as _node-names])
(import doeff-hy._expansion-names [alias-symbol :as _alias-symbol
                                  import-form :as _import-form
                                  effect-symbol :as _effect-symbol
                                  continuation-symbol :as _continuation-symbol
                                  WRITER-VISIBLE :as _WRITER-VISIBLE])
(import doeff-hy._binding-forms [parse-declaration :as _parse-declaration
                                Timing :as _Timing
                                Mutability :as _Mutability
                                SessionName :as _SessionName
                                BodyKind :as _BodyKind
                                legacy-session-finding :as _legacy-session-finding
                                legacy-session-message :as _legacy-session-message])

(defn _do-import []
  "Import of `_doeff-do`, which wraps the handler body. The expansion for type checking (doeff_hy/_static_view.py)
   takes the typed doeff_hy._static_types.do — if two different types were attached to the same name as the defk in the
   same module, `_doeff_do` as seen by pyright would become a union of the two and cause false positives
   (same decision as `_helper-imports` in macros.hy). The runtime `do` lives in `_runtime-do` of the helper table."
  (if (_static-view-enabled)
      ;; doeff-hy-check imports the typed do only once at the head of the module (STATIC_HELPER_IMPORTS in static_view.py —
      ;; emitting it per clause makes pyright treat a name as Unknown in a module where the declarations of one name exceed 64).
      '(do)
      (do
        (import doeff-hy.macros [_runtime-do-import])
        (_runtime-do-import '_doeff-do))))


(defn _clause-endings-import [name alias]
  "Import that fetches the clause-ending check (name in clause_endings.hy) via an alias. In the expansion for type checking,
   doeff-hy-check imports the same name from the typed doeff_hy._static_types only once at the head of the module (HANDLER_STATIC_NAMES
   in static_view.py — clause_endings is a .hy file, so pyright cannot see its types)."
  (if (_static-view-enabled)
      '(do)
      `(import doeff-hy._clause-endings [~name :as ~alias])))


(defn _vm-imports []
  "Import of the doeff nodes used by the handler expansion — the clause-operation nodes (nodes / static-nodes in the clause_ops.hy table), plus what the handler itself
   uses: Pass (hands an effect outside the guard, and an effect matching no clause, to the outside) and WithHandlerType (name of the doeff root — doeff_vm is not imported). The resume / transfer of the expansion for type checking become
   typed-resume / typed-transfer and do not use Resume / Transfer, so they are not listed in the table's static-nodes (an unused import is a strict
   error).

   Names are bundled under `_doeff_<name>` aliases (doeff_hy/_expansion_names.py). If bundled under plain names, when a writer named their own effect the same (a
   Transfer in a bank transfer), the isinstance at the head of a clause would point at the VM node and the effect would silently slip through to the outer handler, and at module level the writer's
   name was overwritten. The spelling by which the expansion reads these nodes also fetches the same alias via alias-symbol."
  (_import-form (+ (_node-names (_static-view-enabled)) ["Pass" "WithHandlerType"])))


(defn _handler-fn-form [endings-check]
  "Definition of the function __doeff-handler-fn__ that wraps the body with the handler. In the expansion for type checking, the body is annotated as
   `HandlerBody[HandledAnswer]` and the answer as `HandledScope[HandledAnswer]` (carrying the body's answer type as is)
   (doeff_hy/_static_types.pyi). The runtime expansion adds no annotations."
  (if (_static-view-enabled)
      `(defn #^ (get _doeff_HandledScope _doeff_HandledAnswer) __doeff-handler-fn__
             [#^ (get _doeff_HandlerBody _doeff_HandledAnswer) __doeff-body__]
         ~endings-check
         (~(_alias-symbol "WithHandlerType") __doeff-handler-data__ __doeff-body__))
      `(defn __doeff-handler-fn__ [__doeff-body__]
         ~endings-check
         (~(_alias-symbol "WithHandlerType") __doeff-handler-data__ __doeff-body__))))


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
  "Parse (lazy[-val|-var] name body...) → #(name-sym body-forms mutability legacy?)."
  (assert (_is-lazy-clause form))
  #((get form 1) (list (cut form 2 None)) (_lazy-mutability form) True))

(defn _extract-lazy-clauses [clauses [handler-name None]]
  "Split clauses into (lazy-defs, effect-clauses).
   lazy-defs: list of #(name-sym body-forms mutability legacy?) — session val / session var
   (ADR-DOE-HY-006) and both of the old lazy / lazy-val / lazy-var. The old forms keep their behavior, and
   DeprecationWarning and the findings of doeff-hy-check guide migration to session val / session var.
   effect-clauses: remaining clauses."
  (setv lazys []
        effects [])
  (for [c clauses]
    (setv decl (_parse-declaration c))
    (cond
      (and (is-not decl None) (= (. decl timing) _Timing.SESSION))
        (.append lazys #((. decl symbol) [(. decl init)]
                         (if (= (. decl mutability) _Mutability.VAR) :var :val)
                         False))
      (is-not decl None)
        (raise (SyntaxError (+ "\ndefhandler " (str handler-name) ": (" (. decl spelled) " "
                               (. decl name) " ...) goes inside the clause body (a value for the duration of that one run of the clause).\n"
                               "  What goes directly under the handler (alongside the clauses) is a value shared across the session: "
                               "(session val " (. decl name) " expr) / (session var " (. decl name) " expr)."
                               " [ADR-DOE-HY-006]\n")))
      (_is-lazy-clause c)
        (do
          (import warnings)
          (warnings.warn (_legacy-session-message c (str handler-name)) DeprecationWarning
                         :stacklevel 3)
          (_report-findings [(_legacy-session-finding c (str handler-name))])
          (.append lazys (_parse-lazy c)))
      True
        (.append effects c)))
  #(lazys effects))

(defn _session-names [lazy-defs]
  "List of names held per session, passed to the clause-body rewriting (binding_forms)."
  (lfor #(lname _ mut legacy?) lazy-defs
    (_SessionName (str lname) (if (= mut :var) _Mutability.VAR _Mutability.VAL) legacy?)))

(defn _is-set-bang [form]
  "Check if form is (set! name expr)."
  (and (isinstance form Expression)
       (= (len form) 3)
       (isinstance (get form 0) Symbol)
       (= (str (get form 0)) "set!")))

(defn _check-set-bang-violations [lazy-defs clause-forms]
  "Raise SyntaxError if set! is used on a lazy-val name."
  (setv val-names
    (set (gfor #(lname _ mut _legacy) lazy-defs :if (= mut :val) (str lname))))
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

(defn _fresh-lazy-tmp [lazy-name suffix]
  "To make temporary names for lazy initialization. Made with Hy's gensym — the numbers in the names are reassigned to the order within the module by the canonicalization at the compile entrance
   (doeff_hy_bytecode_guard._records.canonical_gensyms), so they do not depend on compile order
   (with a counter of our own, the numbers used to change with compile order)."
  (hy.gensym (+ "lazy_" (str lazy-name) "_" suffix)))

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

(defn _names-symbol [form sym-name]
  "Check if form's AST contains the Symbol sym-name, or a dotted Symbol headed by it (`effect.amount`).
   Same traversal as _references-symbol."
  (cond
    (isinstance form Symbol) (or (= (str form) sym-name) (.startswith (str form) (+ sym-name ".")))
    (isinstance form Sequence)
      (any (gfor f form (_names-symbol f sym-name)))
    True False))

(defn _clause-aliases [clause lazy-defs]
  "Of the `effect` and `k` the writer wrote in a clause body / guard (the WRITER-VISIBLE row of expansion_names.py), the list of rows this clause bundles.

   The expansion reads the clause-iterating function's arguments and the clause operations through bundles `_doeff_effect` and `_doeff_k`, so plain `effect` and `k` stay in the writer's namespace.
   Only a clause where the writer wrote something like `(reperform effect)` or `:when (isinstance effect X)` can read that name as the clause's effect and continuation
   (the bundling is in `_alias-forms`; the effect in the expansion for type checking is bundled in `_narrowed-alias-bindings` and narrowed in `_clause-test`). If the clause's fields or a session name use the same name, it is not bundled —
   the name the writer chose in the fields / session takes priority. A dotted symbol (`effect.amount`) also counts as using the name. It counts even if the name appears only in a local binding inside the body
   (`(lfor k ...)`) — an extra bundle does no harm (the expansion for type checking reads the bundled name)."
  (import doeff-hy._clause-endings [parse-clause-options])
  (setv options (parse-clause-options (list (cut clause 2 None)) "handle clause"))
  (setv taken (+ (lfor f (get clause 1) (str f))
                 (lfor #(lname _ _mut _legacy) (or lazy-defs []) (str lname))))
  (setv written-forms (+ (if (is (. options guard) None) [] [(. options guard)]) (. options body)))
  (lfor row _WRITER-VISIBLE
        :if (and (not-in row.written taken)
                 (any (gfor form written-forms (_names-symbol form row.written))))
        row))

(defn _alias-forms [row]
  "Bundle of the writer's name row.written, placed at the head of the clause body. The runtime expansion is `(setv effect _doeff_effect)`. The expansion for type checking
   declares a type for the name (`k: _doeff_Continuation`) and reads the bundled name (`_ = k` — a name the writer does not use becomes a reportUnusedVariable error.
   Same as `_ = field` in the field bundle). The effect that the clause's `isinstance` narrows is not bundled here: it is bundled to the pre-narrowing value at the head of the clause-iterating function
   (`_narrowed-alias-bindings`), and the clause test narrows and reads it (`_clause-test`)."
  (setv written (Symbol row.written))
  (cond
    (not (_static-view-enabled)) [`(setv ~written ~(Symbol row.internal))]
    row.narrowed []
    True [`(setv (annotate ~written ~(Symbol row.declared)) ~(Symbol row.internal))
          `(setv _ ~written)]))

(defn _narrowed-alias-bindings [clauses lazy-defs]
  "In the expansion for type checking, the list of bundles of the writer's names (`effect = _doeff_effect`) placed at the head of the clause-iterating function (before the chain of clause tests).
   Placed only in a handler that has at least one clause where the writer wrote a narrowed name (row.narrowed). The runtime expansion is bundled by the body of the clause the writer wrote (`_alias-forms`).

   If the effect that a clause's `isinstance` narrows is assigned after narrowing, the assignment target variable receives the narrowed generic type (`EffectBase[Unknown]`) and
   it becomes a reportUnknownVariableType error (the same even if a type is declared on the target). Bundle it to the pre-narrowing value, and the test of the clause the writer wrote also narrows the writer's name
   (`_clause-test`). We do not adopt the form that bundles inside the test (`isinstance((effect := _doeff_effect), T) and isinstance(_doeff_effect, T)`):
   whether the right side of a walrus is narrowed varies by pyright version (1.1.411 only the left, 1.1.414 also the right), and in a version that narrows the right too, the second becomes 'always true'
   (reportUnnecessaryIsInstance) — one per clause the writer wrote, which the writer cannot fix.'
  (lfor row _WRITER-VISIBLE
        :if (and row.narrowed
                 (any (gfor clause clauses (in row (_clause-aliases clause lazy-defs)))))
        `(setv ~(Symbol row.written) ~(Symbol row.internal))))

(defn _effect-form-binding [etype fields rewritten]
  "In the expansion for type checking, the list of forms that rebind the `_doeff_effect` narrowed by a clause's `isinstance` to a type where all type arguments of the effect's type are resolved to Any
   (empty for the runtime expansion and for clauses that need no rebinding).

   pyright makes the type arguments of a generic class narrowing Unknown (`isinstance(object value, Race)` gives `Race[Unknown]`). Both the clause's field bundle
   (`tasks = _doeff_effect.tasks`) and the effect argument of typed_resume / typed_transfer become partly-Unknown errors, which the writer cannot fix
   (`(Race [tasks] ...)`, `(Spawn [program] ...)` — field names are decided by the effect definition, and the error comes from the name the expansion makes and from the call). If the narrowing source type is
   `EffectBase[Any]`, only the type arguments resolvable from the base class are filled in, and a type argument not in the base, like `_E` of `Spawn`, stays Unknown;
   the default of a type variable is not used by isinstance narrowing. The number of type arguments cannot be known at expansion time (a macro cannot look up the class of an imported name), so
   the type is built with the constructor call `_doeff_any_specialized(Race, *_doeff_any_args, **_doeff_any_kwargs)` (doeff_hy/_static_types.pyi). Since all the constructor's
   arguments are Any, all type arguments are resolved to Any, and the field types stay the types in the effect definition (`tuple[Task[Any] | Future[Any], ...]`);
   a non-generic class gets the same type as the narrowing. The expansion for type checking is not run and only pyright reads it, so the value of the call is not needed.

   Rebinding is done only when the clause reads the effect: it bundles fields, or the rewritten body reads `_doeff_effect` (typed_resume / typed_transfer,
   `(pass)`). Nothing is added to a clause that reads nothing (`(Spawn [] (reperform effect))`). A clause whose head is not a name (`#(A B)`, `(| A B)`) cannot be
   called as a constructor, so it is not rebound."
  (if (and (_static-view-enabled)
           (isinstance etype Symbol)
           (or (> (len fields) 0)
               (any (gfor form rewritten (_references-symbol form (str (_effect-symbol)))))))
      [`(setv ~(_effect-symbol) (_doeff_any_specialized ~etype #* _doeff_any_args #** _doeff_any_kwargs))]
      []))

(defn _clause-test [etype aliases]
  "Clause test — whether the received effect is the clause's effect type. In the expansion for type checking, for a clause where the writer uses the narrowed name (row.narrowed), the writer's name is also narrowed
   to the same type (`isinstance(_doeff_effect, T) and isinstance(effect, T)`). The writer's name was bundled to the pre-narrowing value at the head of the clause-iterating function
   (`_narrowed-alias-bindings`), so the test narrows a separate name once each — narrowing is not an assignment, so no error appears even if the narrowed type is partly Unknown, and
   no name is narrowed twice, so it never becomes an 'always true' isinstance. `_doeff_effect` is read by the following `typed_resume` and the field bundle."
  (setv narrowed (lfor row aliases :if row.narrowed row))
  (if (and (_static-view-enabled) narrowed)
      `(and (isinstance ~(_effect-symbol) ~etype)
            ~@(lfor row narrowed `(isinstance ~(Symbol row.written) ~etype)))
      `(isinstance ~(_effect-symbol) ~etype)))

(defn _build-lazy-init-forms [handler-name lazy-name lazy-body [legacy? True]]
  "Build the yield-based lazy init code for one lazy def.
   Returns list of Hy forms (already in yield IR — no <- needed).

   Generated pattern (Get / Put / Some are the `_doeff_` aliases — expansion_names.py):
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
  ;; The old lazy keeps its existing expression. session val / session var make the key through the single public session-key
  ;; (the same string — the outer handler and the check also fetch the same key via doeff_hy._session.session_key).
  (setv key-suffix (+ "/" (str handler-name) "/" (str lazy-name)))
  (setv key-expr
    (if legacy?
        `(+ __name__ ~key-suffix)
        `(_doeff-session-key __name__ ~(str handler-name) ~(str lazy-name))))

  ;; Key variable for set! macro to reference
  (setv key-var (Symbol (+ "_lazy_" (str lazy-name) "_key")))

  ;; Build the init forms
  (setv else-body
    (+ init-forms
       [`(setv ~val-var ~value-expr)
        `(yield (~(_alias-symbol "Put") ~key-var (~(_alias-symbol "Some") ~val-var)))
        `(setv ~(Symbol (str lazy-name)) ~val-var)]))

  ;; Build full lazy init sequence
  ;; Expansion for type checking (doeff_hy/_static_view.py — not run, only pyright reads it): the name is bound to the value of the initialization expression.
  ;; At runtime it is either the answer of Get (the content of Some) or the initialization value, but what is placed in the state is the same initialization value, so the type is the same —
  ;; the answer of Get has no type, so copying it as is made the name and every expression using it Unknown, producing errors the writer could not fix.
  ;; The key name is kept because the Put of := fetches it.
  (if (_static-view-enabled)
      (+ [`(setv ~key-var ~key-expr)]
         init-forms
         [`(setv ~val-var ~value-expr)
          `(setv ~(Symbol (str lazy-name)) ~val-var)])
      [`(setv ~key-var ~key-expr)
       `(setv ~cached-var (yield (~(_alias-symbol "Get") ~key-var)))
       `(if (isinstance ~cached-var ~(_alias-symbol "Some"))
            (setv ~(Symbol (str lazy-name)) (. ~cached-var value))
            (do ~@else-body))]))


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
  "Check if a single form always reaches a clause operation (resume/transfer/finish/reperform/raise — the table in
   clause_ops.hy) — on every path (each if / cond / match / try branch; when / unless / and / or / loops never guarantee it)."
  (setv head (_head-name form))
  (cond
    (is head None) False

    ;; Direct terminals — every clause operation ends the clause
    (is-not (_clause-op form) None) True

    ;; (if test then else) — both branches must terminate
    (= head "if")
      (and (>= (len form) 4)
           (_terminates (get form 2))
           (_terminates (get form 3)))

    ;; (cond test1 body1 test2 body2 ...) — all bodies must terminate and the last test is True
    ;; (otherwise a value that matches no test falls through)
    (= head "cond")
      (let [pairs (cut form 1 None)
            tests (cut pairs 0 None 2)
            bodies (cut pairs 1 None 2)]
        (and (> (len bodies) 0)
             (= (len tests) (len bodies))
             (in (str (get tests -1)) #{"True" "else" ":else"})
             (all (gfor b bodies (_terminates b)))))

    ;; (match subject pattern body ...) — every arm terminates and the last arm is irrefutable
    (= head "match")
      (do (import doeff-hy._clause-endings [match-terminates?])
          (match-terminates? form))

    ;; (do form1 form2 ...) — sequence terminates if any form terminates
    (= head "do")
      (_seq-terminates (cut form 1 None))

    ;; (let [...] body...) / (with [...] body...) — check body forms
    (in head ["let" "with"])
      (and (>= (len form) 3)
           (_seq-terminates (cut form 2 None)))

    ;; (try body (except ...)) — the body and every except clause terminate
    (= head "try")
      (do (import doeff-hy._clause-endings [try-terminates?])
          (try-terminates? form))

    ;; One-branch and short-circuit forms and loops never guarantee termination; a resume inside a
    ;; nested function or comprehension does not run as the clause's step at all
    (in head ["when" "unless" "and" "or" "for" "while"
              "fn" "fn/a" "defn" "defn/a" "lfor" "gfor" "dfor" "sfor" "quote" "quasiquote"]) False

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
         ": missing resume/transfer/finish/reperform on some branch — every if / cond / match / try"
         " branch must end the clause (cond needs a final True, match a final _); a clause that should"
         " end its scope without resuming says so with (finish value) [ADR-DOE-CORE-EFFECTS-003 R15]")))))


;; ---------------------------------------------------------------------------
;; TCO: tail-position (resume expr) → (transfer expr)
;; ---------------------------------------------------------------------------

(defn _match-cases-to-transfer [form]
  "Rewrite the tail resume of every arm body of (match subject pattern [:if guard] body ...).
   The arms are split by clause_endings' match-arms — the same split the clause-ending check
   uses, so the two never disagree on where a body is. Only bodies are in tail position
   (patterns and guards are left as written); a malformed match is left untouched."
  (import doeff-hy._clause-endings [match-arms])
  (setv arms (match-arms form))
  (if (is arms None)
      form
      (Expression
        (+ [(get form 0) (get form 1)]
           (lfor arm arms
                 part (+ [arm.pattern]
                         (if (is arm.guard None) [] [(Keyword "if") arm.guard])
                         [(_tail-resume-to-transfer arm.body)])
                 part)))))


(defn _tail-resume-to-transfer [form]
  "Replace a tail-position clause operation with the operation its table row names as tail (clause_ops.hy —
   (resume expr) → (transfer expr)). Recurses into if/cond/match/do/let but NOT into try (need frame for except)."
  (cond
    (not (isinstance form Expression)) form
    (= (len form) 0) form
    True
      (let [hname (_sym-name (get form 0))
            op (_clause-op form)]
        (cond
          ;; (resume expr) → (transfer expr) — the row's tail
          (and (is-not op None) (is-not op.tail None) (= (len form) (+ 1 op.arity)))
            (Expression [(Symbol op.tail) #* (cut form 1 None)])

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

          ;; (match subject pattern [:as name] [:if guard] body ...) → optimize each case's body.
          ;; Without this a resume at the end of a match branch stayed a Resume: the handler's
          ;; generator frame was kept (holding the resumed value) for as long as the handled program
          ;; ran — a long-running loop under the handler piled one frame per handled effect
          ;; (an intake reader held every page it read).
          (and (= hname "match") (>= (len form) 4))
            (_match-cases-to-transfer form)

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
  "Recursively rewrite the clause operations in a handler clause body with the table's rewrites (clause_ops.hy):
   (resume expr)      → (yield (Resume _doeff_k expr))  — and marks the clause as resumed
   (transfer expr)    → (yield (Transfer _doeff_k expr))
   (finish expr)      → (return expr)  — drop the continuation, the handled scope answers expr
   (reperform effect) → (yield (Pass effect _doeff_k))     — OCaml 5 aligned
   (pass)             → (yield (Pass _doeff_effect _doeff_k))      — deprecated, use reperform
   (the clause's effect and continuation are `_doeff_effect` / `_doeff_k` — expansion_names.py — so a clause field named
   `k` or `effect` does not overwrite them; the Resume / Transfer / Pass above are the `_doeff_` aliases too)
   Only a form whose argument count matches the operation's arity is rewritten; raise (no rewrite) and every
   other form are walked into."
  (cond
    (not (isinstance form Expression)) form
    (= (len form) 0) form
    True
      (let [op (_clause-op form)
            args (list (cut form 1 None))]
        (cond
          (or (is op None) (is op.rewrite None) (!= (len args) op.arity))
            (Expression (lfor f form (_rewrite-ops f)))
          ;; An operation that returns to the clause after the continuation finishes (the table's returns — resume) records "resumed" after returning (a path where the clause
          ;; swallowed an exception raised in the value expression or the continuation and exited gets no mark, and becomes an error at run time · ADR-DOE-CORE-EFFECTS-003 R15). The mark's name is known only to the run-time
          ;; check of _build-clause and to this function — the table row's rewriting only builds the form handed to the VM.
          op.returns
            `(do (setv _doeff_resumed_value ~(op.rewrite args _rewrite-ops))
                 (setv _doeff_clause_resumed True)
                 _doeff_resumed_value)
          True
            (op.rewrite args _rewrite-ops)))))


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
   (<- name expr) → (setv name <open-bind binding>)  — delegate to outer handler(open-form of outcome-forms)
   (<- name Type expr) → same + isinstance assert (macros.hy _bind-yield —
   the single definition point shared with <- / do! / defp / deftest / for/do)
   (! expr) → (yield expr) in place [ADR-DOE-HY-003]"
  (import doeff-hy.macros [_is-bind _expand-bangs])
  (import doeff-hy._outcome-forms [bind-form :as _bind-form])

  (setv expanded [])
  (for [form forms]
    (setv rewritten (_expand-bangs form owner))
    (if (_is-bind rewritten)
        (.append expanded (_bind-form rewritten))
        (.append expanded rewritten)))
  expanded)


(defn _build-clause [clause [lazy-defs None] [handler-name None] [module-names None] [specs None]]
  "Parse one handler clause: (EffectType [fields] [:when guard] [:finish-reason \"…\"] body...).
   Validates termination and the clause's ending against the effect (ADR-DOE-CORE-EFFECTS-003 R15).
   Returns #(effect-type cond-body). When `specs` is a list, appends the clause's ending
   description for the definition-time check (clause_endings.check-clause-endings).
   If lazy-defs is provided, inject lazy init for referenced lazy names."
  (import doeff-hy._clause-endings [parse-clause-options clause-ops check-clause-by-name ending-spec-form])
  (assert (isinstance clause Expression)
          "handle clause must be an expression")
  (assert (>= (len clause) 3)
          "handle clause needs (EffectType [fields] body...)")

  (setv etype (get clause 0))
  (setv fields (get clause 1))
  (setv raw-body (list (cut clause 2 None)))
  (setv where (if (is handler-name None)
                  (+ "handle clause " (str etype))
                  (+ "defhandler " (str handler-name) " clause " (str etype))))

  (assert (isinstance fields List)
          "handle clause fields must be [field1 ...]")

  ;; Extract :when guard and :finish-reason
  (setv options (parse-clause-options raw-body where))
  (setv guard (. options guard))
  (setv cbody (. options body))
  (setv cbody-written cbody)

  ;; Termination check BEFORE rewriting (on original body)
  (_check-clause-terminates (str etype) cbody)

  ;; The clause's ending against what the effect declares — by name at expansion (Raise / Absent,
  ;; finish without a reason), by the effect type's declaration when the handler is defined.
  (setv ops (clause-ops cbody))
  (check-clause-by-name etype ops options where)
  (when (is-not specs None)
    (.append specs (ending-spec-form etype ops options)))

  ;; TCO: tail-position (resume expr) → (transfer expr)
  ;; Must run BEFORE _expand-handler-binds and _rewrite-ops so it sees
  ;; the original (resume ...) forms, not the rewritten (yield (Resume ...)).
  (setv cbody (_tco-seq cbody))

  ;; Rewriting of val / var / lazy val / lazy var / := (ADR-DOE-HY-006). The clause's fields, effect, k, and
  ;; the session names directly under the handler are names bound from before the body (effect and k are reserved names the expansion binds
  ;; only when the writer wrote them in the body / guard — _clause-aliases).
  (import doeff-hy.macros [_rewrite-bindings])
  (setv cbody
    (_rewrite-bindings cbody
                       (if (is handler-name None)
                           (+ "handle clause " (str etype))
                           (+ "defhandler " (str handler-name) " clause " (str etype)))
                       _BodyKind.CLAUSE
                       (+ (lfor f fields (str f)) ["effect" "k"])
                       (_session-names (or lazy-defs []))
                       :module module-names))

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
  ;; Fetching a session value (session val / var, old lazy-val / lazy-var) is placed before the place that uses that name:
  ;; a name read by the :when guard goes before the guard (guard-prefix), a name read only by the body goes after the guard passes (lazy-prefix).
  ;; The guard is also a "place of use", so if it is first used in the guard, it is made there (keeping the meaning of lazy). Without placing it before the guard, a name inside the guard was
  ;; undefined (a local variable of the clause function = UnboundLocalError). A name the guard does not read is, as before, not made for an effect the guard rejected.
  (setv guard-prefix [])
  (setv lazy-prefix [])
  (when (and lazy-defs handler-name)
    (for [#(lname lbody _mut legacy?) lazy-defs]
      ;; Symbol scan: only inject if clause body references this lazy name
      (cond
        (and (is-not guard None) (_references-symbol guard (str lname)))
          (.extend guard-prefix
            (_build-lazy-init-forms handler-name lname lbody legacy?))
        (any (gfor form cbody-written (_references-symbol form (str lname))))
          (.extend lazy-prefix
            (_build-lazy-init-forms handler-name lname lbody legacy?)))))

  ;; Field bindings: (setv field (. _doeff_effect field))
  ;; In the expansion for type checking (doeff_hy/_static_view.py — not run, only pyright reads it), right after the bundle
  ;; place `_ = field` to mark the name as "read". The bundle uses the field name, so a field the body does not use (e.g. mark in `[stored mark]`) becomes a
  ;; reportUnusedVariable error, and the writer cannot change the name (starting with `_` would no longer match the field). Deciding whether the body references the name by
  ;; scanning symbols would miss dotted symbols like `stored.version` and references made by macros, so it is placed for every field.
  ;; A misspelled field name is still an error, as before, via the attribute read `(. _doeff_effect field)`. The runtime expansion is unchanged.
  ;; Fields read the clause's effect from `_doeff_effect`: even if a field name is `effect` or `k`, it does not overwrite the received effect and continuation.
  ;; The bundle of `effect` and `k` that the writer wrote in the body / guard is placed before the field bundle (if a field has the same name, it is not bundled and the field takes priority).
  (setv bindings
    (+ (lfor row (_clause-aliases clause lazy-defs)
             form (_alias-forms row)
         form)
       (lfor f fields
             form (if (_static-view-enabled)
                      [`(setv ~f (. ~(_effect-symbol) ~(Symbol (str f))))
                       `(setv _ ~f)]
                      [`(setv ~f (. ~(_effect-symbol) ~(Symbol (str f))))])
         form)))

  ;; Rewrite resume/transfer/finish/pass (lazy-prefix is already in yield IR,
  ;; but _rewrite-ops only touches the clause operations — safe to pass through)
  (setv rewritten (+ lazy-prefix (lfor form cbody (_rewrite-ops form))))

  ;; Clause for a generic effect: rebind before the field bundle so the type arguments of the narrowed `_doeff_effect` do not become Unknown (expansion for type checking only).
  (setv bindings (+ (_effect-form-binding etype fields rewritten) bindings))

  ;; A clause that leaves without resume / finish (transfer / reperform / raise never return) is an
  ;; error at run time — the VM would otherwise end the handled scope silently with the clause's value
  ;; (ADR-DOE-CORE-EFFECTS-003 R15).
  (setv checked
    `(do (setv _doeff_clause_resumed False)
         (setv _doeff_clause_value (do ~@rewritten))
         (if _doeff_clause_resumed
             _doeff_clause_value
             (raise (do ~(_clause-endings-import 'fell-through '_doeff-fell-through)
                        (_doeff-fell-through ~(if (is handler-name None) "handle" (str handler-name))
                                             ~(str etype)))))))

  ;; Build body: bindings first, then guard, then logic
  (setv full-body
    (if (is guard None)
        `(do ~@bindings ~checked)
        `(do ~@bindings
             ~@guard-prefix
             (if (not ~guard)
                 (yield (~(_alias-symbol "Pass") ~(_effect-symbol) ~(_continuation-symbol)))
                 ~checked))))

  #(etype full-body))


(defn _build-handler-expr [clauses [lazy-defs None] [handler-name None] [module-names None] [specs None]]
  "Build handler expression from clauses. Returns _doeff-do wrapped fn.
   When `specs` is a list, appends each clause's ending description — the caller emits the check
   of those against the effects' declared resumption (ADR-DOE-CORE-EFFECTS-003 R15 — catches what
   the name alone cannot, e.g. an aliased Raise).
   If lazy-defs is provided, lazy init is injected into clauses that reference them."
  (setv cond-forms [])

  (for [clause clauses]
    (setv #(etype body) (_build-clause clause
                                       :lazy-defs lazy-defs
                                       :handler-name handler-name
                                       :module-names module-names
                                       :specs specs))
    (.append cond-forms (_clause-test etype (_clause-aliases clause lazy-defs)))
    (.append cond-forms body))

  ;; Default: pass unmatched
  (.append cond-forms 'True)
  (.append cond-forms `(yield (~(_alias-symbol "Pass") ~(_effect-symbol) ~(_continuation-symbol))))

  ;; Expansion for type checking: effect is object (the clause's isinstance narrows it to each clause's type — annotating it with the union of the clause types
  ;; would make the last clause's isinstance an "always true" error), k is the continuation, and the answer is ClauseRun (doeff_hy/_static_types.pyi).
  ;; In a handler that has a clause where the writer wrote `effect`, `effect` is bundled to the pre-narrowing value before the clause test (_narrowed-alias-bindings).
  ;; The runtime expansion annotates effect with the clause's type for the VM's type narrowing (_effect-parameter).
  (setv dispatcher
    (if (_static-view-enabled)
        `(_doeff-do (fn #^ _doeff_ClauseRun [(annotate ~(_effect-symbol) object)
                                             (annotate ~(_continuation-symbol) _doeff_Continuation)]
                      ~@(_narrowed-alias-bindings clauses lazy-defs)
                      (cond ~@cond-forms)))
        `(_doeff-do (fn [~@(_effect-parameter (lfor clause clauses (get clause 0))) ~(_continuation-symbol)]
                      (cond ~@cond-forms)))))
  (setv passed (_passed-effects-source clauses (_session-names (or lazy-defs []))))
  (if (is passed None)
      dispatcher
      `((do (import doeff._vm [declare-passes :as _doeff-declare-passes]) _doeff-declare-passes)
        ~dispatcher ~passed)))


(defn _passed-effects-source [clauses session-names]
  "The thunk telling the VM which effects a catch-all handler passes on untouched, or None.
   A handler whose one EffectBase clause is guarded by (not (isinstance effect X)) — the fence shape: answer
   everything except X — passes an X effect that no earlier clause names straight to (Pass effect k). The
   annotation cannot say \"everything except X\", so the VM entered the handler for every effect (agora-redesign
   #2008 — the doeff-cluster fence and dead-process gate: 18,000 entries in one invariant test). The thunk answers
   #(X #(earlier clause types)) at install (doeff._vm.declare-passes) and the VM skips the handler for
   an X effect outside those types — the same as the guard failing. Any other guard shape, fields on the clause, or
   an X that reads a session name (bound only inside the clause) → None (the handler sees every effect)."
  (setv catch-alls (lfor #(index clause) (enumerate clauses) :if (= (str (get clause 0)) "EffectBase") index))
  (when (!= (len catch-alls) 1)
    (return None))
  (setv index (get catch-alls 0))
  (setv clause (get clauses index))
  (setv #(guard _body) (_parse-guard (list (cut clause 2 None))))
  (setv test (if (and (isinstance guard Expression) (= (len guard) 2) (= (str (get guard 0)) "not"))
                 (get guard 1)
                 None))
  (setv shaped (and (isinstance test Expression)
                    (= (len test) 3)
                    (= (str (get test 0)) "isinstance")
                    (= (str (get test 1)) "effect")
                    (= (len (get clause 1)) 0)))
  (when (or (not shaped)
            (_references-symbol (get test 2) "effect")
            (any (gfor name session-names (_references-symbol (get test 2) (str name)))))
    (return None))
  `(fn [] #(~(get test 2) #(~@(lfor earlier (cut clauses 0 index) (get earlier 0))))))


(defn _effect-parameter [etypes]
  "The handler's effect parameter, annotated with the clauses' effect types — the VM reads the annotation
   once at install (doeff_vm._effect_types / SPEC-WITHHANDLER-TYPE-FILTER) and skips this handler for any
   other effect without calling into Python, the same as the handler passing it on first thing
   (the handler list walk was 28〜39% of an invariant test's CPU). The clauses
   already answer only their own types (every other effect falls to (Pass effect k)), so the
   annotation changes nothing but the cost. On Python 3.14+ the annotation is evaluated lazily (PEP 649),
   so an effect type defined later or in an enclosing function resolves. Before 3.14 an annotation is
   evaluated when the function is made, so it is written as text and evaluated at install in the module's
   globals — a name it cannot resolve there leaves the handler unfiltered (today's behaviour) with a warning,
   never an import error. No clauses → no annotation."
  (import sys)
  (cond
    (not etypes) [(_effect-symbol)]
    (>= sys.version-info #(3 14)) [`(annotate ~(_effect-symbol) ~(if (= (len etypes) 1) (get etypes 0) `(| ~@etypes)))]
    True [`(annotate ~(_effect-symbol) ~(.join " | " (lfor t etypes (.join "." (map hy.mangle (.split (str t) "."))))))]))


(defn _refuse-session-clauses [clauses]
  "SyntaxError if a handle clause contains a session val / session var / old lazy declaration. If it were read as a defhandler clause,
   the declaration's form would hit the field-list check and raise an AssertionError 'handle clause fields must be [field1 ...]', leaving what to fix unreadable."
  (for [c clauses]
    (setv decl (_parse-declaration c))
    (when (or (and (is-not decl None) (= (. decl timing) _Timing.SESSION)) (_is-lazy-clause c))
      (raise (SyntaxError (+ "\nhandle: " (cut (hy.repr c) 1 None) " — handle does not support session val / session var. "
                             "Session values make their key from the handler's name, so move the clause to a defhandler, give it a name, "
                             "and apply it with (with-handler [name] body).\n"))))))


;; ---------------------------------------------------------------------------
;; Public macros
;; ---------------------------------------------------------------------------

(defmacro handle [_hy-compiler body #* clauses]
  "Inline pattern-matching effect handler.

   (handle body
     (EffectType [field1 field2]
       (resume (compute field1 field2)))
     (OtherEffect [x]
       :when (pred x)
       (resume (+ x 1))))

   Wraps body with the Rust handler node. Unmatched effects auto-Pass.
   Compile-time error if any clause branch lacks resume/transfer/finish/reperform; each clause's
   ending is checked against its effect's declared resumption when the handle form is evaluated.

   Inside a function body (such as the body of a defk) or a class body, put the import, the clause-ending check, and the handler assembly into an immediately
   called zero-argument function, and apply the 'function that wraps the body with the handler' that it returns to the body `body`. A name imported inside a function
   becomes a local name of the whole function, so if left side by side, the `_doeff_do` of the traverse / for/do read earlier in the same body
   failed with UnboundLocalError (tests/test_handle_scope.py). A name imported in a class body becomes a class attribute, and the clause
   function skips the class level when looking up names, so the clause's Transfer, Resume, and Pass failed with NameError.
   If imported inside the immediately called function, the clause function reads names from that function's closure, and depends neither on the globals of the module where it is written nor on
   the writer's same-named names. The body belongs to the outer do's context (the yield of `!` belongs to the outer generator), so it is not put inside the immediately called function, and
   is evaluated where it is written. At module level, they are placed side by side as before (imports go in the module's globals).

   The wrapping function is bound to a unique name (gensym) in a statement before the body, and then applied to the body. Hy hoists the statement parts of the body (the
   `(f)` of `(do (f) prog)`, let bindings, the yield of `!`) into statements before the expression containing them. If the immediately called function is called in the same expression as the body, the body's
   statement parts would run before the check, and even for a handler whose check fails, the body's effects came out first (
   blind A). The only name left in the outer function is this unique name, and there is no place that reads it earlier, so it never becomes unbound.

   The clause grammar is the same one as defhandler (_build-handler-expr), and the resulting program returns the same answer as `((defhandler name clause...) body)`
   (handle for one-off use, defhandler for reuse — just move the clauses to a defhandler and name it). It does not accept session val / session var
   (and old lazy) directly under the handler: session values make their key from the handler's name (doeff_hy._session.session_key), so
   put them in a named defhandler."
  (import doeff-hy.macros [_module-names _in-function-body? _in-class-body?])
  (_refuse-session-clauses clauses)
  (setv specs [])
  (setv h-expr (_build-handler-expr clauses :module-names (_module-names _hy-compiler) :specs specs))
  (setv prelude [(_do-import)
                 (_vm-imports)
                 (_clause-endings-import 'check-clause-endings '_doeff-check-clause-endings)
                 `(_doeff-check-clause-endings "handle" [~@specs])])
  (_locate-synthesized (if (or (_in-function-body? _hy-compiler) (_in-class-body? _hy-compiler))
      ;; gensym is fetched only in this branch — to keep the unique-name numbering of the module-level expansion the same as before
      (let [install (hy.gensym "handle_install")]
        `(do
           (setv ~install ((fn []
                             ~@prelude
                             (setv __doeff-handler-data__ ~h-expr)
                             (fn [__doeff-body__] (~(_alias-symbol "WithHandlerType") __doeff-handler-data__ __doeff-body__)))))
           (~install ~body)))
      `(do
         ~@prelude
         (~(_alias-symbol "WithHandlerType") ~h-expr ~body)))))


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


(defmacro defhandler [_hy-compiler name #* rest]
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

   ;; Values shared across the session (through the state effects Get / Put — ADR-DOE-HY-006)
   (defhandler my-handler
     (session val client (Client :password (! (Ask \"api_key\"))))
     (session var calls 0)
     (Effect [field]
       (:= calls (+ calls 1))
       (resume (.fetch client field))))
   ;; The old (lazy ...) / (lazy-val ...) / (lazy-var ...) / (set! ...) are accepted with unchanged behavior, and
   ;; DeprecationWarning guides migration to session val / session var / :=.

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

  ;; Optional declaration dict {:effects [...] :tags {:context … :role …}} (doeff_hy._declarations).
  ;; A handler has no :pre / :post, so only :effects and :tags are accepted.
  (setv declarations None)
  (when (and (> (len clauses) 0) (isinstance (get clauses 0) Dict))
    (setv declarations (get clauses 0))
    (setv clauses (cut clauses 1 None))
    (_refuse-unknown-keys declarations _DECLARATION-KEYS (+ "defhandler " (str name))))
  (setv declared (_declaration-setters name declarations (+ "defhandler " (str name))))

  ;; Separate lazy defs from effect clauses
  (setv #(lazy-defs effect-clauses) (_extract-lazy-clauses clauses name))

  (import doeff-hy.macros [_module-names _scoped-definition _in-class-body?])
  (setv module-names (_module-names _hy-compiler))
  (setv specs [])
  (setv handler-expr
    (if lazy-defs
        (_build-handler-expr effect-clauses
                             :lazy-defs lazy-defs
                             :handler-name name
                             :module-names module-names
                             :specs specs)
        (_build-handler-expr effect-clauses :handler-name name :module-names module-names :specs specs)))
  ;; The check that matches clause endings against effect-resume declarations runs once, the first time the handler wraps a body (so that the types of effects written after the
  ;; definition can also be looked up — ADR-DOE-CORE-EFFECTS-003 R15).
  (setv endings-check
    `(_doeff-check-clause-endings-once __doeff-handler-data__ ~(str name) __doeff-clause-endings__))

  ;; Preserve s-expr body as a list of all clauses (including lazy) — carried as one hy.repr string and read back
  ;; on first access (macros._quoted-forms: a quote here compiled into code that rebuilt the model tree on every import).
  (import doeff-hy.macros [_quoted-forms])
  (setv quoted-body (_quoted-forms clauses))

  ;; Extra imports needed when lazy is used
  ;; The expansion for type checking does not use Get (the name is bound to the initialization value — _build-lazy-init-forms). Put, Some, and the key's session-key are
  ;; fetched as `_doeff_<name>`, and imported only when doeff-hy-check reads the head of the module (SESSION_STATIC_IMPORTS in static_view.py).
  ;; Emitting them per handler here made Put / Some an "import is unused" error in a module whose handlers have neither := nor set!
  ;; (the re-export spelling X :as X does not reach pyright because Hy drops an alias with the same name).
  ;; Get, Put, and Some are bundled under `_doeff_<name>` aliases (so as not to hide the writer's own KV effects such as Put and Get — expansion_names.py).
  (setv lazy-imports
    (cond
      (or (not lazy-defs) (_static-view-enabled)) `(do)
      True
        `(do ~(_import-form ["Some" "Get" "Put"])
             (import doeff-hy._session [session-key :as _doeff-session-key]))))

  ;; defhandler produces a Program -> Program function instead of exposing a
  ;; raw handler dispatcher. The inner dispatcher is stored for introspection
  ;; and installed with the Rust VM WithHandler node.
  ;; A defhandler written inside a function body puts the import and the definition into an immediately called function and binds only name (_scoped-definition —
  ;; so the `_doeff_do` of the traverse / for/do read earlier in the outer function does not become unbound).
  ;; In a class body, the import is moved into the function that assembles the handler (an immediately called function for a handler without arguments, the name
  ;; function itself for a handler with arguments). An import in a class body becomes a class attribute, and the assembling function, the clause function, and the wrapping function skip the class level
  ;; when looking up names, so _doeff_do, Transfer, etc. failed with NameError. If imported inside,
  ;; those functions read names from the assembling function's locals and closure. The definition of name stays at class level (`__qualname__`).
  (setv imports [(_do-import)
                 (_vm-imports)
                 (_clause-endings-import 'check-clause-endings-once '_doeff-check-clause-endings-once)
                 lazy-imports]
        #(outer-imports inner-imports) (if (_in-class-body? _hy-compiler) #([] imports) #(imports [])))
  ;; A defhandler with arguments is a handler factory (a function that returns a handler when called). The runtime expansion annotates the factory's return type as doeff's ProgramHandler
  ;; — so that the public API inventory (tools/public_api_inventory.py) and inspect.signature can read the factory as a function that returns a handler (without the annotation it
  ;; was unclassified as a "function with no return type"). It is not annotated in a class body (the import moves into the assembling function, and
  ;; the name is not visible from the definition level). The expansion for type checking is not annotated either (pyright infers the type of the wrapping function, and the type-declaration generator static_stub.py
  ;; writes the factory's answer as Handler).
  (setv factory-annotated (and (is-not params None) (not (_static-view-enabled)) (not (_in-class-body? _hy-compiler)))
        factory-head (if factory-annotated `(annotate ~name ~(_alias-symbol "ProgramHandler")) name)
        factory-imports (if factory-annotated [(_import-form ["ProgramHandler"])] []))
  (_locate-synthesized (_scoped-definition _hy-compiler name (if (is params None)
      [#* outer-imports
       `(setv ~name
         ((fn []
            ~@inner-imports
            (setv __doeff-handler-data__ ~handler-expr)
            (setv __doeff-clause-endings__ (fn [] [~@specs]))
            ~(_handler-fn-form endings-check)
            (setattr __doeff-handler-fn__ "__doc__" ~docstring)
            (setattr __doeff-handler-fn__ "_doeff_is_handler_fn" True)
            (setattr __doeff-handler-fn__ "__doeff_handler_data__"
                  __doeff-handler-data__)
            __doeff-handler-fn__)))
       `(setattr ~name "__doeff_body__" ~quoted-body)
       `(setattr ~name "__doeff_name__" ~(str name))
       #* declared]
      [#* outer-imports
       #* factory-imports
       `(defn ~factory-head [~@params]
         ~@inner-imports
         (setv __doeff-handler-data__ ~handler-expr)
         (setv __doeff-clause-endings__ (fn [] [~@specs]))
         ~(_handler-fn-form endings-check)
         (setattr __doeff-handler-fn__ "__doc__" ~docstring)
         (setattr __doeff-handler-fn__ "_doeff_is_handler_fn" True)
         (setattr __doeff-handler-fn__ "__doeff_handler_data__"
               __doeff-handler-data__)
         (setattr __doeff-handler-fn__ "__doeff_name__" ~(str name))
         __doeff-handler-fn__)
       `(setattr ~name "__doc__" ~docstring)
       `(setattr ~name "__doeff_body__" ~quoted-body)
       `(setattr ~name "__doeff_name__" ~(str name))
       #* declared]))))
