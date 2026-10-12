;;; S4 の利用者の module: defk の本体で traverse の後に handle を書く(直す前の handle.hy で compile すると UnboundLocalError)。
(require doeff-hy.macros [defk <- traverse handle])
(import dataclasses [dataclass])
(import doeff [EffectBase run])
(import doeff_traverse [Traverse :as _doeff_traverse_Traverse])
(import doeff_core_effects [try_handler])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_traverse.handlers [fail_handler sequential])

(defclass [(dataclass :frozen True)] Bump [EffectBase] #^ int n)
(defk bump-n [n] {:pre [(: n int)] :post [(: % int)]} (<- y (Bump n)) y)

(defk subject [items]
  {:pre [(: items list)] :post [(: % int)]}
  (<- coll (traverse (<- x (Iterate items)) (* x 2)))
  (<- r (handle (bump-n (sum coll)) (Bump [n] (resume (+ n 100)))))
  r)

(defn main []
  "S4 の検の入口: subject を traverse の handler の下で走らせた答えを返すため。"
  (run (scheduled (try_handler (fail_handler ((sequential) (subject [1 2 3])))))))
