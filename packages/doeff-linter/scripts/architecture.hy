;;; Layer declaration for the files in packages/doeff-linter/scripts.
;;;
;;; For now it places only one layer that is excluded from the rule population (the same shape as tools/architecture.hy and packages/doeff-indexer/architecture.hy). doeff-linter
;;; decides the population of per-statement Python rules from the nearest architecture.hy above a file (#2811). Only the named module below is excluded here.
;;;
;;; snapshot = the tool linter_snapshot that assembles the linter's cross-section (a PEP 723 script, with empty dependencies = stdlib only). The commit hook and the pin helper
;;;   call it via `uv run --script`, and it works even without the repo's Python environment — it runs outside a doeff Program and cannot import doeff handlers (the subprocess_handler that
;;;   answers ReadEnvironment). The entry point main reads the environment variables (the storage location name, XDG_CACHE_HOME, HOME, and git's GIT_*) exactly
;;;   once and passes them to the functions below as a mapping (the functions below do not read os.environ).
;;;   - Excluded rule: DOEFF004 (reading or referencing os.environ directly) — limited to the named module (linter_snapshot).
;;;   - Forbidden import: doeff modules. If the stdlib-only promise breaks, DOEFF032 turns red.
;;; How to revert: delete the :exempt line and DOEFF004 applies to the entry-point reads again. If this file is deleted, the files in this dir return to the root's settings for their population.
(defarchitecture doeff-linter-scripts
  :root "."
  :layers [(layer snapshot
             :summary "A stdlib-only tool that assembles the linter's cross-section — invoked with uv run --script, code outside a Program"
             :knows "git, cargo, the cross-section storage location, the mapping of environment variables"
             :does-not-know "doeff Programs, effects, handlers"
             :modules [linter_snapshot]
             :exempt [(rule DOEFF004 "A PEP 723 stdlib-only script runs outside a doeff Program and cannot import doeff handlers, so the entry point main reads the environment-variable mapping exactly once and passes it to the functions below")]
             :forbid-modules [doeff doeff_core_effects doeff_hy doeff_vm doeff_agents doeff_cluster])])
