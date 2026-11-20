;;; The population of the design-evidence rules in doeff-linter itself: the modules of the mechanism (the reader of shapes and evidence, the ledger guard, the
;;; substitute counter). Every type there carries its evidence in the sibling <stem>.evidence.toml; there is no ledger of existing shapes (no type passes without evidence).
(defarchitecture doeff-linter-self-evidence
  :root "packages/doeff-linter/src"
  :layers [(layer project :roles [judgment] :imports [project])]
  :design-evidence {:files ["packages/doeff-linter/src/project/design_evidence.rs"
                            "packages/doeff-linter/src/project/ledger_guard.rs"
                            "packages/doeff-linter/src/project/type_substitutes.rs"]})
