;;; Layer declaration for doeff-openrouter.
;;;
;;; For now only one layer is declared (nothing is excluded from the rule population — the former DOEFF004 exclusion was removed in #3012). doeff-linter decides the per-statement
;;; rule population for Python from the nearest architecture.hy above the file (this file) (#2811). A module's name is the file's name (the tests dir has no
;;; __init__.py).
;;;
;;; local-dotenv = a module (tests/local_dotenv.py) that reads the OpenRouter key used by live tests from the runner's local .env (packages/doeff-openrouter/.env) and returns the value.
;;;   It does not write into the test process's environment — the live test fixture reads the key first from an environment variable (ReadEnvironment and
;;;   the real responder), and from this value if absent. It used to copy .env into os.environ and was therefore excluded from DOEFF004, but that exclusion was removed.
;;;   - Forbidden imports: doeff business modules. If business code enters this layer, DOEFF032 turns red.
;;; How to revert: deleting this file returns this package's file population to the root configuration.
(defarchitecture doeff-openrouter
  :root "."
  :layers [(layer local-dotenv
             :summary "A module that reads the runner's local .env and returns the value (does not write into the test process's environment)"
             :knows "the shape of .env"
             :does-not-know "doeff Programs, effects, handlers, environment variables"
             :modules [local_dotenv]
             :forbid-modules [doeff doeff_openrouter doeff_core_effects doeff_hy doeff_vm])])
