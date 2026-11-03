;;; Executable ADR: a bang (!) inline bind must be observationally equivalent to "hand-writing (yield expr) at the
;;; position where it was written" (evaluation-position preservation).
;;; The former "uniform hoist to just before the statement" produced 4 pathologies — branch escape, ignored short-circuit,
;;; reversed evaluation order, unexpanded inside literals — so it is abolished and replaced with in-place (yield expr) substitution.
;;; Positions where yield cannot syntactically be placed (comprehensions, nested fn) become an expansion-time
;;; SyntaxError in fix-prompt form (same philosophy as ADR-DOE-HY-001's guard-error).

(require doeff-adr.macros [defadr rule law])
(import doeff-adr.macros [fact interpretation counterexample])
(require doeff-hy.macros [deftest defk deff do! <- defhandler])
(import doeff [run EffectBase])
(import dataclasses [dataclass])
(import types)
(import hy)
(import hy.errors)
(import pytest)


;; ---------------------------------------------------------------------------
;; Live probes — reproductions executed by the enforcement deftests.
;; Execution of ProbeEff is observed by probe-handler appending the tag to log (a Python list).
;; ---------------------------------------------------------------------------

(defclass [(dataclass :frozen True)] ProbeEff [EffectBase]
  #^ str tag)

(defclass [(dataclass :frozen True)] BoomEff [EffectBase])

(defhandler probe-handler [log]
  (ProbeEff [tag]
    (.append log tag)
    (resume tag))
  (BoomEff []
    (raise (ValueError "boom"))))

(deff record-step [log tag]
  {:pre [(: log list) (: tag str)]
   :post [(: % str)]}
  (.append log tag)
  tag)

;; probe 1: branch conditionality — a ! inside the False branch must not run (trap: hoist runs it unconditionally outside the if)
(defk probe-branch [urgent]
  {:pre [(: urgent bool)] :post [(: % str)]}
  (if urgent
      (setv r (! (ProbeEff :tag "alert")))
      (setv r "skipped"))
  r)

;; probe 2: short-circuit — (and False (! ...)) must not run the effect
(defk probe-short-circuit [flag]
  {:pre [(: flag bool)] :post [(: % "False or a tag string")]}
  (setv r (and flag (! (ProbeEff :tag "sc-eff"))))
  r)

;; probe 3: a ! inside a dict literal works (trap: the expander does not walk the literal, giving a runtime NameError)
(defk probe-dict-literal []
  {:pre [] :post [(: % dict)]}
  {"k" (! (ProbeEff :tag "dict-eff"))})

;; probe 4: evaluation order within one statement — the pure argument first, the ! after (trap: hoist moves the ! to the start of the statement)
(defk probe-eval-order [log]
  {:pre [(: log list)] :post [(: % list)]}
  [(record-step log "pure-first") (! (ProbeEff :tag "eff-second"))])

;; probe 5: try context — a ! inside try is evaluated inside the try, and the handler's exception reaches except
(defk probe-try-catch []
  {:pre [] :post [(: % str)]}
  (try
    (setv r (! (BoomEff)))
    (except [e ValueError]
      (setv r "caught")))
  r)

;; probe 8: backward compatibility — previously legal bangs in simple statement / argument positions keep the same semantics
(defk probe-legacy-simple []
  {:pre [] :post [(: % str)]}
  (setv first-val (! (ProbeEff :tag "legacy-1")))
  (+ first-val "+" (! (ProbeEff :tag "legacy-2"))))


(defadr ADR-DOE-HY-003
  :title "bang(!) evaluation-position preservation: (! expr) is replaced in place by (yield expr) at its occurrence position at expansion time, preserving the evaluation semantics of the written position (conditionality, short-circuiting, evaluation order, exception context, evaluation inside literals). Hoisting to just before the statement is abolished. A ! at a position where yield cannot syntactically be placed (comprehensions, nested fn) is an expansion-time SyntaxError in fix-prompt form — give agent authors a deterministic, actionable red rather than a silent semantic shift"
  :status "proposed"
  :scope ["packages/doeff-hy/src/doeff_hy/macros.hy"
          "packages/doeff-hy/src/doeff_hy/handle.hy"
          "docs/adr/defadr_doeff_hy_003_bang_evaluation_position.hy"]
  :problem
    [(fact
       "The current _expand-bangs uniformly hoists every (! expr) to a (<- _bang_N expr) just before the statement. A ! inside if/and/or/try is also moved outside the control structure, so (if urgent? (setv r (! (send-alert))) ...) runs send-alert even when urgent? is False. No error or warning appears."
       :evidence "packages/doeff-hy/src/doeff_hy/macros.hy:979-1026 (as of 2026-07-14); ACP ADR-0056 migration measurements, idioms (g)(h)(l)(m)")
     (fact
       "Even within one statement, hoisting reverses evaluation order: in (process (setup-step) (! (fetch))), fetch moves to the start of the statement and runs before setup-step."
       :evidence "packages/doeff-hy/src/doeff_hy/macros.hy:992-1000 walk extracts a ! in argument position to the head of bindings")
     (fact
       "walk descends only into hy.models.Expression and List, so a ! inside a dict/set/tuple/f-string literal stays unexpanded and becomes the cryptic runtime error NameError: hyx_Xexclamation_markX. The same holds for let binding-value positions."
       :evidence "packages/doeff-hy/src/doeff_hy/macros.hy:1017-1023 (no Dict/Set/Tuple/FString branch)")
     (fact
       "Effects inside comprehensions (lfor/gfor) cannot yield under Python syntax, but currently the hoist silently changes the semantics with no clear error or guidance. A ! inside a nested fn is likewise hoisted outward."
       :evidence "packages/doeff-hy/src/doeff_hy/macros.hy:971-977 treats only for/do and traverse as opaque")
     (fact
       "Downstream, agent-control-plane works around this operationally by banning bang entirely for delegated workers, so the tool's sharp edge is shifted onto downstream discipline."
       :evidence "ACP ADR-0056")
     (fact
       "Python's yield is an expression, and Hy 1.3.0 compiles yield in place as a yield expression, without lifting it, at every probed position: if branches, ternaries, and/or, dict/list literals, function arguments, try bodies, let binding values, while conditions, and f-strings (confirmed both via hy2py output and generator execution). A yield inside a comprehension is a SyntaxError in Python 3.8+, and a yield inside a nested fn silently turns the nested fn into a generator."
       :evidence "2026-07-15 Hy 1.3.0 measurement: hy2py output of scratchpad/yield_probe.hy + send-driven execution check (recorded in the PR)")]
  :context
    [(interpretation
       "The only composable contract for ! is 'observationally equivalent to hand-writing (yield expr) at that position'. Hoisting is a different, implicit semantics, 'equivalent to writing it just before the statement', and the gap between syntactic position and execution position creates unpredictable traps for agent authors. Since a defk body is a generator and <- itself is sugar for (setv x (yield expr)), replacing a ! in expression position in place with a yield expression preserves the evaluation position exactly.")
     (interpretation
       "Positions that cannot be preserved (comprehension = separate scope where yield is impossible; nested fn = yield turns it into a separate generator) become a deterministic red at expansion time, not a silent hoist nor a silent NameError. Same judgment as ADR-DOE-HY-001: the authors are agents, and an error message in fix-prompt form becomes the fix instruction as-is.")
     (interpretation
       "Ownership of a ! inside a macro form with its own do-context (for/do, traverse, fnk, do!, handle, etc.) belongs to the innermost do-context macro. If the outer expander descends across the boundary, effects that should be deferred (such as the body of do!) run immediately in the outer context — an evaluation-position bug of the same family as hoisting.")]
  :decision
    [(rule R1 "(! expr) is replaced by (yield expr) at its occurrence position at expansion time (in-place). The evaluation position is not moved by hoisting to just before the statement.")
     (rule R2 "With this replacement, conditionality, short-circuiting, left-to-right evaluation order, and exception context are preserved at every expression position, including branches (if/when/cond), short-circuits (and/or), try/except, dict/list/set/tuple/f-string literals, function arguments, let binding values, and while conditions.")
     (rule R3 "A ! at a position where yield cannot syntactically be placed — comprehensions (lfor/gfor/dfor/sfor) and nested fn/fn/a/defn/defn/a/defclass bodies — is an expansion-time error (the macro raises SyntaxError, which Hy surfaces as HyMacroExpansionError). The error message is in fix-prompt form: it always includes the owning macro name, line number, original expression, and a fix template (a prior <- bind, and rewriting to for/do or fnk).")
     (rule R4 "The outer expander does not enter forms with their own do-context (for/do traverse fnk do! handle defhandler defk deff defp defpp defmcp-tool and their insides) or quote/quasiquote/defmacro. Bang expansion there is done by that form's own macro.")
     (rule R5 "(! ...) takes exactly one form. Any other arity is an expansion-time SyntaxError (silently ignoring extra arguments is forbidden).")
     (rule R6 "The semantics of <- and of ADR-DOE-HY-001's statement guard are unchanged.")]
  :laws
    [(law bang-position-transparency
       :statement "for_all expression_position p in do_context: observable(program_with_bang_at(p)) == observable(program_with_inline_yield_at(p)) — the effect sequence, bound values, and exception context match"
       :counterexamples
         [(counterexample "(if urgent? (setv r (! (send-alert))) (setv r \"skipped\")) — under the old hoist, send-alert ran even when urgent?=False (branch escape)")
          (counterexample "(and (guard) (! (effect))) — under the old hoist, effect ran even when guard was False (ignored short-circuit)")
          (counterexample "(process (setup-step) (! (fetch))) — under the old hoist, fetch ran before setup-step (reversed evaluation order)")
          (counterexample "(try (setv r (! (risky))) (except [e E] ...)) — under the old hoist, risky was evaluated outside the try and never reached except (lost exception context)")
          (counterexample "{\"k\" (! (eff))} — the old implementation did not walk the literal, giving a runtime NameError: hyx_Xexclamation_markX (cryptic)")
          (counterexample "(setv p (do! (! (eff)))) — the old hoist crossed the do! boundary and ran it immediately outside, breaking the deferred-Program semantics")])
     (law bang-impossible-position-loud
       :statement "for_all position p where yield_is_syntactically_impossible(p): bang_at(p) => expansion_time_SyntaxError with fix_prompt; silent_hoist_count == 0 AND silent_nameerror_count == 0"
       :counterexamples
         [(counterexample "(lfor x xs (! (eff x))) — the old implementation hoisted x out of its scope and broke silently (no error, no guidance)")
          (counterexample "(fn [x] (+ x (! (eff)))) — the old implementation ran the effect only once outside the fn, silently changing the closure semantics")])]
  :enforcement
    [(deftest test-adr-doe-hy-003-branch-conditionality
       ;; probe 1: the effect of a ! inside the False branch does not run (run count observed on the handler side)
       (setv log [])
       (setv result (run ((probe-handler log) (probe-branch False))))
       (assert (= result "skipped"))
       (assert (= log []) (+ "An effect in the False branch ran (hoist branch escape): " (str log)))
       (setv log2 [])
       (setv result2 (run ((probe-handler log2) (probe-branch True))))
       (assert (= result2 "alert"))
       (assert (= log2 ["alert"])))
     (deftest test-adr-doe-hy-003-short-circuit
       ;; probe 2: (and False (! ...)) does not run the effect
       (setv log [])
       (setv result (run ((probe-handler log) (probe-short-circuit False))))
       (assert (= result False))
       (assert (= log []) (+ "An effect that should have been stopped by short-circuit ran: " (str log)))
       (setv log2 [])
       (setv result2 (run ((probe-handler log2) (probe-short-circuit True))))
       (assert (= result2 "sc-eff"))
       (assert (= log2 ["sc-eff"])))
     (deftest test-adr-doe-hy-003-dict-literal
       ;; probe 3: a ! inside a dict literal works (in-place replacement chosen — not turned into an error)
       (setv log [])
       (setv result (run ((probe-handler log) (probe-dict-literal))))
       (assert (= result {"k" "dict-eff"}))
       (assert (= log ["dict-eff"])))
     (deftest test-adr-doe-hy-003-same-statement-eval-order
       ;; probe 4: within one statement, the pure argument is evaluated before the ! (left to right)
       (setv log [])
       (setv result (run ((probe-handler log) (probe-eval-order log))))
       (assert (= result ["pure-first" "eff-second"]))
       (assert (= log ["pure-first" "eff-second"])
               (+ "Evaluation order within one statement was reversed: " (str log))))
     (deftest test-adr-doe-hy-003-try-context
       ;; probe 5: a ! inside try is evaluated inside the try, and the handler's exception reaches except
       (setv result (run ((probe-handler []) (probe-try-catch))))
       (assert (= result "caught")
               "The exception of a ! inside try does not reach except (hoist is breaking the try context)"))
     (deftest test-adr-doe-hy-003-comprehension-expansion-error
       ;; probe 6: a ! inside a comprehension -> expansion-time error with a fix prompt.
       ;; The macro raises SyntaxError, which Hy surfaces as a macro expansion error
       ;; (HyMacroExpansionError — Hy's standard wrapping mechanism).
       (setv mod (types.ModuleType "_adr_doe_hy_003_lfor_probe"))
       (setv code (+ "(require doeff-hy.macros [defk <-])\n"
                     "(defk bad-lfor [xs]\n"
                     "  {:pre [(: xs list)] :post [(: % list)]}\n"
                     "  (lfor x xs (! (probe-eff x))))"))
       (with [excinfo (pytest.raises hy.errors.HyMacroExpansionError)]
         (for [form (hy.read-many code)]
           (hy.eval form :module mod)))
       ;; It is an expansion-time red + fix prompt: includes guidance to rewrite to for/do
       (assert (in "SyntaxError" (str excinfo.value)))
       (assert (in "for/do" (str excinfo.value))
               (+ "Expansion-time error lacks the for/do fix guidance: " (str excinfo.value))))
     (deftest test-adr-doe-hy-003-nested-fn-expansion-error
       ;; probe 7: a ! inside a nested fn -> expansion-time error with a fix prompt.
       ;; The macro raises SyntaxError, which Hy surfaces as a macro expansion error.
       (setv mod (types.ModuleType "_adr_doe_hy_003_fn_probe"))
       (setv code (+ "(require doeff-hy.macros [defk <-])\n"
                     "(defk bad-fn [xs]\n"
                     "  {:pre [(: xs list)] :post [(: % list)]}\n"
                     "  (list (map (fn [x] (+ x (! (probe-eff x)))) xs)))"))
       (with [excinfo (pytest.raises hy.errors.HyMacroExpansionError)]
         (for [form (hy.read-many code)]
           (hy.eval form :module mod)))
       ;; It is an expansion-time red + fix prompt: includes guidance to rewrite to fnk
       (assert (in "SyntaxError" (str excinfo.value)))
       (assert (in "fnk" (str excinfo.value))
               (+ "Expansion-time error lacks the fnk fix guidance: " (str excinfo.value))))
     (deftest test-adr-doe-hy-003-legacy-simple-backcompat
       ;; probe 8: backward compatibility of existing legal bang usage (simple statement / argument position)
       (setv log [])
       (setv result (run ((probe-handler log) (probe-legacy-simple))))
       (assert (= result "legacy-1+legacy-2"))
       (assert (= log ["legacy-1" "legacy-2"])))
     (deftest test-adr-doe-hy-003-do-bang-scope
       ;; probe 9 (R4): a ! inside do! runs in do!'s deferred-Program context — it is not hoisted outward
       (setv log [])
       (setv p (do! (! (ProbeEff :tag "inner"))))
       (assert (= log []) "do! is a deferred Program — no effect may fire at construction time")
       (setv result (run ((probe-handler log) p)))
       (assert (= result "inner"))
       (assert (= log ["inner"])))]
  :plans ["docs/doeff-2026-07-14-agent-first-investment-architecture-plan.md"])
