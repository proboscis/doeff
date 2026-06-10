;;; C10 k2-k3 pilot workflow expressed as the Hy macro DSL authoring surface.

(require doeff-hy.conductor
         [defworkflow defphase agent! gate! workspace! merge! parallel loop <-])
(import doeff_conductor.dsl [artifact prompt ref])
(import doeff_conductor.effects [REVIEW_VERDICT_RESULT_SCHEMA])


(setv IMPLEMENT-SCHEMA
  {"type" "object"
   "required" ["summary" "changedFiles"]
   "properties" {"summary" {"type" "string"}
                 "changedFiles" {"type" "array" "items" {"type" "string"}}
                 "notes" {"type" "string"}
                 "openQuestions" {"type" "string"}}
   "additionalProperties" False})

(setv GATE-SCHEMA
  {"type" "object"
   "required" ["buildOk" "testOk" "lintOk" "summary"]
   "properties" {"buildOk" {"type" "boolean"}
                 "testOk" {"type" "boolean"}
                 "lintOk" {"type" "boolean"}
                 "summary" {"type" "string"}
                 "failures" {"type" "string"}
                 "changedFiles" {"type" "array" "items" {"type" "string"}}}
   "additionalProperties" False})

(setv BUILD-COMMAND "PYTHONPATH=src python3 tools/build_check.py")
(setv TEST-COMMAND "PYTHONPATH=src python3 -m unittest discover -s tests")
(setv LINT-COMMAND "PYTHONPATH=src python3 tools/lint_check.py")
(setv FULL-GATE-COMMAND (+ BUILD-COMMAND " && " TEST-COMMAND " && " LINT-COMMAND))

(setv base-workspace (workspace! :from "main"))
(setv impl-a-workspace (workspace! :from "main-impl-a"))
(setv impl-b-workspace (workspace! :from "main-impl-b"))
(setv impl-c-workspace (workspace! :from "main-impl-c"))
(setv impl-d-workspace (workspace! :from "main-impl-d"))
(setv impl-e-workspace (workspace! :from "main-impl-e"))
(setv test-workspace (workspace! :from "main-tests"))
(setv gate-workspace (workspace! :from "main-gate"))


(defworkflow k2_k3_pilot
  :params {"base_ref" str "run_id" str}
  :roles {"implementer" {"profile" "cheap-coder" "retry" 2}
          "fixer" {"profile" "cheap-coder" "retry" 2}
          "test-writer" {"profile" "cheap-coder" "retry" 2}
          "reviewer" {"profile" "cheap-reviewer" "retry" 1}}

  (defphase Implement
    :stakes "normal"
    (<- impls
        (parallel
          (agent! :role "implementer"
                  :class "test-verifiable"
                  :prompt (prompt
                            "Task A: improve src/pilot_pkg/failure_kind.py only. "
                            "Keep the conservative unknown-kind behavior, add a short "
                            "module-level note that this mirrors the k2-k3 "
                            "validation_failed lane, and return the required JSON artifact.")
                  :schema IMPLEMENT-SCHEMA
                  :workspace impl-a-workspace
                  :files #{"src/pilot_pkg/failure_kind.py"}
                  :label "impl:A-failure-kind")
          (agent! :role "implementer"
                  :class "test-verifiable"
                  :prompt (prompt
                            "Task B: improve src/pilot_pkg/router.py only. Ensure both "
                            "merge-agent-not-merged:* and "
                            "merge-agent-validation-failed:* reasons route validation_failed "
                            "to investigate and transient/stale kinds to retry.")
                  :schema IMPLEMENT-SCHEMA
                  :workspace impl-b-workspace
                  :files #{"src/pilot_pkg/router.py"}
                  :label "impl:B-router")
          (agent! :role "implementer"
                  :class "test-verifiable"
                  :prompt (prompt
                            "Task C: improve src/pilot_pkg/gates.py only. Keep the option "
                            "ids exactly ['rebase', 'fresh', 're-observe', 'cancel'] and "
                            "document that each option closes over both the PR and the "
                            "owning issue.")
                  :schema IMPLEMENT-SCHEMA
                  :workspace impl-c-workspace
                  :files #{"src/pilot_pkg/gates.py"}
                  :label "impl:C-gates")
          (agent! :role "implementer"
                  :class "test-verifiable"
                  :prompt (prompt
                            "Task D: improve src/pilot_pkg/investigation.py only. Keep "
                            "pr-code, mainline, control-plane-core, and "
                            "transient-observation classifications explicit.")
                  :schema IMPLEMENT-SCHEMA
                  :workspace impl-d-workspace
                  :files #{"src/pilot_pkg/investigation.py"}
                  :label "impl:D-investigation")
          (agent! :role "implementer"
                  :class "test-verifiable"
                  :prompt (prompt
                            "Task E: improve src/pilot_pkg/ownership.py only. Keep "
                            "MergeValidationInvestigated owned by review-reconciler and "
                            "add a small helper if useful. Do not edit any other file.")
                  :schema IMPLEMENT-SCHEMA
                  :workspace impl-e-workspace
                  :files #{"src/pilot_pkg/ownership.py"}
                  :label "impl:E-ownership"))))

  (defphase Reconcile
    :stakes "high"
    (<- reconciled
        (loop :max 3
              :until "build_gate_passed"
              (<- build_gate (gate! :cmd BUILD-COMMAND :workspace base-workspace))
              (agent! :role "fixer"
                      :class "test-verifiable"
                      :prompt (prompt "fix compile/build failures after "
                                      (ref "impls")
                                      " gate="
                                      (ref "build_gate"))
                      :schema GATE-SCHEMA
                      :workspace base-workspace
                      :files #{"src/pilot_pkg"}
                      :label "fix:compile"))))

  (defphase Tests
    :stakes "normal"
    (<- tests
        (parallel
          (agent! :role "test-writer"
                  :class "test-verifiable"
                  :prompt (prompt
                            "Write tests/test_routing.py only. Cover validation_failed "
                            "routing to investigate, agent_error routing to retry, and "
                            "unknown legacy suffixes routing to investigate. Use "
                            "unittest.TestCase so python -m unittest discover runs them."
                            " after "
                            (ref "reconciled"))
                  :schema IMPLEMENT-SCHEMA
                  :workspace test-workspace
                  :files #{"tests/test_routing.py"}
                  :label "test:routing")
          (agent! :role "test-writer"
                  :class "test-verifiable"
                  :prompt (prompt
                            "Write tests/test_ownership.py only. Cover the "
                            "MergeValidationInvestigated owner and the merge exhausted "
                            "gate option ids. Use unittest or unittest.TestCase so "
                            "python -m unittest discover runs them."
                            " after "
                            (ref "reconciled"))
                  :schema IMPLEMENT-SCHEMA
                  :workspace test-workspace
                  :files #{"tests/test_ownership.py"}
                  :label "test:ownership"))))

  (defphase Gate
    :stakes "high"
    (<- gated
        (loop :max 3
              :until "full_gate_passed"
              (<- full_gate (gate! :cmd FULL-GATE-COMMAND :workspace gate-workspace))
              (agent! :role "fixer"
                      :class "test-verifiable"
                      :prompt (prompt "fix gate failures after "
                                      (ref "tests")
                                      " gate="
                                      (ref "full_gate"))
                      :schema GATE-SCHEMA
                      :workspace gate-workspace
                      :files #{"src/pilot_pkg/lint_sentinel.py"}
                      :label "fix:gate"))))

  (defphase Review
    :stakes "high"
    (<- reviews
        (parallel
          (agent! :role "reviewer"
                  :class "semantic"
                  :prompt (prompt "Review route_failure and gate options for "
                                  "closure-law violations. PASS if no issue. gated="
                                  (ref "gated"))
                  :schema REVIEW_VERDICT_RESULT_SCHEMA
                  :workspace gate-workspace
                  :files #{"review/review-routing-closure.md"}
                  :label "review-routing-closure")
          (agent! :role "reviewer"
                  :class "semantic"
                  :prompt (prompt "Review tests for coverage of validation_failed, "
                                  "agent_error, and ownership. gated="
                                  (ref "gated"))
                  :schema REVIEW_VERDICT_RESULT_SCHEMA
                  :workspace gate-workspace
                  :files #{"review/review-tests.md"}
                  :label "review-tests")
          (agent! :role "reviewer"
                  :class "semantic"
                  :prompt (prompt "Review CONDITION_OWNER consistency and "
                                  "single-writer assumptions. gated="
                                  (ref "gated"))
                  :schema REVIEW_VERDICT_RESULT_SCHEMA
                  :workspace gate-workspace
                  :files #{"review/review-ownership.md"}
                  :label "review-ownership")
          (agent! :role "reviewer"
                  :class "semantic"
                  :prompt (prompt "If docs/known_blocker.md exists, report one "
                                  "BLOCKER finding for that file. gated="
                                  (ref "gated"))
                  :schema REVIEW_VERDICT_RESULT_SCHEMA
                  :workspace gate-workspace
                  :files #{"review/review-known-blocker.md"}
                  :label "review-known-blocker")))
    (<- merged
        (merge! :workspaces [base-workspace test-workspace gate-workspace
                             impl-a-workspace impl-b-workspace impl-c-workspace
                             impl-d-workspace impl-e-workspace]
                :strategy "merge"))
    (artifact (prompt (ref "reviews") (ref "merged")))))


(setv WORKFLOW k2_k3_pilot)
