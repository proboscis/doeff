;;; Layer declaration for doeff-effect-analyzer.
;;;
;;; For now it holds a single layer that is excluded from the rule population. doeff-linter decides the per-statement rule population for Python
;;; from the nearest architecture.hy above the file (this file) (#2811). Only the module named below is excluded here; the population for the other files of this package
;;; is unchanged (DOEFF004 still applies as before).
;;;
;;; environment = the module that reads the analysis cache location and on/off switch from environment variables (doeff_effect_analyzer._env_places). The analyzer is a tool (CLI,
;;;   editor, pytest collection) that runs outside any Program, and the location is decided before any Program, so there is no entry point to receive it with Ask.
;;;   The module only returns the raw variable values (DOEFF_EFFECT_ANALYZER_CACHE, DOEFF_EFFECT_ANALYZER_RESULT_CACHE) and the XDG cache root,
;;;   and leaves the decisions (switching off with "off", which location to use) to the callers (program_effects, result_cache).
;;;   - Excluded rule: DOEFF004 (reading os.environ directly) -- limited to the named module.
;;;   - Forbidden imports: doeff's business modules. If business code enters the excluded layer, DOEFF032 turns red.
;;; How to revert: delete the :exempt line and DOEFF004 applies to env_places again. If this file is deleted, the file population of this package
;;;   returns to the root configuration.
(defarchitecture doeff-effect-analyzer
  :root "."
  :layers [(layer environment
             :summary "module that reads the analysis cache location and on/off switch from environment variables -- decides the location outside any Program"
             :knows "environment variable names, XDG cache root"
             :does-not-know "doeff Programs, effects, handlers, the decision of which location to use"
             :modules [doeff_effect_analyzer._env_places]
             :exempt [(rule DOEFF004 "The analyzer runs outside any Program as a CLI, editor, or pytest collection, and the cache location and on/off switch are decided before any Program, so there is no entry point to receive the setting with Ask (it only returns the raw variable values and leaves decisions to callers)")]
             :forbid-modules [doeff doeff_core_effects doeff_hy doeff_vm doeff_effect_analyzer])])
