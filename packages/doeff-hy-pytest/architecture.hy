;;; Layer declarations for doeff-hy-pytest.
;;;
;;; For now this holds a single layer that is removed from the rule population, in the same form as packages/doeff-hy/architecture.hy and
;;; packages/doeff-secret/architecture.hy. doeff-linter decides the population of per-statement Python rules from the nearest architecture.hy
;;; above a file (this file). Only the named module is exempted; the population for the other files of this package is unchanged.
;;;
;;; tiers = the test-tier plugin (doeff_hy_pytest._tier). Its hook pytest_collection_modifyitems deselects the items of other tiers, and pytest's
;;;   hook contract is to change the list it passes in place (`items[:] = kept`) -- pytest keeps and runs that same list object, so returning a
;;;   new list or rebinding the name deselects nothing.
;;;   - Rule removed: DOEFF007 (mutating a function argument) -- limited to the named module (doeff_hy_pytest._tier).
;;; How to revert: delete the :exempt line and DOEFF007 applies to the plugin again. If this file is deleted, the population for the files of
;;;   this package returns to the root settings.
(defarchitecture doeff-hy-pytest
  :root "."
  :layers [(layer tiers
             :summary "pytest plugin that gives every test a size class and deselects the other classes -- code called by pytest's hooks"
             :knows "pytest's hooks, markers and the collected items"
             :does-not-know "doeff Programs, effects, handlers"
             :modules [doeff_hy_pytest._tier]
             :exempt [(rule DOEFF007 "pytest_collection_modifyitems deselects by changing the items list pytest passes in place -- pytest's hook contract; a new list deselects nothing")])])
