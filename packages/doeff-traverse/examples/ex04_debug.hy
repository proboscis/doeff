;;; Debug: check traverse macro expansion

(require doeff-hy.macros [defk <- traverse])
(import doeff [do :as _doeff-do])
(import doeff_traverse [Traverse :as _doeff_traverse_Traverse])

;; Simple case — what does this expand to?
(print (hy.repr (hy.macroexpand '(traverse
  (<- x (Iterate items))
  (<- y (some-effect x))
  [x y]))))
