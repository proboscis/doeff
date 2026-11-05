;;; Layer declaration for doeff-secret.
;;;
;;; doeff-linter decides the per-statement rule population for Python from the nearest architecture.hy above the file (this file) (#2811).
;;; Only one foundation layer is declared, and it names just one module: the handler that answers GetSecret from environment variables. The population of this package's
;;; other files does not change (DOEFF004 applies as before).
;;;
;;; foundation = the layer of foundation handlers that are allowed to read environment variables (environment reads happen
;;;   only inside foundation handlers; reads outside are violations).
;;;   - doeff_secret._handlers: the handler that answers GetSecret from environment variables (env_var_handler, env_var_handlers). If no mapping is passed,
;;;     it reads this process's environment (os.environ). These are the 2 lines (63 and 97) hit when DOEFF004 was widened to
;;;     "any reference to os.environ" (decided by cisco-c8 2026-10-03 02:0x — a declaration of where the rule allows it, not an exclusion that hides violations).
;;;   - Excluded rule: DOEFF004 (reading os.environ directly) — limited to the one named module; do not add other files.
;;; How to revert: deleting the :exempt line makes DOEFF004 apply to handlers again. Deleting this file returns this package's file population to
;;;   the root configuration.
(defarchitecture doeff-secret
  :root "."
  :layers [(layer foundation
             :summary "The layer of foundation handlers allowed to read environment variables — the handler that answers GetSecret from environment variables"
             :knows "environment variable names, the mapping (prefix) from a secret id to an environment variable name"
             :does-not-know "business Programs, which business uses which secret"
             :modules [doeff_secret._handlers]
             :exempt [(rule DOEFF004 "foundation layer — the handler that answers GetSecret from environment variables. Environment reads are allowed only inside foundation handlers")])])
