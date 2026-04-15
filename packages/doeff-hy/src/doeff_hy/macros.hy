;;; doeff-hy standard macros — effect composition for doeff.
;;;
;;; Usage:
;;;   (require doeff-hy.macros [do! defk deff fnk <- ! <-> set! defp defpp deftest
;;;                             defpipeline traverse for/do
;;;                             defhandler handle])
;;;   (import doeff [do :as _doeff-do])
;;;
;;; Core effects are imported from doeff_core_effects:
;;;   (import doeff_core_effects [Ask Try slog])
;;;
;;; Contract rules:
;;;   - deff:        {:pre [...]} and {:post [...]} are REQUIRED
;;;   - defk:        {:pre [...]} and {:post [...]} are REQUIRED
;;;   - defp/defpp:  {:post [...]} is REQUIRED, :pre not allowed
;;;   - fnk:         no contracts (anonymous)
;;;   - do!:         :pre/:post optional, supports (: name Type) shorthand
;;;   - (: name Type) in :pre/:post expands to (isinstance name Type)
;;;   - Arbitrary expressions can be mixed with (: ...) in the same list

;; ---------------------------------------------------------------------------
;; Internal: .hyk/.hyp extension enforcement
;; ---------------------------------------------------------------------------

(import os.path)
(import inspect)

;; Re-export handle macros so users only need one require line.
;; Without this, forgetting (require doeff-hy.handle [defhandler]) causes
;; (defhandler ...) to compile as a function call, silently leaking yield
;; from nested <- to the enclosing defn scope. See #387.
(require doeff-hy.handle [defhandler handle])

(defn _compiling-file-ext []
  "Return the file extension of the .hy/.hyk/.hyp file being compiled.
   Walks the call stack to find the Hy source_to_code path argument."
  (try
    (for [frame-info (inspect.stack)]
      ;; Hy's _hy_source_to_code has 'path' as a local variable
      (setv loc (. frame-info [0] f_locals))
      (when (in "path" loc)
        (setv path (get loc "path"))
        (when (isinstance path str)
          (setv ext (get (os.path.splitext path) 1))
          (when (in ext [".hyk" ".hyp"])
            (return ext)))))
    (return "")
    (except [e Exception] (return ""))))

(defn _enforce-no-defp-in-hyk [macro-name fn-name]
  "Raise SyntaxError if a defp/defpp macro is used in a .hyk file."
  (setv ext (_compiling-file-ext))
  (when (= ext ".hyk")
    (raise (SyntaxError (.format "
{macro} {name}: cannot define a Program entrypoint in a .hyk file.

  .hyk files are for kleisli functions (defk, deff, defhandler).
  Move this {macro} to a .hyp file instead.
" :macro macro-name :name fn-name)))))

(defn _warn-defk-in-hyp [macro-name fn-name]
  "Warn if defk/deff is used in a .hyp file."
  (setv ext (_compiling-file-ext))
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

(defn _extract-contracts [body]
  "Parse optional {:pre [...] :post [...]} from front of body forms.
   Skips leading docstring if present.
   Returns #(pre-checks post-checks real-body).
   pre-checks/post-checks are None if not specified, [] if specified but empty."
  (setv pre-checks None
        post-checks None
        real-body body)
  ;; Find the contract dict — may be body[0] or body[1] (after docstring)
  (setv contract-idx None)
  (for [#(i form) (enumerate body)]
    (when (isinstance form hy.models.Dict)
      (setv contract-idx i)
      (break))
    ;; Skip string literals (docstrings) at the start
    (when (not (isinstance form hy.models.String))
      (break)))
  (when (is-not contract-idx None)
    (setv contract (get body contract-idx)
          real-body (+ (cut body 0 contract-idx) (cut body (+ contract-idx 1) None)))
    (for [#(k v) (zip (cut contract None None 2) (cut contract 1 None 2))]
      (when (= (str k) ":pre")
        (setv pre-checks (list v)))
      (when (= (str k) ":post")
        (setv post-checks (list v)))))
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
    (import doeff [WithHandler])

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

    ;; Install with WithHandler
    (WithHandler my-handler body)
    (WithHandler (my-handler config) body)
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

(defn _expand-check [check fn-name phase]
  "Expand a single contract check into an assert form.
   (: x T) → isinstance assert with clear type error message.
   (: x \"desc\") → no-op (documentation-only annotation).
   Other  → generic condition assert."
  (if (_is-type-check check)
      (let [target (get check 1)
            tp (get check 2)]
        (if (isinstance tp hy.models.String)
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
            (let [target-label (if (= (str target) "%") "return value" (str target))]
              `(assert (isinstance ~target ~tp)
                       (+ ~(+ (str fn-name) ": " phase " type error: `" target-label "` expected " (str tp) ", got ")
                          (. (type ~target) __name__))))))
      `(assert ~check ~(+ (str fn-name) ": " phase " failed: " (str check)))))

(defn _build-fn-with-contracts [decorators name params pre-checks post-checks real-body]
  "Build a defn form with pre/post assertion wrappers.
   Works for both plain functions (deff) and generator/kleisli functions (defk)."
  (setv pre-code (lfor check (or pre-checks [])
                   (_expand-check check name "pre-condition")))
  (if post-checks
      (let [post-asserts (lfor check post-checks
                           (_expand-check check name "post-condition"))
            init-forms (cut real-body 0 -1)
            last-form (get real-body -1)]
        `(defn ~decorators ~name ~params
           ~@pre-code
           ~@init-forms
           (setv _contract_result ~last-form)
           (let [% _contract_result]
             ~@post-asserts)
           _contract_result))
      `(defn ~decorators ~name ~params
         ~@pre-code
         ~@real-body)))


;; ---------------------------------------------------------------------------
;; deff — defn with :pre/:post contracts
;; ---------------------------------------------------------------------------

(defmacro deff [name params #* body]
  "Define a function with :pre/:post contracts.

   (deff my-fn [x y]
     {:pre [(: x int) (: y str)]
      :post [(: % list)]}
     (list (range x)))

   :pre and :post are REQUIRED.
   (: name Type) is shorthand for (isinstance name Type).
   Arbitrary validation expressions are also allowed in the same list."
  (_warn-defk-in-hyp "deff" name)
  (setv #(pre-checks post-checks real-body) (_extract-contracts body))
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
  (_build-fn-with-contracts [] name params pre-checks post-checks real-body))


;; ---------------------------------------------------------------------------
;; defk — kleisli with :pre/:post contracts + bang expansion
;; ---------------------------------------------------------------------------

(defmacro defk [name params #* body]
  "Define a kleisli function (@do decorator) with :pre/:post contracts.
   Supports ! (bang) inline bind: (! expr) is expanded to (<- _tmp expr).
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
     (k1 (! (k2 x)) (! (k3 y))))"
  ;; Warn if defk is used in .hyp file
  (_warn-defk-in-hyp "defk" name)
  ;; Reject handler-like signatures early — these should use defhandler
  (_reject-handler-signature name params)
  (setv #(pre-checks post-checks real-body) (_extract-contracts body))
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
  ;; Extract lazy clauses from real-body
  (import doeff-hy.handle [_is-lazy-clause _parse-lazy _references-symbol
                           _check-set-bang-violations])
  (setv lazy-defs [])
  (setv body-without-lazy [])
  (for [form real-body]
    (if (_is-lazy-clause form)
        (.append lazy-defs (_parse-lazy form))
        (.append body-without-lazy form)))
  ;; Check set! violations on lazy-val names
  (when lazy-defs
    (_check-set-bang-violations lazy-defs body-without-lazy))
  ;; Build lazy init forms (using <- syntax for defk context)
  (setv lazy-init-forms [])
  (when lazy-defs
    (for [#(lname lbody _mut) lazy-defs]
      ;; Symbol scan: only inject if body references this lazy name
      (when (any (gfor form body-without-lazy
                   (_references-symbol form (str lname))))
        (setv key-suffix (+ "/" (str name) "/" (str lname)))
        (setv key-expr `(+ __name__ ~key-suffix))
        (setv key-var (hy.models.Symbol (+ "_lazy_" (str lname) "_key")))
        (setv cached-var (hy.models.Symbol (+ "_lazy_" (str lname) "_cached")))
        (setv val-var (hy.models.Symbol (+ "_lazy_" (str lname) "_val")))
        ;; init body: all forms except last are setup, last is value
        (setv init-setup (list (cut lbody 0 -1)))
        (setv init-value (get lbody -1))
        (.append lazy-init-forms `(setv ~key-var ~key-expr))
        (.append lazy-init-forms `(<- ~cached-var (Get ~key-var)))
        (.append lazy-init-forms
          `(if (isinstance ~cached-var Some)
               (setv ~(hy.models.Symbol (str lname)) (. ~cached-var value))
               (do
                 ~@init-setup
                 (setv ~val-var ~init-value)
                 (<- (Put ~key-var (Some ~val-var)))
                 (setv ~(hy.models.Symbol (str lname)) ~val-var)))))))
  ;; Prepend lazy init to body
  (setv real-body (+ lazy-init-forms body-without-lazy))
  ;; Expand bangs in the real body
  (setv expanded-forms [])
  (for [form real-body]
    (if (_is-bind form)
        (let [#(nm expr) (_bind-parts form)
              #(inner-bindings rewritten) (_expand-bangs expr)]
          (.extend expanded-forms inner-bindings)
          (if (is nm None)
              (.append expanded-forms `(<- ~rewritten))
              (.append expanded-forms `(<- ~nm ~rewritten))))
        (let [#(inner-bindings rewritten) (_expand-bangs form)]
          (.extend expanded-forms inner-bindings)
          (.append expanded-forms rewritten))))
  (setv fn-form (_build-fn-with-contracts ['_doeff_do] name params pre-checks post-checks expanded-forms))
  ;; Extra imports for lazy
  (setv lazy-imports
    (if lazy-defs
        `(do (import doeff [Some])
             (import doeff_core_effects.effects [Get Put]))
        `(do)))
  `(do
     (import doeff.do [do :as _doeff_do])
     ~lazy-imports
     ~fn-form
     (setv (. ~name __doeff_body__) '~real-body)
     (setv (. ~name __doeff_args__) '~params)
     (setv (. ~name __doeff_name__) ~(str name))))


;; ---------------------------------------------------------------------------
;; fnk — anonymous kleisli (lambda that returns DoExpr)
;; ---------------------------------------------------------------------------

(defmacro fnk [params #* body]
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
  ;; Expand bangs in the body (same logic as defk)
  (setv expanded-forms [])
  (for [form body]
    (if (_is-bind form)
        (let [#(nm expr) (_bind-parts form)
              #(inner-bindings rewritten) (_expand-bangs expr)]
          (.extend expanded-forms inner-bindings)
          (if (is nm None)
              (.append expanded-forms `(<- ~rewritten))
              (.append expanded-forms `(<- ~nm ~rewritten))))
        (let [#(inner-bindings rewritten) (_expand-bangs form)]
          (.extend expanded-forms inner-bindings)
          (.append expanded-forms rewritten))))
  `(do (import doeff.do [do :as _doeff_do])
       (fn [~@params] ((_doeff_do (fn [] (do ~@expanded-forms)))))))


;; ---------------------------------------------------------------------------
;; do! — effectful let (returns a Program)
;; ---------------------------------------------------------------------------

(defmacro do! [#* forms]
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
   expands (! expr) into (<- _tmp expr) bindings.

   Supports :pre/:post contracts with (: name Type) shorthand:

   (do!
     {:pre [(: url str)]
      :post [(: % dict)]}
     (<- resp (http-get url))
     (.json resp))"
  (setv #(pre-checks post-checks real-forms) (_extract-contracts forms))
  ;; Expand bangs in each form (same logic as defk)
  (setv expanded-forms [])
  (for [form real-forms]
    (if (_is-bind form)
        (let [#(nm expr) (_bind-parts form)
              #(inner-bindings rewritten) (_expand-bangs expr)]
          (.extend expanded-forms inner-bindings)
          (if (is nm None)
              (.append expanded-forms `(<- ~rewritten))
              (.append expanded-forms `(<- ~nm ~rewritten))))
        (let [#(inner-bindings rewritten) (_expand-bangs form)]
          (.extend expanded-forms inner-bindings)
          (.append expanded-forms rewritten))))
  (setv #(bindings body-expr) (_parse-do-body expanded-forms "do!"))
  (setv pre-code (lfor check (or pre-checks [])
                   (_expand-check check "do!" "pre-condition"))
        expanded (lfor bind bindings
                   (if (and (isinstance bind tuple) (= (get bind 0) "__plain__"))
                       ;; Plain statement (setv, when, for, etc.) — emit as-is
                       (get bind 1)
                       ;; Effect binding — yield
                       (let [#(name expr) (_bind-parts bind)]
                         (if (is name None)
                             `(yield ~expr)
                             `(setv ~name (yield ~expr)))))))
  (if post-checks
      (let [post-asserts (lfor check post-checks
                           (_expand-check check "do!" "post-condition"))]
        `(do (import doeff.do [do :as _doeff-do])
             ((_doeff-do (fn []
               ~@pre-code
               ~@expanded
               (setv _contract_result ~body-expr)
               (let [% _contract_result]
                 ~@post-asserts)
               (return _contract_result))))))
      `(do (import doeff.do [do :as _doeff-do])
           ((_doeff-do (fn []
             ~@pre-code
             ~@expanded
             (return ~body-expr)))))))


;; ---------------------------------------------------------------------------
;; <- — perform effect with optional type contract
;; ---------------------------------------------------------------------------

(defmacro <- [#* args]
  "Perform an effect. Bind result if name given, optional type contract.
   (<- x (Ask \"key\"))              → (setv x (yield (Ask \"key\")))
   (<- x Type (Ask \"key\"))         → bind + isinstance assertion
   (<- (slog ...))                   → (yield (slog ...))"
  (cond
    (= (len args) 1) `(yield ~(get args 0))
    (= (len args) 2) `(setv ~(get args 0) (yield ~(get args 1)))
    (= (len args) 3) (let [nm (get args 0)
                           tp (get args 1)
                           expr (get args 2)]
                       `(do
                          (setv ~nm (yield ~expr))
                          (assert (isinstance ~nm ~tp)
                                  ~(+ "expected " (str tp) ", got " (str nm)))))))


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
  "Extract (name, expr) from (<- name expr) or (<- name Type expr).
   For (<- expr), returns (None, expr).
   For (<- name Type expr), the Type is ignored here (handled by <- macro)."
  (cond
    (= (len form) 2) #(None (get form 1))
    (= (len form) 3) #((get form 1) (get form 2))
    (= (len form) 4) #((get form 1) (get form 3))))


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

(defn _gen-traverse-body [bindings body-expr]
  "Generate the CPS-converted body for traverse/for-do.
   Finds Iterate/From bindings and nests Traverse effects.
   Recognizes (When pred) as a guard — emits Skip when falsy.
   Non-Iterate bindings become yield expressions inside the inner defk."
  (if (not bindings)
      body-expr
      (let [bind (get bindings 0)
            rest (cut bindings 1 None)]
        ;; Check if this binding is a (When ...) guard
        (if (_is-when bind)
            (let [pred-expr (get bind 1)
                  ;; Expand bangs: (When (! (validate x))) →
                  ;; [(<- _bang_N (validate x))] + rewritten-pred = _bang_N
                  #(bang-bindings rewritten-pred) (_expand-bangs pred-expr)]
              (if bang-bindings
                  ;; Splice bang binds before rewritten When, recurse
                  (let [new-when (hy.models.Expression
                                   [(hy.models.Symbol "When") rewritten-pred])
                        new-bindings (+ (list bang-bindings) [new-when] (list rest))]
                    (_gen-traverse-body new-bindings body-expr))
                  ;; No bangs — emit guard directly
                  (let [inner (_gen-traverse-body rest body-expr)]
                    `(do
                       (when (not ~rewritten-pred)
                         (yield (_doeff_traverse_Skip)))
                       ~inner))))
            ;; Regular binding
            (let [#(name expr) (_bind-parts bind)]
              (if (_is-iterate expr)
                  ;; CPS: wrap rest + body into a defk, emit Traverse effect
                  ;; NOTE: does NOT yield — the outer <- / defk handles yield
                  (let [items (_iterate-arg expr)
                        ;; Extract optional :label from (Iterate items :label "name")
                        label (if (>= (len expr) 4) (get expr 3) None)
                        inner-body (_gen-traverse-body rest body-expr)
                        param (if (is name None)
                                  (hy.models.Symbol "_unused")
                                  name)]
                    (if (is-not label None)
                        `(_doeff_traverse_Traverse
                           (fn [~param] ((_doeff_do (fn [] (do ~inner-body)))))
                           ~items
                           :label ~label)
                        `(_doeff_traverse_Traverse
                           (fn [~param] ((_doeff_do (fn [] (do ~inner-body)))))
                           ~items)))
                  ;; Non-Iterate: regular bind
                  (let [inner (_gen-traverse-body rest body-expr)]
                    (if (is name None)
                        `(do (yield ~expr) ~inner)
                        `(do (setv ~name (yield ~expr)) ~inner)))))))))

(defmacro traverse [#* forms]
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
  (_gen-traverse-body bindings body-expr))


(defmacro for/do [#* forms]
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

   Requires:
     (import doeff [do :as _doeff-do])
     (import doeff_traverse [Traverse :as _doeff_traverse_Traverse])
     (import doeff_traverse [Skip :as _doeff_traverse_Skip])"
  (setv #(bindings body-expr) (_parse-do-body forms "for/do"))
  (_gen-traverse-body bindings body-expr))


;; ---------------------------------------------------------------------------
;; Internal: ! (bang) inline bind expansion
;; ---------------------------------------------------------------------------

(setv _bang-counter 0)

(defn _fresh-tmp []
  "Generate a fresh temporary variable name for bang expansion."
  (global _bang-counter)
  (setv _bang-counter (+ _bang-counter 1))
  (hy.models.Symbol (+ "_bang_" (str _bang-counter))))

(defn _is-bang [form]
  "Check if form is (! expr)."
  (and (isinstance form hy.models.Expression)
       (>= (len form) 2)
       (isinstance (get form 0) hy.models.Symbol)
       (= (str (get form 0)) "!")))

(defn _is-let [form]
  "Check if form is a (let [...] body...) expression."
  (and (isinstance form hy.models.Expression)
       (> (len form) 0)
       (isinstance (get form 0) hy.models.Symbol)
       (= (str (get form 0)) "let")))

(defn _is-comprehension [form]
  "Check if form is (for/do ...) or (traverse ...) — these have
   their own scope and handle bangs internally."
  (and (isinstance form hy.models.Expression)
       (> (len form) 0)
       (isinstance (get form 0) hy.models.Symbol)
       (in (str (get form 0)) #{"for/do" "traverse"})))

(defn _expand-bangs [form]
  "Walk an expression, extracting all (! expr) into (<- tmp expr) bindings.
   Returns #(bindings rewritten-form).

   let forms are handled specially: bang bindings from within a let body
   are kept inside the let (not hoisted out), so that let-bound variables
   remain in scope.

   Comprehension forms (for/do, traverse) are opaque — bangs inside
   them are NOT hoisted, because those macros introduce their own scope
   (From bindings) and will expand bangs themselves."
  (setv bindings [])

  (defn walk [node]
    (cond
      (_is-bang node)
        (let [tmp (_fresh-tmp)
              inner (get node 1)
              #(inner-bindings rewritten-inner) (_expand-bangs inner)]
          (.extend bindings inner-bindings)
          (.append bindings `(<- ~tmp ~rewritten-inner))
          tmp)

      (_is-let node)
        (let [let-bindings (get node 1)
              let-body (cut node 2 None)
              new-body []]
          (for [body-form let-body]
            (setv #(inner-binds rewritten) (_expand-bangs body-form))
            (.extend new-body inner-binds)
            (.append new-body rewritten))
          (hy.models.Expression
            [(get node 0) let-bindings #* new-body]))

      ;; Don't walk into comprehension forms — they have their own scope
      (_is-comprehension node)
        node

      (isinstance node hy.models.Expression)
        (hy.models.Expression (lfor child node (walk child)))

      (isinstance node hy.models.List)
        (hy.models.List (lfor child node (walk child)))

      True node))

  (setv result (walk form))
  #(bindings result))


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

(defn _build-defp [macro-name name body * [program-return-mode "reject"]]
  "Shared implementation for defp/defpp.
   program-return-mode: 'reject' (defp) | 'require' (defpp)"
  (_enforce-no-defp-in-hyk macro-name name)
  (setv #(pre-checks post-checks real-body) (_extract-contracts body))
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
  ;; Inline do! expansion: bang-expand, parse, emit generator with contracts
  (setv expanded-forms [])
  (for [form real-body]
    (if (_is-bind form)
        (let [#(nm expr) (_bind-parts form)
              #(inner-bindings rewritten) (_expand-bangs expr)]
          (.extend expanded-forms inner-bindings)
          (if (is nm None)
              (.append expanded-forms `(<- ~rewritten))
              (.append expanded-forms `(<- ~nm ~rewritten))))
        (let [#(inner-bindings rewritten) (_expand-bangs form)]
          (.extend expanded-forms inner-bindings)
          (.append expanded-forms rewritten))))
  (setv #(bindings body-expr) (_parse-do-body expanded-forms macro-name))
  (setv expanded (lfor bind bindings
                   (if (and (isinstance bind tuple) (= (get bind 0) "__plain__"))
                       ;; Plain statement (import, setv, when, etc.) — emit as-is
                       (get bind 1)
                       ;; Effect binding — yield
                       (let [#(bname expr) (_bind-parts bind)]
                         (if (is bname None)
                             `(yield ~expr)
                             `(setv ~bname (yield ~expr)))))))
  (setv post-asserts (lfor check post-checks
                       (_expand-check check name "post-condition")))
  `(do
     (import inspect)
     (import doeff.do [do :as _doeff_do])
     (defn _doeff_check_program_return [v msg mode]
       "Check Program return value. Raises TypeError on violation. Returns False (for assert)."
       (setv is-program (inspect.isgenerator v))
       (when (and (= mode "reject") is-program)
         (raise (TypeError msg)))
       (when (and (= mode "require") (not is-program))
         (raise (TypeError msg)))
       False)
     (setv ~name ((_doeff_do (fn []
       ~@expanded
       (setv _contract_result ~body-expr)
       (let [% _contract_result]
         ~@post-asserts)
       (return _contract_result)))))
     ;; Preserve S-expr body directly on Program value (DoExpr has __dict__ via pyclass(dict))
     (setv (. ~name __doeff_body__) '~real-body)
     (setv (. ~name __doeff_name__) ~(str name))
     (setv (. ~name __doeff_module__) __name__)))

(defmacro defp [name #* body]
  "Define a Program[T] constant. Errors if the return value is itself a Program.
   :post is REQUIRED. Use defpp if Program[Program[T]] is intended.

   (defp my-pipeline
     {:post [(: % ExportResult)]}
     (<- data (load-data))
     (<- result (process data))
     result)"
  (_build-defp "defp" name body))

(defmacro defpp [name #* body]
  "Define a Program[Program[T]] constant. Errors if return is NOT a Program.
   :post is REQUIRED.

   (defpp my-meta-program
     {:post [(inspect.isgenerator %)]}
     (<- config (load-config))
     (build-pipeline config))"
  (_build-defp "defpp" name body :program-return-mode "require"))

(defmacro defprogram [name #* body]
  "REMOVED: use defp (or defpp for Program[Program[T]])."
  (raise (SyntaxError (.format "
defprogram is removed. Use defp instead.

  Replace:  (defprogram {name} ...)
  With:     (defp {name} ...)
" :name name))))


;; ---------------------------------------------------------------------------
;; deftest — effectful test that expands to pytest function
;; ---------------------------------------------------------------------------

(defn _extract-test-meta [body]
  "Parse optional test metadata dict from front of body.
   Supported keys: :interpreters, :params, :env, :marks, :skip-if, :skip-reason.
   Returns #(interpreters params-dict env-dict marks skip-if skip-reason real-body).
   Skips leading docstring if present."
  (setv interpreters None
        params-dict None
        env-dict None
        marks None
        skip-if-expr None
        skip-reason None
        real-body body)
  (setv meta-idx None)
  (for [#(i form) (enumerate body)]
    (when (isinstance form hy.models.Dict)
      (setv meta-idx i)
      (break))
    ;; Skip string literals (docstrings) at the start
    (when (not (isinstance form hy.models.String))
      (break)))
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
        (setv skip-reason v))))
  #(interpreters params-dict env-dict marks skip-if-expr skip-reason real-body))

(defmacro deftest [name #* args]
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

   Expansion: generates def test_*(doeff_interpreter, ...fixtures...)
   that creates a DoExpr program and passes it to the interpreter."
  ;; Parse optional params list and body
  (setv fixture-params []
        body args)
  (when (and (> (len args) 0) (isinstance (get args 0) hy.models.List))
    (setv fixture-params (list (get args 0))
          body (cut args 1 None)))

  ;; Parse optional metadata dict
  (setv #(interpreters params-dict env-dict marks skip-if-expr skip-reason real-body)
    (_extract-test-meta body))

  ;; Expand bangs in the body (same as defk/defp)
  (setv expanded-forms [])
  (for [form real-body]
    (if (_is-bind form)
        (let [#(nm expr) (_bind-parts form)
              #(inner-bindings rewritten) (_expand-bangs expr)]
          (.extend expanded-forms inner-bindings)
          (if (is nm None)
              (.append expanded-forms `(<- ~rewritten))
              (.append expanded-forms `(<- ~nm ~rewritten))))
        (let [#(inner-bindings rewritten) (_expand-bangs form)]
          (.extend expanded-forms inner-bindings)
          (.append expanded-forms rewritten))))

  ;; Build the generator body: convert <- to yield, plain forms as-is
  (setv gen-body [])
  (for [form expanded-forms]
    (cond
      ;; (<- name expr) → (setv name (yield expr))
      (and (_is-bind form) (is-not (_bind-parts form) None))
      (let [#(bname expr) (_bind-parts form)]
        (if (is bname None)
            (.append gen-body `(yield ~expr))
            (.append gen-body `(setv ~bname (yield ~expr)))))
      ;; Everything else (assert, setv, when, for, print, etc.) — pass through
      True
      (.append gen-body form)))

  ;; Build the test function
  (setv fn-params (+ [(hy.models.Symbol "doeff_interpreter")] fixture-params))

  ;; Build the program creation + interpreter call
  (setv fn-body
    (if (is-not env-dict None)
      `(doeff_interpreter
         ((_doeff_do (fn [] ~@gen-body)))
         :env ~env-dict)
      `(doeff_interpreter
         ((_doeff_do (fn [] ~@gen-body))))))

  ;; Build the parametrize decorators
  (setv decorators [])

  ;; :interpreters → @pytest.mark.parametrize("doeff_interpreter_name", [...])
  (when (is-not interpreters None)
    (.append decorators
      `(.parametrize (. pytest mark) "doeff_interpreter_name"
         ~(hy.models.List interpreters))))

  ;; :params → @pytest.mark.parametrize for each key
  (when (is-not params-dict None)
    (for [#(k v) (zip (cut params-dict None None 2) (cut params-dict 1 None 2))]
      (setv param-name (if (isinstance k hy.models.String) (str k) (str k)))
      (.append decorators
        `(.parametrize (. pytest mark) ~(hy.models.String param-name)
           ~v))))

  ;; :marks → @pytest.mark.<name> for each mark
  (when (is-not marks None)
    (for [m marks]
      (setv mark-name (if (isinstance m hy.models.String) (str m) (str m)))
      (.append decorators
        `(. (. pytest mark) ~(hy.models.Symbol mark-name)))))

  ;; :skip-if → @pytest.mark.skipif(condition, reason=...)
  (when (is-not skip-if-expr None)
    (setv reason (if (is-not skip-reason None) skip-reason
                     (hy.models.String "skip condition met")))
    (.append decorators
      `(.skipif (. pytest mark) ~skip-if-expr :reason ~reason)))

  ;; Assemble the function definition with decorators
  (if decorators
    `(do
       (import pytest)
       (import doeff.do [do :as _doeff_do])
       (defn [~@decorators] ~name [~@fn-params] ~fn-body))
    `(do
       (import doeff.do [do :as _doeff_do])
       (defn ~name [~@fn-params] ~fn-body))))


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

(defmacro defpipeline [pipeline-name #* body]
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

(defmacro <-> [#* forms]
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
;; set! — mutation macro for lazy-var
;; ---------------------------------------------------------------------------

(defmacro set! [name val]
  "Mutate a lazy-var: update local binding and write back to state.

   (set! items (+ items [new-item]))

   Expands to:
     (setv items (+ items [new-item]))
     (<- (Put _lazy_items_key (Some (+ items [new-item]))))

   Requires a lazy-var in scope (the _lazy_{name}_key variable must exist).
   Using set! on a lazy-val raises a compile-time SyntaxError in defhandler/defk."
  (setv key-var (hy.models.Symbol (+ "_lazy_" (str name) "_key")))
  `(do
     (setv ~name ~val)
     (<- (Put ~key-var (Some ~name)))))
