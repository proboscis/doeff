;;; Answer declarations and the translation of <- (ADR-DOE-CORE-EFFECTS-003 stage 2 - :absent / :failure / :value of defeffect and
;;; doeff_core_effects._outcomes.open-bind).
;;;
;;;   * <- on an effect with declarations binds success and value, turns absence into Absent and failure into Raise(answer), and emits them in the caller's scope
;;;   * the same binding line does not change meaning under a boundary handler the caller places (x is always the success type)
;;;   * <- on an effect without declarations yields the same thing as before and binds the same answer (Missing, None, and Nothing as they are)
;;;   * the :absent <failure> of <- turns an absence that came out within that binding (directly or from deeper) into Raise(failure), and builds the failure only when absent
;;;   * <- opens Result / Maybe values (a Python exception inside Err stays an exception)
;;;   * absent-as resumes only the absence of a <- inside its literal body with the default, and ends the scope with the default for a deeper absence or a directly written Absent
;;;   * a handler that resumes Absent / Raise is an error (it does not silently continue)

(require doeff-hy.macros [deftest defk deff defeffect defhandler <- val do! on-raise absent-as])

(import dataclasses [dataclass])
(import hy)
(import pytest)
(import doeff [run EffectBase DoExpr K])
(import doeff.program [Resume Pass])
(import doeff.result [Nothing Some])
(import doeff [Call Err Ok WithHandlerType])
(import doeff_core_effects [Absent Raise])
(import doeff_core_effects [maybe result]
        doeff_core_effects._outcomes [open-bind Outcomes])
(import doeff_core_effects [_outcomes :as outcomes])
(import unittest [mock])


(defclass [(dataclass :frozen True)] Row []
  "A row that could be read (the success answer, for the test)."
  #^ str value)

(defclass [(dataclass :frozen True)] Missing []
  "No row (the absent answer, for the test)."
  #^ str key)

(defclass [(dataclass :frozen True)] Unreachable []
  "Cannot reach the store (the failure answer, for the test)."
  #^ str detail)

(defclass [(dataclass :frozen True)] Stale []
  "Version lost (an answer treated as ordinary in business - :value)."
  #^ int version)

(defclass [(dataclass :frozen True)] Conflict []
  "A failure that the caller throws with :absent."
  #^ str detail)


(defeffect ReadRow
  "A read whose answer is declared as split into success, absence, failure, and value."
  {:fields [(: key str)]
   :answer (| Row Missing Unreachable Stale)
   :absent [Missing]
   :failure [Unreachable]
   :value [Stale]
   :tags {:context "outcomes-test" :role "intent"}})

(defeffect PlainRead
  "A read without declarations (the answer type is the same union)."
  {:fields [(: key str)]
   :answer (| Row Missing Unreachable None)
   :tags {:context "outcomes-test" :role "intent"}})


(val TABLE {"a" (Row "A") "down" (Unreachable "network down") "old" (Stale 3) "none" None})


(defhandler table-rows
  "A foundation handler that answers ReadRow and PlainRead from the table TABLE by value (both absence and failure are answer values)."
  {:tags {:context "outcomes-test" :role "foundation"}}
  (ReadRow [key] (resume (.get TABLE key (Missing key))))
  (PlainRead [key] (resume (.get TABLE key (Missing key)))))


(defk read-value [key]
  {:pre [(: key str)] :post [(: % (| str Stale))]
   :tags {:context "outcomes-test" :role "program"}}
  "Bind one ReadRow that has declarations - x is only the success type or the value type."
  (<- row (ReadRow key))
  (if (isinstance row Row) row.value row))


(defk read-value-bang [key]
  {:pre [(: key str)] :post [(: % str)]
   :tags {:context "outcomes-test" :role "program"}}
  "! opens the same as <- does."
  (+ "got " (. (! (ReadRow key)) value)))


(deftest test-declaration-splits-the-answer
  (val outcomes ReadRow.__doeff_outcomes__)
  (assert (isinstance outcomes Outcomes))
  (assert (= #(outcomes.success outcomes.absent outcomes.failure outcomes.value)
             #(#(Row) #(Missing) #(Unreachable) #(Stale)))
          outcomes)
  (assert (is (getattr PlainRead "__doeff_outcomes__" None) None) "nothing is placed unless declared"))


(defk expansion-refusal [source]
  {:pre [(: source str)] :post [(: % str)]
   :tags {:context "outcomes-test" :role "judgment"}}
  "The error message when source is expanded (an empty string if it passes) - to check by test the macro rule that refuses at expansion time."
  (try
    (hy.eval (hy.read-many source))
    (return "")
    (except [e hy.errors.HyMacroExpansionError]
      (return (str e)))))


(deftest test-declaration-refuses-types-outside-the-answer
  (val head "(require doeff-hy.macros [defeffect]) (defeffect Bad {:answer (| Row Missing) :tags {:context \"t\" :role \"intent\"} ")
  (assert (in "is not an element of :answer" (! (expansion-refusal (+ head ":absent [Unreachable]})")))))
  (assert (in "in both" (! (expansion-refusal (+ head ":absent [Missing] :failure [Missing]})")))))
  (assert (in "list" (! (expansion-refusal (+ head ":absent Missing})"))))))


(deftest test-declared-bind-opens-the-answer
  (<- found (table-rows (maybe (read-value "a"))))
  (<- absent (table-rows (maybe (read-value "zz"))))
  (<- failed (table-rows (result (read-value "down"))))
  (<- stale (table-rows (read-value "old")))
  (assert (= found (Some "A")) found)
  (assert (is absent Nothing) absent)
  (assert (= (repr failed) "Err(Unreachable(detail='network down'))") failed)
  (assert (= stale (Stale 3)) "an answer declared as a value is bound (not made a failure)")
  (<- banged (table-rows (maybe (read-value-bang "a"))))
  (<- banged-absent (table-rows (maybe (read-value-bang "zz"))))
  (assert (= #(banged banged-absent) #((Some "got A") Nothing)) #(banged banged-absent)))


(deftest test-a-bind-has-one-meaning-under-any-caller
  ;; The same read-value line has x = the success type under any of the caller's maybe / result / on-raise (R3, law a-bind-has-one-meaning)
  (<- under-maybe (table-rows (maybe (read-value "a"))))
  (<- under-result (table-rows (result (read-value "a"))))
  (<- under-on-raise (table-rows (on-raise (read-value "a") (Unreachable d) d)))
  (assert (= #(under-maybe (repr under-result) under-on-raise) #((Some "A") "Ok('A')" "A"))
          #(under-maybe under-result under-on-raise)))


(defk read-plain [key]
  {:pre [(: key str)] :post [(: % (| Row Missing Unreachable (type None)))]
   :tags {:context "outcomes-test" :role "program"}}
  "Bind PlainRead, which has no declarations - return the answer as it is."
  (<- row (PlainRead key))
  row)


(defclass [(dataclass :frozen True)] Echo [EffectBase]
  "An effect without declarations (a defclass one) - receives the value passed as its answer itself."
  #^ object value)

(defhandler echo-handler
  "Answer Echo with that value."
  {:tags {:context "outcomes-test" :role "foundation"}}
  (Echo [value] (resume value)))

(defk bind-each [a b c d e]
  {:pre [(: a Missing) (: b (type None)) (: c (type Nothing)) (: d Err) (: e Unreachable)] :post [(: % tuple)]
   :tags {:context "outcomes-test" :role "program"}}
  "Bind the answer values of an effect without declarations (Missing, None, Nothing, Err, Unreachable) one by one - the same thing comes back without translation."
  (<- ga (Echo a))
  (<- gb (Echo b))
  (<- gc (Echo c))
  (<- gd (Echo d))
  (<- ge (Echo e))
  #(ga gb gc gd ge))


(defk bang-in-assignment-target [box]
  {:pre [(: box dict)] :post [(: % dict)]
   :tags {:context "outcomes-test" :role "program"}}
  "Write ! inside an assignment target (`(setv (get (! e) key) value)`) - to see that the binding form is an expression that holds no statements (in a form
   that holds an import statement, Hy cannot build the target and the compile of this file fails)."
  (setv (get (! (Echo box)) "k") "v")
  box)


(deftest test-bang-is-an-expression-in-an-assignment-target
  (<- got (echo-handler (bang-in-assignment-target {})))
  (assert (= got {"k" "v"}) got))


(deftest test-undeclared-bind-is-unchanged
  ;; (c) <- on an effect without declarations yields the same thing as before and binds the answer unchanged
  (val effect (PlainRead "a"))
  (assert (is (open-bind effect) effect) "an effect without declarations yields the same thing")
  (val program (read-plain "a"))
  (assert (is (open-bind program) program) "a Program also yields the same thing")
  (val answers #((Missing "zz") None Nothing (Err "e") (Unreachable "u")))
  (<- got (echo-handler (bind-each #* answers)))
  (assert (all (gfor #(bound given) (zip got answers :strict True) (is bound given))) "answers are bound as the same thing")
  ;; Missing / Unreachable / None of PlainRead are also not translated (no Absent / Raise comes out - it does not stop even with no receiver)
  (<- plain-missing (table-rows (read-plain "zz")))
  (<- plain-down (table-rows (read-plain "down")))
  (<- plain-none (table-rows (read-plain "none")))
  (assert (= #(plain-missing plain-down plain-none) #((Missing "zz") (Unreachable "network down") None))
          #(plain-missing plain-down plain-none)))


(deftest test-yielding-defk-bind-skips-the-python-dispatch
  ;; #2449: binding a defk call (Call) that emits effects without :absent makes open-bind return that call itself inside doeff-vm,
  ;; and it does not enter the Python dispatch (_opened). The answer is the same as the dispatch's answer. The slow form = every binding went through
  ;; _open-bind -> _opened -> _settled and returned the call as it was (13.8k times in the custody contract example).
  (val program (read-plain "a"))
  (assert (is (outcomes._open-bind program) program) "the Python dispatch's answer is also the call itself")
  (with [spy (mock.patch.object outcomes "_opened" :wraps outcomes._opened)]
    (assert (is (open-bind program) program) "the binding yields the call itself")
    (assert (= spy.call-count 0) f"a call of a defk that emits effects does not enter the Python dispatch: {spy.call-count}")
    ;; An effect without translation declarations also yields the same thing without entering the dispatch, using the answer remembered per type (_passes-through).
    ;; An effect with translation declarations is passed to the dispatch (test-declared-bind-opens-the-answer).
    (val effect (PlainRead "a"))
    (assert (is (open-bind effect) effect) "binding an effect without translation declarations yields the same thing")
    (assert (= spy.call-count 0) f"binding an effect without translation declarations does not enter the Python dispatch: {spy.call-count}"))
  (<- got (table-rows (read-plain "a")))
  (assert (= got (Row "A")) got))


(defk judge-row [x]
  {:pre [(: x int)] :post [(: % bool)]
   :tags {:context "outcomes-test" :role "program"}}
  "A judgment that emits no effects (no yield in the body) - the shape of a per-row judgment on a screen."
  (> x 3))

(defk count-judged [xs]
  {:pre [(: xs list)] :post [(: % int)]
   :tags {:context "outcomes-test" :role "program"}}
  "Bind a judgment with <- and count it, and finally bind it with ! too."
  (var n 0)
  (for [x xs]
    (<- ok (judge-row x))
    (when ok (:= n (+ n 1))))
  (if (! (judge-row 9)) n 0))

(defk judge-then-read [x]
  {:pre [(: x int)] :post [(: % (| Row Missing Unreachable (type None)))]
   :tags {:context "outcomes-test" :role "program"}}
  "After a judgment that emits no effects, bind a defk that emits effects."
  (<- ok (judge-row x))
  (<- row (read-plain (if ok "a" "zz")))
  row)


(deftest test-effect-free-defk-bind-runs-in-place
  ;; #844: binding a defk that emits no effects with <- / ! calls it on the spot and binds the answer, without yielding to the VM.
  ;; The slow form = every binding yielded a Program to the VM, and the VM called the function and returned the answer by send (a few microseconds each).
  (val steps ((. (count-judged [1 5 7]) function function) [1 5 7]))
  (with [stopped (pytest.raises StopIteration)]
    (next steps))
  (assert (= (. stopped value value) 2) "the binding finishes without yielding even once")
  (assert (= (. (open-bind (judge-row 5)) value) True) "open-bind returns the answer's Pure")
  ;; Binding a defk that emits effects yields to the VM as before (even after a binding that emits no effects)
  (val reads ((. (judge-then-read 5) function function) 5))
  (assert (isinstance (next reads) Call) "a call of a defk that emits effects is passed to the VM")
  ;; The answer run in the VM is the same
  (<- judged (count-judged [1 5 7]))
  (assert (= judged 2) judged))


(deftest test-effect-free-defk-bind-raises-where-it-binds
  ;; It is called at the binding site, so a contract violation (:pre) rises from that binding's position (the same position as when run through the VM)
  (with [(pytest.raises BaseException :match "judge-row")]
    (run (count-judged [1 "x"]))))


(defclass [(dataclass :frozen True)] Shaped [EffectBase]
  "An effect whose field names are builtin names (type, len, list) (a test of the binding form's hygiene)."
  #^ str type
  #^ int len
  #^ list list)

(defhandler shadowing-handler
  "An arm whose argument name is a builtin name and an arm that binds with <- are in the same handler - Python treats type as a local name over the handler's whole function."
  {:tags {:context "outcomes-test" :role "foundation"}}
  (Shaped [type len list] (resume #(type len list)))
  (Echo [value]
    (<- ok (judge-row value))
    (resume ok)))

(defk judge-shadowed [type len list]
  {:pre [(: type str) (: len int) (: list tuple)] :post [(: % tuple)]
   :tags {:context "outcomes-test" :role "program"}}
  "Bind inside a defk whose argument name is a builtin name."
  (<- ok (judge-row len))
  #(type ok list))

(defk echo-and-shape []
  {:pre [] :post [(: % tuple)]
   :tags {:context "outcomes-test" :role "program"}}
  "Emit Echo and Shaped."
  (<- judged (Echo 5))
  (<- shaped (Shaped "t" 2 [1]))
  #(judged shaped))


(deftest test-bind-does-not-read-the-callers-names
  ;; Cleanup of L562: the binding form looked up the builtin type by name, so in a handler that has an arm with type as an argument
  ;; (Relate of kanban-board-records in agora-controllers), the <- of another arm failed with UnboundLocalError
  (<- got (shadowing-handler (echo-and-shape)))
  (assert (= got #(True #("t" 2 [1]))) got)
  (<- shadowed (judge-shadowed "t" 5 #(1)))
  (assert (= shadowed #("t" True #(1))) shadowed))


;; Record the key that built the :absent failure (to check that the :absent failure is built only when absent). The failure is a value expression (! / <- cannot be written), so
;; the building site is written inside the :absent expression rather than as a function - open-bind evaluates that expression only when absent.
(val FAILURES-BUILT [])

(defk deep-read [key]
  {:pre [(: key str)] :post [(: % (| str Stale))]
   :tags {:context "outcomes-test" :role "program"}}
  "A defk that emits an absence deeper (it only calls read-value)."
  (<- v (read-value key))
  v)

(defk read-or-conflict [key]
  {:pre [(: key str)] :post [(: % (| str Stale))]
   :tags {:context "outcomes-test" :role "program"}}
  "(e) With :absent, throw this binding's absence (including one that came out inside a deeper defk) as a Conflict failure."
  (<- v (deep-read key) :absent (do (.append FAILURES-BUILT key) (Conflict (+ key ": no such row"))))
  v)

(defk nothing-or-conflict []
  {:pre [] :post [(: % str)]
   :tags {:context "outcomes-test" :role "program"}}
  "Open the Maybe value Nothing with :absent."
  (<- v Nothing :absent (do (.append FAILURES-BUILT "nothing") (Conflict "nothing: no such row")))
  v)


(deftest test-absent-option-maps-this-bind-to-a-failure
  (.clear FAILURES-BUILT)
  (<- found (table-rows (result (read-or-conflict "a"))))
  (assert (= (repr found) "Ok('A')") found)
  (assert (= FAILURES-BUILT []) "no failure value is built on success")
  (<- mapped (table-rows (result (read-or-conflict "zz"))))
  (assert (= (repr mapped) "Err(Conflict(detail='zz: no such row'))") mapped)
  (<- down (table-rows (result (read-or-conflict "down"))))
  (assert (= (repr down) "Err(Unreachable(detail='network down'))") "failure stays as it is (:absent maps only absence)")
  (<- from-nothing (result (nothing-or-conflict)))
  (assert (= (repr from-nothing) "Err(Conflict(detail='nothing: no such row'))") from-nothing)
  (assert (in "value expression" (! (expansion-refusal "(require doeff-hy.macros [<-]) (<- x (f) :absent (! (g)))"))))
  (assert (in "unknown key" (! (expansion-refusal "(require doeff-hy.macros [<-]) (<- x (f) :absnt 1)")))))


(deftest test-bind-refuses-a-plain-value-in-the-value-position
  ;; Failure case: (<- split (if (is existing None) None (observe-split ...))) on a card with no row
  ;; yielded a bare None, and the VM crashed with TypeError (yield got None). A binding that has a literal value at a value position (the expression itself, an if branch, a cond value branch,
  ;; the fallthrough of when / unless) is refused at expansion time, with <- and also with !.
  (val head "(require doeff-hy.macros [<- do!]) (fn [c] ")
  (val bad #(#("(<- x (if c None (f))) x)" "literal value None")
             #("(<- x None) x)" "literal value None")
             #("(<- x (if c (f) (if c (g) []))) x)" "literal value []")
             #("(<- x (cond c (f) True False)) x)" "literal value False")
             #("(<- (when c (f))))" "literal value (when c (f))")
             #("(<- x {\"a\" 1}) x)" "Pure {\"a\" 1}")
             #("(do! (+ 1 (! (if c 0 (g))))))" "a literal value 0")))
  (for [#(body said) bad]
    (val refused (! (expansion-refusal (+ head body))))
    (assert (in said refused) #(body refused)))
  ;; Branches wrapping the value in Pure, expressions where a literal value is not at a value position (an argument of a call), and expressions where every branch is a Program all pass.
  (val clean #("(<- x (if c (Pure None) (f))) x)"
               "(<- x (f (if c None 1))) x)"
               "(<- x (cond c (f) True (Pure False))) x)"
               "(<- x (if c (g) (f))) x)"
               "(do! (+ 1 (! (if c (Pure 0) (g))))))"))
  (for [body clean]
    (val passed (! (expansion-refusal (+ head body))))
    (assert (= passed "") #(body passed))))


(defk open-value [value]
  {:pre [(: value (| Ok Err Some (type Nothing)))] :post [(: % int)]
   :tags {:context "outcomes-test" :role "program"}}
  "Open Result / Maybe values with <-."
  (<- x value)
  (+ x 1))


(deftest test-bind-opens-result-and-option-values
  (<- from-ok (result (open-value (Ok 1))))
  (<- from-err (result (open-value (Err "reason"))))
  (<- from-some (maybe (open-value (Some 2))))
  (<- from-nothing (maybe (open-value Nothing)))
  (assert (= (repr #(from-ok from-err from-some from-nothing)) "(Ok(2), Err('reason'), Some(3), Nothing)")
          #(from-ok from-err from-some from-nothing))
  ;; A Python exception inside Err (an implementation mistake that Try folded in) is raised as an exception, not as Raise (R11)
  (with [(pytest.raises ValueError :match "implementation")]
    (run (result (open-value (Err (ValueError "implementation bug")))))))


(defk direct-default [key]
  {:pre [(: key str)] :post [(: % str)]
   :tags {:context "outcomes-test" :role "program"}}
  "An absence of a <- inside the literal body is resumed with the default, and the continuation runs."
  (<- v (absent-as (Row "default") (do! (<- row (ReadRow key)) (+ "continued: " row.value))))
  v)

(defk deep-default [key]
  {:pre [(: key str)] :post [(: % str)]
   :tags {:context "outcomes-test" :role "program"}}
  "An absence deeper inside a called defk is not resumed, and the scope ends with the default."
  (<- v (absent-as "default" (do! (<- got (read-value key)) (+ "continued: " got))))
  v)

(defk written-absent-default []
  {:pre [] :post [(: % str)]
   :tags {:context "outcomes-test" :role "program"}}
  "A directly written (<- (Absent ...)) is not resumed, and the scope ends with the default."
  (<- v (absent-as "default" (do! (<- (Absent "written absence")) "continued")))
  v)


(deftest test-absent-as-resumes-only-direct-binds
  (<- direct (table-rows (direct-default "zz")))
  (<- deep (table-rows (deep-default "zz")))
  (<- written (written-absent-default))
  (<- present (table-rows (direct-default "a")))
  (assert (= #(direct deep written present) #("continued: default" "default" "default" "continued: A"))
          #(direct deep written present)))


(deff resume-anything [effect k]  ; cannot be a defk: defk reads the arguments (effect k) as a handler and refuses, and defhandler refuses resuming Raise / Absent - a counterexample handler that breaks the rule can only be written as a bare handler function
  {:pre [(: effect EffectBase) (: k K)] :post [(: % DoExpr)]
   :tags {:context "outcomes-test" :role "foundation"}}
  "A handler that ends up resuming Raise / Absent (a counterexample that breaks the rule) - to see that the opening side treats it as an error."
  (if (isinstance effect #(Raise Absent))
      (Resume k "continued anyway")
      (Pass effect k)))


(defk raises-then-continues []
  {:pre [] :post [(: % str)]
   :tags {:context "outcomes-test" :role "program"}}
  "Emit Raise (it is an error if it is resumed)."
  (<- (Raise (Conflict "c")))
  "continued")


(deftest test-resuming-raise-or-absent-is-an-error
  (with [(pytest.raises RuntimeError :match "Raise was resumed")]
    (run (WithHandlerType resume-anything (raises-then-continues))))
  (with [(pytest.raises RuntimeError :match "Absent was resumed")]
    (run (WithHandlerType resume-anything (table-rows (read-value "zz"))))))
