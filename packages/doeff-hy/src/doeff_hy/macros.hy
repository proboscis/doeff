;;; doeff-hy standard macros — effect composition for doeff.
;;;
;;; Usage:
;;;   (require doeff-hy.macros [do! defk deff fnk <- ! defp defpp defprogram
;;;                             defpipeline traverse for/do])
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

(defn _expand-check [check fn-name phase]
  "Expand a single contract check into an assert form.
   (: x T) → isinstance assert with clear type error message.
   Other  → generic condition assert."
  (if (_is-type-check check)
      (let [target (get check 1)
            tp (get check 2)
            target-label (if (= (str target) "%") "return value" (str target))]
        `(assert (isinstance ~target ~tp)
                 (+ ~(+ (str fn-name) ": " phase " type error: `" target-label "` expected " (str tp) ", got ")
                    (. (type ~target) __name__))))
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
  (_build-fn-with-contracts [] name params pre-checks post-checks real-body))


;; ---------------------------------------------------------------------------
;; defk — kleisli with :pre/:post contracts + bang expansion
;; ---------------------------------------------------------------------------

(defmacro defk [name params #* body]
  "Define a kleisli function (@do decorator) with :pre/:post contracts.
   Supports ! (bang) inline bind: (! expr) is expanded to (<- _tmp expr).
   Requires (import doeff [do :as _doeff-do]) in the calling module.

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
  `(do
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
   Requires (import doeff [do :as _doeff-do]) in the calling module.

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
  `(fn [~@params] ((_doeff_do (fn [] (do ~@expanded-forms))))))


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
                   (let [#(name expr) (_bind-parts bind)]
                     (if (is name None)
                         `(yield ~expr)
                         `(setv ~name (yield ~expr))))))
  (if post-checks
      (let [post-asserts (lfor check post-checks
                           (_expand-check check "do!" "post-condition"))]
        `((fn []
           ~@pre-code
           ~@expanded
           (setv _contract_result ~body-expr)
           (let [% _contract_result]
             ~@post-asserts)
           (return _contract_result))))
      `((fn []
         ~@pre-code
         ~@expanded
         (return ~body-expr)))))


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
   All (<- ...) and (When ...) forms are bindings. Last non-binding form is body."
  (setv bindings []
        body-expr None)
  (for [form forms]
    (cond
      (_is-bind form) (.append bindings form)
      (_is-when form) (.append bindings form)
      True (setv body-expr form)))
  (when (is body-expr None)
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
  "Shared implementation for defp/defpp/defprogram.
   program-return-mode: 'reject' (defp) | 'require' (defpp)"
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
                   (let [#(bname expr) (_bind-parts bind)]
                     (if (is bname None)
                         `(yield ~expr)
                         `(setv ~bname (yield ~expr))))))
  (setv post-asserts (lfor check post-checks
                       (_expand-check check name "post-condition")))
  `(do
     (import inspect)
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
  "DEPRECATED: use defp (or defpp for Program[Program[T]])."
  (import warnings)
  (warnings.warn
    (.format "defprogram is deprecated. Use defp (or defpp for Program[Program[T]]).\n  Replace: (defprogram {name} ...) → (defp {name} ...)"
             :name name)
    DeprecationWarning
    :stacklevel 2)
  (_build-defp "defprogram" name body))


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
