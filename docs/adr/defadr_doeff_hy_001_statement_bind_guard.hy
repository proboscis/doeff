;;; Executable ADR: forbid silently discarding a Program/EffectBase evaluated at statement
;;; position in a defk/do! body (guard-error). auto-bind is not adopted.
;;; The error message is designed as a fix prompt for agent authors.

(require doeff-adr.macros [defadr defsemgrep rule law])
(import doeff-adr.macros [fact interpretation counterexample])
(require doeff-hy.macros [deftest defk <-])
(import doeff [run])
(import pytest)


;; Live reproduction of the ADR-DOE-HY-001 counterexample and the class-level guard scope (run by enforcement).
;; A module-level defk imports helpers into globals and masks the defect, so the probe is
;; self-contained inside the class.
(defclass _ClassLevelProbe []
  (defk inner-method [self]
    {:pre [(: self "_ClassLevelProbe instance")]
     :post [(: % int)]}
    1)

  (defk bound-method [self]
    {:pre [(: self "_ClassLevelProbe instance")]
     :post [(: % int)]}
    (<- value (.inner-method self))
    value)

  (defk bare-statement-method [self]
    {:pre [(: self "_ClassLevelProbe instance")]
     :post [(: % int)]}
    (.inner-method self)  ;; <- trap: builds a bare Program at statement position and discards it
    2))


(defadr ADR-DOE-HY-001
  :title "statement-position bind guard: when a value evaluated at statement position in a defk/do! body is a Program (DoExpr)/EffectBase, a macro-inserted runtime guard raises RuntimeError immediately (guard-error). auto-bind (Haskell do-notation-style automatic binding) is not adopted — the authors are agents, and what they need is not ergonomics but a deterministic, actionable red"
  :status "proposed"
  :scope ["packages/doeff-hy/src/doeff_hy/macros.hy"
          "docs/adr/defadr_doeff_hy_001_statement_bind_guard.hy"]
  :problem
    [(fact
       "The current macros pass expressions at non-final statement positions of a defk/do! body through as 'Plain statement (setv, when, for, etc.) — emit as-is'. A bare kleisli call builds a Program and silently discards it; neither execution nor an exception happens."
       :evidence "packages/doeff-hy/src/doeff_hy/macros.hy:623, 748, 1069 (as of 2026-07-14)")
     (fact
       "The existing guard `_guard-performed` covers only 'the final expression is a bare EffectBase', and only at runtime. DoExpr/Program is explicitly excluded in its docstring ('The Program-return composition pattern returns a DoExpr ... so this never fires on it')."
       :evidence "packages/doeff-hy/src/doeff_hy/macros.hy:300-317")
     (fact
       "Downstream damage: agent-control-plane's bulk deff->defk migration plan lists silent-passthrough (forgotten bind -> the Program neither runs nor raises) as a non-obvious gotcha. The color error in the opposite direction (over-binding a Program-as-value) crash-looped the production supervisor on 2026-07-13 while deftest/CI stayed green."
       :evidence "agent-control-plane ADR 0056 migration plan (facts a–p); ACP hotfix commit 2026-07-13 'K2 latent defect: restore over-bind of Program-as-value — dogfood outage incident'")
     (fact
       "This repository's development style: code is always written by agents, and humans take part only as reviewers. Agents do not learn from silent semantics; they respond most reliably to a deterministic red that includes line numbers and a fix template."
       :evidence "2026-07-14 doeff investment plan discussion (docs/doeff-2026-07-14-agent-first-investment-architecture-plan.md)")]
  :context
    [(interpretation
       "doeff did not erase the sync/async color; it unified it into a new single color, 'Program or value'. A forgotten bind or an over-bind is a color error of this color, and unlike async/await the syntax does not enforce it, so the macro layer (the only layer that can see the syntax tree) is the correct owning layer to guard it. Detection by a Python-side linter needs type inference and produces false negatives — since Hy is a first-class language, the line of defense is placed in the macros.")
     (interpretation
       "auto-bind is more ergonomic but doubly unsuitable: (1) it is a behavior change where dormant bare forms start running on the day it is introduced 'with no diff at all in that file', and (2) it gives agent authors no teaching signal at all. guard-error fails inside the agent's execution loop, and its message becomes the fix prompt as-is.")
     (interpretation
       "Static (expansion-time) detection is sound only for narrow cases such as literal EffectBase constructors. Rather than a static check mixed with false negatives in a dynamic language, choose a uniform, deterministic runtime guard (one isinstance per statement).")]
  :decision
    [(rule R1 "When a value evaluated at statement position in a defk/do! body (other than binds, return, and known control forms) is a DoExpr/Program or an EffectBase, a macro-inserted runtime guard raises RuntimeError immediately.")
     (rule R2 "auto-bind is not adopted. The official form for running something while discarding its value is the explicit discard-bind `(<- _ expr)`.")
     (rule R3 "The guard's error message is in fix-prompt form: it always includes the function name, file:line, the evaluated type name, and the fix template '(<- _ (<expr>))'. The error message is a fix instruction for agents, not an explanation for humans.")
     (rule R4 "Statements whose value is neither a Program nor an EffectBase, such as setv/print, pass through untouched (the guard is only an isinstance check).")
     (rule R5 "Rollout to consuming repositories goes through enabling the guard -> exhaustively finding and fixing dormant discards (plan A2). Until the sweep is complete, the guard may be limited to doeff itself and opt-in consumers.")]
  :laws
    [(law statement-discard-impossible
       :statement "for_all defk_or_do!_body: evaluated_at_statement_position(v) AND isinstance(v, DoExpr | EffectBase) => raises(RuntimeError); silent_discard_count == 0"
       :counterexamples
         [(counterexample "A bare kleisli call `(notify-user order receipt)` in an intermediate statement — the notification Program is built and discarded, the notification is never sent, and neither a test failure nor an exception appears (current behavior)")
          (counterexample "A bare `(LaunchEffect ...)` just before do!'s final expression — passes through because _guard-performed only looks at the final expression")])]
  :enforcement
    [(deftest test-adr-doe-hy-001-bare-statement-program-raises
       ;; A bare (.inner-method probe) must not silently discard the Program.
       (with [(pytest.raises RuntimeError)]
         (run (.bare-statement-method (_ClassLevelProbe)))))
     (deftest test-adr-doe-hy-001-class-level-defk-guard-scope
       ;; RED (2026-07-14): the helper import is trapped in class scope, so even the normal bind path
       ;; becomes a NameError on `_guard_performed`. After injecting into module globals it returns the normal value.
       (<- result (.bound-method (_ClassLevelProbe)))
       (assert (= result 1))
       ;; The statement guard is also resolved from the same module globals and becomes the expected RuntimeError.
       (with [(pytest.raises RuntimeError :match "\\[ADR-DOE-HY-001\\]")]
         (run (.bare-statement-method (_ClassLevelProbe)))))]
  :plans ["docs/doeff-2026-07-14-agent-first-investment-architecture-plan.md"])
