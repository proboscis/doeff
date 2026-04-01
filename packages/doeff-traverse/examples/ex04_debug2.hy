(require doeff-hy.macros [defk <- traverse])
(import doeff [do :as _doeff-do])
(import doeff_traverse [Traverse :as _doeff_traverse_Traverse])

(defk double [x]
  (* x 2))

;; Manually construct what the macro produces
(setv f (fn [x] (_doeff_do (fn [] (do (setv y (yield (double x))) y)))))

(print "f:" f)
(print "f(1):" (f 1))
(print "type:" (type (f 1)))
