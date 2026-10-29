;;; Hy macros for the doeff-conductor workflow DSL.
;;;
;;;   (require doeff-conductor.macros [defworkflow defphase agent! gate! workspace! merge!
;;;                                    parallel parallel-for loop <- time! random! pipeline])
;;;
;;; These macros are intentionally thin: they only translate the surface
;;; vocabulary into doeff_conductor._dsl IR objects. Expansion-time validation
;;; lives in the Python DSL module so Python and Hy authors share one checker.
;;;
;;; 持ち主は doeff-conductor(2026-10-27 に doeff-hy の doeff_hy.conductor から移した — 利用者の答え
;;; lt-GXXY5TVWBHJW4ZCBDTCRN68XZS の by-group・card ki-f2107259b141)。doeff-hy は effect・handler・test を書く macro と、
;;; 定義の macro の土台(_scoped-definition)だけを持つ。
;;;
;;; この module は名に _ を付けずに公開の境界に置く(ADR-DOE-API-001 R30 — Hy の require が module の path で macro を読む
;;; ので、R14・R15 と同じく R5 の外の境界。card ki-d80c9c978b35 が段 1 の _macros から戻した)。

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


;; 関数の本体の中に書いた defworkflow は、import(と本体の phase・agent! などが出す import)と定義をすぐ呼ぶ関数に入れて
;; name だけを束ねる — doeff_hy.macros の _scoped-definition(定義の macro が全部通る 1 か所 — 持ち主の package の定義の macro も
;; この土台を通る)。並べたままだと import した
;; `_conductor_defworkflow` が外の関数全体の局所の名になり、外の関数が先に読む module の段の同じ名が UnboundLocalError で落ちた。
(defmacro defworkflow [_hy-compiler name #* forms]
  (import doeff_hy.macros [_scoped-definition])
  (setv #(options body) (_parse-leading-options forms #{":params" ":roles" ":budget"}))
  (when (not (in ":params" options))
    (raise (SyntaxError "defworkflow requires :params")))
  (when (not (in ":roles" options))
    (raise (SyntaxError "defworkflow requires :roles")))
  (setv params (get options ":params")
        roles (get options ":roles")
        budget (if (in ":budget" options) (get options ":budget") None))
  (_scoped-definition _hy-compiler name
    [`(import doeff_conductor._dsl [defworkflow :as _conductor_defworkflow])
     `(setv ~name
        (_conductor_defworkflow ~(str name)
          :params ~params
          :roles ~roles
          :budget ~budget
          :body [~@body]))]))


(defmacro defphase [name #* forms]
  (setv stakes "normal")
  (setv body (list forms))
  (when (and (>= (len body) 2) (_kw? (get body 0) ":stakes"))
    (setv stakes (get body 1)
          body (cut body 2 None)))
  `(do
     (import doeff_conductor._dsl [defphase :as _conductor_defphase])
     (_conductor_defphase ~(str name) :stakes ~stakes :body [~@body])))


(defmacro agent! [#* forms]
  (setv rewritten (_rewrite-keywords forms {":class" "verification-class"}))
  `(do
     (import doeff_conductor._dsl [agent_bang :as _conductor_agent_bang])
     (_conductor_agent_bang ~@rewritten)))


(defmacro gate! [#* forms]
  `(do
     (import doeff_conductor._dsl [gate_bang :as _conductor_gate_bang])
     (_conductor_gate_bang ~@forms)))


(defmacro workspace! [#* forms]
  (setv rewritten (_rewrite-keywords forms {":from" "from_"}))
  `(do
     (import doeff_conductor._dsl [workspace_bang :as _conductor_workspace_bang])
     (_conductor_workspace_bang ~@rewritten)))


(defmacro merge! [#* forms]
  `(do
     (import doeff_conductor._dsl [merge_bang :as _conductor_merge_bang])
     (_conductor_merge_bang ~@forms)))


(defmacro parallel [#* forms]
  `(do
     (import doeff_conductor._dsl [parallel :as _conductor_parallel])
     (_conductor_parallel ~@forms)))


(defmacro parallel-for [binding #* body]
  (when (or (not (isinstance binding List)) (!= (len binding) 2))
    (raise (SyntaxError "parallel-for requires [name literal-seq]")))
  (setv var-name (get binding 0)
        values (get binding 1))
  `(do
     (import doeff_conductor._dsl [parallel_for :as _conductor_parallel_for])
     (_conductor_parallel_for ~(str var-name) ~values (fn [~var-name] (do ~@body)))))


(defmacro loop [#* forms]
  (setv #(options body) (_parse-leading-options forms #{":max" ":until" ":budget"}))
  (when (not (in ":max" options))
    (raise (SyntaxError "loop requires :max")))
  (when (not (in ":until" options))
    (raise (SyntaxError "loop requires :until")))
  (setv budget (if (in ":budget" options) (get options ":budget") None))
  `(do
     (import doeff_conductor._dsl [loop :as _conductor_loop])
     (_conductor_loop
       :max ~(get options ":max")
       :until ~(get options ":until")
       :budget ~budget
       :body [~@body])))


(defmacro <- [target expr]
  `(do
     (import doeff_conductor._dsl [bind :as _conductor_bind])
     (_conductor_bind ~(_target-form target) ~expr)))


(defmacro time! [#* forms]
  `(do
     (import doeff_conductor._dsl [time_bang :as _conductor_time_bang])
     (_conductor_time_bang ~@forms)))


(defmacro random! [#* forms]
  `(do
     (import doeff_conductor._dsl [random_bang :as _conductor_random_bang])
     (_conductor_random_bang ~@forms)))


(defmacro pipeline [#* forms]
  `(do
     (import doeff_conductor._dsl [pipeline :as _conductor_pipeline])
     (_conductor_pipeline ~@forms)))
