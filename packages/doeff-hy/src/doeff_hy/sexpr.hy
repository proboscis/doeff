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

(defn _bind-var [form]
  "Get the variable name from a bind form. None for (<- expr)."
  (cond
    (= (len form) 2) None
    (>= (len form) 3) (get form 1)))

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

(defn _is-effect-class [obj]
  "Check if obj is an EffectBase subclass."
  (and (isinstance obj type)
       (do (import doeff [EffectBase])
           (issubclass obj EffectBase))))

(defn _is-effect-factory [obj]
  "Check if obj is a function that produces EffectBase instances.
   Safe checks only — no calling the function:
   1. Explicit __doeff_effect__ marker
   2. Return annotation is an EffectBase subclass (type, not string)"
  (when (not (callable obj)) (return False))
  ;; 1. Explicit marker (preferred — set by decorator or manually)
  (when (getattr obj "__doeff_effect__" False) (return True))
  ;; 2. Return annotation is EffectBase subclass
  (try
    (do
      (import doeff [EffectBase])
      (import inspect)
      (setv hints (inspect.get-annotations obj))
      (setv ret (.get hints "return"))
      (when (and ret (isinstance ret type) (issubclass ret EffectBase))
        (return True)))
    (except [Exception] None))
  False)

(defn classify-call [symbol module-globals * [extra-effects None]]
  "Classify what a symbol resolves to.
   Returns :kleisli, :effect, :effect-factory, :python-fn, or :unknown.

   extra-effects: set of symbol name strings to treat as effects regardless
   of introspection. Specify at call site for factories without markers."
  (when (and extra-effects (in (str symbol) extra-effects))
    (return :effect))
  (setv obj (_resolve symbol module-globals))
  (cond
    (is obj None) :unknown
    (hasattr obj "__doeff_body__") :kleisli
    (_is-effect-class obj) :effect
    (_is-effect-factory obj) :effect-factory
    True :python-fn))


;; ---------------------------------------------------------------------------
;; S-expr walking
;; ---------------------------------------------------------------------------

(defn _walk-calls [sexpr]
  "Recursively collect all call expressions from an S-expr body.
   Walks into nested blocks: do, when, if, cond, let, etc."
  (setv results [])

  (defn _collect [form]
    (when (not (isinstance form hy.models.Sequence)) (return))
    (when (not (isinstance form hy.models.Expression)) (return))
    (when (= (len form) 0) (return))
    (cond
      ;; (<- name (call ...)) → extract the call, don't recurse into it
      (_is-bind form)
        (let [expr (_bind-expr form)]
          (when (and expr (_is-call expr))
            (.append results expr)))
      ;; (do ...) / (when ...) / (let [...] ...) → recurse into children
      (and (isinstance (get form 0) hy.models.Symbol)
           (in (str (get form 0)) #{"do" "when" "if" "cond" "let" "setv"
                                     "for" "return" "try" "except"}))
        (for [child (cut form 1 None)]
          (_collect child))
      ;; (call ...) at any level
      (_is-call form)
        (.append results form)))

  (for [form sexpr]
    (_collect form))
  results)


;; ---------------------------------------------------------------------------
;; Effect collection — call graph walk
;; ---------------------------------------------------------------------------

(defn collect-effects [fn-obj * [visited None] [extra-effects None]]
  "Recursively collect all effect types used by a defk function.
   Walks the call graph — each function analyzed in its own module context.
   Returns a set of effect class names (strings).

   extra-effects: set of symbol name strings to treat as effects regardless
   of introspection. Use for effect factories that lack markers/annotations.
   Example: (collect-effects my-pipeline :extra-effects #{\"slog\" \"WaitUntil\"})"
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
    (setv kind (classify-call sym g :extra-effects extra-effects))
    (cond
      (or (= kind :effect) (= kind :effect-factory))
        (.add effects (str sym))
      (= kind :kleisli)
        (let [callee (_resolve sym g)]
          (.update effects (collect-effects callee :visited visited
                                                   :extra-effects extra-effects)))))
  effects)


;; ---------------------------------------------------------------------------
;; Effect tree — structured view
;; ---------------------------------------------------------------------------

(defn effect-tree [fn-obj * [visited None] [extra-effects None]]
  "Build a dependency tree showing effects at each call level.
   Returns {\"name\" str \"effects\" set \"children\" {name → subtree}}.

   extra-effects: set of symbol name strings to treat as effects."
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
    (setv kind (classify-call sym g :extra-effects extra-effects))
    (cond
      (or (= kind :effect) (= kind :effect-factory))
        (.add direct-effects (str sym))
      (= kind :kleisli)
        (let [callee (_resolve sym g)]
          (setv (get children (str sym))
                (effect-tree callee :visited visited
                                    :extra-effects extra-effects)))))

  {"name" (or (name-of fn-obj) "?")
   "effects" direct-effects
   "children" children})


;; ---------------------------------------------------------------------------
;; Handler coverage verification
;; ---------------------------------------------------------------------------

(defn assert-handlers [fn-obj handled-effects * [extra-effects None]]
  "Assert all effects used by fn-obj (recursively) have handlers.
   handled-effects: set of effect class name strings.
   extra-effects: passed to collect-effects for factory detection.
   Raises AssertionError if unhandled effects exist."
  (setv used (collect-effects fn-obj :extra-effects extra-effects))
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


;; ---------------------------------------------------------------------------
;; Stage extraction — partial pipeline reuse
;; ---------------------------------------------------------------------------

(defn list-stages [fn-obj]
  "List available stage names (bind variable names) from a defk/defp body.
   Returns a list of strings."
  (setv body (body-of fn-obj))
  (when (is body None) (return []))
  (setv stages [])
  (defn _scan [forms]
    (for [form forms]
      (cond
        ;; Direct bind: (<- name expr)
        (and (_is-bind form) (_bind-var form))
          (.append stages (str (_bind-var form)))
        ;; Recurse into blocks
        (and (isinstance form hy.models.Expression)
             (> (len form) 0)
             (isinstance (get form 0) hy.models.Symbol)
             (in (str (get form 0)) #{"do" "when" "if" "cond" "let"
                                       "try" "except"}))
          (_scan (cut form 1 None)))))
  (_scan body)
  stages)

(defn _truncate-body-at [body stage-name]
  "Truncate a body at the bind for stage-name.
   Returns forms up to and including that bind, plus the var as return value."
  (setv result [])
  (for [form body]
    (.append result form)
    (when (and (_is-bind form)
               (_bind-var form)
               (= (str (_bind-var form)) stage-name))
      ;; Add the variable as return expression
      (.append result (_bind-var form))
      (return result)))
  ;; stage-name not found at top level — try nested blocks
  None)

(defn stage-of [fn-obj stage-name]
  "Return a Program that runs fn-obj up to the named stage.
   The stage-name must match a (<- name ...) bind variable in the body.
   Compiles the truncated body via hy.eval."
  (setv body (body-of fn-obj))
  (when (is body None)
    (raise (ValueError (+ "No S-expr body on " (repr fn-obj)))))
  (setv truncated (_truncate-body-at (list body) stage-name))
  (when (is truncated None)
    (setv available (list-stages fn-obj))
    (raise (ValueError (+ "Stage '" stage-name "' not found. Available: "
                          (str available)))))
  ;; Compile: need the same imports as the original module
  (import hy)
  (setv args (or (args-of fn-obj) []))
  (setv mod-name (getattr fn-obj "__module__" "__main__"))
  ;; Build a self-contained compilation unit
  (setv compile-form
    `(do
       (require doeff-hy.macros [defk <-])
       (import doeff [do :as _doeff-do])
       ;; Re-import everything from the original module
       (import ~(hy.models.Symbol mod-name) *)
       (defk _staged ~args
         {:pre [] :post []}
         ~@truncated)
       (_staged ~@(lfor a args a))))
  ;; Evaluate in a fresh namespace with the original module's globals
  (import sys)
  (setv mod (.get sys.modules mod-name))
  (setv ns (dict (vars mod) :if mod :else {}))
  (hy.eval compile-form :module mod))
