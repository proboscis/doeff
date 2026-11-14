;;; Layer declarations for the files under scripts.
;;;
;;; For now this holds only the layers that are excluded from the rule population (same form as packages/doeff-linter/scripts/architecture.hy and tools/architecture.hy).
;;; doeff-linter decides the population of per-statement Python rules from the nearest architecture.hy found walking up from the file (#2811). Only the
;;; named module below is exempted here; the population for the other files in this dir is unchanged (DOEFF004 still applies as before).
;;;
;;; changed-tests = run_changed_tests, the entry point that tests what changed (a PEP 723 script, with empty dependencies = stdlib only). The author runs it with
;;;   `uv run --script` before registering, and it works without the repo's Python environment -- it runs outside a doeff Program and cannot import doeff's handlers
;;;   (the subprocess_handler that answers ReadEnvironment). This module reads the environment in a single place, the entry point (main(os.environ)), and passes
;;;   it down to the functions below as a mapping (the functions below do not read os.environ).
;;;   - Exempted rule: DOEFF004 (reading or referencing os.environ directly) -- limited to the named module (run_changed_tests) (not the whole dir).
;;;   - Forbidden imports: doeff modules. If the stdlib-only promise breaks, DOEFF032 turns red.
;;; leftovers = find_internal_leftovers, which lists what private tooling left in the tree (a PEP 723 script, with empty dependencies = stdlib
;;;   only, run with `uv run --script`). It runs outside a doeff Program and cannot import doeff's handlers. The site passes the names of its
;;;   machines, which are not written in this public tree, in DOEFF_INTERNAL_MACHINE_NAMES; the module reads it in one place (MACHINE_WORDS).
;;;   - Exempted rule: DOEFF004 -- limited to the named module (find_internal_leftovers).
;;;   - Forbidden imports: doeff modules, as for changed-tests.
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
             :forbid-modules [doeff doeff_core_effects doeff_hy doeff_vm doeff_agents doeff_cluster])
           (layer leftovers
             :summary "Stdlib-only script that lists what private tooling left in the tree -- called with uv run --script, code outside a Program"
             :knows "the tree's files, the kinds of leftovers, the site's machine names"
             :does-not-know "doeff Programs, effects, handlers"
             :modules [find_internal_leftovers]
             :exempt [(rule DOEFF004 "A PEP 723 stdlib-only script runs outside a doeff Program and cannot import doeff's handlers; the site passes its machine names, which this public tree does not hold, in an environment variable read in one place")]
             :forbid-modules [doeff doeff_core_effects doeff_hy doeff_vm doeff_agents doeff_cluster])])
