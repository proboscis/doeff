;;; Executable ADR: P0 Semgrep rules carry installed-rule hit/clean fixtures.

(require doeff-adr.macros [defadr defsemgrep rule law])
(import doeff-adr.macros [fact counterexample])


(defadr ADR-DOE-ENFORCE-002
  :title "P0 semgrep ルールは hit/clean fixture で生存証明を常設する"
  :status "proposed"
  :scope [".semgrep.yaml"
          "docs/adr/defadr_doeff_enforce_002_semgrep_p0_fixtures.hy"
          "docs/adr/enforcement-ledger.json"]
  :problem
    [(fact
       "2026-07-14 棚卸しで 229 中 8 ルールが無音で死んでいた。"
       :evidence "docs/semgrep-rules-inventory-2026-07-14.md")
     (fact
       "一括ゲート(baseline 0)は「違反ゼロ」と「ルール死亡」を区別できない。"
       :evidence "tests/test_semgrep_gate.py")]
  :decision
    [(rule R1 "P0 の 16 ルールは hit/clean fixture を defsemgrep で常設する。")
     (rule R2 "P1 以降への拡張は P0 の運用経験を見て別途裁定する。")]
  :laws
    [(law p0-rule-liveness
       :statement
         "for_all p0_rule: fires_on(bad_fixture) AND not fires_on(good_fixture)"
       :counterexamples
         [(counterexample
            "一括ゲートが green のまま P0 ルールの matcher または path 条件が死ぬ")])]
  :enforcement
    [(defsemgrep p0-no-anthropic-api-key-agent-env
       "doeff-agents-no-anthropic-api-key-agent-env"
       []
       [])
     (defsemgrep p0-no-claude-print-mode
       "doeff-agents-no-claude-print-mode"
       []
       [])
     (defsemgrep p0-require-real-claude-result-retry-e2e
       "doeff-agents-require-real-claude-result-retry-e2e"
       []
       [])
     (defsemgrep p0-require-real-codex-result-retry-e2e
       "doeff-agents-require-real-codex-result-retry-e2e"
       []
       [])
     (defsemgrep p0-real-agent-e2e-must-not-be-skipped
       "doeff-agents-real-agent-e2e-must-not-be-skipped"
       []
       [])
     (defsemgrep p0-real-agent-e2e-must-not-use-command-override
       "doeff-agents-real-agent-e2e-must-not-use-command-override"
       []
       [])
     (defsemgrep p0-agentd-request-requires-result-key
       "doeff-agents-agentd-request-requires-result-key"
       []
       [])
     (defsemgrep p0-await-result-requires-protocol-fields
       "doeff-agents-await-result-requires-protocol-fields"
       []
       [])
     (defsemgrep p0-no-return-control-primitives-in-handlers
       "doeff-no-return-control-primitives-in-handlers"
       []
       [])
     (defsemgrep p0-no-effect-substitution-on-forward
       "doeff-no-effect-substitution-on-forward"
       []
       [])
     (defsemgrep p0-vm-core-no-wildcard-unreachable-on-match
       "vm-core-no-wildcard-unreachable-on-match"
       []
       [])
     (defsemgrep p0-vm-no-raw-runtime-or-type-throw
       "vm-no-raw-runtime-or-type-throw"
       []
       [])
     (defsemgrep p0-scheduler-spawn-must-capture-boundaries
       "doeff-scheduler-spawn-must-capture-boundaries"
       []
       [])
     (defsemgrep p0-k4-no-blocking-agent-handler
       "k4-no-blocking-agent-handler"
       []
       [])
     (defsemgrep p0-k4-no-sync-await-result-in-scheduled-handler
       "k4-no-sync-await-result-in-scheduled-handler"
       []
       [])
     (defsemgrep p0-k4-deadline-not-transport-timeout
       "k4-deadline-not-transport-timeout"
       []
       [])]
  :plans ["docs/doeff-2026-07-14-agent-first-investment-architecture-plan.md"
          "docs/semgrep-rules-inventory-2026-07-14.md"])
