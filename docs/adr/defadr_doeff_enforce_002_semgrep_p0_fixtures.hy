;;; Executable ADR: P0 Semgrep rules carry installed-rule hit/clean fixtures.

(require doeff-adr.macros [defadr defsemgrep rule law])
(import doeff-adr.macros [fact counterexample])


(defadr ADR-DOE-ENFORCE-002
  :title "P0 semgrep rules keep permanent hit/clean fixtures as proof of liveness"
  :status "proposed"
  :scope [".semgrep.yaml"
          "docs/adr/defadr_doeff_enforce_002_semgrep_p0_fixtures.hy"
          "docs/adr/enforcement-ledger.json"]
  :problem
    [(fact
       "The 2026-07-14 inventory found 8 of 229 rules silently dead."
       :evidence "docs/semgrep-rules-inventory-2026-07-14.md")
     (fact
       "The bulk gate (baseline 0) cannot distinguish \"zero violations\" from \"rule is dead\"."
       :evidence "tests/test_semgrep_gate.py")]
  :decision
    [(rule R1 "The 5 P0 rules keep permanent hit/clean fixtures via defsemgrep.")
     (rule R2 "Extending this to P1 and beyond is decided separately, based on operating experience with P0.")]
  :laws
    [(law p0-rule-liveness
       :statement
         "for_all p0_rule: fires_on(bad_fixture) AND not fires_on(good_fixture)"
       :counterexamples
         [(counterexample
            "A P0 rule's matcher or path condition dies while the bulk gate stays green")])]
  :enforcement
    [(defsemgrep p0-no-return-control-primitives-in-handlers
       "doeff-no-return-control-primitives-in-handlers"
       [{"relative-path" "packages/sample/handler.py"
         "source"
           "@do\ndef handle(effect, k):\n    return Pass()\n"}]
       [{"relative-path" "packages/sample/handler.py"
         "source"
           "@do\ndef handle(effect, k):\n    yield Pass()\n"}])
     (defsemgrep p0-no-effect-substitution-on-forward
       "doeff-no-effect-substitution-on-forward"
       [{"relative-path" "packages/sample/handler.py"
         "source"
           "def handle(effect):\n    yield Delegate(effect)\n"}]
       [{"relative-path" "packages/sample/handler.py"
         "source"
           "def handle(effect):\n    yield effect\n    yield Delegate()\n"}])
     (defsemgrep p0-vm-core-no-wildcard-unreachable-on-match
       "vm-core-no-wildcard-unreachable-on-match"
       [{"relative-path" "packages/doeff-vm-core/src/vm.rs"
         "source"
           "fn dispatch(value: Value) {\n    match value {\n        _ => unreachable!(),\n    }\n}\n"}]
       [{"relative-path" "packages/doeff-vm-core/src/vm.rs"
         "source"
           "fn dispatch(value: Value) {\n    match value {\n        Value::Done => unreachable!(),\n    }\n}\n"}])
     (defsemgrep p0-vm-no-raw-runtime-or-type-throw
       "vm-no-raw-runtime-or-type-throw"
       [{"relative-path" "packages/doeff-vm-core/src/vm.rs"
         "source"
           "fn fail(error: String) {\n    let mode = Mode::Throw(PyException::runtime_error(error));\n}\n"}]
       [{"relative-path" "packages/doeff-vm-core/src/vm.rs"
         "source"
           "fn fail(error: String) {\n    let mode = contextual_throw_mode(PyException::runtime_error(error));\n}\n"}])
     (defsemgrep p0-scheduler-spawn-must-capture-boundaries
       "doeff-scheduler-spawn-must-capture-boundaries"
       [{"relative-path"
           "packages/doeff-core-effects/doeff_core_effects/_scheduler.py"
         "source" "boundaries = yield get_inner_handlers(k)\n"}]
       [{"relative-path"
           "packages/doeff-core-effects/doeff_core_effects/_scheduler.py"
         "source" "boundaries = yield get_inner_boundaries(k)\n"}])]
  :plans ["docs/doeff-2026-07-14-agent-first-investment-architecture-plan.md"
          "docs/semgrep-rules-inventory-2026-07-14.md"])
