;;; Executable ADR: val, var, lazy val, lazy var, session val, session var, and (:= x value).
;;; Name binding defaults to "once only (val)", and names that are rewritten must be declared var. Deferred values (lazy — within one
;;; call) and values shared across a session (session — defhandler only, through the state effects Get / Put) are separate words.
;;; Design record = docs/design/defk-val-var-lazy/design.md.

(require doeff-adr.macros [defadr rule law])
(import doeff-adr.macros [fact interpretation counterexample])
(require doeff-hy.macros [deftest defk defp <- defhandler])
(import doeff [run EffectBase Some])
(import doeff_core_effects [state])
(import doeff_core_effects [scheduled])
(import doeff_hy._session [session-key])
(import dataclasses [dataclass])
(import types)
(import hy)
(import hy.errors)
(import pytest)

;; ---------------------------------------------------------------------------
;; Live probes — reproductions run by the enforcement deftests. probe-handler records each Probe execution in a log.
;; ---------------------------------------------------------------------------

(defclass [(dataclass :frozen True)] Probe [EffectBase]
  #^ str tag)

(defhandler probe-handler [log]
  (Probe [tag] (.append log tag) (resume tag)))

;; probe 1: lazy val — the initializer expression runs at most once regardless of the number of uses (0 times if unused)
(defk lazy-uses [uses]
  {:pre [(: uses int)] :post [(: % list)]}
  (lazy val client !(Probe "connect"))
  (var seen [])
  (for [_ (range uses)]
    (:= seen (+ seen [client])))
  seen)

;; probe 2: lazy var — if rewritten before first use, the initializer expression is not evaluated
(defk lazy-var-given [given]
  {:pre [(: given bool)] :post [(: % str)]}
  (lazy var name !(Probe "default"))
  (when given
    (:= name "given"))
  name)

;; probe 3: session val / session var — session values go through the state effects (Get / Put)
(defclass [(dataclass :frozen True)] Next [EffectBase])

(defhandler counter-handler
  (session var count 0)
  (Next []
    (:= count (+ count 1))
    (resume count)))

(defp two-nexts
  {:post [(: % list)]}
  (<- a (Next))
  (<- b (Next))
  [a b])

(defk expansion-error [code]
  {:pre [(: code str)] :post [(: % str)]}
  "Confirm that expanding a Hy fragment is an error, and return its message."
  (val module (types.ModuleType "_adr_doe_hy_006_probe"))
  (with [caught (pytest.raises hy.errors.HyMacroExpansionError)]
    (hy.eval (hy.read-many (+ "(require doeff-hy.macros [defk])\n" code)) :module module))
  (str caught.value))


(defadr ADR-DOE-HY-006
  :title "In the bodies of defk, deftest, and defhandler and at module top level, name binding is written as val (once only), var (rewritten with :=), and lazy val / lazy var (evaluated on first use within that call). Values shared across a session go in defhandler's session val / session var, through the state effects (Get / Put). The old lazy-val / lazy-var / set! keep their behavior in defhandler and warn about migration; in defk they are errors"
  :status "accepted"
  :scope ["packages/doeff-hy/src/doeff_hy/_binding_forms.py"
          "packages/doeff-hy/src/doeff_hy/_lazy.py"
          "packages/doeff-hy/src/doeff_hy/_session.py"
          "packages/doeff-hy/src/doeff_hy/macros.hy"
          "packages/doeff-hy/src/doeff_hy/handle.hy"
          "packages/doeff-hy/src/doeff_hy/_static_check.py"
          "docs/adr/defadr_doeff_hy_006_val_var_lazy.hy"]
  :problem
    [(fact
       "Decision: bring Scala-style lazy and val / var into doeff's defk / deftest so one can write (lazy val some_variable !(effectful-expr)). The default is val, and var must be written explicitly. setv gets a warning recommending var / val, and four forms are provided: val, var, lazy val, lazy var. The name is session. defhandler's lazyval / lazyvar keep their behavior for now and warn to move to session val / session var."
       :evidence "Design record docs/design/defk-val-var-lazy/design.md §1")
     (fact
       "The old lazy / lazy-val / lazy-var meant \"a value stored in the state effects across sessions\" in both defhandler and defk. lazy inside defk is hidden state that does not appear in the function's contract, and its meaning collides with the new lazy val (deferral within that call) under the same word."
       :evidence "defk in packages/doeff-hy/src/doeff_hy/macros.hy (lazy injection before 2026-09-26), _build-lazy-init-forms in handle.hy")
     (fact
       "Measured across 6 mainline repos (agora-controllers, proboscis-ema, doeff, agent-control-plane, argus, agora): 0 old lazy / set! inside defk / deftest; 115 old lazy clauses and 61 clauses with set! in defhandler. Of 7,381 defk, 1,155 (15.6%) rebind the same name."
       :evidence "Design record §10 (the new analysis binding_forms.rewrite_body applied as-is to each repo's origin/main)")
     (fact
       "The Hy 1.3.0 reader reads !(f) as two elements, ! and (f), and reads the head of (:= x 1) as a Keyword. (:= x v) at module top level does not go through a macro; it compiles as a keyword call and rewrites nothing."
       :evidence "packages/doeff-hy/tests/test_val_var_lazy.py::test_bang_written_as_two_tokens_is_the_same_as_the_bang_form, design record §3")]
  :context
    [(interpretation
       "Rebinding errors where new syntax is involved (val twice, setv of a val name, etc.) can be expansion errors from the start (existing code has no new syntax). Rebinding between old forms exists in 15.6% of existing defk, so making it an expansion error at once would break mainline — first make it red in the static check, and switch to an expansion error after the count reaches 0.")
     (interpretation
       "If session values are not kept in a hidden Python location but go through the state effects, an outer handler can answer Get for the same key to substitute the value, and can receive Put to observe writes. Publishing one key-building function lets outer handlers and checks look up the same key.")]
  :decision
    [(rule R1 "(val x expr) is a once-only binding. Binding the same name again (another val, setv, <-, for, augmented assignment) is an expansion error. Mutating the value's contents (subscript or attribute assignment, methods) is out of scope. (<- x effect) is another way to write val.")
     (rule R2 "(var x expr) is rewritten only by (:= x new-value) (inside the body). setv of a var name, and := on a val, a session val, or an undeclared name, are expansion errors.")
     (rule R3 "(lazy val x expr) evaluates and memoizes expr only when x is first used within that call (one run of a defk, one run of a deftest, one run of a defhandler clause). If unused, it is not evaluated. If the first evaluation raises, nothing is memoized and it is evaluated again on the next use. (lazy var x expr) can additionally be rewritten with :=, and if rewritten before first use the initializer expression is not evaluated.")
     (rule R4 "A lazy reference is a bare name; the macro rewrites the reference. A lazy declaration may only be written in the outermost sequence of the body. References where yield is impossible (fn, comprehensions, nested definitions, handle / defhandler clauses), references before the declaration, and shadowing with the same name are expansion errors, with guidance to \"take it out first with (val v x)\".")
     (rule R5 "In the value part of a declaration or :=, `! expr` (the reader's two-element reading of !(f)) is accepted the same as (! expr).")
     (rule R6 "Rebinding between old forms (the same name twice via setv, <-, or augmented assignment — for variables and mutually exclusive branches are not counted) is red in doeff-hy-check (doeff-hy-rebind). The switch to an expansion error happens after doeff-hy-rebind reaches 0 on the mainline of each target repo (procedure = design record §8.3).")
     (rule R7 "setv in the bodies of defk, deftest, and defhandler clauses, and setv at module top level (those that bind a name), get a doeff-hy-check warning (doeff-hy-setv) recommending val or var. Expansion is not stopped.")
     (rule R8 "(session val x expr) / (session var x expr) may only be written directly under defhandler. The value is read and written through the state effects Get / Put, and the key is doeff_hy._session.session_key(module, handler, name) = \"<module>/<handler>/<name>\" (the same string as the old lazy-val / lazy-var). := on a session var writes back to storage (Put). session in defk / deftest is an expansion error.")
     (rule R9 "The old lazy / lazy-val / lazy-var / set! in defhandler keep their behavior entirely unchanged, and an expansion-time DeprecationWarning plus a doeff-hy-check warning (doeff-hy-legacy-lazy) guide migration to session val / session var / :=. The old lazy / lazy-val / lazy-var / set! in defk / deftest are expansion errors.")
     (rule R10 "At module top level one may write (val x expr), (var x expr), and (lazy val x expr) (require doeff-hy.macros [val var lazy]). lazy val is only for expressions that use no effects (an effectful expression is an error, with guidance to defhandler's session val). At module top level, lazy var, session, val / var expressions that use effects, and a second binding of the same name are expansion errors. A module var is rewritten with setv at module top level (in Hy, := at module top level does not go through the macro).")]
  :laws
    [(law lazy-val-evaluates-at-most-once-per-call
       :statement "for_all n >= 0: effects(call(lazy-uses n)) == (if (= n 0) [] [\"connect\"]) — per call, the initializer expression runs at most once, and 0 times if unused"
       :counterexamples
         [(counterexample "The old defk lazy stored its value in the session, so on a second call in the same session the initializer did not run, and the first value was reused even for values that depend on arguments")
          (counterexample "With setv, which evaluates where written, effects run even in branches that do not use the value")])
     (law session-values-flow-through-state-effects
       :statement "for_all handler h with (session var x e): the value of x is read by (Get (session-key module h x)) and written by (Put (session-key module h x) (Some v)) — not kept in a hidden Python location"
       :counterexamples
         [(counterexample "If the value is kept in a handler closure or a module variable, an outer handler can neither substitute the value nor observe writes")])
     (law new-syntax-rebinding-is-loud
       :statement "for_all name declared by val / var / lazy / session: a second binding of name (other than := on a var) => expansion-time SyntaxError with fix prompt"
       :counterexamples
         [(counterexample "(val x 1) (setv x 2) — passing it silently breaks val's promise")])]
  :enforcement
    [(deftest test-adr-doe-hy-006-lazy-val-at-most-once
       (for [#(uses expected) [#(0 []) #(1 ["connect"]) #(3 ["connect"])]]
         (setv log [])
         (setv seen (run ((probe-handler log) (lazy-uses uses))))
         (assert (= (len seen) uses))
         (assert (= log expected) (+ "Wrong number of initializer evaluations: " (str log)))))
     (deftest test-adr-doe-hy-006-lazy-var-assigned-before-use-skips-init
       (setv log [])
       (assert (= (run ((probe-handler log) (lazy-var-given True))) "given"))
       (assert (= log []) "The initializer ran even though the var was rewritten before first use")
       (setv log2 [])
       (assert (= (run ((probe-handler log2) (lazy-var-given False))) "default"))
       (assert (= log2 ["default"])))
     (deftest test-adr-doe-hy-006-session-var-uses-the-state-key
       (setv key (session-key __name__ "counter-handler" "count"))
       (assert (= (run (scheduled ((state) (counter-handler two-nexts)))) [1 2]))
       (assert (= (run (scheduled ((state :initial {key (Some 10)}) (counter-handler two-nexts)))) [11 12])
               "session var is not read from the state-effect key"))
     (deftest test-adr-doe-hy-006-val-twice-is-an-expansion-error
       (<- message (expansion-error "(defk f [x] {:pre [(: x int)] :post [(: % int)]} (val y 1) (val y 2) y)"))
       (assert (in "is already declared with (val y \u2026)" message) message))
     (deftest test-adr-doe-hy-006-legacy-lazy-in-defk-is-an-expansion-error
       (<- message (expansion-error "(defk f [] {:pre [] :post [(: % str)]} (lazy-val c \"c\") c)"))
       (assert (in "(lazy val c expr)" message) message)
       (assert (in "(session val c expr)" message) message))]
  :plans ["docs/design/defk-val-var-lazy/design.md"])
