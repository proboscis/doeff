;;; Hy macros for the doeff-conductor workflow DSL.
;;;
;;; These macros are intentionally thin: they only translate the surface
;;; vocabulary into doeff_conductor.dsl IR objects. Expansion-time validation
;;; lives in the Python DSL module so Python and Hy authors share one checker.

(import hy.models [Keyword List Symbol])


(defn _kw? [form name]
  (and (isinstance form Keyword) (= (str form) name)))


(defn _parse-leading-options [forms option-names]
  "Return #(options body) for leading keyword/value pairs."
  (setv options {}
        index 0
        total (len forms))
  (while (and (< index total)
              (isinstance (get forms index) Keyword)
              (in (str (get forms index)) option-names))
    (when (>= (+ index 1) total)
      (raise (SyntaxError (+ "missing value for " (str (get forms index))))))
    (dict.__setitem__ options (str (get forms index)) (get forms (+ index 1)))
    (setv index (+ index 2)))
  #(options (list (cut forms index None))))


(defn _rewrite-keywords [forms rewrites]
  "Rewrite keyword tokens according to rewrites; leave values untouched."
  (setv rewritten [])
  (for [form forms]
    (if (and (isinstance form Keyword) (in (str form) rewrites))
        (do
          (setv keyword-name (get rewrites (str form)))
          (when (.startswith keyword-name ":")
            (setv keyword-name (cut keyword-name 1 None)))
          (.append rewritten (Keyword keyword-name)))
        (.append rewritten form)))
  rewritten)


(defn _target-form [target]
  (cond
    (isinstance target Symbol)
      (str target)
    (isinstance target List)
      `(list ~@(lfor item target (str item)))
    True
      (raise (SyntaxError "<- target must be a symbol or a destructuring list"))))


(defmacro defworkflow [name #* forms]
  (setv #(options body) (_parse-leading-options forms #{":params" ":roles" ":budget"}))
  (when (not (in ":params" options))
    (raise (SyntaxError "defworkflow requires :params")))
  (when (not (in ":roles" options))
    (raise (SyntaxError "defworkflow requires :roles")))
  (setv params (get options ":params")
        roles (get options ":roles")
        budget (if (in ":budget" options) (get options ":budget") None))
  `(do
     (import doeff_conductor.dsl [defworkflow :as _conductor_defworkflow])
     (setv ~name
       (_conductor_defworkflow ~(str name)
         :params ~params
         :roles ~roles
         :budget ~budget
         :body [~@body]))))


(defmacro defphase [name #* forms]
  (setv stakes "normal")
  (setv body (list forms))
  (when (and (>= (len body) 2) (_kw? (get body 0) ":stakes"))
    (setv stakes (get body 1)
          body (cut body 2 None)))
  `(do
     (import doeff_conductor.dsl [defphase :as _conductor_defphase])
     (_conductor_defphase ~(str name) :stakes ~stakes :body [~@body])))


(defmacro agent! [#* forms]
  (setv rewritten (_rewrite-keywords forms {":class" "verification-class"}))
  `(do
     (import doeff_conductor.dsl [agent_bang :as _conductor_agent_bang])
     (_conductor_agent_bang ~@rewritten)))


(defmacro gate! [#* forms]
  `(do
     (import doeff_conductor.dsl [gate_bang :as _conductor_gate_bang])
     (_conductor_gate_bang ~@forms)))


(defmacro workspace! [#* forms]
  (setv rewritten (_rewrite-keywords forms {":from" "from_"}))
  `(do
     (import doeff_conductor.dsl [workspace_bang :as _conductor_workspace_bang])
     (_conductor_workspace_bang ~@rewritten)))


(defmacro merge! [#* forms]
  `(do
     (import doeff_conductor.dsl [merge_bang :as _conductor_merge_bang])
     (_conductor_merge_bang ~@forms)))


(defmacro parallel [#* forms]
  `(do
     (import doeff_conductor.dsl [parallel :as _conductor_parallel])
     (_conductor_parallel ~@forms)))


(defmacro parallel-for [binding #* body]
  (when (or (not (isinstance binding List)) (!= (len binding) 2))
    (raise (SyntaxError "parallel-for requires [name literal-seq]")))
  (setv var-name (get binding 0)
        values (get binding 1))
  `(do
     (import doeff_conductor.dsl [parallel_for :as _conductor_parallel_for])
     (_conductor_parallel_for ~(str var-name) ~values (fn [~var-name] (do ~@body)))))


(defmacro loop [#* forms]
  (setv #(options body) (_parse-leading-options forms #{":max" ":until" ":budget"}))
  (when (not (in ":max" options))
    (raise (SyntaxError "loop requires :max")))
  (when (not (in ":until" options))
    (raise (SyntaxError "loop requires :until")))
  (setv budget (if (in ":budget" options) (get options ":budget") None))
  `(do
     (import doeff_conductor.dsl [loop :as _conductor_loop])
     (_conductor_loop
       :max ~(get options ":max")
       :until ~(get options ":until")
       :budget ~budget
       :body [~@body])))


(defmacro <- [target expr]
  `(do
     (import doeff_conductor.dsl [bind :as _conductor_bind])
     (_conductor_bind ~(_target-form target) ~expr)))


(defmacro time! [#* forms]
  `(do
     (import doeff_conductor.dsl [time_bang :as _conductor_time_bang])
     (_conductor_time_bang ~@forms)))


(defmacro random! [#* forms]
  `(do
     (import doeff_conductor.dsl [random_bang :as _conductor_random_bang])
     (_conductor_random_bang ~@forms)))


(defmacro pipeline [#* forms]
  `(do
     (import doeff_conductor.dsl [pipeline :as _conductor_pipeline])
     (_conductor_pipeline ~@forms)))
