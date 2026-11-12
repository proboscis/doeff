;;; doeff-hy standard macros — effect composition for doeff.
;;;
;;; Usage:
;;;   (require doeff-hy.macros [do! defk deff fnk <- ! <-> set! defp defpp deftest
;;;                             defpipeline traverse for/do
;;;                             defhandler handle with-handler defmcp-tool
;;;                             on-raise absent-as   ; boundary between absence and failure (ADR-DOE-CORE-EFFECTS-003)
;;;                             validate check])   ; validate / check require doeff-validation to be installed
;;;   (import doeff [do :as _doeff-do])
;;;
;;; Effects of the reference implementation are imported from doeff_core_effects:
;;;   (import doeff_core_effects [Ask Try slog])
;;;
;;; Contract rules:
;;;   - deff:        {:pre [...]} and {:post [...]} are REQUIRED
;;;   - defk:        {:pre [...]} and {:post [...]} are REQUIRED
;;;   - defp/defpp:  {:post [...]} is REQUIRED, :pre not allowed
;;;   - fnk:         no contracts (anonymous)
;;;   - do!:         :pre/:post optional, supports (: name Type) shorthand
;;;   - (: name Type) in :pre/:post expands to (isinstance name Type)
;;;   - Generic types with element types can also be written: (: % (get tuple #(X ...))) / (: % (of dict K V)) (also inside a union) — at runtime only
;;;     the outer type (tuple / dict) is checked; element types are left to the static type checker (runtime-type-form, #1790)
;;;   - Arbitrary expressions can be mixed with (: ...) in the same list

;; ---------------------------------------------------------------------------
;; Internal: .hyk/.hyp extension enforcement
;; ---------------------------------------------------------------------------

(import os.path)
(import dataclasses [dataclass])
(import importlib)
(import inspect)
;; The types that the runtime guards (_guard-performed, _guard-statement-value) look up on every defk call and every statement (#844)
(import doeff [DoExpr EffectBase])
(import doeff.mcp [mcp-param-annotation mcp-json-type-names])
;; Expansion helpers are bundled under `_` names — this module is the boundary of macros that Hy's require reads by path, and the only names it exposes are macros
;; (ADR-DOE-API-001 R14). If they were bundled under plain names, the inventory would count them as public names of doeff_hy.macros.
(import doeff-hy._declarations [CONTRACT-KEYS :as _CONTRACT-KEYS
                               FUNCTION-CONTRACT-KEYS :as _FUNCTION-CONTRACT-KEYS
                               refuse-unknown-keys :as _refuse-unknown-keys
                               declaration-setters :as _declaration-setters
                               defeffect-form :as _defeffect-form
                               declared-value :as _declared-value
                               type-param-symbols :as _type-param-symbols
                               record-names :as _record-names])
(import doeff-hy._positions [locate-synthesized :as _locate-synthesized])
(import doeff-hy._expansion-names [alias-symbol :as _alias-symbol])
;; Read from the Hy compiler's scope at expansion time whether a definition macro was written inside a function body (_scoped-definition)
(import hy.scoping [nearest-python-scope is-function-scope ScopeFn])
;; Argument types of a macro — at expansion time a macro receives the Hy compiler (`_hy-compiler`) and the written form (`hy.models.Object`).
;; defmacro creates a function at compile time, and up to 3.13 it evaluates argument annotations on the spot, so these are also placed in the compile-time namespace.
(eval-and-compile
  (import hy.compiler [HyASTCompiler])
  (import hy.models [Object]))

;; Re-export handle macros so users only need one require line.
;; Without this, forgetting (require doeff-hy.handle [defhandler]) causes
;; (defhandler ...) to compile as a function call, silently leaking yield
;; from nested <- to the enclosing defn scope. See #387.
(require doeff-hy.handle [defhandler handle with-handler])

(defn _compiling-file-ext [compiler]
  "Return the file extension (.hyk / .hyp, else \"\") of the file the macro is being expanded in.
   Reads the Hy compiler's filename (the macro's _hy-compiler argument) — the old form walked
   inspect.stack() on every defk/deff/defp expansion, which reads the source of every frame on
   the stack and made compiling a large .hy file take minutes."
  (setv filename compiler.filename)
  (setv ext (if (isinstance filename str) (get (os.path.splitext filename) 1) ""))
  (if (in ext [".hyk" ".hyp"]) ext ""))

(defn _enforce-no-defp-in-hyk [compiler macro-name fn-name]
  "Raise SyntaxError if a defp/defpp macro is used in a .hyk file."
  (setv ext (_compiling-file-ext compiler))
  (when (= ext ".hyk")
    (raise (SyntaxError (.format "
{macro} {name}: cannot define a Program entrypoint in a .hyk file.

  .hyk files are for kleisli functions (defk, deff, defhandler).
  Move this {macro} to a .hyp file instead.
" :macro macro-name :name fn-name)))))

(defn _warn-defk-in-hyp [compiler macro-name fn-name]
  "Warn if defk/deff is used in a .hyp file."
  (setv ext (_compiling-file-ext compiler))
  (when (= ext ".hyp")
    (import warnings)
    (warnings.warn
      (.format "{macro} {name}: .hyp files are for Program entrypoints (defp). Consider moving {macro} to a .hyk file."
               :macro macro-name :name fn-name)
      UserWarning
      :stacklevel 4)))

;; ---------------------------------------------------------------------------
;; Internal: contract extraction
;; ---------------------------------------------------------------------------

(defn _find-contract [body]
  "Find the optional contract dict at the front of body forms (body[0], or body[1] after a docstring).
   Returns #(contract-or-None body-without-contract) — the definition macros read :effects / :tags from it."
  (setv contract-idx None)
  (for [#(i form) (enumerate body)]
    (when (isinstance form hy.models.Dict)
      (setv contract-idx i)
      (break))
    ;; Skip string literals (docstrings) at the start
    (when (not (isinstance form hy.models.String))
      (break)))
  (if (is contract-idx None)
      #(None body)
      #((get body contract-idx) (+ (cut body 0 contract-idx) (cut body (+ contract-idx 1) None)))))

(defn _extract-contracts [body [allowed _CONTRACT-KEYS] [where "contract"]]
  "Parse optional {:pre [...] :post [...]} from front of body forms.
   Skips leading docstring if present.
   Returns #(pre-checks post-checks real-body).
   pre-checks/post-checks are None if not specified, [] if specified but empty.
   Keys outside `allowed` are refused (SyntaxError) instead of silently dropped —
   definitions accept :effects / :tags as well (doeff_hy._declarations)."
  (setv pre-checks None
        post-checks None)
  (setv #(contract real-body) (_find-contract body))
  (when (is-not contract None)
    (_refuse-unknown-keys contract allowed where)
    (for [#(k v) (zip (cut contract None None 2) (cut contract 1 None 2))]
      (when (= (str k) ":pre")
        (setv pre-checks (list v)))
      (when (= (str k) ":post")
        (setv post-checks (list v)))
      ;; :writes only has its shape checked and leaves nothing in the expansion (the linter reads the source spelling — FUNCTION-CONTRACT-KEYS in doeff_hy/_declarations.hy).
      (when (= (str k) ":writes")
        (_record-names v where ":writes"))))
  #(pre-checks post-checks real-body))

(defn _is-type-check [form]
  "Check if form is (: name Type) — type contract shorthand.
   In Hy, bare : is read as Keyword(''), not Symbol(':')."
  (and (isinstance form hy.models.Expression)
       (>= (len form) 3)
       (isinstance (get form 0) hy.models.Keyword)
       (= (str (get form 0)) ":")))

(defn _type-check-target [form]
  "If form is (: name Type), return name as string. Otherwise None."
  (when (_is-type-check form)
    (str (get form 1))))

(defn _extract-param-names [params]
  "Extract parameter names from a defk/deff param list.
   Handles: [x y], [x * [timeout 30]], [x #^ int y], etc.
   Returns set of name strings (excludes * separator)."
  (setv names (set))
  (for [p params]
    (cond
      ;; * is keyword-only separator
      (and (isinstance p hy.models.Symbol) (= (str p) "*"))
        None
      ;; [name default] — keyword arg with default
      (isinstance p hy.models.List)
        (when (> (len p) 0)
          (.add names (str (get p 0))))
      ;; #^ type name — annotated param (annotation is previous form, name follows)
      ;; Hy puts annotation as FComponent, we see Symbol
      (isinstance p hy.models.Symbol)
        (when (!= (str p) "&rest")
          (.add names (str p)))))
  names)

(setv _HANDLER-PARAM-NAMES #{"effect" "eff" "k"})

(setv _DEFHANDLER-EXAMPLE "
  Use defhandler instead of defk for effect handlers:

    (require doeff-hy.macros [defk <- defhandler])
    ;; Simple handler — pattern match on effects, unmatched auto-Pass
    (defhandler my-handler
      (MyEffect [field1 field2]
        (resume (compute field1 field2)))
      (OtherEffect [x]
        (resume (+ x 1))))

    ;; Handler with parameters — returns a handler factory
    (defhandler my-handler [config]
      (MyEffect [field]
        (resume (process field config))))

    ;; Handler with guard — auto-reperform when guard is false
    (defhandler my-handler
      (MyEffect [field]
        :when (> field 0)
        (resume field)))

    ;; Conditional forwarding — (reperform effect) is terminal
    (defhandler my-handler
      (MyEffect [field]
        (if (can-handle field)
            (resume (process field))
            (reperform effect))))

    ;; Delegation — (<- result effect) is non-terminal (keeps control)
    (defhandler my-handler
      (MyEffect [field]
        (<- base-result (MyEffect :field field))
        (resume (* base-result 2))))

    ;; Install through explicit handler stack syntax
    (with-handler [my-handler] body)
    (with-handler [(my-handler config)] body)
")

(defn _reject-handler-signature [fn-name params]
  "Reject defk with handler-like parameter names (effect, eff, k).
   These should use defhandler instead."
  (setv param-names (_extract-param-names params))
  (setv handler-params (sorted (& param-names _HANDLER-PARAM-NAMES)))
  (when handler-params
    (setv found (.join ", " handler-params))
    (raise (SyntaxError (+ (.format "
defk {name}: parameter names [{found}] look like an effect handler signature.
" :name fn-name :found found) _DEFHANDLER-EXAMPLE)))))

(setv _USELESS-TYPES #{"object" "Any"})

(defn _reject-useless-types [fn-name phase checks]
  "Reject (: name Type) where Type is object or Any — too broad to be useful.
   String literals are allowed (documentation-only annotations).
   Raises SyntaxError with a fix suggestion."
  (for [check checks]
    (when (_is-type-check check)
      (setv tp (get check 2))
      (when (isinstance tp hy.models.String) (continue))  ; string = doc annotation, skip
      (setv tp-str (str tp)
            target-str (str (get check 1)))
      (when (in tp-str _USELESS-TYPES)
        (raise (SyntaxError (+ (.format "
defk {name}: {phase} type `{tp}` on `{target}` is too broad to be a useful contract.

  Use a specific type instead of `{tp}`:

    (: {target} SomeConcreteType)

  Contracts exist to catch bugs early — `{tp}` matches everything and catches nothing.

  If this is an effect handler, the return type depends on the wrapped program
  and cannot be expressed as a :post contract.
" :name fn-name :phase phase :tp tp-str :target target-str) _DEFHANDLER-EXAMPLE)))))))

(defn _validate-pre-type-checks [fn-name params pre-checks]
  "Validate that :pre has a (: param Type) for every parameter.
   Raises SyntaxError if any parameter lacks a type check."
  (_reject-useless-types fn-name ":pre" pre-checks)
  (setv param-names (_extract-param-names params))
  (when (not param-names) (return))  ; zero-arg function — nothing to check
  (setv checked (set))
  (for [check pre-checks]
    (setv target (_type-check-target check))
    (when (and target (in target param-names))
      (.add checked target)))
  (setv missing (sorted (- param-names checked)))
  (when missing
    (setv missing-str (.join ", " missing))
    (setv all-params (sorted param-names))
    (setv full-pre (.join " " (lfor p all-params (+ "(: " p " SomeType)"))))
    (raise (SyntaxError (.format "
defk {name}: :pre must have a (: param Type) for every parameter.

  Missing: {missing}

  Fix — add type checks for each parameter:

    (defk {name} [{params}]
      {{:pre [{pre}]
       :post [(: % ReturnType)]}}
      ...)
" :name fn-name :missing missing-str
  :params (.join " " all-params)
  :pre full-pre)))))

(defn _validate-post-type-check [fn-name post-checks]
  "Validate that :post has at least one (: % Type) return type check.
   Raises SyntaxError if missing."
  (_reject-useless-types fn-name ":post" post-checks)
  (setv has-return-type False)
  (for [check post-checks]
    (setv target (_type-check-target check))
    (when (= target "%")
      (setv has-return-type True)
      (break)))
  (when (not has-return-type)
    (raise (SyntaxError (.format "
defk {name}: :post must include a return type check (: % Type).

  Fix:

    (defk {name} [...]
      {{:pre [...]
       :post [(: % dict)]}}              ;; ← add return type
      ...)

  (: % Type) checks isinstance on the return value.
  You can add extra validations too:
    {{:post [(: % pd.DataFrame) (> (len %) 0)]}}
" :name fn-name)))))

;; The single point that maps a type expression into a form usable as the second argument of isinstance is runtime-type-form in doeff_hy/_type_forms.py (called by the contract `(: x T)` and
;; the binding `(<- x T e)`; an external tool [the Hy projection of the quality check] calls the same function — moved to a public pure-Python function in root 2 of #3366).
(import doeff-hy._type-forms [runtime-type-form :as _runtime-type-form])

(defn _runtime-type-check [target rt message]
  "The single point of the form that checks types at runtime for the contract `(: x T)` and the binding `(<- x T e)`. rt = the type expression mapped by runtime-type-form.
   The path for a correct type stays a plain isinstance (entering a try has no cost since Python 3.11). Only when isinstance raises TypeError —
   when the type expression is a name (a type alias) whose value is a generic type with type arguments or a union containing one (the value of the name is unknown at expansion time) —
   the value is mapped to its outer type with doeff_hy._type_forms.runtime-class and checked again (at runtime only the outer type — the decision of #1790). A name whose value is not a type
   is rejected with the TypeError that runtime-class raises."
  `(try
     (assert (isinstance ~target ~rt) ~message)
     (except [TypeError]
       (import doeff-hy._type-forms [runtime-class :as _doeff-runtime-class])
       (assert (isinstance ~target (_doeff-runtime-class ~rt)) ~message))))

;; ---------------------------------------------------------------------------
;; Type annotations — also write the contract's `(: x T)` into the function's annotations (read by the static checker)
;; ---------------------------------------------------------------------------
;;
;; Contract types have been used for runtime isinstance checks but were not emitted into function annotations,
;; so pyright did not know the argument and return types of defk / deff (measured 2026-09-23:
;; the Hy version statically caught none of 7 deliberate mistakes). Map the same types into annotations.
;; Annotations are written as strings (not evaluated at definition time even up to Python 3.13 — a contract
;; that names a type defined later does not make the definition fail). A string type `(: x "description")` does not become an annotation.

(import doeff-hy._static-view [static-view-enabled :as _static-view-enabled])

(defn _static-view? []
  "True only while doeff-hy-check is expanding for type checking (doeff_hy/_static_view.py)."
  (_static-view-enabled))

(defn _type-source [tp]
  "Turn a contract type expression into a Python source string. A string type (description only) gives None.
   The tuple `#(int None)` is isinstance notation, so the annotation maps it to `int | None`."
  (import ast)
  (import hy.compiler [hy-compile])
  (cond
    (isinstance tp hy.models.String) None
    (and (isinstance tp hy.models.Symbol) (= (str tp) "None")) "None"
    (isinstance tp hy.models.Tuple)
      (let [parts (lfor item tp (_type-source item))]
        (if (in None parts) None (.join " | " parts)))
    ;; `(type None)` (the way to pass None to isinstance) becomes `None` in an annotation.
    (and (isinstance tp hy.models.Expression) (= (len tp) 2)
         (= (str (get tp 0)) "type") (= (str (get tp 1)) "None"))
      "None"
    ;; root=ast.Expression makes Hy pass type_ignores to ast.Expression, which gives a
    ;; DeprecationWarning on Python 3.14, so compile as a module and take the last expression.
    ;; A call form (such as `(type x)`) cannot be written as a type annotation, so it does not become an annotation.
    True
      (let [node (. (get (. (hy-compile (_of-as-get tp) "__main__" :import-stdlib False) body) -1) value)]
        (if (or (isinstance node ast.Call) (_has-call? node)) None (ast.unparse node)))))

(defn _has-call? [node]
  "Whether a call remains inside the annotation expression (so as not to build a false annotation string such as `int | of(...)`)."
  (import ast)
  (any (gfor sub (ast.walk node) (isinstance sub ast.Call))))

(defn _of-as-get [tp]
  "Map `(of base arg …)` inside a type expression to the equivalent `(get base arg)` / `(get base #(arg …))` (also inside nesting and `|`).
   `of` is a macro, so passing it directly to hy-compile, which has no macros, gives the call `of(tuple, X, ...)`, and an argument of a contract written
   as `(of tuple X ...)` lost its annotation (inside `|` it became the false annotation `of(...) | None`)."
  (cond
    (not (isinstance tp hy.models.Expression)) tp
    (and (> (len tp) 1) (isinstance (get tp 0) hy.models.Symbol) (= (str (get tp 0)) "of"))
      (let [base (_of-as-get (get tp 1))
            args (lfor arg (cut tp 2 None) (_of-as-get arg))]
        (if (= (len args) 1)
            (hy.models.Expression [(hy.models.Symbol "get") base (get args 0)])
            (hy.models.Expression [(hy.models.Symbol "get") base (hy.models.Tuple args)])))
    True (hy.models.Expression (lfor part tp (_of-as-get part)))))

(defn _contract-types [checks]
  "Build {name: source of T} from the contract's `(: name T)` (types that are only descriptions are excluded)."
  (setv types {})
  (for [check (or checks [])]
    (when (_is-type-check check)
      (setv source (_type-source (get check 2)))
      (when (is-not source None)
        (setv (get types (str (get check 1))) source))))
  types)

(defn _type-params [body where]
  "Return the contract dict's `:tp [T …]` as a list of symbols (empty if absent). This is so that the type of a function whose answer is decided by the argument types (such as a wrapper returning the body's answer as is) can be passed to the type check and the .pyi as a generic that cannot be checked at runtime.
   A value that is not a sequence of names, or overlapping names, is a SyntaxError."
  (setv contract (get (_find-contract body) 0))
  ;; The shape check is shared with defeffect's :tp (type-param-symbols in declarations.hy).
  (_type-param-symbols (when (is-not contract None) (_declared-value contract ":tp")) where))

(defn _object-for [names form]
  "Map names (type-argument names) inside a type-expression form to object (also inside nested expressions, tuples and unions) — so that type arguments are not passed to the runtime isinstance."
  (match form
    (hy.models.Symbol) (if (in (str form) names) (hy.models.Symbol "object") form)
    (| (hy.models.Expression) (hy.models.List) (hy.models.Tuple)) ((type form) (lfor item form (_object-for names item)))
    _ form))

(defn _annotate-params [params types]
  "Attach the contract's type annotation to each argument in the argument list. Leave alone arguments the writer already wrote as `#^ T x`,
   and `*`, `/` and `#* args`."
  (hy.models.List
    (lfor p params
      (cond
        (and (isinstance p hy.models.Symbol) (in (str p) types))
          `(annotate ~p ~(hy.models.String (get types (str p))))
        (and (isinstance p hy.models.List) (> (len p) 0)
             (isinstance (get p 0) hy.models.Symbol) (in (str (get p 0)) types))
          `(annotate ~p ~(hy.models.String (get types (str (get p 0)))))
        True p))))

(defn _static-pre-type-check? [target phase]
  "Whether this is a place in the expansion for type checking (`_static-view?`) where the isinstance for an argument-type contract `(: x T)` (x in :pre is a name) is not emitted.
   The argument types are carried by annotations written from the contract (_annotate-params, and the field annotations of defeffect), so the type check does not need the isinstance. If emitted, a type alias containing a generic type with
   element types (for example `JsonBody = Mapping[str, JsonBody] | tuple[JsonBody, ...] | …`, a form written only for the type check —
   the same as doeff_hy/_json_value.py) turned red with 'the second argument of isinstance is not a type', and the alias could not carry element types. The runtime expansion is
   unchanged (it still rejects with isinstance). `(: % T)` in :post is still emitted, because some writers rely on the form that narrows the body's answer."
  (and (_static-view?)
       (in phase #("pre-condition" ":pre"))
       (isinstance target hy.models.Symbol)
       (!= (str target) "%")))

(defn _expand-check [check fn-name phase [type-params #()]]
  "Expand a single contract check into an assert form.
   (: x T) → isinstance assert with clear type error message.
   (: x \"desc\") → no-op (documentation-only annotation).
   Other  → generic condition assert.
   type-params (the contract's :tp) are erased to object only inside the type passed to isinstance — `(of Program T)` goes to the outer type Program as before
   (runtime-type-form), and a plain `(: % T)` is always true. This is the same direction as the existing decision (#1790) that only the outer type is checked at runtime; the failure message and the annotations
   (_contract-types) keep the type as written."
  (if (_is-type-check check)
      (let [target (get check 1)
            tp (get check 2)]
        (cond
          (isinstance tp hy.models.String)
            ;; String literal — documentation-only, no runtime check
            (do
              (when (not (.strip (str tp)))
                (raise (SyntaxError (.format "
defk {name}: :post type annotation cannot be an empty string.

  Describe what the return value represents:

    (: % \"DataFrame with OHLCV columns\")
    (: % \"list of matched handler results\")
" :name fn-name))))
              '(do))
          (_static-pre-type-check? target phase)
            '(do)
          True
            (let [target-label (if (= (str target) "%") "return value" (str target))
                  rt (_runtime-type-form (_object-for (sfor p type-params (str p)) tp))
                  message `(+ ~(+ (str fn-name) ": " phase " type error: `" target-label "` expected " (str tp) ", got ")
                              (. (type ~target) __name__))]
              ;; The expansion for type checking is a plain isinstance (a narrowing form — the same reading as the annotation); at runtime, the one point that does not fail even if the name's value is a generic type.
              (if (_static-view?)
                  `(assert (isinstance ~target ~rt) ~message)
                  (_runtime-type-check target rt message)))))
      `(assert ~check ~(+ (str fn-name) ": " phase " failed: " (str check)))))

(defn _is-validation-check [form]
  "Whether one condition of a contract is a doeff-validation (check …) form."
  (and (isinstance form hy.models.Expression)
       (> (len form) 0)
       (isinstance (get form 0) hy.models.Symbol)
       (= (str (get form 0)) "check")))

(defn _contract-code [checks fn-name phase kleisli? [type-params #()]]
  "Turn the list of conditions of a contract (:pre / :post) into a list of statements placed inside the function.

   - Type (: x T) and boolean expressions: one assert each as before (stops at the first failure — does not
     change the meaning of existing contracts).
   - (check operator arg … :reason reason): evaluated with doeff-validation semantics — evaluate all of them, collect
     the failures, and if there is even one, raise ValidationException (function name, pre / post, and each failure's expression, value and reason).
     A (! …) in an argument is executed on the spot. Only defk / do! (generators) accept this.
   Place check after assert: the check expressions can assume the types match."
  (setv checks (or checks []))
  (setv code (lfor c checks :if (not (_is-validation-check c)) (_expand-check c fn-name phase type-params)))
  (setv validation-checks (lfor c checks :if (_is-validation-check c) c))
  (when validation-checks
    (when (not kleisli?)
      (raise (SyntaxError (+ (str fn-name) ": " phase " (check …) can only be written in the contract of defk / do!"
                             " (it evaluates checks that use effects, so a generator body is required) — define it with defk"))))
    (.extend code (_validation-contract-block validation-checks fn-name phase)))
  code)

(defn _guard-performed [result label]
  "Raise if a kleisli (defk/do!) last expression evaluated to an unperformed
   effect. A bare EffectBase as the final form is RETURNED as a value, never
   performed — almost always a bug (regression: a do! whose last form was
   `(LaunchEffect ...)` returned the effect object and never launched the
   agent). The Program-return composition pattern returns a DoExpr (Pure/Expand/
   a @do call), NOT an EffectBase, so this never fires on it; plain functions
   (deff/defn) returning effect constructors are not kleisli and are not guarded.
   Returns `result` unchanged when fine, so it can wrap a return position.
   The generated return checks EffectBase inline and calls this helper only for
   an effect; its type comes from the module's import (no import per call — #844)."
  (when (isinstance result EffectBase)
    (raise (RuntimeError
             (+ label ": last expression is an unperformed effect `"
                (. (type result) __name__)
                "`. A defk/do! last form is RETURNED as a value, not performed — "
                "bind it instead: (<- v (" (. (type result) __name__)
                " ...)) then return v."))))
  result)

(defn _performed-guard-form [label]
  "Reject bare effects without a Python guard call on successful returns (#2817)."
  ;; The static view keeps the typed helper: an inline test on a known return
  ;; type would produce reportUnnecessaryIsInstance or widen it to EffectBase.
  (if (_static-view?)
      `(_guard-performed _contract_result ~label)
      `(when (isinstance _contract_result _doeff_effect_base)
         (_guard-performed _contract_result ~label))))


;; ADR-DOE-HY-001: statement-position bind guard --------------------------------
;;
;; When, in the body of defk/do!/defp/deftest, an expression placed at statement position without being bound
;; evaluates to a Program (DoExpr) / EffectBase, it is a computation that was "created but never runs"
;; and is almost certainly a bug (silent-passthrough). The macro wraps expression-form forms at statement position
;; in _guard-statement-value and makes them fail deterministically on first execution.
;; Auto-bind is not adopted (ADR-DOE-HY-001 R2). The error message is a fix prompt
;; aimed at the writer agent (R3).

(setv _STATEMENT-FORM-HEADS
  #{"setv" "setx" "import" "require" "assert" "del" "raise" "return" "global"
    "nonlocal" "yield" "await" "for" "while" "with" "with/a" "when" "unless"
    "if" "do" "cond" "try" "let" "match" "defn" "defn/a" "defclass" "defmacro"
    "quote" "quasiquote" "unquote" "annotate" "<-" "!" "When" "lazy" "lazy-val"
    "lazy-var" "set!"})

(defn _guard-statement-value [value owner line source]
  "Runtime guard of ADR-DOE-HY-001 R1. If the value evaluated at statement position is a
   Program (DoExpr) / EffectBase, raise RuntimeError. Any other value is
   returned as is (R4: non-Program statements are untouched). It runs for every statement of a defk body, so the name is looked up from the module's import."
  (when (isinstance value #(DoExpr EffectBase))
    (raise (RuntimeError
             (+ owner " (line " (str line) "): statement-position expression evaluated to an "
                "unperformed " (. (type value) __name__) " — a bare Program/EffectBase in "
                "statement position never runs. "
                "Fix: bind it — (<- _ " source ") — or make it the final body expression. "
                "[ADR-DOE-HY-001]"))))
  value)

(defn _doeff-check-program-return [value message mode]
  "Check defp/defpp's return-color contract. Returns False for use in assert."
  (setv is-program (inspect.isgenerator value))
  (when (and (= mode "reject") is-program)
    (raise (TypeError message)))
  (when (and (= mode "require") (not is-program))
    (raise (TypeError message)))
  False)

;; Names in the module's globals through which the binding in a defk body (<- and !) looks up open_bind and Pure — so that `__import__` is not fired on every binding
;; (about 0.2µs each). The values are put into the defk module's globals by _install-guard-globals.
;; Held as name strings, not models — a Symbol is created each time open-form inserts it. If one Symbol were
;; reused across expansions, the position that locate-synthesized attached to it in the first expansion would stay, and the line of every later
;; binding expression would shift to that line.
(setv _HELPERS-NAME "_doeff_outcomes")

(defn _install-guard-globals [wrapped [runtime-globals None]]
  "Install generated-function runtime helpers in the defining module globals.

   Macro expansion can run in a class body, whose namespace is not part of a
   method's LEGB lookup. doeff.do.do uses functools.wraps, so descend through
   __wrapped__ to the generated function and seed its module globals once.
   setdefault preserves an explicit module-level binding."
  (setv target wrapped)
  (while (hasattr target "__wrapped__")
    (setv target (. target __wrapped__)))
  (when (not (hasattr target "__globals__"))
    (raise (TypeError
             "doeff-hy generated callable has no reachable __globals__")))
  (setv globals-dict (. target __globals__))
  (.setdefault globals-dict "_guard_performed" _guard-performed)
  (.setdefault globals-dict "_doeff_effect_base" EffectBase)
  (.setdefault globals-dict "_guard_statement_value" _guard-statement-value)
  (.setdefault globals-dict "_doeff_check_program_return" _doeff-check-program-return)
  ;; The outcomes module that the bindings in a defk body look up by name (_HELPERS-NAME — helpers of _expand-bangs, plan a of #844).
  (.setdefault globals-dict _HELPERS-NAME (importlib.import-module "doeff_core_effects._outcomes"))
  (for [#(name value) (.items (or runtime-globals {}))]
    (.setdefault globals-dict name value))
  wrapped)

(defn _wrap-statement-guard [form owner]
  "Wrap an expression-form form at statement position (function call or symbol) in _guard-statement-value
   and return it. Statement forms (setv/for/with etc., _STATEMENT-FORM-HEADS) and non-expression forms are
   returned as is. owner is the defk/do!/defp/deftest name for the error message."
  (setv head (if (and (isinstance form hy.models.Expression) (> (len form) 0))
                 (str (get form 0))
                 None))
  (setv eligible
    (or (isinstance form hy.models.Symbol)
        (and (is-not head None) (not (in head _STATEMENT-FORM-HEADS)))))
  (if eligible
      (do
        (setv line (or (getattr form "start_line" None) -1))
        (setv source
          (try (.lstrip (hy.repr form) "'")
               (except [Exception] (str form))))
        ;; Expansion for type checking: also emit the value's type with `reveal_type`. pyright picks the deprecated overload (macros.pyi)
        ;; even for a value of unknown type (Unknown), so doeff-hy-check
        ;; drops the red if the type at the same position is Unknown / Any (it does not conclude it is a Program that never runs).
        (if (_static-view?)
            `(_guard-statement-value (reveal_type ~form) ~(str owner) ~line ~source)
            `(_guard-statement-value ~form ~(str owner) ~line ~source)))
      form))


(defclass [(dataclass :frozen True)] _RuntimeDo []
  "The location of the `do` that the runtime expansion looks up (`_runtime-do`) — a module and a name inside that module (both in Python spelling).

   The reason it is not held as a Hy symbol: Hy reads a symbol containing a dot, `doeff.do`, as the expression `(. doeff do)` — if the string passed to `__import__` is built from a
   symbol, the repr of the expression ended up as the module name."
  #^ str module
  #^ str name)

(defn _runtime-do []
  "The location of doeff's `do` that the runtime expansion looks up — the single source of definition for the `do` row of the helper table (`_helper-imports`).

   Every expansion that looks up `do` builds its spelling from here: `_doeff_do` of the helper table (defk, defp, deftest, defmcp-tool), `_doeff_do` of defhandler and handle in handle.hy
   (`_do-import`), the unbound lookup of fnk's name (`_do-reference`), and `_doeff_validation_do` of validate and contract check
   (`_validation-imports`). The alias and the way of looking up differ according to the scope where it is written, but the location is one —
   back when each macro spelled the location, changing the table left only the macros holding a copy wrapping with the old `do` (
   tests/test_helper_imports_table.py). The `do` of the expansion for type checking is held by STATIC_HELPER_IMPORTS in static_view.py."
  (_RuntimeDo "doeff.do" "do"))

(defn _runtime-do-import [alias]
  "The runtime import that binds the `do` of `_runtime-do` to an alias."
  (setv source (_runtime-do))
  `(import ~(hy.read (. source module)) [~(hy.models.Symbol (. source name)) :as ~alias]))

(defn _helper-imports []
  "The imports of the `do` and runtime helpers that a macro's expansion refers to (one point).

   Runtime: doeff's `do` (`_doeff_do` — the location is `_runtime-do`), `_install-guard-globals`, and helpers that the body calls by name (`_guard-performed` etc.).
   The helpers are also put into the function's globals by `_install-guard-globals` (so that they are visible from a method of a
   defk expanded in a class body), so the import doubles as a declaration for pyright
   (types in macros.pyi).
   The expansion for type checking (`_static-view?`) emits nothing — the typed `do`, `_doeff_perform` and the helpers are imported by doeff-hy-check
   once at the top of the module (STATIC_HELPER_IMPORTS in doeff_hy/_static_view.py). If emitted per defk, declarations pile up on one name,
   and in a module exceeding 64, pyright turns `_doeff_do` into Unknown."
  (if (_static-view?)
      '(do)
      `(do
         ~(_runtime-do-import '_doeff_do)
         (import doeff [EffectBase :as _doeff_effect_base])
         (import doeff-hy.macros [_install-guard-globals _guard-performed
                                  _guard-statement-value _doeff-check-program-return]))))

(defn _with-scoped-helpers [form]
  "The expansion of an expression macro (do!) that may also be written inside a function body, importing the helpers before the expression `form`.

   The runtime expansion puts the helper imports and `form` inside an immediately-called function with no arguments. A name imported inside a function becomes
   a local name of the whole function, so importing it directly in a defk body made `_doeff_do` of traverse / for/do read earlier in the same body,
   `_guard_statement_value` of the statement guard, and `_doeff_effect_base` of the last-value guard
   fail with UnboundLocalError (tests/test_fnk_do_bang_scope.py). Inside an immediately-called function the names stay in that
   function, and the outside keeps reading the module-level names. The expansion for type checking emits no import, so it is not wrapped.

   Precondition: do not put user expressions in the part of `form` that runs immediately at evaluation time (put user expressions only in function bodies inside `form`).
   An expression evaluated inside an immediately-called function cannot read names of a class body, and a yield of the outer do context makes that function a generator.
   fnk, which evaluates argument defaults immediately, does not satisfy this precondition, so it is not used (`_do-reference`)."
  (if (_static-view?)
      `(do ~(_helper-imports) ~form)
      `((fn [] ~(_helper-imports) ~form))))

(defn _do-reference []
  "The expression that looks up doeff's `do` when fnk's lambda is called — it binds no name in the scope where it is written.

   fnk's lambda is created where it is written (argument defaults are evaluated in that scope — names of a class body can also be read), so if `do` were
   read by name, that name would be placed in the scope where it is written, and in a defk body it would leave the earlier reads of `_doeff_do` of traverse / for/do
   unbound (see the note of `_with-scoped-helpers`). At runtime it is looked up with `__import__`, the same as the binding (open-form of outcome_forms.hy)
   (about 0.3µs each — within the about 7µs of assembling `@do` on every call). The location is `_runtime-do`. The expansion for type checking uses
   `_doeff_do` at the top of the module."
  (if (_static-view?)
      '_doeff_do
      (let [source (_runtime-do)]
        `(. (__import__ ~(. source module) :fromlist #(~(. source name)))
            ~(hy.models.Symbol (. source name))))))

(defn _scoped-definition [compiler name forms [module-forms #()] [nested-reads False]]
  "Assemble, to fit the place where it is written, the sequence of statements `forms` (including helper imports) of the expansion of a macro that defines the name `name` (defk, deff, defp, deftest, defeffect, defmcp-tool, defhandler of handle.hy, defrecord and defwire of record.hy, and definition macros of the owning package — defsystem of doeff-cluster (doeff_cluster/_shared/entry/macros.hy)).
   `module-forms` are statements, assumed to run only once at module load, that tell the module about the definition (the recording of deftest's pytest item).
   In module and class bodies they are placed after `forms`, and inside a function body they are emitted in neither the runtime expansion nor the expansion for type checking —
   a definition inside a function is not a module-level definition, and would pile up on every call of the outer function.
   Only this function holds the decision of where it runs once; callers just pass the statements.

   In module and class bodies the statements are lined up as before — imported names become module globals or class attributes and do not break
   earlier reads. Inside a function body (nested definition), the sequence of statements is put into an immediately-called function with no arguments, and only the defined value is
   bound to `name`. A name imported inside a function becomes a local name of the whole function, so leaving them lined up made `_doeff_do` of traverse / for/do read earlier in the outer function,
   `_guard_statement_value` of the statement guard, and `_doeff_effect_base` of the last-value guard
   fail with UnboundLocalError (tests/test_nested_definition_scope.py). Module and class bodies are
   not wrapped because wrapping would change the definition's `__qualname__` (pickle could not look it up by name) and the argument-less
   `super()` of a method in a class body would lose `__class__`. The expansion for type checking imports no helpers, so it is not wrapped.

   What the caller (the definition macro) owes to `forms` — since this function decides whether to wrap, make it a form that holds in any place:
   - Inside a function `forms` are evaluated on every call of the outer function. Make `forms` only define and bind `name`, and place no statement that changes any module
     state (neither the globals of the module where the definition is written, nor doeff's module index or registry) (it would pile up on every call —
     test_definition_sample_leaves_module_state_unchanged of tests/test_nested_definition_scope.py, blind B). Pass statements that tell the module
     once to `module-forms`.
   - In a class body, the imports of `forms` become class attributes and are not visible from functions defined in `forms` or from nested class bodies
     (name lookup skips the class level). Helpers that the definition reads inside a function are put into the function's globals with `_install-guard-globals`
     (defk, deftest and defmcp-tool, whose definition function itself reads the helpers — the same test_class_body_definition_runs_without_module_definitions),
     or imported inside the function that builds the function and read from the closure
     (defhandler — `_in-class-body?`, test_class_body_handle_clauses_read_their_operations of tests/test_handle_scope.py). Definitions that build a class (defrecord, defwire, defeffect) have
     decorators, annotations and methods in that class body that read the names of the expansion's imports (`doeff_hy`, `_doeff_ClassVar` and others), which a closure cannot reach — call with `nested-reads`
     true. In a class body, the names bound by the imports of `forms` are also placed in the module's globals before the definition (`_publish-imports`), so they
     are reached even if the module has no other definition at module level (test_class_body_type_definition_runs_without_module_doeff_hy of
     tests/test_nested_definition_scope.py). defhandler is decided not to change the module's globals, so it does not make
     `nested-reads` true (test_class_body_handle_leaves_module_globals_alone).
   - The expansion for type checking imports no helpers (use the empty expansion of `_helper-imports`). It does not wrap here, so the imports
     become local names of the outer function or class body — the post-processing of the type-check expansion moves them to module level (with_module_level_imports of
     doeff_hy/_static_check.py — the doeff_hy module, imports of `_doeff_` aliases, and the import of the module that splits defeffect's answer)."
  (cond
    (_in-function-body? compiler) `(setv ~name ((fn [] ~@forms ~name)))
    (_function-scope? compiler) `(do ~@forms)
    (and nested-reads (_in-class-body? compiler)) `(do ~@(_publish-imports forms) ~@forms ~@module-forms)
    True `(do ~@forms ~@module-forms)))

(defn _imports-of [forms]
  "The `(import …)` forms in the sequence of statements `forms` of an expansion (also reads inside `(do …)` — imports inside function, class and conditional bodies
   belong to that body and do not change the scope where they are placed, so they are not read). The reader is `_publish-imports`."
  (sum (lfor form forms
             (cond
               (not (and (isinstance form hy.models.Expression) form (isinstance (get form 0) hy.models.Symbol))) []
               (= (str (get form 0)) "import") [form]
               (= (str (get form 0)) "do") (_imports-of (cut form 1 None))
               True []))
       []))

(defn _publish-names [module-globals bound]
  "Put the names imported by the expansion of a class body (bound — a dict of name to value) into the module's globals. If the writer has
   put the same name at module level, keep it (`setdefault`). What runs at runtime is only the statement that `_publish-imports` emits."
  (for [#(name value) (.items bound)]
    (.setdefault module-globals name value)))

(defn _publish-imports [forms]
  "In the expansion of a class body, the sequence of statements that also places the names bound by the imports of `forms` into the module's globals (empty if there is no import).

   A name imported in a class body becomes a class attribute, and bodies of nested classes and methods skip the outer class level when looking up names
   (the same as name lookup inside a function) — `doeff_hy` and `_doeff_ClassVar`, read by the decorators, annotations and methods of the body of the defined type,
   failed with NameError (in a module with `doeff_hy` at module level, the imports of other definitions hid it). Make them readable from the module's globals, the same as
   when a definition is placed at module level. The imports of the class body stay as before — expressions
   evaluated in a class body (decorators, bases, defaults) read their own imports before the same names at the writer's module level.

   The spelling of names is not read: run the import inside a function with no arguments (and delete it after the call without leaving it as a class attribute), and place its local names (only the names
   bound by the import) with `_publish-names`. The helper is looked up with `__import__` — looking up by name would mix that name's import into the local names. The statement is emitted before the definition
   (the body of a nested type reads names at definition time)."
  (setv imports (_imports-of forms))
  (if imports
      (let [publish (hy.gensym "publish")]
        [`(defn ~publish []
            ~@imports
            ((. (__import__ "doeff_hy.macros" :fromlist #("_publish_names")) _publish-names) (globals) (locals)))
         `(~publish)
         `(del ~publish)])
      []))

(defn _function-scope? [compiler]
  "Whether a macro was written inside a function body (the same answer for the runtime expansion and the expansion for type checking).

   True if the nearest Python scope of the Hy compiler's scope at expansion time, skipping let and comprehensions (nearest-python-scope of `hy.scoping`),
   is a function. False for module and class bodies. Readers are `_in-function-body?` and `_scoped-definition`, which emits no
   `module-forms` inside a function."
  (is-function-scope (nearest-python-scope (. compiler scope))))

(defn _in-function-body? [compiler]
  "In the runtime expansion, whether a macro was written inside a function body — a place where importing helpers where it is written creates local names of the outer function.

   True when `_function-scope?` is true and this is not the expansion for type checking. False for module and class bodies (imported names become the module's
   globals or class attributes and do not break earlier reads). False for the expansion for type checking (not wrapped — see the note of `_scoped-definition`).
   Readers are `_scoped-definition` and handle of handle.hy."
  (and (not (_static-view?))
       (_function-scope? compiler)))

(defn _in-class-body? [compiler]
  "In the runtime expansion, whether a macro was written directly in a class body — a place where names imported where it is written become class attributes and are not
   visible from the functions created in the expansion (name lookup inside a function skips the class level).

   True if the nearest Python scope of the Hy compiler's scope at expansion time, skipping let and comprehensions, is a class (a ScopeFn
   that is not a function, created by defclass). False for the expansion for type checking (the expansion does not change — the same as `_in-function-body?`). Readers are handle of handle.hy
   and defhandler."
  (setv scope (nearest-python-scope (. compiler scope)))
  (and (not (_static-view?))
       (isinstance scope ScopeFn)
       (not (is-function-scope scope))))

(defn _quoted-forms [forms]
  "The body expression placed in `__doeff_body__`: a `QuotedForms` holding the body as a single string of `hy.repr` (turned back into models when read).
   If the body were embedded with quote, Hy would compile it into code that builds the model tree on every import, and half of the compile time without a .pyc
   went there (measured 2026-09-28 — the note at the top of doeff_hy/_quoted_forms.py)."
  `(do
     (import doeff-hy._quoted-forms [QuotedForms :as _doeff-QuotedForms])
     (_doeff-QuotedForms ~(hy.models.String (hy.repr (hy.models.List forms))))))

(defn _result-symbol [origin]
  "`_contract_result`, which points at the result of the body. The position is taken from the last expression (so that the red of `return` and of the return type
   points at the last expression, not at the head of the defk)."
  (.replace (hy.models.Symbol "_contract_result") origin))

(defn _result-binding [last-form return-source]
  "`(setv _contract_result last-expression)`. If the contract has a return type, annotate the variable (for do! and defp, whose exit is a single last expression)."
  (if (is return-source None)
      `(setv ~(_result-symbol last-form) ~last-form)
      `(setv (annotate ~(_result-symbol last-form) ~(hy.models.String return-source))
             ~last-form)))

(defn _result-declaration [last-form return-source]
  "The sequence of statements that declares `_contract_result: T` without a value (empty if there is no T). Placed at the head of a defk / deff function with :post (right after the :pre checks, before the body),
   and the intermediate exits (`_route-returns`) and the assignment of the last expression carry no annotation (`_exit-binding`) — the value of every exit is matched against the declared T,
   and even if a value with a hole in its type (such as `tuple[Unknown, ...]`) is assigned, the red of the hole is only at the one assignment (the writer's return line) and does not propagate to the later guard, `%` and
   return. If the annotation were attached only to the assignment of the last expression, then with a body ending in the form `while True` the annotation would be placed where it is unreachable, and the type hole of the value of an intermediate
   exit propagated to the guard's argument, `_hy_let_%` and the copy into return, adding 3 reds per return (
   parent #3167). When the value's type is entirely Unknown, pyright narrows the variable that has a declaration to that Unknown, so it propagates (the same as the exit of the last expression).
   The position is taken from the last expression (the red of an error in T points at the same line as before). Annotations of local variables are not evaluated at runtime."
  (if (is return-source None)
      []
      [`(annotate ~(_result-symbol last-form) ~(hy.models.String return-source))]))

(defn _exit-binding [value origin]
  "The statement `(setv #(_contract_result) #(value))` that puts the value of an exit (an intermediate `(return x)` and the last expression) into `_contract_result`. No annotation is attached
   (the type is the declaration at the head of the function, `_result-declaration`). It is written as an assignment of a one-element tuple because Hy's `(setv name value)` moves the temporary name of the value (`_hy_anon_N` when match, if or
   cond is used as a value) to the assignment target — match puts `temporary = None` before the cases, so moving it would match
   `_contract_result = None` against the declared T, giving a red the writer cannot fix even for an exhaustive match (
   measured). No move happens for a tuple assignment target, and the temporary name goes in with the type narrowed by match exhaustiveness. CPython compiles an assignment that builds a one-element tuple and
   immediately unpacks it to the same bytecode as a plain assignment, so no runtime cost is added. The position is taken from origin."
  `(setv #(~(_result-symbol origin)) #(~value)))

(defn _writes-yield? [form]
  "Whether a yield / yield-from expression exists anywhere in form (nested fn is also counted — a judgment that errs on the side of removing the annotation)."
  (match form
    (hy.models.Expression)
      (or (and (> (len form) 0) (isinstance (get form 0) hy.models.Symbol)
               (in (str (get form 0)) #{"yield" "yield-from"}))
          (any (gfor item form (_writes-yield? item))))
    (| (hy.models.List) (hy.models.Tuple) (hy.models.Set) (hy.models.Dict))
      (any (gfor item form (_writes-yield? item)))
    _ False))

(defn _annotates-return? [kleisli? return-source body]
  "Whether to write T of the contract's `(: % T)` in the function head (the return annotation).

   - deff (ordinary function): write it if the contract has a type (at runtime and in the type check, as before).
   - defk: write it only in the expansion for type checking (`_static-view?`). In that expansion `<-` and `(! …)` become `_doeff_perform`
     and the body has no yield, so the function is an ordinary function returning the answer T, and the type of `do` (static_types.pyi) makes it
     `Expand[T, Never]`. Without the annotation, for a defk that calls itself pyright cannot infer the answer type (the self call during inference
     is Unknown), and the name bound to the answer of the recursive call and the return value turned into strict reds for Unknown.
     A defk whose writer wrote yield directly in the body is a generator, so it does not write it (T does not become the return annotation of a generator).
     The runtime expansion does not write it, as before — a runtime defk is a generator, and an annotation of T would disagree."
  (cond
    (is return-source None) False
    (not kleisli?) True
    True (and (_static-view?) (not (any (gfor form body (_writes-yield? form)))))))

;; Forms that make an exit of another function or class inside the body, and forms that are not evaluated (a return inside them is not an exit of the outer definition).
(setv _NESTED-EXIT-HEADS
  #{"fn" "fn/a" "fnk" "defn" "defn/a" "defk" "deff" "defp" "defpp" "defclass" "defmacro" "defhandler" "deftest"
    "quote" "quasiquote"})

(defn _route-returns [form exit-forms]
  "Rewrite `(return x)` inside the body forms to the same exit as the last expression — put into `_contract_result`, passed through the guard and the :post checks,
   and then returned (an intermediate return used to leave the function before the checks). `exit-forms` is the sequence of statements placed after the result is put into
   `_contract_result`. Definitions of other functions and classes and the inside of quote are not rewritten (their return is not an exit of the outer definition)."
  (cond
    (and (isinstance form hy.models.Expression) (> (len form) 0)
         (isinstance (get form 0) hy.models.Symbol) (= (str (get form 0)) "return"))
      (.replace `(do ~(_exit-binding (if (> (len form) 1) (_route-returns (get form 1) exit-forms) 'None) form)
                     ~@exit-forms
                     (return _contract_result))
                form)
    (and (isinstance form hy.models.Expression) (> (len form) 0)
         (isinstance (get form 0) hy.models.Symbol) (in (str (get form 0)) _NESTED-EXIT-HEADS))
      form
    (isinstance form (tuple [hy.models.Expression hy.models.List hy.models.Set hy.models.Dict hy.models.Tuple]))
      (.replace ((type form) (lfor f form (_route-returns f exit-forms))) form)
    True form))

(defn _build-fn-with-contracts [decorators name params pre-checks post-checks real-body [type-params #()]]
  "Build a defn form with pre/post assertion wrappers.
   Works for both plain functions (deff) and generator/kleisli functions (defk).
   Kleisli functions (decorator `_doeff_do`) additionally guard against a bare
   unperformed effect as the last form (see `_guard-performed`).
   A leading docstring is emitted BEFORE the pre-condition asserts so it stays
   the first statement and becomes the function's __doc__ (a lone string body
   is left in place — it is the return value, not a docstring).
   type-params = the type arguments of the contract's :tp (_type-params) — in the type-check expansion they are placed in the type parameters of PEP 695 `def f[T]`, and annotations read T
   as is. In runtime checks (isinstance) they are erased to object (_expand-check)."
  (setv docstring-forms [])
  (when (and (> (len real-body) 1)
             (isinstance (get real-body 0) hy.models.String))
    (setv docstring-forms [(get real-body 0)])
    (setv real-body (cut real-body 1 None)))
  (setv kleisli? (any (gfor d decorators (= (str d) "_doeff_do"))))
  (setv pre-code (_contract-code pre-checks name "pre-condition" kleisli? type-params))
  ;; Type arguments go only in the type-check expansion — the runtime expansion emits no PEP 695 spelling (so the definition can be read even in a requires-python 3.10 environment).
  (setv type-param-head (if (and type-params (_static-view?))
                            [(hy.models.Keyword "tp") (hy.models.List type-params)]
                            []))
  ;; Contract types go into annotations (arguments, and the return value of deff). defk is a generator, so the return value itself
  ;; is not annotated; instead, the local variable `_contract_result` that holds the result is declared as T at the head of the function (`_result-declaration`)
  ;; — without the macro judging whether it is a generator (whether the body has yield), the types of intermediate exits and the last expression can be
  ;; matched against T. Annotations of local variables are not evaluated at runtime.
  ;; A defk in the type-check expansion is not a generator, so T is also annotated on the return value (`_annotates-return?`).
  (setv params (_annotate-params params (_contract-types pre-checks))
        return-source (.get (_contract-types post-checks) "%"))
  (setv head (if (_annotates-return? kleisli? return-source real-body)
                 `(annotate ~name ~(hy.models.String return-source))
                 name))
  (setv guard-stmt
    (if kleisli?
        (_performed-guard-form (str name))
        `(do)))
  (if post-checks
      (let [post-asserts (_contract-code post-checks name "post-condition" kleisli? type-params)
            ;; An intermediate return also goes through the same exit as the last expression (guard → :post checks).
            exit-forms [guard-stmt `(let [% _contract_result] ~@post-asserts)]
            routed (lfor f real-body (_route-returns f exit-forms))
            ;; ADR-DOE-HY-001: guard statement positions of the kleisli body (the final expression = return value is excluded)
            init-forms (if kleisli?
                           (lfor f (cut routed 0 -1) (_wrap-statement-guard f name))
                           (list (cut routed 0 -1)))
            last-form (get routed -1)]
        `(defn ~decorators ~@type-param-head ~head ~params
           ~@docstring-forms
           ~@pre-code
           ~@(_result-declaration last-form return-source)
           ~@init-forms
           ~(_exit-binding last-form last-form)
           ~guard-stmt
           (let [% _contract_result]
             ~@post-asserts)
           ~(_result-symbol last-form)))
      `(defn ~decorators ~@type-param-head ~head ~params
         ~@docstring-forms
         ~@pre-code
         ~@real-body)))


;; ---------------------------------------------------------------------------
;; val / var / lazy val / lazy var / session val / session var and := (ADR-DOE-HY-006)
;; ---------------------------------------------------------------------------
;;
;; The single definition point of body rewriting is rewrite-body in doeff_hy/_binding_forms.py. The clauses of defk, deftest and defhandler
;; (handle.hy) go through it. Errors that stop the expansion are SyntaxError; findings that do not stop it (use of setv, rebinding,
;; old lazy) are collected by doeff-hy-check (static_view.report-findings).

(import doeff-hy._binding-forms [rewrite-body :as _rewrite-body
                                BodyKind :as _BodyKind
                                ModuleBindings :as _ModuleBindings
                                ModuleNames :as _ModuleNames
                                module-declaration :as _module-declaration])
;; Fix the names of the fields of a match class pattern to Python attribute names (a workaround for a defect in Hy 1.3.1; used by defk)
(import doeff-hy._match-fields [mangle-match-fields :as _mangle-match-fields])
(import doeff-hy._static-view [report-findings :as _report-findings])
;; The entry point for recording pytest items (the definition source of the record shape is pytest_items.py)
(import doeff-hy._pytest-items [record-function :as _record-test-function
                               record-module-binding :as _record-module-binding])
(import hy.scoping [ScopeGlobal])

(defn _module-bindings [compiler]
  "The list of names of module-level val / var / lazy val, alive only while this file compiles (attached to the Hy compiler).
   For an expansion with no compiler (hy.macroexpand etc.), an empty list for that occasion only."
  (if (is compiler None)
      (_ModuleBindings)
      (do
        (setv found (getattr compiler "_doeff_module_bindings" None))
        (when (is found None)
          (setv found (_ModuleBindings))
          (setattr compiler "_doeff_module_bindings" found))
        found)))

(defn _module-names [compiler]
  "The names of lazy val / var of the module declared earlier in the same file, passed to the bodies of defk, deftest and defhandler."
  (if (is compiler None) (_ModuleNames) (.names (_module-bindings compiler))))

(defn _rewrite-bindings [forms owner kind params [sessions #()] [module None]]
  "Rewrite declarations, := and lazy references in the body, pass the findings to doeff-hy-check, and return the rewritten body.
   A helper called at expansion time, so it is a defn (it cannot be a defk)."
  (setv rewritten (_rewrite-body forms
                                :owner owner
                                :kind kind
                                :params params
                                :sessions sessions
                                :module (if (is module None) (_ModuleNames) module)
                                :static (_static-view?)
                                :expand-bangs _expand-bangs))
  (_report-findings (. rewritten findings))
  (list (. rewritten forms)))

(defn _module-level [compiler head args]
  "Expand module-level (val …) / (var …) / (lazy val …) (lazy var / session are errors with guidance). Declarations inside the bodies of defk, deftest and defhandler
   are rewritten first by the body's macro, so what arrives here is the module level, or the inside of
   defn / fn / class where rewriting does not reach (an error)."
  (setv form (if (is compiler None)
                 (hy.models.Expression [(hy.models.Symbol head) #* args])
                 (. compiler this)))
  (when (and (is-not compiler None) (not (isinstance (. compiler scope) ScopeGlobal)))
    (raise (SyntaxError (+ "\n(" head " …) (line " (str (getattr form "start_line" "?")) "): "
                           "can only be written in the clause body of defk, deftest or defhandler, or at module level"
                           " (not inside defn, fn, class or let — write functions with defk)."
                           " [ADR-DOE-HY-006]\n"))))
  (setv declaration (_module-declaration (_module-bindings compiler) form (_static-view?)))
  ;; Put a module-level pytestmark in the record of pytest items (#1291 — add the record expression to the expansion)
  (setv item-record (when (is-not compiler None) (_record-module-binding head args)))
  (if (is item-record None)
      declaration
      `(do ~declaration ~item-record)))

(defmacro val [#^ HyASTCompiler _hy-compiler #^ Object #* args]
  "A module-level one-time binding (val name expr). Inside the bodies of defk, deftest and defhandler clauses, the body's macro handles it."
  (_module-level _hy-compiler "val" args))

(defmacro var [#^ HyASTCompiler _hy-compiler #^ Object #* args]
  "A module-level rebindable binding (var name expr). A module-level rebinding is setv (in Hy, := does not go through the macro)."
  (_module-level _hy-compiler "var" args))

(defmacro lazy [#^ HyASTCompiler _hy-compiler #^ Object #* args]
  "A module-level (lazy val name expr) — only for expressions that use no effects. Evaluated once on first use and remembered."
  (_module-level _hy-compiler "lazy" args))

(defmacro session [#^ HyASTCompiler _hy-compiler #^ Object #* args]
  "(session val …) / (session var …) can be written only directly under defhandler (if it arrives here, it is an error with guidance)."
  (_module-level _hy-compiler "session" args))


;; ---------------------------------------------------------------------------
;; defeffect — an effect type (frozen dataclass on EffectBase) with its answer type and tags
;; ---------------------------------------------------------------------------

(defmacro defeffect [#^ HyASTCompiler _hy-compiler #^ Object name #^ Object #* rest]
  "Define an effect type: a frozen dataclass inheriting EffectBase.

   (defeffect BorrowToken
     \"Borrow an access token from the custody service.\"
     {:fields [(: profile str) (: purpose str)]
      :answer (| Lease Refused)
      :tags   {:context \"agent-task\" :role \"intent\"}})

   :answer (the type a handler resumes with) and :tags are required; :fields may be omitted.
   The class carries __doeff_answer__, __doeff_tags__ and __doeff_defeffect__ = True.

   The answer's members may be split by meaning (ADR-DOE-CORE-EFFECTS-003 R5 — all optional):

   (defeffect ReadRow
     {:fields [(: table str) (: key tuple)]
      :answer (| Row Missing Unreachable)
      :absent [Missing]          ; <- performs Absent instead of binding it
      :failure [Unreachable]     ; <- performs Raise(answer) instead of binding it
      :tags {:context \"records\" :role \"intent\"}})

   :value [Conflict] marks an answer handled as an ordinary value (not a failure); members
   in none of the lists are the success. Writing any of them sets __doeff_outcomes__
   (doeff_core_effects._outcomes.Outcomes); an effect without them binds its answer as-is.

   :runs-carried [program] names the fields holding a Program the handler runs where the
   effect was performed (under the handlers there — SqlTransaction's program). It sets
   __doeff_runs_carried__ (read by doeff_core_effects._effects.runs_carried_of; a closure
   check reads those Programs as run at the performing site).

   :tp [T] names the type parameters the answer and the fields read (the same form as defk / deff's :tp). The type-check
   expansion writes `class SqlTransaction[T](…)` (PEP 695), so a field `(: program (of Program T object))` and
   :answer (| T SqlFailed SqlUnreachable) are generic in T; the runtime expansion writes no type parameter and reads T from
   the module (`(val T (TypeVar \"T\"))`).

   When written inside a function body, put the helper imports (_doeff-dataclass, _doeff-effect-base, etc.) and the class into an immediately-called function and
   bind only <name> (_scoped-definition)."
  (setv docstring None contract None forms (list rest))
  (when (and forms (isinstance (get forms 0) hy.models.String))
    (setv docstring (get forms 0) forms (cut forms 1 None)))
  (when (and forms (isinstance (get forms 0) hy.models.Dict))
    (setv contract (get forms 0) forms (cut forms 1 None)))
  (when (or (is contract None) forms)
    (raise (SyntaxError (.format "defeffect {}: write it in the form name, (docstring), leading dict {{:fields [...] :answer type :tags {{...}}}}" (str name)))))
  (setv where (+ "defeffect " (str name))
        pre (_declared-value contract ":pre"))
  (when (and (is-not pre None) (not (isinstance pre hy.models.List)))
    (raise (SyntaxError (.format "{}: :pre must be a list of conditions: {}" where (hy.repr pre)))))
  ;; The (: name type) of a type can point only at fields (the return value % does not exist for an effect).
  (setv field-names (lfor item (or (_declared-value contract ":fields") [])
                          :if (and (isinstance item hy.models.Expression) (> (len item) 1))
                          (str (get item 1))))
  (for [check (or pre [])]
    (when (and (_is-type-check check) (not-in (str (get check 1)) field-names))
      (raise (SyntaxError (.format "{}: {} in :pre is not a field (fields = {})" where (hy.repr check) (.join " " field-names))))))
  (_locate-synthesized (_scoped-definition _hy-compiler name
    [(_defeffect-form name docstring contract where (_contract-code pre (str name) ":pre" False))]
    :nested-reads True)))


;; ---------------------------------------------------------------------------
;; deff — defn with :pre/:post contracts
;; ---------------------------------------------------------------------------

(defmacro deff [#^ HyASTCompiler _hy-compiler #^ Object name #^ Object params #^ Object #* body]
  "Define a function with :pre/:post contracts.

   (deff my-fn [x y]
     {:pre [(: x int) (: y str)]
      :post [(: % list)]}
     (list (range x)))

   :pre and :post are REQUIRED.
   (: name Type) is shorthand for (isinstance name Type).
   Arbitrary validation expressions are also allowed in the same list.

   When written inside a function body, put the declaration import (doeff_hy) and the definition into an immediately-called function and bind only name
   (_scoped-definition)。"
  (_warn-defk-in-hyp _hy-compiler "deff" name)
  (setv #(pre-checks post-checks real-body) (_extract-contracts body _FUNCTION-CONTRACT-KEYS (+ "deff " (str name))))
  (when (is pre-checks None)
    (raise (SyntaxError (.format "
deff {name}: {{:pre [...]}} is required.

  Correct usage:

    (deff {name} [x y]
      {{:pre [(: x int) (: y str)]          ;; type checks
             (> x 0)]                        ;; arbitrary validation
       :post [(: % list)]}}                  ;; return type
      (list (range x)))

  (: name Type) expands to (isinstance name Type).
  % binds to return value in :post checks.
" :name name))))
  (when (is post-checks None)
    (raise (SyntaxError (.format "
deff {name}: {{:post [...]}} is required.

  Correct usage:

    (deff {name} [x y]
      {{:pre [(: x int) (: y str)]
       :post [(: % list)]}}                  ;; return type
      (list (range x)))

  (: name Type) expands to (isinstance name Type).
  % binds to return value in :post checks.
" :name name))))
  (_validate-pre-type-checks name params pre-checks)
  (_validate-post-type-check name post-checks)
  (_locate-synthesized (_scoped-definition _hy-compiler name [
    (_build-fn-with-contracts [] name params pre-checks post-checks real-body (_type-params body (+ "deff " (str name))))
    #* (_declaration-setters name (get (_find-contract body) 0) (+ "deff " (str name)))])))


;; ---------------------------------------------------------------------------
;; defk — kleisli with :pre/:post contracts + bang expansion
;; ---------------------------------------------------------------------------

(defmacro defk [#^ HyASTCompiler _hy-compiler #^ Object name #^ Object params #^ Object #* body]
  "Define a kleisli function (@do decorator) with :pre/:post contracts.
   Supports ! (bang) inline bind: (! expr) is rewritten IN PLACE to
   (yield expr), preserving the written evaluation position (conditionality,
   short-circuit, order, exception context). [ADR-DOE-HY-003]
   No extra imports needed — macro injects its own runtime deps.

   (defk my-fn [x y]
     {:pre [(: x Asset)]
      :post [(: % SegmentSelection)]}
     (<- result (some-effect))
     (return result))

   :pre and :post are REQUIRED. (: name Type) is shorthand for (isinstance name Type).
   Arbitrary validation expressions are also allowed in the same list.

   With bang:
   (defk my-fn [x y]
     {:pre [(: x int) (: y int)]
      :post [(: % int)]}
     (k1 (! (k2 x)) (! (k3 y))))

   Field names in a match class pattern can be written with `-` as in the Hy spelling — defk fixes them to attribute names
   (Hy 1.3.1's match does not fix them, so (Rec :ended-reason None) outside defk never matches — doeff_hy/_match_fields.py):
   (match write (RecordWrite :ended-reason None :usage None) \"open\" _ \"other\")"
  ;; Warn if defk is used in .hyp file
  (_warn-defk-in-hyp _hy-compiler "defk" name)
  ;; Reject handler-like signatures early — these should use defhandler
  (_reject-handler-signature name params)
  ;; Fix the keyword field names of match class patterns in the body, contracts and arguments to attribute names with hy.mangle — Hy 1.3.1's match
  ;; emits field names to case without mangling, so the clause (Rec :ended-reason None) never matches and silently falls through to the next clause
  ;; (Hy does not fix this = ADR-DOE-HY-008). A match outside defk is stopped by DOEFF169 of doeff-linter
  ;; (the rule does not apply inside a defk form). The body is kept as written in __doeff_body__ (written-forms).
  (setv written-forms body
        body (lfor form body (_mangle-match-fields form))
        params (_mangle-match-fields params))
  (setv #(pre-checks post-checks real-body) (_extract-contracts body _FUNCTION-CONTRACT-KEYS (+ "defk " (str name))))
  (when (is pre-checks None)
    (raise (SyntaxError (.format "
defk {name}: {{:pre [...]}} is required.

  Correct usage:

    (defk {name} [x y]
      {{:pre [(: x Asset) (: y str)]        ;; type checks
             (> (len y) 0)]                  ;; arbitrary validation
       :post [(: % Result)]}}               ;; return type
      (<- result (some-effect x))
      result)

  (: name Type) expands to (isinstance name Type).
  % binds to return value in :post checks.
" :name name))))
  (when (is post-checks None)
    (raise (SyntaxError (.format "
defk {name}: {{:post [...]}} is required.

  Correct usage:

    (defk {name} [x y]
      {{:pre [(: x Asset) (: y str)]
       :post [(: % Result)]}}               ;; return type
      (<- result (some-effect x))
      result)

  (: name Type) expands to (isinstance name Type).
  % binds to return value in :post checks.
" :name name))))
  ;; Validate type coverage: every param needs (: param Type), post needs (: % Type)
  (_validate-pre-type-checks name params pre-checks)
  (_validate-post-type-check name post-checks)
  ;; Rewriting of val / var / lazy val / lazy var / :=, and rejection of old lazy / lazy-val / lazy-var / set!
  ;; (ADR-DOE-HY-006). The body is kept as written in __doeff_body__.
  (setv written-body (get (_find-contract written-forms) 1))
  ;; A name that an earlier <- bound with a plain-value type (such as a FileFailed), written in the value position of a later bind, makes that branch yield
  ;; a value that is neither an effect nor a Program (TypeError at runtime) — refuse it here, where the bound types are known.
  (import doeff-hy._outcome-forms [bound-value-refusal])
  (setv refused (bound-value-refusal real-body (_extract-param-names params) (+ "defk " (str name))))
  (when (is-not refused None)
    (raise (SyntaxError refused)))
  (setv real-body (_rewrite-bindings real-body (+ "defk " (str name)) _BodyKind.DEFK
                                     (_extract-param-names params)
                                     :module (_module-names _hy-compiler)))
  ;; Expand bangs in the real body — in-place (yield ...) rewrite [ADR-DOE-HY-003]
  (setv expanded-forms
    (lfor form real-body (_expand-bangs form (+ "defk " (str name)) _HELPERS-NAME)))
  (setv fn-form (_build-fn-with-contracts ['_doeff_do] name params pre-checks post-checks expanded-forms
                                          (_type-params body (+ "defk " (str name)))))
  (_locate-synthesized (_scoped-definition _hy-compiler name [
     (_helper-imports)
     fn-form
     `(_install-guard-globals ~name)
     `(setattr ~name "__doeff_body__" ~(_quoted-forms written-body))
     `(setattr ~name "__doeff_args__" ~(_quoted-forms params))
     `(setattr ~name "__doeff_name__" ~(str name))
     #* (_declaration-setters name (get (_find-contract body) 0) (+ "defk " (str name)))])))


;; ---------------------------------------------------------------------------
;; fnk — anonymous kleisli (lambda that returns DoExpr)
;; ---------------------------------------------------------------------------

(defmacro fnk [#^ Object params #^ Object #* body]
  "Anonymous kleisli function. Like fn but returns a DoExpr.
   Supports <- (effect bind) and ! (bang) inline bind.
   No extra imports needed — macro injects its own runtime deps.

   (fnk [x] (* x 2))

   With effects:
   (fnk [acc x]
     (<- enriched (enrich x))
     (+ acc enriched))

   With bang:
   (fnk [x y] (+ (! (k1 x)) (! (k2 y))))"
  ;; Expand bangs in the body — in-place (yield ...) rewrite [ADR-DOE-HY-003]
  (setv expanded-forms (lfor form body (_expand-bangs form "fnk")))
  ;; The lambda is created where it is written (defaults are evaluated in that scope), and `do` is looked up without binding a name (`_do-reference`).
  ;; The expansion for type checking has the same form as before (`_helper-imports` emits nothing).
  (setv kleisli `(fn [~@params] ((~(_do-reference) (fn [] (do ~@expanded-forms))))))
  (_locate-synthesized
    (if (_static-view?)
        `(do ~(_helper-imports) ~kleisli)
        kleisli)))


;; ---------------------------------------------------------------------------
;; do! — effectful let (returns a Program)
;; ---------------------------------------------------------------------------

(defmacro do! [#^ Object #* forms]
  "Effectful let — returns a Program (generator) that sequences effect bindings.

   (do!
     (<- x (some-effect))
     (<- y (another-effect x))
     (+ x y))

   Returns a Program object. Caller decides how to use it:
     (<- result (do! ...))   ; yield it inside a defk/deff
     (setv p (do! ...))      ; store as a Program value

   Supports ! (bang) inline bind:
     (do! (f (! (g x)) (! (h y))))
   rewrites each (! expr) in place to (yield expr). [ADR-DOE-HY-003]

   Supports :pre/:post contracts with (: name Type) shorthand:

   (do!
     {:pre [(: url str)]
      :post [(: % dict)]}
     (<- resp (http-get url))
     (.json resp))"
  (import doeff-hy._outcome-forms [bind-form :as _bind-form])
  (setv #(pre-checks post-checks real-forms) (_extract-contracts forms #(":pre" ":post") "do!"))
  ;; Expand bangs in each form — in-place (yield ...) rewrite [ADR-DOE-HY-003]
  (setv expanded-forms (lfor form real-forms (_expand-bangs form "do!")))
  (setv #(bindings body-expr) (_parse-do-body expanded-forms "do!"))
  (setv pre-code (_contract-code pre-checks "do!" "pre-condition" True)
        expanded (lfor bind bindings
                   (if (and (isinstance bind tuple) (= (get bind 0) "__plain__"))
                       ;; Plain statement — ADR-DOE-HY-001 guard (wrap expression forms only)
                       (_wrap-statement-guard (get bind 1) "do!")
                       ;; Effect binding — yield (typed bind keeps its isinstance)
                       (_bind-form bind))))
  (_locate-synthesized (_with-scoped-helpers (if post-checks
      (let [post-asserts (_contract-code post-checks "do!" "post-condition" True)]
        `((_install-guard-globals
            (_doeff-do (fn []
              ~@pre-code
              ~@expanded
              ~(_result-binding body-expr (.get (_contract-types post-checks) "%"))
              ~(_performed-guard-form "do!")
              (let [% _contract_result]
                ~@post-asserts)
              (return ~(_result-symbol body-expr)))))))
      `((_install-guard-globals
          (_doeff-do (fn []
            ~@pre-code
            ~@expanded
            ~(_result-binding body-expr None)
            ~(_performed-guard-form "do!")
            (return ~(_result-symbol body-expr))))))))))


;; ---------------------------------------------------------------------------
;; <- — perform effect with optional type contract
;; ---------------------------------------------------------------------------

(defmacro <- [#^ Object #* args]
  "Perform an effect. Bind result if name given, optional type contract.
   (<- x (Ask \"key\"))              → (setv x (yield (Ask \"key\")))
   (<- x Type (Ask \"key\"))         → bind + isinstance assertion
   (<- (slog ...))                   → (yield (slog ...))
   The expansion is `_bind-yield` — the same definition point the body
   expanders (do! / defp / deftest / for/do / defhandler) use, so a typed bind
   carries the isinstance guarantee wherever it is written."
  (import doeff-hy._outcome-forms [bind-form :as _bind-form])
  (_locate-synthesized (_bind-form (hy.models.Expression ['<- #* args]))))


;; ---------------------------------------------------------------------------
;; on-raise / absent-as — the boundary that folds failure and absence into values (ADR-DOE-CORE-EFFECTS-003 R7, R8)
;; ---------------------------------------------------------------------------
;;
;; maybe / result are values of a handler with no settings (doeff_core_effects._outcomes, ADR-DOE-API-002 R1) — write (maybe body).
;; They need neither a pattern nor a literal form, so they are not macros.

(defmacro on-raise [#^ Object program #^ Object #* clauses]
  "(on-raise body pattern [:if guard] target …) — of the Raise in the body, only those matching the pattern (the shape of the failure type) are turned into the target's
   business answer and end the scope. A Raise that does not match is passed outward, and success is returned as is.

   (<- answer (on-raise (do! (<- body (read-typed …) :absent (Conflict \"row is missing\"))
                             (<- (revise …))
                             (WriteLanded))
                (Conflict d)        (WriteConflict :detail d)
                (Unreachable d)     (WriteUnreachable :detail d)))

   The pattern is only (Type field …) or (| (A …) (B …)) — catch-all forms (_, a single name, object, Exception) are rejected at expansion time.
   Python exceptions are not caught (only Raise is seen). The guard and target are value expressions. [ADR-DOE-CORE-EFFECTS-003 R7]"
  (import doeff-hy._outcome-forms [on-raise-form])
  (_locate-synthesized (on-raise-form program clauses)))

(defmacro absent-as [#^ Object default #^ Object program]
  "(absent-as default body) — fold an Absent in the body into the default.

   If the body is (do! …), an absence emitted in place of a value by a <- / ! written directly in its text is **resumed** with the default (that binding receives the default
   and continues). An absence deep inside a called defk, and a directly written (<- (Absent …)), do not resume; they end the absent-as scope with the default
   (do not hand a value of another type to the line deep inside, and do not run a continuation that does not assume absence). [ADR-DOE-CORE-EFFECTS-003 R8]

   (<- n (absent-as 0 (do! (<- row (ReadCount \"k\")) (+ row 1))))   ; if absent, continue with row = 0, n = 1"
  (import doeff-hy._outcome-forms [absent-as-form])
  (_locate-synthesized (absent-as-form default program)))


;; ---------------------------------------------------------------------------
;; Internal: binding helpers
;; ---------------------------------------------------------------------------

(defn _is-bind [form]
  "Check if a form is a (<- ...) binding."
  (and (isinstance form hy.models.Expression)
       (> (len form) 0)
       (= (str (get form 0)) "<-")))

(defn _is-iterate [expr]
  "Check if expr is (Iterate ...) or (From ...)."
  (and (isinstance expr hy.models.Expression)
       (> (len expr) 0)
       (in (str (get expr 0)) #{"Iterate" "From"})))

(defn _is-when [form]
  "Check if form is (When ...)."
  (and (isinstance form hy.models.Expression)
       (> (len form) 0)
       (= (str (get form 0)) "When")))

(defn _iterate-arg [expr]
  "Extract the items arg from (Iterate items)."
  (get expr 1))

(defn _bind-parts [form]
  "Extract (name, Type, expr) from a (<- ...) form (a trailing `:absent failure` is set aside — see `_split-absent`).
     (<- expr)           → (None, None, expr)
     (<- name expr)      → (name, None, expr)
     (<- name Type expr) → (name, Type, expr)"
  (import doeff-hy._outcome-forms [split-absent])
  (setv core (. (split-absent form) core))
  (cond
    (= (len core) 2) #(None None (get core 1))
    (= (len core) 3) #((get core 1) None (get core 2))
    (= (len core) 4) #((get core 1) (get core 2) (get core 3))))

(defn _bind-yield [name tp expr [absent None] [helpers None]]
  "Single definition point for the yield form of an effect binding.
   Used by the <- macro AND by every body expander that pre-parses <- forms
   (do! / defp / deftest / for/do / traverse / defhandler clauses), so the type contract of a
   4-element bind is honored at runtime wherever it is written — the shared
   quality checker (dotfiles agent/quality/hy_dsl.py effect_bind) projects the
   same isinstance and relies on this guarantee existing.
   B = the expression of one binding (open-form of outcome-forms): if the answer of open-bind is Pure, its value; otherwise the answer yielded
     (<- expr)           → B = (if (is (. (:= b (open-bind expr)) __class__) Pure) b.value (yield b))
     (<- name expr)      → (setv name B)
     (<- name Type expr) → (do (setv name B)
                               (assert (isinstance name Type) \"expected Type, got <actual>\"))
     (<- … :absent F)    → the receiver of open-bind turns an Absent that occurs in that binding into Raise(F)
   open-bind (doeff_core_effects._outcomes) turns the absent and failure answers of an effect with a declaration into Absent / Raise and emits them in the caller's
   scope, and opens the values of Result / Maybe. An effect with no declaration, and a Program, return what they received as is
   (ADR-DOE-CORE-EFFECTS-003 R5, R6).
   After binding, b is deleted (`(del b)` is placed after the setv; an unnamed (<- expr) receives the answer in a temporary name a and removes a too):
   b holds the yielded Program (Call — function and arguments), so if kept, it keeps the arguments alive until the same line runs next (a long-loop
   generator kept the table of the previous iteration). del is a statement, so for `!` inside an expression, open-form resets the name to None inside the expression."
  (import doeff-hy._outcome-forms [open-form])
  (setv bound (hy.gensym "bound"))
  (setv performed (open-form expr absent helpers bound))
  (cond
    ;; Expansion for type checking (doeff_hy/_static_view.py): type of x = type of the answer of expr.
    ;; The same form as `x = perform(e)` of Python's `@effectful` (docs/24-effectful-perform.md),
    ;; and the declaration of `_doeff_perform` is in static_types.pyi. `(<- x T e)` annotates a gensym temporary with T and
    ;; passes it to x without an annotation (`_hy_gensym_bound_N: T = _doeff_perform(e)` then `x = _hy_gensym_bound_N`),
    ;; so pyright reports red if the type of e's answer does not fit in T, and x is narrowed to T at that place. e appears only once.
    ;; x itself carries no declared type: the clauses of a defhandler and the branches of cond / if / do! become one Python
    ;; function, and pyright keeps one declared type per variable (the last wins), so `(<- read int …)` in one branch and
    ;; `(<- read str …)` in another reported the first as reportRedeclaration / reportAssignmentType although each was right.
    ;; No yield is emitted (types do not depend on yield — on 2026-09-23 it was confirmed that the reds are the same 57 as the form that kept yield in
    ;; controllers/worker of agora-controllers).
    ;; The runtime expansion does not go through this branch (the runtime `(yield e)` is the same as the result of @effectful's rewriting).
    (_static-view?)
      ;; `_doeff_perform` is imported by doeff-hy-check once at the top of the module (STATIC_HELPER_IMPORTS), so the name resolves even for a `<-`
      ;; in places that emit no `_helper-imports`, such as defhandler clauses and a user's defn. If the form held an import,
      ;; pyright would turn `_doeff_perform` into Unknown in a function with 65 `<-`."
      (let [bound `(_doeff-perform ~expr)
            source (when (is-not tp None) (_type-source tp))]
        (cond
          (is name None) bound
          (or (is source None) (not (isinstance name hy.models.Symbol)))
            `(setv ~name ~bound)
          True (let [held (hy.gensym "bound")]
                 `(do (setv (annotate ~held ~(hy.models.String source)) ~bound)
                      (setv ~name ~held)))))
    (is name None) (let [answer (hy.gensym "answer")]
                     `(do (setv ~answer ~performed)
                          (del ~bound)
                          (get #(~answer (setx ~answer None)) 0)))
    (is tp None) `(do (setv ~name ~performed) (del ~bound))
    True `(do
            (setv ~name ~performed)
            (del ~bound)
            ~(_runtime-type-check name (_runtime-type-form tp)
                                  `(+ ~(+ "expected " (str tp) ", got ") (. (type ~name) __name__))))))


;; ---------------------------------------------------------------------------
;; Internal: parse do body
;; ---------------------------------------------------------------------------

(defn _parse-do-body [forms [macro-name "do"]]
  "Split forms into (bindings, body-expr).
   (<- ...) and (When ...) forms are bindings.
   Last non-binding form is body. Other non-binding forms are plain statements
   (setv, when, for, etc.) emitted as-is into the function body."
  (setv bindings []
        body-expr None)
  ;; Collect all forms; track the last non-bind form as body candidate
  (setv all-forms (list forms))
  ;; Find the last non-bind form (= body expression)
  (setv body-idx None)
  (for [#(i form) (enumerate all-forms)]
    (when (not (or (_is-bind form) (_is-when form)))
      (setv body-idx i)))
  (when (is body-idx None)
    (raise (SyntaxError (.format "
{macro}: missing body expression — only bindings found.

  The last form must be a non-binding expression (the return value):

    ({macro}
      (<- x (some-effect))     ;; ← binding
      (<- y (other-effect x))  ;; ← binding
      (+ x y))                 ;; ← body expression (required)

  Every (<- ...) and (When ...) is a binding. You need at least one
  plain expression at the end as the result.
" :macro macro-name))))
  ;; Partition: everything before body-idx goes to bindings, body-idx is body
  (for [#(i form) (enumerate all-forms)]
    (cond
      (= i body-idx) (setv body-expr form)
      (_is-bind form) (.append bindings form)
      (_is-when form) (.append bindings form)
      ;; Non-bind, non-body form → plain statement (setv, when, for, etc.)
      ;; Mark with :plain tag so expansion emits it as-is
      True (.append bindings #("__plain__" form))))
  #(bindings body-expr))


;; ---------------------------------------------------------------------------
;; traverse — applicative traverse as effect (CPS-converted Iterate)
;; ---------------------------------------------------------------------------

(defn _gen-traverse-body [bindings body-expr [in-step False]]
  "Generate the CPS-converted body for traverse/for-do.
   Finds Iterate/From bindings and nests Traverse effects.
   Recognizes (When pred) as a guard — emits Skip when falsy.
   Non-Iterate bindings become yield expressions inside the inner defk.
   in-step: the bindings are inside the per-item function of an outer From, so a From here
   is performed in that function (see _place-traverse)."
  (import doeff-hy._outcome-forms [bind-form :as _bind-form])
  (if (not bindings)
      body-expr
      (let [bind (get bindings 0)
            rest (cut bindings 1 None)]
        ;; Check if this binding is a (When ...) guard
        (if (_is-when bind)
            (let [pred-expr (get bind 1)
                  ;; In-place bang expansion: (When (! (validate x))) →
                  ;; (When (yield (validate x))) — evaluated at guard position
                  ;; [ADR-DOE-HY-003]
                  rewritten-pred (_expand-bangs pred-expr "for/do")
                  inner (_gen-traverse-body rest body-expr in-step)]
              `(do
                 (when (not ~rewritten-pred)
                   ~(_skip-form))
                 ~inner))
            ;; Regular binding
            (let [#(name tp expr) (_bind-parts bind)]
              (if (_is-iterate expr)
                  ;; CPS: wrap rest + body into a defk, emit Traverse effect
                  (let [items (_iterate-arg expr)
                        ;; Extract optional :label from (Iterate items :label "name")
                        label (if (>= (len expr) 4) (get expr 3) None)
                        inner-body (_gen-traverse-body rest body-expr True)
                        param (if (is name None)
                                  (hy.models.Symbol "_unused")
                                  name)]
                    (_place-traverse (_traverse-form param items label inner-body)
                                     (_binds-from? rest)
                                     in-step))
                  ;; Non-Iterate: regular bind (typed bind keeps its isinstance)
                  (let [inner (_gen-traverse-body rest body-expr in-step)]
                    `(do ~(_bind-form bind) ~inner))))))))

(defn _binds-from? [bindings]
  "Whether bindings contain a From / Iterate binding — if so, the per-item function of the preceding From returns the Collection of the inner From."
  (any (gfor bind bindings
             (and (_is-bind bind) (_is-iterate (get (_bind-parts bind) 2))))))

(defn _place-traverse [traverse inner-from? in-step]
  "Turn a Traverse of a single From into the form that fits the place where it is put.

   Nested Froms flatten the body value of each tuple into one item, as with a comprehension (list <- and SQL CROSS JOIN). Therefore:
   - A From inside the per-item function of an outer From (in-step) executes the Traverse inside that function and makes the answer Collection the item's value.
     Before, it did not execute and returned the Traverse effect as the item's value, so the inner Traverse got into valid_values.
   - The Traverse of a From followed by another From (inner-from?) is flattened by one level with Collection.flatten, since the item's value answers the Collection of the inner Collection.
     The outermost of for/do (not in-step) becomes a Program that the writer's `<-` executes
     — the Traverse effect is built at expansion time (when reading items, the same as for a single From), and the Program executes it and flattens.
   A for/do with a single From (outermost, with no From after it) is the Traverse effect as is, as before.
   The expansion for type checking emits no yield and wraps with `_doeff_perform` (for the same reason as _skip-form), and the outermost Program becomes a function
   with no arguments (a lambda's arguments cannot carry annotations, which gives strict reds)."
  (setv perform (if (_static-view?)
                    (fn [expr] `(_doeff_perform ~expr))
                    (fn [expr] `(yield ~expr))))
  (cond
    (and in-step inner-from?) `(.flatten ~(perform traverse))
    in-step (perform traverse)
    (not inner-from?) traverse
    (_static-view?) `((_doeff_do (fn [] (.flatten ~(perform traverse)))))
    True (let [made (hy.gensym "traverse")]
           `((_doeff_do (fn [~made] (.flatten ~(perform made)))) ~traverse))))

(defn _skip-form []
  "To emit Skip, which drops an item whose When is false. At runtime it is `(yield (_doeff_traverse_Skip))`. In the expansion for type checking, as with `<-`,
   no yield is emitted and it is wrapped with `_doeff_perform` — if yield were kept, the per-item function would become a generator, and its answer
   `Generator[Skip, Unknown, T]`, whose send type is unknown, would be a strict red (the answer of Skip is Never, so the following statements are unreachable in the false branch)."
  (if (_static-view?)
      '(_doeff_perform (_doeff_traverse_Skip))
      '(yield (_doeff_traverse_Skip))))

(defn _traverse-form [param items label inner-body]
  "Make one From / Iterate a Traverse effect: the per-item function (receives param, wraps the remaining bindings and body with do) and items.

   Runtime expansion: `(_doeff_traverse_Traverse (_doeff_do (fn [param] (do body))) items)`. The per-item function is wrapped with `@do` just once per Traverse
   — wrapping again per item as `(fn [param] ((_doeff_do (fn [] …))))` cost about 7 µs per item to assemble `@do`
   (DoFunction, functools.wraps). The Program of an item is the same (one call that receives the item as its argument).
   In the expansion for type checking (`_static-view?`), the item's argument is received under an alias `#^ object`, and before the body it is
   re-read as the item's type with `(setv param (_doeff_traverse_item copy-of-items item))` — a Hy fn becomes a def when its body has statements, and
   without an annotation on the argument pyright strict reports red that \"the argument type is unknown\" (the writer has no place to write the item's type).
   traverse_item in static_types.pyi answers the element type of items, so the item's argument can be read as the element type of items.
  items is placed under a name just once, and the function looks that name up (the copy in the expansion does not run)."
  (if (_static-view?)
      (let [item (hy.gensym "item")
            items-ref (hy.gensym "items")
            step `(fn [#^ object ~item]
                    (setv ~param (_doeff_traverse_item ~items-ref ~item))
                    ((_doeff_do (fn [] (do ~inner-body)))))]
        (if (is-not label None)
            `(do (setv ~items-ref ~items)
                 (_doeff_traverse_Traverse ~step ~items-ref :label ~label))
            `(do (setv ~items-ref ~items)
                 (_doeff_traverse_Traverse ~step ~items-ref))))
      (let [step `(_doeff_do (fn [~param] (do ~inner-body)))]
        (if (is-not label None)
            `(_doeff_traverse_Traverse ~step ~items :label ~label)
            `(_doeff_traverse_Traverse ~step ~items)))))

(defmacro traverse [#^ Object #* forms]
  "Applicative traverse — batch processing with handler-injected strategy.
   Alias for for/do. Prefer for/do with From in new code.

   (traverse
     (<- x (Iterate items :label \"extract\"))
     (<- y (some-effect x))
     [x y])

   Requires:
     (import doeff [do :as _doeff-do])
     (import doeff_traverse [Traverse :as _doeff_traverse_Traverse])"
  (setv #(bindings body-expr) (_parse-do-body forms "traverse"))
  (_locate-synthesized (_gen-traverse-body bindings body-expr)))


(defmacro for/do [#^ Object #* forms]
  "Collection comprehension — From/When/bind with handler-injected strategy.

   From: generator bind (like SQL FROM / Haskell <- on list)
   When: guard (like SQL WHERE / Haskell guard)
   <-:  effect bind (kleisli)
   Last expression: yield value (like SQL SELECT / Haskell return)

   (for/do
     (<- item (From items :label \"extract\"))
     (<- ok (validate item))
     (When ok)
     (<- result (process item))
     result)

   Multiple generators (nested):
   (for/do
     (<- item (From items :label \"outer\"))
     (When (active? item))
     (<- sub (From (children item) :label \"inner\"))
     (When (valid? sub))
     (<- result (process item sub))
     result)
   The answer has one item per (item, sub) combination, like a list comprehension — not one
   item per outer item.

   Requires:
     (import doeff [do :as _doeff-do])
     (import doeff_traverse [Traverse :as _doeff_traverse_Traverse])
     (import doeff_traverse [Skip :as _doeff_traverse_Skip])   ; only when a (When …) guard is used

   The `_doeff-do` import has the same type as the `do` doeff-hy-check places for defk
   (doeff_hy/_static_types.pyi re-exports doeff.do.do), so it does not change how the type checker
   reads the module's defk. Without a When guard the expansion never names
   Skip, and pyright strict reports its import as unused — leave it out then."
  (setv #(bindings body-expr) (_parse-do-body forms "for/do"))
  (_locate-synthesized (_gen-traverse-body bindings body-expr)))


;; ---------------------------------------------------------------------------
;; Internal: ! (bang) inline bind expansion — evaluation-position preserving
;; [ADR-DOE-HY-003]
;; ---------------------------------------------------------------------------

(defn _is-bang [form]
  "Check if form is (! expr)."
  (and (isinstance form hy.models.Expression)
       (>= (len form) 2)
       (isinstance (get form 0) hy.models.Symbol)
       (= (str (get form 0)) "!")))

;; Forms that own their own do-context (or treat their body as data).
;; The outer expander must not cross these boundaries: the innermost
;; do-context macro expands its own bangs. [ADR-DOE-HY-003 R4]
;; `handle` is special-cased in the walk: its first argument (the wrapped
;; program) belongs to the ENCLOSING do-context, only its clauses are opaque.
(setv _BANG-OPAQUE-HEADS
  #{"for/do" "traverse" "fnk" "do!" "defhandler"
    "defk" "deff" "defp" "defpp" "deftest" "defmcp-tool"
    "defmacro" "quote" "quasiquote"
    ;; validate of doeff-validation: wrap each check in a separate small Program, and run the (! …) in the arguments of check
    ;; inside that item (running them first in the outer body would break the independence of items and the collection of failures).
    "validate"})

;; Python compiles comprehensions to a separate scope where yield is illegal
;; (SyntaxError since 3.8). A bang here cannot preserve its written position.
(setv _BANG-COMPREHENSION-HEADS #{"lfor" "gfor" "dfor" "sfor"})

;; A nested plain function/class is a separate scope: an inline yield there
;; would silently turn it into a generator instead of performing the effect
;; in the enclosing do-context.
(setv _BANG-FN-HEADS #{"fn" "fn/a" "defn" "defn/a" "defclass"})

(defn _bang-node-line [node]
  (or (getattr node "start_line" None) -1))

(defn _bang-node-src [node]
  (try (.lstrip (hy.repr node) "'")
       (except [Exception] (str node))))

(defn _bang-arity-msg [owner node]
  (.format "
{owner} (line {line}): (! ...) takes exactly one form, got {n}: {src}

  Correct usage:

    (! (effect-expr))
" :owner owner :line (_bang-node-line node) :n (- (len node) 1)
  :src (_bang-node-src node)))

(defn _bang-comprehension-msg [owner head node]
  (setv inner-src (_bang-node-src (get node 1)))
  (.format "
{owner} (line {line}): (! ...) inside ({head} ...) cannot preserve its written
evaluation position — Python compiles {head} to a separate comprehension scope
where yield is illegal, so the effect cannot be performed at this position.

  Fix — perform the effect before the comprehension and bind the value:

    (<- v {src})
    ({head} x xs (use v x))

  Fix — or rewrite as for/do, the effectful comprehension (per-item effects;
  sequential/parallel strategy is chosen by the handler, not the call site):

    (<- results
      (for/do
        (<- x (From xs))
        (<- r {src})
        r))

  [ADR-DOE-HY-003]
" :owner owner :line (_bang-node-line node) :head head :src inner-src))

(defn _bang-nested-fn-msg [owner head node]
  (setv inner-src (_bang-node-src (get node 1)))
  (.format "
{owner} (line {line}): (! ...) inside a nested ({head} ...) cannot preserve its
written evaluation position — the nested {head} is a separate scope, so an
inline bind there would silently turn it into a generator instead of performing
the effect in the enclosing do-context.

  Fix — perform the effect before the {head} and close over the value:

    (<- v {src})
    (fn [x] (use v x))

  Fix — or make the nested function an effectful kleisli with fnk (it returns
  a Program the caller must bind):

    (fnk [x] (use (! {src}) x))

  [ADR-DOE-HY-003]
" :owner owner :line (_bang-node-line node) :head head :src inner-src))

(defn _expand-bangs [form [owner "do-context"] [helpers None]]
  "Rewrite every (! expr) IN PLACE to (yield expr), preserving the written
   evaluation position: conditionality (if/when/cond), short-circuit (and/or),
   left-to-right order within a statement, exception context (try), and
   element positions inside dict/list/set/tuple/f-string literals.
   Returns the rewritten form. [ADR-DOE-HY-003 R1/R2]

   Positions where yield is syntactically impossible raise SyntaxError at
   expansion time with a fix prompt (R3): comprehensions (lfor/gfor/dfor/sfor)
   and nested fn/fn/a/defn/defn/a/defclass bodies.

   Forms that own their own do-context (for/do, traverse, fnk, do!, handle,
   nested defk/deff/defp/deftest/..., defmacro, quote/quasiquote) are opaque —
   their own macro expands their bangs (R4).

   helpers = the module-global name of doeff_core_effects._outcomes (defk passes _HELPERS-NAME,
   which it installs in its module's globals): the binds of this body — every (! e) and every
   (<- …) outside a nested function — reach open_bind by that name instead of `__import__`
   (about 0.2µs a bind). Other callers pass None and keep their (<- …)
   forms for their own expansion."
  (defn #^ Object walk [#^ Object node #^ (| (get tuple #(str str)) None) ctx]
    (cond
      (_is-bang node)
        (do
          (when (!= (len node) 2)
            (raise (SyntaxError (_bang-arity-msg owner node))))
          (when (is-not ctx None)
            (setv #(kind head) ctx)
            (raise (SyntaxError
                     (if (= kind "comprehension")
                         (_bang-comprehension-msg owner head node)
                         (_bang-nested-fn-msg owner head node)))))
          ;; At runtime it goes through the same open-bind as <- (turns the absence and failure of an effect with a declaration into Absent / Raise,
          ;; and opens the values of Result / Maybe — ADR-DOE-CORE-EFFECTS-003 R6). The expansion for type checking is the same `_doeff_perform` as `<-`
          ;; (the type of the expression's answer — the static branch of _bind-yield). Before, it was `(yield e)`, and the yield value of a generator
          ;; with no annotation is Unknown, so an Unknown red that the writer could not fix appeared at every place that used (! …).
          ;; The binding's name passes no bound, so open-form resets it to None inside the expression (a statement del cannot be placed).
          (if (_static-view?)
              `(_doeff-perform ~(walk (get node 1) ctx))
              (do (import doeff-hy._outcome-forms [open-form])
                  (open-form (walk (get node 1) ctx) None helpers))))

      (isinstance node hy.models.Expression)
        (do
          (setv head
            (when (and (> (len node) 0) (isinstance (get node 0) hy.models.Symbol))
              (str (get node 0))))
          (cond
            (and (is-not head None) (in head _BANG-OPAQUE-HEADS))
              node
            ;; handle / on-raise: the wrapped program (arg 1) is evaluated in the
            ;; ENCLOSING do-context — walk it. The clauses own their own
            ;; do-context (handle) or are value expressions the macro checks
            ;; (on-raise: patterns and the answers they map to) — opaque.
            (and (in head #{"handle" "on-raise"}) (>= (len node) 2))
              (hy.models.Expression
                [(get node 0) (walk (get node 1) ctx) #* (cut node 2 None)])
            True
              (do
                (setv new-ctx
                  (cond
                    (and (is-not head None) (in head _BANG-COMPREHENSION-HEADS))
                      #("comprehension" head)
                    (and (is-not head None) (in head _BANG-FN-HEADS))
                      #("fn" head)
                    True ctx))
                (setv walked (hy.models.Expression (lfor child node (walk child new-ctx))))
                ;; A (<- …) in a defk body is expanded here into the form that looks it up by the helpers name (inside a nested function
                ;; it is that function's own binding, so it is left alone — the <- macro expands it into the form as before).
                (if (and (= head "<-") (is-not helpers None) (is ctx None) (not (_static-view?)))
                    (do (import doeff-hy._outcome-forms [bind-form])
                        (bind-form walked helpers))
                    walked))))

      (isinstance node hy.models.FString)
        (hy.models.FString (lfor child node (walk child ctx))
                           :brackets (. node brackets))

      (isinstance node hy.models.FComponent)
        (hy.models.FComponent (lfor child node (walk child ctx))
                              :conversion (. node conversion))

      (isinstance node #(hy.models.List hy.models.Tuple hy.models.Set hy.models.Dict))
        ((type node) (lfor child node (walk child ctx)))

      True node))

  (walk form None))


;; ---------------------------------------------------------------------------
;; defp / defpp — define a Program constant with implicit do context
;; ---------------------------------------------------------------------------

(defn _defp-post-required-msg [macro-name name]
  (.format "
{macro} {name}: {{:post [...]}} is required.

  Correct usage:

    ({macro} {name}
      {{:post [(: % ExportResult)]}}         ;; return type check
      (<- data (load-data :path \"data.csv\"))
      (<- result (process data))
      (export result))

  (: name Type) expands to (isinstance name Type).
  % binds to the program's return value in :post checks.
" :macro macro-name :name name))

(defn _defp-program-return-msg [name]
  (.format "
{name}: return value is a Program (generator), but defp defines Program[T].

  The last expression in your defp body returned a Program instead of a plain
  value. This usually means you forgot to bind it with (<- ...):

    ;; WRONG — returns Program, not the value:
    (defp {name}
      {{:post [(: % str)]}}
      (some-effect))              ;; ← this is a Program, not a str

    ;; CORRECT — bind the Program to get the value:
    (defp {name}
      {{:post [(: % str)]}}
      (<- result (some-effect))   ;; ← binds the effect result
      result)                     ;; ← returns the plain value

  If you intentionally want Program[Program[T]], use defpp instead:

    (defpp {name}
      {{:post [...]}}
      ...)
" :name name))

(defn _defpp-not-program-return-msg [name]
  (.format "
{name}: return value is NOT a Program, but defpp defines Program[Program[T]].

  defpp requires the last expression to return a Program (generator).
  If you don't need Program[Program[T]], use defp instead:

    ;; WRONG — returns a plain value from defpp:
    (defpp {name}
      {{:post [(: % str)]}}
      (<- result (some-effect))
      result)                       ;; ← plain value, not a Program

    ;; CORRECT — return a Program from defpp:
    (defpp {name}
      {{:post [(inspect.isgenerator %)]}}
      (<- config (load-config))
      (build-pipeline config))      ;; ← returns a Program

    ;; Or just use defp if you want Program[T]:
    (defp {name}
      {{:post [(: % str)]}}
      (<- result (some-effect))
      result)
" :name name))

(defn _build-defp [compiler macro-name name body * [program-return-mode "reject"]]
  "Shared implementation for defp/defpp.
   program-return-mode: 'reject' (defp) | 'require' (defpp)"
  (_enforce-no-defp-in-hyk compiler macro-name name)
  (import doeff-hy._outcome-forms [bind-form :as _bind-form])
  (setv #(pre-checks post-checks real-body) (_extract-contracts body _CONTRACT-KEYS (+ macro-name " " (str name))))
  (when (is-not pre-checks None)
    (raise (SyntaxError (.format "
{macro} {name}: :pre is not allowed — {macro} has no parameters.

  Remove {{:pre [...]}} and keep only {{:post [...]}}:

    ({macro} {name}
      {{:post [(: % ExportResult)]}}
      ...)
" :macro macro-name :name name))))
  (when (is post-checks None)
    (raise (SyntaxError (_defp-post-required-msg macro-name name))))
  ;; Inject Program-return guard into :post
  (cond
    (= program-return-mode "reject")
      (do
        (setv guard-msg (_defp-program-return-msg (str name)))
        (setv post-checks (+ [
          (hy.models.Expression
            [(hy.models.Symbol "not")
             (hy.models.Expression
               [(hy.models.Symbol "_doeff_check_program_return") (hy.models.Symbol "%")
                (hy.models.String guard-msg) (hy.models.String "reject")])])]
          (list post-checks))))
    (= program-return-mode "require")
      (do
        (setv guard-msg (_defpp-not-program-return-msg (str name)))
        (setv post-checks (+ [
          (hy.models.Expression
            [(hy.models.Symbol "not")
             (hy.models.Expression
               [(hy.models.Symbol "_doeff_check_program_return") (hy.models.Symbol "%")
                (hy.models.String guard-msg) (hy.models.String "require")])])]
          (list post-checks)))))
  ;; Inline do! expansion: in-place bang rewrite [ADR-DOE-HY-003], parse,
  ;; emit generator with contracts
  (setv expanded-forms
    (lfor form real-body (_expand-bangs form (+ macro-name " " (str name)))))
  (setv #(bindings body-expr) (_parse-do-body expanded-forms macro-name))
  (setv expanded (lfor bind bindings
                   (if (and (isinstance bind tuple) (= (get bind 0) "__plain__"))
                       ;; Plain statement — ADR-DOE-HY-001 guard (wrap expression forms only)
                       (_wrap-statement-guard (get bind 1) (str name))
                       ;; Effect binding — yield (typed bind keeps its isinstance)
                       (_bind-form bind))))
  (setv post-asserts (lfor check post-checks
                       (_expand-check check name "post-condition")))
  (_scoped-definition compiler name [
     (_helper-imports)
     `(setv ~name
       ((_install-guard-globals
          (_doeff_do (fn []
            ~@expanded
            ~(_result-binding body-expr (.get (_contract-types post-checks) "%"))
            (let [% _contract_result]
              ~@post-asserts)
            (return ~(_result-symbol body-expr)))))))
     ;; Preserve S-expr body directly on Program value (DoExpr has __dict__ via pyclass(dict))
     `(setattr ~name "__doeff_body__" ~(_quoted-forms real-body))
     `(setattr ~name "__doeff_name__" ~(str name))
     `(setattr ~name "__doeff_module__" __name__)
     #* (_declaration-setters name (get (_find-contract body) 0) (+ macro-name " " (str name)))]))

(defmacro defp [#^ HyASTCompiler _hy-compiler #^ Object name #^ Object #* body]
  "Define a Program[T] constant. Errors if the return value is itself a Program.
   :post is REQUIRED. Use defpp if Program[Program[T]] is intended.

   (defp my-pipeline
     {:post [(: % ExportResult)]}
     (<- data (load-data))
     (<- result (process data))
     result)"
  (_locate-synthesized (_build-defp _hy-compiler "defp" name body)))

(defmacro defpp [#^ HyASTCompiler _hy-compiler #^ Object name #^ Object #* body]
  "Define a Program[Program[T]] constant. Errors if return is NOT a Program.
   :post is REQUIRED.

   (defpp my-meta-program
     {:post [(inspect.isgenerator %)]}
     (<- config (load-config))
     (build-pipeline config))"
  (_locate-synthesized (_build-defp _hy-compiler "defpp" name body :program-return-mode "require")))


;; ---------------------------------------------------------------------------
;; deftest — effectful test that expands to pytest function
;; ---------------------------------------------------------------------------

(defn _test-scans [value]
  "Check the value of deftest's :scans (the dir or glob that the test scans and reads) and make it a list of globs.
   Reject anything other than a tuple of strings (non-empty, containing no empty string) — if silently dropped, a test that was meant to be declared would not be selected by the selection tool.
   A helper called at expansion time, so it is a defn (it cannot be a defk)."
  (when (or (not (isinstance value hy.models.Tuple)) (= (len value) 0)
            (not (all (gfor g value (and (isinstance g hy.models.String) (> (len g) 0))))))
    (raise (SyntaxError (.format "deftest :scans must be a tuple of strings of dirs or globs to scan and read (e.g. #(\"scripts/x/*.txt\")) — received: {}"
                                 (hy.repr value)))))
  (list value))

(setv _TEST-TIERS #("small" "medium" "large"))

(defn _test-tier [value]
  "Check the value of deftest's :tier (the size class of the test: small, medium or large) and return it as a string.
   Reject anything else — a misspelt tier silently read as the default would put a slow test into the fast set.
   A helper called at expansion time, so it is a defn (it cannot be a defk)."
  (when (not (and (isinstance value hy.models.String) (in (str value) _TEST-TIERS)))
    (raise (SyntaxError (.format "deftest :tier must be one of the strings {} — received: {}"
                                 (.join " " (gfor t _TEST-TIERS (hy.repr (hy.models.String t))))
                                 (hy.repr value)))))
  (str value))

(defn _extract-test-meta [body]
  "Parse optional test metadata dict from front of body.
   Supported keys: :interpreters, :params, :env, :marks, :skip-if, :skip-reason, :scans, :tier.
   :scans = the dirs or globs the test walks and reads (a tuple of strings).
   :tier = the size class of the test (\"small\", \"medium\" or \"large\"; None when not written).
   Returns #(interpreters params-dict env-dict marks skip-if skip-reason scans tier real-body).
   Skips leading docstring if present."
  (setv interpreters None
        params-dict None
        env-dict None
        marks None
        skip-if-expr None
        skip-reason None
        scans None
        tier None
        real-body body)
  (setv meta-idx None)
  (for [#(i form) (enumerate body)]
    (when (isinstance form hy.models.Dict)
      (setv meta-idx i)
      (break))
    ;; Skip string literals (docstrings) at the start
    (when (not (isinstance form hy.models.String))
      (break)))
  ;; Reject a form where another map follows right after the meta map: previously the second one silently became a body expression, and its keys
  ;; (e.g. :skip-if) were dropped — a marker map was added before the existing meta in an agora test, skip disappeared, and a KeyError occurred in the daily run (#2704).
  ;; A map expression at the head of the body has no meaning, so combine the meta into a single map.
  (when (and (is-not meta-idx None) (< (+ meta-idx 1) (len body)) (isinstance (get body (+ meta-idx 1)) hy.models.Dict))
    (raise (SyntaxError (.format "deftest has two meta maps in a row — the keys of the second one, {}, would be dropped as a body expression. Combine the meta into a single map"
                                 (lfor k (cut (get body (+ meta-idx 1)) None None 2) (str k))))))
  (when (is-not meta-idx None)
    (setv meta-dict (get body meta-idx)
          real-body (+ (cut body 0 meta-idx) (cut body (+ meta-idx 1) None)))
    (for [#(k v) (zip (cut meta-dict None None 2) (cut meta-dict 1 None 2))]
      (when (= (str k) ":interpreters")
        (setv interpreters (list v)))
      (when (= (str k) ":params")
        (setv params-dict v))
      (when (= (str k) ":env")
        (setv env-dict v))
      (when (= (str k) ":marks")
        (setv marks (list v)))
      (when (= (str k) ":skip-if")
        (setv skip-if-expr v))
      (when (= (str k) ":skip-reason")
        (setv skip-reason v))
      (when (= (str k) ":scans")
        (setv scans (_test-scans v)))
      (when (= (str k) ":tier")
        (setv tier (_test-tier v)))))
  #(interpreters params-dict env-dict marks skip-if-expr skip-reason scans tier real-body))

(import doeff-hy._static-view [deftest-fixture-annotation :as _deftest-fixture-annotation])

(defn _deftest-param [param]
  "Turn one argument of deftest into #(name writer's-annotation-or-None). Hy's reader reads `#^ T name` as `(annotate name T)`.
   A helper called at expansion time, so it is a defn (it cannot be a defk)."
  (match param
    [head param-name written] :if (and (isinstance param hy.models.Expression) (= head 'annotate))
      #(param-name written)
    _ #(param None)))

(defn _deftest-annotation [param-name written]
  "The annotation attached to deftest's arguments in the expansion for type checking. If the writer wrote `#^ T name`, that; otherwise
   the type from the table of fixture names (DEFTEST_FIXTURE_TYPES in doeff_hy/_static_view.py); for a name not in the table (a user's
   fixture, an argument of :params), `object` is attached — so it neither becomes a strict red as Unknown nor lets the type escape."
  (if (is-not written None)
      written
      (hy.models.Symbol (_deftest-fixture-annotation (hy.mangle (str param-name))))))

(defmacro deftest [#^ HyASTCompiler _hy-compiler #^ Object name #^ Object #* args]
  "Define an effectful test that expands to a pytest-compatible function.
   The test body uses <- for effect binding, same as defk/defp.
   No :pre/:post contracts — use assert for validation.

   (deftest test-signal
     (<- plan (compute-signal \"2026-04-01\"))
     (assert (> (len plan.orders) 0)))

   With interpreter list (string keys resolved by conftest.py):

   (deftest test-signal-multi
     {:interpreters [\"cllm_test\" \"cllm_sim\"]}
     (<- plan (compute-signal \"2026-04-01\"))
     (assert (> (len plan.orders) 0)))

   With fixture parameters:

   (deftest test-signal-dates [trade-date]
     {:params {\"trade-date\" [\"2026-04-01\" \"2026-03-15\"]}}
     (<- plan (compute-signal trade-date))
     (assert (> (len plan.orders) 0)))

   With env overrides (merged with default env by conftest fixture):

   (deftest test-with-sim-time
     {:interpreters [\"cllm_sim\"]
      :env {\"nakagawa.sim_start_time\" \"2026-04-10T06:00:00+09:00\"}}
     (<- result (my-pipeline))
     (assert result))

   With marks and conditional skip:

   (deftest test-kabu-prices
     {:interpreters [\"cllm_paper\"]
      :marks [\"e2e\" \"slow\"]
      :skip-if (not (can-reach \"plutus\" 18082))
      :skip-reason \"kabuStation unreachable\"}
     (<- result (fetch-prices))
     (assert result))

   A test that walks a dir or glob instead of importing what it reads declares it, so a changed-file selector
   (agora-controllers' land_focus_gate) picks the test when a scanned file changes:

   (deftest test-every-table-row-loads
     {:scans #(\"scripts/business_fakes/*/*.txt\")}
     (assert (all-rows-load)))

   A test that is not small (it runs child processes, opens sockets or takes over a minute) declares its
   size class, which pytest's --doeff-tier option of doeff-hy-pytest selects on (a test without :tier is
   small unless the plugin's settings map one of its marks to another tier):

   (deftest test-git-push-round-trip
     {:tier \"medium\"}
     (assert (push-and-fetch)))

   A fixture may carry its type for the static check — `(deftest test-x [#^ str label] …)`;
   pytest's built-in fixtures (tmp_path, monkeypatch, capsys, …) and doeff_interpreter are typed
   by doeff-hy-check without it, any other unannotated fixture is `object`.

   Expansion: generates def test_*(doeff_interpreter, ...fixtures...)
   that creates a DoExpr program and passes it to the interpreter."
  (import doeff-hy._outcome-forms [bind-form :as _bind-form])
  ;; Parse optional params list and body
  (setv fixture-params []
        body args)
  (when (and (> (len args) 0) (isinstance (get args 0) hy.models.List))
    (setv fixture-params (list (get args 0))
          body (cut args 1 None)))
  ;; The writer's annotation `#^ T name` is used only in the type-check expansion; the list of names is read by the runtime expansion, the record and the rewriting of bindings
  (setv fixture-annotations (lfor p fixture-params (_deftest-param p))
        fixture-params (lfor #(param-name _) fixture-annotations param-name))

  ;; Parse optional metadata dict
  (setv #(interpreters params-dict env-dict marks skip-if-expr skip-reason scans tier real-body)
    (_extract-test-meta body))

  ;; Rewriting of val / var / lazy val / lazy var / := (ADR-DOE-HY-006)
  (setv real-body (_rewrite-bindings real-body (+ "deftest " (str name)) _BodyKind.DEFTEST
                                     (sfor p fixture-params (str p))
                                     :module (_module-names _hy-compiler)))

  ;; Expand bangs in the body — in-place (yield ...) rewrite [ADR-DOE-HY-003]
  (setv expanded-forms
    (lfor form real-body (_expand-bangs form (+ "deftest " (str name)))))

  ;; Build the generator body: convert <- to yield, plain forms as-is
  (setv gen-body [])
  (for [form expanded-forms]
    (cond
      ;; (<- name expr) → (setv name (yield expr));
      ;; (<- name Type expr) → same + isinstance assert (see _bind-yield)
      (and (_is-bind form) (is-not (_bind-parts form) None))
      (.append gen-body (_bind-form form))
      ;; Everything else — ADR-DOE-HY-001 guard (wrap expression forms only, statements as is)
      True
      (.append gen-body (_wrap-statement-guard form (str name)))))

  ;; Build the test function
  (setv fn-params (+ [(hy.models.Symbol "doeff_interpreter")] fixture-params))

  ;; Build the program creation + interpreter call
  (setv run-form
    (if (is-not env-dict None)
      `(doeff_interpreter
         ((_doeff_do (fn [] ~@gen-body)))
         :env ~env-dict)
      `(doeff_interpreter
         ((_doeff_do (fn [] ~@gen-body))))))

  ;; Expansion for type checking: attach fixture type annotations to the arguments, and the return value is None (a test function does not
  ;; return a value — the interpreter's answer is discarded as a statement). The runtime expansion returns the answer without annotations, as before
  ;; (up to Python 3.13 annotations are evaluated at definition time, so names used only for type checking are not placed at runtime).
  (setv #(fn-name fn-signature fn-body)
    (if (_static-view?)
      #(`(annotate ~name None)
        (+ [`(annotate doeff_interpreter ~(_deftest-annotation (hy.models.Symbol "doeff_interpreter") None))]
           (lfor #(param-name written) fixture-annotations
             `(annotate ~param-name ~(_deftest-annotation param-name written))))
        `(do ~run-form None))
      #(name fn-params run-form)))

  ;; The pytest decorators, in source order, as #(kind first second) — the one list that both the
  ;; decorator forms below and the item record (doeff-hy._pytest-items) are made from.
  (setv item-decorators [])

  ;; :interpreters → @pytest.mark.parametrize("doeff_interpreter_name", [...])
  (when (is-not interpreters None)
    (.append item-decorators #("parametrize" "doeff_interpreter_name" (hy.models.List interpreters))))

  ;; :params → @pytest.mark.parametrize for each key
  (when (is-not params-dict None)
    (for [#(k v) (zip (cut params-dict None None 2) (cut params-dict 1 None 2))]
      (.append item-decorators #("parametrize" (str k) v))))

  ;; :marks → @pytest.mark.<name> for each mark
  (when (is-not marks None)
    (for [m marks]
      (.append item-decorators #("mark" (str m) None))))

  ;; :skip-if → @pytest.mark.skipif(condition, reason=...)
  (when (is-not skip-if-expr None)
    (.append item-decorators #("skipif" None None)))

  ;; :scans → @pytest.mark.scans("glob", ...) (a declaration of the dir or glob to scan and read — the selection tool reads the source, and it can be read from the marker when run)
  (when (is-not scans None)
    (setv item-decorators (+ item-decorators [#("scans" None (hy.models.List scans))])))

  ;; :tier → @pytest.mark.tier_<tier> (the size class as a mark name — the item record keeps only mark names,
  ;; and the tier option of doeff-hy-pytest reads it at collection; a test without :tier gets its tier from the plugin's settings)
  (when (is-not tier None)
    (.append item-decorators #("mark" (+ "tier_" tier) None)))

  ;; In the expansion for type checking, the decorator's pytest is looked up through the alias
  ;; `_doeff_pytest` that doeff-hy-check places once at the top of the module (STATIC_HELPER_IMPORTS in doeff_hy/_static_view.py) — an `import pytest` per deftest
  ;; overlapped with the writer's import and caused strict reportDuplicateImport.
  (setv #(pytest-name pytest-import)
    (if (_static-view?) #('_doeff_pytest '(do)) #('pytest '(import pytest))))
  (setv decorators
    (lfor #(kind first second) item-decorators
      (match kind
        "parametrize" `(.parametrize (. ~pytest-name mark) ~(hy.models.String first) ~second)
        "mark" `(. (. ~pytest-name mark) ~(hy.models.Symbol first))
        "scans" `(.scans (. ~pytest-name mark) ~@second)
        "skipif" `(.skipif (. ~pytest-name mark) ~skip-if-expr
                    :reason ~(if (is-not skip-reason None) skip-reason
                                 (hy.models.String "skip condition met"))))))

  ;; Recording of pytest items — collection reads it without importing the test module (#1291 — add the record expression to the expansion).
  ;; It is a statement that runs just once at module level, so pass it to module-forms of _scoped-definition (a deftest inside a function body is not recorded —
  ;; pytest collects only module-level functions).
  (setv item-record (_record-test-function name fn-params item-decorators))

  ;; Assemble the function definition with decorators
  (_locate-synthesized (_scoped-definition _hy-compiler name
    (if decorators
      [pytest-import
       (_helper-imports)
       `(defn [~@decorators] ~fn-name [~@fn-signature] ~fn-body)
       `(_install-guard-globals ~name {"_doeff_do" _doeff_do})]
      [(_helper-imports)
       `(defn ~fn-name [~@fn-signature] ~fn-body)
       `(_install-guard-globals ~name {"_doeff_do" _doeff_do})])
    [item-record])))


;; ---------------------------------------------------------------------------
;; defpipeline — named-stage pipeline composition
;; ---------------------------------------------------------------------------

(defn _replace-stage-refs [expr stage-map]
  "Walk an expression and replace stage-name symbols with (! p-pipeline-stage)."
  (cond
    (and (isinstance expr hy.models.Symbol)
         (in (str expr) stage-map))
      (hy.models.Expression
        [(hy.models.Symbol "!") (get stage-map (str expr))])
    (isinstance expr hy.models.Expression)
      (hy.models.Expression (lfor child expr (_replace-stage-refs child stage-map)))
    (isinstance expr hy.models.List)
      (hy.models.List (lfor child expr (_replace-stage-refs child stage-map)))
    (isinstance expr hy.models.FComponent)
      (hy.models.FComponent (lfor child expr (_replace-stage-refs child stage-map)))
    True expr))

(defmacro defpipeline [#^ Object pipeline-name #^ Object #* body]
  "Define a pipeline as named stages. Each stage becomes a clickable defp.

   (defpipeline daily-cllm
     [ohlc]   (fetch-ohlc :ticker \"7203.T\" :day day)
     [news]   (fetch-news :day day)
     [data]   (merge-data ohlc news)
     [signal] (compute-signal data :model \"gpt-5\")
     [result] (execute-and-report signal))

   Expands to:
     (defp p-daily-cllm-ohlc   {:post []} (fetch-ohlc ...))
     (defp p-daily-cllm-news   {:post []} (fetch-news ...))
     (defp p-daily-cllm-data   {:post []} (merge-data (! p-daily-cllm-ohlc) (! p-daily-cllm-news)))
     (defp p-daily-cllm-signal {:post []} (compute-signal (! p-daily-cllm-data) ...))
     (defp p-daily-cllm        {:post []} (execute-and-report (! p-daily-cllm-signal)))

   Stage names in expressions are auto-replaced with (! p-...) references.
   The last stage is also aliased as p-{pipeline-name}.
   Each stage is independently runnable via IDE click."
  ;; Parse body: skip optional docstring, then [name] expr pairs
  (setv forms (list body))
  (setv docstring None)
  (when (and forms (isinstance (get forms 0) hy.models.String))
    (setv docstring (get forms 0))
    (setv forms (list (cut forms 1 None))))
  ;; Parse [name] expr pairs
  (setv stages [])
  (setv i 0)
  (while (< i (len forms))
    (setv name-form (get forms i))
    (when (not (isinstance name-form hy.models.List))
      (raise (SyntaxError (.format "
defpipeline {pipeline}: expected [stage-name], got {got}.

  Each stage must be a [name] expr pair:

    (defpipeline {pipeline}
      [fetch]  (fetch-data :day day)
      [signal] (compute-signal fetch)
      [result] (export signal))
" :pipeline pipeline-name :got (repr name-form)))))
    (when (= (len name-form) 0)
      (raise (SyntaxError (.format "
defpipeline {pipeline}: empty stage name [].
" :pipeline pipeline-name))))
    (when (>= (+ i 1) (len forms))
      (raise (SyntaxError (.format "
defpipeline {pipeline}: stage [{stage}] has no expression.
" :pipeline pipeline-name :stage (get name-form 0)))))
    (setv expr (get forms (+ i 1)))
    (.append stages #((get name-form 0) expr))
    (+= i 2))
  (when (= (len stages) 0)
    (raise (SyntaxError (.format "
defpipeline {pipeline}: no stages defined.
" :pipeline pipeline-name))))
  ;; Build stage-name → defp-name mapping
  (setv prefix (str pipeline-name))
  (setv stage-map {})
  (for [#(sname _) stages]
    (setv (get stage-map (str sname))
      (hy.models.Symbol (+ "p-" prefix "-" (str sname)))))
  ;; Generate defp for each stage
  ;; Each stage expr is wrapped as: (<- _stage expr) _stage
  ;; This ensures both effect constructors and kleisli calls are properly yielded.
  (setv result-forms [])
  (for [#(sname expr) stages]
    (setv defp-name (get stage-map (str sname)))
    (setv resolved-expr (_replace-stage-refs expr stage-map))
    (setv stage-var (hy.models.Symbol (+ "_stage_" (str sname))))
    (.append result-forms
      `(defp ~defp-name {:post []}
         (<- ~stage-var ~resolved-expr)
         ~stage-var)))
  ;; Last stage also defines p-{pipeline-name}
  (setv #(last-sname _) (get stages -1))
  (setv last-defp (get stage-map (str last-sname)))
  (setv pipeline-defp (hy.models.Symbol (+ "p-" prefix)))
  (.append result-forms `(setv ~pipeline-defp ~last-defp))
  `(do ~@result-forms))


;; ---------------------------------------------------------------------------
;; <-> — effectful first-arg threading macro
;; ---------------------------------------------------------------------------

(setv _thread-counter 0)

(defn _fresh-thread-tmp []
  "Generate a fresh temporary variable for <-> threading."
  (global _thread-counter)
  (setv _thread-counter (+ _thread-counter 1))
  (hy.models.Symbol (+ "_thread_" (str _thread-counter))))

(defmacro <-> [#^ Object #* forms]
  "Effectful first-arg threading macro.

   (<-> (f :k v) (g :k2 v2) (h))

   Expands to:
     (<- _t0 (f :k v))
     (<- _t1 (g _t0 :k2 v2))
     (<- _t2 (h _t1))
     _t2

   First form: no threading (initial value).
   Subsequent forms: previous result inserted as first arg after fn name.
   Keyword args (:k v pairs) are preserved in position."
  (when (= (len forms) 0)
    (raise (SyntaxError "<-> requires at least one form")))
  (setv result-forms [])
  (setv prev-tmp None)
  (for [form forms]
    (setv tmp (_fresh-thread-tmp))
    (setv call
      (if (is prev-tmp None)
          ;; First form: no threading
          form
          ;; Subsequent: insert prev-tmp as first arg after fn name
          (hy.models.Expression
            (+ [(get form 0) prev-tmp] (list (cut form 1 None))))))
    (.append result-forms `(<- ~tmp ~call))
    (setv prev-tmp tmp))
  `(do ~@result-forms ~prev-tmp))


;; ---------------------------------------------------------------------------
;; defmcp-tool — MCP tool definition backed by doeff handler
;; ---------------------------------------------------------------------------

(defn _mcp-param-field [p key]
  "The form of the field `key` (such as \":name\") of the Hy dict `p` of the schema (`{:name \"x\" :type \"string\" :description \"…\" …}`) —
   None if not written (the writer's `None` is a symbol form, so it is distinguished from not being written)."
  (next (gfor #(k v) (zip (cut p None None 2) (cut p 1 None 2)) :if (= (str k) key) v) None))

(defn _mcp-param-schema [schema p]
  "The expression that builds the McpParamSchema of one MCP argument (the schema's Hy dict `p`) — passes only the written fields once as keywords
   (:enum becomes a tuple). Back when the expression was rebuilt per field, writing :default together with :required or :enum broke the keywords
   and the definition failed at definition time. schema = the name by which the expansion looks up McpParamSchema."
  (setv enum (_mcp-param-field p ":enum")
        required (_mcp-param-field p ":required")
        default (_mcp-param-field p ":default"))
  `(~schema
     :name ~(str (_mcp-param-field p ":name"))
     :type ~(str (_mcp-param-field p ":type"))
     :description ~(str (_mcp-param-field p ":description"))
     ~@(if (is enum None) [] [(hy.models.Keyword "enum") `(tuple ~enum)])
     ~@(if (is required None) [] [(hy.models.Keyword "required") required])
     ~@(if (is default None) [] [(hy.models.Keyword "default") default])))

(defn _mcp-handler-param [tool p]
  "The handler argument that receives one MCP argument (the schema's Hy dict `p`) — attach the schema's type annotation to the name (a string annotation —
   not evaluated at runtime, the same as defk's contract annotations).

   The annotation is taken from the row of the table MCP_JSON_TYPES of doeff.mcp (`mcp-param-annotation`). The MCP server loop checks
   received values with the same table and rejects a mismatched value or an omitted required argument before calling the handler, so the annotation is also true at runtime
   (back when values were passed without checking, `\"100\"` reached an `int` argument).
   A non-required argument is `| None` (the server passes None for an argument that was not sent and has no default). An argument that does not write :required is required
   (the default of McpParamSchema), and a :required that is not literally True, and an argument whose default is `None`, are not decided to be required.
   Back when there were no annotations, strict type checking gave 2 reds per argument that the writer could not fix (no annotation, type Unknown), and 1 more
   at every place in the body that passes that argument. An unknown :type is made an error at expansion time."
  (setv pname (str (_mcp-param-field p ":name"))
        ptype (_mcp-param-field p ":type")
        required (_mcp-param-field p ":required")
        default (_mcp-param-field p ":default")
        annotation (mcp-param-annotation (str ptype)
                                         (and (or (is required None) (= required 'True))
                                              (!= default 'None))))
  (when (is annotation None)
    (raise (SyntaxError
             (.format "defmcp-tool {}: the :type {} of argument {} is an unknown type — write one of {} (it is also mapped to the type annotation of the handler's argument)"
                      tool pname (hy.repr ptype)
                      (.join ", " (gfor type-name (mcp-json-type-names) (+ "\"" type-name "\"")))))))
  `(annotate ~(hy.models.Symbol (.replace pname "-" "_")) ~(hy.models.String annotation)))

(defn _parse-mcp-params [param-list schema tool]
  "Parse MCP parameter list into (param-schemas, fn-params).
   Each element is a dict with :name, :type, :description, and optional
   :enum, :required, :default.

   Returns #(schema-exprs params) where schema-exprs are
   McpParamSchema constructor calls (`_mcp-param-schema`) and params are
   the @do function's parameters, annotated with the schema's type
   (`_mcp-handler-param`). schema = the symbol the expansion reads
   McpParamSchema by (defmcp-tool picks it per view); tool = the tool's
   name for error messages."
  #((lfor p param-list (_mcp-param-schema schema p))
    (lfor p param-list (_mcp-handler-param tool p))))

(defn _mcp-self-calls [name body]
  "Of the body `body` (a sequence of forms) of defmcp-tool, the sequence of call expressions (in written order) whose head is the tool's own name `name`.

   The expansion binds `name` to McpToolDef, so a `(name …)` in the body points at the tool even if a defk of the same name exists earlier, and it fails at run time with
   TypeError: 'McpToolDef' object is not callable (the MCP tool example in docs chapter 12). Names are
   compared after mangling (`submit-order` and `submit_order` are the same name). The inside of quotes (quote, quasiquote) is data, so it is not looked at.
   A reference that only reads the tool's value (`(. name field)` and `name` passed as an argument) is not a call, so it is not counted. Only the spelling is looked at, so a form that
   rebinds the same name in the body and calls it is also rejected — renaming the tool is enough."
  (setv own (hy.mangle (str name)))
  (defn #^ bool head-is? [#^ hy.models.Expression node #^ (get tuple #(str ...)) names]
    (and (> (len node) 0)
         (isinstance (get node 0) hy.models.Symbol)
         (in (hy.mangle (str (get node 0))) names)))
  (defn #^ (get list hy.models.Expression) walk [#^ Object node]
    (cond
      (and (isinstance node hy.models.Expression) (head-is? node #("quote" "quasiquote")))
        []
      (isinstance node hy.models.Expression)
        (+ (if (head-is? node #(own)) [node] [])
           (lfor child node found (walk child) found))
      (isinstance node hy.models.Sequence)
        (lfor child node found (walk child) found)
      True
        []))
  (lfor form body found (walk form) found))

(defmacro defmcp-tool [#^ HyASTCompiler _hy-compiler #^ Object name #^ Object description #^ Object param-list #^ Object #* body]
  "Define an MCP tool backed by a doeff @do handler.

   The body uses <- for effect binding, same as defk.
   Typically the body is a single line calling an existing defk.

   (defmcp-tool submit-order-tool
     \"Submit a trading order to Kabustation\"
     [{:name \"symbol\" :type \"string\" :description \"Stock symbol code\"}
      {:name \"side\" :type \"string\" :enum [\"buy\" \"sell\"] :description \"Order side\"}
      {:name \"qty\" :type \"integer\" :description \"Quantity to trade\"}]
     (<- result (submit-order symbol side qty))
     result)

   Expands to:
   1. A @do function _<name>_mcp_handler with params from the schema, each annotated with
      the schema's :type as a Python type (`_mcp-handler-param`)
   2. An McpToolDef instance <name> with the schema + handler

   <name> is bound to McpToolDef, so give the tool a name different from the defk the body calls. A form where the body calls <name> is rejected at expansion time
   (`_mcp-self-calls`) — before it was rejected, even if a defk of the same name existed earlier, the call in the body pointed at the tool, and it failed at run time with
   TypeError: 'McpToolDef' object is not callable (the MCP tool example in docs chapter 12).

   When written inside a function body, put the import and the handler into an immediately-called function and bind only <name> (_scoped-definition —
   so as not to leave `_doeff_do` of for/do and traverse, read earlier in the outer function, unbound. The handler is looked up by <name>'s :handler).

   The handler decorator's `_doeff_do` is taken from `_helper-imports`, the same as defk. The expansion for type checking emits no import and
   reads the `_doeff_do` that doeff-hy-check places at the top of the module — back when `doeff.do` was imported per tool, in a module with 65 tools
   the declarations of `_doeff_do` exceeded 64, pyright turned it into Unknown, and calls of defk in the same module were seen
   as the type of :post instead of Program.

   The expansion for type checking emits no import at all. The tool's values McpToolDef / McpParamSchema are also looked up through `_doeff_McpToolDef` / `_doeff_McpParamSchema`,
   which doeff-hy-check places once at the top of the module (MCP_STATIC_IMPORTS in doeff_hy/_static_view.py) —
   importing `doeff.mcp` per tool made the declarations of McpToolDef exceed 64 in a module or class with 65 tools, and the tool's value became
   Unknown. The runtime expansion imports doeff.mcp per tool, as before."
  (when (not (isinstance description hy.models.String))
    (raise (SyntaxError (.format "
defmcp-tool {name}: second argument must be a description string.

  (defmcp-tool {name}
    \"Description of what this tool does\"
    [{{:name \"param\" :type \"string\" :description \"param desc\"}}]
    body)
" :name name))))
  (when (not (isinstance param-list hy.models.List))
    (raise (SyntaxError (.format "
defmcp-tool {name}: third argument must be a parameter list [...].

  (defmcp-tool {name}
    \"Description\"
    [{{:name \"param\" :type \"string\" :description \"param desc\"}}]
    body)
" :name name))))
  (setv self-calls (_mcp-self-calls name body))
  (when self-calls
    (raise (SyntaxError
             (.format "defmcp-tool {name}: the body calls the tool's own name {name} ({call}) — the expansion binds this name to McpToolDef, so even if a defk of the same name exists earlier the call in the body points at the tool, and it fails at run time with TypeError: 'McpToolDef' object is not callable. Give the tool a name different from the defk it calls (e.g. {name}-tool)"
                      :name name
                      :call (cut (hy.repr (get self-calls 0)) 1 None)))))
  ;; The name that builds the tool's value: in the expansion for type checking, `_doeff_<name>` at the top of the module (MCP_STATIC_IMPORTS of static_view.py),
  ;; at runtime, the import of doeff.mcp per tool
  (setv #(tool-def param-schema mcp-import)
    (if (_static-view?)
        #('_doeff_McpToolDef '_doeff_McpParamSchema '(do))
        #('McpToolDef 'McpParamSchema '(import doeff.mcp [McpToolDef McpParamSchema]))))
  ;; Parse param list
  (setv #(schema-exprs param-syms) (_parse-mcp-params param-list param-schema (str name)))
  ;; Build handler function name
  (setv handler-name (hy.models.Symbol (+ "_" (str name) "_mcp_handler")))
  ;; Expand bangs in body — in-place (yield ...) rewrite [ADR-DOE-HY-003]
  (setv expanded-forms
    (lfor form body (_expand-bangs form (+ "defmcp-tool " (str name)))))
  (_scoped-definition _hy-compiler name [
     (_helper-imports)
     mcp-import
     `(defn [_doeff_do] ~handler-name ~param-syms
       ~@expanded-forms)
     `(setv ~name
       (~tool-def
         :name ~(str name)
         :description ~description
         :params (tuple [~@schema-exprs])
         :handler ~handler-name))]))


;; ---------------------------------------------------------------------------
;; set! — mutation macro for lazy-var
;; ---------------------------------------------------------------------------

(defmacro set! [#^ Object name #^ Object val]
  "Mutate a lazy-var: update local binding and write back to state.

   (set! items (+ items [new-item]))

   Expands to:
     (setv items (+ items [new-item]))
     (<- (Put _lazy_items_key (Some (+ items [new-item]))))

   Requires a lazy-var in scope (the _lazy_{name}_key variable must exist).
   Using set! on a lazy-val raises a compile-time SyntaxError in defhandler/defk."
  (import warnings)
  (warnings.warn
    (+ "(set! " (str name) " …) has been replaced by (:= " (str name) " new-value) — please move to it (the meaning is the same)"
       " [ADR-DOE-HY-006]")
    DeprecationWarning
    :stacklevel 2)
  (setv key-var (hy.models.Symbol (+ "_lazy_" (str name) "_key")))
  ;; Both expansions look it up through aliases with a `_doeff_` prefix (so as not to hide the writer's effects such as KV's Put — doeff_hy/_expansion_names.py).
  ;; The alias bindings are placed in the expansion for type checking only when doeff-hy-check reads them at the top of the module (SESSION_STATIC_IMPORTS of doeff_hy/_static_view.py),
  ;; and at runtime they are the imports that defhandler emits.
  (setv put (_alias-symbol "Put")
        some (_alias-symbol "Some"))
  `(do
     (setv ~name ~val)
     (<- (~put ~key-var (~some ~name)))))


;; ---------------------------------------------------------------------------
;; validate / check — run all independent checks and collect the failures (the Hy face of doeff-validation)
;; ---------------------------------------------------------------------------
;;
;; The runtime part (Check, validate, ValidationException) belongs to doeff-validation, and this holds
;; only the expansion (ADR-DOE-HY-005 R5 — macros are placed only in doeff-hy). The expanded code
;; imports doeff_validation, so the user side installs doeff-validation.
;;
;;   (require doeff-hy.macros [defk validate check])
;;
;;   (defk ensure-placement-matches [job request]
;;     {:pre [(: job AgentJob) (: request PlacementRequest)] :post [(: % NoneType)]}
;;     (! (validate
;;          (check = job.phase Phase.PENDING :reason PlacementMismatch.PHASE)
;;          (check is-not (! (LookupConversation request.conversation)) None
;;                 :reason PlacementMismatch.CONVERSATION))))
;;
;; - validate: what is lined up directly under it are independent items — two kinds, check and Program (a call of defk).
;;   Run all the items (sequential, concurrent or fail-fast is chosen by the handler of doeff-traverse), collect all the failures of the failed items,
;;   and it becomes a Program that raises ValidationException if there is even one. In a defk body,
;;   run it with (! (validate …)) or (<- _ (validate …)). A bare (<- …) / (! …) directly under it is an error at expansion time.
;; - check: can be written only directly under validate and inside :pre / :post of defk / do!.
;;   (check operator arg … :reason reason) or (check expr :reason reason). Each argument is evaluated from the left, and only arguments marked
;;   with (! …) are run as effects, and the comparison uses the resulting values. The expression's text and the evaluated values are recorded
;;   (short-circuit and control forms such as and / or are not decomposed). A check anywhere else is an error at expansion time.
;; - The functions in this section are defn: they are called at macro expansion time (compile time), and there is no place to run a Program,
;;   so they cannot be defk.
;;
;; Design record: docs/design/doeff-validation/design.md.

;; Forms whose meaning changes if arguments are evaluated first (short-circuit, control, binding). In the parenthesized form they are not decomposed, and only the expression and truth value are
;; recorded; if placed on a flat-form operator, it is an error at expansion time.
(setv _VALIDATION-NOT-DECOMPOSABLE
  #{"and" "or" "if" "when" "unless" "cond" "let" "do" "fn" "lfor" "sfor" "dfor" "gfor"
    "->" "->>" "doto" "match" "setv" "quote" "quasiquote" "!"})


(defn _validation-head-name [form]
  "Return the name at the head of a form (to distinguish forms directly under validate and a contract's check)."
  (when (and (isinstance form hy.models.Expression) (> (len form) 0) (isinstance (get form 0) hy.models.Symbol))
    (str (get form 0))))


(defn _validation-is-check-form [form]
  "Whether form is a (check …) — used by doeff-hy's defk / do! to recognize a check inside a contract."
  (= (_validation-head-name form) "check"))


(defn _validation-source [form]
  "The text as written, to be left in the failure record."
  (setv text (hy.repr form))
  (if (.startswith text "'") (cut text 1 None) text))


(defn _validation-split-reason [args]
  "Extract :reason reason from the arguments of check."
  (setv positional [] reason 'None i 0)
  (while (< i (len args))
    (setv arg (get args i))
    (cond
      (and (isinstance arg hy.models.Keyword) (= (str arg) ":reason"))
        (do (when (>= (+ i 1) (len args))
              (raise (SyntaxError "check: write a reason after :reason")))
            (setv reason (get args (+ i 1)))
            (+= i 2))
      (isinstance arg hy.models.Keyword)
        (raise (SyntaxError (+ "check: unknown keyword " (str arg) " — only :reason is allowed")))
      True (do (.append positional arg) (+= i 1))))
  #(positional reason))


(defn _validation-parse-check [args]
  "Resolve the arguments of check into #(expression-text operator-or-None argument-list reason) (the analysis shared by validate and contracts).

   When the operator is None, judge by the truth value of the single expression in the argument list itself (a form that is not decomposed)."
  (setv #(positional reason) (_validation-split-reason args))
  (cond
    (= (len positional) 0)
      (raise (SyntaxError "check: write it in the form (check operator arg …) or (check expr)"))
    (= (len positional) 1)
      (let [expr (get positional 0)
            head (_validation-head-name expr)]
        (if (and head (not-in head _VALIDATION-NOT-DECOMPOSABLE) (> (len expr) 1))
            #((_validation-source expr) (get expr 0) (list (cut expr 1 None)) reason)
            #((_validation-source expr) None [expr] reason)))
    True
      (let [op (get positional 0)
            operands (list (cut positional 1 None))]
        (when (and (isinstance op hy.models.Symbol) (in (str op) _VALIDATION-NOT-DECOMPOSABLE))
          (raise (SyntaxError (+ "check: " (str op) " changes meaning if its arguments are evaluated first — "
                                 "write it in the parenthesized form (check (" (str op) " …))"))))
        #((_validation-source (hy.models.Expression [op #* operands])) op operands reason))))


(defn _validation-contains-bang [form]
  "Whether the argument expression has a (! …) mark (only marked arguments are run as effects)."
  (cond
    (= (_validation-head-name form) "!") True
    (in (_validation-head-name form) #{"quote" "quasiquote"}) False
    (isinstance form hy.models.Sequence) (any (gfor sub form (_validation-contains-bang sub)))
    True False))


(defn _validation-imports []
  "The imports of doeff-validation and doeff's do that the expanded code refers to (so that pyright can follow the types).
   The runtime location of do is `_runtime-do` of the helper table."
  ;; Make the aliases names used only by the expansion of validate / check: importing inside a defk body makes them local names of the function, so
  ;; if the same name as the `_doeff_do` etc. used by defk is imported, earlier references become unbound.
  (if (_static-view?)
      `(do (import doeff-hy._static-types [do :as _doeff_validation_do])
           (import doeff-validation :as _doeff_validation)
           (import doeff-validation._api :as _doeff_validation_api)
           (import doeff-validation._failures :as _doeff_validation_failures))
      `(do ~(_runtime-do-import '_doeff_validation_do)
           (import doeff-validation :as _doeff_validation)
           (import doeff-validation._api :as _doeff_validation_api)
           (import doeff-validation._failures :as _doeff_validation_failures))))


(defn _validation-predicate [op params]
  "The judgment function's expression: if there is an operator, (fn [a b] (op a b)); otherwise the single value itself."
  (if (is op None)
      `(fn [~@params] ~(get params 0))
      `(fn [~@params] (~op ~@params))))


;; ---------------------------------------------------------------------------
;; A check directly under validate — make it an item (a value of type Check)
;; ---------------------------------------------------------------------------

(defn _validation-argument [form]
  "Turn one argument into a pair of the text and a thunk evaluated inside the item (so that an evaluation failure becomes that item's failure).

   For an argument with a (! …) mark, the thunk builds a small Program in which the mark is rewritten to yield by doeff-hy's rule (ADR-DOE-HY-003),
   and it is run inside the item. An argument without a mark is evaluated as an ordinary value (a Program value is also compared as is)."
  (if (_validation-contains-bang form)
      `(_doeff_validation_failures.CheckArgument
         ~(_validation-source form)
         (fn [] ((_doeff_validation_do (fn [] (return ~(_expand-bangs form "check"))))))
         True)
      `(_doeff_validation_failures.CheckArgument ~(_validation-source form) (fn [] ~form) False)))


(defn _validation-check-spec [args]
  "Turn a check directly under validate into an expression that constructs the type Check (each argument is passed as CheckArgument, and Check is used as is)."
  (setv #(expression op operands reason) (_validation-parse-check args))
  (setv params (lfor _ operands (hy.gensym "arg")))
  `(_doeff_validation.Check
     ~(_validation-predicate op params)
     ~@(lfor o operands (_validation-argument o))
     :reason ~reason
     :expression ~expression))


(defn _validation-item [form]
  "Turn a form directly under validate into an item — check becomes a check specification, anything else is a Program as is."
  (setv head (_validation-head-name form))
  (cond
    (in head #{"<-" "!"})
      (raise (SyntaxError (+ "validate: (" head " …) cannot be written directly under it — " (_validation-source form) "\n\n"
                             "  What is lined up directly under validate are independent items (check or Program).\n"
                             "  Place a Program item as is, without (! …) (validate runs it).\n"
                             "  Do the processing that depends on a previous value before validate, or have a helper defk\n"
                             "  hold its own validate.")))
    (= head "check") (_validation-check-spec (list (cut form 1 None)))
    True form))


;; ---------------------------------------------------------------------------
;; A check inside the contract of defk / do! — make it a sequence of statements evaluated on the spot
;; ---------------------------------------------------------------------------

(defn _validation-contract-block [checks fn-name phase]
  "Turn the sequence of checks of a contract (:pre / :post) into a sequence of statements that evaluates all of them, collects the failures, and raises ValidationException
   if there is even one. Called by defk / do! of doeff-hy when assembling the contract.

   Each check's arguments are evaluated from the left, and arguments marked with (! …) are run on the spot (the body of defk / do! is
   a generator, so yield can be written). If the evaluation of an argument or of the judgment is an exception, it is collected as CheckError."
  (setv failures (hy.gensym "failures"))
  (setv owner (+ (str fn-name) " " phase))
  (setv per-check
    (lfor form checks
      (let [#(expression op operands reason) (_validation-parse-check (list (cut form 1 None)))
            params (lfor _ operands (hy.gensym "arg"))
            evaluated (hy.gensym "evaluated")
            error (hy.gensym "error")]
        `(do
           (setv ~evaluated [])
           (try
             ~@(lfor #(p o) (zip params operands)
                 `(do
                    (setv ~p ~(if (_validation-contains-bang o) (_expand-bangs o owner) o))
                    (.append ~evaluated (_doeff_validation_failures.EvaluatedArgument ~(_validation-source o) ~p))))
             (when (not (_doeff_validation_api.judge (~(_validation-predicate op params) ~@params)))
               (.append ~failures
                        (_doeff_validation_failures.CheckFailure ~expression (tuple ~evaluated) ~reason)))
             (except [~error Exception]
               (.append ~failures
                        (_doeff_validation_failures.CheckError ~expression (tuple ~evaluated) ~error
                                                                   ~reason))))))))
  [(_validation-imports)
   `(setv ~failures [])
   #* per-check
   `(when ~failures
      (raise (_doeff_validation.ValidationException (tuple ~failures) :context ~owner)))])


;; ---------------------------------------------------------------------------
;; Macros
;; ---------------------------------------------------------------------------

(defmacro validate [#^ Object #* forms]
  "Build a Program that runs all independent items (check and Program) and collects the failures of all the failed items.

   In a defk body, run it with (! (validate …)) or (<- _ (validate …)).

   (! (validate
        (check = (! (CountSeats node)) 0 :reason Reason.SEATS-LEFT)
        (check = job.phase Phase.PENDING :reason Reason.PHASE)))"
  `(do ~(_validation-imports)
       (_doeff_validation.validate ~@(lfor form forms (_validation-item form)))))


(defmacro check [#^ Object #* args]
  "check can be written only directly under validate and in :pre / :post of defk / do!. If it is expanded anywhere else, it is an error."
  (raise (SyntaxError (+ "check: it can be written only directly under validate and in :pre / :post of defk / do!"
                         " (not allowed in a defk body, fn or a comprehension)\n\n"
                         "    (! (validate\n"
                         "         (check = job.phase Phase.PENDING :reason PlacementMismatch.PHASE)\n"
                         "         (check is-not (! (LookupConversation request.conversation)) None\n"
                         "                :reason PlacementMismatch.CONVERSATION)))\n\n"
                         "  To reuse a group of checks, have a helper defk hold its own validate,\n"
                         "  and line up the calls of that defk in the outer validate."))))