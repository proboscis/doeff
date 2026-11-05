;;; Layer declarations for the files under scripts.
;;;
;;; For now this holds only one layer that is excluded from the rule population (same form as packages/doeff-linter/scripts/architecture.hy and tools/architecture.hy).
;;; doeff-linter decides the population of per-statement Python rules from the nearest architecture.hy found walking up from the file (#2811). Only the
;;; named module below is exempted here; the population for the other files in this dir is unchanged (DOEFF004 still applies as before).
;;;
;;; changed-tests = run_changed_tests, the entry point that tests what changed (a PEP 723 script, with empty dependencies = stdlib only). The author runs it with
;;;   `uv run --script` before registering, and it works without the repo's Python environment -- it runs outside a doeff Program and cannot import doeff's handlers
;;;   (the subprocess_handler that answers ReadEnvironment). This module reads the environment in a single place, the entry point (main(os.environ)), and passes
;;;   it down to the functions below as a mapping (the functions below do not read os.environ).
;;;   - Exempted rule: DOEFF004 (reading or referencing os.environ directly) -- limited to the named module (run_changed_tests) (not the whole dir).
;;;   - Forbidden imports: doeff modules. If the stdlib-only promise breaks, DOEFF032 turns red.
;;; Decision = cisco-c8 2026-10-03 02:4x (treated the same as linter_snapshot in bundle A -- do not add doeff-core-effects to the dependencies and change the entry point's structure).
;;; How to revert: delete the :exempt line and DOEFF004 applies to the entry point's reads again. If this file is deleted, the population for the files in this dir returns to the root settings.
(defarchitecture doeff-scripts
  :root "."
  :layers [(layer changed-tests
             :summary "Entry point that tests what changed -- a stdlib-only script called with uv run --script, code outside a Program"
             :knows "git diffs, pytest cutoffs, a mapping of environment variables"
             :does-not-know "doeff Programs, effects, handlers"
             :modules [run_changed_tests]
             :exempt [(rule DOEFF004 "A PEP 723 stdlib-only script runs outside a doeff Program and cannot import doeff's handlers. This module reads the environment in a single place, the entry point (main(os.environ)), and passes the mapping to the functions below")]
             :forbid-modules [doeff doeff_core_effects doeff_hy doeff_vm doeff_agents doeff_cluster])])
