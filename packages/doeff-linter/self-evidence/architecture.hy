;;; The population of DOEFF212 in doeff-linter itself: the three modules of the mechanism that read and judge (the reader of shapes and evidence, the ledger guard,
;;; the substitute counter). Every type there carries its evidence in the sibling <stem>.evidence.toml; there is no ledger of existing shapes (no type passes without
;;; evidence), and a review must name a review recorded in docs/design/design-evidence/artifacts/v2/loophole_close.md (its date-stamped name).
;;; Not in the population, and why:
;;;   - architecture.rs holds the declaration types of every rule of the linter (DesignEvidenceDecl and LedgerGuardDecl among some hundred); putting it in would make
;;;     this the evidence of the whole declaration reader, a change of its own.
;;;   - typed_values.rs (SubstituteKind, SubstituteHit) is the reader of DOEFF144/145 that DOEFF213 reuses; the same reason.
;;; DOEFF213 is not applied: it reads .hy files only, and these modules are Rust.
(defarchitecture doeff-linter-self-evidence
  :root "packages/doeff-linter/src"
  :layers [(layer project :roles [judgment] :imports [project])]
  :design-evidence {:files ["packages/doeff-linter/src/project/design_evidence.rs"
                            "packages/doeff-linter/src/project/ledger_guard.rs"
                            "packages/doeff-linter/src/project/type_substitutes.rs"]
                    :review-pattern r"^design-evidence-review-[0-9]{4}-[0-9]{2}-[0-9]{2}$"})
