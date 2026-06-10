;;; C8 k2-k3 pilot workflow expressed as a DSL-only request artifact.

(require doeff-hy.conductor [defworkflow defphase agent! gate! workspace! merge! parallel loop <-])
(import doeff_conductor.dsl [artifact prompt ref])
(import doeff_conductor.effects [REVIEW-VERDICT-RESULT-SCHEMA])


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


;; Static task descriptors shared by the DSL shape and the scratch repo.
(setv IMPLEMENTER-TASKS
  [{"label" "impl:A-failure-kind"
    "suffix" "impl-a"
    "files" #{"src/pilot_pkg/failure_kind.py"}
    "prompt" (+ "Task A: improve src/pilot_pkg/failure_kind.py only. Keep the conservative "
                "unknown-kind behavior, add a short module-level note that this mirrors the "
                "k2-k3 validation_failed lane, and return the required JSON artifact.")}
   {"label" "impl:B-router"
    "suffix" "impl-b"
    "files" #{"src/pilot_pkg/router.py"}
    "prompt" (+ "Task B: improve src/pilot_pkg/router.py only. Ensure both "
                "merge-agent-not-merged:* and merge-agent-validation-failed:* reasons route "
                "validation_failed to investigate and transient/stale kinds to retry.")}
   {"label" "impl:C-gates"
    "suffix" "impl-c"
    "files" #{"src/pilot_pkg/gates.py"}
    "prompt" (+ "Task C: improve src/pilot_pkg/gates.py only. Keep the option ids exactly "
                "['rebase', 'fresh', 're-observe', 'cancel'] and document that each option "
                "closes over both the PR and the owning issue.")}
   {"label" "impl:D-investigation"
    "suffix" "impl-d"
    "files" #{"src/pilot_pkg/investigation.py"}
    "prompt" (+ "Task D: improve src/pilot_pkg/investigation.py only. Keep pr-code, mainline, "
                "control-plane-core, and transient-observation classifications explicit.")}
   {"label" "impl:E-ownership"
    "suffix" "impl-e"
    "files" #{"src/pilot_pkg/ownership.py"}
    "prompt" (+ "Task E: improve src/pilot_pkg/ownership.py only. Keep "
                "MergeValidationInvestigated owned by review-reconciler and add a small helper "
                "if useful. Do not edit any other file.")}])

(setv TEST-WRITER-TASKS
  [{"label" "test:routing"
    "suffix" "tests-routing"
    "files" #{"tests/test_routing.py"}
    "prompt" (+ "Write tests/test_routing.py only. Cover validation_failed routing to "
                "investigate, agent_error routing to retry, and unknown legacy suffixes routing "
                "to investigate. Use unittest.TestCase so python -m unittest discover runs them.")}
   {"label" "test:ownership"
    "suffix" "tests-ownership"
    "files" #{"tests/test_ownership.py"}
    "prompt" (+ "Write tests/test_ownership.py only. Cover the MergeValidationInvestigated "
                "owner and the merge exhausted gate option ids. Use unittest or "
                "unittest.TestCase so python -m unittest discover runs them.")}])

(setv REVIEW-AXES
  [["review-routing-closure"
    "Review route_failure and gate options for closure-law violations. PASS if no issue."]
   ["review-tests"
    "Review tests for coverage of validation_failed, agent_error, and ownership."]
   ["review-ownership"
    "Review CONDITION_OWNER consistency and single-writer assumptions."]
   ["review-known-blocker"
    "If docs/known_blocker.md exists, report one BLOCKER finding for that file."]])

(setv BUILD-COMMAND "PYTHONPATH=src python3 tools/build_check.py")
(setv TEST-COMMAND "PYTHONPATH=src python3 -m unittest discover -s tests")
(setv LINT-COMMAND "PYTHONPATH=src python3 tools/lint_check.py")
(setv FULL-GATE-COMMAND f"{BUILD-COMMAND} && {TEST-COMMAND} && {LINT-COMMAND}")

(setv base-workspace (workspace! :from "main"))
(setv impl-workspaces
  (lfor task IMPLEMENTER-TASKS
    (workspace! :from (+ "main-" (get task "suffix")))))
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
          #* (lfor [index task] (enumerate IMPLEMENTER-TASKS)
               (agent! :role "implementer"
                       :class "test-verifiable"
                       :prompt (prompt (get task "prompt"))
                       :schema IMPLEMENT-SCHEMA
                       :workspace (get impl-workspaces index)
                       :files (get task "files")
                       :label (get task "label"))))))

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
          #* (lfor task TEST-WRITER-TASKS
               (agent! :role "test-writer"
                       :class "test-verifiable"
                       :prompt (prompt (get task "prompt") " after " (ref "reconciled"))
                       :schema IMPLEMENT-SCHEMA
                       :workspace test-workspace
                       :files (get task "files")
                       :label (get task "label"))))))

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
          #* (lfor [axis axis-prompt] REVIEW-AXES
               (agent! :role "reviewer"
                       :class "semantic"
                       :prompt (prompt axis-prompt " gated=" (ref "gated"))
                       :schema REVIEW-VERDICT-RESULT-SCHEMA
                       :workspace gate-workspace
                       :files #{f"review/{axis}.md"}
                       :label axis))))
    (<- merged
        (merge! :workspaces [base-workspace test-workspace gate-workspace #* impl-workspaces]
                :strategy "merge"))
    (artifact (prompt (ref "reviews") (ref "merged")))))

(setv WORKFLOW k2_k3_pilot)
