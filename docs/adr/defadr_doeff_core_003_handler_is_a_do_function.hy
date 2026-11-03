;;; Executable ADR: a handler is a def decorated with @handler, and @handler turns that def into a @do function (a handler body is always a @do function).
;;; @do under @handler is deprecated. handler() refuses anything that is neither a def nor a @do function, and with_handlers and stacked_handlers refuse anything
;;; that is not a handler, with a TypeError at composition time; there is no fallback that turns an unmarked object into a handler internally.
;;;
;;; Order of decisions: first it was decided not to accept anything other than a @do function as a handler (0.7.0 turned plain functions
;;; into handlers); then it was decided that a def may be written with @handler alone and that @handler @do is deprecated (the decisions are the :problem facts).
;;;
;;; Advice at the moment of writing lives outside doeff (defjevrule) (R6). The doeff side holds the refusals at construction and composition (R1, R2), the reinstall entry point (R3),
;;; the deprecated shape (R4), the 4 semgrep rules (R5), and the deftests and pytest that check them.
;;;
;;; Rollback: to return to the shape before the later decision (write with @handler alone), i.e. handlers accept only @do functions, change _as_do_function in doeff/program.py back to "refuse
;;; anything that is not a @do function", change doeff-handler-over-do-is-deprecated in .semgrep.yaml back to a rule that "refuses @handler on a def without @do", and add @do back to the repo's
;;; @handler defs. Remove this only on the maintainer's word.

(require doeff-adr.macros [defadr defsemgrep rule law])
(import doeff-adr.macros [fact interpretation counterexample])
(require doeff-hy.macros [deftest val])
(import pytest)
(import doeff [Ask Pass Pure Resume handler run with-handlers stacked-handlers])
(import doeff [do :as doeff-do])


;; Cannot be a defk: defk reads the arguments (effect k) as a handler and refuses. Sample showing that @handler turns a def into a @do function (R1).
(defn [handler] passes-on [effect k]
  "R1 sample: a def with only @handler. Passes the received effect outward unchanged."
  (yield (Pass effect k)))

;; Cannot be a defk: same as above. Counterexample sample that returns the continuation's Resume without yielding it (R1 refuses it at run time).
(defn unrun-answer [effect k]
  "R1 counterexample sample: returns the continuation's Resume without yielding it — the shape of a 0.7.0 plain-function handler."
  (Resume k 1))


(defadr ADR-DOE-CORE-003
  :title "A handler is a def decorated with @handler, and @handler turns that def into a @do function. @do under @handler is deprecated. handler() refuses anything that is neither a def nor a @do function, and with_handlers and stacked_handlers refuse anything that is not a handler, with a TypeError at composition time. Reinstalling handlers held by the VM is the job of reinstaller"
  :status "accepted"
  :scope ["doeff/program.py (handler, with_handlers, stacked_handlers, _install_raw, _as_do_function)"
          "doeff/handler_utils.py (reinstaller)"
          ".semgrep.yaml (doeff-handler-over-do-is-deprecated, doeff-handler-yields-its-control, doeff-handler-is-not-a-lambda, doeff-handler-stack-takes-only-handlers)"
          "docs/adr/defadr_doeff_core_003_handler_is_a_do_function.hy"]
  :problem
    [(fact
       "Decided in the review of the published article: a fallback where with-handlers applies @handler internally is forbidden. For type consistency, anything not properly built with @handler that is passed as a handler is an immediate error, and the linter forbids it too. doeff basically does not accept functions without @do — a handler must be a @do function, and passing a function that is @handler but not @do to with-handlers raises an exception. If this is not enforced throughout, the API differs from its intent, so fix it as p0 before writing the article.")
     (fact
       "Decided in the following review: it was questioned why one would ever write return yield x, and writing @handler @do on every handler is redundant, so allow @handler alone so that @handler def … can be written, and deprecate @handler @do.")
     (fact
       "In 0.7.0, handler()'s docstring said it accepts 'a @do function, a plain function, a bound method, a functools.partial or a callable instance', and it turned a plain function into a handler as a receiver whose returned DoExpr the VM runs. Making a plain def containing yield a handler causes, at run time, the RuntimeError 'handler must return DoExpr: expected DoExpr or EffectBase, got: <class 'generator'>'. with_handlers had a fallback that passed unmarked objects through handler() internally."
       :evidence "The docstring of doeff/program.py in doeff 0.7.0 on PyPI, and the result of running a yielding def with only @handler on 0.7.0 (2026-10-20)")
     (fact
       "try_handler, local_handler, listen_handler, Spawn, and doeff-traverse reinstalled the inner handlers read with GetHandlers(k) through handler(). What the VM holds are raw receivers (@do functions, defhandler clause data, the Rust scheduler's SchedulerPrompt), not necessarily @do functions."
       :evidence "_program_handler(h) in packages/doeff-core-effects/doeff_core_effects/_handlers.py and packages/doeff-traverse/doeff_traverse/_handlers.py")]
  :context
    [(interpretation
       "Making the handler body a single shape, a @do function, aligns the types with the reader's assumptions. What is yielded in the body runs, and the value of the yield is its result. The returned value becomes the answer of the handled block. So a continuation is written return (yield Resume(k, v)); merely returning Resume(k, v) does not run it.")
     (interpretation
       "Because @handler turns a def into a @do function, stacking @do on top adds nothing. Passing a @do function to handler() (a @do function also called as a Kleisli arrow, a partial, a bound method) denotes the same thing, so it remains accepted.")
     (interpretation
       "Reinstalling only installs again a handler that was already installed; it does not create a new handler. Separating the creation entry point (handler()) from the reinstall entry point (reinstaller) lets only the creation entry point be strict.")]
  :decision
    [(rule R1 "handler(f): if f is a def (a plain function that is not a lambda), it is made a @do function and becomes a handler. If f is a @do function, or a partial or bound method of one, it becomes a handler as is. An already marked installer is returned as is. Anything else (lambda, bound method of a plain method, callable instance, builtin) is refused at construction with TypeError '<name> is not a handler body'. Objects that cannot take (effect, k), and annotations that match no effect, are refused at composition time as before. If a def without yield returns a control expression or an effect without yielding it, it is refused at run time with a TypeError that shows the return (yield …) shape.")
     (rule R2 "with_handlers and stacked_handlers take only handlers carrying the mark _doeff_is_handler_fn (a @handler def, a defhandler, a stacked_handlers bundle), and refuse anything else at composition time with TypeError '<name> is not a handler'. They do not pass anything through handler() internally.")
     (rule R3 "Reinstalling handlers read with GetHandlers(k) is the job of doeff.handler_utils.reinstaller, which installs them directly with the VM's WithHandler. It is not used to create new handlers.")
     (rule R4 "Writing @do under @handler is deprecated. It stays accepted at run time (for users' code on PyPI 0.7.0, and for the examples in examples/testing_without_mocks and publications/zenn-use-cases-v0 in the doeff-publications repo, which run daily against the PyPI release), and semgrep refuses it inside the repo. Those 2 dirs are rewritten when a release supporting the @handler-only shape is out on PyPI.")
     (rule R5 "The 4 rules in .semgrep.yaml refuse these shapes wherever they are written outside tests: @do under @handler; returning a control expression without yield in a @handler def; a lambda passed to handler functions; passing a def without @handler from the same scope into a handler list. Tests are excluded because they build the refused shapes on purpose to check the refusal.")
     (rule R6 "Advice at the moment of writing lives outside doeff (defjevrule). The doeff side holds only R1–R5 and their checks.")]
  :laws
    [(law handler-is-a-do-function
       :statement "handler(f) ⇒ (is_def(f) ∧ body = do(f)) ∨ is_do_function(f) ∨ marked(f) ∨ TypeError ∧ with_handlers(hs, p) ⇒ ∀h∈hs. marked(h) ∨ TypeError"
       :counterexamples
         [(counterexample "with_handlers([lambda effect, k: ...], prog) passes it through handler() internally, and the lambda runs as a handler")
          (counterexample "A @handler def writes return Resume(k, v); the Resume becomes the answer value and the block ends without running the continuation")
          (counterexample "handler() is loosened for reinstalling, and a callable instance passes through the creation entry point too")
          (counterexample "A def with @handler is run under a different contract, as something that is not a Kleisli arrow and has no @do")]
       :enforced-by ["docs/adr/defadr_doeff_core_003_handler_is_a_do_function.hy::test-adr-doe-core-003-handler-makes-a-def-a-do-function"
                     "docs/adr/defadr_doeff_core_003_handler_is_a_do_function.hy::test-adr-doe-core-003-handler-stacks-take-only-handlers"
                     "tests/test_handler_is_a_do_function.py"
                     "tests/test_with_handlers_refuses_unmarked_program_function.py"
                     "defsemgrep doeff-handler-over-do-is-deprecated, doeff-handler-yields-its-control, doeff-handler-is-not-a-lambda, doeff-handler-stack-takes-only-handlers"]
       :wiring "Wired (2026-10-20) — this file's deftests and defsemgreps, and the 2 files under tests. Advice at the moment of writing is defjevrule (R6)")]
  :enforcement
    [(deftest test-adr-doe-core-003-handler-makes-a-def-a-do-function
       ;; R1: a def is made a @do function and becomes a handler; a @do function is accepted as is. A lambda and a builtin are refused at construction, and a Resume returned without yield is refused at run time.
       (assert (is (getattr passes-on "_doeff_is_handler_fn" False) True))
       (assert (is (handler passes-on) passes-on))
       (assert (= (run (passes-on (Pure "ok"))) "ok"))
       (val do-passes (handler (doeff-do (fn [effect k] (yield (Pass effect k))))))
       (assert (= (run (do-passes (Pure "ok"))) "ok"))
       (with [(pytest.raises TypeError :match "print is not a handler body")]
         (handler print))
       (with [(pytest.raises TypeError :match "is not a handler body")]
         (handler (fn [effect k] (yield (Pass effect k)))))
       (with [(pytest.raises TypeError :match "unrun_answer returned Resume")]
         (run ((handler unrun-answer) ((doeff-do (fn [] (yield (Ask "key")))))))))
     (deftest test-adr-doe-core-003-handler-stacks-take-only-handlers
       ;; R2: with_handlers and stacked_handlers refuse unmarked objects (a plain def, a @do function without @handler, a lambda) and take handlers.
       (val passes (doeff-do (fn [effect k] (yield (Pass effect k)))))
       (with [(pytest.raises TypeError :match "is not a handler")]
         (with-handlers [passes] (Pure "ok")))
       (with [(pytest.raises TypeError :match "is not a handler")]
         (with-handlers [(fn [effect k] (Pass effect k))] (Pure "ok")))
       (with [(pytest.raises TypeError :match "is not a handler")]
         (stacked-handlers passes))
       (assert (= (run (with-handlers [(handler passes)] (Pure "ok"))) "ok"))
       (assert (= (run ((stacked-handlers (handler passes)) (Pure "ok"))) "ok")))
     (defsemgrep handler-over-do-is-deprecated
       "doeff-handler-over-do-is-deprecated"
       [{"relative-path" "packages/doeff-core-effects/doeff_core_effects/example_handlers.py"
         "source" "@handler\n@do\ndef answer(effect, k):\n    return (yield Resume(k, 1))\n"}]
       [{"relative-path" "packages/doeff-core-effects/doeff_core_effects/example_handlers.py"
         "source" "@handler\ndef answer(effect, k):\n    return (yield Resume(k, 1))\n"}
        {"relative-path" "packages/doeff-core-effects/doeff_core_effects/example_non_tail.py"
         "source" "@handler\n@do(non_tail=True)\ndef answer(effect, k):\n    return (yield Resume(k, 1))\n"}
        {"relative-path" "examples/testing_without_mocks/example_pypi.py"
         "source" "@handler\n@do\ndef answer(effect, k):\n    return (yield Resume(k, 1))\n"}])
     (defsemgrep handler-yields-its-control
       "doeff-handler-yields-its-control"
       [{"relative-path" "packages/doeff-core-effects/doeff_core_effects/example_handlers.py"
         "source" "@handler\ndef answer(effect, k):\n    return Resume(k, 1)\n"}]
       [{"relative-path" "packages/doeff-core-effects/doeff_core_effects/example_handlers.py"
         "source" "@handler\ndef answer(effect, k):\n    return (yield Resume(k, 1))\n"}])
     (defsemgrep handler-is-not-a-lambda
       "doeff-handler-is-not-a-lambda"
       [{"relative-path" "examples/example_stack.py"
         "source" "program = with_handlers([lambda effect, k: Resume(k, 1)], body())\n"}
        {"relative-path" "examples/example_installer.py"
         "source" "program = handler(lambda effect, k: Resume(k, 1))(body())\n"}]
       [{"relative-path" "examples/example_stack.py"
         "source" "program = with_handlers([answer], body())\n"}])
     (defsemgrep handler-stack-takes-only-handlers
       "doeff-handler-stack-takes-only-handlers"
       [{"relative-path" "examples/example_stack.py"
         "source" "def answer(effect, k):\n    return (yield Resume(k, 1))\n\nprogram = with_handlers([answer], body())\n"}
        {"relative-path" "examples/example_bundle.py"
         "source" "def answer(effect, k):\n    return (yield Resume(k, 1))\n\nbundle = stacked_handlers(answer)\n"}]
       [{"relative-path" "examples/example_stack.py"
         "source" "@handler\ndef answer(effect, k):\n    return (yield Resume(k, 1))\n\nprogram = with_handlers([answer], body())\n"}
        {"relative-path" "examples/example_installer.py"
         "source" "def answer(effect, k):\n    return (yield Resume(k, 1))\n\nprogram = with_handlers([handler(answer)], body())\n"}])]
  :plans ["docs/adr/defadr_doeff_core_003_handler_is_a_do_function.hy"
          "doeff/program.py (_is_do_function, _as_do_function, _yields_its_control, _refuse_unmarked — handler, with_handlers, stacked_handlers)"
          "doeff/handler_utils.py (reinstaller)"
          "packages/doeff-core-effects/doeff_core_effects/_handlers.py, packages/doeff-traverse/doeff_traverse/_handlers.py (reinstalling moved to reinstaller)"
          "tests/test_handler_is_a_do_function.py, tests/test_with_handlers_refuses_unmarked_program_function.py"
          ".semgrep.yaml (4 rules)"])
