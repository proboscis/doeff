(require doeff-hy.handle [defhandler])
(require doeff-hy.macros [defeffect])
(defeffect Ping {:fields [(: x int)] :answer int :tags {:context "t" :role "intent"}})
(defclass Outer []
  (defhandler h (Ping [x] (resume x))))
