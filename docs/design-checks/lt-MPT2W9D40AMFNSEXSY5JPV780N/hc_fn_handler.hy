(require doeff-hy.handle [defhandler])
(require doeff-hy.macros [defeffect])
(defeffect Ping {:fields [(: x int)] :answer int :tags {:context "t" :role "intent"}})
(defhandler top (Ping [x] (resume x)))
(defn subject []
  (setv first top)
  (defhandler h (Ping [x] (resume x)))
  [first h])
