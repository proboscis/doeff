;;; Layer declarations for the files under tools.
;;;
;;; For now this holds only two layers that are excluded from the rule population (build and release). doeff-linter decides the
;;; population of per-statement Python rules from the nearest architecture.hy found walking up from the file (#2811).
;;; The file's location is its real location after resolving symlinks -- placing a symlink does not change which rules
;;; apply to that file. Only the named modules below are exempted here; the population for the other files in tools is unchanged.
;;;
;;; build = doeff_cargo_backend, the PEP 517 build entry point for the Rust packages. The real file is the single
;;;   tools/doeff_cargo_backend.py; the doeff_cargo_backend.py at the roots of doeff-effect-analyzer, doeff-indexer, doeff-linter and
;;;   doeff-vm are symlinks to it
;;;   (so the declaration lives here, not in packages -- placed in packages, there would be no declaration above the real location
;;;   after resolving the symlink, and the exemption would not apply).
;;;   It is called when uv or pip builds a wheel and runs outside a doeff Program. The settings for cargo and maturin
;;;   (CARGO_TARGET_DIR and others) are given by environment variables themselves, and there is no entry point to receive them
;;;   through dependency injection (doeff's Ask).
;;;   - Exempted rule: DOEFF004 (reading os.environ directly) -- limited to the named module (doeff_cargo_backend).
;;;   - Forbidden imports: doeff's business modules. If business code enters the exempted layer, DOEFF032 turns red.
;;; release = rust_wheel_release, the CI tool that uploads Rust wheels to a store outside the machine (release rust-wheels). It runs only
;;;   inside doeff's GitHub Actions (.github/workflows/rust-wheels.yml) and is code outside a doeff Program. The store's repo
;;;   (GITHUB_REPOSITORY) is given by the environment variables Actions passes, and the settings handed to the build entry point
;;;   (DOEFF_WHEEL_CACHE and DOEFF_WHEEL_REPORT) are likewise given by the child uv build's environment.
;;;   - Exempted rule: DOEFF004 -- limited to the named module (rust_wheel_release).
;;;   - Forbidden imports: same as build.
;;; How to revert: delete the :exempt line and DOEFF004 applies to that layer's module again. If this file is deleted, the population
;;;   for the files in tools returns to the root settings.
(defarchitecture doeff-tools
  :root "."
  :layers [(layer build
             :summary "PEP 517 build entry point for the Rust packages -- called when uv or pip builds a wheel, code outside a Program"
             :knows "cargo, maturin, wheels, PEP 517 hooks"
             :does-not-know "doeff Programs, effects, handlers"
             :modules [doeff_cargo_backend]
             :exempt [(rule DOEFF004 "The PEP 517 build entry point is called by uv or pip and runs outside a doeff Program, so the settings for cargo and maturin (CARGO_TARGET_DIR and others) are given by environment variables themselves, and there is no entry point to receive settings through Ask")]
             :forbid-modules [doeff doeff_core_effects doeff_hy doeff_vm doeff_agents doeff_cluster])
           (layer release
             :summary "CI tool that uploads Rust wheels to the store (release rust-wheels) -- called by GitHub Actions, code outside a Program"
             :knows "uv build, gh release, the GitHub Actions environment"
             :does-not-know "doeff Programs, effects, handlers"
             :modules [rust_wheel_release]
             :exempt [(rule DOEFF004 "It runs only inside GitHub Actions and outside a doeff Program, so the store's repo (GITHUB_REPOSITORY) is given by the environment variables Actions passes, the settings handed to the build entry point are likewise given by the child uv build's environment, and there is no entry point to receive settings through Ask")]
             :forbid-modules [doeff doeff_core_effects doeff_hy doeff_vm doeff_agents doeff_cluster])])
