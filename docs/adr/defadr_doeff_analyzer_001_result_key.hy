;;; Executable ADR: the key of the store for doeff-effect-analyzer's closure-analysis answers (result_cache) is only the set of modules the answer depends on — the set reached
;;; by following the modules whose source the analysis read, the analysis's own modules, and the modules that made values from them. Not every module the process loaded.
;;;
;;; Motivation = when the store key misses, the bodies of the 10 closure checks take about 240 seconds in total. The former key (ebb060dec, 733107dae, 094738fb3) was
;;; "the names and contents of every module loaded by the process that made the answer"; ebb060dec took it broadly for fear of missing "modules that rewrite other modules' values at import time".
;;; This ADR re-decides that.
;;;
;;; Rollback: revert the commit that added this ADR (the one ADR file, and the result_cache key goes back to every loaded module. Entries with the store
;;; shape mark v4 just stop being read).

(require doeff-adr.macros [defadr rule law])
(import doeff-adr.macros [fact interpretation counterexample])
(require doeff-hy.macros [deftest val])
(import importlib)
(import os)
(import sys)
(import tempfile)
(import pathlib [Path])
(import unittest.mock [patch])


;; The analyzer's Python front part (readable without building the Rust extension — not installed in the root venv. Same placement as the conftest of the analyzer's checks).
(val ANALYZER-PYTHON (str (/ (. (Path __file__) parent parent parent) "packages" "doeff-effect-analyzer" "python")))


(defadr ADR-DOE-ANALYZER-001
  :title "The key of the store for closure-analysis answers is only the set of modules the answer depends on — the set reached by following the modules whose source the analysis read, the analysis's own modules, and the modules that made values from them (parent packages, defining modules of namespace values, Hy macros, imports in function bodies, dotted names). Not every loaded module"
  :status "accepted"
  :scope ["packages/doeff-effect-analyzer/python/doeff_effect_analyzer/_result_cache.py"
          "packages/doeff-effect-analyzer/python/doeff_effect_analyzer/_program_effects.py"
          "docs/adr/defadr_doeff_analyzer_001_result_key.hy"]
  :problem
    [(fact
       "The daily pytest stage of agora-controllers distributes 951 files over 3 sessions. Every session loads hundreds of check files unrelated to the closure checks, and the store key was \"the names and contents of every module loaded by the process that made the answer\", so it missed whenever any one of them changed within 4 hours. conftests with overlapping names and __mp_main__, which varies with how the process is started, were also in the key.")
     (fact
       "On a hit one closure check takes 0.05 to 1.2 seconds; on a miss 20 to 60 seconds. The bodies of the 10 closure checks of the daily 08:00 run total about 240 seconds, the size of a miss."
       :evidence "local measurement")
     (fact
       "The former key counted \"modules that rewrite other modules' values at import time\" only by their contents when the process that made the answer had loaded them, and did not count whether the reading process had loaded them (a module not loaded is compared by the contents of the file the import path finds). A rewriting module present only on the reading side has been outside the key since ebb060dec."
       :evidence "the module docstring of result_cache.py (the version before this change), _current_material")]
  :context
    [(interpretation
       "The answer is determined by the source the analysis reads and the runtime values made by importing it. Modules whose source is read go through the analysis's reading point (program_effects._module_source), so their names can be counted while the analysis runs (reading_sources). The values live in those modules' namespaces, and the modules that made the values can be traced from the values: the defining module of functions and classes (__module__ and the code's file), the defining module of an instance's class, modules bound to names, the Hy macro table, imports in function bodies (the analysis also follows imports inside functions), dotted names passed to __import__, functions wrapped by a function (defk's __wrapped__) and functions in closures. This is repeated for each module reached.")
     (interpretation
       "A package holds every loaded submodule as an attribute, regardless of who imported it. So the trace goes from a package to a submodule only when the code of a reached module uses that submodule's name (using a.b.x after import a.b). Bare names in code (attribute or variable names such as conftest, test) are not taken as modules — merely because pytest's code uses the name conftest, a different conftest per session entered the key (measured during this change).")
     (interpretation
       "What narrowing loses stays the same kind as before: a module not reachable from the set the answer depends on rewriting values at import time (the former key did not count its presence in the reading process either), values decided at import time from non-module files and environment variables, and bare values (numbers, strings) made at import time by a module that leaves no traceable reference. agora-controllers does none of these at import time.")]
  :decision
    [(rule R1 "The key of the answer store is the names and contents of the set of modules reached from the modules whose source the analysis read (program_effects.reading_sources) and the analysis's own modules (the defining module of cached_result's compute), by following parent packages, defining modules of namespace values, Hy macros, imports and dotted names in function bodies, and the defining modules of wrapped functions and closure functions. Do not key on every module the process loaded.")
     (rule R2 "The trace goes from a package to a submodule only when the code of a reached module uses that submodule's name. Bare names in code are not taken as modules (only names in import statements and dotted strings are taken).")
     (rule R3 "What is not in the key (rewriting modules not reachable from the set the answer depends on, non-module files and environment variables, bare values leaving no reference) is written in the module docstring of result_cache; to change it, amend this ADR.")]
  :laws
    [(law an-unrelated-module-does-not-take-part-in-the-key
       :statement "for_all module m loaded but neither read by the analysis nor reached from what it read: change(m) => the stored answer is reused"
       :counterexamples
         [(counterexample "the key is every loaded module (the shape before this change) — a change to one unrelated check file in the same session makes a closure check redo a 20 to 60 second analysis")
          (counterexample "a bare name in code (an attribute name) is put in the key when it equals the name of a loaded module — conftest enters the key and it misses per session")])
     (law a-module-the-answer-depends-on-is-in-the-key
       :statement "for_all module m read by the analysis, or whose values a read module holds, or imported in a read function's body: change(m) => the analysis runs again"
       :counterexamples
         [(counterexample "the key is only the modules the analysis read — a change to the module that made a read module's values at import time (a handler factory) returns a stale answer")])]
  :enforcement
    [(deftest test-adr-doe-analyzer-001-an-unrelated-module-does-not-take-part-in-the-key
       ;; The analysis reads the job module's source and puts the contents of an unrelated module's file in the answer (if reused, the old contents come back).
       ;; Counterexample: even when the job function's code uses the unrelated module's name as a bare name (attribute result_key_adr_other), that module is not in the key.
       (val other-name (.join "_" #("result_key_adr" "other")))
       (with [scratch (tempfile.TemporaryDirectory)
              _env (patch.dict os.environ {"DOEFF_EFFECT_ANALYZER_CACHE" (str (/ (Path scratch) "trees"))})
              _modules (patch.dict sys.modules)
              _path (patch.object sys "path" [scratch ANALYZER-PYTHON #* sys.path])]
         (os.environ.pop "DOEFF_EFFECT_ANALYZER_RESULT_CACHE" None)
         (val program-effects (importlib.import-module "doeff_effect_analyzer._program_effects"))
         (val result-cache (importlib.import-module "doeff_effect_analyzer._result_cache"))
         (.write-text (/ (Path scratch) "result_key_adr_job.py") f"def job(config):\n    return config.{other-name}\n" :encoding "utf-8")
         (val other (/ (Path scratch) f"{other-name}.py"))
         (.write-text other "VALUE = 1\n" :encoding "utf-8")
         (importlib.import-module "result_key_adr_job")
         (importlib.import-module other-name)
         (val analysis (fn []
                         (program-effects.module-ast (get sys.modules "result_key_adr_job"))
                         (.read-text other :encoding "utf-8")))
         (val first (result-cache.cached-result #("adr.analysis.unrelated") analysis))
         (.write-text other "VALUE = 2\n" :encoding "utf-8")
         (val second (result-cache.cached-result #("adr.analysis.unrelated") analysis))
         (assert (= first second "VALUE = 1\n")
                 (+ "re-analyzed on a change to an unrelated module (ADR-DOE-ANALYZER-001 R1, R2): " (repr second)))))
     (deftest test-adr-doe-analyzer-001-a-module-the-answer-depends-on-is-in-the-key
       ;; The analysis reads only the made module, but made's value HANDLER was made by the factory module at import time — a change to the factory triggers re-analysis
       ;; (the answer holds the factory file's contents; on re-analysis the new contents come back).
       (with [scratch (tempfile.TemporaryDirectory)
              _env (patch.dict os.environ {"DOEFF_EFFECT_ANALYZER_CACHE" (str (/ (Path scratch) "trees"))})
              _modules (patch.dict sys.modules)
              _path (patch.object sys "path" [scratch ANALYZER-PYTHON #* sys.path])]
         (os.environ.pop "DOEFF_EFFECT_ANALYZER_RESULT_CACHE" None)
         (val program-effects (importlib.import-module "doeff_effect_analyzer._program_effects"))
         (val result-cache (importlib.import-module "doeff_effect_analyzer._result_cache"))
         (val factory (/ (Path scratch) "result_key_adr_factory.py"))
         (.write-text factory "def make():\n    def handler():\n        return 1\n    return handler\n" :encoding "utf-8")
         (.write-text (/ (Path scratch) "result_key_adr_made.py") "from result_key_adr_factory import make\nHANDLER = make()\n"
                      :encoding "utf-8")
         (importlib.import-module "result_key_adr_made")
         (val analysis (fn []
                         (program-effects.module-ast (get sys.modules "result_key_adr_made"))
                         (.read-text factory :encoding "utf-8")))
         (result-cache.cached-result #("adr.analysis.depended") analysis)
         (.write-text factory "def make():\n    def handler():\n        return 22\n    return handler\n" :encoding "utf-8")
         (val second (result-cache.cached-result #("adr.analysis.depended") analysis))
         (assert (in "return 22" second)
                 (+ "did not re-analyze on a change to the module that made the value (ADR-DOE-ANALYZER-001 R1): " (repr second)))))]
  :plans [])
