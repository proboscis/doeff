;;; S4 の利用者の module: class の本体に handle・defhandler を直に書く(直す前の handle.hy で compile すると NameError)。
(require doeff-hy.handle [handle defhandler])
(import dataclasses [dataclass])
(import doeff [EffectBase run])
(import doeff_core_effects [state])
(import doeff_core_effects.scheduler [scheduled])

(defclass [(dataclass :frozen True)] Bump [EffectBase] #^ int n)

(defclass Handlers []
  (defhandler bump-h [base] (Bump [n] (resume (+ n base))))
  (setv program (handle (Bump 1) (Bump [n] (resume (+ n 100))))))

(defn main []
  "S4 の検の入口: class の本体の defhandler と handle の Program を走らせた答えを返すため。"
  #((run (scheduled ((state :initial {}) ((Handlers.bump-h 100) (Bump 1))))) (run Handlers.program)))
