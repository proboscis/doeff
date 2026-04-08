;;; doeff-hy S-expression analysis tools.
;;;
;;; Provides call-graph walk, effect collection, and handler coverage
;;; verification for defk functions that preserve their S-expr body.
;;;
;;; Usage:
;;;   (import doeff_hy.sexpr [body-of args-of collect-effects effect-tree
;;;                           assert-handlers classify-call])

(import hy.models)


;; ---------------------------------------------------------------------------
;; Accessors — read S-expr metadata from defk functions
;; ---------------------------------------------------------------------------

(defn body-of [kleisli]
  "Get the preserved S-expr body of a defk function.
   Returns None if the function has no __doeff_body__ (e.g. plain Python function)."
  (getattr kleisli "__doeff_body__" None))

(defn args-of [kleisli]
  "Get the parameter list of a defk function as an S-expr."
  (getattr kleisli "__doeff_args__" None))

(defn name-of [kleisli]
  "Get the original name of a defk function."
  (getattr kleisli "__doeff_name__" None))


;; ---------------------------------------------------------------------------
;; Helpers — S-expr introspection
;; ---------------------------------------------------------------------------

(defn _mangle [sym]
  "Convert Hy kebab-case symbol to Python snake_case name."
  (.replace (str sym) "-" "_"))

(defn _is-bind [form]
  "Check if form is (<- ...)."
  (and (isinstance form hy.models.Expression)
       (> (len form) 0)
       (= (str (get form 0)) "<-")))

(defn _is-call [form]
  "Check if form is a function/constructor call (expr ...)."
  (and (isinstance form hy.models.Expression)
       (> (len form) 0)
       (isinstance (get form 0) hy.models.Symbol)))

(defn _call-head [form]
  "Get the head symbol of a call expression."
  (get form 0))

(defn _bind-expr [form]
  "Get the expression part of a bind form.
   (<- name expr) → expr.  (<- expr) → expr."
  (cond
    (= (len form) 2) (get form 1)
    (= (len form) 3) (get form 2)
    (= (len form) 4) (get form 3)  ;; (<- name Type expr)
    True None))


;; ---------------------------------------------------------------------------
;; Symbol resolution
;; ---------------------------------------------------------------------------

(defn _resolve [symbol module-globals]
  "Resolve a Hy symbol to a Python object via module globals."
  (setv py-name (_mangle symbol))
  (.get module-globals py-name))

(defn _get-module-globals [fn-obj]
  "Get the globals dict of the module where fn-obj was defined.
   Prefers __module__ + importlib over __globals__ because defk-generated
   inner functions may have a different __globals__ than the defining module."
  ;; First try __module__ via importlib (most reliable for defk)
  (setv mod-name (getattr fn-obj "__module__" None))
  (when mod-name
    (import importlib sys)
    ;; Try sys.modules first (already imported, has current state)
    (setv mod (.get sys.modules mod-name))
    (when mod
      (return (vars mod)))
    ;; Fallback to importlib
    (try
      (return (vars (importlib.import-module mod-name)))
      (except [ImportError] None)))
  ;; Last resort: __globals__
  (when (hasattr fn-obj "__globals__")
    (return fn-obj.__globals__))
  {})

(defn classify-call [symbol module-globals]
  "Classify what a symbol resolves to.
   Returns :kleisli, :effect, :python-fn, or :unknown."
  (setv obj (_resolve symbol module-globals))
  (cond
    (is obj None) :unknown
    (hasattr obj "__doeff_body__") :kleisli
    (and (isinstance obj type)
         (do
           (import doeff [EffectBase])
           (issubclass obj EffectBase))) :effect
    True :python-fn))


;; ---------------------------------------------------------------------------
;; S-expr walking
;; ---------------------------------------------------------------------------

(defn _walk-calls [sexpr]
  "Yield all call expressions found in an S-expr body (shallow — one level of forms)."
  (setv results [])
  (for [form sexpr]
    (cond
      ;; (<- name (call ...)) → extract the call
      (_is-bind form)
        (let [expr (_bind-expr form)]
          (when (and expr (_is-call expr))
            (.append results expr)))
      ;; (call ...) at top level
      (_is-call form)
        (.append results form)))
  results)


;; ---------------------------------------------------------------------------
;; Effect collection — call graph walk
;; ---------------------------------------------------------------------------

(defn collect-effects [fn-obj * [visited None]]
  "Recursively collect all effect types used by a defk function.
   Walks the call graph — each function analyzed in its own module context.
   Returns a set of effect class names (strings)."
  (when (is visited None)
    (setv visited (set)))
  (setv fn-id (id fn-obj))
  (when (in fn-id visited)
    (return (set)))
  (.add visited fn-id)

  (setv body (body-of fn-obj))
  (when (is body None)
    (return (set)))

  (setv g (_get-module-globals fn-obj))
  (when (is g None)
    (return (set)))

  (setv effects (set))
  (for [call (_walk-calls body)]
    (setv sym (_call-head call))
    (setv kind (classify-call sym g))
    (cond
      (= kind :effect)
        (.add effects (str sym))
      (= kind :kleisli)
        (let [callee (_resolve sym g)]
          (.update effects (collect-effects callee :visited visited)))))
  effects)


;; ---------------------------------------------------------------------------
;; Effect tree — structured view
;; ---------------------------------------------------------------------------

(defn effect-tree [fn-obj * [visited None]]
  "Build a dependency tree showing effects at each call level.
   Returns {:name str :effects set :children {name → subtree}}."
  (when (is visited None)
    (setv visited (set)))
  (setv fn-id (id fn-obj))
  (when (in fn-id visited)
    (return {"name" (or (name-of fn-obj) "?") "effects" (set) "children" {} "cycle" True}))
  (.add visited fn-id)

  (setv body (body-of fn-obj))
  (when (is body None)
    (return {"name" (or (name-of fn-obj) "?") "effects" (set) "children" {}}))

  (setv g (_get-module-globals fn-obj))
  (setv direct-effects (set))
  (setv children {})

  (for [call (_walk-calls body)]
    (setv sym (_call-head call))
    (setv kind (classify-call sym g))
    (cond
      (= kind :effect)
        (.add direct-effects (str sym))
      (= kind :kleisli)
        (let [callee (_resolve sym g)]
          (setv (get children (str sym))
                (effect-tree callee :visited visited)))))

  {"name" (or (name-of fn-obj) "?")
   "effects" direct-effects
   "children" children})


;; ---------------------------------------------------------------------------
;; Handler coverage verification
;; ---------------------------------------------------------------------------

(defn assert-handlers [fn-obj handled-effects]
  "Assert all effects used by fn-obj (recursively) have handlers.
   handled-effects: set of effect class name strings.
   Raises AssertionError if unhandled effects exist."
  (setv used (collect-effects fn-obj))
  (setv unhandled (- used (set (lfor e handled-effects (str e)))))
  (when unhandled
    (raise (AssertionError
      (+ "Unhandled effects in "
         (or (name-of fn-obj) "?")
         ": " (str unhandled))))))


;; ---------------------------------------------------------------------------
;; Pretty printing
;; ---------------------------------------------------------------------------

(defn print-effect-tree [tree * [indent 0]]
  "Print an effect tree to stdout."
  (setv prefix (* "  " indent))
  (print (+ prefix (get tree "name")))
  (when (get tree "effects")
    (for [e (sorted (get tree "effects"))]
      (print (+ prefix "  > " e))))
  (when (.get tree "cycle")
    (print (+ prefix "  (cycle)")))
  (for [#(cname child) (sorted (.items (get tree "children")))]
    (print-effect-tree child :indent (+ indent 1))))
