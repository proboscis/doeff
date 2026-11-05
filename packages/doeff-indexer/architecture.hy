;;; Declaration of the file layers of packages/doeff-indexer.
;;;
;;; For now this holds only one layer that is excluded from the rule population (same shape as tools/architecture.hy). doeff-linter decides the population of
;;; per-statement Python rules from the nearest architecture.hy above the file (#2811). What is excluded here is only the named module below; the population of the other files
;;; of doeff-indexer (the package under python/) is unchanged. The root doeff_cargo_backend.py is a symlink to tools, and the declaration at the real location
;;; (tools/architecture.hy) applies to it.
;;;
;;; build = doeff-indexer's PEP 517 build backend doeff_indexer_build_backend. uv and pip call it when building a wheel, and it runs outside doeff's Program.
;;;   The settings for cargo, which builds the CLI binary (CARGO, CARGO_BUILD_TARGET, DOEFF_INDEXER_SKIP_CLI_BUILD), are entered through the environment variables themselves, and
;;;   there is no entry point that takes them by dependency injection (doeff's Ask).
;;;   - Excluded rule: DOEFF004 (reading os.environ directly) -- limited to the named module (doeff_indexer_build_backend).
;;;   - Forbidden import: doeff's business modules. If business code enters the excluded layer, DOEFF032 turns red.
;;; Previously the root pyproject.toml's exclude took the whole file out of all rules, but because of a partial-match bug in the linter's exclude, it was
;;;   not being judged either way (#3012).
;;; How to revert: delete the :exempt line and DOEFF004 applies to the build backend again. If this file is deleted, the file population of doeff-indexer
;;;   returns to the root settings.
(defarchitecture doeff-indexer-build
  :root "."
  :layers [(layer build
             :summary "doeff-indexer's PEP 517 build backend -- called by uv and pip when building a wheel, code outside the Program"
             :knows "cargo, maturin, wheel, PEP 517 hooks"
             :does-not-know "doeff's Program, effect, handler"
             :modules [doeff_indexer_build_backend]
             :exempt [(rule DOEFF004 "A PEP 517 build backend is called by uv and pip and runs outside doeff's Program, so the settings for cargo (CARGO, CARGO_BUILD_TARGET, DOEFF_INDEXER_SKIP_CLI_BUILD) are entered through the environment variables themselves, and there is no entry point to take the settings via Ask")]
             :forbid-modules [doeff doeff_core_effects doeff_hy doeff_vm doeff_agents doeff_cluster])])
