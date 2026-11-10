;;; Helpers for expanding absence and failure (ADR-DOE-CORE-EFFECTS-003) — the :absent of <-, the open-bind form that a bind yields,
;;; and the expansions of on-raise and absent-as.
;;;
;;; The runtime meaning lives in doeff_core_effects._outcomes; this only builds the shapes:
;;;
;;;   (<- x e)              → (setv x (yield (open-bind e)))       open the answer of an effect that has a declaration, open the value of a Result / Maybe,
;;;                                                                 otherwise yield e itself (the same thing as before)
;;;   (<- x e :absent F)    → (setv x (yield (open-bind e (fn [] F))))   turn an Absent raised inside the bind into Raise(F)
;;;   (on-raise body (Conflict d) (WriteConflict :detail d) …)       map only a Raise of a matching type to a domain answer (a match-anything form is refused)
;;;   (absent-as 0 body)                                             fold Absent into a default value (resume only the absence of a <- inside the literal text)
;;;
;;; The expansion targets of on-raise and absent-as are factories that return a handler when called — ((on-raise receivers…) body), ((absent-as 0 :token mark) body)
;;; (ADR-DOE-API-001 R6)。
;;;
;;; macros.hy and handle.hy import this module inside a function at expansion time (while macros.hy is being compiled, the helpers that the deff
;;; expansion calls do not exist yet, so helpers written with deff are placed outside macros.hy).

(require doeff-hy.macros [deff])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import re)
(import hy)
(import hy.models [Expression Symbol Keyword String List Tuple Dict Set FString FComponent Sequence Object Bytes Integer Float Complex])
(import doeff_hy._match_fields [mangle-match-fields])


(defrecord SplitBind
  #^ Expression core
  #^ (| Object None) absent)


;; Forms that do not count a bind inside the literal text (having their own do context, held as a value, a nested function) — absent-as does not cross them.
(setv OPAQUE-HEADS
  #{"for/do" "traverse" "fnk" "do!" "defhandler" "defk" "deff" "defp" "defpp" "deftest" "defmcp-tool"
    "defmacro" "quote" "quasiquote" "validate" "absent-as"
    "fn" "fn/a" "defn" "defn/a" "defclass" "lfor" "gfor" "dfor" "sfor"})

;; Type names that cannot be put in an on-raise pattern (match-anything forms, Python exceptions — R7).
(setv CATCH-ALL-NAMES #{"_" "object" "Exception" "BaseException"})


(deff head-name [form]  ; defk にできない: a function called at macro expansion time
  {:pre [(: form Object)] :post [(: % (| str None))]
   :tags {:context "doeff-hy-outcomes" :role "foundation"}}
  "The name of the head symbol of form (None if it is not an expression or the head is not a symbol) — to dispatch by shape."
  (when (and (isinstance form Expression) (> (len form) 0) (isinstance (get form 0) Symbol))
    (str (get form 0))))


(deff source-of [form]  ; defk にできない: a function called at macro expansion time
  {:pre [(: form Object)] :post [(: % str)]
   :tags {:context "doeff-hy-outcomes" :role "foundation"}}
  "The spelling of form to show in error messages."
  (.lstrip (hy.repr form) "'"))


(deff performs-effect? [form]  ; defk にできない: a function called at macro expansion time
  {:pre [(: form Object)] :post [(: % bool)]
   :tags {:context "doeff-hy-outcomes" :role "foundation"}}
  "Whether form performs an effect (whether it contains `!`, `<-`, or `yield` — inside quote is not counted). To check places that hold only a value expression (the failure of `:absent` and
   the mapping target of on-raise)."
  (let [head (head-name form)]
    (match form
      (Expression) :if (in head #{"quote" "quasiquote"}) False
      (Expression) :if (in head #{"!" "<-" "yield"}) True
      (Sequence) (any (gfor child form (performs-effect? child)))
      _ False)))


;; The shapes of literal values that, written in the value position of a bound expression, make the VM fail with a TypeError (yield got …) when yielded
;; (failure example: `(<- x (if c None (f)))` yielded a bare None when c held).
(setv PLAIN-VALUE-SYMBOLS #{"None" "True" "False"})


(deff plain-value-of [expr]  ; defk にできない: a function called at macro expansion time
  {:pre [(: expr Object)] :post [(: % (| Object None))]
   :tags {:context "doeff-hy-outcomes" :role "foundation"}}
  "One literal value that the bound expression expr (the expression of `<-`, the content of `!`) can evaluate to (None if none) — to refuse at expansion time a bind that
   yields a value that is neither an effect nor a Program. Only value positions are looked at: the expression itself, the two branches of if, and the value branches of cond (nested too).
   when / unless become None when the condition fails, so the whole expression is returned."
  (let [head (head-name expr)
        branches (match head
                   "if" (cut expr 2 None)
                   "cond" (cut expr 2 None 2)
                   _ #())]
    (match expr
      (Symbol) :if (in (str expr) PLAIN-VALUE-SYMBOLS) expr
      (| (Keyword) (String) (FString) (Bytes) (Integer) (Float) (Complex) (List) (Tuple) (Dict) (Set)) expr
      (Expression) :if (in head #{"when" "unless"}) expr
      (Expression) (next (gfor branch branches :setv found (plain-value-of branch) :if (is-not found None) found) None)
      _ None)))


(deff plain-value-refusal [expr]  ; defk にできない: a function called at macro expansion time
  {:pre [(: expr Object)] :post [(: % (| str None))]
   :tags {:context "doeff-hy-outcomes" :role "foundation"}}
  "The refusal message if there is a literal value in the value position of the bound expression expr, otherwise None — the open-form that builds the bind shape (the one point <- and ! pass through)
   raises it as a SyntaxError."
  (let [found (plain-value-of expr)]
    (when (is-not found None)
      (+ "<- / !: a literal value " (source-of found) " is in the value position of the bound expression — yielding a value that is neither an effect nor a Program fails at runtime with "
         "TypeError (yield got …). To bind the value as is, wrap it with (Pure " (source-of found)
         ") or place it with val without binding: " (source-of expr)))))


;; The type names whose values a bind can yield again: a Program, an effect, or a value that open-bind opens (Ok / Err / Some / Nothing, Absent / Raise).
;; A name bound by <- with a type that names none of these holds a plain value (failure example: `(<- made (| FileFailed None) (MakeDirectory d))`
;; then `(<- written (| FileFailed None) (if (isinstance made FileFailed) made (WriteBytes …)))` yielded the FileFailed when the dir could not be made).
(setv YIELDABLE-TYPE-NAMES
  #{"Program" "ProgramBase" "ProgramLike" "KleisliProgram" "EffectBase" "Effect" "DoExpr" "DoCtrl" "Call" "Pure" "Expand" "EffectGenerator"
    "object" "Any" "Ok" "Err" "Result" "Some" "Nothing" "Maybe" "Option" "Absent" "Raise"})

;; The forms whose leading items name bound places (the names are collected as not-plain — another binding of the same spelling is not a plain-value name).
(setv BINDER-HEADS #{"val" "var" "setv" "setx" ":=" "global" "nonlocal"})
(setv BRACKET-BINDER-HEADS #{"for" "with" "let" "lfor" "gfor" "dfor" "sfor" "fn" "fn/a" "fnk" "defn" "defn/a" "for/do" "traverse"})


(deff type-names [form]  ; defk にできない: a function called at macro expansion time
  {:pre [(: form Object)] :post [(: % set)]
   :tags {:context "doeff-hy-outcomes" :role "foundation"}}
  "The identifiers that the type form of a bind names (each part of a dotted name, the identifiers of a string forward reference) — to judge whether the type
   can hold a yieldable value."
  (match form
    (Symbol) (set (.split (str form) "."))
    (String) (set (re.findall r"[A-Za-z_][A-Za-z0-9_]*" (str form)))
    (Sequence) (.union (set) #* (gfor child form (type-names child)))
    _ (set)))


(deff plain-value-type? [tp]  ; defk にできない: a function called at macro expansion time
  {:pre [(: tp Object)] :post [(: % bool)]
   :tags {:context "doeff-hy-outcomes" :role "foundation"}}
  "Whether the type form tp of a bind (None = no type) names only plain-value types (none of YIELDABLE-TYPE-NAMES)."
  (and (is-not tp None) (not (& (type-names tp) YIELDABLE-TYPE-NAMES))))


(deff symbols-in [form]  ; defk にできない: a function called at macro expansion time
  {:pre [(: form Object)] :post [(: % set)]
   :tags {:context "doeff-hy-outcomes" :role "foundation"}}
  "The spellings of all symbols in form."
  (match form
    (Symbol) #{(str form)}
    (Sequence) (.union (set) #* (gfor child form (symbols-in child)))
    _ (set)))


(deff bound-spellings [node [opaque False]]  ; defk にできない: a function called at macro expansion time
  {:pre [(: node Object) (: opaque bool)] :post [(: % tuple)]
   :tags {:context "doeff-hy-outcomes" :role "foundation"}}
  "The names that node (a body form, or the list of them) binds, as #(plain other): plain = names bound by a <- with a plain-value type (outside nested
   functions and do contexts — OPAQUE-HEADS; opaque = node is inside one), other = names bound any other way anywhere (a <- with no type or a yieldable type,
   val / var / setv / :=, the brackets of for / with / let / comprehensions / fn, the patterns of match and except). A spelling in other is not taken as plain
   (the same spelling may hold a Program)."
  (let [head (head-name node)
        parts (when (= head "<-") (cut (. (split-absent node) core) 1 None))
        named? (and parts (>= (len parts) 2) (isinstance (get parts 0) Symbol))
        plain? (and named? (not opaque) (= (len parts) 3) (plain-value-type? (get parts 1)))
        own-plain (if plain? #{(str (get parts 0))} (set))
        own-other (cond
                    (and named? (not plain?)) #{(str (get parts 0))}
                    (in head BINDER-HEADS) (.union (set) #* (gfor target (cut node 1 None 2) (symbols-in target)))
                    (in head BRACKET-BINDER-HEADS) (.union (set) #* (gfor child (cut node 1 None) :if (isinstance child List) (symbols-in child)))
                    (= head "match") (.union (set) #* (gfor pattern (cut node 2 None 2) (symbols-in pattern)))
                    (= head "except") (symbols-in node)
                    True (set))
        children (cond (isinstance node Expression) (cut node 1 None)
                       (isinstance node Sequence) node
                       True #())
        inner (or opaque (in head OPAQUE-HEADS))
        below (tuple (gfor child children (bound-spellings child inner)))]
    #((.union own-plain #* (gfor b below (get b 0)))
      (.union own-other #* (gfor b below (get b 1))))))


(deff bound-value-of [expr names]  ; defk にできない: a function called at macro expansion time
  {:pre [(: expr Object) (: names set)] :post [(: % (| Symbol None))]
   :tags {:context "doeff-hy-outcomes" :role "foundation"}}
  "A name of names (bound to a plain value) in the value position of the bound expression expr (the expression itself, the two branches of if, the value branches of
   cond, nested too — the same positions as plain-value-of), or None."
  (let [head (head-name expr)
        branches (match head
                   "if" (cut expr 2 None)
                   "cond" (cut expr 2 None 2)
                   _ #())]
    (match expr
      (Symbol) :if (in (str expr) names) expr
      (Expression) (next (gfor branch branches :setv found (bound-value-of branch names) :if (is-not found None) found) None)
      _ None)))


(deff bound-value-bind [node names]  ; defk にできない: a function called at macro expansion time
  {:pre [(: node Object) (: names set)] :post [(: % (| tuple None))]
   :tags {:context "doeff-hy-outcomes" :role "foundation"}}
  "The first bind (<- or !) inside node that has a name of names in a value position of its bound expression, as #(name bind), or None — to point at the
   bind that the refusal names. Nested functions and do contexts (OPAQUE-HEADS) are not entered (their own expansion checks them)."
  (let [head (head-name node)
        expr (cond (= head "<-") (let [core (. (split-absent node) core)] (when (>= (len core) 2) (get core -1)))
                   (and (= head "!") (= (len node) 2)) (get node 1)
                   True None)
        hit (when (is-not expr None) (bound-value-of expr names))]
    (cond
      (is-not hit None) #(hit node)
      (and (isinstance node Expression) (in head OPAQUE-HEADS)) None
      (isinstance node Sequence) (next (gfor child node :setv found (bound-value-bind child names) :if (is-not found None) found) None)
      True None)))


(deff bound-value-refusal [forms params where]  ; defk にできない: a function called at macro expansion time
  {:pre [(: forms (| list tuple Sequence)) (: params (| set list tuple Sequence)) (: where str)] :post [(: % (| str None))]
   :tags {:context "doeff-hy-outcomes" :role "foundation"}}
  "The refusal message if a later bind of the body forms has, in a value position of its bound expression, a name that an earlier <- bound with a plain-value type
   (the name holds a value that is neither an effect nor a Program, so the branch fails at runtime with TypeError — yield got …), otherwise None. params = the
   argument names (an argument may hold a Program). defk raises it as a SyntaxError."
  (let [#(plain other) (bound-spellings (List forms))
        names (- plain other (set (gfor p params (str p))))
        found (when names (bound-value-bind (List forms) names))]
    (when (is-not found None)
      (let [#(hit node) found]
        (+ where ": the name " (str hit) " was bound by an earlier <- with a plain-value type and is in the value position of the bound expression — "
           "yielding a value that is neither an effect nor a Program fails at runtime with TypeError (yield got …). Branch on it before the bind "
           "((when … (return …))), or wrap it with (Pure " (str hit) "): " (source-of node))))))


(deff split-absent [form]  ; defk にできない: a function called at macro expansion time
  {:pre [(: form Expression)] :post [(: % SplitBind)]
   :tags {:context "doeff-hy-outcomes" :role "foundation"}}
  "To separate the trailing `:absent failure` of (<- … :absent failure) (R6). Any key other than `:absent`, and a failure expression that performs an effect
   (the failure is a value expression — evaluated only when absent) is a SyntaxError."
  (let [suffix? (and (>= (len form) 4)
                     (isinstance (get form -2) Keyword)
                     (= (str (get form -2)) ":absent"))
        core (if suffix? (Expression (cut form 0 -2)) form)]
    (for [item (cut core 1 None)]
      (when (isinstance item Keyword)
        (raise (SyntaxError (+ "<-: unknown key " (str item) " — the only key <- accepts is a trailing :absent <failure>: "
                               (source-of form))))))
    (when (and suffix? (performs-effect? (get form -1)))
      (raise (SyntaxError (+ "<-: the :absent failure must be a value expression (evaluated only when absent — ! and <- cannot be used): "
                             (source-of (get form -1))))))
    (SplitBind :core core :absent (when suffix? (get form -1)))))


(deff open-form [expr absent [helpers None] [bound None]]  ; defk にできない: a function called at macro expansion time
  {:pre [(: expr Object) (: absent (| Object None)) (: helpers (| str None)) (: bound (| Symbol None))]
   :post [(: % Expression)]
   :tags {:context "doeff-hy-outcomes" :role "foundation"}}
  "The expression of one bind (R5, R6): if the answer of `(open-bind expr)` is `Pure`, its value; otherwise yield and take the VM's answer.
   open-bind calls on the spot an @do call that performs no effect (such as a defk with no yield in its body) and returns its answer as `Pure`,
   so that bind does not round-trip through the VM. Yielding a `Pure` gives the same answer, so the meaning is the same as the yielding form.
   The lookups of open-bind and `Pure` are held inside the shape — so the names resolve wherever the `<-` / `!` is written, such as a defhandler clause or a user's defn.
   The shape is one expression with no statements (a conditional expression and an assignment expression) — `!` can be written anywhere inside an expression,
   so it can also come inside an assignment target such as `(setv (get (! e) key) value)`. With `(do (import …) …)`, which holds an import statement, Hy cannot build the target and
   compile fails (agora-controllers' kanban_services.hy, 2026-09-28). The module lookup (`__import__`) costs
   about 0.3µs per bind (the same order as an import statement; hy.I is 4µs — measured 2026-09-28).
   helpers = the name of the outcomes module present in the module globals (for the binds in a defk body — the
   `_doeff_outcomes` that defk places in globals, HELPERS-NAME). If present, look it up by that name instead of `__import__` (removes the roughly 0.2µs of `__import__` per
   bind). Only a defk body is known to have the name, so it stays None elsewhere.
   helpers is received as a name string and a new Symbol is made at each insertion point — reusing one model across expansions
   leaves the position that locate-synthesized attached on it, shifting the lines of later binds.
   An expression with a literal value in a value position (such as `(if c None (f))`) yields a bare value in that branch and fails at runtime, so it is refused before the shape is built.
   bound = a local name holding the answer (a Pure or a yielded Program). If the caller passes it, after binding the name can be del-ed to release it from the frame
   (_bind-yield — the name kept the Call until the next bind and kept the previous iteration's arguments alive in a long loop).
   If not passed, make a gensym and reset the name to None inside the expression before answering (inside the expression of `!` — a del statement cannot be placed):
   `(. (get #(b (setx b None)) 0) value)` / `(yield (get #(b (setx b None)) 0))` — take the original answer at slot 0 of the tuple, release the name,
   and then read the value or yield (the frame does not hold the Call during the yield either). The analyzer reads both shapes
   (_bind_expression and _released_name of doeff-effect-analyzer)."
  (let [refused (plain-value-refusal expr)]
    (when (is-not refused None)
      (raise (SyntaxError refused))))
  (let [module (if (is helpers None) (hy.gensym "outcomes") (Symbol helpers))
        release? (is bound None)
        bound (if release? (hy.gensym "bound") bound)
        ;; The answer after releasing the name: when the caller does not del, take the original answer at slot 0 of the tuple and reset the name to None (a new shape per branch)
        answer (fn [] (if release? `(get #(~bound (setx ~bound None)) 0) bound))
        imported (if (is helpers None)
                     `(setx ~module (__import__ "doeff_core_effects._outcomes" :fromlist #("open_bind")))
                     (Symbol helpers))
        opened (if (is absent None)
                   `(. ~imported (open_bind ~expr))
                   `(. ~imported (open_bind ~expr (fn [] ~absent))))]
    ;; Do not look up built-in names (such as type) by name — if the caller's argument or local name has the same spelling, Python treats it as a local name of
    ;; the whole function and the bind breaks (UnboundLocalError with the defhandler arm argument type, the cleanup of L562).
    ;; Read the type through the attribute __class__, and look up by name only the gensym bindings.
    `(if (is (. (setx ~bound ~opened) __class__) (. ~module Pure))
         (. ~(answer) value)
         (yield ~(answer)))))


(deff bind-form [form [helpers None]]  ; defk にできない: a function called at macro expansion time
  {:pre [(: form Expression) (: helpers (| str None))] :post [(: % Object)]
   :tags {:context "doeff-hy-outcomes" :role "foundation"}}
  "The expansion of one (<- …) form — the one point shared by the <- macro and the macros that read the body first (do!, defp, deftest, for/do, defhandler clauses, defk).
   The split is _bind-parts in macros.hy, the shape is _bind-yield. helpers is passed to open-form (defk bodies only)."
  (import doeff-hy.macros [_bind-parts _bind-yield])
  (let [parts (_bind-parts form)]
    (when (is parts None)
      (raise (SyntaxError (+ "<-: expected (<- expr) / (<- name expr) / (<- name Type expr), optionally followed by "
                             ":absent <failure>, got " (source-of form)))))
    (_bind-yield (get parts 0) (get parts 1) (get parts 2) (. (split-absent form) absent) helpers)))


;; ---------------------------------------------------------------------------
;; on-raise(R7)
;; ---------------------------------------------------------------------------

(deff pattern-types [pattern]  ; defk にできない: a function called at macro expansion time
  {:pre [(: pattern Object)] :post [(: % list)]
   :tags {:context "doeff-hy-outcomes" :role "foundation"}}
  "The list of forms of the failure types that one on-raise pattern names. Accepts only a type pattern `(Type …)` and its or `(| (A …) (B …))`, and
   a match-anything form (`_`, a single-name capture, object, Exception, BaseException) is a SyntaxError (R7)."
  (let [head (head-name pattern)]
    (match pattern
      (Expression) :if (= head "|")
        (lfor alternative (cut pattern 1 None) :setv types (pattern-types alternative) t types t)
      (Expression) :if (and (is-not head None) (not-in head CATCH-ALL-NAMES))
        [(get pattern 0)]
      _ (raise (SyntaxError
                 (+ "on-raise: pattern " (source-of pattern) " is not accepted — write only the form (Type field …) that names a failure type "
                    "(match-anything forms and Python exceptions are not accepted: ADR-DOE-CORE-EFFECTS-003 R7)"))))))


(deff on-raise-form [program clauses]  ; defk にできない: a function called at macro expansion time
  {:pre [(: program Object) (: clauses tuple)] :post [(: % Expression)]
   :tags {:context "doeff-hy-outcomes" :role "foundation"}}
  "The expansion of (on-raise body pattern [:if guard] target …) (R7). For each receiver, make a RaiseCase of the pattern's type and a function that returns Some(target) if the pattern matches and
   Nothing if not. The target and guard are value expressions (effects cannot be used)."
  (let [items (list clauses)
        reason (hy.gensym "reason")
        cases []]
    (when (not items)
      (raise (SyntaxError "on-raise: (on-raise body pattern target …) — there is no receiver")))
    (while items
      (let [pattern (.pop items 0)
            guard (when (and items (isinstance (get items 0) Keyword) (= (str (get items 0)) ":if"))
                    (.pop items 0)
                    (if items (.pop items 0) (raise (SyntaxError (+ "on-raise: the :if guard is missing: " (source-of pattern))))))
            mapped (if items (.pop items 0) (raise (SyntaxError (+ "on-raise: pattern " (source-of pattern) " has no target"))))]
        (for [value [guard mapped]]
          (when (and (is-not value None) (performs-effect? value))
            (raise (SyntaxError (+ "on-raise: the guard and target are value expressions (! and <- cannot be used — do a mapping that needs effects inside the body): "
                                   (source-of value))))))
        (.append cases
          `(_doeff-raise-case
             #(~@(pattern-types pattern))
             (fn [#^ object ~reason]
               ;; This match is born in the expansion after the field-name rewrite of the defk body (doeff_hy/_match_fields.py), so rewrite here —
               ;; otherwise (AppendRefused :idempotency-key k) would look up the attribute idempotency-key, silently not match, and the type-check expansion would also
               ;; fail with a SyntaxError from ast.parse.
               ~(mangle-match-fields
                  `(match ~reason
                     ~pattern ~@(if (is guard None) [] [(Keyword "if") guard]) (_doeff-some ~mapped)
                     _ _doeff-nothing)))))))
    `(do (import doeff_core_effects [on-raise :as _doeff-on-raise RaiseCase :as _doeff-raise-case])
         (import doeff.result [Some :as _doeff-some Nothing :as _doeff-nothing])
         ((_doeff-on-raise ~@cases) ~program))))


;; ---------------------------------------------------------------------------
;; absent-as(R8)
;; ---------------------------------------------------------------------------

(deff mark-direct-binds [node token]  ; defk にできない: a function called at macro expansion time
  {:pre [(: node Object) (: token Symbol)] :post [(: % Object)]
   :tags {:context "doeff-hy-outcomes" :role "foundation"}}
  "To wrap the <- and ! expressions written directly in the literal text of absent-as with (direct-bind mark expr) — absent-as resumes with the default only the Absent raised by those binds
   (R8). It does not enter nested do contexts, functions, or value forms (OPAQUE-HEADS) (an absence inside is an absence of the inner scope)."
  (let [head (head-name node)]
    (match node
      (Expression) :if (= head "<-")
        (let [split (split-absent node)
              core (. split core)
              marked (+ (list (cut core 0 -1))
                        [`(_doeff-direct-bind ~token ~(mark-direct-binds (get core -1) token))])]
          (Expression (+ marked (if (is (. split absent) None) [] [(Keyword "absent") (. split absent)]))))
      (Expression) :if (and (= head "!") (= (len node) 2))
        `(! (_doeff-direct-bind ~token ~(mark-direct-binds (get node 1) token)))
      (Expression) :if (in head OPAQUE-HEADS)
        node
      (Expression) :if (and (in head #{"handle" "on-raise"}) (>= (len node) 2))
        (Expression [(get node 0) (mark-direct-binds (get node 1) token) #* (cut node 2 None)])
      (Expression) (Expression (lfor child node (mark-direct-binds child token)))
      (FString) (FString (lfor child node (mark-direct-binds child token)) :brackets (. node brackets))
      (FComponent) (FComponent (lfor child node (mark-direct-binds child token)) :conversion (. node conversion))
      (| (List) (Tuple) (Dict)) ((type node) (lfor child node (mark-direct-binds child token)))
      _ node)))


(deff absent-as-form [default program]  ; defk にできない: a function called at macro expansion time
  {:pre [(: default Object) (: program Object)] :post [(: % Expression)]
   :tags {:context "doeff-hy-outcomes" :role "foundation"}}
  "The expansion of (absent-as default body) (R8). Make a mark at each evaluation, and if the body is (do! …), wrap the <- and ! in its literal text with the mark.
   The body itself is also wrapped with the mark (when an effect that has a declaration is passed directly, resume its absence with the default)."
  (let [token (hy.gensym "absent_token")
        body (if (= (head-name program) "do!")
                 (Expression [(get program 0) #* (lfor form (cut program 1 None) (mark-direct-binds form token))])
                 program)]
    `(do (import doeff_core_effects [absent-as :as _doeff-absent-as]
                 doeff_core_effects._outcomes [new-absent-token :as _doeff-new-absent-token direct-bind :as _doeff-direct-bind])
         (let [~token (_doeff-new-absent-token)]
           ((_doeff-absent-as ~default :token ~token) (_doeff-direct-bind ~token ~body))))))
