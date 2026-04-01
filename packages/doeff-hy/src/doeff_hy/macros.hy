;;; doeff-hy standard macros — effect composition for doeff.
;;;
;;; Usage:
;;;   (require doeff-hy.macros [do! defk deff <- ! defprogram
;;;                             do-list do-list-try do-try-list do-dict-try do-try])
;;;   (import doeff [do :as _doeff-do])
;;;
;;; Core effects are imported from doeff_core_effects:
;;;   (import doeff_core_effects [Ask Try slog])

;; ---------------------------------------------------------------------------
;; Internal: contract extraction
;; ---------------------------------------------------------------------------

(defn _extract-contracts [body]
  "Parse optional {:pre [...] :post [...]} from front of body forms.
   Returns #(pre-checks post-checks real-body)."
  (setv pre-checks []
        post-checks []
        real-body body)
  (when (and (> (len body) 0) (isinstance (get body 0) hy.models.Dict))
    (setv contract (get body 0)
          real-body (cut body 1 None))
    (for [#(k v) (zip (cut contract None None 2) (cut contract 1 None 2))]
      (when (= (str k) ":pre")
        (setv pre-checks (list v)))
      (when (= (str k) ":post")
        (setv post-checks (list v)))))
  #(pre-checks post-checks real-body))

(defn _build-fn-with-contracts [decorators name params pre-checks post-checks real-body]
  "Build a defn form with pre/post assertion wrappers."
  (setv pre-code (lfor check pre-checks
                   `(assert ~check ~(+ "pre-condition failed: " (str check)))))
  (if post-checks
      (let [post-asserts (lfor check post-checks
                           `(assert ~check ~(+ "post-condition failed: " (str check))))
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

(defn _build-kleisli-with-contracts [decorators name params pre-checks post-checks real-body]
  "Build a defn form with pre/post contracts, generator-safe for kleisli functions.
   Unlike _build-fn-with-contracts, this emits body forms sequentially and captures
   only the last form as the result — (do ...) wrapping would break yield-based
   effect bindings (<-)."
  (setv pre-code (lfor check pre-checks
                   `(assert ~check ~(+ "pre-condition failed: " (str check)))))
  (if post-checks
      (let [post-asserts (lfor check post-checks
                           `(assert ~check ~(+ "post-condition failed: " (str check))))
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
  "Define a function with optional :pre/:post contracts.

   (deff my-fn [x y]
     {:pre [(isinstance x int) (isinstance y str)]
      :post [(isinstance % list)]}
     (list (range x)))

   :pre  — assertions checked at function entry
   :post — assertions checked on return value (% binds to result)
   Both are optional."
  (setv #(pre-checks post-checks real-body) (_extract-contracts body))
  (_build-fn-with-contracts [] name params pre-checks post-checks real-body))


;; ---------------------------------------------------------------------------
;; defk — kleisli with :pre/:post contracts + bang expansion
;; ---------------------------------------------------------------------------

(defmacro defk [name params #* body]
  "Define a kleisli function (@do decorator) with optional :pre/:post contracts.
   Supports ! (bang) inline bind: (! expr) is expanded to (<- _tmp expr).
   Requires (import doeff [do :as _doeff-do]) in the calling module.

   (defk my-fn [x y]
     {:pre [(isinstance x Asset)]
      :post [(isinstance % SegmentSelection)]}
     (<- result (some-effect))
     (return result))

   With bang:
   (defk my-fn [x y]
     (k1 (! (k2 x)) (! (k3 y))))"
  (setv #(pre-checks post-checks real-body) (_extract-contracts body))
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
  (_build-kleisli-with-contracts ['_doeff_do] name params pre-checks post-checks expanded-forms))


;; ---------------------------------------------------------------------------
;; fnk — anonymous kleisli (lambda that returns DoExpr)
;; ---------------------------------------------------------------------------

(defmacro fnk [params #* body]
  "Anonymous kleisli function. Like fn but returns a DoExpr.
   Can perform effects with <- inside.
   Requires (import doeff [do :as _doeff-do]) in the calling module.

   (fnk [x] (* x 2))

   With effects:
   (fnk [acc x]
     (<- enriched (enrich x))
     (+ acc enriched))"
  `(fn [~@params] ((_doeff_do (fn [] (do ~@body))))))


;; ---------------------------------------------------------------------------
;; do! — monadic do block (inline effect sequencing)
;; ---------------------------------------------------------------------------

(defmacro do! [#* forms]
  "Monadic do block — sequence effect bindings and return the final expression.
   Use inside a defk body or anywhere a generator context is active.

   (do!
     (<- x (some-effect))
     (<- y (another-effect x))
     (+ x y))

   Supports :pre/:post contracts (Clojure-style):

   (do!
     {:pre [(isinstance url str)]
      :post [(isinstance % dict)]}
     (<- resp (http-get url))
     (.json resp))"
  (setv #(pre-checks post-checks real-forms) (_extract-contracts forms))
  (setv #(bindings body-expr) (_parse-do-body real-forms))
  (setv pre-code (lfor check pre-checks
                   `(assert ~check ~(+ "pre-condition failed: " (str check))))
        expanded (lfor bind bindings
                   (let [#(name expr) (_bind-parts bind)]
                     (if (is name None)
                         `(yield ~expr)
                         `(setv ~name (yield ~expr))))))
  (if post-checks
      (let [post-asserts (lfor check post-checks
                           `(assert ~check ~(+ "post-condition failed: " (str check))))]
        `(do
           ~@pre-code
           ~@expanded
           (setv _contract_result ~body-expr)
           (let [% _contract_result]
             ~@post-asserts)
           _contract_result))
      `(do
         ~@pre-code
         ~@expanded
         ~body-expr)))


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
  "Check if expr is (Iterate ...)."
  (and (isinstance expr hy.models.Expression)
       (> (len expr) 0)
       (= (str (get expr 0)) "Iterate")))

(defn _is-try [expr]
  "Check if expr is (Try ...)."
  (and (isinstance expr hy.models.Expression)
       (> (len expr) 0)
       (= (str (get expr 0)) "Try")))

(defn _iterate-arg [expr]
  "Extract the items arg from (Iterate items)."
  (get expr 1))

(defn _try-arg [expr]
  "Extract the program arg from (Try program)."
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
;; Internal: code generation for do-* blocks
;; ---------------------------------------------------------------------------

(defn _gen-do-list [bindings body-expr]
  "Generate code for do-list: list[T]."
  (if (not bindings)
      `(.append _acc ~body-expr)
      (let [bind (get bindings 0)
            rest (cut bindings 1 None)
            #(name expr) (_bind-parts bind)]
        (if (_is-iterate expr)
            (let [items (_iterate-arg expr)]
              `(for [~name ~items]
                 ~(_gen-do-list rest body-expr)))
            (if (is name None)
                `(do (yield ~expr)
                     ~(_gen-do-list rest body-expr))
                `(do (setv ~name (yield ~expr))
                     ~(_gen-do-list rest body-expr)))))))

(defn _gen-do-list-try [bindings body-expr]
  "Generate code for do-list-try: list[Result[T]]."
  (if (not bindings)
      `(.append _acc (_Ok ~body-expr))
      (let [bind (get bindings 0)
            rest (cut bindings 1 None)
            #(name expr) (_bind-parts bind)]
        (cond
          (_is-iterate expr)
            (let [items (_iterate-arg expr)]
              `(for [~name ~items]
                 ~(_gen-do-list-try rest body-expr)))
          (_is-try expr)
            (let [program (_try-arg expr)]
              `(try
                 (setv ~name (yield ~program))
                 ~(_gen-do-list-try rest body-expr)
                 (except [_e Exception]
                   (.append _acc (_Err _e)))))
          True
            (if (is name None)
                `(do (yield ~expr)
                     ~(_gen-do-list-try rest body-expr))
                `(do (setv ~name (yield ~expr))
                     ~(_gen-do-list-try rest body-expr)))))))

(defn _gen-do-try-list [bindings body-expr]
  "Generate code for do-try-list: Result[list[T]]."
  (if (not bindings)
      `(.append _acc ~body-expr)
      (let [bind (get bindings 0)
            rest (cut bindings 1 None)
            #(name expr) (_bind-parts bind)]
        (cond
          (_is-iterate expr)
            (let [items (_iterate-arg expr)]
              `(for [~name ~items]
                 ~(_gen-do-try-list rest body-expr)))
          (_is-try expr)
            (let [program (_try-arg expr)]
              `(do (setv ~name (yield ~program))
                   ~(_gen-do-try-list rest body-expr)))
          True
            (if (is name None)
                `(do (yield ~expr)
                     ~(_gen-do-try-list rest body-expr))
                `(do (setv ~name (yield ~expr))
                     ~(_gen-do-try-list rest body-expr)))))))

(defn _gen-do-dict-try [bindings body-expr]
  "Generate code for do-dict-try: dict[K,V] (skip errors)."
  (if (not bindings)
      `(setv (get _acc (get ~body-expr 0)) (get ~body-expr 1))
      (let [bind (get bindings 0)
            rest (cut bindings 1 None)
            #(name expr) (_bind-parts bind)]
        (cond
          (_is-iterate expr)
            (let [items (_iterate-arg expr)]
              `(for [~name ~items]
                 ~(_gen-do-dict-try rest body-expr)))
          (_is-try expr)
            (let [program (_try-arg expr)]
              `(try
                 (setv ~name (yield ~program))
                 ~(_gen-do-dict-try rest body-expr)
                 (except [_e Exception] None)))
          True
            (if (is name None)
                `(do (yield ~expr)
                     ~(_gen-do-dict-try rest body-expr))
                `(do (setv ~name (yield ~expr))
                     ~(_gen-do-dict-try rest body-expr)))))))


;; ---------------------------------------------------------------------------
;; Internal: parse do-* body
;; ---------------------------------------------------------------------------

(defn _parse-do-body [forms]
  "Split forms into (bindings, body-expr).
   All (<- ...) forms are bindings. Last non-binding form is body."
  (setv bindings []
        body-expr None)
  (for [form forms]
    (if (_is-bind form)
        (.append bindings form)
        (setv body-expr form)))
  (when (is body-expr None)
    (raise (SyntaxError "do-* block must have a body expression")))
  #(bindings body-expr))


;; ---------------------------------------------------------------------------
;; do-list — list[T]
;; ---------------------------------------------------------------------------

(defmacro do-list [#* forms]
  "Monadic do block returning list[T].
   Iterate → for loop. Other <- → yield.

   (do-list
     (<- x (Iterate items))
     (<- y (some-effect x))
     [x y])"
  (setv #(bindings body-expr) (_parse-do-body forms))
  `(do
     (setv _acc [])
     ~(_gen-do-list bindings body-expr)
     _acc))


;; ---------------------------------------------------------------------------
;; do-list-try — list[Result[T]]
;; ---------------------------------------------------------------------------

(defmacro do-list-try [#* forms]
  "Monadic do block returning list[Result[T]].
   Iterate → for loop. Try → per-element error handling.

   (do-list-try
     (<- event (Iterate events))
     (<- batch (Try (rank event)))
     [event batch])"
  (setv #(bindings body-expr) (_parse-do-body forms))
  (setv _Ok (hy.models.Symbol "_Ok")
        _Err (hy.models.Symbol "_Err"))
  `(do
     (import doeff [Ok :as ~_Ok Err :as ~_Err])
     (setv _acc [])
     ~(_gen-do-list-try bindings body-expr)
     _acc))


;; ---------------------------------------------------------------------------
;; do-try-list — Result[list[T]]
;; ---------------------------------------------------------------------------

(defmacro do-try-list [#* forms]
  "Monadic do block returning Result[list[T]].
   Any error aborts entire list.

   (do-try-list
     (<- event (Iterate events))
     (<- batch (Try (rank event)))
     [event batch])"
  (setv #(bindings body-expr) (_parse-do-body forms))
  (setv _Ok (hy.models.Symbol "_Ok")
        _Err (hy.models.Symbol "_Err"))
  `(do
     (import doeff [Ok :as ~_Ok Err :as ~_Err])
     (try
       (do
         (setv _acc [])
         ~(_gen-do-try-list bindings body-expr)
         (_Ok _acc))
       (except [_e Exception]
         (_Err _e)))))


;; ---------------------------------------------------------------------------
;; do-dict-try — dict[K,V] (skip errors)
;; ---------------------------------------------------------------------------

(defmacro do-dict-try [#* forms]
  "Monadic do block returning dict[K,V], skipping errors.
   Body must be [key value].

   (do-dict-try
     (<- symbol (Iterate universe))
     (<- frame (Try (fetch-price symbol)))
     [symbol frame])"
  (setv #(bindings body-expr) (_parse-do-body forms))
  `(do
     (setv _acc {})
     ~(_gen-do-dict-try bindings body-expr)
     _acc))


;; ---------------------------------------------------------------------------
;; do-try — Result[T]
;; ---------------------------------------------------------------------------

(defmacro do-try [#* forms]
  "Monadic do block returning Result[T].

   (do-try
     (<- x (Try (validate input)))
     (<- y (process x))
     y)"
  (setv #(bindings body-expr) (_parse-do-body forms))
  (setv _Ok (hy.models.Symbol "_Ok")
        _Err (hy.models.Symbol "_Err"))
  `(do
     (import doeff [Ok :as ~_Ok Err :as ~_Err])
     (try
       (do
         ~@(lfor bind bindings
             (let [#(name expr) (_bind-parts bind)]
               (if (_is-try expr)
                   (let [program (_try-arg expr)]
                     (if (is name None)
                         `(yield ~program)
                         `(setv ~name (yield ~program))))
                   (if (is name None)
                       `(yield ~expr)
                       `(setv ~name (yield ~expr))))))
         (_Ok ~body-expr))
       (except [_e Exception]
         (_Err _e)))))


;; ---------------------------------------------------------------------------
;; traverse — applicative traverse as effect (CPS-converted Iterate)
;; ---------------------------------------------------------------------------

(defn _gen-traverse-body [bindings body-expr]
  "Generate the CPS-converted body for traverse.
   Finds Iterate bindings and nests Traverse effects.
   Non-Iterate bindings become yield expressions inside the inner defk."
  (if (not bindings)
      body-expr
      (let [bind (get bindings 0)
            rest (cut bindings 1 None)
            #(name expr) (_bind-parts bind)]
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
                  `(do (setv ~name (yield ~expr)) ~inner)))))))

(defmacro traverse [#* forms]
  "Applicative traverse — batch processing with handler-injected strategy.
   Replaces do-list/do-list-try/do-try-list/do-dict-try.

   Iterate bindings are CPS-converted into Traverse effects.
   The handler decides execution order (sequential/parallel)
   and failure strategy (fail-fast/run-all).

   (traverse
     (<- x (Iterate items))
     (<- y (some-effect x))
     [x y])

   With label for per-stage history:
   (traverse
     (<- x (Iterate items :label \"extract\"))
     (<- y (extract x))
     y)

   Requires:
     (import doeff [do :as _doeff-do])
     (import doeff_traverse [Traverse :as _doeff_traverse_Traverse])"
  (setv #(bindings body-expr) (_parse-do-body forms))
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

(defn _expand-bangs [form]
  "Walk an expression, extracting all (! expr) into (<- tmp expr) bindings.
   Returns #(bindings rewritten-form).

   let forms are handled specially: bang bindings from within a let body
   are kept inside the let (not hoisted out), so that let-bound variables
   remain in scope."
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

      (isinstance node hy.models.Expression)
        (hy.models.Expression (lfor child node (walk child)))

      (isinstance node hy.models.List)
        (hy.models.List (lfor child node (walk child)))

      True node))

  (setv result (walk form))
  #(bindings result))


;; ---------------------------------------------------------------------------
;; defprogram — define a Program constant with implicit do context
;; ---------------------------------------------------------------------------

(defmacro defprogram [name #* body]
  "Define a Program constant. Body is an implicit do-block where
   ! (bang) inline bind and <- are available.

   (defprogram my-pipeline
     (process (! (load-data :path \"data.csv\"))))

   Multi-form bodies with <- also work:

   (defprogram my-pipeline
     (<- data (load-data :path \"data.csv\"))
     (<- result (process data))
     (export result))"
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
  (setv factory-name (hy.models.Symbol (+ "_" (str name) "_factory")))
  `(do
     (defk ~factory-name []
       ~@expanded-forms)
     (setv ~name (~factory-name))))
