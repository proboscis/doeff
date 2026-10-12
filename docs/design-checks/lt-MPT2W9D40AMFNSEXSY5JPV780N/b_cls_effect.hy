(require doeff-hy.macros [defeffect])
(defclass Outer []
  (defeffect Inner {:fields [(: x int)] :answer int :tags {:context "t" :role "intent"}}))
