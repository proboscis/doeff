;;; テストの子 process が組む handler の組(job_entry の --env に import path で渡す)。
(import doeff_core_effects.handlers [reader])


(defn #^ list plain-env [#^ dict config ctx]
  [(reader {"worker" "child" "base" 100})])
