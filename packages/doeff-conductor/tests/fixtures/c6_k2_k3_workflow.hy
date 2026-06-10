(require doeff-hy.conductor
         [defworkflow defphase agent! gate! workspace! merge! parallel loop <-])
(import doeff_conductor.dsl [artifact prompt ref])


(setv RESULT-SCHEMA {"type" "object"
                     "required" ["status"]
                     "properties" {"status" {"type" "string"}}})

(setv VERDICT-SCHEMA {"type" "object"
                      "required" ["verdict" "findings"]
                      "properties" {"verdict" {"enum" ["PASS" "CHANGES_REQUESTED"]}
                                    "findings" {"type" "array"}}})

(setv base (workspace! :from "main"))
(setv impl0 (workspace! :from "main-impl-0"))
(setv impl1 (workspace! :from "main-impl-1"))
(setv impl2 (workspace! :from "main-impl-2"))
(setv impl3 (workspace! :from "main-impl-3"))
(setv impl4 (workspace! :from "main-impl-4"))
(setv test-workspace (workspace! :from "main-tests"))
(setv gate-workspace (workspace! :from "main-gate"))


(defworkflow k2_k3_reference_shape
  :params {"issue" str "base_ref" str}
  :roles {"implementer" {"profile" "cheap-coder" "retry" 3}
          "fixer" {"profile" "cheap-coder" "retry" 3}
          "test_writer" {"profile" "cheap-coder" "retry" 2}
          "reviewer" {"profile" "cheap-reviewer" "retry" 1}}

  (defphase Implement
    :stakes "normal"
    (<- impls
        (parallel
          (agent! :role "implementer"
                  :class "test-verifiable"
                  :prompt (prompt "implement variant " 0)
                  :schema RESULT-SCHEMA
                  :workspace impl0
                  :files #{"impl-0.py"}
                  :label "impl-0")
          (agent! :role "implementer"
                  :class "test-verifiable"
                  :prompt (prompt "implement variant " 1)
                  :schema RESULT-SCHEMA
                  :workspace impl1
                  :files #{"impl-1.py"}
                  :label "impl-1")
          (agent! :role "implementer"
                  :class "test-verifiable"
                  :prompt (prompt "implement variant " 2)
                  :schema RESULT-SCHEMA
                  :workspace impl2
                  :files #{"impl-2.py"}
                  :label "impl-2")
          (agent! :role "implementer"
                  :class "test-verifiable"
                  :prompt (prompt "implement variant " 3)
                  :schema RESULT-SCHEMA
                  :workspace impl3
                  :files #{"impl-3.py"}
                  :label "impl-3")
          (agent! :role "implementer"
                  :class "test-verifiable"
                  :prompt (prompt "implement variant " 4)
                  :schema RESULT-SCHEMA
                  :workspace impl4
                  :files #{"impl-4.py"}
                  :label "impl-4"))))

  (defphase Fix
    :stakes "high"
    (<- fixed
        (loop :max 3
              :until "tests_pass"
              (<- fix_gate (gate! :cmd "uv run pytest" :workspace base))
              (agent! :role "fixer"
                      :class "test-verifiable"
                      :prompt (prompt "fix failures after " (ref "impls") " from " (ref "fix_gate"))
                      :schema RESULT-SCHEMA
                      :workspace base
                      :files #{"doeff/fix.py"}
                      :label "fixer"))))

  (defphase Tests
    :stakes "normal"
    (<- tests
        (parallel
          (agent! :role "test_writer"
                  :class "test-verifiable"
                  :prompt (prompt "write unit tests for " (ref "fixed"))
                  :schema RESULT-SCHEMA
                  :workspace test-workspace
                  :files #{"tests/test_unit.py"}
                  :label "unit-tests")
          (agent! :role "test_writer"
                  :class "test-verifiable"
                  :prompt (prompt "write integration tests for " (ref "fixed"))
                  :schema RESULT-SCHEMA
                  :workspace test-workspace
                  :files #{"tests/test_integration.py"}
                  :label "integration-tests"))))

  (defphase Gate
    :stakes "high"
    (<- gated
        (loop :max 3
              :until "gate_passed"
              (<- test_gate (gate! :cmd "uv run pytest" :workspace gate-workspace))
              (agent! :role "fixer"
                      :class "test-verifiable"
                      :prompt (prompt "repair gate failures after "
                                      (ref "tests")
                                      " "
                                      (ref "test_gate"))
                      :schema RESULT-SCHEMA
                      :workspace gate-workspace
                      :files #{"doeff/gate_fix.py"}
                      :label "gate-fixer"))))

  (defphase Review
    :stakes "high"
    (<- reviews
        (parallel
          (agent! :role "reviewer"
                  :class "semantic"
                  :prompt (prompt "review axis correctness for " (ref "gated"))
                  :schema VERDICT-SCHEMA
                  :workspace gate-workspace
                  :files #{"review/correctness.md"}
                  :label "review-correctness")
          (agent! :role "reviewer"
                  :class "semantic"
                  :prompt (prompt "review axis tests for " (ref "gated"))
                  :schema VERDICT-SCHEMA
                  :workspace gate-workspace
                  :files #{"review/tests.md"}
                  :label "review-tests")
          (agent! :role "reviewer"
                  :class "semantic"
                  :prompt (prompt "review axis architecture for " (ref "gated"))
                  :schema VERDICT-SCHEMA
                  :workspace gate-workspace
                  :files #{"review/architecture.md"}
                  :label "review-architecture")
          (agent! :role "reviewer"
                  :class "semantic"
                  :prompt (prompt "review axis docs for " (ref "gated"))
                  :schema VERDICT-SCHEMA
                  :workspace gate-workspace
                  :files #{"review/docs.md"}
                  :label "review-docs")))
    (<- merged
        (merge! :workspaces [base test-workspace gate-workspace impl0 impl1 impl2 impl3 impl4]
                :strategy "merge"))
    (artifact (prompt (ref "reviews") (ref "merged")))))


(setv WORKFLOW k2_k3_reference_shape)
