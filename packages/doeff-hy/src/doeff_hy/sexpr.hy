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

(defn _bind-var-name [form]
  "Get a human-readable stage name from a bind variable.
   Handles: Symbol → str, Tuple → 'a,b,c', None → None.
   Returns None for discard binds (_ or unnamed)."
  (setv var (_bind-var form))
  (when (is var None) (return None))
  (cond
    (isinstance var hy.models.Symbol)
      (let [name (str var)]
        (if (= name "_") None name))
    (isinstance var hy.models.Tuple)
      (.join "," (lfor v var (str v)))
    (isinstance var hy.models.List)
      (.join "," (lfor v var (str v)))
    True (str var)))

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
  ;; Prefer __doeff_module__ (set by defp/defprogram on Program values),
  ;; then __module__ (set on defk functions by Python).
  ;; Note: DoExpr classes have __module__="doeff_vm.doeff_vm" from pyclass,
  ;; which leaks to instances via class lookup — so __doeff_module__ must come first.
  (setv mod-name (or (getattr fn-obj "__doeff_module__" None)
                     (getattr fn-obj "__module__" None)))
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


(defn _effect-label [call-expr]
  "Build a human-readable label for an effect call.
   Extracts literal positional args and keyword args from the S-expr.
   E.g. (Ask \"key\") → 'Ask(\"key\")', (FetchOhlc :ticker \"7203.T\") → 'FetchOhlc(ticker=\"7203.T\")'."
  (setv head (str (get call-expr 0)))
  (setv args [])
  (setv i 1)
  (while (< i (len call-expr))
    (setv item (get call-expr i))
    (cond
      ;; Keyword arg: :key value
      (isinstance item hy.models.Keyword)
        (do
          (when (< (+ i 1) (len call-expr))
            (setv val (get call-expr (+ i 1)))
            (when (isinstance val #(hy.models.String hy.models.Integer hy.models.Float))
              (.append args (+ (str item.name) "=" (repr (str val)) )))
            (+= i 2))
          (when (>= (+ i 1) (len call-expr))
            (+= i 1)))
      ;; Positional literal
      (isinstance item hy.models.String)
        (do (.append args (repr (str item)))
            (+= i 1))
      (isinstance item #(hy.models.Integer hy.models.Float))
        (do (.append args (str item))
            (+= i 1))
      ;; Symbol or complex expr — skip
      True (+= i 1)))
  (if args
    (+ head "(" (.join ", " args) ")")
    head))


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
        (.add direct-effects (_effect-label call))
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
  "List available stages from a defk/defp body.
   Returns a list of dicts: [{\"name\" str \"index\" int} ...].
   Duplicate names are included with distinct indices."
  (setv body (body-of fn-obj))
  (when (is body None) (return []))
  (setv stages [])
  (setv idx 0)
  (defn _scan [forms]
    (nonlocal idx)
    (for [form forms]
      (cond
        ;; Direct bind: (<- name expr)
        (and (_is-bind form) (_bind-var-name form))
          (do
            (.append stages {"name" (_bind-var-name form) "index" idx})
            (+= idx 1))
        ;; Recurse into blocks
        (and (isinstance form hy.models.Expression)
             (> (len form) 0)
             (isinstance (get form 0) hy.models.Symbol)
             (in (str (get form 0)) #{"do" "when" "if" "cond" "let"
                                       "try" "except"}))
          (_scan (cut form 1 None)))))
  (_scan body)
  stages)

(defn list-all-stages [fn-obj * [prefix ""] [_visited None]]
  "Recursively list all stages including sub-stages from defk calls.
   Returns a flat list of dicts:
     [{\"path\" \"data\" \"index\" 0 \"needs-args\" False}
      {\"path\" \"data.prices\" \"index\" [0 0] \"needs-args\" False}
      ...]
   For defk with args, top-level needs-args is True.
   Sub-stages of a call inherit the parent's binding context."
  (when (is _visited None)
    (setv _visited (set)))
  (setv fn-id (id fn-obj))
  (when (in fn-id _visited)
    (return []))
  (.add _visited fn-id)

  (setv body (body-of fn-obj))
  (when (is body None) (return []))
  (setv g (_get-module-globals fn-obj))
  (setv has-args (and (args-of fn-obj) (> (len (args-of fn-obj)) 0)))

  (setv result [])
  (setv idx 0)
  (for [form body]
    (when (and (_is-bind form) (_bind-var-name form))
      (setv vname (_bind-var-name form))
      (setv path (if prefix (+ prefix "." vname) vname))
      (.append result {"path" path "index" idx "needs-args" has-args "name" vname})
      ;; Check if the bind expr calls a defk → recurse
      (setv expr (_bind-expr form))
      (when (and expr (_is-call expr) g)
        (setv callee-sym (_call-head expr))
        (setv kind (classify-call callee-sym g))
        (when (= kind :kleisli)
          (setv callee (_resolve callee-sym g))
          (when callee
            (.extend result (list-all-stages callee :prefix path :_visited _visited)))))
      (+= idx 1)))
  result)

(defn print-all-stages [fn-obj]
  "Pretty-print all available stages."
  (setv stages (list-all-stages fn-obj))
  (for [s stages]
    (setv indent (* "  " (.count (get s "path") ".")))
    (setv args-mark (if (get s "needs-args") " (needs args)" ""))
    (print (+ indent (get s "path") args-mark))))

(defn _truncate-body-at [body stage-ref]
  "Truncate a body at a stage.
   stage-ref: string (name — matches LAST occurrence) or int (bind index).
   Returns forms up to and including that bind, plus the var as return value."
  ;; Collect all top-level binds with positions
  (setv binds [])
  (for [#(i form) (enumerate body)]
    (when (and (_is-bind form) (_bind-var-name form))
      (.append binds {"pos" i "name" (_bind-var-name form) "var" (_bind-var form)})))
  ;; Find target bind
  (setv target None)
  (cond
    (isinstance stage-ref int)
      ;; By index
      (when (< stage-ref (len binds))
        (setv target (get binds stage-ref)))
    (isinstance stage-ref str)
      ;; By name — match LAST occurrence (most complete version)
      (for [b (reversed binds)]
        (when (= (get b "name") stage-ref)
          (setv target b)
          (break))))
  (when (is target None)
    (return None))
  ;; Truncate: all forms up to and including the target bind position
  (setv result (list (cut body 0 (+ (get target "pos") 1))))
  (.append result (get target "var"))
  result)

(defn stage-of [fn-obj stage-ref]
  "Return a Program that runs fn-obj up to the named/indexed stage.
   stage-ref: string (bind variable name — last occurrence) or int (bind index).
   For defk with args, returns a function. For zero-arg, returns a Program."
  (setv body (body-of fn-obj))
  (when (is body None)
    (raise (ValueError (+ "No S-expr body on " (repr fn-obj)))))
  (setv truncated (_truncate-body-at (list body) stage-ref))
  (when (is truncated None)
    (setv available (list-stages fn-obj))
    (setv avail-str (.join ", " (lfor s available
                                  (+ (get s "name") "[" (str (get s "index")) "]"))))
    (raise (ValueError (+ "Stage '" (str stage-ref) "' not found. Available: " avail-str))))
  ;; Compile: evaluate in the original module's context
  (import hy sys)
  (setv args (or (args-of fn-obj) []))
  (setv mod-name (getattr fn-obj "__module__" "__main__"))
  (setv mod (.get sys.modules mod-name))
  ;; Ensure macros are available in the module
  (when mod
    (hy.macros.require "doeff_hy.macros" mod
      :assignments [["defk" "defk"] ["<-" "<-"]]))
  ;; Build compilation form — generate (: param object) for each arg
  (setv pre-checks (lfor a args
    :if (and (isinstance a hy.models.Symbol) (!= (str a) "*"))
    `(: ~a object)))
  (when (not pre-checks) (setv pre-checks []))
  (setv compile-form
    `(do
       (defk _staged ~args
         {:pre ~(hy.models.List pre-checks) :post [(: % object)]}
         ~@truncated)
       ~(if args
          '_staged
          '(_staged))))
  (hy.eval compile-form :module mod))


;; ---------------------------------------------------------------------------
;; Pre-flight analysis — call from interpreter before run()
;; ---------------------------------------------------------------------------

(defn _collect-ask-keys [tree]
  "Recursively collect all Ask key strings from an effect tree."
  (setv keys (set))
  (for [label (get tree "effects")]
    (when (.startswith label "Ask(")
      ;; Extract key from Ask('some.key') or Ask("some.key")
      (setv inner (cut label 4 -1))  ; strip Ask( and )
      (setv key (.strip inner "\"'"))
      (.add keys key)))
  (for [child (.values (get tree "children"))]
    (.update keys (_collect-ask-keys child)))
  keys)


(defn show-program-analysis [program * [env None]]
  "Best-effort static analysis of a Program or defk function.
   Prints effect tree and stage listing. Warns if no S-expr body available.
   If env (dict) is provided, checks that all Ask keys are present."
  (import warnings)
  (setv body (body-of program))
  (when (is body None)
    (setv name (repr program))
    (warnings.warn f"No S-expr body on {name} — static analysis unavailable" :stacklevel 2)
    (return))
  (import sys)
  (setv pname (or (name-of program) "?"))
  (print f"=== {pname} ===" :flush True)
  (print "Effects:" :flush True)
  (setv tree (effect-tree program))
  (print-effect-tree tree :indent 1)
  (print "Stages:")
  (setv stages (list-all-stages program))
  (if stages
    (for [s stages]
      (setv indent (+ "  " (* "  " (.count (get s "path") "."))))
      (setv args-mark (if (get s "needs-args") " (needs args)" ""))
      (print (+ indent (get s "path") args-mark)))
    (print "  (none)"))
  ;; Env key check
  (setv ask-keys (_collect-ask-keys tree))
  (when ask-keys
    (if (is env None)
      (do
        (print "Ask keys:")
        (for [k (sorted ask-keys)]
          (print f"  {k}")))
      (do
        (setv missing (sorted (- ask-keys (set (.keys env)))))
        (when missing
          (print "MISSING env keys:")
          (for [k missing]
            (print f"  !! {k}"))
          (.flush sys.stdout)
          (warnings.warn f"Missing env keys: {missing}" :stacklevel 2)))))
  (.flush sys.stdout))
